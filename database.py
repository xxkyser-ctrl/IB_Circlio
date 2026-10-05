"""Shared plain/encrypted SQLite connection and safe database migration."""

import hashlib
import json
import os
import shutil
import sqlite3 as plain_sqlite
from datetime import datetime, timezone
from pathlib import Path

from sqlcipher3 import dbapi2 as cipher_sqlite

import encryption


SQLITE_ERRORS = (plain_sqlite.Error, cipher_sqlite.Error)
SQLITE_OPERATIONAL_ERRORS = (
    plain_sqlite.OperationalError,
    cipher_sqlite.OperationalError,
)
_pending_recovery_keys = {}


def _cipher_key(master_key):
    return '"x\'' + master_key.hex() + '\'"'


def _connect_cipher(path, master_key, *, readonly=False):
    path = Path(path)
    if readonly:
        connection = cipher_sqlite.connect(
            f"{path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False
        )
    else:
        connection = cipher_sqlite.connect(str(path), check_same_thread=False)
    try:
        connection.execute(f"PRAGMA key = {_cipher_key(master_key)}")
        connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return connection
    except cipher_sqlite.Error as error:
        connection.close()
        raise encryption.EncryptionError(
            "The encrypted database could not be authenticated. Check the key or restore a backup."
        ) from error


def _is_plain_sqlite(path):
    with Path(path).open("rb") as database_file:
        return database_file.read(16) == b"SQLite format 3\x00"


def take_pending_recovery_key(data_dir):
    """Return a newly-created recovery code once, in the process that created it."""
    return _pending_recovery_keys.pop(str(Path(data_dir).resolve()), None)


def open_database(path, *, readonly=False, create=True):
    path = Path(path)
    data_dir = path.parent
    recover_interrupted_migration(data_dir)
    metadata = encryption.load_metadata(data_dir)
    if metadata is not None:
        if not path.is_file():
            raise encryption.EncryptionError(
                "Encryption metadata exists but the database is missing; restore a backup."
            )
        master_key = encryption.load_master_key(data_dir)
        connection = _connect_cipher(path, master_key, readonly=readonly)
        connection.row_factory = cipher_sqlite.Row
        return connection

    if path.exists():
        if not path.is_file() or path.stat().st_size == 0 or not _is_plain_sqlite(path):
            raise encryption.EncryptionError(
                "The database is not a readable plaintext SQLite file and has no valid encryption metadata."
            )
        if readonly:
            connection = plain_sqlite.connect(
                f"{path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False
            )
        else:
            connection = plain_sqlite.connect(str(path), check_same_thread=False)
        connection.row_factory = plain_sqlite.Row
        return connection

    if not create:
        raise FileNotFoundError(f"Database does not exist: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        master_key, recovery_key = encryption.initialize_encryption(data_dir)
        try:
            connection = _connect_cipher(path, master_key)
            connection.row_factory = cipher_sqlite.Row
            _pending_recovery_keys[str(data_dir.resolve())] = recovery_key
            return connection
        except Exception:
            for name in (encryption.METADATA_NAME, encryption.DPAPI_KEY_NAME):
                try:
                    (data_dir / name).unlink()
                except FileNotFoundError:
                    pass
            encryption.clear_cached_master_key(data_dir)
            raise
    connection = plain_sqlite.connect(str(path), check_same_thread=False)
    connection.row_factory = plain_sqlite.Row
    return connection


def database_integrity(connection):
    rows = connection.execute("PRAGMA integrity_check").fetchall()
    return len(rows) == 1 and rows[0][0] == "ok"


def _table_fingerprints(connection):
    names = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    result = {}
    for name in names:
        quoted = '"' + name.replace('"', '""') + '"'
        columns = [
            row[1]
            for row in connection.execute(f"PRAGMA table_info({quoted})")
        ]
        digest = hashlib.sha256()
        count = 0
        for row in connection.execute(f"SELECT * FROM {quoted}"):
            normalized = json.dumps(
                [row[index] for index in range(len(columns))],
                ensure_ascii=False,
                separators=(",", ":"),
                default=lambda value: {"bytes": value.hex()}
                if isinstance(value, bytes)
                else str(value),
            ).encode("utf-8")
            digest.update(len(normalized).to_bytes(8, "big"))
            digest.update(normalized)
            count += 1
        result[name] = (count, digest.hexdigest())
    return result


def _check_service_stopped(data_dir):
    marker = Path(data_dir) / "service.lock"
    if not marker.exists():
        return
    handle = marker.open("r+b")
    try:
        _lock_service_file(handle)
    except OSError as error:
        handle.close()
        raise encryption.EncryptionError(
            "Stop the IB Circlio collection service before changing encryption."
        ) from error
    _unlock_service_file(handle)
    handle.close()


def _lock_service_file(handle):
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_service_file(handle):
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def acquire_service_lock(data_dir):
    path = Path(data_dir) / "service.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b" ")
        handle.flush()
    try:
        _lock_service_file(handle)
    except OSError as error:
        handle.close()
        raise encryption.EncryptionError(
            "The IB Circlio collection service is already running for this data folder."
        ) from error
    handle.seek(0)
    handle.truncate()
    handle.write(f"{os.getpid()}\n".encode("ascii"))
    handle.flush()
    return handle


def release_service_lock(handle):
    if handle is None:
        return
    path = Path(handle.name)
    _unlock_service_file(handle)
    handle.close()
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _backup_data_dir(data_dir):
    data_dir = Path(data_dir)
    files_size = sum(
        item.stat().st_size for item in data_dir.rglob("*") if item.is_file()
    )
    parent = data_dir.parent
    free_space = shutil.disk_usage(parent).free
    if free_space < files_size * 2 + 16 * 1024 * 1024:
        raise encryption.EncryptionError(
            "There is not enough free disk space to create a verified migration backup."
        )
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = parent / f"{data_dir.name} backup {timestamp}"
    if backup.exists():
        raise encryption.EncryptionError("The timestamped migration backup already exists.")
    shutil.copytree(data_dir, backup)
    return backup


def _write_migration_journal(data_dir, backup, state):
    payload = {"format_version": 1, "state": state, "backup": str(backup)}
    temporary = Path(data_dir) / ".migration.json.tmp"
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, Path(data_dir) / "migration.json")


def recover_interrupted_migration(data_dir):
    """Restore migration-touched files from the retained backup before opening."""
    data_dir = Path(data_dir).resolve()
    journal = data_dir / "migration.json"
    if not journal.exists():
        return
    try:
        state = json.loads(journal.read_text(encoding="utf-8"))
        backup = Path(state["backup"]).resolve()
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise encryption.EncryptionError(
            "An interrupted data migration has an unreadable recovery journal. Preserve this data folder and its backup."
        ) from error
    if (
        state.get("format_version") != 1
        or backup.parent != data_dir.parent
        or not backup.name.startswith(f"{data_dir.name} backup ")
        or not backup.is_dir()
    ):
        raise encryption.EncryptionError(
            "An interrupted migration backup is missing or invalid. Preserve this data folder and its backup."
        )

    backup_database = backup / "instagram.db"
    if backup_database.exists():
        temporary_database = data_dir / ".instagram.rollback.tmp"
        shutil.copy2(backup_database, temporary_database)
        os.replace(temporary_database, data_dir / "instagram.db")
    current_avatars = data_dir / "avatars"
    backup_avatars = backup / "avatars"
    if current_avatars.exists():
        shutil.rmtree(current_avatars)
    if backup_avatars.exists():
        shutil.copytree(backup_avatars, current_avatars)
    for filename in (
        "server-token.txt",
        encryption.TOKEN_NAME,
        encryption.METADATA_NAME,
        encryption.DPAPI_KEY_NAME,
    ):
        source = backup / filename
        target = data_dir / filename
        if source.is_file():
            temporary = data_dir / f".{filename}.rollback.tmp"
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
        else:
            target.unlink(missing_ok=True)
    for filename in (
        ".instagram.encrypted.tmp",
        ".instagram.plaintext.tmp",
        ".token.encrypted.tmp",
        ".token.plaintext.tmp",
        ".migration.json.tmp",
    ):
        (data_dir / filename).unlink(missing_ok=True)
    for directory_name in (
        ".avatars.encrypted.tmp",
        ".avatars.plaintext.tmp",
        ".avatars.plaintext.backup",
        ".avatars.encrypted.backup",
    ):
        directory = data_dir / directory_name
        if directory.exists():
            shutil.rmtree(directory)
    journal.unlink()
    if encryption.load_metadata(data_dir) is None:
        encryption.clear_cached_master_key(data_dir)


def _export_encrypted_database(source_path, staged_path, master_key):
    source = cipher_sqlite.connect(str(source_path))
    try:
        source.execute(
            f"ATTACH DATABASE ? AS encrypted KEY {_cipher_key(master_key)}",
            (str(staged_path),),
        )
        source.execute("SELECT sqlcipher_export('encrypted')")
        source.execute("DETACH DATABASE encrypted")
    finally:
        source.close()
    verify = _connect_cipher(staged_path, master_key, readonly=True)
    try:
        if not database_integrity(verify):
            raise encryption.EncryptionError("The encrypted database failed its integrity check.")
    finally:
        verify.close()


def _encrypt_avatar_tree(data_dir, staged_dir, master_key):
    source_dir = Path(data_dir) / "avatars"
    if not source_dir.exists():
        return []
    staged_dir.mkdir(parents=True, exist_ok=True)
    verified = []
    for source in source_dir.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(source_dir)
        destination = staged_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        plaintext = source.read_bytes()
        ciphertext = encryption.encrypt_avatar(plaintext, master_key)
        destination.write_bytes(ciphertext)
        recovered = encryption.decrypt_avatar(destination.read_bytes(), master_key)
        if not hashlib.sha256(plaintext).digest() == hashlib.sha256(recovered).digest():
            raise encryption.EncryptionError(f"Avatar verification failed: {relative.name}")
        verified.append((source, destination))
    return verified


def enable_encryption(data_dir, *, mode="dpapi", passphrase=None):
    """Back up, stage, verify, then switch a plaintext data directory to encryption."""
    data_dir = Path(data_dir).resolve()
    _check_service_stopped(data_dir)
    database_path = data_dir / "instagram.db"
    if encryption.load_metadata(data_dir):
        raise encryption.EncryptionError("This data folder is already encrypted.")
    if not database_path.is_file() or not _is_plain_sqlite(database_path):
        raise encryption.EncryptionError("A valid plaintext database is required for migration.")

    backup = _backup_data_dir(data_dir)
    _write_migration_journal(data_dir, backup, "started")
    original = plain_sqlite.connect(str(database_path))
    original.row_factory = plain_sqlite.Row
    try:
        original.execute("BEGIN EXCLUSIVE")
        before = _table_fingerprints(original)
        if not database_integrity(original):
            raise encryption.EncryptionError("The plaintext database failed its integrity check.")
    finally:
        original.rollback()
        original.close()

    master_key, recovery_key = encryption.initialize_encryption(
        data_dir, mode=mode, passphrase=passphrase
    )
    staging_db = data_dir / ".instagram.encrypted.tmp"
    staging_avatars = data_dir / ".avatars.encrypted.tmp"
    journal = data_dir / "migration.json"
    try:
        _export_encrypted_database(database_path, staging_db, master_key)
        encrypted_connection = _connect_cipher(staging_db, master_key, readonly=True)
        try:
            if _table_fingerprints(encrypted_connection) != before:
                raise encryption.EncryptionError(
                    "The encrypted database contents do not match the original."
                )
        finally:
            encrypted_connection.close()

        avatar_files = _encrypt_avatar_tree(data_dir, staging_avatars, master_key)
        _write_migration_journal(data_dir, backup, "switching")
        token_source = data_dir / "server-token.txt"
        staged_token = data_dir / ".token.encrypted.tmp"
        if token_source.exists():
            token = token_source.read_text(encoding="utf-8").strip()
            staged_token.write_bytes(encryption.encrypt_token(token, master_key))
            if encryption.decrypt_token(staged_token.read_bytes(), master_key) != token:
                raise encryption.EncryptionError("The encrypted service token failed verification.")
        if avatar_files:
            old_avatar_dir = data_dir / ".avatars.plaintext.backup"
            if old_avatar_dir.exists():
                raise encryption.EncryptionError("A prior avatar migration stage exists.")
            (data_dir / "avatars").rename(old_avatar_dir)
            staging_avatars.rename(data_dir / "avatars")
        os.replace(staging_db, database_path)
        if token_source.exists():
            os.replace(staged_token, data_dir / encryption.TOKEN_NAME)
            token_source.unlink()
        if (data_dir / ".avatars.plaintext.backup").exists():
            shutil.rmtree(data_dir / ".avatars.plaintext.backup")
        journal.unlink(missing_ok=True)
    except Exception:
        recover_interrupted_migration(data_dir)
        raise
    finally:
        staging_db.unlink(missing_ok=True)
        (data_dir / ".token.encrypted.tmp").unlink(missing_ok=True)
        if staging_avatars.exists():
            shutil.rmtree(staging_avatars)

    return {"backup": backup, "recovery_key": recovery_key}


def disable_encryption(data_dir, *, passphrase=None, recovery_key=None):
    """Back up, stage, verify, then switch an encrypted data directory to plaintext."""
    data_dir = Path(data_dir).resolve()
    _check_service_stopped(data_dir)
    metadata = encryption.load_metadata(data_dir)
    if metadata is None:
        raise encryption.EncryptionError("This data folder is already plaintext.")
    database_path = data_dir / "instagram.db"
    master_key = encryption.load_master_key(
        data_dir, passphrase=passphrase, recovery_key=recovery_key
    )
    backup = _backup_data_dir(data_dir)
    _write_migration_journal(data_dir, backup, "started")
    encrypted_connection = _connect_cipher(database_path, master_key, readonly=True)
    try:
        before = _table_fingerprints(encrypted_connection)
        if not database_integrity(encrypted_connection):
            raise encryption.EncryptionError("The encrypted database failed its integrity check.")
    finally:
        encrypted_connection.close()

    staging_db = data_dir / ".instagram.plaintext.tmp"
    staging_avatars = data_dir / ".avatars.plaintext.tmp"
    try:
        source = cipher_sqlite.connect(str(database_path))
        try:
            source.execute(f"PRAGMA key = {_cipher_key(master_key)}")
            source.execute("ATTACH DATABASE ? AS plaintext KEY ''", (str(staging_db),))
            source.execute("SELECT sqlcipher_export('plaintext')")
            source.execute("DETACH DATABASE plaintext")
        finally:
            source.close()
        plaintext_connection = plain_sqlite.connect(str(staging_db))
        try:
            if not database_integrity(plaintext_connection):
                raise encryption.EncryptionError("The plaintext database failed its integrity check.")
            if _table_fingerprints(plaintext_connection) != before:
                raise encryption.EncryptionError(
                    "The plaintext database contents do not match the encrypted original."
                )
        finally:
            plaintext_connection.close()

        encrypted_avatar_dir = data_dir / "avatars"
        if encrypted_avatar_dir.exists():
            staging_avatars.mkdir(parents=True, exist_ok=True)
            for source_path in encrypted_avatar_dir.rglob("*"):
                if not source_path.is_file():
                    continue
                relative = source_path.relative_to(encrypted_avatar_dir)
                destination = staging_avatars / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                clear = encryption.decrypt_avatar(source_path.read_bytes(), master_key)
                destination.write_bytes(clear)
                if destination.read_bytes() != clear:
                    raise encryption.EncryptionError(
                        f"Avatar verification failed: {relative.name}"
                    )

        _write_migration_journal(data_dir, backup, "switching")
        if encrypted_avatar_dir.exists():
            old_avatar_dir = data_dir / ".avatars.encrypted.backup"
            encrypted_avatar_dir.rename(old_avatar_dir)
            staging_avatars.rename(data_dir / "avatars")
        token_path = data_dir / encryption.TOKEN_NAME
        if token_path.exists():
            clear_token = encryption.decrypt_token(token_path.read_bytes(), master_key)
            staged_token = data_dir / ".token.plaintext.tmp"
            staged_token.write_text(clear_token + "\n", encoding="utf-8")
            if staged_token.read_text(encoding="utf-8").strip() != clear_token:
                raise encryption.EncryptionError("The plaintext service token failed verification.")
            os.replace(staged_token, data_dir / "server-token.txt")
            token_path.unlink()
        os.replace(staging_db, database_path)
        for filename in (encryption.METADATA_NAME, encryption.DPAPI_KEY_NAME):
            (data_dir / filename).unlink(missing_ok=True)
        old_avatar_dir = data_dir / ".avatars.encrypted.backup"
        if old_avatar_dir.exists():
            shutil.rmtree(old_avatar_dir)
        (data_dir / "migration.json").unlink(missing_ok=True)
        encryption.clear_cached_master_key(data_dir)
    except Exception:
        recover_interrupted_migration(data_dir)
        raise
    finally:
        staging_db.unlink(missing_ok=True)
        (data_dir / ".token.plaintext.tmp").unlink(missing_ok=True)
        if staging_avatars.exists():
            shutil.rmtree(staging_avatars)

    return {"backup": backup}

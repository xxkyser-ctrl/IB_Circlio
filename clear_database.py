"""Password-protected local SQLite data reset utility."""

import argparse
import getpass
import hashlib
import hmac
import secrets
import shutil
from pathlib import Path

import database as database_access
import encryption
from data_paths import default_data_dir
from file_permissions import restrict_to_current_user


ITERATIONS = 600_000


def password_digest(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, ITERATIONS
    )


def read_or_create_password(path):
    if path.exists():
        parts = path.read_text(encoding="utf-8").strip().split("$")
        if len(parts) != 3:
            raise ValueError("The password file is invalid. Delete it to set a new password.")
        salt = bytes.fromhex(parts[0])
        expected = bytes.fromhex(parts[2])
        password = getpass.getpass("Enter the database-clear password: ")
        if not hmac.compare_digest(password_digest(password, salt), expected):
            raise ValueError("Incorrect password.")
        restrict_to_current_user(path)
        return

    print("No clear password exists yet.")
    password = getpass.getpass("Create a clear password: ")
    confirmation = getpass.getpass("Confirm the clear password: ")
    if not password or password != confirmation:
        raise ValueError("Passwords are empty or do not match.")
    salt = secrets.token_bytes(16)
    digest = password_digest(password, salt)
    path.write_text(
        f"{salt.hex()}${ITERATIONS}${digest.hex()}\n", encoding="utf-8"
    )
    restrict_to_current_user(path)
    print("Clear password created. Only its salted hash was stored.")


def clear_database(path):
    if not path.exists():
        print("Database does not exist; nothing to clear.")
        return
    data_dir = path.parent
    database_access._check_service_stopped(data_dir)
    connection = database_access.open_database(path, create=False)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN")
        existing_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        for table in (
            "membership_changes",
            "collection_memberships",
            "collection_avatar_versions",
            "collections",
            "profiles",
            "users",
            "avatar_cache",
        ):
            if table in existing_tables:
                connection.execute(f"DELETE FROM {table}")
        connection.commit()
    finally:
        connection.close()
    avatars = data_dir / "avatars"
    if avatars.exists():
        shutil.rmtree(avatars)
    print("All profiles, collections, users, and changes were cleared.")


def unlock_encrypted_data(data_dir):
    metadata = encryption.load_metadata(data_dir)
    if metadata is None:
        return
    try:
        if metadata["key_mode"] == "dpapi":
            encryption.load_master_key(data_dir)
            return
        passphrase = getpass.getpass("Enter the data-encryption passphrase: ")
        if passphrase:
            encryption.load_master_key(data_dir, passphrase=passphrase)
            return
    except encryption.EncryptionError as error:
        print(f"Could not unlock the data key: {error}")
    except OSError as error:
        print(f"Could not open the Windows-protected key: {error}")

    recovery_key = getpass.getpass("Enter the saved data recovery key: ")
    if not recovery_key:
        raise encryption.MissingKeyError(
            "A valid passphrase or recovery key is required to clear encrypted data."
        )
    encryption.load_master_key(data_dir, recovery_key=recovery_key)


def main():
    parser = argparse.ArgumentParser(description="Clear Instagram Exporter SQLite data")
    parser.add_argument("--data-dir", default=str(default_data_dir()))
    args = parser.parse_args()
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    password_path = data_dir / "clear-password.txt"
    database_path = data_dir / "instagram.db"
    read_or_create_password(password_path)
    confirmation = input("Type CLEAR to permanently delete all database data: ")
    if confirmation != "CLEAR":
        raise ValueError("Clear cancelled.")
    unlock_encrypted_data(data_dir)
    clear_database(database_path)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, *database_access.SQLITE_ERRORS) as error:
        print(f"Error: {error}")
        raise SystemExit(1)

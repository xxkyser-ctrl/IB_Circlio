import sqlite3
import tempfile
import unittest
from pathlib import Path

import clear_database
import database
import encryption
import launcher


class EncryptionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.directory.name) / "IB Circlio Data"
        self.data_dir.mkdir()

    def tearDown(self):
        encryption.clear_cached_master_key(self.data_dir)
        self.directory.cleanup()

    def test_encrypted_database_requires_correct_passphrase(self):
        database_path = self.data_dir / "instagram.db"
        master_key, _recovery_key = encryption.initialize_encryption(
            self.data_dir, mode="passphrase", passphrase="fixture-passphrase-123"
        )
        connection = database._connect_cipher(database_path, master_key)
        connection.execute("CREATE TABLE fixture (name TEXT)")
        connection.execute("INSERT INTO fixture VALUES ('private')")
        connection.commit()
        connection.close()

        encryption.clear_cached_master_key(self.data_dir)
        with self.assertRaises(encryption.EncryptionError):
            encryption.load_master_key(self.data_dir, passphrase="incorrect-passphrase")
        encryption.load_master_key(self.data_dir, passphrase="fixture-passphrase-123")
        connection = database.open_database(database_path, readonly=True)
        try:
            self.assertEqual(
                connection.execute("SELECT name FROM fixture").fetchone()[0],
                "private",
            )
        finally:
            connection.close()

    def test_avatar_and_token_tampering_fails_authentication(self):
        master_key = b"x" * encryption.KEY_BYTES
        for encrypted, decrypt in (
            (
                encryption.encrypt_avatar(b"fixture avatar", master_key),
                encryption.decrypt_avatar,
            ),
            (
                encryption.encrypt_token("fixture-token", master_key),
                encryption.decrypt_token,
            ),
        ):
            tampered = encrypted[:-1] + bytes([encrypted[-1] ^ 1])
            with self.assertRaises(encryption.EncryptionError):
                decrypt(tampered, master_key)

    def test_extension_config_receives_token_without_plaintext_token_file(self):
        encryption.initialize_encryption(
            self.data_dir, mode="passphrase", passphrase="fixture-passphrase-123"
        )
        (self.data_dir / "server-token.txt").write_text(
            "legacy-fixture-token", encoding="utf-8"
        )
        config_path = self.data_dir / "extension" / "config.js"
        token_path = launcher.prepare_local_config(self.data_dir, config_path)

        self.assertEqual(token_path.name, encryption.TOKEN_NAME)
        self.assertTrue(token_path.read_bytes().startswith(encryption.TOKEN_MAGIC))
        self.assertFalse((self.data_dir / "server-token.txt").exists())
        self.assertIn(
            encryption.read_token(self.data_dir),
            config_path.read_text(encoding="utf-8"),
        )

    def test_enable_and_disable_encryption_preserve_database_avatars_and_token(self):
        database_path = self.data_dir / "instagram.db"
        connection = sqlite3.connect(database_path)
        connection.execute("CREATE TABLE fixture (name TEXT)")
        connection.execute("INSERT INTO fixture VALUES ('private')")
        connection.commit()
        connection.close()
        token = "fixture-token-with-more-than-32-characters"
        (self.data_dir / "server-token.txt").write_text(token, encoding="utf-8")
        avatar_dir = self.data_dir / "avatars"
        avatar_dir.mkdir()
        avatar = avatar_dir / "fixture.jpg"
        avatar.write_bytes(b"fixture avatar bytes")

        migration = database.enable_encryption(
            self.data_dir, mode="passphrase", passphrase="fixture-passphrase-123"
        )
        self.assertTrue(Path(migration["backup"]).is_dir())
        self.assertTrue(encryption.is_encrypted(self.data_dir))
        self.assertTrue(avatar.read_bytes().startswith(encryption.AVATAR_MAGIC))
        self.assertEqual(encryption.read_token(self.data_dir), token)
        connection = database.open_database(database_path, readonly=True)
        try:
            self.assertEqual(
                connection.execute("SELECT name FROM fixture").fetchone()[0],
                "private",
            )
        finally:
            connection.close()

        database.disable_encryption(self.data_dir)
        self.assertFalse(encryption.is_encrypted(self.data_dir))
        self.assertEqual(avatar.read_bytes(), b"fixture avatar bytes")
        self.assertEqual(
            (self.data_dir / "server-token.txt").read_text(encoding="utf-8").strip(),
            token,
        )
        connection = sqlite3.connect(database_path)
        try:
            self.assertEqual(
                connection.execute("SELECT name FROM fixture").fetchone()[0],
                "private",
            )
        finally:
            connection.close()

    def test_clear_database_preserves_encryption_and_key_configuration(self):
        database_path = self.data_dir / "instagram.db"
        master_key, _recovery_key = encryption.initialize_encryption(
            self.data_dir, mode="passphrase", passphrase="fixture-passphrase-123"
        )
        connection = database._connect_cipher(database_path, master_key)
        connection.execute("CREATE TABLE users (username TEXT)")
        connection.execute("INSERT INTO users VALUES ('fixture_user')")
        connection.commit()
        connection.close()
        (self.data_dir / "avatars").mkdir()
        (self.data_dir / "avatars" / "fixture.jpg").write_bytes(b"fixture")

        clear_database.clear_database(database_path)

        self.assertTrue(encryption.is_encrypted(self.data_dir))
        self.assertFalse((self.data_dir / "avatars").exists())
        connection = database.open_database(database_path, readonly=True)
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0
            )
        finally:
            connection.close()

    def test_interrupted_migration_restores_the_sibling_backup(self):
        database_path = self.data_dir / "instagram.db"
        connection = sqlite3.connect(database_path)
        connection.execute("CREATE TABLE fixture (name TEXT)")
        connection.execute("INSERT INTO fixture VALUES ('before')")
        connection.commit()
        connection.close()
        avatar_dir = self.data_dir / "avatars"
        avatar_dir.mkdir()
        avatar = avatar_dir / "fixture.jpg"
        avatar.write_bytes(b"original avatar")
        token_path = self.data_dir / "server-token.txt"
        token_path.write_text("original-token", encoding="utf-8")

        backup = database._backup_data_dir(self.data_dir)
        database._write_migration_journal(self.data_dir, backup, "switching")
        database_path.write_bytes(b"interrupted database switch")
        avatar.write_bytes(b"interrupted avatar switch")
        (self.data_dir / ".avatars.plaintext.backup").mkdir()
        (self.data_dir / ".avatars.plaintext.backup" / "fixture.jpg").write_bytes(
            b"staged plaintext duplicate"
        )
        token_path.write_text("interrupted-token", encoding="utf-8")
        (self.data_dir / encryption.METADATA_NAME).write_text(
            '{"encrypted": true}', encoding="utf-8"
        )

        database.recover_interrupted_migration(self.data_dir)

        self.assertFalse((self.data_dir / "migration.json").exists())
        self.assertFalse((self.data_dir / ".avatars.plaintext.backup").exists())
        self.assertFalse(encryption.is_encrypted(self.data_dir))
        self.assertEqual(avatar.read_bytes(), b"original avatar")
        self.assertEqual(token_path.read_text(encoding="utf-8"), "original-token")
        connection = sqlite3.connect(database_path)
        try:
            self.assertEqual(
                connection.execute("SELECT name FROM fixture").fetchone()[0],
                "before",
            )
        finally:
            connection.close()

    def test_migration_refuses_to_run_while_service_lock_is_held(self):
        database_path = self.data_dir / "instagram.db"
        connection = sqlite3.connect(database_path)
        connection.execute("CREATE TABLE fixture (name TEXT)")
        connection.commit()
        connection.close()
        lock = database.acquire_service_lock(self.data_dir)
        try:
            with self.assertRaisesRegex(
                encryption.EncryptionError, "Stop the IB Circlio collection service"
            ):
                database.enable_encryption(self.data_dir)
        finally:
            database.release_service_lock(lock)


if __name__ == "__main__":
    unittest.main()

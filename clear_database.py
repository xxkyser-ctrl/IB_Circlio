"""Password-protected local SQLite data reset utility."""

import argparse
import getpass
import hashlib
import hmac
import secrets
import sqlite3
import shutil
from pathlib import Path


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
    print("Clear password created. Only its salted hash was stored.")


def clear_database(path):
    if not path.exists():
        print("Database does not exist; nothing to clear.")
        return
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN")
        for table in (
            "membership_changes",
            "collection_memberships",
            "collection_avatar_versions",
            "collections",
            "profiles",
            "users",
            "avatar_cache",
        ):
            try:
                connection.execute(f"DELETE FROM {table}")
            except sqlite3.OperationalError:
                pass
        connection.commit()
    finally:
        connection.close()
    shutil.rmtree(path.parent / "avatars", ignore_errors=True)
    print("All profiles, collections, users, and changes were cleared.")


def main():
    parser = argparse.ArgumentParser(description="Clear Instagram Exporter SQLite data")
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    password_path = data_dir / "clear-password.txt"
    database_path = data_dir / "instagram.db"
    read_or_create_password(password_path)
    confirmation = input("Type CLEAR to permanently delete all database data: ")
    if confirmation != "CLEAR":
        raise ValueError("Clear cancelled.")
    clear_database(database_path)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        print(f"Error: {error}")
        raise SystemExit(1)

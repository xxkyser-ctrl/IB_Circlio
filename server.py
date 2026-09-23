"""Local SQLite backend for the Instagram Exporter extension."""

import argparse
import hmac
import json
import os
import re
import sqlite3
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "Instagram Exporter Data")
DEFAULT_DB_PATH = os.path.join(DEFAULT_DATA_DIR, "instagram.db")
USERNAME_RE = re.compile(r"^[a-z0-9._]{1,30}$")
MAX_REQUEST_BYTES = 25 * 1024 * 1024
EXTENSION_ORIGIN_RE = re.compile(r"^chrome-extension://[a-p]{32}$")


def normalize_username(value):
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    return value if USERNAME_RE.fullmatch(value) else None


def normalize_profile(value):
    if not isinstance(value, str):
        return None
    value = value.strip().strip("/").lower()
    return normalize_username(value)


def normalize_users(values):
    if not isinstance(values, list):
        raise ValueError("followers and following must be arrays")
    result = {user for value in values if (user := normalize_username(value))}
    return sorted(result)


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    avatar_path TEXT
);
CREATE TABLE IF NOT EXISTS avatar_cache (
    username TEXT PRIMARY KEY,
    image_path TEXT NOT NULL,
    source_url TEXT,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    captured_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    complete INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS collection_memberships (
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    relationship TEXT NOT NULL CHECK (relationship IN ('followers', 'following')),
    PRIMARY KEY (collection_id, user_id, relationship)
);
CREATE TABLE IF NOT EXISTS membership_changes (
    id INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    relationship TEXT NOT NULL CHECK (relationship IN ('followers', 'following')),
    change TEXT NOT NULL CHECK (change IN ('added', 'removed')),
    UNIQUE (collection_id, user_id, relationship, change)
);
CREATE INDEX IF NOT EXISTS collections_profile_captured
    ON collections(profile_id, captured_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS changes_collection
    ON membership_changes(collection_id, relationship, change);
"""


def synchronized(method):
    def wrapper(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return wrapper


class Database:
    def __init__(self, path=DEFAULT_DB_PATH):
        self.path = path
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.RLock()
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        for column in ("followers_header_total", "following_header_total"):
            try:
                self.connection.execute(
                    f"ALTER TABLE collections ADD COLUMN {column} INTEGER"
                )
            except sqlite3.OperationalError:
                pass
        try:
            self.connection.execute(
                "ALTER TABLE collections ADD COLUMN complete INTEGER NOT NULL DEFAULT 1"
            )
        except sqlite3.OperationalError:
            pass
        try:
            self.connection.execute("ALTER TABLE users ADD COLUMN avatar_path TEXT")
        except sqlite3.OperationalError:
            pass
        self.connection.commit()

    def close(self):
        self.connection.close()

    @synchronized
    def save_collection(self, payload):
        profile = normalize_profile(payload.get("profile"))
        if not profile:
            raise ValueError("profile must be an Instagram username")
        followers = normalize_users(payload.get("followers"))
        following = normalize_users(payload.get("following"))
        avatar_urls = payload.get("avatars") or {}
        if not isinstance(avatar_urls, dict):
            avatar_urls = {}
        refresh_avatars = bool(payload.get("refreshAvatars") or payload.get("refresh_avatars"))
        header_totals = payload.get("headerTotals") or {}
        followers_header_total = header_totals.get("followers")
        following_header_total = header_totals.get("following")
        complete = 1 if payload.get("complete", True) else 0
        captured_at = datetime.now(timezone.utc).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        db = self.connection
        try:
            db.execute("BEGIN")
            db.execute(
                "INSERT OR IGNORE INTO profiles(username, created_at) VALUES (?, ?)",
                (profile, now),
            )
            profile_id = db.execute(
                "SELECT id FROM profiles WHERE username = ?", (profile,)
            ).fetchone()["id"]
            previous_collection = db.execute(
                """SELECT id FROM collections WHERE profile_id = ?
                   AND complete = 1 ORDER BY captured_at DESC, id DESC LIMIT 1""",
                (profile_id,),
            ).fetchone()
            previous = db.execute(
                """SELECT cm.relationship, u.username FROM collection_memberships cm
                   JOIN users u ON u.id = cm.user_id
                   JOIN collections c ON c.id = cm.collection_id
                   WHERE c.profile_id = ?
                   AND c.id = ?""",
                (profile_id, previous_collection["id"] if previous_collection else -1),
            ).fetchall()
            previous_sets = {"followers": set(), "following": set()}
            for row in previous:
                previous_sets[row["relationship"]].add(row["username"])
            current_sets = {"followers": set(followers), "following": set(following)}
            db.execute(
                """INSERT INTO collections
                   (profile_id, captured_at, created_at, followers_header_total,
                    following_header_total, complete) VALUES (?, ?, ?, ?, ?, ?)""",
                (profile_id, captured_at, now, followers_header_total, following_header_total, complete),
            )
            collection_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            changes = {"followers": {"added": [], "removed": []},
                       "following": {"added": [], "removed": []}}
            for relationship, usernames in current_sets.items():
                for username in sorted(usernames):
                    db.execute("INSERT OR IGNORE INTO users(username) VALUES (?)", (username,))
                    user_id = db.execute(
                        "SELECT id FROM users WHERE username = ?", (username,)
                    ).fetchone()["id"]
                    db.execute(
                        """INSERT INTO collection_memberships(collection_id, user_id, relationship)
                           VALUES (?, ?, ?)""",
                        (collection_id, user_id, relationship),
                    )
                differences = (
                    (("added", usernames - previous_sets[relationship]),
                     ("removed", previous_sets[relationship] - usernames))
                    if previous_collection else ()
                )
                for change, usernames in differences:
                    changes[relationship][change] = sorted(usernames)
                    for username in changes[relationship][change]:
                        db.execute("INSERT OR IGNORE INTO users(username) VALUES (?)", (username,))
                        user_id = db.execute(
                            "SELECT id FROM users WHERE username = ?", (username,)
                        ).fetchone()["id"]
                        db.execute(
                            """INSERT INTO membership_changes
                               (collection_id, user_id, relationship, change)
                               VALUES (?, ?, ?, ?)""",
                            (collection_id, user_id, relationship, change),
                        )
            # Avatar downloads are best effort: a bad/private image must not
            # discard an otherwise valid collection.
            all_usernames = set(followers) | set(following)
            for username in sorted(all_usernames):
                url = avatar_urls.get(username)
                if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                    continue
                path = self._avatar_path(username, url, refresh_avatars)
                if path:
                    db.execute("UPDATE users SET avatar_path = ? WHERE username = ?", (path, username))
            db.commit()
        except Exception:
            db.rollback()
            raise
        return self.get_collection(collection_id)

    def _avatar_path(self, username, source_url, refresh=False):
        row = self.connection.execute(
            "SELECT image_path, source_url FROM avatar_cache WHERE username = ?", (username,)
        ).fetchone()
        if row and not refresh and row["source_url"] == source_url and os.path.isfile(row["image_path"]):
            return row["image_path"]
        avatar_dir = os.path.join(os.path.dirname(os.path.abspath(self.path)), "avatars")
        os.makedirs(avatar_dir, exist_ok=True)
        destination = os.path.join(avatar_dir, f"{username}.jpg")
        try:
            request = urllib.request.Request(source_url, headers={"User-Agent": "IB-Circlio/1.0"})
            with urllib.request.urlopen(request, timeout=10) as response:
                content_type = response.headers.get_content_type()
                if content_type not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
                    return None
                data = response.read(5 * 1024 * 1024 + 1)
                if not data or len(data) > 5 * 1024 * 1024:
                    return None
            with open(destination, "wb") as image_file:
                image_file.write(data)
            now = datetime.now(timezone.utc).isoformat()
            self.connection.execute(
                """INSERT INTO avatar_cache(username, image_path, source_url, fetched_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(username) DO UPDATE SET image_path=excluded.image_path,
                   source_url=excluded.source_url, fetched_at=excluded.fetched_at""",
                (username, destination, source_url, now),
            )
            return destination
        except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError):
            return None

    @synchronized
    def get_collection(self, collection_id):
        row = self.connection.execute(
            """SELECT c.id, p.username AS profile, c.captured_at, c.complete,
                      c.followers_header_total, c.following_header_total
               FROM collections c JOIN profiles p ON p.id = c.profile_id
               WHERE c.id = ?""", (collection_id,)
        ).fetchone()
        if not row:
            return None
        result = {"id": row["id"], "profile": row["profile"],
                  "capturedAt": row["captured_at"],
                  "complete": bool(row["complete"]),
                  "headerTotals": {
                      "followers": row["followers_header_total"],
                      "following": row["following_header_total"]
                  },
                  "followers": [], "following": [],
                  "avatarPaths": {},
                  "changes": {"followers": {"added": [], "removed": []},
                              "following": {"added": [], "removed": []}}}
        memberships = self.connection.execute(
            """SELECT u.username, cm.relationship FROM collection_memberships cm
               JOIN users u ON u.id = cm.user_id WHERE cm.collection_id = ?
               ORDER BY u.username""", (collection_id,)
        )
        for membership in memberships:
            result[membership["relationship"]].append(membership["username"])
        avatars = self.connection.execute(
            """SELECT u.username, u.avatar_path FROM collection_memberships cm
               JOIN users u ON u.id = cm.user_id
               WHERE cm.collection_id = ? AND u.avatar_path IS NOT NULL""",
            (collection_id,),
        )
        for avatar in avatars:
            result["avatarPaths"][avatar["username"]] = avatar["avatar_path"]
        changes = self.connection.execute(
            """SELECT u.username, mc.relationship, mc.change FROM membership_changes mc
               JOIN users u ON u.id = mc.user_id WHERE mc.collection_id = ?
               ORDER BY u.username""", (collection_id,)
        )
        for change in changes:
            result["changes"][change["relationship"]][change["change"]].append(change["username"])
        return result

    @synchronized
    def history(self, profile, latest_only=False):
        profile = normalize_profile(profile)
        if not profile:
            raise ValueError("profile must be an Instagram username")
        rows = self.connection.execute(
            """SELECT c.id FROM collections c JOIN profiles p ON p.id = c.profile_id
               WHERE p.username = ? ORDER BY c.captured_at DESC, c.id DESC""", (profile,)
        ).fetchall()
        collections = [self.get_collection(row["id"]) for row in rows]
        return collections[:1] if latest_only else collections


class Handler(BaseHTTPRequestHandler):
    db = None
    token = None

    def log_message(self, *_args):
        pass

    def origin_allowed(self):
        origin = self.headers.get("Origin")
        return not origin or EXTENSION_ORIGIN_RE.fullmatch(origin)

    def authorized(self):
        value = self.headers.get("Authorization", "")
        expected = f"Bearer {self.token}"
        return bool(self.token) and hmac.compare_digest(value, expected)

    def require_access(self):
        if not self.origin_allowed():
            self.send_json(403, {"ok": False, "error": "Origin not allowed"})
            return False
        if not self.authorized():
            self.send_json(401, {"ok": False, "error": "Authentication required"})
            return False
        return True

    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        origin = self.headers.get("Origin")
        if origin and EXTENSION_ORIGIN_RE.fullmatch(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        if not self.origin_allowed():
            return self.send_json(403, {"ok": False, "error": "Origin not allowed"})
        self.send_json(204, {})

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/health":
                return self.send_json(200, {"ok": True, "service": "instagram-exporter"})
            if not self.require_access():
                return
            if parsed.path in ("/api/collections/latest", "/api/collections/history"):
                profile = query.get("profile", [None])[0]
                collections = self.db.history(profile, parsed.path.endswith("/latest"))
                if parsed.path.endswith("/latest"):
                    return self.send_json(200, {"ok": True, "collection": collections[0] if collections else None})
                return self.send_json(200, {"ok": True, "collections": collections})
            self.send_json(404, {"ok": False, "error": "Not found"})
        except ValueError as error:
            self.send_json(400, {"ok": False, "error": str(error)})
        except Exception:
            self.send_json(500, {"ok": False, "error": "Internal server error"})

    def do_POST(self):
        if not self.require_access():
            return
        request_path = urlparse(self.path).path
        if request_path != "/api/collections":
            return self.send_json(404, {"ok": False, "error": "Not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("request body is too large")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            collection = self.db.save_collection(payload)
            self.send_json(201, {"ok": True, "collection": collection})
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self.send_json(400, {"ok": False, "error": str(error)})
        except Exception:
            self.send_json(500, {"ok": False, "error": "Internal server error"})

    def do_DELETE(self):
        if not self.require_access():
            return
        parsed = urlparse(self.path)
        if parsed.path != "/api/collections":
            return self.send_json(404, {"ok": False, "error": "Not found"})
        profile = parse_qs(parsed.query).get("profile", [None])[0]
        try:
            profile = normalize_profile(profile)
            if not profile:
                raise ValueError("profile must be an Instagram username")
            with self.db.lock:
                row = self.db.connection.execute(
                    "SELECT id FROM profiles WHERE username = ?", (profile,)
                ).fetchone()
                if row:
                    self.db.connection.execute("DELETE FROM profiles WHERE id = ?", (row["id"],))
                    self.db.connection.commit()
            self.send_json(200, {"ok": True, "profile": profile})
        except ValueError as error:
            self.send_json(400, {"ok": False, "error": str(error)})
        except Exception:
            self.send_json(500, {"ok": False, "error": "Internal server error"})


def main():
    parser = argparse.ArgumentParser(description="Instagram Exporter local SQLite backend")
    parser.add_argument("--port", type=int, default=int(os.environ.get("INSTAGRAM_PORT", "8765")))
    parser.add_argument("--db", default=os.environ.get("INSTAGRAM_DB", DEFAULT_DB_PATH))
    parser.add_argument("--token", default=os.environ.get("INSTAGRAM_EXPORTER_TOKEN"))
    parser.add_argument("--token-file")
    args = parser.parse_args()
    token = args.token
    if args.token_file:
        try:
            with open(args.token_file, "r", encoding="utf-8") as token_file:
                token = token_file.read().strip()
        except OSError as error:
            parser.error(f"cannot read token file: {error}")
    if not token or len(token) < 32 or any(character.isspace() for character in token):
        parser.error("a random token of at least 32 characters is required")
    os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    database = Database(args.db)
    Handler.db = database
    Handler.token = token
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Instagram Exporter backend listening on http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        database.close()


if __name__ == "__main__":
    main()

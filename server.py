"""Local SQLite backend for the Instagram Exporter extension."""

import argparse
import hashlib
import hmac
import ipaddress
import io
import json
import os
import re
import sqlite3
import socket
import threading
import urllib.error
import urllib.request
from urllib.request import HTTPRedirectHandler
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from PIL import Image

import database as database_access
import encryption
from data_paths import default_data_dir
from version import VERSION


PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA_DIR = str(default_data_dir())
DEFAULT_DB_PATH = os.path.join(DEFAULT_DATA_DIR, "instagram.db")
USERNAME_RE = re.compile(r"^[a-z0-9._]{1,30}$")
MAX_REQUEST_BYTES = 25 * 1024 * 1024
EXTENSION_ORIGIN_RE = re.compile(r"^chrome-extension://[a-p]{32}$")
FIREFOX_ORIGIN_RE = re.compile(
    r"^moz-extension://[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
AVATAR_HOST_SUFFIXES = (".cdninstagram.com", ".fbcdn.net")
MAX_AVATAR_BYTES = 5 * 1024 * 1024
MAX_AVATAR_PIXELS = 20_000_000


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
    result = set()
    for value in values:
        user = normalize_username(value)
        if user is None:
            raise ValueError("followers and following must contain valid usernames")
        result.add(user)
    return sorted(result)


def validate_avatar_url(url):
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or not hostname.endswith(AVATAR_HOST_SUFFIXES)
    ):
        raise ValueError("Avatar URL is not from a permitted HTTPS CDN.")
    try:
        addresses = socket.getaddrinfo(
            hostname, 443, type=socket.SOCK_STREAM
        )
    except OSError as error:
        raise ValueError("Avatar CDN hostname could not be resolved.") from error
    if not addresses:
        raise ValueError("Avatar CDN hostname has no addresses.")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0].split("%", 1)[0])
        if not ip.is_global:
            raise ValueError("Avatar CDN resolved to a non-public IP address.")
    return parsed


class SafeAvatarRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        validate_avatar_url(new_url)
        return super().redirect_request(
            request, file_pointer, code, message, headers, new_url
        )


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
CREATE TABLE IF NOT EXISTS collection_avatar_versions (
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    image_path TEXT NOT NULL,
    source_url TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (collection_id, user_id)
);
CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    captured_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    complete INTEGER NOT NULL DEFAULT 1,
    followers_header_text TEXT,
    following_header_text TEXT
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
        self.connection = database_access.open_database(path)
        self.lock = threading.RLock()
        self.connection.executescript(SCHEMA)
        for column in (
            "followers_header_total", "following_header_total",
            "followers_header_text", "following_header_text",
        ):
            try:
                self.connection.execute(
                    f"ALTER TABLE collections ADD COLUMN {column} INTEGER"
                )
            except database_access.SQLITE_OPERATIONAL_ERRORS:
                pass
        try:
            self.connection.execute(
                "ALTER TABLE collections ADD COLUMN complete INTEGER NOT NULL DEFAULT 1"
            )
        except database_access.SQLITE_OPERATIONAL_ERRORS:
            pass
        try:
            self.connection.execute("ALTER TABLE users ADD COLUMN avatar_path TEXT")
        except database_access.SQLITE_OPERATIONAL_ERRORS:
            pass
        self.connection.commit()

    def close(self):
        self.connection.close()

    @synchronized
    def save_collection(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("collection body must be a JSON object")
        profile = normalize_profile(payload.get("profile"))
        if not profile:
            raise ValueError("profile must be an Instagram username")
        followers = normalize_users(payload.get("followers"))
        following = normalize_users(payload.get("following"))
        avatar_urls = payload.get("avatars", {})
        if avatar_urls is None:
            avatar_urls = {}
        if not isinstance(avatar_urls, dict):
            raise ValueError("avatars must be an object")
        for username, url in avatar_urls.items():
            if (
                normalize_username(username) != username
                or not isinstance(url, str)
                or len(url) > 2048
            ):
                raise ValueError("avatars must map valid usernames to URL strings")
        refresh_avatars = payload.get(
            "refreshAvatars", payload.get("refresh_avatars", False)
        )
        if not isinstance(refresh_avatars, bool):
            raise ValueError("refreshAvatars must be true or false")
        header_totals = payload.get("headerTotals", {})
        if header_totals is None:
            header_totals = {}
        if not isinstance(header_totals, dict):
            raise ValueError("headerTotals must be an object")
        followers_header_total = header_totals.get("followers")
        following_header_total = header_totals.get("following")
        for total in (followers_header_total, following_header_total):
            if total is not None and (
                isinstance(total, bool)
                or not isinstance(total, int)
                or total < 0
                or total > 2_147_483_647
            ):
                raise ValueError("header totals must be non-negative integers")
        header_labels = payload.get("headerTotalLabels", {})
        if header_labels is None:
            header_labels = {}
        if not isinstance(header_labels, dict):
            raise ValueError("headerTotalLabels must be an object")
        followers_header_text = header_labels.get("followers")
        following_header_text = header_labels.get("following")
        for label in (followers_header_text, following_header_text):
            if label is not None and (
                not isinstance(label, str) or len(label) > 300
            ):
                raise ValueError("header total labels must be strings of at most 300 characters")
        complete = payload.get("complete", True)
        if not isinstance(complete, bool):
            raise ValueError("complete must be true or false")
        complete = int(complete)
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
                    following_header_total, complete, followers_header_text,
                    following_header_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    profile_id, captured_at, now, followers_header_total,
                    following_header_total, complete, followers_header_text,
                    following_header_text,
                ),
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
                if not isinstance(url, str):
                    continue
                user_id = db.execute(
                    "SELECT id FROM users WHERE username = ?", (username,)
                ).fetchone()["id"]
                path, fetched_at = self._avatar_path(
                    username, url, refresh_avatars, collection_id
                )
                if path:
                    db.execute("UPDATE users SET avatar_path = ? WHERE username = ?", (path, username))
                    db.execute(
                        """INSERT OR REPLACE INTO collection_avatar_versions
                           (collection_id, user_id, image_path, source_url, fetched_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        (collection_id, user_id, path, url, fetched_at),
                    )
            db.commit()
        except Exception:
            db.rollback()
            raise
        return self.get_collection(collection_id)

    def _avatar_path(self, username, source_url, refresh=False, collection_id=None):
        try:
            validate_avatar_url(source_url)
        except (OSError, ValueError):
            return None, None
        avatar_dir = os.path.join(os.path.dirname(os.path.abspath(self.path)), "avatars")
        os.makedirs(avatar_dir, exist_ok=True)
        row = self.connection.execute(
            "SELECT image_path, source_url FROM avatar_cache WHERE username = ?", (username,)
        ).fetchone()
        if row and not refresh and row["source_url"] == source_url:
            cache_path = os.path.realpath(row["image_path"])
            try:
                cache_path_is_local = (
                    os.path.commonpath((os.path.realpath(avatar_dir), cache_path))
                    == os.path.realpath(avatar_dir)
                )
            except ValueError:
                cache_path_is_local = False
            if cache_path_is_local and os.path.isfile(cache_path):
                fetched = self.connection.execute(
                    "SELECT fetched_at FROM avatar_cache WHERE username = ?", (username,)
                ).fetchone()["fetched_at"]
                return row["image_path"], fetched
        version = collection_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        try:
            request = urllib.request.Request(source_url, headers={"User-Agent": "IB-Circlio/1.0"})
            opener = urllib.request.build_opener(SafeAvatarRedirectHandler())
            with opener.open(request, timeout=10) as response:
                validate_avatar_url(response.geturl())
                content_type = response.headers.get_content_type()
                if content_type not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
                    return None, None
                data = response.read(MAX_AVATAR_BYTES + 1)
                if not data or len(data) > MAX_AVATAR_BYTES:
                    return None, None
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > MAX_AVATAR_PIXELS:
                    raise ValueError("Avatar image dimensions are too large.")
                image.verify()
                suffix = {
                    "JPEG": ".jpg",
                    "PNG": ".png",
                    "WEBP": ".webp",
                    "GIF": ".gif",
                }.get(image.format)
            if not suffix:
                return None, None
            digest = hashlib.sha256(
                f"{username}:{source_url}:{version}".encode("utf-8")
            ).hexdigest()[:32]
            destination = os.path.join(avatar_dir, f"{digest}{suffix}")
            data_dir = os.path.dirname(os.path.abspath(self.path))
            if encryption.is_encrypted(data_dir):
                data = encryption.encrypt_avatar(
                    data, encryption.load_master_key(data_dir)
                )
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
            return destination, now
        except (
            OSError,
            ValueError,
            Image.DecompressionBombError,
            urllib.error.URLError,
            urllib.error.HTTPError,
        ):
            return None, None

    @synchronized
    def get_collection(self, collection_id):
        row = self.connection.execute(
            """SELECT c.id, p.username AS profile, c.captured_at, c.complete,
                      c.followers_header_total, c.following_header_total,
                      c.followers_header_text, c.following_header_text
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
                  "headerTotalLabels": {
                      "followers": row["followers_header_text"],
                      "following": row["following_header_text"]
                  },
                  "followers": [], "following": [],
                  "avatarPaths": {}, "avatarVersions": {}, "avatarChanges": [],
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
            """SELECT u.username, cav.image_path, cav.source_url, cav.fetched_at
               FROM collection_avatar_versions cav
               JOIN users u ON u.id = cav.user_id
               WHERE cav.collection_id = ?""",
            (collection_id,),
        )
        for avatar in avatars:
            result["avatarPaths"][avatar["username"]] = avatar["image_path"]
            result["avatarVersions"][avatar["username"]] = {
                "imagePath": avatar["image_path"], "sourceUrl": avatar["source_url"],
                "fetchedAt": avatar["fetched_at"]
            }
        previous = self.connection.execute(
            """SELECT cav.source_url, u.username
               FROM collection_avatar_versions cav JOIN users u ON u.id = cav.user_id
               WHERE cav.collection_id = (
                 SELECT id FROM collections WHERE profile_id = (
                   SELECT profile_id FROM collections WHERE id = ?
                 ) AND complete = 1 AND id < ?
                 ORDER BY captured_at DESC, id DESC LIMIT 1
               )""",
            (collection_id, collection_id),
        ).fetchall()
        previous_urls = {row["username"]: row["source_url"] for row in previous}
        for username, version in result["avatarVersions"].items():
            if username in previous_urls and previous_urls[username] != version["sourceUrl"]:
                result["avatarChanges"].append({
                    "username": username, "from": previous_urls[username],
                    "to": version["sourceUrl"]
                })
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

    def host_allowed(self):
        port = self.server.server_port
        host = self.headers.get("Host", "")
        return host in (f"127.0.0.1:{port}", f"localhost:{port}")

    def origin_allowed(self):
        origin = self.headers.get("Origin")
        return (
            not origin
            or bool(EXTENSION_ORIGIN_RE.fullmatch(origin))
            or bool(FIREFOX_ORIGIN_RE.fullmatch(origin))
        )

    def authorized(self):
        value = self.headers.get("Authorization", "")
        expected = f"Bearer {self.token}"
        return bool(self.token) and hmac.compare_digest(value, expected)

    def require_access(self):
        if not self.host_allowed():
            self.send_json(403, {"ok": False, "error": "Host not allowed"})
            return False
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
        if origin and (
            EXTENSION_ORIGIN_RE.fullmatch(origin)
            or FIREFOX_ORIGIN_RE.fullmatch(origin)
        ):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        if not self.host_allowed():
            return self.send_json(403, {"ok": False, "error": "Host not allowed"})
        if not self.origin_allowed():
            return self.send_json(403, {"ok": False, "error": "Origin not allowed"})
        self.send_json(204, {})

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if not self.host_allowed():
                return self.send_json(403, {"ok": False, "error": "Host not allowed"})
            if not self.origin_allowed():
                return self.send_json(403, {"ok": False, "error": "Origin not allowed"})
            if parsed.path == "/api/health":
                return self.send_json(200, {"ok": True, "service": "instagram-exporter"})
            if parsed.path == "/version":
                if not self.require_access():
                    return
                return self.send_json(
                    200, {"ok": True, "version": VERSION}
                )
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
        except Exception as error:
            print(f"GET {parsed.path} failed: {error}", flush=True)
            self.send_json(500, {"ok": False, "error": "Internal server error"})

    def do_POST(self):
        if not self.require_access():
            return
        request_path = urlparse(self.path).path
        if request_path != "/api/collections":
            return self.send_json(404, {"ok": False, "error": "Not found"})
        try:
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("transfer-encoded request bodies are not supported")
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Content-Type must be application/json")
            length = int(self.headers.get("Content-Length", ""))
            if length < 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("request body is too large")
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError("request body length does not match Content-Length")
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            collection = self.db.save_collection(payload)
            self.send_json(201, {"ok": True, "collection": collection})
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as error:
            self.send_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            print(f"POST {request_path} failed: {error}", flush=True)
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
        except Exception as error:
            print(f"DELETE {parsed.path} failed: {error}", flush=True)
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
            token_path = os.path.abspath(args.token_file)
            if os.path.basename(token_path) == encryption.TOKEN_NAME:
                token = encryption.read_token(os.path.dirname(token_path))
            else:
                with open(token_path, "r", encoding="utf-8") as token_file:
                    token = token_file.read().strip()
        except (OSError, ValueError) as error:
            parser.error(f"cannot read token file: {error}")
    if not token or len(token) < 32 or any(character.isspace() for character in token):
        parser.error("a random token of at least 32 characters is required")
    os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    lock_handle = database_access.acquire_service_lock(
        os.path.dirname(os.path.abspath(args.db))
    )
    try:
        database = Database(args.db)
        recovery_key = database_access.take_pending_recovery_key(
            os.path.dirname(os.path.abspath(args.db))
        )
        if recovery_key:
            print(
                "IMPORTANT: Save this one-time IB Circlio recovery key outside this PC:\n"
                f"{recovery_key}\n"
                "It cannot be displayed again.",
                flush=True,
            )
        Handler.db = database
        Handler.token = token
        http_server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
        print(
            f"IB_Circlio collection service listening on http://127.0.0.1:{args.port}",
            flush=True,
        )
        try:
            http_server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            http_server.server_close()
            database.close()
    finally:
        database_access.release_service_lock(lock_handle)


if __name__ == "__main__":
    main()

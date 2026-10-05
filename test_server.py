import tempfile
import json
import io
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import launcher
import server
from PIL import Image
from version import VERSION


def png_bytes():
    output = io.BytesIO()
    Image.new("RGB", (1, 1), color=(20, 40, 60)).save(output, format="PNG")
    return output.getvalue()


class MockImageHeaders:
    def get_content_type(self):
        return "image/png"


class MockImageResponse:
    headers = MockImageHeaders()

    def __init__(self, data, url):
        self.data = data
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self.url

    def read(self, _size):
        return self.data


class OversizedImage:
    format = "PNG"
    width = 5000
    height = 5000

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def verify(self):
        raise AssertionError("Oversized images must be rejected before verification.")


class MockImageOpener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def open(self, _request, timeout):
        self.calls += 1
        if timeout != 10:
            raise AssertionError("Avatar requests must use a bounded timeout.")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class LocalConfigTests(unittest.TestCase):
    def test_local_config_reuses_token_and_uses_loopback_service(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            config_path = Path(directory) / "config.js"
            token_path = launcher.prepare_local_config(data_dir, config_path)
            token = token_path.read_text(encoding="utf-8").strip()
            self.assertGreaterEqual(len(token), 32)
            self.assertIn('API_BASE = "http://127.0.0.1:8765"', config_path.read_text(
                encoding="utf-8"
            ))
            self.assertEqual(
                launcher.prepare_local_config(data_dir, config_path), token_path
            )
            self.assertEqual(token_path.read_text(encoding="utf-8").strip(), token)

    def test_local_config_is_generated_for_each_packaged_browser_family(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "app"
            data_dir = Path(directory) / "data"
            for browser in ("chromium", "firefox"):
                (root / "extensions" / browser).mkdir(parents=True)
            token_path = launcher.prepare_extension_configs(data_dir, root)
            token = token_path.read_text(encoding="utf-8").strip()
            for browser in ("chromium", "firefox"):
                content = (
                    root / "extensions" / browser / "config.js"
                ).read_text(encoding="utf-8")
                self.assertIn(token, content)
                self.assertIn("http://127.0.0.1:8765", content)


class VersionEndpointTests(unittest.TestCase):
    def test_version_endpoint_returns_current_service_version(self):
        token = "test-token-" + ("x" * 32)
        old_token = server.Handler.token
        old_database = server.Handler.db
        server.Handler.token = token
        server.Handler.db = None
        http_server = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        thread.start()
        try:
            address, port = http_server.server_address
            request = urllib.request.Request(
                f"http://{address}:{port}/version",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(request, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(payload, {"ok": True, "version": VERSION})
        finally:
            http_server.shutdown()
            http_server.server_close()
            thread.join(timeout=2)
            server.Handler.token = old_token
            server.Handler.db = old_database


class AvatarCollectionTests(unittest.TestCase):
    def test_avatar_is_downloaded_once_when_in_both_lists(self):
        with tempfile.TemporaryDirectory() as directory:
            database = server.Database(str(Path(directory) / "instagram.db"))
            image_url = "https://scontent.cdninstagram.com/avatar"
            opener = MockImageOpener(MockImageResponse(png_bytes(), image_url))
            payload = {
                "profile": "owner",
                "followers": ["shared"],
                "following": ["shared"],
                "avatars": {"shared": image_url},
                "headerTotalLabels": {
                    "followers": "438 followers",
                    "following": "849 following",
                },
            }
            with patch("server.validate_avatar_url"), patch(
                "server.urllib.request.build_opener", return_value=opener
            ):
                collection = database.save_collection(payload)
                self.assertEqual(opener.calls, 1)
            self.assertEqual(len(list((Path(directory) / "avatars").glob("*"))), 1)
            first_path = collection["avatarPaths"]["shared"]
            self.assertRegex(Path(first_path).name, r"^[0-9a-f]{32}\.png$")
            database.save_collection(payload)
            self.assertEqual(opener.calls, 1)
            second = database.get_collection(collection["id"] + 1)
            self.assertEqual(second["avatarPaths"]["shared"], first_path)
            self.assertEqual(
                second["headerTotalLabels"]["following"], "849 following"
            )
            database.close()

    def test_changed_avatar_url_creates_version_and_reports_change(self):
        with tempfile.TemporaryDirectory() as directory:
            database = server.Database(str(Path(directory) / "instagram.db"))
            first_url = "https://scontent.cdninstagram.com/one"
            second_url = "https://scontent.cdninstagram.com/two"
            opener = MockImageOpener(
                MockImageResponse(png_bytes(), first_url),
                MockImageResponse(png_bytes(), second_url),
            )
            with patch("server.validate_avatar_url"), patch(
                "server.urllib.request.build_opener", return_value=opener
            ):
                first = database.save_collection({
                    "profile": "owner", "followers": ["shared"], "following": [],
                    "avatars": {"shared": first_url},
                })
                second = database.save_collection({
                    "profile": "owner", "followers": ["shared"], "following": [],
                    "avatars": {"shared": second_url},
                })
            self.assertEqual(opener.calls, 2)
            self.assertNotEqual(first["avatarPaths"]["shared"], second["avatarPaths"]["shared"])
            self.assertEqual(len(list((Path(directory) / "avatars").glob("*"))), 2)
            self.assertEqual(second["avatarChanges"][0]["username"], "shared")
            self.assertEqual(second["avatarChanges"][0]["from"], first_url)
            database.close()

    def test_avatar_failure_does_not_fail_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            database = server.Database(str(Path(directory) / "instagram.db"))
            with patch(
                "server.validate_avatar_url",
                side_effect=ValueError("untrusted URL"),
            ):
                collection = database.save_collection({
                    "profile": "owner",
                    "followers": ["private_account"],
                    "following": [],
                    "avatars": {
                        "private_account": "https://scontent.cdninstagram.com/avatar"
                    },
                })
            self.assertEqual(collection["followers"], ["private_account"])
            self.assertEqual(collection["avatarPaths"], {})
            database.close()

    def test_oversized_avatar_dimensions_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            database = server.Database(str(Path(directory) / "instagram.db"))
            image_url = "https://scontent.cdninstagram.com/avatar"
            opener = MockImageOpener(MockImageResponse(png_bytes(), image_url))
            with patch("server.validate_avatar_url"), patch(
                "server.urllib.request.build_opener", return_value=opener
            ), patch("server.Image.open", return_value=OversizedImage()):
                collection = database.save_collection({
                    "profile": "owner",
                    "followers": ["oversized_avatar"],
                    "following": [],
                    "avatars": {"oversized_avatar": image_url},
                })
            self.assertEqual(collection["avatarPaths"], {})
            self.assertEqual(list((Path(directory) / "avatars").glob("*")), [])
            database.close()


class AvatarUrlValidationTests(unittest.TestCase):
    def test_only_public_https_instagram_cdn_urls_are_accepted(self):
        with self.assertRaises(ValueError):
            server.validate_avatar_url("http://scontent.cdninstagram.com/image")
        with self.assertRaises(ValueError):
            server.validate_avatar_url("https://example.test/image")
        with patch(
            "server.socket.getaddrinfo",
            return_value=[(None, None, None, None, ("127.0.0.1", 443))],
        ):
            with self.assertRaisesRegex(ValueError, "non-public"):
                server.validate_avatar_url(
                    "https://scontent.cdninstagram.com/image"
                )
        with patch(
            "server.socket.getaddrinfo",
            return_value=[(None, None, None, None, ("93.184.216.34", 443))],
        ):
            self.assertEqual(
                server.validate_avatar_url(
                    "https://scontent.cdninstagram.com/image"
                ).hostname,
                "scontent.cdninstagram.com",
            )

    def test_redirects_to_untrusted_hosts_are_rejected(self):
        redirect = server.SafeAvatarRedirectHandler()
        with self.assertRaises(ValueError):
            redirect.redirect_request(
                urllib.request.Request(
                    "https://scontent.cdninstagram.com/image"
                ),
                None,
                302,
                "Found",
                {},
                "http://127.0.0.1/private",
            )


class ServiceAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.token = "service-test-" + ("x" * 32)
        cls.old_token = server.Handler.token
        cls.old_database = server.Handler.db
        server.Handler.token = cls.token
        cls.database_directory = tempfile.TemporaryDirectory()
        cls.database = server.Database(
            str(Path(cls.database_directory.name) / "service.db")
        )
        server.Handler.db = cls.database
        cls.http_server = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.http_server.server_address[1]
        cls.thread = threading.Thread(
            target=cls.http_server.serve_forever, daemon=True
        )
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http_server.shutdown()
        cls.http_server.server_close()
        cls.thread.join(timeout=2)
        cls.database.close()
        cls.database_directory.cleanup()
        server.Handler.token = cls.old_token
        server.Handler.db = cls.old_database

    def request(self, method="GET", token=None, origin=None, host=None, body=None):
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if origin is not None:
            headers["Origin"] = origin
        if host is not None:
            headers["Host"] = host
        data = None if body is None else json.dumps(body).encode("utf-8")
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/version",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def test_missing_and_wrong_tokens_are_rejected(self):
        self.assertEqual(self.request()[0], 401)
        self.assertEqual(self.request(token="wrong-token")[0], 401)

    def test_host_header_is_exactly_limited_to_local_service(self):
        self.assertEqual(
            self.request(token=self.token, host=f"attacker.example:{self.port}")[0],
            403,
        )

    def test_origin_header_must_match_supported_extension_origin(self):
        valid_chrome = "chrome-extension://" + ("a" * 32)
        valid_firefox = "moz-extension://12345678-1234-1234-1234-123456789abc"
        self.assertEqual(
            self.request(token=self.token, origin=valid_chrome)[0], 200
        )
        self.assertEqual(
            self.request(token=self.token, origin=valid_firefox)[0], 200
        )
        self.assertEqual(
            self.request(token=self.token, origin="https://evil.example")[0], 403
        )

    def test_collection_input_rejects_invalid_usernames(self):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        body = json.dumps({
            "profile": "owner",
            "followers": ["not a username"],
            "following": [],
        }).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/collections",
            data=body,
            headers=headers,
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 400)


if __name__ == "__main__":
    unittest.main()

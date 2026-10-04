import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import launcher
import server


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


class AvatarCollectionTests(unittest.TestCase):
    def test_avatar_is_downloaded_once_when_in_both_lists(self):
        with tempfile.TemporaryDirectory() as directory:
            database = server.Database(str(Path(directory) / "instagram.db"))

            class Headers:
                def get_content_type(self):
                    return "image/jpeg"

            class Response:
                headers = Headers()

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def read(self, _size):
                    return b"avatar"

            payload = {
                "profile": "owner",
                "followers": ["shared"],
                "following": ["shared"],
                "avatars": {"shared": "https://example.test/avatar"},
                "headerTotalLabels": {
                    "followers": "438 followers",
                    "following": "849 following",
                },
            }
            with patch("server.urllib.request.urlopen", return_value=Response()) as download:
                collection = database.save_collection(payload)
                self.assertEqual(download.call_count, 1)
            self.assertEqual(len(list((Path(directory) / "avatars").glob("*"))), 1)
            first_path = collection["avatarPaths"]["shared"]
            self.assertIn("shared_", Path(first_path).name)
            database.save_collection(payload)
            self.assertEqual(download.call_count, 1)
            second = database.get_collection(collection["id"] + 1)
            self.assertEqual(second["avatarPaths"]["shared"], first_path)
            self.assertEqual(
                second["headerTotalLabels"]["following"], "849 following"
            )
            database.close()

    def test_changed_avatar_url_creates_version_and_reports_change(self):
        with tempfile.TemporaryDirectory() as directory:
            database = server.Database(str(Path(directory) / "instagram.db"))

            class Headers:
                def get_content_type(self):
                    return "image/jpeg"

            class Response:
                headers = Headers()
                def __init__(self, data):
                    self.data = data
                def __enter__(self):
                    return self
                def __exit__(self, *_args):
                    return False
                def read(self, _size):
                    return self.data

            with patch("server.urllib.request.urlopen",
                       side_effect=[Response(b"one"), Response(b"two")]) as download:
                first = database.save_collection({
                    "profile": "owner", "followers": ["shared"], "following": [],
                    "avatars": {"shared": "https://example.test/one"},
                })
                second = database.save_collection({
                    "profile": "owner", "followers": ["shared"], "following": [],
                    "avatars": {"shared": "https://example.test/two"},
                })
            self.assertEqual(download.call_count, 2)
            self.assertNotEqual(first["avatarPaths"]["shared"], second["avatarPaths"]["shared"])
            self.assertEqual(len(list((Path(directory) / "avatars").glob("*"))), 2)
            self.assertEqual(second["avatarChanges"][0]["username"], "shared")
            self.assertEqual(second["avatarChanges"][0]["from"], "https://example.test/one")
            database.close()

    def test_avatar_failure_does_not_fail_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            database = server.Database(str(Path(directory) / "instagram.db"))
            with patch("server.urllib.request.urlopen", side_effect=OSError("offline")):
                collection = database.save_collection({
                    "profile": "owner",
                    "followers": ["private_account"],
                    "following": [],
                    "avatars": {"private_account": "https://example.test/avatar"},
                })
            self.assertEqual(collection["followers"], ["private_account"])
            self.assertEqual(collection["avatarPaths"], {})
            database.close()


if __name__ == "__main__":
    unittest.main()

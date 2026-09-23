import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


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
            }
            with patch("server.urllib.request.urlopen", return_value=Response()) as download:
                collection = database.save_collection(payload)
                self.assertEqual(download.call_count, 1)
            self.assertEqual(len(list((Path(directory) / "avatars").glob("*"))), 1)
            self.assertEqual(collection["avatarPaths"]["shared"].endswith("shared.jpg"), True)
            database.save_collection(payload)
            self.assertEqual(download.call_count, 1)
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

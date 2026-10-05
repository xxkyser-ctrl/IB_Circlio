import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import build_release
from version import VERSION


class BrowserPackageBuildTests(unittest.TestCase):
    def test_browser_families_receive_correct_manifest_and_no_local_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for manifest_name, family in (
                ("manifest.json", "chromium"),
                ("manifest.firefox.json", "firefox"),
            ):
                extension = build_release.build_extension_directory(
                    manifest_name, family, output_root=root
                )
                manifest = json.loads(
                    (extension / "manifest.json").read_text(encoding="utf-8")
                )
                self.assertEqual(manifest["version"], VERSION)
                self.assertTrue((extension / "LICENSE").is_file())
                self.assertFalse((extension / "config.js").exists())

                archive_path = build_release.zip_extension_directory(
                    extension, family, release_root=root
                )
                with zipfile.ZipFile(archive_path) as archive:
                    self.assertIn("manifest.json", archive.namelist())
                    self.assertIn("LICENSE", archive.namelist())
                    self.assertNotIn("config.js", archive.namelist())
                    self.assertNotIn("server-token.txt", archive.namelist())

    def test_windows_package_rejects_local_config_and_token_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "application"
            package.mkdir()
            (package / "config.js").write_text("secret", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "secret file"):
                build_release.zip_windows_release(
                    release_dir=package,
                    release_root=root,
                    version="1.0.0",
                )


if __name__ == "__main__":
    unittest.main()

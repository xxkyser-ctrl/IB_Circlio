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
                self.assertTrue((extension / "THIRD_PARTY_NOTICES.md").is_file())
                self.assertFalse((extension / "config.js").exists())

                archive_path = build_release.zip_extension_directory(
                    extension, family, release_root=root
                )
                with zipfile.ZipFile(archive_path) as archive:
                    self.assertIn("manifest.json", archive.namelist())
                    self.assertIn("LICENSE", archive.namelist())
                    self.assertIn("THIRD_PARTY_NOTICES.md", archive.namelist())
                    self.assertNotIn("config.js", archive.namelist())
                    self.assertNotIn("server-token.txt", archive.namelist())

    def test_packages_reject_local_data_and_secret_files(self):
        for filename in (
            "config.js",
            "server-token.enc",
            "encryption.json",
            "master-key.dpapi",
            "instagram.db",
            "fixture.xlsx",
        ):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                package = root / "application"
                package.mkdir()
                (package / filename).write_text("fixture secret", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "local data or secret"):
                    build_release.zip_windows_release(
                        release_dir=package,
                        release_root=root,
                        version="1.0.0",
                    )

    def test_all_in_one_windows_zip_has_files_at_root_and_both_extensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "application"
            (package / "extensions" / "chromium").mkdir(parents=True)
            (package / "extensions" / "firefox").mkdir(parents=True)
            (package / "icons").mkdir()
            (package / "ib_circlio.exe").write_bytes(b"app")
            (package / "INSTALL.txt").write_text("quick start", encoding="utf-8")
            (package / "icons" / "ib-circlio.ico").write_bytes(b"icon")
            (package / "icons" / "ib-128.png").write_bytes(b"png")
            (package / "extensions" / "chromium" / "manifest.json").write_text(
                "{}", encoding="utf-8"
            )
            (package / "extensions" / "firefox" / "manifest.json").write_text(
                "{}", encoding="utf-8"
            )

            archive_path = build_release.zip_windows_release(
                release_dir=package, release_root=root, version="1.0.0"
            )

            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())
            self.assertIn("ib_circlio.exe", names)
            self.assertIn("INSTALL.txt", names)
            self.assertIn("extensions/chromium/manifest.json", names)
            self.assertIn("extensions/firefox/manifest.json", names)
            self.assertIn("icons/ib-circlio.ico", names)
            self.assertIn("icons/ib-128.png", names)
            self.assertFalse(any(name.startswith("application/") for name in names))


if __name__ == "__main__":
    unittest.main()

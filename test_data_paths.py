import tempfile
import unittest
from pathlib import Path

from data_paths import default_data_dir


class DefaultDataDirectoryTests(unittest.TestCase):
    def test_new_install_uses_local_app_data(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            local = Path(directory) / "local"
            result = default_data_dir(
                {"LOCALAPPDATA": str(local)},
                home=home,
            )
            self.assertEqual(result, local / "IB Circlio")

    def test_existing_legacy_folder_is_preserved_until_new_folder_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            local = Path(directory) / "local"
            legacy = home / "Desktop" / "Instagram Exporter Data"
            legacy.mkdir(parents=True)
            result = default_data_dir(
                {"LOCALAPPDATA": str(local)},
                home=home,
            )
            self.assertEqual(result, legacy)

            new_directory = local / "IB Circlio"
            new_directory.mkdir(parents=True)
            result = default_data_dir(
                {"LOCALAPPDATA": str(local)},
                home=home,
            )
            self.assertEqual(result, new_directory)

    def test_fallback_uses_appdata_local_when_environment_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            result = default_data_dir({}, home=home)
            self.assertEqual(result, home / "AppData" / "Local" / "IB Circlio")


if __name__ == "__main__":
    unittest.main()

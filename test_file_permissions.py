import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import file_permissions


class SecretFilePermissionTests(unittest.TestCase):
    def test_windows_permissions_disable_inheritance_and_grant_current_user(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secret.txt"
            path.write_text("secret", encoding="utf-8")
            with patch("file_permissions.os.name", "nt"), patch(
                "file_permissions.subprocess.run",
                side_effect=[
                    SimpleNamespace(stdout="DOMAIN\\user\n"),
                    SimpleNamespace(returncode=0, stderr="", stdout=""),
                ],
            ) as run:
                file_permissions.restrict_to_current_user(path)

        self.assertEqual(run.call_count, 2)
        self.assertEqual(
            run.call_args.args[0],
            [
                "icacls.exe",
                str(path),
                "/inheritance:r",
                "/grant:r",
                "DOMAIN\\user:(F)",
            ],
        )

    def test_windows_permission_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secret.txt"
            path.write_text("secret", encoding="utf-8")
            with patch("file_permissions.os.name", "nt"), patch(
                "file_permissions.subprocess.run",
                side_effect=[
                    SimpleNamespace(stdout="DOMAIN\\user\n"),
                    SimpleNamespace(returncode=1, stderr="Access denied", stdout=""),
                ],
            ):
                with self.assertRaisesRegex(OSError, "Access denied"):
                    file_permissions.restrict_to_current_user(path)


if __name__ == "__main__":
    unittest.main()

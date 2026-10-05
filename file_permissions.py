"""Restrict secret files to the current OS user where supported."""

import getpass
import os
import stat
import subprocess


def restrict_to_current_user(path):
    path = str(path)
    if os.name != "nt":
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        return

    identity = subprocess.run(
        ["whoami.exe"],
        check=True,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout.strip()
    if not identity:
        identity = getpass.getuser()
    result = subprocess.run(
        [
            "icacls.exe",
            path,
            "/inheritance:r",
            "/grant:r",
            f"{identity}:(F)",
        ],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise OSError(f"Could not restrict secret-file permissions: {detail}")

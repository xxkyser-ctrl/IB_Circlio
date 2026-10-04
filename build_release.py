"""Build the portable Windows executables used by the IB Circlio release."""

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VERSION = "1.0.5"
RELEASE_NAME = f"IB Circlio-{VERSION}-windows"
BUILD_DIR = ROOT / "build" / RELEASE_NAME
DIST_DIR = BUILD_DIR / "dist"
RELEASE_DIR = ROOT / "release" / RELEASE_NAME


def run_pyinstaller(script, name, windowed=False):
    interface_mode = "--windowed" if windowed else "--console"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        interface_mode,
        "--name",
        name,
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(BUILD_DIR / name),
        "--specpath",
        str(BUILD_DIR),
        str(ROOT / script),
    ]
    if windowed:
        command.extend(["--icon", str(ROOT / "icons" / "ib-circlio.ico")])
    subprocess.run(command, cwd=ROOT, check=True)


def main():
    if sys.platform != "win32":
        raise SystemExit("Portable release builds are supported on Windows only.")
    if RELEASE_DIR.exists():
        raise SystemExit(
            f"Release output already exists; move it aside before rebuilding: {RELEASE_DIR}"
        )
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    RELEASE_DIR.parent.mkdir(parents=True, exist_ok=True)
    DIST_DIR.mkdir(parents=True)
    RELEASE_DIR.mkdir(parents=True)
    for script, name in (
        ("server.py", "ib-circlio-server"),
        ("result_gui.py", "ib_circlio"),
        ("clear_database.py", "ib-circlio-clear-database"),
    ):
        run_pyinstaller(script, name, windowed=(script == "result_gui.py"))
        shutil.copy2(DIST_DIR / f"{name}.exe", RELEASE_DIR / f"{name}.exe")
    for filename in (
        "result_gui.py",
        "clear_database.bat",
        "README.md",
        "RELEASE_NOTES.md",
        "SECURITY.md",
        "manifest.json",
        "manifest.firefox.json",
        "background.js",
        "content.js",
        "popup.html",
        "popup.js",
        "config.template.js",
    ):
        shutil.copy2(ROOT / filename, RELEASE_DIR / filename)
    shutil.copytree(ROOT / "icons", RELEASE_DIR / "icons")
    if (ROOT / "docs" / "images").exists():
        shutil.copytree(ROOT / "docs" / "images", RELEASE_DIR / "docs" / "images")
    print(f"Portable release created at: {RELEASE_DIR}")


if __name__ == "__main__":
    main()

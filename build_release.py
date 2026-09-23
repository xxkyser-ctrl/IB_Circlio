"""Build the portable Windows executables used by the IB Circlio release."""

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BUILD_DIR = ROOT / "build"
DIST_DIR = ROOT / "dist"
RELEASE_DIR = ROOT / "release" / "IB Circlio-1.0.2-windows"


def run_pyinstaller(script, name):
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--console",
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
    subprocess.run(command, cwd=ROOT, check=True)


def main():
    if sys.platform != "win32":
        raise SystemExit("Portable release builds are supported on Windows only.")
    for path in (BUILD_DIR, DIST_DIR, RELEASE_DIR):
        if path.exists():
            shutil.rmtree(path)
    DIST_DIR.mkdir(parents=True)
    RELEASE_DIR.mkdir(parents=True)
    for script, name in (
        ("launcher.py", "ib-circlio-launcher"),
        ("server.py", "ib-circlio-server"),
        ("result.py", "ib-circlio-result"),
        ("clear_database.py", "ib-circlio-clear-database"),
    ):
        run_pyinstaller(script, name)
        shutil.copy2(DIST_DIR / f"{name}.exe", RELEASE_DIR / f"{name}.exe")
    for filename in (
        "run_server.bat",
        "result.bat",
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

"""Build the portable Windows executables used by the IB Circlio release."""

import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from version import VERSION


ROOT = Path(__file__).resolve().parent
RELEASE_NAME = f"IB Circlio-{VERSION}-windows"
BUILD_DIR = ROOT / "build" / RELEASE_NAME
DIST_DIR = BUILD_DIR / "dist"
RELEASE_DIR = ROOT / "release" / RELEASE_NAME
RELEASE_ROOT = ROOT / "release"


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


def build_extension_directory(manifest_name, family, output_root=RELEASE_DIR):
    extension_dir = output_root / "extensions" / family
    extension_dir.mkdir(parents=True)
    manifest = json.loads(
        (ROOT / manifest_name).read_text(encoding="utf-8")
    )
    manifest["version"] = VERSION
    (extension_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    for filename in ("background.js", "content.js", "popup.html", "popup.js"):
        shutil.copy2(ROOT / filename, extension_dir / filename)
    shutil.copytree(ROOT / "icons", extension_dir / "icons")
    shutil.copy2(ROOT / "LICENSE", extension_dir / "LICENSE")
    (extension_dir / "README.txt").write_text(
        "Extract this directory as extensions\\" + family + " inside the IB Circlio "
        "Windows package. Start ib_circlio.exe before loading the extension so the "
        "desktop app can generate the local, private config.js file. Load this "
        "directory as an unpacked browser extension. The package does not contain "
        "your token or collection data.\n",
        encoding="utf-8",
    )
    return extension_dir


def zip_extension_directory(
    extension_dir, family, release_root=RELEASE_ROOT, version=VERSION
):
    output = release_root / f"IB-Circlio-{version}-{family}-extension.zip"
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(extension_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(extension_dir))
    return output


def zip_windows_release(
    release_dir=RELEASE_DIR, release_root=RELEASE_ROOT, version=VERSION
):
    output = release_root / f"IB-Circlio-{version}-windows.zip"
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(release_dir.rglob("*")):
            if path.is_file():
                if path.name in ("config.js", "server-token.txt"):
                    raise ValueError(
                        f"Refusing to package local secret file: {path}"
                    )
                archive.write(path, path.relative_to(release_dir.parent))
    return output


def write_release_checksums(paths, release_root=RELEASE_ROOT):
    output = release_root / "SHA256SUMS.txt"
    lines = []
    for path in sorted(paths, key=lambda item: item.name.casefold()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    output.write_text("\n".join(lines) + "\n", encoding="ascii")
    return output


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
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "version.py",
        "release_check.py",
        "data_paths.py",
        "file_permissions.py",
        "launcher.py",
        "server.py",
        "result.py",
        "clear_database.py",
        "requirements-build.txt",
        "background.js",
        "content.js",
        "popup.html",
        "popup.js",
        "config.template.js",
    ):
        shutil.copy2(ROOT / filename, RELEASE_DIR / filename)
    for manifest_name in ("manifest.json", "manifest.firefox.json"):
        manifest = json.loads((ROOT / manifest_name).read_text(encoding="utf-8"))
        manifest["version"] = VERSION
        (RELEASE_DIR / manifest_name).write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
    shutil.copytree(ROOT / "icons", RELEASE_DIR / "icons")
    shutil.copytree(ROOT / "vendor", RELEASE_DIR / "vendor")
    if (ROOT / "docs" / "screenshots").exists():
        shutil.copytree(
            ROOT / "docs" / "screenshots", RELEASE_DIR / "docs" / "screenshots"
        )
    chromium_dir = build_extension_directory("manifest.json", "chromium")
    firefox_dir = build_extension_directory("manifest.firefox.json", "firefox")
    packages = [
        zip_windows_release(),
        zip_extension_directory(chromium_dir, "chromium"),
        zip_extension_directory(firefox_dir, "firefox"),
    ]
    write_release_checksums(packages)
    print(f"Portable release created at: {RELEASE_DIR}")
    print(f"Release ZIPs and checksums created in: {RELEASE_ROOT}")


if __name__ == "__main__":
    main()

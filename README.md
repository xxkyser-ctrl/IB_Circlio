# IB Circlio

**A local-first Windows app and browser extension for tracking Instagram follower and following lists over time.**

[![Latest release](https://img.shields.io/github/v/release/xxkyser-ctrl/IB_Circlio)](https://github.com/xxkyser-ctrl/IB_Circlio/releases/latest)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows-3766A3)](#installation)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

IB Circlio collects the Followers and Following lists visible on a profile in your signed-in browser, saves timestamped snapshots to your PC, and helps you review changes and export reports. Collection history stays on your PC; IB Circlio has no cloud account or hosted data service. `IB_Circlio` is the repository and project-site identifier.

[Project website](https://xxkyser-ctrl.github.io/IB_Circlio/) · [Latest release](https://github.com/xxkyser-ctrl/IB_Circlio/releases/latest) · [Source code](https://github.com/xxkyser-ctrl/IB_Circlio)

## What it does

- Collects Followers and Following from the profile lists rendered in Instagram, scrolling as the page loads more accounts.
- Saves complete and partial collections locally. A first complete collection is the baseline; later complete collections can show accounts added to or removed from either list.
- Lets you browse and search saved lists, compare any two complete snapshots, and review changes at the collection times they were observed.
- Shows overview totals, relationship categories, and best-effort profile-picture history when image URLs are available.
- Exports a formatted Excel workbook for a snapshot or the currently displayed rows of a report table.

Collection results reflect what Instagram exposed to the browser during that run. They are not a live feed, and cannot reveal changes from before the first snapshot or the exact time a relationship changed.

## Screenshots

These existing, privacy-redacted screenshots show example data.

| Desktop overview | Collection complete |
|---|---|
| ![IB Circlio desktop overview with saved collection totals](docs/screenshots/demo-overview.png) | ![IB Circlio browser extension after a collection](docs/screenshots/demo-finished.png) |

| Browse a saved list | Export a report |
|---|---|
| ![Browse and filter a saved Instagram list](docs/screenshots/demo-browse.png) | ![Choose a snapshot and export an Excel report](docs/screenshots/demo-export.png) |

## How it works

1. Start the IB Circlio desktop app and its local collection service.
2. Open an Instagram profile in a supported Chromium-based browser and start a collection from the extension.
3. The extension reads usernames and any available image URLs from the visible Followers and Following dialogs, then sends the results to the local service.
4. The service saves a timestamped snapshot and calculates changes against the previous complete snapshot for that profile.
5. Browse, compare, and export the saved data in the desktop reports.

The service listens on `127.0.0.1` and stores data on the same PC. The desktop application reads that local data to prepare reports.

![IB Circlio architecture: browser extension, local service, SQLite, and reports](docs/architecture.svg)

## Installation

IB Circlio is intended for Windows. The latest published portable package is available from [GitHub Releases](https://github.com/xxkyser-ctrl/IB_Circlio/releases/latest). The portable app does not require a separate Python installation. Supported browsers are Chrome, Edge, Brave, Opera, other Chromium-based browsers, and Firefox.

1. Download and extract the latest Windows ZIP.
2. Start `ib_circlio.exe`.
3. In the **Collection service** tab, select **Start service**.
4. In your browser, enable developer/developer mode and load the matching unpacked extension from `extensions\chromium` or `extensions\firefox`.
5. Open an Instagram profile, select the IB Circlio extension, and start a collection.
6. Return to the desktop app to browse, compare, and export your snapshots.

For Firefox, open `about:debugging`, choose **This Firefox**, select **Load Temporary Add-on...**, and choose `manifest.json` inside `extensions\firefox`. For Chrome, Edge, Brave, Opera, and other Chromium browsers, open the browser's extensions page, enable developer mode, and select **Load unpacked** for `extensions\chromium`.

The standalone browser-extension ZIPs are also published with releases. Extract one into the corresponding `extensions\<family>` directory inside the Windows app folder, then start the desktop app so it can generate the local extension configuration. Keep the extracted app and extension folders in place while installed. Reload an Instagram tab that was already open after installing or updating the extension. Stop the service when finished, or close the desktop app to stop the service it started.

The source checkout includes `manifest.firefox.json` for temporary Firefox add-on loading; release builds package it as `extensions\firefox\manifest.json`.

## Development

### Requirements

- Windows
- Git
- Python 3.13 or later
- Chrome, Edge, Brave, Opera, another Chromium browser, or Firefox

### Set up and run from source

```powershell
git clone https://github.com/xxkyser-ctrl/IB_Circlio.git
Set-Location IB_Circlio
py -3 -m pip install -r requirements-build.txt
.\ib_circlio.bat
```

Load the repository folder as an unpacked extension. Start the local service from the desktop app, then use the extension on Instagram.

To launch the report application directly with an explicit data directory:

```powershell
py -3 -B result_gui.py --data-dir "$env:LOCALAPPDATA\IB Circlio"
```

Run the tests and build the portable Windows package with:

```powershell
py -3 -B -m unittest -q
py -3 -B build_release.py
```

The build uses `requirements-build.txt` and writes version-specific output beneath `build/` and `release/`. The `-B` option prevents Python bytecode cache files from being written into the source tree.

### Project structure

| Path | Purpose |
|---|---|
| `background.js`, `content.js`, `popup.js`, `popup.html` | Browser extension collection workflow |
| `server.py`, `launcher.py` | Local API, SQLite persistence, and application startup |
| `result_gui.py`, `result.py` | Desktop reports, command-line reporting, and workbook export |
| `clear_database.py`, `clear_database.bat` | Confirmed local-data reset utility |
| `manifest.json` | Chromium extension manifest |
| `manifest.firefox.json` | Firefox manifest; see the compatibility note above |
| `release_check.py`, `version.py`, `data_paths.py`, `file_permissions.py` | Update checks, canonical version, local data paths, and secret-file permissions |
| `test_server.py`, `test_result.py`, `test_updates.py`, `test_data_paths.py`, `test_file_permissions.py`, `test_build_release.py` | Automated tests |
| `docs/` | Project website, architecture diagram, and existing screenshots |

## Local data and privacy

- By default, the SQLite database and archived profile pictures are stored under `%LOCALAPPDATA%\IB Circlio`. The database file is `instagram.db`; pictures are stored in an `avatars` subfolder. If the legacy `%USERPROFILE%\Desktop\Instagram Exporter Data` folder exists and the new folder does not, IB Circlio continues using the legacy folder. No data is moved or deleted automatically.
- The extension sends collection results to the service on `127.0.0.1:8765`. The app creates a local token for requests to protected service routes.
- Collection history is not sent to an IB Circlio cloud service. The browser still connects to Instagram, and the service may download profile pictures from URLs Instagram exposed in the page.
- Optional update checks are enabled by default in the desktop app and extension. Each checks GitHub's public release API at most once per day (plus manual checks) and sends no usernames, collection data, token, or other personal data. Turn the checks off independently using **Check for updates automatically** in the desktop app and **Check for updates** in the extension popup.
- The database, local token, archived pictures, and exported workbooks are not encrypted. Database encryption is planned but is not currently available as an option. Protect your Windows account and local backups.
- The extension stores only update-check preferences and release metadata in browser extension storage; follower/following lists and collection history remain in the local SQLite database.
- Do not publish `config.js`, `server-token.txt`, database files, exported workbooks, or screenshots containing real usernames.

For direct service or development workflows, `INSTAGRAM_DB` overrides the default database path and `INSTAGRAM_PORT` sets the port (default `8765`). Starting the service directly also requires a random `INSTAGRAM_EXPORTER_TOKEN` of at least 32 non-whitespace characters, or a `--token-file`. The desktop app creates and manages the local token and extension configuration for normal use. The direct `--data-dir` option remains available for the desktop app and reset utility.

## Clearing local data

The reset utility permanently deletes local collection history and archived pictures. Stop the service and close the desktop app first.

- Portable release: run `clear_database.bat` or `ib-circlio-clear-database.exe`.
- Source checkout: run `clear_database.bat`, or use the default location:

```powershell
py -3 -B clear_database.py
```

Use `--data-dir "<path>"` to reset a specific data directory. The utility requires a separate password and an explicit `CLEAR` confirmation before deleting data. Keep the password safe; it is stored locally as a salted PBKDF2 hash.

## Troubleshooting

- **The extension cannot connect:** start the **Collection service** in the desktop app, check that its status is **Running**, and confirm that the extension was loaded from the extracted folder.
- **A list does not open:** allow the profile to finish loading. If needed, open its Followers or Following list once manually, then try again.
- **A collection is partial or short:** Instagram loads list rows dynamically and may change its page behavior. Keep the tab open and retry later. Partial collections are saved but excluded from complete-snapshot comparisons.
- **No changes appear:** collect a second complete snapshot for the same profile. A first snapshot only establishes a baseline.
- **Pictures are missing:** image capture is best-effort and depends on Instagram exposing a usable, downloadable image URL during collection.
- **The service cannot start:** check the service log for errors, including whether another process is already using port `8765`.

## Security and limitations

The local service binds to loopback, validates the `Host` and browser `Origin`, and requires the local token for protected routes. Avatar downloads are limited to HTTPS Instagram/Facebook CDN hosts and verified image content. See [SECURITY.md](SECURITY.md) for the security model and release checklist.

IB Circlio only collects accounts visible to the signed-in browser and depends on Instagram's rendered page, loading behavior, and rate limits. It cannot infer historical changes before the first snapshot. Profile-picture capture is best-effort. Use the extension in accordance with Instagram's terms and applicable law.

IB Circlio is an independent project and is not affiliated with, endorsed by, or sponsored by Instagram or Meta.

## License

Released under the MIT License. See [LICENSE](LICENSE). Third-party software is listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Windows and browser-extension ZIPs are accompanied by `SHA256SUMS.txt`. After downloading, compare the file's SHA-256 digest with its line in the checksum file. For example:

```powershell
Get-FileHash .\IB-Circlio-1.0.7-windows.zip -Algorithm SHA256
```

The `VERSION` constant in `version.py` is the source version for the desktop app, local service, and portable build; the build synchronizes both packaged browser manifests to it.

## Links

- [Project website](https://xxkyser-ctrl.github.io/IB_Circlio/)
- [Source code and releases](https://github.com/xxkyser-ctrl/IB_Circlio)
- [Latest Windows download](https://github.com/xxkyser-ctrl/IB_Circlio/releases/latest)
- [Questions and bug reports](https://github.com/xxkyser-ctrl/IB_Circlio/issues)
- [Security policy](SECURITY.md)

Developed and maintained by [@xxkyser-ctrl](https://github.com/xxkyser-ctrl).

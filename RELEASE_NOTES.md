# IB Circlio v1.0.7

## What's new

- Add optional, once-daily release checks to the desktop app and browser extension, with release notes and a link to the official GitHub release. No files are downloaded or installed automatically.
- Add Firefox support alongside Chrome, Edge, Brave, Opera, and other Chromium browsers, with separate browser-family extension packages and a service-version warning.
- Harden the loopback service with exact local-host and extension-origin checks, token-required data routes, strict collection validation, bounded image downloads, and formula-safe workbook text.
- Store new installations under `%LOCALAPPDATA%\IB Circlio`, while continuing to use the legacy Desktop folder when it exists and the new folder does not.
- Add the MIT license, third-party notices, release checksums, pinned build dependencies, and GitHub Actions CI/release workflows.

## Important notes

- The database, local token, avatar archive, and exported workbooks remain unencrypted. Database encryption is planned but is not available in this release.
- Update checks are enabled by default and can be disabled separately in the desktop app and extension. They contact the public GitHub Releases API only and send no personal or collection data.

# IB_Circlio v1.0.6

## What's new

- Refresh report views automatically when a collection is saved while the local service is running. Manual **Refresh reports** remains available.
- Set a stable Windows app identity and use the IB_Circlio logo for the application window and taskbar.
- Replace the older documentation screenshots with updated, privacy-redacted collection and report screens.
- Keep the versioned portable release and browser extension manifests in sync at v1.0.6.

## Included

- Chromium browser extension for collecting visible Instagram Followers and Following lists.
- Local Python service and SQLite snapshot history.
- One Windows desktop application for starting/stopping the service and browsing, searching, comparing, and exporting saved collections.
- Combined-list membership filters for follower-only, following-only, and mutual accounts.
- Local profile-picture archive when Instagram exposes a downloadable image in a list row.
- Portable Windows executables; Python is not needed when using the packaged release.

## Install and use

1. Download `IB-Circlio-1.0.6-windows.zip` from the [GitHub v1.0.6 release](https://github.com/xxkyser-ctrl/IB_Circlio/releases/tag/v1.0.6).
2. Extract the archive to a normal folder and load that folder as an unpacked extension in a supported Chromium-based browser.
3. Double-click the single application, `ib_circlio.exe`.
4. Select the green **Start service** button and wait for the status to show **Running**.
5. Open an Instagram profile and start a collection from the extension.
6. Reports refresh automatically while the service is running. Select the red **Stop service** button when finished; closing the app also stops the service.

The database, token, and archived profile pictures are stored on the PC under `Desktop\Instagram Exporter Data` by default. See [README.md](README.md) for complete setup, usage, and limitations.

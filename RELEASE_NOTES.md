# IB_Circlio v1.0.4

## What's new

- Start and stop the local collection service directly from the IB_Circlio desktop application.
- View service status and live startup/error output in the Collection service tab.
- Keep the existing overview, snapshot reports, changes, Browse, Compare, and Excel export views in that same application.
- Filter a combined Browse list to accounts that follow the selected profile only, accounts the profile follows only, or mutual follows.
- Refresh report data after a collection without closing or reopening the application.
- Launch the same integrated application with either `result.bat` or the compatibility `run_server.bat` script.
- Keep existing build and release folders when creating a versioned release build.

## Included

- Chromium browser extension for collecting visible Instagram Followers and Following lists.
- Local Python service and SQLite snapshot history.
- Windows desktop application for browsing, searching, comparing, and exporting saved collections.
- Local profile-picture archive when Instagram exposes a downloadable image in a list row.
- Portable Windows executables; Python is not required for normal use of the packaged release.

## Install and use

1. Download `IB-Circlio-1.0.4-windows.zip` from the [GitHub v1.0.4 release](https://github.com/xxkyser-ctrl/IB_Circlio/releases/tag/v1.0.4).
2. Extract the archive to a normal folder and load that folder as an unpacked extension in a supported Chromium-based browser.
3. Double-click `result.bat` or `run_server.bat` to open the unified IB_Circlio application.
4. In **Collection service**, select **Start service** and wait for the status to show **Running**.
5. Open an Instagram profile and start a collection from the extension.
6. Return to the application and select **Refresh reports** to see the saved snapshot.

The database, token, and archived profile pictures are stored on the PC under `Desktop\Instagram Exporter Data` by default. See [README.md](README.md) for installation, supported-browser details, usage, and limitations.

# IB_Circlio v1.0.5

## What's new

- Use one launcher named `ib_circlio`: the portable release provides `ib_circlio.exe`; source mode provides `ib_circlio.bat`.
- Show the service start action in green and the stop action in red.
- Close the avatar preview by clicking back into the main application.
- Wait at least one second for each Instagram list dialog to stabilize before collecting usernames.
- Keep the Changes table empty when a second complete snapshot is not yet available instead of adding an explanatory message as a data row.
- Apply the IB_Circlio icon to the application window and packaged executable.
- Stop the local collection service when the application closes.
- Build into a version-specific output folder without replacing earlier releases.

## Included

- Chromium browser extension for collecting visible Instagram Followers and Following lists.
- Local Python service and SQLite snapshot history.
- One Windows desktop application for starting/stopping the service and browsing, searching, comparing, and exporting saved collections.
- Combined-list membership filters for follower-only, following-only, and mutual accounts.
- Local profile-picture archive when Instagram exposes a downloadable image in a list row.
- Portable Windows executables; Python is not needed when using the packaged release.

## Install and use

1. Download `IB-Circlio-1.0.5-windows.zip` from the [GitHub v1.0.5 release](https://github.com/xxkyser-ctrl/IB_Circlio/releases/tag/v1.0.5).
2. Extract the archive to a normal folder and load that folder as an unpacked extension in a supported Chromium-based browser.
3. Double-click the single application, `ib_circlio.exe`.
4. Select the green **Start service** button and wait for the status to show **Running**.
5. Open an Instagram profile and start a collection from the extension.
6. Return to the same app and select **Refresh reports**. Select the red **Stop service** button when finished; closing the app also stops the service.

The database, token, and archived profile pictures are stored on the PC under `Desktop\Instagram Exporter Data` by default. See [README.md](README.md) for complete setup, usage, and limitations.

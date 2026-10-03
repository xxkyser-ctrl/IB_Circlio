# IB Circlio — Instagram Follower & Following Tracker

IB Circlio is a **local-first Instagram follower tracker and following tracker for Windows**. Collect snapshots of the follower and following lists shown on an Instagram profile, keep the history in a SQLite database on your PC, and compare snapshots to see which accounts appeared or disappeared.

Use the desktop app to search saved usernames, browse followers and following, trace changes by collection timestamp, inspect archived profile pictures, and export reports to Excel. IB Circlio is designed for people looking for an **Instagram unfollower tracker**, a **local Instagram follower history**, or a way to compare follower and following lists over time. To investigate **who unfollowed you on Instagram**, compare snapshots from before and after the account disappeared; results only cover the collections you saved.

> IB Circlio only knows what it collected in your snapshots. It cannot recover changes from before your first collection, see private Instagram data that is not available to your logged-in browser, or guarantee that Instagram has rendered every account in a list.

[![Latest release](https://img.shields.io/github/v/release/xxkyser-ctrl/IB_Circlio)](https://github.com/xxkyser-ctrl/IB_Circlio/releases/latest)
[![Issues](https://img.shields.io/github/issues/xxkyser-ctrl/IB_Circlio)](https://github.com/xxkyser-ctrl/IB_Circlio/issues)

## Download

Download the latest Windows release from [GitHub Releases](https://github.com/xxkyser-ctrl/IB_Circlio/releases/latest). The release includes the browser extension files and portable Windows executables; installing Python is not required for normal use.

## Quick start

1. Download and extract the latest `IB-Circlio-<version>-windows.zip` to a regular folder. Keep the extracted files together.
2. In a Chromium browser, open its extensions page:
   - Chrome: `chrome://extensions`
   - Edge: `edge://extensions`
   - Brave: `brave://extensions`
   - Opera: `opera://extensions`
   - Vivaldi: `vivaldi://extensions`
3. Turn on **Developer mode**, choose **Load unpacked**, and select the extracted release folder.
4. Double-click `run_server.bat` in that folder. Keep the local server window open while collecting.
5. Log into Instagram normally, open the profile you want to collect, and use the IB Circlio extension to start a collection.
6. Later, double-click `result.bat` in the release folder to open the report app.

The first collection establishes the baseline. Run another collection later to identify changes between snapshots. A collection stopped before completion is saved as partial and is not used as a complete comparison baseline.

### Firefox

The release folder also includes `manifest.firefox.json`. Firefox users should make a copy of the extracted release folder, replace that copy's `manifest.json` with `manifest.firefox.json` renamed to `manifest.json`, then load the copied folder through `about:debugging#/runtime/this-firefox` using **Load Temporary Add-on**. Start the local backend with `run_server.bat` as usual. Temporary Firefox add-ons may need to be loaded again after restarting the browser.

## What you can do

- **Collect followers and following:** start one collection from an Instagram profile; IB Circlio opens each visible list and records the usernames it can read.
- **Keep a local history:** every completed collection is timestamped and stored in SQLite on your PC.
- **Find follower changes:** compare snapshots to identify accounts newly present or no longer present in Followers.
- **Track following changes:** compare snapshots to see which accounts were added to or removed from Following.
- **Trace when changes happened:** the Compare view shows each membership transition at the timestamp of the collection where it occurred. If an account leaves and later returns between the selected endpoints, both transitions are shown. Choose an older snapshot for **From** and a newer snapshot for **To**.
- **Browse and search lists:** view Followers, Following, or Both for a selected snapshot. Use the **Search username** field above each report table to filter the visible rows as you type; matching is case-insensitive.
- **Understand list membership:** Browse → Both lists each username once and marks accounts as Both, Followers only, or Following only.
- **Inspect profile pictures:** view archived local thumbnails in report tables and double-click an available avatar to zoom. Images that were not successfully archived appear as placeholders.
- **Export to Excel:** export the visible rows from a table, or create a full formatted workbook for a selected snapshot.
- **Check collection counts:** Instagram header totals that do not match the distinct collected usernames are marked **Unverified** rather than shown as reliable counts.

## How the collection works

1. You open an Instagram profile in a supported browser and start IB Circlio.
2. The extension reads usernames and available profile-image URLs from Instagram's visible Followers and Following dialogs.
3. It sends the collection to the local IB Circlio service on your own PC.
4. The service records the snapshot and any available profile pictures in the local data folder.
5. The desktop report app reads that local database to browse, compare, search, and export your history.

IB Circlio does not ask for or store your Instagram password, does not use Instagram API credentials, and does not upload your collection to an IB Circlio cloud service. Your browser still connects to Instagram as part of normal Instagram use.

## Using the report app

Double-click `result.bat` to open the desktop report. Its tabs include:

- **Overview:** latest snapshot counts and recent complete collections.
- **Snapshot report:** the selected collection's timestamp, saved Followers and Following, and changes from the previous complete collection.
- **Changes:** accounts added or removed in the latest complete comparison.
- **Browse lists:** a saved Followers list, Following list, or combined membership view.
- **Compare:** transitions between two selected complete snapshots, with the timestamp for each event. **From** must be older than **To**.
- **Export:** a full formatted Excel workbook for a selected snapshot.

Each table has a **Search username** field and an **Export table** button. Table exports contain the currently displayed rows and headings. The full workbook export remains available separately and includes snapshot summary and change information.

When an avatar is available locally, double-click it in a table to open a larger view. A placeholder means that an image was unavailable or could not be archived for that account and collection; historical images cannot be recreated if they were never saved.

## Data location and privacy

The default local data folder is:

```text
%USERPROFILE%\Desktop\Instagram Exporter Data
```

It contains the SQLite database (`instagram.db`), archived profile pictures (`avatars`), and the local server token. Reports you export are saved wherever you choose. Keep this folder and exported workbooks private: follower/following lists and profile images can reveal sensitive social-graph information.

No usernames, snapshots, or archived images are stored in browser local storage. The extension's temporary collection status is held in extension memory; the persistent collection history is on your PC.

To permanently erase the saved database and images, stop the local server and use `clear_database.bat`. This deletion is irreversible.

## Troubleshooting

- **The extension cannot start:** make sure `run_server.bat` is still running and that you loaded the extracted release folder, not the ZIP file.
- **The extension does not respond on an already-open Instagram tab:** reload the Instagram tab after installing or updating the extension.
- **The collection is incomplete:** leave the profile list dialog open while it is being read, avoid navigating away, and try again later if Instagram is still loading or rate-limiting the list.
- **A count says Unverified:** the visible Instagram total did not match the number of distinct usernames collected. Treat the collected list count as the count IB Circlio actually saved.
- **An avatar is missing:** that image URL may not have been exposed beside the username or the image could not be downloaded at collection time. IB Circlio does not fetch missing historic images later.
- **There are no detected changes:** compare two complete snapshots from different collection times. Changes before the first snapshot are not available.

## Screenshots

The screenshots below use a redacted demo profile. Usernames and account identifiers are not included.

![IB Circlio extension ready to start a collection](docs/images/demo-start.png)

![IB Circlio collecting Instagram followers](docs/images/demo-followers.png)

![IB Circlio collecting Instagram following](docs/images/demo-following.png)

![IB Circlio collection finished](docs/images/demo-finished.png)

![IB Circlio local SQLite service](docs/images/demo-server.png)

![IB Circlio timestamped comparison report](docs/images/demo-result.png)

## Build from source

Normal use should use the portable release. For development or to build the Windows package:

1. Install Python 3.13 or newer.
2. Install build requirements with `py -3 -m pip install -r requirements-build.txt`.
3. Run the tests with `py -3 -B -m unittest -q`.
4. Build the portable release on Windows with `py -3 -B build_release.py`.

The generated release folder is under `release`. The source-mode batch files use Python when a packaged executable is not present.

## Project information

- **Source code and releases:** [github.com/xxkyser-ctrl/IB_Circlio](https://github.com/xxkyser-ctrl/IB_Circlio)
- **Latest Windows download:** [GitHub Releases](https://github.com/xxkyser-ctrl/IB_Circlio/releases/latest)
- **Questions and bug reports:** [GitHub Issues](https://github.com/xxkyser-ctrl/IB_Circlio/issues)
- **Security reporting:** see [SECURITY.md](SECURITY.md)

IB Circlio is an independent project and is not affiliated with, endorsed by, or sponsored by Instagram or Meta.

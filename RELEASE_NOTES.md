# IB Circlio v1.0.3

IB Circlio is a privacy-first Instagram follower and following tracker for Windows.

## What's new

- Search every report table by username with case-insensitive matching.
- Compare snapshots with a timestamp for every membership transition, including accounts that leave and return.
- Filter Browse lists by follower/following membership and see the membership colors.
- Export visible table rows to Excel without replacing the full snapshot workbook export.
- Open archived profile pictures in a larger view and use the closest available image from that snapshot or an earlier one.
- Collect and archive more profile pictures when Instagram exposes the image in the account's list row.
- Reject unreliable Instagram totals and show collected username counts instead.
- Use the updated, searchable report interface without a command window.

## Included

- Automatic Followers and Following collection
- PC-local SQLite snapshots and dated profile-picture archive
- New follower, unfollower, and following change history
- Chrome, Edge, Brave, Opera, Vivaldi, and Firefox extension manifests
- Portable Windows executables; Python is not required for end users

## Install

1. Download `IB-Circlio-1.0.3-windows.zip`.
2. Extract it to a normal folder and load that folder as an unpacked extension.
3. Double-click `run_server.bat`.
4. Open an Instagram profile and click **Start collection**.
5. Open `result.bat` to search, compare, browse, and export local snapshots.

The local database and image archive are stored under `Desktop\Instagram Exporter Data`.

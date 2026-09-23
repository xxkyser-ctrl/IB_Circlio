"""Local reports, snapshot browsing, and comparisons."""

import argparse
import sqlite3
import zipfile
from html import escape
from datetime import datetime, date
from pathlib import Path


def choose_profile(connection, requested):
    if requested:
        return requested.strip().lower()
    rows = connection.execute(
        "SELECT username FROM profiles ORDER BY username"
    ).fetchall()
    if not rows:
        raise ValueError("No collections exist yet.")
    if len(rows) == 1:
        return rows[0][0]
    print("Profiles:")
    for index, row in enumerate(rows, 1):
        print(f"{index}. {row[0]}")
    choice = int(input("Choose a profile number: "))
    return rows[choice - 1][0]


def report(connection, profile):
    collection = connection.execute(
        """SELECT c.id, c.captured_at, c.complete, c.followers_header_total,
                  c.following_header_total
           FROM collections c JOIN profiles p ON p.id = c.profile_id
           WHERE p.username = ? ORDER BY c.captured_at DESC, c.id DESC LIMIT 1""",
        (profile,),
    ).fetchone()
    if not collection:
        raise ValueError(f"No collection found for {profile}.")
    changes = connection.execute(
        """SELECT u.username, mc.relationship, mc.change
           FROM membership_changes mc JOIN users u ON u.id = mc.user_id
           WHERE mc.collection_id = ? ORDER BY mc.relationship, mc.change, u.username""",
        (collection[0],),
    ).fetchall()
    grouped = {
        "followers": {"added": [], "removed": []},
        "following": {"added": [], "removed": []},
    }
    for username, relationship, change in changes:
        grouped[relationship][change].append(username)

    collection_time = collection[1]
    print(f"\nProfile: {profile}")
    print(f"Collection timestamp: {collection_time}")
    print(f"Status: {'complete' if collection[2] else 'partial (not used as a comparison baseline)'}")
    print(f"Followers shown by Instagram: {collection[3] if collection[3] is not None else 'unknown'}")
    print(f"Followers collected: {count_members(connection, collection[0], 'followers')}")
    print(f"Following shown by Instagram: {collection[4] if collection[4] is not None else 'unknown'}")
    print(f"Following collected: {count_members(connection, collection[0], 'following')}")
    for relationship in ("followers", "following"):
        print(f"\n{relationship.title()} changes:")
        print(f"  New: {len(grouped[relationship]['added'])}")
        print(f"  Removed: {len(grouped[relationship]['removed'])}")
    avatar_differences = avatar_changes(connection, profile, collection[0])
    print(f"\nProfile picture changes: {len(avatar_differences)}")
    for change in avatar_differences:
        print(f"  {change['username']}: {change['from']} -> {change['to']}")

    if input("\nShow changed usernames? [y/N] ").strip().lower() == "y":
        for relationship in ("followers", "following"):
            for change in ("added", "removed"):
                print(f"\n{relationship.title()} {change} ({collection_time}):")
                print("\n".join(grouped[relationship][change]) or "(none)")

    if input("\nCreate a formatted Excel workbook? [y/N] ").strip().lower() == "y":
        output = Path.cwd() / f"ib-circlio_{profile}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
        write_workbook(output, profile, collection, grouped, connection, avatar_differences)
        print(f"Report written to: {output}")


def avatar_changes(connection, profile, collection_id):
    previous = connection.execute(
        """SELECT cav.source_url, cav.image_path, u.username
           FROM collection_avatar_versions cav
           JOIN users u ON u.id = cav.user_id
           JOIN collections c ON c.id = cav.collection_id
           JOIN profiles p ON p.id = c.profile_id
           WHERE p.username = ? AND c.complete = 1 AND c.id = (
             SELECT previous.id FROM collections previous
             WHERE previous.profile_id = c.profile_id AND previous.complete = 1
               AND previous.id < ?
             ORDER BY previous.captured_at DESC, previous.id DESC LIMIT 1
           )""",
        (profile, collection_id),
    ).fetchall()
    current = connection.execute(
        """SELECT cav.source_url, cav.image_path, u.username
           FROM collection_avatar_versions cav
           JOIN users u ON u.id = cav.user_id
           WHERE cav.collection_id = ?""",
        (collection_id,),
    ).fetchall()
    previous_by_user = {row[2]: row for row in previous}
    differences = []
    for row in current:
        old = previous_by_user.get(row[2])
        if old and old[0] != row[0]:
            differences.append({
                "username": row[2],
                "from": old[1],
                "to": row[1],
            })
    return sorted(differences, key=lambda item: item["username"])


def write_workbook(output, profile, collection, grouped, connection, avatar_differences=None):
    summary_rows = [
        ["Profile", profile],
        ["Collection timestamp", collection[1]],
        ["Status", "Complete" if collection[2] else "Partial"],
        ["Followers shown by Instagram", collection[3] if collection[3] is not None else "Unknown"],
        ["Followers collected", count_members(connection, collection[0], "followers")],
        ["Following shown by Instagram", collection[4] if collection[4] is not None else "Unknown"],
        ["Following collected", count_members(connection, collection[0], "following")],
        ["New followers", len(grouped["followers"]["added"])],
        ["Removed followers", len(grouped["followers"]["removed"])],
        ["New following", len(grouped["following"]["added"])],
        ["Removed following", len(grouped["following"]["removed"])],
    ]
    change_rows = [["Collection timestamp", "Relationship", "Change", "Username"]]
    for relationship in ("followers", "following"):
        for change in ("added", "removed"):
            for username in grouped[relationship][change]:
                change_rows.append([collection[1], relationship.title(), change.title(), username])
    for change in avatar_differences or avatar_changes(connection, profile, collection[0]):
        change_rows.append([
            collection[1], "Profile picture", "Changed",
            f"{change['username']} | {change['from']} -> {change['to']}"
        ])

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""
    workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Summary" sheetId="1" r:id="rId1"/><sheet name="Changes" sheetId="2" r:id="rId2"/></sheets></workbook>"""
    relationships = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>"""
    workbook_relationships = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>"""
    styles = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>
<fills count="5"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF2563EB"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFE0F2FE"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFDCFCE7"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellXfs count="5"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" applyFont="1" applyFill="1"/><xf numFmtId="0" fontId="0" fillId="3" borderId="0" applyFill="1"/><xf numFmtId="0" fontId="0" fillId="4" borderId="0" applyFill="1"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" applyFont="1" applyFill="1"/></cellXfs></styleSheet>"""

    def cell(value, style=0):
        value = escape(str(value))
        return f'<c t="inlineStr" s="{style}"><is><t>{value}</t></is></c>'

    def sheet(rows, widths):
        xml_rows = []
        for index, row in enumerate(rows, 1):
            cells = []
            for column, value in enumerate(row, 1):
                if rows is summary_rows and column == 1:
                    style = 2 if index % 2 else 3
                elif rows is change_rows and index == 1:
                    style = 1
                else:
                    style = 0
                cells.append(cell(value, style))
            cells = "".join(cells)
            xml_rows.append(f'<row r="{index}">{cells}</row>')
        cols = "".join(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>' for i, width in enumerate(widths, 1))
        return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><cols>{cols}</cols><sheetData>{"".join(xml_rows)}</sheetData><autoFilter ref="A1:{chr(64 + len(rows[0]))}{len(rows)}"/></worksheet>'

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_relationships)
        archive.writestr("xl/styles.xml", styles)
        archive.writestr("xl/worksheets/sheet1.xml", sheet(summary_rows, [34, 42]))
        archive.writestr("xl/worksheets/sheet2.xml", sheet(change_rows, [28, 18, 18, 32]))


def count_members(connection, collection_id, relationship):
    return connection.execute(
        "SELECT COUNT(*) FROM collection_memberships WHERE collection_id = ? AND relationship = ?",
        (collection_id, relationship),
    ).fetchone()[0]


RELATIONSHIPS = ("followers", "following")


def snapshot_dates(connection, profile):
    """Return the available snapshot dates in chronological order."""
    rows = connection.execute(
        """SELECT DISTINCT substr(c.captured_at, 1, 10) AS snapshot_date
           FROM collections c JOIN profiles p ON p.id = c.profile_id
           WHERE p.username = ? AND c.complete = 1
           ORDER BY snapshot_date""",
        (profile,),
    ).fetchall()
    return [row[0] for row in rows]


def _valid_date(value):
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        return None


def choose_snapshot_date(dates, prompt):
    """Prompt until a numbered or ISO date selection is made."""
    while True:
        choice = input(prompt).strip()
        if choice.isdigit():
            index = int(choice) - 1
            if 0 <= index < len(dates):
                return dates[index]
        else:
            parsed = _valid_date(choice)
            if parsed in dates:
                return parsed
        print("Invalid selection. Enter a listed number or date (YYYY-MM-DD).")


def choose_relationship(prompt="Show [followers/following/both]: "):
    while True:
        value = input(prompt).strip().lower()
        if value == "both":
            return RELATIONSHIPS
        if value in RELATIONSHIPS:
            return (value,)
        print("Invalid choice. Enter followers, following, or both.")


def collection_for_date(connection, profile, snapshot):
    """Resolve a date or collection id to the newest complete collection."""
    parsed = _valid_date(snapshot)
    if parsed:
        row = connection.execute(
            """SELECT c.id, c.captured_at
               FROM collections c JOIN profiles p ON p.id = c.profile_id
               WHERE p.username = ? AND c.complete = 1
                 AND substr(c.captured_at, 1, 10) = ?
               ORDER BY c.captured_at DESC, c.id DESC LIMIT 1""",
            (profile, parsed),
        ).fetchone()
    elif str(snapshot).isdigit():
        row = connection.execute(
            """SELECT c.id, c.captured_at
               FROM collections c JOIN profiles p ON p.id = c.profile_id
               WHERE p.username = ? AND c.complete = 1 AND c.id = ?""",
            (profile, int(snapshot)),
        ).fetchone()
    else:
        row = None
    if not row:
        raise ValueError(f"No complete snapshot found for {profile} on {snapshot}.")
    return row


def _members(connection, collection_id, relationship):
    rows = connection.execute(
        """SELECT u.username, COALESCE(cav.image_path, u.avatar_path)
           FROM collection_memberships cm JOIN users u ON u.id = cm.user_id
           LEFT JOIN collection_avatar_versions cav
             ON cav.collection_id = cm.collection_id AND cav.user_id = cm.user_id
           WHERE cm.collection_id = ? AND cm.relationship = ?
           ORDER BY u.username""",
        (collection_id, relationship),
    ).fetchall()
    return rows


def browse(connection, profile):
    dates = snapshot_dates(connection, profile)
    if not dates:
        raise ValueError(f"No complete snapshots found for {profile}.")
    print("Available snapshots:")
    for index, snapshot in enumerate(dates, 1):
        print(f"{index}. {snapshot}")
    selected = choose_snapshot_date(dates, "Choose a snapshot: ")
    relationships = choose_relationship()
    collection = collection_for_date(connection, profile, selected)
    print(f"\nSnapshot: {selected} (collection {collection[0]})")
    for relationship in relationships:
        print(f"\n{relationship.title()}:")
        for username, avatar_path in _members(connection, collection[0], relationship):
            print(f"{username}" + (f" [{avatar_path}]" if avatar_path else ""))


def compare(connection, profile, from_snapshot=None, to_snapshot=None,
            relationships=None, interactive=False):
    dates = snapshot_dates(connection, profile)
    if len(dates) < 2:
        raise ValueError("At least two complete snapshots are required.")
    if interactive:
        print("Available snapshots:")
        for index, snapshot in enumerate(dates, 1):
            print(f"{index}. {snapshot}")
        from_snapshot = choose_snapshot_date(dates, "From snapshot: ")
        to_snapshot = choose_snapshot_date(dates, "To snapshot: ")
    elif (from_snapshot is None) != (to_snapshot is None):
        raise ValueError("--from and --to must be supplied together.")
    elif from_snapshot is None:
        from_snapshot, to_snapshot = dates[-2], dates[-1]
    from_collection = collection_for_date(connection, profile, str(from_snapshot))
    to_collection = collection_for_date(connection, profile, str(to_snapshot))
    if from_collection[0] == to_collection[0]:
        raise ValueError("The two snapshots must be different.")
    relationships = relationships or RELATIONSHIPS
    # The labels describe the requested snapshots; ordering makes the diff
    # deterministic even when the user supplies them in reverse order.
    if from_collection[1] > to_collection[1]:
        from_collection, to_collection = to_collection, from_collection
    for relationship in relationships:
        old = {row[0] for row in _members(connection, from_collection[0], relationship)}
        new = {row[0] for row in _members(connection, to_collection[0], relationship)}
        print(f"\n{relationship.title()} ({from_collection[1][:10]} → {to_collection[1][:10]}):")
        for username in sorted(new - old):
            print(f"+ {username}")
        for username in sorted(old - new):
            print(f"− {username}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--profile")
    parser.add_argument("command", nargs="?", choices=("browse", "compare"))
    parser.add_argument("--from", dest="from_snapshot")
    parser.add_argument("--to", dest="to_snapshot")
    parser.add_argument("--list", dest="relationships",
                        choices=("followers", "following", "both"))
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()
    database = Path(args.data_dir) / "instagram.db"
    if not database.exists():
        raise ValueError("The SQLite database does not exist.")
    connection = sqlite3.connect(database)
    try:
        profile = choose_profile(connection, args.profile)
        if args.command == "browse":
            browse(connection, profile)
        elif args.command == "compare":
            relationships = (args.relationships,) if args.relationships in RELATIONSHIPS else RELATIONSHIPS
            if args.relationships == "both":
                relationships = RELATIONSHIPS
            compare(connection, profile, args.from_snapshot, args.to_snapshot,
                    relationships, args.interactive)
        else:
            report(connection, profile)
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, IndexError, sqlite3.Error) as error:
        print(f"Error: {error}")
        raise SystemExit(1)

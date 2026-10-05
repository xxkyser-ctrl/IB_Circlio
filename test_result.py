import contextlib
import io
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import result
import result_gui
import database as database_access
import server


class SnapshotReportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = server.Database(str(Path(self.directory.name) / "instagram.db"))

    def tearDown(self):
        self.database.close()
        self.directory.cleanup()

    def save(self, captured_at, followers, following):
        collection = self.database.save_collection({
            "profile": "owner",
            "followers": followers,
            "following": following,
        })
        self.database.connection.execute(
            "UPDATE collections SET captured_at = ?, created_at = ? WHERE id = ?",
            (captured_at, captured_at, collection["id"]),
        )
        self.database.connection.commit()
        return collection["id"]

    def test_compare_accepts_arbitrary_dates_and_normalizes_order(self):
        self.save("2026-09-18T10:00:00+00:00", ["alice"], ["x"])
        self.save("2026-09-19T10:00:00+00:00", ["alice", "bob"], ["y"])
        self.save("2026-09-21T10:00:00+00:00", ["carol"], ["x", "z"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result.compare(
                self.database.connection,
                "owner",
                "2026-09-21",
                "2026-09-18",
                ("followers",),
            )
        text = output.getvalue()
        self.assertIn("+ carol", text)
        self.assertIn("− alice", text)
        self.assertNotIn("+ bob", text)

    def test_browse_reprompts_and_prints_avatar_path(self):
        self.save("2026-09-18T10:00:00+00:00", ["alice"], [])
        self.save("2026-09-19T10:00:00+00:00", ["alice"], [])
        self.database.connection.execute(
            "UPDATE users SET avatar_path = ? WHERE username = ?",
            ("avatars/alice.jpg", "alice"),
        )
        self.database.connection.commit()
        output = io.StringIO()
        with patch("builtins.input", side_effect=["99", "1", "followers"]), \
                contextlib.redirect_stdout(output):
            result.browse(self.database.connection, "owner")
        self.assertIn("alice [avatars/alice.jpg]", output.getvalue())

    def test_snapshot_export_has_report_fields_and_writes_workbook(self):
        self.save("2026-09-18T10:00:00+00:00", ["alice"], ["x"])
        collection = result.collection_for_date(
            self.database.connection, "owner", "2026-09-18"
        )
        self.assertEqual(len(collection), 5)
        result.generate_workbook(
            self.database.connection,
            "owner",
            collection,
            Path(self.directory.name),
        )
        workbooks = list(Path(self.directory.name).glob("*.xlsx"))
        self.assertEqual(len(workbooks), 1)
        self.assertTrue(zipfile.is_zipfile(workbooks[0]))

    def test_gui_comparison_returns_additions_and_removals(self):
        self.save("2026-09-18T10:00:00+00:00", ["alice", "gone"], ["x"])
        self.save("2026-09-19T10:00:00+00:00", ["alice", "new"], ["y"])
        _before, _after, differences = result_gui.compare_members(
            self.database.connection, "owner", "2026-09-18", "2026-09-19",
            result.RELATIONSHIPS,
        )
        self.assertIn(("Followers", "Added", "new"), differences)
        self.assertIn(("Followers", "Removed", "gone"), differences)
        self.assertIn(("Following", "Added", "y"), differences)
        self.assertIn(("Following", "Removed", "x"), differences)
        with self.assertRaisesRegex(ValueError, "From must be an older snapshot"):
            result_gui.compare_members(
                self.database.connection, "owner", "2026-09-19", "2026-09-18",
                result.RELATIONSHIPS,
            )

    def test_database_data_version_detects_service_commits(self):
        database_path = Path(self.directory.name) / "instagram.db"
        reader = database_access.open_database(database_path, readonly=True)
        try:
            initial_version = result_gui.database_data_version(reader)
            self.save("2026-09-18T10:00:00+00:00", ["alice"], ["x"])
            updated_version = result_gui.database_data_version(reader)
            self.assertGreater(updated_version, initial_version)
        finally:
            reader.close()

    def test_gui_count_audit_reports_header_delta(self):
        collection = self.database.save_collection({
            "profile": "owner",
            "followers": ["alice", "bob", "carol"],
            "following": ["x"],
            "headerTotals": {"followers": 4, "following": 2},
            "headerTotalLabels": {
                "followers": "4 followers",
                "following": "2 following",
            },
        })
        row = self.database.connection.execute(
            """SELECT id, captured_at, complete, followers_header_total,
                      following_header_total FROM collections WHERE id = ?""",
            (collection["id"],),
        ).fetchone()
        audit = result_gui.collection_count_audit(self.database.connection, row)
        self.assertEqual(audit[0]["difference"], -1)
        self.assertEqual(audit[1]["difference"], -1)
        self.assertEqual(audit[0]["source_label"], "4 followers")

    def test_gui_snapshot_choices_keep_multiple_collections_on_one_day(self):
        self.save("2026-10-02T23:34:41+00:00", ["alice"], ["x"])
        self.save("2026-10-02T23:48:09+00:00", ["alice"], ["x", "y"])
        choices = result_gui.snapshot_choices(self.database.connection, "owner")
        self.assertEqual(len(choices), 2)
        self.assertNotEqual(choices[0][1], choices[1][1])
        self.assertNotEqual(choices[0][0], choices[1][0])

    def test_gui_count_audit_rejects_impossible_following_total(self):
        collection = self.database.save_collection({
            "profile": "owner",
            "followers": ["alice"],
            "following": ["x", "y"],
            "headerTotals": {"followers": 1, "following": 4000000},
        })
        row = self.database.connection.execute(
            """SELECT id, captured_at, complete, followers_header_total,
                      following_header_total FROM collections WHERE id = ?""",
            (collection["id"],),
        ).fetchone()
        audit = result_gui.collection_count_audit(self.database.connection, row)
        self.assertEqual(audit[1]["shown"], None)
        self.assertEqual(audit[1]["recorded_shown"], 4000000)
        self.assertIn("7,500", audit[1]["invalid_reason"])
        self.assertEqual(audit[1]["difference"], -3999998)

    def test_reports_hide_unverified_instagram_totals(self):
        collection = self.database.save_collection({
            "profile": "owner",
            "followers": ["alice"],
            "following": ["x", "y"],
            "headerTotals": {"followers": 400000, "following": 400000},
        })
        row = self.database.connection.execute(
            """SELECT id, captured_at, complete, followers_header_total,
                      following_header_total FROM collections WHERE id = ?""",
            (collection["id"],),
        ).fetchone()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result.report(self.database.connection, "owner", prompt=False)
        self.assertIn("Followers shown by Instagram: Unverified", output.getvalue())
        self.assertIn("Following shown by Instagram: Unverified", output.getvalue())
        self.assertNotIn("400000", output.getvalue())
        grouped = {
            "followers": {"added": [], "removed": []},
            "following": {"added": [], "removed": []},
        }
        workbook = Path(self.directory.name) / "report.xlsx"
        result.write_workbook(
            workbook, "owner", row, grouped, self.database.connection, []
        )
        with zipfile.ZipFile(workbook) as archive:
            workbook_xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("Unverified", workbook_xml)
        self.assertNotIn("400000", workbook_xml)

    def test_gui_avatar_coverage_counts_both_relationship_lists(self):
        collection = self.database.save_collection({
            "profile": "owner",
            "followers": ["shared"],
            "following": ["shared"],
        })
        user_id = self.database.connection.execute(
            "SELECT id FROM users WHERE username = 'shared'"
        ).fetchone()[0]
        self.database.connection.execute(
            """INSERT INTO collection_avatar_versions
               (collection_id, user_id, image_path, source_url, fetched_at)
               VALUES (?, ?, ?, ?, ?)""",
            (collection["id"], user_id, "avatar.jpg", "https://example.test/a", "now"),
        )
        self.database.connection.commit()
        self.assertEqual(
            result_gui.collection_avatar_coverage(
                self.database.connection, collection["id"]
            ),
            {"total": 2, "saved": 2},
        )

    def test_browse_both_lists_unique_accounts_with_membership_markers(self):
        collection = self.database.save_collection({
            "profile": "owner",
            "followers": ["shared", "follower_only"],
            "following": ["shared", "following_only"],
        })
        rows = result_gui.browse_members(
            self.database.connection, collection["id"], "both"
        )
        membership = {username: marker for marker, username, _ in rows}
        self.assertEqual(len(rows), 3)
        self.assertEqual(membership["shared"], "[=] Both")
        self.assertEqual(membership["follower_only"], "[F] Followers only")
        self.assertEqual(membership["following_only"], "[>] Following only")
        self.assertEqual(len({username for _, username, _ in rows}), 3)

    def test_browse_membership_filter_separates_one_way_and_mutual_relationships(self):
        collection = self.database.save_collection({
            "profile": "owner",
            "followers": ["shared", "follower_only"],
            "following": ["shared", "following_only"],
        })
        rows = result_gui.browse_members(
            self.database.connection, collection["id"], "both"
        )
        self.assertEqual(
            [username for _, username, _ in result_gui.filter_browse_members(
                rows, "Follows profile only"
            )],
            ["follower_only"],
        )
        self.assertEqual(
            [username for _, username, _ in result_gui.filter_browse_members(
                rows, "Profile follows only"
            )],
            ["following_only"],
        )
        self.assertEqual(
            [username for _, username, _ in result_gui.filter_browse_members(
                rows, "Mutual follows"
            )],
            ["shared"],
        )
        self.assertEqual(
            result_gui.filter_browse_members(rows, "Everyone"), rows
        )

    def test_browse_single_relationship_marks_rows(self):
        collection = self.database.save_collection({
            "profile": "owner",
            "followers": ["follower"],
            "following": ["followed"],
        })
        rows = result_gui.browse_members(
            self.database.connection, collection["id"], "followers"
        )
        self.assertEqual(rows, [("Followers", "follower", None)])

    def test_gui_username_search_matches_case_insensitive_row_values(self):
        items = [
            ("a", ("Followers", "Alice.Example")),
            ("b", ("Following", "bob_example")),
            ("c", ("Following", "carol")),
        ]
        self.assertEqual(
            result_gui.matching_tree_items(items, "BOB_"),
            {"b"},
        )
        self.assertEqual(
            result_gui.matching_tree_items(items, "  "),
            {"a", "b", "c"},
        )

    def test_gui_comparison_timeline_reports_each_transition_with_timestamp(self):
        first = self.save("2026-09-18T10:00:00+00:00", ["alice", "bob"], [])
        second = self.save("2026-09-19T10:00:00+00:00", ["alice"], [])
        third = self.save("2026-09-20T10:00:00+00:00", ["alice", "bob"], [])
        _before, _after, events = result_gui.comparison_timeline(
            self.database.connection, "owner", first, third, ("followers",)
        )
        self.assertEqual(events, [
            ("2026-09-19T10:00:00+00:00", second, "Followers", "Removed", "bob"),
            ("2026-09-20T10:00:00+00:00", third, "Followers", "Added", "bob"),
        ])

    def test_gui_comparison_timeline_requires_from_to_order_and_same_profile(self):
        first = self.save("2026-09-18T10:00:00+00:00", ["alice"], [])
        second = self.save("2026-09-19T10:00:00+00:00", ["alice", "bob"], [])
        with self.assertRaisesRegex(ValueError, "From must be an older snapshot"):
            result_gui.comparison_timeline(
                self.database.connection, "owner", second, first, ("followers",)
            )
        with self.assertRaisesRegex(ValueError, "complete collections for this profile"):
            result_gui.comparison_timeline(
                self.database.connection, "other", first, second, ("followers",)
            )

    def test_table_export_is_valid_xlsx_with_headers_and_visible_rows(self):
        workbook = Path(self.directory.name) / "table.xlsx"
        result.write_table_workbook(
            workbook,
            "Changes",
            ("Changed at", "List", "Change", "Username"),
            (("2026-09-19 10:00", "Followers", "Removed", "a&b"),),
        )
        self.assertTrue(zipfile.is_zipfile(workbook))
        with zipfile.ZipFile(workbook) as archive:
            for name in (
                "[Content_Types].xml",
                "xl/workbook.xml",
                "xl/_rels/workbook.xml.rels",
                "xl/styles.xml",
                "xl/worksheets/sheet1.xml",
            ):
                ET.fromstring(archive.read(name))
            sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        values = [
            node.text
            for node in sheet.findall(".//x:is/x:t", ns)
        ]
        self.assertEqual(
            values,
            ["Changed at", "List", "Change", "Username",
             "2026-09-19 10:00", "Followers", "Removed", "a&b"],
        )

    def test_avatar_lookup_never_uses_a_future_archived_image(self):
        first = self.save("2026-09-18T10:00:00+00:00", ["alice"], [])
        middle = self.save("2026-09-19T10:00:00+00:00", ["alice"], [])
        last = self.save("2026-09-20T10:00:00+00:00", ["alice"], [])
        user_id = self.database.connection.execute(
            "SELECT id FROM users WHERE username = 'alice'"
        ).fetchone()[0]
        for collection_id, image_path in (
            (first, "avatars/alice-old.jpg"),
            (last, "avatars/alice-new.jpg"),
        ):
            self.database.connection.execute(
                """INSERT INTO collection_avatar_versions
                   (collection_id, user_id, image_path, source_url, fetched_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (collection_id, user_id, image_path, "https://example.test/a", "now"),
            )
        lookup = result_gui.CirclioReportApp.__new__(result_gui.CirclioReportApp)
        lookup.connection = self.database.connection
        self.assertEqual(lookup._avatar_path(middle, "alice"), "avatars/alice-old.jpg")
        self.assertEqual(lookup._avatar_path(last, "alice"), "avatars/alice-new.jpg")

    def test_table_export_neutralizes_formula_like_text(self):
        workbook = Path(self.directory.name) / "safe.xlsx"
        result.write_table_workbook(
            workbook,
            "Formula test",
            ["Username"],
            [["=1+1"], ["+command"], ["-command"], ["@command"]],
        )
        with zipfile.ZipFile(workbook) as archive:
            sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        values = [node.text for node in sheet.findall(".//x:is/x:t", ns)]
        self.assertEqual(
            values,
            ["Username", "'=1+1", "'+command", "'-command", "'@command"],
        )


if __name__ == "__main__":
    unittest.main()

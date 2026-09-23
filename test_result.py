import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import result
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


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import release_check
from result_gui import CirclioReportApp
from version import VERSION


def release(tag="v1.10.0", **changes):
    payload = {
        "tag_name": tag,
        "html_url": (
            "https://github.com/xxkyser-ctrl/IB_Circlio/releases/tag/" + tag
        ),
        "body": "Release notes",
        "draft": False,
        "prerelease": False,
    }
    payload.update(changes)
    return payload


class VersionComparisonTests(unittest.TestCase):
    def test_application_and_manifest_versions_and_notes_match(self):
        root = Path(__file__).resolve().parent
        for manifest_name in ("manifest.json", "manifest.firefox.json"):
            manifest = json.loads(
                (root / manifest_name).read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["version"], VERSION)
        notes = (root / "RELEASE_NOTES.md").read_text(encoding="utf-8")
        self.assertTrue(notes.startswith(f"# IB Circlio v{VERSION}\n"))

    def test_compares_numeric_semver_components(self):
        self.assertTrue(release_check.is_newer_version("1.10.0", "1.9.0"))
        self.assertFalse(release_check.is_newer_version("1.9.0", "1.10.0"))

    def test_accepts_tag_prefix_and_build_metadata(self):
        self.assertTrue(release_check.is_newer_version("v2.0.0", "1.99.0"))
        self.assertEqual(release_check.stable_version_tuple("1.2.3+build.7"), (1, 2, 3))

    def test_rejects_prerelease_or_malformed_versions(self):
        self.assertIsNone(release_check.stable_version_tuple("v1.2.3-rc.1"))
        self.assertIsNone(release_check.stable_version_tuple("latest"))
        self.assertFalse(release_check.is_newer_version("v1.bad.0", "1.0.0"))


class ReleaseLookupTests(unittest.TestCase):
    def test_lookup_uses_the_canonical_github_latest_release_endpoint(self):
        self.assertEqual(
            release_check.LATEST_RELEASE_URL,
            "https://api.github.com/repos/xxkyser-ctrl/IB_Circlio/releases/latest",
        )

    def test_newer_release_is_detected(self):
        parsed = release_check.parse_latest_release(release())
        self.assertEqual(parsed["version"], "1.10.0")
        self.assertTrue(release_check.update_available("1.9.0", parsed))

    def test_same_and_older_releases_are_not_updates(self):
        same = release_check.parse_latest_release(release("v1.0.6"))
        older = release_check.parse_latest_release(release("v1.0.5"))
        self.assertFalse(release_check.update_available("1.0.6", same))
        self.assertFalse(release_check.update_available("1.0.6", older))

    def test_draft_prerelease_and_malformed_payloads_are_ignored(self):
        self.assertIsNone(release_check.parse_latest_release(release(draft=True)))
        self.assertIsNone(release_check.parse_latest_release(release(prerelease=True)))
        self.assertIsNone(release_check.parse_latest_release({"tag_name": "not-a-version"}))
        self.assertIsNone(release_check.parse_latest_release(None))

    def test_unreachable_github_request_is_reported_to_caller(self):
        with patch("release_check.urllib.request.urlopen", side_effect=URLError("offline")):
            with self.assertRaises(URLError):
                release_check.fetch_latest_release()

    def test_request_uses_short_timeout_and_user_agent(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(release()).encode("utf-8")

        with patch("release_check.urllib.request.urlopen", return_value=Response()) as open_url:
            parsed = release_check.fetch_latest_release()
        request, = open_url.call_args.args
        self.assertEqual(request.get_header("User-agent"), "IB-Circlio-Update-Check")
        self.assertEqual(open_url.call_args.kwargs["timeout"], 5)
        self.assertEqual(parsed["version"], "1.10.0")

    def test_malformed_json_response_is_reported_without_fallback(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b"{invalid json"

        with patch("release_check.urllib.request.urlopen", return_value=Response()):
            with self.assertRaises(json.JSONDecodeError):
                release_check.fetch_latest_release()


class UpdateSettingsTests(unittest.TestCase):
    def test_settings_default_on_and_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            self.assertTrue(release_check.load_settings(path)["automatic_updates"])
            settings = {
                "automatic_updates": False,
                "last_checked": "2026-10-04T00:00:00+00:00",
            }
            release_check.save_settings(path, settings)
            self.assertEqual(release_check.load_settings(path), settings)

    def test_automatic_check_interval_is_one_day(self):
        now = datetime(2026, 10, 4, tzinfo=timezone.utc)
        yesterday = (now - timedelta(hours=24)).isoformat()
        recent = (now - timedelta(hours=23, minutes=59)).isoformat()
        self.assertTrue(release_check.automatic_check_due(yesterday, now))
        self.assertFalse(release_check.automatic_check_due(recent, now))
        self.assertTrue(release_check.automatic_check_due("invalid", now))


class FakeWidget:
    def __init__(self):
        self.options = {}
        self.content = ""
        self.packed = False

    def configure(self, **options):
        self.options.update(options)

    def delete(self, *_args):
        self.content = ""

    def insert(self, _position, content):
        self.content += content

    def pack(self, **_options):
        self.packed = True


class DesktopUpdateNoticeTests(unittest.TestCase):
    def make_app(self, directory):
        app = CirclioReportApp.__new__(CirclioReportApp)
        app.update_settings = release_check.default_settings()
        app.update_settings_path = Path(directory) / "settings.json"
        app.update_check_running = True
        app.update_check_button = FakeWidget()
        app.update_status_label = FakeWidget()
        app.update_notice_heading = FakeWidget()
        app.update_notes_view = FakeWidget()
        app.update_notice = FakeWidget()
        app.server_log_view = None
        return app

    def test_newer_release_displays_notice_and_release_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(directory)
            parsed = release_check.parse_latest_release(release("v1.0.8"))
            app._finish_update_check(False, parsed, None)

            self.assertEqual(
                app.update_notice_heading.options["text"],
                "Update available: v1.0.8",
            )
            self.assertEqual(app.update_notes_view.content, "Release notes")
            self.assertTrue(app.update_notice.packed)
            self.assertEqual(app.update_check_button.options["state"], "normal")
            self.assertIsNotNone(
                release_check.load_settings(app.update_settings_path)["last_checked"]
            )

    def test_same_or_older_release_displays_no_update(self):
        for version in ("1.0.7", "1.0.6"):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                app = self.make_app(directory)
                parsed = release_check.parse_latest_release(release(f"v{version}"))
                app._finish_update_check(True, parsed, None)
                self.assertEqual(
                    app.update_status_label.options["text"], "No update available"
                )
                self.assertFalse(app.update_notice.packed)

    def test_network_or_response_error_completes_without_crashing(self):
        for error in ("network unavailable", "malformed response"):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as directory:
                app = self.make_app(directory)
                app._finish_update_check(True, None, error)
                self.assertFalse(app.update_check_running)
                self.assertEqual(app.update_check_button.options["state"], "normal")
                self.assertFalse(app.update_notice.packed)

    def test_automatic_check_toggle_prevents_background_fetch_when_disabled(self):
        app = CirclioReportApp.__new__(CirclioReportApp)
        app.update_check_running = False
        app.auto_updates = type("BooleanValue", (), {"get": lambda _self: False})()
        app.update_check_button = FakeWidget()
        with patch.object(app, "_fetch_update_in_background") as fetch:
            app._start_update_check(manual=False)
        fetch.assert_not_called()


class BrowserManifestTests(unittest.TestCase):
    def test_browser_manifests_match_canonical_version_and_minimal_permissions(self):
        root = Path(__file__).resolve().parent
        for manifest_name in ("manifest.json", "manifest.firefox.json"):
            manifest = json.loads(
                (root / manifest_name).read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["version"], VERSION)
            self.assertTrue(
                {"alarms", "storage", "scripting"}.issubset(
                    set(manifest["permissions"])
                )
            )
            self.assertIn("https://api.github.com/*", manifest["host_permissions"])
            self.assertIn("https://www.instagram.com/*", manifest["host_permissions"])
            self.assertIn("http://127.0.0.1:8765/*", manifest["host_permissions"])

    def test_firefox_manifest_has_stable_addon_id_and_background_scripts(self):
        root = Path(__file__).resolve().parent
        manifest = json.loads(
            (root / "manifest.firefox.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            manifest["browser_specific_settings"]["gecko"]["id"],
            "ib-circlio@xxkyser-ctrl.github.io",
        )
        self.assertEqual(manifest["background"]["scripts"][0], "config.js")


if __name__ == "__main__":
    unittest.main()

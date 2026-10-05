import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import release_check
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

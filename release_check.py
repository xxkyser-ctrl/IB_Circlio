"""Shared GitHub release lookup and stable-version comparison helpers."""

import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone


LATEST_RELEASE_URL = (
    "https://api.github.com/repos/xxkyser-ctrl/IB_Circlio/releases/latest"
)
RELEASE_TAG_RE = re.compile(
    r"^v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\+[0-9A-Za-z.-]+)?$"
)
AUTO_CHECK_INTERVAL = timedelta(hours=24)


def stable_version_tuple(version):
    if not isinstance(version, str):
        return None
    match = RELEASE_TAG_RE.fullmatch(version.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def is_newer_version(candidate, current):
    candidate_parts = stable_version_tuple(candidate)
    current_parts = stable_version_tuple(current)
    return (
        candidate_parts is not None
        and current_parts is not None
        and candidate_parts > current_parts
    )


def parse_latest_release(payload):
    if not isinstance(payload, dict):
        return None
    if payload.get("draft") is True or payload.get("prerelease") is True:
        return None

    version = payload.get("tag_name")
    if stable_version_tuple(version) is None:
        return None
    url = payload.get("html_url")
    if not isinstance(url, str) or not url.startswith(
        "https://github.com/xxkyser-ctrl/IB_Circlio/releases/"
    ):
        return None

    body = payload.get("body")
    return {
        "version": version.strip().removeprefix("v"),
        "url": url,
        "notes": body if isinstance(body, str) else "",
    }


def fetch_latest_release(timeout=5):
    request = urllib.request.Request(
        LATEST_RELEASE_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "IB-Circlio-Update-Check",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return parse_latest_release(payload)


def update_available(current_version, release):
    if not release:
        return False
    return is_newer_version(release.get("version"), current_version)


def automatic_check_due(last_checked, now=None):
    if not last_checked:
        return True
    try:
        checked_at = datetime.fromisoformat(last_checked)
    except (TypeError, ValueError):
        return True
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return now - checked_at.astimezone(timezone.utc) >= AUTO_CHECK_INTERVAL


def default_settings():
    return {"automatic_updates": True, "last_checked": None}


def load_settings(path):
    try:
        with open(path, "r", encoding="utf-8") as settings_file:
            settings = json.load(settings_file)
    except FileNotFoundError:
        return default_settings()
    if not isinstance(settings, dict):
        raise ValueError("Update settings must be a JSON object.")

    result = default_settings()
    if "automatic_updates" in settings:
        if not isinstance(settings["automatic_updates"], bool):
            raise ValueError("automatic_updates must be true or false.")
        result["automatic_updates"] = settings["automatic_updates"]
    if "last_checked" in settings:
        value = settings["last_checked"]
        if value is not None and not isinstance(value, str):
            raise ValueError("last_checked must be a timestamp or null.")
        result["last_checked"] = value
    return result


def save_settings(path, settings):
    with open(path, "w", encoding="utf-8") as settings_file:
        json.dump(settings, settings_file, indent=2)
        settings_file.write("\n")

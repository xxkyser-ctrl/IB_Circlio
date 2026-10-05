"""Resolve the default local data directory while preserving existing data."""

import os
from pathlib import Path


LEGACY_DATA_DIRECTORY = Path("Desktop") / "Instagram Exporter Data"


def default_data_dir(environ=None, home=None):
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    local_app_data = environ.get("LOCALAPPDATA")
    if local_app_data:
        new_directory = Path(local_app_data) / "IB Circlio"
    else:
        new_directory = home / "AppData" / "Local" / "IB Circlio"
    legacy_directory = home / LEGACY_DATA_DIRECTORY
    if legacy_directory.exists() and not new_directory.exists():
        return legacy_directory
    return new_directory

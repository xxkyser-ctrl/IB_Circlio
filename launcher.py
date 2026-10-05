"""Prepare the ignored local token and extension configuration."""

import argparse
import secrets
from pathlib import Path

import encryption
from file_permissions import restrict_to_current_user


def prepare_local_config(data_dir, config_path):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    token = encryption.read_token(data_dir)
    if len(token) < 32 or any(character.isspace() for character in token):
        token = secrets.token_urlsafe(32)
    token_path = encryption.write_token(data_dir, token)

    config = (
        "// Generated locally. Do not commit this file.\n"
        'globalThis.INSTAGRAM_EXPORTER_API_BASE = "http://127.0.0.1:8765";\n'
        f'globalThis.INSTAGRAM_EXPORTER_TOKEN = "{token}";\n'
    )
    config_path = Path(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config, encoding="utf-8")
    restrict_to_current_user(config_path)
    return token_path


def prepare_extension_configs(data_dir, app_directory):
    app_directory = Path(app_directory)
    token_path = prepare_local_config(data_dir, app_directory / "config.js")
    for browser in ("chromium", "firefox"):
        extension_directory = app_directory / "extensions" / browser
        if extension_directory.is_dir():
            prepare_local_config(data_dir, extension_directory / "config.js")
    return token_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    prepare_local_config(args.data_dir, args.config)


if __name__ == "__main__":
    main()

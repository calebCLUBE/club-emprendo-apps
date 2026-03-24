#!/usr/bin/env python3
"""
Quick test: upload one local file to Google Drive using a service account.

Usage:
  DRIVE_SERVICE_ACCOUNT_JSON=/absolute/path/to/service-account.json \
  DRIVE_FOLDER_ID=your_folder_id \
  ./venv/bin/python scripts/quick_drive_test.py

Optional:
  DRIVE_LOCAL_FILE=/path/to/file.txt
  DRIVE_REMOTE_NAME=file_name_in_drive.txt
"""

from __future__ import annotations

import os
import pathlib
import sys

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


def _get_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required env var: {name}")
    return value


def main() -> int:
    try:
        key_path = _get_env("DRIVE_SERVICE_ACCOUNT_JSON")
        folder_id = _get_env("DRIVE_FOLDER_ID")
    except ValueError as exc:
        print(str(exc))
        print(
            "Set env vars first:\n"
            "  export DRIVE_SERVICE_ACCOUNT_JSON=/path/to/service-account.json\n"
            "  export DRIVE_FOLDER_ID=your_folder_id"
        )
        return 1

    local_file = os.getenv("DRIVE_LOCAL_FILE", "test_upload.txt").strip() or "test_upload.txt"
    remote_name = os.getenv("DRIVE_REMOTE_NAME", pathlib.Path(local_file).name).strip()

    if not pathlib.Path(key_path).exists():
        print(f"Service account JSON not found: {key_path}")
        return 1

    if not pathlib.Path(local_file).exists():
        print(f"Local file not found: {local_file}")
        return 1

    creds = Credentials.from_service_account_file(
        key_path,
        scopes=["https://www.googleapis.com/auth/drive.file"],
    )
    drive = build("drive", "v3", credentials=creds)

    metadata = {"name": remote_name, "parents": [folder_id]}
    media = MediaFileUpload(local_file, resumable=False)

    try:
        result = (
            drive.files()
            .create(body=metadata, media_body=media, fields="id,name,webViewLink")
            .execute()
        )
    except Exception as exc:
        print("Upload failed:", exc)
        print(
            "Checklist:\n"
            "1) Folder is shared with your service account email (Editor)\n"
            "2) DRIVE_FOLDER_ID is correct\n"
            "3) Drive API is enabled in Google Cloud"
        )
        return 1

    print("Upload success")
    print("File ID:", result.get("id"))
    print("Name:", result.get("name"))
    print("View:", result.get("webViewLink"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

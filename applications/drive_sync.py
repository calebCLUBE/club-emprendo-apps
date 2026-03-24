import json
import logging
import os
import re
from dataclasses import dataclass


logger = logging.getLogger(__name__)

FOLDER_MIMETYPE = "application/vnd.google-apps.folder"
GROUP_PREFIX_RE = re.compile(r"^\s*2\.(?P<num>\d+)\b", re.IGNORECASE)
FOLDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass
class DriveGroupSyncResult:
    status: str  # created | exists | skipped
    detail: str
    folder_name: str = ""
    folder_id: str = ""


def _month_label(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return v
    return v[:1].upper() + v[1:]


def _load_config() -> tuple[str, str, str]:
    key_path = (
        os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON", "").strip()
        or os.getenv("DRIVE_SERVICE_ACCOUNT_JSON", "").strip()
    )
    key_json = (
        os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON_CONTENT", "").strip()
        or os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_INFO", "").strip()
    )
    raw_root_folder = (
        os.getenv("GOOGLE_DRIVE_GROUPS_ROOT_FOLDER_ID", "").strip()
        or os.getenv("DRIVE_GROUPS_ROOT_FOLDER_ID", "").strip()
        or os.getenv("DRIVE_FOLDER_ID", "").strip()
    )
    root_folder_id = _normalize_folder_id(raw_root_folder)
    return key_path, key_json, root_folder_id


def _normalize_folder_id(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return ""

    m = re.search(r"/folders/([A-Za-z0-9_-]+)", v)
    if m:
        return m.group(1)

    m = re.search(r"[?&]id=([A-Za-z0-9_-]+)", v)
    if m:
        return m.group(1)

    last = v.rstrip("/").split("/")[-1]
    if FOLDER_ID_RE.fullmatch(last):
        return last
    if FOLDER_ID_RE.fullmatch(v):
        return v
    return ""


def _build_service(key_path: str, key_json: str = ""):
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    if key_json:
        try:
            info = json.loads(key_json)
        except Exception as exc:
            raise ValueError(
                "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON_CONTENT is not valid JSON."
            ) from exc
        creds = Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/drive"],
        )
    else:
        creds = Credentials.from_service_account_file(
            key_path,
            scopes=["https://www.googleapis.com/auth/drive"],
        )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _list_child_folders(service, parent_id: str) -> list[dict]:
    query = (
        f"'{parent_id}' in parents "
        f"and mimeType='{FOLDER_MIMETYPE}' "
        "and trashed=false"
    )
    out: list[dict] = []
    page_token = None
    while True:
        resp = (
            service.files()
            .list(
                q=query,
                fields="nextPageToken, files(id,name)",
                pageSize=200,
                pageToken=page_token,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
            )
            .execute()
        )
        out.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out


def _find_existing_group_folder(service, root_folder_id: str, group_num: int) -> dict | None:
    for folder in _list_child_folders(service, root_folder_id):
        name = (folder.get("name") or "").strip()
        m = GROUP_PREFIX_RE.search(name)
        if not m:
            continue
        try:
            num = int(m.group("num"))
        except Exception:
            continue
        if num == int(group_num):
            return folder
    return None


def _create_folder(service, name: str, parent_id: str) -> dict:
    body = {
        "name": name,
        "mimeType": FOLDER_MIMETYPE,
        "parents": [parent_id],
    }
    return (
        service.files()
        .create(
            body=body,
            fields="id,name,webViewLink",
            supportsAllDrives=True,
        )
        .execute()
    )


def ensure_group_drive_tree(
    *,
    group_num: int,
    start_month: str,
    end_month: str,
    year: int,
) -> DriveGroupSyncResult:
    raw_root = (
        os.getenv("GOOGLE_DRIVE_GROUPS_ROOT_FOLDER_ID", "").strip()
        or os.getenv("DRIVE_GROUPS_ROOT_FOLDER_ID", "").strip()
        or os.getenv("DRIVE_FOLDER_ID", "").strip()
    )
    key_path, key_json, root_folder_id = _load_config()
    if (not key_path and not key_json) or not root_folder_id:
        return DriveGroupSyncResult(
            status="skipped",
            detail=(
                "Drive sync skipped: set GOOGLE_DRIVE_GROUPS_ROOT_FOLDER_ID and either "
                "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON (path) or "
                "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON_CONTENT (JSON text)."
            ),
        )

    if raw_root and not root_folder_id:
        return DriveGroupSyncResult(
            status="skipped",
            detail=(
                "Drive sync skipped: root folder value could not be parsed. "
                "Use a folder id or a valid Drive folder URL."
            ),
        )

    if key_path and not os.path.exists(key_path):
        return DriveGroupSyncResult(
            status="skipped",
            detail=f"Drive sync skipped: key file not found at {key_path}.",
        )

    service = _build_service(key_path, key_json)
    existing = _find_existing_group_folder(service, root_folder_id, group_num)
    if existing:
        return DriveGroupSyncResult(
            status="exists",
            detail=f"Group G{group_num} already exists in Drive. No changes made.",
            folder_name=(existing.get("name") or ""),
            folder_id=(existing.get("id") or ""),
        )

    start_label = _month_label(start_month)
    end_label = _month_label(end_month)
    top_folder_name = f"2.{group_num} G{group_num} Mentorias - {start_label} a {end_label} {year}"

    top = _create_folder(service, top_folder_name, root_folder_id)
    top_id = top.get("id")

    if not top_id:
        raise RuntimeError("Drive API did not return an id for the group folder.")

    _create_folder(service, f"G{group_num} Certificados", top_id)
    _create_folder(service, "Actas de compromiso", top_id)
    _create_folder(service, f"G{group_num} Recursos Usados", top_id)
    _create_folder(service, f"G{group_num} Emparejamiento", top_id)
    apps_folder = _create_folder(service, f"G{group_num} Aplicaciones", top_id)

    apps_id = apps_folder.get("id")
    if not apps_id:
        raise RuntimeError("Drive API did not return an id for the applications folder.")

    _create_folder(service, "Mentoras", apps_id)
    _create_folder(service, "Emprendedoras", apps_id)

    logger.info(
        "Created Drive tree for G%s in folder %s (%s)",
        group_num,
        top_folder_name,
        top_id,
    )
    return DriveGroupSyncResult(
        status="created",
        detail=f"Drive folders created for G{group_num}.",
        folder_name=top_folder_name,
        folder_id=top_id,
    )

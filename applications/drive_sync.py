import json
import logging
import os
import re
import ast
import base64
import csv
import io
import threading
from urllib.parse import parse_qs, urlparse
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

FOLDER_MIMETYPE = "application/vnd.google-apps.folder"
GROUP_PREFIX_RE = re.compile(r"^\s*2\.(?P<num>\d+)\b", re.IGNORECASE)
FOLDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
GROUP_TRACK_FORM_RE = re.compile(r"^G(?P<num>\d+)_(?P<track>E|M)_A[12]$", re.IGNORECASE)
PAIR_FORM_RE = re.compile(r"^PAIR_G(?P<num>\d+)$", re.IGNORECASE)


@dataclass
class DriveGroupSyncResult:
    status: str  # created | exists | skipped
    detail: str
    folder_name: str = ""
    folder_id: str = ""


@dataclass
class DriveCsvSyncResult:
    status: str  # updated | skipped | error
    detail: str
    file_name: str = ""
    file_id: str = ""


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
    key_json_b64 = os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON_CONTENT_B64", "").strip()
    if key_json_b64:
        try:
            key_json = base64.b64decode(key_json_b64).decode("utf-8")
        except Exception:
            # Leave as-is; validation in parser will provide a clean error.
            key_json = key_json_b64
    raw_root_folder = (
        os.getenv("GOOGLE_DRIVE_GROUPS_ROOT_FOLDER_ID", "").strip()
        or os.getenv("DRIVE_GROUPS_ROOT_FOLDER_ID", "").strip()
        or os.getenv("DRIVE_FOLDER_ID", "").strip()
    )
    root_folder_id = _normalize_folder_id(raw_root_folder)
    return key_path, key_json, root_folder_id


def _oauth_env_config() -> tuple[str, str, str]:
    client_id = (os.getenv("GOOGLE_DRIVE_OAUTH_CLIENT_ID", "") or os.getenv("DRIVE_OAUTH_CLIENT_ID", "")).strip()
    client_secret = (os.getenv("GOOGLE_DRIVE_OAUTH_CLIENT_SECRET", "") or os.getenv("DRIVE_OAUTH_CLIENT_SECRET", "")).strip()
    refresh_token = (os.getenv("GOOGLE_DRIVE_OAUTH_REFRESH_TOKEN", "") or os.getenv("DRIVE_OAUTH_REFRESH_TOKEN", "")).strip()
    return client_id, client_secret, refresh_token


def _has_oauth_config(client_id: str, client_secret: str, refresh_token: str) -> bool:
    return bool(client_id and client_secret and refresh_token)


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


def _normalize_file_id(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return ""

    m = re.search(r"/d/([A-Za-z0-9_-]+)", v)
    if m:
        return m.group(1)

    m = re.search(r"[?&]id=([A-Za-z0-9_-]+)", v)
    if m:
        return m.group(1)

    last = v.rstrip("/").split("/")[-1]
    if FILE_ID_RE.fullmatch(last):
        return last
    if FILE_ID_RE.fullmatch(v):
        return v
    return ""


def _extract_sheet_gid(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    for source in (parsed.query or "", parsed.fragment or ""):
        if not source:
            continue
        params = parse_qs(source)
        gid_values = params.get("gid") or []
        if gid_values:
            gid = str(gid_values[0] or "").strip()
            if gid.isdigit():
                return gid
    return ""


def _build_service(
    key_path: str,
    key_json: str = "",
    oauth_client_id: str = "",
    oauth_client_secret: str = "",
    oauth_refresh_token: str = "",
):
    from google.oauth2.service_account import Credentials
    from google.oauth2.credentials import Credentials as UserCredentials
    from googleapiclient.discovery import build

    scopes = ["https://www.googleapis.com/auth/drive"]

    if _has_oauth_config(oauth_client_id, oauth_client_secret, oauth_refresh_token):
        creds = UserCredentials(
            token=None,
            refresh_token=oauth_refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=oauth_client_id,
            client_secret=oauth_client_secret,
            scopes=scopes,
        )
    elif key_json:
        try:
            info = _parse_service_account_info(key_json)
        except Exception as exc:
            raise ValueError(
                "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON_CONTENT is not valid JSON. "
                "Use full JSON object text (with double quotes), or set "
                "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON_CONTENT_B64."
            ) from exc
        creds = Credentials.from_service_account_info(
            info,
            scopes=scopes,
        )
    else:
        creds = Credentials.from_service_account_file(
            key_path,
            scopes=scopes,
        )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _build_sheets_service(
    key_path: str,
    key_json: str = "",
    oauth_client_id: str = "",
    oauth_client_secret: str = "",
    oauth_refresh_token: str = "",
):
    from google.oauth2.service_account import Credentials
    from google.oauth2.credentials import Credentials as UserCredentials
    from googleapiclient.discovery import build

    if _has_oauth_config(oauth_client_id, oauth_client_secret, oauth_refresh_token):
        # Refresh tokens cannot be expanded to newly requested scopes. The
        # existing deployment token was issued for Drive, and the Sheets API
        # accepts the full Drive scope for spreadsheet reads and writes.
        scopes = ["https://www.googleapis.com/auth/drive"]
        creds = UserCredentials(
            token=None,
            refresh_token=oauth_refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=oauth_client_id,
            client_secret=oauth_client_secret,
            scopes=scopes,
        )
    elif key_json:
        scopes = [
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/spreadsheets",
        ]
        creds = Credentials.from_service_account_info(
            _parse_service_account_info(key_json),
            scopes=scopes,
        )
    else:
        scopes = [
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/spreadsheets",
        ]
        creds = Credentials.from_service_account_file(key_path, scopes=scopes)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _friendly_upload_error(exc: Exception) -> str:
    text = str(exc or "").strip()
    if "storageQuotaExceeded" in text or "Service Accounts do not have storage quota" in text:
        return (
            "Google Drive blocked upload because service accounts have no personal storage quota. "
            "Use a Shared Drive root folder or set OAuth user credentials "
            "(GOOGLE_DRIVE_OAUTH_CLIENT_ID / GOOGLE_DRIVE_OAUTH_CLIENT_SECRET / GOOGLE_DRIVE_OAUTH_REFRESH_TOKEN)."
        )
    return text or repr(exc)


def _friendly_sheets_error(exc: Exception) -> str:
    text = str(exc or "").strip()
    if "sheets.googleapis.com" in text and (
        "SERVICE_DISABLED" in text or "has not been used" in text or "it is disabled" in text
    ):
        project_match = re.search(r"projects?[/ ](\d+)|project=(\d+)", text)
        project_id = next(
            (value for value in (project_match.groups() if project_match else ()) if value),
            "the Google Cloud project",
        )
        activation_match = re.search(
            r"https://console\.developers\.google\.com/apis/api/sheets\.googleapis\.com/overview\?project=\d+",
            text,
        )
        activation_url = (
            activation_match.group(0)
            if activation_match
            else "https://console.cloud.google.com/apis/library/sheets.googleapis.com"
        )
        return (
            f"Google Sheets API is disabled for project {project_id}. "
            f"Enable it here, wait a few minutes, then retry: {activation_url}"
        )
    if "PERMISSION_DENIED" in text or "The caller does not have permission" in text:
        return (
            "Google denied access to this spreadsheet. Share it with the configured Google account "
            "and give that account Editor access."
        )
    return text or repr(exc)


def _parse_service_account_info(raw_text: str) -> dict:
    """
    Parse service account JSON from env with tolerance for common paste mistakes:
    - Wrapped in extra single/double quotes
    - JSON string that itself contains JSON
    - Python dict literal with single quotes
    """
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("empty service account JSON text")

    candidates = [text]
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        candidates.append(text[1:-1].strip())

    for candidate in candidates:
        # First try strict JSON.
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, str):
                parsed = json.loads(parsed)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        # Fallback: tolerate Python dict style {'k': 'v'}.
        try:
            parsed = ast.literal_eval(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

    raise ValueError("unparseable JSON content")


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


def _list_children(service, parent_id: str) -> list[dict]:
    query = f"'{parent_id}' in parents and trashed=false"
    out: list[dict] = []
    page_token = None
    while True:
        resp = (
            service.files()
            .list(
                q=query,
                fields="nextPageToken, files(id,name,mimeType)",
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


def _find_child_folder_by_name(service, parent_id: str, name: str) -> dict | None:
    target = (name or "").strip().lower()
    for item in _list_child_folders(service, parent_id):
        if (item.get("name") or "").strip().lower() == target:
            return item
    return None


def _find_child_item_by_name(service, parent_id: str, name: str) -> dict | None:
    target = (name or "").strip().lower()
    for item in _list_children(service, parent_id):
        if (item.get("name") or "").strip().lower() == target:
            return item
    return None


def _upsert_csv_file(service, parent_id: str, filename: str, csv_text: str) -> dict:
    from googleapiclient.http import MediaIoBaseUpload

    data = (csv_text or "").encode("utf-8")
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype="text/csv", resumable=False)
    existing = _find_child_item_by_name(service, parent_id, filename)
    if existing and existing.get("id"):
        return (
            service.files()
            .update(
                fileId=existing["id"],
                media_body=media,
                fields="id,name",
                supportsAllDrives=True,
            )
            .execute()
        )
    return (
        service.files()
        .create(
            body={"name": filename, "parents": [parent_id]},
            media_body=media,
            fields="id,name",
            supportsAllDrives=True,
        )
        .execute()
    )


def _resolve_group_folder(service, root_folder_id: str, group_num: int) -> dict | None:
    return _find_existing_group_folder(service, root_folder_id, group_num)


def _resolve_track_target_folder_id(service, root_folder_id: str, group_num: int, track: str) -> str | None:
    group_folder = _resolve_group_folder(service, root_folder_id, group_num)
    if not group_folder or not group_folder.get("id"):
        return None
    apps_folder = _find_child_folder_by_name(service, group_folder["id"], f"G{group_num} Aplicaciones")
    if not apps_folder or not apps_folder.get("id"):
        return None
    role_folder_name = "Mentoras" if (track or "").upper() == "M" else "Emprendedoras"
    role_folder = _find_child_folder_by_name(service, apps_folder["id"], role_folder_name)
    return role_folder.get("id") if role_folder else None


def _resolve_pairing_target_folder_id(service, root_folder_id: str, group_num: int) -> str | None:
    group_folder = _resolve_group_folder(service, root_folder_id, group_num)
    if not group_folder or not group_folder.get("id"):
        return None
    pairing_folder = _find_child_folder_by_name(service, group_folder["id"], f"G{group_num} Emparejamiento")
    return pairing_folder.get("id") if pairing_folder else None


def _service_and_root() -> tuple[object, str]:
    raw_root = (
        os.getenv("GOOGLE_DRIVE_GROUPS_ROOT_FOLDER_ID", "").strip()
        or os.getenv("DRIVE_GROUPS_ROOT_FOLDER_ID", "").strip()
        or os.getenv("DRIVE_FOLDER_ID", "").strip()
    )
    key_path, key_json, root_folder_id = _load_config()
    oauth_client_id, oauth_client_secret, oauth_refresh_token = _oauth_env_config()
    has_oauth = _has_oauth_config(oauth_client_id, oauth_client_secret, oauth_refresh_token)
    if (not key_path and not key_json and not has_oauth) or not root_folder_id:
        raise RuntimeError(
            "Missing Drive config. Set GOOGLE_DRIVE_GROUPS_ROOT_FOLDER_ID and "
            "either service-account credentials (GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON_CONTENT_B64 "
            "or JSON/path variants) or OAuth credentials "
            "(GOOGLE_DRIVE_OAUTH_CLIENT_ID/SECRET/REFRESH_TOKEN)."
        )
    if raw_root and not root_folder_id:
        raise RuntimeError(
            "Drive root folder value could not be parsed. Use a folder ID or valid Drive folder URL."
        )
    if key_path and not key_json and not has_oauth and not os.path.exists(key_path):
        raise RuntimeError(f"Drive key file not found at {key_path}.")
    return (
        _build_service(
            key_path,
            key_json,
            oauth_client_id=oauth_client_id,
            oauth_client_secret=oauth_client_secret,
            oauth_refresh_token=oauth_refresh_token,
        ),
        root_folder_id,
    )


def _service_for_drive_reads() -> object:
    key_path, key_json, _root_folder_id = _load_config()
    oauth_client_id, oauth_client_secret, oauth_refresh_token = _oauth_env_config()
    has_oauth = _has_oauth_config(oauth_client_id, oauth_client_secret, oauth_refresh_token)
    if not key_path and not key_json and not has_oauth:
        raise RuntimeError(
            "Missing Drive credentials. Set service-account credentials "
            "(GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON_CONTENT_B64 or JSON/path variants) "
            "or OAuth credentials "
            "(GOOGLE_DRIVE_OAUTH_CLIENT_ID/SECRET/REFRESH_TOKEN)."
        )
    if key_path and not key_json and not has_oauth and not os.path.exists(key_path):
        raise RuntimeError(f"Drive key file not found at {key_path}.")
    return _build_service(
        key_path,
        key_json,
        oauth_client_id=oauth_client_id,
        oauth_client_secret=oauth_client_secret,
        oauth_refresh_token=oauth_refresh_token,
    )


def _service_for_sheets() -> object:
    key_path, key_json, _root_folder_id = _load_config()
    oauth_client_id, oauth_client_secret, oauth_refresh_token = _oauth_env_config()
    has_oauth = _has_oauth_config(oauth_client_id, oauth_client_secret, oauth_refresh_token)
    if not key_path and not key_json and not has_oauth:
        raise RuntimeError(
            "Missing Google credentials. Configure the Drive service account or OAuth credentials."
        )
    if key_path and not key_json and not has_oauth and not os.path.exists(key_path):
        raise RuntimeError(f"Drive key file not found at {key_path}.")
    return _build_sheets_service(
        key_path,
        key_json,
        oauth_client_id=oauth_client_id,
        oauth_client_secret=oauth_client_secret,
        oauth_refresh_token=oauth_refresh_token,
    )


def _quoted_sheet_title(title: str) -> str:
    return "'" + str(title or "").replace("'", "''") + "'"


def _sheet_values_range(title: str) -> str:
    # The Sheets values API does not accept a bare quoted tab title. Include
    # an explicit A1 grid range; the response still trims unused trailing cells.
    return f"{_quoted_sheet_title(title)}!A:ZZZ"


def fetch_google_spreadsheet_tabs(file_ref: str) -> dict:
    """Read every tab from a Google Sheet, preserving tab and cell order."""
    file_id = _normalize_file_id(file_ref)
    if not file_id:
        raise ValueError("Invalid Google Sheet link. Paste the full Sheets URL.")

    service = _service_for_sheets()
    try:
        metadata = (
            service.spreadsheets()
            .get(
                spreadsheetId=file_id,
                fields="spreadsheetId,properties.title,sheets.properties(sheetId,title,index)",
            )
            .execute()
        )
    except Exception as exc:
        raise RuntimeError(_friendly_sheets_error(exc)) from exc
    sheets = sorted(
        metadata.get("sheets") or [],
        key=lambda item: int((item.get("properties") or {}).get("index") or 0),
    )
    titles = [str((item.get("properties") or {}).get("title") or "").strip() for item in sheets]
    titles = [title for title in titles if title]
    if not titles:
        raise ValueError("The linked Google Sheet has no tabs.")

    try:
        values_response = (
            service.spreadsheets()
            .values()
            .batchGet(
                spreadsheetId=file_id,
                ranges=[_sheet_values_range(title) for title in titles],
                majorDimension="ROWS",
                valueRenderOption="UNFORMATTED_VALUE",
            )
            .execute()
        )
    except Exception as exc:
        raise RuntimeError(_friendly_sheets_error(exc)) from exc
    value_ranges = values_response.get("valueRanges") or []
    tabs = []
    for index, title in enumerate(titles):
        props = sheets[index].get("properties") or {}
        values = value_ranges[index].get("values") if index < len(value_ranges) else []
        tabs.append(
            {
                "title": title,
                "sheet_id": int(props.get("sheetId") or 0),
                "values": values or [],
            }
        )
    return {
        "spreadsheet_id": file_id,
        "title": str((metadata.get("properties") or {}).get("title") or file_id),
        "tabs": tabs,
    }


def update_google_spreadsheet_values(file_ref: str, updates: list[dict]) -> int:
    """Write RAW values to explicit A1 ranges in a linked Google Sheet."""
    file_id = _normalize_file_id(file_ref)
    if not file_id:
        raise ValueError("Invalid Google Sheet link. Paste the full Sheets URL.")
    clean_updates = [
        {"range": str(item.get("range") or ""), "values": item.get("values") or []}
        for item in updates
        if str(item.get("range") or "").strip()
    ]
    if not clean_updates:
        return 0
    service = _service_for_sheets()
    try:
        response = (
            service.spreadsheets()
            .values()
            .batchUpdate(
                spreadsheetId=file_id,
                body={"valueInputOption": "RAW", "data": clean_updates},
            )
            .execute()
        )
    except Exception as exc:
        raise RuntimeError(_friendly_sheets_error(exc)) from exc
    return int(response.get("totalUpdatedCells") or 0)


def fetch_drive_csv_file_text(file_ref: str) -> tuple[str, str, str]:
    """
    Download a CSV payload from Drive.

    Supports:
    - Google Sheets files (exported to CSV)
    - Regular CSV files stored in Drive

    Returns:
      (csv_text, file_id, file_name)
    """
    file_id = _normalize_file_id(file_ref)
    if not file_id:
        raise ValueError("Invalid Drive file reference. Use a file ID or full Drive/Sheets URL.")

    service = _service_for_drive_reads()
    meta = (
        service.files()
        .get(
            fileId=file_id,
            fields="id,name,mimeType",
            supportsAllDrives=True,
        )
        .execute()
    )

    mime_type = str(meta.get("mimeType") or "").strip().lower()
    file_name = str(meta.get("name") or "").strip() or file_id

    if mime_type == "application/vnd.google-apps.spreadsheet":
        raw = None
        gid = _extract_sheet_gid(file_ref)
        if gid:
            try:
                creds = getattr(getattr(service, "_http", None), "credentials", None)
                token = ""
                if creds is not None:
                    try:
                        from google.auth.transport.requests import Request

                        if not getattr(creds, "valid", False) or not getattr(creds, "token", ""):
                            creds.refresh(Request())
                    except Exception:
                        pass
                    token = str(getattr(creds, "token", "") or "").strip()
                headers = {"Accept": "text/csv"}
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                export_url = f"https://docs.google.com/spreadsheets/d/{file_id}/export"
                resp = httpx.get(
                    export_url,
                    params={"format": "csv", "gid": gid},
                    headers=headers,
                    timeout=30,
                    follow_redirects=True,
                )
                if resp.status_code < 400 and resp.content:
                    raw = resp.content
            except Exception:
                raw = None
        if raw is None:
            raw = (
                service.files()
                .export_media(
                    fileId=file_id,
                    mimeType="text/csv",
                )
                .execute()
            )
    elif mime_type in {"text/csv", "application/csv", "application/vnd.ms-excel"}:
        raw = (
            service.files()
            .get_media(
                fileId=file_id,
                supportsAllDrives=True,
            )
            .execute()
        )
    else:
        raise RuntimeError(
            f"Unsupported Drive file type for CSV preview: {mime_type or 'unknown'}."
        )

    if isinstance(raw, bytes):
        csv_text = raw.decode("utf-8-sig", errors="replace")
    else:
        csv_text = str(raw or "")
    return csv_text, file_id, file_name


def _build_group_track_rows(group_num: int, track: str) -> tuple[list[str], list[list[str]]]:
    from applications.models import Application, FormDefinition

    t = (track or "").upper().strip()
    if t not in {"E", "M"}:
        return [], []

    # Include classic slugs (G#_E_A1/A2, G#_M_A1/A2) and combined variants (e.g. G#_E... / G#_M...).
    forms = [
        fd
        for fd in FormDefinition.objects.filter(group__number=group_num, is_master=False)
        if re.match(rf"^G{int(group_num)}_{t}(?:_|$)", (fd.slug or "").strip(), flags=re.IGNORECASE)
    ]
    if not forms:
        return [], []

    question_slugs: list[str] = []
    seen: set[str] = set()
    for fd in forms:
        for q in fd.questions.filter(active=True).order_by("position", "id"):
            if q.slug in seen:
                continue
            seen.add(q.slug)
            question_slugs.append(q.slug)

    headers = ["created_at", "application_id", "group_number", "name", "email"] + question_slugs

    apps = (
        Application.objects.filter(form__in=forms)
        .select_related("form", "form__group")
        .prefetch_related("answers__question")
        .order_by("created_at", "id")
    )
    rows: list[list[str]] = []
    for app in apps:
        amap = {a.question.slug: (a.value or "") for a in app.answers.all() if getattr(a, "question_id", None)}
        rows.append(
            [
                app.created_at.isoformat(),
                str(app.id),
                str(group_num),
                app.name or "",
                app.email or "",
            ]
            + [amap.get(slug, "") for slug in question_slugs]
        )
    return headers, rows


def _rows_to_csv_text(headers: list[str], rows: list[list[str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    return buf.getvalue()


def sync_group_track_responses_csv(group_num: int, track: str) -> DriveCsvSyncResult:
    try:
        service, root_folder_id = _service_and_root()
    except Exception as exc:
        return DriveCsvSyncResult(status="skipped", detail=str(exc))

    target_folder_id = _resolve_track_target_folder_id(service, root_folder_id, group_num, track)
    if not target_folder_id:
        return DriveCsvSyncResult(
            status="skipped",
            detail=f"Target folder not found for G{group_num} track {track}.",
        )

    filename = f"G{group_num}_{track.upper()} Respuestas.csv"
    headers, rows = _build_group_track_rows(group_num, track)
    no_forms = not headers
    if no_forms:
        # Ensure a placeholder CSV exists for brand-new groups even before submissions/forms are present.
        headers = ["created_at", "application_id", "group_number", "name", "email"]
        rows = []

    csv_text = _rows_to_csv_text(headers, rows)
    try:
        uploaded = _upsert_csv_file(service, target_folder_id, filename, csv_text)
    except Exception as exc:
        return DriveCsvSyncResult(
            status="error",
            detail=f"Upload failed for {filename}: {_friendly_upload_error(exc)}",
            file_name=filename,
        )
    return DriveCsvSyncResult(
        status="updated",
        detail=(
            f"Synced {filename} (placeholder; no forms found yet)."
            if no_forms
            else f"Synced {filename}"
        ),
        file_name=filename,
        file_id=(uploaded.get("id") or ""),
    )


def _sync_group_track_responses_csv_safe(group_num: int, track: str) -> None:
    try:
        res = sync_group_track_responses_csv(group_num, track)
        logger.info("Drive responses sync: %s", res.detail)
    except Exception:
        logger.exception("Drive responses sync failed for G%s track %s", group_num, track)


def schedule_group_track_responses_sync(group_num: int, track: str) -> None:
    t = threading.Thread(
        target=_sync_group_track_responses_csv_safe,
        args=(int(group_num), (track or "").upper()),
        daemon=True,
    )
    t.start()


def sync_generated_csv_artifact(form_slug: str, csv_text: str) -> DriveCsvSyncResult:
    slug = (form_slug or "").strip()
    m_track = GROUP_TRACK_FORM_RE.match(slug)
    m_pair = PAIR_FORM_RE.match(slug)

    if not m_track and not m_pair:
        return DriveCsvSyncResult(status="skipped", detail=f"Unsupported artifact slug: {slug}")

    try:
        service, root_folder_id = _service_and_root()
    except Exception as exc:
        return DriveCsvSyncResult(status="skipped", detail=str(exc))

    if m_track:
        group_num = int(m_track.group("num"))
        track = m_track.group("track").upper()
        target_folder_id = _resolve_track_target_folder_id(service, root_folder_id, group_num, track)
        if not target_folder_id:
            return DriveCsvSyncResult(
                status="skipped",
                detail=f"Target folder not found for graded file {slug}.",
            )
        filename = f"G{group_num}_{track} Graded.csv"
        try:
            uploaded = _upsert_csv_file(service, target_folder_id, filename, csv_text or "")
        except Exception as exc:
            return DriveCsvSyncResult(
                status="error",
                detail=f"Upload failed for {filename}: {_friendly_upload_error(exc)}",
                file_name=filename,
            )
        return DriveCsvSyncResult(
            status="updated",
            detail=f"Synced {filename}",
            file_name=filename,
            file_id=(uploaded.get("id") or ""),
        )

    group_num = int(m_pair.group("num"))
    target_folder_id = _resolve_pairing_target_folder_id(service, root_folder_id, group_num)
    if not target_folder_id:
        return DriveCsvSyncResult(
            status="skipped",
            detail=f"Target pairing folder not found for {slug}.",
        )
    filename = f"G{group_num} Emparejamiento.csv"
    try:
        uploaded = _upsert_csv_file(service, target_folder_id, filename, csv_text or "")
    except Exception as exc:
        return DriveCsvSyncResult(
            status="error",
            detail=f"Upload failed for {filename}: {_friendly_upload_error(exc)}",
            file_name=filename,
        )
    return DriveCsvSyncResult(
        status="updated",
        detail=f"Synced {filename}",
        file_name=filename,
        file_id=(uploaded.get("id") or ""),
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
    oauth_client_id, oauth_client_secret, oauth_refresh_token = _oauth_env_config()
    has_oauth = _has_oauth_config(oauth_client_id, oauth_client_secret, oauth_refresh_token)
    if (not key_path and not key_json and not has_oauth) or not root_folder_id:
        return DriveGroupSyncResult(
            status="skipped",
            detail=(
                "Drive sync skipped: set GOOGLE_DRIVE_GROUPS_ROOT_FOLDER_ID and either "
                "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON (path) or "
                "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON_CONTENT (JSON text), or set OAuth envs "
                "(GOOGLE_DRIVE_OAUTH_CLIENT_ID/SECRET/REFRESH_TOKEN)."
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

    if key_path and not key_json and not has_oauth and not os.path.exists(key_path):
        return DriveGroupSyncResult(
            status="skipped",
            detail=f"Drive sync skipped: key file not found at {key_path}.",
        )

    service = _build_service(
        key_path,
        key_json,
        oauth_client_id=oauth_client_id,
        oauth_client_secret=oauth_client_secret,
        oauth_refresh_token=oauth_refresh_token,
    )
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

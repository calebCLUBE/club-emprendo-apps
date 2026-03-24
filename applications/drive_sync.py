import json
import logging
import os
import re
import ast
import base64
import csv
import io
import threading
from dataclasses import dataclass


logger = logging.getLogger(__name__)

FOLDER_MIMETYPE = "application/vnd.google-apps.folder"
GROUP_PREFIX_RE = re.compile(r"^\s*2\.(?P<num>\d+)\b", re.IGNORECASE)
FOLDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
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
            info = _parse_service_account_info(key_json)
        except Exception as exc:
            raise ValueError(
                "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON_CONTENT is not valid JSON. "
                "Use full JSON object text (with double quotes), or set "
                "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON_CONTENT_B64."
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
    if (not key_path and not key_json) or not root_folder_id:
        raise RuntimeError(
            "Missing Drive config. Set GOOGLE_DRIVE_GROUPS_ROOT_FOLDER_ID and "
            "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON_CONTENT_B64 (or JSON/path variants)."
        )
    if raw_root and not root_folder_id:
        raise RuntimeError(
            "Drive root folder value could not be parsed. Use a folder ID or valid Drive folder URL."
        )
    if key_path and not os.path.exists(key_path):
        raise RuntimeError(f"Drive key file not found at {key_path}.")
    return _build_service(key_path, key_json), root_folder_id


def _build_group_track_rows(group_num: int, track: str) -> tuple[list[str], list[list[str]]]:
    from applications.models import Application, FormDefinition

    t = (track or "").upper().strip()
    if t not in {"E", "M"}:
        return [], []

    suffixes = [f"_{t}_A1", f"_{t}_A2"]
    forms = [
        fd
        for fd in FormDefinition.objects.filter(group__number=group_num)
        if (fd.slug or "").endswith(suffixes[0]) or (fd.slug or "").endswith(suffixes[1])
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

    headers, rows = _build_group_track_rows(group_num, track)
    if not headers:
        return DriveCsvSyncResult(
            status="skipped",
            detail=f"No forms found for G{group_num} track {track}.",
        )

    csv_text = _rows_to_csv_text(headers, rows)
    filename = f"G{group_num}_{track.upper()} Respuestas.csv"
    uploaded = _upsert_csv_file(service, target_folder_id, filename, csv_text)
    return DriveCsvSyncResult(
        status="updated",
        detail=f"Synced {filename}",
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
        uploaded = _upsert_csv_file(service, target_folder_id, filename, csv_text or "")
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
    uploaded = _upsert_csv_file(service, target_folder_id, filename, csv_text or "")
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

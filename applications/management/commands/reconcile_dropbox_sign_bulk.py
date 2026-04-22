from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import httpx

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from applications.admin_profiles_views import (
    _EMPRENDEDORA_GROUP_TITLE_RE,
    _MENTORA_TITLE_RE,
    _apply_contract_signed_to_rows,
    _build_emprendedoras_rows,
    _build_mentoras_rows,
    _clean_valid_emails,
    _latest_apps_by_email_for_group_track,
    _mark_participant_sheet_acta_signed,
    _number_sheet_rows,
    _participant_emails_from_list,
    _signature_status_is_signed,
    EMPRENDEDORAS_ACTA_COL,
    EMPRENDEDORAS_EMAIL_COL,
    MENTORAS_ACTA_COL,
    MENTORAS_EMAIL_COL,
)
from applications.models import FormGroup, GroupParticipantList, ParticipantEmailStatus


@dataclass
class SignatureRequestLite:
    signature_request_id: str
    title: str
    created_at: int | None
    signer_emails: list[str]
    signed_emails: list[str]


class Command(BaseCommand):
    help = (
        "Passive Dropbox Sign reconciliation for bulk sends. "
        "Marks contract signed + Acta checkboxes for a target group by signer email."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--group",
            required=True,
            type=int,
            help="Target group number to reconcile (e.g. 800).",
        )
        parser.add_argument(
            "--track",
            default="both",
            choices=["E", "M", "both"],
            help="Which participant track to update (default: both).",
        )
        parser.add_argument(
            "--days-back",
            type=int,
            default=3650,
            help="Only inspect requests created in the last N days (default: 3650).",
        )
        parser.add_argument(
            "--max-pages",
            type=int,
            default=250,
            help="Max pages to fetch from Dropbox Sign list API (default: 250).",
        )
        parser.add_argument(
            "--title-contains",
            default="acta de compromiso",
            help="Case-insensitive title filter (default: 'acta de compromiso').",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be updated without writing to DB.",
        )
        parser.add_argument(
            "--api-base",
            default="",
            help=(
                "Optional API base override, e.g. https://api.hellosign.com/v3 "
                "(default tries api.hellosign.com then api.dropboxsign.com)."
            ),
        )

    def handle(self, *args, **options):
        group_num = int(options["group"])
        track_opt = str(options.get("track") or "both").strip().upper()
        days_back = int(options.get("days_back") or 3650)
        max_pages = int(options.get("max_pages") or 250)
        title_contains = str(options.get("title_contains") or "").strip().lower()
        dry_run = bool(options.get("dry_run"))
        api_base_override = str(options.get("api_base") or "").strip()

        api_key = (getattr(settings, "DROPBOX_SIGN_API_KEY", "") or "").strip()
        if not api_key:
            raise CommandError("DROPBOX_SIGN_API_KEY is not configured.")

        group = FormGroup.objects.filter(number=group_num).first()
        if not group:
            raise CommandError(f"Group {group_num} was not found.")
        participant_list = GroupParticipantList.objects.filter(group=group).first()
        if not participant_list:
            mentoras_emails = sorted(_latest_apps_by_email_for_group_track(group_num, "M").keys())
            emprendedoras_emails = sorted(_latest_apps_by_email_for_group_track(group_num, "E").keys())
            mentoras_rows = _build_mentoras_rows(group_num, mentoras_emails)
            emprendedoras_rows = _build_emprendedoras_rows(group_num, emprendedoras_emails)
            mentoras_rows = _apply_contract_signed_to_rows(
                mentoras_rows,
                email_col=MENTORAS_EMAIL_COL,
                acta_col=MENTORAS_ACTA_COL,
            )
            emprendedoras_rows = _apply_contract_signed_to_rows(
                emprendedoras_rows,
                email_col=EMPRENDEDORAS_EMAIL_COL,
                acta_col=EMPRENDEDORAS_ACTA_COL,
            )
            participant_list, _ = GroupParticipantList.objects.update_or_create(
                group=group,
                defaults={
                    "mentoras_emails_text": "\n".join(mentoras_emails),
                    "emprendedoras_emails_text": "\n".join(emprendedoras_emails),
                    "mentoras_sheet_rows": _number_sheet_rows(mentoras_rows, number_col=2),
                    "emprendedoras_sheet_rows": _number_sheet_rows(emprendedoras_rows, number_col=2),
                },
            )
            self.stdout.write(
                self.style.WARNING(
                    (
                        f"Group {group_num} had no participant list; rebuilt from assigned applications "
                        f"(M:{len(mentoras_emails)} E:{len(emprendedoras_emails)})."
                    )
                )
            )

        e_pool = _participant_emails_from_list(participant_list, "E")
        m_pool = _participant_emails_from_list(participant_list, "M")
        if track_opt in {"E", "BOTH"} and not e_pool:
            self.stdout.write(self.style.WARNING(f"Group {group_num} has no Emprendedora participant emails."))
        if track_opt in {"M", "BOTH"} and not m_pool:
            self.stdout.write(self.style.WARNING(f"Group {group_num} has no Mentora participant emails."))

        base_candidates = (
            [api_base_override]
            if api_base_override
            else [
                "https://api.hellosign.com/v3",
                "https://api.dropboxsign.com/v3",
            ]
        )

        since_ts = int((timezone.now() - timedelta(days=max(days_back, 0))).timestamp())
        requests = self._fetch_signature_requests(
            api_key=api_key,
            base_candidates=base_candidates,
            max_pages=max(max_pages, 1),
            since_ts=since_ts,
        )
        self.stdout.write(f"Fetched {len(requests)} signature request(s) to inspect.")

        signed_by_track: dict[str, set[str]] = {"E": set(), "M": set()}
        request_ids_by_email: dict[str, str] = {}
        matched_reqs_by_track = {"E": 0, "M": 0}
        skipped_no_scope = 0
        skipped_no_signed = 0
        skipped_not_target = 0

        for req in requests:
            title = (req.title or "").strip()
            if title_contains and title_contains not in title.lower():
                continue

            signer_pool = set(req.signer_emails)
            signed_set = set(req.signed_emails)
            if not signed_set:
                skipped_no_signed += 1
                continue

            applies_e = False
            applies_m = False

            em = _EMPRENDEDORA_GROUP_TITLE_RE.search(title)
            if em and track_opt in {"E", "BOTH"}:
                try:
                    title_group_num = int(em.group(1))
                except Exception:
                    title_group_num = -1
                applies_e = title_group_num == group_num

            if _MENTORA_TITLE_RE.search(title) and track_opt in {"M", "BOTH"}:
                applies_m = True

            if not applies_e and not applies_m:
                skipped_no_scope += 1
                continue

            if applies_e:
                # Group number in title is the primary scope key for emprendedoras.
                matched_signed = sorted(email for email in signed_set if email in e_pool)
                if matched_signed:
                    matched_reqs_by_track["E"] += 1
                    for email in matched_signed:
                        signed_by_track["E"].add(email)
                        if req.signature_request_id and email not in request_ids_by_email:
                            request_ids_by_email[email] = req.signature_request_id

            if applies_m:
                # Mentora titles are groupless; target by signer membership in the selected group pool.
                matched_signed = sorted(email for email in signed_set if email in m_pool)
                if matched_signed:
                    if signer_pool and m_pool and not signer_pool.issubset(m_pool):
                        # Keep a counter for diagnostics, but still apply signed emails that
                        # clearly belong to this target group.
                        skipped_not_target += 1
                    matched_reqs_by_track["M"] += 1
                    for email in matched_signed:
                        signed_by_track["M"].add(email)
                        if req.signature_request_id and email not in request_ids_by_email:
                            request_ids_by_email[email] = req.signature_request_id

        union_signed = sorted(set().union(*signed_by_track.values()))
        self.stdout.write(
            (
                f"Matched signed emails -> E:{len(signed_by_track['E'])} "
                f"M:{len(signed_by_track['M'])} unique:{len(union_signed)}"
            )
        )
        self.stdout.write(
            (
                f"Request counters -> E:{matched_reqs_by_track['E']} M:{matched_reqs_by_track['M']} "
                f"skipped_no_scope:{skipped_no_scope} skipped_no_signed:{skipped_no_signed} "
                f"skipped_not_target:{skipped_not_target}"
            )
        )

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN: no database updates were applied."))
            return

        now = timezone.now()
        status_marked = 0
        status_unchanged = 0
        for email in union_signed:
            row, _created = ParticipantEmailStatus.objects.get_or_create(
                email=email,
                defaults={"participated": False},
            )
            changed = False
            if not row.contract_signed:
                row.contract_signed = True
                changed = True
            if not row.contract_signed_at:
                row.contract_signed_at = now
                changed = True
            req_id = request_ids_by_email.get(email, "")
            if req_id and row.contract_signature_request_id != req_id:
                row.contract_signature_request_id = req_id
                changed = True
            if row.contract_source != "dropbox_sign_passive":
                row.contract_source = "dropbox_sign_passive"
                changed = True
            if changed:
                row.save(
                    update_fields=[
                        "contract_signed",
                        "contract_signed_at",
                        "contract_signature_request_id",
                        "contract_source",
                        "updated_at",
                    ]
                )
                status_marked += 1
            else:
                status_unchanged += 1

        e_rows_matched = e_rows_changed = 0
        m_rows_matched = m_rows_changed = 0

        if signed_by_track["E"] and track_opt in {"E", "BOTH"}:
            e_rows_matched, e_rows_changed, e_note = _mark_participant_sheet_acta_signed(
                group_num=group_num,
                track="E",
                signed_emails=sorted(signed_by_track["E"]),
            )
            self.stdout.write(f"E Acta update -> matched:{e_rows_matched} changed:{e_rows_changed} note:{e_note}")

        if signed_by_track["M"] and track_opt in {"M", "BOTH"}:
            m_rows_matched, m_rows_changed, m_note = _mark_participant_sheet_acta_signed(
                group_num=group_num,
                track="M",
                signed_emails=sorted(signed_by_track["M"]),
            )
            self.stdout.write(f"M Acta update -> matched:{m_rows_matched} changed:{m_rows_changed} note:{m_note}")

        self.stdout.write(
            self.style.SUCCESS(
                (
                    f"Done. ParticipantEmailStatus updated:{status_marked} unchanged:{status_unchanged} | "
                    f"Acta changed -> E:{e_rows_changed} M:{m_rows_changed}"
                )
            )
        )

    def _fetch_signature_requests(
        self,
        *,
        api_key: str,
        base_candidates: list[str],
        max_pages: int,
        since_ts: int,
    ) -> list[SignatureRequestLite]:
        errors: list[str] = []
        for base in base_candidates:
            try:
                return self._fetch_signature_requests_from_base(
                    api_key=api_key,
                    base=base.rstrip("/"),
                    max_pages=max_pages,
                    since_ts=since_ts,
                )
            except Exception as exc:
                errors.append(f"{base}: {exc}")
                continue
        joined = " | ".join(errors) or "unknown error"
        raise CommandError(f"Could not fetch signature requests from Dropbox Sign API. {joined}")

    def _fetch_signature_requests_from_base(
        self,
        *,
        api_key: str,
        base: str,
        max_pages: int,
        since_ts: int,
    ) -> list[SignatureRequestLite]:
        out: list[SignatureRequestLite] = []
        url = f"{base}/signature_request/list"

        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            for page in range(1, max_pages + 1):
                resp = client.get(url, params={"page": page}, auth=(api_key, ""))
                if resp.status_code == 404:
                    raise RuntimeError("404 Not Found")
                if resp.status_code >= 400:
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:240]}")

                data = resp.json() if resp.content else {}
                reqs = data.get("signature_requests") or []
                if not isinstance(reqs, list):
                    reqs = []

                if not reqs:
                    break

                for raw_req in reqs:
                    lite = self._to_lite_request(raw_req)
                    created_at = lite.created_at or 0
                    if created_at and created_at < since_ts:
                        continue
                    out.append(lite)

                list_info = data.get("list_info") or {}
                try:
                    num_pages = int(list_info.get("num_pages") or 0)
                except Exception:
                    num_pages = 0
                try:
                    cur_page = int(list_info.get("page") or page)
                except Exception:
                    cur_page = page

                has_more = bool(list_info.get("has_more"))
                if num_pages and cur_page >= num_pages and not has_more:
                    break
                if not num_pages and not has_more:
                    break

        return out

    def _to_lite_request(self, raw_req: dict) -> SignatureRequestLite:
        signature_request_id = str(raw_req.get("signature_request_id") or "").strip()
        title = str(raw_req.get("title") or "").strip()
        created_at_raw = raw_req.get("created_at")
        try:
            created_at = int(created_at_raw) if created_at_raw is not None else None
        except Exception:
            created_at = None

        signatures = raw_req.get("signatures") or []
        if not isinstance(signatures, list):
            signatures = []

        signer_emails: list[str] = []
        signed_emails: list[str] = []
        for sig in signatures:
            if not isinstance(sig, dict):
                continue
            email = (
                sig.get("signer_email_address")
                or sig.get("email_address")
                or sig.get("email")
            )
            email_norm = str(email or "").strip().lower()
            if not email_norm:
                continue
            signer_emails.append(email_norm)

            status_raw = (
                sig.get("status_code")
                or sig.get("status")
                or sig.get("signer_status_code")
                or sig.get("state")
            )
            signed_at_raw = str(sig.get("signed_at") or "").strip()
            if _signature_status_is_signed(status_raw) or bool(signed_at_raw):
                signed_emails.append(email_norm)

        signer_emails = _clean_valid_emails(signer_emails)
        signer_set = set(signer_emails)
        req_complete = bool(raw_req.get("is_complete"))
        req_declined = bool(raw_req.get("is_declined"))
        signed_emails = [email for email in _clean_valid_emails(signed_emails) if email in signer_set]
        if req_complete and not req_declined and not signed_emails and signer_emails:
            # Some list payloads omit detailed per-signer status when complete.
            signed_emails = list(signer_emails)

        return SignatureRequestLite(
            signature_request_id=signature_request_id,
            title=title,
            created_at=created_at,
            signer_emails=signer_emails,
            signed_emails=signed_emails,
        )

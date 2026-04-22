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
    bulk_send_job_id: str
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
            "--title-exact",
            action="append",
            default=[],
            help=(
                "Case-insensitive exact title filter. "
                "Can be provided multiple times to allow multiple exact titles."
            ),
        )
        parser.add_argument(
            "--bulk-send-job-id",
            action="append",
            default=[],
            help=(
                "Only include signature requests from these bulk send jobs. "
                "Can be provided multiple times."
            ),
        )
        parser.add_argument(
            "--signature-request-id",
            action="append",
            default=[],
            help=(
                "Only include these signature request ids. "
                "Can be provided multiple times."
            ),
        )
        parser.add_argument(
            "--show-candidates",
            action="store_true",
            help=(
                "Print candidate request ids/job ids/titles and overlap diagnostics "
                "for the selected group after filters are applied."
            ),
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
        title_exact_filter = {
            str(v or "").strip().lower()
            for v in (options.get("title_exact") or [])
            if str(v or "").strip()
        }
        bulk_send_job_filter = {
            str(v or "").strip()
            for v in (options.get("bulk_send_job_id") or [])
            if str(v or "").strip()
        }
        signature_request_filter = {
            str(v or "").strip()
            for v in (options.get("signature_request_id") or [])
            if str(v or "").strip()
        }
        show_candidates = bool(options.get("show_candidates"))
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
        e_pool_by_canon = self._pool_by_canonical(e_pool)
        m_pool_by_canon = self._pool_by_canonical(m_pool)
        e_pool_canon = set(e_pool_by_canon.keys())
        m_pool_canon = set(m_pool_by_canon.keys())
        self.stdout.write(f"Target participant pool sizes -> E:{len(e_pool)} M:{len(m_pool)}")
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
        if title_exact_filter:
            self.stdout.write(f"Exact title filter count: {len(title_exact_filter)}")
        if bulk_send_job_filter:
            self.stdout.write(f"Bulk send job filter count: {len(bulk_send_job_filter)}")
        if signature_request_filter:
            self.stdout.write(f"Signature request id filter count: {len(signature_request_filter)}")

        signed_by_track: dict[str, set[str]] = {"E": set(), "M": set()}
        request_ids_by_email: dict[str, str] = {}
        matched_reqs_by_track = {"E": 0, "M": 0}
        scoped_reqs_by_track = {"E": 0, "M": 0}
        e_title_candidates = 0
        e_title_candidates_with_signed_overlap = 0
        skipped_by_explicit_filter = 0
        skipped_no_scope = 0
        skipped_no_signed = 0
        skipped_not_target = 0
        candidate_lines: list[str] = []
        e_jobs: dict[str, dict] = {}
        m_jobs: dict[str, dict] = {}

        def _accumulate_job(
            buckets: dict[str, dict],
            *,
            job_key: str,
            req: SignatureRequestLite,
            title: str,
            signer_pool: set[str],
            signed_set: set[str],
            group_hint: int | None = None,
        ) -> None:
            bucket = buckets.setdefault(
                job_key,
                {
                    "bulk_send_job_id": req.bulk_send_job_id,
                    "request_ids": set(),
                    "titles": set(),
                    "signers": set(),
                    "signed": set(),
                    "group_hints": set(),
                },
            )
            if req.signature_request_id:
                bucket["request_ids"].add(req.signature_request_id)
            if title:
                bucket["titles"].add(title)
            bucket["signers"].update(signer_pool)
            bucket["signed"].update(signed_set)
            if group_hint is not None:
                bucket["group_hints"].add(int(group_hint))

        for req in requests:
            title = (req.title or "").strip()
            title_lower = title.lower()
            if title_contains and title_contains not in title.lower():
                continue
            if title_exact_filter and title_lower not in title_exact_filter:
                skipped_by_explicit_filter += 1
                continue
            if bulk_send_job_filter and req.bulk_send_job_id not in bulk_send_job_filter:
                skipped_by_explicit_filter += 1
                continue
            if signature_request_filter and req.signature_request_id not in signature_request_filter:
                skipped_by_explicit_filter += 1
                continue

            signer_pool = set(req.signer_emails)
            signed_set = set(req.signed_emails)
            if not signed_set:
                skipped_no_signed += 1
            job_key = req.bulk_send_job_id or f"req:{req.signature_request_id or title_lower or 'unknown'}"

            if show_candidates:
                signer_canon = {self._canonical_email(v) for v in signer_pool if self._canonical_email(v)}
                e_overlap = len(signer_canon.intersection(e_pool_canon))
                m_overlap = len(signer_canon.intersection(m_pool_canon))
                candidate_lines.append(
                    (
                        f"req={req.signature_request_id or '-'} bulk={req.bulk_send_job_id or '-'} "
                        f"job={job_key} signers={len(signer_pool)} signed={len(signed_set)} "
                        f"overlap(E:{e_overlap},M:{m_overlap}) title={title}"
                    )
                )

            em = _EMPRENDEDORA_GROUP_TITLE_RE.search(title)
            looks_like_emprendedora = (
                "acta" in title_lower
                and "compromiso" in title_lower
                and "emprendedora" in title_lower
            )
            if looks_like_emprendedora and track_opt in {"E", "BOTH"}:
                e_title_candidates += 1
                signed_canon = {self._canonical_email(v) for v in signed_set if self._canonical_email(v)}
                if signed_canon and e_pool_canon and bool(signed_canon.intersection(e_pool_canon)):
                    e_title_candidates_with_signed_overlap += 1

            applies_e_signal = False
            e_group_hint: int | None = None
            if track_opt in {"E", "BOTH"}:
                if em:
                    applies_e_signal = True
                    try:
                        e_group_hint = int(em.group(1))
                    except Exception:
                        e_group_hint = None
                elif looks_like_emprendedora:
                    applies_e_signal = True
                elif signer_pool and e_pool_canon:
                    signer_canon = {self._canonical_email(v) for v in signer_pool if self._canonical_email(v)}
                    if bool(signer_canon.intersection(e_pool_canon)):
                        applies_e_signal = True
                if not applies_e_signal and signer_pool and e_pool and bool(signer_pool.intersection(e_pool)):
                    applies_e_signal = True

            applies_m_signal = False
            if track_opt in {"M", "BOTH"}:
                if _MENTORA_TITLE_RE.search(title):
                    applies_m_signal = True
                elif signer_pool and m_pool_canon:
                    signer_canon = {self._canonical_email(v) for v in signer_pool if self._canonical_email(v)}
                    if bool(signer_canon.intersection(m_pool_canon)):
                        applies_m_signal = True
                if not applies_m_signal and signer_pool and m_pool and bool(signer_pool.intersection(m_pool)):
                    applies_m_signal = True

            if not applies_e_signal and not applies_m_signal:
                skipped_no_scope += 1
                continue

            if applies_e_signal:
                _accumulate_job(
                    e_jobs,
                    job_key=job_key,
                    req=req,
                    title=title,
                    signer_pool=signer_pool,
                    signed_set=signed_set,
                    group_hint=e_group_hint,
                )
            if applies_m_signal:
                _accumulate_job(
                    m_jobs,
                    job_key=job_key,
                    req=req,
                    title=title,
                    signer_pool=signer_pool,
                    signed_set=signed_set,
                )

        selected_e_keys: set[str] = set()
        selected_m_keys: set[str] = set()
        selected_e_reason = "none"
        selected_m_reason = "none"

        if track_opt in {"E", "BOTH"} and e_jobs:
            e_metrics: dict[str, tuple[int, int, bool]] = {}
            for key, bucket in e_jobs.items():
                signers = set(bucket.get("signers") or set())
                signed = set(bucket.get("signed") or set())
                group_hints = set(bucket.get("group_hints") or set())
                signer_canon = {self._canonical_email(v) for v in signers if self._canonical_email(v)}
                signed_canon = {self._canonical_email(v) for v in signed if self._canonical_email(v)}
                signer_overlap = len(signer_canon.intersection(e_pool_canon))
                signed_overlap = len(signed_canon.intersection(e_pool_canon))
                group_match = group_num in group_hints
                e_metrics[key] = (signer_overlap, signed_overlap, group_match)

            hinted_keys = [
                key for key, (signer_overlap, _signed_overlap, group_match) in e_metrics.items()
                if group_match and signer_overlap > 0
            ]
            if hinted_keys:
                selected_e_keys = set(hinted_keys)
                selected_e_reason = "title-group-match"
            else:
                best_signer_overlap = max((m[0] for m in e_metrics.values()), default=0)
                if best_signer_overlap > 0:
                    selected_e_keys = {
                        key for key, (signer_overlap, _signed_overlap, _group_match) in e_metrics.items()
                        if signer_overlap == best_signer_overlap
                    }
                    selected_e_reason = f"best-signer-overlap:{best_signer_overlap}"

            scoped_reqs_by_track["E"] = len(selected_e_keys)
            for key in sorted(selected_e_keys):
                bucket = e_jobs.get(key) or {}
                signed = set(bucket.get("signed") or set())
                matched_signed = self._match_signed_to_pool(
                    signed=signed,
                    pool=e_pool,
                    pool_by_canon=e_pool_by_canon,
                )
                if not matched_signed:
                    continue
                matched_reqs_by_track["E"] += 1
                req_ids_sorted = sorted(str(v) for v in (bucket.get("request_ids") or set()) if str(v).strip())
                req_id_for_row = req_ids_sorted[0] if req_ids_sorted else ""
                for email in matched_signed:
                    signed_by_track["E"].add(email)
                    if req_id_for_row and email not in request_ids_by_email:
                        request_ids_by_email[email] = req_id_for_row

        if track_opt in {"M", "BOTH"} and m_jobs:
            m_metrics: dict[str, tuple[int, bool, bool]] = {}
            for key, bucket in m_jobs.items():
                signers = set(bucket.get("signers") or set())
                signer_canon = {self._canonical_email(v) for v in signers if self._canonical_email(v)}
                signer_overlap = len(signer_canon.intersection(m_pool_canon))
                exact_match = bool(signer_canon) and bool(m_pool_canon) and signer_canon == m_pool_canon
                subset_match = bool(signer_canon) and bool(m_pool_canon) and signer_canon.issubset(m_pool_canon)
                m_metrics[key] = (signer_overlap, exact_match, subset_match)

            exact_keys = [
                key for key, (signer_overlap, exact_match, _subset_match) in m_metrics.items()
                if exact_match and signer_overlap > 0
            ]
            if exact_keys:
                selected_m_keys = set(exact_keys)
                selected_m_reason = "exact-pool-match"
            else:
                subset_best_overlap = max(
                    (
                        signer_overlap
                        for _key, (signer_overlap, _exact_match, subset_match) in m_metrics.items()
                        if subset_match
                    ),
                    default=0,
                )
                if subset_best_overlap > 0:
                    selected_m_keys = {
                        key
                        for key, (signer_overlap, _exact_match, subset_match) in m_metrics.items()
                        if subset_match and signer_overlap == subset_best_overlap
                    }
                    selected_m_reason = f"subset-best-overlap:{subset_best_overlap}"
                else:
                    best_signer_overlap = max((m[0] for m in m_metrics.values()), default=0)
                    if best_signer_overlap > 0:
                        selected_m_keys = {
                            key
                            for key, (signer_overlap, _exact_match, _subset_match) in m_metrics.items()
                            if signer_overlap == best_signer_overlap
                        }
                        selected_m_reason = f"best-signer-overlap:{best_signer_overlap}"

            scoped_reqs_by_track["M"] = len(selected_m_keys)
            for key in sorted(selected_m_keys):
                bucket = m_jobs.get(key) or {}
                signers = set(bucket.get("signers") or set())
                signed = set(bucket.get("signed") or set())
                matched_signed = self._match_signed_to_pool(
                    signed=signed,
                    pool=m_pool,
                    pool_by_canon=m_pool_by_canon,
                )
                if not matched_signed:
                    continue
                signer_canon = {self._canonical_email(v) for v in signers if self._canonical_email(v)}
                if signer_canon and m_pool_canon and not signer_canon.issubset(m_pool_canon):
                    skipped_not_target += 1
                matched_reqs_by_track["M"] += 1
                req_ids_sorted = sorted(str(v) for v in (bucket.get("request_ids") or set()) if str(v).strip())
                req_id_for_row = req_ids_sorted[0] if req_ids_sorted else ""
                for email in matched_signed:
                    signed_by_track["M"].add(email)
                    if req_id_for_row and email not in request_ids_by_email:
                        request_ids_by_email[email] = req_id_for_row

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
                f"skipped_by_filter:{skipped_by_explicit_filter} "
                f"skipped_no_scope:{skipped_no_scope} skipped_no_signed:{skipped_no_signed} "
                f"skipped_not_target:{skipped_not_target}"
            )
        )
        self.stdout.write(
            (
                f"Scoped requests by title -> E:{scoped_reqs_by_track['E']} "
                f"M:{scoped_reqs_by_track['M']}"
            )
        )
        self.stdout.write(
            (
                f"Selected envio match mode -> E:{selected_e_reason} "
                f"M:{selected_m_reason}"
            )
        )
        if track_opt in {"E", "BOTH"}:
            self.stdout.write(
                (
                    f"E title candidates seen: {e_title_candidates} | "
                    f"with signed overlap to group pool: {e_title_candidates_with_signed_overlap}"
                )
            )
        if show_candidates:
            self.stdout.write(f"Candidate requests after explicit filters: {len(candidate_lines)}")
            for line in candidate_lines[:120]:
                self.stdout.write(line)
            if len(candidate_lines) > 120:
                self.stdout.write(f"... {len(candidate_lines) - 120} more candidate rows omitted")
            self.stdout.write(
                (
                    f"Selected jobs -> E:{','.join(sorted(selected_e_keys)) or '-'} "
                    f"| M:{','.join(sorted(selected_m_keys)) or '-'}"
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
        bulk_send_job_id = str(raw_req.get("bulk_send_job_id") or "").strip()
        if not bulk_send_job_id:
            raw_bulk = raw_req.get("bulk_send_job") or {}
            if isinstance(raw_bulk, dict):
                bulk_send_job_id = str(
                    raw_bulk.get("bulk_send_job_id")
                    or raw_bulk.get("id")
                    or ""
                ).strip()
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
        req_complete = self._as_truthy(raw_req.get("is_complete"))
        req_declined = self._as_truthy(raw_req.get("is_declined"))
        signed_emails = [email for email in _clean_valid_emails(signed_emails) if email in signer_set]
        if req_complete and not req_declined and not signed_emails and signer_emails:
            # Some list payloads omit detailed per-signer status when complete.
            signed_emails = list(signer_emails)

        return SignatureRequestLite(
            signature_request_id=signature_request_id,
            bulk_send_job_id=bulk_send_job_id,
            title=title,
            created_at=created_at,
            signer_emails=signer_emails,
            signed_emails=signed_emails,
        )

    @staticmethod
    def _as_truthy(value) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        text = str(value).strip().lower()
        if not text:
            return False
        return text in {"1", "true", "yes", "y"}

    @staticmethod
    def _canonical_email(raw: str | None) -> str:
        email = str(raw or "").strip().lower()
        if "@" not in email:
            return ""
        local, domain = email.split("@", 1)
        local = local.strip()
        domain = domain.strip()
        if not local or not domain:
            return ""
        if domain in {"gmail.com", "googlemail.com"}:
            local = local.split("+", 1)[0].replace(".", "")
            domain = "gmail.com"
        return f"{local}@{domain}"

    def _pool_by_canonical(self, pool: set[str]) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for email in pool:
            canon = self._canonical_email(email)
            if not canon:
                continue
            out.setdefault(canon, set()).add(email)
        return out

    def _match_signed_to_pool(
        self,
        *,
        signed: set[str],
        pool: set[str],
        pool_by_canon: dict[str, set[str]],
    ) -> list[str]:
        matched: set[str] = set()
        for email in signed:
            if email in pool:
                matched.add(email)
            canon = self._canonical_email(email)
            if canon and canon in pool_by_canon:
                matched.update(pool_by_canon[canon])
        return sorted(matched)

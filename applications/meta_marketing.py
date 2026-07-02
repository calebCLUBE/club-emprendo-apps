import json
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx


META_GRAPH_VERSION_DEFAULT = "v20.0"
META_GRAPH_BASE_URL = "https://graph.facebook.com"
ZERNIO_BASE_URL_DEFAULT = "https://zernio.com/api/v1"
ZERNIO_META_PLATFORM_PRIORITY = ("metaads", "facebook", "instagram")
ZERNIO_META_PLATFORMS = set(ZERNIO_META_PLATFORM_PRIORITY)


@dataclass(frozen=True)
class MetaMarketingConfig:
    access_token: str
    ad_account_id: str
    page_id: str = ""
    instagram_business_account_id: str = ""
    graph_version: str = META_GRAPH_VERSION_DEFAULT

    @property
    def is_configured(self) -> bool:
        return bool(self.access_token and self.ad_account_id)


@dataclass(frozen=True)
class ZernioMarketingConfig:
    api_key: str
    account_id: str = ""
    base_url: str = ZERNIO_BASE_URL_DEFAULT

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def load_meta_marketing_config() -> MetaMarketingConfig:
    ad_account_id = _env("META_AD_ACCOUNT_ID")
    if ad_account_id and ad_account_id.isdigit():
        ad_account_id = f"act_{ad_account_id}"
    return MetaMarketingConfig(
        access_token=_env("META_ACCESS_TOKEN"),
        ad_account_id=ad_account_id,
        page_id=_env("META_PAGE_ID"),
        instagram_business_account_id=_env("META_INSTAGRAM_BUSINESS_ACCOUNT_ID"),
        graph_version=_env("META_GRAPH_VERSION", META_GRAPH_VERSION_DEFAULT),
    )


def load_zernio_marketing_config() -> ZernioMarketingConfig:
    return ZernioMarketingConfig(
        api_key=_env("ZERNIO_API_KEY"),
        account_id=_env("ZERNIO_META_ADS_ACCOUNT_ID") or _env("ZERNIO_ACCOUNT_ID"),
        base_url=_env("ZERNIO_BASE_URL", ZERNIO_BASE_URL_DEFAULT),
    )


def default_date_range() -> tuple[date, date]:
    end = date.today()
    start = end - timedelta(days=30)
    return start, end


def parse_iso_date(value: str | None) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


class MetaMarketingClient:
    def __init__(self, config: MetaMarketingConfig | None = None, timeout: float = 30.0):
        self.config = config or load_meta_marketing_config()
        self.timeout = timeout

    def _url(self, path: str) -> str:
        version = self.config.graph_version.strip().strip("/")
        path = path.strip("/")
        return f"{META_GRAPH_BASE_URL}/{version}/{path}"

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        if not self.config.access_token:
            raise RuntimeError("META_ACCESS_TOKEN is not configured.")
        payload = dict(params or {})
        payload["access_token"] = self.config.access_token
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            response = client.get(self._url(path), params=payload)
            response.raise_for_status()
            data = response.json()
        if isinstance(data, dict) and data.get("error"):
            error = data["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise RuntimeError(message or "Meta API returned an error.")
        return data

    def _get_paged(self, path: str, params: dict[str, Any] | None = None) -> list[dict]:
        if not self.config.access_token:
            raise RuntimeError("META_ACCESS_TOKEN is not configured.")
        payload = dict(params or {})
        payload["access_token"] = self.config.access_token
        rows: list[dict] = []
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            url = self._url(path)
            while url:
                response = client.get(url, params=payload if "?" not in url else None)
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict) and data.get("error"):
                    error = data["error"]
                    message = error.get("message") if isinstance(error, dict) else str(error)
                    raise RuntimeError(message or "Meta API returned an error.")
                rows.extend(data.get("data") or [])
                url = ((data.get("paging") or {}).get("next") or "").strip()
                payload = {}
        return rows

    def ad_insights(
        self,
        *,
        date_from: date,
        date_to: date,
        level: str = "campaign",
    ) -> list[dict]:
        if not self.config.ad_account_id:
            raise RuntimeError("META_AD_ACCOUNT_ID is not configured.")
        level = level if level in {"campaign", "adset", "ad"} else "campaign"
        fields = [
            "campaign_name",
            "adset_name",
            "ad_name",
            "spend",
            "impressions",
            "reach",
            "clicks",
            "ctr",
            "cpc",
            "cpm",
            "actions",
            "cost_per_action_type",
            "date_start",
            "date_stop",
        ]
        return self._get_paged(
            f"{self.config.ad_account_id}/insights",
            {
                "fields": ",".join(fields),
                "level": level,
                "time_range": json.dumps(
                    {"since": date_from.isoformat(), "until": date_to.isoformat()}
                ),
                "limit": 100,
            },
        )

    def instagram_user_insights(
        self,
        *,
        date_from: date,
        date_to: date,
    ) -> list[dict]:
        if not self.config.instagram_business_account_id:
            return []
        return self._get_paged(
            f"{self.config.instagram_business_account_id}/insights",
            {
                "metric": "reach,profile_views,website_clicks",
                "period": "day",
                "since": date_from.isoformat(),
                "until": date_to.isoformat(),
                "limit": 100,
            },
        )


class ZernioMarketingClient:
    def __init__(self, config: ZernioMarketingConfig | None = None, timeout: float = 30.0):
        self.config = config or load_zernio_marketing_config()
        self.timeout = timeout
        self.last_account_id = ""

    def _url(self, path: str) -> str:
        return f"{self.config.base_url.rstrip('/')}/{path.strip('/')}"

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        if not self.config.api_key:
            raise RuntimeError("ZERNIO_API_KEY is not configured.")
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            response = client.get(self._url(path), params=params or {}, headers=headers)
            response.raise_for_status()
            data = response.json()
        if isinstance(data, dict) and data.get("error"):
            error = data["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise RuntimeError(message or "Zernio API returned an error.")
        return data if isinstance(data, dict) else {"data": data}

    def accounts(self) -> list[dict]:
        data = self._get("accounts")
        rows = data.get("data") or data.get("accounts") or data.get("results") or []
        return rows if isinstance(rows, list) else []

    def resolve_account_id(self) -> str:
        if self.config.account_id:
            self.last_account_id = self.config.account_id
            return self.config.account_id
        for platform_name in ZERNIO_META_PLATFORM_PRIORITY:
            for account in self.accounts():
                platform = str(account.get("platform") or account.get("type") or "").lower()
                if platform != platform_name:
                    continue
                account_id = zernio_account_id(account)
                if account_id:
                    self.last_account_id = str(account_id)
                    return str(account_id)
        raise RuntimeError(
            "No Zernio Meta Ads account id found. Set ZERNIO_META_ADS_ACCOUNT_ID."
        )

    def ad_insights(
        self,
        *,
        date_from: date,
        date_to: date,
        level: str = "campaign",
    ) -> list[dict]:
        account_id = self.resolve_account_id()
        params = {
            "accountId": account_id,
            "fromDate": date_from.isoformat(),
            "toDate": date_to.isoformat(),
            "source": "all",
        }
        if level == "campaign":
            campaign_rows: list[dict] = []
            page = 1
            while page <= 100:
                data = self._get(
                    "ads/campaigns",
                    {**params, "page": page, "limit": 100},
                )
                page_rows = _extract_zernio_campaign_nodes(data)
                campaign_rows.extend(page_rows)
                if len(page_rows) < 100 or not _zernio_has_next_page(data, page):
                    break
                page += 1
        else:
            data = self._get("ads/tree", {**params, "level": level})
            campaign_rows = _extract_zernio_campaign_nodes(data)
        return [
            _normalize_zernio_campaign(row)
            for row in campaign_rows
            if isinstance(row, dict)
        ]

    def account_analytics(
        self,
        *,
        date_from: date,
        date_to: date,
        account_id: str = "",
        accounts: list[dict] | None = None,
    ) -> dict:
        end = date_to
        start = max(date_from, end - timedelta(days=88))
        selected_id = (account_id or "").strip()
        source_accounts = accounts if accounts is not None else self.accounts()
        rows: list[dict] = []
        for account in source_accounts:
            account_platform = str(account.get("platform") or account.get("type") or "").lower()
            account_identifier = zernio_account_id(account)
            if account_platform not in {"facebook", "instagram"}:
                continue
            if selected_id and selected_id != account_identifier:
                continue
            try:
                if account_platform == "facebook":
                    data = self._get(
                        "analytics/facebook/page-insights",
                        {
                            "accountId": account_identifier,
                            "since": start.isoformat(),
                            "until": end.isoformat(),
                            "metricType": "total_value",
                        },
                    )
                else:
                    data = self._get(
                        "analytics/instagram/account-insights",
                        {
                            "accountId": account_identifier,
                            "since": start.isoformat(),
                            "until": end.isoformat(),
                            "metricType": "total_value",
                        },
                    )
                rows.append(
                    {
                        "account_id": account_identifier,
                        "account_label": zernio_account_label(account),
                        "platform": account_platform,
                        "metrics": _extract_zernio_metric_totals(data),
                    }
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {400, 404}:
                    continue
                raise
        return summarize_zernio_account_analytics(rows)

    def instagram_user_insights(
        self,
        *,
        date_from: date,
        date_to: date,
    ) -> list[dict]:
        return []


def _metric_value(row: dict, name: str) -> Any:
    for key in ("metrics", "insights", "summary"):
        nested = row.get(key)
        if isinstance(nested, dict) and nested.get(name) is not None:
            return nested.get(name)
    return row.get(name)


def _extract_zernio_metric_totals(data: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
    for name, payload in metrics.items():
        if isinstance(payload, dict):
            out[name] = _to_float(payload.get("total") if payload.get("total") is not None else payload.get("value"))
        else:
            out[name] = _to_float(payload)
    return out


def summarize_zernio_account_analytics(rows: list[dict]) -> dict:
    totals: defaultdict[str, float] = defaultdict(float)
    platforms: defaultdict[str, int] = defaultdict(int)
    for row in rows:
        platform = str(row.get("platform") or "").lower()
        if platform:
            platforms[platform] += 1
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        for name, value in metrics.items():
            totals[name] += _to_float(value)
    return {
        "accounts": rows,
        "account_count": len(rows),
        "platforms": dict(platforms),
        "reach": int(totals["reach"]),
        "views": int(totals["views"] + totals["page_views_total"]),
        "media_views": int(totals["page_media_view"]),
        "engagements": int(totals["page_post_engagements"] + totals["total_interactions"] + totals["accounts_engaged"]),
        "followers": int(totals["page_follows"]),
        "followers_gained": int(totals["followers_gained"]),
        "followers_lost": int(totals["followers_lost"]),
        "likes": int(totals["likes"]),
        "comments": int(totals["comments"]),
        "shares": int(totals["shares"]),
        "saves": int(totals["saves"]),
        "clicks": int(totals["profile_links_taps"]),
    }


def _extract_zernio_campaign_nodes(data: dict) -> list[dict]:
    return _find_zernio_rows_by_key(data, {"campaigns", "items", "results", "data"})


def _zernio_has_next_page(data: dict, current_page: int) -> bool:
    pagination = data.get("pagination") or data.get("meta") or {}
    nested_data = data.get("data")
    if not pagination and isinstance(nested_data, dict):
        pagination = nested_data.get("pagination") or nested_data.get("meta") or {}
    if not isinstance(pagination, dict):
        return False
    if pagination.get("hasNextPage") is not None:
        return bool(pagination.get("hasNextPage"))
    if pagination.get("has_next") is not None:
        return bool(pagination.get("has_next"))
    total_pages = pagination.get("totalPages") or pagination.get("total_pages")
    try:
        return int(current_page) < int(total_pages)
    except (TypeError, ValueError):
        return False


def _coerce_zernio_rows(value: Any) -> list[dict]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        for key in ("campaigns", "items", "results", "nodes"):
            rows = _coerce_zernio_rows(value.get(key))
            if rows:
                return rows
    return []


def _find_zernio_rows_by_key(value: Any, keys: set[str]) -> list[dict]:
    if isinstance(value, dict):
        for key in keys:
            rows = _coerce_zernio_rows(value.get(key))
            if rows:
                return rows
        for child in value.values():
            rows = _find_zernio_rows_by_key(child, keys)
            if rows:
                return rows
    if isinstance(value, list):
        for item in value:
            rows = _find_zernio_rows_by_key(item, keys)
            if rows:
                return rows
    return []


def zernio_account_id(account: dict) -> str:
    account_id = (
        account.get("_id")
        or account.get("id")
        or account.get("accountId")
        or account.get("socialAccountId")
    )
    return str(account_id or "").strip()


def zernio_account_label(account: dict) -> str:
    platform = str(account.get("platform") or account.get("type") or "account").strip()
    name = str(
        account.get("name")
        or account.get("username")
        or account.get("pageName")
        or account.get("displayName")
        or ""
    ).strip()
    account_id = zernio_account_id(account)
    label = f"{platform}: {name}" if name else platform
    return f"{label} ({account_id})" if account_id else label


def _normalize_zernio_campaign(row: dict) -> dict:
    campaign = row.get("campaign") if isinstance(row.get("campaign"), dict) else {}
    name = row.get("campaign_name") or row.get("campaignName") or row.get("name")
    name = name or campaign.get("name")
    return {
        "campaign_name": str(name or "Unnamed campaign"),
        "spend": _metric_value(row, "spend"),
        "impressions": _metric_value(row, "impressions"),
        "reach": _metric_value(row, "reach"),
        "clicks": _metric_value(row, "clicks"),
        "ctr": _metric_value(row, "ctr"),
        "cpc": _metric_value(row, "cpc"),
        "cpm": _metric_value(row, "cpm"),
        "date_start": row.get("date_start") or row.get("dateStart"),
        "date_stop": row.get("date_stop") or row.get("dateStop"),
    }


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def summarize_ad_insights(rows: list[dict]) -> dict:
    spend = sum(_to_float(row.get("spend")) for row in rows)
    impressions = sum(_to_int(row.get("impressions")) for row in rows)
    reach = sum(_to_int(row.get("reach")) for row in rows)
    clicks = sum(_to_int(row.get("clicks")) for row in rows)
    ctr = round((clicks / impressions) * 100, 2) if impressions else 0
    cpc = round(spend / clicks, 2) if clicks else 0
    cpm = round((spend / impressions) * 1000, 2) if impressions else 0
    return {
        "spend": round(spend, 2),
        "impressions": impressions,
        "reach": reach,
        "clicks": clicks,
        "ctr": ctr,
        "cpc": cpc,
        "cpm": cpm,
    }


def campaign_rows(rows: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for row in rows:
        name = str(row.get("campaign_name") or "Unnamed campaign").strip()
        item = grouped.setdefault(
            name,
            {
                "name": name,
                "spend": 0.0,
                "impressions": 0,
                "reach": 0,
                "clicks": 0,
            },
        )
        item["spend"] += _to_float(row.get("spend"))
        item["impressions"] += _to_int(row.get("impressions"))
        item["reach"] += _to_int(row.get("reach"))
        item["clicks"] += _to_int(row.get("clicks"))
    out = []
    for item in grouped.values():
        impressions = item["impressions"]
        clicks = item["clicks"]
        spend = item["spend"]
        out.append(
            {
                **item,
                "spend": round(spend, 2),
                "ctr": round((clicks / impressions) * 100, 2) if impressions else 0,
                "cpc": round(spend / clicks, 2) if clicks else 0,
            }
        )
    out.sort(key=lambda item: (-item["spend"], item["name"]))
    return out


def summarize_instagram_insights(rows: list[dict]) -> dict:
    totals: dict[str, int] = {}
    for metric in rows:
        name = str(metric.get("name") or "").strip()
        total = 0
        for value in metric.get("values") or []:
            total += _to_int(value.get("value") if isinstance(value, dict) else value)
        if name:
            totals[name] = total
    return totals

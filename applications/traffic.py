import logging
import uuid
from zoneinfo import ZoneInfo

from django.db import IntegrityError
from django.db.models import F
from django.utils import timezone

from .models import WebsiteTrafficVisit


logger = logging.getLogger(__name__)

TRAFFIC_COOKIE_NAME = "ce_traffic_visitor"
TRAFFIC_COOKIE_MAX_AGE = 60 * 60 * 24 * 365 * 2
BOGOTA_TIMEZONE = ZoneInfo("America/Bogota")
EXCLUDED_PATH_PREFIXES = (
    "/admin/",
    "/static/",
    "/media/",
    "/favicon",
    "/robots.txt",
    "/website-traffic/heartbeat/",
)
BOT_USER_AGENT_TOKENS = (
    "bot",
    "crawler",
    "spider",
    "slurp",
    "facebookexternalhit",
    "whatsapp",
    "preview",
    "headlesschrome",
    "uptimerobot",
    "healthcheck",
)


def is_traffic_bot(request):
    user_agent = str(request.META.get("HTTP_USER_AGENT") or "").lower()
    return any(token in user_agent for token in BOT_USER_AGENT_TOKENS)


def traffic_visitor_id(request):
    raw = str(request.COOKIES.get(TRAFFIC_COOKIE_NAME) or "").strip()
    try:
        return uuid.UUID(raw)
    except (TypeError, ValueError, AttributeError):
        return uuid.uuid4()


def normalize_traffic_path(path):
    value = str(path or "/").strip()
    if not value.startswith("/"):
        value = "/"
    return value[:500]


def record_website_traffic(visitor_id, path, *, pageview, now=None):
    now = now or timezone.now()
    visit_date = now.astimezone(BOGOTA_TIMEZONE).date()
    path = normalize_traffic_path(path)
    defaults = {
        "pageviews": 1 if pageview else 0,
        "first_seen_at": now,
        "last_seen_at": now,
    }
    try:
        visit, created = WebsiteTrafficVisit.objects.get_or_create(
            visitor_id=visitor_id,
            visit_date=visit_date,
            path=path,
            defaults=defaults,
        )
    except IntegrityError:
        visit = WebsiteTrafficVisit.objects.get(
            visitor_id=visitor_id,
            visit_date=visit_date,
            path=path,
        )
        created = False

    if not created:
        updates = {"last_seen_at": now}
        if pageview:
            updates["pageviews"] = F("pageviews") + 1
        WebsiteTrafficVisit.objects.filter(pk=visit.pk).update(**updates)
    return visit


def should_track_website_request(request, response):
    if request.method != "GET" or response.status_code != 200:
        return False
    path = str(request.path or "/")
    if any(path.startswith(prefix) for prefix in EXCLUDED_PATH_PREFIXES):
        return False
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_staff", False):
        return False
    content_type = str(response.get("Content-Type", "")).lower()
    if "text/html" not in content_type:
        return False
    return not is_traffic_bot(request)


def set_traffic_cookie(response, visitor_id, request):
    response.set_cookie(
        TRAFFIC_COOKIE_NAME,
        str(visitor_id),
        max_age=TRAFFIC_COOKIE_MAX_AGE,
        httponly=True,
        secure=request.is_secure(),
        samesite="Lax",
    )


class WebsiteTrafficMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not should_track_website_request(request, response):
            return response

        visitor_id = traffic_visitor_id(request)
        try:
            record_website_traffic(
                visitor_id,
                request.path,
                pageview=True,
            )
        except Exception:
            # Analytics must never make the public website unavailable.
            logger.exception("Website traffic pageview tracking failed")
        set_traffic_cookie(response, visitor_id, request)
        return response

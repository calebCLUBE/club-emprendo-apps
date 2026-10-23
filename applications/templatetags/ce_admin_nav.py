from __future__ import annotations

from django import template
from django.urls import NoReverseMatch, reverse

register = template.Library()

# (section key, label, url name, icon id). Grading and Emparejamiento live
# under Profiles, which links to them from its own "Profiles options" row.
PRIMARY_NAV = (
    ("home", "Home", "admin:index", "home"),
    ("applications", "Applications", "admin_apps_list", "file-text"),
    ("profiles", "Profiles", "admin_profiles_list", "users"),
    ("database", "Database", "admin_database", "database"),
    ("dashboards", "Dashboards", "admin_dashboards_home", "bar-chart"),
    ("tasks", "Tasks", "admin_task_manager_home", "list-checks"),
)

# Path prefixes that belong to each section. The longest matching prefix wins,
# so "/admin/apps/dashboard/" resolves to dashboards rather than applications.
SECTION_PREFIXES = (
    ("dashboards", ("/admin/dashboards/", "/admin/apps/dashboard/", "/admin/communications/")),
    ("applications", ("/admin/apps/", "/admin/applications/formdefinition/", "/builder/")),
    ("profiles", (
        "/admin/profiles/",
        "/admin/grading/",
        "/admin/emparejamiento/",
        "/admin/applications/pairingconfig/",
    )),
    ("database", ("/admin/database/",)),
    ("tasks", ("/admin/task-manager/",)),
    ("users", ("/admin/accounts/",)),
)

# Second-level tabs: (label, url name, path prefixes that activate the tab).
SUBNAV = {
    "dashboards": (
        ("Overview", "admin_dashboards_home", ("/admin/dashboards/",)),
        ("Impact", "admin_impact_dashboard", ("/admin/dashboards/impact/",)),
        ("Applications", "admin_applications_dashboard", ("/admin/apps/dashboard/",)),
        ("Incomplete applications", "admin_application_progress_dashboard", ("/admin/dashboards/application-progress/",)),
        ("Marketing", "admin_marketing_dashboard", ("/admin/dashboards/marketing/",)),
        ("Website traffic", "admin_website_traffic_dashboard", ("/admin/dashboards/website-traffic/",)),
        ("Bulk email", "admin_bulk_email_compose", ("/admin/communications/",)),
    ),
    "tasks": (
        ("Team", "admin_task_manager_home", ("/admin/task-manager/", "/admin/task-manager/user/")),
        ("My tasks", "admin_task_manager_my_tasks", ("/admin/task-manager/my-tasks/",)),
        ("All tasks", "admin_task_manager_overview", (
            "/admin/task-manager/overview/",
            "/admin/task-manager/task/",
            "/admin/task-manager/edit/",
        )),
        ("Assign task", "admin_task_manager_assign", ("/admin/task-manager/assign/",)),
        ("Website revisions", "admin_task_manager_website_revisions", ("/admin/task-manager/website-revisions/",)),
    ),
}


def _safe_reverse(name: str) -> str:
    try:
        return reverse(name)
    except NoReverseMatch:
        return ""


def _longest_prefix(path: str, prefixes) -> int:
    return max((len(prefix) for prefix in prefixes if path.startswith(prefix)), default=-1)


def _section_for(path: str) -> str:
    if path in ("/admin/", "/admin"):
        return "home"
    best_key, best_len = "", -1
    for key, prefixes in SECTION_PREFIXES:
        length = _longest_prefix(path, prefixes)
        if length > best_len:
            best_key, best_len = key, length
    return best_key


@register.simple_tag(takes_context=True)
def ce_admin_nav(context):
    """Navigation state for the admin shell; never raises on a bad URL name."""
    request = context.get("request")
    path = getattr(request, "path", "") or ""
    section = _section_for(path)

    primary = []
    for key, label, url_name, icon in PRIMARY_NAV:
        url = _safe_reverse(url_name)
        if url:
            primary.append({"key": key, "label": label, "url": url, "icon": icon, "active": key == section})

    subnav = []
    items = SUBNAV.get(section, ())
    if items:
        scores = [_longest_prefix(path, prefixes) for _label, _name, prefixes in items]
        best = max(scores)
        for (label, url_name, _prefixes), score in zip(items, scores):
            url = _safe_reverse(url_name)
            if url:
                subnav.append({"label": label, "url": url, "active": best >= 0 and score == best})

    return {
        "section": section,
        "primary": primary,
        "subnav": subnav,
        "surveys_url": _safe_reverse("surveys_index"),
        "users_url": _safe_reverse("admin:accounts_user_changelist"),
        "password_url": _safe_reverse("admin:password_change"),
        "logout_url": _safe_reverse("admin:logout"),
    }


@register.filter
def ce_initials(value) -> str:
    """Up to two initials for an avatar: "Ana María Pérez" -> "AP", "ana@x.org" -> "A"."""
    text = str(value or "").strip()
    if "@" in text:
        text = text.split("@", 1)[0].replace(".", " ").replace("_", " ").replace("-", " ")
    words = [word for word in text.split() if word[:1].isalnum()]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:1].upper()
    return (words[0][:1] + words[-1][:1]).upper()

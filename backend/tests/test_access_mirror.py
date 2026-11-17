"""The frontend's role map (frontend/src/auth/access.ts) must agree with the
backend write guards (the policy table in test_write_role_matrix.py).

For each admin screen we list the write routes it calls. Whoever the frontend
says may CHANGE things on that screen (canChange) must be let through by every
one of those routes, and whoever it marks "View only" must be refused by them:
otherwise a login is offered a button the API refuses, or told it is view only
when it is not.
"""
import re
from pathlib import Path

import pytest

from tests.test_write_role_matrix import WRITE_POLICY

ACCESS_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "auth" / "access.ts"
ALL_ROLES = {"admin", "manager", "dj", "viewer", "sponsor"}


def _parse_access():
    src = ACCESS_TS.read_text()
    consts = {
        name: set(re.findall(r"'(\w+)'", body))
        for name, body in re.findall(r"const (\w+): Role\[\] = \[([^\]]*)\]", src)
    }

    def table(name):
        block = re.search(rf"const {name}[^=]*= \[(.*?)\n\];", src, re.S).group(1)
        return [(p, consts[c]) for p, c in re.findall(r"\['([^']+)', (\w+)\]", block)]

    return table("ROUTE_ROLES"), table("WRITE_ROLES")


ROUTE_ROLES, WRITE_ROLES = _parse_access()


def can_access(role, path):
    best = None
    for prefix, roles in ROUTE_ROLES:
        if path == prefix or path.startswith(prefix + "/"):
            if best is None or len(prefix) > len(best[0]):
                best = (prefix, roles)
    return role in best[1] if best else True


def can_change(role, path):
    if not can_access(role, path):
        return False
    for prefix, roles in WRITE_ROLES:  # first match wins, as in access.ts
        if path == prefix or path.startswith(prefix + "/"):
            return role in roles
    return True


# screen -> the write routes its buttons call (frontend/src/pages, api/, hooks/)
SCREEN_WRITES = {
    "/admin/dashboard": [("POST", "/stations/{station_id}/queue/skip"), ("POST", "/stations/{station_id}/queue/insert"),
                         ("POST", "/stations/{station_id}/queue/fire-now"), ("DELETE", "/stations/{station_id}/queue/{entry_id}")],
    "/admin/stations": [("POST", "/stations"), ("PUT", "/stations/{station_id}"), ("DELETE", "/stations/{station_id}")],
    "/admin/assets": [("PATCH", "/assets/{asset_id}"), ("DELETE", "/assets/{asset_id}"), ("POST", "/assets/{asset_id}/trim")],
    "/admin/assets/upload": [("POST", "/assets/upload")],
    "/admin/users": [("POST", "/users"), ("PUT", "/users/{user_id}"), ("DELETE", "/users/{user_id}")],
    "/admin/rules": [("POST", "/rules"), ("PUT", "/rules/{rule_id}"), ("DELETE", "/rules/{rule_id}")],
    "/admin/schedules": [("POST", "/schedules"), ("PATCH", "/schedules/{schedule_id}"), ("POST", "/schedules/blocks")],
    "/admin/holidays": [("POST", "/holidays"), ("PUT", "/holidays/{holiday_id}"), ("DELETE", "/holidays/{holiday_id}")],
    "/admin/sponsors": [("POST", "/sponsors"), ("PUT", "/sponsors/{sponsor_id}"), ("DELETE", "/sponsors/{sponsor_id}")],
    "/admin/campaigns": [("POST", "/campaigns"), ("PATCH", "/campaigns/{campaign_id}/status"),
                         ("POST", "/campaigns/{campaign_id}/drafts"), ("POST", "/campaigns/{campaign_id}/comments")],
    "/admin/spot-schedule": [("POST", "/spot-schedules"), ("PUT", "/spot-schedules/{schedule_id}")],
    "/admin/spot-recordings": [("POST", "/spot-recordings"), ("POST", "/spot-recordings/{recording_id}/approve")],
    "/admin/program-blocks": [("POST", "/program-blocks"), ("PUT", "/program-blocks/{block_id}")],
    "/admin/sponsorship-rules": [("PUT", "/sponsorship/rules")],
    "/admin/playlists": [("POST", "/playlists"), ("PATCH", "/playlists/{template_id}")],
    "/admin/categories": [("POST", "/categories"), ("DELETE", "/categories/{category_id}")],
    "/admin/asset-types": [("POST", "/asset-types"), ("DELETE", "/asset-types/{asset_type_id}")],
    "/admin/alerts": [("PATCH", "/alerts/{alert_id}/resolve"), ("POST", "/alerts/resolve-all")],
    "/admin/bug-reports": [("PATCH", "/bug-reports/{report_id}")],
    "/admin/site-updates": [("PUT", "/site-release"), ("POST", "/site-release/push")],
    "/admin/live": [("POST", "/live-shows"), ("POST", "/live-shows/{show_id}/start")],
    "/admin/requests": [("PATCH", "/song-requests/{request_id}"), ("DELETE", "/song-requests/{request_id}")],
    "/admin/archives": [("POST", "/archives"), ("PATCH", "/archives/{archive_id}")],
    "/admin/audio-enhance": [("POST", "/assets/{asset_id}/enhance")],
    "/admin/studio": [("POST", "/assets/upload")],  # RecordingStudio saves the take with uploadAsset
    "/admin/crm": [("POST", "/crm/raffles"), ("POST", "/crm/raffles/{raffle_id}/draw")],
    "/admin/program-clock": [("PUT", "/stations/{station_id}"), ("DELETE", "/rules/{rule_id}")],
    "/sponsor/campaigns": [("POST", "/campaigns"), ("POST", "/campaigns/{campaign_id}/comments")],
    "/sponsor/billing": [("POST", "/billing/invoices/{invoice_id}/checkout")],
    "/sponsor/wording": [("POST", "/sponsorship/copy-review")],
}

# Screens where the two sides disagree today (recorded as bugs below).
KNOWN_MISMATCH = {
    "/admin/studio": "BUG: DJs may open the Recording Studio and are not marked view-only, but saving a take "
                     "(POST /assets/upload) is managers only, so a DJ's recording is refused at the end",
    "/admin/program-clock": "BUG: managers may change the program clock (WRITE_ROLES MANAGERS) but deleting a "
                            "rule there (DELETE /rules/{id}) is admin only",
}


def _disagreements(screen):
    out = []
    for route in SCREEN_WRITES[screen]:
        allowed = WRITE_POLICY[route]
        for role in sorted(ALL_ROLES):
            shown = can_change(role, screen)
            if shown and role not in allowed:
                out.append(f"{role} may change {screen} per access.ts but {route} refuses it")
            if can_access(role, screen) and not shown and role in allowed:
                out.append(f"{role} is shown View only on {screen} but {route} lets it change")
    return out


def test_access_ts_parsed():
    assert ROUTE_ROLES and WRITE_ROLES
    assert dict(WRITE_ROLES)["/admin/rules"] == {"admin"}


def test_every_screen_route_is_in_the_policy():
    for screen, routes in SCREEN_WRITES.items():
        for route in routes:
            assert route in WRITE_POLICY, f"{screen}: {route} not in the write policy"


@pytest.mark.parametrize("screen", sorted(s for s in SCREEN_WRITES if s not in KNOWN_MISMATCH))
def test_screen_write_roles_match_backend(screen):
    assert not _disagreements(screen)


@pytest.mark.parametrize(
    "screen",
    [pytest.param(s, marks=pytest.mark.xfail(strict=True, reason=r)) for s, r in sorted(KNOWN_MISMATCH.items())],
)
def test_known_screen_mismatch(screen):
    assert not _disagreements(screen)

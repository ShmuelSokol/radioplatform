"""Every custom X-... header the website sends must pass the browser's CORS preflight.

X-CRM-PIN was missing, so on kbrlive.com the preflight for GET /crm/my-ratings (and the
rating POST) got 400 and listeners' stars and hearts were never remembered (F-LISTEN live
check, 2026-11-17). This scans frontend/src for X- headers so a new one cannot be forgotten.
"""
import re
from pathlib import Path

import pytest

FRONTEND_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"
ORIGIN = "https://kbrlive.com"


def _headers_the_site_sends() -> set[str]:
    found: set[str] = set()
    for path in FRONTEND_SRC.rglob("*.ts*"):
        found.update(re.findall(r"""['"](X-[A-Za-z-]+)['"]""", path.read_text(encoding="utf-8")))
    return found


def test_the_scan_finds_the_known_headers():
    if not FRONTEND_SRC.exists():
        pytest.skip("frontend sources not in this checkout")
    assert {"X-CRM-PIN", "X-Request-ID"} <= _headers_the_site_sends()


@pytest.mark.asyncio
@pytest.mark.parametrize("path,method", [
    ("/api/v1/crm/my-ratings", "GET"),
    ("/api/v1/crm/rate", "POST"),
])
async def test_preflight_allows_every_header_the_site_sends(client, path, method):
    if not FRONTEND_SRC.exists():
        pytest.skip("frontend sources not in this checkout")
    headers = sorted(_headers_the_site_sends() | {"Content-Type", "Authorization"})
    r = await client.options(path, headers={
        "Origin": ORIGIN,
        "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": ", ".join(h.lower() for h in headers),
    })
    assert r.status_code == 200, r.text
    allowed = {h.strip().lower() for h in r.headers.get("access-control-allow-headers", "").split(",")}
    for h in headers:
        assert h.lower() in allowed, f"{h} is not allowed by CORS"

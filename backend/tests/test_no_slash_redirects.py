"""No API call the site makes may be answered with a redirect.

Behind Railway's proxy FastAPI builds a redirect (trailing slash added or taken off) with
http://, and the browser blocks it on the https site as mixed content: the Recording
Studio's weather readouts never loaded (/weather-readouts/ was the only route declared
with a trailing slash, live browser pass 2026-11-22)."""
import pytest
from fastapi.routing import APIRoute

from tests.route_list import app_routes


def test_every_slash_route_also_answers_without_the_slash():
    from app.main import create_app

    paths = {(r.path, m) for r in app_routes(create_app()) if isinstance(r.route, APIRoute) for m in r.methods}
    lonely = sorted(f"{m} {p}" for p, m in paths if p.endswith("/") and (p.rstrip("/"), m) not in paths)
    assert not lonely, f"routes the site's call (no slash) reaches only through a redirect: {lonely}"


@pytest.mark.asyncio
async def test_weather_readouts_list_answers_without_a_redirect(client, auth_headers):
    r = await client.get("/api/v1/weather-readouts", headers=auth_headers, follow_redirects=False)
    assert r.status_code == 200, r.status_code
    r = await client.get("/api/v1/weather-readouts/", headers=auth_headers, follow_redirects=False)
    assert r.status_code == 200, r.status_code

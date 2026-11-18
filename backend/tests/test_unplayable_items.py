"""An item whose file cannot be measured is checked once more, then taken off the air.

2026-11-18: two items failed the upcoming-item analysis again and again with "Could not
measure the length of the audio". They must stop being scheduled, be flagged for a
person, and never be deleted.
"""
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.models.alert import Alert
from app.models.asset import Asset
from app.services import audio_analysis_service, upcoming_analysis
from tests.conftest import TestSessionLocal


async def _asset(db):
    a = Asset(id=uuid.uuid4(), title="Broken story", file_path="broken.mp3", asset_type="music",
              category="stories", duration=600)
    db.add(a)
    await db.commit()
    return a


def _fake_analysis(monkeypatch, error):
    calls = []

    async def fake(db, asset_id, full=False):
        calls.append(full)
        if error is not None:
            raise error

    monkeypatch.setattr(audio_analysis_service, "analyze_audio", fake)
    monkeypatch.setattr("app.db.session.async_session_factory", TestSessionLocal)
    return calls


@pytest.mark.asyncio
async def test_a_file_that_cannot_be_measured_twice_goes_off_the_air(db_session, monkeypatch):
    a = await _asset(db_session)
    calls = _fake_analysis(monkeypatch, ValueError("Could not measure the length of the audio"))
    reason = upcoming_analysis.unplayable_reason(ValueError("Could not measure the length of the audio"))
    assert await upcoming_analysis.check_and_take_off_air(str(a.id), reason) is True
    assert calls == [True]  # the whole file was measured once more first
    async with TestSessionLocal() as db:
        got = await db.get(Asset, a.id)
        assert got is not None  # never deleted
        assert got.category == "do_not_play"
        assert got.metadata_extra["category_before_do_not_play"] == "stories"  # "allow again" restores it
        assert "cannot be measured" in got.metadata_extra["unplayable"]["reason"]
        alerts = (await db.execute(select(Alert))).scalars().all()
        assert any("Broken story" in al.title for al in alerts)


@pytest.mark.asyncio
async def test_a_file_that_measures_whole_stays_on_the_air(db_session, monkeypatch):
    a = await _asset(db_session)
    _fake_analysis(monkeypatch, None)
    assert await upcoming_analysis.check_and_take_off_air(str(a.id), "x") is False
    async with TestSessionLocal() as db:
        assert (await db.get(Asset, a.id)).category == "stories"


@pytest.mark.asyncio
async def test_a_network_error_says_nothing_about_the_file(db_session, monkeypatch):
    a = await _asset(db_session)
    _fake_analysis(monkeypatch, httpx.ConnectError("no route"))
    with pytest.raises(httpx.ConnectError):
        await upcoming_analysis.check_and_take_off_air(str(a.id), "x")
    async with TestSessionLocal() as db:
        assert (await db.get(Asset, a.id)).category == "stories"


def test_which_failures_mean_a_broken_file():
    assert upcoming_analysis.unplayable_reason(ValueError("Could not measure the length of the audio"))
    req = httpx.Request("GET", "https://x/a.mp3")
    missing = httpx.HTTPStatusError("404", request=req, response=httpx.Response(404, request=req))
    assert "missing" in upcoming_analysis.unplayable_reason(missing)
    busy = httpx.HTTPStatusError("503", request=req, response=httpx.Response(503, request=req))
    assert upcoming_analysis.unplayable_reason(busy) is None
    assert upcoming_analysis.unplayable_reason(TimeoutError("slow")) is None

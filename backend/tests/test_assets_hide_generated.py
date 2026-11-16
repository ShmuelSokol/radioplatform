"""Dated time and weather recordings stay out of the jingle cart.

A "Time Announcement - 2026-07-14T00" fired from the cart would air the wrong
time, so the control room asks for jingles with hide_generated=true.
"""
import uuid

import pytest

from app.models.asset import Asset


@pytest.mark.asyncio
async def test_hide_generated_leaves_out_time_and_weather(client, auth_headers, db_session):
    titles = ["Station ID", "Time Announcement - 2026-07-14T00", "Weather Report - 2026-11-15", "Time Announcement - 15:00"]
    for t in titles:
        db_session.add(Asset(
            id=uuid.uuid4(), title=t, duration=8.0,
            file_path="https://cdn.example.com/j.mp3", asset_type="jingle",
        ))
    await db_session.commit()

    everything = (await client.get("/api/v1/assets?asset_type=jingle", headers=auth_headers)).json()
    assert {a["title"] for a in everything["assets"]} == set(titles)

    res = (await client.get("/api/v1/assets?asset_type=jingle&hide_generated=true", headers=auth_headers)).json()
    assert [a["title"] for a in res["assets"]] == ["Station ID"]
    assert res["total"] == 1

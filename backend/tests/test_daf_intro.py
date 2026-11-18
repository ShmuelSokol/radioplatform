"""Daf Yomi intro clip (Shmuel, 2026-11-18): "Up next, today's daf: Maseches Menachos,
daf thirty-four" airs right before each Daf Yomi shiur.

The words and the day's daf come from infra/daf-yomi/daf_intro.py (used by the PC job
that makes the clips in the station voice). The clip goes in the "Shiur: Daf Yomi"
program block right before the shiur, so the queue must keep them back to back.
"""
import importlib.util
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.program_block import ProgramBlock
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services.program_block_service import reconcile_block_entries

_spec = importlib.util.spec_from_file_location(
    "daf_intro", Path(__file__).resolve().parents[2] / "infra" / "daf-yomi" / "daf_intro.py"
)
daf_intro = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(daf_intro)


def test_the_calendar_names_the_right_daf():
    # Known days (checked against the hdate library over four whole cycles, 0 differences)
    assert daf_intro.daf_for(date(2020, 1, 5)) == ("Berachos", 2)  # cycle 14 began
    assert daf_intro.daf_for(date(2026, 11, 18)) == ("Bechoros", 61)
    assert daf_intro.daf_for(date(2026, 11, 19)) == ("Arachin", 2)
    assert daf_intro.daf_for(date(2027, 6, 8)) == ("Berachos", 2)  # cycle 15
    assert daf_intro.CYCLE_DAYS == 2711
    # Kinnim, Tamid and Middos run on from Me'ilah's numbering
    kinnim = [d for d in (date(2027, 1, 1) + timedelta(i) for i in range(300))
              if daf_intro.daf_for(d)[0] in ("Kinnim", "Tamid", "Middos")]
    assert [daf_intro.daf_for(d)[1] for d in kinnim] == list(range(23, 38))


def test_daf_numbers_are_said_as_words():
    words = daf_intro.number_words
    assert [words(n) for n in (2, 13, 34, 40, 100, 101, 176)] == [
        "two", "thirteen", "thirty-four", "forty", "a hundred", "a hundred and one",
        "a hundred and seventy-six",
    ]


def test_the_clip_words():
    assert daf_intro.intro_text("Menachos", 34) == "Up next, today's daf: Maseches Menachos, daf thirty-four."
    assert daf_intro.intro_text("Arachin", 2) == "Up next, today's daf: we begin Maseches Arachin, daf two."
    assert daf_intro.intro_text("Tamid", 26).startswith("Up next, today's daf: we begin Maseches Tamid")
    # A shiur that is not the calendar's daf never says "today's daf"
    assert daf_intro.intro_text("Yoma", 79, todays=False) == "Up next, the Daf Yomi shiur: Maseches Yoma, daf seventy-nine."
    assert daf_intro.intro_title("Menachos", 34) == "Daf intro: Menachos 34"
    assert "Meh-nah-khos" in daf_intro.voice_text("Menachos", 34)
    # Every masechta has a voice spelling
    assert all(say for _, say, _, _ in daf_intro.MASECHTOS)


@pytest.mark.parametrize("title, expected", [
    ("Menachos 34", ("Menachos", 34)),
    ("Daf Yomi - Menachot Daf 34a", ("Menachos", 34)),
    ("menachos_034.mp3", ("Menachos", 34)),
    ("R' Yosef Sokol - Bechoros 61 (Miami Daf)", ("Bechoros", 61)),
    ("Bava Basra 176", ("Bava Basra", 176)),
    ("Baba Metzia 2", ("Bava Metzia", 2)),
    ("Rosh Hashanah 10", ("Rosh Hashanah", 10)),
    ("Menachos 300", None),  # no such daf
    ("Daf Yomi shiur", None),  # no masechta: no clip rather than a wrong one
])
def test_a_shiur_title_gives_its_daf(title, expected):
    assert daf_intro.parse_title(title) == expected


def test_a_made_clip_is_checked_against_what_was_heard():
    ok = daf_intro.transcript_matches
    assert ok("Up next, today's daf. Maseches Menachot, daf 34.", "Menachos", 34)
    assert ok("up next todays daf maseches bava basra daf a hundred and one", "Bava Basra", 101)
    assert ok("one hundred and seventy six bava basra", "Bava Basra", 176)
    assert not ok("maseches menachos daf fourteen", "Menachos", 4)
    assert not ok("maseches menachos daf forty four", "Menachos", 34)
    assert not ok("maseches bechoros daf thirty four", "Menachos", 34)
    # How the voice's Arachin came back from Whisper on the PC (2026-11-18)
    assert ok("Up next, today's daf. We begin Masakas Arachan, daf two.", "Arachin", 2)


def test_the_recognition_prompt_names_every_masechta_and_no_daf():
    prompt = daf_intro.whisper_prompt()
    assert all(name in prompt for name, _, _, _ in daf_intro.MASECHTOS)
    assert not any(ch.isdigit() for ch in prompt)


def test_the_plan_lists_one_clip_a_day():
    days = daf_intro.plan(date(2026, 11, 18), 3)
    assert [(d["date"], d["title"]) for d in days] == [
        ("2026-11-18", "Daf intro: Bechoros 61"),
        ("2026-11-19", "Daf intro: Arachin 2"),
        ("2026-11-20", "Daf intro: Arachin 3"),
    ]


@pytest.mark.asyncio
async def test_the_intro_airs_right_before_the_shiur(db_session: AsyncSession):
    """Main at 8 PM: the hourly schedule announcement block and the Daf Yomi block share
    the minute. The intro and the shiur are queued back to back, in that order, in the
    order every player reads (preempt_at, position), so nothing airs between them."""
    station = Station(id=uuid.uuid4(), name="Main", timezone="UTC", is_active=True)
    sched = Asset(id=uuid.uuid4(), title="KBR schedule", duration=8.0, file_path="s.mp3",
                  asset_type="announcement", category="block_only")
    intro = Asset(id=uuid.uuid4(), title="Daf intro: Arachin 2", duration=5.0, file_path="i.mp3",
                  asset_type="announcement", category="block_only")
    shiur = Asset(id=uuid.uuid4(), title="Arachin 2", duration=2700.0, file_path="d.mp3",
                  asset_type="shiur", category="block_only")
    db_session.add_all([station, sched, intro, shiur])
    await db_session.flush()
    at = (datetime.now(UTC) + timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
    hhmm = at.strftime("%H:%M")
    db_session.add_all([
        ProgramBlock(id=uuid.uuid4(), station_id=station.id, name="Hourly schedule announcement",
                     times=",".join(f"{h:02d}:00" for h in range(24)), timing_mode="hard",
                     items=[{"kind": "asset", "asset_id": str(sched.id)}]),
        ProgramBlock(id=uuid.uuid4(), station_id=station.id, name="Shiur: Daf Yomi",
                     times=hhmm, timing_mode="hard",
                     items=[{"kind": "asset", "asset_id": str(intro.id), "label": "Daf intro"},
                            {"kind": "asset", "asset_id": str(shiur.id), "label": "Daf Yomi"}]),
    ])
    await db_session.flush()
    await reconcile_block_entries(db_session, station.id)
    await db_session.commit()

    rows = (await db_session.execute(
        select(QueueEntry).where(QueueEntry.station_id == station.id, QueueEntry.preempt_at.isnot(None))
        .order_by(QueueEntry.preempt_at, QueueEntry.position)
    )).scalars().all()
    group = [r.asset_id for r in rows if r.preempt_at.replace(tzinfo=UTC) == at]
    assert group == [sched.id, intro.id, shiur.id]

"""A stand-in for the playout engine (infra/streaming/radio.liq), driven by virtual time.

What it copies from radio.liq, because the backend's decisions depend on it:
- music lane: one item on air and one pre-loaded. It asks GET /next as soon as an item
  goes on air (so the next is ready), again right after a /flush, and every 2 s while the
  backend answers nothing. An item ends at its cue-out (or length) and the pre-loaded one
  starts at once (song crossfades are left out: a 1 s overlap does not change any rule).
- timed lane: /timed items wait for their `at`; items with the same `at` are one group,
  played in `seq` order. The group takes the air on its second, the music item on air is
  cut and does not come back (the backend re-queues a paused shiur itself). After the
  group the music lane carries on with its pre-loaded item.
- /skip ends the item on air now (music or timed); /timed_drop forgets waiting timed items; /hello on start.
- With nothing to play the real engine plays its emergency music: logged here as
  `emergency`, which the checks count as dead air.

Every start is reported with POST /started semantics (playout_engine.handle_started), each
in its own database session, as a request would be.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import joinedload

_FIELD = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')
NEXT_RETRY = 2.0  # radio.liq retries an empty /next after a moment
PROTO = 5
HOLD_SECONDS = 8.0


def parse_uri(uri: str) -> dict:
    body = uri[len("annotate:"):] if uri.startswith("annotate:") else uri
    return {k: v.replace('\\"', '"').replace("\\\\", "\\") for k, v in _FIELD.findall(body)}


@dataclass
class Item:
    entry_id: str
    lane: str
    fields: dict
    length: float = 0.0  # seconds it plays from its cue-in


@dataclass
class Segment:
    """Something on air: an item from a lane, or the engine's emergency music."""
    station: str
    entry_id: str | None
    lane: str  # main, timed, emergency
    start: float
    end: float | None = None
    title: str = ""
    kind: str = ""
    cue_in: float = 0.0
    cut: bool = False  # ended before its own end (Next, a timed take-over)


@dataclass
class TimedWaiting:
    at: float
    entry_id: str
    uri: str
    seq: int = 0
    of: int = 1


@dataclass
class FakeEngine:
    sim: object
    station_id: object
    on_air: Segment | None = None
    on_air_item: Item | None = None
    cued: Item | None = None
    given: list = field(default_factory=list)  # newest first
    timed: list = field(default_factory=list)
    timed_held: set = field(default_factory=set)
    group: list = field(default_factory=list)  # timed items of the group on air, after the current
    ask_next_at: float | None = None
    commands: list = field(default_factory=list)
    # None: the engine talks to the backend itself (the stream server). Else a local FM
    # box's relay stands between them (tests/sim/offline.py): .next(given), .started(...)
    backend: object = None

    @property
    def sid(self) -> str:
        return str(self.station_id)

    # ── commands from the backend (playout_engine._command) ────────────────────
    async def command(self, path: str, payload: dict | None) -> bool:
        payload = payload or {}
        now = self.sim.t
        self.commands.append((now, path, payload))
        if path == "/flush":
            # radio.liq forgets the dropped entry was handed to it (it will not air), unless
            # it is the one on air; otherwise the backend takes it for aired unreported
            on_air = self.on_air.entry_id if self.on_air is not None and self.on_air.lane == "main" else None
            if self.given and self.given[0] != on_air:
                self.given.pop(0)
            self.cued = None
            self.ask_next_at = now + 0.3
        elif path == "/skip":
            # radio.liq's skip_on_air ends whatever is on air, a timed item too (the
            # silence still playing when a blackout ends)
            if self.on_air is not None:
                self.sim.schedule(now + 0.2, self.sid, "skip")
        elif path == "/timed":
            eid = str(payload.get("id") or "")
            if eid and eid in self.timed_held:
                return True  # it ignores an id it already holds
            if eid:
                self.timed_held.add(eid)
            self.timed.append(TimedWaiting(float(payload["at"]), eid, payload["uri"],
                                           int(payload.get("seq") or 0), int(payload.get("of") or 1)))
            self.sim.schedule(max(float(payload["at"]), now), self.sid, "timed")
        elif path == "/timed_drop":
            ids = {str(i) for i in payload.get("ids") or []}
            self.timed = [w for w in self.timed if w.entry_id not in ids]
            self.group = [w for w in self.group if w.entry_id not in ids]
        # /live, /overlay, /alert_stop: nothing the simulated stations use yet
        return True

    def status(self) -> dict:
        seg = self.on_air
        return {"ok": True, "lane": seg.lane if seg else None,
                "entry": seg.entry_id if seg else None}

    # ── the engine's own clock ─────────────────────────────────────────────────
    async def start(self):
        from app.services import playout_engine
        await playout_engine.handle_hello(self.station_id, PROTO, f"sim-{self.sid[:8]}")
        await self.ask_next(start_if_idle=True)

    async def on_event(self, kind: str):
        now = self.sim.t
        if kind == "end":
            if self.on_air is not None and self.on_air.end is not None and self.on_air.end <= now + 1e-6:
                await self._item_ended()
        elif kind == "skip":
            if self.on_air is not None:
                self._close(now, cut=True)
                await self._play_after(now)
        elif kind == "timed":
            await self._maybe_take_over()
        elif kind == "ask":
            if self.ask_next_at is not None and self.ask_next_at <= now + 1e-6:
                self.ask_next_at = None
                await self.ask_next(start_if_idle=self.on_air is None or self.on_air.lane == "emergency")

    def next_event_time(self) -> float | None:
        times = []
        if self.on_air is not None and self.on_air.end is not None:
            times.append(self.on_air.end)
        if self.ask_next_at is not None:
            times.append(self.ask_next_at)
        return min(times) if times else None

    async def tick(self):
        """Called at every simulation step: due ends, asks and timed take-overs."""
        now = self.sim.t
        await self._maybe_take_over()
        if self.ask_next_at is not None and self.ask_next_at <= now + 1e-6:
            await self.on_event("ask")
        if self.on_air is not None and self.on_air.end is not None and self.on_air.end <= now + 1e-6:
            await self._item_ended()

    async def _maybe_take_over(self):
        now = self.sim.t
        due = [w for w in self.timed if w.at <= now + 1e-6]
        if not due:
            return
        at = min(w.at for w in due)
        group = sorted([w for w in self.timed if w.at == at], key=lambda w: w.seq)
        self.timed = [w for w in self.timed if w.at != at]
        # radio.liq timed_take_over(): music on air is cut; an earlier timed item on air
        # (a block's shiur) is cut too unless it has only just started or is about to
        # end; items still queued in the timed lane play first (it is one queue).
        seg = self.on_air
        if seg is not None and seg.lane == "timed":
            elapsed, left = now - seg.start, (seg.end or now) - now
            if elapsed > 1.0 and left > 1.0:
                self._close(now, cut=True)
                self.group = self.group + group
                await self._next_in_group(now)
            else:
                self.group = self.group + group
            return
        if seg is not None:
            self._close(now, cut=True)
        self.group = self.group + group
        await self._next_in_group(now)

    async def _next_in_group(self, now: float) -> bool:
        while self.group:
            w = self.group.pop(0)
            item = await self.sim.item_for(w.entry_id, "timed", w.uri)
            if item is None:
                continue
            await self._put_on_air(item, now)
            return True
        return False

    async def _item_ended(self):
        now = self.on_air.end
        self._close(now, cut=False)
        await self._play_after(now)

    async def _play_after(self, now: float):
        if await self._next_in_group(now):
            return
        # A timed group due meanwhile (or now) takes the air first
        if any(w.at <= now + 1e-6 for w in self.timed):
            await self._maybe_take_over()
            return
        if self.cued is not None:
            item, self.cued = self.cued, None
            await self._put_on_air(item, now)
            return
        await self.ask_next(start_if_idle=True)

    async def ask_next(self, start_if_idle: bool):
        from app.services import playout_engine
        now = self.sim.t
        if self.cued is not None:
            return
        if self.backend is not None:
            got = await self.backend.next(list(self.given[:5]))
        else:
            async with self.sim.session() as db:
                got = await playout_engine.handle_next(db, self.station_id, list(self.given[:5]),
                                                       proto=PROTO, engine_live_on=False)
        if got is None:
            # Deep inside a Shabbos or Yom Tov window the answer stays "nothing" for
            # hours; the real engine keeps asking every few seconds, here once a minute
            deep = getattr(self.sim, "_deep_in_blackout", None)
            self.ask_next_at = now + (60.0 if deep and deep(self.sid, now) else NEXT_RETRY)
            if self.on_air is None and any(w.at - now <= HOLD_SECONDS for w in self.timed):
                pass  # radio.liq's hold lane: quiet for up to 8 s before a loaded timed item
            elif self.on_air is None:
                # nothing on air and nothing to play: the emergency music
                self.on_air = Segment(self.sid, None, "emergency", now, None, "emergency music")
                self.sim.log.append(self.on_air)
            return
        eid = got["entry_id"]
        if eid:
            self.given.insert(0, eid)
            del self.given[8:]
        item = await self.sim.item_for(eid, "main", got["uri"])
        if item is None:
            self.ask_next_at = now + NEXT_RETRY
            return
        idle = self.on_air is None or self.on_air.lane == "emergency"
        if idle and start_if_idle:
            if self.on_air is not None:
                self._close(now, cut=True)
            await self._put_on_air(item, now)
        else:
            self.cued = item

    async def _put_on_air(self, item: Item, now: float):
        from app.services import playout_engine
        cue_in = float(item.fields.get("liq_cue_in") or 0.0)
        seg = Segment(self.sid, item.entry_id, item.lane, now, now + max(item.length, 0.5),
                      item.fields.get("title", ""), item.fields.get("kbr_kind", ""), cue_in)
        self.on_air, self.on_air_item = seg, item
        self.sim.log.append(seg)
        self.sim.schedule(seg.end, self.sid, "end")
        if self.backend is not None:
            await self.backend.started(item.entry_id, item.lane, now)
        else:
            async with self.sim.session() as db:
                await playout_engine.handle_started(db, self.station_id, item.entry_id, item.lane, at=now)
        if item.lane == "main" and self.cued is None:
            await self.ask_next(start_if_idle=False)

    def _close(self, now: float, cut: bool):
        seg = self.on_air
        if seg is None:
            return
        if seg.end is None or now < seg.end - 1e-6:
            seg.cut = cut or seg.end is None and seg.lane != "emergency"
            seg.end = now
        self.on_air, self.on_air_item = None, None


def _dt(epoch: float) -> datetime:
    from datetime import UTC
    return datetime.fromtimestamp(epoch, UTC)


async def item_from_db(db, entry_id: str, lane: str, uri: str) -> Item | None:
    from app.models.queue_entry import QueueEntry
    import uuid as _uuid
    row = None if not entry_id else (await db.execute(
        select(QueueEntry).options(joinedload(QueueEntry.asset)).where(QueueEntry.id == _uuid.UUID(entry_id))
    )).unique().scalar_one_or_none()
    if row is None and entry_id:
        # deleted on the site since the box got its plan, or an item the box worked out
        # itself (a song it drew, a shiur's resume): the box has the file
        from app.models.asset import Asset
        f = parse_uri(uri)
        asset = None
        try:
            asset = await db.get(Asset, _uuid.UUID(f.get("kbr_asset", "")))
        except ValueError:
            pass
        length = float(asset.duration or 0.0) if asset is not None else float(f.get("sim_length") or 240.0)
        cue_out = f.get("liq_cue_out")
        if cue_out:
            length = min(length, float(cue_out)) if length else float(cue_out)
        return Item(entry_id, lane, f, max(length - float(f.get("liq_cue_in") or 0.0), 0.0))
    fields = parse_uri(uri)
    if not entry_id:
        # the box's own silence file during a blackout (cut where the blackout ends)
        return Item("", lane, fields, min(60.0, float(fields.get("liq_cue_out") or 60.0)))
    if row is None or row.asset is None:
        return None
    length = float(row.asset.duration or 0.0)
    cue_out = fields.get("liq_cue_out")
    if cue_out:
        length = min(length, float(cue_out)) if length else float(cue_out)
    cue_in = float(fields.get("liq_cue_in") or 0.0)
    return Item(entry_id, lane, fields, max(length - cue_in, 0.0))

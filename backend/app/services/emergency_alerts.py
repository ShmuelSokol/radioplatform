"""Emergency alerts (EAS) on our side: the override that takes a station's air.

How a US station meets its EAS duty: an FCC-certified EAS box at the transmitter (for an
LPFM a decoder is enough) listens to the stations the state plan assigns and polls
FEMA's IPAWS feed, and interrupts the air itself. Nobody from the government connects
to a station's own computers. This module is the automation's side of that hand-off,
as professional automation does it:

  * the EAS box (or the station's own tool) sends the alert's audio to
    POST /emergency/device/activate with the station's emergency token;
  * on a local FM box, the EAS box's audio line goes into the engine's alert line and
    takes the air by itself, with no internet (radio.liq, KBR_ALERT_ALSA_DEVICE);
  * staff can send a spoken test of the override (never EAS tones: 47 CFR 11.45).

The engine plays the alert above everything (live shows and time checks included),
whole, then the programme carries on. Every alert is logged (EmergencyAlert), shown on
the Emergency Alerts page, raised as an alert and pushed to ntfy. See
wiki/emergency-alerts.md.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.emergency_alert import EmergencyAlert
from app.models.station import Station
from app.services import eas_tones

logger = logging.getLogger(__name__)

# What is on air now, per station: {"alert_id", "since", "source", "kind", "title", "until"}
# ("until" for a browser-player station, which sends no reports: the alert's length)
_active: dict[str, dict] = {}
MAX_AUDIO_BYTES = 30 * 1024 * 1024
MAX_SECONDS = 15 * 60  # an EAS message is at most two minutes; a longer file is refused


def token_ok(presented: str | None) -> bool:
    expected = settings.EMERGENCY_API_TOKEN
    return bool(expected) and bool(presented) and hmac.compare_digest(presented, expected)


def active(station_id) -> dict | None:
    got = _active.get(str(station_id))
    if got and got.get("until") and time.time() > got["until"]:
        _active.pop(str(station_id), None)
        return None
    return got


def all_active() -> dict[str, dict]:
    return {sid: a for sid in list(_active) if (a := active(sid))}


def reset() -> None:
    _active.clear()


def duration_of(data: bytes) -> float | None:
    """Length in seconds, from the decoded audio (ffprobe on a pipe cannot tell an MP3's
    length), or None when it cannot be read."""
    try:
        seconds = len(eas_tones.decode(data)) / eas_tones.RATE
        return seconds if seconds > 0.05 else None
    except Exception:
        return None


def _ext(filename: str | None, content_type: str | None) -> str:
    name = (filename or "").lower()
    for ext in (".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac"):
        if name.endswith(ext):
            return ext
    if content_type and "wav" in content_type:
        return ".wav"
    return ".mp3"


async def store_audio(data: bytes, filename: str | None, content_type: str | None) -> tuple[str, str]:
    """Stores the alert's audio; returns (storage key, address the engine fetches)."""
    from app.services import storage_service

    key = f"emergency/{uuid.uuid4()}{_ext(filename, content_type)}"
    await storage_service.upload_file(data, key, content_type or "audio/mpeg")
    if settings.supabase_storage_enabled:
        url = f"{settings.SUPABASE_URL}/storage/v1/object/public/{settings.SUPABASE_STORAGE_BUCKET}/{key}"
    else:
        # Local and test setups: served by GET /emergency/audio/{key}
        base = (settings.BACKEND_PUBLIC_URL or "http://127.0.0.1:8000").rstrip("/")
        url = f"{base}/api/v1/emergency/audio/{key.split('/', 1)[1]}"
    return key, url


def _uri(alert: EmergencyAlert, url: str) -> str:
    from app.services.playout_engine import _quote

    fields = {"kbr_alert": str(alert.id), "kbr_lane": "alert", "kbr_kind": "alert",
              "title": alert.title or "Emergency alert"}
    return "annotate:" + ",".join(f"{k}={_quote(v)}" for k, v in fields.items()) + ":" + url


async def _station_name(db: AsyncSession, station_id) -> str:
    station = await db.get(Station, station_id) if station_id else None
    return station.name if station else "a station"


async def _notify(db: AsyncSession, alert: EmergencyAlert, what: str) -> None:
    """Alert row (Alerts page, texts for those who asked) and an ntfy push."""
    from app.services import alert_service, bug_report_service

    name = await _station_name(db, alert.station_id)
    real = alert.kind != "test"
    kind = {"test": "Test of the emergency override", "rmt": "Required Monthly Test",
            "rwt": "Required Weekly Test"}.get(alert.kind, "Emergency alert")
    who = {"device": "the EAS box", "line": "the EAS box's audio line", "staff": alert.requested_by or "staff"}
    message = f"{kind} {what} on {name} (sent by {who.get(alert.source, alert.source)})."
    if alert.title:
        message += f" {alert.title}"
    try:
        await alert_service.create_alert(
            db, "system", "critical" if real and what != "ended" else "warning",
            f"{kind} {what}", message, station_id=alert.station_id,
            context={"emergency_alert_id": str(alert.id), "kind": alert.kind, "source": alert.source},
        )
    except Exception as e:
        logger.warning("Emergency alert: alert row failed: %s", e)
    asyncio.ensure_future(bug_report_service.send_ntfy(
        message, title=f"{kind} {what}", tags="rotating_light",
        priority="urgent" if real and what == "ON AIR" else "high",
        click="https://kbrlive.com/admin/emergency",
    ))


async def activate(
    db: AsyncSession, *, station_ids: list, data: bytes, filename: str | None,
    content_type: str | None, kind: str, title: str | None, source: str,
    requested_by: str | None, has_tones: bool, found: dict | None = None,
) -> list[EmergencyAlert]:
    """Sends the alert to each station at once; returns one log row per station."""
    from app.api.v1.websocket import manager as ws_manager
    from app.services import playout_engine

    key, url = await store_audio(data, filename, content_type)
    seconds = duration_of(data)
    now = datetime.now(timezone.utc)
    rows: list[EmergencyAlert] = []
    for station_id in station_ids:
        alert = EmergencyAlert(
            id=uuid.uuid4(), station_id=station_id, kind=kind, source=source, title=title,
            requested_by=requested_by, audio_key=key, audio_url=url, has_tones=has_tones,
            status="sent", requested_at=now,
            details={"seconds": seconds, "tones_found": found or {}},
        )
        db.add(alert)
        rows.append(alert)
    await db.flush()

    async def send(alert: EmergencyAlert) -> None:
        sid = str(alert.station_id)
        if settings.engine_drives(sid):
            # The engine answers once the file is loaded; a slow download is still taken
            ok = await playout_engine._command(
                "/alert", {"uri": _uri(alert, url), "id": str(alert.id)},
                station_id=sid, wait=8.0, slow_is_ok=True,
            )
            if not ok:
                alert.status = "failed"
                alert.error = "The playout engine did not answer"
            else:
                # Shown (banner, Stop) from now: the engine's start report follows within a
                # second or two, but staff screens waited for it and then for their next
                # poll, and Stop could be pressed only 9 s after Send (live test 2026-11-18).
                # "until" ends it if the engine never reports.
                _active[sid] = {"alert_id": str(alert.id), "since": time.time(), "source": alert.source,
                                "kind": alert.kind, "title": alert.title,
                                "until": time.time() + (seconds or 120.0) + 30.0}
            # status becomes on_air when the engine reports the start
        else:
            # Browser player: every open player stops its programme, plays this, carries on
            await ws_manager.broadcast_to_station(sid, {"type": "emergency_alert", "data": {
                "id": str(alert.id), "url": url, "title": alert.title or "Emergency alert",
            }})
            alert.status = "on_air"
            alert.started_at = datetime.now(timezone.utc)
            if seconds:
                alert.ended_at = alert.started_at + timedelta(seconds=seconds)
                alert.seconds_on_air = round(seconds, 1)
                alert.status = "done"
            _active[sid] = {"alert_id": str(alert.id), "since": time.time(), "source": alert.source,
                            "kind": alert.kind, "title": alert.title,
                            "until": time.time() + (seconds or 120.0)}

    await asyncio.gather(*(send(a) for a in rows))
    await db.flush()
    await _nudge_screens()
    for alert in rows:
        if alert.status == "failed":
            await _notify(db, alert, "FAILED to reach the station")
        elif not settings.engine_drives(str(alert.station_id)):
            await _notify(db, alert, "ON AIR")
    return rows


async def engine_report(
    db: AsyncSession, station_id, *, phase: str, alert_id: str | None, source: str | None,
    at: float | None, started_at: float | None,
) -> EmergencyAlert | None:
    """The engine says an alert went on air or came off (also for its alert line, which
    the backend did not send: that one gets its log row here)."""
    from app.services import playout_engine

    sid = str(station_id)
    when = datetime.fromtimestamp(at, timezone.utc) if at else datetime.now(timezone.utc)
    alert = None
    if alert_id:
        try:
            alert = await db.get(EmergencyAlert, uuid.UUID(alert_id))
        except ValueError:
            alert = None
    if alert is None and source == "line":
        # The line's start: one row per spell, found again by its start time at the end
        began = datetime.fromtimestamp(started_at, timezone.utc) if started_at else when
        alert = (await db.execute(
            select(EmergencyAlert).where(
                EmergencyAlert.station_id == station_id, EmergencyAlert.source == "line",
                EmergencyAlert.requested_at >= began - timedelta(seconds=2),
                EmergencyAlert.requested_at <= began + timedelta(seconds=2),
            ).limit(1)
        )).scalar_one_or_none()
        if alert is None:
            alert = EmergencyAlert(
                id=uuid.uuid4(), station_id=station_id, kind="alert", source="line",
                title="From the EAS box's audio line", status="on_air", requested_at=began,
                started_at=began,
            )
            db.add(alert)
            await db.flush()
    if alert is None:
        logger.warning("Emergency alert report for an unknown alert %s on %s", alert_id, sid)
        return None
    if phase == "started":
        if alert.status in ("sent", "on_air", "failed"):
            alert.status = "on_air"
            alert.started_at = alert.started_at or when
            alert.error = None
        _active[sid] = {"alert_id": str(alert.id), "since": time.time(), "source": alert.source,
                        "kind": alert.kind, "title": alert.title, "until": None}
        await db.flush()
        await _notify(db, alert, "ON AIR")
    elif phase == "ended":
        alert.ended_at = when
        if alert.started_at:
            start = alert.started_at if alert.started_at.tzinfo else alert.started_at.replace(tzinfo=timezone.utc)
            alert.seconds_on_air = round((when - start).total_seconds(), 1)
        if alert.status != "cancelled":
            alert.status = "done"
        if (_active.get(sid) or {}).get("alert_id") == str(alert.id):
            _active.pop(sid, None)
        await db.flush()
        if settings.engine_drives(sid):
            # Hard items due while the alert was on air never aired: closed, as after a
            # live show (the engine drops them too), so none plays minutes late
            try:
                await playout_engine.close_timed_lost_to_live_show(db, station_id, datetime.now(timezone.utc))
            except Exception as e:
                logger.warning("Emergency alert: closing late timed items failed: %s", e)
        await _notify(db, alert, "ended")
    await _nudge_screens()
    return alert


async def stop(db: AsyncSession, alert: EmergencyAlert, by: str | None) -> bool:
    """Ends the alert on air now (and drops any waiting behind it on that station)."""
    from app.api.v1.websocket import manager as ws_manager
    from app.services import playout_engine

    sid = str(alert.station_id)
    ok = True
    if settings.engine_drives(sid):
        ok = await playout_engine._command("/alert_stop", {}, station_id=sid)
    else:
        await ws_manager.broadcast_to_station(sid, {"type": "emergency_alert_stop", "data": {"id": str(alert.id)}})
    if alert.status in ("sent", "on_air"):
        alert.status = "cancelled"
        alert.details = {**(alert.details or {}), "stopped_by": by}
        if not alert.ended_at:
            alert.ended_at = datetime.now(timezone.utc)
    if (_active.get(sid) or {}).get("alert_id") == str(alert.id):
        _active.pop(sid, None)
    await db.flush()
    await _nudge_screens()
    return ok


async def _nudge_screens() -> None:
    """Tell every open tab (its /ws/site socket) that emergency state changed: staff pages
    refetch the banner and the Emergency Alerts page at once instead of at their next poll.
    The message carries nothing; listener tabs ignore it."""
    async def go() -> None:
        # A moment's wait so the request has committed when the screens read the log
        await asyncio.sleep(0.3)
        try:
            from app.api.v1.site_release import broadcast_event

            await broadcast_event("emergency")
        except Exception as e:
            logger.debug("Emergency alert: screen nudge failed: %s", e)

    asyncio.ensure_future(go())

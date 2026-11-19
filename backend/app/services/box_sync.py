"""What was done at a local FM box while its internet line was down, brought to the site.

The box at BMG has a control room of its own (`infra/local/kbr_panel.py`) for the days
the internet is out: someone there can queue a song, schedule a recording at a time,
load a spot or shiur from a USB stick, or take an item off. Offline the box is in charge.
When the line returns:

* what aired comes from the box: its kept start reports (playout_engine.handle_started);
* recordings loaded at the box are uploaded into the library first (`store_box_upload`),
  so their plays are logged against them;
* the future is the site's: whatever staff changed online stands. Items added at the
  box that have not aired yet are put into the site's queue (`apply_box_changes`); items
  of the site's queue taken off at the box are NOT removed here (staff may have changed
  them meanwhile): an alert lists them, and the other additions, for staff to check.

See wiki/local-fm-box.md ("Control room on the box").
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.queue_entry import QueueEntry

logger = logging.getLogger(__name__)

BOX_CATEGORY = "box_upload"
KINDS = {"music", "spot", "shiur", "jingle", "story", "weather"}
MAX_BOX_UPLOAD_BYTES = 300 * 1024 * 1024


def _safe_title(title: str | None, fallback: str) -> str:
    title = re.sub(r"\s+", " ", (title or "").strip())[:200]
    return title or fallback


async def store_box_upload(db: AsyncSession, upload_id: str, audio: bytes, title: str | None, kind: str | None) -> Asset:
    """A recording loaded from a USB stick at the box, now in the library (once: the
    same upload id sent again gets the asset made the first time)."""
    from app.services.weather_spot_service import _store_upload

    existing = (await db.execute(
        select(Asset).where(Asset.category == BOX_CATEGORY, Asset.metadata_extra["box_upload_id"].astext == upload_id)
    )).scalars().first()
    if existing is not None:
        return existing
    kind = (kind or "music").lower()
    if kind not in KINDS:
        kind = "music"
    asset_type, category = ("spot", "weather_spot") if kind == "weather" else (kind, BOX_CATEGORY)
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", upload_id)[:40]
    url, duration = await _store_upload(audio, f"box/{safe}")
    asset = Asset(
        id=uuid.uuid4(), title=_safe_title(title, f"Loaded at the FM box {safe}"),
        artist="Loaded at the FM box", file_path=url, duration=duration,
        asset_type=asset_type, category=category if kind != "weather" else BOX_CATEGORY,
        metadata_extra={"box_upload_id": upload_id, "source": "fm_box",
                        "uploaded_at": datetime.now(UTC).isoformat()},
    )
    db.add(asset)
    await db.flush()
    logger.info("FM box upload %s stored as %s (%s, %.0f s)", upload_id, asset.id, kind, duration)
    return asset


async def apply_box_changes(db: AsyncSession, station_id, changes: dict) -> dict:
    """Additions made at the box that are still to come go into the site's queue;
    removals and anything that could not be applied go into one alert for staff."""
    from app.services.alert_service import create_alert

    now = datetime.now(UTC)
    added, notes = 0, []
    max_pos = (await db.execute(
        select(func.coalesce(func.max(QueueEntry.position), 0)).where(
            QueueEntry.station_id == station_id, QueueEntry.status.in_(["pending", "playing"]))
    )).scalar() or 0
    for item in changes.get("added") or []:
        try:
            asset_id = uuid.UUID(str(item.get("asset_id")))
        except ValueError:
            notes.append(f"Not added (unknown recording): {item.get('title') or '?'}")
            continue
        asset = await db.get(Asset, asset_id)
        if asset is None:
            notes.append(f"Not added (not in the library): {item.get('title') or '?'}")
            continue
        at = item.get("at")
        when = datetime.fromtimestamp(float(at), UTC) if at else None
        if when is not None and when <= now:
            continue  # its time passed while the line was down (it aired, or was dropped there)
        max_pos += 1
        db.add(QueueEntry(
            id=uuid.uuid4(), station_id=station_id, asset_id=asset.id, position=max_pos,
            status="pending", source="manual", preempt_at=when,
            timing_mode=(item.get("mode") if item.get("mode") in ("hard", "soft") else "hard") if when else None,
        ))
        added += 1
        notes.append(f"Added to the queue: {asset.title}" + (f" at {when.isoformat(timespec='minutes')}" if when else ""))
    removed = changes.get("removed") or []
    for item in removed:
        at = item.get("at")
        when = f" (due {datetime.fromtimestamp(float(at), UTC).isoformat(timespec='minutes')})" if at else ""
        notes.append(f"Taken off at the box, still in the site's queue: {item.get('title') or '?'}{when}")
    await db.flush()
    if notes:
        await create_alert(
            db, "system", "warning" if removed else "info", "Changes made at the FM box while it was offline",
            "\n".join(notes[:60]) + (f"\n... and {len(notes) - 60} more" if len(notes) > 60 else ""),
            station_id=station_id, context={"box_changes": changes},
        )
    return {"ok": True, "added": added, "noted": len(notes)}

# Hourly and Half-Hour Slots

## How scheduling works

`queue_replenish_service.py` pre-schedules announcements 48 hours ahead using `preempt_at` timestamps on queue entries.

## Slot types

| Time | Type | Preempt | Source |
|------|------|---------|--------|
| :00 | Station ID + Time announcement + Weather | Hard (interrupts current track) | `queue_replenish_service.py:585-655` |
| :15 | Ad slot | Soft (waits for song to finish) | `queue_replenish_service.py:657-719` |
| :30 | Ad slot only | Soft | Same |
| :45 | Ad slot | Soft | Same |

## Station automation_config

```json
{
  "hourly_station_id": true,
  "hourly_time_announcement": true,
  "weather_enabled": true,
  "weather_interval_minutes": 30,
  "ad_slot_minutes": [15, 30, 45]
}
```

## FIXED 2026-06-10: announcement-masking bug + :30 weather

Two bugs fixed in `_schedule_hourly_announcements`:

1. **Masking (severe)**: the dedup set bucketed ALL pending preempts (including the 504 seven-day ad slots at :15/:30/:45) to the top of the hour — so once ads were scheduled, every hour looked "already scheduled" and time/weather/station-ID announcements silently stopped being created. Fix: dedup excludes `source == "ad_slot"` and compares exact minutes.
2. **:30 weather**: `weather_interval_minutes: 30` was ignored (weather only at :00). Fix: weather-only entry also scheduled at `hour + 30min` when interval == 30.

If announcements ever vanish again, check the dedup set in `_schedule_hourly_announcements` first.

## TTS generation

- Time + weather generated via ElevenLabs (`weather_spot_service.py`)
- Uploaded to Supabase as `weather/{slot}_time.mp3` and `weather/{slot}_weather.mp3`
- Stored as Assets (type: jingle/spot, category: time_announcement/weather_spot)

## Uploaded weather spots (Shprach Kol Berama voice) — added 2026-11-15

The weather spot is ONE asset per Eastern day (`asset_type="spot"`, `category="weather_spot"`, `metadata_extra.slot_key="YYYY-MM-DD"`). `get_or_create_weather_spot_assets` only calls ElevenLabs when that day has no such asset.

- `PUT /api/v1/weather-spots/{YYYY-MM-DD}` (manager auth, multipart: `file`, optional `script`, optional `source` default `shprach`) makes an uploaded recording that day's spot. `GET` on the same path shows what the day holds (404 if none). Code: `api/v1/weather_spots.py`, `store_uploaded_weather_spot()` in `weather_spot_service.py`.
- **It updates the existing asset in place** (same id, new `file_path`/`duration`, `metadata_extra` replaced). This matters: the scheduler pre-queues the day's asset up to 48h ahead, so an ElevenLabs spot usually already exists and is already in the queue. Creating a second asset would leave the queue playing the old one.
- Each upload gets a new storage path (`weather/{date}_weather_{source}_{HHMMSS}.mp3`) because the public Supabase URL is cached.
- Days with no upload keep the ElevenLabs fallback.
- **Time announcements**: `PUT /api/v1/time-announcements/{hour}` (0-23 Eastern, same multipart fields) stores ONE reusable clip per hour of the day (`category="time_announcement"`, `metadata_extra.time_of_day="15"`), played at that hour every day. `GET /api/v1/time-announcements` lists the hours that have one. An uploaded hour clip wins over the per-slot ElevenLabs clip; hours without one fall back to ElevenLabs. Entries queued before the first upload still point at the old per-slot ElevenLabs assets, so the switch takes up to 48h to show.
- ElevenLabs is now optional for the hourly scheduler: with no key, `get_or_create_weather_spot_assets` returns only what was uploaded (None for the rest) and never calls TTS or the forecast. The manual preview/force-insert endpoints in `api/v1/queue.py` (`_maybe_insert_weather_spot`, weather preview) still require an ElevenLabs key.
- FIXED 2026-11-15: the ElevenLabs time text used the generation time, not the slot's hour, so clips scheduled ahead all announced the hour they were made. It now speaks the slot's hour.
- The uploader lives on the voice PC, not in this repo: `C:\shprach\realness\kolberama\daily_station.ps1` (scheduled task "Kol Berama Daily Weather Spot") runs `weekly.py` then `station_upload.py`.

## Hard vs soft preempts

- **Hard** (`source != "ad_slot"`): Interrupts current track immediately at `preempt_at` time
- **Soft** (`source == "ad_slot"`): Plays after current song finishes

See also: [streaming-architecture](streaming-architecture.md)

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

## Hard vs soft preempts

- **Hard** (`source != "ad_slot"`): Interrupts current track immediately at `preempt_at` time
- **Soft** (`source == "ad_slot"`): Plays after current song finishes

See also: [streaming-architecture](streaming-architecture.md)

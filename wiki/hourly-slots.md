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

## Bug: weather_interval_minutes ignored (as of 2026-05-24)

Config says `weather_interval_minutes: 30` but code only inserts weather at :00 of each hour. The old 30-min logic in `scheduling.py:359-379` was disabled to prevent duplicates, and the replacement (`queue_replenish_service.py:620`) only loops hourly boundaries. **Weather at :30 is NOT happening.**

## TTS generation

- Time + weather generated via ElevenLabs (`weather_spot_service.py`)
- Uploaded to Supabase as `weather/{slot}_time.mp3` and `weather/{slot}_weather.mp3`
- Stored as Assets (type: jingle/spot, category: time_announcement/weather_spot)

## Hard vs soft preempts

- **Hard** (`source != "ad_slot"`): Interrupts current track immediately at `preempt_at` time
- **Soft** (`source == "ad_slot"`): Plays after current song finishes

See also: [streaming-architecture](streaming-architecture.md)

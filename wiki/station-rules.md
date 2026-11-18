# Which schedule rules apply to a station

Schedule rules (`ScheduleRule`, the Rules page) carry an optional `station_id`.

- A station with at least one active rule of its own follows only those.
- A station with none follows the global rules (`station_id` empty).
- A rule of another station never applies.

Read them only through `rules_for_station(db, station_id)` in
`services/queue_replenish_service.py`, and compare hours and weekdays with the
station's own clock using `rule_covers(rule, local_time)` (ranges past midnight
such as 22-2 work).

Who reads them:

- the fill (`QueueReplenishService._load_rules`),
- the warning shown after a drag-and-drop move in the control room
  (`queue._validate_queue_move`; a warning never stops the move),
- `GET /rules/preview?station_id=` (without a station it still mixes every rule).

## What went wrong (2026-11-17)

Shmuel moved a song up Main's queue and was warned: Daypart rule "All Day Stories
(Stories Station)" expects stories during 0:00-24:00, but this asset is music. The
move check selected every active daypart rule of every station and compared rule
hours with the UTC hour. The move itself went through; only the warning was wrong.

Tests: `tests/test_rules_station_scope.py`.

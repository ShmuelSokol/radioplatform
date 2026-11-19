# Blackout window generation (Shabbos and Yom Tov)

`shabbos_service` computes the windows: candle lighting (18 minutes before shekia, 40 in an
Israel time zone) to 72 minutes after shekia; Yom Tov next to Shabbos merges into one window.
`station_service.store_generated_windows` saves them, for both the Holidays page's
`POST /holidays/auto-generate` and the automatic run when a station gets a location.

## Regenerating corrects stored windows

A window already stored for the station (same name, start within 3 hours) is never made twice.
When its stored times differ from the computed ones by more than a minute it is corrected, and
the answer counts it in `updated`. It is corrected only when the generator clearly owns it:

- it has not started, and would not have started by the new times;
- its station list is this station alone (a window shared by hand is left);
- nobody edited it since it was made (`updated_at` within 5 s of `created_at`). A corrected
  row gets both stamps set to now, so a later rule change corrects it again.

Why: on 2026-11-19 Jerusalem's candle lighting went from 18 to 40 minutes, but regenerate
skipped any same-named window within an hour, so 58 stored windows kept the start 22 minutes late.

A row changed straight in the database (Supabase REST) does not move `updated_at`, so it still
counts as the generator's. An edit on the Holidays page does move it and is kept.

SQLite (tests) hands stored times back as the clock time written, with no zone; `_apart`
compares those with the computed clock time. Postgres gives the zone.

Tests: `backend/tests/test_holiday_regenerate_corrects.py`, `tests/test_year_plan.py` (the
computed windows for a year).

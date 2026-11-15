# RDS now-playing feed

`GET /api/v1/stations/{station_id}/rds.txt` (`backend/app/api/v1/rds.py`) is a public, one-line, plain-text feed for the FM RDS encoder software (the station runs Magic RDS beside its automation). It returns what is on air right now:

- music and programs: `Artist - Title`
- a sponsorship (asset_type `spot`): the asset title only, which is the sponsor's line
- jingles, station IDs, time, weather, or nothing playing: the station's name

Trimmed to 64 characters (RDS RadioText limit; `?max=` overrides), single line, typographic punctuation folded to plain ASCII, `Cache-Control: no-store`.

Source of truth is the `queue_entries` row with status `playing`, the same one the control room shows. It is polled, not pushed; if the playout engine later reports real track starts, feed that instead so the display changes with the audio.

# RDS: what FM radios show

The station's RDS (Programme Service name, 8 characters, and RadioText, 64 characters) is
run from the staff page **/admin/rds** (`frontend/src/pages/admin/Rds.tsx`), stored in
`rds_settings`, `rds_messages`, `rds_plays` (`backend/app/models/rds.py`), worked out by
`backend/app/services/rds_service.py` and served by `backend/app/api/v1/rds.py`.

## Feeds (public, plain, `Cache-Control: no-store`)

- `GET /api/v1/stations/{id}/rds.txt`: the RadioText line now (scheduled rotation). `?mode=now_playing` is the old song-only feed (PR 34). `?max=` trims shorter.
- `GET /api/v1/stations/{id}/rds-ps.txt`: the PS now (fixed unless the station's PS mode is "rotate").
- `GET /api/v1/stations/{id}/rds.json`: `{ps, rt, kind, message_id, sponsor_id, sponsor_name, changes_at}`.

`rds.txt` and `rds.json` fetches are the play log: one `rds_plays` row per run of the
same line, repeated polls only move `last_seen_at` (written at most every 30 s per station,
a 3 min gap starts a new row). Nothing is logged until an encoder (or someone) polls, so
sponsor RDS counts are what the encoder really picked up.

## Rotation

Stateless: slot = epoch seconds // `rotation_seconds` (default 15). Items: "now playing"
(weight `now_playing_weight`, default 2, 0 = off) and each message running now (days 0=Mon..6=Sun,
station-local time window that may pass midnight, date range, active), each with a `weight`.
Smooth weighted round robin spreads turns. Lines with nothing to show (a `{weather}` line while
weather is down, the song turn during a jingle or a Hebrew-only title) are left out of the round,
so the next line is not doubled. Empty round: `fallback_text`, else the station name.

While a spot (`asset_type` spot) is on air and `sponsor_while_spot_airs` is on, the sponsor's
own RDS line (an active message with that `sponsor_id`, its time window ignored) or else the
spot's title takes the line for the whole spot.
An item counts as a sponsor's spot when its type is spot, or when a switched-on contract (SpotSchedule) airs it on
this station (`contract_sponsor_of`): a booked library recording filed under another type still shows the
contract's sponsor line.

Sponsor side: a sponsor's lines are the same `RdsMessage` rows (kind sponsor, `sponsor_id`). The client page's
Radio text (RDS) tab adds, changes, switches off and removes them; "RDS set" = the sponsor has a switched-on line
(`components/sponsors/SponsorRds.tsx`, also the client list column and the booking window's last step).

Placeholders: `{time}` "3:05 PM", `{date}`, `{day}`, `{temp}` "48F", `{weather}`, `{station}`.

## Characters and limits

RDS radios show Latin letters only. `rds_safe` folds typographic punctuation and accents to
ASCII and drops everything else, Hebrew included; a separator left dangling is removed
("Artist - <Hebrew>" -> "Artist"). An asset's `metadata_extra.rds_text` overrides its song line
(transliteration). RadioText is cut at 64 at a word break; PS at 8. Saving a line refuses
over-64 static text, text with nothing showable, PS over 8 or with non-Latin characters; lines
with placeholders only warn. Sponsor lines run the sponsorship keyword check (`basic_review`).

## Magic RDS (the encoder program beside KBR Studio at BMG)

Pira.cz Magic RDS 4 reads external text from a local file, an HTTP(S)/FTP URL ("Internet
Website" text source), a window title or its internal channel; no raw TCP/UDP input
(https://www.pira.cz/rds/extsrc.pdf). Setup: Text Source "Internet Website" = the rds.txt URL,
Coding ANSI, Process "First line", Keep in cache ~10 s, Output purpose Radiotext. Vendor advice:
keep PS static (dynamic PS is deprecated and restricted in some countries).
Not yet done (2026-11-18): nobody has pointed Magic RDS at the feed; whether the studio PC
has internet is unknown. Without internet the local FM box would write the line to a file.
Magic RDS 3 (older) is not documented here.

The engine's Icecast stream title (ICY) still carries the track title from the Liquidsoap
annotations, not the RDS rotation.

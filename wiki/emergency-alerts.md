# Emergency alerts (EAS override)

Shmuel 2026-11-18: national emergency alerts must air whenever they are issued, set up
the way other stations do it. Code: `infra/streaming/radio.liq` (alert lane),
`backend/app/services/emergency_alerts.py`, `backend/app/services/eas_tones.py`,
`backend/app/api/v1/emergency.py`, `POST /playout/{id}/alert`, page
`frontend/src/pages/admin/EmergencyAlerts.tsx`, banner `components/layout/EmergencyBanner.tsx`.

## How stations really do it (and what is ours)
- Nobody from the government connects to a station's computers or API. The station owns
  an FCC-certified EAS box (47 CFR 11.11: an LPFM needs a decoder; encoder optional)
  that monitors the two sources the NJ state plan assigns plus FEMA's IPAWS CAP feed,
  and interrupts the air itself. That box, its log and ETRS filings are the legal part.
- Automation's part: take the alert in cleanly, put it above everything, resume, log.
  Pro systems do it by (a) the box's audio + relay into the air chain, (b) the box
  sending the alert's audio file over the network to the automation.
- Ours does both: `POST /emergency/device/activate` (token) and, on a local FM box, the
  EAS box's audio output wired into a line input (`KBR_ALERT_ALSA_DEVICE`).

## Legal guard (47 CFR 11.45)
Airing EAS header codes, EOM bursts or the 853+960 Hz attention signal outside a real
alert or authorised test is a violation, on air and on streams. `eas_tones.find` checks
every upload (attention: both tones ~1 s; SAME: >85% of sound in 1.45-2.2 kHz for 0.3 s).
Staff uploads with tones get 422; only the EAS box's token may send them (real relays,
RMTs). Test the override with a spoken clip only. Shmuel's recording of the 2026-11-17
national test (NPT) holds real tones: harness only (`KBR_HARNESS_EAS_RECORDING`), never
a live station.

## Engine (proto 6)
- `alert_lane` = `request.queue("alert")` (+ line gate) in a fallback ABOVE the whole
  programme, placed after the master level and mix-over (Next fades, jingles never touch
  it) and before compressor/limiter. Below it everything pauses and resumes.
- `/alert {uri,id}` queues (two alerts play in turn), `/alert_stop` drops all,
  `/alert_line {on}` forces the line. `/status` has alert, alert_id, alert_source.
- Reports `POST /playout/{id}/alert {phase, alert_id, source, at, started_at}`.
- Line: `blank.detect` (threshold -40 dB, 0.3 s noise, release `KBR_ALERT_LINE_RELEASE`
  4 s) kept running by `output.dummy`; `buffer` into the main clock.
- After the alert, a hard group that came due during it is dropped (engine
  `alert_drop_stale_timed`, backend `close_timed_lost_to_live_show`): no late time check.
  The engine takes the group back through `timed_dropped` (skipped the moment it would
  start), never with `timed_queue.skip()`: the timed lane is not being played during the
  alert, and a skip then broke the NEXT group (it aired with no start report and only its
  first item played; harness `emergency_alert_over_time_check` then `three_item_timed_group`).

## Backend
- `EmergencyAlert` row per station per alert (kind alert/test/rmt/rwt, source
  device/staff/line, status sent/on_air/done/failed/cancelled, seconds_on_air, has_tones).
- Engine stations: `/alert` command (wait 8 s, slow_is_ok); status on_air on the report.
  Browser-player stations: WS `emergency_alert` → `useAudioEngine` mutes the bus and plays
  it to the speakers (programme runs on muted underneath); `emergency_alert_stop`.
- Each start/end/failure: Alert row (critical for real alerts) + ntfy (urgent).
- Audio stored at `emergency/<uuid>.ext` (Supabase public URL in production;
  `GET /emergency/audio/{name}` locally with `BACKEND_PUBLIC_URL`).
- Local FM box: relay passes `/alert` on, keeps reports in `alert-spool.jsonl` offline,
  sends them first in `catch_up`. `setup-box.sh` keeps `KBR_ALERT_ALSA_DEVICE`.

## Tests
`backend/tests/test_emergency_alerts.py`; harness `emergency_alert_over_music`,
`_over_time_check`, `_over_live_show`, `_stop`, `_two_in_a_row`, `_tones`,
`_line` (PulseAudio: `pactl load-module module-null-sink sink_name=alertin`, engine
`KBR_HARNESS_ALERT_DEVICE=pulse` with `PULSE_SOURCE=alertin.monitor`; with `--local-box`
the line is cut both ways during the alert).

## Staff screens follow at once (2026-11-18 live test)

The first live test on KBR Jerusalem played whole (on air 1.9 s after Send) and Stop took it off in under 0.1 s. But the banner showed 6 s after Send, and the page's Stop card only after 7 to 9 s, because screens waited for the engine's start report and then for their next poll. Now:
- an engine station counts as on air the moment the alert is sent, before the engine answers (its answer waits for the file to load: the second live test still showed the banner at +5 s when it waited for that). Stop pressed before the row is saved goes through `stop_unsaved`, and the send tells the engine to stop once more after it answers. An `until` ends it if no report comes
- every change (sent, started, ended, stopped) sends `{"type":"emergency"}` over `/ws/site`; `lib/appUpdate.ts` turns it into the `kbr-emergency-changed` window event, and the banner and page refetch.

Alerts titles carry the alert's short id (`#1a2b3c`), so each alert gets its own row; with one shared title the second test's ON AIR and ended folded into the first's open alerts (alerts fold repeats). The log's length field is `seconds_on_air`.

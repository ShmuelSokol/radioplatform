# Moving Kol B'ramah Main onto the stream server: test list

Owner: the "Review and test every station change" thread. Written 2026-11-17 after Shmuel asked
for Main to be the main station on the server. Row ids in brackets point at `docs/test-checklist.md`.

Main was on the engine once (2026-11-16) and was rolled back at 02:59Z on 2026-11-17: a song was
handed out twice, the station stalled, and the 9 PM time check aired at 9:09 behind a Daf Yomi
shiur. Every one of those faults has a test below. Main does not move until section A is all green.

Rule for every line: PASS needs a recording or a log line, not "looked fine".
Where: **H** = harness (`infra/streaming/harness/run_harness.py`, real Liquidsoap + Icecast + backend),
**T** = backend/frontend automated tests in CI, **J** = live on KBR Jerusalem (already on the engine),
**M** = live on Main after the move, **D** = on the droplet itself.

## A. Before the move (gates)

### A1. Time-critical
| # | Check | Where | Pass means |
|---|---|---|---|
| A1-1 | Hard time check during a song | H `song_spans_hour`; J two top-of-hours | starts within 1 s of hh:00:00, song fades, no silence over 1 s [S-9, F-PLAYER-12] |
| A1-2 | The 9:09 PM case: a Daf Yomi shiur inside a block runs past the hour; pause, wait and cut rules | H `block_shiur_spans_hour`, `_wait`, `_cut`, `block_with_slow_shiur`, `block_with_very_slow_shiur` | pause: time on the second, shiur resumes where it stopped; wait: time right after the shiur; cut: time on the second, nothing resumes [F-ENGINE2-6/7/8, V1-10c] |
| A1-3 | The same with the shiur queued as a song | H `queued_shiur_spans_hour`, `_wait`, `_cut` | as A1-2 |
| A1-4 | A1-2 live with Main's real 9 PM Daf Yomi file and Main's rule (`timed_during_long_items`) copied to Jerusalem for one evening | J (PC records 20:58 to 21:12 local) | time check at 21:00:00 within 1 s; shiur carries on; nothing aired twice |
| A1-5 | Soft items at :15/:30/:45 wait for the song; pause a long shiur when over 5 min are left | H `soft_timed_waits_for_song`, `soft_pauses_long_shiur`, `soft_waits_for_short_shiur` | [B-JOB-35, V1-34] |
| A1-6 | Timed groups: 3 items, two groups 0.3 s and 1 s apart, duplicate id, slow file | H `three_item_timed_group`, `two_groups_*`, `duplicate_timed_id`, `slow_timed_file` | every item once, in order, at full level [H-10, F-ENGINE2-9] |
| A1-7 | Songs fitted before the hour (PR 221) | H `fit_before_hour`; J two top-of-hours | song on air at the hour loses 15 s or less where the queue allows [F-BLOCKS-8, -8b] |
| A1-8 | Engine or backend restart inside the 45 s before the hour | H `engine_crash_restart`; T | time check still airs once, on the second [V1-30, V1-31] (V1-30/31 at the hour itself: NOT COVERED, to add) |
| A1-9 | Droplet clock | D `timedatectl` + `chronyc tracking` | synchronised, offset under 100 ms [V2-122] |

### A2. Operator actions (control room, stream-server station)
| # | Check | Where | Pass means |
|---|---|---|---|
| A2-1 | Next, Fade, Soft fade, Play now, Panic | H `rapid_next`, `control_endpoints`; J by hand | heard within 1.5 s, no silence [S-2, F-MODES-10] |
| A2-2 | Next twice quickly | H `next_twice` | every press changes the item. **OPEN BUG**: a second Next within about 3 s is lost [V1-12c] |
| A2-3 | Next after 35 s idle and right after an engine restart | H `next_after_idle`, `engine_crash_restart` | [V1-12b] |
| A2-4 | Insert, reorder, delete, Loop, Mix over, instant buttons | H `queue_change_drops_preloaded`, `timed_and_mix_over_levels`; T `test_playout_engine.py`; J by hand | the stream follows the queue; the pre-loaded song is dropped when the head changes [B-JOB-34, B-QUEUE-17/31/37, V1-40] |
| A2-5 | Timed item on air: Next is refused, Panic cuts it | T; J | [V1-11] |
| A2-6 | Resume position, cue points, item level | H `resume_position`, `cue_points`, `track_level`, `song_with_level_muted_under_time_check` | |
| A2-7 | Control room on iPhone size against a stream-server station | T `e2e/control-room-actions.mjs::engine`; J on a real phone | buttons work, meters move and fall to zero on Stop [F-CR-TRANSPORT-25b open] |

### A3. Never silent, never twice
| # | Check | Where | Pass means |
|---|---|---|---|
| A3-1 | Empty queue | H `empty_queue` | emergency music within seconds, alert raised, back to the queue when it fills [V1-18, V1-33] |
| A3-2 | Backend down, slow, or started after the engine | H `backend_unreachable`, `slow_started`, `engine_starts_backend_down` | no dead air; state reconciled after [V1-13/14, B-MODE-26] |
| A3-3 | Same song never handed out twice (Shema, 02:49Z) | T `test_playout_engine.py` (F-ENGINE2-1/2/3) | with about 700 pending rows the start report takes under 300 ms |
| A3-4 | Schedule redraw while a song is with the engine | T (B-MODE-21, B-PLAYOUT-14) | the site still shows it on air |
| A3-5 | Engine did not move on at 0:00 | T (F-ENGINE2-4); J watchdog | alert within 30 s [H-9] |
| A3-6 | Main's emergency folder is not empty at the moment of the move | D `ls /var/lib/kbr-engine/<main id>/emergency | wc -l` | 10 or more files before the switch (it only fills itself from songs Main's engine has aired, so copy Jerusalem's or pre-seed) [F-ENGINE2-11] |
| A3-7 | 12 hours on Jerusalem with today's code | J watchdog + PC 12 h watch | every top of hour within 1 s, zero silence over 2 s, zero "catchup" lines, nothing aired twice |

### A4. Sound
| # | Check | Where | Pass means |
|---|---|---|---|
| A4-1 | Level and true peak | H `output_limiter`, `track_level`, `icecast_output`; J (done 20:47Z: 16.3 to 19.1 LUFS, peaks -2.8 dBFS or lower) | [V1-6, V1-4d/e] |
| A4-2 | Every item handed over with a measured level | T `test_item_gets_its_level_before_it_is_handed_over`; J the Carlebach story re-measured (open) | |
| A4-3 | Top 500 Hits: the 500 files Main plays 6 AM to 10 PM all have a real loudness and a length | D/PC query on production | 0 without; else analyse them before the move, a few at a time |

### A5. Schedule, sponsors, Shabbos
| # | Check | Where | Pass means |
|---|---|---|---|
| A5-1 | Blackout starts now, ends early, ends on time | H `blackout_ended_early`; T `test_blackout_starting_now_moves_the_engine_on`; J a 5-minute test window | music stops within 3 s, the stream stays connected and silent, music returns within 5 s of the end |
| A5-2 | A real Shabbos on the engine before Main meets one | J Friday 2026-11-20 (Jerusalem's candle lighting is 7 hours before Lakewood's) | recording across the start; if Main moves before Friday this runs as a canary the same day |
| A5-3 | Program blocks, weather, sponsorships in their dayparts | T; J with the 20 TEST sponsorships (sponsorship thread) | each airs once in its slot [B-JOB-29/32, B-BLOCK-ANN] |
| A5-4 | Playlist take-over at a block boundary (Top 500 starts 6 AM, ends 10 PM) | T `schedule_refresh`; J any block boundary | the new playlist starts within one song of the boundary, the pre-loaded song is replaced once, not repeatedly [F-ENGINE2-5] |
| A5-5 | Song requests matched while the engine holds the next track | T; J | plays after the held track [F-MATCH-18] |

### A6. Listeners
| # | Check | Where | Pass means |
|---|---|---|---|
| A6-1 | Desktop Chrome, Safari, Firefox; Android | T `e2e/listen-page.mjs::stream` (WebKit too); J | sound within 2 s; a broken stream falls back to the built-in player [F-PLAYER-21/23] |
| A6-2 | iPhone Safari (HLS copy) | J on a real iPhone: needs Shmuel's phone or the Mac | sound within 2 s, never silent 10 s, lock screen controls [S-11, F-ENGINE2-13 to 21]. **Not testable in the cloud** |
| A6-3 | Live show on a stream-server station: host heard once, music silent during, back after; web call-in | J with the PC as host | [F-LISTEN-42] **NOT COVERED today** |
| A6-4 | Site shows what is heard | J watchdog `engine_drift` | within 20 s, normally 1 s [W-4, F-AIRWATCH-10, V1-147] |
| A6-5 | Listener counts include iPhone listeners | J | [F-ENGINE2-27] open |

### A7. Server capacity (Main and Jerusalem together)
| # | Check | Where | Pass means |
|---|---|---|---|
| A7-1 | Two engines for one hour on the droplet (1 vCPU, 961 MB, no swap), Main's engine playing but not yet routed | D `journalctl -u 'kbr-engine@*' | grep -c catchup`, `top`, `free -m` | 0 catch-up lines, CPU under 60%, 200 MB free. One engine measured 20% and 207 MB at 21:20Z |
| A7-2 | Both engines cross the top of the hour together | D + recordings of both streams | both time checks within 1 s |
| A7-3 | setup.sh rerun with nothing changed | D | no engine restarts [V1-21b] |
| A7-4 | 50 listeners on Main's stream | PC or cloud: 50 `curl` pulls for 10 minutes | no catch-up lines; bandwidth as expected [V1-27] |
| A7-5 | Disk | D `df -h` | emergency copies and HLS segments stay bounded [F-ENGINE2-23/25] |

## B. The move itself (quiet hour, rollback ready)
1. Engine thread: Main's id added to `PLAYOUT_ENGINE_STATION_ID`; setup.sh run with both ids; A7-1 passes with Main's engine idle-playing.
2. Pick a minute at least 20 minutes from any timed item, outside 6 to 9 AM and outside the 9 PM shiur.
3. Start recordings of Main (stream and a browser tab) before the switch.
4. Switch. Rollback is `PLAYOUT_ENGINE_ENABLED=false` and `PLAYOUT_STREAM_LIVE=false` together (or Main's id removed), then check the browser player took over.
5. Roll back at once if any of: silence over 5 s; a timed item over 5 s late; an item aired twice; site and stream differ for 60 s; catch-up lines on either engine.

## C. After the move (on Main, first 24 hours)
| # | When | Check | Pass means |
|---|---|---|---|
| C-1 | first 2 minutes | stream plays; site shows the same item; open tabs moved to the stream without a reload | no silence over 1 s at the switch |
| C-2 | first 10 minutes | two natural song changes | crossfade, start reports in the backend log, play log rows right |
| C-3 | first top of hour | time check | within 1 s of hh:00:00; the song before it lost 15 s or less |
| C-4 | first :15/:30/:45 | soft item | airs after its song |
| C-5 | first sponsorship and weather | airs once, logged for the sponsor report | |
| C-6 | 9 PM | Daf Yomi and the 9 PM time check (the 9:09 case) | time check at 21:00:00 within 1 s; the shiur follows Main's rule |
| C-7 | 10 PM and 6 AM | playlist boundary | Top 500 stops and starts on time |
| C-8 | every hour for 24 h | watchdog: top of hour, dead air, drift, skipped or repeated items, catch-up lines; Jerusalem unaffected | all zero; ntfy on any miss |
| C-9 | once, by hand | Next, Fade, Play now, insert, Loop, Mix over, Panic from the control room, desktop and phone size | each heard within 1.5 s |
| C-10 | once | iPhone and a car or Bluetooth speaker | sound within 2 s, stays on for 30 minutes |
| C-11 | Friday | Shabbos blackout start and Motzei Shabbos return | silent on time, back on time, first item right |
| C-12 | after 24 h | compare the play log with the planned queue | nothing skipped, nothing twice, every timed item within 1 s |

## Open blockers (2026-11-17 22:30Z)
1. A second Next within 3 s is lost (A2-2). Engine thread.
2. Two engines on the 1-core droplet are unproven since PR 209/214 (A7-1, A7-2). If they do not pass, the droplet needs 2 vCPU / 2 GB, which costs money and needs Shmuel.
3. Live show on a stream-server station has never been tested (A6-3). Main is the station that carries live shows.
4. iPhone has only been checked in desktop WebKit (A6-2).
5. Main's emergency folder starts empty (A3-6).
6. How the FM transmitter is fed must be known before the move: if it plays the website's Main in a browser it will switch to the stream by itself and run a few seconds behind the clock; if it is fed another way nothing changes for it. Unknown today.
7. No Shabbos has yet passed on the engine (A5-2).

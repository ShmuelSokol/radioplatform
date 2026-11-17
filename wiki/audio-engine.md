# Client-Side Audio Engine

## Location

`frontend/src/hooks/useAudioEngine.ts`

## How it works

Dual-deck Web Audio system (A/B decks) with crossfade support. Used as fallback when Icecast stream is unavailable.

## Key behaviors

- **Pre-loads next track** on inactive deck when `nextAssetId` is known
- **Crossfade triggers** at `cross_start` point (from audio analysis) OR 3s before track end if no analysis exists
- **Instant deck swap** if inactive deck already has the track buffered (`readyState >= 2`)
- **Replay gain** applied per-track from audio analysis metadata
- **Periodic sync** every 10s corrects drift > 5s vs server elapsed time
- **Track change cooldown** 15s after track change before sync kicks in

## Track-change rules (2026-11-15)

- Each deck tracks which asset it holds (`deckAssetRef`). A track change picks the deck by asset id, never by comparing URLs.
- A new track is positioned from `currentStartedAt` (server `started_at`), NOT `elapsedSeconds`: on the render where the track changes, the page's elapsed state still belongs to the previous track. Before this, a skipped-to song started at the old song's elapsed time, and after every natural crossfade the server's late announcement re-loaded the already-playing song and jumped it.
- If the active deck already plays the announced asset (client crossfaded first), adopt it. Never re-set `audio.src`; that restarts the element.
- Skip / play-now = 0.6s fade-out + 0.15s fade-in, not a hard cut. On a cold load the old track stays on air until `canplay` (max 4s).
- The pre-loader must not touch a deck that is crossfading, fading out, or holding the on-air / about-to-air asset. It re-runs on `swapTick`.
- Crossfade trigger runs on `setInterval(100ms)`, not `requestAnimationFrame` (rAF stops in background tabs).

## Server and player change track at the same moment (2026-11-15)

- The precise timer (`scheduler_engine._precise_advance`) advances the queue at `cross_start` (3s before the end by default) via `_check_advance(..., early_seconds=)`. Before, it fired early but `_check_advance` only advanced at full duration, so the 3s poll did it up to 3s late.
- Why it matters: the player crossfades at `cross_start`. With the server ~5s later per song and the player no longer re-loading on each announcement, the player ran further ahead every song, then went silent and repeated a short item.
- The client crossfade check only runs when the active deck holds the track the server says is on air (`deckAssetRef[active] === lastAssetId`). While a Next is pending (new now-playing received, new audio not up yet) `crossStartRef` already belongs to the NEW track; applying it to the old deck started a bogus 3s crossfade whose timer later flipped the decks back (seen in Safari, Skip at 29s of a 40s track).
- One track change at a time per station: `advance_lock(station_id)` in `api/v1/queue.py` wraps `_check_advance` and Next (`skip_current`), and the playing entry is re-read with `populate_existing`. The poll, the precise timer and Next each have their own DB session; once the early advance was real (not a no-op) two of them could promote two different pending entries at one song change, so about 1 item in 8 was marked playing and never heard. The poll also no longer re-arms the timer once `remaining <= cross`. The lock is per process (Railway runs one).
- A song change is keyed on the play, not only the asset: same asset id with a new server `started_at` is a new play (the same item queued twice in a row). The player then checks whether a deck is already playing that play (it crossfaded into a pre-loaded copy); if not, it brings the asset up again. The pre-loader loads a second copy of the current asset on the idle deck when next == current. Before this the second play was silent.
- `goLive` starts from the top when the live position would be past the end of the new track: that means the `started_at` it holds still belongs to the previous track (asset and start time can arrive in separate updates).
- Browser test for all of this: `cd frontend && node e2e/audio-engine.mjs` (real hook, test tones, no backend; about 90 s; not in CI).
- Player guards: it will not start its own crossfade more than 1s before the server reaches `cross_start` (`MAX_LEAD_SECONDS`), and an announcement that arrives within 4s of the natural end gets the full 3s crossfade instead of the quick skip fade.

## Known limitations

- Each listener plays independently — no cross-listener sync (that's what Icecast solves)
- Cold loads (no pre-loaded track) still cause a brief gap while fetching + decoding
- `audio.duration` must be finite for auto-crossfade fallback to work

## Gap fixes (2026-05-24)

1. Auto-crossfade 3s before end when no `cross_start` analysis exists
2. Use pre-loaded inactive deck instantly instead of re-fetching

See also: [streaming-architecture](streaming-architecture.md)

## Next during an automatic change (2026-11-15)

The server advances at the crossfade point, so for about 3 s the "playing" entry is a song that
is still fading in. A Next in that window used to skip it: listeners heard a blip of it, a gap,
then a third song. `skip_current` now answers the first Next within
`NEXT_KEEPS_INCOMING_SECONDS` (4 s) of an automatic change with the incoming song unchanged
(`kept_incoming: true`); a second press is a normal Next. The marker is in memory
(`_automatic_change` in `api/v1/queue.py`) and is only set by the automatic advance, so quick
repeated presses of Next still each skip a song.

## Clean item starts (2026-11-16)

A change heard less than 1 s into the new item starts it from the top with no seek
(`START_FROM_TOP_SECONDS`), and `seekIfNeeded` skips seeks to where the deck already is.
Safari lands MP3 seeks on an earlier frame, so the first fraction of a second was heard twice.
Checked by `e2e/audio-engine.mjs` scenarios `next-starts-clean` and `next-starts-clean-cold`
(the harness records `seeking` events). Not yet checked in real Safari.

## Listen mid-item (Safari, 2026-11-16)

Safari refuses `play()` that a click did not start, and leaves `AudioContext.resume()`
unsettled until a click. The control room auto-inits the player on load (no click), so the deck
holding the item on air was loaded and refused; Listen then gave no sound until the next item.
`initAudio` now builds the graph before awaiting `resume()` (a second call can no longer
replace the decks with empty ones) and, still inside the click, starts the on-air deck at its
live position and starts-then-pauses the idle deck so it may play later. If the deck is still
paused after the click, `listenTick` brings the item on air up again. Checked by
`e2e/audio-engine.mjs listen-mid-item`, which acts out Safari's rules in Chromium.

## Hard timed items are loaded ahead (2026-11-16)

The idle deck normally holds the next track. When a hard timed item (program block, hard
sponsorship) is due before the song on air ends, it is loaded on the idle deck 60 s ahead
(`TIMED_PRELOAD_AHEAD_MS`) instead, so the change at its time is instant. A cold load there
left about half a second of silence in the control room. Scenario
`hard-timed-item-has-no-hole` in `e2e/audio-engine.mjs`.

## Control room Next is heard at once (2026-11-16)

Next took 0.5 to 0.8 s from click to sound in the live control room, nearly all of it the server
round trip. `useAudioEngine.playNextNow(asset, fade)` brings the item the server will move to
(`next_entry_id` from `GET /queue`) up locally on the click; `Dashboard.handleNext` calls it
before `skip`. The player follows that local item until the server announces a change made after
the click (the same item: kept, no restart, since the start times are within 1 s; another item:
followed), or for at most 4 s. Not done within 4.5 s of an automatic change, when the server
keeps the incoming item. Listeners still change when the server announces it. Scenarios
`next-heard-before-the-server-answers` and `next-ahead-then-server-differs`.

## Quiet gaps at song changes (2026-11-16)

The Mac's listener heard 2 to 3 s of near silence at song changes. Causes on our side:
- `silence_service.get_audio_duration` read "Duration:" from ffmpeg on a pipe, which is
  "N/A" for MP3, so `analyze_audio_bytes` fell back to 180 s for every item: cue_out 180,
  cross_start 177, and `asset.duration` overwritten with 180. It now takes the decoder's
  last `time=` and analysis refuses to guess a length. Analyses carry `version`
  (`ANALYSIS_VERSION = 2` in `models/asset.py`); `stored_analysis()` keeps only the start
  cue and loudness from older ones, and every reader of `audio_analysis` goes through it.
- Most of the library has no analysis, so the hand-over came 3 s before the stored end,
  over the song's quiet tail. `services/upcoming_analysis.py` analyses the item on air
  and the next one in the background (one at a time) when the analysis is missing or
  old. Re-analysis reads only parts of the file (`services/tail_analysis.py`): the MP3
  header (length from it and the file size), the last 20 s, and the first 8 s only when
  no start cue is stored; stored loudness is kept. About 0.5 MB a song instead of the
  whole file: downloading all ~10,600 items would have been ~100 GB of storage egress.
  `POST /assets/bulk-analyze?asset_types=music&limit=N` uses the same path (music only by
  default). Only the upload path and the single-item `analyze-audio` route read whole files.
- The player faded the incoming song in over 3 s. Now it comes up in 0.4 s
  (`CROSSFADE_FADE_IN`); only the outgoing song fades over 3 s.
- The player hands over at `handOverPoint()`: the crossfade point, but never later than
  3 s before the end of the file it actually loaded. The periodic sync no longer applies
  the server's elapsed time to a deck that holds a different item.
Checks: `backend/tests/test_song_change_gaps.py`, `e2e/audio-engine.mjs stored-length-longer-than-file`.

## Timed item due right after a song change (2026-11-16)

`_pick_next_pending` starts a timed item (hard or soft) due within
`TIMED_EARLY_START_SECONDS` (8 s) of a song change at that change, instead of waiting
for the clock or starting a song that would be cut seconds later. Live: a block at
08:35:00 waited 3.3 s after the song before it ended. Test:
`backend/tests/test_timed_item_starts_early.py`.

## Output is a stereo bus (2026-11-16)
Both decks (and mix-over jingles) go into one GainNode bus fixed at 2 channels ('speakers' interpretation), then to the speakers and through a ChannelSplitter to the left/right meters. It used to be a ChannelMerger: each merger input is mono, so the whole station came out of the left speaker only. Checked by `e2e/audio-engine.mjs stereo` (stereo and mono files must reach both meters).

## Station switch (2026-11-16)
`useNowPlayingWS` bumps a generation on every station change; the old socket, its close handler, poll and reconnect timer check it and stop. Before, the old socket's close handler reconnected to the old station a second after a switch, so the control room got both stations' now-playing and played both. Checked by `e2e/control-room.mjs` (station switch) and `e2e/audio-engine.mjs switch-station*`.

## One hand-over rule (2026-11-17)

`handover_seconds(entry)` in `api/v1/queue.py` is the only rule for when the item on air
hands over: the scheduler's timer (`_advance_queue`), the now-playing `cross_start` and
`switch_time` all use it. Short items (under 20 s) and timed items (preempt_at, ad_slot,
block) hand over 0.3 s before their end even when their analysis has a crossfade point;
songs use their analysed `cross_start_seconds` (default 3 s before the end). The page
(`useAudioEngine`) also hands a short file over at its end, and when the server moves on
while a short item that started late on the page is still talking, it lets it finish
(`letFinish`, up to 8 s) unless the operator pressed Next. Before, a 5 s time check was
cut about 1.8 s early and the resumed story was heard over it (All Day Stories, 10 PM
2026-11-16). Tests: `backend/tests/test_handover_rule.py`, e2e `audio-engine.mjs`
scenarios `time-check-not-talked-over` and `time-check-hands-over-at-its-end`.

Since 2026-11-17 (All Day Stories 03:00Z: the paused story came in 2.9 s over the time
check): the page's own crossfade check never crossfades a short item; the next item
comes in 0.15 s before the file's end (also after `ended`, the check runs every 100 ms).
`letFinish` holds for any short item still talking when the server moves on. A short
item brought up up to 3 s late after an item change starts from its first word, not
joined mid-sentence. And a pre-loaded hard timed item (or a soft one at its `hard_by`)
starts on the page at its due time by itself (pre-arm, via `localNext` with a 0.3 s fade
of the item on air); the server's message confirms it a moment later. Pages were
1.1-1.8 s late waiting for that message. Listen gets the due time from
`next_asset.cut_at` in the now-playing message (control room: from the queue,
`nextCut`). Scenarios `story-resumes-after-time-check`,
`story-resumes-after-late-time-check`, `hard-time-check-starts-on-time-before-the-message`.
On the real Listen page: `e2e/listen-page.mjs` ONLY=handover (short item at its end and
when the server moves on 3 s early, time check 2 s and 3.6 s late, pre-armed time check
confirmed 1.5 s late or never: back to the song once the item ends, 4 s or more after).
The fake station sends `cut_at` like the server. `backend/tests/test_next_asset_cut_at.py`
covers the soft limit, a hard ad slot and the polling answer.

## Server clock, not the device clock (2026-11-17)
Every position and due time (`started_at`, `preempt_at`, `ends_at`, a push's `at`) is server time, so the player compares them with `serverNow()` from `src/lib/serverClock.ts`, never `Date.now()`. The offset is measured from `server_time` in the release policy (`lib/appUpdate.ts`, on connect and every minute). Before, a PC 20 s fast joined every song 20 s in and faded the song for the hourly time check 20 s early. Used by useAudioEngine (live position, timed fades, crossfade, playNextNow), useNowPlayingWS (overdue check), Listen, Dashboard, `controlroom/timing.ts nextCut` and the transport clock. Checked by `e2e/listen-page.mjs` ONLY=clock (device clock 20 s fast and 30 s slow).

## Known: cue-in runs ahead of the server (2026-11-17)
The server counts an item from the top of the file (hand-over at `started_at + cross_start`; a resumed shiur moves `started_at` back). The player starts a fresh item at `cue_in` but a listener joining later at `max(cue_in, elapsed)`, so with a cue-in of N s the two are N s apart and the crossfade waits up to N - 1 s past the cross point (MAX_LEAD bound). Harmless for the usual sub-second leading silence. `e2e/audio-engine.mjs cue-gain` marks it XFAIL; fix would be server-side (start the clock at cue-in, e.g. `started_at = now - cue_in` in `mark_started`).

## Browser checks
`e2e/lib/fake-station.mjs` is a fake backend for page.route (queue, actions, live shows, CRM, now-playing socket, site socket, ranged tone files, `skewClock`) with an audio probe (`window.__decks()`, `__audible()`, `__outLevel()`, Media Session handlers). Suites on the built site (`npx vite preview --port 4311 --strictPort`): `control-room-actions.mjs` (every control room button, desktop + iPhone, S-1/S-2 timings), `listen-page.mjs` (Listen page, lock screen, network loss, socket back-off/polling, stream mode with `lib/stream-server.mjs`, time check on the second), `call-in.mjs`, `release-timing.mjs` (release pickup limits under a fake clock). Each takes `ONLY=<part>`.

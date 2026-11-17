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

## Polling fall-back (2026-11-17)
`useNowPlayingWS` reconnects after 1, 2, 4, 8 and 16 s, then polls `/now-playing` every 3 s. Polling carries now-playing only (no `mix_over`, and `change_fade_seconds` goes out once, on the socket), so while polling it tries the socket once a minute. Before, a visible tab stayed on polling until it was hidden and shown again. Checked on a fake clock by `e2e/listen-page.mjs` ONLY=polling.

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
`nextCut`). Live at 14:00Z the pre-arm still did not fire: the message pages hold is
the one sent when the item on air started, and a time check queued later (or during a
30 min story) was not in it, so nothing was pre-loaded. The scheduler now sends the
now-playing message again once a hard timed item is under a minute away
(`SchedulerEngine._announce_timed`, `TIMED_ANNOUNCE_AHEAD_SECONDS = 60`, once per due
time). The page records each decision in `window.__kbrTimedStart` and a console line
`[kbr] timed start` (started here / not loaded / sound off). Scenarios `story-resumes-after-time-check`,
`story-resumes-after-late-time-check`, `hard-time-check-starts-on-time-before-the-message`.
On the real Listen page: `e2e/listen-page.mjs` ONLY=handover (short item at its end and
when the server moves on 3 s early, time check 2 s and 3.6 s late, pre-armed time check
confirmed 1.5 s late or never: back to the song once the item ends, 4 s or more after).
The fake station sends `cut_at` like the server. `backend/tests/test_next_asset_cut_at.py`
covers the soft limit, a hard ad slot and the polling answer.

**Second item of a program block.** Every item of a block occurrence shares one
`preempt_at` (see `program-blocks.md`), so with the time check on air the Daf Yomi
announcement after it still carried hh:00 as its cut time. The page took that as "cut
in now": the preempt fade faded the time check, the pre-arm could start the
announcement over it, and the announcement was joined seconds in (Main, 15:00Z
2026-11-17, heard from "the Miami daf"). Now a cut time at or before the start of the
item on air (+1 s) is that item's own group: the server sends `cut_at: null`
(`_cut_time(next, on_air)`) and the page ignores such a cut itself
(`GROUP_CUT_SLACK_MS`). And whatever follows a short item this page played to its end
starts from the top if it comes in up to 8 s late (`AFTER_SHORT_FROM_TOP_SECONDS`, a
slow phone still fetching it); its tail is cut instead. Scenarios
`second-item-of-a-timed-group-from-the-top` and `...-slow-file` (harness `cut` field =
Listen's `next_asset.cut_at`); test `test_next_item_of_the_group_on_air_has_no_cut_time`.

**Start-up delay inside the element.** Live at 16:00Z 2026-11-17 the pre-arm called
play() on time, but the element only began sounding 1.0 s later (its `playing` event),
on two stations; the song had already faded at +0.4 s. A few seconds before a hard
item's time (`PREARM_WARM_AHEAD_MS = 3000`) the page now plays the pre-loaded item on
the idle deck silently until `playing`, then pauses it and seeks back to its cue-in, so
the start-up is done before the due time (`window.__kbrTimedWarm`, console
`[kbr] timed warm-up`). On any item change the item on air keeps playing until the new
one fires `playing` (at most `OUT_WAITS_FOR_IN_MAX_MS = 1500`); each start's real
latency is in `window.__kbrLastStart` (`[kbr] item sounding`). Also fixed: the silent
unlock play on the decks after Listen left the idle deck playing the pre-loaded item
silently when the pre-loader got there first. Rig: `coldStartMs` emulates a first
play that sounds late; scenario `hard-time-check-slow-starting-file-on-time` (1.1 s
late with 0.6 s silence without the warm-up). The emulation is a model of what was
seen live, not the cause itself; check `playingAfterMs` live.

Follow-ups (same day). The warm-up is retried every 250 ms from 3 s before the time,
so a deck that gets the item late (short notice, or an operator's Next took the warming
deck) is warmed too, if at least 1.2 s remain. The pre-fade before a hard item now
stops at 12 % of the song's level (`PREEMPT_FLOOR`) instead of silence; the item takes
it out once it sounds, or it goes out 1.5 s after the time if nothing comes. Live at
17:00Z the warm-up's `playing` fired after 1 ms and it was paused at once, yet the
time check still sounded at +1.05 s: a paused, pre-loaded element seems to need its
pipeline really started (Chrome suspends idle players). The warm-up now plays muted
until its position has moved 0.3 s (`WARM_PLAY_SECONDS`, at most 1.8 s), then pauses
and seeks back to the cue-in well before the time. Every deck media event is kept in
`window.__kbrDeckEvents` (last 80, with server time and position) and each start's
`playingAfterMs` in `window.__kbrStarts` (last 10). Known bug, recorded as XFAIL:
an operator Next during the warm-up leaves no deck to warm on in time (1.1 s late, no
silence); it needs a deck of its own.

## Server clock, not the device clock (2026-11-17)
Every position and due time (`started_at`, `preempt_at`, `ends_at`, a push's `at`) is server time, so the player compares them with `serverNow()` from `src/lib/serverClock.ts`, never `Date.now()`. The offset is measured from `server_time` in the release policy (`lib/appUpdate.ts`, on connect and every minute). Before, a PC 20 s fast joined every song 20 s in and faded the song for the hourly time check 20 s early. Used by useAudioEngine (live position, timed fades, crossfade, playNextNow), useNowPlayingWS (overdue check), Listen, Dashboard, `controlroom/timing.ts nextCut` and the transport clock. Checked by `e2e/listen-page.mjs` ONLY=clock (device clock 20 s fast and 30 s slow).

## Known: cue-in runs ahead of the server (2026-11-17)
The server counts an item from the top of the file (hand-over at `started_at + cross_start`; a resumed shiur moves `started_at` back). The player starts a fresh item at `cue_in` but a listener joining later at `max(cue_in, elapsed)`, so with a cue-in of N s the two are N s apart and the crossfade waits up to N - 1 s past the cross point (MAX_LEAD bound). Harmless for the usual sub-second leading silence. `e2e/audio-engine.mjs cue-gain` marks it XFAIL; fix would be server-side (start the clock at cue-in, e.g. `started_at = now - cue_in` in `mark_started`).

## Listen page: nothing on air, stream retry, resume (2026-11-17)
- Nothing on air is `{"type":"now_playing","data":null}` on the socket (on connect, and if the server ever announces a stop) and `null` from `GET /now-playing/{id}`. `useNowPlayingWS` clears the item on both (socket, polling, overdue and back-from-background re-reads). The server sends no message when the queue runs dry, so the page clears at the overdue re-read (ends_at + 6-10 s). The fake station sends null too; `legacyStopShape` keeps its old `{started_at:null}`.
- A server stream that fails falls back to the built-in player, but is tried again: in the Listen click, and on each new item (muted, beside the built-in player; once it plays the page switches back). Before, one engine or Icecast restart left the listener on the built-in player for the session.
- Release resume on a stream station counts the stream element really playing (`playing` event), not "Listen was pressed": Safari refuses a stream started outside a tap and now gets "Tap to keep listening".
- Listener profile: the registration PIN is shown above the panel until dismissed; auto-login forgets a saved PIN only on 401; the heart never sends a rating the listener did not pick (the API needs a rating with a favourite, so on an unrated song it waits for the first star); stars come from `/crm/my-ratings`; raffle errors show, 409 counts as entered.

## Level meters must not re-draw the page every frame (2026-11-17)
`useAudioEngine` keeps the meter levels in React state, so each change re-draws the whole page that calls the hook. It used to set them on every animation frame (60 a second) on the control room and on the Listen page, which shows no meter at all. Now the levels are reported in `METER_SEGMENTS` (30) steps and only when a segment changes, and the hook's last argument `meter` (Listen passes false) turns the reading off. Do not put per-frame values in this hook's state; draw them from a ref or a small component of their own.

## Browser checks
`e2e/lib/fake-station.mjs` is a fake backend for page.route (queue, actions, live shows, CRM, now-playing socket, site socket, ranged tone files, `skewClock`) with an audio probe (`window.__decks()`, `__audible()`, `__outLevel()`, Media Session handlers). Suites on the built site (`npx vite preview --port 4311 --strictPort`): `control-room-actions.mjs` (every control room button, desktop + iPhone, S-1/S-2 timings), `listen-page.mjs` (Listen page, lock screen, network loss, socket back-off/polling, stream mode with `lib/stream-server.mjs`, time check on the second), `listener-pages.mjs` (a11y basics of the public pages, global player bar), `call-in.mjs`, `release-timing.mjs` (release pickup limits under a fake clock). Each takes `ONLY=<part>`.

Real WebKit (Safari's engine): `e2e/webkit/listener-webkit.py` drives WebKitGTK's MiniBrowser through Selenium (`apt-get install webkit2gtk-driver xvfb gstreamer1.0-plugins-good gstreamer1.0-libav`, `pip install selenium`, `xvfb-run -a python3 e2e/webkit/listener-webkit.py` with the site on `vite preview --port 4183`). `e2e/webkit/fake-server.mjs` serves the same `makeStation()` answers over plain HTTP + WebSocket and proxies the site. WebKitGTK refuses sound without a tap like Safari (`--autoplay-policy=allow` turns that off), has MSE for MP3 and, under WebDriver, ignores `--enable-mediasource=false`, so `?kbr_no_mse=1` removes `window.MediaSource` to get the iPhone's path. No native HLS, so the iPhone's HLS branch is not reachable; narrowest window about 447 px. It does not stand in for iOS (audio session, lock screen, background, CarPlay): a real iPhone is still needed.

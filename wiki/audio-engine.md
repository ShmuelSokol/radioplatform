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

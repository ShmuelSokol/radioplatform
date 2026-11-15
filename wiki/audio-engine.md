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
- Player guards: it will not start its own crossfade more than 1s before the server reaches `cross_start` (`MAX_LEAD_SECONDS`), and an announcement that arrives within 4s of the natural end gets the full 3s crossfade instead of the quick skip fade.

## Known limitations

- Each listener plays independently — no cross-listener sync (that's what Icecast solves)
- Cold loads (no pre-loaded track) still cause a brief gap while fetching + decoding
- `audio.duration` must be finite for auto-crossfade fallback to work

## Gap fixes (2026-05-24)

1. Auto-crossfade 3s before end when no `cross_start` analysis exists
2. Use pre-loaded inactive deck instantly instead of re-fetching

See also: [streaming-architecture](streaming-architecture.md)

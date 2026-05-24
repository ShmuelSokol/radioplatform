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

## Known limitations

- Each listener plays independently — no cross-listener sync (that's what Icecast solves)
- Cold loads (no pre-loaded track) still cause a brief gap while fetching + decoding
- `audio.duration` must be finite for auto-crossfade fallback to work

## Gap fixes (2026-05-24)

1. Auto-crossfade 3s before end when no `cross_start` analysis exists
2. Use pre-loaded inactive deck instantly instead of re-fetching

See also: [streaming-architecture](streaming-architecture.md)

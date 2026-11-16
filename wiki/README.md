# Wiki index

One concept per file. Lazy-loaded — Claude greps `log.md` first, then reads only relevant pages.

## Pages

- [streaming-architecture](streaming-architecture.md) — The playout engine (Liquidsoap + Icecast): pull next / report started, flags, server setup, outage behaviour
- [bug-reports](bug-reports.md) — 🐞 Report a bug: what a report carries, private GitHub copy, hourly Claude triage, ntfy push
- [audio-engine](audio-engine.md) — Client-side dual-deck Web Audio engine, crossfade, pre-loading, gap fixes
- [hourly-slots](hourly-slots.md) — Station ID, time announcements, weather, ad slots at :00/:15/:30/:45
- [admin-page-layout](admin-page-layout.md) — Full-width rule for every admin page, the padding the dark pages depend on, phone tables, the layout audit script
- [control-room](control-room.md) — Operator screen layout, liveControl.ts seam, insert-by-entry-id, where instant buttons are stored
- [dashboard-timing](dashboard-timing.md) — Elapsed timer overflow fix, WS vs REST data sources
- [skip-latency](skip-latency.md) — Instant skip rule: now-playing changes must WS-broadcast
- [v2-ui](v2-ui.md) — V1/V2 UI toggle, V2 design system, dev-against-prod proxy
- [lint-workflow](lint-workflow.md) — Wiki housekeeping procedure
- [release-pickup](release-pickup.md) — open tabs reload themselves onto a new release, never while audio plays
- [playlist-generation](playlist-generation.md) — queue generated ahead from sequences of song types, artist distance, longest-rested first
- [rds-feed](rds-feed.md) — plain-text now-playing line for the FM RDS encoder (Magic RDS)
- [song-request-matching.md](song-request-matching.md) — how listener requests map to library assets; known nickname + recall bugs
- [spot-schedules](spot-schedules.md) — Sponsor spot scheduling by daypart, how clock slots get filled, legacy single-spot fallback, sponsor portal data sources
- [schedule-look-ahead](schedule-look-ahead.md) — Conflict look-ahead page, shared block resolver, tie-break order, blocks are station-local time (were UTC)
- [sponsorship-copy-review](sponsorship-copy-review.md) — LP station sponsorship (not advertising) wording rules as editable data, AI copy review, basic-check fallback
- [spot-recordings](spot-recordings.md) — Record Spots page: script, wording check, audio (Shprach stub makes placeholder tones), listen, approve into the library
- [program-blocks](program-blocks.md) — Named blocks ("Weather Sponsors"): one event that queues a run of items at clock times, hard or soft; replaces the built-in hourly time and weather
- [operator-actions](operator-actions.md) — Next with a fade choice, fire-now, insert at a point, intro length, hard/soft timed items (`timing_mode`)
- [multi-station-load.md](multi-station-load.md) — load test for many stations and listeners; what it found
- [station-permissions.md](station-permissions.md) — who may operate which station; the protected (FM) station

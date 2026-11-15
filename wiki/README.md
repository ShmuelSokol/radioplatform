# Wiki index

One concept per file. Lazy-loaded — Claude greps `log.md` first, then reads only relevant pages.

## Pages

- [streaming-architecture](streaming-architecture.md) — Liquidsoap + Icecast on VPS, TCP telnet, Icecast stream vs client-side fallback
- [audio-engine](audio-engine.md) — Client-side dual-deck Web Audio engine, crossfade, pre-loading, gap fixes
- [hourly-slots](hourly-slots.md) — Station ID, time announcements, weather, ad slots at :00/:15/:30/:45
- [control-room](control-room.md) — Operator screen layout, liveControl.ts seam, insert-by-entry-id, where instant buttons are stored
- [dashboard-timing](dashboard-timing.md) — Elapsed timer overflow fix, WS vs REST data sources
- [skip-latency](skip-latency.md) — Instant skip rule: now-playing changes must WS-broadcast
- [v2-ui](v2-ui.md) — V1/V2 UI toggle, V2 design system, dev-against-prod proxy
- [lint-workflow](lint-workflow.md) — Wiki housekeeping procedure
- [release-pickup](release-pickup.md) — open tabs reload themselves onto a new release, never while audio plays
- [song-request-matching.md](song-request-matching.md) — how listener requests map to library assets; known nickname + recall bugs
- [spot-schedules](spot-schedules.md) — Sponsor spot scheduling by daypart, how clock slots get filled, legacy single-spot fallback, sponsor portal data sources
- [schedule-look-ahead](schedule-look-ahead.md) — Conflict look-ahead page, shared block resolver, tie-break order, blocks are station-local time (were UTC)
- [sponsorship-copy-review](sponsorship-copy-review.md) — LP station sponsorship (not advertising) wording rules as editable data, AI copy review, basic-check fallback

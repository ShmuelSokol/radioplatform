# Wiki index

One concept per file. Lazy-loaded — Claude greps `log.md` first, then reads only relevant pages.

## Pages

- [streaming-architecture](streaming-architecture.md) — Liquidsoap + Icecast on VPS, TCP telnet, Icecast stream vs client-side fallback
- [audio-engine](audio-engine.md) — Client-side dual-deck Web Audio engine, crossfade, pre-loading, gap fixes
- [hourly-slots](hourly-slots.md) — Station ID, time announcements, weather, ad slots at :00/:15/:30/:45
- [dashboard-timing](dashboard-timing.md) — Elapsed timer overflow fix, WS vs REST data sources
- [skip-latency](skip-latency.md) — Instant skip rule: now-playing changes must WS-broadcast
- [v2-ui](v2-ui.md) — V1/V2 UI toggle, V2 design system, dev-against-prod proxy
- [lint-workflow](lint-workflow.md) — Wiki housekeeping procedure

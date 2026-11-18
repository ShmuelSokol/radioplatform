# Wiki index

One concept per file. Lazy-loaded — Claude greps `log.md` first, then reads only relevant pages.

## Pages

- [streaming-architecture](streaming-architecture.md) — The playout engine (Liquidsoap + Icecast): pull next / report started, flags, server setup, outage behaviour
- [bug-reports](bug-reports.md) — 🐞 Report a bug: what a report carries, private GitHub copy, hourly Claude triage, ntfy push
- [audio-engine](audio-engine.md) — Client-side dual-deck Web Audio engine, crossfade, pre-loading, gap fixes
- [hourly-slots](hourly-slots.md) — Station ID, time announcements, weather, ad slots at :00/:15/:30/:45
- [admin-page-layout](admin-page-layout.md) — Full-width rule for every admin page, the padding the dark pages depend on, phone tables, the layout audit script
- [qnet-site-theme](qnet-site-theme.md) — The KBR Studio look on every admin and sponsor page: one shared skin (`.qn-site`), the generated colour remap, how to write a page under it
- [control-room](control-room.md) — Operator screen layout, liveControl.ts seam, insert-by-entry-id, where instant buttons are stored
- [station-modes](station-modes.md) — HS (hard start) and Spots mode switches, Panic, Errors pane: what they do and their limits
- [databank](databank.md) — KBR Studio's database window: songs/jingles/spots/blocks tables, spot end dates (on the schedules), day log to the millisecond
- [dashboard-timing](dashboard-timing.md) — Elapsed timer overflow fix, WS vs REST data sources
- [skip-latency](skip-latency.md) — Instant skip rule: now-playing changes must WS-broadcast
- [lint-workflow](lint-workflow.md) — Wiki housekeeping procedure
- [release-pickup](release-pickup.md) — open tabs reload themselves onto a new release, never while audio plays
- [live-listeners](live-listeners.md): when a listener counts as live, instant join/leave push to admin pages
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
- [client-ip](client-ip.md) — the one helper that says who is calling (rightmost non-internal X-Forwarded-For hop) for rate limits, listeners, activity log
- [activity-log](activity-log.md) — who did what: every staff change with before/after, sign-ins, screens opened, listener sessions; how rows are captured
- [air-watch](air-watch.md) — on-air self-check: late or missing time checks, dead air, engine drift, skipped timed items; alerts and ntfy
- [local-fm-box](local-fm-box.md) — Feeding FM from the transmitter room: the engine on a local box with a relay that keeps it on air with no internet (plan + library on disk); stages and gaps
- [engine-harness](engine-harness.md) — run the real Liquidsoap engine locally with tones and check what was heard and when
- [live-shows](live-shows.md) — how a live show goes on air without a broadcast server: mic relay, automation pause/resume, hard stop
- [deploys](deploys.md) — what a merge to main publishes: website only for frontend changes, backend only for backend changes; how to check and force

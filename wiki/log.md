# Wiki operation log

Append-only. Format: `<OP> <YYYY-MM-DD> <page-or-topic> <summary>`

Operations: INGEST | QUERY | UPDATE | LINT

INGEST 2026-05-24 README.md Wiki system bootstrapped with 4 initial pages from today's session.
INGEST 2026-05-24 streaming-architecture.md Liquidsoap+Icecast moved to dedicated VPS (TCP telnet, no HLS, plain <audio>). VPS not provisioned yet.
INGEST 2026-05-24 audio-engine.md Client-side dual-deck crossfade engine. Fixed gap: auto-crossfade 3s before end + instant pre-loaded deck swap.
INGEST 2026-05-24 hourly-slots.md Hourly/half-hour slot scheduling. Bug: weather_interval_minutes=30 ignored, weather only at :00 not :30.
INGEST 2026-05-24 dashboard-timing.md Elapsed timer overflow fixed — now uses WS data + Math.min cap instead of stale 10s REST poll.
INGEST 2026-06-10 v2-ui.md V2 UI toggle (navbar pill, localStorage). Native V2: stations+listen; other pages in light sheet. Vite proxy VITE_PROXY_TARGET for dev-against-prod.
INGEST 2026-06-10 v2-ui.md V2 deployed live to kbrlive.com + verified in prod browser (zero JS errors). Vercel CLI note: device login needs `vercel@latest` (cached 50.18.2 rejects codes); creds at AppData/Roaming/com.vercel.cli.
INGEST 2026-06-10 skip-latency.md Skip lag root cause: skip/start endpoints never WS-broadcast; Dashboard audio fed by 10s REST poll. Both fixed.
INGEST 2026-06-10 hourly-slots.md SEVERE: ad-slot preempts masked ALL hourly announcements via hour-bucketed dedup. Fixed + :30 weather added.
INGEST 2026-09-16 song-request-matching.md fuzzy_match_asset BUG: artist nicknames pick wrong recording ("Reb Shlomo"->Shlomo Simcha, not Carlebach); can auto-approve to air. Recall is char-level so semantic requests surface garbage. Library has heavy dup rows.
INGEST 2026-11-15 audio-engine.md Stale-elapsed bug: new track was seeked to previous track's elapsed (skip + every natural transition re-load). Fixed via started_at positioning, per-deck asset tracking, faded skip, timer-based crossfade check.
INGEST 2026-11-15 streaming-architecture.md Liquidsoap wiring can't be switched on as-is: _advance_queue re-pushes the current track every 3s poll, two clocks (DB duration vs engine), telnet 1234 open with no auth. Plan: engine pulls next track + reports real starts.
INGEST 2026-11-15 control-room.md Dashboard rebuilt as control room: app-shell layout, liveControl.ts is the single seam for fire-now/insert (2 calls, not atomic), insert target tracked by entry id, instant buttons in localStorage (don't PUT automation_config).
QUERY 2026-11-15 hourly-slots.md Before changing how ad slots at :15/:30/:45 are filled.
INGEST 2026-11-15 spot-schedules.md Sponsor spot schedules replace the single hardcoded ad-slot asset once any schedule exists; portal history was always empty (nothing wrote PlaySource.AD) and now matches plays by asset.
INGEST 2026-11-15 sponsorship-copy-review.md Sponsorship wording rules are data (versioned table, manager-editable), never hardcoded in prompts; copy review reads the version in force. Real Claude call not exercised in tests.
UPDATE 2026-11-15 spot-schedules.md review follow-ups: legacy spot off switch, DST missing times, validation, audio ownership, preview hides past
UPDATE 2026-11-15 spot-schedules.md planner now keeps spots out of HolidayWindow blackouts; preview reports off_air windows
INGEST 2026-11-15 (no page) queue_entries/play_logs had no station indexes in prod path (create_all, not alembic); added via main.py migrations + model __table_args__. Fresh-DB create_all currently fails on playback_mode enum default.
INGEST 2026-11-15 hourly-slots.md Time announcement text was built from generation time, not the slot hour (clips are made up to 48h ahead; after a restart all 48 said the same wrong time). Now derived from slot_key via _slot_time.
INGEST 2026-11-15 roles Screens were role-blind: any signed-in user (viewer, DJ, sponsor) could open every admin page and sponsors could read the library via bare get_current_user. Added require_staff (backend) and frontend/src/auth/access.ts route map + RoleRoute guard + role-filtered navbar. Write buttons inside allowed screens are NOT yet hidden per role.

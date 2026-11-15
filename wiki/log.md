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
UPDATE 2026-11-15 hourly-slots.md Uploaded weather spots: PUT /weather-spots/{date} replaces the day's weather asset in place (Shprach voice); ElevenLabs is the fallback.
INGEST 2026-11-15 control-room.md Dashboard rebuilt as control room: app-shell layout, liveControl.ts is the single seam for fire-now/insert (2 calls, not atomic), insert target tracked by entry id, instant buttons in localStorage (don't PUT automation_config).
QUERY 2026-11-15 hourly-slots.md Before changing how ad slots at :15/:30/:45 are filled.
INGEST 2026-11-15 spot-schedules.md Sponsor spot schedules replace the single hardcoded ad-slot asset once any schedule exists; portal history was always empty (nothing wrote PlaySource.AD) and now matches plays by asset.
INGEST 2026-11-15 sponsorship-copy-review.md Sponsorship wording rules are data (versioned table, manager-editable), never hardcoded in prompts; copy review reads the version in force. Real Claude call not exercised in tests.
UPDATE 2026-11-15 spot-schedules.md review follow-ups: legacy spot off switch, DST missing times, validation, audio ownership, preview hides past
UPDATE 2026-11-15 spot-schedules.md planner now keeps spots out of HolidayWindow blackouts; preview reports off_air windows
INGEST 2026-11-15 (no page) queue_entries/play_logs had no station indexes in prod path (create_all, not alembic); added via main.py migrations + model __table_args__. Fresh-DB create_all currently fails on playback_mode enum default.
UPDATE 2026-11-15 hourly-slots.md Reusable uploaded time clips per hour (PUT /time-announcements/{hour}); ElevenLabs optional for the hourly scheduler; fixed time text using generation time instead of the slot hour.
INGEST 2026-11-15 fresh-database A brand-new Postgres could not be built from the code: playback_mode server_default was lowercase 'sequential' but the enum labels are uppercase, create_all failed and the error was a one-line warning. Also the default category seed omitted fade_allowed. Both fixed; setup failure now logs a full error. _seed_stations has never worked (':param::jsonb' is a syntax error) and is left alone on purpose: fixing it would create three stations in production.
INGEST 2026-11-15 schedule-look-ahead.md Engine matched schedule blocks in UTC, not station time (fixed); equal-priority overlaps now have a fixed tie-break; look-ahead page shares the engine's resolver
INGEST 2026-11-15 hourly-slots.md Time announcement text was built from generation time, not the slot hour (clips are made up to 48h ahead; after a restart all 48 said the same wrong time). Now derived from slot_key via _slot_time.
INGEST 2026-11-15 roles Screens were role-blind: any signed-in user (viewer, DJ, sponsor) could open every admin page and sponsors could read the library via bare get_current_user. Added require_staff (backend) and frontend/src/auth/access.ts route map + RoleRoute guard + role-filtered navbar. Write buttons inside allowed screens are NOT yet hidden per role.
INGEST 2026-11-15 audio-engine.md Player drifted ~5.5s ahead of server per song after PR 1 (server advanced at full duration + poll; player at cross_start). Fixed: precise timer advances at cross_start; player lead bounded to 1s.
INGEST 2026-11-15 credentials Default admin password was hard-coded in auth.py, scripts, loadtest and CLAUDE.md of a public repo. Now ADMIN_SEED_PASSWORD env (seed refuses when unset); scripts use KBR_ADMIN_PASSWORD. seed-all (wipes library/stations/rules) refused in production unless ALLOW_DESTRUCTIVE_SEED=true. The old password is still in git history: it must be changed, not just removed.
INGEST 2026-11-15 (no page) lint is now meaningful: ruff keeps F/E/N/UP bug rules, style-only rules (E501, I001, UP017...) ignored in backend/pyproject.toml; frontend has eslint.config.js (flat, ESLint 9) and CI runs npm run lint. GET /stations/{id}/stream was crashing (settings never imported), found by ruff F821.
QUERY 2026-11-15 control-room.md before making library sends optimistic
INGEST 2026-11-15 control-room.md library sends (next / end / insert) now put a placeholder row in the cached queue before the request; id starts with optimistic-, replaced on refetch, rolled back on error. e2e: API_DELAY_MS=300 checks it.
INGEST 2026-11-15 control-room.md The control room refreshes queue, played log and last-played the moment the now-playing socket reports a different track (asset or start time), not at the next poll. The 10 s queue poll stays as the net for other operators' edits, which are not pushed yet.
INGEST 2026-11-15 spot-recordings.md Record Spots flow; Shprach generation is a stub (spot_voice_service._shprach) that makes placeholder tones which cannot be approved; upload endpoint matches the weather/time upload shape (file + source)
INGEST 2026-11-15 (no page) pool_pre_ping cost 4 DB round trips per request (asyncpg ping = BEGIN, parse, run, ROLLBACK; no statement cache on the Supabase pooler). Replaced by a checkout event in db/engine.py that pings only after 15s idle. A one-query request is still 4 round trips (BEGIN, parse, run, COMMIT), so Railway/Supabase region distance multiplies everything.
INGEST 2026-11-15 release-pickup.md Every backend deploy cut the current song on every station (startup marked 'playing' entries as played and started the next). Fixed: songs still within their duration are left on air. Added scheduler_leases so overlapping processes during a Railway deploy cannot both advance the queue.
INGEST 2026-11-15 release-pickup.md Open tabs now detect a new frontend release via /version.json and reload at a free moment, never while audio plays or a mic is live. Backend must stay compatible with the previous frontend.
INGEST 2026-11-15 admin-page-layout.md all admin/sponsor pages are full width (max-w removed from Layout and 19 page wrappers); layout padding must stay px-4 sm:px-6 lg:px-8 py-6 because dark pages cancel it with negative margins; Library table hides columns on phones; e2e/admin-layout-audit.mjs checks every page at 1920 and iPhone.
INGEST 2026-11-15 audio-engine.md Double advance at song change after PR 11 (poll + precise timer in separate sessions each promoted a pending entry). Fixed with a per-station advance lock shared by _check_advance and Next.
INGEST 2026-11-15 audio-engine.md Next is one request: POST queue/skip returns the GET /queue body under "queue" (client writes it to the ['queue', stationId] cache); the player goes live on a pre-loaded deck without looking up the audio URL again.

# KBR Live: full test checklist

Every feature and function the site offers, one line per testable behaviour. Built from the
code (every route, page, button, API endpoint, background job, setting, role, and stream
server (engine) vs browser-player station), then checked by an independent verifier until it
found nothing missing.

Row format: `- [ ] <id> <behaviour> | modes | covered: <test file or NOT COVERED>`.
Modes: **engine** = station played by the stream server (Liquidsoap), **browser** = station
played by the in-browser player, **both**, or n.a.

Every action row is also a speed row: the result must show (on screen and on air) within
the limits in section S. A row passes only when it does the right thing **and** is fast.

## S. Speed limits (apply to every action row)

- [ ] S-1 Button press to visible change on screen (optimistic or confirmed): under 150 ms | both | covered: NOT COVERED
- [ ] S-2 Go next / Fade / Soft fade / Play now: next item audible in the control room within 0.5 s (browser) or 1.5 s (engine, incl. stream delay) | both | covered: partial (e2e/audio-engine.mjs browser only)
- [ ] S-3 Every operator API call (skip, fire-now, insert, loop, mix-over, move, remove, reorder) answers in under 300 ms server time, 1 s round trip from the US East Coast | both | covered: tests/test_speed_operator_actions.py::test_operator_calls_answer_within_300_ms (server time, test DB); live: backend/loadtests/operator_speed.py
- [ ] S-4 Queue, now-playing and library search reads: under 300 ms server time; library search results on screen under 500 ms | n.a. | covered: tests/test_speed_operator_actions.py::test_reads_answer_within_300_ms (server time); on-screen 500 ms NOT COVERED
- [ ] S-5 Every admin page: first content under 1.5 s, interactive under 2.5 s on a warm load (desktop and iPhone) | n.a. | covered: NOT COVERED
- [ ] S-6 Saves (schedule, block, playlist, station settings, program block, spot schedule): confirmed under 1 s; on-air effect on time | both | covered: NOT COVERED
- [ ] S-7 Listen (control room or public): sound within 1.5 s of the press; stream mode stays about 1 s behind live | both | covered: partial
- [ ] S-8 Now-playing on every open screen updates within 1 s of a real change on air | both | covered: NOT COVERED
- [ ] S-9 Hard timed item (time check, hard block, hard spot) heard within 1 s of its time | both | covered: harness (engine), pw10 scripts (browser, not in repo)
- [ ] S-10 Upload: progress shown at once; item playable in the library within 10 s of upload end for a 5 MB file | n.a. | covered: NOT COVERED

## H. On-air timing on a real engine (infra/streaming/harness/run_harness.py)

- [ ] H-1 Shiur inside a hard block when the next hour's time check is due: time check on time (pause), shiur carries on after time and weather | engine | covered: harness block_shiur_spans_hour (FAILS on main: the 9 PM bug)
- [ ] H-2 Same with the station set to wait: time check after the shiur | engine | covered: harness block_shiur_spans_hour_wait
- [ ] H-3 Same with cut: time check on time, shiur does not come back | engine | covered: harness block_shiur_spans_hour_cut
- [ ] H-4 Shiur queued as an ordinary item across the hour: pause / wait / cut | engine | covered: harness queued_shiur_spans_hour[_wait|_cut]
- [ ] H-5 Song across the hour: time check on time, no silence | engine | covered: harness song_spans_hour
- [ ] H-6 Soft timed item waits for the song, no silence | engine | covered: harness soft_timed_waits_for_song
- [ ] H-7 Rapid Next (6 presses, hard/fade/soft): one item on air, what the site shows is what is heard, no silence | engine | covered: harness rapid_next
- [ ] H-8 Queue runs out: no silence, no emergency files, something on air | engine | covered: harness empty_queue

## W. Production self-check (runs on the live backend all the time)

- [ ] W-1 At every hh:00 each station's hourly time check (or the hard item due then) started within a few seconds; alert and push if not | both | covered: NOT COVERED
- [ ] W-2 Any hard timed item late by more than a few seconds or never started: alert | both | covered: NOT COVERED
- [ ] W-3 Dead air: nothing on air, or the item on air ran past its length: alert | both | covered: partial (scheduler dead-air alert)
- [ ] W-4 Engine station: what the engine plays differs from what the site shows for more than 20 s: alert | engine | covered: NOT COVERED
- [ ] W-5 A time check, weather or sponsorship skipped without an operator doing it: alert | both | covered: NOT COVERED

# Backend test checklist (exhaustive, from code)

Generated 2026-11-17 from backend/app (read-only). All API paths are under `/api/v1` unless noted. Guard in [brackets]; PUBLIC = no auth dependency. Coverage = path/function grep of backend/tests/ ("covered" means a test touches it, not that every branch is asserted). Row prefix ↳ = a behaviour/branch of the endpoint above it.

## 1. API by router

### main.py
- [ ] B-MAIN-1 `GET /health` [PUBLIC] Liveness + DB check (not under /api/v1) | modes: n.a. | covered: tests/test_health.py
- [ ] B-MAIN-2 `GET /api/v1/init` [PUBLIC] Create tables on demand (Vercel compat) | modes: n.a. | NOT COVERED

### activity.py (prefix `/activity`)
- [ ] B-ACTIVITY-1 `GET /activity` [require_admin] list activity | modes: n.a. | covered: tests/test_activity_log.py
- [ ] B-ACTIVITY-2 `GET /activity/people` [require_admin] One line per person for the period: how many actions, screens opened | modes: n.a. | covered: tests/test_activity_log.py
- [ ] B-ACTIVITY-3 `GET /activity/filters` [require_admin] People and stations to filter by, and the action groups. | modes: n.a. | NOT COVERED
- [ ] B-ACTIVITY-4 `POST /activity/page-view` [get_current_user] The admin site reports the screens a signed-in user opens. | modes: n.a. | covered: tests/test_activity_log.py
- [ ] B-ACTIVITY-5 `GET /activity/listeners` [require_admin] Each listening session: station, when, how long, where from, which device. | modes: n.a. | covered: tests/test_activity_log.py

### ai_emails.py (prefix `/ai-emails`)
- [ ] B-AIEMAILS-1 `POST /ai-emails/draft` [require_manager] generate draft | modes: n.a. | NOT COVERED
- [ ] B-AIEMAILS-2 `POST /ai-emails/send` [require_manager] send drafted email | modes: n.a. | NOT COVERED

### alerts.py (prefix `/alerts`)
- [ ] B-ALERTS-1 `GET /alerts` [require_manager] List alerts with optional filters. | modes: n.a. | covered: tests/test_alert_repeats.py, tests/test_role_access.py
- [ ] B-ALERTS-2 `GET /alerts/unresolved-count` [require_manager] Get unresolved alert count for notification badge. | modes: n.a. | covered: tests/test_alert_repeats.py
- [ ] B-ALERTS-3 `POST /alerts/resolve-all` [require_manager] Resolve every open alert (a fresh start). | modes: n.a. | covered: tests/test_alert_repeats.py
- [ ] B-ALERTS-4 `PATCH /alerts/{alert_id}/resolve` [require_manager] Mark an alert as resolved. | modes: n.a. | NOT COVERED
- [ ] B-ALERTS-5 `PATCH /alerts/{alert_id}/reopen` [require_manager] Reopen a resolved alert. | modes: n.a. | NOT COVERED
- [ ] B-ALERTS-6 `DELETE /alerts/{alert_id}` [require_admin] Delete an alert (admin only). | modes: n.a. | covered: tests/test_alert_repeats.py

### analytics.py (prefix `/analytics`)
- [ ] B-ANALYTICS-1 `GET /analytics/play-counts` [require_manager] Get play counts by asset, grouped by day. | modes: n.a. | NOT COVERED
- [ ] B-ANALYTICS-2 `GET /analytics/top-assets` [require_manager] Get most played assets. | modes: n.a. | NOT COVERED
- [ ] B-ANALYTICS-3 `GET /analytics/category-breakdown` [require_manager] Get play counts grouped by asset category. | modes: n.a. | NOT COVERED
- [ ] B-ANALYTICS-4 `GET /analytics/hourly-distribution` [require_manager] Get play distribution by hour of day. | modes: n.a. | NOT COVERED
- [ ] B-ANALYTICS-5 `GET /analytics/summary` [require_manager] Get high-level analytics summary. | modes: n.a. | NOT COVERED

### archives.py (prefix `/archives`)
- [ ] B-ARCHIVES-1 `POST /archives` [require_manager] create archive | modes: n.a. | NOT COVERED
- [ ] B-ARCHIVES-2 `GET /archives` [PUBLIC] Public: list published archives. Admin sees all. | modes: n.a. | NOT COVERED
- [ ] B-ARCHIVES-3 ↳ public sees published only; manager sees all | modes: n.a. | NOT COVERED
- [ ] B-ARCHIVES-4 `GET /archives/{archive_id}` [PUBLIC] get archive | modes: n.a. | NOT COVERED
- [ ] B-ARCHIVES-5 `PATCH /archives/{archive_id}` [require_manager] update archive | modes: n.a. | NOT COVERED
- [ ] B-ARCHIVES-6 `DELETE /archives/{archive_id}` [require_manager] delete archive | modes: n.a. | NOT COVERED
- [ ] B-ARCHIVES-7 `GET /archives/station/{station_id}/rss` [PUBLIC] Public: RSS feed for a station's show archives (podcast format). | modes: n.a. | NOT COVERED

### asset_types.py (prefix `/asset-types`)
- [ ] B-ASSETTYPES-1 `GET /asset-types` [require_staff] list asset types | modes: n.a. | covered: tests/test_role_access.py
- [ ] B-ASSETTYPES-2 `POST /asset-types` [require_manager] create asset type | modes: n.a. | NOT COVERED
- [ ] B-ASSETTYPES-3 `PATCH /asset-types/{asset_type_id}` [require_manager] update asset type | modes: n.a. | NOT COVERED
- [ ] B-ASSETTYPES-4 `DELETE /asset-types/{asset_type_id}` [require_manager] delete asset type | modes: n.a. | NOT COVERED

### assets.py (prefix `/assets`)
- [ ] B-ASSETS-1 `GET /assets/ffmpeg-check` [require_manager] Diagnostic: check FFmpeg, run conversion test, and do full roundtrip. | modes: n.a. | NOT COVERED
- [ ] B-ASSETS-2 `POST /assets/refresh-requested-category` [require_manager] Manually refresh the dynamic 'requested' category. | modes: n.a. | NOT COVERED
- [ ] B-ASSETS-3 `POST /assets/backfill-durations` [require_manager] Measure the length of items that have none (they broke the queue view). | modes: n.a. | NOT COVERED
- [ ] B-ASSETS-4 ↳ measures assets with no duration | modes: n.a. | covered: tests/test_items_without_length.py
- [ ] B-ASSETS-5 `POST /assets/backfill-release-dates` [require_manager] Batch-lookup release dates for music assets missing them. | modes: n.a. | NOT COVERED
- [ ] B-ASSETS-6 `POST /assets/upload` [require_manager] upload asset | modes: n.a. | covered: tests/test_assets.py, tests/test_mpeg_conversion.py
- [ ] B-ASSETS-7 ↳ mp3/wav/etc. converted; duration measured; codec check | modes: n.a. | covered: tests/test_mpeg_conversion.py
- [ ] B-ASSETS-8 `POST /assets/{asset_id}/analyze-audio` [require_manager] Manually trigger audio analysis for a single asset. | modes: n.a. | NOT COVERED
- [ ] B-ASSETS-9 `POST /assets/bulk-analyze` [require_manager] Batch-analyse items with no analysis or one older than ANALYSIS_VERSION (those | modes: n.a. | covered: tests/test_safe_migrations.py
- [ ] B-ASSETS-10 `GET /assets/bulk-analyze/status/{job_id}` [require_manager] Poll the status of a bulk audio analysis job. | modes: n.a. | NOT COVERED
- [ ] B-ASSETS-11 `POST /assets/bulk-auto-trim` [require_manager] Start a bulk auto-trim job. Returns a job_id to poll for status. | modes: n.a. | NOT COVERED
- [ ] B-ASSETS-12 `GET /assets/bulk-auto-trim/status/{job_id}` [require_manager] Poll the status of a bulk auto-trim job. | modes: n.a. | NOT COVERED
- [ ] B-ASSETS-13 `PATCH /assets/bulk-category` [require_manager] bulk set category | modes: n.a. | NOT COVERED
- [ ] B-ASSETS-14 `GET /assets` [require_staff] list all | modes: n.a. | covered: tests/test_assets.py, tests/test_assets_hide_generated.py, tests/test_playlist_generation.py
- [ ] B-ASSETS-15 ↳ generated weather/time clips hidden from library list | modes: n.a. | covered: tests/test_assets_hide_generated.py
- [ ] B-ASSETS-16 `GET /assets/{asset_id}/audio-url` [require_staff] Return a public URL for wavesurfer.js to fetch audio directly. | modes: n.a. | NOT COVERED
- [ ] B-ASSETS-17 `PATCH /assets/{asset_id}/request-settings` [require_manager] Update auto_approve_requests and max_requests_per_day in metadata_extra. | modes: n.a. | NOT COVERED
- [ ] B-ASSETS-18 `GET /assets/enhance-presets` [require_staff] Return all available audio enhancement presets. | modes: n.a. | NOT COVERED
- [ ] B-ASSETS-19 `POST /assets/{asset_id}/enhance` [require_manager] Apply enhancement filters to an asset. Non-destructive: keeps original. | modes: n.a. | NOT COVERED
- [ ] B-ASSETS-20 `POST /assets/{asset_id}/auto-enhance` [require_manager] AI auto-enhancement: analyze audio and apply optimal filters. | modes: n.a. | NOT COVERED
- [ ] B-ASSETS-21 `POST /assets/{asset_id}/enhance-preview` [require_staff] Preview enhancement on a short segment. Returns audio/mpeg blob. | modes: n.a. | NOT COVERED
- [ ] B-ASSETS-22 `POST /assets/{asset_id}/detect-audience` [require_staff] Detect audience/student questions in a shiur recording. | modes: n.a. | NOT COVERED
- [ ] B-ASSETS-23 `GET /assets/{asset_id}` [require_staff] get one | modes: n.a. | covered: tests/test_assets.py, tests/test_mpeg_conversion.py, tests/test_operator_actions.py
- [ ] B-ASSETS-24 `PATCH /assets/{asset_id}` [require_manager] update asset | modes: n.a. | covered: tests/test_assets.py, tests/test_mpeg_conversion.py, tests/test_operator_actions.py
- [ ] B-ASSETS-25 `GET /assets/{asset_id}/download` [require_staff] download asset | modes: n.a. | NOT COVERED
- [ ] B-ASSETS-26 `POST /assets/{asset_id}/transcode` [require_manager] transcode | modes: n.a. | NOT COVERED
- [ ] B-ASSETS-27 `POST /assets/{asset_id}/clip` [require_manager] clip | modes: n.a. | NOT COVERED
- [ ] B-ASSETS-28 `POST /assets/{asset_id}/detect-silence` [require_staff] Detect silence regions in an asset's audio file. | modes: n.a. | NOT COVERED
- [ ] B-ASSETS-29 `POST /assets/{asset_id}/trim` [require_manager] Trim audio to [trim_start, trim_end]. Non-destructive: keeps original. | modes: n.a. | covered: tests/test_activity_log.py
- [ ] B-ASSETS-30 ↳ non-destructive trim, restore-original returns file | modes: n.a. | NOT COVERED
- [ ] B-ASSETS-31 `POST /assets/{asset_id}/restore-original` [require_manager] Restore asset to its original file before any trims. | modes: n.a. | NOT COVERED
- [ ] B-ASSETS-32 `POST /assets/bulk-create` [require_manager] Create asset records for files already uploaded to Supabase Storage. | modes: n.a. | NOT COVERED
- [ ] B-ASSETS-33 `DELETE /assets/{asset_id}` [require_manager] delete | modes: n.a. | covered: tests/test_assets.py, tests/test_mpeg_conversion.py, tests/test_operator_actions.py

### auth.py (prefix `/auth`)
- [ ] B-AUTH-1 `POST /auth/login` [PUBLIC] login | modes: n.a. | covered: tests/conftest.py, tests/test_activity_log.py, tests/test_auth.py
- [ ] B-AUTH-2 ↳ wrong password -> 401; inactive user refused | modes: n.a. | covered: tests/test_auth.py
- [ ] B-AUTH-3 ↳ sponsor and staff both can log in; token type access+refresh returned | modes: n.a. | covered: tests/test_auth.py
- [ ] B-AUTH-4 ↳ rate limit (slowapi 200/min default) answers 429 when exceeded | modes: n.a. | NOT COVERED
- [ ] B-AUTH-5 `POST /auth/refresh` [PUBLIC] refresh | modes: n.a. | covered: tests/test_auth.py
- [ ] B-AUTH-6 ↳ refresh with an access token (wrong type) refused; expired refresh refused | modes: n.a. | covered: tests/test_auth.py
- [ ] B-AUTH-7 `GET /auth/me` [get_current_user] me | modes: n.a. | covered: tests/test_auth.py, tests/test_cold_start.py, tests/test_role_access.py
- [ ] B-AUTH-8 ↳ any_role incl. sponsor gets 200; anonymous 401/403 | modes: n.a. | covered: tests/test_role_access.py
- [ ] B-AUTH-9 `POST /auth/seed` [PUBLIC] seed admin | modes: n.a. | covered: tests/test_auth.py
- [ ] B-AUTH-10 ↳ refused (503) when ADMIN_SEED_PASSWORD unset; refused (403) when an admin already exists | modes: n.a. | covered: tests/test_auth.py
- [ ] B-AUTH-11 `POST /auth/seed-reset` [require_admin] Reset admin credentials — requires existing admin auth. | modes: n.a. | NOT COVERED
- [ ] B-AUTH-12 `POST /auth/migrate` [PUBLIC] Add new columns to existing tables (safe to run multiple times). | modes: n.a. | NOT COVERED
- [ ] B-AUTH-13 ↳ PUBLIC (no guard): safe to repeat; confirm it cannot be abused (idempotent ALTERs only) | modes: n.a. | NOT COVERED
- [ ] B-AUTH-14 `POST /auth/seed-all` [require_admin] Drop all data and re-seed everything: admin, station, assets, rules. | modes: n.a. | covered: tests/test_auth.py
- [ ] B-AUTH-15 ↳ refused in production unless ALLOW_DESTRUCTIVE_SEED=true; wipes library/stations/rules otherwise | modes: n.a. | covered: tests/test_auth.py
- [ ] B-AUTH-16 `POST /auth/seed-assets` [require_admin] Redirect to seed-all. | modes: n.a. | NOT COVERED

### billing.py (prefix `/billing`)
- [ ] B-BILLING-1 `GET /billing/invoices` [require_sponsor_or_manager] list invoices | modes: n.a. | NOT COVERED
- [ ] B-BILLING-2 `POST /billing/invoices` [require_manager] create invoice | modes: n.a. | NOT COVERED
- [ ] B-BILLING-3 `POST /billing/invoices/{invoice_id}/checkout` [require_sponsor] create checkout session | modes: n.a. | NOT COVERED
- [ ] B-BILLING-4 ↳ sponsor can only checkout own invoice; stripe disabled -> error | modes: n.a. | NOT COVERED
- [ ] B-BILLING-5 `POST /billing/webhook` [PUBLIC] stripe webhook | modes: n.a. | NOT COVERED
- [ ] B-BILLING-6 ↳ Stripe signature verified; invoice marked paid | modes: n.a. | NOT COVERED
- [ ] B-BILLING-7 `GET /billing/summary` [require_sponsor] get billing summary | modes: n.a. | NOT COVERED

### bug_reports.py (prefix `/bug-reports`)
- [ ] B-BUGREPORTS-1 `POST /bug-reports` [_optional_user] create bug report | modes: n.a. | covered: tests/test_bug_reports.py
- [ ] B-BUGREPORTS-2 ↳ anonymous allowed; mirrored to GitHub when token set; ntfy push on receipt | modes: n.a. | covered: tests/test_bug_reports.py
- [ ] B-BUGREPORTS-3 `GET /bug-reports` [require_staff] list bug reports | modes: n.a. | covered: tests/test_bug_reports.py
- [ ] B-BUGREPORTS-4 `GET /bug-reports/{report_id}` [require_staff] get bug report | modes: n.a. | covered: tests/test_bug_reports.py
- [ ] B-BUGREPORTS-5 `GET /bug-reports/{report_id}/screenshot` [require_staff] get bug screenshot | modes: n.a. | covered: tests/test_bug_reports.py
- [ ] B-BUGREPORTS-6 `PATCH /bug-reports/{report_id}` [require_admin] set bug status | modes: n.a. | covered: tests/test_bug_reports.py
- [ ] B-BUGREPORTS-7 `POST /bug-reports/{report_id}/resend` [require_admin] Copy a report to GitHub again (e.g. one sent before the token was set). | modes: n.a. | NOT COVERED
- [ ] B-BUGREPORTS-8 ↳ admin only; copies to GitHub again | modes: n.a. | NOT COVERED

### call_in.py (prefix `/call-in`)
- [ ] B-CALLIN-1 `POST /call-in/{station_id}` [PUBLIC] Join the queue of the station's live show. Returns the call and the token its | modes: n.a. | covered: tests/test_web_call_in.py
- [ ] B-CALLIN-2 ↳ no live show -> 409; show not taking calls -> 409; burst -> 429 | modes: n.a. | covered: tests/test_web_call_in.py

### campaigns.py (prefix `/campaigns`)
- [ ] B-CAMPAIGNS-1 `GET /campaigns` [require_sponsor_or_manager] list campaigns | modes: n.a. | NOT COVERED
- [ ] B-CAMPAIGNS-2 ↳ sponsor sees only own campaigns; manager sees all | modes: n.a. | NOT COVERED
- [ ] B-CAMPAIGNS-3 `POST /campaigns` [require_sponsor_or_manager] create campaign | modes: n.a. | NOT COVERED
- [ ] B-CAMPAIGNS-4 `GET /campaigns/{campaign_id}` [require_sponsor_or_manager] get campaign | modes: n.a. | NOT COVERED
- [ ] B-CAMPAIGNS-5 `PATCH /campaigns/{campaign_id}` [require_sponsor_or_manager] update campaign | modes: n.a. | NOT COVERED
- [ ] B-CAMPAIGNS-6 `PATCH /campaigns/{campaign_id}/status` [require_manager] update campaign status | modes: n.a. | NOT COVERED
- [ ] B-CAMPAIGNS-7 `GET /campaigns/{campaign_id}/drafts` [require_sponsor_or_manager] list drafts | modes: n.a. | NOT COVERED
- [ ] B-CAMPAIGNS-8 `POST /campaigns/{campaign_id}/drafts` [require_sponsor_or_manager] create draft | modes: n.a. | NOT COVERED
- [ ] B-CAMPAIGNS-9 `POST /campaigns/{campaign_id}/drafts/{draft_id}/upload-audio` [require_sponsor_or_manager] upload draft audio | modes: n.a. | NOT COVERED
- [ ] B-CAMPAIGNS-10 `GET /campaigns/{campaign_id}/comments` [require_sponsor_or_manager] list comments | modes: n.a. | NOT COVERED
- [ ] B-CAMPAIGNS-11 `POST /campaigns/{campaign_id}/comments` [require_sponsor_or_manager] create comment | modes: n.a. | NOT COVERED

### categories.py (prefix `/categories`)
- [ ] B-CATEGORIES-1 `GET /categories` [require_staff] list categories | modes: n.a. | covered: tests/test_role_access.py
- [ ] B-CATEGORIES-2 `POST /categories` [require_manager] create category | modes: n.a. | NOT COVERED
- [ ] B-CATEGORIES-3 `PATCH /categories/{category_id}` [require_manager] update category | modes: n.a. | NOT COVERED
- [ ] B-CATEGORIES-4 `DELETE /categories/{category_id}` [require_manager] delete category | modes: n.a. | NOT COVERED

### channels.py (prefix `/channels`)
- [ ] B-CHANNELS-1 `GET /channels` [PUBLIC] list channels | modes: n.a. | NOT COVERED
- [ ] B-CHANNELS-2 `POST /channels` [require_manager] create channel | modes: n.a. | NOT COVERED
- [ ] B-CHANNELS-3 `PUT /channels/{channel_id}` [require_manager] update channel | modes: n.a. | NOT COVERED
- [ ] B-CHANNELS-4 `DELETE /channels/{channel_id}` [require_manager] delete channel | modes: n.a. | NOT COVERED

### controls.py (prefix `/stations`) — router guard: require_station_operator on writes
- [ ] B-CONTROLS-1 `POST /stations/{station_id}/controls/play` [require_dj_or_manager] play | modes: both | covered: tests/test_controls_streams_scheduler.py::test_controls_play_with_empty_redis_queue; BUG controls 500 without Redis (tests/test_controls_streams_scheduler.py::test_controls_do_not_500_without_redis, xfail)
- [ ] B-CONTROLS-2 ↳ play/pause/stop/play-now/enqueue respect require_station_operator (protected station) | modes: both | covered: tests/test_station_permissions.py
- [ ] B-CONTROLS-3 `POST /stations/{station_id}/controls/pause` [require_dj_or_manager] pause | modes: both | covered: tests/test_controls_streams_scheduler.py::test_controls_pause_and_enqueue; BUG 500 without Redis (xfail)
- [ ] B-CONTROLS-4 `POST /stations/{station_id}/controls/stop` [require_dj_or_manager] stop | modes: both | covered: tests/test_station_permissions.py
- [ ] B-CONTROLS-5 `POST /stations/{station_id}/controls/play-now` [require_dj_or_manager] play now endpoint | modes: both | covered: BUG play-now runs an HLS player beside the queue (tests/test_controls_streams_scheduler.py::test_controls_play_now_goes_through_the_queue, xfail)
- [ ] B-CONTROLS-6 ↳ play-now starts the asset immediately; interplay with queue's single player | modes: both | covered: BUG second player (tests/test_controls_streams_scheduler.py::test_controls_play_now_goes_through_the_queue, xfail)
- [ ] B-CONTROLS-7 `POST /stations/{station_id}/controls/enqueue` [require_dj_or_manager] enqueue | modes: both | covered: tests/test_controls_streams_scheduler.py::test_controls_pause_and_enqueue

### copy_review.py (prefix `/sponsorship`)
- [ ] B-COPYREVIEW-1 `GET /sponsorship/rules` [require_sponsor_or_manager] get rules | modes: n.a. | covered: tests/test_copy_review.py
- [ ] B-COPYREVIEW-2 `PUT /sponsorship/rules` [require_manager] Save edited rules as a new version. Reviews use it immediately. | modes: n.a. | covered: tests/test_copy_review.py
- [ ] B-COPYREVIEW-3 `GET /sponsorship/rules/history` [require_manager] rules history | modes: n.a. | covered: tests/test_copy_review.py
- [ ] B-COPYREVIEW-4 `POST /sponsorship/copy-review` [require_sponsor_or_manager] Check proposed sponsorship wording against the rules in force. | modes: n.a. | covered: tests/test_copy_review.py

### crm.py (prefix `/crm`)
- [ ] B-CRM-1 `POST /crm/register` [PUBLIC] Register a new CRM member. Returns a 6-digit PIN (shown once). | modes: n.a. | NOT COVERED
- [ ] B-CRM-2 `POST /crm/login` [PUBLIC] Login with a 6-digit PIN. | modes: n.a. | NOT COVERED
- [ ] B-CRM-3 `POST /crm/rate` [get_crm_member] Rate or favorite a song. Upserts if a rating already exists. | modes: n.a. | NOT COVERED
- [ ] B-CRM-4 `DELETE /crm/rate/{asset_id}` [get_crm_member] Remove a rating for a song. | modes: n.a. | NOT COVERED
- [ ] B-CRM-5 `GET /crm/my-ratings` [get_crm_member] List all ratings/favorites for the authenticated member. | modes: n.a. | NOT COVERED
- [ ] B-CRM-6 `GET /crm/raffles/active` [PUBLIC] List open raffles. | modes: n.a. | NOT COVERED
- [ ] B-CRM-7 `POST /crm/raffles/{raffle_id}/enter` [get_crm_member] Enter a raffle. | modes: n.a. | NOT COVERED
- [ ] B-CRM-8 `GET /crm/members` [require_manager] List CRM members with optional search. | modes: n.a. | NOT COVERED
- [ ] B-CRM-9 `GET /crm/members/{member_id}` [require_manager] Get a single member detail. | modes: n.a. | NOT COVERED
- [ ] B-CRM-10 `DELETE /crm/members/{member_id}` [require_manager] Deactivate a CRM member (soft delete). | modes: n.a. | NOT COVERED
- [ ] B-CRM-11 `GET /crm/song-rankings` [require_manager] Songs ranked by listener ratings — useful for playlist curation. | modes: n.a. | NOT COVERED
- [ ] B-CRM-12 `GET /crm/raffles` [require_manager] List all raffles (admin). | modes: n.a. | NOT COVERED
- [ ] B-CRM-13 `POST /crm/raffles` [require_manager] Create a new raffle. | modes: n.a. | NOT COVERED
- [ ] B-CRM-14 `PATCH /crm/raffles/{raffle_id}` [require_manager] Update a raffle. | modes: n.a. | NOT COVERED
- [ ] B-CRM-15 `POST /crm/raffles/{raffle_id}/draw` [require_manager] Randomly draw a winner for a raffle. | modes: n.a. | NOT COVERED
- [ ] B-CRM-16 `GET /crm/raffles/{raffle_id}/entries` [require_manager] List entries for a raffle. | modes: n.a. | NOT COVERED

### holidays.py (prefix `/holidays`)
- [ ] B-HOLIDAYS-1 `GET /holidays` [require_manager] list holidays | modes: both | covered: tests/test_holidays.py
- [ ] B-HOLIDAYS-2 `POST /holidays` [require_manager] create holiday | modes: both | covered: tests/test_holidays.py
- [ ] B-HOLIDAYS-3 `PUT /holidays/{holiday_id}` [require_manager] update holiday | modes: both | covered: tests/test_holidays.py
- [ ] B-HOLIDAYS-4 `DELETE /holidays/{holiday_id}` [require_manager] delete holiday | modes: both | covered: tests/test_holidays.py
- [ ] B-HOLIDAYS-5 `POST /holidays/preview` [require_manager] Preview auto-generated blackout windows without saving to DB. | modes: both | NOT COVERED
- [ ] B-HOLIDAYS-6 `POST /holidays/auto-generate` [require_manager] Auto-generate Shabbos & Yom Tov blackout windows based on station location. | modes: both | NOT COVERED
- [ ] B-HOLIDAYS-7 ↳ Shabbos/Yom Tov windows from station location (candle-lighting/havdalah) | modes: both | NOT COVERED
- [ ] B-HOLIDAYS-8 `POST /holidays/ensure-silence-asset` [require_manager] Ensure a silence audio asset exists. Creates one via FFmpeg if needed. | modes: both | NOT COVERED
- [ ] B-HOLIDAYS-9 ↳ creates silence asset via FFmpeg if missing | modes: n.a. | NOT COVERED

### icecast.py (prefix `/icecast`)
- [ ] B-ICECAST-1 `GET /icecast/status` [require_admin] Get Icecast configuration status. | modes: n.a. | NOT COVERED
- [ ] B-ICECAST-2 `POST /icecast/{station_id}/start` [require_admin] Start Icecast OTA stream for a station. | modes: n.a. | NOT COVERED
- [ ] B-ICECAST-3 `POST /icecast/{station_id}/stop` [require_admin] Stop Icecast OTA stream for a station. | modes: n.a. | NOT COVERED

### listeners.py (prefix `/listeners`)
- [ ] B-LISTENERS-1 `POST /listeners/heartbeat` [PUBLIC] Public endpoint — called by the Listen page every 30s to track active listeners. | modes: both | covered: tests/test_listeners_live.py
- [ ] B-LISTENERS-2 ↳ new session created with geo lookup; rejoin within window reuses session | modes: both | covered: tests/test_listeners_live.py
- [ ] B-LISTENERS-3 `POST /listeners/disconnect` [PUBLIC] Called when a listener stops — finalizes the session and drops it from the live count. | modes: both | covered: tests/test_listeners_live.py
- [ ] B-LISTENERS-4 `POST /listeners/leave` [PUBLIC] Same as /disconnect, for navigator.sendBeacon when the page closes. | modes: both | covered: tests/test_listeners_live.py
- [ ] B-LISTENERS-5 `WEBSOCKET /listeners/ws` [PUBLIC] Admin pages keep this open and refetch /live when told something changed. | modes: both | NOT COVERED
- [ ] B-LISTENERS-6 ↳ admin watchers told to refetch on change; dead socket dropped | modes: n.a. | NOT COVERED
- [ ] B-LISTENERS-7 `GET /listeners/live` [require_manager] Current active listeners across all stations, grouped by station + region. | modes: both | covered: tests/test_listeners_live.py
- [ ] B-LISTENERS-8 `GET /listeners/today` [require_manager] Today's listener stats: total sessions, unique listeners, total minutes. | modes: both | NOT COVERED
- [ ] B-LISTENERS-9 ↳ total sessions, unique, minutes, peak concurrent | modes: n.a. | NOT COVERED
- [ ] B-LISTENERS-10 `GET /listeners/history` [require_manager] Daily listener aggregates for charting. | modes: both | NOT COVERED
- [ ] B-LISTENERS-11 `GET /listeners/regions` [require_manager] Listener breakdown by country/region over a period. | modes: both | NOT COVERED

### live_shows.py (prefix `/live-shows`) — router guard: station_write_guard(show_id)
- [ ] B-LIVESHOWS-1 `POST /live-shows` [require_dj_or_manager] Create a new live show. | modes: both | NOT COVERED
- [ ] B-LIVESHOWS-2 `GET /live-shows` [require_staff] List live shows with optional filters. | modes: both | covered: tests/test_role_access.py
- [ ] B-LIVESHOWS-3 `GET /live-shows/{show_id}` [require_dj_or_manager] Get a live show by ID. | modes: both | NOT COVERED
- [ ] B-LIVESHOWS-4 `PATCH /live-shows/{show_id}` [require_dj_or_manager] Update a live show. | modes: both | NOT COVERED
- [ ] B-LIVESHOWS-5 `DELETE /live-shows/{show_id}` [require_admin] Delete a live show (admin only). | modes: both | NOT COVERED
- [ ] B-LIVESHOWS-6 `POST /live-shows/{show_id}/start` [require_dj_or_manager] Start a live show (go live). | modes: both | covered (indirect, calls handler): tests/test_live_show_lifecycle.py, tests/test_live_show_on_air.py
- [ ] B-LIVESHOWS-7 ↳ start pauses automation (live_show_id set), queue resumes after end | modes: both | covered: tests/test_live_show_on_air.py
- [ ] B-LIVESHOWS-8 ↳ station not operable by user -> 403 (station_write_guard) | modes: n.a. | NOT COVERED
- [ ] B-LIVESHOWS-9 `POST /live-shows/{show_id}/end` [require_dj_or_manager] End a live show. | modes: both | NOT COVERED
- [ ] B-LIVESHOWS-10 ↳ end clears live_show_id and resumes automation; archive created? | modes: both | covered: tests/test_live_show_on_air.py
- [ ] B-LIVESHOWS-11 `GET /live-shows/{show_id}/time-remaining` [require_dj_or_manager] Get seconds until hard stop. | modes: both | NOT COVERED
- [ ] B-LIVESHOWS-12 `GET /live-shows/{show_id}/calls` [require_dj_or_manager] List all callers for a show. | modes: both | NOT COVERED
- [ ] B-LIVESHOWS-13 `POST /live-shows/{show_id}/calls/{call_id}/approve` [require_dj_or_manager] Approve a caller. | modes: both | NOT COVERED
- [ ] B-LIVESHOWS-14 `POST /live-shows/{show_id}/calls/{call_id}/reject` [require_dj_or_manager] Reject a caller. | modes: both | NOT COVERED
- [ ] B-LIVESHOWS-15 `POST /live-shows/{show_id}/calls/{call_id}/on-air` [require_dj_or_manager] Push an approved caller on air. Only one caller can be on_air at a time (409 if attempted). | modes: both | covered: tests/test_web_call_in.py
- [ ] B-LIVESHOWS-16 ↳ only one caller on air at a time -> 409 | modes: n.a. | covered: tests/test_web_call_in.py
- [ ] B-LIVESHOWS-17 `POST /live-shows/{show_id}/calls/{call_id}/end-call` [require_dj_or_manager] End an active call. | modes: both | NOT COVERED
- [ ] B-LIVESHOWS-18 `PATCH /live-shows/{show_id}/calls/{call_id}` [require_dj_or_manager] Update caller name/notes. | modes: both | covered: tests/test_web_call_in.py
- [ ] B-LIVESHOWS-19 `GET /live-shows/station/{station_id}/active` [PUBLIC] Public endpoint: get the active live show for a station (if any). | modes: both | NOT COVERED
- [ ] B-LIVESHOWS-20 `POST /live-shows/twilio/inbound` [PUBLIC] Twilio inbound call webhook — find active show, create CallInRequest, return hold TwiML. | modes: both | NOT COVERED
- [ ] B-LIVESHOWS-21 ↳ Twilio signature/no active show -> polite TwiML; creates CallInRequest | modes: n.a. | NOT COVERED
- [ ] B-LIVESHOWS-22 `POST /live-shows/twilio/status` [PUBLIC] Twilio call status callback — update call status when caller hangs up. | modes: both | NOT COVERED
- [ ] B-LIVESHOWS-23 `GET /live-shows/twilio/hold-music` [PUBLIC] Return TwiML with hold music URL for Twilio conference waitUrl. | modes: both | NOT COVERED

### live_shows_ws.py
- [ ] B-LIVESHOWSW-1 `WEBSOCKET /ws/live/station/{station_id}/listen` [PUBLIC] Public: a listener page hears the station's live show here while one is on air. | modes: both | NOT COVERED
- [ ] B-LIVESHOWSW-2 `WEBSOCKET /ws/live/{show_id}/events` [PUBLIC] Real-time show state + caller updates WebSocket. | modes: both | NOT COVERED
- [ ] B-LIVESHOWSW-3 `WEBSOCKET /ws/live/{show_id}/audio` [PUBLIC] Binary audio WebSocket — receives MP3 chunks from host browser, forwards to Icecast. | modes: both | NOT COVERED
- [ ] B-LIVESHOWSW-4 ↳ host MP3 chunks relayed to station listeners only; dead listener dropped | modes: both | covered: tests/test_live_show_on_air.py
- [ ] B-LIVESHOWSW-5 `WEBSOCKET /ws/live/call/{call_id}` [PUBLIC] A web caller (public page): first message {"type":"auth","token":...} with the | modes: both | NOT COVERED
- [ ] B-LIVESHOWSW-6 ↳ first message must be auth token; wrong token closes | modes: n.a. | covered: tests/test_web_call_in.py

### mixer.py (prefix `/studio`)
- [ ] B-MIXER-1 `POST /studio/mix` [require_manager] Mix two assets (backtrack + overlay) and save as a new asset. | modes: n.a. | NOT COVERED

### now_playing.py (prefix `/now-playing`)
- [ ] B-NOWPLAYING-1 `GET /now-playing/{station_id}` [PUBLIC] Get current now-playing state for a station. | modes: both | covered: tests/test_now_playing_broadcast.py
- [ ] B-NOWPLAYING-2 `GET /now-playing/{station_id}/active-block` [PUBLIC] get active block | modes: both | covered: tests/test_controls_streams_scheduler.py::test_active_block_none_and_fixed
- [ ] B-NOWPLAYING-3 ↳ returns schedule block active now (sun-relative times resolved) | modes: both | covered: tests/test_controls_streams_scheduler.py::test_active_block_resolves_sunset_times
- [ ] B-NOWPLAYING-4 `PATCH /now-playing/{station_id}` [require_dj_or_manager] update now playing | modes: both | covered: tests/test_now_playing_broadcast.py
- [ ] B-NOWPLAYING-5 `DELETE /now-playing/{station_id}` [require_dj_or_manager] Clear now-playing state for a station. | modes: both | covered: tests/test_now_playing_broadcast.py

### nowplaying.py (prefix `/ws`) — NOT MOUNTED (dead code)
- [ ] B-NOWPLAYING-6 `WEBSOCKET /ws/nowplaying/{station_id}` [PUBLIC] nowplaying websocket | modes: both | covered: tests/test_controls_streams_scheduler.py::test_old_nowplaying_socket_is_not_mounted
- [ ] B-NOWPLAYING-7 ↳ DEAD CODE: nowplaying.py router is not mounted in api/v1/__init__.py - confirm/remove | modes: n.a. | covered: tests/test_controls_streams_scheduler.py::test_old_nowplaying_socket_is_not_mounted (confirmed not mounted)

### playlists.py (prefix `/playlists`) — router guard: station_write_guard(template/slot)
- [ ] B-PLAYLISTS-1 `GET /playlists` [require_manager] list templates | modes: both | NOT COVERED
- [ ] B-PLAYLISTS-2 `POST /playlists` [require_manager] create template | modes: both | NOT COVERED
- [ ] B-PLAYLISTS-3 `GET /playlists/asset-types` [require_manager] Return distinct asset_type + category combinations from the assets table. | modes: both | NOT COVERED
- [ ] B-PLAYLISTS-4 `GET /playlists/generation` [require_manager] Settings, plus a dry run of what generation would queue next. Changes nothing. | modes: both | covered: tests/test_playlist_generation.py, tests/test_station_permissions.py
- [ ] B-PLAYLISTS-5 `PUT /playlists/generation` [require_manager] set generation | modes: both | covered: tests/test_playlist_generation.py, tests/test_station_permissions.py
- [ ] B-PLAYLISTS-6 ↳ enable generation: queue fills from block sequences N hours ahead; artist distance, min vote | modes: both | covered: tests/test_playlist_generation.py
- [ ] B-PLAYLISTS-7 `POST /playlists/generation/regenerate` [require_manager] Throw away the automatically queued songs that have not started and generate again. | modes: both | covered: tests/test_station_permissions.py
- [ ] B-PLAYLISTS-8 ↳ drops unplayed automatic songs and regenerates; manual items kept | modes: both | covered: tests/test_playlist_generation.py
- [ ] B-PLAYLISTS-9 `GET /playlists/{template_id}` [require_manager] get template | modes: both | covered: tests/test_playlist_generation.py, tests/test_station_permissions.py
- [ ] B-PLAYLISTS-10 `PATCH /playlists/{template_id}` [require_manager] update template | modes: both | covered: tests/test_playlist_generation.py, tests/test_station_permissions.py
- [ ] B-PLAYLISTS-11 `DELETE /playlists/{template_id}` [require_manager] delete template | modes: both | covered: tests/test_playlist_generation.py, tests/test_station_permissions.py
- [ ] B-PLAYLISTS-12 `POST /playlists/slots` [require_manager] create slot | modes: both | NOT COVERED
- [ ] B-PLAYLISTS-13 `PATCH /playlists/slots/{slot_id}` [require_manager] update slot | modes: both | covered: tests/test_schedule_refresh.py
- [ ] B-PLAYLISTS-14 `DELETE /playlists/slots/{slot_id}` [require_manager] delete slot | modes: both | covered: tests/test_schedule_refresh.py

### playout.py (prefix `/playout`) — X-Playout-Token, engine stations only
- [ ] B-PLAYOUT-1 `POST /playout/{station_id}/hello` [_require_engine] The engine announces that it (re)started. | modes: engine | NOT COVERED
- [ ] B-PLAYOUT-2 ↳ engine restart resets in-memory state (cued, timed_sent) | modes: engine | NOT COVERED
- [ ] B-PLAYOUT-3 `GET /playout/{station_id}/next` [_require_engine] What should play next. 204 when the queue is empty (the engine retries). | modes: engine | covered: tests/test_playout_engine.py
- [ ] B-PLAYOUT-4 ↳ missing/wrong X-Playout-Token -> 403 | modes: engine | covered: tests/test_playout_engine.py
- [ ] B-PLAYOUT-5 ↳ station not engine-driven -> 404 | modes: engine | covered: tests/test_playout_engine.py
- [ ] B-PLAYOUT-6 ↳ empty queue -> 204 after one refill attempt (no refill during blackout) | modes: engine | NOT COVERED
- [ ] B-PLAYOUT-7 ↳ due soft ad slots first, then by position; future-timed wait | modes: engine | covered: tests/test_playout_engine.py
- [ ] B-PLAYOUT-8 ↳ cued entry already handed out is not handed again; lost start report caught up | modes: engine | covered: tests/test_playout_engine.py
- [ ] B-PLAYOUT-9 ↳ entry with no playable file is dropped and next tried | modes: engine | NOT COVERED
- [ ] B-PLAYOUT-10 ↳ PAST-DUE hard time/weather not handed to timed lane is picked by position (late) - should be first | modes: engine | NOT COVERED
- [ ] B-PLAYOUT-11 `POST /playout/{station_id}/started` [_require_engine] The engine reports what just went on air. | modes: engine | covered: tests/test_playout_engine.py
- [ ] B-PLAYOUT-12 ↳ closes previous playing entry, logs PlayLog (>=1 s), marks played/skipped | modes: engine | covered: tests/test_playout_engine.py
- [ ] B-PLAYOUT-13 ↳ no entry id -> emergency playlist alert once | modes: engine | covered: tests/test_playout_engine.py
- [ ] B-PLAYOUT-14 ↳ entry deleted by redraw but handed -> still shown as on air | modes: engine | covered: tests/test_playout_engine.py
- [ ] B-PLAYOUT-15 ↳ broadcasts now-playing and replenishes (not during blackout) | modes: engine | NOT COVERED

### playout_status.py (prefix `/playout-status`)
- [ ] B-PLAYOUTSTA-1 `GET /playout-status` [PUBLIC] {station_id: status} for every engine station; any other station is "browser". | modes: both | covered: tests/test_playout_status.py
- [ ] B-PLAYOUTSTA-2 ↳ per station: browser vs server; engine status fields | modes: both | covered: tests/test_playout_status.py

### program_blocks.py (prefix `/program-blocks`)
- [ ] B-PROGRAMBLO-1 `GET /program-blocks` [require_manager] list program blocks | modes: both | covered: tests/test_program_blocks.py
- [ ] B-PROGRAMBLO-2 `GET /program-blocks/capabilities` [require_manager] What blocks can do on this server, for scripts that set blocks up (the Daf Yomi job | modes: both | covered: tests/test_blocks_keep_hourly.py
- [ ] B-PROGRAMBLO-3 `GET /program-blocks/preview` [require_manager] What the blocks will air over the coming hours, with each run's length. | modes: both | covered: tests/test_program_blocks.py
- [ ] B-PROGRAMBLO-4 ↳ next 24h runs with lengths; changes nothing | modes: n.a. | covered: tests/test_program_blocks.py
- [ ] B-PROGRAMBLO-5 `POST /program-blocks` [require_manager] create program block | modes: both | covered: tests/test_program_blocks.py
- [ ] B-PROGRAMBLO-6 ↳ block items expanded into source=block queue entries at clock times (hard/soft/soft+limit) | modes: both | covered: tests/test_program_blocks.py
- [ ] B-PROGRAMBLO-7 ↳ blocks replace built-in hourly time/weather unless blocks_keep_hourly | modes: both | covered: tests/test_blocks_keep_hourly.py
- [ ] B-PROGRAMBLO-8 ↳ non-assigned station -> 403 | modes: n.a. | NOT COVERED
- [ ] B-PROGRAMBLO-9 `PUT /program-blocks/{block_id}` [require_manager] update program block | modes: both | covered: tests/test_blocks_keep_hourly.py, tests/test_program_blocks.py
- [ ] B-PROGRAMBLO-10 `DELETE /program-blocks/{block_id}` [require_manager] delete program block | modes: both | covered: tests/test_blocks_keep_hourly.py, tests/test_program_blocks.py

### queue.py (prefix `/stations/{station_id}/queue`) — router guard: require_station_operator on writes
- [ ] B-QUEUE-1 `GET /stations/{station_id}/queue` [require_staff] get queue | modes: both | covered: tests/test_items_without_length.py, tests/test_next_shown_is_next_played.py, tests/test_operator_actions.py
- [ ] B-QUEUE-2 ↳ next_due/up-next shows a timed item due before the song ends | modes: both | covered: tests/test_up_next_shows_timed_item.py
- [ ] B-QUEUE-3 ↳ items with no duration do not break the read | modes: both | covered: tests/test_items_without_length.py
- [ ] B-QUEUE-4 ↳ queue read failure returns graceful error, not 500 storm | modes: both | covered: tests/test_queue_read_failure.py
- [ ] B-QUEUE-5 ↳ viewer may read; sponsor 403 | modes: n.a. | covered: tests/test_queue_endpoints.py::test_viewer_may_read_the_queue_and_a_sponsor_may_not
- [ ] B-QUEUE-6 `GET /stations/{station_id}/queue/log` [require_staff] Get recent play history. | modes: both | covered: tests/test_queue_endpoints.py::test_play_log_is_newest_first_and_honours_limit
- [ ] B-QUEUE-7 ↳ recent play history ordered newest first, limit param | modes: both | covered: tests/test_queue_endpoints.py::test_play_log_is_newest_first_and_honours_limit
- [ ] B-QUEUE-8 `GET /stations/{station_id}/queue/last-played` [require_staff] Get the most recent play time for each asset. | modes: both | covered: tests/test_queue_endpoints.py::test_last_played_gives_the_latest_play_per_item
- [ ] B-QUEUE-9 ↳ served from last_played in-memory cache; new plays appear without full re-read | modes: both | covered: tests/test_last_played_cache.py
- [ ] B-QUEUE-10 `POST /stations/{station_id}/queue/add` [require_dj_or_manager] add to queue | modes: both | covered: tests/test_queue_endpoints.py::test_add_appends_at_the_end_as_manual
- [ ] B-QUEUE-11 ↳ appends at end with source=manual; unknown asset -> error | modes: both | covered: tests/test_queue_endpoints.py::test_unknown_asset_is_refused_with_404 (was 500, fixed)
- [ ] B-QUEUE-12 `POST /stations/{station_id}/queue/bulk-add` [require_dj_or_manager] bulk add to queue | modes: both | covered: tests/test_queue_endpoints.py::test_bulk_add_appends_all_in_order
- [ ] B-QUEUE-13 `POST /stations/{station_id}/queue/play-next` [require_dj_or_manager] play next | modes: both | covered: tests/test_queue_endpoints.py::test_play_next_goes_right_after_the_item_on_air
- [ ] B-QUEUE-14 ↳ shifts pending positions +1 and inserts after the playing item; empty queue -> position 1 | modes: both | covered: tests/test_queue_endpoints.py::test_play_next_on_an_empty_queue_gets_position_one
- [ ] B-QUEUE-15 `POST /stations/{station_id}/queue/fire-now` [require_dj_or_manager] Put an asset on air now (one call instead of play-next + skip). | modes: both | covered: tests/test_operator_actions.py, tests/test_playout_engine.py
- [ ] B-QUEUE-16 ↳ browser: item starts immediately, current skipped, response carries new queue | modes: browser | covered: tests/test_operator_actions.py
- [ ] B-QUEUE-17 ↳ engine: item handed to engine timed lane; engine down -> 503 and item stays queued as next | modes: engine | covered: tests/test_playout_engine.py
- [ ] B-QUEUE-18 ↳ mode=after-current only queues it | modes: both | covered: tests/test_playout_engine.py
- [ ] B-QUEUE-19 ↳ client_request_id repeat returns duplicate:true without firing twice | modes: both | covered: tests/test_operator_actions.py
- [ ] B-QUEUE-20 ↳ fire-now while a timed item (time/weather) is on air still puts the item on air | modes: browser | covered: tests/test_queue_endpoints.py::test_fire_now_during_a_timed_item_still_goes_on_air (was only queued, fixed)
- [ ] B-QUEUE-21 `POST /stations/{station_id}/queue/mix-over` [require_dj_or_manager] Play an asset on top of what is on air. The item on air carries on and the queue | modes: both | covered: tests/test_operator_actions.py
- [ ] B-QUEUE-22 ↳ browser: WS mix_over broadcast to station listeners; queue does not move | modes: browser | covered: tests/test_operator_actions.py
- [ ] B-QUEUE-23 ↳ engine: engine_overlay; engine down -> 503 | modes: engine | NOT COVERED
- [ ] B-QUEUE-24 ↳ asset without audio file -> 409; unknown asset -> 404 | modes: both | covered: tests/test_operator_actions.py
- [ ] B-QUEUE-25 ↳ PlayLog row written with source=manual | modes: both | covered: tests/test_queue_endpoints.py::test_mix_over_logs_once_and_ignores_a_double_tap
- [ ] B-QUEUE-26 `POST /stations/{station_id}/queue/skip` [require_dj_or_manager] skip current | modes: both | covered: tests/test_activity_log.py, tests/test_operator_actions.py, tests/test_playout_engine.py
- [ ] B-QUEUE-27 ↳ fade=hard -> 0 s, fade (default) -> 0.5 s, soft -> 3 s returned and broadcast | modes: both | covered: tests/test_operator_actions.py
- [ ] B-QUEUE-28 ↳ timed item on air (time/weather/sponsor) is protected: 'Timed item plays to the end' | modes: both | covered: tests/test_rapid_next.py
- [ ] B-QUEUE-29 ↳ Next during an automatic change keeps the incoming song (no double skip) | modes: browser | covered: tests/test_queue_advance.py
- [ ] B-QUEUE-30 ↳ Next on last item refills queue and carries on; blackout -> no refill | modes: browser | covered: tests/test_queue_never_runs_dry.py
- [ ] B-QUEUE-31 ↳ skip ends loop; engine: flush + skip, engine down -> 503 'Nothing was skipped' | modes: engine | covered: tests/test_playout_engine.py
- [ ] B-QUEUE-32 ↳ PlayLog source=manual for skipped item; status=skipped | modes: both | covered: tests/test_playout_engine.py
- [ ] B-QUEUE-33 ↳ rapid repeated Next presses advance one item each, no skipped weather | modes: browser | covered: tests/test_rapid_next.py
- [ ] B-QUEUE-34 `POST /stations/{station_id}/queue/loop` [require_dj_or_manager] Loop on: the item on air plays again each time it ends. Off (or Next) ends it; | modes: both | covered: tests/test_operator_actions.py, tests/test_playout_engine.py
- [ ] B-QUEUE-35 ↳ loop on with nothing on air -> 409 | modes: both | covered: tests/test_operator_actions.py
- [ ] B-QUEUE-36 ↳ loop on: item re-queued after each end; Next ends the loop | modes: both | covered: tests/test_operator_actions.py
- [ ] B-QUEUE-37 ↳ engine: loop on/off flushes the pre-loaded track; engine plays same item again | modes: engine | covered: tests/test_playout_engine.py
- [ ] B-QUEUE-38 `POST /stations/{station_id}/queue/insert` [require_dj_or_manager] Insert assets, in order, right after a queue entry (or at the top). One call: | modes: both | covered: tests/test_operator_actions.py
- [ ] B-QUEUE-39 ↳ insert after a given entry, or at top when no anchor; multiple assets keep order | modes: both | covered: tests/test_operator_actions.py
- [ ] B-QUEUE-40 ↳ no asset ids -> 422; unknown asset -> 404 | modes: both | covered: tests/test_operator_actions.py
- [ ] B-QUEUE-41 ↳ engine: flush so engine re-asks | modes: engine | NOT COVERED
- [ ] B-QUEUE-42 `POST /stations/{station_id}/queue/move-up` [require_dj_or_manager] Move a queue entry up (lower position number). | modes: both | covered: tests/test_queue_endpoints.py::test_move_up_and_down_swap_with_neighbours
- [ ] B-QUEUE-43 ↳ swap with previous pending; top entry stays | modes: both | covered: tests/test_queue_endpoints.py::test_move_up_and_down_swap_with_neighbours, tests/test_queue_endpoints.py::test_entries_of_another_station_cannot_be_changed_through_this_station (cross-station, fixed)
- [ ] B-QUEUE-44 `POST /stations/{station_id}/queue/move-down` [require_dj_or_manager] Move a queue entry down (higher position number). | modes: both | covered: tests/test_queue_endpoints.py::test_move_up_and_down_swap_with_neighbours
- [ ] B-QUEUE-45 ↳ swap with next pending; last entry stays | modes: both | covered: tests/test_queue_endpoints.py::test_move_up_and_down_swap_with_neighbours
- [ ] B-QUEUE-46 `PUT /stations/{station_id}/queue/reorder` [require_dj_or_manager] reorder queue | modes: both | covered: tests/test_queue_endpoints.py::test_reorder_sets_the_position
- [ ] B-QUEUE-47 ↳ bulk reorder by ids; engine pre-load flushed by next tick when head changes | modes: both | covered: tests/test_queue_endpoints.py::test_reorder_sets_the_position (browser part)
- [ ] B-QUEUE-48 `POST /stations/{station_id}/queue/reorder-dnd` [require_dj_or_manager] Drag-and-drop reorder: move entry to new position with rule validation. | modes: both | covered: tests/test_queue_endpoints.py::test_drag_and_drop_moves_and_shifts_the_rest
- [ ] B-QUEUE-49 ↳ non-pending entry -> 404 | modes: both | covered: tests/test_queue_endpoints.py::test_drag_and_drop_moves_and_shifts_the_rest
- [ ] B-QUEUE-50 ↳ warnings for category transitions, rule conflicts and do_not_play | modes: both | covered: tests/test_queue_endpoints.py::test_drag_and_drop_warns_about_rules
- [ ] B-QUEUE-51 `DELETE /stations/{station_id}/queue/{entry_id}` [require_dj_or_manager] remove from queue | modes: both | covered: tests/test_activity_log.py, tests/test_operator_actions.py, tests/test_playout_engine.py
- [ ] B-QUEUE-52 ↳ remove pending entry; engine flush when it was the cued one | modes: both | covered: tests/test_playout_engine.py
- [ ] B-QUEUE-53 `POST /stations/{station_id}/queue/start` [require_dj_or_manager] start playback | modes: both | covered: tests/test_queue_never_runs_dry.py
- [ ] B-QUEUE-54 ↳ already playing -> 'Already playing', no second item started | modes: browser | covered: tests/test_one_player_per_station.py
- [ ] B-QUEUE-55 ↳ blackout active -> silence entries queued | modes: browser | covered: tests/test_queue_endpoints.py::test_start_during_a_blackout_queues_silence
- [ ] B-QUEUE-56 ↳ engine station -> 'Playout engine will start the next track' (no local start) | modes: engine | covered: tests/test_queue_endpoints.py::test_start_on_an_engine_station_starts_nothing_locally
- [ ] B-QUEUE-57 ↳ empty library -> 'Queue empty' | modes: browser | covered: tests/test_queue_never_runs_dry.py
- [ ] B-QUEUE-58 `POST /stations/{station_id}/queue/preview-weather` [require_staff] Generate (or return cached) weather + time TTS for preview — does NOT insert into queue. | modes: both | covered: tests/test_queue_endpoints.py::test_weather_triggers_with_clips, tests/test_queue_endpoints.py::test_weather_triggers_without_tts_say_so_and_insert_nothing
- [ ] B-QUEUE-59 ↳ returns cached time+weather audio for preview, inserts nothing | modes: n.a. | covered: tests/test_queue_endpoints.py::test_weather_triggers_with_clips
- [ ] B-QUEUE-60 `POST /stations/{station_id}/queue/insert-weather` [require_manager] Force-insert time announcement + weather spot into queue (admin trigger). | modes: both | covered: tests/test_queue_endpoints.py::test_weather_triggers_with_clips
- [ ] B-QUEUE-61 ↳ force-insert time + weather now (manager only) | modes: both | covered: tests/test_queue_endpoints.py::test_weather_triggers_with_clips
- [ ] B-QUEUE-62 `POST /stations/{station_id}/queue/schedule-hourly` [require_manager] Force-run hourly announcement scheduling for this station. | modes: both | covered: tests/test_queue_endpoints.py::test_schedule_hourly_is_manager_only_and_does_not_duplicate
- [ ] B-QUEUE-63 ↳ forces _schedule_hourly_announcements for station; no duplicates per hour | modes: both | covered: tests/test_queue_endpoints.py::test_schedule_hourly_with_the_real_scheduler_does_not_duplicate
- [ ] B-QUEUE-64 `POST /stations/{station_id}/queue/force-replenish` [require_manager] Force a full queue replenish for debugging. | modes: both | covered: tests/test_queue_endpoints.py::test_force_replenish_reports_counts
- [ ] B-QUEUE-65 ↳ full replenish runs, returns counts | modes: both | covered: tests/test_queue_endpoints.py::test_force_replenish_reports_counts

### rds.py (prefix `/stations`)
- [ ] B-RDS-1 `GET /stations/{station_id}/rds.txt` [PUBLIC] rds text | modes: both | covered: tests/test_rds.py
- [ ] B-RDS-2 ↳ plain text title for RDS encoder; unknown station 404 | modes: both | covered: tests/test_rds.py

### reviews.py (prefix `/reviews`)
- [ ] B-REVIEWS-1 `POST /reviews/queues` [require_manager] create queue | modes: n.a. | NOT COVERED
- [ ] B-REVIEWS-2 `GET /reviews/queues` [require_staff] list queues | modes: n.a. | covered: tests/test_role_access.py
- [ ] B-REVIEWS-3 `GET /reviews/queues/{queue_id}` [require_staff] get queue | modes: n.a. | NOT COVERED
- [ ] B-REVIEWS-4 `GET /reviews/queues/{queue_id}/items` [require_staff] list queue items | modes: n.a. | NOT COVERED
- [ ] B-REVIEWS-5 `GET /reviews/queues/{queue_id}/next` [require_staff] Get the next unreviewed item and assign to current user. | modes: n.a. | NOT COVERED
- [ ] B-REVIEWS-6 `PATCH /reviews/items/{item_id}` [require_staff] update item | modes: n.a. | NOT COVERED
- [ ] B-REVIEWS-7 `POST /reviews/queues/{queue_id}/batch-update` [require_manager] batch update | modes: n.a. | NOT COVERED
- [ ] B-REVIEWS-8 `GET /reviews/assets/{asset_id}/history` [require_staff] get asset history | modes: n.a. | NOT COVERED
- [ ] B-REVIEWS-9 `POST /reviews/assets/{asset_id}/comment` [require_staff] add comment | modes: n.a. | NOT COVERED
- [ ] B-REVIEWS-10 `GET /reviews/queues/{queue_id}/activity` [require_staff] get queue activity | modes: n.a. | NOT COVERED

### rules.py (prefix `/rules`)
- [ ] B-RULES-1 `GET /rules` [require_staff] list rules | modes: n.a. | covered: tests/test_role_access.py
- [ ] B-RULES-2 `POST /rules` [require_admin] create rule | modes: n.a. | NOT COVERED
- [ ] B-RULES-3 `PUT /rules/{rule_id}` [require_admin] update rule | modes: n.a. | NOT COVERED
- [ ] B-RULES-4 `DELETE /rules/{rule_id}` [require_admin] delete rule | modes: n.a. | NOT COVERED
- [ ] B-RULES-5 `GET /rules/preview` [require_staff] Generate a preview of what the schedule would look like for a given date. | modes: n.a. | NOT COVERED

### scheduler.py (prefix `/scheduler`)
- [ ] B-SCHEDULER-1 `POST /scheduler/start` [require_manager] Start the automatic scheduling engine. | modes: both | covered: tests/test_controls_streams_scheduler.py::test_scheduler_start_stop_status
- [ ] B-SCHEDULER-2 `POST /scheduler/stop` [require_manager] Stop the automatic scheduling engine. | modes: both | covered: tests/test_controls_streams_scheduler.py::test_scheduler_start_stop_status
- [ ] B-SCHEDULER-3 ↳ stopping scheduler halts browser-station advance; engine stations keep playing but timed hand-over stops | modes: both | covered: tests/test_controls_streams_scheduler.py::test_stopped_scheduler_no_longer_advances (browser part)
- [ ] B-SCHEDULER-4 `GET /scheduler/status` [PUBLIC] Get current scheduler status. | modes: both | covered: tests/test_controls_streams_scheduler.py::test_scheduler_start_stop_status

### schedules.py (prefix `/schedules`) — router guard: station_write_guard(schedule/block/entry, body winner/other ids)
- [ ] B-SCHEDULES-1 `GET /schedules/epg/{station_id}` [PUBLIC] Public EPG: returns all schedule blocks for a station on a given day, sorted by start_time. | modes: both | NOT COVERED
- [ ] B-SCHEDULES-2 ↳ public EPG for a date sorted by start; one-time blocks only on their date | modes: n.a. | NOT COVERED
- [ ] B-SCHEDULES-3 `POST /schedules` [require_manager] Create a new schedule for a station. | modes: both | covered: tests/test_schedules.py
- [ ] B-SCHEDULES-4 `GET /schedules` [PUBLIC] List all schedules, optionally filtered by station. | modes: both | covered: tests/test_schedules.py
- [ ] B-SCHEDULES-5 `POST /schedules/blocks` [require_manager] Create a new schedule block. | modes: both | covered: tests/test_block_waits_for_its_time.py, tests/test_schedule_refresh.py, tests/test_schedules.py
- [ ] B-SCHEDULES-6 ↳ creating/updating a block triggers refresh_station (unplayed automatic songs replaced) | modes: both | covered: tests/test_schedule_refresh.py
- [ ] B-SCHEDULES-7 ↳ sun-relative start (start_sun_event/offset) and start_date honoured | modes: both | covered: tests/test_schedule_lookahead.py
- [ ] B-SCHEDULES-8 `GET /schedules/blocks` [PUBLIC] List all schedule blocks, optionally filtered by schedule. | modes: both | covered: tests/test_block_waits_for_its_time.py, tests/test_schedule_refresh.py, tests/test_schedules.py
- [ ] B-SCHEDULES-9 `GET /schedules/blocks/{block_id}` [PUBLIC] Get a single schedule block by ID. | modes: both | covered: tests/test_schedule_lookahead.py, tests/test_schedules.py
- [ ] B-SCHEDULES-10 `PATCH /schedules/blocks/{block_id}` [require_manager] Update a schedule block. | modes: both | covered: tests/test_schedule_lookahead.py, tests/test_schedules.py
- [ ] B-SCHEDULES-11 ↳ block waits for its time; starts on time (realign 15 min before) | modes: both | covered: tests/test_block_waits_for_its_time.py
- [ ] B-SCHEDULES-12 `DELETE /schedules/blocks/{block_id}` [require_manager] Delete a schedule block. | modes: both | covered: tests/test_schedule_lookahead.py, tests/test_schedules.py
- [ ] B-SCHEDULES-13 `POST /schedules/blocks/{block_id}/set-prerecorded` [require_manager] Set a block to play a single pre-recorded show asset. | modes: both | NOT COVERED
- [ ] B-SCHEDULES-14 ↳ block plays a single pre-recorded show asset at its time | modes: both | NOT COVERED
- [ ] B-SCHEDULES-15 `POST /schedules/playlist-entries` [require_manager] Add an asset to a schedule block's playlist. | modes: both | NOT COVERED
- [ ] B-SCHEDULES-16 ↳ adds asset to block playlist; refresh_station queued | modes: both | NOT COVERED
- [ ] B-SCHEDULES-17 `GET /schedules/playlist-entries` [PUBLIC] List all playlist entries, optionally filtered by block. | modes: both | NOT COVERED
- [ ] B-SCHEDULES-18 `GET /schedules/playlist-entries/{entry_id}` [PUBLIC] Get a single playlist entry by ID. | modes: both | NOT COVERED
- [ ] B-SCHEDULES-19 `PATCH /schedules/playlist-entries/{entry_id}` [require_manager] Update a playlist entry. | modes: both | NOT COVERED
- [ ] B-SCHEDULES-20 `DELETE /schedules/playlist-entries/{entry_id}` [require_manager] Delete a playlist entry. | modes: both | NOT COVERED
- [ ] B-SCHEDULES-21 `GET /schedules/timeline-preview` [PUBLIC] Preview what block is active and blackout status at a given time. | modes: both | NOT COVERED
- [ ] B-SCHEDULES-22 ↳ active block + blackout flag at given time | modes: n.a. | NOT COVERED
- [ ] B-SCHEDULES-23 `GET /schedules/look-ahead` [require_manager] What collides over the coming days, how each collision is settled | modes: both | covered: tests/test_program_blocks.py, tests/test_schedule_lookahead.py, tests/test_schedule_refresh.py
- [ ] B-SCHEDULES-24 `POST /schedules/look-ahead/settle` [require_manager] Choose which of the overlapping blocks plays, by raising its priority above the others. | modes: both | covered: tests/test_schedule_lookahead.py
- [ ] B-SCHEDULES-25 ↳ raises winner priority above others; other station's block -> 403 | modes: n.a. | covered: tests/test_schedule_lookahead.py
- [ ] B-SCHEDULES-26 `GET /schedules/{schedule_id}` [PUBLIC] Get a single schedule by ID. | modes: both | covered: tests/test_block_waits_for_its_time.py, tests/test_program_blocks.py, tests/test_schedule_lookahead.py
- [ ] B-SCHEDULES-27 `PATCH /schedules/{schedule_id}` [require_manager] Update a schedule. | modes: both | covered: tests/test_block_waits_for_its_time.py, tests/test_program_blocks.py, tests/test_schedule_lookahead.py
- [ ] B-SCHEDULES-28 `DELETE /schedules/{schedule_id}` [require_manager] Delete a schedule. | modes: both | covered: tests/test_block_waits_for_its_time.py, tests/test_program_blocks.py, tests/test_schedule_lookahead.py

### site_release.py
- [ ] B-SITERELEAS-1 `GET /site-release` [PUBLIC] The release policy. Public: every tab needs it, and it holds nothing private. | modes: n.a. | covered: tests/test_site_release.py
- [ ] B-SITERELEAS-2 `PUT /site-release` [require_admin] set mode | modes: n.a. | covered: tests/test_site_release.py
- [ ] B-SITERELEAS-3 ↳ admin only; mode auto/manual broadcast to all tabs over /ws/site | modes: n.a. | covered: tests/test_site_release.py
- [ ] B-SITERELEAS-4 `POST /site-release/push` [require_admin] push release | modes: n.a. | covered: tests/test_site_release.py
- [ ] B-SITERELEAS-5 ↳ scheduled push at time; cancel push; tabs reload after their audio ends | modes: n.a. | covered: tests/test_site_release.py
- [ ] B-SITERELEAS-6 `DELETE /site-release/push` [require_admin] cancel push | modes: n.a. | covered: tests/test_site_release.py
- [ ] B-SITERELEAS-7 `GET /site-release/tabs` [require_admin] Every open tab of the site right now: its release, screen and whether it plays audio. | modes: n.a. | covered: tests/test_site_release.py
- [ ] B-SITERELEAS-8 `WEBSOCKET /ws/site` [PUBLIC] site socket | modes: n.a. | NOT COVERED
- [ ] B-SITERELEAS-9 ↳ tab registers with info (cleaned), receives release state; silent tab expires | modes: n.a. | NOT COVERED

### song_requests.py (prefix `/song-requests`)
- [ ] B-SONGREQUES-1 `POST /song-requests` [PUBLIC] Public: submit a song request with fuzzy matching and auto-approval. | modes: both | NOT COVERED
- [ ] B-SONGREQUES-2 ↳ fuzzy match to library asset; unmatched saved pending | modes: both | NOT COVERED
- [ ] B-SONGREQUES-3 ↳ auto-approve when asset allows and under max_requests_per_day -> queued | modes: both | NOT COVERED
- [ ] B-SONGREQUES-4 `GET /song-requests/analytics/top-requested` [require_manager] Admin: get most-requested songs grouped by matched asset. | modes: both | NOT COVERED
- [ ] B-SONGREQUES-5 `GET /song-requests` [require_manager] Admin: list song requests with optional filters. | modes: both | NOT COVERED
- [ ] B-SONGREQUES-6 `PATCH /song-requests/{request_id}` [require_manager] Admin: approve/reject/update a song request. | modes: both | NOT COVERED
- [ ] B-SONGREQUES-7 ↳ approving matched request adds asset to the station queue | modes: both | NOT COVERED
- [ ] B-SONGREQUES-8 `DELETE /song-requests/{request_id}` [require_manager] Admin: delete a song request. | modes: both | NOT COVERED
- [ ] B-SONGREQUES-9 `GET /song-requests/station/{station_id}/count` [PUBLIC] Public: count pending song requests for a station. | modes: both | NOT COVERED

### sponsor_portal.py (prefix `/sponsor-portal`)
- [ ] B-SPONSORPOR-1 `GET /sponsor-portal/spots` [require_sponsor] get my spots | modes: n.a. | covered: tests/test_spot_schedules.py
- [ ] B-SPONSORPOR-2 `GET /sponsor-portal/play-history` [require_sponsor] get play history | modes: n.a. | covered: tests/test_spot_schedules.py
- [ ] B-SPONSORPOR-3 `GET /sponsor-portal/upcoming-schedule` [require_sponsor] The sponsor's spots that are actually in the station queues, soonest first. | modes: n.a. | covered: tests/test_spot_schedules.py
- [ ] B-SPONSORPOR-4 ↳ only spots in station queues for this sponsor, soonest first | modes: both | covered: tests/test_spot_schedules.py
- [ ] B-SPONSORPOR-5 `GET /sponsor-portal/stats` [require_sponsor] get sponsor stats | modes: n.a. | covered: tests/test_spot_schedules.py

### sponsors.py (prefix `/sponsors`)
- [ ] B-SPONSORS-1 `GET /sponsors` [require_manager] list sponsors | modes: n.a. | covered: tests/test_program_blocks.py, tests/test_role_access.py, tests/test_sponsors.py
- [ ] B-SPONSORS-2 `POST /sponsors` [require_manager] create sponsor | modes: n.a. | covered: tests/test_program_blocks.py, tests/test_sponsors.py
- [ ] B-SPONSORS-3 `PUT /sponsors/{sponsor_id}` [require_manager] update sponsor | modes: n.a. | covered: tests/test_sponsors.py
- [ ] B-SPONSORS-4 `DELETE /sponsors/{sponsor_id}` [require_manager] delete sponsor | modes: n.a. | covered: tests/test_sponsors.py

### spot_recordings.py (prefix `/spot-recordings`)
- [ ] B-SPOTRECORD-1 `GET /spot-recordings` [require_manager] list spot recordings | modes: n.a. | covered: tests/test_spot_recordings.py
- [ ] B-SPOTRECORD-2 `POST /spot-recordings` [require_manager] create spot recording | modes: n.a. | covered: tests/test_spot_recordings.py
- [ ] B-SPOTRECORD-3 `PUT /spot-recordings/{recording_id}` [require_manager] update spot recording | modes: n.a. | NOT COVERED
- [ ] B-SPOTRECORD-4 `POST /spot-recordings/{recording_id}/generate` [require_manager] Make the audio from the script (Shprach when connected, placeholder until then). | modes: n.a. | covered: tests/test_spot_recordings.py
- [ ] B-SPOTRECORD-5 `POST /spot-recordings/{recording_id}/audio` [require_manager] upload audio | modes: n.a. | covered: tests/test_spot_recordings.py
- [ ] B-SPOTRECORD-6 `GET /spot-recordings/{recording_id}/audio` [require_manager] get audio | modes: n.a. | covered: tests/test_spot_recordings.py
- [ ] B-SPOTRECORD-7 `POST /spot-recordings/{recording_id}/approve` [require_manager] Put the spot in the library as this sponsor's audio so it can be scheduled. | modes: n.a. | covered: tests/test_spot_recordings.py
- [ ] B-SPOTRECORD-8 `POST /spot-recordings/{recording_id}/reject` [require_manager] reject spot recording | modes: n.a. | covered: tests/test_spot_recordings.py
- [ ] B-SPOTRECORD-9 `DELETE /spot-recordings/{recording_id}` [require_manager] delete spot recording | modes: n.a. | NOT COVERED

### spot_schedules.py (prefix `/spot-schedules`) — router guard: station_write_guard(schedule_id)
- [ ] B-SPOTSCHEDU-1 `GET /spot-schedules` [require_manager] list spot schedules | modes: both | covered: tests/test_program_blocks.py, tests/test_spot_recordings.py, tests/test_spot_schedules.py
- [ ] B-SPOTSCHEDU-2 `GET /spot-schedules/preview` [require_manager] Look ahead: every sponsor spot planned for the next N days on a station | modes: both | covered: tests/test_program_blocks.py, tests/test_spot_schedules.py
- [ ] B-SPOTSCHEDU-3 `GET /spot-schedules/timing-defaults` [require_manager] get timing defaults | modes: both | covered: tests/test_spot_schedules.py
- [ ] B-SPOTSCHEDU-4 `PUT /spot-schedules/timing-defaults` [require_manager] How a sponsor spot honours its time when its own schedule does not say. | modes: both | covered: tests/test_spot_schedules.py
- [ ] B-SPOTSCHEDU-5 ↳ station spot_timing default on_the_hour/other hard|soft | modes: both | covered: tests/test_spot_schedules.py
- [ ] B-SPOTSCHEDU-6 `POST /spot-schedules` [require_manager] create spot schedule | modes: both | covered: tests/test_program_blocks.py, tests/test_spot_recordings.py, tests/test_spot_schedules.py
- [ ] B-SPOTSCHEDU-7 ↳ pending ad_slot entries reconciled for station; start_date > end_date -> 422 | modes: both | covered: tests/test_spot_schedules.py
- [ ] B-SPOTSCHEDU-8 ↳ audio of another sponsor -> 422 | modes: n.a. | covered: tests/test_spot_schedules.py
- [ ] B-SPOTSCHEDU-9 `PUT /spot-schedules/{schedule_id}` [require_manager] update spot schedule | modes: both | covered: tests/test_program_blocks.py, tests/test_spot_schedules.py
- [ ] B-SPOTSCHEDU-10 `DELETE /spot-schedules/{schedule_id}` [require_manager] delete spot schedule | modes: both | covered: tests/test_program_blocks.py, tests/test_spot_schedules.py

### stations.py (prefix `/stations`)
- [ ] B-STATIONS-1 `POST /stations` [require_manager] create | modes: n.a. | covered: tests/test_activity_log.py, tests/test_bug_reports.py, tests/test_queue.py
- [ ] B-STATIONS-2 ↳ manager+ only; dj/viewer/sponsor 403 | modes: n.a. | NOT COVERED
- [ ] B-STATIONS-3 ↳ timezone, lat/lon, automation_config saved; new station gets queue on next replenish | modes: both | NOT COVERED
- [ ] B-STATIONS-4 `GET /stations` [PUBLIC] list all | modes: n.a. | covered: tests/test_activity_log.py, tests/test_bug_reports.py, tests/test_queue.py
- [ ] B-STATIONS-5 `GET /stations/geocode` [require_manager] Search for cities by name using OpenStreetMap Nominatim. Returns up to 5 results. | modes: n.a. | NOT COVERED
- [ ] B-STATIONS-6 ↳ Nominatim lookup returns <=5 results; upstream failure handled | modes: n.a. | NOT COVERED
- [ ] B-STATIONS-7 `GET /stations/{station_id}` [PUBLIC] get one | modes: n.a. | covered: tests/test_activity_log.py, tests/test_station_permissions.py, tests/test_stations.py
- [ ] B-STATIONS-8 `PUT /stations/{station_id}` [require_manager] update | modes: n.a. | covered: tests/test_activity_log.py, tests/test_station_permissions.py, tests/test_stations.py
- [ ] B-STATIONS-9 ↳ non-admin cannot toggle `protected` (403) | modes: n.a. | covered: tests/test_station_permissions.py
- [ ] B-STATIONS-10 ↳ manager not assigned to station gets 403 | modes: n.a. | covered: tests/test_station_permissions.py
- [ ] B-STATIONS-11 ↳ changing automation_config (weather_region, spot_timing, timed_during_long_items...) takes effect at next replenish | modes: both | NOT COVERED
- [ ] B-STATIONS-12 ↳ changing timezone moves hourly time clips to the new local hour (_drop_wrong_hour_announcements) | modes: both | NOT COVERED
- [ ] B-STATIONS-13 `DELETE /stations/{station_id}` [require_manager] delete | modes: n.a. | covered: tests/test_activity_log.py, tests/test_station_permissions.py, tests/test_stations.py
- [ ] B-STATIONS-14 ↳ protected station: only admin may delete | modes: n.a. | covered: tests/test_station_permissions.py
- [ ] B-STATIONS-15 ↳ delete cascades queue/schedules/now-playing without orphan errors in scheduler loop | modes: both | NOT COVERED

### streams.py (prefix `/stations`)
- [ ] B-STREAMS-1 `GET /stations/{station_id}/stream` [PUBLIC] stream info | modes: both | covered: tests/test_stations.py
- [ ] B-STREAMS-2 ↳ stream_url only when PLAYOUT_STREAM_LIVE and engine drives station; else null (browser) | modes: both | covered: tests/test_playout_engine.py
- [ ] B-STREAMS-3 `GET /stations/{station_id}/now-playing` [PUBLIC] now playing | modes: both | covered (indirect, calls handler): tests/test_items_without_length.py, tests/test_next_shown_is_next_played.py
- [ ] B-STREAMS-4 ↳ returns current queue item + next (predict_next_entry) for listener pre-load | modes: both | covered: tests/test_next_shown_is_next_played.py
- [ ] B-STREAMS-5 `GET /stations/{station_id}/live-audio` [PUBLIC] Public endpoint: returns audio URL + timing for the currently playing track. | modes: both | covered: tests/test_controls_streams_scheduler.py::test_live_audio_gives_url_timing_and_next, tests/test_controls_streams_scheduler.py::test_live_audio_nothing_playing
- [ ] B-STREAMS-6 ↳ audio URL + elapsed timing for current track; nothing playing -> empty; liquidsoap branch when LIQUIDSOAP_ENABLED | modes: browser | covered: tests/test_controls_streams_scheduler.py::test_live_audio_storage_paths_and_liquidsoap_branch (next-item URL 500, fixed)

### users.py (prefix `/users`)
- [ ] B-USERS-1 `GET /users/public/hosts` [PUBLIC] Public: list all hosts/DJs with public profiles. | modes: n.a. | NOT COVERED
- [ ] B-USERS-2 `GET /users` [require_admin] list users | modes: n.a. | covered: tests/test_activity_log.py, tests/test_role_access.py
- [ ] B-USERS-3 `POST /users` [require_admin] create user | modes: n.a. | covered: tests/test_activity_log.py
- [ ] B-USERS-4 ↳ admin creates user with role and station_ids | modes: n.a. | covered: tests/test_station_permissions.py
- [ ] B-USERS-5 `PUT /users/{user_id}` [require_admin] update user | modes: n.a. | covered: tests/test_auth.py, tests/test_station_permissions.py
- [ ] B-USERS-6 ↳ cannot disable own account / remove own admin role -> 400 | modes: n.a. | NOT COVERED
- [ ] B-USERS-7 `DELETE /users/{user_id}` [require_admin] delete user | modes: n.a. | covered: tests/test_auth.py, tests/test_station_permissions.py
- [ ] B-USERS-8 ↳ cannot delete yourself -> 400 | modes: n.a. | NOT COVERED
- [ ] B-USERS-9 `GET /users/audit-log` [require_admin] Admin: view persistent audit log of all write actions. | modes: n.a. | NOT COVERED
- [ ] B-USERS-10 `GET /users/me/preferences` [get_current_user] get my preferences | modes: n.a. | NOT COVERED
- [ ] B-USERS-11 `PATCH /users/me/preferences` [get_current_user] update my preferences | modes: n.a. | NOT COVERED

### weather_readouts.py (prefix `/weather-readouts`)
- [ ] B-WEATHERREA-1 `GET /weather-readouts` [require_staff] list readouts | modes: both | NOT COVERED
- [ ] B-WEATHERREA-2 `POST /weather-readouts` [require_manager] create readout | modes: both | NOT COVERED
- [ ] B-WEATHERREA-3 `GET /weather-readouts/template-preview` [require_staff] template preview | modes: both | NOT COVERED
- [ ] B-WEATHERREA-4 `GET /weather-readouts/{readout_id}` [require_staff] get readout | modes: both | NOT COVERED
- [ ] B-WEATHERREA-5 `PATCH /weather-readouts/{readout_id}` [require_manager] update readout | modes: both | NOT COVERED
- [ ] B-WEATHERREA-6 `POST /weather-readouts/{readout_id}/regenerate` [require_manager] regenerate readout | modes: both | NOT COVERED
- [ ] B-WEATHERREA-7 `POST /weather-readouts/{readout_id}/queue` [require_manager] queue readout | modes: both | NOT COVERED
- [ ] B-WEATHERREA-8 ↳ queues readout into station queue | modes: both | NOT COVERED
- [ ] B-WEATHERREA-9 `DELETE /weather-readouts/{readout_id}` [require_manager] delete readout | modes: both | NOT COVERED

### weather_spots.py (prefix `/weather-spots`)
- [ ] B-WEATHERSPO-1 `GET /weather-spots/{spot_date}` [require_manager] The weather spot stations of this region will play on this date (404 if none exists yet). | modes: both | covered: tests/test_weather_regions.py, tests/test_weather_spots.py
- [ ] B-WEATHERSPO-2 `PUT /weather-spots/{spot_date}` [require_manager] Upload a finished recording as the weather spot for a date (the region's local date, YYYY-MM-DD). | modes: both | covered: tests/test_weather_regions.py, tests/test_weather_spots.py
- [ ] B-WEATHERSPO-3 ↳ upload per region (lowercase) ; empty -> 422; too large -> 413; bad date -> 422 | modes: n.a. | covered: tests/test_weather_spots.py
- [ ] B-WEATHERSPO-4 ↳ uploaded spot replaces ElevenLabs fallback in queued weather for that date | modes: both | covered: tests/test_weather_regions.py
- [ ] B-WEATHERSPO-5 `GET /time-announcements` [require_manager] Hourly time clips (per region) | modes: n.a. | covered: tests/test_time_clip_regions.py, tests/test_weather_spots.py
- [ ] B-WEATHERSPO-6 ↳ lists hourly clips per region | modes: n.a. | covered: tests/test_time_clip_regions.py
- [ ] B-WEATHERSPO-7 `PUT /time-announcements/{hour}` [require_manager] Upload a ready-made clip for an hour (0-23) | modes: n.a. | covered: tests/test_time_clip_regions.py, tests/test_weather_spots.py
- [ ] B-WEATHERSPO-8 ↳ hour outside 0-23 -> 422; uploaded clip used for that hour, per region | modes: n.a. | covered: tests/test_weather_spots.py
- [ ] B-WEATHERSPO-9 ↳ re-upload realigns already queued time clips (_realign_time_clips) | modes: both | covered: tests/test_time_clip_regions.py

### websocket.py
- [ ] B-WEBSOCKET-1 `WEBSOCKET /ws/alerts` [PUBLIC] WebSocket endpoint for real-time alert notifications to admin clients. | modes: both | NOT COVERED
- [ ] B-WEBSOCKET-2 `WEBSOCKET /ws/now-playing/{station_id}` [PUBLIC] websocket now playing | modes: both | NOT COVERED
- [ ] B-WEBSOCKET-3 ↳ receives now_playing on every change (incl. engine started reports), stream_url field | modes: both | covered: tests/test_now_playing_broadcast.py
- [ ] B-WEBSOCKET-4 ↳ mix_over and fade_seconds messages delivered | modes: browser | NOT COVERED
## 2. Background jobs and periodic behaviour

### 2a. Scheduler loop (`services/scheduler_engine.py`, every 3 s, leader-only)
- [ ] B-JOB-1 Scheduler takes a 15 s DB lease; only the leader process advances stations; second process stays idle | modes: both | covered: tests/test_scheduler_lease.py
- [ ] B-JOB-2 Lease handover when leader dies: follower takes over within ~15 s, no double advance | modes: both | covered: tests/test_scheduler_lease.py
- [ ] B-JOB-3 Each pass uses its own session and ends its transaction (no "idle in transaction" blocking deploy migrations) | modes: both | covered: tests/test_scheduler_jobs.py::test_each_pass_ends_its_transaction
- [ ] B-JOB-4 One station's exception rolls back, raises critical "Station check failed" alert, other stations still advance in the same pass | modes: both | covered: tests/test_scheduler_jobs.py::test_one_station_failing_does_not_stop_the_others
- [ ] B-JOB-5 Browser station: `_advance_queue` -> `_check_advance` advances when duration elapsed (minus crossfade) | modes: browser | covered: tests/test_queue_advance.py
- [ ] B-JOB-6 Browser: precise timer armed at cross_start (default 3 s before end; short/timed items 0.3 s) so server switch = client crossfade start | modes: browser | covered: tests/test_queue_advance.py, tests/test_song_change_gaps.py
- [ ] B-JOB-7 Browser: poll does not advance before the end; precise timer does not advance too early | modes: browser | covered: tests/test_queue_advance.py
- [ ] B-JOB-8 Browser: two concurrent advances (timer + poll + Next) promote only one entry (advance_lock) | modes: browser | covered: tests/test_queue_advance.py
- [ ] B-JOB-9 Browser: duplicate "playing" rows cleaned to one (most recent) | modes: browser | covered: tests/test_scheduler_jobs.py::test_duplicate_playing_rows_are_cleaned_to_the_newest
- [ ] B-JOB-10 Browser: playing entry with no started_at gets started_at=now | modes: browser | covered: tests/test_scheduler_jobs.py::test_playing_entry_without_start_time_gets_now
- [ ] B-JOB-11 Browser: `_arm_timed_start` arms an exact timer for a hard timed item due within 10 s (top-of-hour time check starts on the second, not up to 3 s late) | modes: browser | covered: tests/test_timed_start_on_time.py
- [ ] B-JOB-12 Browser: soft timed item with hard_by fires when its wait runs out; without hard_by waits for the song end | modes: browser | covered: tests/test_timed_start_on_time.py
- [ ] B-JOB-13 Browser: hard timed item due (preempt_at <= now) cuts the current song exactly at its time; current logged and marked played | modes: browser | covered: tests/test_timed_start_on_time.py, tests/test_top_of_hour_block.py
- [ ] B-JOB-14 Browser: time check then weather (same timed group) - weather follows time with no song in between, does not cut it | modes: browser | covered: tests/test_top_of_hour_block.py
- [ ] B-JOB-15 Browser: a time check queued twice for the same hour plays once (`_drop_timed_twins`) | modes: both | covered: tests/test_top_of_hour_block.py
- [ ] B-JOB-16 Browser: timed item due within TIMED_EARLY_START_SECONDS starts early when the song ends a few seconds before (no gap) | modes: browser | covered: tests/test_timed_item_starts_early.py
- [ ] B-JOB-17 END-TO-END: hourly time announcement starts at hh:00:00 (+/- 0.5 s) in a real running server with a song crossing the hour | modes: both | covered: tests/test_scheduler_jobs.py::test_time_check_starts_on_the_second_with_the_real_timers (browser, in process with real timers)
- [ ] B-JOB-18 END-TO-END: time announcement on the hour while a shiur plays, for each timed_during_long_items policy (pause/wait/cut) | modes: both | covered: tests/test_timed_pauses_shiur.py (browser only)
- [ ] B-JOB-19 Time announcement on the hour when the scheduler pass is slow / a deploy restarts the backend at :59:50 | modes: both | covered: tests/test_scheduler_jobs.py::test_time_check_after_a_restart_just_before_the_hour
- [ ] B-JOB-20 Time announcement when the hourly entry was created late (preempt_at already past when queued) - must still air immediately, not by queue position | modes: both | covered: tests/test_scheduler_jobs.py::test_time_check_queued_late_airs_at_once_not_by_position
- [ ] B-JOB-21 Browser: precise advance does nothing when the process lost leadership or station is engine-driven | modes: browser | covered: tests/test_scheduler_jobs.py::test_precise_advance_skipped_without_leadership
- [ ] B-JOB-22 Browser: blackout ended while silence plays -> silence stopped at once, remaining silence entries cleared, next real song starts | modes: browser | covered: tests/test_scheduler_jobs.py::test_blackout_ended_while_silence_plays
- [ ] B-JOB-23 Browser: queue ran out on advance -> `_refill_for_next` fills and continues (not during blackout) | modes: browser | covered: tests/test_queue_never_runs_dry.py
- [ ] B-JOB-24 After each automatic change positions are compacted (playing=1) and replenish runs in background after the broadcast | modes: browser | covered: tests/test_scheduler_jobs.py::test_positions_compacted_and_refill_after_the_change
- [ ] B-JOB-25 Now-playing WS payload sent on every change; unchanged message not resent; older song never announced after newer | modes: both | covered: tests/test_now_playing_broadcast.py
- [ ] B-JOB-26 Payload includes next entry = what the server will really pick at switch time (predict_next_entry) | modes: browser | covered: tests/test_next_shown_is_next_played.py, tests/test_next_follows_soft_slot.py
- [ ] B-JOB-27 Upcoming analysis requested for on-air and next item when analysis missing/old (ANALYSIS_VERSION) | modes: both | covered: tests/test_song_change_gaps.py
- [ ] B-JOB-28 Engine station: scheduler calls `playout_engine.tick` instead of advancing by duration | modes: engine | covered: tests/test_playout_engine.py
- [ ] B-JOB-29 Engine tick: hard-timed items handed to engine 45 s ahead, once each (timed_sent) | modes: engine | covered: tests/test_playout_engine.py
- [ ] B-JOB-30 Engine tick: hard-timed entry whose preempt_at already passed is never handed to the timed lane (falls back to position order) | modes: engine | NOT COVERED
- [ ] B-JOB-31 Engine tick: engine_timed failure is retried next tick while still in the 45 s window; alert if never delivered | modes: engine | NOT COVERED
- [ ] B-JOB-32 Engine tick: block items with same time are handed in queue order | modes: engine | covered: tests/test_playout_engine.py
- [ ] B-JOB-33 Engine tick: blackout ended with silence on air -> silence cleared, engine flush + skip | modes: engine | NOT COVERED
- [ ] B-JOB-34 Engine tick: queue head changed after engine pre-loaded -> engine_flush | modes: engine | covered: tests/test_playout_engine.py
- [ ] B-JOB-35 Engine tick: soft group's hard_by reached while song still on -> engine_skip once | modes: engine | covered: tests/test_playout_engine.py
- [ ] B-JOB-36 Engine tick watchdog: no engine contact > 120 s -> alert, repeated at most every 30 min | modes: engine | NOT COVERED
- [ ] B-JOB-37 Engine tick skipped during a live show (live_show_id set) - verify timed items due during the show are dropped/handled | modes: engine | NOT COVERED
- [ ] B-JOB-38 `_check_station`: live show active -> hard stop check only; stale live_show_id cleared and committed | modes: both | covered: tests/test_live_show_lifecycle.py
- [ ] B-JOB-39 Live show reaches scheduled_end -> hard_stop_show committed once (not repeated every pass), WS show_hard_stopped broadcast | modes: both | covered: tests/test_live_show_lifecycle.py
- [ ] B-JOB-40 Blackout check: HolidayWindow is_blackout covering now; affected_stations null = all stations, else listed ids only | modes: both | covered: tests/test_scheduler_jobs.py::test_blackout_covers_listed_stations_only
- [ ] B-JOB-41 `fill_blackout_queue` queues silence for active AND upcoming blackouts each pass (no duplicates) | modes: both | covered: tests/test_scheduler_jobs.py::test_silence_queued_for_upcoming_and_active_blackouts_once, tests/test_scheduler_jobs.py::test_active_blackout_silences_the_station_now
- [ ] B-JOB-42 Leftover now_playing rows (old second player) are deleted | modes: both | covered: tests/test_one_player_per_station.py
- [ ] B-JOB-43 Station check never plays anything beside the queue | modes: browser | covered: tests/test_one_player_per_station.py
- [ ] B-JOB-44 Dead air: nothing playing for silence_threshold_seconds (default SILENCE_DETECTION_SECONDS=30) -> one critical alert per spell + start_from_queue | modes: browser | covered: tests/test_one_player_per_station.py
- [ ] B-JOB-45 Dead air timer reset when something plays; not counted during blackout | modes: browser | covered: tests/test_scheduler_jobs.py::test_dead_air_timer_resets_and_ignores_blackouts
- [ ] B-JOB-46 Engine station: no backend dead-air recovery (engine emergency playlist instead) | modes: engine | NOT COVERED
- [ ] B-JOB-47 Daily: holiday coverage extended when < 365 days ahead or no window in next 7 days (`_maybe_extend_holidays`) | modes: both | covered: tests/test_scheduler_jobs.py::test_daily_holiday_extension_is_per_station (per station, fixed)
- [ ] B-JOB-48 Daily weather readout generated at station local generate_time (default 06:00) when weather_readout.enabled | modes: both | NOT COVERED
- [ ] B-JOB-49 Weather readout auto-queued at queue_time when weather_readout.auto_queue | modes: both | NOT COVERED
- [ ] B-JOB-50 Legacy Liquidsoap health check / push / skip only when LIQUIDSOAP_ENABLED (must stay off) | modes: browser | NOT COVERED

### 2b. Queue replenish (`services/queue_replenish_service.py`)
- [ ] B-JOB-51 requests_only station: fill from approved song requests / popular (popular_request_threshold, default 3) | modes: both | NOT COVERED
- [ ] B-JOB-52 Dated weather/time queued as ordinary songs are dropped (drop_dated_clips) | modes: both | covered: tests/test_schedule_refresh.py
- [ ] B-JOB-53 Station with block playlists fills from them a few hours ahead even with generation off; >2x hours of old fill is replaced | modes: both | covered: tests/test_schedule_refresh.py
- [ ] B-JOB-54 15 min before a block's playlist takes over, automatic songs are redrawn so the block starts on time (realign_before_block_change) | modes: both | covered: tests/test_block_on_time.py, tests/test_schedule_refresh.py
- [ ] B-JOB-55 Hourly time + weather scheduled for next 48 h at each hh:00 with preempt_at, one set per hour (exact-minute dedupe, ad_slot/block excluded) | modes: both | covered: tests/test_spot_schedules.py, tests/test_blocks_keep_hourly.py
- [ ] B-JOB-56 Hourly entries get timing_mode from spot_timing default (hard on the hour, soft elsewhere); legacy NULL timing_mode backfilled | modes: both | covered: tests/test_spot_schedules.py
- [ ] B-JOB-57 Half-hour weather at hh:30 only when weather_enabled and weather_interval_minutes == 30 | modes: both | covered: tests/test_blocks_keep_hourly.py
- [ ] B-JOB-58 No hourly announcements when Supabase storage is not configured (logs skip) | modes: both | covered: tests/test_queue_endpoints.py::test_no_hourly_announcements_without_storage
- [ ] B-JOB-59 Each station announces its own local hour (station timezone); wrong-hour clips removed after timezone change | modes: both | covered: tests/test_time_announcement.py (text only); tests/test_restart_holidays_timezones.py::test_wrong_hour_time_checks_are_replaced
- [ ] B-JOB-60 Weather and time clips realigned to the station's weather_region | modes: both | covered: tests/test_weather_regions.py, tests/test_time_clip_regions.py
- [ ] B-JOB-61 Uploaded time clip used for the hour; ElevenLabs only fallback (default region only); missing both -> no entry, no crash | modes: both | covered: tests/test_weather_spots.py
- [ ] B-JOB-62 ElevenLabs failure pauses generation (backoff) then resumes | modes: both | NOT COVERED
- [ ] B-JOB-63 Weather fetch cached per 15-min slot; OpenWeatherMap 429 handled | modes: both | covered: tests/test_weather_fetch_cache.py
- [ ] B-JOB-64 Program blocks replace built-in hourly time/weather once a station has any; blocks_keep_hourly keeps both (hour first, block after) | modes: both | covered: tests/test_blocks_keep_hourly.py
- [ ] B-JOB-65 Ad slots at ad_slot_minutes (default 15/30/45) soft-timed; legacy test sponsor placeholder off unless ad_slot_legacy_spot | modes: both | covered: tests/test_spot_schedules.py
- [ ] B-JOB-66 Placeholders and do_not_play items never auto-queued; skipped if found at play time | modes: both | covered: tests/test_placeholder_never_auto.py, tests/test_do_not_play_and_codec.py, tests/test_no_test_items_in_fill.py
- [ ] B-JOB-67 Playlist generation (playlist_generation.enabled): picks from block sequences, cursor saved, artist distance, min vote | modes: both | covered: tests/test_playlist_generation.py
- [ ] B-JOB-68 Generation finds no sequence -> ordinary music fill only as far as `hours` | modes: both | covered: tests/test_playlist_generation.py
- [ ] B-JOB-69 Rule-based 7-day fill: daypart, fixed_time, interval, rotation rules; time-aware across dayparts | modes: both | NOT COVERED
- [ ] B-JOB-70 Global sponsors inserted only for stations using global rules | modes: both | NOT COVERED
- [ ] B-JOB-71 oldies_only filter (oldies_min_years, default 10) | modes: both | NOT COVERED
- [ ] B-JOB-72 No rules -> random music fill; library empty -> queue_empty alert | modes: both | NOT COVERED
- [ ] B-JOB-73 Replenish skipped during blackout after advance; serialized per station (_refill_lock); slow refill (>5 s) logged | modes: both | NOT COVERED
- [ ] B-JOB-74 Quick successive playlist edits share one background rebuild | modes: both | covered: tests/test_listeners_live.py

### 2c. Schedule refresh / spots / blocks
- [ ] B-JOB-75 Schedule, block, playlist-entry or template change -> refresh_station replaces unplayed automatic songs; manual and timed items kept | modes: both | covered: tests/test_schedule_refresh.py
- [ ] B-JOB-76 Removing automatic songs rewinds the generation cursor by the number dropped per block | modes: both | NOT COVERED
- [ ] B-JOB-77 Spot planning: days, dayparts (morning/afternoon/evening/overnight), plays per daypart, date range, fixed times, two sponsors never share a slot | modes: both | covered: tests/test_spot_schedules.py
- [ ] B-JOB-78 Spot planning skips Shabbos/blackouts and DST spring-forward missing times | modes: both | covered: tests/test_spot_schedules.py
- [ ] B-JOB-79 reconcile_spot_slots keeps pending ad_slot entries in step; slots about to air left alone | modes: both | covered: tests/test_spot_schedules.py
- [ ] B-JOB-80 Program block expansion: items in order, hard/soft/soft-with-limit (hard_by), sponsor places, rotation by last played | modes: both | covered: tests/test_program_blocks.py
- [ ] B-JOB-81 A due slot deleted while about to play does not leave dead air | modes: both | covered: tests/test_due_slot_deleted.py
- [ ] B-JOB-82 7-day look-ahead: overlaps, gaps, off-air windows, unplaced spots, using the engine's resolver | modes: n.a. | covered: tests/test_schedule_lookahead.py
- [ ] B-JOB-83 detect_schedule_conflicts raises an alert when a new/edited block overlaps | modes: n.a. | NOT COVERED

### 2d. Long items (`services/long_items.py`) and resume
- [ ] B-JOB-84 policy pause (default): time check plays, shiur re-queued as resume@<seconds> and continues where it stopped | modes: browser | covered: tests/test_timed_pauses_shiur.py
- [ ] B-JOB-85 policy wait: hard timed items due before the shiur ends are deferred to its end (_defer_hard_until_end) | modes: browser | covered: tests/test_restart_holidays_timezones.py::test_wait_defers_only_items_due_before_the_shiur_ends, tests/test_timed_pauses_shiur.py::test_wait_lets_the_shiur_finish_first
- [ ] B-JOB-86 policy cut: time check cuts the shiur | modes: browser | covered: tests/test_restart_holidays_timezones.py::test_cut_ends_the_shiur_for_good, tests/test_timed_pauses_shiur.py::test_cut_keeps_the_old_behaviour
- [ ] B-JOB-87 Long-item policies in ENGINE mode (engine timed lane always cuts? resume supported?) | modes: engine | NOT COVERED

### 2e. Startup (`main.py` lifespan)
- [ ] B-JOB-88 ensure_tables + `_add_missing_columns` migrations run each in own transaction; failed ones retried every 30 s up to 40 times | modes: n.a. | covered: tests/test_safe_migrations.py
- [ ] B-JOB-89 migrations_to_run skips already-applied/unsafe statements (no lock storms at deploy) | modes: n.a. | covered: tests/test_safe_migrations.py
- [ ] B-JOB-90 Connection pool warmed (timeout 20 s) before serving | modes: n.a. | NOT COVERED
- [ ] B-JOB-91 Default categories and asset types seeded idempotently | modes: n.a. | NOT COVERED
- [ ] B-JOB-92 Dynamic "requested" category refreshed at startup | modes: n.a. | NOT COVERED
- [ ] B-JOB-93 Restart: song still within its duration keeps playing (not cut); its open PlayLog kept | modes: browser | covered: tests/test_cold_start.py
- [ ] B-JOB-94 Restart: only recent (STARTUP_OPEN_LOG_DAYS) open play logs closed | modes: both | covered: tests/test_cold_start.py
- [ ] B-JOB-95 Restart: finished playing entries marked played + logged, EXCEPT engine stations (engine reports itself) | modes: both | covered: tests/test_restart_holidays_timezones.py::test_restart_settles_finished_items_but_not_engine_stations
- [ ] B-JOB-96 Restart: every active station replenished; browser stations started via start_from_queue; engine stations not started locally | modes: both | covered: tests/test_items_without_length.py (partial)
- [ ] B-JOB-97 Restart with empty queue -> "Queue empty after restart" warning alert; recovered stale entries -> "Server restarted" info alert | modes: both | covered: tests/test_restart_holidays_timezones.py::test_restart_with_an_empty_queue_raises_a_warning (alert was never committed, fixed)
- [ ] B-JOB-98 Scheduler started after resume; stopped cleanly on shutdown (lease released) | modes: both | covered: tests/test_scheduler_lease.py
- [ ] B-JOB-99 Duration backfill runs once 60 s after start in background (limit 100) | modes: n.a. | covered: tests/test_items_without_length.py
- [ ] B-JOB-100 Concurrent indexes built in background without blocking | modes: n.a. | NOT COVERED
- [ ] B-JOB-101 Fallback HTTP middleware creates tables when lifespan unsupported (Vercel) | modes: n.a. | NOT COVERED

### 2f. Other background services
- [ ] B-JOB-102 last_played cache: one full read per station per process (and hourly), then incremental | modes: both | covered: tests/test_last_played_cache.py
- [ ] B-JOB-103 tail_analysis: song end/crossfade point from HTTP range reads (no full download) | modes: both | covered: tests/test_song_change_gaps.py
- [ ] B-JOB-104 Alert creation dedupes repeats; _notify_users dispatches SMS/WhatsApp/email per user preference | modes: n.a. | covered: tests/test_alert_repeats.py
- [ ] B-JOB-105 Bug report notify_loop: first check after 60 s, then every NOTIFY_EVERY_SECONDS; pushes "fix ready" to ntfy when labels/PR appear | modes: n.a. | covered: tests/test_bug_reports.py (check_github_once); notify_loop NOT COVERED
- [ ] B-JOB-106 send_ntfy disabled when NTFY_TOPIC empty; uses NTFY_SERVER | modes: n.a. | NOT COVERED
- [ ] B-JOB-107 Activity log middleware: records every write with user, station, objects changed, redacts secrets, device/IP/location; batched inserts | modes: n.a. | covered: tests/test_activity_log.py
- [ ] B-JOB-108 last_seen_at written at most once a minute per user | modes: n.a. | covered: tests/test_cold_start.py
- [ ] B-JOB-109 Site release: scheduled push fires at its time to all open tabs; tabs silent past TAB_SILENT_LIMIT dropped from list | modes: n.a. | covered: tests/test_site_release.py (API); timing NOT COVERED
- [ ] B-JOB-110 Listener sessions finalised on disconnect/leave; stale sessions (no heartbeat) expire from live count | modes: both | covered: tests/test_listeners_live.py
- [ ] B-JOB-111 Live show start pauses automation (_pause_automation) and end resumes it (_resume_automation) without a song restart | modes: both | covered: tests/test_live_show_on_air.py
- [ ] B-JOB-112 Host opening broadcast WS starts a scheduled show (start_if_scheduled) | modes: both | covered: tests/test_live_show_on_air.py
- [ ] B-JOB-113 Live show with no end gets an hour; show started late keeps planned length | modes: both | covered: tests/test_live_show_lifecycle.py
- [ ] B-JOB-114 Request "requested" category refresh endpoint and auto-approve daily caps | modes: both | NOT COVERED
- [ ] B-JOB-115 App logging/log buffer: logs configured, errors keep CORS headers, request id header | modes: n.a. | covered: tests/test_app_logging.py (log lines only; CORS-on-error and request id NOT COVERED)

## 3. Settings

### 3a. Station `automation_config` keys
- [ ] B-SET-1 `weather_enabled` (bool) - schedules weather after time check (scheduling + replenish) | modes: both | covered: tests/test_blocks_keep_hourly.py
- [ ] B-SET-2 `hourly_time_announcement` (bool) - enables hourly time check; off -> no time clips queued | modes: both | NOT COVERED (off branch)
- [ ] B-SET-3 `weather_interval_minutes` (default 30; 30 = extra :30 weather, other = hourly only) | modes: both | covered: tests/test_blocks_keep_hourly.py
- [ ] B-SET-4 `weather_region` (default lakewood; normalised lowercase) - which uploaded weather/time clips the station uses | modes: both | covered: tests/test_weather_regions.py, tests/test_time_clip_regions.py
- [ ] B-SET-5 `spot_timing` {on_the_hour, other} hard|soft - default timing of spots and of time/weather; on_the_hour=soft makes the time check wait for the song (NOT on the hour) | modes: both | covered: tests/test_spot_schedules.py
- [ ] B-SET-6 `timed_during_long_items` pause|wait|cut (default pause) | modes: both | covered: tests/test_timed_pauses_shiur.py (pause only)
- [ ] B-SET-7 `blocks_keep_hourly` (bool) | modes: both | covered: tests/test_blocks_keep_hourly.py
- [ ] B-SET-8 `ad_slot_minutes` (default [15,30,45]) - custom list honoured by replenish and spot planner | modes: both | NOT COVERED
- [ ] B-SET-9 `ad_slot_legacy_spot` (default false) + `ad_slot_asset_title` | modes: both | covered: tests/test_spot_schedules.py
- [ ] B-SET-10 `requests_only` (bool) | modes: both | NOT COVERED
- [ ] B-SET-11 `popular_request_threshold` (default 3) | modes: both | NOT COVERED
- [ ] B-SET-12 `oldies_only` + `oldies_min_years` (default 10) | modes: both | NOT COVERED
- [ ] B-SET-13 `silence_threshold_seconds` (default SILENCE_DETECTION_SECONDS) | modes: browser | NOT COVERED
- [ ] B-SET-14 `live_show_id` (set by live show start; cleared on end/hard stop/stale) | modes: both | covered: tests/test_live_show_lifecycle.py
- [ ] B-SET-15 `loop_asset_id` (set by /queue/loop) | modes: both | covered: tests/test_operator_actions.py
- [ ] B-SET-16 `playlist_generation` {enabled, hours, artist_distance, min_vote, cursor} | modes: both | covered: tests/test_playlist_generation.py
- [ ] B-SET-17 `weather_readout` {enabled, generate_time, auto_queue, queue_time, template, city_name, brand_name} | modes: both | NOT COVERED
- [ ] B-SET-18 `hourly_station_id` (scheduling._check_automation_insertions, legacy path) - verify still used or dead | modes: both | NOT COVERED
- [ ] B-SET-19 Station.timezone drives announced hour, dayparts, blackouts, spot clock | modes: both | covered: tests/test_spot_schedules.py (partial)
- [ ] B-SET-20 Station lat/lon drive sunrise/sunset blocks and Shabbos generation | modes: both | covered: tests/test_schedule_lookahead.py (sun blocks)
- [ ] B-SET-21 Station.is_active false -> scheduler ignores station, restart does not resume it | modes: both | NOT COVERED
- [ ] B-SET-22 Station.protected (see roles) | modes: n.a. | covered: tests/test_station_permissions.py

### 3b. Env flags (`app/config.py`) that change behaviour
- [ ] B-SET-23 PLAYOUT_ENGINE_ENABLED + PLAYOUT_ENGINE_STATION_ID(s) -> engine_drives(station) true only for listed stations | modes: engine | covered: tests/test_playout_engine.py
- [ ] B-SET-24 PLAYOUT_ENGINE_URL / engine_control_url per station; PLAYOUT_ENGINE_TOKEN both ways | modes: engine | covered: tests/test_playout_engine.py
- [ ] B-SET-25 PLAYOUT_STREAM_LIVE -> stream_url published, site plays engine stream | modes: engine | covered: tests/test_playout_status.py
- [ ] B-SET-26 LIQUIDSOAP_ENABLED (legacy telnet) must stay off; when on, skip/push calls made | modes: browser | NOT COVERED
- [ ] B-SET-27 SILENCE_DETECTION_SECONDS default 30 | modes: browser | NOT COVERED
- [ ] B-SET-28 ELEVENLABS_API_KEY/VOICE_ID unset -> no TTS fallback, uploaded clips only | modes: both | covered: tests/test_weather_spots.py
- [ ] B-SET-29 OPENWEATHERMAP_API_KEY unset -> weather disabled gracefully | modes: both | NOT COVERED
- [ ] B-SET-30 SUPABASE_URL/SERVICE_KEY unset -> uploads/local storage path; hourly announcements skipped | modes: both | NOT COVERED
- [ ] B-SET-31 ICECAST_* (icecast_enabled) -> /icecast start/stop usable | modes: n.a. | NOT COVERED
- [ ] B-SET-32 STRIPE_* (stripe_enabled) -> checkout/webhook enabled else refused | modes: n.a. | NOT COVERED
- [ ] B-SET-33 RESEND_API_KEY -> emails sent else skipped | modes: n.a. | NOT COVERED
- [ ] B-SET-34 ANTHROPIC_API_KEY -> AI email drafts and copy review via Claude; else keyword fallback | modes: n.a. | covered: tests/test_copy_review.py
- [ ] B-SET-35 TWILIO_* (twilio_enabled / twilio_voice_enabled) -> SMS alerts and phone call-ins | modes: n.a. | NOT COVERED
- [ ] B-SET-36 BUG_REPORTS_GITHUB_TOKEN / BUG_REPORTS_REPO -> mirror to GitHub | modes: n.a. | covered: tests/test_bug_reports.py
- [ ] B-SET-37 NTFY_TOPIC / NTFY_SERVER -> push notifications | modes: n.a. | NOT COVERED
- [ ] B-SET-38 SENTRY_DSN -> Sentry enabled | modes: n.a. | NOT COVERED
- [ ] B-SET-39 ADMIN_SEED_PASSWORD / ALLOW_DESTRUCTIVE_SEED / APP_ENV=production | modes: n.a. | covered: tests/test_auth.py
- [ ] B-SET-40 JWT expiry (30 min access, 7 day refresh) honoured | modes: n.a. | NOT COVERED
- [ ] B-SET-41 CORS_ORIGINS parsing (JSON array or CSV) and error responses still carry CORS | modes: n.a. | NOT COVERED
- [ ] B-SET-42 DATABASE_URL fix-up (postgres:// -> asyncpg), statement_cache_size=0 for pooler | modes: n.a. | NOT COVERED
- [ ] B-SET-43 REDIS_URL / CELERY_* empty -> workers disabled without errors | modes: n.a. | NOT COVERED
- [ ] B-SET-44 EMERGENCY_FALLBACK_CATEGORY still loads but is unused | modes: n.a. | NOT COVERED
- [ ] B-SET-45 BACKEND_PUBLIC_URL / LIVE_SHOW_HOLD_MUSIC_URL used in Twilio TwiML | modes: n.a. | NOT COVERED

## 4. Engine-vs-browser differences (each must be tested in the mode(s) listed)
- [ ] B-MODE-1 Who advances: browser = backend by duration + precise timers; engine = engine's own clock, backend never advances by duration | modes: both | covered: tests/test_queue_advance.py, tests/test_playout_engine.py
- [ ] B-MODE-2 Next (skip): browser changes queue + broadcasts fade_seconds; engine sends /skip with fade, real start reported back | modes: both | covered: tests/test_operator_actions.py, tests/test_playout_engine.py
- [ ] B-MODE-3 Next protection for a timed item on air, in both modes | modes: both | covered: tests/test_rapid_next.py (browser); engine NOT COVERED
- [ ] B-MODE-4 Fire-now: browser skip-to-entry under advance_lock; engine timed lane with 503 fallback | modes: both | covered: tests/test_operator_actions.py, tests/test_playout_engine.py
- [ ] B-MODE-5 Mix-over: browser WS broadcast to listeners; engine overlay lane | modes: both | covered: tests/test_operator_actions.py (browser); engine NOT COVERED
- [ ] B-MODE-6 Loop: browser re-queues at end; engine flush + re-ask | modes: both | covered: tests/test_operator_actions.py, tests/test_playout_engine.py
- [ ] B-MODE-7 Insert / reorder / delete: engine pre-loaded track flushed when head changes | modes: engine | covered: tests/test_playout_engine.py (tick flush); insert NOT COVERED
- [ ] B-MODE-8 Start: browser starts next; engine returns "engine will start" | modes: both | NOT COVERED (engine branch)
- [ ] B-MODE-9 Hourly time check exactly on the hour: browser via preempt + _arm_timed_start; engine via timed lane handed 45 s ahead (radio.liq schedule_timed) | modes: both | covered: tests/test_timed_start_on_time.py (browser), tests/test_playout_engine.py (hand-over); real-clock NOT COVERED
- [ ] B-MODE-10 Weather following time check in same group: browser same-group rule; engine timed group order (timed_group_at within 0.5 s) | modes: both | covered: tests/test_top_of_hour_block.py (browser); engine NOT COVERED
- [ ] B-MODE-11 Soft ad slots / soft blocks: browser waits for song end + hard_by; engine pick_next step 1 + _enforce_hard_by | modes: both | covered: tests/test_next_follows_soft_slot.py, tests/test_playout_engine.py
- [ ] B-MODE-12 Past-due hard item pick order differs: browser picks due timed items first; engine pick_next step 2 by position only | modes: both | NOT COVERED
- [ ] B-MODE-13 Long-item pause/wait/cut policy implemented in browser advance; engine behaviour undefined | modes: both | NOT COVERED (engine)
- [ ] B-MODE-14 Blackout end with silence on air: browser _check_advance; engine tick flush+skip | modes: both | NOT COVERED
- [ ] B-MODE-15 Dead air: browser backend restarts queue; engine falls to emergency playlist and reports start without entry id -> alert | modes: both | covered: tests/test_one_player_per_station.py, tests/test_playout_engine.py
- [ ] B-MODE-16 Restart: browser keeps current song, starts next; engine stations' entries untouched, engine re-hellos | modes: both | covered: tests/test_cold_start.py (browser); engine NOT COVERED
- [ ] B-MODE-17 Play logging: browser logs on advance (source scheduler/manual); engine logs on started report (>=1 s, skipped -> manual) | modes: both | covered: tests/test_playout_engine.py
- [ ] B-MODE-18 Now-playing stream_url null (browser) vs engine stream URL when PLAYOUT_STREAM_LIVE | modes: both | covered: tests/test_playout_engine.py
- [ ] B-MODE-19 Live show: browser _check_station returns early; engine tick returns early - what plays on the engine during the show? | modes: both | NOT COVERED (engine)
- [ ] B-MODE-20 Placeholders / do_not_play skipped before hand-over in both | modes: both | covered: tests/test_do_not_play_and_codec.py
- [ ] B-MODE-21 Schedule redraw deleting a handed entry: engine still shows it on air | modes: engine | covered: tests/test_playout_engine.py
- [ ] B-MODE-22 Multiple engine stations each with own control URL/state | modes: engine | covered: tests/test_playout_engine.py
- [ ] B-MODE-23 radio.liq: timed lane mutes main lane, interrupted song does not resume; Next = clean cut / fade; main crossfade 3 s | modes: engine | NOT COVERED (no Liquidsoap test)
- [ ] B-MODE-24 radio.liq: emergency dir playlist used when backend unreachable; reports lane=emergency | modes: engine | NOT COVERED
- [ ] B-MODE-25 radio.liq: /timed dedupes by entry id (last 50); control endpoints require token | modes: engine | NOT COVERED
- [ ] B-MODE-26 radio.liq: /next request retried on 204/timeout without dead air | modes: engine | NOT COVERED
- [ ] B-MODE-27 Many stations x many listeners switch songs together (load) | modes: browser | NOT COVERED (loadtests/multi_station.py, manual)

## 5. Roles and permissions
- [ ] B-ROLE-1 Role matrix GET: assets/categories/asset-types/rules/reviews/live-shows = staff; users = admin; sponsors/alerts = admin+manager; auth/me = all | modes: n.a. | covered: tests/test_role_access.py
- [ ] B-ROLE-2 Anonymous refused (401/403) on every protected GET in the matrix | modes: n.a. | covered: tests/test_role_access.py
- [ ] B-ROLE-3 Role matrix for WRITES (POST/PUT/PATCH/DELETE) per router guard: viewer cannot change anything; dj only queue/controls/live shows/now-playing | modes: n.a. | NOT COVERED
- [ ] B-ROLE-4 require_admin-only writes: rules create/update/delete, users CRUD, alert delete, live-show delete, icecast, site-release, bug status/resend, activity | modes: n.a. | NOT COVERED (partly site-release)
- [ ] B-ROLE-5 Sponsor cannot reach staff endpoints (library, queue, rules, reviews) | modes: n.a. | covered: tests/test_role_access.py
- [ ] B-ROLE-6 Sponsor isolation: sponsor sees only own campaigns, drafts, comments, invoices, spots, play history | modes: n.a. | covered: tests/test_spot_schedules.py (portal); campaigns/billing NOT COVERED
- [ ] B-ROLE-7 require_sponsor_or_manager: dj/viewer refused on campaigns/billing/copy-review | modes: n.a. | NOT COVERED
- [ ] B-ROLE-8 may_operate_station: admin always; others assigned stations only; station_ids=None -> all non-protected | modes: n.a. | covered: tests/test_station_permissions.py
- [ ] B-ROLE-9 Protected station needs explicit assignment even for manager | modes: n.a. | covered: tests/test_station_permissions.py
- [ ] B-ROLE-10 require_station_operator applies to all non-GET /stations/{id}/queue and /controls routes; GET left to route guards | modes: both | covered: tests/test_station_permissions.py
- [ ] B-ROLE-11 station_write_guard on schedules, playlists, spot-schedules, live-shows (path and body ids incl. winner/other block ids) | modes: n.a. | covered: tests/test_station_permissions.py (schedules, playlists); spot-schedules/live-shows NOT COVERED
- [ ] B-ROLE-12 Program blocks: not-assigned station -> 403 | modes: n.a. | NOT COVERED
- [ ] B-ROLE-13 Station update/delete: manager not assigned -> 403; only admin toggles/deletes protected | modes: n.a. | covered: tests/test_station_permissions.py
- [ ] B-ROLE-14 Admin cannot disable/demote/delete self | modes: n.a. | NOT COVERED
- [ ] B-ROLE-15 Inactive user token refused; access-vs-refresh token type enforced | modes: n.a. | covered: tests/test_auth.py
- [ ] B-ROLE-16 Public (no auth) endpoints work anonymously and leak nothing private: stations list/get, now-playing, stream, live-audio, EPG, schedules list/blocks/entries GET, archives, rss, hosts, song request submit/count, listeners heartbeat, channels list, scheduler status, playout-status, rds.txt, site-release, call-in, crm register/login/raffles | modes: n.a. | NOT COVERED
- [ ] B-ROLE-17 Unguarded writes to check for abuse: POST /auth/migrate, GET /api/v1/init, PUT/POST via live-shows twilio webhooks (signature?), /billing/webhook (Stripe signature) | modes: n.a. | NOT COVERED
- [ ] B-ROLE-18 GET /schedules, /schedules/blocks, /schedules/playlist-entries are public - confirm intended | modes: n.a. | NOT COVERED
- [ ] B-ROLE-19 Playout endpoints: X-Playout-Token required; wrong station 404 | modes: engine | covered: tests/test_playout_engine.py
- [ ] B-ROLE-20 CRM member auth (6-digit PIN token) separate from staff JWT; staff token refused on member routes and vice versa | modes: n.a. | NOT COVERED
- [ ] B-ROLE-21 WebSockets: /ws/alerts and /ws/live/{show}/events|audio require staff auth; /ws/now-playing public | modes: n.a. | NOT COVERED
- [ ] B-ROLE-22 Refused action returns 403 with "not allowed" detail the frontend ViewOnlyNotice shows | modes: n.a. | covered: tests/test_station_permissions.py (partial)
# Frontend test checklist (from frontend/src, 2026-11-17)

Format: id, behaviour | sizes | engine/browser player | existing browser test.

Existing browser tests: e2e/control-room.mjs (fake backend), e2e/audio-engine.mjs (harness, fake tones), e2e/web-call-in.cjs, e2e/site-release/check.cjs, e2e/admin-layout-audit.mjs (layout/no-errors only, every admin page, desktop + iPhone 13 + iPhone SE).

## Routes and roles (App.tsx + auth/access.ts)

- [ ] F-ACCESS-1 `/` redirects to `/stations` | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ACCESS-2 Anonymous visitor to any /admin/* route is sent to /admin/login | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ACCESS-3 Anonymous visitor to any /sponsor/* route (except /sponsor/login) is sent to /sponsor/login | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ACCESS-4 Signed-in user whose account lookup fails sees 'Could not reach the server' + Try again (not thrown to login); Try again refetches | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ACCESS-5 admin can open every /admin/* route (all 38 admin routes render, no error box) | sizes: both | n.a. | covered: e2e/admin-layout-audit.mjs (layout only)
- [ ] F-ACCESS-6 manager can open everything under /admin except /admin/users, /admin/site-updates, /admin/activity (redirected to /admin/dashboard) | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ACCESS-7 dj can open only dashboard, assets (not upload), reviews, schedules, rules, program-clock, live/*, studio; any other /admin path redirects to /admin/dashboard | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ACCESS-8 viewer can open dashboard, assets, reviews, schedules, rules, program-clock only; /admin/live and /admin/studio redirect | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ACCESS-9 sponsor opening any /admin/* path is redirected to /sponsor/dashboard; staff opening /sponsor/* redirected to /admin/dashboard | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ACCESS-10 canChange: viewer on /admin/dashboard sees 'View only' and transport/queue writes refused; dj may operate dashboard | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ACCESS-11 canChange: dj/viewer on /admin/assets, /admin/schedules, /admin/program-clock are view only; only admin may change /admin/rules | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ACCESS-12 Any 403 from the API shows the app-wide 'not allowed' notice (ViewOnlyNotice, FORBIDDEN_EVENT) and the control room shows no connection-error toast | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ACCESS-13 Station permissions: staff limited to named stations (User.station_ids) only see/operate those; protected station needs explicit assignment (control room actions refused otherwise) | sizes: both | both | covered: NOT COVERED
- [ ] F-ACCESS-14 Unknown route under Layout renders nothing broken (no crash) | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ACCESS-15 Route error boundary: a page that throws shows the error box with Reload; moving to another route clears it | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ACCESS-16 Lazy chunk missing after a new release (stale screen): page reloads onto the new release instead of showing an error | sizes: both | n.a. | covered: e2e/site-release/check.cjs

## Shared shell: Navbar, Layout, global player, bug reporter, site updates

- [ ] F-GLOBAL-1 Public navbar links: Stations, Program Guide, DJs, Archives navigate correctly | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-GLOBAL-2 Signed-in navbar shows Dashboard link; sponsor sees Sponsor Portal link | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-GLOBAL-3 Admin dropdown groups Content / Broadcast / Audience / Settings open and close; each of their ~27 links navigates and is hidden for roles that cannot open it | sizes: desktop | n.a. | covered: NOT COVERED
- [ ] F-GLOBAL-4 Alerts bell shows unresolved count; opens list; clicking an alert or 'View all' goes to /admin/alerts | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-GLOBAL-5 Logout button clears tokens and returns to login | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-GLOBAL-6 Phone: hamburger menu opens, every menu entry navigates and closes the menu, Logout in menu works | sizes: phone | n.a. | covered: NOT COVERED
- [ ] F-GLOBAL-7 Navbar stays at top when page scrolls; no sideways page scroll on every admin page | sizes: both | n.a. | covered: e2e/admin-layout-audit.mjs (layout only)
- [ ] F-GLOBAL-8 Admin pages fill the window width (no max-w wrapper) on desktop | sizes: desktop | n.a. | covered: e2e/admin-layout-audit.mjs (layout only)
- [ ] F-GLOBAL-9 App-shell routes (control room) render full-height without page scrollbars | sizes: both | n.a. | covered: e2e/control-room.mjs
- [ ] F-GLOBAL-10 Global AudioPlayer bar (playerStore): plays an asset, volume slider, Stop | sizes: both | browser | covered: NOT COVERED
- [ ] F-GLOBAL-11 PageViewTracker records page views on route change (activity log shows them) | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-GLOBAL-12 JWT refresh: an expired access token is refreshed automatically by the Axios interceptor without logging the user out | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-GLOBAL-13 Bug reporter: floating 🐞 button opens dialog; description + expected fields; screenshot checkbox; Send posts /bug-reports with breadcrumbs; Sending… state; success screen; Close/Cancel/× and backdrop close | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-GLOBAL-14 Bug reporter opens from the control room footer 'Report a bug' link (REPORT_BUG_EVENT) | sizes: desktop | n.a. | covered: NOT COVERED
- [ ] F-GLOBAL-15 Site release auto mode: idle tab with no audio reloads onto the new version | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-GLOBAL-16 Site release manual mode: no tab reloads on its own; tab reports waiting for admin push | sizes: both | n.a. | covered: e2e/site-release/check.cjs
- [ ] F-GLOBAL-17 Site release push now: silent tab reloads at once | sizes: both | n.a. | covered: e2e/site-release/check.cjs
- [ ] F-GLOBAL-18 Site release push without 'include playing tabs': listener keeps playing on old version | sizes: both | both | covered: e2e/site-release/check.cjs
- [ ] F-GLOBAL-19 Site release push including playing tabs: listener reloads right after the next item change and plays on (Chrome) / shows 'Tap to keep listening' (Safari) and one tap restores sound | sizes: both | both | covered: e2e/site-release/check.cjs
- [ ] F-GLOBAL-20 Scheduled push: no reload before its time, reload after | sizes: both | n.a. | covered: e2e/site-release/check.cjs
- [ ] F-GLOBAL-21 Release picked up on navigation to another screen (UpdateOnNavigation) | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-GLOBAL-22 Release never reloads a tab while the mic is live or the person is typing | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-GLOBAL-23 Release while control room is listening: control room comes back with sound on (useResumeAfterRelease) / ResumePrompt on Safari | sizes: both | both | covered: e2e/audio-engine.mjs (release hold only)
- [ ] F-GLOBAL-24 Release with real Safari / iPhone (not simulated): tap-to-resume prompt and audio resumes | sizes: phone | both | covered: NOT COVERED

## Admin login (/admin/login)

- [ ] F-LOGIN-1 Valid username/email + password logs in and lands on /admin/dashboard (homeFor role) | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-LOGIN-2 Sponsor credentials on admin login land on /sponsor/dashboard | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-LOGIN-3 Wrong password shows an error, no redirect; Processing… state while submitting | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-LOGIN-4 Enter key submits; already signed-in user visiting login is handled | sizes: both | n.a. | covered: NOT COVERED

## Control room: transport bar and header (/admin/dashboard)

- [ ] F-CR-TRANSPORT-1 Station switcher dropdown (shown when >1 station) changes station; choice remembered in localStorage radio_last_station across reloads | sizes: both | both | covered: NOT COVERED
- [ ] F-CR-TRANSPORT-2 Station switch: old station's now-playing socket closes, only the new station is heard/shown (no double audio) | sizes: both | both | covered: e2e/control-room.mjs + e2e/audio-engine.mjs
- [ ] F-CR-TRANSPORT-3 Station switch cancels a pending insert marker and day filter | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CR-TRANSPORT-4 Single station: name shown instead of dropdown | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CR-TRANSPORT-5 PLAYING indicator when on air; '▶ Start' button when stopped; Start calls /queue start; 'Starting…' state; empty queue message 'The queue is empty. Add something…' | sizes: both | both | covered: NOT COVERED
- [ ] F-CR-TRANSPORT-6 Remaining countdown counts down from server started_at/ends_at; turns red and pulses in the last 10 s | sizes: both | both | covered: NOT COVERED
- [ ] F-CR-TRANSPORT-7 Intro countdown shows while the intro runs and disappears after | sizes: both | both | covered: e2e/control-room.mjs
- [ ] F-CR-TRANSPORT-8 On air elapsed / total time display (hidden on phone) | sizes: desktop | both | covered: NOT COVERED
- [ ] F-CR-TRANSPORT-9 Now-playing title + type and Next title + type boxes; Next shows the server's next_entry_id (skips items timed for later); 'nothing queued' when empty | sizes: both | both | covered: NOT COVERED
- [ ] F-CR-TRANSPORT-10 Progress bar fills and turns red near the end | sizes: both | both | covered: NOT COVERED
- [ ] F-CR-TRANSPORT-11 Clock shows station timezone time, ticking each second | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CR-TRANSPORT-12 'Can't reach the station' / 'Loading the queue…' instead of 'Stopped' when queue fails to load | sizes: both | n.a. | covered: e2e/control-room.mjs
- [ ] F-CR-TRANSPORT-13 Go next (hard cut) button calls /queue/skip?fade=hard; disabled when stopped or busy (shows …) | sizes: both | both | covered: e2e/control-room.mjs
- [ ] F-CR-TRANSPORT-14 N key = Go next (hard); ignored with modifiers, while typing in an input, on key repeat, and inside the PFL deck | sizes: desktop | both | covered: e2e/control-room.mjs
- [ ] F-CR-TRANSPORT-15 Fade button = skip?fade=fade (0.5 s); Soft fade = skip?fade=soft (3 s); audible fade of those lengths | sizes: both | both | covered: e2e/control-room.mjs (request only)
- [ ] F-CR-TRANSPORT-16 Next pressed on a protected timed item (time/weather/sponsor) shows the 'always play to the end' toast and nothing changes | sizes: both | both | covered: NOT COVERED
- [ ] F-CR-TRANSPORT-17 Next pressed right after an automatic change shows 'just started, keeps playing' toast (kept_incoming) | sizes: both | both | covered: NOT COVERED
- [ ] F-CR-TRANSPORT-18 Next is heard at once in this browser before the server answers (playNextNow) and lands on the server's item | sizes: both | browser | covered: e2e/audio-engine.mjs
- [ ] F-CR-TRANSPORT-19 Loop toggles on (button lit, aria-pressed) / off via /queue/loop; on-toast and off-toast; disabled when stopped | sizes: both | both | covered: e2e/control-room.mjs
- [ ] F-CR-TRANSPORT-20 Loop on: the item really repeats at its end (server + engine), Next or play-now ends the loop | sizes: both | both | covered: NOT COVERED
- [ ] F-CR-TRANSPORT-21 Listen (🔊) button switches sound on in this browser; remembered across reloads (kbr-cr-listen) | sizes: both | browser | covered: e2e/control-room.mjs
- [ ] F-CR-TRANSPORT-22 Mute/Unmute button; mute remembered after reload; meters keep moving while muted | sizes: both | browser | covered: e2e/control-room.mjs
- [ ] F-CR-TRANSPORT-23 Monitor volume slider (visible at 2xl widths) changes level | sizes: desktop | browser | covered: NOT COVERED
- [ ] F-CR-TRANSPORT-24 Level meters (L/R) move without pressing Listen, at the real RMS level | sizes: desktop | browser | covered: e2e/control-room.mjs
- [ ] F-CR-TRANSPORT-25 Level meters move when the station plays through the server stream (engine mode) too | sizes: desktop | engine | covered: NOT COVERED
- [ ] F-CR-TRANSPORT-26 Only one control room tab makes sound: switching sound on in tab B silences tab A with message 'Sound moved to another control room tab' | sizes: desktop | browser | covered: NOT COVERED
- [ ] F-CR-TRANSPORT-27 Full screen button enters/exits fullscreen (desktop only) | sizes: desktop | n.a. | covered: NOT COVERED
- [ ] F-CR-TRANSPORT-28 LIVE: <show> badge appears when a live show is on and links to host console | sizes: desktop | n.a. | covered: NOT COVERED
- [ ] F-CR-TRANSPORT-29 Playout badge: 'Browser player' / 'Server stream' / 'not answering' / 'out of step' from /playout-status | sizes: both | both | covered: NOT COVERED
- [ ] F-CR-TRANSPORT-30 Engine mode: control room plays the stream_url through <audio> when listening, falls back to the browser player if the stream errors | sizes: both | engine | covered: NOT COVERED
- [ ] F-CR-TRANSPORT-31 Back from a hidden tab (visibilitychange) the queue is re-fetched at once | sizes: both | both | covered: NOT COVERED
- [ ] F-CR-TRANSPORT-32 A pushed track change (WS) refreshes queue and Played log at once, not at next poll | sizes: both | both | covered: e2e/control-room.mjs
- [ ] F-CR-TRANSPORT-33 Toasts: ok auto-hide 2.5 s, warn/error 6 s, ✕ dismisses | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CR-TRANSPORT-34 Footer shows shortcuts, station + timezone, user email + role | sizes: desktop | n.a. | covered: NOT COVERED
- [ ] F-CR-TRANSPORT-35 Time & weather preview button ('Preview time & weather') plays time then weather clips off air only; 'No weather announcement' toast when none; error toast on failure | sizes: both | n.a. | covered: NOT COVERED

## Control room: Up next queue

- [ ] F-CR-QUEUE-1 Queue rows show air time to the second, length, artist + title, type, last played | sizes: desktop | both | covered: e2e/control-room.mjs
- [ ] F-CR-QUEUE-2 Phone row layout: time, title (2 lines), artist · type, length; 66 px rows | sizes: phone | both | covered: e2e/control-room.mjs
- [ ] F-CR-QUEUE-3 Count label 'N items · total duration' and 'loaded of total' when partially loaded | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CR-QUEUE-4 Long queue: only first rows polled; scrolling to the end loads the rest; scrolling back to the top returns to first rows | sizes: both | n.a. | covered: e2e/control-room.mjs
- [ ] F-CR-QUEUE-5 Virtualised list: only rows near the screen drawn, scrollbar covers whole queue, correct rows mid-list and at end | sizes: both | n.a. | covered: e2e/control-room.mjs
- [ ] F-CR-QUEUE-6 Show day dropdown lists days in station tz; picking a day loads whole queue and filters; 'Nothing scheduled for that day'; back to All days | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CR-QUEUE-7 + Insert-here button arms insert marker ('Inserting here. Pick a song or button.'); Cancel and second click disarm | sizes: both | n.a. | covered: e2e/control-room.mjs
- [ ] F-CR-QUEUE-8 Esc cancels insert mode (and blurs an input) | sizes: desktop | n.a. | covered: e2e/control-room.mjs
- [ ] F-CR-QUEUE-9 Arming insert switches to the library pane/tab | sizes: both | n.a. | covered: e2e/control-room.mjs
- [ ] F-CR-QUEUE-10 Insert from library lands exactly before the marked row (/queue/insert after the row above) | sizes: both | both | covered: e2e/control-room.mjs
- [ ] F-CR-QUEUE-11 Insert from an instant button (cart) while armed inserts at the marker | sizes: both | both | covered: NOT COVERED
- [ ] F-CR-QUEUE-12 Insert when the marked row already started: plays next instead with warn toast | sizes: both | both | covered: NOT COVERED
- [ ] F-CR-QUEUE-13 Insert marker disappears when that row starts playing or is removed | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CR-QUEUE-14 Desktop hover quick actions on a row: ▲ move up, ▼ move down, ✕ remove (call queue move-up/move-down/remove) | sizes: desktop | both | covered: NOT COVERED
- [ ] F-CR-QUEUE-15 Phone: tap a row opens action bar (+ Insert before, ▲, ▼, Remove); tap again closes | sizes: phone | both | covered: e2e/control-room.mjs (insert only)
- [ ] F-CR-QUEUE-16 Drag a row onto another row reorders it (/queue reorder DnD); warnings shown as toast; the playing row is not draggable | sizes: desktop | both | covered: NOT COVERED
- [ ] F-CR-QUEUE-17 Optimistic placeholder row appears at once on next/end/insert; failure restores old queue and shows error toast | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CR-QUEUE-18 Playing row marked ▶, not selectable, no actions | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CR-QUEUE-19 Timed rows (preempt_at / ad_slot / block) highlighted; ⏰ exact time shown; SPONSOR badge on ad_slot; RESUMES m:ss badge on resume@ rows | sizes: both | both | covered: NOT COVERED
- [ ] F-CR-QUEUE-20 Items due at the same moment grouped with 'BLOCK · N ITEMS' badge | sizes: both | both | covered: NOT COVERED
- [ ] F-CR-QUEUE-21 Blackout silence rows show 'Scheduled silence: <name>' in red | sizes: both | both | covered: NOT COVERED
- [ ] F-CR-QUEUE-22 Hourly time announcement row appears with ⏰ at hh:00:00 and the projected air time of the rows around it matches | sizes: both | both | covered: NOT COVERED
- [ ] F-CR-QUEUE-23 Empty queue text 'The queue is empty. Add songs from the library, or press Start.' | sizes: both | n.a. | covered: NOT COVERED

## Control room: library (Songs tab)

- [ ] F-CR-LIBRARY-1 '/' key opens library and focuses search; typed characters not lost | sizes: desktop | n.a. | covered: e2e/control-room.mjs
- [ ] F-CR-LIBRARY-2 Search is server-side (/assets?search=) debounced 250 ms; stale results dimmed; '· searching…'; error text 'Could not load the library.' | sizes: both | n.a. | covered: e2e/control-room.mjs
- [ ] F-CR-LIBRARY-3 Clear search (✕) empties and refocuses | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CR-LIBRARY-4 Type tabs (all + each asset type) filter results | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CR-LIBRARY-5 Result count shown; 'Load more' adds 50 more | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CR-LIBRARY-6 Row 'Next' button queues right after current item (/queue next) | sizes: both | both | covered: e2e/control-room.mjs
- [ ] F-CR-LIBRARY-7 Row 'End' (add to end) button (/queue end) | sizes: both | both | covered: NOT COVERED
- [ ] F-CR-LIBRARY-8 Row Insert button shown while insert armed; insert banner with Cancel | sizes: both | both | covered: e2e/control-room.mjs
- [ ] F-CR-LIBRARY-9 ☆/★ pins/unpins a library item as an instant button on the current cart page | sizes: both | n.a. | covered: e2e/control-room.mjs
- [ ] F-CR-LIBRARY-10 Row pre-listen (PFL) button opens the PFL deck for that item; pressing again stops | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CR-LIBRARY-11 Buttons disabled while that item is being sent (busy) and when no station | sizes: both | n.a. | covered: NOT COVERED

## Control room: instant buttons / cart

- [ ] F-CR-CART-1 Hot button in Play-now mode puts it on air immediately with one /queue/fire-now request | sizes: both | both | covered: e2e/control-room.mjs
- [ ] F-CR-CART-2 Play-next mode: button waits for the current item | sizes: both | both | covered: e2e/control-room.mjs
- [ ] F-CR-CART-3 Mix-over mode: plays on top, on-air item and queue unchanged (/queue/mix-over) | sizes: both | both | covered: e2e/control-room.mjs
- [ ] F-CR-CART-4 Mix-over WS message is played on top by this browser's engine (kbr-mix-over) | sizes: both | browser | covered: e2e/control-room.mjs
- [ ] F-CR-CART-5 Mix-over heard on the server stream (engine mode) | sizes: both | engine | covered: NOT COVERED
- [ ] F-CR-CART-6 Fire mode choice remembered across reloads (kbr_ctl_fire_mode) | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CR-CART-7 Cart pages 1-10: each page separate; tabs show filled/empty titles | sizes: both | n.a. | covered: e2e/control-room.mjs
- [ ] F-CR-CART-8 Button sets: New set (prompt), Rename (prompt), Delete (confirm) work; switching sets restores buttons | sizes: both | n.a. | covered: e2e/control-room.mjs (new/switch only)
- [ ] F-CR-CART-9 Sets and pages survive reload (per station localStorage) | sizes: both | n.a. | covered: e2e/control-room.mjs
- [ ] F-CR-CART-10 Edit mode: ✕ removes a button from the page; buttons cannot fire while editing | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CR-CART-11 Jingles wall lists station IDs/sweepers automatically; empty hint when none | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CR-CART-12 Sponsor spots wall lists spots | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CR-CART-13 Empty page hint text | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CR-CART-14 PFL toggle: buttons pre-listen instead of airing; green banner with Done; nothing reaches the station | sizes: both | n.a. | covered: e2e/control-room.mjs
- [ ] F-CR-CART-15 Headphones output picker (setSinkId) lists devices, choice remembered; 'find outputs' button | sizes: desktop | n.a. | covered: NOT COVERED
- [ ] F-CR-CART-16 Desktop bottom strip layout (bar) vs phone Buttons tab grid | sizes: both | n.a. | covered: e2e/control-room.mjs

## Control room: pre-listen (PFL) deck

- [ ] F-CR-PFL-1 Deck opens with title, play/pause, elapsed and -remaining | sizes: both | n.a. | covered: e2e/control-room.mjs
- [ ] F-CR-PFL-2 Waveform drawn from the file (shimmer while loading, cached per asset) | sizes: both | n.a. | covered: e2e/control-room.mjs
- [ ] F-CR-PFL-3 Click/tap on the wave jumps there; drag moves playhead; hover shows time | sizes: both | n.a. | covered: e2e/control-room.mjs
- [ ] F-CR-PFL-4 Keys in deck: ←/→ 5 s, J/L 10 s, Space/K play-pause, Home start, End 10 s before end, 0-9 jump to tenths, Esc close | sizes: desktop | n.a. | covered: e2e/control-room.mjs (→, Space only)
- [ ] F-CR-PFL-5 Back 10 s / Forward 10 s buttons | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CR-PFL-6 START / INTRO / MIX / END markers shown; jump buttons (Start, Intro end, Mix -5 s, End -5 s) | sizes: both | n.a. | covered: e2e/control-room.mjs (Mix only)
- [ ] F-CR-PFL-7 Unplayable file (MP2 as .mp3): deck stays open with message, play disabled, close works | sizes: both | n.a. | covered: e2e/control-room.mjs
- [ ] F-CR-PFL-8 Network error: retry message, ▶ retries | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CR-PFL-9 Nothing sent to the station from the deck; N in deck never skips | sizes: both | n.a. | covered: e2e/control-room.mjs
- [ ] F-CR-PFL-10 Close (Esc / ✕) hides the deck | sizes: both | n.a. | covered: e2e/control-room.mjs
- [ ] F-CR-PFL-11 Compact deck on phone | sizes: phone | n.a. | covered: NOT COVERED
- [ ] F-CR-PFL-12 PFL audio goes to chosen headphones output, not the monitor | sizes: desktop | n.a. | covered: NOT COVERED

## Control room: Go live, Played, Schedule tabs and layout

- [ ] F-CR-SIDE-1 Desktop side tabs Songs / Go live / Played / Schedule switch the lower pane; choice remembered (kbr_ctl_side_tab); Go live tab turns red '● Live' during a show | sizes: desktop | n.a. | covered: e2e/control-room.mjs
- [ ] F-CR-SIDE-2 Phone bottom nav Queue / Buttons / Library / Go live / More; More shows Played + Schedule switch | sizes: phone | n.a. | covered: e2e/control-room.mjs
- [ ] F-CR-SIDE-3 Go live: type a show name + Go live now (Enter works) creates and starts a show | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CR-SIDE-4 Go live: scheduled shows listed with Start; Start disabled while another show is live | sizes: both | n.a. | covered: e2e/control-room.mjs (renders only)
- [ ] F-CR-SIDE-5 Go live: End show asks 'Yes, end the show' / cancel; ends the show | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CR-SIDE-6 During a live show the control room hears the host instead of music; music returns after the show | sizes: both | browser | covered: NOT COVERED
- [ ] F-CR-SIDE-7 Played log lists recently played items in station tz | sizes: both | n.a. | covered: e2e/control-room.mjs
- [ ] F-CR-SIDE-8 Schedule panel shows what is on now; datetime 'Check a time' previews another time; reset | sizes: both | n.a. | covered: e2e/control-room.mjs (renders only)
- [ ] F-CR-SIDE-9 Drag bar between Up next and lower pane resizes; kept after reload; double-click resets; ArrowUp/Down keyboard | sizes: desktop | n.a. | covered: e2e/control-room.mjs (drag + reload only)
- [ ] F-CR-SIDE-10 Desktop: fills window, no page scrollbars, header frozen while queue scrolls | sizes: desktop | n.a. | covered: e2e/control-room.mjs
- [ ] F-CR-SIDE-11 Phone (iPhone 13 and SE): fits screen, no sideways scroll, every button ≥ tap size, header frozen | sizes: phone | n.a. | covered: e2e/control-room.mjs
- [ ] F-CR-SIDE-12 Tablet width (768-1023 px) uses phone layout without breaking | sizes: phone | n.a. | covered: NOT COVERED
- [ ] F-CR-SIDE-13 No script errors through a full session | sizes: both | n.a. | covered: e2e/control-room.mjs

## Browser player engine (hooks/useAudioEngine.ts) and stream player (lib/liveStream.ts)

- [ ] F-PLAYER-1 Song-to-song crossfade at analysed cross_start / cue_out; never silent; outgoing gone within limit | sizes: both | browser | covered: e2e/audio-engine.mjs
- [ ] F-PLAYER-2 Player does not run ahead of the server at the crossfade point | sizes: both | browser | covered: e2e/audio-engine.mjs
- [ ] F-PLAYER-3 Next during a crossfade / just before / just after crossfade began / right after automatic advance | sizes: both | browser | covered: e2e/audio-engine.mjs
- [ ] F-PLAYER-4 Next with stale start time; next quick fade near the end | sizes: both | browser | covered: e2e/audio-engine.mjs
- [ ] F-PLAYER-5 Same item twice in a row (warm and cold) | sizes: both | browser | covered: e2e/audio-engine.mjs
- [ ] F-PLAYER-6 Next starts clean from cue_in, no seek on a fresh item (warm and cold) | sizes: both | browser | covered: e2e/audio-engine.mjs
- [ ] F-PLAYER-7 Stored length longer than file: no hole | sizes: both | browser | covered: e2e/audio-engine.mjs
- [ ] F-PLAYER-8 Hard-timed item (time announcement): song fades preempt_fade_ms before the due time; timed item starts on the second, no hole | sizes: both | browser | covered: e2e/audio-engine.mjs
- [ ] F-PLAYER-9 Soft timed item does not fade the song; soft item fades only at its hard_by limit; older-server ad_slot not faded | sizes: both | browser | covered: e2e/audio-engine.mjs
- [ ] F-PLAYER-10 Hourly time announcement heard at hh:00:00 (±1 s) end-to-end against a real backend + scheduler, in control room | sizes: both | browser | covered: NOT COVERED
- [ ] F-PLAYER-11 Hourly time announcement heard at hh:00:00 on the PUBLIC Listen page (note: Listen passes nextPreemptAt=null to the engine, so it has no pre-fade and switches only when the WS announcement arrives) | sizes: both | browser | covered: NOT COVERED
- [ ] F-PLAYER-12 Hourly time announcement on air at hh:00:00 on the server stream (engine mode) | sizes: both | engine | covered: NOT COVERED
- [ ] F-PLAYER-13 Weather spot at :30 (or station interval) heard on time; station ID jingle hourly | sizes: both | both | covered: NOT COVERED
- [ ] F-PLAYER-14 Cue in/out and replay gain applied | sizes: both | browser | covered: NOT COVERED
- [ ] F-PLAYER-15 Stereo files reach both speakers; mono file reaches both | sizes: both | browser | covered: e2e/audio-engine.mjs
- [ ] F-PLAYER-16 Station switch (and back, during crossfade, old crossfade time) leaves only the new station on air | sizes: both | browser | covered: e2e/audio-engine.mjs
- [ ] F-PLAYER-17 Safari: Listen pressed mid-item brings up the item on air at its position at once | sizes: both | browser | covered: e2e/audio-engine.mjs (Safari simulated)
- [ ] F-PLAYER-18 Listen from cold: one press heard at once | sizes: both | browser | covered: e2e/audio-engine.mjs
- [ ] F-PLAYER-19 Stop then Listen joins the station where it is now (not resume from pause point) | sizes: both | browser | covered: e2e/audio-engine.mjs
- [ ] F-PLAYER-20 Release hold: a tab with audio playing never reloads; silent control room does | sizes: both | browser | covered: e2e/audio-engine.mjs
- [ ] F-PLAYER-21 Stream mode: MSE playback starts on first data, kept near live; jumps forward after stall | sizes: both | engine | covered: NOT COVERED
- [ ] F-PLAYER-22 Stream mode: browsers without MSE for MP3 (Safari/iOS) use plain <audio> | sizes: phone | engine | covered: NOT COVERED
- [ ] F-PLAYER-23 Stream error falls back to the browser player | sizes: both | engine | covered: NOT COVERED
- [ ] F-PLAYER-24 WS now-playing reconnects automatically after drop; falls back to REST polling after repeated failures | sizes: both | both | covered: NOT COVERED
- [ ] F-PLAYER-25 Tab back from background > few seconds: re-reads now-playing and reconnects | sizes: both | both | covered: NOT COVERED
- [ ] F-PLAYER-26 Overdue item (shown past its end with no update) triggers re-read + reconnect (car / locked phone) | sizes: phone | both | covered: NOT COVERED
- [ ] F-PLAYER-27 Next item preloaded on the idle deck; not downloaded in stream mode | sizes: both | both | covered: NOT COVERED
- [ ] F-PLAYER-28 Network loss mid-song and recovery: player keeps going / rejoins at live position | sizes: phone | both | covered: NOT COVERED
- [ ] F-PLAYER-29 Real Safari / iOS device run of all player scenarios (simulated only today) | sizes: phone | both | covered: NOT COVERED

## Public Listen page (/listen/:stationId)

- [ ] F-LISTEN-1 Station header: name, type · timezone, description, playout badge; 'Station not found' for bad id; Loading… | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-LISTEN-2 Listen Live button starts sound inside the click (browser player or stream) | sizes: both | both | covered: e2e/audio-engine.mjs (harness)
- [ ] F-LISTEN-3 Stop button stops; Listen again joins live | sizes: both | both | covered: e2e/audio-engine.mjs (harness)
- [ ] F-LISTEN-4 On Air / Offline / Connecting… status pill; stream playing counts as On Air | sizes: both | both | covered: NOT COVERED
- [ ] F-LISTEN-5 Now Playing card: title, artist, album update on each change | sizes: both | both | covered: NOT COVERED
- [ ] F-LISTEN-6 'Nothing playing right now' when stopped | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-LISTEN-7 Mute button and volume slider (percent) control both stream and browser player | sizes: both | both | covered: NOT COVERED
- [ ] F-LISTEN-8 Lock screen / Media Session metadata (title, artist, station) shown and updated per item (CarPlay / Android Auto / iOS lock screen) | sizes: phone | both | covered: NOT COVERED
- [ ] F-LISTEN-9 Media Session play/pause/stop actions: pause stops, play rejoins live (not resume from pause); seek actions disabled | sizes: phone | both | covered: NOT COVERED
- [ ] F-LISTEN-10 Media Session playbackState follows Listen/Stop | sizes: phone | both | covered: NOT COVERED
- [ ] F-LISTEN-11 Preconnect link to stream origin added before Listen | sizes: both | engine | covered: NOT COVERED
- [ ] F-LISTEN-12 Listener heartbeat sent while listening (shows in /admin/listeners) | sizes: both | both | covered: NOT COVERED
- [ ] F-LISTEN-13 Live show banner 'LIVE: <title>' with description; 'Call in from here' link when calls enabled; polled every 15 s | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-LISTEN-14 During a live show the host (and on-air caller) is heard instead of music; music resumes with a fresh now-playing after | sizes: both | both | covered: e2e/web-call-in.cjs (host/caller heard)
- [ ] F-LISTEN-15 Song request: expand form; name + song required; artist + message optional; Submit posts /song-requests; success and error messages; Submitting… state | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-LISTEN-16 CRM: 6-digit PIN login (digits only, error on wrong length), register form (name required, phone, email), Logout | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-LISTEN-17 CRM: rate current song 1-5 stars, favorite toggle; profile counts refresh | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-LISTEN-18 CRM: active raffles listed; Enter Raffle → 'entered' state | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-LISTEN-19 Release while listening shows 'Tap to keep listening' on Safari and resumes | sizes: phone | both | covered: e2e/site-release/check.cjs
- [ ] F-LISTEN-20 Phone layout: buttons ≥ 44 px, no sideways scroll | sizes: phone | n.a. | covered: NOT COVERED
- [ ] F-LISTEN-21 Station switch: going from one /listen/A to /listen/B stops A | sizes: both | both | covered: NOT COVERED

## Web call-in page (/call/:stationId)

- [ ] F-CALLIN-1 No live show / calls closed: says so (polls active show) | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CALLIN-2 Name (required) + note; Call in asks for the microphone inside the click | sizes: both | n.a. | covered: e2e/web-call-in.cjs
- [ ] F-CALLIN-3 Mic permission denied shows an error | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CALLIN-4 Status text follows waiting → screening → approved → on_air (red pulse) | sizes: both | n.a. | covered: e2e/web-call-in.cjs
- [ ] F-CALLIN-5 Waiting caller hears the show (host), not on air | sizes: both | n.a. | covered: e2e/web-call-in.cjs
- [ ] F-CALLIN-6 On air: listeners hear host + caller; caller hears host not themselves | sizes: both | n.a. | covered: e2e/web-call-in.cjs
- [ ] F-CALLIN-7 Hang up ends the call; screener sees completed | sizes: both | n.a. | covered: e2e/web-call-in.cjs
- [ ] F-CALLIN-8 Rejected by screener shows rejected state | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CALLIN-9 Dropped connection reconnects and call is held | sizes: phone | n.a. | covered: NOT COVERED
- [ ] F-CALLIN-10 Mic level meter moves | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CALLIN-11 iPhone Safari call-in | sizes: phone | n.a. | covered: NOT COVERED

## Public station list (/stations)

- [ ] F-STLIST-1 Lists active stations with name/description; card links to /listen/:id | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-STLIST-2 Empty / loading states | sizes: both | n.a. | covered: NOT COVERED

## Public program guide (/guide)

- [ ] F-GUIDE-1 Station dropdown and date picker load /schedules/epg/:station for that date | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-GUIDE-2 Timeline shows blocks in station timezone; today default | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-GUIDE-3 Phone layout readable, no sideways scroll | sizes: phone | n.a. | covered: NOT COVERED

## Public DJs (/hosts)

- [ ] F-HOSTS-1 Public host profile cards (photo, bio, title, social links) for users with Public Profile on | sizes: both | n.a. | covered: NOT COVERED

## Public archives (/archives)

- [ ] F-PARCH-1 Station filter; only published archives shown | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-PARCH-2 Play overlay on a card plays the archive audio | sizes: both | browser | covered: NOT COVERED
- [ ] F-PARCH-3 Archives page after a stale release loads with no error box | sizes: both | n.a. | covered: e2e/site-release/check.cjs

## Admin stations (/admin/stations)

- [ ] F-STATIONS-1 Station list table with type, timezone, Active/Inactive | sizes: both | n.a. | covered: e2e/admin-layout-audit.mjs (layout only)
- [ ] F-STATIONS-2 New station form: city search (/stations/geocode) results, pick, Create Station | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-STATIONS-3 Delete station with confirm | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-STATIONS-4 Protected checkbox (admin only) toggles station.protected; non-admin sees badge only | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-STATIONS-5 Row expands: Location search + Save Location | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-STATIONS-6 Automation config: Hourly Station ID, Hourly Time Announcement, Weather Spots + interval (5-120, disabled when off), Save | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-STATIONS-7 'When the time check comes during a shiur or story': pause / wait / cut; saved and honoured on air | sizes: both | both | covered: NOT COVERED
- [ ] F-STATIONS-8 Turning Hourly Time Announcement off removes it from the queue; on adds it at :00 | sizes: both | both | covered: NOT COVERED

## Library (/admin/assets)

- [ ] F-ASSETS-1 Column filters: title, artist, album search; category, type; duration min/max ('e.g. 3:00'); Clear filters | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ASSETS-2 Page size select; pager prev/next/numbers | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ASSETS-3 Per-row Play/Stop preview | sizes: both | browser | covered: NOT COVERED
- [ ] F-ASSETS-4 Per-row download menu by format | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ASSETS-5 Per-row View → /admin/assets/:id; Delete | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ASSETS-6 Category badge dropdown changes category inline; Sponsor badge dropdown with search sets/clears sponsor | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ASSETS-7 Row checkboxes + select all; 'Select all N filtered' / 'Clear, keep only this page' | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ASSETS-8 Bulk: set category, download (format), auto-trim, create review queue (dialog with Close) | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ASSETS-9 Upload link only for managers; view-only for dj/viewer | sizes: both | n.a. | covered: NOT COVERED

## Asset upload (/admin/assets/upload)

- [ ] F-UPLOAD-1 Drag files onto drop zone or pick with file input; list shows each file | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-UPLOAD-2 Asset type, category, format, optional artist/album fields | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-UPLOAD-3 Remove one file, Clear All | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-UPLOAD-4 Upload All shows 'Uploading i of n…', results; link back to Assets | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-UPLOAD-5 Upload from iPhone Files/Photos | sizes: phone | n.a. | covered: NOT COVERED

## Asset detail (/admin/assets/:id)

- [ ] F-ASSETDETAIL-1 Metadata shown; release date and vote (0-100, saved on blur) update the asset | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ASSETDETAIL-2 Waveform player: Play/Pause, Stop, speed buttons, seek slider | sizes: both | browser | covered: NOT COVERED
- [ ] F-ASSETDETAIL-3 Preview controls: Play first N s, Play last N s, Save as default | sizes: both | browser | covered: NOT COVERED
- [ ] F-ASSETDETAIL-4 Silence detection: Detect, threshold sliders, region list, Play region, Trim region (confirm), Auto-clip, Undo trim / Restore original, Save | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ASSETDETAIL-5 Enhance panel: presets, Preview, Apply, AI Auto-Enhance, Undo, Save, link to full enhance page | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ASSETDETAIL-6 Audience detection: Detect Questions, sliders, segment list, Play segment / with context, Deselect | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ASSETDETAIL-7 Comment box posts a comment; history shows changes | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ASSETDETAIL-8 Back to Assets link | sizes: both | n.a. | covered: NOT COVERED

## Audio enhance (/admin/audio-enhance/:assetId)

- [ ] F-ENHANCE-1 Preset buttons, Custom filters (checkboxes, sliders) | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ENHANCE-2 Preview original / Preview enhanced | sizes: both | browser | covered: NOT COVERED
- [ ] F-ENHANCE-3 Apply Enhancement, AI Auto-Enhance, Undo Enhancement, Save; back link | sizes: both | n.a. | covered: NOT COVERED

## Review queues (/admin/reviews) and review flow (/admin/reviews/:id)

- [ ] F-REVIEWS-1 Queue list; empty state links to Assets | sizes: both | n.a. | covered: e2e/admin-layout-audit.mjs (layout only)
- [ ] F-REVIEWS-2 Flow: Approve / Reject / Flag / Skip with notes, advance to next | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-REVIEWS-3 Previous / Next navigation, item list jump | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-REVIEWS-4 Change category in flow; waveform, silence, enhance, audience, preview, comments, history panels | sizes: both | n.a. | covered: NOT COVERED

## Users (/admin/users, admin only)

- [ ] F-USERS-1 Create user: username/email, password, role (admin/manager/dj/viewer/sponsor), display name, phone, job title | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-USERS-2 Edit user (password blank keeps), Delete (confirm), Deactivate / Activate | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-USERS-3 Alert preferences: SMS/WhatsApp checkboxes, minimum severity | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-USERS-4 DJ/host profile: bio, photo URL, Public Profile, Twitter, Instagram, website (appears on /hosts) | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-USERS-5 Stations this person may operate: all / named stations / protected station ticks; enforced in control room | sizes: both | both | covered: NOT COVERED
- [ ] F-USERS-6 Last online / last action refresh every 5 s | sizes: both | n.a. | covered: NOT COVERED

## Rules (/admin/rules)

- [ ] F-RULES-1 Rule list; Edit; Delete (confirm); active toggle | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-RULES-2 New rule: type, asset type, station, numeric params, days of week buttons, date, active | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-RULES-3 Schedule preview toggle shows preview | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-RULES-4 Only admin can change; others view only | sizes: both | n.a. | covered: NOT COVERED

## Schedules (/admin/schedules)

- [ ] F-SCHED-1 Station filter; list | sizes: both | n.a. | covered: e2e/admin-layout-audit.mjs (layout only)
- [ ] F-SCHED-2 Create / Edit (station, priority number, active) / Cancel / Delete (confirm) | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-SCHED-3 Blocks button opens /admin/schedules/:id/blocks | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-SCHED-4 dj/viewer view only | sizes: both | n.a. | covered: NOT COVERED

## Schedule blocks (/admin/schedules/:id/blocks)

- [ ] F-BLOCKS-1 Back to Schedules | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-BLOCKS-2 New/Edit block: name, recurrence type, day toggles, presets, start/end time, sunrise/sunset events + offsets, date range, playlist template, playback mode, one-time | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-BLOCKS-3 Delete block (confirm) | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-BLOCKS-4 Add playlist entry from asset select; Remove entry; category badge | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-BLOCKS-5 Block change takes effect in the control room queue on time (schedule refresh) | sizes: both | both | covered: NOT COVERED

## Blackouts (/admin/holidays)

- [ ] F-HOLIDAYS-1 Filters: reason, status, station | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-HOLIDAYS-2 Add/Edit blackout: name, reason, start/end datetime, station chips, all stations, active | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-HOLIDAYS-3 Delete (confirm) | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-HOLIDAYS-4 Blackout window produces scheduled silence row in queue and silence on air | sizes: both | both | covered: NOT COVERED

## Sponsors (/admin/sponsors)

- [ ] F-SPONSORS-1 Add/Edit sponsor: name, audio path, insertion policy, numeric params; Delete (confirm) | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-SPONSORS-2 Link to sponsor portal login | sizes: both | n.a. | covered: NOT COVERED

## Spot schedule (/admin/spot-schedule)

- [ ] F-SPOTSCHED-1 New schedule: sponsor, station, spot search + Listen/Stop, Change spot, days, when (dayparts vs times), times add/remove, plays per daypart, date range, timing; Save schedule | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-SPOTSCHED-2 List: Listen, Turn on/off, Remove (confirm) | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-SPOTSCHED-3 Station selector + 7-day look-ahead; timing defaults per daypart | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-SPOTSCHED-4 Planned spots appear as SPONSOR rows in the control room queue | sizes: both | both | covered: NOT COVERED

## Record spots (/admin/spot-recordings)

- [ ] F-SPOTREC-1 New spot: sponsor, name, script; create | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-SPOTREC-2 Edit script / Save / Cancel; Make audio; Upload a recording (file); ▶ Listen | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-SPOTREC-3 Approve / Reject / Delete | sizes: both | n.a. | covered: NOT COVERED

## Program blocks (/admin/program-blocks)

- [ ] F-PROGBLOCKS-1 New block: name, station, timing (hard/soft/soft-with-limit), day buttons, minute of every hour, every hour button, times add/remove/clear all | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-PROGBLOCKS-2 Items: library search add, category combo add, sponsors (places), Add the time, Add the weather; ↑/↓ move; remove | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-PROGBLOCKS-3 Save block / Save changes; Edit; Turn on/off; Delete | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-PROGBLOCKS-4 Next-24-hours preview | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-PROGBLOCKS-5 Block with time item replaces built-in hourly time and airs at its clock time | sizes: both | both | covered: NOT COVERED

## Schedule check (/admin/schedule-check)

- [ ] F-SCHEDCHECK-1 Station select; overlaps/gaps/off-air/unplaced spots listed | sizes: both | n.a. | covered: e2e/admin-layout-audit.mjs (layout only)
- [ ] F-SCHEDCHECK-2 Settle a conflict: Keep "<block>" buttons | sizes: both | n.a. | covered: NOT COVERED

## Sponsorship rules (/admin/sponsorship-rules)

- [ ] F-SPRULES-1 Edit rules text; Save rules (disabled unless changed); Undo changes | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-SPRULES-2 Show/Hide history; Load into editor | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-SPRULES-3 Copy review panel: sponsor name + wording, Check my wording, Use this wording | sizes: both | n.a. | covered: NOT COVERED

## Playlists (/admin/playlists)

- [ ] F-PLAYLISTS-1 Station filter; Create playlist (name, station, pattern) | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-PLAYLISTS-2 Delete template (confirm); Add slot (asset type + category); move slot up/down; delete slot; optimistic | sizes: both | n.a. | covered: e2e/control-room.mjs (optimistic move/remove)
- [ ] F-PLAYLISTS-3 Playlist generation panel: settings checkbox/numbers, Save, Regenerate (disabled while dirty) | sizes: both | n.a. | covered: NOT COVERED

## Categories (/admin/categories) and Types (/admin/asset-types)

- [ ] F-CATS-1 Add (button and Enter), Rename (Enter saves, Esc cancels), Save/Cancel, Delete (confirm) — categories | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CATS-2 Same for asset types | sizes: both | n.a. | covered: NOT COVERED

## Alerts (/admin/alerts)

- [ ] F-ALERTS-1 Filters severity/type/status; pager | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ALERTS-2 Resolve, Reopen, Delete (confirm), Resolve all (confirm) | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ALERTS-3 Bell count updates after resolving | sizes: both | n.a. | covered: NOT COVERED

## Bug reports (/admin/bug-reports)

- [ ] F-BUGS-1 List reports; Details/Hide details; Close sets status closed | sizes: both | n.a. | covered: NOT COVERED

## Site updates (/admin/site-updates, admin)

- [ ] F-SITEUPD-1 Mode Automatically / Only when I push | sizes: both | n.a. | covered: e2e/site-release/check.cjs (via API)
- [ ] F-SITEUPD-2 Push latest release: now or at datetime; include playing tabs checkbox; Cancel push | sizes: both | n.a. | covered: e2e/site-release/check.cjs (via API)
- [ ] F-SITEUPD-3 Open tabs list with version/playing/waiting | sizes: both | n.a. | covered: NOT COVERED

## Activity (/admin/activity, admin)

- [ ] F-ACTIVITY-1 Staff / Listeners tab, remembered | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ACTIVITY-2 Staff: search, user/station/category filters, date range + quick ranges, checkboxes, expand item, pager, Try again | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-ACTIVITY-3 Listeners: search, station, min minutes, date range, view toggle, expand session | sizes: both | n.a. | covered: NOT COVERED

## Analytics (/admin/analytics)

- [ ] F-ANALYTICS-1 Station and days selects; daily plays, top assets, by category, by hour bars | sizes: both | n.a. | covered: e2e/admin-layout-audit.mjs (layout only)

## Live listeners (/admin/listeners)

- [ ] F-LISTENERS-1 Stat cards (active now, today sessions/minutes, peak) update live (push) | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-LISTENERS-2 Active stations list; history 7/30/90d; regions days buttons | sizes: both | n.a. | covered: NOT COVERED

## Song requests (/admin/requests)

- [ ] F-REQUESTS-1 Status tabs; Approve / Reject / Delete (confirm); analytics top requested toggle | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-REQUESTS-2 Phone card layout actions | sizes: phone | n.a. | covered: NOT COVERED

## Admin archives (/admin/archives)

- [ ] F-AARCH-1 Station filter; New Archive modal (title, station, audio URL, description, host, duration, cover, date); Edit; Cancel/× | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-AARCH-2 Publish/Unpublish toggle; Delete (confirm) | sizes: both | n.a. | covered: NOT COVERED

## Live shows (/admin/live)

- [ ] F-LIVE-1 Tabs (upcoming/live/ended…); New show modal (title, description, station, broadcast mode, start/end, calls enabled); Edit; Delete (confirm) | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-LIVE-2 Start show; links to host console and screener | sizes: both | n.a. | covered: NOT COVERED

## Host console (/admin/live/:id/host)

- [ ] F-HOST-1 Start Broadcasting asks for mic; VU meter moves; Stop Broadcasting | sizes: both | n.a. | covered: e2e/web-call-in.cjs (audio path)
- [ ] F-HOST-2 Countdown to hard stop, re-synced via WS; hard stop warning | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-HOST-3 Caller queue: put on air (one at a time), end call | sizes: both | n.a. | covered: e2e/web-call-in.cjs (via API)
- [ ] F-HOST-4 End Show (confirm) → back to Live shows; listeners return to music | sizes: both | both | covered: NOT COVERED
- [ ] F-HOST-5 Hard stop ends show at scheduled end and queue resumes | sizes: both | both | covered: NOT COVERED

## Call screener (/admin/live/:id/screen)

- [ ] F-SCREENER-1 Waiting / approved / on-air columns update in real time | sizes: both | n.a. | covered: e2e/web-call-in.cjs (lists caller)
- [ ] F-SCREENER-2 Edit caller name (Enter saves, Esc cancels); Approve; Reject; Put on air; End call | sizes: both | n.a. | covered: NOT COVERED

## Recording studio (/admin/studio)

- [ ] F-STUDIO-1 Record / Pause / Resume / Stop / Cancel; title, type, category; Save to Library; Discard | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-STUDIO-2 Weather readouts: station + date, create, custom template toggle, record per readout (Save & Link), regenerate, skip, queue, delete (confirm) | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-STUDIO-3 Audio mixer: pick bed + voice-over (search), offsets/volumes, voice-over preset, show waveforms, Mix & Save to Library | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-STUDIO-4 Recording on iPhone Safari | sizes: phone | n.a. | covered: NOT COVERED

## CRM (/admin/crm)

- [ ] F-CRM-1 Tabs; members search, Deactivate (confirm) | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CRM-2 Song rankings | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CRM-3 Create raffle (title, dates), Draw winner (confirm), view entries | sizes: both | n.a. | covered: NOT COVERED

## Program clock (/admin/program-clock)

- [ ] F-CLOCK-1 Station select, day buttons, hour list; click hour opens panel | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CLOCK-2 Config: ad minutes toggles; station config save | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CLOCK-3 New/Edit rule: name, type, asset type, genres, days, numbers, active; Save; Delete (confirm); Back | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-CLOCK-4 dj/viewer view only | sizes: both | n.a. | covered: NOT COVERED

## Sponsor login (/sponsor/login)

- [ ] F-SPLOGIN-1 Sign in lands on /sponsor/dashboard; wrong password error; Signing in… state | sizes: both | n.a. | covered: NOT COVERED

## Sponsor layout

- [ ] F-SPLAYOUT-1 Nav Dashboard / Campaigns / Check Wording / Billing; Logout | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-SPLAYOUT-2 Phone layout no sideways scroll | sizes: phone | n.a. | covered: NOT COVERED

## Sponsor dashboard

- [ ] F-SPDASH-1 Stats, my spots, play history table with Prev/Next pager, upcoming schedule | sizes: both | n.a. | covered: NOT COVERED

## Sponsor campaigns and detail

- [ ] F-SPCAMP-1 New campaign form → Create Campaign; list | sizes: both | n.a. | covered: NOT COVERED
- [ ] F-SPCAMP-2 Detail: Submit draft (script + notes) versioned; comment thread Send | sizes: both | n.a. | covered: NOT COVERED

## Sponsor billing

- [ ] F-SPBILL-1 Invoice table + summary; Pay now opens Stripe checkout; return with success/cancel query param message | sizes: both | n.a. | covered: NOT COVERED

## Sponsor check wording

- [ ] F-SPWORD-1 Wording check shows what must change and why; Use this wording | sizes: both | n.a. | covered: NOT COVERED

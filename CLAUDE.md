# Studio Kol Bramah — Project Guide

> **IMPORTANT**: Keep this file current. Whenever you add models, routes, services, hooks, pages, or env vars — update this file to reflect the change. This is the single source of truth for the project.

## Agent-maintained wiki at `wiki/` — READ IT FIRST

This repo has an **agent-maintained knowledge graph at `wiki/`** — lazy-loaded markdown pages, one concept per file. Different from `docs/` (long-form for humans) and from this CLAUDE.md (always-loaded rules).

**Intent**: the wiki only pays off if future-you READS it before working. Writing alone is documentation theater. The lazy-load discipline IS the value.

### Mandatory first move: grep before you investigate

Before touching any non-trivial area — **`grep wiki/log.md` for the relevant topic**. If it returns any line, Read the linked page. Skipping this is how you re-discover patterns we already wrote down.

If grep returns nothing relevant, you're in genuinely new territory — proceed and INGEST your findings.

### Four operations — every time you do one, append a line to `wiki/log.md`

- **INGEST `<YYYY-MM-DD>` `<page>` `<summary>`** — when you learn something a future session would be surprised by if they only read the code. If the git log already tells the story, skip. After INGEST, `grep -r <old-fact> wiki/` and UPDATE any contradicting pages.
- **QUERY `<YYYY-MM-DD>` `<page>` `<why>`** — before touching auth/payment/cross-file invariants/external systems. Renames/docs/obvious fixes don't need it. Logging surfaces pages that get read often (load-bearing) AND pages that never get read (candidates for LINT/delete).
- **UPDATE `<YYYY-MM-DD>` `<page(s)>` `<what-changed>`** — when a single fact change touches multiple pages. Risk = INGEST one, leave four contradicting. `grep` the wiki for the old fact before considering INGEST complete.
- **LINT `<YYYY-MM-DD>` `<summary>`** — scheduled every 2-4 weeks. **If you do a big refactor between scheduled lints, manually trigger one immediately after.** Procedure in `wiki/lint-workflow.md`. **Prune ruthlessly** — a wrong page is worse than no page.

### Discipline

- **Lazy-load.** Read only relevant pages, not the whole wiki. But DO read them — grep `wiki/log.md` first; if it has a hit, follow the link.
- One concept per page. Title is the concept, not the date.
- Cross-link, don't duplicate.
- The log is grep-able. `grep ^LINT wiki/log.md` for history. `grep <topic> wiki/log.md` for everything that touched a topic.
- **Write-then-read failure mode:** documenting an INGEST then re-discovering the same pattern next session because you skipped the QUERY. If you ever catch yourself rebuilding context from DB queries when the wiki has it — that's the failure. Course-correct by grepping `wiki/log.md` immediately.

Start by reading `wiki/README.md` if the topic might already be covered. If creating a new page, also update `wiki/README.md` index.

## What is this?
Multi-channel radio streaming platform with playlist automation, ad insertion, weather/time announcements (ElevenLabs TTS), silence trimming, timezone-aware scheduling (Sabbath/holiday blackouts, sunset/sunrise rules), and admin + public listener UIs.

## Live URLs
- **Frontend**: https://kbrlive.com (alias: https://studio-kolbramah-radio.vercel.app)
- **Backend API**: https://api.kbrlive.com (alias: https://studio-kolbramah-api-production.up.railway.app)
- **GitHub**: https://github.com/ShmuelSokol/radioplatform

## Tech Stack
- **Frontend**: React 18 + TypeScript + Vite + Tailwind CSS + Zustand + React Query
- **Backend**: Python 3.12 + FastAPI (async) + SQLAlchemy 2.0 (asyncpg)
- **Database**: Supabase PostgreSQL (transaction pooler on port 6543)
- **Hosting**: Frontend on Vercel, Backend on Railway (persistent process — enables real WebSockets + scheduler)
- **TTS**: ElevenLabs API for weather/time announcements
- **Weather**: OpenWeatherMap API (current + 3-day forecast)
- **Storage**: Supabase Storage for audio files (bucket: `audio`)
- **Workers**: Celery + Redis (not yet deployed — needed for media processing)
- **Media**: FFmpeg for transcoding, clipping, HLS generation
- **Streaming**: in-browser player today; a single playout engine (Liquidsoap 2.2 + Icecast on its own server) is built and switched off until the server exists. See `wiki/streaming-architecture.md`

## Project Structure
```
radioplatform/
├── backend/
│   ├── api/index.py              # Vercel serverless entry point
│   ├── app/main.py               # FastAPI app factory
│   ├── app/config.py             # Pydantic BaseSettings (all env vars)
│   ├── app/core/                 # security (JWT), dependencies (auth guards), exceptions, middleware
│   ├── app/db/                   # async engine (statement_cache_size=0), Base, session
│   ├── app/models/               # 22 SQLAlchemy models
│   ├── app/schemas/              # Pydantic v2 request/response schemas
│   ├── app/api/v1/               # 14 route handlers
│   ├── app/services/             # 17 business logic services
│   ├── app/workers/tasks/        # Celery tasks (media processing)
│   ├── app/streaming/            # HLS generator, playlist engine
│   ├── liquidsoap/radio.liq      # Liquidsoap pipeline (crossfade, normalization, HLS output)
│   ├── start.sh                  # Process launcher (uvicorn only — Liquidsoap runs on VPS)
│   ├── tests/                    # pytest async tests
│   ├── vercel.json               # Rewrites all routes to api/index
│   └── pyproject.toml            # bcrypt==4.1.3 pinned for passlib compat
├── frontend/
│   └── src/
│       ├── api/                  # 14 API client modules + Axios client with JWT interceptor
│       ├── components/           # layout (Navbar, Layout), audio (AudioPlayer = the bar while a station plays, ListenHost keeps the Listen page playing across pages; the server stream plays through one shared element, `lib/stationStream.ts`; see `wiki/listener-player-bar.md`)
│       ├── pages/admin/          # Dashboard, Stations, Assets, AssetUpload, Schedules, Rules, Users, Login, SongRequests, Archives
│       ├── pages/public/         # StationList, Listen, ProgramGuide, Hosts, Archives
│       ├── hooks/                # 21 custom hooks
│       ├── stores/               # Zustand: authStore, playerStore
│       └── types/                # TypeScript interfaces
├── docs/                         # API reference documentation
├── docker/                       # Dockerfiles, nginx.conf
├── infra/streaming/              # Playout engine server: radio.liq, setup.sh, Icecast, nginx, systemd, kbr-engine-watch (restarts a stalled engine)
├── infra/local/                  # Local FM box: kbr_relay.py keeps the engine on air with no internet (a week ahead), kbr_panel.py is the box's own control room for days without internet, setup-box.sh sets a box up (wiki/local-fm-box.md)
├── infra/daf-yomi/             # Daf Yomi calendar and intro-clip words for the PC job that makes the clips (wiki/daf-yomi-intro.md)
└── infra/terraform/              # Terraform IaC (Vercel frontend provisioning)
```

## Backend Details

### Models (`backend/app/models/`)
| Model | File | Description |
|-------|------|-------------|
| User | user.py | Admin/manager/viewer/sponsor with JWT auth |
| Station | station.py | Radio station config |
| Asset | asset.py | Audio file metadata |
| Category | category.py | Asset categorization |
| AssetTypeModel | asset_type.py | Dynamic asset types (music, shiur, spot, etc.) |
| QueueEntry | queue_entry.py | Playback queue items |
| Schedule | schedule.py | Scheduling blocks |
| ScheduleBlock | schedule_block.py | Time blocks within schedules |
| ScheduleEntry | schedule_entry.py | Entries within schedule blocks |
| PlaylistEntry | playlist_entry.py | Playlist items |
| NowPlaying | now_playing.py | Current playback state |
| PlayLog | play_log.py | Playback history |
| RuleSet | rule_set.py | Scheduling rules |
| HolidayWindow | holiday_window.py | Sabbath/holiday blackouts |
| ChannelStream | channel_stream.py | Stream configuration |
| Sponsor | sponsor.py | A client: name on air, company, contact, notes; portal login via user_id |
| SpotSchedule | spot_schedule.py | When a sponsor's spot airs: days, three-hour time slots (6-9 AM ... 9 PM-12 AM) or broad dayparts (morning/afternoon/evening/overnight), plays per slot, date range |
| SpotRecording | spot_recording.py | A sponsorship spot in the making: script, wording review, audio, status (draft/ready/approved/rejected) |
| BugReport | bug_report.py | Report from the 🐞 button: words, screenshot, browser state, server log slice, GitHub issue and fix link (see `wiki/bug-reports.md`) |
| ProgramBlock | program_block.py | A named run of items (station ID, weather, PSA, sponsor places, programme) that airs as one event at set clock times: hard, soft, or soft with a limit; optionally only part of every year (`season_start`/`season_end`, MM-DD) |
| HolidayItem / HolidaySetting | holiday_music.py | A song or shiur on a holiday's list (every station or one; holiday-only unless all_year), and one station's settings for one holiday: on/off, days ahead, 1 in N at start / day before / holiday days, shiurim, stop (see `wiki/holiday-music.md`) |
| AuditLog | audit_log.py | Activity log row: who, what (label, category), station, before/after, status, IP, device, place (see `wiki/activity-log.md`) |
| HsSoftened | hs_softened.py | A timed queue item that HS off made wait, with its old timing so HS on restores it (see `wiki/station-modes.md`) |
| StationBlockedAsset | station_blocked_asset.py | An item one station never plays by itself ("Not on this station"); do_not_play is the all-stations version (see `wiki/control-room.md`) |
| EngineHandoff | engine_handoff.py | A queue entry handed to the playout engine and its asset, so a start report for an entry deleted meanwhile still shows what is on air after a backend restart (pruned after 6 h; see `wiki/streaming-architecture.md`) |
| EmergencyAlert | emergency_alert.py | One emergency alert on one station: kind (alert/test/rmt/rwt), source (EAS box API, its audio line, staff), on air/off times, status, EAS tones found (see `wiki/emergency-alerts.md`) |
| RdsSettings / RdsMessage / RdsPlay | rds.py | RDS: per-station PS and rotation settings, scheduled RadioText lines (slogan, time, weather, sponsor), and the log of lines the encoder fetched (see `wiki/rds-feed.md`) |
| LiveDump | live_dump.py | One Dump of a live show: who, when, seconds cut, caller dropped, and the stored recording of what aired before and what was cut (see `wiki/live-shows.md`) |
| LiveChatMessage | live_chat_message.py | One line of a live show's staff chat (host and screener, never on air; see `wiki/live-shows.md`) |
| CallerProfile | caller_profile.py | What the staff decided about one caller across shows: priority, blocked, a note (see `wiki/live-shows.md`) |
| SiteRelease | site_release.py | One row: how open tabs take a new website release (auto/manual) and the admin's last push (see `wiki/release-pickup.md`) |
| SponsorshipRules | sponsorship_rules.py | Versioned sponsorship wording rules, editable by managers (highest version is in force) |
| ScheduleRule | schedule_rule.py | Schedule-specific rules |
| PlaylistTemplate | playlist_template.py | Rotation pattern templates |
| TemplateSlot | playlist_template.py | Slots within a template |
| AdCampaign | ad_campaign.py | Sponsor ad campaigns with status workflow |
| AdDraft | ad_campaign.py | Versioned ad creative drafts |
| AdComment | ad_campaign.py | Campaign collaboration comments |
| Invoice | invoice.py | Billing invoices with Stripe integration |
| Alert | alert.py | System alerts with severity, type, and resolution tracking |
| LiveShow | live_show.py | Live broadcast shows with scheduling + Twilio call-in |
| CallInRequest | call_in_request.py | Caller queue entries for live shows |
| SongRequest | song_request.py | Public song request queue with admin review |
| ShowArchive | show_archive.py | Archived show recordings with podcast RSS feed |

### API Routes (`backend/app/api/v1/`)
| Router | File | Prefix |
|--------|------|--------|
| auth | auth.py | /auth |
| stations | stations.py | /stations |
| assets | assets.py | /assets (`DELETE /{id}` answers 503 with Retry-After when the queue holds the item's rows, see `wiki/library-delete.md`; `POST /{id}/remove-silence`: cut out every silent region the scan found (start and end completely; each middle gap shortened to `keep_gap` s, 0 = cut out; original kept for Restore Original); `POST /bulk-auto-trim` also shortens middle gaps longer than `middle_gap_over` to `keep_gap`; `GET /progress/{progress_id}`: how far a long edit sent with `?progress_id=` (auto-enhance, remove-silence) has got, for the progress bar; `GET`/`PUT /{id}/categories`, `POST /bulk-extra-category`: a song in several categories ("Also in"; see `wiki/song-categories.md`); `POST /{id}/do-not-play`: the control room's quick "do not play" / "allow again" on every station; `POST` and `GET /{id}/station-block`: "not on this station", one station only; see `wiki/control-room.md`) |
| streams | streams.py | /streams |
| controls | controls.py | /controls |
| users | users.py | /users |
| queue | queue.py | /stations/{id}/queue (operator actions in one call each: `skip?fade=hard\|fade\|soft`, `{entry_id}/skip-sponsorship` (skip or put back one coming sponsorship play; the row stays as `cancelled`), `fire-now`, `insert`, `loop`, `mix-over`, `panic`, `modes` (HS / Spots mode); see `wiki/operator-actions.md`, `wiki/station-modes.md`) |
| rules | rules.py | /rules |
| schedules | schedules.py | /schedules |
| now_playing | now_playing.py | /now-playing |
| websocket | websocket.py | /ws |
| scheduler | scheduler.py | /scheduler |
| holidays | holidays.py | /holidays (deleting or moving a blackout takes its queued silence out of the queue and the engine, `queue.release_blackout`; see `wiki/streaming-architecture.md`) |
| sponsors | sponsors.py | /sponsors (CRUD; `/clients`, `/{id}/account`, `/{id}/plays`, `/{id}/missed-plays`: the client database, see `wiki/sponsor-accounts.md`; delete refused with contracts or plays) |
| channels | channels.py | /channels |
| icecast | icecast.py | /icecast: retired 2026-11-19 (the backend's own FM source); `GET /status` names the engine's streams, start and stop answer 410. The FM feed is the playout engine's stream |
| analytics | analytics.py | /analytics |
| playlists | playlists.py | /playlists (the list is readable by every staff role, so a schedule's blocks can name their playlist; everything else manager only) |
| sponsor_portal | sponsor_portal.py | /sponsor-portal |
| spot_schedules | spot_schedules.py | /spot-schedules (`GET /for-asset/{asset_id}?station_id=`: the control room's Sponsor panel, any staff role; `GET /station-day?station_id=`: today's sponsorship plays on one station, the control room's Sponsors tab; `GET /room?station_id=&days=`: how full each time slot is per day, for Book a sponsorship and the board) |
| spot_recordings | spot_recordings.py | /spot-recordings |
| program_blocks | program_blocks.py | /program-blocks (CRUD + /preview) |
| holiday_music | holiday_music.py | /holiday-music: overview per station (`?station_id=`), `/{holiday}/days`, `/{holiday}/settings` (PUT, manager), `/{holiday}/items` (GET; POST manager), `/items/{id}` (PATCH/DELETE manager), `/{holiday}/suggest` (library titles naming the holiday); see `wiki/holiday-music.md` |
| databank | databank.py | /databank: KBR Studio style tables (items sorted by any column, every type of a table, spots with schedule end dates + PATCH end-date, one day's play log); see `wiki/databank.md` |
| copy_review | copy_review.py | /sponsorship (rules + copy review) |
| campaigns | campaigns.py | /campaigns: sponsorship campaigns. Reads (list with `?sponsor_id=`, detail, drafts, comments) for every staff role and sponsors (own only); create/edit/drafts/comments for managers and sponsors (staff name the `sponsor_id`); status for managers |
| billing | billing.py | /billing |
| ai_emails | ai_emails.py | /ai-emails |
| alerts | alerts.py | /alerts (list pages with skip/limit; POST /alerts/resolve-all; repeats of an open alert fold into it) |
| live_shows | live_shows.py | /live-shows (`POST /{id}/start` answers 409 while another show is live on the station or the station is off the air for Shabbos or Yom Tov; a show's end moves to 30 s before candle lighting; `GET /{id}/air`, `POST /{id}/dump`, `POST /{id}/delay`: the broadcast delay; `GET /{id}/dumps` and `/dumps/{dump_id}/audio`: the dump log; `GET` and `POST /{id}/chat`: the staff chat; `calls/{id}/on-air` answers 409 `delay_not_safe` unless `?force=true`; `calls/{id}/hold`, `calls/{id}/lock` and `calls/next`: the caller desk (up to four callers on air together); see `wiki/live-shows.md`) |
| live_shows_ws | live_shows_ws.py | /ws/live (WebSocket): show events, host mic audio, `/ws/live/{show}/talk/{call}` (staff talk to a caller off the air), `/ws/live/phone/{call}` (a phone caller's Twilio media stream), `/ws/live/{show}/cohost` (a co-host joins from another place), `/ws/live/{show}/video` (the host's camera) and `/ws/live/station/{id}/watch` (pages watching it), and `/ws/live/station/{id}/listen` where listener pages hear a live show (see `wiki/live-shows.md`) |
| song_requests | song_requests.py | /song-requests |
| archives | archives.py | /archives |
| playout | playout.py | /playout/{station_id}/next, /started, /started-batch (a local FM box's kept reports, 200 a call), /hello, /levels (the on-air level for the control room meters, passed to the station's sockets): called by the playout engine only, guarded by `X-Playout-Token` (no user login); one engine per station: hello/next/started from a second engine (other `X-Playout-Instance`) get 409 and an alert; /plan (`?days=` for a week offline), /library, /box-upload and /box-changes (token only) and /commands/poll for a local FM box's relay (the poll also carries the box's health report); `/live.mp3` (token only): the live show as listeners hear it, for the engine's live lane (see `wiki/live-shows.md`); `/blackouts`: the blackout windows the engine keeps itself, so a blackout never falls back to emergency music (see `wiki/streaming-architecture.md`) |
| rds | rds.py | Public `/stations/{id}/rds.txt` (RadioText now; `?mode=now_playing` = song only), `/rds-ps.txt`, `/rds.json` for the RDS encoder; staff `/rds/...`: settings, messages CRUD, check, preview, plays (see `wiki/rds-feed.md`) |
| site_release | site_release.py | /site-release (GET public; PUT mode, POST/DELETE push, GET tabs: admin) and WebSocket /ws/site that every tab keeps open (see `wiki/release-pickup.md`) |
| emergency | emergency.py | /emergency: `POST /device/activate` (the EAS box, `X-Emergency-Token` or Basic auth password = `EMERGENCY_API_TOKEN`; real alert audio, tones allowed), `POST /activate` (manager: a spoken test, EAS tones refused, 47 CFR 11.45), `POST /{id}/stop`, `GET` log, `/status`, `/active` (staff banner); engine reports on `POST /playout/{id}/alert` (see `wiki/emergency-alerts.md`) |
| playout_status | playout_status.py | /playout-status (public): per station, server stream or browser player, and whether the engine answers and matches the queue (badge in control room, station list, Listen); `box` = a local FM box's own health report; `engine_proto` and `live_on_stream` (the engine's stream carries a live show, proto 5) |
| caller_profiles | caller_profiles.py | /caller-profiles (DJ and up): callers marked priority or blocked, and lifting it; marking happens on a call, `PUT /live-shows/{id}/calls/{call_id}/caller` (see `wiki/live-shows.md`) |
| call_in | call_in.py | /call-in/{station_id} (public): a listener joins the live show's caller queue from the browser; their mic goes on `/ws/live/call/{call_id}` (see `wiki/live-shows.md`) |
| activity | activity.py | /activity (admin): who did what with filters and `after` for live updates, /activity/people (one line per person: counts for the period), /activity/filters, /activity/listeners (listening sessions); POST /activity/page-view (any signed-in user) |
| air_watch | air_watch.py | /air-watch (staff): recent on-air self-check findings |
| bug_reports | bug_reports.py | /bug-reports (POST open to all, rate-limited; GET staff; PATCH/resend admin) |
| omer_count | omer_count.py | /omer-count: the 49 Omer clips and which are recorded (staff), `/station/{id}?on=`: what a station says each hour of a day, `PUT /{day}`: upload a recording (manager; see `wiki/omer-count.md`) |
| weather_spots | weather_spots.py | /weather-spots/{date} and /time-announcements/{hour}: upload ready-made recordings (Shprach voice) for the daily weather spot and the reusable hourly time clips, both per region (`?region=jerusalem`); `?minute=30` for the half-hour clip ("The time is 10:30", played when the station has `half_hour_time_check`); ElevenLabs is only the fallback |

### Services (`backend/app/services/`)
| Service | Description |
|---------|-------------|
| auth_service.py | User authentication, JWT tokens |
| station_service.py | Station CRUD |
| asset_service.py | Asset management |
| media_service.py | FFmpeg transcoding |
| storage_service.py | File storage abstraction |
| supabase_storage_service.py | Supabase Storage integration |
| playback_service.py | Playback state management |
| schedule_service.py | Schedule CRUD operations |
| scheduling.py | Schedule resolution logic |
| scheduler_engine.py | Background scheduling engine: advances each station's queue (or ticks the playout engine), live-show hard stops, blackout silence, dead-air recovery through the queue. Never plays anything beside the queue (see `wiki/streaming-architecture.md`, one player per station) |
| queue_replenish_service.py | Auto-replenish playback queue |
| schedule_refresh_service.py | After a schedule, block or playlist change, replaces the unplayed automatic songs so the change takes over on time, and redraws them 15 min before a block's playlist takes over so it starts on time, not early (keeping the songs before a hard item at the change), and once 15 min before Shabbos or Yom Tov ends (`redraw_before_blackout_end`, as nothing tops the queue up during it); drops dated weather/time queued as songs (see `wiki/playlist-generation.md`) |
| schedule_lookahead_service.py | 7-day look-ahead of block overlaps, gaps, off-air windows and unplaced spots, using the engine's resolver (see `wiki/schedule-look-ahead.md`) |
| sponsor_item.py | What the control room's Sponsor panel shows for one spot on one station: sponsor, contracts, today's and coming plays, missed plays |
| spot_schedule_service.py | Plans sponsor spots into clock slots or block places, levelling each time slot so the emptiest break gets the next play, and keeps pending `ad_slot` queue entries in step; when blocks are over-booked it shares the room fairly, rolls plays to a later block and warns; no sponsorships in a station's quiet hours or the minutes before Shabbos (`sponsor_quiet_hours`, `sponsors_stop_before_blackout_minutes`) (see `wiki/spot-schedules.md`, `wiki/program-blocks.md`) |
| spot_voice_service.py | Makes spot audio from a script; Shprach call is a stub, placeholder tones until wired (see `wiki/spot-recordings.md`) |
| program_block_service.py | Expands program blocks into timed queue entries (`source="block"`) and replaces the built-in hourly time and weather; adds the station ID last in the hh:00 break or every break (`station_ids.placement`) (see `wiki/program-blocks.md`) |
| tts_service.py | ElevenLabs TTS generation |
| weather_service.py | OpenWeatherMap API (current + 3-day forecast) |
| weather_spot_service.py | Weather/time announcement generation + caching; stores uploaded daily weather spots per region (station `automation_config.weather_region`, default lakewood; ElevenLabs is the fallback for the default region only) |
| sun_service.py | Sunrise/sunset calculations (astral) |
| email_service.py | Resend transactional email (campaign updates, invoices, payments) |
| ai_email_service.py | Claude API-powered email drafting for manager outreach |
| copy_review_service.py | Checks a sponsor's proposed wording against the sponsorship rules in force (Claude API, keyword fallback) |
| alert_service.py | Alert creation, resolution, conflict detection, user notification dispatch |
| sms_service.py | Twilio SMS and WhatsApp notification delivery |
| live_show_service.py | Live show lifecycle (create, start, end, hard stop, call management) |
| live_air.py | A live show's broadcast (profanity) delay: listeners hear the show `live_delay_seconds` late, Dump cuts the newest audio, the delay builds back at the voice's own pitch, winds down before the hard stop and plays out at End show; a station's filler (a jingle) plays after a Dump while the delay refills at once (see `wiki/live-shows.md`) |
| live_video.py | Live video for people on the website: the host's encoded camera frames pass the same delay as the sound, a Dump cuts them too (see `wiki/live-shows.md`) |
| live_recording.py | Records every live show as listeners heard it (the delay's output, nothing dumped) and files it in Archives as a draft; station `automation_config.live_record_shows` (see `wiki/live-shows.md`) |
| live_inserts.py | Hard timed items (the time check) that air on time during a live show, over the delay, while the host's words wait |
| twilio_voice_service.py | Twilio Voice call-in handling (TwiML that streams a caller's sound to the show, the old hold conference, hang up) |
| phone_audio.py | A phone caller's sound between Twilio (8 kHz mu-law) and the show (24 kHz PCM), so phone callers are mixed like web callers (see `wiki/live-shows.md`) |
| liquidsoap_client.py | Old telnet push client for Liquidsoap. Superseded by playout_engine.py; keep `LIQUIDSOAP_ENABLED` off |
| duration_backfill.py | Measures the length of items that have none (they broke queue reads and restarts); runs once in the background after each start, and on demand via `POST /assets/backfill-durations` |
| upcoming_analysis.py | Analyses the item on air and the next one in the background when their audio analysis is missing or older than `ANALYSIS_VERSION`, so song changes use real end and crossfade points; an item whose file is broken or missing goes off the air (do_not_play, alert, not deleted); after each start, items it took off whose file now measures come back (see `wiki/audio-engine.md`) |
| last_played.py | When each item last aired per station, kept in memory: a station's first read is the last 30 days, later reads only the new plays; the whole history is read in the background (after the first read, then daily, one station at a time), so no request waits for it. Used by playlist and program-block rotation and `GET /queue/last-played` |
| long_items.py | What a hard timed item does to a shiur or story on air: station `automation_config.timed_during_long_items` = pause (default; it resumes where it stopped), wait or cut; a soft item due with more than 5 min of the shiur left pauses it too (see `wiki/hourly-slots.md`) |
| hour_fit.py | Plans every hard timed item in the next 70 min (fitted again only if it drifts past 15 s); reorders queue positions so a song (or pair) that ends near it replaces one that would be faded soon after starting, and when no order fits puts in up to three songs of the block's own playlist that end on it; automatic songs only, both players; `automation_config.fit_before_hard_items: false` switches it off (see `wiki/hourly-slots.md`) |
| timed_groups.py | A hard timed group due while an earlier short one still plays is moved to follow it (browser check 90 s ahead, engine before hand-over; the engine gets it at the first group's time) (see `wiki/hourly-slots.md`) |
| tail_analysis.py | Song end and crossfade point from HTTP range reads (MP3 header + last 20 s), so re-analysis does not download whole files |
| caller_history.py | Who called before: each call's history (earlier calls, times on air) and the caller's standing, keyed by `caller_key` |
| call_in.py | Web call-in tokens (HMAC of the call id) and closing a call when the caller's page leaves |
| activity_log.py | Activity log: middleware that records every signed-in change, before/after capture from SQLAlchemy flushes, batched background writer (see `wiki/activity-log.md`) |
| bug_report_service.py | Copies bug reports to the private GitHub repo and sends ntfy pushes: "received" at once, then "fix ready" or "needs you" (see `wiki/bug-reports.md`) |
| air_watch.py | On-air self-check every 5 s: hard timed items late or not started, no time check at hh:00, dead air, an engine's output stopped (no levels for 60 s), item shown past its end, engine vs site drift, skipped time/weather/sponsorship, a local FM box's clock, disk and missing audio; alerts + ntfy (see `wiki/air-watch.md`) |
| station_id_guard.py | Only for `station_ids.placement` `between_songs` (IDs normally air in the breaks, see program_block_service). Last check on an automatic station ID as it would go on air: passed over after a break or spoken item, or near a break (both pick paths); an ID that may not air yet moves one song later (`postpone`); after each top-up `drop_misplaced` moves waiting IDs the queue timeline puts beside a break and `pull_late_id` brings the next one forward when it would air more than max_minutes after the last; an ID already that late keeps 1 min from breaks instead of clear_minutes (see `wiki/playlist-generation.md`) |
| station_modes.py | HS (hard start) and Spots mode switches: softens hard timed items while HS is off and restores them; Spots off plans no sponsorships (see `wiki/station-modes.md`) |
| queue_positions.py | Renumbers a station's playing and pending queue entries from 1 in one statement (browser player's song change, blackout fill); `put_playing_first` moves only the item on air before the first waiting one (engine start report) |
| playout_tunnel.py | Commands to an engine with no inbound line (a local FM box): its relay long-polls for them; used by playout_engine only while a relay is polling. Also keeps the box's health report and names its problems for air watch (see `wiki/local-fm-box.md`) |
| box_sync.py | What was done at a local FM box's own control room while it was offline, brought to the site on return: USB recordings into the library (`store_box_upload`), additions still to come into the queue and one alert listing them and anything taken off (`apply_box_changes`) (see `wiki/local-fm-box.md`) |
| playout_plan.py | Read-only plan (pending queue with engine URIs, blackouts) and library pages for a local FM box's relay; `?days=` adds the extension for a week offline (timed items past the queue, songs by hour, 30 days of blackouts) (see `wiki/local-fm-box.md`) |
| rds_service.py | RDS line now: clock-based weighted rotation, sponsor line while their spot airs, Latin-only text (Hebrew dropped), 8/64 limits, play log of encoder fetches (see `wiki/rds-feed.md`) |
| sponsor_room.py | How full each three-hour slot is per day on a station: sponsor places (or clock minutes) on the air against the planner's bookings, and who is booked (see `wiki/sponsor-accounts.md`) |
| sponsor_accounts.py | Sponsors as clients: status from contract dates, spots by stage of the flow, plays per contract, the as-run log (see `wiki/sponsor-accounts.md`) |
| sponsor_delivery.py | What a sponsor was promised against what aired: missed plays of the last 14 days and coming plays with no room or moved, for `GET /sponsor-portal/missed-plays` (see `wiki/spot-schedules.md`) |
| shabbos_ending.py | Last song before Shabbos: the station's chosen song back-timed to end exactly at candle lighting (the blackout's start), the item after it (the shofar) first in the silence group, timed items cleared out of its way; optionally that Friday's candle lighting announcement (`candle_items`) last, the song before it; `automation_config.shabbos_ending` (see `wiki/shabbos-ending.md`) |
| asset_categories.py | A song in several categories: main + extra ("Also in", `asset_categories` table), read by `models.asset.in_category`; holiday lists shown beside them (see `wiki/song-categories.md`) |
| omer_count.py | Sefiras HaOmer on the hour: the count each station says (the next day from shekia at its place, days only as the siddur), the 49 clips (category `omer_count`), wording; queued after each hh:00 time check by `QueueReplenishService._schedule_omer_counts`; `automation_config.omer_count` (see `wiki/omer-count.md`) |
| holiday_music.py | Holiday lists mixed in before and during each holiday: Hebrew calendar per station (Israel or diaspora), rising 1-in-N share, `HolidayMix.pick` used by both fills, rest and carried count, queue tags (see `wiki/holiday-music.md`) |
| station_blocks.py | "Not on this station": the per-station list of items a station does not play, the subquery every pick uses (`blocked_asset_ids`) and block/undo with removal of that station's waiting copies |
| emergency_alerts.py | The emergency override: sends an alert to each station (engine `/alert`, or WS `emergency_alert` to browser players), takes the engine's start/end reports into the log, drops hard items due during it, alert + ntfy on each (see `wiki/emergency-alerts.md`) |
| eas_tones.py | Finds EAS attention signal and SAME bursts in a recording, so a staff upload with them is refused |
| job_progress.py | How far a long audio edit (Smart Enhance, Remove All Found Silence) has got: FFmpeg `-progress` on temp files, kept in memory per progress id for `GET /assets/progress/{id}` and the item page's progress bar (`components/audio/EditProgressBar.tsx`) |
| playout_engine.py | Link to the playout engine: hands it the next track, records what really went on air, sends Next/flush/timed commands (see `wiki/streaming-architecture.md`) |

## Frontend Details

### Pages
**Admin** (`frontend/src/pages/admin/`):
- Dashboard.tsx — Control room (operator screen): frozen transport bar, queue with insert-at-point (times that may still move before the next break shown dimmed, "≈"), instant buttons, server-side library search, go-live panel, Song panel (quick actions on the song on air or a queue row: category, not on this station, do not play on any station, details), queue and library columns the operator drags or double-clicks to fit, a "Playing from" column with a Why panel (playlist, block and step a song was drawn for, or what else queued it), Sponsors tab (today's sponsorships on the station; rows carry a SPONSOR tag) and a Sponsor panel instead for a sponsorship spot (sponsor, contracts, today's and coming plays with Skip, missed plays, Pause contract, links). Panels in `pages/admin/controlroom/`; live actions go through `api/liveControl.ts`. See `wiki/control-room.md`
- Stations.tsx — Station management (incl. "Omer count on the hour", "Station IDs" with where they play)
- Assets.tsx — Audio asset management
- AssetUpload.tsx — File upload
- Schedules.tsx — Schedule management (create/edit/delete)
- ScheduleBlocks.tsx — Manage blocks & playlist entries within a schedule
- Rules.tsx — Scheduling rules
- Users.tsx — User management
- Login.tsx — Authentication
- Holidays.tsx — Sabbath/holiday blackout management
- Sponsors.tsx — Sponsors as a client list: on the air / starting soon / paused / ended / no contract, active dates, contracts, plays; filters and search (see `wiki/sponsor-accounts.md`)
- SponsorDetail.tsx — One client (tabs Contracts, Spots, What aired, Missed plays, Requests, Details): Book a sponsorship (`components/sponsors/BookSponsorship.tsx`, four steps with how full each slot is; also on the Sponsors list and Sponsors > Book a sponsorship, `/admin/sponsors?book=1`); spots by stage of the flow, contracts with their settings (Edit dialog `components/sponsors/ContractEditor.tsx`, also on Spot Schedule; pause, end date), play history with CSV, missed plays, details
- Campaigns.tsx — "Sponsor requests" (campaigns from the portal) across sponsors: sponsor and status filters, New Campaign for a chosen sponsor (managers/admins; DJs and viewers view only)
- CampaignDetail.tsx — One campaign for staff: drafts, comments, Save Status. Drafts and comments panels are shared with the sponsor portal (`components/campaigns/CampaignParts.tsx`)
- ScheduleCheck.tsx — Look-ahead of schedule conflicts: what was settled automatically, what needs a choice
- SpotSchedules.tsx — "All contracts": every client's contracts, Book a sponsorship, the old form as "Book at exact clock times", a 7-day look-ahead, and the break settings (`components/sponsors/BreakSettings.tsx`: :00, :30 and :15/:45, half-hour time check, hours with no sponsorships, minutes before Shabbos)
- SponsorshipBoard.tsx — Sponsorship board: the next 7 days on one station as three-hour slots against days, how full each is, who is booked, Book into this slot, Break settings (see `wiki/sponsor-accounts.md`)
- SpotRecordings.tsx — Record Spots: write a script, make or upload audio, listen, approve into the library
- Databank.tsx — KBR Studio's database window: Songs / Jingles / Spots / Blocks tables, Find, Edit mode, day log to the millisecond
- Rds.tsx — RDS: what radios show now and next 10 minutes, PS and rotation settings, rotation lines (templates, sponsor lines, days/hours/dates), encoder pickup log, feed links for Magic RDS
- HolidayMusic.tsx — Holiday Music: each holiday's next dates, when the mix starts, today's rate, settings per station, day-by-day rates, the list with Suggest from titles and library search
- ProgramBlocks.tsx — Named blocks ("Weather Sponsors"): items in order, clock times, hard or soft, next 24 hours
- SponsorshipRules.tsx — Edit the sponsorship wording rules (versioned) and try the wording check
- Playlists.tsx — Playlist template rotation management
- Analytics.tsx — Analytics & reporting dashboard
- SiteUpdates.tsx — Choose when open browsers move to a new release: automatic or only on push, push now or at a time, optionally including tabs that are playing; live list of open tabs
- Activity.tsx — Who did what: "By person" (one collapsible line per person with today's counts, opening their own timeline) or everything in order; filters by station, kind, dates; search; paging; live and a Listeners tab with each listening session
- BugReports.tsx — Reports from the 🐞 button with screenshots, status and fix links
- EmergencyAlerts.tsx — Emergency alerts (EAS override): what is on air per station with Stop, a spoken test of the override (managers), how the EAS box connects, the log. `components/layout/EmergencyBanner.tsx` shows a red bar on every staff page while an alert is on air
- Alerts.tsx — Alerts management (filter, page through, resolve, resolve all, reopen, delete)
- LiveShows.tsx — Live show management (create, list, Open studio, delete); a show goes on air from the Host Console only
- HostConsole.tsx — Host broadcasting console: Turn on microphone, then one GO LIVE press, a banner that says whether listeners hear you, level meter (`pages/admin/live/LevelMeter.tsx`), Hear myself and a 5 s Mic check, callers waiting ("Calling now") with Put On Air; countdown, mic controls (Cough, Mute mic), carts (the station's instant buttons played into the show, `pages/admin/live/CartPanel.tsx`), caller queue with each caller's topic and notes, Hold and Next caller, Talk off air (a private word with a caller, `hooks/useTalkback.ts`), Join as co-host (`pages/admin/live/CohostPanel.tsx`), Start camera (live video for the website, `pages/admin/live/CameraPanel.tsx`), the staff chat, and the broadcast delay panel (Dump, dump log with recordings, exit, build, length; `pages/admin/live/DelayPanel.tsx`, also on the Call Screener)
- CallScreener.tsx — Two-column call screener (waiting/approved/on-air management), each caller's history with Priority and Block (`pages/admin/live/CallerHistory.tsx`), the list of marked callers, with the delay panel and the staff chat (`pages/admin/live/ChatPanel.tsx`, also on the Host Console)
- SongRequests.tsx — Song request management (approve/reject/delete)
- Archives.tsx — Show archive management (CRUD, publish/unpublish)

**Public** (`frontend/src/pages/public/`):
- StationList.tsx — Browse stations
- Listen.tsx — Listen to a station (server stream or built-in player, song requests; "Watch the show live" when a live show has video); keeps playing, with a bar, on every other page except call-in and the live host screens, and takes over sound the control room was giving
- ProgramGuide.tsx — Public EPG ("Schedule" in the menu; "Public Schedule" in the staff menu) with station/date picker and timeline
- Hosts.tsx — Public DJ/host profile cards
- Archives.tsx — Public show archive browser with playback
- CallIn.tsx — Call the live show from the browser: name and topic, mic, hear the show while waiting, on-air status, Hang up

**Sponsor Portal** (`frontend/src/pages/sponsor/`):
- Login.tsx — Sponsor login (separate from admin, indigo-themed)
- Dashboard.tsx — Play history stats + table + upcoming schedule + Missed Plays (promised plays that did not air, coming plays with no room or moved)
- Campaigns.tsx — Campaign list with create form
- CampaignDetail.tsx — Campaign detail with drafts + comments thread
- Billing.tsx — Invoice table with Stripe checkout + billing summary
- CheckWording.tsx — Sponsor proposes wording, gets back what must change and why

### Hooks (`frontend/src/hooks/`) — 23 hooks
| Hook | Description |
|------|-------------|
| useAuth.ts | Authentication state + mutations |
| useStations.ts | Station CRUD queries/mutations |
| useAssets.ts | Asset queries/mutations |
| useQueue.ts | Queue management + useWeatherPreview |
| useSchedules.ts | Schedule/block/playlist entry hooks |
| useRules.ts | Scheduling rules |
| useUsers.ts | User management |
| useAudioEngine.ts | Audio engine integration |
| useNowPlaying.ts | Now-playing polling |
| useNowPlayingWS.ts | Now-playing WebSocket (with polling fallback) |
| useOnAirLevels.ts | The engine's on-air level for the control room meters (socket `levels` messages), no sound or click needed |
| usePlaylists.ts | Playlist template CRUD + asset type combos |
| useSponsorPortal.ts | Sponsor play history, stats, upcoming schedule |
| useCampaigns.ts | Campaign CRUD (list takes an optional sponsor), status, drafts, comments |
| useBilling.ts | Invoice list + billing summary |
| useAlerts.ts | Alert list, unresolved count, resolve/reopen/delete mutations |
| useLiveShows.ts | Live show CRUD, start/end, call approve/reject/on-air mutations |
| useLiveShowWS.ts | Live show WebSocket (real-time callers, countdown, with polling fallback) |
| useHostAudio.ts | Browser mic capture as 24 kHz PCM (`openMic`), binary WS streaming from `goLive`, level meter, hear-myself monitor, mic check; plays the callers on air sent back by the server; mixes carts into what is sent |
| useLiveAudio.ts | Plays a station's live show (host mic relayed by the server) and tells the page to silence the music meanwhile |
| useSongRequests.ts | Song request list, submit, approve/reject/delete mutations |
| useArchives.ts | Show archive CRUD queries/mutations |
| useTalkback.ts | Talk to one web caller off the air: own microphone capture and socket, nothing reaches listeners |
| useCohost.ts | Join a live show as a co-host: own microphone and socket, hears the host and callers |
| useHostVideo.ts | The host's camera: encoded in the browser (WebCodecs) and sent frame by frame for people watching on the website |
| useLiveVideo.ts | Watch a station's live show on the Listen page: decodes the frames and draws them |
| useEscape.ts | Esc closes an open dialog (New Archive, Live show, bug report) |
| useMayChange.ts | May the signed-in person change things on this screen? The one rule for hiding or disabling change controls (see `wiki/view-only-screens.md`) |
| useNavState.ts | useState kept with the page's place in this tab's history: Back (the Back line, browser back, iPhone swipe) finds filters, search and open panels as they were (see `wiki/back-navigation.md`) |

### API Clients (`frontend/src/api/`) — 14 modules
auth.ts, stations.ts, assets.ts, assetTypes.ts, queue.ts, rules.ts, users.ts, playlists.ts, sponsorPortal.ts, campaigns.ts, billing.ts, alerts.ts, liveShows.ts, songRequests.ts, archives.ts, client.ts (Axios + JWT interceptor)

### Routes (`frontend/src/App.tsx`)
- `/` → redirect to `/stations`
- `/stations` → StationList
- `/listen/:stationId` → Listen
- `/admin/login` → Login
- `/admin/dashboard` → Dashboard (protected)
- `/admin/stations` → Stations (protected)
- `/admin/assets` → Assets (protected)
- `/admin/assets/upload` → AssetUpload (protected)
- `/admin/users` → Users (protected)
- `/admin/rules` → Rules (protected)
- `/admin/schedules` → Schedules (protected)
- `/admin/schedules/:scheduleId/blocks` → ScheduleBlocks (protected)
- `/admin/holidays` → Holidays (protected)
- `/admin/sponsors` → Sponsors (protected)
- `/admin/sponsors/:sponsorId` → SponsorDetail (protected; `?tab=spots|plays|missed|requests|details`, Contracts by default)
- `/admin/campaigns` → AdminCampaigns (staff; managers/admins change, DJs/viewers view only)
- `/admin/campaigns/:id` → AdminCampaignDetail (same roles)
- `/admin/schedule-check` → ScheduleCheck (protected)
- `/admin/spot-schedule` → SpotSchedules ("All contracts"; protected)
- `/admin/sponsorship-board` → SponsorshipBoard (protected)
- `/admin/spot-recordings` → SpotRecordings (protected)
- `/admin/databank` → Databank (protected)
- `/admin/program-blocks` → ProgramBlocks (protected)
- `/admin/holiday-music` → HolidayMusic (staff; managers change)
- `/admin/rds` → Rds (staff; managers change)
- `/admin/sponsorship-rules` → SponsorshipRules (protected)
- `/admin/playlists` → Playlists (protected)
- `/admin/alerts` → Alerts (protected)
- `/admin/emergency` → EmergencyAlerts (staff; managers send tests and stop)
- `/admin/analytics` → Analytics (protected)
- `/admin/live` → LiveShows (protected)
- `/admin/live/:showId/host` → HostConsole (protected)
- `/admin/live/:showId/screen` → CallScreener (protected)
- `/admin/requests` → SongRequests (protected)
- `/admin/archives` → AdminArchives (protected)
- `/admin/site-updates` → SiteUpdates (admin)
- `/admin/activity` → Activity (admin)
- `/guide` → ProgramGuide
- `/hosts` → Hosts
- `/archives` → Archives
- `/call/:stationId` → CallIn
- `/sponsor/login` → SponsorLogin
- `/sponsor/dashboard` → SponsorDashboard (sponsor-protected, SponsorLayout)
- `/sponsor/campaigns` → SponsorCampaigns (sponsor-protected)
- `/sponsor/campaigns/:id` → CampaignDetail (sponsor-protected)
- `/sponsor/billing` → SponsorBilling (sponsor-protected)
- `/sponsor/wording` → CheckWording (sponsor-protected)

## CRITICAL: Read Before You Write
> **NEVER guess function names, class names, import paths, or field names.**
> Before writing ANY code that imports from or references existing modules, you MUST first READ the actual source file to confirm the exact names. This applies to:
> - Dependency functions (`app/core/dependencies.py` exports: `get_db`, `get_current_user`, `require_admin`, `require_manager`)
> - Schema classes (`app/schemas/` — read the file before importing from it)
> - Model fields (read the model file before referencing columns)
> - Frontend types and hooks (read the type/hook file before using its exports)
> - API client exports (`frontend/src/api/client.ts` is a **default** export, not named)
>
> **After writing code, always verify:** run `uv run python -c "import app.main"` for backend, `npx tsc --noEmit` for frontend.

## Key Conventions
- **Backend**: All endpoints under `/api/v1/`. UUID primary keys. Async SQLAlchemy sessions.
- **Auth**: JWT Bearer tokens. Roles: admin, manager, dj, viewer, sponsor. Auth guards: `require_admin` (admin only), `require_manager` (admin + manager), `require_dj_or_manager` (admin + manager + dj), `require_staff` (everyone except sponsors — use this, not bare `get_current_user`, for staff-only reads), `require_sponsor` (sponsor only), `require_sponsor_or_manager` (admin + manager + sponsor). The frontend mirrors these in `frontend/src/auth/access.ts` (route → roles); when you add a screen or change a guard, update both, and `backend/tests/test_role_access.py`. `canChange()` there says who may change things on a screen (drives the "View only" marker); screens hide or disable their change controls with `hooks/useMayChange.ts` (see `wiki/view-only-screens.md`); any refused action shows an app-wide "not allowed" notice (`components/layout/ViewOnlyNotice.tsx`).
- **Sessions**: every token carries the user's `session_version`; a new password or a changed role bumps it and signs the person out everywhere. Any new code that decodes a token must call `security.token_is_current`. See `wiki/sign-in-sessions.md`.
- **Station permissions**: staff can be limited to named stations (`User.station_ids`), and a protected station (`Station.protected`, the FM feed) needs an explicit assignment. Rule: `may_operate_station`; guard: `require_station_operator`. See `wiki/station-permissions.md`.
- **Caller address**: rate limits, listener IPs and the activity log take the caller's address from `app/core/client_ip.py` (rightmost non-internal X-Forwarded-For hop); never read the header by hand. See `wiki/client-ip.md`.
- **Look ("qnet")**: every admin and sponsor page wears the KBR Studio look through one shared skin (`.qn-site`, `frontend/src/styles/qnet-site.css` + generated `qnet-site-colors.css`); write pages with ordinary Tailwind classes and the skin restyles them. Rules and escape hatches in `wiki/qnet-site-theme.md`.
- **Back navigation**: every staff page but the control room has a "‹ Back to …" line (`components/layout/BackNav.tsx`, in `Layout`); coming back restores scroll, page state kept with `hooks/useNavState.ts` and lights the row last tapped (`data-nav-id` on list rows helps find it). Keep filters in the address or in `useNavState`, not plain `useState`, on list pages people open things from. See `wiki/back-navigation.md`.
- **Page layout**: admin and sponsor pages fill the window; no `max-w-* mx-auto` page wrappers. Rules in `wiki/admin-page-layout.md`.
- **Frontend**: All API calls through `src/api/client.ts` (default export, auto JWT refresh). Zustand for global state, React Query for server state. `StationListResponse` wraps stations in `.stations` array — always use `data?.stations?.map()`.
- **Models**: Use `UUIDPrimaryKeyMixin` + `TimestampMixin` from `app/db/base.py`.
- **DB Sessions**: Import `get_db` from `app.db.session` (not `get_async_session`).
- **Tests**: pytest-asyncio with SQLite + type compilation hooks (PG_UUID→VARCHAR, JSONB→TEXT, ENUM→VARCHAR).
- **Railway backend**: Uses lifespan events — tables created on startup, scheduler engine auto-starts. Middleware fallback still in place for local/Vercel compat. Statement cache disabled for Supabase pooler.
- **TTS Pronunciation**: "Kol Bramah" is spelled "Kohl Baramah" in TTS text for correct pronunciation.

## Environment Variables

### Backend (Railway)
| Variable | Description | Required |
|----------|-------------|----------|
| `DATABASE_URL` | Supabase PostgreSQL pooler URI | Yes |
| `JWT_SECRET_KEY` | Secret for JWT signing (with `APP_ENV=production` the public default is refused at startup) | Yes |
| `ADMIN_SEED_PASSWORD` | Password for accounts created by `/auth/seed` (unset = seeding refused) | First-time setup |
| `ALLOW_DESTRUCTIVE_SEED` | Allow `/auth/seed-all` (wipes data) in production | No |
| `CORS_ORIGINS` | JSON array of allowed origins | Yes |
| `APP_ENV` | "production" | Yes |
| `APP_DEBUG` | Debug mode and SQL echo; default false (set "true" only locally) | No |
| `REDIS_URL` | Empty string to disable | No |
| `S3_ENDPOINT_URL` | Empty string to disable | No |
| `ELEVENLABS_API_KEY` | ElevenLabs TTS API key | For weather/time |
| `ELEVENLABS_VOICE_ID` | ElevenLabs voice ID | For weather/time |
| `OPENWEATHERMAP_API_KEY` | OpenWeatherMap API key | For weather |
| `SUPABASE_URL` | Supabase project URL | For storage |
| `SUPABASE_SERVICE_KEY` | Supabase service role key | For storage |
| `SUPABASE_STORAGE_BUCKET` | Storage bucket name (default: "audio") | For storage |
| `STRIPE_SECRET_KEY` | Stripe secret key (empty to disable) | For billing |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signing secret | For billing |
| `STRIPE_PRICE_ID` | Stripe price ID | For billing |
| `RESEND_API_KEY` | Resend API key (empty to disable) | For emails |
| `RESEND_FROM_EMAIL` | Sender email (default: noreply@kolbramah.com) | For emails |
| `ANTHROPIC_API_KEY` | Claude API key for AI email drafting | For AI emails |
| `TWILIO_ACCOUNT_SID` | Twilio account SID (empty to disable) | For SMS/WhatsApp alerts |
| `TWILIO_AUTH_TOKEN` | Twilio auth token; when set, Twilio webhooks must carry a valid X-Twilio-Signature (403 otherwise) | For SMS/WhatsApp alerts |
| `TWILIO_PHONE_NUMBER` | Twilio phone number | For SMS/WhatsApp alerts |
| `TWILIO_VOICE_NUMBER` | Dedicated voice number for live call-ins | For live shows |
| `LIVE_SHOW_HOLD_MUSIC_URL` | Public URL to hold music MP3 | For live shows |
| `BACKEND_PUBLIC_URL` | Public backend URL for Twilio callbacks; must be https for phone callers to go on air (their media stream opens on it) | For live shows |
| `EMERGENCY_FALLBACK_CATEGORY` | Unused since dead air restarts the queue instead (kept so old settings still load) | No |
| `SILENCE_DETECTION_SECONDS` | Seconds of silence before alert (default: 30) | For silence detection |
| `MAX_UPLOAD_MB` | Largest library upload in MB (default 600); bigger files get 413 | No |
| `LIQUIDSOAP_ENABLED` | Enable Liquidsoap (remote VPS) | For streaming |
| `LIQUIDSOAP_HOST` | VPS hostname for Liquidsoap telnet | For streaming |
| `LIQUIDSOAP_TELNET_PORT` | Liquidsoap telnet port (default: 1234) | For streaming |
| `ICECAST_HOST`, `ICECAST_PORT`, `ICECAST_SOURCE_PASSWORD`, `ICECAST_MOUNT` | Unused since the backend's own FM source was retired (kept so old settings still load) | No |
| `ICECAST_STREAM_URL` | Stream base URL (e.g., https://stream.kbrlive.com/live); an engine station streams at `<base>-<station id>` | For streaming |
| `PLAYOUT_ENGINE_ENABLED` | Let the playout engine drive the listed stations (default false) | For the engine |
| `PLAYOUT_ENGINE_STATION_ID` | Comma-separated stations the engine plays (one engine each; setup.sh prints the list) | For the engine |
| `PLAYOUT_ENGINE_URL` | Engine control base (e.g., https://stream.kbrlive.com/control); commands go to `<base>/<station id>/...` | For the engine |
| `PLAYOUT_ENGINE_TOKEN` | Shared secret sent both ways in `X-Playout-Token` | For the engine |
| `PLAYOUT_RELAY_STATION_ID` | Comma-separated stations whose engine is a local FM box: its relay fetches their commands and none go to `PLAYOUT_ENGINE_URL` (empty = no station; see `wiki/local-fm-box.md`) | For a local FM box |
| `BUG_REPORTS_GITHUB_TOKEN` | Fine-grained token for the private bug-report repo (Contents + Issues write); unset = reports stay in the database only | For bug triage |
| `BUG_REPORTS_REPO` | Private repo for bug reports (default ShmuelSokol/kbr-bug-reports) | No |
| `EMERGENCY_API_TOKEN` | Secret the station's EAS box sends to `POST /emergency/device/activate` (header or Basic auth password); empty = only staff can send; never commit it | For the EAS box |
| `NTFY_TOPIC` | Secret ntfy topic for "fix ready" pushes; never commit it | For bug triage |
| `PLAYOUT_STREAM_LIVE` | true = the site plays the engine's stream instead of the in-browser player | For the engine |

### Frontend (Vercel)
- `VITE_API_URL` — Backend API base URL (https://api.kbrlive.com/api/v1)

## Credentials
- **Never write a password, key or token in this repository. It is public.**
- There is no default admin password. `POST /api/v1/auth/seed` (first-time setup only) creates the `admin` account with the password in the `ADMIN_SEED_PASSWORD` environment variable and refuses if it is unset.
- `POST /api/v1/auth/seed-all` deletes the whole library, stations and rules before re-seeding. It is refused in production unless `ALLOW_DESTRUCTIVE_SEED=true`.
- Scripts and load tests read `KBR_ADMIN_EMAIL` / `KBR_ADMIN_PASSWORD` from the environment.

## Known Gotchas
- **ALWAYS verify live site after deploy**: After deploying, test the live URLs (frontend + backend health + key API endpoints) to confirm the site works. Don't assume deploys succeed — check `curl https://api.kbrlive.com/health` and try loading key frontend pages.
- **Railway deploys from GitHub**: Auto-deploy is enabled on `main` branch with root directory `/backend`. A merge deploys the backend only when it changed `backend/` outside the tests, and builds the website only when it changed `frontend/` (see `wiki/deploys.md`). If deploys fail, check Railway dashboard → service → Deployments tab for build logs.
- **nixpacks.toml**: Must use `["...", "ffmpeg"]` (with spread operator) to keep default Python packages. Using `["ffmpeg"]` alone removes Python and breaks the build.
- **requirements.txt must stay in sync**: Railway installs from `requirements.txt`, not `pyproject.toml`. When adding dependencies to pyproject.toml, also add them to requirements.txt.
- **DB migrations in main.py**: When adding columns to models, add corresponding `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` to `_add_missing_columns()` in `main.py`. Each migration runs in its own transaction so one failure doesn't block others.
- **bcrypt**: Must pin `bcrypt==4.1.3` — passlib breaks with bcrypt 5.x
- **Supabase pooler**: Must use port 6543 (transaction mode) + `statement_cache_size=0`
- **Slow requests**: a `SLOW QUERY` line times the app's wait, not the database. Read its "loop held N s" and "pool N in use" parts and look for `EVENT LOOP HELD ... by:` (code that ran without `await`, `app/core/loop_watch.py`) and `SLOW CONNECT` before blaming a query. See "Where the seconds go" in `wiki/playlist-generation.md`.
- **Vercel IPv6**: Direct Supabase connection fails (IPv6 not supported). Use pooler URL.
- **Password URL encoding**: `/` → `%2F`, `[` → `%5B`, `]` → `%5D` in DATABASE_URL
- **Pydantic schemas**: ID fields use `uuid.UUID | str` for cross-DB compatibility (SQLite returns strings)
- **Frontend Vercel deploy**: Repo-local git email is set to `shmuelsokol@yahoo.com` (matching the Vercel account). Deploy directly: `cd frontend && npx vercel --prod --yes`.
- **Weather caching**: Weather TTS audio is cached per 15-min slot. Text changes don't take effect until the next slot.
- **Playout engine**: Liquidsoap 2.2 + Icecast on its own server (not Railway), set up by `infra/streaming/setup.sh` (Ubuntu 24.04). The engine pulls the next track and reports real starts; for an engine station nothing in the backend advances playback by duration. Off until `PLAYOUT_ENGINE_*` is set. The older telnet path (`LIQUIDSOAP_ENABLED`) must stay off. The engine's whole output goes through a broadcast sound chain when its engine has `KBR_PROCESSING=broadcast` (leveler, three-band compressor, limiter at -1.5 dBFS; setup.sh sets it per station from `KBR_BROADCAST_SOUND`; the default `light` is the older chain); never use Liquidsoap's `normalize()` or a plain `amplify()` there, they take each track's `liq_amplify` (see `wiki/streaming-architecture.md`, Broadcast sound).

## Milestones
- **M1** (complete): Backend skeleton, auth, station CRUD, asset upload, React frontend — DEPLOYED
- **M2** (complete): Scheduling engine, admin schedule UI, now-playing WebSocket, queue system, weather/time announcements
- **M3** (complete): Sponsor insertion, holiday/Sabbath blackouts, sunset/sunrise scheduling, multi-channel broadcast, shuffle/weighted playback, one-time schedule blocks
- **M4** (complete): OTA broadcast (Icecast service), analytics & reporting dashboard, Celery/Redis worker infrastructure
- **M5** (complete): API documentation (`docs/API.md`), load testing (Locust — `backend/loadtests/`), Terraform IaC (`infra/terraform/`), schedule blocks admin UI, WebSocket real-time NowPlaying, code splitting
- **M6** (complete): Sponsor Client Portal — self-service portal with play history, campaign management (drafts + comments), Stripe billing, Resend email notifications, Claude AI email drafting

## MCP Servers
- **Playwright** — Browser automation for testing and screenshots. Configured in `~/.claude.json` under project MCP servers.
  - Command: `cmd /c npx -y @playwright/mcp@latest`
  - Tools: browser_navigate, browser_screenshot, browser_click, browser_fill, browser_snapshot, etc.

## Commands
```bash
# Run backend tests locally (SQLite, no external services needed)
cd backend && uv run pytest tests/ -v

# Lint (both run in CI and must stay green; style-only ruff rules are switched off in pyproject.toml)
cd backend && uv run ruff check app/ tests/
cd frontend && npm run lint

# Deploy backend to Vercel
cd backend && npx vercel --prod --yes

# Deploy frontend to Vercel
cd frontend && npx vercel --prod --yes

# Browser check of the control room against a fake backend (desktop + phone sizes)
cd frontend && npm run build && (npx vite preview --port 4173 &) && node e2e/control-room.mjs

# The control room with a phone held sideways (9 phone sizes, tap targets, every control, turning the phone)
cd frontend && BASE_URL=http://localhost:4173 node e2e/control-room-landscape.mjs

# The RDS page against a real backend (Postgres + seeded admin; see wiki/rds-feed.md)
cd frontend && BASE_URL=http://localhost:3000 API=http://localhost:8000 KBR_ADMIN_PASSWORD=... node e2e/rds-page.mjs

# Queue rows and level meters on car screens, tablets, phones and desktop (rows on screen, one-line titles, meters move)
cd frontend && BASE_URL=http://localhost:4173 node e2e/control-room-compact.mjs

# The Sponsors client list and client page at desktop and phone sizes (real backend with clients that have a running contract and plays)
cd frontend && BASE_URL=http://localhost:3000 API=http://localhost:8000 KBR_ADMIN_PASSWORD=... node e2e/sponsor-clients.mjs

# The Sponsorship board: week grid, one day on a phone, who is booked, Book into this slot (real backend)
cd frontend && BASE_URL=http://localhost:3000 API=http://localhost:8000 KBR_ADMIN_PASSWORD=... node e2e/sponsorship-board.mjs

# Book a sponsorship: the four steps, slot room, saving, desktop and iPhone (real backend with clients)
cd frontend && BASE_URL=http://localhost:3000 API=http://localhost:8000 KBR_ADMIN_PASSWORD=... node e2e/book-sponsorship.mjs

# Stations page: "Last song before Shabbos" (song and item at candle lighting from a library search, saves and keeps other settings), desktop and iPhone
cd frontend && BASE_URL=http://localhost:4313 node e2e/shabbos-ending-setting.mjs

# Stations page: "Omer count on the hour" (setting, recordings uploaded, what each hour says), fake backend, desktop and iPhone
cd frontend && BASE_URL=http://localhost:4313 node e2e/omer-count-setting.mjs

# Holiday Music page and the queue's holiday tag against a real backend (seed in wiki/holiday-music.md), desktop and iPhone
cd frontend && BASE_URL=http://localhost:3000 API=http://localhost:8000 KBR_ADMIN_PASSWORD=... node e2e/holiday-music.mjs

# Program Blocks page: "Only part of the year" season pickers (fake backend, desktop and iPhone)
cd frontend && BASE_URL=http://localhost:4173 node e2e/program-block-season.mjs

# Played list and RDS log: start times to the second on each station's clock, three stations, desktop and iPhone (fake backend)
cd frontend && BASE_URL=http://localhost:4173 node e2e/played-seconds.mjs

# Queue and library columns (drag, double-click to fit, kept, lined up with a 17 px scrollbar) and "Why it is in the queue" (playlist, block, step, links), three stations, desktop, car screen, iPhone
cd frontend && BASE_URL=http://localhost:4173 node e2e/queue-columns.mjs

# The control room's Song panel (category, not on this station, do not play, details) on desktop and phone sizes
cd frontend && BASE_URL=http://localhost:4173 node e2e/control-room-song-actions.mjs

# Stations page: "Station IDs" (on, where, minutes, category, hours; saves and keeps other settings), desktop and iPhone
cd frontend && BASE_URL=http://localhost:4313 node e2e/station-ids-setting.mjs

# The control room's Sponsor panel (tapping a sponsorship spot) on three stations, roles, desktop and phone sizes
cd frontend && BASE_URL=http://localhost:4173 node e2e/control-room-sponsor-panel.mjs

# Display findings of the page walk-through: Library cells and menus, Esc on dialogs, phone tables as cards, headings, labels
cd frontend && for s in library-display display-findings; do BASE_URL=http://localhost:4173 node e2e/$s.mjs; done

# Sound in the control room on an iPhone: the Listen button and tap-to-sound time, in Chromium made to follow Safari's tap rule
cd frontend && BASE_URL=http://localhost:4173 node e2e/control-room-iphone-sound.mjs

# iPhone: the stream taken away a second after Listen plays again by itself, and a moment of "nothing on air" does not stop it (Listen page and control room)
cd frontend && BASE_URL=http://localhost:4173 node e2e/iphone-stream-start.mjs

# The stream reconnects by itself, the Listen button says when this device is silent, meters from the engine with no click (fake backend + stand-in Icecast)
cd frontend && BASE_URL=http://localhost:4173 node e2e/stream-keeps-playing.mjs

# A new release published under an open page, then every late-loading screen opened (builds the site twice itself)
cd frontend && node e2e/stale-release.mjs

# Going on air from the Host Console against a real backend: one GO LIVE press, banner, meter, mic check, a Listen page opened in mid show, waiting callers
cd frontend && KBR_ADMIN_EMAIL=... KBR_ADMIN_PASSWORD=... node e2e/live-go-live.cjs <station id>

# Live video against a real backend (Chromium's fake camera; host page + Listen page, Dump cuts the picture; see wiki/live-shows.md)
cd frontend && KBR_ADMIN_EMAIL=... KBR_ADMIN_PASSWORD=... node e2e/live-video.cjs <station id>

# Every control room button, the Listen page, call-in and release-pickup timings, same fake backend (ONLY=<part> for one part; see wiki/audio-engine.md)
cd frontend && for s in control-room-actions listen-page call-in release-timing admin-misc; do BASE_URL=http://localhost:4173 node e2e/$s.mjs; done

# Listener pages in real WebKit (Safari's engine, WebKitGTK + Selenium; setup in the script's docstring and wiki/audio-engine.md)
cd frontend && (npx vite preview --port 4183 --strictPort &) && xvfb-run -a python3 e2e/webkit/listener-webkit.py

# The bug reporter's screenshot in real WebKit: each way of taking it, not blank, no-picture path, picture added by hand (same setup as above)
cd frontend && xvfb-run -a python3 e2e/webkit/bug-screenshot-webkit.py

# The bug report's picture vs a real screenshot, and 🐞 over every kind of dialog (fake backend, Chromium)
cd frontend && for s in bug-screenshot-faithful bug-over-dialogs bug-everywhere; do BASE_URL=http://localhost:4173 node e2e/$s.mjs; done

# Leaving pages in real WebKit logs no version.json console error (reads WebKit's own console via MiniBrowser)
cd frontend && xvfb-run -a python3 e2e/webkit/version-check-webkit.py

# Every staff page, the control room's lower tabs and the main dialogs on desktop and iPhone, failing on leftover dark navy (real backend + e2e/walkthrough-seed.py)
cd frontend && E2E_ADMIN_EMAIL=... E2E_ADMIN_PASS=... BASE_URL=http://localhost:4173 API_URL=http://localhost:8000/api/v1 SHOTS_DIR=/tmp/shots node e2e/qnet-look-shots.mjs

# Check every admin page at desktop and iPhone size (needs the site and a backend running; see wiki/admin-page-layout.md)
cd frontend && BASE_URL=http://localhost:3000 node e2e/admin-layout-audit.mjs

# Many stations x many listeners: does everyone switch songs together? (see wiki/multi-station-load.md)
cd backend && KBR_ADMIN_EMAIL=... KBR_ADMIN_PASSWORD=... uv run python loadtests/multi_station.py --base http://localhost:8000 --listeners 150

# Simulate whole broadcast days fast (virtual clock, fake engine, real scheduling code; see wiki/station-simulator.md)
cd backend && uv run python -m tests.sim.run --export tests/sim/fixtures/stations-2026-11-19.json --start 2026-11-19T00:00 --hours 24 --stations main

# Live shows in the simulator: host, delay, Dump, callers, hard stop, restarts, a show into Shabbos (see wiki/station-simulator.md)
cd backend && SIM_LONG=1 uv run pytest tests/test_live_show_sim.py -q

# Every Yom Tov of the coming year as broadcast days in the simulator (--list shows them; see wiki/station-simulator.md)
cd backend && uv run python -m tests.sim.yomtov --station main --index 0 --out /tmp/yomtov

# Run the real playout engine locally against the backend, with timing scenarios (see wiki/engine-harness.md)
cd backend && uv run python ../infra/streaming/harness/run_harness.py

# Run load tests (install locust first: pip install locust)
cd backend && locust -f loadtests/locustfile.py --host https://api.kbrlive.com


# Node.js path (not in system PATH)
export PATH="/c/Users/shmue/.node/node-v20.19.2-win-x64:$PATH"
```

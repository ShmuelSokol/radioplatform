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
│       ├── components/           # layout (Navbar, Layout), audio (AudioPlayer, NowPlaying)
│       ├── pages/admin/          # Dashboard, Stations, Assets, AssetUpload, Schedules, Rules, Users, Login, SongRequests, Archives
│       ├── pages/public/         # StationList, Listen, ProgramGuide, Hosts, Archives
│       ├── hooks/                # 22 custom hooks
│       ├── stores/               # Zustand: authStore, playerStore
│       └── types/                # TypeScript interfaces
├── docs/                         # API reference documentation
├── docker/                       # Dockerfiles, nginx.conf
├── infra/streaming/              # Playout engine server: radio.liq, setup.sh, Icecast, nginx, systemd
├── infra/local/                  # Local FM box: kbr_relay.py keeps the engine on air with no internet (wiki/local-fm-box.md)
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
| Sponsor | sponsor.py | Sponsor/ad management (linked to User via user_id) |
| SpotSchedule | spot_schedule.py | When a sponsor's spot airs: days, dayparts (morning/afternoon/evening/overnight), plays per daypart, date range |
| SpotRecording | spot_recording.py | A sponsorship spot in the making: script, wording review, audio, status (draft/ready/approved/rejected) |
| BugReport | bug_report.py | Report from the 🐞 button: words, screenshot, browser state, server log slice, GitHub issue and fix link (see `wiki/bug-reports.md`) |
| ProgramBlock | program_block.py | A named run of items (station ID, weather, PSA, sponsor places, programme) that airs as one event at set clock times: hard, soft, or soft with a limit |
| AuditLog | audit_log.py | Activity log row: who, what (label, category), station, before/after, status, IP, device, place (see `wiki/activity-log.md`) |
| HsSoftened | hs_softened.py | A timed queue item that HS off made wait, with its old timing so HS on restores it (see `wiki/station-modes.md`) |
| EngineHandoff | engine_handoff.py | A queue entry handed to the playout engine and its asset, so a start report for an entry deleted meanwhile still shows what is on air after a backend restart (pruned after 6 h; see `wiki/streaming-architecture.md`) |
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
| assets | assets.py | /assets |
| streams | streams.py | /streams |
| controls | controls.py | /controls |
| users | users.py | /users |
| queue | queue.py | /stations/{id}/queue (operator actions in one call each: `skip?fade=hard\|fade\|soft`, `fire-now`, `insert`, `loop`, `mix-over`, `panic`, `modes` (HS / Spots mode); see `wiki/operator-actions.md`, `wiki/station-modes.md`) |
| rules | rules.py | /rules |
| schedules | schedules.py | /schedules |
| now_playing | now_playing.py | /now-playing |
| websocket | websocket.py | /ws |
| scheduler | scheduler.py | /scheduler |
| holidays | holidays.py | /holidays |
| sponsors | sponsors.py | /sponsors |
| channels | channels.py | /channels |
| icecast | icecast.py | /icecast |
| analytics | analytics.py | /analytics |
| playlists | playlists.py | /playlists |
| sponsor_portal | sponsor_portal.py | /sponsor-portal |
| spot_schedules | spot_schedules.py | /spot-schedules |
| spot_recordings | spot_recordings.py | /spot-recordings |
| program_blocks | program_blocks.py | /program-blocks (CRUD + /preview) |
| databank | databank.py | /databank: KBR Studio style tables (items sorted by any column, every type of a table, spots with schedule end dates + PATCH end-date, one day's play log); see `wiki/databank.md` |
| copy_review | copy_review.py | /sponsorship (rules + copy review) |
| campaigns | campaigns.py | /campaigns: sponsorship campaigns. Reads (list with `?sponsor_id=`, detail, drafts, comments) for every staff role and sponsors (own only); create/edit/drafts/comments for managers and sponsors (staff name the `sponsor_id`); status for managers |
| billing | billing.py | /billing |
| ai_emails | ai_emails.py | /ai-emails |
| alerts | alerts.py | /alerts (list pages with skip/limit; POST /alerts/resolve-all; repeats of an open alert fold into it) |
| live_shows | live_shows.py | /live-shows |
| live_shows_ws | live_shows_ws.py | /ws/live (WebSocket): show events, host mic audio, and `/ws/live/station/{id}/listen` where listener pages hear a live show (see `wiki/live-shows.md`) |
| song_requests | song_requests.py | /song-requests |
| archives | archives.py | /archives |
| playout | playout.py | /playout/{station_id}/next, /started, /hello: called by the playout engine only, guarded by `X-Playout-Token` (no user login); /plan and /library (token only) for a local FM box's relay |
| site_release | site_release.py | /site-release (GET public; PUT mode, POST/DELETE push, GET tabs: admin) and WebSocket /ws/site that every tab keeps open (see `wiki/release-pickup.md`) |
| playout_status | playout_status.py | /playout-status (public): per station, server stream or browser player, and whether the engine answers and matches the queue (badge in control room, station list, Listen) |
| call_in | call_in.py | /call-in/{station_id} (public): a listener joins the live show's caller queue from the browser; their mic goes on `/ws/live/call/{call_id}` (see `wiki/live-shows.md`) |
| activity | activity.py | /activity (admin): who did what with filters and `after` for live updates, /activity/people (one line per person: counts for the period), /activity/filters, /activity/listeners (listening sessions); POST /activity/page-view (any signed-in user) |
| air_watch | air_watch.py | /air-watch (staff): recent on-air self-check findings |
| bug_reports | bug_reports.py | /bug-reports (POST open to all, rate-limited; GET staff; PATCH/resend admin) |
| weather_spots | weather_spots.py | /weather-spots/{date} and /time-announcements/{hour}: upload ready-made recordings (Shprach voice) for the daily weather spot and the reusable hourly time clips, both per region (`?region=jerusalem`); ElevenLabs is only the fallback |

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
| schedule_refresh_service.py | After a schedule, block or playlist change, replaces the unplayed automatic songs so the change takes over on time, and redraws them 15 min before a block's playlist takes over so it starts on time, not early; drops dated weather/time queued as songs (see `wiki/playlist-generation.md`) |
| schedule_lookahead_service.py | 7-day look-ahead of block overlaps, gaps, off-air windows and unplaced spots, using the engine's resolver (see `wiki/schedule-look-ahead.md`) |
| spot_schedule_service.py | Plans sponsor spots into clock slots or block places and keeps pending `ad_slot` queue entries in step; when blocks are over-booked it shares the room fairly, rolls plays to a later block and warns (see `wiki/spot-schedules.md`, `wiki/program-blocks.md`) |
| spot_voice_service.py | Makes spot audio from a script; Shprach call is a stub, placeholder tones until wired (see `wiki/spot-recordings.md`) |
| program_block_service.py | Expands program blocks into timed queue entries (`source="block"`) and replaces the built-in hourly time and weather (see `wiki/program-blocks.md`) |
| tts_service.py | ElevenLabs TTS generation |
| weather_service.py | OpenWeatherMap API (current + 3-day forecast) |
| weather_spot_service.py | Weather/time announcement generation + caching; stores uploaded daily weather spots per region (station `automation_config.weather_region`, default lakewood; ElevenLabs is the fallback for the default region only) |
| sun_service.py | Sunrise/sunset calculations (astral) |
| icecast_service.py | Icecast OTA broadcast source client |
| email_service.py | Resend transactional email (campaign updates, invoices, payments) |
| ai_email_service.py | Claude API-powered email drafting for manager outreach |
| copy_review_service.py | Checks a sponsor's proposed wording against the sponsorship rules in force (Claude API, keyword fallback) |
| alert_service.py | Alert creation, resolution, conflict detection, user notification dispatch |
| sms_service.py | Twilio SMS and WhatsApp notification delivery |
| live_show_service.py | Live show lifecycle (create, start, end, hard stop, call management) |
| twilio_voice_service.py | Twilio Voice call-in handling (hold TwiML, conference, hang up) |
| liquidsoap_client.py | Old telnet push client for Liquidsoap. Superseded by playout_engine.py; keep `LIQUIDSOAP_ENABLED` off |
| duration_backfill.py | Measures the length of items that have none (they broke queue reads and restarts); runs once in the background after each start, and on demand via `POST /assets/backfill-durations` |
| upcoming_analysis.py | Analyses the item on air and the next one in the background when their audio analysis is missing or older than `ANALYSIS_VERSION`, so song changes use real end and crossfade points (see `wiki/audio-engine.md`) |
| last_played.py | When each item last aired per station, kept in memory: one full read per station per process (and hourly), then only new plays are fetched. Used by playlist and program-block rotation and `GET /queue/last-played` |
| long_items.py | What a hard timed item does to a shiur or story on air: station `automation_config.timed_during_long_items` = pause (default; it resumes where it stopped), wait or cut; a soft item due with more than 5 min of the shiur left pauses it too (see `wiki/hourly-slots.md`) |
| hour_fit.py | Shortly before a hard timed item, swaps queue positions so a song (or pair) that ends near it replaces one that would be faded soon after starting; automatic songs only, both players; `automation_config.fit_before_hard_items: false` switches it off (see `wiki/hourly-slots.md`) |
| timed_groups.py | A hard timed group due while an earlier short one still plays is moved to follow it (browser check 90 s ahead, engine before hand-over; the engine gets it at the first group's time) (see `wiki/hourly-slots.md`) |
| tail_analysis.py | Song end and crossfade point from HTTP range reads (MP3 header + last 20 s), so re-analysis does not download whole files |
| call_in.py | Web call-in tokens (HMAC of the call id) and closing a call when the caller's page leaves |
| activity_log.py | Activity log: middleware that records every signed-in change, before/after capture from SQLAlchemy flushes, batched background writer (see `wiki/activity-log.md`) |
| bug_report_service.py | Copies bug reports to the private GitHub repo and sends ntfy pushes: "received" at once, then "fix ready" or "needs you" (see `wiki/bug-reports.md`) |
| air_watch.py | On-air self-check every 5 s: hard timed items late or not started, no time check at hh:00, dead air, item shown past its end, engine vs site drift, skipped time/weather/sponsorship; alerts + ntfy (see `wiki/air-watch.md`) |
| station_modes.py | HS (hard start) and Spots mode switches: softens hard timed items while HS is off and restores them; Spots off plans no sponsorships (see `wiki/station-modes.md`) |
| queue_positions.py | Renumbers a station's playing and pending queue entries from 1 in one statement; shared by the browser player's song change, the engine's start report and the blackout fill |
| playout_plan.py | Read-only plan (pending queue with engine URIs, blackouts) and library pages for a local FM box's relay (see `wiki/local-fm-box.md`) |
| sponsor_delivery.py | What a sponsor was promised against what aired: missed plays of the last 14 days and coming plays with no room or moved, for `GET /sponsor-portal/missed-plays` (see `wiki/spot-schedules.md`) |
| playout_engine.py | Link to the playout engine: hands it the next track, records what really went on air, sends Next/flush/timed commands (see `wiki/streaming-architecture.md`) |

## Frontend Details

### Pages
**Admin** (`frontend/src/pages/admin/`):
- Dashboard.tsx — Control room (operator screen): frozen transport bar, queue with insert-at-point, instant buttons, server-side library search, go-live panel. Panels in `pages/admin/controlroom/`; live actions go through `api/liveControl.ts`. See `wiki/control-room.md`
- Stations.tsx — Station management
- Assets.tsx — Audio asset management
- AssetUpload.tsx — File upload
- Schedules.tsx — Schedule management (create/edit/delete)
- ScheduleBlocks.tsx — Manage blocks & playlist entries within a schedule
- Rules.tsx — Scheduling rules
- Users.tsx — User management
- Login.tsx — Authentication
- Holidays.tsx — Sabbath/holiday blackout management
- Sponsors.tsx — Sponsor/ad management
- Campaigns.tsx — Sponsorship campaigns across sponsors: sponsor and status filters, New Campaign for a chosen sponsor (managers/admins; DJs and viewers view only)
- CampaignDetail.tsx — One campaign for staff: drafts, comments, Save Status. Drafts and comments panels are shared with the sponsor portal (`components/campaigns/CampaignParts.tsx`)
- ScheduleCheck.tsx — Look-ahead of schedule conflicts: what was settled automatically, what needs a choice
- SpotSchedules.tsx — Schedule sponsor spots by day and daypart, with a 7-day look-ahead
- SpotRecordings.tsx — Record Spots: write a script, make or upload audio, listen, approve into the library
- Databank.tsx — KBR Studio's database window: Songs / Jingles / Spots / Blocks tables, Find, Edit mode, day log to the millisecond
- ProgramBlocks.tsx — Named blocks ("Weather Sponsors"): items in order, clock times, hard or soft, next 24 hours
- SponsorshipRules.tsx — Edit the sponsorship wording rules (versioned) and try the wording check
- Playlists.tsx — Playlist template rotation management
- Analytics.tsx — Analytics & reporting dashboard
- SiteUpdates.tsx — Choose when open browsers move to a new release: automatic or only on push, push now or at a time, optionally including tabs that are playing; live list of open tabs
- Activity.tsx — Who did what: "By person" (one collapsible line per person with today's counts, opening their own timeline) or everything in order; filters by station, kind, dates; search; paging; live and a Listeners tab with each listening session
- BugReports.tsx — Reports from the 🐞 button with screenshots, status and fix links
- Alerts.tsx — Alerts management (filter, page through, resolve, resolve all, reopen, delete)
- LiveShows.tsx — Live show management (create, list, start, delete)
- HostConsole.tsx — Full-screen host broadcasting console with countdown, mic controls, caller queue
- CallScreener.tsx — Two-column call screener (waiting/approved/on-air management)
- SongRequests.tsx — Song request management (approve/reject/delete)
- Archives.tsx — Show archive management (CRUD, publish/unpublish)

**Public** (`frontend/src/pages/public/`):
- StationList.tsx — Browse stations
- Listen.tsx — Listen to a station (HLS primary + client-side fallback, song requests)
- ProgramGuide.tsx — Public EPG with station/date picker and timeline
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

### Hooks (`frontend/src/hooks/`) — 22 hooks
| Hook | Description |
|------|-------------|
| useAuth.ts | Authentication state + mutations |
| useStations.ts | Station CRUD queries/mutations |
| useAssets.ts | Asset queries/mutations |
| useQueue.ts | Queue management + useWeatherPreview |
| useSchedules.ts | Schedule/block/playlist entry hooks |
| useRules.ts | Scheduling rules |
| useUsers.ts | User management |
| useAudioPlayer.ts | Audio playback controls |
| useAudioEngine.ts | Audio engine integration |
| useNowPlaying.ts | Now-playing polling |
| useNowPlayingWS.ts | Now-playing WebSocket (with polling fallback) |
| usePlaylists.ts | Playlist template CRUD + asset type combos |
| useSponsorPortal.ts | Sponsor play history, stats, upcoming schedule |
| useCampaigns.ts | Campaign CRUD (list takes an optional sponsor), status, drafts, comments |
| useBilling.ts | Invoice list + billing summary |
| useAlerts.ts | Alert list, unresolved count, resolve/reopen/delete mutations |
| useLiveShows.ts | Live show CRUD, start/end, call approve/reject/on-air mutations |
| useLiveShowWS.ts | Live show WebSocket (real-time callers, countdown, with polling fallback) |
| useHostAudio.ts | Browser mic capture as 24 kHz PCM, binary WS streaming, VU meter; plays the callers on air sent back by the server |
| useLiveAudio.ts | Plays a station's live show (host mic relayed by the server) and tells the page to silence the music meanwhile |
| useSongRequests.ts | Song request list, submit, approve/reject/delete mutations |
| useArchives.ts | Show archive CRUD queries/mutations |

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
- `/admin/campaigns` → AdminCampaigns (staff; managers/admins change, DJs/viewers view only)
- `/admin/campaigns/:id` → AdminCampaignDetail (same roles)
- `/admin/schedule-check` → ScheduleCheck (protected)
- `/admin/spot-schedule` → SpotSchedules (protected)
- `/admin/spot-recordings` → SpotRecordings (protected)
- `/admin/databank` → Databank (protected)
- `/admin/program-blocks` → ProgramBlocks (protected)
- `/admin/sponsorship-rules` → SponsorshipRules (protected)
- `/admin/playlists` → Playlists (protected)
- `/admin/alerts` → Alerts (protected)
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
- **Auth**: JWT Bearer tokens. Roles: admin, manager, dj, viewer, sponsor. Auth guards: `require_admin` (admin only), `require_manager` (admin + manager), `require_dj_or_manager` (admin + manager + dj), `require_staff` (everyone except sponsors — use this, not bare `get_current_user`, for staff-only reads), `require_sponsor` (sponsor only), `require_sponsor_or_manager` (admin + manager + sponsor). The frontend mirrors these in `frontend/src/auth/access.ts` (route → roles); when you add a screen or change a guard, update both, and `backend/tests/test_role_access.py`. `canChange()` there says who may change things on a screen (drives the "View only" marker); any refused action shows an app-wide "not allowed" notice (`components/layout/ViewOnlyNotice.tsx`).
- **Station permissions**: staff can be limited to named stations (`User.station_ids`), and a protected station (`Station.protected`, the FM feed) needs an explicit assignment. Rule: `may_operate_station`; guard: `require_station_operator`. See `wiki/station-permissions.md`.
- **Caller address**: rate limits, listener IPs and the activity log take the caller's address from `app/core/client_ip.py` (rightmost non-internal X-Forwarded-For hop); never read the header by hand. See `wiki/client-ip.md`.
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
| `ICECAST_HOST` | Icecast server hostname (empty to disable) | For OTA |
| `ICECAST_PORT` | Icecast server port (default: 8000) | For OTA |
| `ICECAST_SOURCE_PASSWORD` | Icecast source password (no default; empty or "hackme" = nothing streams) | For OTA |
| `ICECAST_MOUNT` | Icecast mount point (default: /live) | For OTA |
| `STRIPE_SECRET_KEY` | Stripe secret key (empty to disable) | For billing |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signing secret | For billing |
| `STRIPE_PRICE_ID` | Stripe price ID | For billing |
| `RESEND_API_KEY` | Resend API key (empty to disable) | For emails |
| `RESEND_FROM_EMAIL` | Sender email (default: noreply@kolbramah.com) | For emails |
| `ANTHROPIC_API_KEY` | Claude API key for AI email drafting | For AI emails |
| `TWILIO_ACCOUNT_SID` | Twilio account SID (empty to disable) | For SMS/WhatsApp alerts |
| `TWILIO_AUTH_TOKEN` | Twilio auth token | For SMS/WhatsApp alerts |
| `TWILIO_PHONE_NUMBER` | Twilio phone number | For SMS/WhatsApp alerts |
| `TWILIO_VOICE_NUMBER` | Dedicated voice number for live call-ins | For live shows |
| `LIVE_SHOW_HOLD_MUSIC_URL` | Public URL to hold music MP3 | For live shows |
| `BACKEND_PUBLIC_URL` | Public backend URL for Twilio callbacks | For live shows |
| `EMERGENCY_FALLBACK_CATEGORY` | Unused since dead air restarts the queue instead (kept so old settings still load) | No |
| `SILENCE_DETECTION_SECONDS` | Seconds of silence before alert (default: 30) | For silence detection |
| `MAX_UPLOAD_MB` | Largest library upload in MB (default 600); bigger files get 413 | No |
| `LIQUIDSOAP_ENABLED` | Enable Liquidsoap (remote VPS) | For streaming |
| `LIQUIDSOAP_HOST` | VPS hostname for Liquidsoap telnet | For streaming |
| `LIQUIDSOAP_TELNET_PORT` | Liquidsoap telnet port (default: 1234) | For streaming |
| `ICECAST_STREAM_URL` | Stream base URL (e.g., https://stream.kbrlive.com/live); an engine station streams at `<base>-<station id>` | For streaming |
| `PLAYOUT_ENGINE_ENABLED` | Let the playout engine drive the listed stations (default false) | For the engine |
| `PLAYOUT_ENGINE_STATION_ID` | Comma-separated stations the engine plays (one engine each; setup.sh prints the list) | For the engine |
| `PLAYOUT_ENGINE_URL` | Engine control base (e.g., https://stream.kbrlive.com/control); commands go to `<base>/<station id>/...` | For the engine |
| `PLAYOUT_ENGINE_TOKEN` | Shared secret sent both ways in `X-Playout-Token` | For the engine |
| `BUG_REPORTS_GITHUB_TOKEN` | Fine-grained token for the private bug-report repo (Contents + Issues write); unset = reports stay in the database only | For bug triage |
| `BUG_REPORTS_REPO` | Private repo for bug reports (default ShmuelSokol/kbr-bug-reports) | No |
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
- **Vercel IPv6**: Direct Supabase connection fails (IPv6 not supported). Use pooler URL.
- **Password URL encoding**: `/` → `%2F`, `[` → `%5B`, `]` → `%5D` in DATABASE_URL
- **Pydantic schemas**: ID fields use `uuid.UUID | str` for cross-DB compatibility (SQLite returns strings)
- **Frontend Vercel deploy**: Repo-local git email is set to `shmuelsokol@yahoo.com` (matching the Vercel account). Deploy directly: `cd frontend && npx vercel --prod --yes`.
- **Weather caching**: Weather TTS audio is cached per 15-min slot. Text changes don't take effect until the next slot.
- **Playout engine**: Liquidsoap 2.2 + Icecast on its own server (not Railway), set up by `infra/streaming/setup.sh` (Ubuntu 24.04). The engine pulls the next track and reports real starts; for an engine station nothing in the backend advances playback by duration. Off until `PLAYOUT_ENGINE_*` is set. The older telnet path (`LIQUIDSOAP_ENABLED`) must stay off.

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

# Every control room button, the Listen page, call-in and release-pickup timings, same fake backend (ONLY=<part> for one part; see wiki/audio-engine.md)
cd frontend && for s in control-room-actions listen-page call-in release-timing; do BASE_URL=http://localhost:4173 node e2e/$s.mjs; done

# Listener pages in real WebKit (Safari's engine, WebKitGTK + Selenium; setup in the script's docstring and wiki/audio-engine.md)
cd frontend && (npx vite preview --port 4183 --strictPort &) && xvfb-run -a python3 e2e/webkit/listener-webkit.py

# Check every admin page at desktop and iPhone size (needs the site and a backend running; see wiki/admin-page-layout.md)
cd frontend && BASE_URL=http://localhost:3000 node e2e/admin-layout-audit.mjs

# Many stations x many listeners: does everyone switch songs together? (see wiki/multi-station-load.md)
cd backend && KBR_ADMIN_EMAIL=... KBR_ADMIN_PASSWORD=... uv run python loadtests/multi_station.py --base http://localhost:8000 --listeners 150

# Run the real playout engine locally against the backend, with timing scenarios (see wiki/engine-harness.md)
cd backend && uv run python ../infra/streaming/harness/run_harness.py

# Run load tests (install locust first: pip install locust)
cd backend && locust -f loadtests/locustfile.py --host https://api.kbrlive.com


# Node.js path (not in system PATH)
export PATH="/c/Users/shmue/.node/node-v20.19.2-win-x64:$PATH"
```

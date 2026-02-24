# Studio Kol Bramah — Project Guide

> **IMPORTANT**: Keep this file current. Whenever you add models, routes, services, hooks, pages, or env vars — update this file to reflect the change. This is the single source of truth for the project.

## What is this?
Multi-channel radio streaming platform with playlist automation, ad insertion, weather/time announcements (ElevenLabs TTS), silence trimming, timezone-aware scheduling (Sabbath/holiday blackouts, sunset/sunrise rules), and admin + public listener UIs. Includes a Telegram bot for remote development via Claude.

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
- **Bot**: Telegram bot with Claude API + OpenAI Whisper for voice transcription
- **Workers**: Celery + Redis (not yet deployed — needed for media processing)
- **Media**: FFmpeg for transcoding, clipping, HLS generation
- **Streaming**: Liquidsoap + Icecast on dedicated VPS for MP3 streaming with crossfade + loudness normalization (client-side Web Audio fallback)

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
├── bot/
│   ├── main.py                   # Telegram bot — Claude Code over Telegram
│   ├── pyproject.toml            # Bot dependencies
│   ├── .env                      # Bot secrets (not committed)
│   └── .env.example              # Template
├── docs/                         # API reference documentation
├── docker/                       # Dockerfiles, nginx.conf
├── infra/streaming/              # VPS config: Liquidsoap, Icecast, Nginx, systemd
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
| queue | queue.py | /stations/{id}/queue |
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
| campaigns | campaigns.py | /campaigns |
| billing | billing.py | /billing |
| ai_emails | ai_emails.py | /ai-emails |
| alerts | alerts.py | /alerts |
| live_shows | live_shows.py | /live-shows |
| live_shows_ws | live_shows_ws.py | /ws/live (WebSocket) |
| song_requests | song_requests.py | /song-requests |
| archives | archives.py | /archives |

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
| scheduler_engine.py | Background scheduling engine |
| queue_replenish_service.py | Auto-replenish playback queue |
| tts_service.py | ElevenLabs TTS generation |
| weather_service.py | OpenWeatherMap API (current + 3-day forecast) |
| weather_spot_service.py | Weather/time announcement generation + caching |
| sun_service.py | Sunrise/sunset calculations (astral) |
| icecast_service.py | Icecast OTA broadcast source client |
| email_service.py | Resend transactional email (campaign updates, invoices, payments) |
| ai_email_service.py | Claude API-powered email drafting for manager outreach |
| alert_service.py | Alert creation, resolution, conflict detection, user notification dispatch |
| sms_service.py | Twilio SMS and WhatsApp notification delivery |
| live_show_service.py | Live show lifecycle (create, start, end, hard stop, call management) |
| twilio_voice_service.py | Twilio Voice call-in handling (hold TwiML, conference, hang up) |
| live_audio_mixer.py | Audio mixer stub (future ffmpeg host+caller mixing) |
| liquidsoap_client.py | Async TCP telnet client for Liquidsoap on VPS (push tracks, skip, status, health check) |

## Frontend Details

### Pages
**Admin** (`frontend/src/pages/admin/`):
- Dashboard.tsx — Main control panel with queue, library, cart machine, weather preview
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
- Playlists.tsx — Playlist template rotation management
- Analytics.tsx — Analytics & reporting dashboard
- Alerts.tsx — Alerts management (filter, resolve, reopen, delete)
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

**Sponsor Portal** (`frontend/src/pages/sponsor/`):
- Login.tsx — Sponsor login (separate from admin, indigo-themed)
- Dashboard.tsx — Play history stats + table + upcoming schedule
- Campaigns.tsx — Campaign list with create form
- CampaignDetail.tsx — Campaign detail with drafts + comments thread
- Billing.tsx — Invoice table with Stripe checkout + billing summary

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
| useCampaigns.ts | Campaign CRUD, drafts, comments |
| useBilling.ts | Invoice list + billing summary |
| useAlerts.ts | Alert list, unresolved count, resolve/reopen/delete mutations |
| useLiveShows.ts | Live show CRUD, start/end, call approve/reject/on-air mutations |
| useLiveShowWS.ts | Live show WebSocket (real-time callers, countdown, with polling fallback) |
| useHostAudio.ts | Browser mic capture, MediaRecorder, binary WS streaming, VU meter |
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
- `/admin/playlists` → Playlists (protected)
- `/admin/alerts` → Alerts (protected)
- `/admin/analytics` → Analytics (protected)
- `/admin/live` → LiveShows (protected)
- `/admin/live/:showId/host` → HostConsole (protected)
- `/admin/live/:showId/screen` → CallScreener (protected)
- `/admin/requests` → SongRequests (protected)
- `/admin/archives` → AdminArchives (protected)
- `/guide` → ProgramGuide
- `/hosts` → Hosts
- `/archives` → Archives
- `/sponsor/login` → SponsorLogin
- `/sponsor/dashboard` → SponsorDashboard (sponsor-protected, SponsorLayout)
- `/sponsor/campaigns` → SponsorCampaigns (sponsor-protected)
- `/sponsor/campaigns/:id` → CampaignDetail (sponsor-protected)
- `/sponsor/billing` → SponsorBilling (sponsor-protected)

## Telegram Bot (`bot/`)
Claude Code accessible over Telegram for remote development.

**Capabilities**: Read/write/edit files, run bash, git, deploy to Vercel, voice notes (Whisper transcription)

**Tools**: read_file, write_file, edit_file, run_bash, list_files

**Config** (in `bot/.env`):
- `TELEGRAM_BOT_TOKEN` — from @BotFather
- `ANTHROPIC_API_KEY` — Claude API key
- `OPENAI_API_KEY` — for Whisper voice transcription (optional)

**Run locally**: `cd bot && uv run python main.py`

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
- **Auth**: JWT Bearer tokens. Roles: admin, manager, viewer, sponsor. Auth guards: `require_admin` (admin only), `require_manager` (admin + manager), `require_sponsor` (sponsor only), `require_sponsor_or_manager` (admin + manager + sponsor).
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
| `JWT_SECRET_KEY` | Secret for JWT signing | Yes |
| `CORS_ORIGINS` | JSON array of allowed origins | Yes |
| `APP_ENV` | "production" | Yes |
| `APP_DEBUG` | "false" in production | No |
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
| `ICECAST_SOURCE_PASSWORD` | Icecast source password | For OTA |
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
| `EMERGENCY_FALLBACK_CATEGORY` | Asset category for emergency fallback (default: "emergency") | For silence detection |
| `SILENCE_DETECTION_SECONDS` | Seconds of silence before alert (default: 30) | For silence detection |
| `LIQUIDSOAP_ENABLED` | Enable Liquidsoap (remote VPS) | For streaming |
| `LIQUIDSOAP_HOST` | VPS hostname for Liquidsoap telnet | For streaming |
| `LIQUIDSOAP_TELNET_PORT` | Liquidsoap telnet port (default: 1234) | For streaming |
| `ICECAST_STREAM_URL` | Public Icecast stream URL (e.g., https://stream.kbrlive.com/live) | For streaming |

### Frontend (Vercel)
- `VITE_API_URL` — Backend API base URL (https://api.kbrlive.com/api/v1)

## Default Credentials
- Admin: `admin` / `613Radio`
- Seed endpoint: `POST /api/v1/auth/seed` (creates or updates admin user)

## Known Gotchas
- **ALWAYS verify live site after deploy**: After deploying, test the live URLs (frontend + backend health + key API endpoints) to confirm the site works. Don't assume deploys succeed — check `curl https://api.kbrlive.com/health` and try loading key frontend pages.
- **Railway deploys from GitHub**: Auto-deploy is enabled on `main` branch with root directory `/backend`. If deploys fail, check Railway dashboard → service → Deployments tab for build logs.
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
- **Liquidsoap + Icecast**: Runs on a dedicated VPS (not Railway). Backend communicates via TCP telnet. Listeners connect directly to Icecast MP3 stream via `<audio>` element. If VPS is down, clients auto-fall back to the dual-buffer Web Audio engine. Set `LIQUIDSOAP_ENABLED=false` to disable. VPS config in `infra/streaming/`.

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

# Deploy backend to Vercel
cd backend && npx vercel --prod --yes

# Deploy frontend to Vercel
cd frontend && npx vercel --prod --yes

# Run load tests (install locust first: pip install locust)
cd backend && locust -f loadtests/locustfile.py --host https://api.kbrlive.com

# Run Telegram bot locally
cd bot && uv run python main.py

# Node.js path (not in system PATH)
export PATH="/c/Users/shmue/.node/node-v20.19.2-win-x64:$PATH"
```

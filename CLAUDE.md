# Studio Kol Bramah — Project Guide

> **IMPORTANT**: Keep this file current. Whenever you add models, routes, services, hooks, pages, or env vars — update this file to reflect the change. This is the single source of truth for the project.

## What is this?
Multi-channel radio streaming platform with playlist automation, ad insertion, weather/time announcements (ElevenLabs TTS), silence trimming, timezone-aware scheduling (Sabbath/holiday blackouts, sunset/sunrise rules), and admin + public listener UIs. Includes a Telegram bot for remote development via Claude.

## Live URLs
- **Frontend**: https://studio-kolbramah-radio.vercel.app
- **Backend API**: https://studio-kolbramah-api-production.up.railway.app
- **GitHub**: https://github.com/ShmuelSokol/radioplatform

## Tech Stack
- **Frontend**: React 18 + TypeScript + Vite + Tailwind CSS + hls.js + Zustand + React Query
- **Backend**: Python 3.12 + FastAPI (async) + SQLAlchemy 2.0 (asyncpg)
- **Database**: Supabase PostgreSQL (transaction pooler on port 6543)
- **Hosting**: Frontend on Vercel, Backend on Railway (persistent process — enables real WebSockets + scheduler)
- **TTS**: ElevenLabs API for weather/time announcements
- **Weather**: OpenWeatherMap API (current + 3-day forecast)
- **Storage**: Supabase Storage for audio files (bucket: `audio`)
- **Bot**: Telegram bot with Claude API + OpenAI Whisper for voice transcription
- **Workers**: Celery + Redis (not yet deployed — needed for media processing)
- **Media**: FFmpeg for transcoding, clipping, HLS generation

## Project Structure
```
radioplatform/
├── backend/
│   ├── api/index.py              # Vercel serverless entry point
│   ├── app/main.py               # FastAPI app factory
│   ├── app/config.py             # Pydantic BaseSettings (all env vars)
│   ├── app/core/                 # security (JWT), dependencies (auth guards), exceptions, middleware
│   ├── app/db/                   # async engine (statement_cache_size=0), Base, session
│   ├── app/models/               # 18 SQLAlchemy models
│   ├── app/schemas/              # Pydantic v2 request/response schemas
│   ├── app/api/v1/               # 12 route handlers
│   ├── app/services/             # 14 business logic services
│   ├── app/workers/tasks/        # Celery tasks (media processing)
│   ├── app/streaming/            # HLS generator, playlist engine
│   ├── tests/                    # pytest async tests
│   ├── vercel.json               # Rewrites all routes to api/index
│   └── pyproject.toml            # bcrypt==4.1.3 pinned for passlib compat
├── frontend/
│   └── src/
│       ├── api/                  # 7 API client modules + Axios client with JWT interceptor
│       ├── components/           # layout (Navbar, Layout), audio (AudioPlayer, NowPlaying)
│       ├── pages/admin/          # Dashboard, Stations, Assets, AssetUpload, Schedules, Rules, Users, Login
│       ├── pages/public/         # StationList, Listen
│       ├── hooks/                # 11 custom hooks
│       ├── stores/               # Zustand: authStore, playerStore
│       └── types/                # TypeScript interfaces
├── bot/
│   ├── main.py                   # Telegram bot — Claude Code over Telegram
│   ├── pyproject.toml            # Bot dependencies
│   ├── .env                      # Bot secrets (not committed)
│   └── .env.example              # Template
├── docs/                         # API reference documentation
├── docker/                       # Dockerfiles, nginx.conf
└── infra/terraform/              # Terraform IaC (Vercel frontend provisioning)
```

## Backend Details

### Models (`backend/app/models/`)
| Model | File | Description |
|-------|------|-------------|
| User | user.py | Admin/manager/viewer with JWT auth |
| Station | station.py | Radio station config |
| Asset | asset.py | Audio file metadata |
| Category | category.py | Asset categorization |
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
| Sponsor | sponsor.py | Sponsor/ad management |
| ScheduleRule | schedule_rule.py | Schedule-specific rules |

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
- Analytics.tsx — Analytics & reporting dashboard

**Public** (`frontend/src/pages/public/`):
- StationList.tsx — Browse stations
- Listen.tsx — Listen to a station

### Hooks (`frontend/src/hooks/`) — 11 hooks
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

### API Clients (`frontend/src/api/`) — 7 modules
auth.ts, stations.ts, assets.ts, queue.ts, rules.ts, users.ts, client.ts (Axios + JWT interceptor)

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
- `/admin/analytics` → Analytics (protected)

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
- **Auth**: JWT Bearer tokens. Roles: admin, manager, viewer. Auth guards: `require_admin` (admin only), `require_manager` (admin + manager).
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

### Frontend (Vercel)
- `VITE_API_URL` — Backend API base URL (https://studio-kolbramah-api.vercel.app/api/v1)

## Default Credentials
- Admin: `admin` / `613Radio`
- Seed endpoint: `POST /api/v1/auth/seed` (creates or updates admin user)

## Known Gotchas
- **ALWAYS verify live site after deploy**: After deploying, test the live URLs (frontend + backend health + key API endpoints) to confirm the site works. Don't assume deploys succeed — check `curl https://studio-kolbramah-api-production.up.railway.app/health` and try loading key frontend pages.
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

## Milestones
- **M1** (complete): Backend skeleton, auth, station CRUD, asset upload, React frontend — DEPLOYED
- **M2** (complete): Scheduling engine, admin schedule UI, now-playing WebSocket, queue system, weather/time announcements
- **M3** (complete): Sponsor insertion, holiday/Sabbath blackouts, sunset/sunrise scheduling, multi-channel broadcast, shuffle/weighted playback, one-time schedule blocks
- **M4** (complete): OTA broadcast (Icecast service), analytics & reporting dashboard, Celery/Redis worker infrastructure
- **M5** (complete): API documentation (`docs/API.md`), load testing (Locust — `backend/loadtests/`), Terraform IaC (`infra/terraform/`), schedule blocks admin UI, WebSocket real-time NowPlaying, code splitting

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
cd backend && locust -f loadtests/locustfile.py --host https://studio-kolbramah-api-production.up.railway.app

# Run Telegram bot locally
cd bot && uv run python main.py

# Node.js path (not in system PATH)
export PATH="/c/Users/shmue/.node/node-v20.19.2-win-x64:$PATH"
```

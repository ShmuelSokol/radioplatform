# Studio Kol Bramah — Project Guide

## What is this?
Multi-channel radio streaming platform with playlist automation, ad insertion, silence trimming, timezone-aware scheduling (Sabbath/holiday blackouts, sunset/sunrise rules), and admin + public listener UIs.

## Live URLs
- **Frontend**: https://studio-kolbramah-radio.vercel.app
- **Backend API**: https://studio-kolbramah-api.vercel.app
- **GitHub**: https://github.com/ShmuelSokol/radioplatform

## Tech Stack
- **Frontend**: React 18 + TypeScript + Vite + Tailwind CSS + hls.js + Zustand + React Query
- **Backend**: Python 3.12 + FastAPI (async) + SQLAlchemy 2.0 (asyncpg)
- **Database**: Supabase PostgreSQL (transaction pooler on port 6543)
- **Hosting**: Vercel (both frontend and backend serverless)
- **Workers**: Celery + Redis (not yet deployed — needed for media processing)
- **Storage**: S3-compatible (not yet configured — needed for audio uploads)
- **Media**: FFmpeg for transcoding, clipping, HLS generation

## Project Structure
```
radioplatform/
├── backend/
│   ├── api/index.py          # Vercel serverless entry point
│   ├── app/main.py           # FastAPI app factory
│   ├── app/config.py         # Pydantic BaseSettings (env vars, auto-fixes DB URL)
│   ├── app/core/             # security (JWT), dependencies (auth guards), exceptions, middleware
│   ├── app/db/               # async engine (statement_cache_size=0 for Supabase pooler), Base, session
│   ├── app/models/           # 10 SQLAlchemy models (User, Station, Asset, etc.)
│   ├── app/schemas/          # Pydantic v2 request/response schemas (uuid.UUID | str for IDs)
│   ├── app/api/v1/           # Route handlers (auth, stations, assets, streams, controls)
│   ├── app/services/         # Business logic (auth, station, asset, media, storage, playback)
│   ├── app/workers/tasks/    # Celery tasks (media processing)
│   ├── app/streaming/        # HLS generator, playlist engine
│   ├── tests/                # pytest async tests (19 tests, all passing with SQLite)
│   ├── vercel.json           # Rewrites all routes to api/index
│   └── pyproject.toml        # bcrypt==4.1.3 pinned for passlib compat
├── frontend/
│   └── src/
│       ├── api/client.ts     # Axios client with JWT interceptor, uses VITE_API_URL
│       ├── components/       # layout (Navbar, Layout), audio (AudioPlayer, NowPlaying)
│       ├── pages/            # admin/ (Login, Dashboard, Stations, Assets, Upload)
│       │                       public/ (StationList, Listen)
│       ├── hooks/            # useAuth, useStations, useAssets, useNowPlaying, useAudioPlayer
│       ├── stores/           # Zustand: authStore, playerStore
│       └── types/            # TypeScript interfaces
└── docker/                   # Dockerfiles, nginx.conf (for Docker-based dev)
```

## Key Conventions
- **Backend**: All endpoints under `/api/v1/`. UUID primary keys. Async SQLAlchemy sessions.
- **Auth**: JWT Bearer tokens. Roles: admin, manager, viewer. Admin/manager guards on write endpoints.
- **Frontend**: All API calls through `src/api/client.ts` (auto JWT refresh). Zustand for global state, React Query for server state.
- **Models**: Use `UUIDPrimaryKeyMixin` + `TimestampMixin` from `app/db/base.py`.
- **Tests**: pytest-asyncio with SQLite + type compilation hooks (PG_UUID→VARCHAR, JSONB→TEXT, ENUM→VARCHAR).
- **Serverless**: No lifespan events. Tables created lazily via middleware on first request. Statement cache disabled for Supabase pooler.

## Important Files
- `backend/app/config.py` — All env var config. Auto-converts `postgres://` → `postgresql+asyncpg://`. Redis/S3 optional.
- `backend/app/db/engine.py` — Async engine with `statement_cache_size=0` for Supabase transaction pooler
- `backend/app/core/dependencies.py` — Auth dependency injection (get_current_user, require_admin)
- `backend/app/api/v1/__init__.py` — Router registration (add new routers here)
- `backend/api/index.py` — Vercel serverless entry point (adds backend root to sys.path)
- `frontend/src/api/client.ts` — Axios instance with JWT interceptor, `VITE_API_URL` env var
- `frontend/src/App.tsx` — All routes defined here

## Environment Variables

### Backend (Vercel)
- `DATABASE_URL` — Supabase PostgreSQL pooler URI (URL-encode special chars in password)
- `JWT_SECRET_KEY` — Secret for JWT signing
- `CORS_ORIGINS` — JSON array of allowed origins
- `REDIS_URL` — Empty string to disable (Redis not needed for basic CRUD)
- `S3_ENDPOINT_URL` — Empty string to disable (falls back to local storage)
- `APP_DEBUG` — "false" in production
- `APP_ENV` — "production"

### Frontend (Vercel)
- `VITE_API_URL` — Backend API base URL (https://studio-kolbramah-api.vercel.app/api/v1)

## Default Credentials
- Admin: `admin` / `613Radio`
- Seed endpoint: `POST /api/v1/auth/seed` (creates or updates admin user)

## Known Gotchas
- **bcrypt**: Must pin `bcrypt==4.1.3` — passlib breaks with bcrypt 5.x
- **Supabase pooler**: Must use port 6543 (transaction mode) + `statement_cache_size=0`
- **Vercel IPv6**: Direct Supabase connection fails (IPv6 not supported). Use pooler URL.
- **Password URL encoding**: `/` → `%2F`, `[` → `%5B`, `]` → `%5D` in DATABASE_URL
- **Pydantic schemas**: ID fields use `uuid.UUID | str` for cross-DB compatibility (SQLite returns strings)

## Milestones
- **M1** (current): Backend skeleton, auth, station CRUD, asset upload, React frontend — DEPLOYED
- **M2**: Scheduling engine, admin schedule UI, now-playing websocket
- **M3**: Sponsor insertion, holiday/timezone/sunset logic, multi-channel
- **M4**: OTA broadcast (Icecast), reporting, monitoring
- **M5**: Docs, load testing, Terraform deployment

## Commands
```bash
# Run backend tests locally (SQLite, no external services needed)
cd backend && uv run pytest tests/ -v

# Deploy backend to Vercel
cd backend && npx vercel --prod --yes

# Deploy frontend to Vercel
cd frontend && npx vercel --prod --yes

# Node.js path (not in system PATH)
export PATH="/c/Users/shmue/.node/node-v20.19.2-win-x64:$PATH"
```

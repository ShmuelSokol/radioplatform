# RadioPlatform — Project Guide

## What is this?
Multi-channel radio streaming platform with playlist automation, ad insertion, silence trimming, timezone-aware scheduling (Sabbath/holiday blackouts, sunset/sunrise rules), and admin + public listener UIs.

## Tech Stack
- **Frontend**: React 18 + TypeScript + Vite + Tailwind CSS + hls.js + Zustand + React Query
- **Backend**: Python 3.12 + FastAPI (async) + SQLAlchemy 2.0 + Alembic
- **Workers**: Celery + Redis
- **Database**: PostgreSQL 16 (Neon in prod) + Redis 7 (Upstash in prod)
- **Storage**: S3-compatible (MinIO local, AWS S3 / Cloudflare R2 in prod)
- **Media**: FFmpeg for transcoding, clipping, HLS generation
- **Deployment**: Vercel (frontend), Render/Railway (backend), Neon (DB), Upstash (Redis)

## Project Structure
```
radioplatform/
├── backend/
│   ├── app/main.py          # FastAPI app factory
│   ├── app/config.py        # Pydantic BaseSettings (env vars)
│   ├── app/core/            # security (JWT), dependencies (auth guards), exceptions, middleware
│   ├── app/db/              # async engine, Base class, session
│   ├── app/models/          # 10 SQLAlchemy models (User, Station, Asset, etc.)
│   ├── app/schemas/         # Pydantic v2 request/response schemas
│   ├── app/api/v1/          # Route handlers (auth, stations, assets, streams, controls)
│   ├── app/services/        # Business logic (auth, station, asset, media, storage, playback)
│   ├── app/workers/tasks/   # Celery tasks (media processing)
│   ├── app/streaming/       # HLS generator, playlist engine
│   ├── alembic/             # Database migrations
│   └── tests/               # pytest async tests
├── frontend/
│   └── src/
│       ├── api/             # Axios client + endpoint modules
│       ├── components/      # layout (Navbar, Layout), audio (AudioPlayer, NowPlaying)
│       ├── pages/           # admin/ (Login, Dashboard, Stations, Assets, Upload)
│       │                      public/ (StationList, Listen)
│       ├── hooks/           # useAuth, useStations, useAssets, useNowPlaying, useAudioPlayer
│       ├── stores/          # Zustand: authStore, playerStore
│       └── types/           # TypeScript interfaces
└── docker/                  # Dockerfiles, nginx.conf (for Docker-based dev)
```

## Key Conventions
- **Backend**: All endpoints under `/api/v1/`. UUID primary keys. Async SQLAlchemy sessions.
- **Auth**: JWT Bearer tokens. Roles: admin, manager, viewer. Admin/manager guards on write endpoints.
- **Frontend**: All API calls through `src/api/client.ts` (auto JWT refresh). Zustand for global state, React Query for server state.
- **Models**: Use `UUIDPrimaryKeyMixin` + `TimestampMixin` from `app/db/base.py`.
- **Tests**: pytest-asyncio with ASGI test client. Override `get_db` dependency for test sessions.

## Important Files
- `backend/app/config.py` — All env var configuration (DATABASE_URL, REDIS_URL, JWT keys, S3, etc.)
- `backend/app/core/dependencies.py` — Auth dependency injection (get_current_user, require_admin)
- `backend/app/api/v1/__init__.py` — Router registration (add new routers here)
- `frontend/src/api/client.ts` — Axios instance with JWT interceptor
- `frontend/src/App.tsx` — All routes defined here

## Default Credentials (dev seed)
- Admin: `admin@radioplatform.com` / `admin123`

## Milestones
- **M1** (current): Backend skeleton, auth, station CRUD, asset upload, HLS streaming, React frontend
- **M2**: Scheduling engine, admin schedule UI, now-playing websocket
- **M3**: Sponsor insertion, holiday/timezone/sunset logic, multi-channel
- **M4**: OTA broadcast (Icecast), reporting, monitoring
- **M5**: Docs, load testing, Terraform deployment

## Commands
```bash
# Local dev (with Docker)
docker compose up -d && make migrate && make seed

# Tests
make test-be          # Backend pytest
make test-fe          # Frontend lint

# Without Docker (portable services)
# Start postgres, redis, minio separately, then:
cd backend && uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev
```

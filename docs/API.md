# Studio Kol Bramah — API Reference

Base URL: `https://studio-kolbramah-api-production.up.railway.app/api/v1`

All endpoints accept and return JSON. Protected endpoints require a Bearer token in the `Authorization` header.

---

## Authentication

### POST `/auth/login`
Login with email/password. Returns JWT tokens.

**Body:**
```json
{ "email": "admin@example.com", "password": "secret" }
```
**Response (200):**
```json
{ "access_token": "eyJ...", "refresh_token": "eyJ...", "token_type": "bearer" }
```

### POST `/auth/refresh`
Refresh an expired access token.

**Body:**
```json
{ "refresh_token": "eyJ..." }
```

### GET `/auth/me`
Get current user info. **Requires auth.**

### POST `/auth/seed`
Create or update the default admin user. Used for initial setup.

---

## Stations

### GET `/stations`
List all stations. Public.

**Query params:** `skip`, `limit`

**Response:** `{ "stations": [...], "total": N }`

### POST `/stations`
Create a station. **Requires manager.**

**Body:**
```json
{ "name": "My Station", "timezone": "US/Eastern", "description": "..." }
```

### GET `/stations/{id}`
Get a single station. Public.

### PUT `/stations/{id}`
Update a station. **Requires manager.**

### DELETE `/stations/{id}`
Delete a station. **Requires admin.** Returns 204.

---

## Assets

### GET `/assets`
List audio assets. **Requires auth.**

**Query params:** `skip`, `limit`, `category_id`, `search`

### POST `/assets`
Upload an audio file. **Requires manager.** Multipart form-data.

### GET `/assets/{id}`
Get asset details. **Requires auth.**

### PUT `/assets/{id}`
Update asset metadata. **Requires manager.**

### DELETE `/assets/{id}`
Delete an asset. **Requires admin.** Returns 204.

---

## Schedules

### POST `/schedules/`
Create a schedule. **Requires manager.**

**Body:**
```json
{ "name": "Morning Schedule", "station_id": "uuid" }
```

### GET `/schedules/`
List schedules. Optional `station_id` filter.

### GET `/schedules/{id}`
Get a schedule with its blocks and playlist entries.

### PATCH `/schedules/{id}`
Update a schedule. **Requires manager.**

### DELETE `/schedules/{id}`
Delete a schedule. **Requires manager.** Returns 204.

---

## Schedule Blocks

### POST `/schedules/blocks`
Create a schedule block. **Requires manager.**

**Body:**
```json
{
  "schedule_id": "uuid",
  "name": "Morning Show",
  "start_time": "08:00:00",
  "end_time": "12:00:00",
  "recurrence_type": "daily",
  "priority": 1,
  "playback_mode": "sequential",
  "start_sun_event": "sunrise",
  "start_sun_offset": 30
}
```

Fields:
- `recurrence_type`: `daily`, `weekly`, `monthly`, `one_time`
- `playback_mode`: `sequential`, `shuffle`, `weighted`
- `start_sun_event` / `end_sun_event`: `sunrise`, `sunset`, `dawn`, `dusk` (optional — overrides fixed time)
- `start_sun_offset` / `end_sun_offset`: minutes offset from sun event
- `start_date` / `end_date`: required for `one_time` recurrence

### GET `/schedules/blocks`
List blocks. Optional `schedule_id` filter.

### GET `/schedules/blocks/{id}`
Get a single block.

### PATCH `/schedules/blocks/{id}`
Update a block. **Requires manager.**

### DELETE `/schedules/blocks/{id}`
Delete a block. **Requires manager.** Returns 204.

---

## Playlist Entries

### POST `/schedules/playlist-entries`
Add an asset to a block's playlist. **Requires manager.**

**Body:**
```json
{ "block_id": "uuid", "asset_id": "uuid", "position": 0 }
```

### GET `/schedules/playlist-entries`
List entries. Optional `block_id` filter.

### PATCH `/schedules/playlist-entries/{id}`
Update an entry (position, weight, enabled). **Requires manager.**

### DELETE `/schedules/playlist-entries/{id}`
Delete an entry. **Requires manager.** Returns 204.

---

## Now Playing

### GET `/now-playing/{station_id}`
Get current playback state for a station. Public.

**Response:**
```json
{
  "station_id": "uuid",
  "asset_id": "uuid",
  "started_at": "2026-02-19T10:00:00Z",
  "ends_at": "2026-02-19T10:03:30Z",
  "asset": { "title": "Song", "artist": "Artist" }
}
```

---

## WebSocket

### WS `/ws/now-playing/{station_id}`
Real-time now-playing updates. Sends JSON messages:

```json
{ "type": "now_playing", "data": { ... } }
```

Server sends `{ "type": "ping" }` periodically; client should reply with `pong`.

---

## Queue

### GET `/stations/{id}/queue`
Get playback queue. **Requires auth.**

### POST `/stations/{id}/queue`
Add item to queue. **Requires manager.**

### DELETE `/stations/{id}/queue/{entry_id}`
Remove queue entry. **Requires manager.**

---

## Rules

### GET `/rules`
List scheduling rules. **Requires auth.**

### POST `/rules`
Create a rule. **Requires manager.**

### PUT `/rules/{id}`
Update a rule. **Requires manager.**

### DELETE `/rules/{id}`
Delete a rule. **Requires admin.** Returns 204.

---

## Holidays (Blackout Windows)

### GET `/holidays`
List holiday/blackout windows. **Requires manager.**

### POST `/holidays`
Create a blackout window. **Requires manager.**

**Body:**
```json
{
  "name": "Shabbat",
  "start_datetime": "2026-02-20T17:30:00",
  "end_datetime": "2026-02-21T18:30:00",
  "is_blackout": true,
  "affected_stations": { "station_ids": ["uuid1", "uuid2"] }
}
```

### PUT `/holidays/{id}`
Update a blackout window. **Requires manager.**

### DELETE `/holidays/{id}`
Delete a blackout window. **Requires manager.** Returns 204.

---

## Sponsors

### GET `/sponsors`
List sponsors. **Requires manager.**

### POST `/sponsors`
Create a sponsor. **Requires manager.**

**Body:**
```json
{
  "name": "Acme Corp",
  "length_seconds": 30.0,
  "priority": 5,
  "audio_file_path": "sponsors/acme.mp3",
  "insertion_policy": "between_tracks",
  "target_rules": { "hour_start": 6, "hour_end": 22, "max_per_hour": 4 }
}
```

### PUT `/sponsors/{id}`
Update a sponsor. **Requires manager.**

### DELETE `/sponsors/{id}`
Delete a sponsor. **Requires manager.** Returns 204.

---

## Channels

### GET `/channels`
List channel streams. **Requires auth.**

### POST `/channels`
Create a channel stream. **Requires manager.**

### PUT `/channels/{id}`
Update a channel. **Requires manager.**

### DELETE `/channels/{id}`
Delete a channel. **Requires admin.** Returns 204.

---

## Icecast (OTA Broadcast)

### POST `/icecast/start`
Start OTA broadcast for a station. **Requires admin.**

**Body:** `{ "station_id": "uuid" }`

### POST `/icecast/stop`
Stop OTA broadcast. **Requires admin.**

### GET `/icecast/status`
Get broadcast status. **Requires auth.**

---

## Analytics

### GET `/analytics/summary`
Get overall analytics summary. **Requires manager.**

**Query params:** `station_id`, `days` (default 30)

### GET `/analytics/play-counts`
Get play counts over time. **Requires manager.**

**Query params:** `station_id`, `days`

### GET `/analytics/top-assets`
Get most-played assets. **Requires manager.**

**Query params:** `station_id`, `limit`, `days`

### GET `/analytics/category-breakdown`
Get plays by category. **Requires manager.**

**Query params:** `station_id`, `days`

### GET `/analytics/hourly-distribution`
Get plays by hour of day. **Requires manager.**

**Query params:** `station_id`, `days`

---

## Scheduler Control

### GET `/scheduler/status`
Get scheduler engine status. **Requires admin.**

### POST `/scheduler/start`
Start the scheduler engine. **Requires admin.**

### POST `/scheduler/stop`
Stop the scheduler engine. **Requires admin.**

---

## Users

### GET `/users`
List users. **Requires admin.**

### POST `/users`
Create a user. **Requires admin.**

### PUT `/users/{id}`
Update a user. **Requires admin.**

### DELETE `/users/{id}`
Delete a user. **Requires admin.** Returns 204.

---

## Error Responses

All errors follow this format:
```json
{ "detail": "Error message describing what went wrong" }
```

Common status codes:
- `400` — Bad request / validation error
- `401` — Not authenticated
- `403` — Insufficient permissions
- `404` — Resource not found
- `409` — Conflict (duplicate)
- `500` — Internal server error

---

## Auth Roles

| Role | Access |
|------|--------|
| `viewer` | Read-only access to protected endpoints |
| `manager` | Full CRUD on most resources |
| `admin` | Everything + user management + destructive ops |

## Interactive Docs

FastAPI auto-generates interactive API docs:
- **Swagger UI**: `{base_url}/docs`
- **ReDoc**: `{base_url}/redoc`

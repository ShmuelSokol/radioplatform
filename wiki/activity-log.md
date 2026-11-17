# Activity log (who did what)

Admin page `/admin/activity` (admin only). Tab "Who did what" lists every change a
signed-in user makes, their sign-ins (and failed ones) and the screens they open.
Tab "Listeners" lists each listening session (station, start, length, place, device).

## How rows get written (backend/app/services/activity_log.py)

- `ActivityMiddleware` looks at every POST/PUT/PATCH/DELETE under `/api/v1` that
  carries a valid access token. It reads the token itself (no DB), so it costs
  nothing for public calls. Excluded: listener heartbeats, token refresh, login
  (logged by the route), playout engine, Twilio, CRM, previews (`EXCLUDED_*`).
- The name ("Pressed Next", "Changed playlist") comes from the route template:
  `LABELS` for named actions, else a noun from the first path segment (`SEGMENTS`)
  plus the method. Add a `LABELS` line when a new operator action needs a nicer name.
- Before/after: a global SQLAlchemy `before_flush` listener records every row the
  request creates, changes (field by field, old and new) or deletes, but only
  while a request context is set (`_capture` contextvar). Scheduler and background
  work are not recorded. Bulk `update()` statements bypass it (label still shows).
  Never stored: `updated_at`, `last_seen_at`, secrets (password/token fields show
  "(changed)"). The JSON body is kept with secret keys masked.
- Station: the `{station_id}` path param, else `station_id` in the body, else the
  changed rows. Next / Play now show "Song A → Song B" from queue status changes.
- Rows also carry status code (403 shows as "Refused"), IP (X-Forwarded-For),
  device ("iPhone · Safari"), place (ip-api lookup, cached per IP) and the
  request id the browser sent (matches bug reports).
- Writing never slows a request: rows go to an in-memory queue and one background
  task inserts them in batches. `created_at` is set when the action happens.
- Page views: `PageViewTracker` in `Layout.tsx` posts `/activity/page-view` on each
  route change (same screen within a minute is skipped).

## By person (default view)

`GET /activity/people` groups the same filters by person (user id, or the typed
email for rows with no account, e.g. failed sign-ins): actions, screens opened,
refused/failed, counts per kind, last action. The page opens on "By person" for
today; opening a person shows their own timeline (`user_id`, or `user_email` for
no-account rows). Postgres refuses a GROUP BY whose expression has bound
parameters (`coalesce(email, '')` became `$1` in SELECT and `$2` in GROUP BY), so
the grouped expression uses `literal_column("''")`. SQLite tests do not catch this.

## Routes that change nothing

When a request writes nothing (e.g. Next while a timed item plays to the end, or
Next sent to the playout engine), the route calls `activity_log.note(text,
asset_ids)` so the row says what happened instead of being blank.

## Errors keep CORS headers

`ErrorsKeepCors` (core/middleware.py) answers unhandled errors with a JSON 500
inside the CORS layer. Before, Starlette answered them outside CORS and browsers
reported a CORS error instead of the 500.

## Tests

`tests/test_activity_log.py`. Tests set `activity_log.INLINE = True`: rows are
written after each request through the test's own session, because SQLite allows
one writer and the shared test session holds its lock.

## Live updates

The page polls `GET /activity?after=<newest - 30s>` every 5 s while visible and on
page 1, and merges new rows by id (the 30 s look-back covers rows written a moment
after a later one). Listeners tab refetches every 15 s.

## Old data

Rows from before 2026-11-17 have no label/category; they show as "Other". The
old endpoint `GET /users/audit-log` still works.

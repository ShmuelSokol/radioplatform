# Sign-in sessions: what ends them

Staff sign in and get two tokens: a short access token and a 7-day refresh token. Nothing
about a session is stored on the server, so "signing someone out" works through one number.

## The number

`User.session_version` (0 for everyone at first). Both tokens carry it as `sv`
(`auth_service.create_tokens`). `security.token_is_current(payload, user)` compares the two;
a token with an older number is refused with 401. A token with no `sv` counts as 0, so the
release that added this signed nobody out.

Checked in every place that accepts a token: `get_current_user`, `/auth/refresh`, the alerts
socket (`websocket.py`), the live-show sockets (`live_shows_ws.py`) and the optional user on
bug reports. A new place that decodes a token must call `token_is_current` too.

## What bumps it (owner decision relayed by the coordinator, 2026-11-18)

- A new password (`PUT /users/{id}` with `password`, and `/auth/seed` resetting the admin).
- A role that really changes. The Users form sends the role on every save; the same role
  again ends nothing.
- Deactivating or deleting the login already ended every session (checked on each request).

A person changing their OWN password stays signed in on the browser they are using: the
answer carries `new_tokens` and `frontend/src/api/users.ts` stores them. Their other devices
get 401, the refresh fails, and the site sends them to the sign-in page.

Not covered: "Log out" still only clears that browser (no server logout); a copied refresh
token works until it expires or one of the changes above happens.

Tests: `backend/tests/test_sessions.py`.

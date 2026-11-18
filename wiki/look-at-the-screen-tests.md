# Look-at-the-screen tests (guard, walk-through, station isolation)

Why: Shmuel moved a song in Main's queue and saw a warning from an All Day Stories rule
(2026-11-17). The move was tested; what the screen showed was not. Since then a test passes
only when the action worked **and** nothing a person would call wrong was shown.
Checklist: sections L and I of `docs/test-checklist.md`.

## The guard (`frontend/e2e/lib/guard.mjs`)

Records warning and error toasts (amber or red), `role="alert"`, the error page, console
errors, uncaught errors, unhandled rejections, failed requests and 4xx/5xx answers.
- Every browser launched through `e2e/lib/fake-station.mjs` is guarded with no change to the
  test; `checker().done()` prints `LOOK` lines and counts them as failures.
- A test that provokes trouble on purpose says so: `allowEverywhere(/pattern/, 'why')`.
- `GUARD=report` prints without failing, `GUARD=off` switches it off.
- A cancelled request (`net::ERR_ABORTED`) is noise by default; a refused one is not.

## The walk-through (`frontend/e2e/walkthrough.mjs`)

Real backend on local Postgres, built site, three stations from `e2e/walkthrough-seed.py`
(everything a station owns carries its tag: MAINX, STORYX, JERUX; library items are lower
case because the library is shared). Per station it opens every staff page, presses every
control, drags a queue row, with a screenshot per step. It reports: anything the guard saw,
another station's tag on this station's page, a control that is off the window, cut off,
under something else or not pressable (1366x900 and phone), a page that opens on another
station than the one last used, a slow press, and a station setting left changed.

Things learned the hard way:
- Press every switch back. The first run left schedules inactive, Spots mode off and a
  blackout saved as "all stations", and half its later findings were its own doing.
- A select that is the page's station picker must not be "pressed", or the rest of the page
  is walked on another station.
- Setup recipe and ports are in project memory (`review-test-setup`), not here.

## Station isolation (`backend/tests/test_station_isolation.py`)

Finds every GET that takes a station from `app.openapi()` and calls it for station A after
giving station B one of everything; fails on B's marker or id. A new endpoint is covered the
day it is added; one that needs more than a station must be listed in `NEEDS_MORE`, one that
is shared on purpose in `SHARED_ON_PURPOSE`, each with a reason.

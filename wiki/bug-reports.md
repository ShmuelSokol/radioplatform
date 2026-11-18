# Bug reports (the 🐞 button) and automatic triage

Shmuel's ask (2026-11-16): users report bugs with an automatic screenshot and the
state at that moment; there must be logs to reproduce it; Claude looks at each
report, plans a fix, and sends an ntfy push when a fix is ready.
Same pattern as his supplier-item-database project (`wiki/bug-autofix.md` there).

## What a report carries
- **Browser** (`frontend/src/lib/diagnostics.ts`, installed in `main.tsx`): last 400
  console lines, uncaught errors, last 300 API calls with status, time and
  `X-Request-ID`, a timeline (page changes, `media.play`/`media.pause`, station
  changes, new AudioContexts), the state of EVERY audio element (also `new Audio()`
  ones not in the page: found by wrapping `HTMLMediaElement.play`) and every
  AudioContext (channels, state), React Query data whose key mentions queue / now
  playing / station / schedule (trimmed), localStorage minus anything token-like.
- **Button**: on a computer, bottom left on every page (the control room and Databank carry it in their own bars). On a phone, a 44 px 🐞 beside the bell in the top bar of every staff page and in the sponsor portal's bar (`BugTopButton` in `BugReporter.tsx`); wherever that bar is on the page the floating button is not shown at phone size (it kept covering controls). The phone menu keeps "🐞 Report a bug" (44 px). Both use the event `kbr:report-bug`; an open menu closes first so the screenshot shows the page. Listener pages for visitors keep the floating button.
- **Screenshot**: `html2canvas` (lazy-loaded) of the visible viewport, taken BEFORE
  the form opens; JPEG, about 20-150 KB. The reporter can untick it.
- **Server** (`backend/app/core/log_buffer.py`): the app keeps its last 3000 log lines
  and 2000 requests in memory; each line carries the request id. A report stores the
  last 15 minutes of both (`server_log`). Requests that fail (5xx) or take > 3 s are
  also written to the Railway log. Per process: a restart empties the buffer.

## Where it goes
1. `POST /api/v1/bug-reports` (no login needed; 8 per 10 min per user, or per address
   from `app/core/client_ip.py`) saves the row in `bug_reports` (screenshot as bytes).
   `number` has a unique key; a report sent at the same moment as another retries with
   the next number. The answer says `screenshot_saved: false` when a screenshot was
   sent but not kept (over 4 MB, not JPEG/PNG/WebP, bad base64) and the dialog says so. Staff read them at `/admin/bug-reports`
   (Settings → Bug Reports); admins can close one.
2. If `BUG_REPORTS_GITHUB_TOKEN` is set on Railway, the report is copied to the
   PRIVATE repo `BUG_REPORTS_REPO` (default `ShmuelSokol/kbr-bug-reports`):
   `reports/<number>/context.json`, `server-log.json`, `screenshot.jpg`, and an issue
   labelled `bug-report`. radioplatform is PUBLIC, so reports never go to its issues.
   The token is a fine-grained PAT for that one repo (Contents + Issues: write).
3. A Claude scheduled task ("Triage KBR bug reports") runs hourly. For each open
   `bug-report` issue without an `auto-fix-*` label it adds `auto-fix-claimed`,
   reads the files, reproduces, comments a fix plan, opens a radioplatform PR, and
   when its checks pass comments the PR link and labels the issue `auto-fix-ready`.
   If unclear or risky: comments a question and labels `auto-fix-needs-owner`.
   It never merges: Shmuel says "merge the fix for bug N".
4. Right after a report arrives, the API pushes "Bug N received" to `NTFY_TOPIC` (added 2026-11-16: Shmuel expected a push the moment he sent one). Then
   `bug_report_service.notify_loop` (in the API process, every 5 min) reads those
   issues and sends ONE ntfy push per label change to `NTFY_TOPIC` (Railway only,
   never in the repo). A closed issue marks the report fixed/closed.

## Gotchas
- PR text goes to a public repo: no screenshots, emails or log dumps in PRs.
- Re-send a report to GitHub (e.g. made before the token was set):
  `POST /api/v1/bug-reports/{id}/resend` (admin).
- To retry triage of one report, remove its `auto-fix-*` labels.

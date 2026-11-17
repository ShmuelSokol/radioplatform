# Deploys: what a merge to main publishes

A merge to `main` publishes only what it changed.

| Part | Host | Publishes when the merge changed | Decided by |
|------|------|----------------------------------|------------|
| Website (kbrlive.com) | Vercel project `studio-kolbramah-radio` | `frontend/` (not `frontend/e2e/`), `vercel.json`, `infra/vercel-ignore.sh` | `infra/vercel-ignore.sh` (root `vercel.json`: `ignoreCommand`) |
| Backend (api.kbrlive.com) | Railway `studio-kolbramah-api` | `backend/` (not `backend/tests/`, `backend/loadtests/`) | `backend/railway.toml`: `watchPatterns` |
| Stream server | DigitalOcean droplet | never by a merge: `infra/streaming/setup.sh` is rerun on the droplet | by hand (PC thread) |

## Why

Until 2026-11-17 every merge built the website and restarted the backend. Of 200
merges in a row, 68 touched a website file and 91 a backend file. The rest were
tests, wiki, or stream-server files. Each needless website build cost Vercel
build minutes and gave the site a new version id, so open tabs were told to pick
up a release that was the same site ([[release-pickup]]). Each needless backend
restart dropped every WebSocket and the in-memory state (engine state, last
played, armed timers) for nothing.

## How the website check works

Vercel runs `ignoreCommand` before a build: exit 0 skips, exit 1 builds. The
script compares `HEAD` with `VERCEL_GIT_PREVIOUS_SHA`, the last commit Vercel
actually published for this branch, not with the parent commit. So when Vercel
drops a queued build in favour of a newer push, the newer push still sees the
older push's website change and builds. Vercel's checkout is shallow (about 10
commits): when the last published commit is not in it, the script builds.

`vercel.json` at the repository root is read by every Vercel project that builds
from the root, and its `ignoreCommand` replaces the project's own "Ignored Build
Step" setting. The two unused projects (`studio-kolbramah-api`, `frontend`) are
skipped by their production address (`VERCEL_PROJECT_PRODUCTION_URL`); the first
merge with the script built the website into `studio-kolbramah-api` once before
that guard was right. Disconnecting those two projects from Git in Vercel would
remove the need for the guard.

Branch previews stay off (the script skips anything that is not production), as
they were in the Vercel project's settings before; `vercel.json` now overrides
that setting.

## Checking it

On the merge commit's status list (`gh api repos/ShmuelSokol/radioplatform/commits/<sha>/status`):
"Canceled by Ignored Build Step" = website skipped; no `KBR-Studio` status =
backend not redeployed.

To force a website build without a change: Redeploy in Vercel, or the CLI from
`frontend/` (`npx vercel --prod --yes`), which does not run the check.
To force a backend deploy: Redeploy in Railway.

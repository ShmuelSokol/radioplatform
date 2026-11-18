#!/usr/bin/env bash
# Vercel "Ignored Build Step" for the website (vercel.json: ignoreCommand).
# Exit 0 = skip this build, exit 1 = build.
#
# Every merge to main used to build and publish the website, although two merges
# in three change no website file (backend, tests, wiki). Each needless build
# cost build minutes and gave the site a new version id, so every open tab was
# told to pick up a "new release" that was the same site.
#
# The website is built only when a file it is made from changed since the last
# website build that went out. When in doubt (first build, history not at hand),
# it builds.

# Branch previews were already switched off in the Vercel project; keep it so.
if [ "${VERCEL_ENV:-}" != "production" ]; then
  echo "Not a production build: skip."
  exit 0
fi

# The repo's two other Vercel projects (the old serverless backend and an early
# copy of the site) never build. Their settings said so, but this file overrides
# project settings, and the first merge with it built the website into the
# "studio-kolbramah-api" project. A deployment address is cut short
# ("studio-kolbramah-<hash>"), so the project is told by its production address.
# That project has two addresses, studio-kolbramah-api.vercel.app and
# backend-gamma-six-68.vercel.app, and Vercel reports the second: matching only
# the first let it build again. "frontend" is frontend-kappa-three-42.vercel.app.
# The website itself is kbrlive.com.
echo "Project production address: ${VERCEL_PROJECT_PRODUCTION_URL:-unknown}"
case "${VERCEL_PROJECT_PRODUCTION_URL:-}" in
  *kolbramah-api*|backend-*|frontend*)
    echo "Not the website project (${VERCEL_PROJECT_PRODUCTION_URL}): skip."
    exit 0
    ;;
esac

previous="${VERCEL_GIT_PREVIOUS_SHA:-}"
if [ -z "$previous" ] || ! git cat-file -e "${previous}^{commit}" 2>/dev/null; then
  echo "Last published commit unknown or not in this checkout: build."
  exit 1
fi

if git diff --quiet "$previous" HEAD -- \
    frontend vercel.json infra/vercel-ignore.sh ':(exclude)frontend/e2e'; then
  echo "No website file changed since $previous: skip."
  exit 0
fi
echo "Website files changed since $previous: build."
exit 1

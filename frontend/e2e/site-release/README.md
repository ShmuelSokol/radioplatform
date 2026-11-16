# Site release browser check

Checks that admin-controlled releases (Settings > Site Updates, `src/lib/appUpdate.ts`) reach open tabs without cutting audio. A fake site serves two real builds (v1, v2) and a tiny API; Chromium plays tone files on the Listen page. `safari.js` makes Chromium refuse sound without a tap, as Safari does after every page load. About 3 minutes.

```bash
cd frontend
for v in v1 v2; do VERCEL_GIT_COMMIT_SHA=$v npx vite build --outDir /tmp/rel/dist-$v --emptyOutDir; done
(cd ../backend && uv run python ../frontend/e2e/site-release/fake.py /tmp/rel &)
node e2e/site-release/check.cjs
```

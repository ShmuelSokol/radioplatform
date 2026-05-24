# LINT workflow

Run every 2-4 weeks (or after any big refactor).

1. `grep -c ^INGEST wiki/log.md` — total ingests.
2. `grep -c ^QUERY wiki/log.md` — total queries.
3. For each page in `wiki/`:
   - Was it ever QUERY'd? If no after 2 months → candidate for deletion (no one's reading it).
   - Does its content still match the code? Run a code-grep for the facts it claims.
4. Update `wiki/README.md` to reflect surviving pages.
5. Append `LINT <YYYY-MM-DD> <summary: N pages deleted, M updated, K unchanged>` to log.md.

**Prune ruthlessly.** A wrong page is worse than no page — it gives a false sense of safety.

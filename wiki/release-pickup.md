
## Server restarts (backend releases)

Every merge to main restarts the backend. Two rules keep that inaudible:

- **A restart does not cut the song.** `_resume_playback_on_startup` (`backend/app/main.py`) used to mark whatever was playing as played and start the next item, on every station, on every deploy. It now leaves an entry alone when `started_at + duration` is still in the future and only cleans up entries that ended while the server was down.
- **One scheduler at a time.** Railway keeps the old process alive until the new one is healthy, so two schedulers overlap. `SchedulerEngine._hold_lease` (table `scheduler_leases`, 15s lease renewed every 3s tick, released on shutdown) lets only one advance queues. A lease row, not `pg_advisory_lock`: the Supabase transaction pooler does not keep session locks.

Tested locally: second backend started beside the first → nothing on air changed; first stopped → second took the lease and advanced the next song exactly once.

# Deleting library items (speed, locks, bulk clean-ups)

`DELETE /assets/{id}` (`asset_service.delete_asset`) removes the row; Postgres then looks in every table that points at the item (queue_entries, play_logs, engine_handoffs, review_items, review_actions, song_requests, spot_schedules, spot_recordings, weather_readouts, playlist_entries, ...) to cascade or set null. The file in storage is not touched.

Two things made one delete take 42 s on production (2026-11-18, duplicate clean-up):

- **No index on the pointing columns.** `queue_entries` keeps every entry ever played and had no index on `asset_id`, so each delete read it whole (0.1 s per delete at 900,000 rows on a test Postgres, 0.01 s with the index; worse on a loaded pooler). Every such column must be indexed: `tests/test_asset_delete_speed.py::test_every_column_that_points_at_an_item_is_indexed` fails if a new one is not. Indexes on big tables go in `CONCURRENT_INDEXES` (`app/main.py`): built `CREATE INDEX CONCURRENTLY` in the background after startup, so reads and writes carry on; an interrupted build is dropped and rebuilt at the next start.
- **Waiting for locks without limit.** The cascade must lock the item's waiting queue entries, and the item itself is share-locked by any open transaction that just queued it (a 48 h refill, a renumber at a song change). The delete now sets `lock_timeout` 3 s and `statement_timeout` 8 s for itself; when it cannot get through it rolls back and answers **503 with `Retry-After: 5`** ("The library is busy"), and logs a warning. Nothing is half deleted. Measured: 3.0 s to give up, 0.01 s once the other transaction ended.

Bulk clean-ups (many deletes by script): one delete at a time, 1 s apart; on 503 wait 10 s and retry the same item (3 tries, then skip it and go on); stop if five in a row are busy; pause from :55 to :05 of each hour (timed groups and refills). A delete is refused (409) for the item on air and for items used by a spot schedule or program block.

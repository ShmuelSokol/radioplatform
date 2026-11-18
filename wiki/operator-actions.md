# Operator actions (live control API)

Each button in the control room is one request. The buttons live in the control room
(`frontend/src/api/liveControl.ts`); this page is the server contract.

## Next, with a fade choice

`POST /stations/{id}/queue/skip?fade=hard|fade|soft` (default `fade`). Durations are
`SKIP_FADE_SECONDS` in `api/v1/queue.py`: hard 0, fade 0.5, soft 3. The response carries
`fade_seconds` and the new queue (same body as GET /queue).

- Web player: the WebSocket message that announces the change carries `change_fade_seconds`
  (only that one message; later re-broadcasts omit it). `useAudioEngine` takes it as its last
  argument and uses it for the outgoing deck.
- Playout engine: sent as `{"fade": seconds}` to the engine's `/skip`.
- `kept_incoming: true` means Next arrived while an automatic change was fading in and the
  incoming song was kept (see [audio-engine](audio-engine.md)).

## A queue change reaches the engine at once (2026-11-18)

On an engine station the engine holds its next song pre-loaded. Play next, delete, move
up/down and both reorders call `playout_engine.take_operator_change`: if the engine holds
something other than the queue head, it is flushed so it asks again. Not in the last
`KEEP_CUED_LAST_SECONDS` (45 s) of the item on air, where a flush would leave nothing
ready. Next (`/skip`) calls it with `before_next=True` (no 45 s rule: the song ends now
anyway), then `settle_before_next`: within 4 s of such a flush it waits for the engine to
ask again and 0.8 s for the file, so the song on air is not ended with nothing loaded. An
ordinary Next is not delayed. Before this, Play next then Next within a few seconds
played the automatic song the engine held and the operator's item one Next later (the
automatic swap `_flush_if_head_changed` runs on the 3 s tick, at most once in 15 s).
A file slower than the wait still gives emergency music until it arrives (harness
`operator_next_slow_file`); removing that needs the engine to hold the song on air until
the next is loaded.

## Fire now

`POST queue/fire-now {asset_id, mode: "fade" | "after-current", client_request_id?}`.
A repeat of the same `client_request_id` within 5 s returns the first answer with
`duplicate: true` and does nothing (double tap). Fire-now always goes on air, also during an
automatic change. The guard is in memory, per process.

## Removing what is on air (2026-11-17)

`DELETE queue/{entry_id}` on the entry that is playing starts the next item at once, under
`advance_lock`, with the same pick as Next (only blackout silence during a blackout; refill if
the queue is empty). Before, the row went and nothing started: a browser station was silent
until dead-air recovery (30 s). Engine stations are unchanged (the engine plays to the end).
Deleting a library item that is on air (`DELETE /assets/{id}`) is refused with 409 ("press
Next first"): the cascade would remove the playing entry. Tests:
`tests/test_restart_holidays_timezones.py` (V1-43, V1-91).

## Old /controls routes

`/stations/{id}/controls/play-now` is fire-now (it calls `queue.fire_now`); it used to start
an ffmpeg HLS player beside the queue. play, pause, stop and enqueue still drive the old Redis
list player; with no Redis (production) they answer 409 with a reason instead of 500.

## Insert at a point

`POST queue/insert {asset_id? , asset_ids?: [...], after_entry_id?: id | null}`. Assets go in,
in order, right after that entry; null or an entry that has already played = top (plays next).
Returns `ids` and the new queue.

## Intro length

`Asset.intro_seconds` (float, null = unknown), set with `PATCH /assets/{id}`. It is counted
**from where the item starts on air (its cue-in), not from the start of the file**, so a
countdown is simply `intro_seconds - elapsed`. It is in queue rows (`asset.intro_seconds`),
in `now_playing.asset`, and in the WebSocket `asset`.

## Hard and soft timed items

`QueueEntry.timing_mode` with `preempt_at`:
- `"hard"`: the item on air fades and this starts exactly at `preempt_at`.
- `"soft"`: the item on air finishes, then this plays.
- null: the older rule (source `ad_slot` = soft, anything else = hard).

Always go through `soft_timed()` / `hard_timed()` / `is_soft_timed()` in
`models/queue_entry.py`; do not compare `source` to `"ad_slot"` for this any more. The
scheduling side sets the field; the engine (`playout_engine.pick_next`, `tick`) and the web
player path (`_check_advance_locked`, skip, queue time estimates) read it.

## Loop

`POST queue/loop {on: bool}` returns `{loop, asset_id, queue}`. On: the item on air plays again
each time it ends. Off, Next or fire-now ends it; the play in progress finishes normally.
GET /queue has `loop_asset_id` (null when off). Stored in the station's
`automation_config["loop_asset_id"]`; each repeat is a real queue entry with source `"loop"`,
added just before it is needed (`add_loop_repeat` in `api/v1/queue.py`, called at the automatic
advance and when the playout engine asks for its next track). A Next within 4 s of a repeat
starting is the "kept incoming" case and leaves the loop on. The loop survives a backend
restart in the middle of the item; known gap (strict xfail in
`tests/test_queue_transport_rows.py`): if the looped item ended while the backend was down,
the restart settles it as played and starts the next song.

## Moving entries

move-up / move-down / reorder-dnd / `PUT reorder` / DELETE look the entry up within the URL's
station (`_station_entry`) and move only waiting (`pending`) entries. `PUT reorder` (not used
by the control room, which drags with reorder-dnd) shifts the waiting entries at or after the
new position down by one, so no two share a position.

## Mix over

`POST queue/mix-over {asset_id, client_request_id?}` plays an asset on top of what is on air.
The item on air carries on, the queue does not move, and there is no now-playing change.
Answer: `{message, asset_id, on_air: true}` (`duplicate: true` for a repeated
`client_request_id` within 5 s). 409 if the asset has no audio file, 503 if the engine did
not answer. One play-log row is written (source `manual`).

- Engine station: the backend posts `/overlay {uri}` to the engine, which mixes it in after
  the lanes and before the compressor. Next, timed items and their fades act on the programme
  only, so a jingle that has started always finishes.
- Browser playback: the now-playing socket sends `{type: "mix_over", data: {url, asset_id,
  title, replay_gain_db}}`; `useNowPlayingWS` passes it on as a `kbr-mix-over` window event and
  `useAudioEngine` plays it through the same audio graph (only once sound has been started on
  that page). Each browser starts it when the message arrives, so it is not sample-aligned
  between listeners.

## Notes

PFL and meters are done in the browser, no server part. The RDS text feed needs no hook: it
reads the playing queue entry, which the engine's start reports keep current.

## The queue never runs dry

Next (any fade), a song ending on its own, and Start all go through `_refill_for_next` in `api/v1/queue.py` when nothing is left to play: it runs the normal fill (schedule, rules, rotation), and if that still adds nothing it queues up to 20 random music items, or failing that any playable item that is not silence, a spot, a jingle or an announcement (`source="fallback"`). Before 2026-11-15, Next on the last item picked the next entry before refilling, so the station stopped with an empty queue. When the station is stopped, the control room shows 0:00 instead of the old item's countdown.

`GET /stations/{id}/queue` returns `next_entry_id`: the entry Next (or the end of the item on air) will play, by the same rule as `_pick_next_pending` (a due soft slot first, else the lowest position not timed for later). The control room's "Next" line shows that entry, not simply the first waiting row, which can be an item timed for later.

## Next never skips a timed item (2026-11-16)

A burst of three Nexts on KBR Jerusalem skipped the weather 3.2 s after the first press
put it on air. Now Next does nothing while a timed item is on air (time check, weather,
sponsorship, program block: `_is_timed_entry`, i.e. preempt_at set or source ad_slot /
block). The response carries `protected: true` and the control room says it plays to
the end; on an engine station the engine is not asked to skip. Play-now (fire-now) still puts
something else on air. Songs stay skippable one per press. Test:
`backend/tests/test_rapid_next.py`.

## Operator actions take turns (2026-11-18)
Play next, fire-now, Next, insert, move, reorder and delete on one station run one at a time, in arrival order (`operator_turn`, a route dependency in `queue.py`, its own lock per station, not the advance lock). Found live on Jerusalem at 03:23Z: Play next and Next sent 0.1 s apart ran side by side, Next read the queue before Play next's commit (50 to 150 ms on Supabase), saw the old head and played the song the engine had pre-loaded. Local Postgres commits in a millisecond, so the harness needs a trigger that delays the insert to show it (`operator_next_overtakes_commit`). A turn is waited for at most 8 s (`OPERATOR_TURN_WAIT`), then the action goes ahead anyway, so one stuck request cannot freeze the control room. A new operator endpoint that changes what is next must add `dependencies=[Depends(operator_turn)]` (`test_every_operator_queue_action_takes_its_turn`).

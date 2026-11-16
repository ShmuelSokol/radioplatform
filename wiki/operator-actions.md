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

## Fire now

`POST queue/fire-now {asset_id, mode: "fade" | "after-current", client_request_id?}`.
A repeat of the same `client_request_id` within 5 s returns the first answer with
`duplicate: true` and does nothing (double tap). Fire-now always goes on air, also during an
automatic change. The guard is in memory, per process.

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
starting is the "kept incoming" case and leaves the loop on.

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
the end; on an engine station the engine is not asked to skip. Play-now still puts
something else on air. Songs stay skippable one per press. Test:
`backend/tests/test_rapid_next.py`.

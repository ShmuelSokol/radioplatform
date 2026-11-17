#!/usr/bin/liquidsoap
# KBR Live playout engine (Liquidsoap 2.2)
#
# One engine plays the station. The backend stays the brain:
#   - the engine ASKS the backend what plays next        (GET  {backend}/playout/{station}/next)
#   - the engine TELLS the backend when a track starts   (POST {backend}/playout/{station}/started)
#   - the backend sends commands to the engine           (POST http://127.0.0.1:{control_port}/...)
#
# Lanes, highest priority first:
#   1. timed  — hard-timed items and Play Now (starts on the second, music fades under it)
#   2. main   — the normal queue, crossfaded
#   3. emergency — files on this machine, played if the backend is unreachable (no dead air)
# On top of the lanes: mix over, a jingle added to whatever is on air without ending it.
#
# Configuration comes from environment variables (see kbr-engine.env.example).

backend_url   = environment.get("KBR_BACKEND_URL")                 # e.g. https://api.kbrlive.com/api/v1
station_id    = environment.get("KBR_STATION_ID")
token         = environment.get("KBR_PLAYOUT_TOKEN")
control_port  = int_of_string(environment.get(default="8081", "KBR_CONTROL_PORT"))
emergency_dir = environment.get(default="/var/lib/kbr-engine/emergency", "KBR_EMERGENCY_DIR")
icecast_host  = environment.get(default="127.0.0.1", "KBR_ICECAST_HOST")
icecast_port  = int_of_string(environment.get(default="8000", "KBR_ICECAST_PORT"))
icecast_pass  = environment.get("KBR_ICECAST_SOURCE_PASSWORD")
icecast_mount = environment.get(default="/live", "KBR_ICECAST_MOUNT")
skip_fade     = float_of_string(environment.get(default="0.5", "KBR_SKIP_FADE_SECONDS"))

settings.log.stdout := true
settings.log.level := 3
# No telnet. Control is HTTP on localhost only, with a shared token.
settings.server.telnet := false
settings.harbor.bind_addrs := ["127.0.0.1"]

auth_headers = [("X-Playout-Token", token), ("Content-Type", "application/json")]

# What this engine understands, sent with /hello and every /next. 2 = timed groups handed
# over in parallel (seq/of) and /timed_drop. 3 = the hold lane (quiet before a hard item
# when no song is handed out). The backend keeps to the old serial hand-over
# for an engine that sends nothing.
engine_proto = 3

# On start-up tell the backend we hold nothing of its queue any more. Done before any
# track is requested, so the two cannot cross. If the backend cannot be reached (or
# answers with an error), keep trying every 5 s: until it hears this it still believes we
# hold the timed items it handed the engine before the restart, never hands them over
# again, and the time check is lost (harness engine_starts_backend_down, 2026-11-17). A
# late hello is safe: the backend forgets what it handed us, but every /next sends `given`
# again and /timed ignores an id it already holds.
hello_done = ref(false)
def send_hello() =
  try
    resp = http.post(headers=auth_headers, timeout=8., data='{"proto":#{engine_proto}}',
                     "#{backend_url}/playout/#{station_id}/hello")
    if resp.status_code == 200 then
      hello_done := true
      log.important("hello: backend answered")
    else
      log.important("hello: backend answered #{resp.status_code}; trying again in 5 s")
    end
  catch err do
    log.important("hello: backend unreachable: #{err}; trying again in 5 s")
  end
end
send_hello()
if not hello_done() then
  thread.run.recurrent(delay=5., fun () -> begin
    send_hello()
    if hello_done() then (-1.) else 5. end
  end)
end

# ── Main lane: ask the backend for the next track ────────────────────────────
# Last entry from the music lane that went on air
last_main_entry = ref("")
# The last few entries the backend handed us, newest first, sent back with every request
# for the next track. The backend never hands these out again, and closes the older ones
# if a start report was lost while it was unreachable (so nothing is played twice).
given = ref([])
# A host is on air and the stream is quiet (see the live lane below). Sent with every
# ask, so a backend that restarted while the show ended still learns of it.
live_on = ref(false)
def next_request() =
  try
    handed = string.concat(separator=",", given())
    resp = http.get(headers=auth_headers, timeout=8.,
                    "#{backend_url}/playout/#{station_id}/next?given=#{handed}&proto=#{engine_proto}&live=#{if live_on() then 1 else 0 end}")
    if resp.status_code == 200 then
      let json.parse (data : {uri : string, entry_id : string}) = resp
      log.important("next: backend gave entry #{data.entry_id}")
      given := list.prefix(5, data.entry_id :: given())
      request.create(data.uri)
    else
      null()
    end
  catch err do
    log.important("next: backend unreachable: #{err}")
    null()
  end
end

# A long song is a big download: give it time to arrive (it is fetched while the song
# before it plays)
main_requests = request.dynamic(id="main", prefetch=1, retry_delay=2., timeout=120., next_request)
# Per-track level from the library's audio analysis (backend sends liq_amplify)
main = amplify(1., override="liq_amplify", main_requests)
# Song-to-song: the outgoing song fades out under the incoming one, which starts at full
# level. How long they overlap comes with each item from the backend (`kbr_overlap`, the
# station's "Song crossfade" setting, 1 s by default; 0 = none). Anything spoken (a
# sponsorship, a time check, a shiur) has overlap 0 and is never talked over: the next
# item starts when it has ended (Shmuel 2026-11-17: sponsorships played in a row ran
# into each other). `liq_cross_duration` tells `cross` how early to call transition().
# After Next or a timed item the old song must not come back, so that change is a clean cut
# (the music has already been faded out by then).
hard_cut = ref(false)
# When the music lane last changed song (`cross` calls transition() at that moment).
# For the length of the overlap after a change `cross` is still playing its transition, and a skip then
# only ends the transition: the song stays on air. skip_main() uses this to see whether
# a skip really changed the song.
main_changed_at = ref(0.)
# Used when an item carries no overlap of its own (the emergency music)
default_overlap = 1.
def transition(ending, starting) =
  main_changed_at := time()
  if hard_cut() then
    hard_cut := false
    add(normalize=false, [starting.source])
  else
    # The incoming song is listed first: `add` passes on the titles of its first source
    # only, and the start report to the backend is made from them.
    overlap = float_of_string(default=default_overlap, ending.metadata["kbr_overlap"])
    if overlap < 0.05 then
      # No overlap: the last moment of the ending item plays out, then the next starts
      sequence([ending.source, starting.source])
    else
      add(normalize=false, [starting.source, fade.out(duration=overlap, ending.source)])
    end
  end
end
main = cross(duration=default_overlap, override_duration="liq_cross_duration", transition, main)

# The music lane is muted while a timed item takes over, so nothing of it leaks through
main_gain = ref(1.)
# override=null(): amplify reads the track's liq_amplify by default, so each amplify
# below applied the track's level again: a -6 dB song played 18 dB down and a +4 dB one
# 12 dB up (harness track_level, 2026-11-17). Only the per-track amplify reads it.
main = amplify(override=null(), {main_gain()}, main)
# Set when a timed item interrupted a song: that song does not resume afterwards
main_interrupted = ref(false)

# Master gain, used for the quick fade on Next and when a timed item takes over
master_gain = ref(1.)

def ramp_master(~target, ~duration, after) =
  steps = 20
  from = master_gain()
  i = ref(0)
  def step() =
    i := i() + 1
    master_gain := from + (target - from) * float_of_int(i()) / float_of_int(steps)
    if i() >= steps then
      after()
      (-1.)
    else
      duration / float_of_int(steps)
    end
  end
  thread.run.recurrent(delay=0., step)
end

# ── Timed lane: hard-timed items and Play Now ────────────────────────────────
# A time check is handed over about 45 s ahead; give its file time to arrive (the default
# 20 s dropped a slow download, harness slow_timed_file)
# prefetch: every item of a group starts downloading when it is handed over. With the
# default (1) the next file was only fetched once the item before it started, and the
# Daf Yomi shiur after a 6 s announcement was not there in time: the music lane played
# instead (Jerusalem 18:00Z 2026-11-17; harness block_with_slow_shiur)
timed_queue = request.queue(id="timed", interactive=false, timeout=90., prefetch=10)
timed_armed = ref(false)
# Which lane is on air right now ("main", "timed" or "emergency")
on_air_lane = ref("")
# Items due at the same time form a group (a block: ID, weather, sponsors). Once the lane
# is on air it stays on until the group has played through, in the order it was handed over.
# length() counts only requests not yet fetched: an item already fetched (the next one of
# the group, downloaded while the one before it plays) is not in it. Counting on length()
# alone closed the lane before the third item of a group, and the Daf Yomi shiur after
# the time check and announcement was left behind (Jerusalem 18:00Z 2026-11-17).
def timed_open() =
  timed_armed() or (on_air_lane() == "timed" and (timed_queue.length() > 0 or timed_queue.is_ready()))
end
# Per-track level for timed items too (time checks, sponsors), as on the music lane
timed = switch(id="timed_gate", track_sensitive=true,
               [(timed_open, amplify(1., override="liq_amplify", timed_queue))])

# Start time of the last group scheduled to take over
timed_group_at = ref(-1.)

def timed_take_over() =
  ramp_master(target=0., duration=skip_fade, fun () -> begin
    # Mute the music lane, put the timed item on air at full level. The interrupted
    # song does not resume: when the music lane comes back it moves to its next song
    # (see report_started).
    timed_armed := true
    if on_air_lane() == "main" then
      main_gain := 0.
      main_interrupted := true
      master_gain := 1.
    elsif on_air_lane() == "timed" then
      # An earlier timed item is still on air (a block's shiur, a Play Now): end it,
      # or this group waits behind it. The 21:00 time check aired at 21:09 after the
      # 20:00 block's Daf Yomi (2026-11-16). The backend has already queued the
      # shiur to carry on afterwards when the station pauses long items.
      # But not an item that has only just started, or one about to end by itself:
      # with the station set to wait, the group is due exactly when the shiur ends,
      # so the item on air at this moment is the group's own first item (the time
      # check), and skipping it dropped the time check (harness, 2026-11-17).
      elapsed = timed_queue.elapsed()
      left = timed_queue.remaining()
      if elapsed > 1.0 and (left < 0. or left > 1.0) then
        timed_queue.skip()
      else
        log.important("timed: item on air just started or is ending; not cut (elapsed #{elapsed}, left #{left})")
      end
      thread.run(delay=0.1, {master_gain := 1.})
    else
      master_gain := 1.
    end
    thread.run(delay=1.5, {timed_armed := false})
  end)
end
# Put the timed lane on air as soon as its next item's file is there (up to `limit`
# tries, 0.25 s apart); meanwhile the music carries on. Muting it while the file was
# missing left 16 s of silence and the time check never aired (harness slow_timed_file).
timed_waiting = ref(false)
# Timed items handed over and not started yet (length() leaves out one being fetched)
timed_pending = ref(0)
# `group`: the group this wait is for; it stops if a later group has been scheduled
# since (that group's own wait takes over), so a later time check is never pulled early
def timed_go_on_air(limit, group) =
  tries = ref(0)
  timed_waiting := true
  def rec attempt() =
    if group != timed_group_at() then
      timed_waiting := false
    elsif not timed_queue.is_ready() and tries() < limit then
      if tries() == 0 then log.important("timed: item not ready at its time; music carries on until it is") end
      tries := tries() + 1
      thread.run(delay=0.25, attempt)
    else
      timed_waiting := false
      if timed_queue.is_ready() then
        timed_take_over()
      else
        log.important("timed: item never arrived; giving up on it")
        timed_pending := 0
      end
    end
  end
  attempt()
end

# Load the item now; put it on air at `at` (unix seconds). The music fades under it.
def schedule_timed(~uri, ~at) =
  r = request.create(uri)
  # Pushed here, not in a thread, so a group keeps the order it was handed over in
  # Counted before the push: with prefetch the push itself can wait for the download
  timed_pending := timed_pending() + 1
  timed_queue.push(r)
  # One take-over per group: the rest of the group follows from the queue
  if abs(at - timed_group_at()) > 0.5 then
    timed_group_at := at
    wait = at - time() - skip_fade
    thread.run(delay=(if wait > 0. then wait else 0. end), {timed_go_on_air(180, at)})
  end
end

# ── Emergency lane: local files, then silence as the very last resort ────────
emergency = playlist(id="emergency", mode="randomize", reload_mode="watch", emergency_dir)
emergency = mksafe(emergency)

# ── Hold lane: a moment of quiet just before a hard timed item ───────────────
# When a song ends a few seconds before a hard item (the hourly time check), the backend
# hands out no new song: it would be chopped after two seconds (Jerusalem 23:00Z
# 2026-11-17). With nothing in the music lane the emergency music would start instead,
# so for those few seconds the stream is quiet until the timed item takes over. Open only
# while a timed item is loaded and due within 8 s; never longer, so a timed item that
# does not arrive still ends in emergency music, not dead air.
# Also open for a few seconds after a live show ends, while the first item loads.
live_release = ref(0.)
def hold_open() =
  left = timed_group_at() - time()
  (timed_pending() > 0 and left < 8. and left > -2.) or time() < live_release()
end
hold = switch(id="hold_gate", track_sensitive=false, [(hold_open, blank(id="hold"))])

# ── Live lane: the stream is quiet while a host is on air ────────────────────
# The host's microphone reaches listeners through the site, not through this stream
# (Shmuel's choice, PR 112), and automation pauses. Without this lane the song on the
# engine played on under the host (Jerusalem 23:06Z 2026-11-17): the backend only
# stopped handing out new items. While the lane is on air the lanes below stand still;
# the backend switches it with /live.
on_air_quiet = switch(id="host_gate", track_sensitive=false, [({live_on()}, blank(id="host_on_air"))])

radio = fallback(id="radio", track_sensitive=false, [on_air_quiet, timed, main, hold, emergency])
radio = amplify(override=null(), {master_gain()}, radio)
# What is on air from the lanes above. Start reports and /status read this, so an item
# mixed over the top (below) never counts as a change of track.
program = radio

# ── Mix over: a jingle played on top of the programme, which carries on ──────
overlay_queue = request.queue(id="overlay", interactive=false)
overlay = amplify(1., override="liq_amplify", overlay_queue)
radio = add(normalize=false, [program, overlay])

# Skip the song on air in the music lane. A second Next within 3 s of a song change was
# lost (harness next_twice, 2026-11-17): the skip only ended cross's transition. So the
# skip is checked: if no song change followed within 0.3 s, it is given once more.
# The output stays faded down until the change (at most 0.4 s), so the old song is not
# heard again in between.
def skip_main() =
  asked = time()
  hard_cut := true
  main.skip()
  thread.run(delay=0.1, {if main_changed_at() >= asked then master_gain := 1. end})
  thread.run(delay=0.3, {
    if main_changed_at() < asked and on_air_lane() != "timed" then
      log.important("skip: no song change after the skip (transition still playing); skipping again")
      hard_cut := true
      main.skip()
      thread.run(delay=0.1, {master_gain := 1.})
    else
      master_gain := 1.
    end
  })
end

# Next: fade out quickly, skip whatever is on air, come back at full level on the new track
def skip_on_air(~seconds) =
  ramp_master(target=0., duration=seconds, fun () -> begin
    if on_air_lane() == "timed" then
      timed_queue.skip()
      thread.run(delay=0.1, {master_gain := 1.})
    else
      skip_main()
    end
  end)
end

# A live show starts: what is on air fades out and the live lane takes the stream.
# (Defined here, beside the other skips: called straight from the /live handler, the
# timed lane's skip kept the engine from starting.)
def go_live() =
  ramp_master(target=0., duration=skip_fade, fun () -> begin
    if on_air_lane() == "main" then
      main_gain := 0.
      main_interrupted := true
    elsif on_air_lane() == "timed" then
      timed_queue.skip()
    end
    live_on := true
    thread.run(delay=0.1, {master_gain := 1.})
  end)
end

# ── Broadcast sound: gentle compression and a limiter on the whole output ────
radio = compress(threshold=-14., ratio=3., attack=20., release=200., radio)
# Brickwall limiter with look-ahead (FFmpeg alimiter): no peak gets past -3 dBFS, so
# with the up to ~1 dB the MP3 encoder adds between samples, true peaks stay under
# -2 dBFS. Liquidsoap's own limit() is a compressor with a 50 ms attack: peaks got
# through it (true peaks of -0.6 dBFS on Jerusalem with threshold -2, 2026-11-17).
def true_peak_limit(s) =
  def graph_of(graph) =
    let {audio = a} = source.tracks(ffmpeg.raw.encode.audio(%ffmpeg(%audio.raw), s))
    a = ffmpeg.filter.audio.input(graph, pass_metadata=true, a)
    a = ffmpeg.filter.alimiter(graph, limit=0.708, attack=5., release=50., level=false, a)
    a = ffmpeg.filter.audio.output(graph, pass_metadata=true, a)
    source({audio = a, metadata = track.metadata(a), track_marks = track.track_marks(a)})
  end
  ffmpeg.raw.decode.audio(ffmpeg.filter.create(graph_of))
end
# (an FFmpeg filter source counts as fallible; it never stops while radio plays)
radio = mksafe(true_peak_limit(radio))

# ── Tell the backend what really went on air ─────────────────────────────────
def report_to_backend(entry, lane, title) =
  payload = json.stringify({entry_id=entry, lane=lane, title=title, at=time()})
  def post() =
    try
      ignore(http.post(headers=auth_headers, timeout=8., data=payload,
                       "#{backend_url}/playout/#{station_id}/started"))
    catch err do
      log.important("started: backend unreachable: #{err}")
    end
  end
  thread.run(fast=false, post)
end

# The emergency folder fills itself: a copy of each song that aired (the newest 40), so
# if the backend cannot be reached the station plays its own music, not silence. It
# was empty on the server (2026-11-17), which would have meant dead air.
emergency_keep = 40
def keep_for_emergency(played) =
  if played != "" and file.exists(played) then
    dest = "#{emergency_dir}/" ^ path.basename(played)
    # Copied under a temporary name first, so the playlist never sees half a file
    part = dest ^ ".part"
    cmd = "cp -f #{process.quote(played)} #{process.quote(part)} && mv -f #{process.quote(part)} #{process.quote(dest)} && cd #{process.quote(emergency_dir)} && ls -1t | tail -n +#{emergency_keep + 1} | tr '\\n' '\\000' | xargs -0 -r rm -f --"
    thread.run(fast=false, {ignore(process.run(cmd))})
  end
end

# Timed items the backend took back (/timed_drop): skipped the moment they would start.
# Silence left in the timed lane when a blackout ended went on air one item per Next.
timed_dropped = ref([])

def send_started(m) =
  entry = m["kbr_entry"]
  # No entry: an emergency file, or the quiet held just before a hard timed item
  lane = if entry != "" then m["kbr_lane"] elsif live_on() then "live" elsif hold_open() then "hold" else "emergency" end
  was = on_air_lane()
  on_air_lane := lane
  if lane == "timed" and timed_pending() > 0 then timed_pending := timed_pending() - 1 end
  if was == "timed" and lane != "timed" and timed_pending() > 0 and not timed_waiting() then
    # A group's next item was not ready when the item before it ended (its file still
    # downloading), so the music lane filled in. Put the group back on air the moment
    # it is there instead of leaving it stranded in the timed lane.
    log.important("timed: rest of the group not ready; music meanwhile, it follows when it is")
    timed_go_on_air(480, timed_group_at())
  end
  if lane == "timed" and entry != "" and list.mem(entry, timed_dropped()) then
    log.important("timed: entry #{entry} was dropped by the backend; skipping it")
    timed_queue.skip()
  elsif lane == "main" and main_interrupted() and entry == last_main_entry() then
    # Back from a timed item: leave the interrupted song and start the next one. The
    # interrupted song is not reported again (it already ended for the backend).
    main_interrupted := false
    hard_cut := true
    main.skip()
    thread.run(delay=0.15, {main_gain := 1.})
  elsif lane == "hold" then
    # Not reported: nothing from the queue is on air and it is not the emergency music
    log.important("hold: quiet until the timed item due in #{timed_group_at() - time()} s")
  elsif lane == "live" then
    log.important("live: a host is on air; the stream is quiet")
  else
    if lane == "main" and main_interrupted() then
      # The music lane came back on a new track, not the interrupted song (after a long
      # block it can start afresh). That track is the one meant to air next, often the
      # paused shiur resuming: skipping it played a song instead (Jerusalem, 2026-11-17
      # 14:00Z, the shiur came back for half a second).
      main_interrupted := false
      main_gain := 1.
    end
    if lane == "main" then
      last_main_entry := entry
      live_release := 0.
    end
    report_to_backend(entry, lane, m["title"])
    if lane == "main" and m["kbr_kind"] == "music" then keep_for_emergency(m["filename"]) end
  end
end


# Fires on every change of what is on air, including returning to a song after a
# timed item. Reported once per change.
last_reported = ref("-")
def report_started(m) =
  entry = m["kbr_entry"]
  if entry != last_reported() then
    last_reported := entry
    send_started(m)
  end
end
program.on_metadata(report_started)
# A new track with no tags at all (an emergency file) raises no metadata event: report
# every track change too, or the backend never learns the engine is on emergency files
program.on_track(report_started)

# ── Control endpoints (localhost only; nginx adds TLS in front) ──────────────
def authorized(req) =
  token != "" and req.headers["x-playout-token"] == token
end

def control(path, handler) =
  def h(req, res) =
    res.content_type("application/json")
    if authorized(req) then
      res.data(handler(req))
    else
      res.status_code(403)
      res.data('{"ok":false,"error":"forbidden"}')
    end
  end
  harbor.http.register(port=control_port, method="POST", path, h)
end

# Body may carry {"fade": seconds}: 0 = cut, longer = soft fade. Default is the quick fade.
control("/skip", fun (req) -> begin
  let json.parse (body : {fade : float?}) = req.body()
  wanted = null.default(body.fade, {skip_fade})
  skip_on_air(seconds=max(0.02, wanted))
  '{"ok":true}'
end)

# A live show starts ({"on": true}) or has ended ({"on": false}). Safe to repeat.
# On: the item on air fades out and does not come back (the backend has queued it to
# carry on afterwards where that applies); a timed item on air is dropped. Off: the
# music lane moves to its next item; the stream stays quiet (hold lane, at most 12 s)
# until that item is loaded, so no emergency music fills in.
control("/live", fun (req) -> begin
  let json.parse (body : {on : bool}) = req.body()
  if body.on and not live_on() then
    log.important("live: show starts")
    go_live()
  elsif not body.on and live_on() then
    log.important("live: show ended")
    live_release := time() + 12.
    live_on := false
  end
  '{"ok":true}'
end)

# Drop the pre-loaded next track (the queue changed) and ask the backend again
control("/flush", fun (_) -> begin
  main_requests.set_queue([])
  # The pre-loaded entry will not air after all: forget that it was handed to us
  if given() != [] and list.hd(default="", given()) != last_main_entry() then
    given := list.tl(given())
  end
  '{"ok":true}'
end)

# Timed items already taken, newest first. The backend may offer one again after it
# restarts; it must not air twice.
timed_seen = ref([])

# The backend hands a group (items due at the same time) over in parallel, each with its
# place (`seq`) and the group's size (`of`), so a slow engine does not hold up its other
# work. The requests can arrive in any order: the group is held until all of it is here
# (or for at most a second, so a part whose hand-over failed holds nothing back), then
# queued in `seq` order, the order it plays in.
timed_buffer = ref([])
def flush_timed_group(at) =
  mine = list.filter(fun (x) -> abs(x.at - at) <= 0.5, timed_buffer())
  timed_buffer := list.filter(fun (x) -> abs(x.at - at) > 0.5, timed_buffer())
  mine = list.sort(fun (a, b) -> a.seq - b.seq, mine)
  list.iter(fun (x) -> schedule_timed(uri=x.uri, at=x.at), mine)
end

control("/timed", fun (req) -> begin
  let json.parse (body : {uri : string, at : float, id : string?, seq : int?, of : int?}) = req.body()
  id = null.default(body.id, {""})
  if id == "" or not list.mem(id, timed_seen()) then
    if id != "" then timed_seen := list.prefix(50, id :: timed_seen()) end
    of = null.default(body.of, {1})
    if of <= 1 then
      schedule_timed(uri=body.uri, at=body.at)
    else
      at = body.at
      timed_buffer := {at=at, seq=null.default(body.seq, {0}), uri=body.uri} :: timed_buffer()
      have = list.length(list.filter(fun (x) -> abs(x.at - at) <= 0.5, timed_buffer()))
      if have >= of then
        flush_timed_group(at)
      else
        wait = at - time() - skip_fade - 0.2
        delay = if wait > 1. then 1. elsif wait > 0.1 then wait else 0.1 end
        thread.run(delay=delay, {flush_timed_group(at)})
      end
    end
  end
  '{"ok":true}'
end)

# Take timed items back: {"ids": [...]}. Any of them still waiting in the timed lane is
# skipped when it would start (see send_started).
control("/timed_drop", fun (req) -> begin
  let json.parse (body : {ids : [string]}) = req.body()
  timed_dropped := list.prefix(100, list.append(body.ids, timed_dropped()))
  '{"ok":true}'
end)

# Mix over: play this on top of what is on air
control("/overlay", fun (req) -> begin
  let json.parse (body : {uri : string}) = req.body()
  log.important("overlay: mixing over the programme")
  overlay_queue.push(request.create(body.uri))
  '{"ok":true}'
end)

control("/status", fun (_) -> begin
  m = program.last_metadata() ?? []
  # Before the music lane's first track its remaining time is infinite, which JSON
  # cannot carry: the handler failed and the connection closed with no answer (Main's
  # engine while it had no station routed, 2026-11-17). -1 = no track.
  left = main.remaining()
  left = if left >= 0. and left < 1000000. then left else -1. end
  json.stringify({ok=true, entry_id=m["kbr_entry"], title=m["title"],
                  main_remaining=left, timed_waiting=timed_queue.length(),
                  live=live_on()})
end)

# Optional: also write the output to a WAV file (testing only)
debug_wav = environment.get("KBR_DEBUG_WAV")
if debug_wav != "" then
  output.file(%wav, debug_wav, radio)
end

# ── One MP3 encode for both outputs ──────────────────────────────────────────
# Best MP3 quality: 320 kbps, 44.1 kHz stereo, slowest and best encoder setting
# (compression_level 0 is LAME's quality 0). Encoded once and copied to Icecast and to
# the HLS segments: two separate encoders cost 14.9% of a core against 8.7% for one
# (measured 2026-11-17), and the server has a single core.
encoded = ffmpeg.encode.audio(
  %ffmpeg(%audio(codec="libmp3lame", b="320k", compression_level=0, ar=44100, channels=2)),
  radio
)
mp3_copy = %ffmpeg(format="mp3", %audio.copy)

# ── HLS copy for iPhone Safari ───────────────────────────────────────────────
# Safari has no Media Source Extensions for MP3, and its plain <audio> waited about
# 10 s on the Icecast stream before the first sound. It plays HLS natively: 1 s MP3
# segments (with the timestamps Apple's packed-audio format needs) start in about a
# second. nginx serves them at /hls/<station id>/mp3.m3u8.
hls_dir = environment.get("KBR_HLS_DIR")
if hls_dir != "" then
  output.file.hls(
    fallible=true,
    playlist="live.m3u8",
    segment_duration=1.0,
    segments=10,
    segments_overhead=5,
    hls_dir,
    [("mp3", mp3_copy)],
    encoded
  )
end

# ── Out to Icecast ───────────────────────────────────────────────────────────
output.icecast(
  mp3_copy,
  fallible=true,
  # A copied stream does not say its bitrate to Icecast by itself
  headers=[("ice-bitrate", "320"), ("ice-audio-info", "ice-samplerate=44100;ice-bitrate=320;ice-channels=2")],
  host=icecast_host,
  port=icecast_port,
  password=icecast_pass,
  mount=icecast_mount,
  name=environment.get(default="Kol B'ramah", "KBR_STATION_NAME"),
  genre="Jewish Radio",
  url="https://kbrlive.com",
  encoded
)

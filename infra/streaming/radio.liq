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

# On start-up tell the backend we hold nothing of its queue any more. Done before any
# track is requested, so the two cannot cross.
try
  ignore(http.post(headers=auth_headers, timeout=8., data="{}",
                   "#{backend_url}/playout/#{station_id}/hello"))
catch err do
  log.important("hello: backend unreachable: #{err}")
end

# ── Main lane: ask the backend for the next track ────────────────────────────
# Last entry from the music lane that went on air
last_main_entry = ref("")
# The last few entries the backend handed us, newest first, sent back with every request
# for the next track. The backend never hands these out again, and closes the older ones
# if a start report was lost while it was unreachable (so nothing is played twice).
given = ref([])
def next_request() =
  try
    handed = string.concat(separator=",", given())
    resp = http.get(headers=auth_headers, timeout=8.,
                    "#{backend_url}/playout/#{station_id}/next?given=#{handed}")
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
# Song-to-song: the outgoing song fades over 3s while the incoming one starts at full level.
# After Next or a timed item the old song must not come back, so that change is a clean cut
# (the music has already been faded out by then).
hard_cut = ref(false)
def transition(ending, starting) =
  if hard_cut() then
    hard_cut := false
    add(normalize=false, [starting.source])
  else
    # The incoming song is listed first: `add` passes on the titles of its first source
    # only, and the start report to the backend is made from them.
    add(normalize=false, [starting.source, fade.out(duration=3., ending.source)])
  end
end
main = cross(duration=3., transition, main)

# The music lane is muted while a timed item takes over, so nothing of it leaks through
main_gain = ref(1.)
main = amplify({main_gain()}, main)
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
timed_queue = request.queue(id="timed", interactive=false)
timed_armed = ref(false)
# Which lane is on air right now ("main", "timed" or "emergency")
on_air_lane = ref("")
# Items due at the same time form a group (a block: ID, weather, sponsors). Once the lane
# is on air it stays on until the group has played through, in the order it was handed over.
def timed_open() =
  timed_armed() or (on_air_lane() == "timed" and timed_queue.length() > 0)
end
timed = switch(id="timed_gate", track_sensitive=true, [(timed_open, timed_queue)])

# Start time of the last group scheduled to take over
timed_group_at = ref(-1.)

# Load the item now; put it on air at `at` (unix seconds). The music fades under it.
def schedule_timed(~uri, ~at) =
  r = request.create(uri)
  def go_on_air() =
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
        timed_queue.skip()
        thread.run(delay=0.1, {master_gain := 1.})
      else
        master_gain := 1.
      end
      thread.run(delay=1.5, {timed_armed := false})
    end)
  end
  # Pushed here, not in a thread, so a group keeps the order it was handed over in
  timed_queue.push(r)
  # One take-over per group: the rest of the group follows from the queue
  if abs(at - timed_group_at()) > 0.5 then
    timed_group_at := at
    wait = at - time() - skip_fade
    thread.run(delay=(if wait > 0. then wait else 0. end), go_on_air)
  end
end

# ── Emergency lane: local files, then silence as the very last resort ────────
emergency = playlist(id="emergency", mode="randomize", reload_mode="watch", emergency_dir)
emergency = mksafe(emergency)

radio = fallback(id="radio", track_sensitive=false, [timed, main, emergency])
radio = amplify({master_gain()}, radio)
# What is on air from the lanes above. Start reports and /status read this, so an item
# mixed over the top (below) never counts as a change of track.
program = radio

# ── Mix over: a jingle played on top of the programme, which carries on ──────
overlay_queue = request.queue(id="overlay", interactive=false)
overlay = amplify(1., override="liq_amplify", overlay_queue)
radio = add(normalize=false, [program, overlay])

# Next: fade out quickly, skip whatever is on air, come back at full level on the new track
def skip_on_air(~seconds) =
  ramp_master(target=0., duration=seconds, fun () -> begin
    if on_air_lane() == "timed" then
      timed_queue.skip()
    else
      hard_cut := true
      main.skip()
    end
    thread.run(delay=0.1, {master_gain := 1.})
  end)
end

# ── Broadcast sound: gentle compression and a limiter on the whole output ────
radio = compress(threshold=-14., ratio=3., attack=20., release=200., radio)
radio = limit(threshold=-1., radio)

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
  lane = if entry == "" then "emergency" else m["kbr_lane"] end
  on_air_lane := lane
  if lane == "timed" and entry != "" and list.mem(entry, timed_dropped()) then
    log.important("timed: entry #{entry} was dropped by the backend; skipping it")
    timed_queue.skip()
  elsif lane == "main" and main_interrupted() then
    # Back from a timed item: leave the interrupted song and start the next one. The
    # interrupted song is not reported again (it already ended for the backend).
    main_interrupted := false
    hard_cut := true
    main.skip()
    thread.run(delay=0.15, {main_gain := 1.})
  else
    if lane == "main" then last_main_entry := entry end
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
  json.stringify({ok=true, entry_id=m["kbr_entry"], title=m["title"],
                  main_remaining=main.remaining(), timed_waiting=timed_queue.length()})
end)

# Optional: also write the output to a WAV file (testing only)
debug_wav = environment.get("KBR_DEBUG_WAV")
if debug_wav != "" then
  output.file(%wav, debug_wav, radio)
end

# ── HLS copy for iPhone Safari ───────────────────────────────────────────────
# Safari has no Media Source Extensions for MP3, and its plain <audio> waited about
# 10 s on the Icecast stream before the first sound. It plays HLS natively: 1 s MP3
# segments (with the timestamps Apple's packed-audio format needs) start in about a
# second. nginx serves them at /hls/<station id>/mp3.m3u8.
hls_dir = environment.get("KBR_HLS_DIR")
if hls_dir != "" then
  output.file.hls(
    playlist="live.m3u8",
    segment_duration=1.0,
    segments=10,
    segments_overhead=5,
    hls_dir,
    [("mp3", %mp3(bitrate=320, samplerate=44100, stereo=true, internal_quality=0))],
    radio
  )
end

# ── Out to Icecast ───────────────────────────────────────────────────────────
output.icecast(
  # Best MP3 quality: 320 kbps, 44.1 kHz stereo, slowest and best encoder setting
  %mp3(bitrate=320, samplerate=44100, stereo=true, internal_quality=0),
  host=icecast_host,
  port=icecast_port,
  password=icecast_pass,
  mount=icecast_mount,
  name=environment.get(default="Kol B'ramah", "KBR_STATION_NAME"),
  genre="Jewish Radio",
  url="https://kbrlive.com",
  radio
)

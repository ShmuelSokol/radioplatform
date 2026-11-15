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

main_requests = request.dynamic(id="main", prefetch=1, retry_delay=2., timeout=30., next_request)
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
    add(normalize=false, [fade.out(duration=3., ending.source), starting.source])
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

# Which lane is on air right now ("main", "timed" or "emergency")
on_air_lane = ref("")

# ── Timed lane: hard-timed items and Play Now ────────────────────────────────
timed_queue = request.queue(id="timed", interactive=false)
timed_armed = ref(false)
timed = switch(id="timed_gate", track_sensitive=true, [({timed_armed()}, timed_queue)])

# Load the item now; put it on air at `at` (unix seconds). The music fades under it.
def schedule_timed(~uri, ~at) =
  r = request.create(uri)
  def go_on_air() =
    ramp_master(target=0., duration=skip_fade, fun () -> begin
      # Mute the music lane, put the timed item on air at full level. The interrupted
      # song does not resume: when the music lane comes back it moves to its next song
      # (see report_started).
      if on_air_lane() == "main" then
        main_gain := 0.
        main_interrupted := true
      end
      timed_armed := true
      master_gain := 1.
      thread.run(delay=1.5, {timed_armed := false})
    end)
  end
  def load() =
    timed_queue.push(r)
    wait = at - time() - skip_fade
    thread.run(delay=(if wait > 0. then wait else 0. end), go_on_air)
  end
  thread.run(fast=false, load)
end

# ── Emergency lane: local files, then silence as the very last resort ────────
emergency = playlist(id="emergency", mode="randomize", reload_mode="watch", emergency_dir)
emergency = mksafe(emergency)

radio = fallback(id="radio", track_sensitive=false, [timed, main, emergency])
radio = amplify({master_gain()}, radio)

# Next: fade out quickly, skip whatever is on air, come back at full level on the new track
def skip_on_air() =
  ramp_master(target=0., duration=skip_fade, fun () -> begin
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
def send_started(m) =
  entry = m["kbr_entry"]
  lane = if entry == "" then "emergency" else m["kbr_lane"] end
  on_air_lane := lane
  if lane == "main" then
    last_main_entry := entry
    if main_interrupted() then
      # Back from a timed item: leave the interrupted song and start the next one
      main_interrupted := false
      hard_cut := true
      main.skip()
      thread.run(delay=0.15, {main_gain := 1.})
    end
  end
  payload = json.stringify({entry_id=entry, lane=lane, title=m["title"], at=time()})
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
radio.on_metadata(report_started)

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

control("/skip", fun (_) -> begin
  skip_on_air()
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

control("/timed", fun (req) -> begin
  let json.parse (body : {uri : string, at : float}) = req.body()
  schedule_timed(uri=body.uri, at=body.at)
  '{"ok":true}'
end)

control("/status", fun (_) -> begin
  m = radio.last_metadata() ?? []
  json.stringify({ok=true, entry_id=m["kbr_entry"], title=m["title"],
                  main_remaining=main.remaining(), timed_waiting=timed_queue.length()})
end)

# Optional: also write the output to a WAV file (testing only)
debug_wav = environment.get("KBR_DEBUG_WAV")
if debug_wav != "" then
  output.file(%wav, debug_wav, radio)
end

# ── Out to Icecast ───────────────────────────────────────────────────────────
output.icecast(
  %mp3(bitrate=192),
  host=icecast_host,
  port=icecast_port,
  password=icecast_pass,
  mount=icecast_mount,
  name="Kol Bramah Live",
  genre="Jewish Radio",
  url="https://kbrlive.com",
  radio
)

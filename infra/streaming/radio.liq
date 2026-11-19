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
# Task queues. The engine's quick steps (level back up after Next or a timed item, the
# fade itself, a time check's take-over at its second) run as tasks, and so do its
# calls to the backend and every download. Liquidsoap's default is two queues for all
# of them: with the backend answering in 3 to 4 s, a start report and the ask for the
# next item filled both, the "level back up" task waited behind them, and the stream
# was digital silence for 2.6 to 3.8 s after Next and after a time check (Jerusalem
# 2026-11-18 05:30Z to 06:02Z; harness slow_backend_no_gap). Now the quick steps have
# queues the slow calls cannot take (a call marked fast=false never runs on a fast
# queue), and there is room for a whole timed group downloading at once.
settings.scheduler.fast_queues := 6
settings.scheduler.generic_queues := 6

# A random id of this engine run, sent with every call and in /status. The backend plays a
# station to one engine only: another engine calling with this station's id (an FM box
# set up with a copied id, one left running after a move) is refused while this one
# answers on its control line, so the two never split the queue or both reach the air.
engine_instance = "#{random.int(min=100000000, max=999999999)}-#{int_of_float(time())}"

auth_headers = [("X-Playout-Token", token), ("X-Playout-Instance", engine_instance),
                ("Content-Type", "application/json")]

# What this engine understands, sent with /hello and every /next. 2 = timed groups handed
# over in parallel (seq/of) and /timed_drop. 3 = the hold lane (quiet before a hard item
# when no song is handed out). 4 = the next item is loaded while a host is on air (the
# live lane keeps it off the stream until the show ends). 5 = the live lane plays the
# show from the backend's live.mp3 (listener pages then hear it on the stream). 6 = the
# emergency alert lane (/alert, /alert_stop, /alert_line; reports to /alert). The
# backend keeps to the old serial hand-over for an engine that sends nothing.
engine_proto = 6

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
      log.important("hello: backend answered (engine #{engine_instance})")
    elsif resp.status_code == 409 then
      log.important("hello: REFUSED, another engine plays this station (wrong station id?); trying again in 5 s")
    else
      log.important("hello: backend answered #{resp.status_code}; trying again in 5 s")
    end
  catch err do
    log.important("hello: backend unreachable: #{err}; trying again in 5 s")
  end
end
send_hello()
# The output starts before the backend has handed over the first item: every restart
# opened with about 2 s of an emergency song, then cut to the queue (both stations at
# the 08:23Z rerun, 2026-11-18; on the FM box it would be every power-up). With the
# backend answering, the stream stays quiet (hold lane) until the first item is loaded,
# at most 8 s. With no backend the emergency music starts at once, as before.
start_quiet_until = ref(if hello_done() then time() + 8. else 0. end)
if not hello_done() then
  thread.run.recurrent(fast=false, delay=5., fun () -> begin
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
# "Live on" has come but the stream is not quiet yet: the music plays on until the
# show's audio is here (see /live). 0 = not waiting, otherwise when it was asked.
live_asked = ref(0.)
def live_wanted() =
  live_on() or live_asked() > 0.
end
# No ask before this moment. While a song plays the music lane calls next_request on
# every frame (25 a second) until it holds the next item; retry_delay only spaces the
# asks while nothing plays. An empty answer (204) was asked again 20 to 25 times a
# second, each a database read on the backend (harness engine_rows, 2026-11-19). After
# an empty answer or an error the next ask waits 1.5 s; a flush, a Next or a live
# change asks at once.
ask_after = ref(0.)
# Counts flushes. An ask that was on its way when the queue changed comes back with the
# item the backend meant before the change: queued after the flush, it aired instead of
# the Play next item (Jerusalem 03:46:43Z 2026-11-19, a station ID). Such an answer is
# dropped and the backend asked again at once.
flush_count = ref(0)
# When the backend last handed us an item (see hold_open: quiet while it loads)
handed_at = ref(0.)
# Items shorter than this get the item after them fetched at once (see ask_backend)
short_item = 12.
# Fetching the item after a short one: the music lane does not ask meanwhile, or the
# two answers could land in the queue in the wrong order
following = ref(false)
# main_requests.add, set once the music lane exists (it is built from next_request)
add_main = ref(fun (_) -> false)
# main_requests.queue, likewise
main_requests_queue = ref(fun () -> [])
# The entry the music lane last began reading (its on_track, before it is heard)
main_track_entry = ref("")
def request_entry(r) =
  list.assoc(default="", 1, r/kbr_entry="([^"]+)"/.exec(request.uri(r)))
end
def short_queued(entry) =
  q = main_requests_queue()
  list.exists(fun (x) -> request_entry(x) == entry, q())
end
def rec ask_backend() =
  flushes = flush_count()
  handed = string.concat(separator=",", given())
  resp = http.get(headers=auth_headers, timeout=8.,
                  "#{backend_url}/playout/#{station_id}/next?given=#{handed}&proto=#{engine_proto}&live=#{if live_wanted() then 1 else 0 end}")
  if resp.status_code == 200 then
    let json.parse (data : {uri : string, entry_id : string, seconds : float?}) = resp
    if flushes != flush_count() then
      log.important("next: the queue changed while asking (entry #{data.entry_id} is out of date); asking again")
      "stale"
    else
      log.important("next: backend gave entry #{data.entry_id}")
      given := list.prefix(5, data.entry_id :: given())
      handed_at := time()
      ask_after := 0.
      # A short item (a 2 s station ID) is over before the next song's file is here,
      # if that is only asked for when the short item starts: 2 to 3 s of emergency
      # music (Jerusalem 03:46Z and 04:00Z 2026-11-19). The item after it is fetched
      # now, into the queue behind it.
      secs = null.get(default=0., data.seconds)
      if secs > 0. and secs < short_item then
        following := true
        short_entry = data.entry_id
        thread.run(fast=false, fun () -> begin
          before = flush_count()
          follow = ask_backend()
          if follow != "" and follow != "stale" then
            r = request.create(follow)
            # Downloaded first (request.resolve), so the queue only ever holds it whole
            if request.resolve(timeout=120., r) and before == flush_count() then
              # Added only once the short item is in the queue (or playing): the music
              # lane queues it only after its own download, and the item after it was
              # sometimes added first and aired first (Main 15:39Z 2026-11-19: a song,
              # then the station ID handed out before it, right before the sponsorships)
              def rec place(tries) =
                if before != flush_count() then
                  log.important("next: the item after a short one was not kept (queue changed)")
                  request.destroy(r)
                  following := false
                elsif main_track_entry() == short_entry or tries >= 150 or short_queued(short_entry) then
                  log.important("next: fetched the item after a #{secs} s item in advance")
                  f = add_main()
                  ignore(f(r))
                  following := false
                else
                  thread.run(delay=0.1, {place(tries + 1)})
                end
              end
              place(0)
            else
              log.important("next: the item after a short one was not kept (queue changed or no file)")
              request.destroy(r)
              following := false
            end
          else
            following := false
          end
        end)
      end
      data.uri
    end
  else
    ask_after := time() + 1.5
    ""
  end
end
def next_request() =
  def ask() = ask_backend() end
  if time() < ask_after() or following() then
    null()
  else
    try
      uri = ref(ask())
      tries = ref(1)
      while uri() == "stale" and tries() < 3 do
        uri := ask()
        tries := tries() + 1
      end
      if uri() == "" or uri() == "stale" then null() else request.create(uri()) end
    catch err do
      log.important("next: backend unreachable: #{err}")
      ask_after := time() + 2.
      null()
    end
  end
end

# A long song is a big download: give it time to arrive (it is fetched while the song
# before it plays)
main_requests = request.dynamic(id="main", prefetch=1, retry_delay=2., timeout=120., next_request)
add_main := fun (r) -> main_requests.add(r)
main_requests_queue := fun () -> main_requests.queue()
# When the music lane last began reading a new item (before `cross` has buffered its
# start and called transition()). skip_main() uses it to tell a skip that took (the next
# item is on its way) from one that only ended a transition.
main_fetched_at = ref(0.)
main_requests.on_track(fun (m) -> begin
  main_fetched_at := time()
  main_track_entry := m["kbr_entry"]
end)
# Per-track level from the library's audio analysis (backend sends liq_amplify)
main = amplify(1., override="liq_amplify", main_requests)
# Song-to-song: the outgoing song fades out under the incoming one, which starts at full
# level. How long they overlap comes with each item from the backend (`kbr_overlap`, the
# station's "Song crossfade" setting, 1 s by default; 0 = none). Anything spoken (a
# sponsorship, a time check, a shiur) has overlap 0 and is never talked over: the next
# item starts when it has ended (Shmuel 2026-11-17: sponsorships played in a row ran
# into each other). `liq_cross_duration` tells `cross` how early to call transition().
# A song is not crossfaded into a spoken item either: it fades out, then the item starts.
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
    # A song before anything spoken (Shmuel 2026-11-17, "Song ends first, then speech":
    # the last second of a song played under the first words of a sponsorship). Every
    # item carries its kind; none (the emergency music) counts as a song.
    kind = starting.metadata["kbr_kind"]
    speech_next = kind != "" and kind != "music"
    if overlap < 0.05 then
      # No overlap: the last moment of the ending item plays out, then the next starts
      sequence([ending.source, starting.source])
    elsif speech_next then
      # The song fades out over its last moment, and only then the spoken item starts
      sequence([fade.out(duration=overlap, ending.source), starting.source])
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

# The group (its `at`) that went on air early because the music lane had run out a few
# seconds before it: its own timer must not take over a second time (it would cut the
# group's first item).
timed_early = ref(-1.)

# Into the timed lane: the item is queued and its group gets its take-over.
# Into the timed lane: the item counts as pending and its group gets its take-over.
# `push=false` when its file is still downloading: the download pushes it when done.
# Timed items the backend took back (/timed_drop): skipped the moment they would start.
# Silence left in the timed lane when a blackout ended went on air one item per Next.
timed_dropped = ref([])
# The entries handed to the lane and their group's time, so an alert can take back a
# group that fell due while it was on air (see alert_drop_stale_timed)
timed_entries = ref([])
def timed_admit(r, at, ~push=true) =
  # Counted before the push: with prefetch the push itself can wait for the download
  timed_pending := timed_pending() + 1
  found = r/kbr_entry="([^"]+)"/.exec(request.uri(r))
  entry = list.assoc(default="", 1, found)
  if entry != "" then timed_entries := list.prefix(50, (at, entry) :: timed_entries()) end
  # Pushed here, not in a thread, so a group keeps the order it was handed over in
  if push then timed_queue.push(r) end
  # One take-over per group: the rest of the group follows from the queue
  if abs(at - timed_group_at()) > 0.5 then
    timed_group_at := at
    wait = at - time() - skip_fade
    # (unless the group already went on air early: see the hold lane)
    thread.run(delay=(if wait > 0. then wait else 0. end),
               {if timed_early() != at then timed_go_on_air(180, at) end})
  end
end

# ── Waiting room: timed items loaded ahead of their time ─────────────────────
# The backend hands a hard item over up to 45 s ahead, so its file is here in time. It
# used to go straight into the timed lane's queue, and that queue knows no times: it
# plays whatever is next. So with a time check loaded for the hour,
#   - a Play now (or a Next that went through this lane) in those 45 s was followed at
#     once by the time check, early;
#   - a second group handed over before the first had started took over the lane's one
#     "group time", the first group's take-over gave up, and it aired at the second
#     group's time (harness live_show_over_hard_item: a second of emergency music at
#     the first time check, 2026-11-18).
# Now an item due later waits here while it downloads, and its group enters the lane
# `timed_room_lead` seconds before its time: before the hold lane's 8 s, so everything
# that looks at the lane (the hold, the early start after a song that ends a few
# seconds before) finds the group there as before. An item still downloading when its
# group enters counts as pending at once (so the lane waits for it, as it did for a
# slow file in the queue) and is pushed the moment its download ends.
timed_room = ref([])
timed_room_lead = 9.
# Pushed once: the group's entry and the end of the download can meet at the same moment
timed_room_pushed = ref([])
def timed_room_push(r) =
  rid = request.id(r)
  if not list.mem(rid, timed_room_pushed()) then
    timed_room_pushed := list.prefix(50, rid :: timed_room_pushed())
    timed_queue.push(r)
  end
end
def timed_room_admit(at) =
  mine = list.filter(fun (x) -> abs(x.at - at) <= 0.5, timed_room())
  timed_room := list.filter(fun (x) -> abs(x.at - at) > 0.5, timed_room())
  log.important("timed: #{list.length(mine)} item(s) due in #{at - time()} s enter the lane")
  list.iter(fun (x) -> begin
    x.admitted := true
    timed_admit(x.r, x.at, push=false)
    if x.done() then
      timed_room_push(x.r)
    else
      log.important("timed: an item of the group is still downloading; it follows when it is here")
    end
  end, mine)
end

# Load the item now; put it on air at `at` (unix seconds). The music fades under it.
def schedule_timed(~uri, ~at) =
  r = request.create(uri)
  if at - time() > timed_room_lead + 0.5 and abs(at - timed_group_at()) > 0.5 then
    first = not list.exists(fun (x) -> abs(x.at - at) <= 0.5, timed_room())
    x = {at=at, r=r, done=ref(false), admitted=ref(false)}
    timed_room := list.append(timed_room(), [x])
    if first then
      thread.run(delay=at - time() - timed_room_lead, {timed_room_admit(at)})
    end
    # Downloaded now, in this handler (as the queue's push did); the items of a group
    # are handed in one after another, so they arrive in order
    ignore(request.resolve(content_type=timed_queue, timeout=90., r))
    x.done := true
    log.important("timed: item due in #{at - time()} s loaded (first of its group: #{first}, in the lane already: #{x.admitted()})")
    # Its group went into the lane while it downloaded: it was counted then, push it now
    if x.admitted() then timed_room_push(r) end
  else
    # Due now or within seconds (a Play now, a late hand-over), or its group is in the
    # lane already
    timed_admit(r, at)
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
# The backend handed us an item that has not started and is not loaded yet: the item
# before it was too short to cover the download (a 2 s station ID before a song,
# Jerusalem 03:46:46Z 2026-11-19, 2 s of emergency music). Quiet while it loads, at
# most 6 s after it was handed out.
def handed_loading() =
  list.hd(default="", given()) != last_main_entry() and list.length(main_requests.queue()) == 0
    and time() - handed_at() < 6.
end
def hold_open() =
  left = timed_group_at() - time()
  (timed_pending() > 0 and left < 8. and left > -2.) or time() < live_release() or time() < start_quiet_until()
    or handed_loading()
end
hold = switch(id="hold_gate", track_sensitive=false, [(hold_open, blank(id="hold"))])

# ── Live lane: the host's show while a host is on air ─────────────────────────
# Without this lane the song on the engine played on under the host (Jerusalem 23:06Z
# 2026-11-17): the backend only stopped handing out new items. While the lane is on
# air the lanes below stand still; the backend switches it with /live.
# The show itself comes from the backend as listeners hear it (after the broadcast
# delay, with Dumps and the time checks in it): live.mp3, opened on "live on" and
# closed on "live off" (engine proto 5). Until its first audio, when it stalls and
# after it ends (the delay played out) the lane is quiet: never music or emergency
# under the host. The token goes as a header (ffmpeg's "headers"), never in the URL.
live_feed = input.http(
  id="live_feed", start=false, timeout=5., poll_delay=1., max_buffer=3.,
  string_args=[("headers", "X-Playout-Token: #{token}\r\n")],
  on_connect=fun (_) -> log.important("live: show audio connected"),
  on_disconnect=fun () -> log.important("live: show audio ended or stalled; quiet"),
  "#{backend_url}/playout/#{station_id}/live.mp3"
)
on_air_quiet = switch(id="host_gate", track_sensitive=false, [
  ({live_on()}, fallback(id="host_lane", track_sensitive=false, [live_feed, blank(id="host_on_air")]))
])

radio = fallback(id="radio", track_sensitive=false, [on_air_quiet, timed, main, hold, emergency])
radio = amplify(override=null(), {master_gain()}, radio)
# What is on air from the lanes above. Start reports and /status read this, so an item
# mixed over the top (below) never counts as a change of track.
program = radio

# ── Mix over: a jingle played on top of the programme, which carries on ──────
overlay_queue = request.queue(id="overlay", interactive=false)
overlay = amplify(1., override="liq_amplify", overlay_queue)
radio = add(normalize=false, [program, overlay])

# ── Emergency alert lane: above everything, live shows and time checks included ─
# An emergency alert (EAS) takes the station over the moment it arrives, plays whole,
# and the programme carries on where it stood. Two ways in:
#   - /alert from the backend: an audio file (the API, a staff test). Queued here, so
#     two alerts play one after the other, never over each other.
#   - the alert line (a local FM box only, KBR_ALERT_ALSA_DEVICE): the EAS decoder's
#     audio output wired into a line input. Sound on it takes the air at once and the
#     air is handed back after KBR_ALERT_LINE_RELEASE seconds of quiet; /alert_line
#     {"on": true} forces it on (a relay contact, a local script). Works with no
#     internet: nothing here waits on the backend.
# Below this lane everything stands still meanwhile (a song or a shiur pauses and
# resumes); hard items that came due during the alert are dropped afterwards, as after
# a live show, so a time check never airs minutes late. Sits after the master level and
# the mix-over, so a fade, a Next or a jingle never touches it; before the compressor and
# limiter, so it is never louder than the programme allows. The backend is told when each
# alert starts and ends (POST /playout/{station}/alert) for its log, page and ntfy.
alert_queue = request.queue(id="alert", interactive=false, timeout=60., prefetch=1)
alert_play = amplify(1., override="liq_amplify", alert_queue)
alert_line_device = environment.get("KBR_ALERT_ALSA_DEVICE")
alert_line_release = float_of_string(environment.get(default="4.", "KBR_ALERT_LINE_RELEASE"))
alert_line_threshold = float_of_string(environment.get(default="-40.", "KBR_ALERT_LINE_THRESHOLD"))
# Forced on by /alert_line, and heard on the line (sound above the threshold)
alert_line_forced = ref(false)
alert_line_heard = ref(false)
def alert_line_open() = alert_line_forced() or alert_line_heard() end
alert_lanes = ref([alert_play])
if alert_line_device != "" then
  line_in = input.alsa(id="alert_line_in", device=alert_line_device, bufferize=true)
  line_in = blank.detect(
    id="alert_line_detect", threshold=alert_line_threshold, max_blank=alert_line_release,
    min_noise=0.15, track_sensitive=false, start_blank=true,
    on_noise={alert_line_heard := true},
    {alert_line_heard := false},
    line_in)
  # Kept running whether or not the line is on air, so its sound is always noticed
  output.dummy(id="alert_line_watch", fallible=true, line_in)
  line_in = buffer(id="alert_line_buffer", buffer=0.1, max=1., fallible=true, line_in)
  line_lane = switch(id="alert_line_gate", track_sensitive=false, [(alert_line_open, line_in)])
  alert_lanes := [alert_play, line_lane]
  log.important("alert line: listening on #{alert_line_device}")
end
alert_lane = fallback(id="alert_lane", track_sensitive=false, alert_lanes())

# What the alert lane is doing, for the reports below and /status
alert_on_air = ref(false)
alert_current = ref("")      # the backend's alert id ("" for the line)
alert_source = ref("")       # "api" or "line"
alert_started_at = ref(0.)

def alert_report(phase, id, source, started) =
  payload = json.stringify({phase=phase, alert_id=id, source=source, at=time(),
                            started_at=started})
  thread.run(fast=false, fun () -> begin
    try
      resp = http.post(headers=auth_headers, timeout=8., data=payload,
                       "#{backend_url}/playout/#{station_id}/alert")
      log.important("alert: #{phase} reported, backend answered #{resp.status_code}")
    catch err do
      log.important("alert: #{phase} not reported, backend unreachable: #{err}")
    end
  end)
end

# A hard group that came due while the alert was on air never started: drop it, as the
# backend does (it closes those items), so it does not play late, now or before the
# next group. Items due later stay.
def alert_drop_stale_timed() =
  if timed_pending() > 0 and timed_group_at() <= time() and timed_group_at() >= alert_started_at() - 1. then
    log.important("alert: the timed group due #{time() - timed_group_at()} s ago is dropped")
    # Taken back as the backend's /timed_drop does: each is skipped the moment it would
    # start. Skipping here did not work: the lane is not playing during the alert, and
    # a skip then left it so the next group's items aired with no start report and only
    # the first of them played (harness: alert over a time check, then a 3-item group).
    at = timed_group_at()
    gone = list.map(snd, list.filter(fun (x) -> abs(fst(x) - at) <= 0.5, timed_entries()))
    timed_dropped := list.prefix(100, list.append(gone, timed_dropped()))
    timed_pending := 0
    timed_armed := false
    # Its take-over had already muted the music lane (behind the alert, so unheard):
    # the item it would have interrupted simply carries on, as after any pause
    if main_interrupted() then
      main_interrupted := false
      main_gain := 1.
    end
    master_gain := 1.
  end
end

def alert_began(id, source) =
  if not alert_on_air() then
    alert_on_air := true
    alert_current := id
    alert_source := source
    alert_started_at := time()
    log.important("alert: ON AIR (#{source} #{id})")
    alert_report("started", id, source, alert_started_at())
  elsif id != "" and id != alert_current() then
    # The next alert of the queue follows straight on: report the change
    alert_report("ended", alert_current(), alert_source(), alert_started_at())
    alert_current := id
    alert_source := source
    alert_started_at := time()
    alert_report("started", id, source, alert_started_at())
  end
end
alert_lane.on_track(fun (m) -> alert_began(m["kbr_alert"], if m["kbr_alert"] != "" then "api" else "line" end))
alert_lane.on_metadata(fun (m) -> if m["kbr_alert"] != "" then alert_began(m["kbr_alert"], "api") end)

# The lane went quiet: the programme is back
thread.run.recurrent(delay=0.25, fun () -> begin
  if alert_line_open() and not alert_on_air() then alert_began("", "line") end
  if alert_on_air() and not alert_queue.is_ready() and not alert_line_open() then
    alert_on_air := false
    log.important("alert: off air after #{time() - alert_started_at()} s; programme resumes")
    alert_report("ended", alert_current(), alert_source(), alert_started_at())
    alert_drop_stale_timed()
    alert_current := ""
  end
  0.25
end)

radio = fallback(id="with_alert", track_sensitive=false, [alert_lane, radio])

# Skip the song on air in the music lane. A second Next within 3 s of a song change was
# lost (harness next_twice, 2026-11-17): the skip only ended cross's transition. So the
# skip is checked: if no song change followed within 0.3 s, it is given once more.
# The output stays faded down until the change (at most 0.4 s), so the old song is not
# heard again in between.
def skip_main() =
  asked = time()
  hard_cut := true
  # Quiet, not emergency music, while the next item gets its first audio. An item can
  # be "prepared" with nothing decoded yet: Main 13:27:42Z 2026-11-18, Next pressed by
  # hand, the crossfade had 0.00 s of the next item to cross into and the emergency
  # lane played for 1.7 s. The next start from the music lane closes it.
  start_quiet_until := time() + 6.
  main.skip()
  thread.run(delay=0.1, {if main_changed_at() >= asked then master_gain := 1. end})
  thread.run(delay=0.3, {
    # Not when the next item is already being read: on the busy server its start took
    # longer than 0.3 s to buffer, and the second skip ran over it (Main 03:46Z
    # 2026-11-18: a sponsorship never aired and a second of emergency music played)
    if main_changed_at() < asked and main_fetched_at() < asked and on_air_lane() != "timed" then
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
# In the music lane it waits until the next item's file is here (at most skip_wait): the
# item on air plays on meanwhile. Skipping at once with nothing loaded put a second of
# emergency music on air before a shiur (Jerusalem 05:55:39Z 2026-11-18: the file needed
# 3 s) and when Next came while the backend was holding back the next song before a
# hard item (it hands one out at the engine's next ask, at most 2 s later).
# 12 s: a file that was never analysed is read once by the backend (for its level) and
# then by the engine, so a slow one takes twice its download (harness
# operator_next_slow_file: 11 s). After that the skip goes ahead whatever is there.
skip_wait = 12.
def main_loaded() =
  list.length(main_requests.queue()) > 0
end
# A Next that is waiting for the file: a second press meanwhile is the same wish, not a
# second skip (it would skip the awaited item the moment it started)
skip_waiting = ref(false)
def skip_on_air(~seconds) =
  def go() =
    ramp_master(target=0., duration=seconds, fun () -> begin
      if on_air_lane() == "timed" then
        timed_queue.skip()
        thread.run(delay=0.1, {master_gain := 1.})
      else
        skip_main()
      end
    end)
  end
  asked = time()
  def rec wait() =
    # The backend may have had nothing a moment ago: while Next waits, ask every 0.5 s
    if ask_after() > time() + 0.5 then ask_after := time() + 0.5 end
    if on_air_lane() != "main" or main_loaded() or time() - asked > skip_wait then
      if time() - asked > 0.05 then
        log.important("skip: waited #{time() - asked} s for the next item's file")
      end
      skip_waiting := false
      go()
    else
      thread.run(delay=0.1, wait)
    end
  end
  if skip_waiting() then
    log.important("skip: already waiting for the next item's file; this press is the same one")
  else
    if on_air_lane() == "main" and not main_loaded() then
      log.important("skip: the next item is not loaded yet; the item on air plays on until it is")
      skip_waiting := true
    end
    wait()
  end
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

# ── Broadcast sound: leveler, three-band compressor, limiter ────────────────
# Shmuel 2026-11-19: "the best audio possible". Before this the output had only a gentle
# compressor and a limiter that never touched it: Main measured -19.5 LUFS, items 6 LU
# apart (songs -17.6, the spoken run after the 8 PM time check -24), peaks at -5.7 dBFS.
# Now, the way a broadcast processor does it (measured on that same recording: -15.4
# LUFS, items within 2 LU, true peaks under -1 dBFS; CPU about 1.5% of a core more):
#   1. Leveler (AGC): rides the level slowly towards one loudness, so a quiet song, a
#      soft-spoken sponsorship and a loud one come out alike. It stands still below
#      -45 dB (pauses between words, the quiet before a timed item, fade-outs), so it
#      never pumps up silence or hiss, and it never moves more than 10 dB down / 15 up.
#   2. Three bands (below 150 Hz, 150 Hz to 4 kHz, above 4 kHz), each compressed on its
#      own: the bass stays firm without pushing the voice down, the voice stays clear,
#      the highs do not hiss or bite.
#   3. Brickwall limiter with look-ahead (FFmpeg alimiter): no peak past -1.5 dBFS, so
#      with what the MP3 encoder adds, true peaks stay around -1 dBFS. Liquidsoap's own
#      limit() is a compressor with a 50 ms attack: peaks got through it (true peaks of
#      -0.6 dBFS on Jerusalem with threshold -2, 2026-11-17).
# Everything on air goes through it: songs, spoken items, the live host, alerts.
# Per engine: KBR_PROCESSING=broadcast for this chain, light (the default, and the sound
# before 2026-11-19: compressor -14 dB 3:1, limiter -3 dBFS) otherwise. setup.sh sets it
# per station (KBR_BROADCAST_SOUND), so one station can go first; a change takes that
# engine's restart.
processing = environment.get(default="light", "KBR_PROCESSING")
processing_target = float_of_string(environment.get(default="-14.", "KBR_PROCESSING_TARGET"))

# Runs `build` (FFmpeg filters) over the whole output. An FFmpeg filter source counts as
# fallible (it never stops while radio plays), and it stays that way: the outputs below
# take a fallible source. It used to be wrapped in mksafe(), and with that the engine
# could spin at 100% CPU at start and never play, depending on nothing more than the
# number of definitions in this file: mksafe's switch asked the filter whether it was
# ready before its input was set up, and the filter ran its inner clock forever (gdb,
# 2026-11-18).
def ffmpeg_chain(s, build) =
  def graph_of(graph) =
    let {audio = a} = source.tracks(ffmpeg.raw.encode.audio(%ffmpeg(%audio.raw), s))
    a = ffmpeg.filter.audio.input(graph, pass_metadata=true, a)
    a = build(graph, a)
    a = ffmpeg.filter.audio.output(graph, pass_metadata=true, a)
    source({audio = a, metadata = track.metadata(a), track_marks = track.track_marks(a)})
  end
  ffmpeg.raw.decode.audio(ffmpeg.filter.create(graph_of))
end

def band_compress(graph, band, threshold, ratio, attack, release, makeup) =
  ffmpeg.filter.acompressor(graph, threshold=lin_of_dB(threshold), ratio=ratio,
                            attack=attack, release=release, knee=4.,
                            makeup=lin_of_dB(makeup), band)
end

# The leveler: Liquidsoap's normalize() (2.2), with one change. normalize() ends in a
# plain amplify(), and amplify takes each track's liq_amplify over its own gain: the
# leveler stood still and every item's level was applied twice (harness
# broadcast_processing, 2026-11-19; the same trap as main_gain above). Here the gain is
# only ever the leveler's (override=null()).
def leveler(~target, ~up, ~down, ~window, ~gain_min, ~gain_max, ~threshold, s) =
  s = rms.smooth(id="leveler_rms", duration=window, s)
  level = s.rms
  v = ref(1.)
  frame = frame.duration()
  gain_min = lin_of_dB(gain_min)
  gain_max = lin_of_dB(gain_max)
  target = lin_of_dB(target)
  threshold = lin_of_dB(threshold)
  def update() =
    now = level()
    # Below the threshold (a pause, the quiet before a timed item) the gain stands still
    if now >= threshold then
      speed = if v() * now <= target then up else down end
      v := v() + (1. - exp(0. - frame / speed)) * ((target / now) - v())
      v := max(gain_min, min(gain_max, v()))
    end
  end
  s = source.on_frame(s, update)
  amplify(id="leveler", override=null(), {v()}, s)
end

def broadcast_filters(graph, a) =
  begin
    # Rumble and DC out first: they eat headroom nobody hears
    a = ffmpeg.filter.highpass(graph, f=35., poles=2, a)
    # Linkwitz-Riley (4th order) bands add back up flat
    let (bands, _) = ffmpeg.filter.acrossover(graph, split="150 4000", order=1, a)
    low = band_compress(graph, list.nth(bands, 0), -24., 3., 40., 350., 3.)
    mid = band_compress(graph, list.nth(bands, 1), -22., 2.5, 12., 220., 2.)
    high = band_compress(graph, list.nth(bands, 2), -26., 2.5, 5., 150., 3.)
    a = ffmpeg.filter.amix(graph, inputs=3, normalize=false, [low, mid, high], [])
    ffmpeg.filter.alimiter(graph, limit=lin_of_dB(-1.5), attack=5., release=60., level=false, a)
  end
end

def broadcast_sound(s) =
  s = leveler(target=processing_target, up=2., down=1., window=1.,
              gain_min=-10., gain_max=15., threshold=-45., s)
  # amix makes new frames, so the titles do not pass the bands: Icecast showed no title
  # (harness broadcast_processing, 2026-11-19). They are put back on the far side as
  # they go in. (The start reports to the backend are taken before all this, from
  # `program`.)
  processed = insert_metadata(id="processed", ffmpeg_chain(s, broadcast_filters))
  s.on_metadata(fun (m) -> processed.insert_metadata(m))
  processed
end

def light_sound(s) =
  s = compress(threshold=-14., ratio=3., attack=20., release=200., s)
  ffmpeg_chain(s, fun (graph, a) ->
    ffmpeg.filter.alimiter(graph, limit=0.708, attack=5., release=50., level=false, a))
end

log.important("sound: #{processing} processing" ^ (if processing == "light" then "" else ", leveler target #{processing_target} dB" end))
radio = if processing == "light" then light_sound(radio) else broadcast_sound(radio) end

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
def keep_for_emergency(played, asset) =
  if played != "" and file.exists(played) then
    # One copy per item, named after it (the download's own name is new every time, so
    # a song played twice took two of the 40 places, harness engine_rows 2026-11-19)
    name = if asset != "" then "#{asset}.mp3" else path.basename(played) end
    dest = "#{emergency_dir}/" ^ name
    # Copied under a temporary name first, so the playlist never sees half a file
    part = dest ^ ".part"
    cmd = "cp -f #{process.quote(played)} #{process.quote(part)} && mv -f #{process.quote(part)} #{process.quote(dest)} && cd #{process.quote(emergency_dir)} && ls -1t | tail -n +#{emergency_keep + 1} | tr '\\n' '\\000' | xargs -0 -r rm -f --"
    thread.run(fast=false, {ignore(process.run(cmd))})
  end
end


def send_started(m) =
  entry = m["kbr_entry"]
  # No entry: an emergency file, or the quiet held just before a hard timed item
  lane = if entry != "" then m["kbr_lane"] elsif live_on() then "live" elsif hold_open() then "hold" else "emergency" end
  was = on_air_lane()
  on_air_lane := lane
  if lane == "timed" and timed_pending() > 0 and not list.mem(entry, timed_dropped()) then timed_pending := timed_pending() - 1 end
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
    # The lane stays muted until the next item starts (the branch below brings the level
    # back). It used to come back 0.15 s after the skip whatever was there: when the
    # next item was not loaded yet (a slow backend), the last second of the interrupted
    # song played, then silence (after a live show and after a timed group, 2026-11-18).
    hard_cut := true
    asked = time()
    main.skip()
    # The skip is checked, as Next's is (skip_main). When the timed item had taken over
    # during a song change, the skip only ended cross's transition and the interrupted
    # song played on, muted, to its end: up to minutes of silence, and the backend never
    # heard of a start (harness timed_and_mix_over_levels then duplicate_timed_id,
    # 2026-11-18). Before the lane stayed muted this was the "old song comes back after
    # the time check" fault. Given again up to 3 times; if the old song still will not
    # go, it is let through rather than leaving the stream silent.
    def rec check(n) =
      if main_interrupted() and on_air_lane() == "main" and main_changed_at() < asked then
        if n < 3 then
          # (not over an item that is already being read: see skip_main)
          if main_fetched_at() < asked then
            log.important("back to music: the interrupted song is still there after the skip; skipping again")
            hard_cut := true
            main.skip()
          end
          thread.run(delay=0.5, {check(n + 1)})
        else
          log.important("back to music: the interrupted song would not go; letting it play")
          main_gain := 1.
          main_interrupted := false
        end
      end
    end
    thread.run(delay=0.5, {check(0)})
  elsif lane == "hold" then
    # Not reported: nothing from the queue is on air and it is not the emergency music
    left = timed_group_at() - time()
    if left > 1. and timed_pending() > 0 and timed_queue.is_ready() then
      # More than a moment to fill: the timed item starts now, a few seconds early,
      # as it does on the browser player (a timed item due within 8 s of a song
      # change starts at that change). Jerusalem 00:00Z 2026-11-18: 6.6 s to the
      # hour were filled with the start of a song that was then cut.
      log.important("hold: timed item due in #{left} s starts now, after the song")
      timed_early := timed_group_at()
      # Opening the timed lane is all it takes: nothing is on air to fade or mute.
      # (Not timed_take_over(): one more call into it from here and Liquidsoap spins
      # at start, see wiki/streaming-architecture.md.)
      timed_armed := true
      thread.run(delay=1.5, {timed_armed := false})
    else
      if time() < live_release() or time() < start_quiet_until() then
        log.important("hold: quiet while the next item loads (engine start, Next or end of a live show)")
      elsif handed_loading() then
        log.important("hold: quiet while the next item loads (the item before it was shorter than its download)")
      else
        log.important("hold: quiet until the timed item due in #{left} s")
      end
    end
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
      start_quiet_until := 0.
    end
    report_to_backend(entry, lane, m["title"])
    if lane == "main" and m["kbr_kind"] == "music" then keep_for_emergency(m["filename"], m["kbr_asset"]) end
  end
end


# Fires on every change of what is on air, including returning to a song after a
# timed item. Reported once per change.
last_reported = ref("-")
# The same item seen again within a moment is not a new start. An item came back after a
# blip of another source in between (item, quiet hold, item again in the same instant)
# and was reported twice: two start reports for one sponsorship at Main's 20:30Z break
# (2026-11-18), two play log rows. Only an entry with an id counts: an emergency file
# has none and must still be reported on every change.
last_started_entry = ref("")
last_started_at = ref(0.)
# The entry on air before the current one, and when the current one came on. A crossfade
# can show the outgoing track's tags once more right after the incoming one's: the item
# that just ended would be reported as started again, and the new one a second time.
previous_reported = ref("-")
reported_at = ref(0.)
def report_started(m) =
  entry = m["kbr_entry"]
  # Same lane only: a song coming back after a timed item that was dropped at once is a
  # real return (the interrupted-song handling in send_started must run for it)
  if entry != "" and entry != last_reported() and entry == previous_reported()
     and m["kbr_lane"] == on_air_lane() and time() - reported_at() < 1.5 then
    log.important("started: #{entry} (the item that just ended) showed again for a moment; ignored")
  elsif entry != last_reported() then
    previous_reported := last_reported()
    reported_at := time()
    last_reported := entry
    if entry != "" and entry == last_started_entry() and time() - last_started_at() < 3. then
      # Back on the item: the lane is its lane again, but nothing is reported or counted
      log.important("started: #{entry} came back within #{time() - last_started_at()} s; not a new start")
      on_air_lane := m["kbr_lane"]
    else
      if entry != "" then
        last_started_entry := entry
        last_started_at := time()
      end
      send_started(m)
    end
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
  ask_after := 0.
  if body.on and not live_wanted() then
    log.important("live: show starts; the music plays on until its audio is here")
    live_asked := time()
    live_feed.start()
    # Going quiet at once left the stream silent until the feed had connected and
    # buffered: 2 to 3.5 s of dead air at every take-over (KBR Jerusalem 17:34Z
    # 2026-11-18). So: quiet when the feed is ready, or after 5 s whatever is there
    # (a show with no sound must still not have music under it).
    def rec wait() =
      if live_asked() > 0. then
        # Also when the song on air is about to end: the item loaded meanwhile is the
        # one meant for after the show, and its change (crossfade) would put it on
        # air, to be cut a moment later and counted as aired. A song's end is a fine
        # moment to take over; a second or two of quiet then is the lesser fault.
        left = main.remaining()
        ending = on_air_lane() == "main" and left >= 0. and left < 2.
        if live_feed.is_ready() or ending or time() - live_asked() > 5. then
          log.important("live: the stream is the show's after #{time() - live_asked()} s (audio ready: #{live_feed.is_ready()}, song ending: #{ending})")
          live_asked := 0.
          go_live()
        else
          thread.run(delay=0.05, wait)
        end
      end
    end
    wait()
    # The item that follows the show loads during it: the backend sends /flush right
    # after this (it takes back a track pre-loaded before the show), and /flush asks
    # again under the live lane. (A second ask from here crossed that one with a slow
    # backend: two items loaded, and the backend flushed both at the show's end.)
  elsif not body.on and live_asked() > 0. then
    # Over before the stream ever went quiet for it: the music never stopped
    log.important("live: show ended before it was heard")
    live_asked := 0.
    live_feed.stop()
  elsif not body.on and live_on() then
    log.important("live: show ended")
    live_release := time() + 12.
    live_on := false
    live_feed.stop()
  end
  '{"ok":true}'
end)

# Drop the pre-loaded next track (the queue changed) and ask the backend again
control("/flush", fun (_) -> begin
  flush_count := flush_count() + 1
  ask_after := 0.
  main_requests.set_queue([])
  # The pre-loaded entry will not air after all: forget that it was handed to us
  if given() != [] and list.hd(default="", given()) != last_main_entry() then
    given := list.tl(given())
  end
  # While another lane is on air the music lane stands still and would only ask again
  # when it comes back: with a slow backend that left 4 s of emergency music after a
  # timed group whose shiur had just been paused (harness, 2026-11-18). Ask now, so the
  # item is loaded by then. On air, the lane asks by itself.
  # During a live show too: the item that follows the show loads under the live lane.
  if on_air_lane() != "main" or live_wanted() then
    thread.run(fast=false, {ignore(main_requests.fetch())})
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

# An emergency alert: {"uri": ..., "id": ...}. Takes the air at once over everything.
control("/alert", fun (req) -> begin
  let json.parse (body : {uri : string, id : string?}) = req.body()
  id = null.default(body.id, {""})
  log.important("alert: #{id} arrived, loading")
  alert_queue.push(request.create(body.uri))
  '{"ok":true}'
end)

# Stop: the alert on air ends now and any alert waiting behind it is dropped.
control("/alert_stop", fun (_) -> begin
  log.important("alert: stopped by the backend")
  alert_queue.set_queue([])
  alert_queue.skip()
  alert_line_forced := false
  '{"ok":true}'
end)

# The alert line forced on or off ({"on": bool}): a relay contact or a local script.
control("/alert_line", fun (req) -> begin
  let json.parse (body : {on : bool}) = req.body()
  alert_line_forced := body.on
  state = if body.on then "on" else "off" end
  has_line = if alert_line_device != "" then "true" else "false" end
  log.important("alert line: forced #{state}")
  '{"ok":true,"line":#{has_line}}'
end)

control("/status", fun (_) -> begin
  m = program.last_metadata() ?? []
  # Before the music lane's first track its remaining time is infinite, which JSON
  # cannot carry: the handler failed and the connection closed with no answer (Main's
  # engine while it had no station routed, 2026-11-17). -1 = no track.
  left = main.remaining()
  left = if left >= 0. and left < 1000000. then left else -1. end
  json.stringify({ok=true, entry_id=m["kbr_entry"], title=m["title"],
                  main_remaining=left, timed_waiting=timed_queue.length() + list.length(timed_room()),
                  live=live_on(), instance=engine_instance,
                  alert=alert_on_air(), alert_id=alert_current(), alert_source=alert_source()})
end)

# Optional: also write the output to a WAV file (testing only)
debug_wav = environment.get("KBR_DEBUG_WAV")
if debug_wav != "" then
  output.file(%wav, fallible=true, debug_wav, radio)
end

# ── Sound card out (a local FM box feeding the transmitter) ──────────────────
# Only when KBR_ALSA_DEVICE is set (e.g. "default" or "hw:1,0"); the stream server
# leaves it empty and none of this runs. The card runs on its own clock behind a small
# buffer: the engine's clock stays the computer's, so timed items, Icecast and HLS are
# exactly as without a card. The card is 0.2 to 0.6 s behind the stream (the buffer never
# holds more, so the time check on FM stays within a second).
#
# A card that is missing or unplugged must not take the station down, and Liquidsoap
# 2.2 cannot bring a failed output back inside a running engine (its clock stops for
# good, and a failed start raises an error no handler can catch; both tried
# 2026-11-18). So the engine asks before it uses the card, and lets systemd do the rest:
#   - card there at start: use it. If it then fails, the engine exits (Liquidsoap's own
#     rule for a failed output) and systemd starts it again within seconds;
#   - card not there at start: stream only, look for it every 10 s, and when it is
#     back restart once to use it (the FM signal was silent anyway).
alsa_device = environment.get("KBR_ALSA_DEVICE")
def card_there() =
  process.test(timeout=4., "aplay -q -D #{process.quote(alsa_device)} -t raw -f cd -d 1 /dev/zero")
end
if alsa_device != "" then
  if card_there() then
    log(level=2, "sound card #{alsa_device}: on")
    card = buffer(buffer=0.2, max=0.6, fallible=true, radio)
    output.alsa(id="sound_card", device=alsa_device, bufferize=false, fallible=true, card)
  else
    log(level=2, "sound card #{alsa_device}: not there, stream only; looking for it every 10 s")
    # fast=false: the probe runs aplay for up to 4 s and must never sit in the queue of
    # the quick steps (level back up after Next, a time check taking over)
    thread.run(fast=false, delay=10., every=10., fun () -> begin
      if card_there() then
        log(level=2, "sound card #{alsa_device}: back, restarting to use it")
        shutdown()
      end
    end)
  end
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

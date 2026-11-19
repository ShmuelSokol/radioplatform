"""Static checks of the stream server files (infra/streaming): the settings the engine,
nginx, systemd and Icecast rely on. They do not run Liquidsoap; real-engine behaviour is
checked by infra/streaming/harness. Verifier rows V1-1..28 (section A)."""

import re
import xml.etree.ElementTree as ET
from pathlib import Path


INFRA = Path(__file__).resolve().parents[2] / "infra" / "streaming"


def _read(name: str) -> str:
    return (INFRA / name).read_text()


def _liq() -> str:
    # Drop comments so a setting only mentioned in a comment does not count
    # (a `#` followed by `{` is string interpolation, not a comment)
    return "\n".join(re.sub(r"(^|\s)#(?!\{).*$", "", line) for line in _read("radio.liq").splitlines())


def test_engine_says_hello_before_asking_for_tracks():
    """V1-1: hello is sent (and its failure only logged) before the main lane is built,
    and retried every 5 s until the backend answers 200."""
    liq = _liq()
    hello = liq.index("/playout/#{station_id}/hello")
    assert hello < liq.index("request.dynamic(")
    # The hello call sits inside try ... catch, so a backend that is down does not stop the engine
    assert re.search(r"^\s*try\s*$", liq[:hello], re.M)
    assert not re.search(r"^\s*end\s*$", liq[liq.rindex("try", 0, hello):hello], re.M)
    assert liq.index("catch err do", hello) < liq.index("request.dynamic(")
    assert 'log.important("hello: backend unreachable: #{err}; trying again in 5 s")' in liq
    # Only a 200 counts, and a failed hello is retried (the backend otherwise keeps the
    # timed items it handed the engine before the restart and never sends them again)
    assert "if resp.status_code == 200 then" in liq[hello:]
    first_call = liq.index("send_hello()", liq.index("def send_hello()") + 20)
    assert first_call < liq.index("request.dynamic(")
    assert "thread.run.recurrent(fast=false, delay=5." in liq[first_call:liq.index("request.dynamic(")]


def test_main_lane_request_settings():
    """V1-2/V1-3: one track pre-loaded, retry every 2 s, 120 s to resolve (raised from 30 s in PR 142: whole files download first); `given` holds 5 ids."""
    liq = _liq()
    assert re.search(r"request\.dynamic\(id=\"main\", prefetch=1, retry_delay=2\., timeout=120\.", liq)
    assert "given := list.prefix(5, data.entry_id :: given())" in liq
    assert "?given=#{handed}" in liq


def test_levels_and_output_processing():
    """V1-4/V1-6/V1-7: per-track gain on main and overlay; compressor and limiter; 320 kbps."""
    liq = _liq()
    assert 'amplify(1., override="liq_amplify", main_requests)' in liq
    assert 'amplify(1., override="liq_amplify", overlay_queue)' in liq
    assert 'amplify(1., override="liq_amplify", timed_queue)' in liq
    # Every other amplify must not read liq_amplify (its default): the track's level then
    # replaced the mute and fade gains for the whole track (harness track_level)
    import re
    for call in re.findall(r"amplify\(([^\n]*)\)", liq):
        assert 'override="liq_amplify"' in call or "override=null()" in call, call
    assert "compress(threshold=-14., ratio=3." in liq
    # Look-ahead brickwall at -3 dBFS (limit() is a compressor: true peaks got through)
    assert "ffmpeg.filter.alimiter(graph, limit=0.708" in liq and "level=false" in liq
    assert "radio = true_peak_limit(radio)" in liq
    assert "limit(threshold=" not in liq
    # 320 kbps, 44.1 kHz stereo, best encoder setting; encoded once and copied to both
    # outputs (two encoders cost 14.9% of a core against 8.7% for one, 2026-11-17)
    assert 'codec="libmp3lame", b="320k", compression_level=0, ar=44100, channels=2' in liq
    assert liq.count("ffmpeg.encode.audio(") == 1 and "%mp3(" not in liq
    assert liq.count("mp3_copy") == 3 and 'mp3_copy = %ffmpeg(format="mp3", %audio.copy)' in liq
    assert '("ice-bitrate", "320")' in liq
    assert 'environment.get(default="Kol B\'ramah", "KBR_STATION_NAME")' in liq


def test_crossfade_and_timed_group_constants():
    """V1-8/V1-10: crossfade length from each item (1 s default); timed gate open 1.5 s; groups within 0.5 s are one take-over."""
    liq = _liq()
    assert 'cross(duration=default_overlap, override_duration="liq_cross_duration", transition, main)' in liq
    assert "default_overlap = 1." in liq
    assert "thread.run(delay=1.5, {timed_armed := false})" in liq
    assert "abs(at - timed_group_at()) > 0.5" in liq
    # length() leaves out items already fetched, so the gate also stays open while one
    # is ready (Jerusalem 18:00Z: the shiur after the announcement was not aired)
    assert ('timed_armed() or (on_air_lane() == "timed" and '
            '(timed_queue.length() > 0 or timed_queue.is_ready()))') in liq


def test_control_endpoints_are_local_and_guarded():
    """V1-16/V1-17: telnet off, harbor on 127.0.0.1, empty token refuses everything, 403 JSON."""
    liq = _liq()
    assert "settings.server.telnet := false" in liq
    assert 'settings.harbor.bind_addrs := ["127.0.0.1"]' in liq
    assert 'token != "" and req.headers["x-playout-token"] == token' in liq
    assert "res.status_code(403)" in liq
    for path in ("/skip", "/flush", "/timed", "/overlay", "/status"):
        assert f'control("{path}"' in liq


def test_timed_dedupe_and_start_report_dedupe():
    """V1-13 and backend V1-31: /timed ignores the last 50 ids; a start is reported once per entry."""
    liq = _liq()
    assert "timed_seen := list.prefix(50, id :: timed_seen())" in liq
    assert "if entry != last_reported() then" in liq


def test_emergency_lane_and_debug_wav():
    """V1-18/V1-19: emergency dir watched and randomized, mksafe; WAV only when KBR_DEBUG_WAV set."""
    liq = _liq()
    assert 'playlist(id="emergency", mode="randomize", reload_mode="watch", emergency_dir)' in liq
    assert "mksafe(emergency)" in liq
    assert 'if debug_wav != "" then' in liq


def test_systemd_unit():
    """V1-25: restarts 3 s after a crash, Nice -10, needs icecast2, runs as liquidsoap."""
    unit = _read("kbr-engine@.service")
    for line in ("Restart=always", "RestartSec=3", "Nice=-10", "Requires=icecast2.service",
                 "User=liquidsoap", "EnvironmentFile=/etc/kbr-engine/stations/%i.env"):
        assert line in unit.splitlines()


def test_nginx_stream_site():
    """V1-26: /live-<uuid> unbuffered with CORS *, everything else 404."""
    conf = _read("nginx-stream.conf")
    assert 'location ~ "^/live-[0-9a-f-]{36}$"' in conf
    assert "proxy_buffering off;" in conf
    assert "add_header Access-Control-Allow-Origin *;" in conf
    assert "location / { return 404; }" in conf


def test_icecast_limits():
    """V1-27: 1000 clients, 40 sources, 128 KB burst, bound to localhost."""
    root = ET.fromstring(_read("icecast.xml").split("-->", 1)[1])
    limits = root.find("limits")
    assert limits.findtext("clients") == "1000"
    assert limits.findtext("sources") == "40"
    assert limits.findtext("burst-on-connect") == "1"
    assert limits.findtext("burst-size") == "131072"
    assert root.find("listen-socket").findtext("bind-address") == "127.0.0.1"
    # Placeholders only: setup.sh fills in random passwords on the server
    auth = root.find("authentication")
    assert {auth.findtext(k) for k in ("source-password", "relay-password", "admin-password")} == {
        "SOURCE_PASSWORD", "RELAY_PASSWORD", "ADMIN_PASSWORD"}


def test_setup_script_rules():
    """V1-20/21/23/24: root only, warns off 24.04, keeps secrets on re-run, files 640,
    control proxy POST only with 10 s timeout, removes unlisted engines, TLS only when DNS matches."""
    sh = _read("setup.sh")
    assert '[ "$(id -u)" = 0 ] || { echo "Run as root."; exit 1; }' in sh
    assert '[ "${VERSION_ID}" = "24.04" ] || echo "Warning' in sh
    assert 'TOKEN="$(old KBR_PLAYOUT_TOKEN)"' in sh and 'TOKEN="$(secret)"; TOKEN_IS_NEW=yes' in sh
    # A rerun does not print the token again (its output gets pasted into chats)
    assert sh.count("PLAYOUT_ENGINE_TOKEN=${TOKEN}") == 1
    assert '[ "$TOKEN_IS_NEW" = yes ] || [ "${KBR_SHOW_TOKEN:-}" = 1 ]' in sh
    assert 'SOURCE_PW="${SOURCE_PW:-$(secret)}"' in sh
    assert 'chown root:liquidsoap "$ENV_FILE"; chmod 640 "$ENV_FILE"' in sh
    assert 'chmod 640 "$STATION_DIR/$SID.env"' in sh
    assert "limit_except POST { deny all; }" in sh
    assert "proxy_read_timeout 10;" in sh
    assert 'systemctl disable --now "kbr-engine@$SID"' in sh
    assert 'if [ -n "$MY_IP" ] && [ "$MY_IP" = "$DNS_IP" ]; then' in sh
    assert "HTTPS:   NOT set up yet" in sh


def test_setup_rerun_restarts_only_what_changed():
    """Droplet, 2026-11-17: every rerun restarted Icecast and all engines at once. Two
    Liquidsoap starts on one core left Jerusalem off air about 50 s and up to 7.6 s behind
    ("We must catchup"). A rerun now checks a new script first, leaves unchanged engines
    and Icecast running, and restarts changed engines one after the other."""
    sh = _read("setup.sh")
    # The new script is checked (at low priority, away from live HLS files) before it replaces the old
    assert 'curl -fsSL "$RAW/radio.liq" -o /etc/kbr-engine/radio.liq.new' in sh
    check = sh.index("liquidsoap --check /etc/kbr-engine/radio.liq.new")
    assert "nice -n 19" in sh[check - 60:check]
    assert "export KBR_HLS_DIR= KBR_DEBUG_WAV=" in sh[:check]
    assert check < sh.index("mv /etc/kbr-engine/radio.liq.new /etc/kbr-engine/radio.liq")
    failed = sh[check:sh.index("mv /etc/kbr-engine/radio.liq.new")]
    assert "exit 1" in failed and "restart" not in failed.replace("Nothing was restarted", "")
    # Icecast: its passwords are kept, so an unchanged file does not restart it
    assert 'RELAY_PW="${RELAY_PW:-$(secret)}"' in sh and 'ADMIN_PW="${ADMIN_PW:-$(secret)}"' in sh
    assert "$(secret)/" not in sh  # no fresh random value written into a file on each run
    assert sh.count("systemctl restart icecast2") == 1
    ice = sh.index("systemctl restart icecast2")
    assert '"$ICE_SUM" != "$(cat "$APPLIED/icecast"' in sh[ice - 200:ice]
    # Engines: skipped when unchanged and running; otherwise one at a time, waiting for an answer
    assert sh.count('systemctl restart "kbr-engine@$SID"') == 1
    restart = sh.index('systemctl restart "kbr-engine@$SID"')
    skip = sh.index("unchanged, left running")
    assert skip < restart and "continue" in sh[skip:restart]
    assert 'engine_answers "$EPORT"' in sh[restart:restart + 300]
    for part in ("/etc/kbr-engine/radio.liq", "kbr-engine@.service", '"$ENV_FILE"', '"$STATION_DIR/$SID.env"'):
        assert part in sh[sh.index('ENG_SUM="$(sum_of'):skip]


def test_each_station_has_its_own_emergency_music():
    """Droplet, 2026-11-17: one shared emergency folder held only Jerusalem's songs, so Main
    would have fallen back to them. Each engine gets its own folder, seeded so it is never
    empty; only music from the main lane is ever copied into it."""
    sh = _read("setup.sh")
    assert "KBR_EMERGENCY_DIR=/var/lib/kbr-engine/emergency/${SID}" in sh
    station_env = sh[sh.index('cat > "$STATION_DIR/$SID.env"'):sh.index("SENV\n", sh.index('cat > "$STATION_DIR/$SID.env"') + 40)]
    assert "KBR_EMERGENCY_DIR" in station_env
    assert "-maxdepth 1 -type f -name '*.mp3' -exec cp -n {} \"$EMERG/\"" in sh
    liq = _read("radio.liq")
    assert 'if lane == "main" and m["kbr_kind"] == "music" then keep_for_emergency(m["filename"], m["kbr_asset"]) end' in liq


def test_a_skip_is_checked_and_repeated_once():
    """Harness next_twice, 2026-11-17: a Next within 3 s of a song change only ended cross's
    transition and the song stayed on air. skip_main() skips again when no change followed."""
    liq = _read("radio.liq")
    assert "main_changed_at := time()" in liq[liq.index("def transition(ending, starting) ="):liq.index("main = cross(")]
    body = liq[liq.index("def skip_main() ="):liq.index("def skip_on_air(")]
    assert body.count("main.skip()") == 2 and "main_changed_at() < asked" in body
    # The retry must not run over an item the engine fetched after the skip
    # (Main 2026-11-18 03:46Z: a waiting sponsorship was thrown away).
    assert "main_fetched_at() < asked" in body
    assert "main_requests.on_track" in liq and "main_fetched_at := time()" in liq
    assert "skip_main()" in liq[liq.index("def skip_on_air("):liq.index('control("/skip"') if 'control("/skip"' in liq else len(liq)]


def test_no_secret_in_infra_files():
    """V1-24: nothing that looks like a generated secret (48 hex chars) is committed."""
    for f in INFRA.iterdir():
        if f.is_file():
            assert not re.search(r"\b[0-9a-f]{48}\b", f.read_text()), f.name


def test_setup_reads_every_station():
    """V1-22. Reproduction: 101 active stations, run setup.sh with 'all' -> 100 engines."""
    sh = _read("setup.sh")
    assert "stations?limit=100\"" not in sh


def test_take_over_waits_for_the_timed_file():
    """V1-8: the music is not muted before the timed item's file has arrived, and a slow
    download is given 90 s instead of the 20 s default. The whole group is fetched
    ahead (prefetch=10), and an item still downloading when the one before it ends
    follows once it arrives (harness block_with_slow_shiur / block_with_very_slow_shiur)."""
    liq = _liq()
    assert 'request.queue(id="timed", interactive=false, timeout=90., prefetch=10)' in liq
    rec = liq.index("def rec attempt()")
    body = liq[rec:liq.index("def timed_admit", rec)]
    assert "timed_queue.is_ready()" in body
    assert "thread.run(delay=0.25, attempt)" in body
    assert body.index("is_ready") < body.index("timed_take_over()")
    # counted before the push: the push can wait for the download
    sched = liq[liq.index("def timed_admit"):]
    assert sched.index("timed_pending := timed_pending() + 1") < sched.index("timed_queue.push(r)")
    started = liq[liq.index("def send_started"):]
    assert 'was == "timed" and lane != "timed" and timed_pending() > 0 and not timed_waiting()' in started


def test_the_engine_holds_quiet_before_a_hard_item_instead_of_emergency_music():
    # Jerusalem 23:00Z 2026-11-17: with no song handed out for the last seconds before a
    # hard item, the emergency lane must not fill in. The hold lane sits above it, is
    # open only while a timed item is loaded and due within 8 s, and is not reported.
    liq = (INFRA / "radio.liq").read_text()
    assert "[on_air_quiet, timed, main, hold, emergency]" in liq
    assert "(timed_pending() > 0 and left < 8. and left > -2.)" in liq
    assert 'elsif hold_open() then "hold" else "emergency"' in liq
    assert "engine_proto = 6" in liq


def test_speech_is_never_overlapped_and_the_song_crossfade_comes_from_the_backend():
    """Sponsorships played in a row ran into each other (Shmuel 2026-11-17): the engine
    overlapped every change by a fixed 3 s. Now each item brings its own overlap."""
    liq = _liq()
    assert 'cross(duration=default_overlap, override_duration="liq_cross_duration", transition, main)' in liq
    assert 'float_of_string(default=default_overlap, ending.metadata["kbr_overlap"])' in liq
    assert "sequence([ending.source, starting.source])" in liq
    assert "fade.out(duration=overlap, ending.source)" in liq
    assert "duration=3." not in liq


def test_a_song_ends_before_a_spoken_item_on_the_engine():
    """Shmuel 2026-11-17 chose "Song ends first, then speech": the song fades out over
    its overlap and the spoken item starts after it, decided by the starting item's kind."""
    liq = _liq()
    assert 'kind = starting.metadata["kbr_kind"]' in liq
    assert 'speech_next = kind != "" and kind != "music"' in liq
    assert "sequence([fade.out(duration=overlap, ending.source), starting.source])" in liq
    # song into song still overlaps
    assert "add(normalize=false, [starting.source, fade.out(duration=overlap, ending.source)])" in liq


def test_engine_items_carry_their_overlap():
    from types import SimpleNamespace

    from app.api.v1 import queue as queue_module
    from app.services import playout_engine

    station = "s1"
    queue_module._station_crossfade.pop(station, None)

    def uri(kind, duration=120.0):
        asset = SimpleNamespace(id="a", title="T", artist="", asset_type=kind, duration=duration,
                                file_path="https://cdn.example.com/a.mp3", metadata_extra={})
        entry = SimpleNamespace(id="e", asset=asset, station_id=station, preempt_at=None,
                                timing_mode=None, source="auto", resume_seconds=None)
        return playout_engine.build_uri(entry, "main")

    song = uri("music")
    assert 'kbr_overlap="1.00"' in song and 'liq_cross_duration="1.00"' in song
    spot = uri("spot", 30.0)
    assert 'kbr_overlap="0.00"' in spot and 'liq_cross_duration="0.20"' in spot
    queue_module._station_crossfade[station] = 0.0
    try:
        song = uri("music")
        assert 'kbr_overlap="0.00"' in song and 'liq_cross_duration="0.20"' in song
    finally:
        queue_module._station_crossfade.pop(station, None)


def test_the_stream_is_quiet_while_a_host_is_on_air():
    # Jerusalem 23:06Z 2026-11-17: the song on the engine played on under the host. The
    # live lane sits above every other lane, is switched by /live, is not reported as a
    # start, and the engine says with each ask whether it is on (a show that ended while
    # the backend restarted).
    liq = (INFRA / "radio.liq").read_text()
    assert 'on_air_quiet = switch(id="host_gate", track_sensitive=false, [' in liq
    assert '({live_on()}, fallback(id="host_lane", track_sensitive=false, [live_feed, blank(id="host_on_air")]))' in liq
    assert 'control("/live"' in liq
    assert 'elsif live_on() then "live"' in liq
    assert "&live=#{if live_wanted() then 1 else 0 end}" in liq
    assert "live_release := time() + 12." in liq



def test_status_answers_before_the_first_track_and_setup_tries_a_new_script_for_real():
    # Main's engine, started with no station routed, closed every /status call with no
    # answer: the music lane's remaining time is infinite before its first track and
    # JSON cannot carry that. And a script that passes `--check` can still hang at
    # start, so setup.sh starts a new script once on a spare port before any restart.
    liq = (INFRA / "radio.liq").read_text()
    assert "left = if left >= 0. and left < 1000000. then left else -1. end" in liq
    assert "main_remaining=left" in liq
    setup = (INFRA / "setup.sh").read_text()
    trial = setup.index("TRIAL_PORT=8990")
    assert setup.index("liquidsoap --check /etc/kbr-engine/radio.liq.new") < trial
    assert trial < setup.index("mv /etc/kbr-engine/radio.liq.new /etc/kbr-engine/radio.liq")
    assert "The new engine script does not start" in setup


def test_the_limiter_is_not_wrapped_in_mksafe_and_a_long_gap_starts_the_timed_item_early():
    # mksafe() around the FFmpeg limiter made the engine spin at start depending on the
    # number of definitions in the script (one unused ref was enough). And a song that
    # ends more than 3 s before a hard item is followed by that item at once (Jerusalem
    # 00:00Z 2026-11-18: 6.6 s were filled with the start of a song that was then cut).
    liq = (INFRA / "radio.liq").read_text()
    assert "radio = true_peak_limit(radio)" in liq
    assert "mksafe(true_peak_limit" not in liq
    assert "output.file(%wav, fallible=true, debug_wav, radio)" in liq
    assert "if left > 1. and timed_pending() > 0 and timed_queue.is_ready() then" in liq
    assert "{if timed_early() != at then timed_go_on_air(180, at) end}" in liq



def test_sound_card_output_is_off_unless_named_and_asks_before_using_the_card():
    """Local FM box: the sound card output exists only with KBR_ALSA_DEVICE (the stream
    server sets none), sits behind a buffer on its own clock, and is used only after the
    card answered a probe; a missing card means stream only and one restart when it is
    back (wiki/local-fm-box.md)."""
    liq = _liq()
    assert 'alsa_device = environment.get("KBR_ALSA_DEVICE")' in liq
    start = liq.index('if alsa_device != "" then')
    block = liq[start:liq.index("encoded = ffmpeg.encode.audio(")]
    assert liq.count("output.alsa(") == 1 and "output.alsa(" in block
    assert "if card_there() then" in block
    assert "card = buffer(buffer=0.2, max=0.6, fallible=true, radio)" in block
    assert 'bufferize=false, fallible=true, card)' in block
    assert "shutdown()" in block and "thread.run(fast=false, delay=10., every=10.," in block
    # No engine setting file of the stream server names a card
    for name in ("kbr-engine.env.example", "setup.sh"):
        assert "KBR_ALSA_DEVICE" not in (INFRA / name).read_text()


def test_quick_steps_have_task_queues_the_backend_calls_cannot_fill():
    """Jerusalem 2026-11-18: with the backend answering in 3 to 4 s, the engine's start
    report and its ask for the next item filled Liquidsoap's two task queues and the
    "level back up" step after Next waited behind them: up to 3.8 s of digital silence
    (harness slow_backend_no_gap). The quick steps need queues of their own, and every
    call to the backend made from a task must be marked slow so it stays off them."""
    liq = _liq()
    fast = int(re.search(r"settings\.scheduler\.fast_queues\s*:=\s*(\d+)", liq).group(1))
    generic = int(re.search(r"settings\.scheduler\.generic_queues\s*:=\s*(\d+)", liq).group(1))
    assert fast >= 4 and generic >= 4
    assert "thread.run(fast=false, post)" in liq
    assert "thread.run.recurrent(fast=false, delay=5., fun () -> begin\n    send_hello()" in liq


def test_next_waits_for_the_next_file_instead_of_skipping_into_nothing():
    """Jerusalem 05:55:39Z 2026-11-18: Next into a shiur whose file needed 3 s put a
    second of emergency music on air. The item on air plays on until the music lane
    has its next file (at most skip_wait), and a second press meanwhile is not a second
    skip."""
    liq = _liq()
    skip = liq[liq.index("def skip_on_air("):liq.index("def go_live(")]
    assert "main_loaded()" in skip and "time() - asked > skip_wait" in skip
    assert "skip_waiting" in skip
    assert re.search(r"skip_wait\s*=\s*(\d+)\.", liq) and float(re.search(r"skip_wait\s*=\s*(\d+)\.", liq).group(1)) >= 10
    assert "list.length(main_requests.queue()) > 0" in liq


def test_the_music_lane_stays_muted_until_the_next_item_starts_and_loads_it_ahead():
    """With a slow backend: after a live show the last second of the interrupted song
    played, then 3 s of silence; after a timed group that had paused a shiur, 4 s of
    emergency music (harness with every backend answer held 4 s, 2026-11-18). The level
    came back on a timer after the skip, and the lane only asked for its next item when
    it was back on air."""
    liq = _liq()
    back = liq[liq.index("elsif lane == \"main\" and main_interrupted() and entry == last_main_entry() then"):]
    back = back[:back.index("elsif lane == \"hold\"")]
    first = back[:back.index("def rec check")]
    assert "main.skip()" in first and "main_gain := 1." not in first and "main_interrupted := false" not in first
    flush = liq[liq.index('control("/flush"'):]
    flush = flush[:flush.index("end)")]
    assert 'on_air_lane() != "main" or live_wanted()' in flush
    assert "thread.run(fast=false, {ignore(main_requests.fetch())})" in flush


def test_timed_items_loaded_ahead_wait_outside_the_lane_until_their_own_time():
    """The timed lane's queue knows no times: it plays what is next. A time check loaded
    45 s ahead followed a Play now item at once, early (V3-52l), and of two groups loaded
    before the first started, the first lost its take-over and aired at the second's
    time (harness, 2026-11-18). Items due later are downloaded and kept outside the lane
    until shortly before their time, earlier than the hold lane looks for them."""
    liq = _liq()
    sched = liq[liq.index("def schedule_timed(~uri, ~at) ="):]
    sched = sched[:sched.index("\nend\n")]
    assert "request.resolve(" in sched
    assert "timed_room := list.append(timed_room(), [x])" in sched
    assert "thread.run(delay=at - time() - timed_room_lead, {timed_room_admit(at)})" in sched
    # the group's entry does not wait for a slow download: the item is counted then and
    # pushed when it arrives (harness block_with_very_slow_shiur)
    assert sched.index("timed_room_admit(at)") < sched.index("request.resolve(")
    assert "if x.admitted() then timed_room_push(r) end" in sched
    room = liq[liq.index("def timed_room_admit(at) ="):]
    room = room[:room.index("\nend\n")]
    assert "timed_admit(x.r, x.at, push=false)" in room and "if x.done() then" in room
    # due now (Play now) or its group already in the lane: straight in, as before
    assert "timed_admit(r, at)" in sched
    assert "timed_queue.push" not in sched
    lead = float(liq.split("timed_room_lead = ")[1].split("\n")[0])
    hold = liq[liq.index("def hold_open() ="):]
    hold = hold[:hold.index("\nend\n")]
    assert "left < 8." in hold and lead > 8.
    # one take-over per group, set when the group enters the lane
    admit = liq[liq.index("def timed_admit(r, at, ~push=true) ="):]
    admit = admit[:admit.index("\nend\n")]
    assert "timed_group_at := at" in admit and "timed_go_on_air(180, at)" in admit
    assert admit.index("timed_pending := timed_pending() + 1") < admit.index("if push then timed_queue.push(r) end")


def test_the_skip_back_to_music_is_checked_and_never_leaves_the_lane_muted():
    """A timed item that took over during a song change: on the way back the skip only
    ended cross's transition, the interrupted song played on muted to its end (silence
    for the rest of the song) and no start was reported (harness, 2026-11-18)."""
    liq = _liq()
    back = liq[liq.index("elsif lane == \"main\" and main_interrupted() and entry == last_main_entry() then"):]
    back = back[:back.index("elsif lane == \"hold\"")]
    assert back.count("main.skip()") == 2
    assert 'main_interrupted() and on_air_lane() == "main" and main_changed_at() < asked' in back
    # not over an item already being read; and in the end sound rather than silence
    assert "if main_fetched_at() < asked then" in back
    give_up = back[back.index("would not go"):]
    assert "main_gain := 1." in give_up and "main_interrupted := false" in give_up


def test_the_engine_starts_quiet_until_its_first_item_instead_of_an_emergency_song():
    """Every engine restart opened with about 2 s of an emergency song before the queue's
    first item (08:23Z rerun, 2026-11-18). With the backend answering hello the hold lane
    keeps the stream quiet until the first item is loaded (at most 8 s); with no backend
    the emergency music starts at once."""
    liq = _liq()
    assert "start_quiet_until = ref(if hello_done() then time() + 8. else 0. end)" in liq
    assert liq.index("send_hello()\n") < liq.index("start_quiet_until = ref(")
    hold = liq[liq.index("def hold_open() ="):]
    hold = hold[:hold.index("\nend\n")]
    assert "time() < start_quiet_until()" in hold
    started = liq[liq.index("def send_started"):]
    first_main = started[started.index('if lane == "main" then\n      last_main_entry := entry'):]
    assert "start_quiet_until := 0." in first_main[:200]


def test_a_skip_holds_quiet_until_the_next_item_has_audio_instead_of_emergency_music():
    """Main 13:27:42Z 2026-11-18: Next by hand onto an item "prepared" with nothing decoded
    yet put 1.7 s of the emergency lane on air. Every skip of the music lane opens the
    hold lane for up to 6 s; the next start from the music lane closes it."""
    liq = _liq()
    skip = liq[liq.index("def skip_main() ="):]
    skip = skip[:skip.index("main.skip()")]
    assert "start_quiet_until := time() + 6." in skip


def test_the_item_after_a_live_show_loads_during_the_show():
    """About 5 s of quiet after a show on Main: the music lane only asked for its next item
    once the show was over. /flush now asks under the live lane too (proto 4), and the
    backend quiets the engine before it flushes, so the flush loads that item."""
    liq = _liq()
    assert "engine_proto = 6" in liq
    flush = liq[liq.index('control("/flush"'):]
    flush = flush[:flush.index("end)")]
    assert 'on_air_lane() != "main" or live_wanted()' in flush
    live = liq[liq.index('control("/live"'):]
    live = live[:live.index("end)")]
    assert "main_requests.fetch()" not in live   # one ask only: the flush's
    svc = (Path(__file__).resolve().parents[1] / "app/services/live_show_service.py").read_text()
    take = svc[svc.index("async def take_over_station"):]
    assert take.index("engine_live(station.id, True)") < take.index("engine_flush(station.id)")
    release = svc[svc.index("async def release_station"):]
    assert "engine_live(station.id, False)" in release[:release.index("return True")]


def test_the_live_lane_plays_the_show_from_live_mp3_with_the_token_in_a_header():
    """Engine proto 5: the host's show (after the broadcast delay) is on the stream. The
    feed opens on live on, closes on live off, and the lane is quiet without it."""
    liq = _liq()
    assert "engine_proto = 6" in liq
    feed = liq[liq.index("live_feed = input.http("):]
    feed = feed[:feed.index("\n)\n")]
    assert "start=false" in feed
    assert '"#{backend_url}/playout/#{station_id}/live.mp3"' in feed
    assert 'X-Playout-Token: #{token}' in feed and "token=" not in feed.split("live.mp3")[1]
    lane = liq[liq.index("on_air_quiet = switch("):]
    lane = lane[:lane.index("\n])\n")]
    assert "[live_feed, blank(" in lane
    live = liq[liq.index('control("/live"'):]
    live = live[:live.index("end)")]
    on, early, off = live.split("elsif not body.on")
    assert "live_feed.start()" in on and "live_feed.stop()" in off and "live_feed.stop()" in early
    # The stream goes quiet when the show's audio is here, not at the command: going
    # quiet at once was 2 to 3.5 s of dead air at every take-over (2026-11-18)
    assert on.index("live_feed.start()") < on.index("live_feed.is_ready()") < on.index("go_live()")
    assert "time() - live_asked() > 5." in on           # never music under a show with no sound
    # A song ending in the wait takes over at once: the item loaded behind it is the
    # one for after the show and must not start (engine thread's review of PR 347)
    assert 'ending = on_air_lane() == "main" and left >= 0. and left < 2.' in on
    assert "live_feed.is_ready() or ending or" in on
    assert "live_release" not in early                  # the music never stopped: nothing to hold
    assert "&live=#{if live_wanted() then 1 else 0 end}" in liq
    assert "live=live_on(), instance=engine_instance" in liq   # /status says what is on the stream now


def test_every_engine_call_carries_its_own_instance_id_for_the_one_engine_rule():
    """One engine per station: each engine run sends a random id with every call and in
    /status, so the backend can refuse a second engine with the same station id."""
    liq = _liq()
    assert 'engine_instance = "#{random.int(' in liq
    assert '("X-Playout-Instance", engine_instance)' in liq
    assert "instance=engine_instance" in liq  # in /status
    # Every backend call uses the shared headers
    assert liq.count("headers=auth_headers") >= 3
    assert "status_code == 409" in liq


def test_the_same_item_back_within_a_moment_is_not_reported_as_a_new_start():
    """Two start reports for one sponsorship at Main's 20:30Z break (2026-11-18) wrote two
    play log rows. The engine reports an item seen again within 3 s (after a blip of
    another source) only once, and an emergency file (no entry id) on every change."""
    liq = _liq()
    assert "last_started_entry" in liq
    assert 'entry != "" and entry == last_started_entry() and time() - last_started_at() < 3.' in liq
    assert "not a new start" in liq
    # A crossfade showing the outgoing item's tags once more is not a start either (same
    # lane, within 1.5 s); a song coming back after a dropped timed item still counts
    assert 'entry == previous_reported()' in liq
    assert 'm["kbr_lane"] == on_air_lane() and time() - reported_at() < 1.5' in liq

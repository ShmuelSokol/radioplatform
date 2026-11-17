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
    assert "thread.run.recurrent(delay=5." in liq[first_call:liq.index("request.dynamic(")]


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
    assert "compress(threshold=-14., ratio=3." in liq
    assert "limit(threshold=-1., radio)" in liq
    assert "%mp3(bitrate=320, samplerate=44100, stereo=true" in liq
    assert 'environment.get(default="Kol B\'ramah", "KBR_STATION_NAME")' in liq


def test_crossfade_and_timed_group_constants():
    """V1-8/V1-10: 3 s crossfade; timed gate open 1.5 s; groups within 0.5 s are one take-over."""
    liq = _liq()
    assert "cross(duration=3., transition, main)" in liq
    assert "thread.run(delay=1.5, {timed_armed := false})" in liq
    assert "abs(at - timed_group_at()) > 0.5" in liq
    assert 'timed_armed() or (on_air_lane() == "timed" and timed_queue.length() > 0)' in liq


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
    assert 'TOKEN="$(old KBR_PLAYOUT_TOKEN)";' in sh and 'TOKEN="${TOKEN:-$(secret)}"' in sh
    assert 'SOURCE_PW="${SOURCE_PW:-$(secret)}"' in sh
    assert 'chown root:liquidsoap "$ENV_FILE"; chmod 640 "$ENV_FILE"' in sh
    assert 'chmod 640 "$STATION_DIR/$SID.env"' in sh
    assert "limit_except POST { deny all; }" in sh
    assert "proxy_read_timeout 10;" in sh
    assert 'systemctl disable --now "kbr-engine@$SID"' in sh
    assert 'if [ -n "$MY_IP" ] && [ "$MY_IP" = "$DNS_IP" ]; then' in sh
    assert "HTTPS:   NOT set up yet" in sh


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
    download is given 90 s instead of the 20 s default."""
    liq = _liq()
    assert 'request.queue(id="timed", interactive=false, timeout=90.)' in liq
    rec = liq.index("def rec go_on_air()")
    body = liq[rec:liq.index("timed_queue.push(r)", rec)]
    assert "timed_queue.is_ready()" in body
    assert "thread.run(delay=0.25, go_on_air)" in body
    assert body.index("is_ready") < body.index("take_over()")

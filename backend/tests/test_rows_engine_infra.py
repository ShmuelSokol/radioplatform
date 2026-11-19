"""Checklist rows on the playout engine's own files (infra/streaming): the settings and
rules each row names, read from radio.liq, nginx-stream.conf and setup.sh, plus what the
backend does when a listed station has no engine behind it. Real-engine behaviour of the
same rows is checked by infra/streaming/harness (scenario engine_rows)."""

import re
from pathlib import Path

import httpx
import pytest

from app.config import settings
from app.services import playout_engine

INFRA = Path(__file__).resolve().parents[2] / "infra" / "streaming"


def _read(name: str) -> str:
    return (INFRA / name).read_text()


def _liq() -> str:
    # Comments dropped, so a setting only mentioned in a comment does not count
    return "\n".join(re.sub(r"(^|\s)#(?!\{).*$", "", line) for line in _read("radio.liq").splitlines())


def _block(text: str, start: str) -> str:
    """From `start` to the first line that closes it at the same indent (`end` / `end)`)."""
    i = text.index(start)
    indent = len(text[:i]) - len(text[:i].rstrip(" "))
    m = re.compile(rf"^ {{{indent}}}end\)?\s*$", re.M).search(text, i)
    return text[i:m.end()]


def _asks(liq: str) -> str:
    """The backend ask (ask_backend) and the lane's callback around it (next_request)."""
    return _block(liq, "def rec ask_backend() =") + _block(liq, "def next_request() =")


def test_given_ids_and_flush_hand_back_the_preloaded_entry():
    """V1-2: /next carries the last 5 handed ids, newest first; /flush forgets the
    pre-loaded id (the head of `given`) unless it is the one that already aired."""
    liq = _liq()
    nxt = _asks(liq)
    assert 'handed = string.concat(separator=",", given())' in nxt
    assert "/next?given=#{handed}" in nxt
    # newest first, at most five
    assert "given := list.prefix(5, data.entry_id :: given())" in nxt
    flush = _block(liq, 'control("/flush", fun (_) -> begin')
    assert "main_requests.set_queue([])" in flush
    assert 'if given() != [] and list.hd(default="", given()) != last_main_entry() then' in flush
    assert "given := list.tl(given())" in flush
    # last_main_entry is what the music lane put on air (set when a main item starts)
    assert "last_main_entry := entry" in _block(liq, "def send_started(m) =")


def test_main_lane_retries_every_2_s_gives_a_download_120_s_and_preloads_one():
    """V1-3 / F-ENGINE2-12: an empty or failed answer yields no request (null), so the lane
    asks again after retry_delay; 120 s per track; exactly one pre-loaded."""
    liq = _liq()
    assert re.search(r'request\.dynamic\(id="main", prefetch=1, retry_delay=2\., timeout=120\., next_request\)', liq)
    nxt = _asks(liq)
    # anything but 200 -> no track, and an unreachable backend too; either waits 1.5 to
    # 2 s before the next ask (the lane itself asks every frame while a track plays)
    assert "if resp.status_code == 200 then" in nxt
    assert "ask_after := time() + 1.5" in nxt and "catch err do" in nxt
    assert "if time() < ask_after() or following() then" in nxt


def test_start_report_dedupe():
    """V1-13: a start is reported only when kbr_entry differs from the last one; the same
    entry twice is reported once, and so is "" (an emergency file) after "" ."""
    liq = _liq()
    rep = _block(liq, "def report_started(m) =")
    assert 'last_reported = ref("-")' in liq
    assert "elsif entry != last_reported() then" in rep
    assert "last_reported := entry" in rep
    # Only reaches send_started (and the backend) inside that branch
    assert rep.count("send_started(m)") == 1
    assert rep.index("send_started(m)") > rep.index("elsif entry != last_reported() then")


def test_start_report_failure_is_only_logged():
    """V1-14: the POST runs in its own thread inside try/catch: a backend that is down is
    logged, nothing retried; the next /next carries `given` so the backend reconciles."""
    liq = _liq()
    rep = _block(liq, "def report_to_backend(entry, lane, title) =")
    assert "try" in rep and 'log.important("started: backend unreachable: #{err}")' in rep
    assert "thread.run(fast=false, post)" in rep
    assert "?given=#{handed}" in _asks(liq)


def test_control_port_token_post_only_and_local():
    """V1-16 / V1-17: an empty token refuses everything, a wrong one gets 403 JSON; nginx
    passes POST only and gives the engine 10 s; harbor listens on 127.0.0.1, telnet off,
    and the firewall opens only 22, 80 and 443."""
    liq = _liq()
    assert 'token != "" and req.headers["x-playout-token"] == token' in liq
    ctl = _block(liq, "def control(path, handler) =")
    assert "res.status_code(403)" in ctl and "res.data('{\"ok\":false,\"error\":\"forbidden\"}')" in ctl
    assert 'res.content_type("application/json")' in ctl
    assert 'harbor.http.register(port=control_port, method="POST", path, h)' in ctl
    assert "settings.server.telnet := false" in liq
    assert 'settings.harbor.bind_addrs := ["127.0.0.1"]' in liq
    setup = _read("setup.sh")
    loc = setup[setup.index("location /control/${SID}/ {"):]
    loc = loc[:loc.index("\n}\n") + 2]
    assert "limit_except POST { deny all; }" in loc
    assert "proxy_pass http://127.0.0.1:${PORT}/;" in loc
    assert "proxy_read_timeout 10;" in loc
    ufw = re.findall(r"ufw allow (\S+)", setup)
    assert sorted(ufw) == ["22/tcp", "443/tcp", "80/tcp"]


def test_emergency_lane_is_watched_randomized_and_never_fails():
    """V1-18: files added while running are picked up, played in random order, and an
    empty folder plays silence (mksafe) rather than stopping the engine."""
    liq = _liq()
    assert 'emergency = playlist(id="emergency", mode="randomize", reload_mode="watch", emergency_dir)' in liq
    assert "emergency = mksafe(emergency)" in liq
    assert re.search(r"radio = fallback\(id=\"radio\", track_sensitive=false, \[[^\]]*emergency\]\)", liq)


def test_every_track_change_is_reported_and_the_emergency_folder_fills_itself():
    """F-ENGINE2-10 / F-ENGINE2-11: on_track reports untagged emergency files too; each
    music-lane song is copied in under .part then renamed, the newest 40 kept; spots,
    jingles, shiurim and timed items are not copied."""
    liq = _liq()
    assert "program.on_metadata(report_started)" in liq
    assert "program.on_track(report_started)" in liq
    keep = _block(liq, "def keep_for_emergency(played, asset) =")
    assert "emergency_keep = 40" in liq
    assert 'part = dest ^ ".part"' in keep
    assert re.search(r"cp -f .*part.* && mv -f .*part.*dest", keep)
    assert "tail -n +#{emergency_keep + 1}" in keep
    started = _block(liq, "def send_started(m) =")
    assert 'if lane == "main" and m["kbr_kind"] == "music" then keep_for_emergency(m["filename"], m["kbr_asset"]) end' in started
    assert liq.count("keep_for_emergency(") == 2  # the definition and that one call


def test_nginx_stream_hls_and_404():
    """V1-26 / F-ENGINE2-19: /live-<uuid> unbuffered with CORS *; /hls/<uuid>/ playlists and
    segments with their types, no caching and CORS *; anything else 404."""
    conf = _read("nginx-stream.conf")
    live = conf[conf.index('location ~ "^/live-[0-9a-f-]{36}$"'):]
    live = live[:live.index("\n    }") + 6]
    for line in ("proxy_pass http://127.0.0.1:8000;", "proxy_buffering off;",
                 "add_header Access-Control-Allow-Origin *;"):
        assert line in live
    m3u8 = conf[conf.index(r'location ~ "^/hls/([0-9a-f-]{36})/([a-z0-9_]+\.m3u8)$"'):]
    mp3 = conf[conf.index(r'location ~ "^/hls/([0-9a-f-]{36})/([a-z0-9_]+\.mp3)$"'):]
    for block, mime in ((m3u8, "application/vnd.apple.mpegurl m3u8;"), (mp3, "audio/mpeg mp3;")):
        assert "alias /var/lib/kbr-engine/hls/$1/$2;" in block[:400]
        assert f"types {{ {mime} }}" in block[:400]
        assert 'add_header Cache-Control "no-cache, no-store" always;' in block[:400]
        assert "add_header Access-Control-Allow-Origin * always;" in block[:400]
    assert "location / { return 404; }" in conf
    assert "include /etc/nginx/kbr-control.inc;" in conf


def test_setup_writes_hls_dir_and_the_engine_skips_hls_without_it():
    """F-ENGINE2-20: setup.sh writes KBR_HLS_DIR per station and makes its folder owned by
    liquidsoap; with KBR_HLS_DIR empty radio.liq writes no HLS. The HLS playlist the
    site asks for (mp3.m3u8) is the stream named "mp3"."""
    setup = _read("setup.sh")
    assert "KBR_HLS_DIR=/var/lib/kbr-engine/hls/${SID}" in setup
    assert ('mkdir -p "/var/lib/kbr-engine/hls/${SID}"; chown liquidsoap:liquidsoap '
            '"/var/lib/kbr-engine/hls/${SID}"') in setup
    liq = _liq()
    assert 'hls_dir = environment.get("KBR_HLS_DIR")' in liq
    hls = _block(liq, 'if hls_dir != "" then')
    assert "output.file.hls(" in hls and '[("mp3", mp3_copy)]' in hls
    assert "segment_duration=1.0" in hls


def test_engines_exist_only_for_stations_setup_was_given():
    """V1-28: setup.sh makes an engine, mount and control route only for the stations it
    reads; one no longer listed is stopped and its env removed. Anything else under
    /control/ falls to nginx's 404."""
    setup = _read("setup.sh")
    loop = setup[setup.index('while IFS=\'|\' read -r SID SNAME; do'):setup.index('done <<< "$STATION_LINES"')]
    assert 'cat > "$STATION_DIR/$SID.env"' in loop and 'cat >> "$CONTROL_INC"' in loop
    assert 'systemctl disable --now "kbr-engine@$SID"' in setup
    assert "location / { return 404; }" in _read("nginx-stream.conf")


@pytest.mark.asyncio
async def test_a_listed_station_with_no_engine_fails_its_commands_quietly(monkeypatch):
    """V1-28: PLAYOUT_ENGINE_STATION_ID names a station setup.sh never made an engine for:
    nginx answers 404. Every command reports failure (False / None) and nothing raises,
    so the scheduler's watchdog and flush carry on."""
    real = httpx.AsyncClient
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(404, text="<html>404 Not Found</html>")

    class NoEngine(real):
        def __init__(self, **kw):
            super().__init__(transport=httpx.MockTransport(handler))

    await playout_engine.close_client()
    monkeypatch.setattr(httpx, "AsyncClient", NoEngine)
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_URL", "https://stream.example.com/control")
    station = __import__("uuid").uuid4()
    try:
        assert await playout_engine._command("/flush", station_id=station) is False
        assert await playout_engine.engine_flush(station) is False
        assert await playout_engine.engine_skip(station, 0.5) is False
        assert playout_engine.get_state(station).skip_requested is False
        assert await playout_engine.engine_status(station) is None
        assert await playout_engine.engine_timed(station, "https://x/a.mp3", 0.0, "e1") is False
    finally:
        await playout_engine.close_client()
    assert paths and all(p.startswith(f"/control/{station}/") for p in paths)

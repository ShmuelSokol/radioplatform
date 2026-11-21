"""The two watchdogs for a stalled engine output (Main 2026-11-20 22:16Z: off air a day).

- radio.liq's own: no audio frame for 30 s, the engine exits and systemd restarts it.
  Checked with a small Liquidsoap script whose clock is blocked (needs liquidsoap).
- kbr-engine-watch.sh, from outside: an engine whose Icecast mount is missing or whose
  HLS playlist stopped changing for 60 s is restarted (systemctl faked here).
"""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parents[2] / "infra" / "streaming"
SID = "11111111-2222-3333-4444-555555555555"


@pytest.mark.skipif(shutil.which("liquidsoap") is None, reason="needs liquidsoap")
def test_the_engine_exits_when_its_clock_is_stuck():
    t0 = time.time()
    cmd = ["liquidsoap", str(INFRA / "harness" / "stall_watchdog_check.liq")]
    if os.geteuid() == 0:  # Liquidsoap refuses to run as root
        if shutil.which("runuser") is None:
            pytest.skip("running as root without runuser")
        cmd = ["runuser", "-u", "nobody", "--"] + cmd
    r = subprocess.run(cmd,
                       capture_output=True, text=True, timeout=60)
    assert "watchdog: no audio" in r.stdout + r.stderr
    assert time.time() - t0 < 40


def _setup(tmp_path, mount_listed: bool, hls_age: float, active_for: float):
    stations = tmp_path / "stations"
    stations.mkdir()
    (stations / f"{SID}.env").write_text("KBR_STATION_ID=x\n")
    hls = tmp_path / "hls" / SID
    hls.mkdir(parents=True)
    playlist = hls / "live.m3u8"
    playlist.write_text("#EXTM3U\n")
    t = time.time() - hls_age
    os.utime(playlist, (t, t))
    status = tmp_path / "status.json"
    url = f"http://h:8000/live-{SID}" if mount_listed else "http://h:8000/live-other"
    status.write_text(json.dumps({"icestats": {"source": {"listenurl": url}}}))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    started = time.strftime("%a %Y-%m-%d %H:%M:%S UTC", time.gmtime(time.time() - active_for))
    (bin_dir / "systemctl").write_text(f"""#!/bin/bash
echo "$@" >> {tmp_path}/systemctl.log
case "$1" in
  is-enabled|is-active) exit 0;;
  show) echo "{started}";;
esac
""")
    (bin_dir / "curl").write_text(f"#!/bin/bash\ncat {status}\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", TZ="UTC",
               KBR_WATCH_STATE=str(tmp_path / "state"), KBR_WATCH_STATIONS=str(stations),
               KBR_WATCH_HLS=str(tmp_path / "hls"), KBR_WATCH_STALL="0")
    return env


def _run(tmp_path, env):
    r = subprocess.run(["bash", str(INFRA / "kbr-engine-watch.sh")], env=env, capture_output=True,
                       text=True, timeout=30)
    log = tmp_path / "systemctl.log"
    return r.stdout, (log.read_text() if log.exists() else "")


def test_a_missing_mount_restarts_the_engine(tmp_path):
    env = _setup(tmp_path, mount_listed=False, hls_age=1, active_for=600)
    out, calls = _run(tmp_path, env)
    assert f"restart kbr-engine@{SID}" in calls and "mount" in out


def test_a_stale_hls_playlist_restarts_the_engine(tmp_path):
    env = _setup(tmp_path, mount_listed=True, hls_age=90, active_for=600)
    out, calls = _run(tmp_path, env)
    assert f"restart kbr-engine@{SID}" in calls and "HLS" in out


def test_a_healthy_or_just_started_engine_is_left_alone(tmp_path):
    env = _setup(tmp_path, mount_listed=True, hls_age=1, active_for=600)
    _, calls = _run(tmp_path, env)
    assert "restart" not in calls
    young = tmp_path / "young"
    young.mkdir()
    env = _setup(young, mount_listed=False, hls_age=90, active_for=30)
    _, calls = _run(young, env)
    assert "restart" not in calls


def test_a_stall_is_restarted_only_after_it_lasts(tmp_path):
    env = _setup(tmp_path, mount_listed=False, hls_age=1, active_for=600)
    env["KBR_WATCH_STALL"] = "60"
    out, calls = _run(tmp_path, env)
    assert "restart" not in calls and "mount" in out

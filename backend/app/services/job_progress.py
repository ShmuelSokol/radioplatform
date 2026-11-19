"""How far a long audio edit has got, for the progress bar on the item page.

Shmuel 2026-11-19: "can it show a progress bar?" AI Enhance and Remove All Found
Silence run FFmpeg over the whole recording, which takes a while on a 35 minute shiur,
and the page showed only a spinner.

The page makes up a progress id, sends it with the edit (`?progress_id=`), and asks
`GET /assets/progress/{id}` every second while it waits. The edit reports here as it
goes: FFmpeg's own `-progress` output gives the time it has reached, against the
recording's length. Kept in memory: there is one backend process, and a lost entry only
means the bar shows no progress.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
import time
from typing import Callable

from app.config import settings

PROGRESS_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
KEEP_SECONDS = 3600
FFMPEG_TIMEOUT = 1800  # a long shiur through noise reduction takes minutes

_jobs: dict[str, dict] = {}
_lock = threading.Lock()

Report = Callable[[float, str | None], None]


def valid_id(progress_id: str | None) -> bool:
    return bool(progress_id) and bool(PROGRESS_ID.match(progress_id))


def _prune(now: float) -> None:
    for key in [k for k, v in _jobs.items() if now - v["updated"] > KEEP_SECONDS]:
        _jobs.pop(key, None)


def report(progress_id: str | None, fraction: float, stage: str | None = None) -> None:
    """Record how far a job is (0..1) and what it is doing. Never goes backwards."""
    if not valid_id(progress_id):
        return
    now = time.time()
    with _lock:
        _prune(now)
        job = _jobs.setdefault(progress_id, {"fraction": 0.0, "stage": None, "done": False, "error": None, "updated": now})
        job["fraction"] = max(job["fraction"], min(1.0, max(0.0, fraction)))
        if stage:
            job["stage"] = stage
        job["updated"] = now


def finish(progress_id: str | None, error: str | None = None) -> None:
    if not valid_id(progress_id):
        return
    with _lock:
        job = _jobs.setdefault(progress_id, {"fraction": 0.0, "stage": None, "updated": time.time()})
        job.update(done=True, error=error, updated=time.time())
        if not error:
            job["fraction"] = 1.0
            job["stage"] = "Done"


def get(progress_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(progress_id)
        return dict(job) if job else None


def reporter(progress_id: str | None, start: float = 0.0, end: float = 1.0) -> Report:
    """A report function for one stage, mapping its own 0..1 into start..end of the bar."""
    def _report(fraction: float, stage: str | None = None) -> None:
        report(progress_id, start + (end - start) * fraction, stage)
    return _report


def scaled(parent: Report | None, start: float, end: float) -> Report | None:
    if parent is None:
        return None
    return lambda fraction, stage=None: parent(start + (end - start) * fraction, stage)


def run_ffmpeg(args: list[str], total_seconds: float, progress: Report | None = None,
               stage: str | None = None) -> tuple[int, str]:
    """Run FFmpeg with `args` (inputs, filters and output; inputs must be files, not a
    pipe) and report how far it is. Returns (exit code, its stderr)."""
    cmd = [settings.FFMPEG_PATH, "-hide_banner", "-nostats", "-progress", "pipe:1", *args]
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, stdin=subprocess.DEVNULL)
        timer = threading.Timer(FFMPEG_TIMEOUT, proc.kill)
        timer.start()
        try:
            if progress:
                progress(0.0, stage)
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if progress and total_seconds > 0 and line.startswith("out_time_us="):
                    try:
                        progress(min(1.0, int(line.split("=", 1)[1]) / 1e6 / total_seconds), stage)
                    except ValueError:
                        pass
            code = proc.wait()
        finally:
            timer.cancel()
        err.seek(0)
        stderr = err.read().decode("utf-8", "replace")
    if progress and code == 0:
        progress(1.0, stage)
    return code, stderr


def temp_path(suffix: str = ".mp3") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return path

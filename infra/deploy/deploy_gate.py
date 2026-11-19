#!/usr/bin/env python3
"""Holds a backend deploy while a live show is on air, and off the clock times when a
restart is heard most (the time checks at :00 and :30).

The last job of CI on a push to main. Railway is set to "Wait for CI", so the backend
restarts only when this job has finished. A restart during a show takes the host off
the air for as long as the backend is down (about 20 s of quiet, 2026-11-18): the
host's sound passes through the backend. See wiki/deploys.md.

Passes at once when: the commit message has [deploy-now], the API does not answer
(the deploy may be the fix), or the wait has reached its limit.
"""
import json
import os
import sys
import time
import urllib.request

API = os.environ.get("KBR_API", "https://api.kbrlive.com/api/v1")
LIMIT = float(os.environ.get("GATE_LIMIT_MINUTES", "240")) * 60
EVERY = float(os.environ.get("GATE_EVERY_SECONDS", "30"))
# Minutes of the hour with no restart: around the hard items at :00 and :30
QUIET = [(55, 60), (0, 5), (27, 33)]


def get(path: str):
    with urllib.request.urlopen(f"{API}{path}", timeout=10) as r:
        return json.load(r)


def live_shows() -> list[str] | None:
    """Names of the stations with a live show now; None when the API does not answer."""
    try:
        stations = get("/stations").get("stations", [])
        return [s.get("name", s["id"]) for s in stations
                if get(f"/live-shows/station/{s['id']}/active").get("active")]
    except Exception as e:  # noqa: BLE001
        print(f"The API does not answer ({e}).")
        return None


def in_quiet_minutes(now: float | None = None) -> bool:
    minute = time.gmtime(now if now is not None else time.time()).tm_min
    return any(a <= minute < b for a, b in QUIET)


def reason_to_wait(now: float | None = None, shows=live_shows) -> str | None:
    on = shows()
    if on is None:
        return None
    if on:
        return "a live show is on air on " + ", ".join(on)
    if in_quiet_minutes(now):
        return "a restart now would be heard at the time check"
    return None


def main() -> int:
    if "[deploy-now]" in os.environ.get("COMMIT_MESSAGE", ""):
        print("[deploy-now] in the commit message: not waiting.")
        return 0
    start = time.time()
    while True:
        why = reason_to_wait()
        if why is None:
            print("Nothing in the way: the backend may deploy.")
            return 0
        if time.time() - start > LIMIT:
            print(f"Waited {LIMIT / 60:.0f} minutes ({why}): deploying anyway.")
            return 0
        print(f"Waiting: {why}.", flush=True)
        time.sleep(EVERY)


if __name__ == "__main__":
    sys.exit(main())

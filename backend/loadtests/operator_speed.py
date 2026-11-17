"""How fast do the control room's calls answer on a running backend? (checklist S-3, S-4)

Times the same calls as tests/test_speed_operator_actions.py against a real server:
reads (queue, play log, last played, now-playing, live-audio, library search) and,
when asked, operator actions. Prints min / median / p95 / max round trip per call and
exits non-zero when a median is over the limit (default 1 s round trip, the S-3 limit
from the US East Coast; server time is under 300 ms when the round trip is).

Safe by default: only reads. Actions change the station, so they need flags:
  --edits     insert --asset after the item on air, move it down and up, reorder it,
              then remove it again (the item on air is not touched; nothing airs)
  --on-air    also skip (hard/fade/soft), fire-now --asset, loop on/off, mix-over
              --asset: THESE CHANGE WHAT IS ON AIR. Use a test station.

Credentials come from the environment, never the command line:
    cd backend
    KBR_ADMIN_EMAIL=... KBR_ADMIN_PASSWORD=... uv run python loadtests/operator_speed.py \
        --base https://api.kbrlive.com --station <id> [--asset <id>] [--edits] [--on-air] [--runs 10]
"""
import argparse
import asyncio
import os
import statistics
import sys
import time

import httpx

API = "/api/v1"


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, max(0, round(p / 100 * (len(values) - 1))))]


class Timer:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.times: dict[str, list[float]] = {}
        self.errors: dict[str, list[str]] = {}

    async def call(self, name, method, url, **kw):
        started = time.perf_counter()
        try:
            res = await self.client.request(method, url, **kw)
        except httpx.HTTPError as exc:
            self.errors.setdefault(name, []).append(type(exc).__name__)
            return None
        took = time.perf_counter() - started
        if res.status_code >= 400:
            self.errors.setdefault(name, []).append(f"{res.status_code} {res.text[:80]}")
            return res
        self.times.setdefault(name, []).append(took)
        return res


async def login(client, email, password):
    res = await client.post(f"{API}/auth/login", json={"email": email, "password": password})
    res.raise_for_status()
    return res.json()["access_token"]


async def reads(t: Timer, station, runs):
    for _ in range(runs):
        await t.call("GET queue", "GET", f"{API}/stations/{station}/queue")
        await t.call("GET queue/log", "GET", f"{API}/stations/{station}/queue/log")
        await t.call("GET queue/last-played", "GET", f"{API}/stations/{station}/queue/last-played")
        await t.call("GET now-playing", "GET", f"{API}/now-playing/{station}")
        await t.call("GET live-audio", "GET", f"{API}/stations/{station}/live-audio")
        await t.call("GET assets?search", "GET", f"{API}/assets", params={"search": "a", "limit": 50})


async def edits(t: Timer, station, asset, runs):
    base = f"{API}/stations/{station}/queue"
    for _ in range(runs):
        res = await t.call("POST insert", "POST", f"{base}/insert", json={"asset_id": asset})
        if res is None or res.status_code >= 400:
            continue
        entry = res.json()["ids"][0]
        await t.call("POST move-down", "POST", f"{base}/move-down", json={"entry_id": entry, "new_position": 0})
        await t.call("POST move-up", "POST", f"{base}/move-up", json={"entry_id": entry, "new_position": 0})
        await t.call("POST reorder-dnd", "POST", f"{base}/reorder-dnd", json={"entry_id": entry, "new_position": 3})
        await t.call("DELETE entry", "DELETE", f"{base}/{entry}")


async def on_air(t: Timer, station, asset, runs):
    base = f"{API}/stations/{station}/queue"
    for i in range(runs):
        for fade in ("hard", "fade", "soft"):
            await t.call(f"POST skip?fade={fade}", "POST", f"{base}/skip", params={"fade": fade})
            await asyncio.sleep(1.0)
        if asset:
            await t.call("POST fire-now", "POST", f"{base}/fire-now",
                         json={"asset_id": asset, "client_request_id": f"speed-{time.time()}-{i}"})
            await t.call("POST mix-over", "POST", f"{base}/mix-over", json={"asset_id": asset})
        await t.call("POST loop on", "POST", f"{base}/loop", json={"on": True})
        await t.call("POST loop off", "POST", f"{base}/loop", json={"on": False})
        await asyncio.sleep(1.0)


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="backend base URL, e.g. http://localhost:8000")
    ap.add_argument("--station", required=True)
    ap.add_argument("--asset", help="library item for insert / fire-now / mix-over")
    ap.add_argument("--edits", action="store_true", help="time insert/move/reorder/remove (nothing airs)")
    ap.add_argument("--on-air", action="store_true", help="time skip/fire-now/loop/mix-over (changes the air)")
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--limit-ms", type=float, default=1000.0, help="median round-trip limit per call")
    args = ap.parse_args()

    email, password = os.environ.get("KBR_ADMIN_EMAIL"), os.environ.get("KBR_ADMIN_PASSWORD")
    if not email or not password:
        sys.exit("Set KBR_ADMIN_EMAIL and KBR_ADMIN_PASSWORD in the environment")
    if (args.edits or args.on_air) and not args.asset:
        sys.exit("--edits and --on-air need --asset")

    async with httpx.AsyncClient(base_url=args.base.rstrip("/"), timeout=20.0,
                                 headers={"User-Agent": "kbr-operator-speed"}) as client:
        client.headers["Authorization"] = f"Bearer {await login(client, email, password)}"
        t = Timer(client)
        # One warm-up read so connection setup is not counted
        await client.get(f"{API}/stations/{args.station}/queue")
        await reads(t, args.station, args.runs)
        if args.edits:
            await edits(t, args.station, args.asset, args.runs)
        if args.on_air:
            await on_air(t, args.station, args.asset, max(1, args.runs // 3))

    failed = False
    print(f"{'call':28} {'n':>3} {'min':>7} {'median':>7} {'p95':>7} {'max':>7}   (ms, round trip)")
    for name, values in t.times.items():
        med = statistics.median(values) * 1000
        flag = "  OVER" if med > args.limit_ms else ""
        failed |= bool(flag)
        print(f"{name:28} {len(values):>3} {min(values) * 1000:>7.0f} {med:>7.0f} "
              f"{pct(values, 95) * 1000:>7.0f} {max(values) * 1000:>7.0f}{flag}")
    for name, errs in t.errors.items():
        failed = True
        print(f"ERROR {name}: {len(errs)} x {errs[0]}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())

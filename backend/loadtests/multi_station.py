"""Several stations at once, many listeners each: does everyone hear the same thing?

Opens LISTENERS now-playing WebSockets per station (what the Listen page uses),
a few REST pollers per station (the fallback path), and one operator per station
polling the queue every 3 s (an open control room). Then it reports, per station:

- every song change seen, how many listeners got it, and the spread between the
  first and last listener to get it ("does everyone switch together");
- lag from the song's recorded start to the listener getting the message;
- listeners that missed a change or saw a different song;
and request latency and errors for the REST calls.

Usage (no secrets in the command line history: put them in the environment):
    cd backend
    KBR_ADMIN_EMAIL=admin KBR_ADMIN_PASSWORD=... \
      uv run python loadtests/multi_station.py --base http://localhost:8000 \
      --listeners 100 --seconds 120 [--station ID ...]

With no --station it uses every active station. Exits non-zero when a check fails.
"""
import argparse
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime

import httpx
import websockets

API = "/api/v1"


def pct(values, p):
    if not values:
        return None
    values = sorted(values)
    k = min(len(values) - 1, max(0, round(p / 100 * (len(values) - 1))))
    return values[k]


def fmt(x, unit="ms", scale=1000):
    return "-" if x is None else f"{x * scale:.0f}{unit}"


class Stats:
    def __init__(self):
        self.latency = defaultdict(list)  # endpoint -> seconds
        self.errors = defaultdict(int)  # endpoint -> count
        self.ws_failed = 0
        self.ws_dropped = 0
        # station -> listener index -> list of (asset_id, started_at, recv_time)
        self.seen = defaultdict(lambda: defaultdict(list))


async def timed_get(client, stats, name, url, **kw):
    t0 = time.monotonic()
    try:
        try:
            r = await client.get(url, **kw)
        except (httpx.ReadError, httpx.RemoteProtocolError):
            # The server closed an idle keep-alive connection just as it was reused;
            # browsers retry these on their own, so do the same once.
            r = await client.get(url, **kw)
        stats.latency[name].append(time.monotonic() - t0)
        if r.status_code >= 400:
            stats.errors[f"{name} {r.status_code}"] += 1
        return r
    except Exception as e:
        stats.errors[f"{name} {type(e).__name__}"] += 1
        return None


async def listener(ws_base, station, idx, stats, stop):
    url = f"{ws_base}{API}/ws/now-playing/{station}"
    try:
        async with websockets.connect(url, open_timeout=20, ping_interval=None) as ws:
            while not stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                msg = json.loads(raw)
                data = msg.get("data") if msg.get("type") == "now_playing" else None
                if data and data.get("asset_id"):
                    stats.seen[station][idx].append((data["asset_id"], data.get("started_at"), time.time()))
    except websockets.ConnectionClosed:
        if not stop.is_set():
            stats.ws_dropped += 1
    except Exception:
        stats.ws_failed += 1


async def poller(client, station, stats, stop):
    while not stop.is_set():
        await timed_get(client, stats, "GET now-playing", f"{API}/now-playing/{station}")
        await asyncio.sleep(3)  # the listener page's fallback polls every 3 s


async def operator(client, station, headers, stats, stop):
    while not stop.is_set():
        await timed_get(client, stats, "GET queue", f"{API}/stations/{station}/queue", headers=headers)
        await asyncio.sleep(3)


def started_epoch(started_at):
    if not started_at:
        return None
    try:
        return datetime.fromisoformat(started_at.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def report(stats, stations, names, listeners):
    failures = []
    print("\n== Song changes per station ==")
    for station in stations:
        per_listener = stats.seen[station]
        # A "change" is one (asset_id, started_at) pair. The first message on connect
        # is the song already playing, not a change.
        changes = defaultdict(list)  # key -> first recv time per listener
        repeats = 0
        for idx, events in per_listener.items():
            first = {}
            for asset_id, started_at, recv in events:
                key = (asset_id, started_at)
                if key in first:
                    repeats += 1  # the same song announced again
                    continue
                first[key] = recv
            for key in list(first)[1:]:
                changes[key].append(first[key])
        connected = len(per_listener)
        print(f"\n{names.get(station, station)}: {connected}/{listeners} listeners connected, {len(changes)} changes")
        spreads, lags = [], []
        for (asset_id, started_at), recvs in sorted(changes.items(), key=lambda kv: min(kv[1])):
            spread = max(recvs) - min(recvs)
            spreads.append(spread)
            start = started_epoch(started_at)
            lag = (max(recvs) - start) if start else None
            if lag is not None:
                lags.append(lag)
            got = len(recvs)
            mark = "" if got >= connected else f"  MISSED BY {connected - got}"
            print(f"  {time.strftime('%H:%M:%S', time.localtime(min(recvs)))}  {asset_id[:8]}  "
                  f"{got}/{connected} got it, spread {fmt(spread)}, last listener {fmt(lag)} after start{mark}")
            if got < connected:
                failures.append(f"{names.get(station, station)}: change {asset_id[:8]} missed by {connected - got}")
        # Same sequence for everyone (ignoring the song that was playing when each connected)
        def order(events):
            out = []
            for asset_id, started_at, _ in events:
                if not out or out[-1] != (asset_id, started_at):
                    if (asset_id, started_at) not in out:
                        out.append((asset_id, started_at))
            return tuple(out[1:])

        seqs = {order(ev) for ev in per_listener.values()}
        if len(seqs) > 1:
            failures.append(f"{names.get(station, station)}: listeners saw {len(seqs)} different song orders")
        if spreads:
            print(f"  spread p50 {fmt(pct(spreads, 50))}, max {fmt(max(spreads))}; "
                  f"lag p95 {fmt(pct(lags, 95))}; {repeats} repeated announcements of a song already playing")
            if max(spreads) > 2.0:
                failures.append(f"{names.get(station, station)}: listeners switched up to {max(spreads):.1f}s apart")
        if connected < listeners:
            failures.append(f"{names.get(station, station)}: only {connected}/{listeners} listeners connected")

    print("\n== REST ==")
    for name, values in sorted(stats.latency.items()):
        print(f"  {name}: {len(values)} calls, p50 {fmt(pct(values, 50))}, p95 {fmt(pct(values, 95))}, "
              f"p99 {fmt(pct(values, 99))}, max {fmt(max(values))}")
        if pct(values, 95) > 1.0:
            failures.append(f"{name}: p95 {pct(values, 95):.2f}s")
    calls = sum(len(v) for v in stats.latency.values()) or 1
    for name, n in sorted(stats.errors.items()):
        print(f"  ERROR {name}: {n}")
    errors = sum(stats.errors.values())
    if errors > calls * 0.005:  # an odd dropped keep-alive connection is not a failure
        failures.append(f"{errors} request errors in {calls} calls")
    print(f"  WebSocket: {stats.ws_failed} failed to connect, {stats.ws_dropped} dropped")
    if stats.ws_failed or stats.ws_dropped:
        failures.append(f"WebSocket: {stats.ws_failed} failed, {stats.ws_dropped} dropped")

    print("\n== Result ==")
    if failures:
        for f in failures:
            print("  FAIL", f)
    else:
        print("  PASS")
    return not failures


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--station", action="append", default=[])
    ap.add_argument("--listeners", type=int, default=50, help="WebSocket listeners per station")
    ap.add_argument("--pollers", type=int, default=5, help="REST now-playing pollers per station")
    ap.add_argument("--seconds", type=int, default=90)
    args = ap.parse_args()

    ws_base = args.base.replace("http", "ws", 1)
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=200)
    async with httpx.AsyncClient(base_url=args.base, timeout=20, limits=limits) as client:
        r = await client.get(f"{API}/stations")
        all_stations = r.json()["stations"]
        names = {s["id"]: s["name"] for s in all_stations}
        stations = args.station or [s["id"] for s in all_stations if s.get("is_active", True)]
        headers = {}
        email, password = os.environ.get("KBR_ADMIN_EMAIL"), os.environ.get("KBR_ADMIN_PASSWORD")
        if email and password:
            r = await client.post(f"{API}/auth/login", json={"email": email, "password": password})
            r.raise_for_status()
            headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

        stats, stop = Stats(), asyncio.Event()
        tasks = []
        for station in stations:
            for i in range(args.listeners):
                tasks.append(asyncio.create_task(listener(ws_base, station, i, stats, stop)))
            for _ in range(args.pollers):
                tasks.append(asyncio.create_task(poller(client, station, stats, stop)))
            if headers:
                tasks.append(asyncio.create_task(operator(client, station, headers, stats, stop)))
        print(f"{len(stations)} stations, {args.listeners} listeners + {args.pollers} pollers each"
              f"{' + 1 operator' if headers else ''}, {args.seconds}s")
        await asyncio.sleep(args.seconds)
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        ok = report(stats, stations, names, args.listeners)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())

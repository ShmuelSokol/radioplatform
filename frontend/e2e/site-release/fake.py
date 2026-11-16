"""Fake kbrlive.com for the site-release browser check: static builds + tiny API."""
import asyncio, io, json, math, os, struct, sys, wave
from datetime import datetime, timezone
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import FileResponse, JSONResponse, Response
import uvicorn

ROOT = sys.argv[1]  # holds dist-v1, dist-v2
app = FastAPI()
S = {"published": "v1", "policy": {"mode": "auto", "push": None}, "np": None, "tabs": {}}
site_socks, np_socks = set(), set()

def now(): return datetime.now(timezone.utc).isoformat()

def tone(freq, secs=60):
    buf = io.BytesIO(); w = wave.open(buf, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
    w.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * freq * i / 8000))) for i in range(8000 * secs)))
    w.close(); return buf.getvalue()
TONES = {"a": tone(440), "b": tone(660), "c": tone(880)}

def item(aid, nxt):
    return {"station_id": "s1", "asset_id": aid, "started_at": now(),
            "asset": {"title": f"Song {aid}", "audio_url": f"/audio/{aid}.wav", "cue_in": 0, "cue_out": 60, "cross_start": 57, "replay_gain_db": 0},
            "next_asset": {"id": nxt, "audio_url": f"/audio/{nxt}.wav"}}
S["np"] = item("a", "b")

async def bcast(socks, msg):
    for ws in list(socks):
        try: await ws.send_text(json.dumps(msg))
        except Exception: socks.discard(ws)

def policy(): return {**S["policy"], "server_time": now()}

@app.post("/__ctl/publish")
async def publish(req: Request): S["published"] = (await req.json())["version"]; return {}
@app.post("/__ctl/policy")
async def set_policy(req: Request):
    S["policy"] = await req.json(); await bcast(site_socks, {"type": "release", "data": policy()}); return {}
@app.post("/__ctl/item")
async def set_item(req: Request):
    b = await req.json(); S["np"] = item(b["id"], b["next"]); await bcast(np_socks, {"type": "now_playing", "data": S["np"]}); return {}
@app.get("/__ctl/tabs")
async def tabs(): return list(S["tabs"].values())

@app.get("/version.json")
async def version(): return {"version": S["published"]}
@app.get("/audio/{name}")
async def audio(name: str): return Response(TONES[name.split(".")[0]], media_type="audio/wav", headers={"Accept-Ranges": "bytes"})
@app.get("/api/v1/site-release")
async def get_policy(): return policy()
@app.get("/api/v1/stations/{sid}")
async def station(sid: str): return {"id": sid, "name": "Test Station", "type": "internet", "timezone": "America/New_York", "description": None, "is_active": True}
@app.get("/api/v1/now-playing/{sid}")
async def np(sid: str): return S["np"]

@app.websocket("/api/v1/ws/site")
async def ws_site(ws: WebSocket):
    await ws.accept(); site_socks.add(ws)
    await ws.send_text(json.dumps({"type": "release", "data": policy()}))
    try:
        while True:
            m = json.loads(await ws.receive_text())
            if m.get("type") == "tab": S["tabs"][m["data"]["client_id"]] = {**m["data"], "at": now()}
    except Exception: pass
    finally: site_socks.discard(ws)

@app.websocket("/api/v1/ws/now-playing/{sid}")
async def ws_np(ws: WebSocket, sid: str):
    await ws.accept(); np_socks.add(ws)
    await ws.send_text(json.dumps({"type": "now_playing", "data": S["np"]}))
    try:
        while True: await ws.receive_text()
    except Exception: pass
    finally: np_socks.discard(ws)

@app.get("/api/v1/auth/me")
async def me(): return {"id": "u1", "email": "admin", "role": "admin", "is_active": True, "display_name": "Admin", "station_ids": None}
@app.get("/api/v1/site-release/tabs")
async def tabs_api(): return {"tabs": [{**t, "connected_seconds": 60} for t in S["tabs"].values()]}
@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def other(rest: str): return JSONResponse({"detail": "not here"}, status_code=404)

@app.get("/{path:path}")
async def static(path: str):
    d = os.path.join(ROOT, f"dist-{S['published']}")
    f = os.path.join(d, path)
    if path and os.path.isfile(f): return FileResponse(f, headers={"Cache-Control": "no-store"})
    return FileResponse(os.path.join(d, "index.html"), headers={"Cache-Control": "no-store"})

uvicorn.run(app, host="127.0.0.1", port=4310, log_level="warning")

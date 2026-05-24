# Streaming Architecture

## Current state (2026-05-24)

Liquidsoap + Icecast run on a **dedicated VPS** (not Railway). Railway backend stays lean: API + scheduler only.

## How it works

- **Backend → Liquidsoap**: TCP telnet on port 1234 (was Unix socket, changed 2026-05-24)
- **Liquidsoap → Icecast**: localhost:8000, MP3 192kbps, `/live` mount
- **Icecast → Listeners**: Direct `<audio>` element (was HLS/hls.js, changed 2026-05-24)
- **Fallback**: If VPS is down, frontend auto-falls back to client-side Web Audio dual-buffer engine

## Config (backend)

- `LIQUIDSOAP_HOST` — VPS hostname (empty = disabled)
- `LIQUIDSOAP_TELNET_PORT` — default 1234
- `LIQUIDSOAP_ENABLED` — must be true AND host must be set
- `ICECAST_STREAM_URL` — public URL (e.g., `https://stream.kbrlive.com/live`)

## VPS config files

All in `infra/streaming/`: icecast.xml, radio.liq, liquidsoap.service, nginx-stream.conf, setup.sh

## VPS not provisioned yet

As of 2026-05-24, the VPS is not set up. `LIQUIDSOAP_ENABLED=false` (default). All listeners use client-side Web Audio engine. Plan: Hetzner CX22 in Ashburn (~$4/mo).

## Why not Railway sidecar?

Tried it — OCaml runtime + audio processing consumed too much CPU/memory on shared container. Caused severe performance degradation.

## Key constraint: listener sync

Client-side engine = each listener plays independently (no sync). Icecast = all listeners hear the same stream. For "all listeners hearing the same audio at the same time," the VPS is required.

See also: [audio-engine](audio-engine.md), [scheduler](scheduler.md)

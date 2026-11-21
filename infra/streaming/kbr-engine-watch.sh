#!/bin/bash
# Outside watchdog for the playout engines (run every 20 s by kbr-engine-watch.timer).
#
# Liquidsoap 2.2 cannot bring a failed output back: its clock stops for good. Main
# 2026-11-20 22:16Z: under overload its Icecast connection broke, the engine went to
# 0.5% CPU, its mount disappeared and stayed gone for a day. The engine has its own
# watchdog (radio.liq "Stall watchdog"); this one checks from outside, for when the
# engine is too stuck even for that: an engine whose Icecast mount is missing, or whose
# HLS playlist has not changed, for STALL seconds is restarted. Not within GRACE seconds
# of its start (it takes ~20 s to come up). Nothing it does can make a sound by itself:
# a restarted engine plays the queue, and in a blackout knows the windows from disk.
set -u
STALL="${KBR_WATCH_STALL:-60}"
GRACE="${KBR_WATCH_GRACE:-120}"
STATE="${KBR_WATCH_STATE:-/run/kbr-engine-watch}"
STATIONS="${KBR_WATCH_STATIONS:-/etc/kbr-engine/stations}"
HLS_ROOT="${KBR_WATCH_HLS:-/var/lib/kbr-engine/hls}"
ICECAST_STATUS="${KBR_WATCH_ICECAST:-http://127.0.0.1:8000/status-json.xsl}"
mkdir -p "$STATE"
STATUS="$(curl -s -m 5 "$ICECAST_STATUS" || true)"
now="$(date +%s)"
for f in "$STATIONS"/*.env; do
  [ -e "$f" ] || continue
  SID="$(basename "$f" .env)"
  systemctl is-enabled --quiet "kbr-engine@$SID" || continue
  if ! systemctl is-active --quiet "kbr-engine@$SID"; then
    rm -f "$STATE/$SID"; continue   # systemd's own restart deals with it
  fi
  started="$(systemctl show -p ActiveEnterTimestamp --value "kbr-engine@$SID")"
  started="$(date -d "$started" +%s 2>/dev/null || echo "$now")"
  if [ $((now - started)) -lt "$GRACE" ]; then rm -f "$STATE/$SID"; continue; fi
  ok=yes
  MOUNT="/live-$SID"
  if [ -n "$STATUS" ] && ! printf '%s' "$STATUS" | grep -q "\"listenurl\":\"[^\"]*${MOUNT}\""; then
    ok=no; why="mount $MOUNT missing"
  fi
  HLS="$HLS_ROOT/$SID/live.m3u8"
  if [ -f "$HLS" ] && [ $((now - $(stat -c %Y "$HLS"))) -gt 30 ]; then
    ok=no; why="HLS playlist not updated for $((now - $(stat -c %Y "$HLS"))) s"
  fi
  if [ "$ok" = yes ]; then rm -f "$STATE/$SID"; continue; fi
  [ -f "$STATE/$SID" ] || echo "$now" > "$STATE/$SID"
  since="$(cat "$STATE/$SID")"
  if [ $((now - since)) -ge "$STALL" ]; then
    echo "kbr-engine-watch: engine $SID stalled ($why for $((now - since)) s); restarting it"
    rm -f "$STATE/$SID"
    systemctl restart "kbr-engine@$SID"
  else
    echo "kbr-engine-watch: engine $SID: $why ($((now - since)) s)"
  fi
done

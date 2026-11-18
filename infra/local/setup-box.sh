#!/bin/bash
# Sets up a local FM box: the small computer beside the transmitter that plays one
# station out of its sound card and keeps playing with no internet (wiki/local-fm-box.md).
# Fresh Ubuntu 24.04. Safe to run again: it keeps its settings, restarts only what
# changed, and puts the old engine script back if the new one does not come up.
#
#   curl -fsSL https://raw.githubusercontent.com/ShmuelSokol/radioplatform/main/infra/local/setup-box.sh \
#     | sudo bash -s -- <station id> [sound card] [backend-url]
#
# sound card: as `aplay -L` names it (e.g. "default", "plughw:CARD=Device,DEV=0").
#   Left out on a first run = no sound card output yet; on a later run = keep what was set.
# The shared engine secret (the backend's PLAYOUT_ENGINE_TOKEN) is asked for on the first
# run, or taken from KBR_PLAYOUT_TOKEN. It is kept on this machine only; never write it
# in the repository.
#
# Nothing has to reach this box from outside: it fetches its plan, its audio and the
# site's commands itself. The backend must list the station in PLAYOUT_ENGINE_STATION_ID
# and PLAYOUT_RELAY_STATION_ID (the script says so at the end).
#
# Updates: the box looks for a new release by itself every hour at :35 and takes it only
# in a quiet time (inside a Shabbos / Yom Tov blackout, or between 3 and 4 AM station
# time), because a new engine script means about 10 s of silence on FM. The engine is
# restarted between two songs. Run by hand, the script sets everything up at once
# except a restart of an engine that is on air, which waits for the quiet time;
# KBR_APPLY=now does it at once. KBR_BOX_AUTO_UPDATE=0 switches the hourly look off,
# KBR_SETUP_REF=<branch or tag> pins the release it follows (both are kept).
#
# After a power cut the box starts by itself if its BIOS is set to "power on after AC
# loss" (set that once by hand), and it needs a working clock battery: with no internet
# and a clock in the past it plays music only, no time checks, until the clock is set.
set -euo pipefail

STATION="${1:-}"
ALSA_ARG="${2-__keep__}"
BACKEND_URL="${3:-}"
REF="${KBR_SETUP_REF:-}"
[ -n "$REF" ] || REF="$( [ -f /etc/kbr-engine/box.env ] && grep -E '^KBR_BOX_REF=' /etc/kbr-engine/box.env | head -1 | cut -d= -f2- || true )"
REF="${REF:-main}"
RAW="https://raw.githubusercontent.com/ShmuelSokol/radioplatform/${REF}/infra"
# KBR_SETUP_FROM=<checkout>/infra takes the files from a local copy instead (tests)
FROM="${KBR_SETUP_FROM:-}"
ETC=/etc/kbr-engine
ENV_FILE=$ETC/env
RELAY_ENV=$ETC/relay.env
BOX_ENV=$ETC/box.env   # what the hourly update needs: station, release, time zone
APPLY="${KBR_APPLY:-quiet}"          # now = restart an engine that is on air at once
BY_TIMER="${KBR_SETUP_TIMER:-}"      # 1 = started by the hourly update
LIB=/var/lib/kbr-engine
RELAY_DIR=/var/lib/kbr-local
OPT=/opt/kbr-engine
CONTROL_PORT=8081
RELAY_PORT=8090

[ "$(id -u)" = 0 ] || { echo "Run as root (sudo)."; exit 1; }
case "$STATION" in
  ""|*[!0-9a-fA-F-]*) echo "Usage: setup-box.sh <station id> [sound card] [backend-url]"; exit 1;;
esac
. /etc/os-release
[ "${VERSION_ID}" = "24.04" ] || echo "Warning: written for Ubuntu 24.04 (found ${VERSION_ID}); Liquidsoap 2.2 is needed."

# ── Quiet time ───────────────────────────────────────────────────────────────
relay_says() {  # relay_says <field>: true / false / empty when the relay does not answer
  curl -s -m 4 "http://127.0.0.1:${RELAY_PORT}/relay/status" 2>/dev/null \
    | python3 -c 'import json,sys; print(str(json.load(sys.stdin).get(sys.argv[1])).lower())' "$1" 2>/dev/null || true
}
quiet_now() {
  # Inside a blackout the station is silent anyway
  [ "$(relay_says in_blackout)" = true ] && return 0
  # Else between 3:05 and 3:50 at the station (never across the hour's time check)
  local tz hm
  tz="$(old KBR_BOX_TZ "$BOX_ENV")"
  hm="$(TZ="${tz:-America/New_York}" date +%H%M)"
  [ "${KBR_FAKE_HHMM:-$hm}" -ge 305 ] && [ "${KBR_FAKE_HHMM:-$hm}" -le 350 ]
}
old() { [ -f "$2" ] && grep -E "^$1=" "$2" | head -1 | cut -d= -f2- || true; }
if [ "$BY_TIMER" = 1 ] && [ "$APPLY" != now ] && ! quiet_now; then
  echo "Not a quiet time (no blackout, not 3 AM at the station): nothing looked at."
  exit 0
fi

fetch() {  # fetch <path under infra/> <destination>
  if [ -n "$FROM" ]; then cp "$FROM/$1" "$2"; else curl -fsSL "$RAW/$1" -o "$2"; fi
}
secret() { openssl rand -hex 24; }

export DEBIAN_FRONTEND=noninteractive
if [ "${KBR_SETUP_SKIP_APT:-}" != 1 ] && [ "$BY_TIMER" != 1 ]; then
  apt-get update -q
  apt-get install -y -q liquidsoap icecast2 alsa-utils python3 curl openssl
fi
# The engine's user may use the sound card
usermod -aG audio liquidsoap

mkdir -p "$ETC/stations" "$LIB/emergency/$STATION" "$RELAY_DIR" "$OPT"
chown -R liquidsoap:liquidsoap "$LIB" "$RELAY_DIR"

# ── Settings: kept from an earlier run where there are any ───────────────────
STATION_ENV="$ETC/stations/$STATION.env"
TOKEN="${KBR_PLAYOUT_TOKEN:-$(old KBR_PLAYOUT_TOKEN "$ENV_FILE")}"
if [ -z "$TOKEN" ]; then
  read -rsp "Engine secret (the backend's PLAYOUT_ENGINE_TOKEN): " TOKEN </dev/tty; echo
fi
[ -n "$TOKEN" ] || { echo "No engine secret given."; exit 1; }
BACKEND_URL="${BACKEND_URL:-$(old KBR_UPSTREAM_URL "$RELAY_ENV")}"
BACKEND_URL="${BACKEND_URL:-https://api.kbrlive.com/api/v1}"
if [ "$ALSA_ARG" = "__keep__" ]; then ALSA="$(old KBR_ALSA_DEVICE "$STATION_ENV")"; else ALSA="$ALSA_ARG"; fi
SOURCE_PW="$(old KBR_ICECAST_SOURCE_PASSWORD "$ENV_FILE")"; SOURCE_PW="${SOURCE_PW:-$(secret)}"
RELAY_PW="$(old KBR_ICECAST_RELAY_PASSWORD "$ENV_FILE")"; RELAY_PW="${RELAY_PW:-$(secret)}"
ADMIN_PW="$(old KBR_ICECAST_ADMIN_PASSWORD "$ENV_FILE")"; ADMIN_PW="${ADMIN_PW:-$(secret)}"
MIRROR="$(old KBR_RELAY_MIRROR_LIBRARY "$RELAY_ENV")"; MIRROR="${MIRROR:-1}"
TIMED="$(old KBR_RELAY_TIMED "$RELAY_ENV")"; TIMED="${TIMED:-offline}"

# The station's name for the stream's title (the box still sets up if the site is away)
NAME_TZ="$(BACKEND_URL="$BACKEND_URL" SID="$STATION" python3 -c '
import json, os, re, urllib.request
try:
    with urllib.request.urlopen(os.environ["BACKEND_URL"] + "/stations/" + os.environ["SID"], timeout=15) as r:
        st = json.load(r)
except Exception:
    st = {}
name = (st.get("name") or "").replace(chr(34), "").replace(chr(92), "").replace("$", "").replace("|", " ")
tz = st.get("timezone") or ""
print(name + "|" + (tz if re.fullmatch(r"[A-Za-z0-9_+/-]+", tz) else ""))
' || true)"
SNAME="${NAME_TZ%%|*}"
[ -n "$SNAME" ] || SNAME="$(old KBR_STATION_NAME "$STATION_ENV" | tr -d '"')"
[ -n "$SNAME" ] || SNAME="Kol B'ramah"
BOX_TZ="${NAME_TZ##*|}"
# A station left on "UTC" is read as New York time, as the hourly time checks do
[ -n "$BOX_TZ" ] && [ "$BOX_TZ" != UTC ] || BOX_TZ="$(old KBR_BOX_TZ "$BOX_ENV")"
BOX_TZ="${BOX_TZ:-America/New_York}"
AUTO="${KBR_BOX_AUTO_UPDATE:-$(old KBR_BOX_AUTO_UPDATE "$BOX_ENV")}"; AUTO="${AUTO:-1}"

fetch streaming/radio.liq "$ETC/radio.liq.new"
fetch streaming/kbr-engine@.service /etc/systemd/system/kbr-engine@.service
fetch streaming/icecast.xml /tmp/kbr-icecast.xml
fetch local/kbr_relay.py "$OPT/kbr_relay.py.new"
fetch local/kbr-relay.service /etc/systemd/system/kbr-relay.service
chmod 644 "$ETC/radio.liq.new" "$OPT/kbr_relay.py.new"

umask 077
# The engine asks the relay on this box, never the website directly
cat > "$ENV_FILE" <<ENV
KBR_BACKEND_URL=http://127.0.0.1:${RELAY_PORT}/api/v1
KBR_PLAYOUT_TOKEN=${TOKEN}
KBR_ICECAST_HOST=127.0.0.1
KBR_ICECAST_PORT=8000
KBR_ICECAST_SOURCE_PASSWORD=${SOURCE_PW}
KBR_ICECAST_RELAY_PASSWORD=${RELAY_PW}
KBR_ICECAST_ADMIN_PASSWORD=${ADMIN_PW}
KBR_SKIP_FADE_SECONDS=0.5
ENV
cat > "$STATION_ENV" <<SENV
KBR_STATION_ID=${STATION}
KBR_STATION_NAME="${SNAME}"
KBR_CONTROL_PORT=${CONTROL_PORT}
KBR_ICECAST_MOUNT=/live-${STATION}
KBR_HLS_DIR=
KBR_EMERGENCY_DIR=${LIB}/emergency/${STATION}
KBR_ALSA_DEVICE=${ALSA}
SENV
cat > "$RELAY_ENV" <<RENV
KBR_UPSTREAM_URL=${BACKEND_URL}
KBR_STATION_ID=${STATION}
KBR_PLAYOUT_TOKEN=${TOKEN}
KBR_RELAY_PORT=${RELAY_PORT}
KBR_ENGINE_CONTROL=http://127.0.0.1:${CONTROL_PORT}
KBR_RELAY_DIR=${RELAY_DIR}
KBR_RELAY_TIMED=${TIMED}
KBR_RELAY_MIRROR_LIBRARY=${MIRROR}
RENV
for f in "$ENV_FILE" "$STATION_ENV" "$RELAY_ENV"; do chown root:liquidsoap "$f"; chmod 640 "$f"; done
umask 022
cat > "$BOX_ENV" <<BENV
KBR_BOX_STATION=${STATION}
KBR_BOX_REF=${REF}
KBR_BOX_TZ=${BOX_TZ}
KBR_BOX_AUTO_UPDATE=${AUTO}
BENV

# The hourly look for a new release (it does nothing outside a quiet time)
cat > /etc/systemd/system/kbr-box-update.service <<'UNIT'
[Unit]
Description=KBR Live local FM box: take a new release in a quiet time
After=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/kbr-engine/box.env
Environment=KBR_SETUP_TIMER=1
TimeoutStartSec=45min
ExecStart=/bin/bash -c 'curl -fsSL "https://raw.githubusercontent.com/ShmuelSokol/radioplatform/$KBR_BOX_REF/infra/local/setup-box.sh" -o /opt/kbr-engine/setup-box.sh.new && KBR_SETUP_REF="$KBR_BOX_REF" exec bash /opt/kbr-engine/setup-box.sh.new "$KBR_BOX_STATION"'
UNIT
cat > /etc/systemd/system/kbr-box-update.timer <<'UNIT'
[Unit]
Description=KBR Live local FM box: hourly look for a new release

[Timer]
OnCalendar=*:35
AccuracySec=30s

[Install]
WantedBy=timers.target
UNIT

# The engine starts after the relay and the sound system at boot
mkdir -p "/etc/systemd/system/kbr-engine@.service.d"
cat > "/etc/systemd/system/kbr-engine@.service.d/box.conf" <<'UNIT'
[Unit]
After=kbr-relay.service sound.target
Wants=kbr-relay.service
UNIT

# Icecast listens on this machine only (the engine needs one beside it)
sed -e "s/STREAM_DOMAIN/localhost/" -e "s/SOURCE_PASSWORD/${SOURCE_PW}/" \
    -e "s/RELAY_PASSWORD/${RELAY_PW}/" -e "s/ADMIN_PASSWORD/${ADMIN_PW}/" \
    /tmp/kbr-icecast.xml > /etc/icecast2/icecast.xml
rm -f /tmp/kbr-icecast.xml
chown icecast2:icecast /etc/icecast2/icecast.xml; chmod 640 /etc/icecast2/icecast.xml
sed -i 's/^ENABLE=.*/ENABLE=true/' /etc/default/icecast2 2>/dev/null || true

# ── Restart only what changed ────────────────────────────────────────────────
APPLIED=$LIB/applied
mkdir -p "$APPLIED"
sum_of() { cat "$@" 2>/dev/null | sha256sum | cut -d' ' -f1; }
engine_answers() {
  [ "$(curl -s -o /dev/null -w '%{http_code}' -m 3 -X POST -H "X-Playout-Token: ${TOKEN}" \
        -H 'Content-Type: application/json' -d '{}' "http://127.0.0.1:${CONTROL_PORT}/status")" = 200 ]
}
wait_engine() { for _ in $(seq 1 45); do sleep 2; if engine_answers; then return 0; fi; done; return 1; }

NEW_SCRIPT=no
if ! cmp -s "$ETC/radio.liq.new" "$ETC/radio.liq"; then
  echo "New engine script: checking it before the engine restarts..."
  if ! ( set -a; . "$ENV_FILE"; . "$STATION_ENV"; set +a
         export KBR_DEBUG_WAV= KBR_ALSA_DEVICE=
         nice -n 19 runuser -u liquidsoap -- liquidsoap --check "$ETC/radio.liq.new" ); then
    rm -f "$ETC/radio.liq.new" "$OPT/kbr_relay.py.new"
    echo "The new engine script does not pass its check. Nothing was restarted."
    exit 1
  fi
  NEW_SCRIPT=yes
fi

systemctl daemon-reload
systemctl disable --now liquidsoap 2>/dev/null || true
systemctl enable icecast2 kbr-relay "kbr-engine@$STATION" >/dev/null 2>&1
if [ "$AUTO" = 1 ]; then systemctl enable --now kbr-box-update.timer >/dev/null 2>&1 || true
else systemctl disable --now kbr-box-update.timer >/dev/null 2>&1 || true; fi

ICE_SUM="$(sum_of /etc/icecast2/icecast.xml /etc/default/icecast2)"
ICE_RESTARTED=no
if [ "$ICE_SUM" != "$(cat "$APPLIED/icecast" 2>/dev/null)" ] || ! systemctl is-active --quiet icecast2; then
  systemctl restart icecast2
  echo "$ICE_SUM" > "$APPLIED/icecast"
  ICE_RESTARTED=yes
fi

# The relay: a restart takes a second and is not heard (the engine holds its next item)
mv "$OPT/kbr_relay.py.new" "$OPT/kbr_relay.py"
RELAY_SUM="$(sum_of "$OPT/kbr_relay.py" /etc/systemd/system/kbr-relay.service "$RELAY_ENV")"
if [ "$RELAY_SUM" != "$(cat "$APPLIED/relay" 2>/dev/null)" ] || ! systemctl is-active --quiet kbr-relay; then
  systemctl restart kbr-relay
  echo "$RELAY_SUM" > "$APPLIED/relay"
  echo "Relay: restarted"
else
  echo "Relay: unchanged, left running"
fi

# The engine: a restart is about 10 s of silence on FM. So: only when something changed,
# only in a quiet time (or KBR_APPLY=now, or when it is not running at all), and between
# two songs.
wait_song_end() {
  # Up to 20 minutes for the item on air to reach its last second and a half
  local left
  for _ in $(seq 1 1200); do
    left="$(curl -s -m 3 -X POST -H "X-Playout-Token: ${TOKEN}" -H 'Content-Type: application/json' -d '{}' \
      "http://127.0.0.1:${CONTROL_PORT}/status" 2>/dev/null \
      | python3 -c 'import json,sys; print(json.load(sys.stdin).get("main_remaining", -1))' 2>/dev/null || echo -1)"
    # -1 = nothing on the music lane (a blackout's silence, an engine that does not answer)
    if python3 -c 'import sys; left=float(sys.argv[1]); sys.exit(0 if left < 1.5 else 1)' "${left:--1}" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}
ENGINE_UP=no; systemctl is-active --quiet "kbr-engine@$STATION" && ENGINE_UP=yes
ENG_SUM_WANTED="$( if [ "$NEW_SCRIPT" = yes ]; then sum_of "$ETC/radio.liq.new" /etc/systemd/system/kbr-engine@.service \
    /etc/systemd/system/kbr-engine@.service.d/box.conf "$ENV_FILE" "$STATION_ENV"
  else sum_of "$ETC/radio.liq" /etc/systemd/system/kbr-engine@.service \
    /etc/systemd/system/kbr-engine@.service.d/box.conf "$ENV_FILE" "$STATION_ENV"; fi )"
ENGINE_WAITS=no
if [ "$ENG_SUM_WANTED" = "$(cat "$APPLIED/$STATION" 2>/dev/null)" ] && [ "$ICE_RESTARTED" = no ] && [ "$ENGINE_UP" = yes ]; then
  rm -f "$ETC/radio.liq.new"
  echo "Engine: unchanged, left running"
elif [ "$ENGINE_UP" = yes ] && [ "$ICE_RESTARTED" = no ] && [ "$APPLY" != now ] && ! quiet_now; then
  # On air and not a quiet time: the change waits (the hourly update takes it)
  rm -f "$ETC/radio.liq.new"
  ENGINE_WAITS=yes
  echo "Engine: a change is waiting for a quiet time (a blackout, or 3 AM at the station). KBR_APPLY=now does it at once."
else
  if [ "$ENGINE_UP" = yes ] && [ "$ICE_RESTARTED" = no ]; then
    echo "Engine: waiting for the item on air to end..."
    wait_song_end || echo "Engine: the item on air did not end within 20 minutes; restarting anyway"
  fi
  if [ "$NEW_SCRIPT" = yes ]; then
    [ -f "$ETC/radio.liq" ] && cp -p "$ETC/radio.liq" "$ETC/radio.liq.old"
    mv "$ETC/radio.liq.new" "$ETC/radio.liq"
  fi
  systemctl restart "kbr-engine@$STATION"
  if wait_engine; then
    echo "$ENG_SUM_WANTED" > "$APPLIED/$STATION"
    echo "Engine: restarted and answering"
  elif [ "$NEW_SCRIPT" = yes ] && [ -f "$ETC/radio.liq.old" ]; then
    echo "Engine: the new script does not answer after 90 s. Putting the old one back."
    cp -p "$ETC/radio.liq.old" "$ETC/radio.liq"
    rm -f "$APPLIED/$STATION"
    systemctl restart "kbr-engine@$STATION"
    if wait_engine; then
      echo "Engine: running the OLD script again"
      sum_of "$ETC/radio.liq" /etc/systemd/system/kbr-engine@.service \
        /etc/systemd/system/kbr-engine@.service.d/box.conf "$ENV_FILE" "$STATION_ENV" > "$APPLIED/$STATION"
    else
      echo "Engine: NOT answering with the old script either (journalctl -u kbr-engine@$STATION -n 50)"
    fi
    exit 1
  else
    rm -f "$APPLIED/$STATION"
    echo "Engine: restarted but NOT answering after 90 s (journalctl -u kbr-engine@$STATION -n 50)"
  fi
fi

sleep 3
echo
echo "================ KBR local FM box ================"
echo "Station: ${SNAME} (${STATION})"
systemctl is-active --quiet "kbr-engine@$STATION" && echo "Engine:  running" || echo "Engine:  NOT running"
systemctl is-active --quiet kbr-relay && echo "Relay:   running" || echo "Relay:   NOT running"
STATUS="$(curl -s -m 5 "http://127.0.0.1:${RELAY_PORT}/relay/status" || true)"
[ -n "$STATUS" ] && echo "         $STATUS"
if [ -z "$ALSA" ]; then
  echo "Sound card: none chosen yet. The cards this box has:"
  aplay -L 2>/dev/null | grep -E '^(default|plughw|hw|sysdefault)' | sed 's/^/           /' || true
  echo "         Run again with the one that feeds the transmitter as the second word."
elif runuser -u liquidsoap -- aplay -q -D "$ALSA" -t raw -f cd -d 1 /dev/zero 2>/dev/null \
     || journalctl -u "kbr-engine@$STATION" -n 200 --no-pager 2>/dev/null | grep -q "sound card ${ALSA}: on"; then
  echo "Sound card: ${ALSA} answers"
else
  echo "Sound card: ${ALSA} does NOT answer (the engine plays without it and takes it when it appears)"
fi
[ "$ENGINE_WAITS" = yes ] && echo "Update:  an engine change waits for a quiet time"
[ "$AUTO" = 1 ] && echo "Updates: looked for every hour at :35, taken in a quiet time (release: ${REF})" \
  || echo "Updates: the hourly look is switched off"
echo "Disk for the library: $(df -h --output=avail "$RELAY_DIR" | tail -1 | tr -d ' ') free"
echo
echo "Backend settings (Railway), both lists must hold this station:"
echo "  PLAYOUT_ENGINE_STATION_ID=...,${STATION}"
echo "  PLAYOUT_RELAY_STATION_ID=...,${STATION}"
echo "=================================================="

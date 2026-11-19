#!/bin/bash
# Sets up the KBR Live playout engine (Liquidsoap 2.2 + Icecast + nginx) on a fresh
# Ubuntu 24.04 server: one engine per station, each with its own stream
# (https://<domain>/live-<station id>). Safe to run again: it keeps the secrets it
# made the first time, adds engines for new stations and removes ones no longer listed.
#
#   curl -fsSL https://raw.githubusercontent.com/ShmuelSokol/radioplatform/main/infra/streaming/setup.sh \
#     | bash -s -- [stations] [stream-domain] [backend-url]
#
# stations: "all" (default: every station the backend lists) or comma-separated ids.
# Name every station that has an engine: one left out has its engine removed.
#
# KBR_RESTART_ONLY=<id>[,<id>]: restart only these engines now, even when the script
# changed for all of them (one station first, the others later: run again without it).
# The others keep running the old script until their own restart.
#
# KBR_BROADCAST_SOUND=<id>[,<id>] | all | none: which engines run the broadcast sound
# chain (leveler, three bands, limiter; KBR_PROCESSING=broadcast); the others run the
# light one. Unset: every engine keeps what it had (light for a new one). One station
# first (KBR Jerusalem), the others once Shmuel has listened.
# KBR_CLEAN_SOUND=<id>[,<id>] | all: these engines run the clean chain instead (leveler
# and limiter only, the recording's own tone; KBR_PROCESSING=clean). It wins over
# KBR_BROADCAST_SOUND for a station named in both.
#
# At the end it prints the settings the backend needs. No password is ever written
# to the repository; they are generated here and stay on this machine.
set -euo pipefail

STATIONS="${1:-all}"
DOMAIN="${2:-stream.kbrlive.com}"
BACKEND_URL="${3:-https://api.kbrlive.com/api/v1}"
REF="${KBR_SETUP_REF:-main}"
RESTART_ONLY="$(echo "${KBR_RESTART_ONLY:-}" | tr ',' ' ')"
BROADCAST_SOUND="$(echo "${KBR_BROADCAST_SOUND-}" | tr ',' ' ')"
BROADCAST_SOUND_SET="${KBR_BROADCAST_SOUND+yes}"
CLEAN_SOUND="$(echo "${KBR_CLEAN_SOUND-}" | tr ',' ' ')"
RAW="https://raw.githubusercontent.com/ShmuelSokol/radioplatform/${REF}/infra/streaming"
ENV_FILE=/etc/kbr-engine/env
STATION_DIR=/etc/kbr-engine/stations
FIRST_PORT=8081

[ "$(id -u)" = 0 ] || { echo "Run as root."; exit 1; }
. /etc/os-release
[ "${VERSION_ID}" = "24.04" ] || echo "Warning: written for Ubuntu 24.04 (found ${VERSION_ID}); Liquidsoap 2.2 is needed."

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q liquidsoap icecast2 nginx certbot python3-certbot-nginx curl ufw openssl

# "<id>|<name>" per line, for the stations to play. The list is read page by page
# (the backend returns at most 100 per call), so 'all' covers every station.
STATION_LINES="$(BACKEND_URL="$BACKEND_URL" WANT="$STATIONS" python3 -c '
import json, os, urllib.request
base = os.environ["BACKEND_URL"]
page_size = 100
rows, skip = [], 0
while True:
    with urllib.request.urlopen(f"{base}/stations?skip={skip}&limit={page_size}", timeout=30) as resp:
        data = json.load(resp)
    page = data.get("stations", data) if isinstance(data, dict) else data
    rows += page
    skip += len(page)
    total = data.get("total") if isinstance(data, dict) else None
    if len(page) < page_size or (total is not None and skip >= total):
        break
want = os.environ["WANT"].strip()
ids = None if want in ("", "all") else {w.strip() for w in want.split(",") if w.strip()}
for r in rows:
    if ids is None or r["id"] in ids:
        print(r["id"] + "|" + (r.get("name") or "Kol B\x27ramah").replace("|", " ").replace(chr(34), "").replace(chr(92), ""))
')"
[ -n "$STATION_LINES" ] || { echo "No stations found at $BACKEND_URL/stations for '$STATIONS'."; exit 1; }
echo "Stations:"; echo "$STATION_LINES" | sed 's/^/  /'

mkdir -p /etc/kbr-engine "$STATION_DIR" /var/lib/kbr-engine/emergency
chown -R liquidsoap:liquidsoap /var/lib/kbr-engine

secret() { openssl rand -hex 24; }
old() { [ -f "$ENV_FILE" ] && grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- || true; }
TOKEN="$(old KBR_PLAYOUT_TOKEN)"
# Shown at the end only when it is new (or KBR_SHOW_TOKEN=1): a rerun's output gets copied around
TOKEN_IS_NEW=no; [ -n "$TOKEN" ] || { TOKEN="$(secret)"; TOKEN_IS_NEW=yes; }
SOURCE_PW="$(old KBR_ICECAST_SOURCE_PASSWORD)"; SOURCE_PW="${SOURCE_PW:-$(secret)}"
# Kept between runs, so an unchanged Icecast configuration stays byte for byte the same
RELAY_PW="$(old KBR_ICECAST_RELAY_PASSWORD)"; RELAY_PW="${RELAY_PW:-$(secret)}"
ADMIN_PW="$(old KBR_ICECAST_ADMIN_PASSWORD)"; ADMIN_PW="${ADMIN_PW:-$(secret)}"

curl -fsSL "$RAW/radio.liq" -o /etc/kbr-engine/radio.liq.new
curl -fsSL "$RAW/kbr-engine@.service" -o /etc/systemd/system/kbr-engine@.service
curl -fsSL "$RAW/nginx-stream.conf" -o /tmp/kbr-nginx.conf
curl -fsSL "$RAW/icecast.xml" -o /tmp/kbr-icecast.xml

umask 077
cat > "$ENV_FILE" <<ENV
KBR_BACKEND_URL=${BACKEND_URL}
KBR_PLAYOUT_TOKEN=${TOKEN}
KBR_EMERGENCY_DIR=/var/lib/kbr-engine/emergency
KBR_ICECAST_HOST=127.0.0.1
KBR_ICECAST_PORT=8000
KBR_ICECAST_SOURCE_PASSWORD=${SOURCE_PW}
KBR_ICECAST_RELAY_PASSWORD=${RELAY_PW}
KBR_ICECAST_ADMIN_PASSWORD=${ADMIN_PW}
KBR_SKIP_FADE_SECONDS=0.5
ENV
chown root:liquidsoap "$ENV_FILE"; chmod 640 "$ENV_FILE"
umask 022

sed -e "s/STREAM_DOMAIN/${DOMAIN}/" -e "s/SOURCE_PASSWORD/${SOURCE_PW}/" \
    -e "s/RELAY_PASSWORD/${RELAY_PW}/" -e "s/ADMIN_PASSWORD/${ADMIN_PW}/" \
    /tmp/kbr-icecast.xml > /etc/icecast2/icecast.xml
chown icecast2:icecast /etc/icecast2/icecast.xml; chmod 640 /etc/icecast2/icecast.xml
sed -i 's/^ENABLE=.*/ENABLE=true/' /etc/default/icecast2 2>/dev/null || true

# One engine per station: its own control port, stream mount and nginx route
CONTROL_INC=/etc/nginx/kbr-control.inc
: > "$CONTROL_INC"
PORT=$FIRST_PORT
WANTED=""
PORTS=""
while IFS='|' read -r SID SNAME; do
  [ -n "$SID" ] || continue
  # The station's sound chain: as asked (KBR_BROADCAST_SOUND), else what it had
  if [ "$BROADCAST_SOUND_SET" = yes ]; then
    case " $BROADCAST_SOUND " in
      *" all "*|*" $SID "*) PROCESSING=broadcast ;;
      *) PROCESSING=light ;;
    esac
  else
    PROCESSING="$( [ -f "$STATION_DIR/$SID.env" ] && grep -E '^KBR_PROCESSING=' "$STATION_DIR/$SID.env" | head -1 | cut -d= -f2- || true)"
    PROCESSING="${PROCESSING:-light}"
  fi
  case " $CLEAN_SOUND " in
    *" all "*|*" $SID "*) PROCESSING=clean ;;
  esac
  echo "Engine $SID: sound chain $PROCESSING"
  cat > "$STATION_DIR/$SID.env" <<SENV
KBR_STATION_ID=${SID}
KBR_STATION_NAME="${SNAME}"
KBR_CONTROL_PORT=${PORT}
KBR_ICECAST_MOUNT=/live-${SID}
KBR_HLS_DIR=/var/lib/kbr-engine/hls/${SID}
KBR_EMERGENCY_DIR=/var/lib/kbr-engine/emergency/${SID}
KBR_PROCESSING=${PROCESSING}
SENV
  # Each station keeps its own emergency music (copies of its last 40 songs). With one
  # shared folder a second station would have fallen back to the first one's songs. A new
  # folder starts with what the shared one holds, so it is never empty, and fills with
  # the station's own songs as they air.
  EMERG="/var/lib/kbr-engine/emergency/${SID}"
  if [ ! -d "$EMERG" ]; then
    mkdir -p "$EMERG"
    find /var/lib/kbr-engine/emergency -maxdepth 1 -type f -name '*.mp3' -exec cp -n {} "$EMERG/" \; 2>/dev/null || true
  fi
  chown -R liquidsoap:liquidsoap "$EMERG"
  mkdir -p "/var/lib/kbr-engine/hls/${SID}"; chown liquidsoap:liquidsoap "/var/lib/kbr-engine/hls/${SID}"
  chown root:liquidsoap "$STATION_DIR/$SID.env"; chmod 640 "$STATION_DIR/$SID.env"
  cat >> "$CONTROL_INC" <<LOC
location /control/${SID}/ {
    limit_except POST { deny all; }
    proxy_pass http://127.0.0.1:${PORT}/;
    proxy_read_timeout 10;
}
LOC
  WANTED="$WANTED $SID"
  PORTS="$PORTS $SID=$PORT"
  PORT=$((PORT + 1))
done <<< "$STATION_LINES"

# Rewritten every run (certbot puts the certificate back below)
sed "s/STREAM_DOMAIN/${DOMAIN}/" /tmp/kbr-nginx.conf > /etc/nginx/sites-available/kbr-stream
ln -sf /etc/nginx/sites-available/kbr-stream /etc/nginx/sites-enabled/kbr-stream
rm -f /etc/nginx/sites-enabled/default /tmp/kbr-nginx.conf /tmp/kbr-icecast.xml

ufw allow 22/tcp >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

systemctl daemon-reload
systemctl disable --now liquidsoap 2>/dev/null || true
systemctl disable --now kbr-engine 2>/dev/null || true   # the old single-station engine
rm -f /etc/systemd/system/kbr-engine.service
# Engines for stations no longer listed
for f in "$STATION_DIR"/*.env; do
  [ -e "$f" ] || continue
  SID="$(basename "$f" .env)"
  case " $WANTED " in *" $SID "*) ;; *)
    systemctl disable --now "kbr-engine@$SID" 2>/dev/null || true
    rm -f "$f" "/var/lib/kbr-engine/applied/$SID"; echo "Removed engine for $SID";;
  esac
done
# ── Restart only what changed, one engine at a time ──────────────────────────
# A rerun used to restart Icecast and every engine at the same moment. Each engine
# start checks the whole script at full CPU for about 20 s; on a one-core server two
# of them at once left a station off air for about 50 s and the others stuttering
# ("We must catchup" in the journal, 2026-11-17). Now: the new script is checked
# before it replaces the old one, Icecast and an engine whose script and settings
# are unchanged keep running, and changed engines restart one after the other.
APPLIED=/var/lib/kbr-engine/applied
mkdir -p "$APPLIED"
sum_of() { cat "$@" 2>/dev/null | sha256sum | cut -d' ' -f1; }

FIRST_SID="$(echo $WANTED | awk '{print $1}')"
if ! cmp -s /etc/kbr-engine/radio.liq.new /etc/kbr-engine/radio.liq; then
  echo "New engine script: checking it before any engine restarts..."
  chmod 644 /etc/kbr-engine/radio.liq.new
  # Low priority: the check must not take CPU from an engine that is on air
  # No HLS folder or test file for the check: it must not touch a running engine's files
  if ! ( set -a; . "$ENV_FILE"; . "$STATION_DIR/$FIRST_SID.env"; set +a
         export KBR_HLS_DIR= KBR_DEBUG_WAV=
         nice -n 19 runuser -u liquidsoap -- liquidsoap --check /etc/kbr-engine/radio.liq.new ); then
    rm -f /etc/kbr-engine/radio.liq.new
    echo "The new engine script does not pass its check. Nothing was restarted; the engines keep running the old script."
    exit 1
  fi
  # The check only reads the script. Start it once for real, beside the running engines
  # (own control port, no backend, no Icecast, no HLS), and see that it answers: a
  # script that passed the check has hung at start with no error (2026-11-17), which
  # on a real engine would have meant a silent station.
  echo "Starting the new script once on a spare port to see that it runs..."
  TRIAL_PORT=8990
  ( set -a; . "$ENV_FILE"; . "$STATION_DIR/$FIRST_SID.env"; set +a
    export KBR_HLS_DIR= KBR_DEBUG_WAV= KBR_CONTROL_PORT=$TRIAL_PORT KBR_STATION_ID=trial
    export KBR_BACKEND_URL=http://127.0.0.1:9/api/v1 KBR_ICECAST_PORT=9 KBR_ICECAST_MOUNT=/trial
    exec nice -n 19 timeout 75 runuser -u liquidsoap -- liquidsoap /etc/kbr-engine/radio.liq.new ) >/tmp/kbr-trial.log 2>&1 &
  TRIAL_PID=$!
  TRIAL_OK=no
  for _ in $(seq 1 30); do
    sleep 2
    if [ "$(curl -s -o /dev/null -w '%{http_code}' -m 2 -X POST -H "X-Playout-Token: ${TOKEN}" \
            -H 'Content-Type: application/json' -d '{}' "http://127.0.0.1:$TRIAL_PORT/status")" = 200 ]; then
      TRIAL_OK=yes; break
    fi
    kill -0 "$TRIAL_PID" 2>/dev/null || break
  done
  pkill -f "liquidsoap /etc/kbr-engine/radio.liq.new" 2>/dev/null || true
  wait "$TRIAL_PID" 2>/dev/null || true
  if [ "$TRIAL_OK" != yes ]; then
    rm -f /etc/kbr-engine/radio.liq.new
    echo "The new engine script does not start (see /tmp/kbr-trial.log). Nothing was restarted; the engines keep running the old script."
    exit 1
  fi
  mv /etc/kbr-engine/radio.liq.new /etc/kbr-engine/radio.liq
else
  rm -f /etc/kbr-engine/radio.liq.new
fi

systemctl enable icecast2 nginx >/dev/null
ICE_SUM="$(sum_of /etc/icecast2/icecast.xml /etc/default/icecast2)"
if [ "$ICE_SUM" != "$(cat "$APPLIED/icecast" 2>/dev/null)" ] || ! systemctl is-active --quiet icecast2; then
  systemctl restart icecast2
  echo "$ICE_SUM" > "$APPLIED/icecast"
  ICE_RESTARTED=yes
  echo "Icecast restarted (its settings changed)"
else
  ICE_RESTARTED=no
fi
nginx -t && systemctl reload nginx

engine_answers() {  # $1 = control port
  [ "$(curl -s -o /dev/null -w '%{http_code}' -m 3 -X POST -H "X-Playout-Token: ${TOKEN}" \
        -H 'Content-Type: application/json' -d '{}' "http://127.0.0.1:$1/status")" = 200 ]
}
for PAIR in $PORTS; do
  SID="${PAIR%%=*}"; EPORT="${PAIR##*=}"
  systemctl enable "kbr-engine@$SID" >/dev/null 2>&1
  ENG_SUM="$(sum_of /etc/kbr-engine/radio.liq /etc/systemd/system/kbr-engine@.service "$ENV_FILE" "$STATION_DIR/$SID.env")"
  if [ "$ENG_SUM" = "$(cat "$APPLIED/$SID" 2>/dev/null)" ] && [ "$ICE_RESTARTED" = no ] \
     && systemctl is-active --quiet "kbr-engine@$SID"; then
    echo "Engine $SID: unchanged, left running"
    continue
  fi
  if [ -n "$RESTART_ONLY" ] && ! case " $RESTART_ONLY " in *" $SID "*) true;; *) false;; esac; then
    # Not now: it keeps the old script; a later run without KBR_RESTART_ONLY restarts it
    echo "Engine $SID: changed but left running (KBR_RESTART_ONLY), restart it later"
    continue
  fi
  systemctl restart "kbr-engine@$SID"
  # Wait until it answers before the next one starts (its start-up needs the CPU)
  for _ in $(seq 1 45); do
    sleep 2
    if engine_answers "$EPORT"; then break; fi
  done
  if engine_answers "$EPORT"; then
    echo "$ENG_SUM" > "$APPLIED/$SID"
    echo "Engine $SID: restarted and answering"
  else
    rm -f "$APPLIED/$SID"
    echo "Engine $SID: restarted but NOT answering after 90 s (journalctl -u kbr-engine@$SID -n 50)"
  fi
done

# HTTPS needs the domain to point at this machine already
MY_IP="$(curl -fsS4 https://api.ipify.org || true)"
DNS_IP="$(getent ahostsv4 "$DOMAIN" | awk 'NR==1{print $1}' || true)"
TLS=no
if [ -n "$MY_IP" ] && [ "$MY_IP" = "$DNS_IP" ]; then
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect --keep-until-expiring && TLS=yes
fi

sleep 5
IDS="$(echo $WANTED | tr ' ' ',')"
echo
echo "================ KBR playout engine ================"
RUNNING=0; TOTAL=0
for SID in $WANTED; do
  TOTAL=$((TOTAL + 1))
  if systemctl is-active --quiet "kbr-engine@$SID"; then RUNNING=$((RUNNING + 1))
  else echo "Engine NOT running for $SID (journalctl -u kbr-engine@$SID -n 50)"; fi
done
echo "Engines: ${RUNNING} of ${TOTAL} running"
systemctl is-active --quiet icecast2 && echo "Icecast: running" || echo "Icecast: NOT running"
echo "Memory:  $(free -m | awk '/^Mem:/{print $3" MB used of "$2" MB"}')"
if [ "$TLS" = yes ]; then
  echo "HTTPS:   ready (streams at https://${DOMAIN}/live-<station id>, iPhone HLS at /hls/<station id>/mp3.m3u8)"
else
  echo "HTTPS:   NOT set up yet. Point ${DOMAIN} (DNS A record) at ${MY_IP:-this server}, then run this script again."
fi
echo
echo "Backend settings (Railway):"
echo "  PLAYOUT_ENGINE_ENABLED=true"
echo "  PLAYOUT_ENGINE_STATION_ID=${IDS}"
echo "  PLAYOUT_ENGINE_URL=https://${DOMAIN}/control"
if [ "$TOKEN_IS_NEW" = yes ] || [ "${KBR_SHOW_TOKEN:-}" = 1 ]; then
  echo "  PLAYOUT_ENGINE_TOKEN=${TOKEN}"
else
  echo "  PLAYOUT_ENGINE_TOKEN=(unchanged; KBR_SHOW_TOKEN=1 shows it, or see ${ENV_FILE})"
fi
echo "  ICECAST_STREAM_URL=https://${DOMAIN}/live"
echo "  PLAYOUT_STREAM_LIVE=false   (set true when listeners should hear the engine)"
echo "===================================================="

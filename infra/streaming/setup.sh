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
#
# At the end it prints the settings the backend needs. No password is ever written
# to the repository; they are generated here and stay on this machine.
set -euo pipefail

STATIONS="${1:-all}"
DOMAIN="${2:-stream.kbrlive.com}"
BACKEND_URL="${3:-https://api.kbrlive.com/api/v1}"
REF="${KBR_SETUP_REF:-main}"
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

# "<id>|<name>" per line, for the stations to play
STATION_LINES="$(curl -fsSL "$BACKEND_URL/stations?limit=100" | WANT="$STATIONS" python3 -c '
import json, os, sys
data = json.load(sys.stdin)
rows = data.get("stations", data) if isinstance(data, dict) else data
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
TOKEN="$(old KBR_PLAYOUT_TOKEN)";            TOKEN="${TOKEN:-$(secret)}"
SOURCE_PW="$(old KBR_ICECAST_SOURCE_PASSWORD)"; SOURCE_PW="${SOURCE_PW:-$(secret)}"

curl -fsSL "$RAW/radio.liq" -o /etc/kbr-engine/radio.liq
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
KBR_SKIP_FADE_SECONDS=0.5
ENV
chown root:liquidsoap "$ENV_FILE"; chmod 640 "$ENV_FILE"
umask 022

sed -e "s/STREAM_DOMAIN/${DOMAIN}/" -e "s/SOURCE_PASSWORD/${SOURCE_PW}/" \
    -e "s/RELAY_PASSWORD/$(secret)/" -e "s/ADMIN_PASSWORD/$(secret)/" \
    /tmp/kbr-icecast.xml > /etc/icecast2/icecast.xml
chown icecast2:icecast /etc/icecast2/icecast.xml; chmod 640 /etc/icecast2/icecast.xml
sed -i 's/^ENABLE=.*/ENABLE=true/' /etc/default/icecast2 2>/dev/null || true

# One engine per station: its own control port, stream mount and nginx route
CONTROL_INC=/etc/nginx/kbr-control.inc
: > "$CONTROL_INC"
PORT=$FIRST_PORT
WANTED=""
while IFS='|' read -r SID SNAME; do
  [ -n "$SID" ] || continue
  cat > "$STATION_DIR/$SID.env" <<SENV
KBR_STATION_ID=${SID}
KBR_STATION_NAME="${SNAME}"
KBR_CONTROL_PORT=${PORT}
KBR_ICECAST_MOUNT=/live-${SID}
SENV
  chown root:liquidsoap "$STATION_DIR/$SID.env"; chmod 640 "$STATION_DIR/$SID.env"
  cat >> "$CONTROL_INC" <<LOC
location /control/${SID}/ {
    limit_except POST { deny all; }
    proxy_pass http://127.0.0.1:${PORT}/;
    proxy_read_timeout 10;
}
LOC
  WANTED="$WANTED $SID"
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
    rm -f "$f"; echo "Removed engine for $SID";;
  esac
done
systemctl enable icecast2 nginx >/dev/null
systemctl restart icecast2
nginx -t && systemctl reload nginx
for SID in $WANTED; do
  systemctl enable "kbr-engine@$SID" >/dev/null 2>&1
  systemctl restart "kbr-engine@$SID"
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
  echo "HTTPS:   ready (streams at https://${DOMAIN}/live-<station id>)"
else
  echo "HTTPS:   NOT set up yet. Point ${DOMAIN} (DNS A record) at ${MY_IP:-this server}, then run this script again."
fi
echo
echo "Backend settings (Railway):"
echo "  PLAYOUT_ENGINE_ENABLED=true"
echo "  PLAYOUT_ENGINE_STATION_ID=${IDS}"
echo "  PLAYOUT_ENGINE_URL=https://${DOMAIN}/control"
echo "  PLAYOUT_ENGINE_TOKEN=${TOKEN}"
echo "  ICECAST_STREAM_URL=https://${DOMAIN}/live"
echo "  PLAYOUT_STREAM_LIVE=false   (set true when listeners should hear the engine)"
echo "===================================================="

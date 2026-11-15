#!/bin/bash
# Sets up the KBR Live playout engine (Liquidsoap 2.2 + Icecast + nginx) on a fresh
# Ubuntu 24.04 server. Safe to run again: it keeps the secrets it made the first time.
#
#   curl -fsSL https://raw.githubusercontent.com/ShmuelSokol/radioplatform/main/infra/streaming/setup.sh \
#     | bash -s -- <station-id> [stream-domain] [backend-url]
#
# At the end it prints the settings the backend needs. No password is ever written
# to the repository; they are generated here and stay on this machine.
set -euo pipefail

STATION_ID="${1:?usage: setup.sh <station-id> [stream-domain] [backend-url]}"
DOMAIN="${2:-stream.kbrlive.com}"
BACKEND_URL="${3:-https://api.kbrlive.com/api/v1}"
REF="${KBR_SETUP_REF:-main}"
RAW="https://raw.githubusercontent.com/ShmuelSokol/radioplatform/${REF}/infra/streaming"
ENV_FILE=/etc/kbr-engine/env

[ "$(id -u)" = 0 ] || { echo "Run as root."; exit 1; }
. /etc/os-release
[ "${VERSION_ID}" = "24.04" ] || echo "Warning: written for Ubuntu 24.04 (found ${VERSION_ID}); Liquidsoap 2.2 is needed."

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q liquidsoap icecast2 nginx certbot python3-certbot-nginx curl ufw openssl

mkdir -p /etc/kbr-engine /var/lib/kbr-engine/emergency
chown -R liquidsoap:liquidsoap /var/lib/kbr-engine

secret() { openssl rand -hex 24; }
old() { [ -f "$ENV_FILE" ] && grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- || true; }
TOKEN="$(old KBR_PLAYOUT_TOKEN)";            TOKEN="${TOKEN:-$(secret)}"
SOURCE_PW="$(old KBR_ICECAST_SOURCE_PASSWORD)"; SOURCE_PW="${SOURCE_PW:-$(secret)}"

curl -fsSL "$RAW/radio.liq" -o /etc/kbr-engine/radio.liq
curl -fsSL "$RAW/kbr-engine.service" -o /etc/systemd/system/kbr-engine.service
curl -fsSL "$RAW/nginx-stream.conf" -o /tmp/kbr-nginx.conf
curl -fsSL "$RAW/icecast.xml" -o /tmp/kbr-icecast.xml

umask 077
cat > "$ENV_FILE" <<ENV
KBR_BACKEND_URL=${BACKEND_URL}
KBR_STATION_ID=${STATION_ID}
KBR_PLAYOUT_TOKEN=${TOKEN}
KBR_CONTROL_PORT=8081
KBR_EMERGENCY_DIR=/var/lib/kbr-engine/emergency
KBR_ICECAST_HOST=127.0.0.1
KBR_ICECAST_PORT=8000
KBR_ICECAST_SOURCE_PASSWORD=${SOURCE_PW}
KBR_ICECAST_MOUNT=/live
KBR_SKIP_FADE_SECONDS=0.5
ENV
chown root:liquidsoap "$ENV_FILE"; chmod 640 "$ENV_FILE"
umask 022

sed -e "s/STREAM_DOMAIN/${DOMAIN}/" -e "s/SOURCE_PASSWORD/${SOURCE_PW}/" \
    -e "s/RELAY_PASSWORD/$(secret)/" -e "s/ADMIN_PASSWORD/$(secret)/" \
    /tmp/kbr-icecast.xml > /etc/icecast2/icecast.xml
chown icecast2:icecast /etc/icecast2/icecast.xml; chmod 640 /etc/icecast2/icecast.xml
sed -i 's/^ENABLE=.*/ENABLE=true/' /etc/default/icecast2 2>/dev/null || true

# Keep an existing nginx site: certbot has already added the certificate to it
if [ ! -f /etc/nginx/sites-available/kbr-stream ]; then
  sed "s/STREAM_DOMAIN/${DOMAIN}/" /tmp/kbr-nginx.conf > /etc/nginx/sites-available/kbr-stream
fi
ln -sf /etc/nginx/sites-available/kbr-stream /etc/nginx/sites-enabled/kbr-stream
rm -f /etc/nginx/sites-enabled/default /tmp/kbr-nginx.conf /tmp/kbr-icecast.xml

ufw allow 22/tcp >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

systemctl daemon-reload
systemctl disable --now liquidsoap 2>/dev/null || true
systemctl enable icecast2 nginx kbr-engine >/dev/null
systemctl restart icecast2
nginx -t && systemctl reload nginx
systemctl restart kbr-engine

# HTTPS needs the domain to point at this machine already
MY_IP="$(curl -fsS4 https://api.ipify.org || true)"
DNS_IP="$(getent ahostsv4 "$DOMAIN" | awk 'NR==1{print $1}' || true)"
TLS=no
if [ -n "$MY_IP" ] && [ "$MY_IP" = "$DNS_IP" ]; then
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect && TLS=yes
fi

sleep 4
echo
echo "================ KBR playout engine ================"
systemctl is-active --quiet kbr-engine && echo "Engine:  running" || echo "Engine:  NOT running (journalctl -u kbr-engine -n 50)"
systemctl is-active --quiet icecast2  && echo "Icecast: running" || echo "Icecast: NOT running"
if [ "$TLS" = yes ]; then
  echo "HTTPS:   ready at https://${DOMAIN}/live"
else
  echo "HTTPS:   NOT set up yet. Point ${DOMAIN} (DNS A record) at ${MY_IP:-this server}, then run this script again."
fi
echo
echo "Backend settings (Railway):"
echo "  PLAYOUT_ENGINE_ENABLED=true"
echo "  PLAYOUT_ENGINE_STATION_ID=${STATION_ID}"
echo "  PLAYOUT_ENGINE_URL=https://${DOMAIN}/control"
echo "  PLAYOUT_ENGINE_TOKEN=${TOKEN}"
echo "  ICECAST_STREAM_URL=https://${DOMAIN}/live"
echo "  PLAYOUT_STREAM_LIVE=false   (set true when listeners should hear the engine)"
echo "===================================================="

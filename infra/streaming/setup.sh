#!/bin/bash
# Install Liquidsoap + Icecast on Ubuntu 22.04
# Run on a fresh VPS (DigitalOcean, Hetzner, etc.)
set -euo pipefail

apt-get update && apt-get install -y liquidsoap icecast2 nginx certbot python3-certbot-nginx

# Create directories
mkdir -p /etc/liquidsoap /var/log/liquidsoap /tmp/liquidsoap

# Copy config files (uploaded separately)
# /etc/icecast2/icecast.xml
# /etc/liquidsoap/radio.liq
# /etc/systemd/system/liquidsoap.service
# /etc/nginx/sites-available/stream

# Enable services
systemctl enable icecast2 liquidsoap nginx
systemctl start icecast2 liquidsoap nginx

# Firewall
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP (nginx -> certbot)
ufw allow 443/tcp   # HTTPS (nginx reverse proxy for Icecast)
ufw allow 1234/tcp  # Liquidsoap telnet (restrict to Railway IP range)
ufw enable

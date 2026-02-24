#!/bin/bash
# Start Liquidsoap in background, then FastAPI

mkdir -p /tmp/hls/default

if command -v liquidsoap &>/dev/null; then
  liquidsoap /app/liquidsoap/radio.liq &
  LSOP_PID=$!

  # Wait for socket to appear (max 15s)
  for i in $(seq 1 30); do
    [ -S /tmp/liquidsoap.sock ] && break
    sleep 0.5
  done

  if [ -S /tmp/liquidsoap.sock ]; then
    echo "Liquidsoap is up (PID $LSOP_PID)"
  else
    echo "WARNING: Liquidsoap socket not found after 15s — clients will use fallback"
  fi
else
  echo "Liquidsoap not installed — clients will use client-side engine"
fi

# Start FastAPI
exec uvicorn app.main:app --host 0.0.0.0 --port $PORT --timeout-graceful-shutdown 30

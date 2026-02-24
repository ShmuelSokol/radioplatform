#!/bin/bash
# Start FastAPI (Liquidsoap runs on dedicated VPS, not as sidecar)
exec uvicorn app.main:app --host 0.0.0.0 --port $PORT --timeout-graceful-shutdown 30

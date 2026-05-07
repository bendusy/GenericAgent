#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
: "${PORT:=5678}"
: "${UPSTREAM:=https://api.anthropic.com}"
: "${DRY_RUN:=0}"

if lsof -i ":$PORT" -t >/dev/null 2>&1; then
  echo "[proxy] port $PORT busy; killing stale process"
  lsof -i ":$PORT" -t 2>/dev/null | xargs -r kill 2>/dev/null || true
  sleep 1
  if lsof -i ":$PORT" -t >/dev/null 2>&1; then
    echo "[proxy] still busy; force-killing"
    lsof -i ":$PORT" -t 2>/dev/null | xargs -r kill -9 2>/dev/null || true
    sleep 1
  fi
fi

exec python3 proxy.py

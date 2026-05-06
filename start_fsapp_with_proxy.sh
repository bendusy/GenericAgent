#!/usr/bin/env bash
# Launch claude-max-proxy in background, then fsapp in foreground.
# Ctrl+C stops fsapp; trap then stops proxy.
set -euo pipefail
cd "$(dirname "$0")"

: "${PORT:=5678}"
: "${DRY_RUN:=0}"
: "${CC_MODEL:=claude-opus-4-7}"
LOG="/tmp/claude-proxy.log"

cleanup() {
  echo
  echo "[launcher] stopping proxy on :$PORT"
  lsof -i ":$PORT" -t 2>/dev/null | xargs -r kill 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if lsof -i ":$PORT" -t >/dev/null 2>&1; then
  echo "[launcher] port $PORT already in use; aborting" >&2
  exit 1
fi

echo "[launcher] starting proxy (PORT=$PORT DRY_RUN=$DRY_RUN CC_MODEL=$CC_MODEL) -> $LOG"
PORT="$PORT" DRY_RUN="$DRY_RUN" CC_MODEL="$CC_MODEL" \
  nohup ./claude-max-proxy/start_proxy.sh > "$LOG" 2>&1 &
PROXY_PID=$!

for _ in $(seq 1 15); do
  if curl -sf "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -sf "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
  echo "[launcher] proxy failed to come up; see $LOG" >&2
  exit 1
fi

echo "[launcher] proxy up: $(curl -s http://127.0.0.1:$PORT/)"
echo "[launcher] launching fsapp (Ctrl+C to stop both)"
echo "----------------------------------------------------------------"

: "${GA_LLM_NOS:=opus-4-7,gpt,sonnet,opus-4-6}"
echo "[launcher] GA_LLM_NOS=$GA_LLM_NOS"
GA_CLAUDE_PROXY_URL="http://127.0.0.1:$PORT" GA_LLM_NOS="$GA_LLM_NOS" \
  exec python3 frontends/fsapp.py

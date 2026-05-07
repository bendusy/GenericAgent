#!/usr/bin/env bash
# Launch claude-max-proxy in background, then fsapp in foreground.
# Ctrl+C stops fsapp; trap then stops proxy.
set -euo pipefail
cd "$(dirname "$0")"

: "${PORT:=5678}"
: "${DRY_RUN:=0}"
: "${CC_MODEL:=claude-opus-4-7}"
: "${BBS_PORT:=58800}"
: "${BBS_ENABLE:=1}"
LOG="/tmp/claude-proxy.log"
BBS_LOG="/tmp/agent-bbs.log"

cleanup() {
  echo
  echo "[launcher] stopping proxy on :$PORT"
  lsof -i ":$PORT" -t 2>/dev/null | xargs -r kill 2>/dev/null || true
  if [[ "$BBS_ENABLE" = "1" ]]; then
    echo "[launcher] stopping BBS on :$BBS_PORT"
    lsof -i ":$BBS_PORT" -t 2>/dev/null | xargs -r kill 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if lsof -i ":$PORT" -t >/dev/null 2>&1; then
  echo "[launcher] port $PORT busy; killing stale proxy"
  lsof -i ":$PORT" -t 2>/dev/null | xargs -r kill 2>/dev/null || true
  sleep 1
  if lsof -i ":$PORT" -t >/dev/null 2>&1; then
    echo "[launcher] port $PORT still busy after kill; force-killing"
    lsof -i ":$PORT" -t 2>/dev/null | xargs -r kill -9 2>/dev/null || true
    sleep 1
  fi
fi

if pgrep -f "frontends/fsapp.py" >/dev/null 2>&1; then
  echo "[launcher] killing stale fsapp.py"
  pkill -f "frontends/fsapp.py" 2>/dev/null || true
  sleep 1
  pkill -9 -f "frontends/fsapp.py" 2>/dev/null || true
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

if [[ "$BBS_ENABLE" = "1" ]]; then
  if lsof -i ":$BBS_PORT" -t >/dev/null 2>&1; then
    echo "[launcher] BBS port $BBS_PORT busy; killing stale BBS"
    lsof -i ":$BBS_PORT" -t 2>/dev/null | xargs -r kill 2>/dev/null || true
    sleep 1
    lsof -i ":$BBS_PORT" -t 2>/dev/null | xargs -r kill -9 2>/dev/null || true
  fi
  echo "[launcher] starting BBS on :$BBS_PORT -> $BBS_LOG"
  ( cd assets && nohup python3 agent_bbs.py > "$BBS_LOG" 2>&1 & )
  for _ in $(seq 1 10); do
    if curl -sf -H 'X-API-Key: agent-bbs-test' "http://127.0.0.1:$BBS_PORT/posts?limit=1" >/dev/null 2>&1; then
      echo "[launcher] BBS up"
      break
    fi
    sleep 1
  done
  if ! curl -sf -H 'X-API-Key: agent-bbs-test' "http://127.0.0.1:$BBS_PORT/posts?limit=1" >/dev/null 2>&1; then
    echo "[launcher] BBS failed to come up; see $BBS_LOG (continuing without BBS)" >&2
  fi
fi

echo "[launcher] launching fsapp (Ctrl+C to stop all)"
echo "----------------------------------------------------------------"

: "${GA_LLM_NOS:=opus-4-7,gpt,sonnet,opus-4-6}"
echo "[launcher] GA_LLM_NOS=$GA_LLM_NOS"
GA_CLAUDE_PROXY_URL="http://127.0.0.1:$PORT" GA_LLM_NOS="$GA_LLM_NOS" \
  exec python3 frontends/fsapp.py

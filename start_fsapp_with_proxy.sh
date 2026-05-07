#!/usr/bin/env bash
# Launch claude-max-proxy + agent_bbs in background, then fsapp in foreground.
# On exit / Ctrl+C / SIGTERM the trap reaps all children.
set -euo pipefail
cd "$(dirname "$0")"

: "${PORT:=5678}"
: "${DRY_RUN:=0}"
: "${CC_MODEL:=claude-opus-4-7}"
: "${BBS_PORT:=58800}"
: "${BBS_ENABLE:=1}"
LOG="/tmp/claude-proxy.log"
BBS_LOG="/tmp/agent-bbs.log"

PROXY_PID=""
BBS_PID=""

# Portable kill: macOS xargs has no -r; guard manually.
kill_port() {
  local port="$1" sig="${2:-TERM}"
  local pids
  pids="$(lsof -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
  [[ -n "$pids" ]] && kill "-$sig" $pids 2>/dev/null || true
}

cleanup() {
  local rc=$?
  echo
  echo "[launcher] cleanup (rc=$rc)"
  # Prefer PID-based kill (covers nohup'd children whose port isn't yet open).
  [[ -n "$PROXY_PID" ]] && kill "$PROXY_PID" 2>/dev/null || true
  [[ -n "$BBS_PID"   ]] && kill "$BBS_PID"   2>/dev/null || true
  # Belt-and-suspenders: also clear listening sockets in case PIDs drifted.
  kill_port "$PORT"
  [[ "$BBS_ENABLE" = "1" ]] && kill_port "$BBS_PORT"
  sleep 0.3
  kill_port "$PORT" KILL
  [[ "$BBS_ENABLE" = "1" ]] && kill_port "$BBS_PORT" KILL
}
trap cleanup EXIT INT TERM

# --- preflight: clear stale listeners before starting our own ---
if lsof -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo "[launcher] port $PORT busy; killing stale proxy"
  kill_port "$PORT"; sleep 1
  kill_port "$PORT" KILL; sleep 1
fi

if pgrep -f "frontends/fsapp.py" >/dev/null 2>&1; then
  echo "[launcher] killing stale fsapp.py"
  pkill -f "frontends/fsapp.py" 2>/dev/null || true
  sleep 1
  pkill -9 -f "frontends/fsapp.py" 2>/dev/null || true
fi

# --- proxy ---
echo "[launcher] starting proxy (PORT=$PORT DRY_RUN=$DRY_RUN CC_MODEL=$CC_MODEL) -> $LOG"
PORT="$PORT" DRY_RUN="$DRY_RUN" CC_MODEL="$CC_MODEL" \
  nohup ./claude-max-proxy/start_proxy.sh > "$LOG" 2>&1 &
PROXY_PID=$!

for _ in $(seq 1 15); do
  curl -sf "http://127.0.0.1:$PORT/" >/dev/null 2>&1 && break
  sleep 1
done
if ! curl -sf "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
  echo "[launcher] proxy failed to come up; see $LOG" >&2
  exit 1
fi
echo "[launcher] proxy up: $(curl -s http://127.0.0.1:$PORT/)"

# --- BBS (optional) ---
if [[ "$BBS_ENABLE" = "1" ]]; then
  if lsof -iTCP:"$BBS_PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "[launcher] BBS port $BBS_PORT busy; killing stale BBS"
    kill_port "$BBS_PORT"; sleep 1
    kill_port "$BBS_PORT" KILL; sleep 1
  fi
  echo "[launcher] starting BBS on :$BBS_PORT -> $BBS_LOG"
  # Run python directly (no subshell) so $! is the python PID we can kill.
  (
    cd assets
    nohup python3 agent_bbs.py > "$BBS_LOG" 2>&1 &
    echo $! > /tmp/agent-bbs.pid
  )
  BBS_PID="$(cat /tmp/agent-bbs.pid 2>/dev/null || echo '')"
  rm -f /tmp/agent-bbs.pid

  bbs_up=0
  for _ in $(seq 1 10); do
    if curl -sf -H 'X-API-Key: agent-bbs-test' "http://127.0.0.1:$BBS_PORT/posts?limit=1" >/dev/null 2>&1; then
      bbs_up=1; break
    fi
    sleep 1
  done
  if [[ "$bbs_up" = "1" ]]; then
    echo "[launcher] BBS up (pid=$BBS_PID)"
  else
    echo "[launcher] BBS failed to come up; see $BBS_LOG (continuing without BBS)" >&2
    BBS_PID=""
  fi
fi

# --- fsapp (foreground; trap fires when it exits) ---
echo "[launcher] launching fsapp (Ctrl+C to stop all)"
echo "----------------------------------------------------------------"

: "${GA_LLM_NOS:=opus-4-7,gpt,sonnet,opus-4-6}"
echo "[launcher] GA_LLM_NOS=$GA_LLM_NOS"

# NOTE: no `exec` — keep bash alive so the trap can reap proxy + BBS on exit.
GA_CLAUDE_PROXY_URL="http://127.0.0.1:$PORT" GA_LLM_NOS="$GA_LLM_NOS" \
  python3 frontends/fsapp.py

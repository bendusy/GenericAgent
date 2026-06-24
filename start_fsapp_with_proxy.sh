#!/usr/bin/env bash
# Launch agent_bbs in background, then fsapp in foreground.
# claude-max-proxy is NOT started here — it is owned solely by the
# com.genericagent.claudemaxproxy launchd agent (single source, no :5678 race).
# This script only preflight-checks that the proxy is already listening.
# On exit / Ctrl+C / SIGTERM the trap reaps BBS (never the launchd-owned proxy).
set -euo pipefail
cd "$(dirname "$0")"

: "${PORT:=5678}"
: "${BBS_PORT:=58800}"
: "${BBS_ENABLE:=1}"
BBS_LOG="/tmp/agent-bbs.log"

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
  # Only reap BBS. The proxy is launchd-owned; never touch :$PORT here.
  [[ -n "$BBS_PID" ]] && kill "$BBS_PID" 2>/dev/null || true
  [[ "$BBS_ENABLE" = "1" ]] && kill_port "$BBS_PORT"
  sleep 0.3
  [[ "$BBS_ENABLE" = "1" ]] && kill_port "$BBS_PORT" KILL
}
trap cleanup EXIT INT TERM

# --- preflight: proxy must already be provided by the claudemaxproxy launchd agent ---
if ! curl -sf "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
  echo "[launcher] ERROR: claude-max-proxy not listening on :$PORT" >&2
  echo "[launcher]   start it: launchctl kickstart -k gui/\$(id -u)/com.genericagent.claudemaxproxy" >&2
  exit 1
fi
echo "[launcher] proxy up: $(curl -s http://127.0.0.1:$PORT/)"

if pgrep -f "frontends/fsapp.py" >/dev/null 2>&1; then
  echo "[launcher] killing stale fsapp.py"
  pkill -f "frontends/fsapp.py" 2>/dev/null || true
  sleep 1
  pkill -9 -f "frontends/fsapp.py" 2>/dev/null || true
fi

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
# LLM 链真源 = mykey.py mixin_config['llm_nos']（agentmain 直读）。
# 不再注入 GA_LLM_NOS / GA_CLAUDE_PROXY_URL —— fsapp 不消费，是死环变。
echo "[launcher] launching fsapp (Ctrl+C to stop all)"
echo "----------------------------------------------------------------"

# NOTE: no `exec` — keep bash alive so the trap can reap BBS on exit.
python3 frontends/fsapp.py

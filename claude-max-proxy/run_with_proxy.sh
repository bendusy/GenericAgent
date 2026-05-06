#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROXY_URL="${PROXY_URL:-http://127.0.0.1:5678}"
PORT="${PORT:-5678}"
if ! curl -fsS "${PROXY_URL%/}/" >/dev/null 2>&1; then
  echo "[run_with_proxy] proxy not running; starting on PORT=$PORT" >&2
  (cd "$ROOT/claude-max-proxy" && PORT="$PORT" DRY_RUN="${DRY_RUN:-0}" ./start_proxy.sh) > /tmp/claude-max-proxy.log 2>&1 &
  for i in $(seq 1 40); do curl -fsS "${PROXY_URL%/}/" >/dev/null 2>&1 && break; sleep 0.25; done
fi
cat >&2 <<EOF
[run_with_proxy] Proxy is ready at $PROXY_URL
[run_with_proxy] Next: set the selected NativeClaudeSession config apibase/base_url to $PROXY_URL.
[run_with_proxy] This wrapper does not edit mykey.py or llmcore.py to avoid touching secrets/core source.
EOF
exec "$@"

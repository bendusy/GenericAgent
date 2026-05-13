#!/usr/bin/env bash
# CC / Codex Stop hook 共用脚本：从 stdin 读 hook JSON，调 python 写飞书 Task。
# 详见 feishu_hub/README.md "M3.B" 节 + feishu_hub/stop_hook.py。
set -u

HOOK_JSON="$(cat || true)"

extract() {
  local key="$1"
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$HOOK_JSON" | jq -r --arg k "$key" '.[$k] // empty' 2>/dev/null
  fi
}

SUMMARY="$(extract last_assistant_message | head -c 200)"
CWD="$(extract cwd)"
SESSION="$(extract session_id)"

[ -z "${CWD:-}" ] && CWD="$PWD"

AGENT="${FEISHU_HUB_AGENT:-unknown}"
TARGET="${FEISHU_NOTIFY_TO:-}"
[ -z "$TARGET" ] && exit 0

# 调 python 入口；任何错误吞掉不阻塞 agent
python3 -m feishu_hub.stop_hook \
  --agent "$AGENT" \
  --session "${SESSION:-no-session}" \
  --cwd "$CWD" \
  --summary "${SUMMARY:-}" \
  --assignee-open-id "$TARGET" \
  >/dev/null 2>&1 || true

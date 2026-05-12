#!/usr/bin/env bash
# CC / Codex Stop hook 共用脚本：从 stdin 读 hook JSON，发飞书 IM。
# 详见 docs/FEISHU_OFFICE_HUB_DESIGN_V2.md §7.0。
set -u

HOOK_JSON="$(cat || true)"

# 字段提取（jq 不存在时静默退化，仍发通知但 summary 为空）
extract() {
  local key="$1"
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$HOOK_JSON" | jq -r --arg k "$key" '.[$k] // empty' 2>/dev/null
  fi
}

SUMMARY="$(extract last_assistant_message | head -c 200)"
CWD="$(extract cwd)"
SESSION="$(extract session_id)"
TURN="$(extract turn_id)"

[ -z "${CWD:-}" ] && CWD="$PWD"

AGENT="${FEISHU_HUB_AGENT:-unknown}"
TARGET="${FEISHU_NOTIFY_TO:-}"
[ -z "$TARGET" ] && exit 0

TEXT="[${AGENT}] 任务完成 @ $(basename "$CWD")"
[ -n "${SUMMARY:-}" ] && TEXT="${TEXT} — ${SUMMARY}"

KEY="${AGENT}-stop-${SESSION:-na}-${TURN:-$(date +%s)}"

FEISHU_HUB_AGENT="$AGENT" \
FEISHU_HUB_SESSION="${SESSION:-}" \
FEISHU_HUB_TURN="${TURN:-}" \
FEISHU_HUB_TAGS="task_done" \
  lark-cli im +messages-send \
    --user-id "$TARGET" \
    --text "$TEXT" \
    --idempotency-key "$KEY" \
  >/dev/null 2>&1 || true

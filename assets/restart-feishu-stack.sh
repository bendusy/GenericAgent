#!/usr/bin/env bash
# 一键重启飞书办公中台栈：fsapp + dispatcher + daily_report。
# 按依赖序：先 dispatcher（journal 消费者）→ daily → 最后 fsapp（journal 生产者）。
set -u

UID_=${UID:-$(id -u)}
PLISTS=(
  com.genericagent.feishu_dispatcher
  com.genericagent.feishu_daily
  com.genericagent.fsapp
)

echo "==> restarting feishu stack"
for label in "${PLISTS[@]}"; do
  if launchctl print "gui/$UID_/$label" >/dev/null 2>&1; then
    printf '  • kickstart %-42s ' "$label"
    launchctl kickstart -k "gui/$UID_/$label" >/dev/null 2>&1 && echo "ok" || echo "failed"
  else
    printf '  • %-42s SKIP (not installed)\n' "$label"
  fi
done

sleep 3
echo "==> current state"
for label in "${PLISTS[@]}"; do
  if launchctl print "gui/$UID_/$label" >/dev/null 2>&1; then
    state=$(launchctl print "gui/$UID_/$label" 2>/dev/null | awk -F'= ' '/^\tstate = /{print $2; exit}')
    pid=$(launchctl print "gui/$UID_/$label" 2>/dev/null | awk -F'= ' '/^\tpid = /{print $2; exit}')
    printf '  • %-42s state=%-10s pid=%s\n' "$label" "${state:-?}" "${pid:--}"
  fi
done

echo "==> tail logs (Ctrl+C to stop):"
echo "  tail -f $(cd "$(dirname "$0")/.." && pwd)/temp/autostart/{fsapp,feishu_dispatcher,feishu_daily}.{out,err}.log"

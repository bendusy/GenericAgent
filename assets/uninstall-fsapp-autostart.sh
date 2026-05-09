#!/usr/bin/env bash
set -euo pipefail

case "$(uname -s)" in
  Darwin)
    TARGET_FS="$HOME/Library/LaunchAgents/com.genericagent.fsapp.plist"
    TARGET_WX="$HOME/Library/LaunchAgents/com.genericagent.wechatapp.plist"
    launchctl bootout "gui/$UID/com.genericagent.fsapp" 2>/dev/null || true
    launchctl bootout "gui/$UID/com.genericagent.wechatapp" 2>/dev/null || true
    rm -f "$TARGET_FS" "$TARGET_WX"
    echo "✅ macOS LaunchAgents removed:"
    echo "   $TARGET_FS"
    echo "   $TARGET_WX"
    ;;
  Linux)
    systemctl --user disable --now genericagent-fsapp.service genericagent-wechatapp.service 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/genericagent-fsapp.service" \
          "$HOME/.config/systemd/user/genericagent-wechatapp.service"
    systemctl --user daemon-reload || true
    echo "✅ systemd user units removed: genericagent-fsapp genericagent-wechatapp"
    ;;
  *)
    echo "❌ unsupported OS: $(uname -s)" >&2
    exit 1
    ;;
esac

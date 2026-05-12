#!/usr/bin/env bash
set -euo pipefail

case "$(uname -s)" in
  Darwin)
    TARGET_FS="$HOME/Library/LaunchAgents/com.genericagent.fsapp.plist"
    TARGET_WX="$HOME/Library/LaunchAgents/com.genericagent.wechatapp.plist"
    TARGET_FD="$HOME/Library/LaunchAgents/com.genericagent.feishu_daily.plist"
    TARGET_DSP="$HOME/Library/LaunchAgents/com.genericagent.feishu_dispatcher.plist"
    launchctl bootout "gui/$UID/com.genericagent.fsapp" 2>/dev/null || true
    launchctl bootout "gui/$UID/com.genericagent.wechatapp" 2>/dev/null || true
    launchctl bootout "gui/$UID/com.genericagent.feishu_daily" 2>/dev/null || true
    launchctl bootout "gui/$UID/com.genericagent.feishu_dispatcher" 2>/dev/null || true
    rm -f "$TARGET_FS" "$TARGET_WX" "$TARGET_FD" "$TARGET_DSP"
    echo "✅ macOS LaunchAgents removed:"
    echo "   $TARGET_FS"
    echo "   $TARGET_WX"
    echo "   $TARGET_FD"
    echo "   $TARGET_DSP"
    ;;
  Linux)
    systemctl --user disable --now genericagent-fsapp.service genericagent-wechatapp.service \
                                    genericagent-feishu_daily.timer 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/genericagent-fsapp.service" \
          "$HOME/.config/systemd/user/genericagent-wechatapp.service" \
          "$HOME/.config/systemd/user/genericagent-feishu_daily.service" \
          "$HOME/.config/systemd/user/genericagent-feishu_daily.timer"
    systemctl --user daemon-reload || true
    echo "✅ systemd user units removed: genericagent-fsapp genericagent-wechatapp genericagent-feishu_daily"
    ;;
  *)
    echo "❌ unsupported OS: $(uname -s)" >&2
    exit 1
    ;;
esac

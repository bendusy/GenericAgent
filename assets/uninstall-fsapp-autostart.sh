#!/usr/bin/env bash
set -euo pipefail

case "$(uname -s)" in
  Darwin)
    TARGET="$HOME/Library/LaunchAgents/com.genericagent.fsapp.plist"
    launchctl bootout "gui/$UID/com.genericagent.fsapp" 2>/dev/null || true
    rm -f "$TARGET"
    echo "✅ macOS LaunchAgent removed: $TARGET"
    ;;
  Linux)
    systemctl --user disable --now genericagent-fsapp.service 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/genericagent-fsapp.service"
    systemctl --user daemon-reload || true
    echo "✅ systemd user unit removed"
    ;;
  *)
    echo "❌ unsupported OS: $(uname -s)" >&2
    exit 1
    ;;
esac

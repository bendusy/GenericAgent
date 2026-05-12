#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
if [[ -n "${PYTHON_BIN:-}" ]]; then
  PYTHON="$PYTHON_BIN"
elif [[ -x "$REPO/.venv/bin/python" ]]; then
  PYTHON="$REPO/.venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi
LARK_CLI_PATH="$(command -v lark-cli || true)"
LARK_BIN_DIR="${LARK_CLI_PATH:+$(dirname "$LARK_CLI_PATH")}"
LOG_DIR="$REPO/temp/autostart"
mkdir -p "$LOG_DIR"

echo "==> repo:    $REPO"
echo "==> python:  ${PYTHON:-<not found>}"
echo "==> lark:    ${LARK_CLI_PATH:-<not found>}"
echo "==> logs:    $LOG_DIR"

# Preflight 1: python3 found
if [[ -z "$PYTHON" || ! -x "$PYTHON" ]]; then
  echo "❌ python3 not found; set PYTHON_BIN env or install python3" >&2
  exit 1
fi

# Preflight 2: required imports work from this python (mykeys + lark_oapi)
if ! "$PYTHON" -c "import sys; sys.path.insert(0,'$REPO'); from llmcore import mykeys; import lark_oapi" 2>&1; then
  echo "❌ python at $PYTHON cannot import mykeys or lark_oapi" >&2
  echo "   fix: pip install -r $REPO/requirements.txt   (or uv pip install -r ...)" >&2
  exit 1
fi

# Preflight 3: lark-cli authorized
if [[ -z "$LARK_CLI_PATH" ]]; then
  echo "❌ lark-cli not in PATH; install with 'npm i -g @larksuite/cli'" >&2
  exit 1
fi
if ! "$LARK_CLI_PATH" doctor >/dev/null 2>&1; then
  echo "❌ lark-cli not authorized; run 'lark-cli auth login' first" >&2
  exit 1
fi

render() {
  sed -e "s|{{PYTHON}}|$PYTHON|g" \
      -e "s|{{REPO}}|$REPO|g" \
      -e "s|{{LOG_DIR}}|$LOG_DIR|g" \
      -e "s|{{LARK_CLI_PATH}}|$LARK_CLI_PATH|g" \
      -e "s|{{LARK_BIN_DIR}}|$LARK_BIN_DIR|g" \
      -e "s|{{USER_NAME}}|$USER|g" \
      "$1"
}

# Preflight 4: launcher script exists and is executable (drives proxy + fsapp)
LAUNCHER="$REPO/start_fsapp_with_proxy.sh"
if [[ ! -x "$LAUNCHER" ]]; then
  echo "❌ launcher missing or not executable: $LAUNCHER" >&2
  exit 1
fi

case "$(uname -s)" in
  Darwin)
    TARGET_FS="$HOME/Library/LaunchAgents/com.genericagent.fsapp.plist"
    TARGET_WX="$HOME/Library/LaunchAgents/com.genericagent.wechatapp.plist"
    TARGET_FD="$HOME/Library/LaunchAgents/com.genericagent.feishu_daily.plist"
    TARGET_DSP="$HOME/Library/LaunchAgents/com.genericagent.feishu_dispatcher.plist"
    render "$SCRIPT_DIR/com.genericagent.fsapp.plist.tpl" > "$TARGET_FS"
    render "$SCRIPT_DIR/com.genericagent.wechatapp.plist.tpl" > "$TARGET_WX"
    render "$SCRIPT_DIR/com.genericagent.feishu_daily.plist.tpl" > "$TARGET_FD"
    render "$SCRIPT_DIR/com.genericagent.feishu_dispatcher.plist.tpl" > "$TARGET_DSP"
    launchctl bootout "gui/$UID/com.genericagent.fsapp" 2>/dev/null || true
    launchctl bootout "gui/$UID/com.genericagent.wechatapp" 2>/dev/null || true
    launchctl bootout "gui/$UID/com.genericagent.feishu_daily" 2>/dev/null || true
    launchctl bootout "gui/$UID/com.genericagent.feishu_dispatcher" 2>/dev/null || true
    launchctl bootstrap "gui/$UID" "$TARGET_FS"
    launchctl bootstrap "gui/$UID" "$TARGET_WX"
    launchctl bootstrap "gui/$UID" "$TARGET_FD"
    launchctl bootstrap "gui/$UID" "$TARGET_DSP"
    launchctl enable "gui/$UID/com.genericagent.fsapp" || true
    launchctl enable "gui/$UID/com.genericagent.wechatapp" || true
    launchctl enable "gui/$UID/com.genericagent.feishu_daily" || true
    launchctl enable "gui/$UID/com.genericagent.feishu_dispatcher" || true
    echo "✅ macOS LaunchAgents installed:"
    echo "   $TARGET_FS"
    echo "   $TARGET_WX"
    echo "   $TARGET_FD   (daily 21:00)"
    echo "   $TARGET_DSP  (dispatcher tail mode)"
    echo "   inspect: launchctl print gui/$UID/com.genericagent.fsapp"
    echo "            launchctl print gui/$UID/com.genericagent.wechatapp"
    echo "            launchctl print gui/$UID/com.genericagent.feishu_daily"
    echo "   trigger feishu daily now: launchctl kickstart -k gui/$UID/com.genericagent.feishu_daily"
    echo "   logs:    tail -f $LOG_DIR/fsapp.out.log $LOG_DIR/fsapp.err.log $LOG_DIR/wechatapp.out.log $LOG_DIR/wechatapp.err.log $LOG_DIR/feishu_daily.out.log $LOG_DIR/feishu_daily.err.log"
    ;;
  Linux)
    DIR="$HOME/.config/systemd/user"
    mkdir -p "$DIR"
    TARGET_FS="$DIR/genericagent-fsapp.service"
    TARGET_WX="$DIR/genericagent-wechatapp.service"
    TARGET_FD="$DIR/genericagent-feishu_daily.service"
    TARGET_FT="$DIR/genericagent-feishu_daily.timer"
    render "$SCRIPT_DIR/genericagent-fsapp.service.tpl" > "$TARGET_FS"
    render "$SCRIPT_DIR/genericagent-wechatapp.service.tpl" > "$TARGET_WX"
    render "$SCRIPT_DIR/genericagent-feishu_daily.service.tpl" > "$TARGET_FD"
    render "$SCRIPT_DIR/genericagent-feishu_daily.timer.tpl" > "$TARGET_FT"
    systemctl --user daemon-reload
    systemctl --user enable --now genericagent-fsapp.service genericagent-wechatapp.service \
                                   genericagent-feishu_daily.timer
    echo "✅ systemd user units installed:"
    echo "   $TARGET_FS"
    echo "   $TARGET_WX"
    echo "   $TARGET_FD + $TARGET_FT  (daily 21:00)"
    echo "   inspect: systemctl --user status genericagent-fsapp genericagent-wechatapp genericagent-feishu_daily.timer"
    echo "   logs:    tail -f $LOG_DIR/fsapp.out.log $LOG_DIR/fsapp.err.log $LOG_DIR/wechatapp.out.log $LOG_DIR/wechatapp.err.log $LOG_DIR/feishu_daily.out.log $LOG_DIR/feishu_daily.err.log"
    ;;
  *)
    echo "❌ unsupported OS: $(uname -s)" >&2
    exit 1
    ;;
esac

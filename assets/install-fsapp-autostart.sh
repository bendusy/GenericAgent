#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="${PYTHON_BIN:-$(command -v python3)}"
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
    TARGET="$HOME/Library/LaunchAgents/com.genericagent.fsapp.plist"
    render "$SCRIPT_DIR/com.genericagent.fsapp.plist.tpl" > "$TARGET"
    launchctl bootout "gui/$UID/com.genericagent.fsapp" 2>/dev/null || true
    launchctl bootstrap "gui/$UID" "$TARGET"
    launchctl enable "gui/$UID/com.genericagent.fsapp" || true
    echo "✅ macOS LaunchAgent installed: $TARGET"
    echo "   inspect: launchctl print gui/$UID/com.genericagent.fsapp"
    echo "   logs:    tail -f $LOG_DIR/fsapp.out.log $LOG_DIR/fsapp.err.log"
    ;;
  Linux)
    DIR="$HOME/.config/systemd/user"
    mkdir -p "$DIR"
    TARGET="$DIR/genericagent-fsapp.service"
    render "$SCRIPT_DIR/genericagent-fsapp.service.tpl" > "$TARGET"
    systemctl --user daemon-reload
    systemctl --user enable --now genericagent-fsapp.service
    echo "✅ systemd user unit installed: $TARGET"
    echo "   inspect: systemctl --user status genericagent-fsapp"
    echo "   logs:    tail -f $LOG_DIR/fsapp.out.log $LOG_DIR/fsapp.err.log"
    ;;
  *)
    echo "❌ unsupported OS: $(uname -s)" >&2
    exit 1
    ;;
esac

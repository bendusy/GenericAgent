#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
UV="$(command -v uv || true)"
LARK_CLI_PATH="$(command -v lark-cli || true)"
LOG_DIR="$REPO/temp/autostart"
mkdir -p "$LOG_DIR"

echo "==> repo:    $REPO"
echo "==> uv:      ${UV:-<not found>}"
echo "==> lark:    ${LARK_CLI_PATH:-<not found>}"
echo "==> logs:    $LOG_DIR"

# Preflight 1: uv installed
if [[ -z "$UV" ]]; then
  echo "❌ uv not installed: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi

# Preflight 2: sync venv (network now, not at boot)
( cd "$REPO" && "$UV" sync )
VENV_PY="$REPO/.venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
  echo "❌ uv sync did not produce $VENV_PY" >&2
  exit 1
fi

# Preflight 3: mykeys importable from venv
if ! "$VENV_PY" -c "import sys; sys.path.insert(0,'$REPO'); from llmcore import mykeys" 2>/dev/null; then
  echo "❌ mykeys.py not configured; copy mykey.py.bak_* to mykeys.py and fill in keys" >&2
  exit 1
fi

# Preflight 4: lark-cli authorized
if [[ -z "$LARK_CLI_PATH" ]]; then
  echo "❌ lark-cli not in PATH; install with 'npm i -g @larksuite/cli'" >&2
  exit 1
fi
if ! "$LARK_CLI_PATH" doctor >/dev/null 2>&1; then
  echo "❌ lark-cli not authorized; run 'lark-cli auth login' first" >&2
  exit 1
fi

render() {
  sed -e "s|{{REPO}}|$REPO|g" \
      -e "s|{{LOG_DIR}}|$LOG_DIR|g" \
      -e "s|{{LARK_CLI_PATH}}|$LARK_CLI_PATH|g" \
      "$1"
}

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

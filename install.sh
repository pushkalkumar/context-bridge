#!/usr/bin/env bash
set -euo pipefail

GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'
UPGRADE=0
UNINSTALL=0

# PyPI name is claude-context-bridge ("context-bridge" is an unrelated package).
# The git tag fallback covers the window before a PyPI release exists.
PYPI_NAME="claude-context-bridge"
GIT_PIN="git+https://github.com/pushkalkumar/context-bridge.git@v0.4.0"
PLIST="$HOME/Library/LaunchAgents/com.context-bridge.server.plist"

for arg in "$@"; do
  case "$arg" in
    --upgrade)   UPGRADE=1 ;;
    --uninstall) UNINSTALL=1 ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: install.sh [--upgrade | --uninstall]" >&2
      exit 1
      ;;
  esac
done

# Prompt that works under `curl | bash` (stdin is the script, not the terminal).
ask() {
  local prompt="$1" reply=""
  if [ -t 0 ]; then
    read -r -p "$prompt" reply
  elif [ -e /dev/tty ]; then
    read -r -p "$prompt" reply < /dev/tty
  fi
  echo "$reply"
}

if ! python3 -c "import sys; assert sys.version_info >= (3, 11)" 2>/dev/null; then
  echo "Error: Python 3.11 or higher is required." >&2
  exit 1
fi

if [ "$UNINSTALL" -eq 1 ]; then
  echo ""
  echo "Removing Context Bridge..."
  confirm=$(ask "This removes the hooks, the hook script, and ~/.context-bridge/ (your checkpoint database). Continue? [y/N]: ")
  case "$confirm" in
    [yY]|[yY][eE][sS]) ;;
    *) echo "Aborted."; exit 0 ;;
  esac
  if [ -f "$PLIST" ]; then
    launchctl unload "$PLIST" >/dev/null 2>&1 || true
    rm -f "$PLIST"
    echo "✗ Launch agent removed → $PLIST"
  fi
  if command -v context-bridge >/dev/null 2>&1; then
    context-bridge uninstall
    python3 -m pip uninstall -q -y "$PYPI_NAME" >/dev/null 2>&1 || true
  fi
  rm -rf "$HOME/.context-bridge"
  echo "✗ Data removed       → ~/.context-bridge/"
  echo "Done."
  exit 0
fi

install_package() {
  local flags=()
  [ "$UPGRADE" -eq 1 ] && flags+=(--upgrade)
  python3 -m pip install -q "${flags[@]+"${flags[@]}"}" "$PYPI_NAME" 2>/dev/null \
    || python3 -m pip install -q "${flags[@]+"${flags[@]}"}" "$GIT_PIN"
}

echo ""
echo "Installing Context Bridge..."
echo ""

if curl -s -m 2 http://127.0.0.1:7723/health >/dev/null 2>&1; then
  echo "Note: the backend is currently running on port 7723."
  echo "Re-installing under a live session is safe, but restart the server afterward."
  echo ""
fi

install_package

mkdir -p "$HOME/.context-bridge"
[ ! -f "$HOME/.context-bridge/.env" ] && echo "ANTHROPIC_API_KEY=" > "$HOME/.context-bridge/.env"

# Prints the wired hooks (✓ SessionStart / PostToolUse / Stop / Skill).
context-bridge install

echo ""
echo -e "${GREEN}Done.${NC}"
echo ""
echo -e "${CYAN}Start the backend:${NC}"
echo "  context-bridge"
echo ""

if [[ "$OSTYPE" == darwin* ]]; then
  launchd_choice=$(ask "Auto-start the backend on login? (macOS only) [y/N]: ")
  case "$launchd_choice" in
    [yY]|[yY][eE][sS])
      mkdir -p "$HOME/Library/LaunchAgents"
      cat > "$PLIST" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.context-bridge.server</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>context-bridge</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/tmp/context-bridge-server.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/context-bridge-server.err</string>
</dict>
</plist>
EOF
      launchctl load "$PLIST" >/dev/null 2>&1 || true
      echo "Launch agent installed at $PLIST"
      echo "Unload it with: launchctl unload $PLIST"
      ;;
    *) ;;
  esac
fi

echo ""
echo -e "${CYAN}Add AI planning (optional):${NC}"
echo "  echo 'ANTHROPIC_API_KEY=sk-ant-...' >> ~/.context-bridge/.env"
echo "  echo 'OLLAMA_HOST=http://localhost:11434' >> ~/.context-bridge/.env"
echo ""

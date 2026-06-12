#!/usr/bin/env bash
set -euo pipefail

GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'
UPGRADE=0
UNINSTALL=0

for arg in "$@"; do
  case "$arg" in
    --upgrade)
      UPGRADE=1
      ;;
    --uninstall)
      UNINSTALL=1
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

if ! python3 -c "import sys; assert sys.version_info >= (3, 11)" 2>/dev/null; then
  echo "Error: Python 3.11 or higher is required." >&2
  exit 1
fi

if [ "$UNINSTALL" -eq 1 ]; then
  echo ""
  echo "Removing Context Bridge hooks and data..."
  echo ""
  read -r -p "This will remove hooks, the hook script, and ~/.context-bridge/. Continue? [y/N]: " confirm
  case "$confirm" in
    [yY]|[yY][eE][sS])
      ;;
    *)
      echo "Aborted."
      exit 0
      ;;
  esac
  if command -v context-bridge >/dev/null 2>&1; then
    context-bridge uninstall
  else
    python3 -m pip install -q "context-bridge" >/dev/null 2>&1 || python3 -m pip install -q "git+https://github.com/pushkalkumar/context-bridge.git@v0.3.0" >/dev/null 2>&1
    context-bridge uninstall
  fi
  rm -rf "$HOME/.context-bridge"
  echo "Removed ~/.context-bridge/"
  exit 0
fi

install_package() {
  if [ "$UPGRADE" -eq 1 ]; then
    python3 -m pip install -q --upgrade "context-bridge" || python3 -m pip install -q --upgrade "git+https://github.com/pushkalkumar/context-bridge.git@v0.3.0"
  else
    python3 -m pip install -q "context-bridge" || python3 -m pip install -q "git+https://github.com/pushkalkumar/context-bridge.git@v0.3.0"
  fi
}

echo ""
echo "Installing Context Bridge..."
echo ""
install_package

mkdir -p "$HOME/.context-bridge"
[ ! -f "$HOME/.context-bridge/.env" ] && echo "ANTHROPIC_API_KEY=" > "$HOME/.context-bridge/.env"

context-bridge install

echo "✓ SessionStart hook  → ~/.claude/context-bridge-hook.py"
echo "✓ PostToolUse hook   → ~/.claude/context-bridge-hook.py"
echo "✓ Stop hook          → ~/.claude/context-bridge-hook.py"
echo "✓ Skill imported     → CLAUDE.md ← context-bridge.md"
echo ""
echo -e "${GREEN}Done.${NC}"
echo ""
echo -e "${CYAN}Start the backend:${NC}"
echo "  context-bridge"
echo ""

if [[ "$OSTYPE" == darwin* ]]; then
  read -r -p "Auto-start the backend on login? (macOS only) [y/N]: " launchd_choice
  case "$launchd_choice" in
    [yY]|[yY][eE][sS])
      mkdir -p "$HOME/Library/LaunchAgents"
      cat > "$HOME/Library/LaunchAgents/com.context-bridge.server.plist" <<'EOF'
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
      launchctl load "$HOME/Library/LaunchAgents/com.context-bridge.server.plist" >/dev/null 2>&1 || true
      echo "Launch agent installed at $HOME/Library/LaunchAgents/com.context-bridge.server.plist"
      echo "Unload it with: launchctl unload $HOME/Library/LaunchAgents/com.context-bridge.server.plist"
      ;;
    *)
      ;;
  esac
fi

echo ""
echo -e "${CYAN}Add AI planning (optional):${NC}"
echo "  echo 'ANTHROPIC_API_KEY=sk-ant-...' >> ~/.context-bridge/.env"
echo "  echo 'OLLAMA_HOST=http://localhost:11434' >> ~/.context-bridge/.env"
echo ""

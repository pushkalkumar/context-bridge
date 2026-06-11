#!/usr/bin/env bash
set -e

GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

echo ""
echo "Installing Context Bridge..."
echo ""

if ! python3 -c "import sys; assert sys.version_info >= (3, 11)" 2>/dev/null; then
    echo "Error: Python 3.11 or higher is required."
    exit 1
fi

pip install -q "git+https://github.com/pushkal-kumar/context-bridge.git"

mkdir -p "$HOME/.context-bridge"
[ ! -f "$HOME/.context-bridge/.env" ] && echo "ANTHROPIC_API_KEY=" > "$HOME/.context-bridge/.env"

context-bridge install

echo ""
echo -e "${GREEN}Done.${NC}"
echo ""
echo -e "${CYAN}Start the backend:${NC}"
echo "  context-bridge"
echo ""
echo -e "${CYAN}Add AI planning (optional):${NC}"
echo "  echo 'ANTHROPIC_API_KEY=sk-ant-...' >> ~/.context-bridge/.env"
echo "  echo 'OLLAMA_HOST=http://localhost:11434' >> ~/.context-bridge/.env"
echo ""

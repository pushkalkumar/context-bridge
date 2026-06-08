#!/usr/bin/env bash
set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo ""
echo "Installing Context Bridge..."
echo ""

# Require Python 3.9+
if ! python3 -c "import sys; assert sys.version_info >= (3, 9)" 2>/dev/null; then
    echo "Error: Python 3.9 or higher is required."
    exit 1
fi

# Install the package from GitHub
pip install -q "git+https://github.com/pushkal-kumar/context-bridge.git"

# Set up config directory
CONFIG_DIR="$HOME/.context-bridge"
mkdir -p "$CONFIG_DIR"

# Create .env if it doesn't exist
if [ ! -f "$CONFIG_DIR/.env" ]; then
    echo "ANTHROPIC_API_KEY=" > "$CONFIG_DIR/.env"
fi

# Download the Claude Code skill
SKILL_DIR="$HOME/.claude"
mkdir -p "$SKILL_DIR"
SKILL_DEST="$SKILL_DIR/context-bridge.md"
SKILL_URL="https://raw.githubusercontent.com/pushkal-kumar/context-bridge/main/skill/CLAUDE.md"

if curl -fsSL "$SKILL_URL" -o "$SKILL_DEST" 2>/dev/null; then
    echo -e "${GREEN}Skill installed:${NC} $SKILL_DEST"
else
    echo -e "${YELLOW}Could not download skill automatically. Find it at: skill/CLAUDE.md in the repo.${NC}"
fi

echo ""
echo -e "${GREEN}Done! Context Bridge is installed.${NC}"
echo ""
echo -e "${CYAN}Start the backend:${NC}"
echo "  context-bridge"
echo ""
echo -e "${CYAN}Add your API key (optional — works without one):${NC}"
echo "  echo 'ANTHROPIC_API_KEY=sk-ant-...' > $CONFIG_DIR/.env"
echo ""
echo -e "${CYAN}Free alternative — install Ollama for local AI planning:${NC}"
echo "  https://ollama.ai"
echo ""
echo -e "${CYAN}Add the skill to your project's CLAUDE.md:${NC}"
echo "  # Add this line to your project's CLAUDE.md:"
echo "  # @$SKILL_DEST"
echo ""

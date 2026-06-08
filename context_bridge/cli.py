import argparse
import shutil
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

_DATA_DIR = Path.home() / ".context-bridge"
_CLAUDE_DIR = Path.home() / ".claude"
_SKILL_DEST = _CLAUDE_DIR / "context-bridge.md"
_CLAUDE_MD = _CLAUDE_DIR / "CLAUDE.md"
_SKILL_SRC = Path(__file__).parent / "skill.md"
_IMPORT_LINE = f"@{_SKILL_DEST}"


def _cmd_install():
    _CLAUDE_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Ensure skill file exists at ~/.claude/context-bridge.md
    if not _SKILL_DEST.exists():
        if _SKILL_SRC.exists():
            shutil.copy(_SKILL_SRC, _SKILL_DEST)
            print(f"  Skill copied  → {_SKILL_DEST}")
        else:
            print(f"  Warning: skill source not found at {_SKILL_SRC}")
            print(f"  Download it manually:")
            print(f"    curl -fsSL https://raw.githubusercontent.com/pushkal-kumar/context-bridge/main/skill/CLAUDE.md -o {_SKILL_DEST}")
            return
    else:
        print(f"  Skill exists  → {_SKILL_DEST}")

    # 2. Add @import to ~/.claude/CLAUDE.md if not already present
    if _CLAUDE_MD.exists():
        content = _CLAUDE_MD.read_text()
        if _IMPORT_LINE in content:
            print(f"  Already wired → {_CLAUDE_MD}")
        else:
            _CLAUDE_MD.write_text(content.rstrip() + f"\n\n{_IMPORT_LINE}\n")
            print(f"  Wired skill   → {_CLAUDE_MD}")
    else:
        _CLAUDE_MD.write_text(f"{_IMPORT_LINE}\n")
        print(f"  Created       → {_CLAUDE_MD}")

    print("")
    print("✅ Skill installed. Claude Code will now checkpoint every task.")


def _cmd_start():
    _DATA_DIR.mkdir(exist_ok=True)
    env_file = _DATA_DIR / ".env"
    if env_file.exists():
        load_dotenv(env_file)
    else:
        load_dotenv()
    print("Context Bridge running at http://127.0.0.1:8000")
    print(f"Data stored at: {_DATA_DIR}")
    print("Dashboard:      http://127.0.0.1:8000/")
    print("Press Ctrl+C to stop.\n")
    uvicorn.run("context_bridge.main:app", host="127.0.0.1", port=8000)


def main():
    parser = argparse.ArgumentParser(
        prog="context-bridge",
        description="Persistent memory for Claude Code — checkpoint-based replanning.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("install", help="Install the Claude Code skill to ~/.claude/")
    sub.add_parser("start", help="Start the backend server (default when no command given)")

    args = parser.parse_args()

    if args.command == "install":
        _cmd_install()
    else:
        _cmd_start()

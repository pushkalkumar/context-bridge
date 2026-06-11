import argparse
import json
import shutil
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

_DATA_DIR = Path.home() / ".context-bridge"
_CLAUDE_DIR = Path.home() / ".claude"
_SKILL_DEST = _CLAUDE_DIR / "context-bridge.md"
_HOOK_DEST = _CLAUDE_DIR / "context-bridge-hook.py"
_SETTINGS_PATH = _CLAUDE_DIR / "settings.json"
_CLAUDE_MD = _CLAUDE_DIR / "CLAUDE.md"
_SKILL_SRC = Path(__file__).parent / "skill.md"
_HOOK_SRC = Path(__file__).parent / "hook.py"
_IMPORT_LINE = "@{}".format(_SKILL_DEST)


def _cmd_install():
    _CLAUDE_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Skill file → ~/.claude/context-bridge.md
    if not _SKILL_DEST.exists():
        if _SKILL_SRC.exists():
            shutil.copy(_SKILL_SRC, _SKILL_DEST)
            print("  Skill copied  → {}".format(_SKILL_DEST))
        else:
            print("  Warning: skill source not found at {}".format(_SKILL_SRC))
            print("  Download it manually:")
            print(
                "    curl -fsSL https://raw.githubusercontent.com/pushkal-kumar/"
                "context-bridge/main/skill/CLAUDE.md -o {}".format(_SKILL_DEST)
            )
            return
    else:
        print("  Skill exists  → {}".format(_SKILL_DEST))

    # 2. Wire @import to ~/.claude/CLAUDE.md
    if _CLAUDE_MD.exists():
        content = _CLAUDE_MD.read_text()
        if _IMPORT_LINE in content:
            print("  Already wired → {}".format(_CLAUDE_MD))
        else:
            _CLAUDE_MD.write_text(content.rstrip() + "\n\n{}\n".format(_IMPORT_LINE))
            print("  Wired skill   → {}".format(_CLAUDE_MD))
    else:
        _CLAUDE_MD.write_text("{}\n".format(_IMPORT_LINE))
        print("  Created       → {}".format(_CLAUDE_MD))

    # 3. Hook script → ~/.claude/context-bridge-hook.py
    if _HOOK_SRC.exists():
        shutil.copy(_HOOK_SRC, _HOOK_DEST)
        _HOOK_DEST.chmod(0o755)
        print("  Hook installed → {}".format(_HOOK_DEST))
    else:
        print("  Warning: hook source not found at {}".format(_HOOK_SRC))

    # 4. Wire lifecycle hooks in ~/.claude/settings.json
    _configure_hooks()

    print("")
    print("✅ Skill installed. Claude Code will now checkpoint every task automatically.")
    print("   • SessionStart  — restores last checkpoint context before your first message")
    print("   • PostToolUse   — auto-checkpoints after every Task completion")
    print("   • PostToolUse   — polls for priority changes every 5 tool calls")


def _configure_hooks():
    hook_cmd = "python3 {}".format(_HOOK_DEST)

    if _SETTINGS_PATH.exists():
        try:
            settings = json.loads(_SETTINGS_PATH.read_text())
        except (ValueError, OSError):
            settings = {}
    else:
        settings = {}

    hooks = settings.setdefault("hooks", {})

    new_entries = {
        "SessionStart": {"hooks": [{"type": "command", "command": hook_cmd}]},
        "PostToolUse": {"matcher": "", "hooks": [{"type": "command", "command": hook_cmd}]},
    }

    changed = False
    for event, entry in new_entries.items():
        existing = hooks.get(event, [])
        already_installed = any(
            any(h.get("command") == hook_cmd for h in e.get("hooks", []))
            for e in existing
        )
        if not already_installed:
            hooks[event] = existing + [entry]
            changed = True

    if changed:
        _SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + "\n")
        print("  Hooks wired   → {}".format(_SETTINGS_PATH))
    else:
        print("  Hooks exist   → {}".format(_SETTINGS_PATH))


def _cmd_start():
    _DATA_DIR.mkdir(exist_ok=True)
    env_file = _DATA_DIR / ".env"
    if env_file.exists():
        load_dotenv(env_file)
    else:
        load_dotenv()
    print("Context Bridge running at http://127.0.0.1:8000")
    print("Data stored at: {}".format(_DATA_DIR))
    print("Dashboard:      http://127.0.0.1:8000/")
    print("Press Ctrl+C to stop.\n")
    uvicorn.run("context_bridge.main:app", host="127.0.0.1", port=8000)


def main():
    parser = argparse.ArgumentParser(
        prog="context-bridge",
        description="Persistent memory for Claude Code — checkpoint-based replanning.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser(
        "install",
        help="Install the Claude Code skill and lifecycle hooks to ~/.claude/",
    )
    sub.add_parser("start", help="Start the backend server (default when no command given)")

    args = parser.parse_args()

    if args.command == "install":
        _cmd_install()
    else:
        _cmd_start()

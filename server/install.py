import json
import os
import shutil
import tempfile
from pathlib import Path

_SKILL_SRC = Path(__file__).resolve().parent.parent / "skill" / "CLAUDE.md"
_HOOK_SRC = Path(__file__).parent / "hook.py"
_CLAUDE_DIR = Path.home() / ".claude"
_SKILL_DEST = _CLAUDE_DIR / "context-bridge.md"
_HOOK_DEST = _CLAUDE_DIR / "context-bridge-hook.py"
_SETTINGS_PATH = _CLAUDE_DIR / "settings.json"
_CLAUDE_MD = _CLAUDE_DIR / "CLAUDE.md"
_IMPORT_LINE = f"@{_SKILL_DEST}"
_COMMANDS_DIR = _CLAUDE_DIR / "commands"
_SLASH_COMMANDS: dict[str, str] = {
    "cb-why":    "Run `context-bridge why` in the terminal and show the stagnation diagnosis and velocity report for the current project.",
    "cb-replay": "Run `context-bridge replay` in the terminal and show the full chronological attempt history for the current stagnant task.",
    "cb-status": "Run `context-bridge status` in the terminal and show backend health, planner tier, velocity, and embedding status.",
    "cb-diff":   "Run `context-bridge diff` in the terminal and show what changed between the two most recent task checkpoints.",
    "cb-export": "Run `context-bridge export` in the terminal and write a CLAUDE.md-compatible Markdown snapshot of the current project's session history.",
}
_HOOK_EVENTS = ("SessionStart", "PreToolUse", "PostToolUse", "Stop")


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically: write to a sibling temp file then os.replace."""
    content = (json.dumps(data, indent=2) + "\n").encode()
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, content)
        os.close(fd)
        os.replace(tmp, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _install_slash_commands() -> None:
    _COMMANDS_DIR.mkdir(parents=True, exist_ok=True)
    for name, body in _SLASH_COMMANDS.items():
        (_COMMANDS_DIR / f"{name}.md").write_text(body + "\n")


def _uninstall_slash_commands() -> bool:
    removed = False
    for name in _SLASH_COMMANDS:
        path = _COMMANDS_DIR / f"{name}.md"
        if path.exists():
            path.unlink()
            removed = True
    return removed


def _configure_hooks() -> None:
    hook_cmd = f"python3 {_HOOK_DEST}"
    try:
        settings_data = json.loads(_SETTINGS_PATH.read_text()) if _SETTINGS_PATH.exists() else {}
    except (ValueError, OSError):
        settings_data = {}

    hooks = settings_data.setdefault("hooks", {})
    entries = {
        "SessionStart": {"hooks": [{"type": "command", "command": hook_cmd}]},
        "PreToolUse":   {"matcher": "", "hooks": [{"type": "command", "command": hook_cmd}]},
        "PostToolUse":  {"matcher": "", "hooks": [{"type": "command", "command": hook_cmd}]},
        "Stop":         {"hooks": [{"type": "command", "command": hook_cmd}]},
    }
    changed = False
    for event, entry in entries.items():
        existing = hooks.get(event, [])
        if not any(any(h.get("command") == hook_cmd for h in e.get("hooks", [])) for e in existing):
            hooks[event] = existing + [entry]
            changed = True

    if changed:
        _atomic_write_json(_SETTINGS_PATH, settings_data)


def _unconfigure_hooks() -> bool:
    hook_cmd = f"python3 {_HOOK_DEST}"
    try:
        settings_data = json.loads(_SETTINGS_PATH.read_text()) if _SETTINGS_PATH.exists() else {}
    except (ValueError, OSError):
        return False

    hooks = settings_data.get("hooks", {})
    changed = False
    for event in _HOOK_EVENTS:
        kept = [
            e for e in hooks.get(event, [])
            if not any(h.get("command") == hook_cmd for h in e.get("hooks", []))
        ]
        if kept != hooks.get(event, []):
            changed = True
            if kept:
                hooks[event] = kept
            else:
                hooks.pop(event, None)

    if changed:
        _atomic_write_json(_SETTINGS_PATH, settings_data)
    return changed


def do_install() -> None:
    _CLAUDE_DIR.mkdir(parents=True, exist_ok=True)

    if not _SKILL_SRC.exists():
        print(f"ERROR: skill source missing at {_SKILL_SRC}")
        raise SystemExit(1)
    shutil.copy(_SKILL_SRC, _SKILL_DEST)

    if _CLAUDE_MD.exists():
        content = _CLAUDE_MD.read_text()
        if _IMPORT_LINE not in content:
            _CLAUDE_MD.write_text(content.rstrip() + f"\n\n{_IMPORT_LINE}\n")
    else:
        _CLAUDE_MD.write_text(f"{_IMPORT_LINE}\n")

    if _HOOK_SRC.exists():
        shutil.copy(_HOOK_SRC, _HOOK_DEST)
        _HOOK_DEST.chmod(0o755)

    _configure_hooks()
    _install_slash_commands()

    hook_dest = str(_HOOK_DEST).replace(str(Path.home()), "~")
    commands_dest = str(_COMMANDS_DIR).replace(str(Path.home()), "~")
    print(f"✓ SessionStart hook  → {hook_dest}")
    print(f"✓ PreToolUse hook    → {hook_dest}  (cross-session stagnation warning)")
    print(f"✓ PostToolUse hook   → {hook_dest}")
    print(f"✓ Stop hook          → {hook_dest}")
    print(f"✓ Skill imported     → CLAUDE.md ← {_SKILL_DEST.name}")
    print(f"✓ Slash commands     → {commands_dest}/  (/cb-why /cb-replay /cb-status /cb-diff /cb-export)")


def do_uninstall() -> None:
    if _unconfigure_hooks():
        print(f"✗ Hooks removed      → {_SETTINGS_PATH}")
    for path, label in ((_HOOK_DEST, "Hook script removed"), (_SKILL_DEST, "Skill removed")):
        if path.exists():
            path.unlink()
            print(f"✗ {label:<18} → {path}")
    if _CLAUDE_MD.exists():
        content = _CLAUDE_MD.read_text()
        if _IMPORT_LINE in content:
            _CLAUDE_MD.write_text(
                content.replace(f"\n\n{_IMPORT_LINE}\n", "\n").replace(f"{_IMPORT_LINE}\n", "")
            )
            print(f"✗ Import removed     → {_CLAUDE_MD}")
    if _uninstall_slash_commands():
        print(f"✗ Slash commands     → {_COMMANDS_DIR}/")
    print("Done. The checkpoint database at ~/.context-bridge/ was not touched.")

import json
import os
import shutil
import tempfile
from pathlib import Path

_SKILL_SRC_DIR = Path(__file__).resolve().parent.parent / "skill"
_HOOK_SRC = Path(__file__).parent / "hook.py"
_CLAUDE_DIR = Path.home() / ".claude"
_SKILL_DEST_DIR = _CLAUDE_DIR / "skills" / "context-bridge"
_HOOK_DEST = _CLAUDE_DIR / "context-bridge-hook.py"
_SETTINGS_PATH = _CLAUDE_DIR / "settings.json"
_CLAUDE_MD = _CLAUDE_DIR / "CLAUDE.md"
_COMMANDS_DIR = _CLAUDE_DIR / "commands"
_SKILL_FILES = ("SKILL.md", "references/api.md")
# Pre-0.8.0 installs copied the skill to ~/.claude/context-bridge.md and
# imported it from CLAUDE.md — migrated away on install, cleaned on uninstall.
_LEGACY_SKILL_DEST = _CLAUDE_DIR / "context-bridge.md"
_LEGACY_IMPORT_LINE = f"@{_LEGACY_SKILL_DEST}"
_SLASH_COMMANDS: dict[str, str] = {
    "cb-why":    "Run `context-bridge why` in the terminal and show the stagnation diagnosis and velocity report for the current project.",
    "cb-replay": "Run `context-bridge replay` in the terminal and show the full chronological attempt history for the current stagnant task.",
    "cb-status": "Run `context-bridge status` in the terminal and show backend health, planner tier, velocity, and embedding status.",
    "cb-diff":   "Run `context-bridge diff` in the terminal and show what changed between the two most recent task checkpoints.",
    "cb-export": "Run `context-bridge export` in the terminal and write a CLAUDE.md-compatible Markdown snapshot of the current project's session history.",
    "cb-forget": "Run `context-bridge forget` in the terminal to delete all checkpoints for the current project, resetting stagnation state and starting fresh.",
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


def _install_skill() -> None:
    for rel in _SKILL_FILES:
        src = _SKILL_SRC_DIR / rel
        if not src.exists():
            print(f"ERROR: skill source missing at {src}")
            raise SystemExit(1)
        dest = _SKILL_DEST_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dest)


def _remove_legacy_install() -> bool:
    """Remove the pre-0.8.0 CLAUDE.md import and copied skill file."""
    removed = False
    if _LEGACY_SKILL_DEST.exists():
        _LEGACY_SKILL_DEST.unlink()
        removed = True
    if _CLAUDE_MD.exists():
        content = _CLAUDE_MD.read_text()
        if _LEGACY_IMPORT_LINE in content:
            _CLAUDE_MD.write_text(
                content.replace(f"\n\n{_LEGACY_IMPORT_LINE}\n", "\n")
                       .replace(f"{_LEGACY_IMPORT_LINE}\n", "")
                       .replace(_LEGACY_IMPORT_LINE, "")
            )
            removed = True
    return removed


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

    _install_skill()
    migrated = _remove_legacy_install()

    if _HOOK_SRC.exists():
        shutil.copy(_HOOK_SRC, _HOOK_DEST)
        _HOOK_DEST.chmod(0o755)

    _configure_hooks()
    _install_slash_commands()

    hook_dest = str(_HOOK_DEST).replace(str(Path.home()), "~")
    skill_dest = str(_SKILL_DEST_DIR).replace(str(Path.home()), "~")
    commands_dest = str(_COMMANDS_DIR).replace(str(Path.home()), "~")
    print(f"✓ SessionStart hook  → {hook_dest}")
    print(f"✓ PreToolUse hook    → {hook_dest}  (cross-session stagnation warning)")
    print(f"✓ PostToolUse hook   → {hook_dest}")
    print(f"✓ Stop hook          → {hook_dest}")
    print(f"✓ Skill installed    → {skill_dest}/  (loaded on demand, not per-session)")
    print(f"✓ Slash commands     → {commands_dest}/  (/cb-why /cb-replay /cb-status /cb-diff /cb-export /cb-forget)")
    print("✓ Backend auto-start → on next Claude Code session  (disable: CONTEXT_BRIDGE_NO_AUTOSTART=1)")
    if migrated:
        print("✓ Migrated pre-0.8.0 install: removed CLAUDE.md import and ~/.claude/context-bridge.md")


def do_uninstall() -> None:
    if _unconfigure_hooks():
        print(f"✗ Hooks removed      → {_SETTINGS_PATH}")
    if _HOOK_DEST.exists():
        _HOOK_DEST.unlink()
        print(f"✗ Hook script removed → {_HOOK_DEST}")
    if _SKILL_DEST_DIR.exists():
        shutil.rmtree(_SKILL_DEST_DIR)
        print(f"✗ Skill removed      → {_SKILL_DEST_DIR}")
    if _remove_legacy_install():
        print(f"✗ Legacy skill/import removed → {_CLAUDE_MD}")
    if _uninstall_slash_commands():
        print(f"✗ Slash commands     → {_COMMANDS_DIR}/")
    print("Done. The checkpoint database at ~/.context-bridge/ was not touched.")

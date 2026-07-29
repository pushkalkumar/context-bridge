"""Install/uninstall behavior: skill layout, hook wiring, legacy migration."""
import json

import pytest

from server import install


@pytest.fixture
def claude_dir(tmp_path, monkeypatch):
    """Redirect every install destination into a temp ~/.claude."""
    d = tmp_path / ".claude"
    monkeypatch.setattr(install, "_CLAUDE_DIR", d)
    monkeypatch.setattr(install, "_SKILL_DEST_DIR", d / "skills" / "context-bridge")
    monkeypatch.setattr(install, "_HOOK_DEST", d / "context-bridge-hook.py")
    monkeypatch.setattr(install, "_SETTINGS_PATH", d / "settings.json")
    monkeypatch.setattr(install, "_CLAUDE_MD", d / "CLAUDE.md")
    monkeypatch.setattr(install, "_COMMANDS_DIR", d / "commands")
    monkeypatch.setattr(install, "_LEGACY_SKILL_DEST", d / "context-bridge.md")
    monkeypatch.setattr(install, "_LEGACY_IMPORT_LINE", f"@{d / 'context-bridge.md'}")
    return d


def test_install_creates_skill_files(claude_dir):
    install.do_install()
    skill_dir = claude_dir / "skills" / "context-bridge"
    assert (skill_dir / "SKILL.md").exists()
    assert (skill_dir / "references" / "api.md").exists()
    content = (skill_dir / "SKILL.md").read_text()
    assert content.startswith("---")
    assert "name: context-bridge" in content


def test_install_wires_all_hook_events(claude_dir):
    install.do_install()
    settings = json.loads((claude_dir / "settings.json").read_text())
    for event in ("SessionStart", "PreToolUse", "PostToolUse", "Stop"):
        commands = [
            h["command"]
            for entry in settings["hooks"][event]
            for h in entry.get("hooks", [])
        ]
        assert any("context-bridge-hook.py" in c for c in commands)


def test_install_is_idempotent(claude_dir):
    install.do_install()
    install.do_install()
    settings = json.loads((claude_dir / "settings.json").read_text())
    for event in ("SessionStart", "PreToolUse", "PostToolUse", "Stop"):
        matching = [
            e for e in settings["hooks"][event]
            if any("context-bridge-hook.py" in h["command"] for h in e.get("hooks", []))
        ]
        assert len(matching) == 1


def test_install_writes_all_slash_commands(claude_dir):
    install.do_install()
    names = {p.stem for p in (claude_dir / "commands").glob("cb-*.md")}
    assert names == {"cb-why", "cb-replay", "cb-status", "cb-diff", "cb-export", "cb-forget"}


def test_install_does_not_touch_claude_md(claude_dir):
    """0.8.0+ never writes an import into global CLAUDE.md."""
    install.do_install()
    assert not (claude_dir / "CLAUDE.md").exists()


def test_install_migrates_legacy_layout(claude_dir):
    claude_dir.mkdir(parents=True)
    legacy_skill = claude_dir / "context-bridge.md"
    legacy_skill.write_text("# old skill\n")
    (claude_dir / "CLAUDE.md").write_text(f"# My rules\n\n@{legacy_skill}\n")

    install.do_install()

    assert not legacy_skill.exists()
    remaining = (claude_dir / "CLAUDE.md").read_text()
    assert str(legacy_skill) not in remaining
    assert "# My rules" in remaining


def test_uninstall_removes_everything_but_preserves_other_settings(claude_dir):
    claude_dir.mkdir(parents=True)
    (claude_dir / "settings.json").write_text(json.dumps({"model": "opus"}))
    install.do_install()

    install.do_uninstall()

    assert not (claude_dir / "skills" / "context-bridge").exists()
    assert not (claude_dir / "context-bridge-hook.py").exists()
    assert not list((claude_dir / "commands").glob("cb-*.md"))
    settings = json.loads((claude_dir / "settings.json").read_text())
    assert settings.get("model") == "opus"
    assert not settings.get("hooks")

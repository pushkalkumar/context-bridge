"""Backend auto-start in the SessionStart hook (server/hook.py)."""
import importlib.util
from pathlib import Path

import pytest

_HOOK_PATH = Path(__file__).resolve().parent.parent / "server" / "hook.py"


@pytest.fixture
def hook():
    spec = importlib.util.spec_from_file_location("hook_autostart_module", _HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_autostart_respects_opt_out(hook, monkeypatch):
    monkeypatch.setenv("CONTEXT_BRIDGE_NO_AUTOSTART", "1")
    monkeypatch.setattr(hook, "_find_binary", lambda: "/usr/local/bin/context-bridge")
    assert hook._autostart_backend() is False


def test_autostart_fails_without_binary(hook, monkeypatch):
    monkeypatch.delenv("CONTEXT_BRIDGE_NO_AUTOSTART", raising=False)
    monkeypatch.setattr(hook.shutil, "which", lambda name: None)
    monkeypatch.setattr(hook, "_FALLBACK_BINARY_DIRS", ())
    assert hook._autostart_backend() is False


def test_autostart_spawns_and_waits_for_health(hook, monkeypatch, tmp_path):
    monkeypatch.delenv("CONTEXT_BRIDGE_NO_AUTOSTART", raising=False)
    monkeypatch.setattr(hook, "_find_binary", lambda: "/fake/context-bridge")
    monkeypatch.setattr(hook.Path, "home", classmethod(lambda cls: tmp_path))

    spawned = {}

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            spawned["cmd"] = cmd
            spawned["kwargs"] = kwargs

    monkeypatch.setattr(hook.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(hook, "_get", lambda path, timeout=5.0: {"status": "ok"})
    monkeypatch.setattr(hook.time, "sleep", lambda s: None)

    assert hook._autostart_backend(wait_s=1.0) is True
    assert spawned["cmd"] == ["/fake/context-bridge", "start"]
    assert spawned["kwargs"]["start_new_session"] is True

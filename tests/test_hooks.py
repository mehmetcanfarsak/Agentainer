"""Tests for lib/hooks.py -- hook installation + per-type dispatch.

Targets 100 % line coverage of ``lib/hooks.py``. Agents are built through the
real config loader (tests/support.load_swarm) so they match the v2 ``Agent``
dataclass exactly, and hook commands are asserted to be absolute paths into
the repo's ``hooks/`` dir regardless of the agent's cwd.
"""

import json
import os
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

import hooks  # noqa: E402
from config import Agent  # noqa: E402
from tests.support import load_swarm  # noqa: E402


def claude_cfg(tmp_path, type_="claude", block=None):
    """Load a one-agent config and return (cfg, agent)."""
    if block is None:
        block = f"- {{name: A, command: 'true', type: {type_}}}\n"
    cfg = load_swarm(tmp_path, block)
    return cfg, cfg.get("A")


# ----------------------------------------------------------- claude pretrust


def test_pretrust_no_claude_json(tmp_path, monkeypatch):
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "home"))
    _, agent = claude_cfg(tmp_path)
    # No ~/.claude.json -> should return without touching anything.
    hooks.pretrust_claude_dir(agent)
    assert not (tmp_path / "home" / ".claude.json").exists()


def test_pretrust_bad_json(tmp_path, monkeypatch):
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "home"))
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").write_text("{not valid json")
    _, agent = claude_cfg(tmp_path)
    with mock.patch.object(hooks, "warn") as w:
        hooks.pretrust_claude_dir(agent)
    assert w.called
    # The corrupt file is left untouched (we never write over it).
    assert (home / ".claude.json").read_text() == "{not valid json"


def test_pretrust_updates_existing(tmp_path, monkeypatch):
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "home"))
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").write_text(json.dumps({"projects": {}}))
    _, agent = claude_cfg(tmp_path)
    agent.workdir.mkdir(parents=True)
    hooks.pretrust_claude_dir(agent)
    data = json.loads((home / ".claude.json").read_text())
    entry = data["projects"][str(agent.workdir)]
    assert entry["hasTrustDialogAccepted"] is True
    assert entry["projectOnboardingSeenCount"] == 1


def test_pretrust_already_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "home"))
    home = tmp_path / "home"
    home.mkdir()
    _, agent = claude_cfg(tmp_path)
    (home / ".claude.json").write_text(
        json.dumps(
            {"projects": {str(agent.workdir): {"hasTrustDialogAccepted": True}}}
        )
    )
    before = (home / ".claude.json").read_text()
    hooks.pretrust_claude_dir(agent)
    assert (home / ".claude.json").read_text() == before


def test_pretrust_write_error_is_swallowed(tmp_path, monkeypatch):
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "home"))
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").write_text(json.dumps({"projects": {}}))
    _, agent = claude_cfg(tmp_path)
    agent.workdir.mkdir(parents=True)
    with mock.patch.object(hooks, "warn") as w, mock.patch.object(
        os, "replace", side_effect=OSError("boom")
    ), mock.patch.object(Path, "unlink"):
        hooks.pretrust_claude_dir(agent)
    assert w.called


# ----------------------------------------------------------- claude hook


def test_install_claude_hook(tmp_path):
    _, agent = claude_cfg(tmp_path)
    agent.workdir.mkdir(parents=True)
    # Pre-seed a settings.json with the user's own Stop hook to ensure it is kept.
    settings = agent.workdir / ".claude"
    settings.mkdir(parents=True)
    (settings / "settings.json").write_text(
        json.dumps({"hooks": {"Stop": [{"command": "user-hook"}]}})
    )
    with mock.patch.object(hooks, "pretrust_claude_dir"):
        hooks.install_claude_hook(agent)
    data = json.loads((settings / "settings.json").read_text())
    stops = data["hooks"]["Stop"]
    assert any("user-hook" in json.dumps(h) for h in stops)
    # Our own stale entry is dropped, the new one uses an absolute path.
    cmds = [json.dumps(h) for h in stops]
    assert sum(hooks.HOOKS_DIR.name in c for c in cmds) == 1
    assert "user-hook" in cmds[0]
    assert str(hooks.HOOKS_DIR / "claude_stop.sh") in cmds[1]


def test_install_claude_hook_command_is_absolute(tmp_path):
    _, agent = claude_cfg(tmp_path)
    agent.workdir.mkdir(parents=True)
    with mock.patch.object(hooks, "pretrust_claude_dir"):
        hooks.install_claude_hook(agent)
    data = json.loads((agent.workdir / ".claude" / "settings.json").read_text())
    cmd = data["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert os.path.isabs(cmd)
    assert cmd == str(hooks.HOOKS_DIR / "claude_stop.sh")


def test_install_claude_hook_corrupt_json(tmp_path):
    _, agent = claude_cfg(tmp_path)
    agent.workdir.mkdir(parents=True)
    settings = agent.workdir / ".claude"
    settings.mkdir(parents=True)
    (settings / "settings.json").write_text("{not valid")
    with mock.patch.object(hooks, "pretrust_claude_dir"), mock.patch.object(
        hooks, "warn"
    ) as w:
        hooks.install_claude_hook(agent)
    assert w.called
    assert (settings / "settings.json").exists()


# ------------------------------------------------------------- codex hook


def test_install_codex_hook_basic(tmp_path):
    _, agent = claude_cfg(tmp_path, type_="codex")
    agent.workdir.mkdir(parents=True)
    with mock.patch.object(hooks, "valid_toml", return_value=True):
        path = hooks.install_codex_hook(agent)
    toml = (path / "config.toml").read_text()
    assert "notify" in toml and "trust_level" in toml
    # notify program is an absolute path to the repo hook script.
    assert str(hooks.HOOKS_DIR / "codex_notify.sh") in toml


def test_install_codex_hook_carries_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "home"))
    user_home = tmp_path / "home" / ".codex"
    user_home.mkdir(parents=True)
    (user_home / "auth.json").write_text('{"token": 1}')
    _, agent = claude_cfg(tmp_path, type_="codex")
    agent.workdir.mkdir(parents=True)
    with mock.patch.object(hooks, "valid_toml", return_value=True):
        path = hooks.install_codex_hook(agent)
    assert (path / "auth.json").exists()


def test_install_codex_hook_merges_user_config(tmp_path, monkeypatch):
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "home"))
    user_home = tmp_path / "home" / ".codex"
    user_home.mkdir(parents=True)
    (user_home / "config.toml").write_text('other = 1\nnotify = ["old"]\n')
    _, agent = claude_cfg(tmp_path, type_="codex")
    agent.workdir.mkdir(parents=True)
    with mock.patch.object(hooks, "valid_toml", return_value=True):
        path = hooks.install_codex_hook(agent)
    toml = (path / "config.toml").read_text()
    assert 'other = 1' in toml
    assert 'notify = ["old"]' not in toml  # user's notify is stripped


def test_install_codex_hook_symlink_falls_back_to_copy(tmp_path, monkeypatch):
    # When the user's auth.json cannot be symlinked (e.g. a cross-device link),
    # install_codex_hook must fall back to copying it.
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "home"))
    user_home = tmp_path / "home" / ".codex"
    user_home.mkdir(parents=True)
    (user_home / "auth.json").write_text('{"token": 1}')
    _, agent = claude_cfg(tmp_path, type_="codex")
    agent.workdir.mkdir(parents=True)
    with mock.patch.object(hooks.os, "symlink", side_effect=OSError("cross-device")), \
         mock.patch.object(hooks, "valid_toml", return_value=True):
        path = hooks.install_codex_hook(agent)
    dst = path / "auth.json"
    assert dst.exists()
    assert not dst.is_symlink()


def test_install_codex_hook_trust_already_present(tmp_path, monkeypatch):
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "home"))
    _, agent = claude_cfg(tmp_path, type_="codex")
    agent.workdir.mkdir(parents=True)
    user_home = tmp_path / "home" / ".codex"
    user_home.mkdir(parents=True)
    user_home.joinpath("config.toml").write_text(
        f'[projects.{json.dumps(str(agent.workdir))}]\ntrust_level = "trusted"\n'
    )
    with mock.patch.object(hooks, "valid_toml", return_value=True):
        path = hooks.install_codex_hook(agent)
    toml = (path / "config.toml").read_text()
    # The user's trust header is kept; no second trust_level is appended.
    assert toml.count("trust_level") == 1


def test_install_codex_hook_invalid_toml_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "home"))
    user_home = tmp_path / "home" / ".codex"
    user_home.mkdir(parents=True)
    user_home.joinpath("config.toml").write_text("this = is = not = valid\n")
    _, agent = claude_cfg(tmp_path, type_="codex")
    agent.workdir.mkdir(parents=True)
    with mock.patch.object(hooks, "valid_toml", return_value=False), mock.patch.object(
        hooks, "warn"
    ) as w:
        path = hooks.install_codex_hook(agent)
    assert w.called
    toml = (path / "config.toml").read_text()
    assert "trust_level" in toml


def test_install_codex_hook_no_user_home(tmp_path):
    # No ~/.codex at all -> still writes a valid notify + trust config.
    _, agent = claude_cfg(tmp_path, type_="codex")
    agent.workdir.mkdir(parents=True)
    with mock.patch.object(hooks, "valid_toml", return_value=True):
        path = hooks.install_codex_hook(agent)
    toml = (path / "config.toml").read_text()
    assert "notify" in toml and "trust_level" in toml


# ------------------------------------------------------------- valid_toml


def test_valid_toml_good():
    assert hooks.valid_toml('a = 1\n[b]\nc = "x"\n') is True


def test_valid_toml_bad():
    assert hooks.valid_toml("a = = b\n") is False


def test_valid_toml_no_tomllib(monkeypatch):
    # Simulate Python < 3.11 where tomllib is absent -> assume valid.
    real = sys.modules.pop("tomllib", None)
    sys.modules["tomllib"] = None  # import tomllib -> ImportError
    try:
        assert hooks.valid_toml("anything at all") is True
    finally:
        if real is not None:
            sys.modules["tomllib"] = real
        else:
            sys.modules.pop("tomllib", None)


# ----------------------------------------------------------- install_capture


def test_install_capture_not_hook(tmp_path):
    _, agent = claude_cfg(tmp_path, type_="gemini")
    agent.capture = "pane"
    agent.workdir.mkdir(parents=True)
    assert hooks.install_capture(agent) == {}


def test_install_capture_claude(tmp_path):
    _, agent = claude_cfg(tmp_path)
    agent.capture = "hook"
    agent.workdir.mkdir(parents=True)
    with mock.patch.object(hooks, "install_claude_hook") as h:
        env = hooks.install_capture(agent)
    assert env == {}
    assert h.called


def test_install_capture_codex(tmp_path):
    _, agent = claude_cfg(tmp_path, type_="codex")
    agent.capture = "hook"
    agent.workdir.mkdir(parents=True)
    env = hooks.install_capture(agent)
    assert "CODEX_HOME" in env


def test_install_capture_unknown_type_falls_back(tmp_path):
    _, agent = claude_cfg(tmp_path)
    agent.type = "weird"
    agent.capture = "hook"
    agent.workdir.mkdir(parents=True)
    with mock.patch.object(hooks, "warn") as w:
        env = hooks.install_capture(agent)
    assert env == {}
    assert agent.capture == "pane"
    assert w.called


# ----------------------------------------------------- install_turn_detection


def test_install_turn_detection_claude(tmp_path):
    _, agent = claude_cfg(tmp_path)
    agent.workdir.mkdir(parents=True)
    with mock.patch.object(hooks, "pretrust_claude_dir") as pt, mock.patch.object(
        hooks, "install_claude_hook"
    ) as h:
        env = hooks.install_turn_detection(agent)
    assert env == {}
    pt.assert_called_once()
    h.assert_called_once()


def test_install_turn_detection_codex(tmp_path):
    _, agent = claude_cfg(tmp_path, type_="codex")
    agent.workdir.mkdir(parents=True)
    with mock.patch.object(hooks, "install_codex_hook") as h:
        env = hooks.install_turn_detection(agent)
    assert env == {}
    h.assert_called_once()


def test_install_turn_detection_pane_type(tmp_path):
    # gemini / hermes: delegate to install_capture (pane polling).
    _, agent = claude_cfg(tmp_path, type_="gemini")
    agent.capture = "pane"
    agent.workdir.mkdir(parents=True)
    with mock.patch.object(hooks, "install_capture") as cap:
        cap.return_value = {}
        env = hooks.install_turn_detection(agent)
    cap.assert_called_once_with(agent)
    assert env == {}


def test_install_turn_detection_pane_hook_falls_back(tmp_path):
    # gemini with capture=hook is unsupported -> install_capture downgrades it.
    _, agent = claude_cfg(tmp_path, type_="gemini")
    agent.capture = "hook"
    agent.workdir.mkdir(parents=True)
    env = hooks.install_turn_detection(agent)
    assert env == {}
    assert agent.capture == "pane"


# ------------------------------------------------------------- write_shim


def test_write_shim(tmp_path):
    fake_cfg = types.SimpleNamespace(bin_dir=tmp_path / "bin")
    hooks.write_shim(fake_cfg)
    shim = fake_cfg.bin_dir / "agentainer"
    assert shim.is_file()
    assert os.access(shim, os.X_OK)
    text = shim.read_text()
    assert "agentainer send" in text
    assert str(hooks.AGENTAINER_HOME / "agentainer") in text


# ------------------------------------------------------------- constants


def test_paths_are_absolute_and_point_at_hooks():
    assert os.path.isabs(str(hooks.HOOKS_DIR))
    assert hooks.HOOKS_DIR.name == "hooks"
    assert hooks.HOOK_CAPABLE == ("claude", "codex")
    assert str(hooks.HOOKS_DIR / "claude_stop.sh") in (
        str(hooks.AGENTAINER_HOME / "hooks" / "claude_stop.sh"),
    )

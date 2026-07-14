"""100% line-coverage tests for lib/scaffold.py (the interactive swarm builder).

Everything runs under ``mock_tmux`` so no real CLI / tmux session is ever
spawned, and ``HOME`` is redirected to a temp dir so the per-type pre-trust
never touches the developer's real ``~/.claude.json`` / ``~/.codex``. The autouse
``_isolate_state_dir`` fixture keeps the registry/settings in a throwaway dir.
"""

import importlib.util
import sys
from types import SimpleNamespace

import pytest

import config as cfgmod
import registry
import scaffold
from support import load_swarm, mock_tmux


BUILDER_AGENTS = """
- name: alice
  role: hi
  can_talk_to: [user]
"""


@pytest.fixture
def cfg(tmp_path):
    return load_swarm(tmp_path, BUILDER_AGENTS, name="demo")


def _fast_paste(monkeypatch):
    """Replace the (slow, retry-looping) paste with a recorder returning True."""
    calls = []
    monkeypatch.setattr(
        scaffold.tmux, "paste_into",
        lambda cfg, session, text, **k: calls.append((session, text)) or True,
    )
    return calls


def _new_sessions(runner):
    return [c for c in runner.calls if "new-session" in c]


# --------------------------------------------------------------------------
# builder_session_name
# --------------------------------------------------------------------------


def test_builder_session_name_uses_prefix(cfg):
    assert scaffold.builder_session_name(cfg) == "t-builder"


def test_builder_session_name_falls_back_to_name():
    c = SimpleNamespace(session_prefix="", name="myswarm")
    assert scaffold.builder_session_name(c) == "myswarm_builder"


# --------------------------------------------------------------------------
# builder_prompt (both modes)
# --------------------------------------------------------------------------


def test_builder_prompt_adapt(tmp_path):
    path = tmp_path / "agentainer.yaml"
    p = scaffold.builder_prompt("adapt", path, notes="add a tester agent")
    assert "adapt" in p.lower()
    assert str(path) in p
    assert scaffold.DOCS_URL in p
    assert "add a tester agent" in p
    assert "Approve & Launch" in p


def test_builder_prompt_adapt_without_notes(tmp_path):
    p = scaffold.builder_prompt("adapt", tmp_path / "x.yaml")
    assert "What the operator wants" not in p
    assert "Approve & Launch" in p


def test_builder_prompt_scratch(tmp_path):
    path = tmp_path / "x.yaml"
    p = scaffold.builder_prompt("scratch", path, notes="a research team")
    assert "scratch" in p.lower()
    assert "claude, codex, gemini, hermes" in p
    assert str(path) in p
    assert scaffold.DOCS_URL in p
    assert "a research team" in p
    assert "Approve & Launch" in p


def test_builder_prompt_scratch_without_notes(tmp_path):
    p = scaffold.builder_prompt("scratch", tmp_path / "x.yaml")
    assert "What the operator wants" not in p


# --------------------------------------------------------------------------
# open_builder_session
# --------------------------------------------------------------------------


def test_open_builder_session_default_type_is_claude(cfg, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    pastes = _fast_paste(monkeypatch)
    with mock_tmux(has_session=False) as runner:
        session = scaffold.open_builder_session(cfg, mode="adapt", notes="hello")
    assert session == "t-builder"
    news = _new_sessions(runner)
    assert any("claude" in " ".join(c) for c in news)  # builtin default command
    # the onboarding prompt was pasted into the builder session
    assert pastes and pastes[0][0] == "t-builder"
    assert scaffold.DOCS_URL in pastes[0][1]


def test_open_builder_session_idempotent_when_running(cfg, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    pastes = _fast_paste(monkeypatch)
    with mock_tmux(has_session=True) as runner:
        session = scaffold.open_builder_session(cfg, agent_type="claude")
    assert session == "t-builder"
    assert _new_sessions(runner) == []  # nothing spawned
    assert pastes == []  # no prompt re-pasted


def test_open_builder_session_empty_command_rejected(cfg, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    with mock_tmux(has_session=False):
        with pytest.raises(ValueError):
            scaffold.open_builder_session(cfg, agent_type="claude", agent_command="   ")


def test_open_builder_session_unknown_type_rejected(cfg, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    with mock_tmux(has_session=False):
        with pytest.raises(ValueError):
            scaffold.open_builder_session(cfg, agent_type="bogus")


def test_open_builder_session_explicit_command(cfg, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _fast_paste(monkeypatch)
    with mock_tmux(has_session=False) as runner:
        scaffold.open_builder_session(cfg, agent_type="claude", agent_command="mycli --alias")
    assert any("mycli" in " ".join(c) for c in _new_sessions(runner))


def test_open_builder_session_claude_pretrust(cfg, monkeypatch, tmp_path):
    # A ~/.claude.json present -> pretrust_claude_dir records the trusted workdir.
    import json
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").write_text(json.dumps({"projects": {}}))
    monkeypatch.setenv("HOME", str(home))
    _fast_paste(monkeypatch)
    with mock_tmux(has_session=False):
        scaffold.open_builder_session(cfg, agent_type="claude")
    data = json.loads((home / ".claude.json").read_text())
    assert str(cfg.path.parent) in data["projects"]


def test_open_builder_session_codex_pretrust(cfg, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _fast_paste(monkeypatch)
    with mock_tmux(has_session=False):
        scaffold.open_builder_session(cfg, agent_type="codex")
    # codex pre-trust writes a private CODEX_HOME config under the workdir.
    assert (cfg.path.parent / ".codex" / "config.toml").is_file()


def test_open_builder_session_skip_type_has_no_pretrust(cfg, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _fast_paste(monkeypatch)
    with mock_tmux(has_session=False) as runner:
        session = scaffold.open_builder_session(cfg, agent_type="gemini")
    assert session == "t-builder"
    assert not (cfg.path.parent / ".codex").exists()  # gemini has no trust modal
    assert any("gemini" in " ".join(c) for c in _new_sessions(runner))


def test_open_builder_session_no_holder(tmp_path, monkeypatch):
    # history-limit 0 + mouse off -> configure_tmux returns no holder to tear down.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    cfg = load_swarm(
        tmp_path, BUILDER_AGENTS, name="noh", tmux_history_limit=0, tmux_mouse="false"
    )
    _fast_paste(monkeypatch)
    with mock_tmux(has_session=False) as runner:
        session = scaffold.open_builder_session(cfg, agent_type="claude")
    assert session == "t-builder"  # uses the swarm's session_prefix
    assert _new_sessions(runner)  # the builder session was still created


# --------------------------------------------------------------------------
# close_builder_session
# --------------------------------------------------------------------------


def test_close_builder_session_running(cfg):
    with mock_tmux(has_session=True):
        assert scaffold.close_builder_session(cfg) is True


def test_close_builder_session_not_running(cfg):
    with mock_tmux(has_session=False):
        assert scaffold.close_builder_session(cfg) is False


# --------------------------------------------------------------------------
# approve_swarm
# --------------------------------------------------------------------------


def test_approve_swarm_valid_calls_up(monkeypatch, tmp_path):
    registry.create_swarm(
        "appr",
        raw={
            "defaults": {"type": "claude"},
            "agents": [{"name": "w1", "command": "true", "can_talk_to": []}],
        },
    )
    import cli

    monkeypatch.setattr(cli, "up_config", lambda cfg: [SimpleNamespace(name="w1")])
    with mock_tmux(has_session=True):  # also exercises close_builder (running)
        result = scaffold.approve_swarm("appr")
    assert result == {"ok": True, "started": ["w1"]}


def test_approve_swarm_valid_real_up_empty(tmp_path):
    registry.create_swarm("appr2")  # empty swarm -> up_config starts nothing
    with mock_tmux(has_session=False):  # close_builder (not running) branch
        result = scaffold.approve_swarm("appr2")
    assert result["ok"] is True
    assert result["started"] == []


def test_approve_swarm_no_up(tmp_path):
    registry.create_swarm("appr3")
    with mock_tmux(has_session=True):
        result = scaffold.approve_swarm("appr3", do_up=False)
    assert result == {"ok": True, "started": []}


def test_approve_swarm_unknown(tmp_path):
    result = scaffold.approve_swarm("ghost")
    assert result["ok"] is False
    assert "unknown swarm" in result["error"]


def test_approve_swarm_invalid_config(tmp_path):
    bad = tmp_path / "bad" / "agentainer.yaml"
    bad.parent.mkdir(parents=True)
    bad.write_text(
        "swarm: {name: bad, root: ./ws}\n"
        "defaults: {type: claude}\n"
        "agents:\n  - {name: a, command: 'true', can_talk_to: [ghost]}\n"
    )
    registry.register("badsw", bad)
    result = scaffold.approve_swarm("badsw")
    assert result["ok"] is False
    assert "error" in result


# --------------------------------------------------------------------------
# import-time sys.path guard
# --------------------------------------------------------------------------


def test_scaffold_inserts_lib_path_when_missing():
    saved = sys.path[:]
    original = sys.modules.get("scaffold")
    sys.path = [p for p in sys.path if p != str(scaffold._LIB)]
    sys.modules.pop("scaffold", None)
    try:
        spec = importlib.util.spec_from_file_location(
            "scaffold", str(scaffold._LIB / "scaffold.py")
        )
        fresh = importlib.util.module_from_spec(spec)
        sys.modules["scaffold"] = fresh
        spec.loader.exec_module(fresh)
        assert str(scaffold._LIB) in sys.path
    finally:
        sys.path[:] = saved
        if original is not None:
            sys.modules["scaffold"] = original
        else:
            sys.modules.pop("scaffold", None)

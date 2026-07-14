"""100% line-coverage tests for lib/cli.py.

The CLI is a thin shell over the tested core modules, so every handler here is
exercised against mocked tmux / supervisor / clock -- no real sessions, no API
keys. The only core module that may be absent in this checkout is ``supervisor``;
it is imported lazily and degraded gracefully, and both code paths are covered.
"""

import io
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

import cli
from config import ConfigError
from tmux import SwarmError
from support import mock_tmux, load_swarm


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def build(tmp_path, agents_block, **over):
    return load_swarm(tmp_path, agents_block, **over)


GENERAL_AGENTS = """
  - name: orchestrator
    type: claude
    capture: none
    can_talk_to: "*"
    role: "You are the orchestrator."
  - name: worker
    type: gemini
    capture: none
    can_talk_to: [orchestrator, user]
    role: ""
"""

RESUME_AGENTS = """
  - name: claude1
    type: claude
    capture: none
    can_talk_to: [codex1]
    role: "c1"
  - name: codex1
    type: codex
    can_talk_to: [claude1]
    role: "x1"
  - name: gemini1
    type: gemini
    capture: pane
    can_talk_to: [claude1]
    role: "g1"
  - name: gemini2
    type: gemini
    capture: pane
    can_talk_to: [claude1]
    role: "g2"
  - name: silent1
    type: hermes
    capture: none
    can_talk_to: [claude1]
    role: ""
"""


def patch_launch(monkeypatch, n_agents=2, wait=True, paste=True, always=False):
    """Neutralise the slow / side-effecting launch bits used by cmd_up.

    session_exists returns False for the first *n_agents* calls (so the start-of-
    loop "already running?" check lets each agent launch) and True afterwards;
    with wait/paste replaced, it is never consulted again. ``always=True`` makes
    every session look alive (used by the restart path).
    """
    monkeypatch.setattr(cli.hooks, "install_turn_detection", lambda agent: {})
    monkeypatch.setattr(cli.tmux, "wait_until_ready", lambda cfg, agent: wait)
    monkeypatch.setattr(cli.tmux, "paste_into", lambda *a, **k: paste)
    monkeypatch.setattr(cli.time, "sleep", lambda *a, **k: None)
    state = {"n": n_agents, "seen": 0}

    def session_exists(s):
        if always:
            return True
        if state["seen"] < state["n"]:
            state["seen"] += 1
            return False
        return True

    monkeypatch.setattr(cli.tmux, "session_exists", session_exists)


# --------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------


def test_read_version_real():
    assert cli.read_version() == "2.1.0"


def test_read_version_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "AGENTAINER_HOME", tmp_path)
    assert cli.read_version() == "unknown"


def test_default_config_env(monkeypatch, tmp_path):
    p = tmp_path / "explicit.yaml"
    p.write_text("x: 1")
    monkeypatch.setenv("AGENTAINER_CONFIG", str(p))
    assert cli.default_config() == str(p)


def test_default_config_local(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENTAINER_CONFIG", raising=False)
    local = tmp_path / "agentainer.yaml"
    local.write_text("x: 1")
    monkeypatch.chdir(tmp_path)
    assert cli.default_config() == str(local)


def test_default_config_home(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENTAINER_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    assert cli.default_config() == str(cli.AGENTAINER_HOME / "agentainer.yaml")


def test_config_from_state_found(monkeypatch, tmp_path):
    (tmp_path / ".agentainer").mkdir()
    (tmp_path / ".agentainer" / "state.json").write_text(json.dumps({"config": "/x/y.yaml"}))
    monkeypatch.chdir(tmp_path)
    assert cli.config_from_state() == "/x/y.yaml"


def test_config_from_state_missing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert cli.config_from_state() is None


def test_config_from_state_corrupt(monkeypatch, tmp_path):
    (tmp_path / ".agentainer").mkdir()
    (tmp_path / ".agentainer" / "state.json").write_text("not json {")
    monkeypatch.chdir(tmp_path)
    assert cli.config_from_state() is None


def test_agent_from_cwd_match(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    agent = cfg.get("orchestrator")
    agent.workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(agent.workdir)
    assert cli.agent_from_cwd(cfg) == "orchestrator"


def test_agent_from_cwd_no_match(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    monkeypatch.chdir(tmp_path)
    assert cli.agent_from_cwd(cfg) is None


def test_discover_context_env(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    monkeypatch.setenv("AGENTAINER_CONFIG", str(cfg.path))
    monkeypatch.setenv("AGENTAINER_AGENT", "orchestrator")
    got_cfg, agent = cli.discover_context(None, None)
    assert got_cfg.path == cfg.path
    assert agent.name == "orchestrator"


def test_discover_context_cwd(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    agent = cfg.get("worker")
    agent.workdir.mkdir(parents=True, exist_ok=True)
    (agent.workdir / ".agentainer").mkdir()
    (agent.workdir / ".agentainer" / "state.json").write_text(
        json.dumps({"config": str(cfg.path)})
    )
    monkeypatch.chdir(agent.workdir)
    got_cfg, found = cli.discover_context(None, None)
    assert got_cfg.path == cfg.path
    assert found.name == "worker"


def test_discover_context_failure(tmp_path):
    with pytest.raises(ConfigError):
        cli.discover_context("/does/not/exist.yaml", None)


def test_discover_context_bad_env_config(monkeypatch, tmp_path):
    # A corrupt AGENTAINER_CONFIG is skipped (ConfigError -> continue); resolution
    # falls through to config_from_state, which points at the real config.
    cfg = build(tmp_path, GENERAL_AGENTS)
    bad = tmp_path / "bad.yaml"
    bad.write_text("not a mapping")
    agent = cfg.get("worker")
    agent.workdir.mkdir(parents=True, exist_ok=True)
    (agent.workdir / ".agentainer").mkdir()
    (agent.workdir / ".agentainer" / "state.json").write_text(json.dumps({"config": str(cfg.path)}))
    monkeypatch.setenv("AGENTAINER_CONFIG", str(bad))
    monkeypatch.chdir(agent.workdir)
    got_cfg, found = cli.discover_context(None, None)
    assert got_cfg.path == cfg.path
    assert found.name == "worker"


def test_select_agents_all(tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    assert cli.select_agents(cfg, None) == cfg.agents


def test_select_agents_subset(tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    assert [a.name for a in cli.select_agents(cfg, "orchestrator,worker")] == [
        "orchestrator",
        "worker",
    ]


def test_select_agents_unknown(tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    with pytest.raises(ConfigError):
        cli.select_agents(cfg, "nope")


def test_read_message_args():
    args = mock.Mock(file=None, message=["hello", "world"])
    assert cli.read_message(args) == "hello world"


def test_read_message_file(tmp_path):
    f = tmp_path / "body.txt"
    f.write_text("from file")
    args = mock.Mock(file=str(f), message=None)
    assert cli.read_message(args) == "from file"


def test_read_message_stdin():
    args = mock.Mock(file=None, message=["-"])
    with mock.patch.object(cli.sys, "stdin", io.StringIO("piped in")):
        assert cli.read_message(args) == "piped in"


def test_read_message_no_tty_die():
    args = mock.Mock(file=None, message=None)
    with mock.patch.object(cli.sys.stdin, "isatty", lambda: True):
        with pytest.raises(SystemExit):
            cli.read_message(args)


# --------------------------------------------------------------------------
# validate
# --------------------------------------------------------------------------


def test_validate_happy(capsys, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    assert cli.main(["validate", "-c", str(cfg.path)]) == 0
    out = capsys.readouterr().out
    assert "config ok" in out
    assert "orchestrator" in out
    assert str(cfg.root) in out


def test_validate_show_prompts(capsys, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    cli.main(["validate", "-c", str(cfg.path), "--show-prompts"])
    out = capsys.readouterr().out
    assert "You are the orchestrator." in out


def test_validate_missing_config(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["validate", "-c", "/nope/agentainer.yaml"])


def test_validate_workdir_exists(capsys, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    cfg.get("orchestrator").workdir.mkdir(parents=True, exist_ok=True)
    assert cli.main(["validate", "-c", str(cfg.path)]) == 0
    assert "exists" in capsys.readouterr().out


def test_start_agent_creates_workdir(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    agent = cfg.get("orchestrator")
    assert not agent.workdir.exists()
    with mock_tmux(has_session=False):
        cli.start_agent(cfg, agent)
    assert agent.workdir.is_dir()


# --------------------------------------------------------------------------
# up / down / restart
# --------------------------------------------------------------------------


def test_up_happy(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    sup = mock.MagicMock()
    monkeypatch.setitem(sys.modules, "supervisor", sup)
    patch_launch(monkeypatch, n_agents=2)
    with mock_tmux(has_session=False):
        assert cli.main(["up", "-c", str(cfg.path)]) == 0
    sup.start_supervisor.assert_called_once()


def test_up_supervisor_absent(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    monkeypatch.setattr(cli, "_import_supervisor", lambda: (_ for _ in ()).throw(ImportError("nope")))
    patch_launch(monkeypatch, n_agents=2)
    with mock_tmux(has_session=False):
        # ImportError is caught -> warn, return 0 (no supervisor available).
        assert cli.main(["up", "-c", str(cfg.path)]) == 0


def test_up_prints_serve_hint(monkeypatch, tmp_path, capsys):
    """After `up`, the operator should see the exact serve command to launch the UI."""
    cfg = build(tmp_path, GENERAL_AGENTS)
    patch_launch(monkeypatch, n_agents=2)
    with mock_tmux(has_session=False):
        assert cli.main(["up", "-c", str(cfg.path), "--no-supervise"]) == 0
    err = capsys.readouterr().err
    assert "agentainer serve --host 0.0.0.0" in err
    assert str(cfg.path) in err
    assert "--token" in err
    assert "--port 8000" in err


def test_up_first_prompt_is_standby(monkeypatch, tmp_path):
    """The first prompt pasted at `up` must be the standby notice, not the raw role."""
    cfg = build(tmp_path, GENERAL_AGENTS)
    monkeypatch.setattr(cli.hooks, "install_turn_detection", lambda agent: {})
    monkeypatch.setattr(cli.tmux, "wait_until_ready", lambda c, a: True)
    monkeypatch.setattr(cli.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(cli.tmux, "session_exists", lambda s: False)
    pasted = []
    monkeypatch.setattr(
        cli.tmux, "paste_into", lambda cfg, session, text: pasted.append(text) or True
    )
    with mock_tmux(has_session=False):
        assert cli.main(["up", "-c", str(cfg.path), "--no-supervise"]) == 0
    # two agents launched -> two prompts pasted.
    assert len(pasted) == 2
    for text in pasted:
        assert "initialization message" in text
        assert "do NOT send" in text
    # The orchestrator's role is still delivered, wrapped by the standby notice.
    assert any("You are the orchestrator." in t for t in pasted)
    # The role-less worker still gets the standby (with its mailbox paths), not nothing.
    assert any("standing role has not been set" in t for t in pasted)


def test_up_nothing_to_start(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    patch_launch(monkeypatch, always=True)
    with mock_tmux(has_session=True):
        # every selected agent already has a session and --restart is off.
        assert cli.main(["up", "-c", str(cfg.path), "--only", "orchestrator"]) == 0


def test_up_no_tmux(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit):
        cli.main(["up", "-c", str(cfg.path)])


def test_up_unknown_agent(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/tmux")
    with pytest.raises(SystemExit):
        cli.main(["up", "-c", str(cfg.path), "--only", "ghost"])


def test_up_wait_false(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    patch_launch(monkeypatch, n_agents=2, wait=False)
    with mock_tmux(has_session=False):
        assert cli.main(["up", "-c", str(cfg.path)]) == 0


def test_up_paste_false(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    patch_launch(monkeypatch, n_agents=2, paste=False)
    with mock_tmux(has_session=False):
        assert cli.main(["up", "-c", str(cfg.path)]) == 0


def test_up_paste_raises(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    monkeypatch.setattr(cli.hooks, "install_turn_detection", lambda agent: {})
    monkeypatch.setattr(cli.tmux, "wait_until_ready", lambda cfg, agent: True)
    monkeypatch.setattr(
        cli.tmux, "paste_into", lambda *a, **k: (_ for _ in ()).throw(SwarmError("x"))
    )
    monkeypatch.setattr(cli.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(cli.tmux, "session_exists", lambda s: False)
    with mock_tmux(has_session=False):
        assert cli.main(["up", "-c", str(cfg.path)]) == 0


def test_up_called_process_error(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    monkeypatch.setattr(cli.hooks, "install_turn_detection", lambda agent: {})
    monkeypatch.setattr(cli.tmux, "session_exists", lambda s: False)

    def fake_run(args, *a, **kw):
        if kw.get("check"):
            raise subprocess.CalledProcessError(1, list(args))
        return subprocess.CompletedProcess(list(args), 0, "", "")

    monkeypatch.setattr(cli.tmux.subprocess, "run", fake_run)
    monkeypatch.setattr(cli.tmux.shutil, "which", lambda name: "/usr/bin/tmux")
    with pytest.raises(SystemExit):
        cli.main(["up", "-c", str(cfg.path)])


def test_up_swarm_error(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/tmux")
    monkeypatch.setattr(
        cli.tmux, "configure_tmux", lambda cfg: (_ for _ in ()).throw(SwarmError("x"))
    )
    with pytest.raises(SystemExit):
        cli.main(["up", "-c", str(cfg.path)])


def test_up_resume(monkeypatch, tmp_path):
    import sessions as sessions_mod

    cfg = build(tmp_path, RESUME_AGENTS)
    sessions_mod.write_sessions(
        cfg,
        {
            "claude1": {"type": "claude", "session_id": "S-C", "updated_at": "t1"},
            "codex1": {"type": "codex", "session_id": "S-X", "updated_at": "t2"},
            "gemini2": {"type": "gemini", "session_id": "S-G2", "updated_at": "t3"},
        },
    )
    patch_launch(monkeypatch, n_agents=5)
    with mock_tmux(has_session=False):
        # --no-supervise avoids needing the supervisor module here.
        assert cli.main(["up", "-c", str(cfg.path), "--resume", "--no-supervise"]) == 0


def test_up_resumes_by_default(monkeypatch, tmp_path):
    """With no flag, `up` reattaches agents that have a recorded conversation."""
    import sessions as sessions_mod

    cfg = build(tmp_path, RESUME_AGENTS)
    sessions_mod.write_sessions(
        cfg, {"claude1": {"type": "claude", "session_id": "S-C", "updated_at": "t"}}
    )
    # Capture the resume_cmd handed to launch_agent_full for each agent.
    launches = []
    monkeypatch.setattr(
        cli, "launch_agent_full", lambda c, a, r=None: launches.append((a.name, r))
    )
    monkeypatch.setattr(cli.tmux, "session_exists", lambda s: False)
    with mock_tmux(has_session=False):
        assert cli.main(["up", "-c", str(cfg.path), "--no-supervise"]) == 0
    by = {name: rcmd for name, rcmd in launches}
    assert by["claude1"] is not None  # recorded -> resumed by default
    assert by["gemini1"] is None      # nothing recorded -> fresh conversation


def test_up_no_resume_forces_fresh(monkeypatch, tmp_path):
    """`--no-resume` overrides the default and starts fresh despite a recording."""
    import sessions as sessions_mod

    cfg = build(tmp_path, RESUME_AGENTS)
    sessions_mod.write_sessions(
        cfg, {"claude1": {"type": "claude", "session_id": "S-C", "updated_at": "t"}}
    )
    launches = []
    monkeypatch.setattr(
        cli, "launch_agent_full", lambda c, a, r=None: launches.append((a.name, r))
    )
    monkeypatch.setattr(cli.tmux, "session_exists", lambda s: False)
    with mock_tmux(has_session=False):
        assert cli.main(["up", "-c", str(cfg.path), "--no-resume", "--no-supervise"]) == 0
    by = {name: rcmd for name, rcmd in launches}
    assert by["claude1"] is None  # --no-resume wins


def test_up_quiet_when_no_recording(monkeypatch, tmp_path, capsys):
    """A default first launch (nothing recorded) must not nag about resume."""
    cfg = build(tmp_path, RESUME_AGENTS)
    monkeypatch.setattr(cli, "launch_agent_full", lambda c, a, r=None: None)
    monkeypatch.setattr(cli.tmux, "session_exists", lambda s: False)
    with mock_tmux(has_session=False):
        assert cli.main(["up", "-c", str(cfg.path), "--no-supervise"]) == 0
    err = capsys.readouterr().err
    assert "no recorded conversation" not in err


def test_remove_session_clears_runtime_and_mailboxes(monkeypatch, tmp_path):
    import sessions as sessions_mod

    cfg = build(tmp_path, GENERAL_AGENTS)
    # Seed the orchestrator runtime (sessions.yaml) + a mailbox with a message.
    sessions_mod.write_sessions(
        cfg, {"orchestrator": {"type": "claude", "session_id": "S", "updated_at": "t"}}
    )
    mp = cfg.mail_paths(cfg.get("orchestrator"))
    mp.inbox.mkdir(parents=True, exist_ok=True)
    (mp.inbox / "m.txt").write_text("stale mail")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(cli.tmux, "session_exists", lambda s: False)
    monkeypatch.setattr(cli, "_supervisor_alive", lambda c: False)

    assert cfg.runtime.exists()
    assert (mp.inbox / "m.txt").exists()
    assert cli.main(["remove-session", "-c", str(cfg.path)]) == 0
    assert not cfg.runtime.exists()              # sessions.yaml + all runtime state gone
    assert not mp.inbox.exists()                 # mailbox folders gone
    assert not mp.inbox.parent.joinpath("outbox").exists()


def test_remove_session_refuses_when_running(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(cli.tmux, "session_exists", lambda s: True)
    with pytest.raises(SystemExit):
        cli.main(["remove-session", "-c", str(cfg.path)])


def test_remove_session_refuses_when_supervisor_alive(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(cli.tmux, "session_exists", lambda s: False)
    monkeypatch.setattr(cli, "_supervisor_alive", lambda c: True)
    with pytest.raises(SystemExit):
        cli.main(["remove-session", "-c", str(cfg.path)])


def test_remove_session_nothing(monkeypatch, tmp_path, capsys):
    cfg = build(tmp_path, GENERAL_AGENTS)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(cli.tmux, "session_exists", lambda s: False)
    monkeypatch.setattr(cli, "_supervisor_alive", lambda c: False)
    assert not cfg.runtime.exists()
    assert cli.main(["remove-session", "-c", str(cfg.path)]) == 0
    assert "nothing to remove" in capsys.readouterr().err


def test_down_all(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    sup = mock.MagicMock()
    monkeypatch.setitem(sys.modules, "supervisor", sup)
    with mock_tmux(has_session=True):
        assert cli.main(["down", "-c", str(cfg.path)]) == 0
    sup.stop_supervisor.assert_called_once()


def test_down_only_skips_supervisor(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    sup = mock.MagicMock()
    monkeypatch.setitem(sys.modules, "supervisor", sup)
    with mock_tmux(has_session=False):
        assert cli.main(["down", "-c", str(cfg.path), "--only", "orchestrator"]) == 0
    sup.stop_supervisor.assert_not_called()


def test_down_unknown_agent(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    with mock_tmux(has_session=False):
        with pytest.raises(SystemExit):
            cli.main(["down", "-c", str(cfg.path), "--only", "ghost"])


def test_down_supervisor_absent(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    monkeypatch.setattr(cli, "_import_supervisor", lambda: (_ for _ in ()).throw(ImportError("nope")))
    with mock_tmux(has_session=False):
        assert cli.main(["down", "-c", str(cfg.path)]) == 0


def test_restart(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    sup = mock.MagicMock()
    monkeypatch.setitem(sys.modules, "supervisor", sup)
    patch_launch(monkeypatch, always=True)
    with mock_tmux(has_session=True):
        assert cli.main(["restart", "-c", str(cfg.path)]) == 0
    sup.stop_supervisor.assert_called_once()
    sup.start_supervisor.assert_called_once()


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------


def test_status_running(monkeypatch, tmp_path):
    import turn as turn_mod

    cfg = build(
        tmp_path,
        GENERAL_AGENTS
        + "  - name: idlebot\n    type: claude\n    capture: none\n    can_talk_to: []\n    role: r\n",
    )
    turn_mod.write_turn_state(
        cfg, "orchestrator",
        {"delivered": 2, "completed": 1, "since": cli.time.time() - 1, "by": "user"},
    )
    sup = mock.MagicMock()
    monkeypatch.setitem(sys.modules, "supervisor", sup)
    with mock_tmux(has_session=True):
        assert cli.main(["status", "-c", str(cfg.path)]) == 0


def test_status_down(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    monkeypatch.delitem(sys.modules, "supervisor", raising=False)
    with mock_tmux(has_session=False):
        assert cli.main(["status", "-c", str(cfg.path)]) == 0


def test_status_unknown_agent(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    with mock_tmux(has_session=False):
        with pytest.raises(SystemExit):
            cli.main(["status", "-c", str(cfg.path), "--only", "ghost"])


def test_status_supervisor_absent(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    monkeypatch.setattr(cli, "_import_supervisor", lambda: (_ for _ in ()).throw(ImportError("nope")))
    with mock_tmux(has_session=True):
        assert cli.main(["status", "-c", str(cfg.path)]) == 0


# --------------------------------------------------------------------------
# attach
# --------------------------------------------------------------------------


def test_attach_running(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    called = {}
    monkeypatch.setattr(cli.os, "execvp", lambda *a, **k: called.setdefault("x", True))
    with mock_tmux(has_session=True):
        assert cli.main(["attach", "-c", str(cfg.path), "orchestrator"]) == 0
    assert called.get("x") is True


def test_attach_not_running(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    monkeypatch.setattr(cli.os, "execvp", lambda *a, **k: None)
    with mock_tmux(has_session=False):
        with pytest.raises(SystemExit):
            cli.main(["attach", "-c", str(cfg.path), "orchestrator"])


def test_attach_unknown_agent(tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    with pytest.raises(SystemExit):
        cli.main(["attach", "-c", str(cfg.path), "ghost"])


# --------------------------------------------------------------------------
# send
# --------------------------------------------------------------------------


def test_send_as_user(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    with mock_tmux(has_session=True):
        assert cli.main(["send", "-c", str(cfg.path), "--to", "orchestrator", "hi user"]) == 0
    assert any(cfg.mail_paths(cfg.get("orchestrator")).inbox.iterdir())


def test_send_as_unknown_sender_treated_as_user(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    with mock_tmux(has_session=True):
        assert (
            cli.main(["send", "-c", str(cfg.path), "--from", "ghost", "--to", "orchestrator", "x"]) == 0
        )


def test_send_as_agent_routes(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    import mail as mail_mod

    mail_mod.init_mailboxes(cfg)
    out = cfg.mail_paths(cfg.get("orchestrator")).outbox / "worker"
    out.mkdir(parents=True, exist_ok=True)
    (out / "m.md").write_text("hello worker")
    with mock_tmux(has_session=True):
        assert (
            cli.main(["send", "-c", str(cfg.path), "--from", "orchestrator", "--to", "worker", "x"]) == 0
        )
    assert any(cfg.mail_paths(cfg.get("worker")).inbox.iterdir())
    assert not (out / "m.md").exists()  # moved to sent/


def test_send_from_file(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    body = tmp_path / "b.txt"
    body.write_text("file body")
    with mock_tmux(has_session=True):
        assert (
            cli.main(["send", "-c", str(cfg.path), "--to", "orchestrator", "--file", str(body)]) == 0
        )


# --------------------------------------------------------------------------
# user
# --------------------------------------------------------------------------


def test_user_available_away(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    with mock_tmux():
        assert cli.main(["user", "-c", str(cfg.path), "available"]) == 0
        card = cfg.mail_paths(cfg.get("worker")).outbox / "user" / "about.md"
        assert "available" in card.read_text()
        assert cli.main(["user", "-c", str(cfg.path), "away"]) == 0
        assert "away" in card.read_text()


def test_user_inbox_empty(tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    assert cli.main(["user", "-c", str(cfg.path), "inbox"]) == 0


def test_user_inbox_with_mail(tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    q = cfg.queue_dir / "user"
    q.mkdir(parents=True, exist_ok=True)
    (q / "m.txt").write_text("a message for you")
    assert cli.main(["user", "-c", str(cfg.path), "inbox"]) == 0


def test_user_send(tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    with mock_tmux(has_session=True):
        assert cli.main(["user", "-c", str(cfg.path), "send", "--to", "orchestrator", "yo"]) == 0
    assert any(cfg.mail_paths(cfg.get("orchestrator")).inbox.iterdir())


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------


def test_sessions_empty(tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    assert cli.main(["sessions", "-c", str(cfg.path)]) == 0


def test_sessions_listed(tmp_path):
    import sessions as sessions_mod

    cfg = build(tmp_path, GENERAL_AGENTS)
    sessions_mod.write_sessions(cfg, {"orchestrator": {"type": "claude", "session_id": "S1", "updated_at": "t"}})
    assert cli.main(["sessions", "-c", str(cfg.path)]) == 0


def test_sessions_raw(tmp_path):
    import sessions as sessions_mod

    cfg = build(tmp_path, GENERAL_AGENTS)
    sessions_mod.write_sessions(cfg, {"orchestrator": {"type": "claude", "session_id": "S1", "updated_at": "t"}})
    assert cli.main(["sessions", "-c", str(cfg.path), "--raw"]) == 0


# --------------------------------------------------------------------------
# queue / idle
# --------------------------------------------------------------------------


def test_queue_lists(tmp_path):
    import turn as turn_mod

    cfg = build(tmp_path, GENERAL_AGENTS)
    q = cfg.queue_dir / "orchestrator"
    q.mkdir(parents=True, exist_ok=True)
    (q / "q1.txt").write_text("line one\nline two")
    turn_mod.write_turn_state(
        cfg, "orchestrator",
        {"delivered": 1, "completed": 0, "since": cli.time.time() - 1, "by": "user"},
    )
    assert cli.main(["queue", "-c", str(cfg.path), "orchestrator"]) == 0


def test_queue_idle(tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    q = cfg.queue_dir / "orchestrator"
    q.mkdir(parents=True, exist_ok=True)
    (q / "q1.txt").write_text("x")
    assert cli.main(["queue", "-c", str(cfg.path), "orchestrator"]) == 0


def test_queue_clear(tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    q = cfg.queue_dir / "orchestrator"
    q.mkdir(parents=True, exist_ok=True)
    (q / "q1.txt").write_text("x")
    assert cli.main(["queue", "-c", str(cfg.path), "orchestrator", "--clear"]) == 0
    assert not any(q.iterdir())


def test_idle_with_drain(tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    assert cli.main(["idle", "-c", str(cfg.path), "orchestrator"]) == 0


def test_idle_no_drain(tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    assert cli.main(["idle", "-c", str(cfg.path), "orchestrator", "--no-drain"]) == 0


def test_idle_drain_releases_and_nudges_next(tmp_path):
    """idle --drain must release the next queued message AND nudge, so the escape
    hatch re-announces mail rather than leaving it silently sitting in the inbox."""
    import mail as mail_mod

    cfg = build(tmp_path, GENERAL_AGENTS)
    mail_mod.init_mailboxes(cfg)
    mail_mod.enqueue(cfg, "orchestrator", "handle this", "m-drain1")
    with mock_tmux(has_session=False):
        assert cli.main(["idle", "-c", str(cfg.path), "orchestrator"]) == 0
    # the queued message was released into the inbox (and nudge was attempted)
    inbox = cfg.mail_paths(cfg.get("orchestrator")).inbox
    assert (inbox / "m-drain1.txt").is_file()


# --------------------------------------------------------------------------
# inbox / logs
# --------------------------------------------------------------------------


def test_inbox_shows(tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    box = cfg.mail_paths(cfg.get("orchestrator")).inbox
    box.mkdir(parents=True, exist_ok=True)
    (box / "m.md").write_text("hello there")
    assert cli.main(["inbox", "-c", str(cfg.path), "orchestrator"]) == 0


def test_inbox_empty(tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    assert cli.main(["inbox", "-c", str(cfg.path), "orchestrator"]) == 0


def test_inbox_from_env(tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    box = cfg.mail_paths(cfg.get("orchestrator")).inbox
    box.mkdir(parents=True, exist_ok=True)
    (box / "m.md").write_text("hi")
    mp = pytest.MonkeyPatch()
    mp.setenv("AGENTAINER_AGENT", "orchestrator")
    try:
        assert cli.main(["inbox", "-c", str(cfg.path)]) == 0
    finally:
        mp.undo()


def test_inbox_no_agent_dies(tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    with pytest.raises(SystemExit):
        cli.main(["inbox", "-c", str(cfg.path)])


def test_logs_none(tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    assert cli.main(["logs", "-c", str(cfg.path)]) == 0


def test_logs_tail(tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    logf = cfg.log_dir / "agentainer.jsonl"
    logf.parent.mkdir(parents=True, exist_ok=True)
    logf.write_text(
        "not json\n"
        + json.dumps({"ts": "t", "agent": "orchestrator", "kind": "first_prompt", "text": "hi"}) + "\n"
    )
    assert cli.main(["logs", "-c", str(cfg.path)]) == 0


def test_logs_follow(tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    logf = cfg.log_dir / "agentainer.jsonl"
    logf.parent.mkdir(parents=True, exist_ok=True)
    logf.write_text(json.dumps({"ts": "t", "agent": "a", "kind": "k"}) + "\n")
    called = {}
    mp = pytest.MonkeyPatch()
    mp.setattr(cli.os, "execvp", lambda *a, **k: called.setdefault("x", True))
    try:
        assert cli.main(["logs", "-c", str(cfg.path), "-f"]) == 0
    finally:
        mp.undo()
    assert called.get("x") is True


def test_logs_agent_filter(tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    logf = cfg.log_dir / "orchestrator.jsonl"
    logf.parent.mkdir(parents=True, exist_ok=True)
    logf.write_text(json.dumps({"ts": "t", "agent": "orchestrator", "kind": "k", "text": "yo"}) + "\n")
    assert cli.main(["logs", "-c", str(cfg.path), "orchestrator"]) == 0


# --------------------------------------------------------------------------
# hook
# --------------------------------------------------------------------------


def _hook_env(monkeypatch, cfg, agent):
    monkeypatch.setenv("AGENTAINER_CONFIG", str(cfg.path))
    monkeypatch.setenv("AGENTAINER_AGENT", agent)


def test_hook_claude_stop_hook_active(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    _hook_env(monkeypatch, cfg, "orchestrator")
    with mock.patch.object(cli.sys, "stdin", io.StringIO('{"stop_hook_active": true}')):
        assert cli.main(["hook", "claude"]) == 0


def test_hook_claude_records_session(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    _hook_env(monkeypatch, cfg, "orchestrator")
    with mock.patch.object(cli.sys, "stdin", io.StringIO('{"session_id": "abc", "transcript_path": "/x"}')):
        assert cli.main(["hook", "claude"]) == 0
    assert "abc" in cfg.sessions_file.read_text()


def test_hook_claude_bad_json(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    _hook_env(monkeypatch, cfg, "orchestrator")
    with mock.patch.object(cli.sys, "stdin", io.StringIO("not json")):
        assert cli.main(["hook", "claude"]) == 0


def test_hook_codex_not_complete(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    _hook_env(monkeypatch, cfg, "orchestrator")
    assert cli.main(["hook", "codex", '{"type": "other"}']) == 0


def test_hook_codex_complete(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    _hook_env(monkeypatch, cfg, "orchestrator")
    assert cli.main(["hook", "codex", '{"type": "agent-turn-complete", "session_id": "c2"}']) == 0
    assert "c2" in cfg.sessions_file.read_text()


def test_hook_codex_bad_json(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    _hook_env(monkeypatch, cfg, "orchestrator")
    assert cli.main(["hook", "codex", "not json at all"]) == 0


def test_hook_generic(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    _hook_env(monkeypatch, cfg, "orchestrator")
    assert cli.main(["hook", "generic"]) == 0


def test_hook_routes_outbox(monkeypatch, tmp_path):
    import mail as mail_mod

    cfg = build(tmp_path, GENERAL_AGENTS)
    mail_mod.init_mailboxes(cfg)
    out = cfg.mail_paths(cfg.get("orchestrator")).outbox / "worker"
    out.mkdir(parents=True, exist_ok=True)
    (out / "m.md").write_text("hello worker from hook")
    _hook_env(monkeypatch, cfg, "orchestrator")
    with mock_tmux(has_session=True):
        with mock.patch.object(cli.sys, "stdin", io.StringIO("")):
            assert cli.main(["hook", "claude"]) == 0
    assert any(cfg.mail_paths(cfg.get("worker")).inbox.iterdir())
    assert not (out / "m.md").exists()
    assert any(cfg.mail_paths(cfg.get("orchestrator")).sent.iterdir())


def test_hook_bad_config(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENTAINER_CONFIG", raising=False)
    with pytest.raises(SystemExit):
        cli.main(["hook", "claude", "-c", "/nope.yaml"])


# --------------------------------------------------------------------------
# watch
# --------------------------------------------------------------------------


def test_watch_wrong_capture(tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)  # orchestrator is capture=hook after upgrade
    with mock_tmux(has_session=True):
        with pytest.raises(SystemExit):
            cli.main(["watch", "-c", str(cfg.path), "orchestrator"])


def test_watch_session_down(tmp_path):
    cfg = build(tmp_path, "- name: g\n  type: gemini\n  capture: pane\n  can_talk_to: []\n  role: r\n")
    with mock_tmux(has_session=False):
        with pytest.raises(SystemExit):
            cli.main(["watch", "-c", str(cfg.path), "g"])


def test_watch_happy(monkeypatch, tmp_path):
    cfg = build(tmp_path, "- name: g\n  type: gemini\n  capture: pane\n  can_talk_to: []\n  role: r\n")
    monkeypatch.setattr(cli, "run_watcher", lambda *a, **k: None)
    with mock_tmux(has_session=True):
        assert cli.main(["watch", "-c", str(cfg.path), "g"]) == 0


def test_watch_tick_changed(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    agent = cfg.get("orchestrator")
    state = {"previous": ["a"], "last_change": 0.0, "dirty": False}
    monkeypatch.setattr(cli.tmux, "capture_pane", lambda c, a: "b")
    assert cli._watch_tick(cfg, agent, state) is False
    assert state["dirty"] is True


def test_watch_tick_same_dirty_false(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    agent = cfg.get("orchestrator")
    state = {"previous": ["a"], "last_change": 0.0, "dirty": False}
    monkeypatch.setattr(cli.tmux, "capture_pane", lambda c, a: "a")
    assert cli._watch_tick(cfg, agent, state) is False


def test_watch_tick_idle_short(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    agent = cfg.get("orchestrator")
    state = {"previous": ["a"], "last_change": 100.0, "dirty": True}
    monkeypatch.setattr(cli.tmux, "capture_pane", lambda c, a: "a")
    monkeypatch.setattr(cli.time, "monotonic", lambda: 100.5)
    assert cli._watch_tick(cfg, agent, state) is False


def test_watch_tick_idle_long(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    agent = cfg.get("orchestrator")
    state = {"previous": ["a"], "last_change": 0.0, "dirty": True}
    monkeypatch.setattr(cli.tmux, "capture_pane", lambda c, a: "a")
    monkeypatch.setattr(cli.time, "monotonic", lambda: 10.0)
    assert cli._watch_tick(cfg, agent, state) is True


def test_run_watcher(monkeypatch, tmp_path):
    cfg = build(tmp_path, "- name: g\n  type: gemini\n  capture: pane\n  can_talk_to: []\n  role: r\n")
    agent = cfg.get("g")
    monkeypatch.setattr(cli.tmux, "capture_pane", lambda c, a: "")
    sess = [True, True, False]
    monkeypatch.setattr(cli.tmux, "session_exists", lambda s: sess.pop(0) if sess else False)
    ticks = [False, True]
    monkeypatch.setattr(cli, "_watch_tick", lambda c, a, st: ticks.pop(0))
    called = {}
    monkeypatch.setattr(cli.mail, "on_stop", lambda c, a: called.setdefault("x", True))
    cli.run_watcher(cfg, agent)
    assert called.get("x") is True


# --------------------------------------------------------------------------
# supervise
# --------------------------------------------------------------------------


def test_supervise_present(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    sup = mock.MagicMock()
    monkeypatch.setitem(sys.modules, "supervisor", sup)
    assert cli.main(["supervise", "-c", str(cfg.path)]) == 0
    sup.run_supervisor.assert_called_once_with(cfg, cfg.names())


def test_supervise_names(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    sup = mock.MagicMock()
    monkeypatch.setitem(sys.modules, "supervisor", sup)
    assert cli.main(["supervise", "-c", str(cfg.path), "orchestrator", "worker"]) == 0
    sup.run_supervisor.assert_called_once_with(cfg, ["orchestrator", "worker"])


def test_supervise_absent(monkeypatch, tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    monkeypatch.setattr(cli, "_import_supervisor", lambda: (_ for _ in ()).throw(ImportError("nope")))
    with pytest.raises(SystemExit):
        cli.main(["supervise", "-c", str(cfg.path)])


# --------------------------------------------------------------------------
# main / argparse
# --------------------------------------------------------------------------


def test_main_version():
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0


def test_main_rewrite_yaml_arg(tmp_path):
    cfg = build(tmp_path, GENERAL_AGENTS)
    # `agentainer <file>.yaml [cmd]` rewrites to `... -c <file>`.
    assert cli.main([str(cfg.path), "validate"]) == 0


def test_main_invalid_command():
    with pytest.raises(SystemExit):
        cli.main(["frobnicate"])


# --------------------------------------------------------------------------
# up_config registers the swarm in the global control plane
# --------------------------------------------------------------------------


def test_up_registers_swarm(monkeypatch, tmp_path):
    import registry

    cfg = build(tmp_path, GENERAL_AGENTS, name="regme")
    patch_launch(monkeypatch, n_agents=2)
    with mock_tmux(has_session=False):
        assert cli.main(["up", "-c", str(cfg.path), "--no-supervise"]) == 0
    assert "regme" in {e["name"] for e in registry.list_entries()}


# --------------------------------------------------------------------------
# swarms: list / create / register / remove / up / down
# --------------------------------------------------------------------------


def test_swarms_list_empty(capsys):
    assert cli.main(["swarms", "list"]) == 0
    assert "no swarms registered" in capsys.readouterr().err


def test_swarms_list_with_registered(monkeypatch, tmp_path, capsys):
    import registry

    cfg = build(tmp_path, GENERAL_AGENTS, name="listme")
    registry.register("listme", cfg.path)
    with mock_tmux(has_session=True):
        assert cli.main(["swarms", "list"]) == 0
    out = capsys.readouterr().out
    assert "listme" in out
    assert "running" in out


def test_swarms_list_invalid_config(tmp_path, capsys):
    import registry

    bad = tmp_path / "broken" / "agentainer.yaml"
    bad.parent.mkdir(parents=True)
    bad.write_text("swarm: {root: ./ws}\nagents:\n  - {name: a, command: 'true', can_talk_to: [ghost]}\n")
    registry.register("brokenswarm", bad)
    assert cli.main(["swarms", "list"]) == 0
    out = capsys.readouterr().out
    assert "brokenswarm" in out
    assert "(invalid:" in out


def test_swarms_create_success(capsys):
    import registry

    assert cli.main(["swarms", "create", "created1"]) == 0
    assert "created swarm" in capsys.readouterr().err
    assert registry.entry("created1") is not None


def test_swarms_create_with_template(tmp_path):
    import registry

    assert cli.main(["swarms", "create", "res2", "--template", "research"]) == 0
    cfg = registry.resolve("res2")
    assert cfg.agents  # seeded from the template


def test_swarms_create_up(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(cli, "up_config", lambda cfg, **k: calls.append(cfg.name) or [])
    assert cli.main(["swarms", "create", "upme", "--up"]) == 0
    assert calls == ["upme"]
    assert "up: started" in capsys.readouterr().err


def test_swarms_create_up_no_tmux(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit):
        cli.main(["swarms", "create", "upme2", "--up"])


def test_swarms_create_duplicate(capsys):
    assert cli.main(["swarms", "create", "dupe"]) == 0
    with pytest.raises(SystemExit):
        cli.main(["swarms", "create", "dupe"])


def test_swarms_register_success(tmp_path, capsys):
    import registry

    cfg = build(tmp_path, GENERAL_AGENTS, name="regsw")
    assert cli.main(["swarms", "register", str(cfg.path)]) == 0
    assert "registered swarm" in capsys.readouterr().err
    assert registry.entry("regsw") is not None


def test_swarms_register_invalid_path():
    with pytest.raises(SystemExit):
        cli.main(["swarms", "register", "/nope/agentainer.yaml"])


def test_swarms_remove_success(tmp_path, capsys):
    import registry

    cfg = build(tmp_path, GENERAL_AGENTS, name="rmme")
    registry.register("rmme", cfg.path)
    assert cli.main(["swarms", "remove", "rmme"]) == 0
    assert "removed swarm" in capsys.readouterr().err
    assert registry.entry("rmme") is None


def test_swarms_remove_not_registered(capsys):
    assert cli.main(["swarms", "remove", "ghost"]) == 1
    assert "was not registered" in capsys.readouterr().err


def test_swarms_up_unknown(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/" + name)
    with pytest.raises(SystemExit):
        cli.main(["swarms", "up", "ghost"])


def test_swarms_up_no_tmux(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit):
        cli.main(["swarms", "up", "whatever"])


def test_swarms_up_success(monkeypatch, capsys):
    import registry

    # An EMPTY swarm brings up nothing (no agents, no supervisor spawned).
    registry.create_swarm("emptyup")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/" + name)
    with mock_tmux(has_session=False):
        assert cli.main(["swarms", "up", "emptyup"]) == 0
    assert "started 0 agent(s)" in capsys.readouterr().err


def test_swarms_down_unknown():
    with pytest.raises(SystemExit):
        cli.main(["swarms", "down", "ghost"])


def test_swarms_down_success(monkeypatch, tmp_path, capsys):
    import registry

    cfg = build(tmp_path, GENERAL_AGENTS, name="downme")
    registry.register("downme", cfg.path)
    sup = mock.MagicMock()
    monkeypatch.setitem(sys.modules, "supervisor", sup)
    with mock_tmux(has_session=True):
        assert cli.main(["swarms", "down", "downme"]) == 0
    sup.stop_supervisor.assert_called_once()
    assert "stopped" in capsys.readouterr().err


def test_swarms_down_supervisor_absent(monkeypatch, tmp_path):
    import registry

    cfg = build(tmp_path, GENERAL_AGENTS, name="downme2")
    registry.register("downme2", cfg.path)
    monkeypatch.setattr(
        cli, "_import_supervisor", lambda: (_ for _ in ()).throw(ImportError("nope"))
    )
    with mock_tmux(has_session=False):
        assert cli.main(["swarms", "down", "downme2"]) == 0


def test_swarms_build_success(monkeypatch, tmp_path, capsys):
    import registry

    registry.create_swarm("buildme")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(cli.scaffold, "open_builder_session",
                        lambda cfg, **k: "buildme_builder")
    assert cli.main(["swarms", "build", "buildme", "--agent", "claude"]) == 0
    assert "buildme_builder" in capsys.readouterr().err


def test_swarms_build_unknown(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/" + name)
    with pytest.raises(SystemExit):
        cli.main(["swarms", "build", "ghost"])


def test_swarms_build_no_tmux(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit):
        cli.main(["swarms", "build", "whatever"])


def test_swarms_build_scaffold_error(monkeypatch, tmp_path):
    import registry

    registry.create_swarm("builderr")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/" + name)

    def _boom(cfg, **k):
        raise ValueError("bad agent type")
    monkeypatch.setattr(cli.scaffold, "open_builder_session", _boom)
    with pytest.raises(SystemExit):
        cli.main(["swarms", "build", "builderr"])


def test_swarms_approve_success(monkeypatch, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(cli.scaffold, "approve_swarm",
                        lambda name, **k: {"ok": True, "started": ["a", "b"]})
    assert cli.main(["swarms", "approve", "anything"]) == 0
    assert "started 2 agent(s)" in capsys.readouterr().err


def test_swarms_approve_failure(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(cli.scaffold, "approve_swarm",
                        lambda name, **k: {"ok": False, "error": "config did not load"})
    with pytest.raises(SystemExit):
        cli.main(["swarms", "approve", "broken"])


def test_swarms_approve_no_tmux(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit):
        cli.main(["swarms", "approve", "whatever"])


def test_swarms_use_success(capsys):
    import registry

    registry.create_swarm("useme")
    assert cli.main(["swarms", "use", "useme"]) == 0
    assert registry.active_swarm() == "useme"


def test_swarms_use_unknown():
    with pytest.raises(SystemExit):
        cli.main(["swarms", "use", "ghost"])

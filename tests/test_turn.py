#!/usr/bin/env python3
"""100% line coverage of lib/turn.py.

Ports the v1 turn-state assertions from AgentSwarm/tests/test_swarm_core.py and
adds coverage for the v2 entry points (on_turn_finished, health_probe).
"""

import time
from unittest import mock

import config as cfgmod
from config import Agent

import turn as turnmod


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def make_agent(cfg, name="X", type="claude", capture="hook", busy_check=True):
    return Agent(
        name=name,
        type=type,
        command="true",
        workdir=cfg.root,
        session=f"t-{name}",
        capture=capture,
        boot_delay_ms=0,
        role="",
        can_talk_to=[],
        mail_dir=cfg.root,
        busy_check=busy_check,
    )


# --------------------------------------------------------------------------
# v1 ported behaviour
# --------------------------------------------------------------------------


def test_turn_state_default_and_write(tmp_runtime):
    assert turnmod.turn_state(tmp_runtime, "X") == {
        "delivered": 0, "completed": 0, "since": 0, "by": None
    }
    state = {"delivered": 2, "completed": 1, "since": 5.0, "by": "A"}
    turnmod.write_turn_state(tmp_runtime, "X", state)
    assert turnmod.turn_state(tmp_runtime, "X") == state


def test_turn_state_missing_file_default(tmp_runtime):
    # No file written yet -> the empty default, no errors.
    assert turnmod.turn_state(tmp_runtime, "ghost") == {
        "delivered": 0, "completed": 0, "since": 0, "by": None
    }


def test_turn_state_corrupt_file_default(tmp_runtime):
    p = tmp_runtime.run_dir
    p.mkdir(parents=True, exist_ok=True)
    (p / "X.turn.json").write_text("{not valid json")
    assert turnmod.turn_state(tmp_runtime, "X") == {
        "delivered": 0, "completed": 0, "since": 0, "by": None
    }


def test_mark_turn_started_and_finished(tmp_runtime):
    cfg = tmp_runtime
    turnmod.mark_turn_started(cfg, "X", "A")
    st = turnmod.turn_state(cfg, "X")
    assert st["delivered"] == 1 and st["by"] == "A" and st["since"] > 0
    turnmod.mark_turn_finished(cfg, "X")
    st = turnmod.turn_state(cfg, "X")
    assert st["completed"] == 1


def test_mark_turn_finished_clamps_not_increments(tmp_runtime):
    # A CLI that folds a queued message into the running turn must not let the
    # counters drift (delivered can already exceed a once-finished completed).
    turnmod.write_turn_state(tmp_runtime, "X", {
        "delivered": 3, "completed": 0, "since": 0, "by": "A"
    })
    turnmod.mark_turn_finished(tmp_runtime, "X")
    st = turnmod.turn_state(tmp_runtime, "X")
    assert st["completed"] == 3


def test_busy_info_states(tmp_runtime):
    cfg = tmp_runtime
    agent = make_agent(cfg)
    # Not busy: delivered <= completed.
    turnmod.write_turn_state(cfg, "X", {"delivered": 0, "completed": 0, "since": 0, "by": None})
    assert turnmod.busy_info(cfg, agent) is None

    # Busy: delivered > completed, recently.
    turnmod.write_turn_state(cfg, "X", {"delivered": 1, "completed": 0, "since": 1e18, "by": "A"})
    state = turnmod.busy_info(cfg, agent)
    assert state is not None and state["by"] == "A" and "age_s" in state

    # Stale-busy: beyond busy_timeout -> treated idle (None) and warns.
    cfg.busy_timeout_ms = 1
    turnmod.write_turn_state(cfg, "X", {"delivered": 1, "completed": 0, "since": 0, "by": "A"})
    with mock.patch.object(turnmod, "warn") as w:
        assert turnmod.busy_info(cfg, agent) is None
        assert w.called


def test_busy_info_disabled(tmp_runtime):
    agent = make_agent(tmp_runtime, capture="none", busy_check=False)
    turnmod.write_turn_state(tmp_runtime, "X", {"delivered": 5, "completed": 0})
    assert turnmod.busy_info(tmp_runtime, agent) is None


def test_busy_message(tmp_runtime):
    agent = make_agent(tmp_runtime)
    state = {"by": "A", "age_s": 3}
    msg = turnmod.busy_message(tmp_runtime, agent, state)
    assert "busy" in msg and "A" in msg and "--queue" in msg and agent.name in msg


# --------------------------------------------------------------------------
# v2 new entry points
# --------------------------------------------------------------------------


def test_on_turn_finished_returns_state_and_resets(tmp_runtime):
    cfg = tmp_runtime
    turnmod.mark_turn_started(cfg, "X", "A")
    state = turnmod.on_turn_finished(cfg, "X")
    assert state["delivered"] == 1
    assert state["completed"] == 1
    # idle again: busy_info returns None
    assert turnmod.busy_info(cfg, make_agent(cfg)) is None


def test_health_probe_alive_capture_none_silent(tmp_runtime):
    cfg = tmp_runtime
    agent = make_agent(cfg, capture="none")
    with mock.patch.object(turnmod, "session_exists", return_value=True):
        probe = turnmod.health_probe(cfg, agent)
    assert probe["session_alive"] is True
    assert probe["capture"] == "none"
    assert probe["busy"] is False
    assert probe["idle_for_ms"] == 0
    assert probe["silent_but_alive"] is True


def test_health_probe_session_dead(tmp_runtime):
    cfg = tmp_runtime
    agent = make_agent(cfg, capture="none")
    with mock.patch.object(turnmod, "session_exists", return_value=False):
        probe = turnmod.health_probe(cfg, agent)
    assert probe["session_alive"] is False
    assert probe["silent_but_alive"] is False
    assert probe["idle_for_ms"] == 0


def test_health_probe_alive_capture_hook_not_silent(tmp_runtime):
    cfg = tmp_runtime
    agent = make_agent(cfg, capture="hook")
    with mock.patch.object(turnmod, "session_exists", return_value=True):
        probe = turnmod.health_probe(cfg, agent)
    assert probe["session_alive"] is True
    assert probe["silent_but_alive"] is False
    assert probe["capture"] == "hook"


def test_health_probe_idle_for_ms_when_busy(tmp_runtime):
    cfg = tmp_runtime
    agent = make_agent(cfg)
    turnmod.write_turn_state(cfg, "X", {
        "delivered": 1, "completed": 0, "since": time.time() - 1.0, "by": "A"
    })
    with mock.patch.object(turnmod, "session_exists", return_value=True):
        probe = turnmod.health_probe(cfg, agent)
    assert probe["busy"] is True
    # ~1000 ms elapsed.
    assert probe["idle_for_ms"] >= 900


def test_health_probe_idle_when_not_busy(tmp_runtime):
    cfg = tmp_runtime
    agent = make_agent(cfg)
    # delivered <= completed -> not busy -> idle_for_ms 0 even with session alive.
    turnmod.write_turn_state(cfg, "X", {
        "delivered": 0, "completed": 0, "since": 0, "by": None
    })
    with mock.patch.object(turnmod, "session_exists", return_value=True):
        probe = turnmod.health_probe(cfg, agent)
    assert probe["busy"] is False
    assert probe["idle_for_ms"] == 0

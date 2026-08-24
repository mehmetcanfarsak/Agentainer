"""Unit tests for the supervisor (the orchestrator's liveness heartbeat).

These exercise ``supervise_once`` and the process-management helpers directly
with the tmux-backed and mailroom primitives monkeypatched, so no real tmux is
needed and the per-tick reconciliation logic is fully deterministic. Adapted
from v1's ``tests/test_swarm_supervisor.py`` (18 cases) to drive the v2 mailroom.
"""

import sys
from pathlib import Path
from unittest import mock

import pytest

import supervisor
import support


def _cfg(tmp_path, agents_block):
    return support.load_swarm(tmp_path, agents_block)


@pytest.fixture
def p():
    """Monkeypatch every external primitive supervise_once touches per agent."""
    rec = {
        "session": {},   # name -> session exists?
        "exited": {},    # name -> agent_exited (zombie fallback shell)?
        "busy": {},      # name -> busy_info return (state dict or None)
        "health": {},    # name -> health_probe return dict
        "state": {},     # name -> turn_state dict
        "mark": [],      # mark_turn_finished args
        "log": [],       # log_event (agent, kind) tuples
        "process": 0,    # process_read_folder call count
        "release": {},   # name -> present_current return (mail was present-able)
        "present": [],   # present_current args
        "flush": {},     # name -> flush_staged_input return
        "flushcalls": [],  # flush_staged_input args
        "nudge": [],     # nudge args
        "ping": [],      # maybe_ping args
    }

    def _session_exists(session):
        name = session.split("t-", 1)[-1] if session.startswith("t-") else session
        return rec["session"].get(name, True)

    def _agent_exited(session, name):
        return rec["exited"].get(name, False)

    def _turn_state(cfg, name):
        return rec["state"].get(name, {"delivered": 0, "completed": 0, "since": 0, "by": None})

    def _busy_info(cfg, agent):
        return rec["busy"].get(agent.name, None)

    def _health_probe(cfg, agent):
        return rec["health"].get(
            agent.name,
            {
                "session_alive": _session_exists(agent.session),
                "capture": agent.capture,
                "busy": _busy_info(cfg, agent) is not None,
                "idle_for_ms": 0,
                "silent_but_alive": False,
            },
        )

    def _mark_finished(cfg, name):
        rec["mark"].append(name)

    def _process(cfg, name):
        rec["process"] += 1
        return 0

    def _release(cfg, name):
        return rec["release"].get(name, False)

    def _present(cfg, name):
        rec["present"].append(name)
        return rec["release"].get(name, False)

    def _flush(cfg, name):
        rec["flushcalls"].append(name)
        return rec["flush"].get(name, False)

    def _nudge(cfg, name):
        rec["nudge"].append(name)
        return True

    def _ping(cfg, name):
        rec["ping"].append(name)
        return True

    def _log(cfg, agent, kind, **kw):
        rec["log"].append((agent, kind))

    with mock.patch.object(supervisor.turn, "turn_state", _turn_state), \
         mock.patch.object(supervisor.turn, "busy_info", _busy_info), \
         mock.patch.object(supervisor.turn, "health_probe", _health_probe), \
         mock.patch.object(supervisor.turn, "mark_turn_finished", _mark_finished), \
         mock.patch.object(supervisor.mail, "process_read_folder", _process), \
         mock.patch.object(supervisor.mail, "release_next", _release), \
         mock.patch.object(supervisor.mail, "present_current", _present), \
         mock.patch.object(supervisor.mail, "flush_staged_input", _flush), \
         mock.patch.object(supervisor.mail, "nudge", _nudge), \
         mock.patch.object(supervisor.mail, "maybe_ping", _ping), \
         mock.patch.object(supervisor.tmux, "session_exists", _session_exists), \
         mock.patch.object(supervisor.tmux, "agent_exited", _agent_exited), \
         mock.patch.object(supervisor.log, "log_event", _log):
        yield rec


# --------------------------------------------------------------------------
# supervise_once: idle
# --------------------------------------------------------------------------


def test_supervise_leaves_idle_agent_alone(tmp_path, p):
    cfg = _cfg(tmp_path, "  - name: A\n    type: claude\n    command: 'true'\n")
    p["session"]["A"] = True
    p["state"]["A"] = {"delivered": 0, "completed": 0, "since": 0, "by": None}
    supervisor.supervise_once(cfg, ["A"], set())
    # No reconcile (no dead/stale/silent) -- only the idle bookkeeping ran.
    assert p["mark"] == []
    assert p["log"] == []
    assert p["process"] == 1
    # present_current found no mail -> ping path.
    assert p["present"] == ["A"]
    assert p["ping"] == ["A"]


def test_supervise_idle_presents_mail_over_ping(tmp_path, p):
    cfg = _cfg(tmp_path, "  - name: A\n    type: claude\n    command: 'true'\n")
    p["session"]["A"] = True
    p["release"]["A"] = True  # present_current had mail to (re)announce -> no ping
    supervisor.supervise_once(cfg, ["A"], set())
    assert p["process"] == 1
    assert p["present"] == ["A"]
    assert p["ping"] == []


def test_supervise_idle_pings_when_inbox_empty(tmp_path, p):
    cfg = _cfg(tmp_path, "  - name: A\n    type: claude\n    command: 'true'\n")
    p["session"]["A"] = True
    p["release"]["A"] = False  # present_current found nothing -> ping
    supervisor.supervise_once(cfg, ["A"], set())
    assert p["present"] == ["A"]
    assert p["ping"] == ["A"]


def test_supervise_idle_flushes_staged_input_over_mail(tmp_path, p):
    # Operator input staged while the agent was busy is submitted the moment it
    # idles; that wins over queued mail and ends the tick (continue) so the
    # freshly-submitted turn is not clobbered by a nudge.
    cfg = _cfg(tmp_path, "  - name: A\n    type: claude\n    command: 'true'\n")
    p["session"]["A"] = True
    p["flush"]["A"] = True    # a staged line was submitted
    p["release"]["A"] = True  # there is also mail, but flush short-circuits the tick
    supervisor.supervise_once(cfg, ["A"], set())
    assert p["flushcalls"] == ["A"]
    assert p["present"] == []  # continue skipped present_current
    assert p["ping"] == []


def test_supervise_busy_pings_but_does_not_deliver(tmp_path, p):
    # A busy agent must not get mail delivered/nudged, but maybe_ping must still
    # run so a when_busy="queue" rule can enqueue mid-turn (regression: maybe_ping
    # used to sit inside the idle-only branch, making when_busy="queue" dead).
    cfg = _cfg(tmp_path, "  - name: A\n    type: claude\n    command: 'true'\n")
    p["session"]["A"] = True
    cfg.busy_timeout_ms = 900000
    p["state"]["A"] = {
        "delivered": 1, "completed": 0,
        "since": __import__("time").time(), "by": "lead",
    }
    p["busy"]["A"] = {"delivered": 1, "completed": 0, "since": 0, "by": "lead"}
    supervisor.supervise_once(cfg, ["A"], set())
    assert p["flushcalls"] == []   # no staged flush into a busy pane
    assert p["present"] == []      # no delivery
    assert p["ping"] == ["A"]      # ping engine still consulted


# --------------------------------------------------------------------------
# supervise_once: stale-busy
# --------------------------------------------------------------------------


def test_supervise_reconciles_stale_busy(tmp_path, p):
    cfg = _cfg(tmp_path, "  - name: A\n    type: claude\n    command: 'true'\n")
    p["session"]["A"] = True
    # delivered > completed and older than the busy_timeout.
    p["state"]["A"] = {"delivered": 3, "completed": 0, "since": 0, "by": "lead"}
    cfg.busy_timeout_ms = 1  # make any non-zero age exceed it
    supervisor.supervise_once(cfg, ["A"], set())
    assert ("A", "stale-busy") in p["log"]
    assert p["mark"] == ["A"]


def test_supervise_ignores_freshly_busy(tmp_path, p):
    cfg = _cfg(tmp_path, "  - name: A\n    type: claude\n    command: 'true'\n")
    p["session"]["A"] = True
    p["state"]["A"] = {
        "delivered": 1,
        "completed": 0,
        "since": __import__("time").time(),  # brand new turn, under timeout
        "by": "lead",
    }
    cfg.busy_timeout_ms = 900000
    # busy_info reports a live turn -> idle delivery must be skipped.
    p["busy"]["A"] = {"delivered": 1, "completed": 0, "since": 0, "by": "lead", "age_s": 0}
    supervisor.supervise_once(cfg, ["A"], set())
    assert p["mark"] == []
    assert p["process"] == 0    # no read-folder sweep while busy
    assert p["present"] == []   # no mail release/nudge while busy
    assert p["nudge"] == []
    # maybe_ping IS still called while busy -- it only ENQUEUES, so a
    # when_busy="queue" rule can come due mid-turn (it's the only place it can).
    assert p["ping"] == ["A"]


# --------------------------------------------------------------------------
# supervise_once: dead sessions
# --------------------------------------------------------------------------


def test_supervise_handles_dead_session_and_logs_once(tmp_path, p):
    cfg = _cfg(tmp_path, "  - name: A\n    type: claude\n    command: 'true'\n")
    p["session"]["A"] = False  # session gone
    seen: set[str] = set()
    supervisor.supervise_once(cfg, ["A"], seen)
    # Logged + reconciled the turn, but did NOT try to deliver into a dead pane.
    assert ("A", "dead") in p["log"]
    assert p["mark"] == ["A"]
    assert p["process"] == 0
    # Second tick: already seen dead -> no duplicate log / reconcile.
    supervisor.supervise_once(cfg, ["A"], seen)
    assert p["log"].count(("A", "dead")) == 1
    assert p["mark"] == ["A"]


def test_supervise_handles_exited_zombie_shell(tmp_path, p):
    cfg = _cfg(tmp_path, "  - name: A\n    type: claude\n    command: 'true'\n")
    p["session"]["A"] = True   # tmux session still alive...
    p["exited"]["A"] = True    # ...but the CLI exited into a fallback shell
    seen: set[str] = set()
    supervisor.supervise_once(cfg, ["A"], seen)
    # Logged distinctly as "exited", reconciled the turn, and -- crucially --
    # did NOT paste any mail/nudge into the bare shell.
    assert ("A", "exited") in p["log"]
    assert ("A", "dead") not in p["log"]
    assert p["mark"] == ["A"]
    assert p["process"] == 0
    assert p["present"] == []
    assert p["nudge"] == []
    assert p["ping"] == []
    # Warned once per transition.
    supervisor.supervise_once(cfg, ["A"], seen)
    assert p["log"].count(("A", "exited")) == 1


def test_supervise_resurrected_agent_clears_seen_dead(tmp_path, p):
    cfg = _cfg(tmp_path, "  - name: A\n    type: claude\n    command: 'true'\n")
    p["session"]["A"] = False
    seen: set[str] = set()
    supervisor.supervise_once(cfg, ["A"], seen)
    assert "A" in seen
    # Agent comes back to life: seen_dead is cleared, and a later death re-logs.
    p["session"]["A"] = True
    p["state"]["A"] = {"delivered": 0, "completed": 0, "since": 0, "by": None}
    supervisor.supervise_once(cfg, ["A"], seen)
    assert "A" not in seen
    p["session"]["A"] = False
    supervisor.supervise_once(cfg, ["A"], seen)
    assert p["log"].count(("A", "dead")) == 2


# --------------------------------------------------------------------------
# supervise_once: silent-but-alive
# --------------------------------------------------------------------------


def test_supervise_silent_but_alive_logs_once(tmp_path, p):
    cfg = _cfg(tmp_path, "  - name: A\n    type: claude\n    command: 'true'\n")
    p["session"]["A"] = True
    p["health"]["A"] = {
        "session_alive": True,
        "capture": "none",
        "busy": False,
        "idle_for_ms": 0,
        "silent_but_alive": True,
    }
    supervisor.supervise_once(cfg, ["A"], set())
    assert ("A", "silent-but-alive") in p["log"]
    # Still silent on the next tick -> no duplicate log.
    supervisor.supervise_once(cfg, ["A"], set())
    assert p["log"].count(("A", "silent-but-alive")) == 1
    # Resolves to not-silent -> cleared (else branch), no new log.
    p["health"]["A"] = {
        "session_alive": True,
        "capture": "hook",
        "busy": False,
        "idle_for_ms": 0,
        "silent_but_alive": False,
    }
    supervisor.supervise_once(cfg, ["A"], set())
    assert p["log"].count(("A", "silent-but-alive")) == 1


# --------------------------------------------------------------------------
# process management
# --------------------------------------------------------------------------


def test_start_supervisor_launches_and_records_pid(tmp_path):
    cfg = _cfg(tmp_path, "  - name: A\n    type: claude\n    command: 'true'\n")
    captured = {}

    class _Popen:
        def __init__(self, args, **kw):
            captured["args"] = args
            captured["env"] = kw.get("env", {})
            self.pid = 2468

    with mock.patch.object(supervisor.subprocess, "Popen", _Popen):
        supervisor.start_supervisor(cfg, ["A"])
    # It launches the supervise subcommand, passing the config via AGENTAINER_*.
    args = captured["args"]
    assert "supervise" in args
    assert args[0] == sys.executable
    assert captured["env"]["AGENTAINER_HOME"]
    assert captured["env"]["AGENTAINER_CONFIG"] == str(cfg.path)
    # The pid file records the launched process id.
    assert (cfg.run_dir / "supervisor.pid").read_text() == "2468"


def test_stop_supervisor_kills_and_clears_pid(tmp_path):
    cfg = _cfg(tmp_path, "  - name: A\n    type: claude\n    command: 'true'\n")
    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    (cfg.run_dir / "supervisor.pid").write_text("4321")
    with mock.patch.object(supervisor.os, "kill") as kill:
        supervisor.stop_supervisor(cfg)
    kill.assert_called_once_with(4321, 15)
    assert not (cfg.run_dir / "supervisor.pid").exists()


def test_stop_supervisor_no_pid_is_noop(tmp_path):
    cfg = _cfg(tmp_path, "  - name: A\n    type: claude\n    command: 'true'\n")
    with mock.patch.object(supervisor.os, "kill") as kill:
        supervisor.stop_supervisor(cfg)
    kill.assert_not_called()


def test_stop_supervisor_survives_kill_error(tmp_path):
    cfg = _cfg(tmp_path, "  - name: A\n    type: claude\n    command: 'true'\n")
    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    (cfg.run_dir / "supervisor.pid").write_text("4321")
    with mock.patch.object(supervisor.os, "kill", side_effect=OSError("esrch")):
        # Must not raise even if the process is already gone; pid file cleared.
        supervisor.stop_supervisor(cfg)
    assert not (cfg.run_dir / "supervisor.pid").exists()


def test_supervisor_alive_kill_error_means_dead(tmp_path):
    cfg = _cfg(tmp_path, "  - name: A\n    type: claude\n    command: 'true'\n")
    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    (cfg.run_dir / "supervisor.pid").write_text("777")
    with mock.patch.object(supervisor.os, "kill", side_effect=OSError("esrch")):
        assert supervisor.supervisor_alive(cfg) is False


def test_supervisor_alive_bad_pid_file(tmp_path):
    cfg = _cfg(tmp_path, "  - name: A\n    type: claude\n    command: 'true'\n")
    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    (cfg.run_dir / "supervisor.pid").write_text("not-a-number")
    # A non-integer pid must not raise; treated as not alive.
    assert supervisor.supervisor_alive(cfg) is False


def test_supervisor_alive_true_and_false(tmp_path):
    cfg = _cfg(tmp_path, "  - name: A\n    type: claude\n    command: 'true'\n")
    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    (cfg.run_dir / "supervisor.pid").write_text("777")
    with mock.patch.object(supervisor.os, "kill") as kill:
        assert supervisor.supervisor_alive(cfg) is True
    kill.assert_called_once_with(777, 0)
    # No pid file -> not alive.
    (cfg.run_dir / "supervisor.pid").unlink()
    assert supervisor.supervisor_alive(cfg) is False


# --------------------------------------------------------------------------
# ensure_supervisor: every "start" surface (UI buttons / MCP / Telegram) must
# get the same heartbeat up_config gives the CLI/dashboard path.
# --------------------------------------------------------------------------


def test_ensure_supervisor_starts_when_running_and_none_alive(tmp_path):
    cfg = _cfg(tmp_path, "  - name: A\n    type: claude\n    command: 'true'\n")
    calls = {}
    with mock.patch.object(supervisor.tmux, "session_exists", lambda s: True), \
         mock.patch.object(supervisor, "supervisor_alive", lambda c: False), \
         mock.patch.object(supervisor, "start_supervisor",
                           lambda c, names: calls.setdefault("names", names)):
        assert supervisor.ensure_supervisor(cfg) is True
    assert calls["names"] == ["A"]  # watches the agents running now


def test_ensure_supervisor_noop_when_supervise_disabled(tmp_path):
    cfg = _cfg(tmp_path, "  - name: A\n    type: claude\n    command: 'true'\n")
    cfg.supervise = False
    with mock.patch.object(supervisor, "start_supervisor",
                           side_effect=AssertionError("must not start")):
        assert supervisor.ensure_supervisor(cfg) is False


def test_ensure_supervisor_noop_when_already_alive(tmp_path):
    cfg = _cfg(tmp_path, "  - name: A\n    type: claude\n    command: 'true'\n")
    with mock.patch.object(supervisor, "supervisor_alive", lambda c: True), \
         mock.patch.object(supervisor, "start_supervisor",
                           side_effect=AssertionError("must not double-start")):
        assert supervisor.ensure_supervisor(cfg) is False


def test_ensure_supervisor_noop_when_nothing_running(tmp_path):
    # No session is up yet -> never spawn a watcher with an empty watch-set.
    cfg = _cfg(tmp_path, "  - name: A\n    type: claude\n    command: 'true'\n")
    with mock.patch.object(supervisor.tmux, "session_exists", lambda s: False), \
         mock.patch.object(supervisor, "supervisor_alive", lambda c: False), \
         mock.patch.object(supervisor, "start_supervisor",
                           side_effect=AssertionError("must not start empty")):
        assert supervisor.ensure_supervisor(cfg) is False


# --------------------------------------------------------------------------
# run_supervisor loop
# --------------------------------------------------------------------------


def test_run_supervisor_exits_when_no_sessions(tmp_path):
    cfg = _cfg(tmp_path, "  - name: A\n    type: claude\n    command: 'true'\n")
    with mock.patch.object(supervisor.tmux, "session_exists", lambda s: False), \
         mock.patch.object(supervisor, "supervise_once") as so, \
         mock.patch.object(supervisor, "sleep_ms", lambda ms: None):
        supervisor.run_supervisor(cfg, ["A"])
    assert not so.called  # nothing left to watch -> exits without reconciling
    # Real _emit ran (logged to the durable log), so the lifecycle events exist.
    events = (cfg.log_dir / "agentainer.jsonl").read_text()
    assert "supervisor-start" in events
    assert "no-watched-sessions" in events


def test_run_supervisor_reconciles_then_exits(tmp_path):
    cfg = _cfg(tmp_path, "  - name: A\n    type: claude\n    command: 'true'\n")
    calls = {"n": 0}

    def _sess(_s):
        calls["n"] += 1
        return calls["n"] == 1  # alive the first tick, gone after

    with mock.patch.object(supervisor.tmux, "session_exists", _sess), \
         mock.patch.object(supervisor, "supervise_once") as so, \
         mock.patch.object(supervisor, "sleep_ms", lambda ms: None):
        supervisor.run_supervisor(cfg, ["A"])
    assert so.call_count == 1


def test_run_supervisor_exits_on_keyboard_interrupt(tmp_path):
    cfg = _cfg(tmp_path, "  - name: A\n    type: claude\n    command: 'true'\n")
    with mock.patch.object(supervisor.tmux, "session_exists", lambda s: True), \
         mock.patch.object(supervisor, "supervise_once", side_effect=KeyboardInterrupt), \
         mock.patch.object(supervisor, "sleep_ms", lambda ms: None):
        supervisor.run_supervisor(cfg, ["A"])
    # The except branch's _emit ran and logged the interrupted lifecycle event.
    events = (cfg.log_dir / "agentainer.jsonl").read_text()
    assert "supervisor-interrupted" in events

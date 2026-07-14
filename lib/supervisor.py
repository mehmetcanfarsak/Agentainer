#!/usr/bin/env python3
"""Agentainer -- the liveness supervisor (the orchestrator's heartbeat).

v1 shipped without this once and had to add it: the swarm is otherwise purely
event-driven -- progress only happens when an agent's capture fires (hook/notify)
or a human sends mail. If that event never arrives (a crashed CLI, a killed tmux
session, a capture that never fired, a "silent but alive" agent whose completion
we cannot trust), nothing wakes the loop and an agent sits on unread mail forever.

This module is the periodic heartbeat that reconciles those failure modes:

  * STALE-BUSY  -- the turn-completion signal (delivered > completed) is older
    than ``busy_timeout_ms``; the hook/notify never fired, so we mark the turn
    finished and let the queue advance.
  * DEAD        -- the tmux session is gone; we warn once (not every tick) and
    reconcile the turn so the agent is not "busy" forever.
  * SILENT-BUT-ALIVE -- the session is up but ``capture == "none"`` (v2 health
    probe), so we have no reliable turn-completion signal; we surface it once.
  * IDLE        -- only then do we process read receipts, release the next queued
    message (one-at-a-time) + nudge, or fire a periodic ping when the inbox would
    be empty. Real mail always takes priority over pings.

Ported from v1 ``lib/swarm.py`` (start_supervisor / stop_supervisor /
supervisor_alive / supervise_once / run_supervisor) and adapted to drive the v2
file-based mailroom. Zero runtime dependencies: stdlib + bundled lib/ only.

Branding: "swarm" is retired -- it's Agentainer everywhere (decision D21).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as cfgmod  # noqa: E402
from config import SwarmConfig  # noqa: E402

import log  # noqa: E402
import mail  # noqa: E402
import tmux  # noqa: E402
import turn  # noqa: E402
from tmux import sleep_ms  # noqa: E402


# PID file lives under the run dir (v1 used run_dir/supervisor.pid). Branded path.
SUPERVISOR_PID = "supervisor.pid"

# The supervise subcommand is the internal plumbing entry the CLI exposes; we keep
# its surface minimal/stable. Launched via the project's launcher so the same
# install resolution (AGENTAINER_HOME) the `./agentainer` script uses applies.
_CLI = Path(__file__).resolve().parent / "cli.py"


# --------------------------------------------------------------------------
# process management: launch / stop / probe the background supervisor
# --------------------------------------------------------------------------


def start_supervisor(cfg: SwarmConfig, names: list[str]) -> None:
    """Launch the background liveness supervisor for *names* (the agents we started)."""
    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    logfile = (cfg.log_dir / "supervisor.log").open("a")
    # Pass the config via AGENTAINER_CONFIG (mirrors v1's SWARM_CONFIG) and export
    # AGENTAINER_HOME so the spawned launcher resolves the install and lib/ path.
    env = dict(os.environ)
    env["AGENTAINER_HOME"] = str(Path(__file__).resolve().parent.parent)
    env["AGENTAINER_CONFIG"] = str(cfg.path)
    proc = subprocess.Popen(
        [sys.executable, str(_CLI), "supervise", *names],
        stdout=logfile,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=env,
        start_new_session=True,
    )
    (cfg.run_dir / SUPERVISOR_PID).write_text(str(proc.pid))


def stop_supervisor(cfg: SwarmConfig) -> None:
    pid_file = cfg.run_dir / SUPERVISOR_PID
    if not pid_file.is_file():
        return
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 15)
    except (OSError, ValueError):
        pass
    pid_file.unlink(missing_ok=True)


def supervisor_alive(cfg: SwarmConfig) -> bool:
    pid_file = cfg.run_dir / SUPERVISOR_PID
    if not pid_file.is_file():
        return False
    try:
        os.kill(int(pid_file.read_text().strip()), 0)
        return True
    except (OSError, ValueError):
        return False


# --------------------------------------------------------------------------
# the heartbeat
# --------------------------------------------------------------------------


def _seen_silent_set(cfg: SwarmConfig) -> set[str]:
    """Return the per-config set of agents currently known silent-but-alive.

    Stored on the config object itself (a process-global "state flag") so the
    fixed ``supervise_once(cfg, names, seen_dead)`` signature need not grow a
    parameter, and the transition (log once, not every tick) survives across
    ticks. A fresh config starts with an empty set.
    """
    s = getattr(cfg, "_seen_silent", None)
    if s is None:
        s = set()
        cfg._seen_silent = s
    return s


def supervise_once(cfg: SwarmConfig, names: list[str], seen_dead: set[str]) -> None:
    """One reconciliation pass over the watched agents.

    Split out of the loop so it can be unit-tested without the timer. Reconciles
    the failure modes the event-driven design otherwise never notices (see the
    module docstring). ``seen_dead`` is an in-memory set (the supervisor is one
    long-lived process) so a dead session is warned about once, not every tick.
    """
    for name in names:
        agent = cfg.get(name)

        # (a) STALE-BUSY. v2's ``turn.busy_info`` already fails open (warns and
        # returns None) when the turn is older than busy_timeout_ms, so read the
        # authoritative turn state directly to detect + recover the wedged agent
        # and record it in the event log.
        state = turn.turn_state(cfg, name)
        if state.get("delivered", 0) > state.get("completed", 0):
            age_ms = (time.time() - state.get("since", 0)) * 1000
            if age_ms > cfg.busy_timeout_ms:
                log.log_event(cfg, name, "stale-busy", age_s=int(age_ms / 1000))
                turn.mark_turn_finished(cfg, name)

        # (b) DEAD session. Don't try to deliver into a pane that no longer
        # exists; reconcile the turn and warn once per transition.
        if not tmux.session_exists(agent.session):
            if name not in seen_dead:
                seen_dead.add(name)
                log.log_event(cfg, name, "dead")
                turn.mark_turn_finished(cfg, name)
            continue
        seen_dead.discard(name)

        # (c) SILENT-BUT-ALIVE. The v2 health probe surfaces an agent that is up
        # in tmux but whose turn-completion signal the orchestrator cannot trust
        # (capture == "none"). Log the transition once; clear it when it resolves.
        hp = turn.health_probe(cfg, agent)
        if hp["silent_but_alive"]:
            silent = _seen_silent_set(cfg)
            if name not in silent:
                silent.add(name)
                log.log_event(cfg, name, "silent-but-alive")
        else:
            _seen_silent_set(cfg).discard(name)

        # (d) IDLE: the only time we push work. Read receipts first, then deliver
        # real queued mail one-at-a-time (nudge), else a periodic ping -- real
        # mail always takes priority over pings.
        if turn.busy_info(cfg, agent) is None:
            mail.process_read_folder(cfg, name)
            # present_current releases the next queued message AND re-nudges a
            # message already sitting unread in the inbox (a nudge whose paste
            # failed once), so the heartbeat actually retries delivery rather
            # than only announcing brand-new releases. Ping only when idle with
            # no mail at all -- real mail always wins over a periodic ping.
            if not mail.present_current(cfg, name):
                mail.maybe_ping(cfg, name)


def _emit(cfg: SwarmConfig, kind: str, msg: str) -> None:
    """Log a supervisor lifecycle event and echo it to stderr for the operator."""
    log.log_event(cfg, "supervisor", kind)
    print(f"supervisor: {msg}", file=sys.stderr)


def run_supervisor(cfg: SwarmConfig, names: list[str]) -> None:
    """Background loop: the heartbeat that keeps one silent agent from wedging the swarm.

    The swarm is otherwise purely event-driven -- progress only happens when an
    agent's capture fires (hook/pane) or a human sends a message. If that event
    never arrives, nothing wakes the loop. This polls on a timer and reconciles
    stale/dead/silent state. It self-exits once every watched session is gone
    (after `down`), so it does not run forever.
    """
    _emit(cfg, "supervisor-start", "started")
    seen_dead: set[str] = set()
    try:
        while True:
            sleep_ms(cfg.supervise_interval_ms)
            if not any(tmux.session_exists(cfg.get(n).session) for n in names):
                _emit(cfg, "no-watched-sessions", "no watched sessions remain, exiting")
                return
            try:
                supervise_once(cfg, names, seen_dead)
            except Exception as exc:  # noqa: BLE001 - one agent's failure must not kill the heartbeat
                # The supervisor exists precisely so one wedged/silent agent
                # can't take down the swarm. A lost release_next race or a
                # bad pane read must be logged and survived, never propagated
                # into the process that keeps the whole swarm alive.
                log.log_event(cfg, "supervisor", "tick-error", error=str(exc))
                print(f"supervisor: tick error (continuing): {exc}", file=sys.stderr)
    except KeyboardInterrupt:
        _emit(cfg, "supervisor-interrupted", "interrupted, exiting")

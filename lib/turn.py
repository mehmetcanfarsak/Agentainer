#!/usr/bin/env python3
"""Agentainer -- per-agent turn-state machinery (ported from v1 swarm.py).

v1 learned that the turn-completion signal is the system clock: when an agent
stops, the orchestrator sweeps its outbox, releases the next queued message, and
re-injects the protocol on a nudge. This module is the durable record of "is
*agent* mid-task right now", persisted as JSON at
``cfg.run_dir / f"{agent}.turn.json"`` holding ``{delivered, completed, since, by}``.

We port v1's proven turns helpers verbatim and add two v2 entry points:

  * ``on_turn_finished`` -- the hook/notify entry point the CLI ``hook`` command
    calls; it marks the turn done and returns the new state so the mailroom can
    sweep.
  * ``health_probe`` -- the v2 "silent-but-alive" probe (plan §8 / D17) that
    surfaces agents whose tmux session is alive but whose turn-completion signal
    the orchestrator cannot trust -- something v1's supervisor could not catch.

Zero runtime dependencies: stdlib + the bundled lib/ modules only.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

try:  # POSIX only, which is fine: tmux is too.
    import fcntl  # noqa: F401 - kept for parity; lock guards the real path
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as cfgmod  # noqa: E402
from config import Agent, SwarmConfig  # noqa: E402
from lock import file_lock  # noqa: E402
from tmux import session_exists, warn  # noqa: E402


def turn_state(cfg: SwarmConfig, agent: str) -> dict:
    """Return the persisted turn state for *agent* (a name string).

    A missing or corrupt file yields the empty default, so every reader sees a
    complete record regardless of whether the agent has ever been messaged.
    """
    try:
        return json.loads((cfg.run_dir / f"{agent}.turn.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {"delivered": 0, "completed": 0, "since": 0, "by": None}


def write_turn_state(cfg: SwarmConfig, agent: str, state: dict) -> None:
    """Persist *state* for *agent* (a name string) as JSON."""
    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    (cfg.run_dir / f"{agent}.turn.json").write_text(json.dumps(state))


def busy_info(cfg: SwarmConfig, agent: Agent) -> dict | None:
    """Return the turn state if *agent* is mid-task, else None. Not locked."""
    if not agent.busy_check:
        return None
    state = turn_state(cfg, agent.name)
    if state.get("delivered", 0) <= state.get("completed", 0):
        return None

    age_ms = (time.time() - state.get("since", 0)) * 1000
    if age_ms > cfg.busy_timeout_ms:
        # The hook never fired -- crashed agent, killed CLI, capture misconfigured.
        # Fail open rather than wedge the swarm forever.
        warn(
            f"{agent.name}: has looked busy for {int(age_ms / 1000)}s "
            f"(over busy_timeout_ms); treating it as idle"
        )
        return None
    state["age_s"] = int(age_ms / 1000)
    return state


def mark_turn_started(cfg: SwarmConfig, agent: str, sender: str) -> None:
    """Record that another message (from *sender*) was just delivered to *agent*."""
    state = turn_state(cfg, agent)
    state["delivered"] = state.get("delivered", 0) + 1
    state["since"] = time.time()
    state["by"] = sender
    write_turn_state(cfg, agent, state)


def mark_turn_finished(cfg: SwarmConfig, agent: str) -> None:
    """The agent finished a turn: everything submitted so far is consumed.

    Clamping (rather than incrementing) keeps the counters from drifting when a
    CLI folds a queued message into the turn already running -- codex does this,
    printing "messages to be submitted after next tool call". Drift would leave
    an agent permanently "busy".
    """
    with file_lock(cfg, agent, "turn.lock"):
        state = turn_state(cfg, agent)
        state["completed"] = state.get("delivered", 0)
        write_turn_state(cfg, agent, state)


def busy_message(cfg: SwarmConfig, agent: Agent, state: dict) -> str:
    """Format a "still busy" reply (from the ``system`` mailbox) for *agent*."""
    by = state.get("by") or "someone"
    return (
        f"{agent.name} is busy right now (working for {state['age_s']}s on a task "
        f"from {by}). Please try again after some time, or put your message in the "
        f"queue and wait for the answer:\n"
        f"  agentainer send --to {agent.name} --queue \"...\"   "
        f"# delivered automatically when {agent.name} is free\n"
        f"  agentainer send --to {agent.name} --wait \"...\"    "
        f"# block here until {agent.name} is free\n"
        f"Meanwhile you are free to do other work."
    )


def on_turn_finished(cfg: SwarmConfig, agent: str) -> dict:
    """Hook/notify entry point: mark *agent*'s turn done and return its state.

    The CLI ``hook`` command calls this, then triggers the mailroom sweep. We
    return the resulting turn-state dict so the caller can act on it (e.g. log
    the event) without re-reading it.
    """
    mark_turn_finished(cfg, agent)
    return turn_state(cfg, agent)


def health_probe(cfg: SwarmConfig, agent: Agent) -> dict:
    """The v2 per-agent "silent-but-alive" probe (plan §8 / D17).

    Returns a snapshot the supervisor can use to surface an agent that is alive
    in tmux yet whose turn-completion signal the orchestrator cannot trust:

      * ``session_alive``   -- tmux.session_exists(agent.session)
      * ``capture``         -- agent.capture
      * ``busy``            -- busy_info(cfg, agent) is not None
      * ``idle_for_ms``     -- now - since in ms when busy, else 0
      * ``silent_but_alive``-- session_alive AND agent.capture == "none": the
        session is up but we have no reliable turn-completion signal, so we
        cannot tell when (or whether) a turn actually ended.
    """
    session_alive = session_exists(agent.session)
    state = busy_info(cfg, agent)
    busy = state is not None
    if busy:
        idle_for_ms = int((time.time() - state.get("since", 0)) * 1000)
    else:
        idle_for_ms = 0
    capture = agent.capture
    silent_but_alive = session_alive and capture == "none"
    return {
        "session_alive": session_alive,
        "capture": capture,
        "busy": busy,
        "idle_for_ms": idle_for_ms,
        "silent_but_alive": silent_but_alive,
    }

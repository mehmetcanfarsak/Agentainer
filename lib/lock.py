#!/usr/bin/env python3
"""Agentainer -- advisory locks for serialising state mutations and pane writes.

Ported verbatim from v1's swarm.py locking helpers. The messaging layer is
file-based (see ``mail.py``); these locks keep that filesystem state from being
torn by concurrent writers (the orchestrator, the supervisor, a web UI) and
keep two pastes from interleaving inside one tmux pane.

Zero runtime dependencies: fcntl + stdlib only.
"""

from __future__ import annotations

import contextlib
import sys
import time

try:  # POSIX only, which is fine: tmux is too.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore

from config import SwarmConfig  # noqa: E402

LOCK_TIMEOUT_S = 180


@contextlib.contextmanager
def file_lock(cfg: SwarmConfig, name: str, what: str = "lock"):
    """An advisory cross-process lock, used to serialise access to one pane/queue.

    Lock ordering, to keep it deadlock-free: queue -> pane -> turn state.
    """
    if fcntl is None:  # pragma: no cover
        yield
        return

    cfg.runtime.mkdir(parents=True, exist_ok=True)
    handle = open(cfg.runtime / f"{name}.{what}", "w")
    deadline = time.monotonic() + LOCK_TIMEOUT_S
    locked = False
    try:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.monotonic() > deadline:
                    print(f"{name}: timed out waiting for the {what}; proceeding anyway", file=sys.stderr)
                    break
                time.sleep(0.05)
        yield
    finally:
        if locked:
            with contextlib.suppress(OSError):
                fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def pane_lock(cfg: SwarmConfig, session: str):
    """Serialise everything that types into one pane.

    A paste and the Enter that submits it are two separate tmux calls. Without a
    lock, a second sender -- another agent, or one of several subagents running
    in parallel inside the same agent -- can paste in between them, so one Enter
    submits two concatenated messages and the other submits nothing.

    The busy check and the "mark this agent busy" write also happen under this
    lock, so two concurrent senders cannot both observe an idle agent and both
    deliver to it.

    The lock is per recipient, so unrelated agents are still messaged in parallel.
    """
    return file_lock(cfg, session, "pane.lock")

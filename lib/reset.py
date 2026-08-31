#!/usr/bin/env python3
"""Agentainer -- swarm reset / start-over (two levels).

The operator-facing "start over" primitive, shared by all four control planes
(CLI ``reset``, UI ``POST /api/swarms/reset``, Telegram ``/reset``, MCP
``reset_swarm``). Two levels:

  * ``state`` (soft) -- delete Agentainer's own data: the per-swarm runtime
    ``.agentainer/`` (recorded conversations, per-agent queue, turn state, the
    durable JSONL log, the run dir) and every agent's five mailbox folders. The
    agents' actual work files are kept. This is the escape hatch from
    default-resume: the next ``up`` starts a fresh conversation for every agent.
  * ``full`` (hard) -- everything ``state`` does, plus delete each agent's
    workspace directory (their produced work / source) and recreate it empty,
    so the swarm is truly blank. Destructive by design; guarded on every surface
    behind an explicit ``full`` and refuses while agents run.

Both levels **refuse while any agent (or the liveness supervisor) is running** --
pulling state out from under a live agent corrupts it -- so callers must bring
the swarm ``down`` first.

Zero runtime deps; imports only sibling ``tmux``/``mail`` (and, lazily,
``supervisor``) so there is no import cycle with ``cli``.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import tmux  # noqa: E402
import mail  # noqa: E402


LEVELS = ("state", "full")


class ResetError(Exception):
    """Raised when a reset is refused (e.g. agents still running) or misused."""


def _supervisor_alive(cfg) -> bool:
    try:
        import supervisor  # lazy: supervisor is the one optionally-absent module

        return supervisor.supervisor_alive(cfg)
    except ImportError:  # pragma: no cover - supervisor is present in a full checkout
        return False


def guard_stopped(cfg) -> None:
    """Raise :class:`ResetError` if any agent session or the supervisor is alive.

    A reset deletes the runtime state and mailboxes; doing that under a live
    agent corrupts it. tmux may be absent (headless test hosts) -- then there is
    nothing running to protect, so the guard passes.
    """
    if shutil.which("tmux"):
        for agent in cfg.agents:
            if tmux.session_exists(agent.session):
                raise ResetError(
                    f"{agent.name} is still running -- bring the swarm down first, "
                    "then reset"
                )
        if _supervisor_alive(cfg):
            raise ResetError(
                "the liveness supervisor is still running -- bring the swarm down first"
            )


def reset_state(cfg) -> list[Path]:
    """Soft reset: delete the runtime ``.agentainer/`` and every mailbox folder.

    Returns the paths actually removed. Keeps the agents' work files and the
    config. Idempotent -- a already-clean swarm removes nothing.
    """
    removed: list[Path] = []
    if cfg.runtime.exists():
        shutil.rmtree(cfg.runtime)
        removed.append(cfg.runtime)
    for agent in cfg.agents:
        mp = cfg.mail_paths(agent)
        for folder in (mp.inbox, mp.outbox, mp.read, mp.sent, mp.failed):
            if folder.exists():
                shutil.rmtree(folder)
                removed.append(folder)
    return removed


def _config_is_inside(directory: Path, cfg_path: Path) -> bool:
    """True if *cfg_path* is *directory* itself or lives anywhere under it.

    A hard wipe must never delete the directory that holds ``agentainer.yaml`` --
    that would erase the swarm's own config (and, for a swarm scaffolded into its
    root, brick it). Resolved so symlinks/relatives can't sneak past.
    """
    d = directory.resolve()
    c = cfg_path.resolve()
    return c == d or d in c.parents


def wipe_all(cfg) -> tuple[list[Path], list[str]]:
    """Hard wipe: soft reset, then delete + recreate each agent's workspace.

    Returns ``(removed_paths, warnings)``. Two safety rails, both of which skip
    (with a warning) rather than delete:

      * a workspace that *contains the config file* -- so ``reset --full`` can
        never destroy the swarm's own ``agentainer.yaml``;
      * a workspace that lives *outside the swarm root* -- so a ``workdir`` pointed
        at, say, ``$HOME`` (whether by design or by a typo) is never ``rmtree``-d.
        The swarm's own files are all under ``root``; anything else is foreign.

    After wiping, the mailbox folders are re-created so the swarm is immediately
    launchable again.
    """
    removed = reset_state(cfg)
    warnings: list[str] = []
    root = cfg.root.resolve()

    seen: set[Path] = set()
    for agent in cfg.agents:
        workdir = agent.workdir.resolve()
        if workdir in seen:
            continue
        seen.add(workdir)
        if not workdir.exists():
            continue
        if _config_is_inside(workdir, cfg.path):
            warnings.append(
                f"kept {workdir} -- it holds the swarm config; its other files "
                "were not deleted"
            )
            continue
        if workdir != root and root not in workdir.parents:
            warnings.append(
                f"kept {workdir} -- it is outside the swarm root {root}; only "
                "workspaces under the root are wiped"
            )
            continue
        shutil.rmtree(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        removed.append(workdir)

    # Re-seed the mailbox folders so `up` (or a UI launch) works straight away.
    mail.init_mailboxes(cfg)
    return removed, warnings


def reset(cfg, level: str = "state") -> dict:
    """Run a reset at *level* (``"state"`` or ``"full"``); return a summary dict.

    The caller is responsible for :func:`guard_stopped` (the surfaces call it so
    they can turn the refusal into a surface-appropriate error). Returns
    ``{"level", "removed": [str], "warnings": [str]}``.
    """
    if level not in LEVELS:
        raise ResetError(f"unknown reset level {level!r} (expected one of {LEVELS})")
    if level == "full":
        removed, warnings = wipe_all(cfg)
    else:
        removed, warnings = reset_state(cfg), []
    return {
        "level": level,
        "removed": [str(p) for p in removed],
        "warnings": warnings,
    }

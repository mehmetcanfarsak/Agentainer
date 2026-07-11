"""Durable event logging for the Agentainer orchestrator.

This module is a faithful port of v1's ``log_event`` / ``archive_message``
helpers from ``lib/swarm.py``. The only change from v1 is branding: the global
event-log file is ``agentainer.jsonl`` (v1 wrote ``swarm.jsonl``).

The global ``.agentainer/logs/agentainer.jsonl`` is the source of truth for
history -- fullscreen TUIs keep no scrollback, so history cannot be recovered
from a pane. Each agent also gets a per-agent ``<agent>.jsonl`` for quick
filtering.

Branding note: "swarm" is retired -- it's Agentainer everywhere (decision D21).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from config import SwarmConfig


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string with second precision."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_event(cfg: SwarmConfig, agent: str, kind: str, **fields) -> None:
    """Append one JSON event record to the per-agent and global logs.

    Each line is ``{"ts": ..., "agent": ..., "kind": ..., **fields}``. The same
    record is written to ``cfg.log_dir/<agent>.jsonl`` for per-agent filtering
    and to ``cfg.log_dir/agentainer.jsonl`` (the global source of truth).
    """
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    record = {"ts": now_iso(), "agent": agent, "kind": kind, **fields}
    with (cfg.log_dir / f"{agent}.jsonl").open("a") as fh:
        fh.write(json.dumps(record) + "\n")
    with (cfg.log_dir / "agentainer.jsonl").open("a") as fh:
        fh.write(json.dumps(record) + "\n")


def archive_message(
    cfg: SwarmConfig,
    agent: str,
    src: Path,
    *,
    subdir: str = "archive",
) -> Path:
    """Move a handled message file into the orchestrator archive and return its path.

    Used by the mailroom's best-effort auto-archive fallback (plan §7) so a
    forgetful model can never wedge the swarm: a message presented N times
    without being handled is moved here and the queue advances. The destination
    is ``cfg.runtime/<subdir>/<agent>/<same-name>``.
    """
    src = Path(src)
    dest_dir = cfg.runtime / subdir / agent
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    shutil.move(str(src), str(dest))
    return dest

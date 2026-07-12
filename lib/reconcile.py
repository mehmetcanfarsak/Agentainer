#!/usr/bin/env python3
"""Agentainer -- P4 dynamic reconcile (add / remove / edit agents at runtime).

This module closes the loop opened by ``up``: it makes the running swarm match
the config *without* a full teardown. The orchestrator owns authoritative state,
so reconcile is the only place that starts/stops sessions to match the YAML.

Three operations, all driven from the tested core (no new orchestration logic):

  * ``diff(cfg)``           -- compare configured agents to running tmux sessions.
  * ``reconcile(cfg, ...)`` -- start agents missing from the running set, stop
                               sessions that are no longer in the config.
  * ``add_agent`` / ``remove_agent`` / ``edit_agent`` -- mutate the YAML on disk
                               (a minimal stdlib emitter, so it works with OR
                               without PyYAML), then return a re-loaded config so
                               the caller can ``reconcile`` the change into effect.

The HTTP UI and the CLI both call these; the UI is the human-facing control
plane that triggers them.

Hard invariants (see CLAUDE.md + ProjectPlan.md §24):
  * Zero runtime deps. The YAML writer below never imports PyYAML -- it only
    *reads* via PyYAML when present and falls back to ``minyaml`` otherwise, but
    it always *writes* with the bundled emitter so the no-PyYAML path stays live.
  * ``can_talk_to`` stays a cooperative ACL -- reconcile never relaxes it.
  * Reconcile is best-effort about tmux: a session that won't start is reported,
    not fatal.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import config as cfgmod  # noqa: E402
from config import ConfigError  # noqa: E402
import tmux  # noqa: E402
import mail  # noqa: E402
import hooks  # noqa: E402
import turn  # noqa: E402
import log  # noqa: E402


# --------------------------------------------------------------------------
# logging (self-contained; mirrors cli.info/warn so reconcile has no cli dep)
# --------------------------------------------------------------------------


def info(msg: str) -> None:
    print(f":: {msg}", file=sys.stderr)


def warn(msg: str) -> None:
    print(f"!! {msg}", file=sys.stderr)


# --------------------------------------------------------------------------
# YAML read / write (write path is stdlib-only, no PyYAML)
# --------------------------------------------------------------------------


def have_yaml() -> bool:
    """True iff PyYAML is importable (used only for *reading*)."""
    try:
        import yaml  # noqa: F401

        return True
    except Exception:
        return False


def load_raw(path) -> dict:
    """Parse *path* into a plain dict using PyYAML if present, else minyaml."""
    text = Path(path).read_text()
    if have_yaml():
        import yaml

        return yaml.safe_load(text) or {}
    import minyaml

    return minyaml.load(text) or {}


def _scalar(v) -> str:
    """Render a scalar for the stdlib YAML emitter."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if s == "":
        return '""'
    # Quote anything that isn't a safe bare token (so "a,b" stays a string, etc).
    if re.fullmatch(r"[A-Za-z0-9_./@:+-]+", s):
        return s
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _dump(data, indent: int = 0) -> str:
    """Minimal stdlib YAML serializer for Agentainer configs.

    Handles exactly the shapes Agentainer configs use: nested mappings, lists of
    scalars, and lists of mappings (the ``agents:`` block). It is intentionally
    small -- the config schema is closed, so we don't need a general emitter.
    """
    pad = "  " * indent
    lines: list[str] = []
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict):
                if v:
                    lines.append(f"{pad}{k}:")
                    lines.append(_dump(v, indent + 1))
                else:
                    lines.append(f"{pad}{k}: {{}}")
            elif isinstance(v, list):
                if v:
                    lines.append(f"{pad}{k}:")
                    lines.append(_dump(v, indent + 1))
                else:
                    lines.append(f"{pad}{k}: []")
            else:
                lines.append(f"{pad}{k}: {_scalar(v)}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item:
                sub = _dump(item, indent + 1).splitlines()
                sub_indent = "  " * (indent + 1)
                first = True
                for line in sub:
                    if first:
                        # "- key: val" at the list level, drop one indent.
                        lines.append(pad + "- " + line[len(sub_indent):])
                        first = False
                    else:
                        lines.append(line)
            else:
                lines.append(f"{pad}- {_scalar(item)}")
    return "\n".join(lines)


def write_raw(path, data: dict) -> None:
    """Serialize *data* to *path* with the stdlib emitter (no PyYAML needed)."""
    Path(path).write_text(_dump(data) + "\n")


# --------------------------------------------------------------------------
# config mutation
# --------------------------------------------------------------------------


def _coerce_field(key: str, value: str):
    """Turn a CLI string into the right Python value for *key*.

    ``can_talk_to`` becomes a list (``*`` stays the wildcard string). Numeric
    fields parse to int; ``true``/``false`` to bool; everything else stays str.
    """
    if key == "can_talk_to":
        if value.strip() == "*":
            return "*"
        return [p.strip() for p in value.split(",") if p.strip()]
    low = value.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    if re.fullmatch(r"-?\d+", value.strip()):
        return int(value.strip())
    if re.fullmatch(r"-?\d+\.\d+", value.strip()):
        return float(value.strip())
    return value


def add_agent(cfg, name, type_, command, can_talk_to, role="", workdir=None, **extra) -> "cfgmod.SwarmConfig":
    """Append *name* to the config on disk and return a re-loaded SwarmConfig.

    Raises ``ValueError`` if the agent already exists. ``can_talk_to`` may be a
    list or the ``"*"`` wildcard string.
    """
    raw = load_raw(cfg.path)
    agents = list(raw.get("agents") or [])
    if any(str(a.get("name")) == name for a in agents):
        raise ValueError(f"agent {name!r} already exists")

    entry = {
        "name": name,
        "type": type_,
        "command": command,
        "can_talk_to": can_talk_to,
    }
    if role:
        entry["role"] = role
    if workdir:
        entry["workdir"] = str(workdir)
    for k, v in extra.items():
        entry[k] = v
    agents.append(entry)
    raw["agents"] = agents
    write_raw(cfg.path, raw)
    return cfgmod.load(cfg.path)


def remove_agent(cfg, name) -> "cfgmod.SwarmConfig":
    """Drop *name* from the config on disk and return a re-loaded SwarmConfig.

    Raises ``ValueError`` if the agent is not present. The caller is expected to
    reconcile afterwards to tear down the now-orphaned session.
    """
    raw = load_raw(cfg.path)
    agents = list(raw.get("agents") or [])
    kept = [a for a in agents if str(a.get("name")) != name]
    if len(kept) == len(agents):
        raise ValueError(f"agent {name!r} not found")
    raw["agents"] = kept
    write_raw(cfg.path, raw)
    return cfgmod.load(cfg.path)


def edit_swarm(cfg, **fields) -> "cfgmod.SwarmConfig":
    """Update swarm-level settings on disk and return a re-loaded SwarmConfig.

    Values arrive already typed (bools/ints/strings from the JSON control plane),
    so -- unlike ``edit_agent`` -- they are written through verbatim. Writing uses
    the stdlib emitter so the no-PyYAML path stays live.
    """
    raw = load_raw(cfg.path)
    swarm = dict(raw.get("swarm") or {})
    for k, v in fields.items():
        swarm[k] = v
    raw["swarm"] = swarm
    write_raw(cfg.path, raw)
    return cfgmod.load(cfg.path)


def edit_telegram(cfg, **fields) -> "cfgmod.SwarmConfig":
    """Update the top-level ``telegram:`` block on disk; return a reloaded config.

    Values arrive already typed from the JSON control plane, so they are written
    through verbatim (the stdlib emitter keeps the no-PyYAML path live).
    """
    raw = load_raw(cfg.path)
    tg = dict(raw.get("telegram") or {})
    for k, v in fields.items():
        tg[k] = v
    raw["telegram"] = tg
    write_raw(cfg.path, raw)
    return cfgmod.load(cfg.path)


def edit_agent(cfg, name, **fields) -> "cfgmod.SwarmConfig":
    """Update *name*'s fields on disk and return a re-loaded SwarmConfig.

    Field values are coerced by ``_coerce_field`` (so ``--set can_talk_to=a,b``
    becomes a list, ``--set boot_delay_ms=500`` becomes an int). Unknown agents
    raise ``ValueError``.
    """
    raw = load_raw(cfg.path)
    found = False
    for a in raw.get("agents") or []:
        if str(a.get("name")) == name:
            for k, v in fields.items():
                a[k] = _coerce_field(k, str(v))
            found = True
            break
    if not found:
        raise ValueError(f"agent {name!r} not found")
    write_raw(cfg.path, raw)
    return cfgmod.load(cfg.path)


# --------------------------------------------------------------------------
# reconcile (the runtime diff)
# --------------------------------------------------------------------------


def _running_sessions(prefix: str) -> list[str]:
    """Names of live tmux sessions whose name starts with *prefix*."""
    try:
        out = tmux.tmux(
            "list-sessions", "-F", "#{session_name}", check=False, capture=True
        ).stdout or ""
    except Exception:
        return []
    return [s.strip() for s in out.splitlines() if s.strip().startswith(prefix)]


def _agent_for_session(cfg, session_name: str):
    """Map a running session name back to a configured agent, or None."""
    cand = session_name[len(cfg.session_prefix):]
    return cfg.get(cand) if cand in cfg.names() else None


def diff(cfg) -> dict:
    """Compare configured agents to running tmux sessions.

    Returns ``{configured, running, missing, extra}`` -- the agent names that
    are configured, currently running, configured-but-not-running (missing), and
    running-but-not-configured (extra sessions to stop).
    """
    running = [a.name for a in cfg.agents if tmux.session_exists(a.session)]
    configured = [a.name for a in cfg.agents]
    extras = [
        s for s in _running_sessions(cfg.session_prefix) if _agent_for_session(cfg, s) is None
    ]
    missing = [n for n in configured if n not in running]
    return {
        "configured": sorted(configured),
        "running": sorted(running),
        "missing": sorted(missing),
        "extra": sorted(extras),
    }


def _start_agent(cfg, agent, start_fn) -> None:
    """Bring *agent* up: ensure dirs, then launch via *start_fn* (launch_agent_full)."""
    for directory in (cfg.runtime, cfg.log_dir, cfg.queue_dir, cfg.run_dir):
        directory.mkdir(parents=True, exist_ok=True)
    mail.init_mailboxes(cfg)
    start_fn(cfg, agent, None)


def reconcile(cfg, *, start_missing: bool = True, stop_extra: bool = True, _start_fn=None) -> dict:
    """Make the running swarm match *cfg*.

    Starts agents that are configured but not running, and stops tmux sessions
    that are running but no longer configured. Returns a summary dict.

    ``_start_fn`` is injectable (defaults to ``cli.launch_agent_full``) so tests
    can observe the start path without a real tmux session.
    """
    start_fn = _start_fn
    if start_fn is None:
        import cli as _cli  # lazy: avoid an import cycle with cli <-> reconcile

        start_fn = _cli.launch_agent_full

    d = diff(cfg)
    started: list[str] = []
    stopped: list[str] = []

    if start_missing:
        for name in d["missing"]:
            agent = cfg.get(name)
            _start_agent(cfg, agent, start_fn)
            started.append(name)
            info(f"reconcile: started {name}")

    if stop_extra:
        for session_name in d["extra"]:
            tmux.tmux("kill-session", "-t", f"={session_name}", check=False, capture=True)
            stopped.append(session_name)
            info(f"reconcile: stopped extra session {session_name}")

    return {
        "started": started,
        "stopped": stopped,
        "running": d["running"],
        "missing": d["missing"],
        "extra": d["extra"],
    }


def start_one(cfg, name: str, *, _start_fn=None) -> bool:
    """Bring a single configured agent up if its tmux session isn't running.

    Returns True if the agent was (re)launched, False if it was already running.
    Raises KeyError via ``cfg.get`` for an unknown name.
    """
    agent = cfg.get(name)
    if tmux.session_exists(agent.session):
        return False
    start_fn = _start_fn
    if start_fn is None:
        import cli as _cli  # lazy: avoid an import cycle with cli <-> reconcile

        start_fn = _cli.launch_agent_full
    _start_agent(cfg, agent, start_fn)
    info(f"start_one: started {name}")
    return True


def stop_one(cfg, name: str) -> bool:
    """Kill a single agent's tmux session if it's running (config is untouched).

    Returns True if a running session was killed, False if it was already down.
    Raises ConfigError via ``cfg.get`` for an unknown name.
    """
    agent = cfg.get(name)
    if not tmux.session_exists(agent.session):
        return False
    tmux.tmux("kill-session", "-t", f"={agent.session}", check=False, capture=True)
    info(f"stop_one: stopped {name}")
    return True


# --------------------------------------------------------------------------
# CLI handlers
# --------------------------------------------------------------------------


def _parse_can_talk_to(raw: str):
    raw = (raw or "").strip()
    if raw == "*" or raw == "":
        return raw or []
    return [p.strip() for p in raw.split(",") if p.strip()]


def cmd_add(args) -> int:
    cfg = cfgmod.load(args.config)
    can_talk_to = _parse_can_talk_to(args.can_talk_to)
    new_cfg = add_agent(
        cfg,
        args.name,
        args.type,
        args.command,
        can_talk_to,
        role=args.role or "",
        workdir=args.workdir,
    )
    result = reconcile(new_cfg)
    info(f"added agent {args.name!r}; reconcile started {result['started']} stopped {result['stopped']}")
    return 0


def cmd_remove(args) -> int:
    cfg = cfgmod.load(args.config)
    if args.name not in cfg.names():
        warn(f"agent {args.name!r} not found in config")
        return 1
    # Stop the session first so it isn't orphaned, then drop it from the config.
    agent = cfg.get(args.name)
    if tmux.session_exists(agent.session):
        tmux.tmux("kill-session", "-t", f"={agent.session}", check=False, capture=True)
        info(f"stopped {args.name}")
    new_cfg = remove_agent(cfg, args.name)
    result = reconcile(new_cfg)
    info(f"removed agent {args.name!r}; reconcile stopped {result['stopped']}")
    return 0


def cmd_edit(args) -> int:
    cfg = cfgmod.load(args.config)
    fields = {}
    for pair in args.set or []:
        if "=" not in pair:
            warn(f"--set value {pair!r} is not key=value; skipping")
            continue
        k, v = pair.split("=", 1)
        fields[k.strip()] = v
    if not fields:
        warn("edit: no --set key=value pairs given")
        return 1
    new_cfg = edit_agent(cfg, args.name, **fields)
    result = reconcile(new_cfg)
    info(f"edited {args.name!r}; reconcile started {result['started']} stopped {result['stopped']}")
    return 0


def cmd_reconcile(args) -> int:
    cfg = cfgmod.load(args.config)
    result = reconcile(cfg)
    info(
        f"reconcile: running={result['running']} "
        f"started={result['started']} stopped={result['stopped']} "
        f"missing={result['missing']} extra={result['extra']}"
    )
    return 0

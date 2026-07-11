"""Session / resume machinery for the Agentainer orchestrator.

A faithful port of v1's ``lib/swarm.py`` session helpers (``read_sessions``,
``write_sessions``, ``record_session``, ``codex_session``) and the lifecycle
helpers ``session_env`` / ``resume_command``, adapted to the v2 branding:

  * ``SWARM_HOME`` -> ``AGENTAINER_HOME``, ``SWARM_ROOT`` -> ``AGENTAINER_ROOT``,
    etc. on every env var name and log string;
  * "swarm" -> "agentainer" in every message;
  * the session file moved to ``cfg.sessions_file`` (``.agentainer/sessions.yaml``),
    which ``config.py`` already exposes.

The YAML session file is the bridge that lets ``agentainer up --resume`` reattach
each agent to its own conversation after a restart. It is written atomically
because the turn-completion hooks write to it concurrently.

Zero runtime dependencies: stdlib + our own ``config`` / ``minyaml`` / ``tmux``
helpers only. PyYAML is used when importable, otherwise the bundled ``minyaml``
subset parser (imported through ``config.parse_yaml`` so the two paths stay in
parity). The hand-written ``yaml_dump`` keeps the no-PyYAML path alive for writes.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import Agent, SwarmConfig, parse_yaml
from tmux import file_lock

# Repo root: AGENTAINER_HOME overrides, else this file's grandparent (lib/..).
AGENTAINER_HOME = Path(
    os.environ.get("AGENTAINER_HOME") or Path(__file__).resolve().parent.parent
)


# --------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string with second precision."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def info(msg: str) -> None:
    print(f"\033[36m::\033[0m {msg}", file=sys.stderr)


def warn(msg: str) -> None:
    print(f"\033[33m!!\033[0m {msg}", file=sys.stderr)


# --------------------------------------------------------------------------
# sessions.yaml -- the conversation id of every agent, so `up --resume` works
# --------------------------------------------------------------------------


def yaml_scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def yaml_dump(data: dict, indent: int = 0) -> str:
    """Emit the small subset we need. Written by hand so PyYAML stays optional."""
    pad = " " * indent
    out = []
    for key, value in data.items():
        if isinstance(value, dict):
            out.append(f"{pad}{key}:")
            out.append(yaml_dump(value, indent + 2) if value else f"{pad}  {{}}")
        else:
            out.append(f"{pad}{key}: {yaml_scalar(value)}")
    return "\n".join(out)


def read_sessions(cfg: SwarmConfig) -> dict:
    """The agents block of sessions.yaml, or {} if it is missing or unreadable."""
    try:
        data = parse_yaml(cfg.sessions_file.read_text())
    except OSError:
        return {}
    except Exception as exc:  # noqa: BLE001 - a corrupt file must not stop the swarm
        warn(f"could not parse {cfg.sessions_file}: {exc}")
        return {}
    if not isinstance(data, dict):
        return {}
    return data.get("agents") or {}


def write_sessions(cfg: SwarmConfig, agents: dict) -> None:
    cfg.runtime.mkdir(parents=True, exist_ok=True)
    header = (
        "# Agentainer session state -- written automatically as agents work.\n"
        "# `agentainer up --resume` reads this to reattach each agent to its own\n"
        "# conversation after a restart. Safe to delete; you then start fresh.\n"
    )
    body = yaml_dump(
        {
            "swarm": cfg.name,
            "config": str(cfg.path),
            "updated_at": now_iso(),
            "agents": agents or {},
        }
    )
    tmp = cfg.sessions_file.with_suffix(".yaml.tmp")
    tmp.write_text(header + body + "\n")
    os.replace(tmp, cfg.sessions_file)  # atomic: hooks write this concurrently


def record_session(cfg: SwarmConfig, agent: Agent, session_id, **fields) -> None:
    """Merge this agent's conversation id into sessions.yaml, under a lock."""
    if not session_id:
        return
    with file_lock(cfg, "sessions", "lock"):
        agents = read_sessions(cfg)
        entry = agents.get(agent.name) or {}
        if entry.get("session_id") == session_id:
            return  # unchanged: do not rewrite the file after every single turn
        entry.update({k: v for k, v in fields.items() if v})
        entry["session_id"] = session_id
        entry["type"] = agent.type
        entry["workdir"] = str(agent.workdir)
        entry["updated_at"] = now_iso()
        agents[agent.name] = entry
        write_sessions(cfg, agents)
    info(f"{agent.name}: recorded conversation {session_id}")


def codex_session(agent: Agent) -> tuple[str | None, str | None]:
    """Find the id of the codex conversation running in this agent's CODEX_HOME.

    Codex does not hand its session id to the notify program, but it writes one
    rollout file per session under CODEX_HOME/sessions, and the newest of those is
    the conversation currently in progress.
    """
    sessions = agent.workdir / ".codex" / "sessions"
    if not sessions.is_dir():
        return None, None

    rollouts = sorted(sessions.rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not rollouts:
        return None, None

    newest = rollouts[-1]
    try:
        with newest.open() as fh:
            first = fh.readline()
        record = json.loads(first)
        if record.get("type") == "session_meta":
            payload = record.get("payload", {})
            return payload.get("session_id") or payload.get("id"), str(newest)
    except (OSError, json.JSONDecodeError):
        pass
    return None, str(newest)


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------


def session_env(cfg: SwarmConfig, agent: Agent, extra: dict[str, str]) -> dict[str, str]:
    """The environment given to an agent's tmux session.

    Always includes the authoritative Agentainer locations (``AGENTAINER_HOME``,
    ``AGENTAINER_ROOT`` == ``cfg.root``, the config path, the swarm name, and the
    agent's own name/session/peers), then the agent's own ``env`` block, then any
    caller-supplied ``extra`` (e.g. capture-hook vars from ``hooks.install_capture``
    when the agent is launched).
    """
    env = {
        "AGENTAINER_HOME": str(AGENTAINER_HOME),
        "AGENTAINER_CONFIG": str(cfg.path),
        "AGENTAINER_ROOT": str(cfg.root),
        "AGENTAINER_NAME": cfg.name,
        "AGENTAINER_AGENT": agent.name,
        "AGENTAINER_SESSION": agent.session,
        "AGENTAINER_PEERS": ",".join(agent.can_talk_to),
    }
    env.update(agent.env)
    env.update(extra)
    return env


def resume_command(cfg: SwarmConfig, agent: Agent, session_id: str) -> str | None:
    """The command that reattaches *agent* to conversation *session_id*.

    ``resume_command`` (an exact recipe) wins, because a command like
    ``bash -ic chy3`` invokes the CLI through an alias and flags cannot simply be
    appended to it. Failing that, ``resume_args`` is formatted and appended to the
    agent's command. Agents whose type has no recoverable session (gemini/hermes --
    a session id cannot be scraped from a pane) have no recipe, so we warn and start
    a fresh conversation; a malformed recipe is treated the same way.
    """
    try:
        if agent.resume_command:
            return agent.resume_command.format(session_id=session_id, command=agent.command)
        if agent.resume_args:
            return f"{agent.command} {agent.resume_args.format(session_id=session_id)}"
    except (KeyError, IndexError, ValueError) as exc:
        warn(
            f"{agent.name}: resume recipe is malformed ({exc}); "
            "starting a fresh conversation"
        )
        return None
    warn(
        f"{agent.name}: type {agent.type} has no resume recipe; "
        "starting a fresh conversation"
    )
    return None

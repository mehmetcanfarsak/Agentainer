"""Load, normalise and validate an Agentainer YAML config.

This is the contract anchor every other module imports. It ports v1's proven
config loader but adapts the schema to the v2 file-based mail model:

  * the agent's standing instructions are now ``role`` (``first_prompt`` is a
    deprecated alias);
  * the XML-envelope comms/reply-reminder machinery is gone -- v2 is plain
    natural-language mail files read/written by the model;
  * per-agent ``mail_dir`` selects where the four mailbox folders live, with
    automatic namespacing when two agents share one workspace (see ``mail_paths``);
  * ``user``/``system`` are reserved virtual mailboxes, never agent names;
  * new ``type`` <-> ``command`` mismatch detection prevents the silent-deadlock
    footgun carried forward from v1.

The orchestrator owns all routing/ACL/state; the model only reads and writes
natural-language files. See ProjectPlan.md (§4-§16, §24, §29).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

try:  # pragma: no cover - exercised by whichever branch is installed
    import yaml as _yaml

    def _parse_yaml(text: str):
        return _yaml.safe_load(text)

except ImportError:  # pragma: no cover
    import minyaml as _yaml  # type: ignore

    def _parse_yaml(text: str):
        return _yaml.load(text)


def parse_yaml(text: str):
    """Parse YAML with PyYAML if present, otherwise the bundled subset parser."""
    return _parse_yaml(text)


class ConfigError(Exception):
    pass


# The four real coding-agent CLIs. A `command` that launches a *different* one
# than `type` implies will never fire its turn-completion signal and wedges the
# agent. Used by the mismatch detector below. A mock command (e.g.
# `bash -c 'while true; do read ...'`) contains none of these tokens and passes.
CLI_TOKENS = ("claude", "codex", "gemini", "hermes")

# Built-in knowledge about each supported coding agent. `capture` says how we
# learn that the agent finished a turn:
#   hook  -- the CLI can call an external program on turn completion
#   pane  -- no such facility; we poll the tmux pane and diff it
#   none  -- do not capture at all
BUILTIN_AGENT_TYPES: dict[str, dict[str, Any]] = {
    "claude": {
        "command": "claude --dangerously-skip-permissions",
        "capture": "hook",
        "boot_delay_ms": 3000,
        # Appended to `command` by `up --resume`. {session_id} is the recorded id.
        "resume_args": "--resume {session_id}",
    },
    "codex": {
        "command": "codex --yolo",
        "capture": "hook",
        "boot_delay_ms": 3000,
        "resume_args": "resume {session_id}",
    },
    "gemini": {
        "command": "gemini --yolo",
        "capture": "pane",
        "boot_delay_ms": 4000,
        # No session id is recoverable from a scraped pane, so no resume recipe.
    },
    "hermes": {
        "command": "hermes",
        "capture": "pane",
        "boot_delay_ms": 3000,
    },
}

VALID_CAPTURE = ("hook", "pane", "none", "auto")

NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")


@dataclass
class Agent:
    name: str
    type: str
    command: str
    workdir: Path
    session: str
    capture: str
    boot_delay_ms: int
    role: str
    can_talk_to: list[str]
    mail_dir: Path
    periodically_ping_seconds: int = 0
    periodically_ping_message: str = ""
    resume_args: str | None = None
    resume_command: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    create_workdir: bool = True
    ready_probe: bool = True
    busy_check: bool = True


@dataclass
class SwarmConfig:
    path: Path
    name: str
    root: Path
    session_prefix: str
    agents: list[Agent]
    # Paste-timing knobs consumed by lib/tmux.py (keep in sync with that module).
    enter_delay_ms: int = 250
    send_delay_ms: int = 150
    supervise: bool = True
    supervise_interval_ms: int = 15000
    ready_timeout_ms: int = 60000
    busy_timeout_ms: int = 900000
    resume: bool = False
    user_available: bool = False
    pane_idle_ms: int = 2500
    pane_poll_ms: int = 700
    pane_scrollback: int = 400
    tmux_history_limit: int = 50000
    tmux_mouse: bool = True
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Set of resolved workdirs shared by 2+ agents. Computed up front so
        # `mail_paths` can namespace mailbox folders to avoid collisions.
        counts: dict[Path, int] = {}
        for agent in self.agents:
            key = agent.workdir.resolve()
            counts[key] = counts.get(key, 0) + 1
        self._shared: set[Path] = {d for d, n in counts.items() if n > 1}

    @property
    def runtime(self) -> Path:
        """Orchestrator-private state (logs, queue, run, sessions)."""
        return self.root / ".agentainer"

    @property
    def log_dir(self) -> Path:
        return self.runtime / "logs"

    @property
    def queue_dir(self) -> Path:
        # Per-agent pending-message buffer (one-at-a-time release lives here).
        # Not created by the config layer -- just the authoritative path.
        return self.runtime / "queue"

    @property
    def run_dir(self) -> Path:
        return self.runtime / "run"

    @property
    def sessions_file(self) -> Path:
        """Where each agent's conversation id is recorded, so `up --resume` works."""
        return self.runtime / "sessions.yaml"

    def get(self, name: str) -> Agent:
        for agent in self.agents:
            if agent.name == name:
                return agent
        known = ", ".join(a.name for a in self.agents)
        raise ConfigError(f"unknown agent {name!r} (known agents: {known})")

    def names(self) -> list[str]:
        return [a.name for a in self.agents]

    def mail_paths(self, agent: Agent) -> SimpleNamespace:
        """Resolve the five mailbox folders for *agent* (plan §16).

        Base is ``agent.mail_dir``. When the agent's workdir is shared by more
        than one agent, every folder is prefixed with ``<name>-`` to avoid
        collisions. The model never sees this -- every nudge/first-prompt is
        handed the exact computed paths.
        """
        base = agent.mail_dir
        prefix = agent.name + "-" if agent.workdir.resolve() in self._shared else ""
        return SimpleNamespace(
            inbox=base / (prefix + "inbox"),
            outbox=base / (prefix + "outbox"),
            read=base / (prefix + "read"),
            sent=base / (prefix + "sent"),
            failed=base / (prefix + "failed"),
        )


def _as_list(value: Any, ctx: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    raise ConfigError(f"{ctx}: expected a string or a list, got {type(value).__name__}")


def _as_bool(value: Any, default: bool, ctx: str) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ConfigError(f"{ctx}: expected true/false, got {value!r}")


def _as_str_map(value: Any, ctx: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{ctx}: expected a mapping")
    return {str(k): str(v) for k, v in value.items()}


def _expand_path(raw: Any, ctx: str, name: str, root: Path, swarm_name: str,
                 atype: str, cfg_parent: Path) -> Path:
    """Expand an optional {name}/{root}/{swarm}/{type} placeholder and resolve
    the result relative to the config file's parent directory."""
    text = str(raw)
    try:
        expanded = text.format(name=name, root=str(root), swarm=swarm_name, type=atype)
    except (KeyError, IndexError) as exc:
        raise ConfigError(
            f"{ctx}: unknown placeholder in {raw!r}: {exc}. "
            "Available: {name} {root} {swarm} {type}"
        ) from exc
    path = Path(os.path.expanduser(expanded))
    if not path.is_absolute():
        path = (cfg_parent / path).resolve()
    return path


def load(path: str | os.PathLike) -> SwarmConfig:
    cfg_path = Path(path).expanduser().resolve()
    if not cfg_path.is_file():
        raise ConfigError(
            f"config file not found: {cfg_path}\n"
            "   Create one with:  cp agentainer.example.yaml agentainer.yaml\n"
            "   Or point at it:   agentainer -c /path/to/agentainer.yaml up"
        )

    try:
        data = _parse_yaml(cfg_path.read_text())
    except Exception as exc:  # noqa: BLE001 - surface parser errors verbatim
        raise ConfigError(f"could not parse {cfg_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"{cfg_path}: top level must be a mapping")

    swarm = data.get("swarm") or {}
    if not isinstance(swarm, dict):
        raise ConfigError("`swarm:` must be a mapping")

    defaults = data.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ConfigError("`defaults:` must be a mapping")

    # Agent type registry: built-ins, overridable and extensible from YAML.
    types: dict[str, dict[str, Any]] = {
        k: dict(v) for k, v in BUILTIN_AGENT_TYPES.items()
    }
    for tname, tconf in (data.get("agent_types") or {}).items():
        if not isinstance(tconf, dict):
            raise ConfigError(f"agent_types.{tname}: must be a mapping")
        types.setdefault(tname, {}).update(tconf)

    root_raw = swarm.get("root") or "./workspace"
    root = Path(os.path.expanduser(str(root_raw)))
    if not root.is_absolute():
        root = (cfg_path.parent / root).resolve()

    prefix = str(swarm.get("session_prefix") or "")
    swarm_name = str(swarm.get("name") or cfg_path.stem)
    create_workdirs = _as_bool(swarm.get("create_workdirs"), True, "swarm.create_workdirs")

    raw_agents = data.get("agents")
    if not raw_agents:
        raise ConfigError("`agents:` must contain at least one agent")
    if not isinstance(raw_agents, list):
        raise ConfigError("`agents:` must be a list")

    # Default mailbox base, if any (overridable per agent).
    default_mail_dir_raw = defaults.get("mail_dir")

    # Pass 1: materialise agents without resolving peer references.
    agents: list[Agent] = []
    seen: set[str] = set()
    # Warnings collected while resolving each agent (capture upgrades + deprecations).
    agent_warnings: list[str] = []
    for index, raw in enumerate(raw_agents):
        if not isinstance(raw, dict):
            raise ConfigError(f"agents[{index}]: must be a mapping")
        name = str(raw.get("name") or "").strip()
        if not name:
            raise ConfigError(f"agents[{index}]: missing `name`")
        if not NAME_RE.match(name):
            raise ConfigError(
                f"agent {name!r}: name must match {NAME_RE.pattern} "
                "(it is used as a tmux session name and a directory name)"
            )
        if name in ("user", "system"):
            raise ConfigError(
                f"agent {name!r}: user and system are reserved virtual mailboxes "
                "and cannot be agent names"
            )
        if name in seen:
            raise ConfigError(f"duplicate agent name: {name!r}")
        seen.add(name)

        atype = str(raw.get("type") or defaults.get("type") or "claude")
        if atype not in types:
            raise ConfigError(
                f"agent {name!r}: unknown type {atype!r}. "
                f"Known types: {', '.join(sorted(types))}. "
                "Define new ones under `agent_types:`."
            )
        tconf = types[atype]

        command = raw.get("command") or tconf.get("command")
        if not command:
            raise ConfigError(f"agent {name!r}: no `command` and type {atype!r} has none")

        # type <-> command mismatch: a command that launches a DIFFERENT agent
        # CLI than `type` implies will never fire its turn-completion signal and
        # pins the agent "busy" forever. Lenient for key-free mocks (word-boundaries).
        if atype in CLI_TOKENS:
            cmd_lower = str(command).lower()
            for token in CLI_TOKENS:
                if token == atype:
                    continue
                if re.search(r"\b" + re.escape(token) + r"\b", cmd_lower):
                    raise ConfigError(
                        f"agent {name!r}: type: {atype} but command launches "
                        f"{token!r} ({command!r}). The command must launch the same "
                        f"agent CLI as `type`, or the turn-completion signal will "
                        f"never fire and the agent will hang. Fix `command` or `type`."
                    )

        capture = str(raw.get("capture") or defaults.get("capture") or "auto")
        if capture not in VALID_CAPTURE:
            raise ConfigError(
                f"agent {name!r}: capture must be one of {', '.join(VALID_CAPTURE)}"
            )
        if capture == "auto":
            capture = str(tconf.get("capture") or "pane")
        # capture: none on a type that HAS a completion hook (claude/codex) removes
        # the agent's only turn-completion signal and leaves the orchestrator blind
        # to a silent turn -- which can wedge the whole swarm. Auto-upgrade to the
        # type's natural capture so the hook keeps the orchestrator informed.
        if capture == "none" and str(tconf.get("capture")) == "hook":
            capture = "hook"
            agent_warnings.append(
                f"agent {name!r}: capture: none on a {atype} agent gives the "
                f"orchestrator no turn-completion signal -- auto-upgraded to "
                f"capture: hook."
            )

        boot = raw.get("boot_delay_ms")
        if boot is None:
            boot = defaults.get("boot_delay_ms")
        if boot is None:
            boot = tconf.get("boot_delay_ms", 5000)

        # Standing instructions. `role` is the v2 field; `first_prompt` is a
        # deprecated alias (warn). `first_prompt_file` is likewise deprecated.
        role = raw.get("role")
        prompt_file = raw.get("first_prompt_file")
        if prompt_file and (role is not None or raw.get("first_prompt") is not None):
            raise ConfigError(
                f"agent {name!r}: set either `role`/`first_prompt` or "
                "`first_prompt_file`, not both"
            )
        if prompt_file:
            fp = Path(os.path.expanduser(str(prompt_file)))
            if not fp.is_absolute():
                fp = cfg_path.parent / fp
            if not fp.is_file():
                raise ConfigError(f"agent {name!r}: first_prompt_file not found: {fp}")
            role = fp.read_text()
        if "first_prompt" in raw:
            agent_warnings.append(
                f"agent {name!r}: `first_prompt` is deprecated; use `role`"
            )
        if "first_prompt_file" in raw:
            agent_warnings.append(
                f"agent {name!r}: `first_prompt_file` is deprecated; use `role` "
                "(write the text directly, or read the file yourself)"
            )
        role = (role or raw.get("first_prompt") or "").strip()

        workdir_raw = raw.get("workdir") or defaults.get("workdir")
        workdir = (
            _expand_path(workdir_raw, f"agent {name}: workdir", name, root,
                         swarm_name, atype, cfg_path.parent)
            if workdir_raw
            else root / name
        )

        # Mailbox base: per-agent mail_dir overrides the global default, both
        # resolved relative to the config file's parent. Default = the workdir,
        # so by default the four folders live inside the workspace.
        mail_dir_raw = raw.get("mail_dir") or default_mail_dir_raw
        mail_dir = (
            _expand_path(mail_dir_raw, f"agent {name}: mail_dir", name, root,
                         swarm_name, atype, cfg_path.parent)
            if mail_dir_raw
            else workdir
        )

        create_workdir = _as_bool(
            raw.get("create_workdir", defaults.get("create_workdir", create_workdirs)),
            True,
            f"agent {name}: create_workdir",
        )

        ping_seconds = raw.get("periodically_ping_seconds")
        if ping_seconds is None:
            ping_seconds = defaults.get("periodically_ping_seconds")
        ping_seconds = int(ping_seconds or 0)
        ping_message = (
            raw.get("periodically_ping_message")
            or defaults.get("periodically_ping_message")
            or ""
        )

        env = dict(_as_str_map(defaults.get("env"), "defaults.env"))
        env.update(_as_str_map(tconf.get("env"), f"agent_types.{atype}.env"))
        env.update(_as_str_map(raw.get("env"), f"agent {name}: env"))

        agents.append(
            Agent(
                name=name,
                type=atype,
                command=str(command),
                workdir=workdir,
                session=f"{prefix}{name}",
                capture=capture,
                boot_delay_ms=int(boot),
                role=role,
                can_talk_to=_as_list(
                    raw.get("can_talk_to", defaults.get("can_talk_to")),
                    f"agent {name}: can_talk_to",
                ),
                mail_dir=mail_dir,
                periodically_ping_seconds=ping_seconds,
                periodically_ping_message=str(ping_message),
                resume_args=raw.get("resume_args")
                or defaults.get("resume_args")
                or tconf.get("resume_args"),
                resume_command=raw.get("resume_command")
                or defaults.get("resume_command")
                or tconf.get("resume_command"),
                env=env,
                create_workdir=create_workdir,
                # Busy tracking needs a "turn finished" signal, which only exists
                # when the agent is captured. capture: none => always accept mail.
                ready_probe=_as_bool(
                    raw.get("ready_probe", defaults.get("ready_probe")),
                    True,
                    f"agent {name}: ready_probe",
                ),
                busy_check=_as_bool(
                    raw.get("busy_check", defaults.get("busy_check")),
                    True,
                    f"agent {name}: busy_check",
                )
                and capture != "none",
            )
        )

    all_names = [a.name for a in agents]

    # Pass 2: expand wildcards and validate the communication graph. `user` is a
    # permitted virtual recipient; `system` is orchestrator-only and may never be
    # addressed by an agent; everything else must name a real agent.
    for agent in agents:
        if "*" in agent.can_talk_to:
            agent.can_talk_to = [n for n in all_names if n != agent.name]
        for peer in agent.can_talk_to:
            if peer == "system":
                raise ConfigError(
                    f"agent {agent.name!r}: `system` is a reserved orchestrator "
                    "mailbox and can never be a recipient -- remove it from "
                    "can_talk_to"
                )
            if peer == "user":
                continue
            if peer not in all_names:
                raise ConfigError(
                    f"agent {agent.name!r}: can_talk_to references unknown agent "
                    f"{peer!r}"
                )
            if peer == agent.name:
                raise ConfigError(f"agent {agent.name!r}: cannot be in its own can_talk_to")

    warnings: list[str] = []
    warnings.extend(agent_warnings)

    for agent in agents:
        if agent.workdir.exists() and not agent.workdir.is_dir():
            raise ConfigError(
                f"agent {agent.name!r}: workdir is not a directory: {agent.workdir}"
            )
        if not agent.workdir.exists() and not agent.create_workdir:
            raise ConfigError(
                f"agent {agent.name!r}: workdir does not exist: {agent.workdir}\n"
                "   Create it yourself, or allow Agentainer to: create_workdir: true"
            )

    shared: dict[Path, list[str]] = {}
    for agent in agents:
        shared.setdefault(agent.workdir.resolve(), []).append(agent.name)
    for directory, names in shared.items():
        if len(names) > 1:
            warnings.append(
                f"agents {', '.join(names)} share the working directory {directory} -- "
                "they can overwrite each other's files, and a shared git checkout will "
                "interleave their commits"
            )

    cfg = SwarmConfig(
        path=cfg_path,
        name=swarm_name,
        warnings=warnings,
        root=root,
        session_prefix=prefix,
        agents=agents,
        enter_delay_ms=int(swarm.get("enter_delay_ms", 250)),
        send_delay_ms=int(swarm.get("send_delay_ms", 150)),
        ready_timeout_ms=int(swarm.get("ready_timeout_ms", 60000)),
        busy_timeout_ms=int(swarm.get("busy_timeout_ms", 900000)),
        resume=_as_bool(swarm.get("resume"), False, "swarm.resume"),
        user_available=_as_bool(
            swarm.get("user_available"), False, "swarm.user_available"
        ),
        pane_idle_ms=int(swarm.get("pane_idle_ms", 2500)),
        pane_poll_ms=int(swarm.get("pane_poll_ms", 700)),
        pane_scrollback=int(swarm.get("pane_scrollback", 400)),
        tmux_history_limit=int(swarm.get("tmux_history_limit", 50000)),
        tmux_mouse=_as_bool(swarm.get("tmux_mouse"), True, "swarm.tmux_mouse"),
        supervise=_as_bool(swarm.get("supervise"), True, "swarm.supervise"),
        supervise_interval_ms=int(swarm.get("supervise_interval_ms", 15000)),
    )
    return cfg

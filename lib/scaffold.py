#!/usr/bin/env python3
"""Agentainer -- the interactive coding-agent swarm builder (P6 multi-swarm).

The operator picks a coding-agent CLI; we open a REAL interactive tmux session
in the new swarm's directory, paste a crafted onboarding prompt, and the operator
converses with the agent through the UI terminal panel until it writes/edits the
swarm's ``agentainer.yaml``. Then the operator clicks **Approve & Launch** (or
runs ``agentainer swarms approve``) and we validate + ``up`` the swarm.

This is deliberately a thin, deterministic wrapper over the already-tested core:
the trust-modal pre-trust (``hooks``), the tmux launcher pattern (mirrors
``cli.start_agent`` so the pane survives the CLI exiting), the paste stack
(``tmux.paste_into``), and the registry/config validators. The model only reads
and writes files; nothing here pushes protocol bookkeeping onto it.

Zero runtime deps: Python stdlib + the bundled lib/ modules only.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import config as cfgmod  # noqa: E402
import hooks  # noqa: E402
import registry  # noqa: E402
import tmux  # noqa: E402

# The public schema/docs the onboarding prompt points the builder agent at.
DOCS_URL = "github.com/mehmetcanfarsak/agentainer"


# --------------------------------------------------------------------------
# session naming
# --------------------------------------------------------------------------


def builder_session_name(cfg) -> str:
    """A unique tmux session name for *cfg*'s builder (never collides with agents).

    Uses the swarm's ``session_prefix`` (which is unique per swarm), falling back
    to ``<name>_`` so two swarms' builder sessions never share a name.
    """
    prefix = cfg.session_prefix or f"{cfg.name}_"
    return f"{prefix}builder"


# --------------------------------------------------------------------------
# the onboarding prompt
# --------------------------------------------------------------------------


def builder_prompt(mode: str, cfg_path, notes: str = "") -> str:
    """The crafted onboarding prompt pasted into the builder agent's terminal.

    ``mode`` is ``"adapt"`` (an existing config the operator wants changed) or
    ``"scratch"`` (design a brand-new swarm from nothing). Both prompts point the
    agent at the public schema/docs, tell it to ask clarifying questions in the
    terminal first, then edit/write the file, and finally tell the operator to
    click **Approve & Launch** in the UI.
    """
    path = str(cfg_path)
    wants = (notes or "").strip()
    if mode == "scratch":
        return (
            "You are helping an Agentainer operator design a brand-new agentic "
            "swarm from scratch, in this interactive terminal.\n\n"
            f"Agentainer runs several coding-agent CLIs together, each in its own "
            f"tmux session and working directory, defined by a single "
            f"`agentainer.yaml`. Read the config schema and the list of usable "
            f"coding-agent types (claude, codex, gemini, hermes) at {DOCS_URL} "
            "before you design anything.\n\n"
            "Do this, in order:\n"
            "  1. Ask the operator, HERE in the terminal, what swarm they want to "
            "build -- the goal, how many agents, and which coding-agent type each "
            "agent should use. Keep asking until the design is clear.\n"
            "  2. Design the agents and their `can_talk_to` message ACL.\n"
            f"  3. Write the finished config to `{path}`.\n"
            f"  4. When the file is written and valid, tell the operator to click "
            "**Approve & Launch** in the Agentainer UI to start the swarm.\n\n"
            + (f"What the operator wants: {wants}\n" if wants else "")
        )
    # default: adapt an existing config
    return (
        "You are helping an Agentainer operator adapt an existing swarm "
        "configuration, in this interactive terminal.\n\n"
        f"The Agentainer swarm config is at `{path}`. The config schema and docs "
        f"are at {DOCS_URL}.\n\n"
        + (f"What the operator wants: {wants}\n\n" if wants else "")
        + "Do this, in order:\n"
        "  1. Read the current config and ask the operator any clarifying "
        "questions you need, HERE in the terminal.\n"
        f"  2. Edit `{path}` to match what they asked for, keeping it valid "
        "against the schema.\n"
        "  3. When your edits are done, tell the operator to click "
        "**Approve & Launch** in the Agentainer UI to (re)start the swarm.\n"
    )


# --------------------------------------------------------------------------
# builder session lifecycle
# --------------------------------------------------------------------------


def _default_command(agent_type: str) -> str:
    """The builtin default launch command for *agent_type* (raises on unknown)."""
    spec = cfgmod.BUILTIN_AGENT_TYPES.get(agent_type)
    if spec is None:
        raise ValueError(
            f"unknown agent type: {agent_type!r} "
            f"(known: {', '.join(sorted(cfgmod.BUILTIN_AGENT_TYPES))})"
        )
    return str(spec["command"])


def open_builder_session(
    cfg,
    agent_type: str | None = None,
    agent_command: str | None = None,
    mode: str = "adapt",
    notes: str = "",
) -> str:
    """Open (or reuse) the interactive builder session for *cfg*; return its name.

    ``agent_type`` selects the coding-agent CLI (defaults to ``claude``) and drives
    both the default launch command and the per-type trust pre-trust.
    ``agent_command`` overrides the command so the UI/CLI can pass the operator's
    real shell alias (which may embed credentials). The pane is launched with the
    same wrapper as ``cli.start_agent`` so it drops to a shell if the CLI exits.

    Idempotent: if the session already exists it is returned untouched (no second
    CLI is spawned and no prompt is re-pasted).
    """
    workdir = cfg.path.parent
    session = builder_session_name(cfg)
    if tmux.session_exists(session):
        return session

    atype = agent_type or "claude"
    command = agent_command if agent_command is not None else _default_command(atype)
    if not str(command).strip():
        raise ValueError("builder command is empty")

    # Set the tmux globals the builder pane inherits, then pre-trust the workdir so
    # no first-run trust modal swallows the onboarding prompt.
    holder = tmux.configure_tmux(cfg)
    hooks.pretrust_dir(workdir, atype)

    inner = (
        f"cd {shlex.quote(str(workdir))} || exit 1; "
        f"{command}; "
        f'status=$?; printf "\\n[agentainer] builder exited (status %s)\\n" "$status"; '
        'exec "${SHELL:-bash}" -l'
    )
    launcher = f"exec bash -lc {shlex.quote(inner)}"
    tmux.tmux(
        "new-session", "-d", "-s", session, "-x", "220", "-y", "50",
        "-c", str(workdir), launcher,
    )

    if holder:
        tmux.tmux("kill-session", "-t", f"={holder}", check=False, capture=True)

    tmux.paste_into(cfg, session, builder_prompt(mode, cfg.path, notes))
    return session


def close_builder_session(cfg) -> bool:
    """Kill *cfg*'s builder session if it is running. Returns True if one was killed."""
    session = builder_session_name(cfg)
    if not tmux.session_exists(session):
        return False
    tmux.tmux("kill-session", "-t", f"={session}", check=False, capture=True)
    return True


# --------------------------------------------------------------------------
# approval: validate the built config, close the builder, and up the swarm
# --------------------------------------------------------------------------


def approve_swarm(name: str, do_up: bool = True) -> dict:
    """Validate a built swarm, close its builder session, and optionally ``up`` it.

    Returns ``{"ok": False, "error": <str>}`` when the swarm is unknown or its
    config does not load (the operator sees exactly what to fix), else
    ``{"ok": True, "started": [names]}`` -- the agents launched (empty when
    ``do_up`` is False).
    """
    try:
        cfg = registry.resolve(name)
    except KeyError:
        return {"ok": False, "error": f"unknown swarm: {name!r}"}
    except cfgmod.ConfigError as exc:
        return {"ok": False, "error": str(exc)}

    close_builder_session(cfg)
    started: list = []
    if do_up:
        import cli  # lazy: cli imports scaffold indirectly, so import at call time

        started = cli.up_config(cfg)
    return {"ok": True, "started": [a.name for a in started]}

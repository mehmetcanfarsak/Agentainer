"""Turn-completion wiring for Agentainer agents.

Ports the proven v1 hook plumbing (trust-modal pre-trust + Stop/notify hook
installation + capture config) and generalizes it behind a single per-type
dispatch, ``install_turn_detection`` (plan §13 / D18). Every function here is
deterministic and dependency-free: the model never has to install a hook
itself, and the hook commands are written with absolute paths resolved from
the repo root so they work regardless of the agent's cwd.

The only capability the model needs is read/write files. Everything about
routing, ACL, and turn-detection is orchestrator code.
"""

import json
import os
import re
import shlex
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import Agent, SwarmConfig  # noqa: E402

# Repo root: AGENTAINER_HOME overrides, else this file's grandparent (lib/..).
AGENTAINER_HOME = Path(
    os.environ.get("AGENTAINER_HOME") or Path(__file__).resolve().parent.parent
)
HOOKS_DIR = AGENTAINER_HOME / "hooks"

# Agent types whose CLI can invoke an external program when a turn completes.
HOOK_CAPABLE = ("claude", "codex")


# --------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------


def info(msg: str) -> None:
    print(f"\033[36m::\033[0m {msg}", file=sys.stderr)


def warn(msg: str) -> None:
    print(f"\033[33m!!\033[0m {msg}", file=sys.stderr)


# --------------------------------------------------------------------------
# capture: hook installation
# --------------------------------------------------------------------------


def pretrust_claude_dir(agent: Agent) -> None:
    """Mark the agent's workdir as trusted in ~/.claude.json.

    Claude Code asks "Do you trust the files in this folder?" the first time it
    runs anywhere new -- even under --dangerously-skip-permissions -- and that
    modal swallows the first prompt (Enter answers the dialog). Codex gets the
    same treatment via its config.toml; this is the claude equivalent.
    """
    path = Path(os.path.expanduser("~")) / ".claude.json"
    if not path.is_file():
        return  # claude has never run here; it will create the file itself

    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        warn(f"{agent.name}: could not read ~/.claude.json; the trust dialog may appear")
        return

    projects = data.setdefault("projects", {})
    entry = projects.setdefault(str(agent.workdir), {})
    if entry.get("hasTrustDialogAccepted"):
        return

    entry["hasTrustDialogAccepted"] = True
    entry.setdefault("projectOnboardingSeenCount", 1)

    # Write atomically: a running claude may be reading this file.
    tmp = path.with_suffix(".json.agentainer-tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, path)
    except OSError as exc:
        warn(f"{agent.name}: could not pre-trust {agent.workdir}: {exc}")
        tmp.unlink(missing_ok=True)


def install_claude_hook(agent: Agent) -> None:
    pretrust_claude_dir(agent)
    settings_path = agent.workdir / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    settings: dict = {}
    if settings_path.is_file():
        try:
            settings = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            warn(f"{settings_path} is not valid JSON; overwriting")

    hook_cmd = str(HOOKS_DIR / "claude_stop.sh")
    # No "matcher" key: Stop is not a tool event, and supplying one can stop the
    # interactive TUI from ever running the hook.
    entry = {"hooks": [{"type": "command", "command": hook_cmd}]}
    hooks = settings.setdefault("hooks", {})
    stop_hooks = [
        h
        for h in hooks.get("Stop", [])
        if hook_cmd not in json.dumps(h)  # drop our own stale entry, keep the user's
    ]
    stop_hooks.append(entry)
    hooks["Stop"] = stop_hooks
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")


def valid_toml(text: str) -> bool:
    try:
        import tomllib
    except ImportError:  # Python < 3.11: cannot check, assume the caller is right
        return True
    try:
        tomllib.loads(text)
        return True
    except tomllib.TOMLDecodeError:
        return False


def install_codex_hook(agent: Agent) -> Path:
    """Give codex a private CODEX_HOME with a `notify` program wired up."""
    codex_home = agent.workdir / ".codex"
    codex_home.mkdir(parents=True, exist_ok=True)

    # Carry over the user's real credentials + settings, so the agent is logged in.
    user_home = Path(os.path.expanduser("~")) / ".codex"
    base = ""
    if user_home.is_dir() and user_home.resolve() != codex_home.resolve():
        for name in ("auth.json",):
            src, dst = user_home / name, codex_home / name
            if src.is_file() and not dst.exists():
                try:
                    dst.symlink_to(src)
                except OSError:
                    shutil.copy2(src, dst)
        user_cfg = user_home / "config.toml"
        if user_cfg.is_file():
            base = "\n".join(
                line
                for line in user_cfg.read_text().splitlines()
                if not re.match(r"\s*notify\s*=", line)
            ).strip()

    notify = json.dumps(str(HOOKS_DIR / "codex_notify.sh"))
    # Without this table codex opens a "do you trust this directory?" modal on
    # first run in a fresh folder, and that modal swallows the first prompt.
    trust = f"[projects.{json.dumps(str(agent.workdir))}]"

    # TOML is order-sensitive: a bare key written after a [table] header belongs
    # to that table. `notify` must therefore come before anything else, or codex
    # reads it as projects.<dir>.notify and never calls it.
    chunks = [
        "# installed by Agentainer -- fires when codex finishes a turn.",
        "# Keep `notify` above every [table] header: TOML is order-sensitive.",
        f"notify = [{notify}]",
        "",
    ]
    if base:
        chunks += [base, ""]
    if trust not in base:  # the user's config may already trust this directory
        chunks += ["# pre-trust the workdir so no modal eats the first prompt", trust,
                   'trust_level = "trusted"', ""]

    body = "\n".join(chunks)
    if not valid_toml(body):
        warn(
            f"{agent.name}: ~/.codex/config.toml could not be merged cleanly "
            "(invalid TOML); writing a minimal config instead"
        )
        body = "\n".join(
            [f"notify = [{notify}]", "", trust, 'trust_level = "trusted"', ""]
        )

    (codex_home / "config.toml").write_text(body)
    return codex_home


def install_capture(agent: Agent) -> dict[str, str]:
    """Install turn-completion capture. Returns extra env vars for the session.

    Mirrors v1: only does work when ``capture == "hook"``. Claude gets a Stop
    hook, Codex gets a CODEX_HOME with a notify program, and any other type
    falls back to pane polling (and has its ``capture`` downgraded so callers
    stop expecting a hook signal).
    """
    env: dict[str, str] = {}
    if agent.capture != "hook":
        return env

    if agent.type == "claude":
        install_claude_hook(agent)
    elif agent.type == "codex":
        env["CODEX_HOME"] = str(install_codex_hook(agent))
    else:
        warn(
            f"agent {agent.name!r}: type {agent.type!r} has no known completion hook "
            f"(only {', '.join(HOOK_CAPABLE)} do); falling back to capture: pane"
        )
        agent.capture = "pane"
    return env


def install_turn_detection(agent: Agent) -> dict[str, str]:
    """Install the correct turn-completion wiring for agent.type / capture.

    Returns the extra environment dict (e.g. capture config) the launcher
    should export into the agent's tmux session. The individual ``install_*``
    functions stay public so other modules and tests may call them directly.
    """
    if agent.type == "claude":
        pretrust_claude_dir(agent)
        install_claude_hook(agent)
        return {}
    if agent.type == "codex":
        install_codex_hook(agent)
        return {}
    # gemini / hermes (pane polling): install_capture writes a capture config
    # (or warns + downgrades an unsupported hook request) and returns the env.
    info(f"installed turn-completion wiring for {agent.name} ({agent.type})")
    return install_capture(agent)


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------


def write_shim(cfg: SwarmConfig) -> None:
    """An `agentainer` executable the agents themselves can call."""
    cfg.bin_dir.mkdir(parents=True, exist_ok=True)
    shim = cfg.bin_dir / "agentainer"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        "# Generated by Agentainer. Lets an agent run `agentainer send ...` from its shell.\n"
        f'exec {shlex.quote(str(AGENTAINER_HOME / "agentainer"))} "$@"\n'
    )
    shim.chmod(0o755)

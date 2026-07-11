#!/usr/bin/env python3
"""Agentainer -- the v2 command line entry point.

This is the thin shell that ties the tested core modules (config, mail, turn,
hooks, tmux, sessions, log) into the operator-facing subcommands. Every handler
here is intentionally small: all the hard work -- routing, ACL, read-state,
queueing, turn-detection, resume -- lives in those modules. See
``ProjectPlan.md`` (§26 build phases) and the v1 ``lib/swarm.py`` CLI it ports.

Zero runtime dependencies: Python stdlib + the bundled lib/ modules only.

Branding: "swarm" is retired -- it's Agentainer everywhere (decision D21).
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

# The bash launcher and bin/agentainer.js both exec this file as a script, so
# Python puts lib/ on sys.path[0]; the bare imports below resolve there.
import config as cfgmod  # noqa: E402
from config import ConfigError  # noqa: E402
import hooks  # noqa: E402
import log  # noqa: E402
import mail  # noqa: E402
import sessions  # noqa: E402
import tmux  # noqa: E402
import turn  # noqa: E402
import ui  # noqa: E402

# Repo root: AGENTAINER_HOME overrides, else this file's grandparent (lib/..).
AGENTAINER_HOME = Path(
    os.environ.get("AGENTAINER_HOME") or Path(__file__).resolve().parent.parent
)


# --------------------------------------------------------------------------
# small CLI utilities
# --------------------------------------------------------------------------


def info(msg: str) -> None:
    print(f":: {msg}", file=sys.stderr)


def warn(msg: str) -> None:
    print(f"!! {msg}", file=sys.stderr)


def die(msg: str) -> None:
    """Print an error and exit non-zero. Never returns."""
    print(f"!! {msg}", file=sys.stderr)
    sys.exit(1)


def _import_supervisor():
    """Lazily import the supervisor module.

    ``supervisor`` is the only core module that may be absent in a partially
    wired checkout; importing it lazily keeps *every other* command working and
    lets the supervisor-dependent paths degrade gracefully (caught by callers).
    """
    import supervisor  # noqa: F401 - may be injected via sys.modules in tests

    return supervisor


def _supervisor_alive(cfg) -> bool:
    try:
        return _import_supervisor().supervisor_alive(cfg)
    except ImportError:
        return False


# --------------------------------------------------------------------------
# config resolution / context discovery (port of v1 discover_context)
# --------------------------------------------------------------------------


def default_config() -> str:
    """AGENTAINER_CONFIG, else ./agentainer.yaml, else $AGENTAINER_HOME/agentainer.yaml."""
    from_env = os.environ.get("AGENTAINER_CONFIG")
    if from_env:
        return from_env
    local = Path.cwd() / "agentainer.yaml"
    if local.is_file():
        return str(local)
    return str(AGENTAINER_HOME / "agentainer.yaml")


def read_version() -> str:
    """Best-effort read of the package version from package.json (single source
    of truth), falling back to 'unknown' if it cannot be found or parsed."""
    try:
        data = json.loads((AGENTAINER_HOME / "package.json").read_text())
        return str(data.get("version") or "unknown")
    except Exception:
        return "unknown"


def config_from_state() -> str | None:
    """Walk up from the cwd looking for the .agentainer/state.json written by up."""
    probe = Path.cwd().resolve()
    for candidate in [probe, *probe.parents]:
        state = candidate / ".agentainer" / "state.json"
        if state.is_file():
            try:
                return json.loads(state.read_text()).get("config")
            except (OSError, json.JSONDecodeError):
                return None
    return None


def agent_from_cwd(cfg) -> str | None:
    cwd = Path.cwd().resolve()
    for agent in cfg.agents:
        workdir = agent.workdir.resolve()
        if cwd == workdir or workdir in cwd.parents:
            return agent.name
    return None


def discover_context(explicit_config: str | None, explicit_agent: str | None):
    """Figure out which swarm + agent we are, from argv, env, or the filesystem.

    A hook runs inside the agent's own process, so it may inherit an
    AGENTAINER_CONFIG (or fall back to an unrelated ./agentainer.yaml) that does
    not describe it. Each candidate config is therefore only accepted if it
    actually contains the calling agent.
    """
    candidates = [explicit_config, os.environ.get("AGENTAINER_CONFIG"), config_from_state()]
    seen: list[str] = []

    for path in candidates:
        if not path or path in seen:
            continue
        seen.append(path)
        if not Path(path).is_file():
            continue
        try:
            cfg = cfgmod.load(path)
        except ConfigError:
            continue
        name = explicit_agent or os.environ.get("AGENTAINER_AGENT") or agent_from_cwd(cfg)
        if name and name in cfg.names():
            return cfg, cfg.get(name)

    raise ConfigError(
        "cannot work out which swarm/agent is calling. Set AGENTAINER_AGENT and "
        "AGENTAINER_CONFIG, or run from inside an agent's working directory. "
        f"Configs tried: {', '.join(seen) or 'none'}"
    )


def select_agents(cfg, only: str | None):
    if not only:
        return list(cfg.agents)
    names = [n.strip() for n in only.split(",") if n.strip()]
    return [cfg.get(n) for n in names]


# --------------------------------------------------------------------------
# message reading (port of v1 read_message)
# --------------------------------------------------------------------------


def read_message(args) -> str:
    if getattr(args, "file", None):
        return Path(args.file).read_text()
    msg = getattr(args, "message", None)
    if not msg or msg == ["-"]:
        if sys.stdin.isatty():
            die("no message given (pass it as an argument, with --file, or on stdin)")
        return sys.stdin.read()
    return " ".join(msg)


# --------------------------------------------------------------------------
# agent launch (port of v1 start_agent -> open one tmux session, send role)
# --------------------------------------------------------------------------


def start_agent(cfg, agent, extra_env=None, resume_cmd: str | None = None) -> None:
    """Launch *agent* in a fresh tmux session; optionally resume a conversation.

    Creates the workdir (when allowed), writes the turn-state, exports the
    session environment, runs the agent command, and returns. The first prompt
    (agent.role) is pasted by the caller once the pane is ready.
    """
    resume = resume_cmd is not None
    if not agent.workdir.is_dir():
        if not agent.create_workdir:
            # config.load already rejects this case, so this is a belt-and-braces
            # guard for any caller that builds an Agent without going through load.
            raise ConfigError(  # pragma: no cover
                f"{agent.name}: workdir does not exist: {agent.workdir} "
                "(create_workdir is false)"
            )
        agent.workdir.mkdir(parents=True, exist_ok=True)
        info(f"{agent.name}: created {agent.workdir}")

    # A newly launched CLI has no turn in flight (resumed or not).
    turn.write_turn_state(cfg, agent.name, {"delivered": 0, "completed": 0, "since": 0, "by": None})

    env = sessions.session_env(cfg, agent, extra_env or {})
    exports = " ".join(f"export {k}={shlex.quote(v)};" for k, v in env.items())

    command = resume_cmd or agent.command
    inner = (
        f"{exports} "
        f"cd {shlex.quote(str(agent.workdir))} || exit 1; "
        f"{command}; "
        f'status=$?; printf "\\n[agentainer] agent %s exited (status %s)\\n" '
        f"{shlex.quote(agent.name)} \"$status\"; "
        'exec "${SHELL:-bash}" -l'
    )
    launcher = f"exec bash -lc {shlex.quote(inner)}"

    tmux.tmux(
        "new-session", "-d", "-s", agent.session, "-x", "220", "-y", "50",
        "-c", str(agent.workdir), launcher,
    )
    info(f"started {agent.name} ({agent.type}) in tmux session {agent.session!r}")


# --------------------------------------------------------------------------
# lifecycle handlers
# --------------------------------------------------------------------------


def cmd_up(args) -> int:
    cfg = cfgmod.load(args.config)
    if not shutil.which("tmux"):
        die("tmux is required but was not found on PATH")

    selected = select_agents(cfg, args.only)
    for message in cfg.warnings:
        warn(message)

    for directory in (cfg.runtime, cfg.log_dir, cfg.queue_dir, cfg.run_dir):
        directory.mkdir(parents=True, exist_ok=True)

    # Set the globals the agent panes inherit, then tear the holder down once
    # the real sessions keep the tmux server alive.
    setup_holder = tmux.configure_tmux(cfg)
    mail.init_mailboxes(cfg)

    resume = cfg.resume if args.resume is None else args.resume
    recorded = sessions.read_sessions(cfg) if resume else {}

    started: list = []
    resumed: set = set()
    for agent in selected:
        if tmux.session_exists(agent.session):
            if not getattr(args, "restart", False):
                warn(f"{agent.name}: session {agent.session!r} already exists, skipping")
                continue
            info(f"{agent.name}: restarting")
            tmux.tmux("kill-session", "-t", f"={agent.session}", check=False, capture=True)

        resume_cmd = None
        if resume:
            session_id = (recorded.get(agent.name) or {}).get("session_id")
            if not session_id:
                warn(f"{agent.name}: no recorded conversation; starting a fresh one")
            else:
                resume_cmd = sessions.resume_command(cfg, agent, session_id)
                if resume_cmd:
                    resumed.add(agent.name)
                    info(f"{agent.name}: resuming conversation {session_id}")
                else:
                    warn(
                        f"{agent.name}: type {agent.type!r} has no resume recipe "
                        "(set resume_args or resume_command); starting a fresh conversation"
                    )

        # Per-type turn-completion wiring (returns extra env for the session).
        extra_env = hooks.install_turn_detection(agent)
        start_agent(cfg, agent, extra_env, resume_cmd)
        started.append(agent)

    if setup_holder:
        tmux.tmux("kill-session", "-t", f"={setup_holder}", check=False, capture=True)

    if not started:
        info("nothing to start")
        return 0

    # Give the CLIs a moment to draw their splash, then wait for each one's
    # input box to actually respond before typing the first prompt.
    boot = max((a.boot_delay_ms for a in started), default=0)
    if boot:
        info(f"waiting {boot}ms for agents to boot...")
        time.sleep(boot / 1000.0)

    for agent in started:
        if agent.name in resumed:
            info(f"{agent.name}: resumed, not re-sending the first prompt")
            continue
        if not agent.role:
            continue
        try:
            if agent.ready_probe and not tmux.wait_until_ready(cfg, agent):
                warn(
                    f"{agent.name}: input box never responded within "
                    f"{cfg.ready_timeout_ms}ms; sending the prompt anyway"
                )
            if tmux.paste_into(cfg, agent.session, agent.role):
                turn.mark_turn_started(cfg, agent.name, "user")
                info(f"sent first prompt to {agent.name}")
            else:
                warn(f"{agent.name}: first prompt may not have been delivered")
            log.log_event(cfg, agent.name, "first_prompt", text=agent.role)
        except tmux.SwarmError as exc:
            warn(f"{agent.name}: could not send first prompt: {exc}")
        time.sleep(cfg.send_delay_ms / 1000.0)

    # The supervisor is the heartbeat the event-driven design lacks: it reconciles
    # dead/stale agents on a timer so one silent agent cannot wedge the swarm.
    if cfg.supervise and not getattr(args, "no_supervise", False):
        try:
            _import_supervisor().start_supervisor(cfg, [a.name for a in started])
        except ImportError:
            warn("supervisor module not available; running without the liveness supervisor")

    print()
    info(f"swarm {cfg.name!r} is up with {len(started)} agent(s)")
    if started:
        info(f"attach with:  tmux attach -t {started[0].session}")
    return 0


def cmd_down(args) -> int:
    cfg = cfgmod.load(args.config)
    if not args.only:
        try:
            _import_supervisor().stop_supervisor(cfg)
        except ImportError:
            pass
    for agent in select_agents(cfg, args.only):
        if tmux.session_exists(agent.session):
            tmux.tmux("kill-session", "-t", f"={agent.session}", check=False, capture=True)
            info(f"stopped {agent.name}")
        else:
            info(f"{agent.name}: not running")
    return 0


def cmd_restart(args) -> int:
    cmd_down(args)
    args.restart = True
    return cmd_up(args)


def cmd_status(args) -> int:
    cfg = cfgmod.load(args.config)
    print(f"swarm: {cfg.name}   root: {cfg.root}")
    for agent in cfg.agents:
        running = tmux.session_exists(agent.session)
        if not running:
            turn_s = "-"
        elif not agent.busy_check:
            turn_s = "untracked"
        else:
            state = turn.busy_info(cfg, agent)
            turn_s = f"busy {state['age_s']}s" if state else "idle"
        q = cfg.queue_dir / agent.name
        depth = len([f for f in q.iterdir() if f.is_file()]) if q.is_dir() else 0
        inbox = cfg.mail_paths(agent).inbox
        unread = len([f for f in inbox.iterdir() if f.is_file()]) if inbox.is_dir() else 0
        print(
            f"  {agent.name} ({agent.type}) "
            f"{'up' if running else 'down'} {turn_s} "
            f"queue={depth} unread={unread} "
            f"talks={', '.join(agent.can_talk_to) or '-'}"
        )
    print(f"supervisor: {'alive' if _supervisor_alive(cfg) else 'down'}")
    return 0


def cmd_attach(args) -> int:
    cfg = cfgmod.load(args.config)
    agent = cfg.get(args.agent)
    if not tmux.session_exists(agent.session):
        die(f"{agent.name} is not running")
    os.execvp("tmux", ["tmux", "attach", "-t", agent.session])
    return 0


def cmd_send(args) -> int:
    cfg = cfgmod.load(args.config)
    text = read_message(args)
    sender = args.sender or os.environ.get("AGENTAINER_AGENT") or "user"

    if sender == "user" or sender not in cfg.names():
        # The operator (or UI stand-in) sends mail as the virtual user.
        mail.send_as_user(cfg, args.to, text)
        info(f"user -> {args.to}: delivered")
        return 0

    # Simulate a real agent sending: drop the message in its outbox, then run the
    # same sweep the completion hook would, so routing + ACL actually execute.
    agent = cfg.get(sender)
    mp = cfg.mail_paths(agent)
    out_dir = mp.outbox / args.to
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{mail.new_message_id()}.md").write_text(text)
    mail.on_stop(cfg, sender)
    info(f"{sender} -> {args.to}: routed")
    return 0


def cmd_user(args) -> int:
    cfg = cfgmod.load(args.config)
    cmd = args.user_cmd
    if cmd == "available":
        mail.set_user_available(cfg, True)
        info("user is now available")
    elif cmd == "away":
        mail.set_user_available(cfg, False)
        info("user is now away")
    elif cmd == "inbox":
        q = cfg.queue_dir / "user"
        files = sorted(f for f in q.iterdir() if f.is_file()) if q.is_dir() else []
        if not files:
            print("user: no mail")
            return 0
        for path in files:
            print(f"\n--- {path.name} ---")
            print(path.read_text().rstrip())
    elif cmd == "send":
        text = read_message(args)
        mail.send_as_user(cfg, args.to, text)
        info(f"user -> {args.to}: delivered")
    return 0


def cmd_sessions(args) -> int:
    cfg = cfgmod.load(args.config)
    agents = sessions.read_sessions(cfg)
    if not agents:
        print(f"no conversations recorded yet ({cfg.sessions_file})")
        print("They are written as each agent finishes its first turn.")
        return 0
    if args.raw:
        print(cfg.sessions_file.read_text().rstrip())
        return 0
    print(f"{cfg.sessions_file}\n")
    for name in cfg.names():
        entry = agents.get(name)
        if not entry:
            print(f"  {name}: -")
            continue
        print(f"  {name} ({entry.get('type')})")
        print(f"      conversation: {entry.get('session_id')}")
        print(f"      last seen:    {entry.get('updated_at')}")
    return 0


def cmd_queue(args) -> int:
    cfg = cfgmod.load(args.config)
    agent = cfg.get(args.agent)
    if args.clear:
        q = cfg.queue_dir / agent.name
        dropped = 0
        if q.is_dir():
            for f in q.iterdir():
                if f.is_file():
                    f.unlink()
                    dropped += 1
        info(f"{agent.name}: dropped {dropped} queued message(s)")
        return 0
    q = cfg.queue_dir / agent.name
    items = sorted(f for f in q.iterdir() if f.is_file()) if q.is_dir() else []
    state = turn.busy_info(cfg, agent)
    status = f"busy for {state['age_s']}s (task from {state['by']})" if state else "idle"
    print(f"{agent.name}: {status}, {len(items)} message(s) queued")
    for index, item in enumerate(items, 1):
        first = item.read_text().strip().splitlines()[0][:70]
        print(f"  {index}. {item.name}: {first}")
    return 0


def cmd_idle(args) -> int:
    """Force an agent back to idle -- the escape hatch when a capture never fired."""
    cfg = cfgmod.load(args.config)
    agent = cfg.get(args.agent)
    turn.mark_turn_finished(cfg, agent.name)
    info(f"{agent.name}: marked idle")
    if not args.no_drain:
        mail.process_read_folder(cfg, agent.name)
    return 0


def cmd_inbox(args) -> int:
    cfg = cfgmod.load(args.config)
    name = args.agent or os.environ.get("AGENTAINER_AGENT")
    if not name:
        die("specify an agent: agentainer inbox <agent>")
    agent = cfg.get(name)
    box = cfg.mail_paths(agent).inbox
    if not box.is_dir() or not any(box.iterdir()):
        print(f"{name}: inbox is empty")
        return 0
    for path in sorted(box.iterdir()):
        if path.is_file():
            print(f"\n--- {path.name} ---")
            print(path.read_text().rstrip())
    return 0


def cmd_logs(args) -> int:
    cfg = cfgmod.load(args.config)
    name = args.agent
    path = (cfg.log_dir / f"{name}.jsonl") if name else (cfg.log_dir / "agentainer.jsonl")
    if not path.is_file():
        print(f"no log yet at {path}")
        return 0
    if args.follow:
        os.execvp("tail", ["tail", "-f", str(path)])
    lines = path.read_text().splitlines()[-args.tail :]
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        detail = rec.get("to") or rec.get("from") or rec.get("source") or ""
        print(f"{rec['ts']} {rec['agent']} {rec['kind']} {detail}")
        body = (rec.get("text") or "").strip().replace("\n", "\n    ")
        if body:
            print(f"    {body}")
    return 0


def cmd_validate(args) -> int:
    cfg = cfgmod.load(args.config)
    for message in cfg.warnings:
        warn(message)
    print(f"config ok: {cfg.path}")
    print(f"  swarm:  {cfg.name}")
    print(f"  root:   {cfg.root}")
    print(f"  agents: {len(cfg.agents)}")
    for agent in cfg.agents:
        peers = ", ".join(agent.can_talk_to) or "none"
        mp = cfg.mail_paths(agent)
        if agent.workdir.is_dir():
            state = "exists"
        else:
            state = "will be created" if agent.create_workdir else "MISSING"
        print(f"\n  - {agent.name} ({agent.type}, capture={agent.capture})")
        print(f"      command:  {agent.command}")
        print(f"      workdir:  {agent.workdir}  [{state}]")
        print(f"      session:  {agent.session}")
        print(f"      inbox:    {mp.inbox}")
        print(f"      outbox:   {mp.outbox}")
        print(f"      talks to: {peers}")
        if args.show_prompts and agent.role:
            body = "\n".join(f"      | {l}" for l in agent.role.splitlines())
            print(f"      role:\n{body}")
    return 0


def cmd_hook(args) -> int:
    """Turn-completion entry point the installed hooks call.

    The whole clock runs on this: discover which swarm/agent we are, then sweep
    the agent's outbox (route every message), finish its turn, and release+nudge
    recipients. A hook must never break the agent, so it always returns 0.
    """
    cfg, agent = discover_context(args.config, args.agent)

    if args.type == "claude":
        try:
            payload = json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError):
            payload = {}
        # Claude sets this when a Stop hook already caused a continuation.
        if payload.get("stop_hook_active"):
            return 0
        # Claude hands us its session id on every turn; that is what --resume wants.
        sessions.record_session(
            cfg, agent, payload.get("session_id"), transcript=payload.get("transcript_path")
        )
    elif args.type == "codex":
        try:
            payload = json.loads(args.payload or "{}")
        except (json.JSONDecodeError, ValueError):
            payload = {}
        if payload.get("type") != "agent-turn-complete":
            return 0
        sessions.record_session(cfg, agent, payload.get("session_id"))
    else:
        # generic: nothing to parse; just sweep + finish the turn.
        pass

    mail.on_stop(cfg, agent.name)
    return 0


def _watch_tick(cfg, agent, state) -> bool:
    """One watcher poll. Returns True when the pane has been idle long enough to
    call it a completed turn. Updates *state* in place."""
    current = tmux.capture_pane(cfg, agent).splitlines()
    if current != state["previous"]:
        state["previous"] = current
        state["last_change"] = time.monotonic()
        state["dirty"] = True
        return False
    if not state["dirty"]:
        return False
    if (time.monotonic() - state["last_change"]) * 1000 < cfg.pane_idle_ms:
        return False
    return True


def run_watcher(cfg, agent) -> None:
    """Poll an agent's tmux pane; when it stops changing, treat the turn as done.

    Fallback for pane-capture agents (gemini/hermes) whose CLI cannot call a
    program on turn completion. Exit when the session disappears.
    """
    info(f"watcher started for {agent.name} (session {agent.session})")
    state = {
        "previous": tmux.capture_pane(cfg, agent).splitlines(),
        "last_change": time.monotonic(),
        "dirty": False,
    }
    while tmux.session_exists(agent.session):
        time.sleep(cfg.pane_poll_ms / 1000.0)
        if _watch_tick(cfg, agent, state):
            mail.on_stop(cfg, agent.name)
            state["dirty"] = False
    info(f"watcher for {agent.name}: session gone, exiting")


def cmd_watch(args) -> int:
    cfg = cfgmod.load(args.config)
    agent = cfg.get(args.agent)
    if agent.capture != "pane":
        die(
            f"{agent.name}: capture={agent.capture}, nothing to watch. The pane "
            f"watcher is only for pane-capture agents (gemini/hermes); "
            f"{agent.type} agents detect turn completion via "
            f"{'a Stop hook' if agent.capture == 'hook' else 'no capture at all'}."
        )
    if not tmux.session_exists(agent.session):
        die(f"{agent.name}: session {agent.session!r} is not running (start it with `up`).")
    run_watcher(cfg, agent)
    return 0


def cmd_supervise(args) -> int:
    cfg = cfgmod.load(args.config)
    names = list(args.names) or cfg.names()
    try:
        _import_supervisor().run_supervisor(cfg, names)
    except ImportError:
        die("supervisor module not available")
    return 0


def gen_ui_token() -> str:
    """A random auth token for the UI control plane (no token == no remote bind)."""
    return secrets.token_hex(16)


def cmd_serve(args) -> int:
    """Serve the HTTP control-plane UI (observability + send-from-UI).

    Binds 127.0.0.1 by default; a token is required for any non-loopback bind
    (enforced inside ``ui.run_server``). The token comes from ``--token``, else
    ``AGENTAINER_UI_TOKEN``, else a freshly generated one printed to stderr.
    """
    cfg = cfgmod.load(args.config)
    token = args.token or os.environ.get("AGENTAINER_UI_TOKEN") or gen_ui_token()
    host = args.host or "127.0.0.1"
    port = args.port or 0
    handle = ui.run_server(cfg, token, host=host, port=port, background=True)
    info(f"UI serving at {handle.url}")
    info(f"UI token: {token}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        handle.shutdown()
    return 0


# --------------------------------------------------------------------------
# argument parser
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=os.environ.get("AGENTAINER_PROG", "agentainer"),
        description="Run a swarm of coding agents (claude, codex, gemini, hermes) in tmux.",
    )
    parser.add_argument(
        "-v", "--version", action="version", version=f"agentainer {read_version()}",
        help="show the Agentainer version and exit",
    )
    parser.add_argument(
        "-c", "--config", default=default_config(), help="path to the swarm YAML (default: agentainer.yaml)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, func, help_text):
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(func=func)
        # SUPPRESS so omitting -c here keeps the top-level -c value intact.
        p.add_argument("-c", "--config", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        return p

    p_val = add("validate", cmd_validate, "parse the config and print the resolved swarm")
    p_val.add_argument("--show-prompts", action="store_true", help="also print each agent's role")

    p_up = add("up", cmd_up, "create agent dirs, install hooks, start tmux sessions, send prompts")
    p_up.add_argument("--only", help="comma-separated subset of agents to start")
    p_up.add_argument("--resume", dest="resume", action="store_true", default=None,
                      help="reattach each agent to the conversation recorded in sessions.yaml")
    p_up.add_argument("--no-resume", dest="resume", action="store_false",
                      help="start fresh conversations even if swarm.resume is true")
    p_up.add_argument("--restart", action="store_true", help="kill and recreate existing sessions")
    p_up.add_argument("--no-supervise", action="store_true", help="do not start the liveness supervisor")

    p_down = add("down", cmd_down, "kill agent tmux sessions")
    p_down.add_argument("--only", help="comma-separated subset of agents to stop")

    p_restart = add("restart", cmd_restart, "down + up")
    p_restart.add_argument("--only", help="comma-separated subset of agents")
    p_restart.add_argument("--resume", dest="resume", action="store_true", default=None)
    p_restart.add_argument("--no-resume", dest="resume", action="store_false")

    add("status", cmd_status, "show which agents are running")

    p_attach = add("attach", cmd_attach, "attach to an agent's tmux session")
    p_attach.add_argument("agent")

    p_send = add("send", cmd_send, "send a message to an agent (as user or another agent)")
    p_send.add_argument("--to", required=True, help="recipient agent name")
    p_send.add_argument("--from", dest="sender", help="sender name (default: $AGENTAINER_AGENT or 'user')")
    p_send.add_argument("--file", help="read the message body from a file")
    p_send.add_argument("message", nargs="*", help="message text, or '-' to read stdin")

    p_user = add("user", cmd_user, "virtual user mailbox: available|away|inbox|send")
    u = p_user.add_subparsers(dest="user_cmd", required=True)
    # Nested subparsers don't inherit the top-level -c, so add it to each.
    for _p in (u.add_parser("available"), u.add_parser("away"), u.add_parser("inbox")):
        _p.add_argument("-c", "--config", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    p_usend = u.add_parser("send")
    p_usend.add_argument("-c", "--config", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    p_usend.add_argument("--to", required=True)
    p_usend.add_argument("--file")
    p_usend.add_argument("message", nargs="*")

    p_sessions = add("sessions", cmd_sessions, "show each agent's recorded conversation id")
    p_sessions.add_argument("--raw", action="store_true", help="print sessions.yaml verbatim")

    p_queue = add("queue", cmd_queue, "show (or clear) the messages waiting for an agent")
    p_queue.add_argument("agent")
    p_queue.add_argument("--clear", action="store_true", help="discard everything queued")

    p_idle = add("idle", cmd_idle, "force an agent back to idle if a capture never fired")
    p_idle.add_argument("agent")
    p_idle.add_argument("--no-drain", action="store_true", help="do not process the read/ folder")

    p_inbox = add("inbox", cmd_inbox, "print the current inbox message for an agent")
    p_inbox.add_argument("agent", nargs="?")
    p_inbox.add_argument("-n", "--tail", type=int, default=5)

    p_logs = add("logs", cmd_logs, "print the swarm event log")
    p_logs.add_argument("agent", nargs="?", help="agent name, or omit for the whole swarm")
    p_logs.add_argument("-n", "--tail", type=int, default=20)
    p_logs.add_argument("-f", "--follow", action="store_true")

    p_hook = add("hook", cmd_hook, "internal: called by an agent's completion hook")
    p_hook.add_argument("type", choices=["claude", "codex", "generic"])
    p_hook.add_argument("payload", nargs="?", help="JSON payload (codex passes it as argv)")
    p_hook.add_argument("--agent", help="override the detected agent name")

    p_watch = add("watch", cmd_watch, "internal: poll an agent's tmux pane for completed turns")
    p_watch.add_argument("agent")

    p_sup = add("supervise", cmd_supervise, "internal: background liveness watchdog")
    p_sup.add_argument("names", nargs="*", help="agents to watch (default: all)")

    p_serve = add("serve", cmd_serve, "serve the HTTP control-plane UI (observability)")
    p_serve.add_argument("--host", default=None, help="bind host (default: 127.0.0.1)")
    p_serve.add_argument("--port", type=int, default=0, help="port (default: auto)")
    p_serve.add_argument("--token", default=None, help="auth token (default: env or random)")

    return parser


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # `agentainer some.yaml up` and `agentainer ./x.yaml` both mean "up with this config".
    if argv and not argv[0].startswith("-") and argv[0].endswith((".yaml", ".yml")):
        path = argv.pop(0)
        argv = [*(argv or ["up"]), "-c", path]

    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        die(str(exc))
    except tmux.SwarmError as exc:
        die(str(exc))
    except subprocess.CalledProcessError as exc:
        die(f"command failed: {exc}")
    except KeyboardInterrupt:  # pragma: no cover - only triggered by a real SIGINT
        return 130


if __name__ == "__main__":  # pragma: no cover - exercised by running the module as a script
    sys.exit(main())

#!/usr/bin/env python3
"""Agentainer -- the paste/capture layer that talks to a live tmux TUI.

These functions type prompts into an agent's tmux pane, score whether the
paste actually landed, capture the pane, and wait for the agent's input box
to be live. Everything here is deterministic orchestrator code; the model
never touches tmux itself. See ``ProjectPlan.md`` §4-§11.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:  # POSIX only, which is fine: tmux is too.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as cfgmod  # noqa: E402
from config import Agent, ConfigError, SwarmConfig  # noqa: E402


# --------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------


class SwarmError(Exception):
    pass


def sleep_ms(ms: int) -> None:
    if ms > 0:
        time.sleep(ms / 1000.0)


# --------------------------------------------------------------------------
# tmux
# --------------------------------------------------------------------------


def tmux(*args: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    if not shutil.which("tmux"):
        raise SwarmError("tmux is not installed or not on PATH")
    return subprocess.run(
        ["tmux", *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


LOCK_TIMEOUT_S = 180


@contextlib.contextmanager
def file_lock(cfg: SwarmConfig, name: str, what: str = "lock"):
    """An advisory cross-process lock, used to serialise access to one pane/queue.

    Lock ordering, to keep it deadlock-free: queue -> pane -> turn state.
    """
    if fcntl is None:  # pragma: no cover
        yield
        return

    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    handle = open(cfg.run_dir / f"{name}.{what}", "w")
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
                    warn(f"{name}: timed out waiting for the {what}; proceeding anyway")
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


def warn(msg: str) -> None:  # pragma: no cover - thin stderr helper exercised elsewhere
    print(f"\033[33m!!\033[0m {msg}", file=sys.stderr)


def configure_tmux(cfg: SwarmConfig) -> str | None:
    """Set the globals the agentainer's panes inherit, before any agent pane is created.

    history-limit is only consulted when a pane is spawned, and the default (2000
    lines) is far too small to hold a long multi-agent conversation -- the user
    attaches, tries to scroll up, and the early messages are already gone. mouse
    mode lets the wheel scroll that backlog.

    Both are global options, but a tmux server with no sessions exits immediately,
    so `set -g` on a cold server (the normal state at `up`) does nothing. We hold
    the server up with a throwaway session while setting them; the agent panes
    created afterwards inherit the values. Returns the holder session name so the
    caller can tear it down once real sessions keep the server alive; None if there
    was nothing to configure. Best effort throughout: never block the agentainer
    coming up.
    """
    if cfg.tmux_history_limit <= 0 and not cfg.tmux_mouse:
        return None
    holder = f"{cfg.session_prefix}agentainer_setup"
    tmux("new-session", "-d", "-s", holder, "sleep 86400", check=False)
    if cfg.tmux_history_limit > 0:
        tmux("set-option", "-g", "history-limit", str(cfg.tmux_history_limit), check=False)
    if cfg.tmux_mouse:
        tmux("set-option", "-g", "mouse", "on", check=False)
    return holder


def session_exists(session: str) -> bool:
    try:
        tmux("has-session", "-t", f"={session}", capture=True)
        return True
    except subprocess.CalledProcessError:
        return False


PASTE_ATTEMPTS = 2
VERIFY_TIMEOUT_MS = 3000
VERIFY_SCROLLBACK = 200
NEEDLE_LEN = 28
# Both CLIs collapse a long paste into a chip instead of showing the text:
#   claude -> "[Pasted text #1 +36 lines]"
#   codex  -> "[Pasted Content 2580 chars]"
# Whitespace is stripped before matching, so this sees "Pastedtext" / "PastedContent".
PASTE_CHIP_RE = re.compile(r"pasted(?:text|content)", re.IGNORECASE)


def pane_text(session: str, scrollback: int = 0) -> str:
    args = ["capture-pane", "-p", "-t", session]
    if scrollback:
        args[2:2] = ["-S", f"-{scrollback}"]
    try:
        return tmux(*args, capture=True).stdout or ""
    except subprocess.CalledProcessError:
        return ""


def needle_for(body: str) -> str:
    """The *tail* of the text, which is what stays on screen after a paste.

    Using the head would be wrong: a long prompt pushes its own first line out
    of the pane, and the cursor -- hence the visible end of the text -- sits at
    the bottom.
    """
    return normalise(body)[-NEEDLE_LEN:]


def paste_score(session: str, needle: str) -> int:
    """How many times the text we are about to send already appears on screen."""
    pane = normalise(pane_text(session, VERIFY_SCROLLBACK))
    return pane.count(needle) + len(PASTE_CHIP_RE.findall(pane))


def send_buffer(session: str, body: str) -> None:
    buf = f"agentainer-{os.getpid()}-{int(time.time() * 1000)}"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
        fh.write(body)
        tmp = fh.name
    try:
        tmux("load-buffer", "-b", buf, tmp)
        tmux("paste-buffer", "-b", buf, "-d", "-p", "-t", session)
    finally:
        os.unlink(tmp)


def paste_into(
    cfg: SwarmConfig, session: str, text: str, enter: bool = True, needle: str | None = None
) -> bool:
    """Type *text* into a session's pane, confirm it arrived, then press Enter.

    Bracketed paste (``paste-buffer -p``) lets a multi-line prompt land in the
    agent's input box as one block instead of being submitted line by line.

    Confirming matters: a TUI can silently discard keystrokes while it is still
    starting up, and Claude Code does exactly that for several seconds partway
    through boot. So we compare the pane before and after the paste, retry if
    the text never showed up, and only press Enter once it has -- which also
    means a retry cannot submit a half-delivered prompt.
    """
    body = text.rstrip("\n")
    if not body:
        return False
    if not session_exists(session):
        raise SwarmError(f"tmux session {session!r} is not running")

    with pane_lock(cfg, session):
        return _paste_locked(cfg, session, body, enter, needle)


def _paste_locked(
    cfg: SwarmConfig, session: str, body: str, enter: bool, needle: str | None = None
) -> bool:
    needle = needle or needle_for(body)
    delivered = False

    for attempt in range(1, PASTE_ATTEMPTS + 1):
        if attempt > 1:
            # Best effort: clear anything a previous attempt may have left behind,
            # so a retry cannot concatenate two copies of the prompt.
            tmux("send-keys", "-t", session, "C-u", check=False)
            sleep_ms(300)

        before = paste_score(session, needle)
        sleep_ms(cfg.send_delay_ms)
        send_buffer(session, body)

        deadline = time.monotonic() + VERIFY_TIMEOUT_MS / 1000.0
        while time.monotonic() < deadline:
            sleep_ms(200)
            if paste_score(session, needle) > before:
                delivered = True
                break
        if delivered:
            break
        warn(f"{session}: pasted text never appeared (attempt {attempt}/{PASTE_ATTEMPTS})")

    if not delivered:
        # Do not press Enter: if the text did arrive and we simply failed to see
        # it, submitting now could send a mangled or duplicated prompt.
        warn(f"{session}: could not confirm the text arrived; NOT pressing Enter")
        return False

    if enter:
        sleep_ms(cfg.enter_delay_ms)
        tmux("send-keys", "-t", session, "Enter")
    return True


# --------------------------------------------------------------------------
# readiness probe
# --------------------------------------------------------------------------


READY_TOKEN = "zqxswarmready"


def normalise(text: str) -> str:
    """Strip all whitespace, so a TUI's line-wrapping cannot hide a needle."""
    return re.sub(r"\s+", "", text)


def visible_pane(session: str) -> str:
    try:
        return tmux("capture-pane", "-p", "-t", session, capture=True).stdout or ""
    except subprocess.CalledProcessError:
        return ""


def clear_token(session: str) -> None:
    """Backspace the readiness token back out of the composer."""
    for _ in range(6):
        count = normalise(visible_pane(session)).count(READY_TOKEN)
        if not count:
            return
        tmux("send-keys", "-t", session, *(["BSpace"] * (count * len(READY_TOKEN))))
        sleep_ms(300)


def wait_until_ready(cfg: SwarmConfig, agent: Agent) -> bool:
    """Block until the agent's TUI is actually accepting keystrokes.

    A fixed sleep is not enough: Claude Code, for instance, discards input for
    several seconds partway through its startup, and a prompt typed into that
    window is silently lost. So we type a throwaway token until the TUI echoes
    it back -- proof that its input box is live -- then erase it. Nothing is
    ever submitted, because Enter is never sent.
    """
    deadline = time.monotonic() + cfg.ready_timeout_ms / 1000.0
    with pane_lock(cfg, agent.session):
        while time.monotonic() < deadline:
            tmux("send-keys", "-t", agent.session, "-l", READY_TOKEN, check=False)
            sleep_ms(600)
            if READY_TOKEN in normalise(visible_pane(agent.session)):
                clear_token(agent.session)
                sleep_ms(cfg.send_delay_ms)
                return True
            if not session_exists(agent.session):
                return False
    return False


def capture_pane(cfg: SwarmConfig, agent: Agent) -> str:
    try:
        result = tmux(
            "capture-pane", "-p", "-J", "-S", f"-{cfg.pane_scrollback}", "-t", agent.session,
            capture=True,
        )
    except subprocess.CalledProcessError:
        return ""
    return result.stdout or ""

"""Tests for tmux.py -- the paste / capture / readiness layer.

Ported from AgentSwarm/tests/test_swarm_tmux.py. The pane-watcher cases from
that file (start/stop watcher, run_watcher) are intentionally omitted: those
functions live in a different lib module. The cases here target exactly the
functions in ``lib/tmux.py`` and drive 100% line coverage of it.
"""

import subprocess
from contextlib import contextmanager
from unittest import mock

import pytest

import tmux
from tests.support import load_swarm, mock_tmux


@contextmanager
def fast_clock():
    """Jump time.monotonic forward so verify/timeout loops exit instantly.

    Without this, a failed paste would really wait VERIFY_TIMEOUT_MS (3s) per
    attempt, and a timeout test would spin. We also no-op sleep_ms so the loops
    do not burn real wall-clock time.
    """

    class Clock:
        def __init__(self):
            self.t = 0.0

        def __call__(self):
            self.t += 1000.0
            return self.t

    clk = Clock()
    with mock.patch.object(tmux.time, "monotonic", clk), mock.patch.object(tmux, "sleep_ms"):
        yield


# ---------------------------------------------------------------------- tmux()

def test_tmux_not_installed():
    with mock.patch.object(tmux.shutil, "which", return_value=None):
        with pytest.raises(tmux.SwarmError):
            tmux.tmux("has-session", "-t", "x")


def test_tmux_runs():
    with mock_tmux() as r:
        rc = tmux.tmux("new-session", "-s", "x")
    assert rc.returncode == 0


# ---------------------------------------------------------------- configure

def test_configure_tmux_nothing_to_do(tmp_path):
    cfg = load_swarm(tmp_path, "- {name: A, command: 'true'}\n")
    cfg.tmux_history_limit = 0
    cfg.tmux_mouse = False
    assert tmux.configure_tmux(cfg) is None


def test_configure_tmux_sets_options(tmp_path):
    cfg = load_swarm(tmp_path, "- {name: A, command: 'true'}\n")
    cfg.tmux_history_limit = 1000
    cfg.tmux_mouse = True
    with mock_tmux() as r:
        holder = tmux.configure_tmux(cfg)
    assert holder == "t-agentainer_setup"
    assert any("new-session" in c for c in r.calls)
    assert any("history-limit" in c for c in r.calls)
    assert any("mouse" in c for c in r.calls)


# -------------------------------------------------------------- session/pane

def test_session_exists(tmp_path):
    with mock_tmux(has_session=True):
        assert tmux.session_exists("t-A") is True
    with mock_tmux(has_session=False):
        assert tmux.session_exists("t-A") is False


def test_pane_text_and_visible_pane(tmp_path):
    with mock_tmux(pane="hello"):
        assert tmux.pane_text("t-A") == "hello"
        assert tmux.visible_pane("t-A") == "hello"


def test_visible_pane_error_returns_empty(tmp_path):
    with mock.patch.object(
        tmux, "tmux", side_effect=subprocess.CalledProcessError(1, ["tmux"])
    ):
        assert tmux.visible_pane("t-A") == ""
        assert tmux.pane_text("t-A", 50) == ""


# ------------------------------------------------------------- small helpers

def test_sleep_ms():
    # ms == 0 must skip time.sleep entirely (no-op, never raises).
    tmux.sleep_ms(0)
    # A positive value exercises the time.sleep branch.
    with mock.patch.object(tmux.time, "sleep") as s:
        tmux.sleep_ms(50)
        s.assert_called_once_with(0.05)


def test_needle_for_short():
    assert tmux.needle_for("a") == "a"
    assert tmux.needle_for("abc\n  def") == "abcdef"[-tmux.NEEDLE_LEN:]


def test_paste_score_basic():
    with mock.patch.object(tmux, "pane_text", return_value="hello world"):
        assert tmux.paste_score("sess", "hello") == 1
    with mock.patch.object(tmux, "pane_text", return_value="Pasted text here"):
        # The paste-chip regex matches "pasted" (case-insensitive) after
        # whitespace is stripped, independent of the needle we searched for.
        assert tmux.paste_score("sess", "zzz") == 1


def test_send_buffer_writes_and_loads(tmp_path):
    with mock_tmux() as r:
        tmux.send_buffer("sess", "my body")
    assert any("load-buffer" in c for c in r.calls)
    assert any("paste-buffer" in c for c in r.calls)


# ---------------------------------------------------------------- paste_into

def test_paste_into_session_missing(tmp_path):
    cfg = load_swarm(tmp_path, "- {name: A, command: 'true'}\n")
    with mock_tmux(has_session=False):
        with pytest.raises(tmux.SwarmError):
            tmux.paste_into(cfg, "t-A", "body")


def test_paste_into_success(tmp_path):
    cfg = load_swarm(tmp_path, "- {name: A, command: 'true'}\n")

    state = {"n": 0}

    def fake_pane(session, scrollback=0):
        state["n"] += 1
        return "body" if state["n"] > 1 else ""

    with mock.patch.object(tmux, "pane_text", fake_pane):
        with mock_tmux():
            assert tmux.paste_into(cfg, "t-A", "body") is True


def test_paste_into_failure(tmp_path):
    cfg = load_swarm(tmp_path, "- {name: A, command: 'true'}\n")
    with mock.patch.object(tmux, "pane_text", return_value=""):
        with mock_tmux(), fast_clock():
            assert tmux.paste_into(cfg, "t-A", "body") is False


def test_paste_into_empty_body():
    with mock_tmux():
        assert tmux.paste_into(None, "t-A", "") is False


# -------------------------------------------------------------- _paste_locked

def test_paste_locked_failure(tmp_path):
    cfg = load_swarm(tmp_path, "- {name: A, command: 'true'}\n")
    with mock.patch.object(tmux, "pane_text", return_value=""):
        with mock_tmux(), fast_clock():
            assert tmux._paste_locked(cfg, "t-A", "body", True) is False


def test_paste_locked_success_enters(tmp_path):
    cfg = load_swarm(tmp_path, "- {name: A, command: 'true'}\n")
    state = {"n": 0}

    def fake_pane(session, scrollback=0):
        state["n"] += 1
        return "body" if state["n"] > 1 else ""

    with mock.patch.object(tmux, "pane_text", fake_pane):
        with mock_tmux() as r:
            assert tmux._paste_locked(cfg, "t-A", "body", True) is True
            assert any("Enter" in c for c in r.calls)


def test_paste_locked_no_enter(tmp_path):
    cfg = load_swarm(tmp_path, "- {name: A, command: 'true'}\n")
    state = {"n": 0}

    def fake_pane(session, scrollback=0):
        state["n"] += 1
        return "body" if state["n"] > 1 else ""

    with mock.patch.object(tmux, "pane_text", fake_pane):
        with mock_tmux() as r:
            assert tmux._paste_locked(cfg, "t-A", "body", False) is True
            assert not any("Enter" in c for c in r.calls)


def test_paste_locked_explicit_needle(tmp_path):
    # Passing a needle should short-circuit needle_for but still deliver.
    cfg = load_swarm(tmp_path, "- {name: A, command: 'true'}\n")
    state = {"n": 0}

    def fake_pane(session, scrollback=0):
        state["n"] += 1
        return "body" if state["n"] > 1 else ""

    with mock.patch.object(tmux, "pane_text", fake_pane):
        with mock_tmux() as r:
            assert tmux._paste_locked(cfg, "t-A", "body", True, needle="body") is True
            assert any("Enter" in c for c in r.calls)


# ---------------------------------------------------------------- clear_token

def test_clear_token_backspaces(tmp_path):
    with mock_tmux(pane=tmux.READY_TOKEN) as r:
        tmux.clear_token("t-A")
    assert any("BSpace" in c for c in r.calls)


def test_clear_token_already_empty(tmp_path):
    # When the token is not present, clear_token returns before sending any keys.
    with mock_tmux(pane="") as r:
        tmux.clear_token("t-A")
    assert not any("BSpace" in c for c in r.calls)


# -------------------------------------------------------------- wait_until_ready

def test_wait_until_ready_token_echoed(tmp_path):
    cfg = load_swarm(tmp_path, "- {name: A, command: 'true'}\n")
    agent = cfg.get("A")
    with mock.patch.object(tmux, "sleep_ms"):
        with mock_tmux(pane=tmux.READY_TOKEN):
            assert tmux.wait_until_ready(cfg, agent) is True


def test_wait_until_ready_session_gone(tmp_path):
    cfg = load_swarm(tmp_path, "- {name: A, command: 'true'}\n")
    agent = cfg.get("A")
    # No fast_clock: let the loop body actually run so we hit the in-loop
    # "session gone" check and return False from inside the loop (line 317).
    with mock_tmux(has_session=False):
        assert tmux.wait_until_ready(cfg, agent) is False


def test_wait_until_ready_timeout(tmp_path):
    cfg = load_swarm(tmp_path, "- {name: A, command: 'true'}\n")
    agent = cfg.get("A")
    cfg.ready_timeout_ms = 0
    with mock_tmux(has_session=True, pane=""), fast_clock():
        assert tmux.wait_until_ready(cfg, agent) is False


# ---------------------------------------------------------------- file_lock

def test_file_lock_retries_then_succeeds(tmp_path):
    # flock fails twice (contention) then succeeds: exercises the retry branch
    # that sleeps 0.05s and re-loops without timing out.
    cfg = load_swarm(tmp_path, "- {name: A, command: 'true'}\n")
    calls = {"n": 0}

    def fake_flock(handle, flag):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise OSError("busy")

    class SlowClock:
        def __init__(self):
            self.t = 0.0

        def __call__(self):
            self.t += 0.01
            return self.t

    with mock.patch.object(tmux.fcntl, "flock", fake_flock), mock.patch.object(
        tmux.time, "monotonic", SlowClock()
    ):
        with tmux.file_lock(cfg, "t-A", "pane.lock"):
            pass
    assert calls["n"] >= 3


def test_file_lock_times_out(tmp_path):
    # flock never succeeds: the lock times out, warns, and proceeds anyway
    # (locked stays False so the finally block skips the unlock).
    cfg = load_swarm(tmp_path, "- {name: A, command: 'true'}\n")

    def fake_flock(handle, flag):
        raise OSError("busy")

    class FastClock:
        def __init__(self):
            self.t = 0.0

        def __call__(self):
            self.t += 1000.0
            return self.t

    with mock.patch.object(tmux.fcntl, "flock", fake_flock), mock.patch.object(
        tmux.time, "monotonic", FastClock()
    ):
        with tmux.file_lock(cfg, "t-A", "pane.lock"):
            pass


# -------------------------------------------------------------- wait_until_ready

def test_wait_until_ready_no_token_session_alive(tmp_path):
    # The loop actually runs (real monotonic), the token never appears, but the
    # session stays up, so we hit the in-loop session check and eventually time out.
    cfg = load_swarm(tmp_path, "- {name: A, command: 'true'}\n")
    agent = cfg.get("A")
    cfg.ready_timeout_ms = 700
    with mock_tmux(has_session=True, pane=""):
        assert tmux.wait_until_ready(cfg, agent) is False


# ---------------------------------------------------------------- capture_pane

def test_capture_pane(tmp_path):
    cfg = load_swarm(tmp_path, "- {name: A, command: 'true'}\n")
    with mock_tmux(pane="pane text") as r:
        assert tmux.capture_pane(cfg, cfg.get("A")) == "pane text"
    with mock.patch.object(
        tmux, "tmux", side_effect=subprocess.CalledProcessError(1, ["tmux"])
    ):
        assert tmux.capture_pane(cfg, cfg.get("A")) == ""

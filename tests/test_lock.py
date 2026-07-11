"""Tests for lib/lock.py -- 100 % line coverage.

Locks are exercised with mocks where real contention would block (flock on a
second file description in the same process would deadlock), so the suite runs
fast and offline. The happy path uses the real fcntl lock on a temp runtime.
"""

import sys
from pathlib import Path
from unittest import mock

import fcntl
import pytest

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import lock  # noqa: E402


def test_file_lock_normal_acquire_release(tmp_runtime):
    """Normal happy path: acquire, hold, release; lock file lives under runtime."""
    cfg = tmp_runtime
    with lock.file_lock(cfg, "queue-a"):
        pass
    # The lock file is created under cfg.runtime.
    assert (cfg.runtime / "queue-a.lock").exists()


def test_file_lock_distinct_names_serial(tmp_runtime):
    """Distinct lock names each acquire cleanly (real flock, no contention)."""
    cfg = tmp_runtime
    with lock.file_lock(cfg, "queue-b1"):
        with lock.file_lock(cfg, "queue-b2"):
            pass
    assert (cfg.runtime / "queue-b1.lock").exists()
    assert (cfg.runtime / "queue-b2.lock").exists()


def test_file_lock_busy_then_acquired(tmp_runtime):
    """First flock fails (contended) but we retry and succeed before deadline."""
    cfg = tmp_runtime
    state = {"calls": 0}

    def flock_side(handle, op):
        state["calls"] += 1
        if state["calls"] == 1:
            raise OSError("contended")
        # Second attempt: success (returns None).

    with mock.patch.object(fcntl, "flock", side_effect=flock_side), \
         mock.patch.object(lock.time, "monotonic", return_value=0.0):
        # deadline == 180; monotonic stays 0 so we never time out; we sleep once.
        with lock.file_lock(cfg, "queue-c"):
            pass
    assert state["calls"] >= 2
    assert (cfg.runtime / "queue-c.lock").exists()


def test_file_lock_timeout_proceeds_anyway(tmp_runtime):
    """If we never get the lock before the deadline, warn and proceed unlocked."""
    cfg = tmp_runtime

    def mono():
        # First read sets the deadline (0 + 180). Every later read is past it.
        mono.first = getattr(mono, "first", True)
        if mono.first:
            mono.first = False
            return 0.0
        return 1000.0

    with mock.patch.object(fcntl, "flock", side_effect=OSError("contended")), \
         mock.patch.object(lock.time, "monotonic", side_effect=mono):
        with lock.file_lock(cfg, "queue-d"):
            pass
    # Timed out: still created the file, but never held the lock.
    assert (cfg.runtime / "queue-d.lock").exists()


def test_file_lock_unlock_error_is_suppressed(tmp_runtime):
    """A failing unlock must not raise out of the context manager."""
    cfg = tmp_runtime

    def flock_side(handle, op):
        if op == fcntl.LOCK_UN:
            raise OSError("cannot unlock")
        # Acquire succeeds.

    with mock.patch.object(fcntl, "flock", side_effect=flock_side):
        with lock.file_lock(cfg, "queue-e"):
            pass
    assert (cfg.runtime / "queue-e.lock").exists()


def test_pane_lock_creates_pane_lock_file(tmp_runtime):
    """pane_lock delegates to file_lock with the pane.lock suffix."""
    cfg = tmp_runtime
    with lock.pane_lock(cfg, "sess-x"):
        pass
    assert (cfg.runtime / "sess-x.pane.lock").exists()


def test_pane_lock_returns_context_manager(tmp_runtime):
    cfg = tmp_runtime
    cm = lock.pane_lock(cfg, "sess-y")
    assert cm is not None
    # It is usable as a context manager.
    with cm:
        pass
    assert (cfg.runtime / "sess-y.pane.lock").exists()

"""Regression tests for the release_next TOCTOU race (and supervisor resilience).

The bug (see analysis): ``release_next`` used to read the inbox / pick
``files[0]`` OUTSIDE the per-recipient lock, guarding only the move.
Two concurrent ``release_next(B)`` callers -- two agents both stopping
and releasing into B, or a hook firing during a supervisor tick -- could
each observe an empty inbox, each pick the same file, and either land
two messages in one inbox or crash with ``FileNotFoundError`` (which,
uncaught in the supervisor tick, killed the liveness heartbeat).

These tests prove the fixed version keeps one-at-a-time and never crashes.
The race is inherently cross-PROCESS (``fcntl.flock`` is per open
file description), so we use ``multiprocessing`` -- threads would NOT
exercise the OS lock.
"""
import sys
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import config as cfgmod  # noqa: E402
import mail             # noqa: E402
import log              # noqa: E402
import tmux             # noqa: E402
import supervisor       # noqa: E402
import multiprocessing as mp  # noqa: E402

YAML = """
swarm:
  name: race
  root: {root}/ws
defaults:
  capture: none
agents:
  - name: A
    type: claude
    command: "true"
    can_talk_to: [B]
  - name: B
    type: claude
    command: "true"
    can_talk_to: [A]
  - name: C
    type: claude
    command: "true"
    can_talk_to: [B]
"""


def _write_config(tmp_path):
    p = tmp_path / "agentainer.yaml"
    p.write_text(YAML.format(root=tmp_path))
    return p


def _seed(tmp_path, k):
    """Empty B's inbox, queue k messages, return the loaded config."""
    cfg = cfgmod.load(_write_config(tmp_path))
    b_q = cfg.queue_dir / "B"
    b_inbox = cfg.mail_paths(cfg.get("B")).inbox
    shutil.rmtree(b_inbox, ignore_errors=True)
    b_q.mkdir(parents=True, exist_ok=True)
    for f in list(b_q.iterdir()):
        f.unlink()
    for i in range(k):
        (b_q / f"m{i}.txt").write_text(f"hi {i}")
    return cfg


def _worker(barrier, path):
    barrier.wait()                       # all pass the unlocked check together
    cfg = cfgmod.load(path)            # independent process, like a fresh on_stop
    mail.release_next(cfg, "B")


def test_release_next_is_atomic_under_concurrent_callers(tmp_path):
    ctx = mp.get_context("fork")
    k = 10
    rounds = 20
    for _ in range(rounds):
        cfg = _seed(tmp_path, k)
        barrier = ctx.Barrier(k)
        procs = [ctx.Process(target=_worker, args=(barrier, str(cfg.path)))
                  for _ in range(k)]
        for p in procs:
            p.start()
        for p in procs:
            p.join()
            assert p.exitcode == 0, f"release_next crashed: exit {p.exitcode}"
        b_inbox = cfg.mail_paths(cfg.get("B")).inbox
        inbox_files = [f for f in b_inbox.iterdir() if f.is_file()]
        q_files = [f for f in (cfg.queue_dir / "B").iterdir() if f.is_file()]
        # One-at-a-time preserved: exactly one delivered, the rest stay queued.
        assert len(inbox_files) == 1, inbox_files
        assert len(q_files) == k - 1, q_files


def test_release_next_swallows_vanished_source(tmp_path, monkeypatch):
    cfg = _seed(tmp_path, 1)

    def flaky_move(src, dst):
        raise FileNotFoundError(f"gone: {src}")

    monkeypatch.setattr(shutil, "move", flaky_move)
    # A lost race (file already moved by another process) must NOT raise.
    assert mail.release_next(cfg, "B") is False
    b_inbox = cfg.mail_paths(cfg.get("B")).inbox
    assert not any(b_inbox.iterdir()), "nothing should have been delivered"


def test_supervisor_tick_error_does_not_kill_heartbeat(tmp_path, monkeypatch):
    cfg = cfgmod.load(_write_config(tmp_path))
    cfg.supervise_interval_ms = 0

    # First tick proceeds to supervise_once; afterwards no watched session -> exits.
    state = {"n": 0}

    def fake_exists(*a, **k):
        state["n"] += 1
        return state["n"] == 1

    monkeypatch.setattr(tmux, "session_exists", fake_exists)

    def boom(*a, **k):
        raise RuntimeError("tick exploded")

    monkeypatch.setattr(supervisor, "supervise_once", boom)

    captured = []
    monkeypatch.setattr(log, "log_event", lambda *a, **k: captured.append(a))

    # Must NOT propagate -- the heartbeat survives a single agent's failure.
    supervisor.run_supervisor(cfg, ["B"])
    assert any("tick-error" in c for c in captured if len(c) > 2), captured

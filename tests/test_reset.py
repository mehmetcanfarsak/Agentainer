"""Tests for lib/reset.py -- the two-level swarm reset / start-over core.

Exercised without a real tmux: the guard is monkeypatched, and the filesystem
effects (runtime + mailboxes soft-cleared; workspaces hard-wiped) are asserted
directly. Shared by all four control planes, so these lock the behaviour once.
"""

import shutil

import pytest

import config as cfgmod
import mail
import reset
from support import load_swarm, write_config


def _seed_state(cfg):
    """Create runtime state + a mailbox message + a work file for each agent."""
    (cfg.runtime / "logs").mkdir(parents=True, exist_ok=True)
    (cfg.runtime / "logs" / "e.jsonl").write_text("{}\n")
    mail.init_mailboxes(cfg)
    for a in cfg.agents:
        mp = cfg.mail_paths(a)
        (mp.inbox / "m.txt").write_text("stale")
        a.workdir.mkdir(parents=True, exist_ok=True)
        (a.workdir / "work.txt").write_text("real work")


# ---- guard_stopped --------------------------------------------------------


def test_guard_passes_without_tmux(monkeypatch, tmp_path):
    cfg = load_swarm(tmp_path, "  - {name: dev, can_talk_to: [user]}\n")
    monkeypatch.setattr(reset.shutil, "which", lambda name: None)
    reset.guard_stopped(cfg)  # no tmux => nothing to protect


def test_guard_refuses_running_agent(monkeypatch, tmp_path):
    cfg = load_swarm(tmp_path, "  - {name: dev, can_talk_to: [user]}\n")
    monkeypatch.setattr(reset.shutil, "which", lambda name: "/usr/bin/tmux")
    monkeypatch.setattr(reset.tmux, "session_exists", lambda s: True)
    with pytest.raises(reset.ResetError):
        reset.guard_stopped(cfg)


def test_guard_refuses_live_supervisor(monkeypatch, tmp_path):
    cfg = load_swarm(tmp_path, "  - {name: dev, can_talk_to: [user]}\n")
    monkeypatch.setattr(reset.shutil, "which", lambda name: "/usr/bin/tmux")
    monkeypatch.setattr(reset.tmux, "session_exists", lambda s: False)
    monkeypatch.setattr(reset, "_supervisor_alive", lambda c: True)
    with pytest.raises(reset.ResetError):
        reset.guard_stopped(cfg)


def test_guard_passes_when_all_down(monkeypatch, tmp_path):
    cfg = load_swarm(tmp_path, "  - {name: dev, can_talk_to: [user]}\n")
    monkeypatch.setattr(reset.shutil, "which", lambda name: "/usr/bin/tmux")
    monkeypatch.setattr(reset.tmux, "session_exists", lambda s: False)
    monkeypatch.setattr(reset, "_supervisor_alive", lambda c: False)
    reset.guard_stopped(cfg)


def test_supervisor_alive_delegates(monkeypatch, tmp_path):
    cfg = load_swarm(tmp_path, "  - {name: dev, can_talk_to: [user]}\n")
    import supervisor
    monkeypatch.setattr(supervisor, "supervisor_alive", lambda c: True)
    assert reset._supervisor_alive(cfg) is True


# ---- reset_state (soft) ---------------------------------------------------


def test_reset_state_clears_runtime_keeps_work(tmp_path):
    cfg = load_swarm(tmp_path, "  - {name: dev, can_talk_to: [user]}\n")
    _seed_state(cfg)
    wd = cfg.get("dev").workdir
    removed = reset.reset_state(cfg)
    assert not cfg.runtime.exists()
    assert not cfg.mail_paths(cfg.get("dev")).inbox.exists()
    assert (wd / "work.txt").exists()  # work is kept
    assert cfg.runtime in removed


def test_reset_state_nothing_when_clean(tmp_path):
    cfg = load_swarm(tmp_path, "  - {name: dev, can_talk_to: [user]}\n")
    assert reset.reset_state(cfg) == []


# ---- wipe_all (hard) ------------------------------------------------------


def test_wipe_deletes_work_recreates_empty(tmp_path):
    cfg = load_swarm(tmp_path, "  - {name: dev, can_talk_to: [user]}\n")
    _seed_state(cfg)
    wd = cfg.get("dev").workdir
    removed, warnings = reset.wipe_all(cfg)
    assert warnings == []
    assert wd.exists() and not (wd / "work.txt").exists()  # emptied, recreated
    assert wd in removed
    # The stale runtime state is gone (a fresh empty runtime may be re-seeded so
    # the swarm is immediately launchable again).
    assert not (cfg.runtime / "logs" / "e.jsonl").exists()


def test_wipe_dedupes_shared_workdir(tmp_path):
    # Two agents sharing one workdir (under root) -> deleted exactly once.
    shared = tmp_path / "ws" / "shared"
    body = (
        f"swarm:\n  root: {tmp_path/'ws'}\n  session_prefix: 't-'\n"
        "defaults: {type: claude}\n"
        "agents:\n"
        f"  - {{name: a, workdir: {shared}, can_talk_to: [user]}}\n"
        f"  - {{name: b, workdir: {shared}, can_talk_to: [user]}}\n"
    )
    path = write_config(tmp_path, body)
    cfg = cfgmod.load(path)
    shared.mkdir(parents=True)
    (shared / "f.txt").write_text("x")
    removed, warnings = reset.wipe_all(cfg)
    assert shared in removed
    assert removed.count(shared) == 1


def test_wipe_skips_workdir_outside_root(tmp_path):
    # A workdir pointed OUTSIDE the swarm root is never deleted (e.g. a $HOME typo).
    outside = tmp_path / "elsewhere"
    body = (
        f"swarm:\n  root: {tmp_path/'ws'}\n  session_prefix: 't-'\n"
        "defaults: {type: claude}\n"
        "agents:\n"
        f"  - {{name: dev, workdir: {outside}, can_talk_to: [user]}}\n"
    )
    path = write_config(tmp_path, body)
    cfg = cfgmod.load(path)
    outside.mkdir()
    (outside / "precious.txt").write_text("keep")
    removed, warnings = reset.wipe_all(cfg)
    assert outside not in removed
    assert (outside / "precious.txt").exists()
    assert any("outside the swarm root" in w for w in warnings)


def test_wipe_skips_workdir_holding_config(tmp_path):
    # workdir == the directory holding agentainer.yaml -> never deleted.
    body = (
        f"swarm:\n  root: {tmp_path}\n  session_prefix: 't-'\n"
        "defaults: {type: claude}\n"
        "agents:\n"
        f"  - {{name: dev, workdir: {tmp_path}, can_talk_to: [user]}}\n"
    )
    path = write_config(tmp_path, body)
    cfg = cfgmod.load(path)
    (tmp_path / "keep.txt").write_text("x")
    removed, warnings = reset.wipe_all(cfg)
    assert path.exists() and (tmp_path / "keep.txt").exists()
    assert any("holds the swarm config" in w for w in warnings)


def test_wipe_ignores_missing_workdir(tmp_path):
    cfg = load_swarm(tmp_path, "  - {name: dev, can_talk_to: [user]}\n")
    # No _seed_state -> the workdir never existed.
    removed, warnings = reset.wipe_all(cfg)
    assert warnings == []


# ---- _config_is_inside ----------------------------------------------------


def test_config_is_inside(tmp_path):
    d = tmp_path / "ws"
    d.mkdir()
    assert reset._config_is_inside(d, d / "agentainer.yaml")
    assert reset._config_is_inside(d, d)  # dir itself
    assert not reset._config_is_inside(d, tmp_path / "agentainer.yaml")


# ---- reset dispatch -------------------------------------------------------


def test_reset_dispatch_levels(tmp_path):
    cfg = load_swarm(tmp_path, "  - {name: dev, can_talk_to: [user]}\n")
    _seed_state(cfg)
    r = reset.reset(cfg, "state")
    assert r["level"] == "state" and r["warnings"] == []
    _seed_state(cfg)
    r2 = reset.reset(cfg, "full")
    assert r2["level"] == "full"
    assert all(isinstance(p, str) for p in r2["removed"])


def test_reset_bad_level(tmp_path):
    cfg = load_swarm(tmp_path, "  - {name: dev, can_talk_to: [user]}\n")
    with pytest.raises(reset.ResetError):
        reset.reset(cfg, "nuke")


def test_sys_path_guard_inserts():
    """Cover the lib/ -> sys.path guard (fires only when lib/ is absent from
    sys.path); load the module source directly with lib/ removed. Mirrors the
    same probe in test_reconcile."""
    import importlib.util
    import sys

    lib_dir = str(reset._LIB)
    saved = list(sys.path)
    sys.path = [p for p in sys.path if p != lib_dir]
    try:
        spec = importlib.util.spec_from_file_location("_reset_guard_probe", reset.__file__)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = saved
    assert mod.LEVELS == ("state", "full")

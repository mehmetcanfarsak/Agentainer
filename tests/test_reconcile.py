"""100% line-coverage tests for ``reconcile`` (P4 dynamic reconcile).

All tmux/session behaviour is mocked: reconcile is exercised purely through the
core modules, so no real tmux server or API keys are needed. The config write
path is validated both via PyYAML (when present) and the stdlib ``minyaml``
fallback, because the no-PyYAML path is a release invariant.
"""

from types import SimpleNamespace
from unittest import mock
from pathlib import Path

import reconcile
import config as cfgmod
import tmux as tmuxmod
from support import load_swarm


AGENTS = """
  - name: alice
    type: claude
    can_talk_to: ["user"]
    role: "you are alice"
  - name: bob
    type: gemini
    can_talk_to: [alice, user]
    role: "you are bob"
"""


# --------------------------------------------------------------------------
# diff
# --------------------------------------------------------------------------


def test_diff_missing_and_running(tmp_path):
    cfg = load_swarm(tmp_path, AGENTS)
    with mock.patch.object(
        tmuxmod, "session_exists", side_effect=lambda s: s == "t-alice"
    ), mock.patch.object(reconcile, "_running_sessions", return_value=[]):
        d = reconcile.diff(cfg)
    assert d["configured"] == ["alice", "bob"]
    assert d["running"] == ["alice"]
    assert d["missing"] == ["bob"]
    assert d["extra"] == []


def test_diff_extra_session(tmp_path):
    cfg = load_swarm(tmp_path, AGENTS)
    with mock.patch.object(tmuxmod, "session_exists", return_value=True), mock.patch.object(
        reconcile,
        "_running_sessions",
        return_value=["t-alice", "t-bob", "t-ghost"],
    ):
        d = reconcile.diff(cfg)
    # t-alice / t-bob map back to configured agents; t-ghost is orphaned.
    assert d["extra"] == ["t-ghost"]
    assert d["missing"] == []


def test_agent_for_session_maps_and_rejects(tmp_path):
    cfg = load_swarm(tmp_path, AGENTS)
    assert reconcile._agent_for_session(cfg, "t-alice").name == "alice"
    assert reconcile._agent_for_session(cfg, "t-ghost") is None


# --------------------------------------------------------------------------
# reconcile
# --------------------------------------------------------------------------


def test_reconcile_starts_missing(tmp_path):
    cfg = load_swarm(tmp_path, AGENTS)
    started = []
    calls = []

    def fake_start(c, agent, resume_cmd):
        started.append(agent.name)
        calls.append((c, agent))

    with mock.patch.object(tmuxmod, "session_exists", return_value=False), mock.patch.object(
        reconcile, "_running_sessions", return_value=[]
    ):
        result = reconcile.reconcile(cfg, _start_fn=fake_start)
    assert sorted(started) == ["alice", "bob"]
    assert result["started"] == ["alice", "bob"]
    assert result["stopped"] == []
    assert len(calls) == 2


def test_reconcile_stops_extra(tmp_path):
    cfg = load_swarm(tmp_path, AGENTS)
    killed = []

    def fake_tmux(*args, **kw):
        from unittest import mock as _m

        if args and args[0] == "kill-session":
            killed.append(args)
        return _m.MagicMock(stdout="", stderr="", returncode=0)

    with mock.patch.object(tmuxmod, "session_exists", return_value=True), mock.patch.object(
        reconcile, "_running_sessions", return_value=["t-ghost"]
    ), mock.patch.object(tmuxmod, "tmux", side_effect=fake_tmux):
        result = reconcile.reconcile(cfg)
    assert result["stopped"] == ["t-ghost"]
    assert any(a[2] == "=t-ghost" for a in killed)
    assert result["started"] == []


def test_reconcile_noop_when_in_sync(tmp_path):
    cfg = load_swarm(tmp_path, AGENTS)
    with mock.patch.object(tmuxmod, "session_exists", return_value=True), mock.patch.object(
        reconcile, "_running_sessions", return_value=["t-alice", "t-bob"]
    ):
        result = reconcile.reconcile(cfg)
    assert result["started"] == []
    assert result["stopped"] == []
    assert result["running"] == ["alice", "bob"]


def test_reconcile_can_disable_start_and_stop(tmp_path):
    cfg = load_swarm(tmp_path, AGENTS)
    with mock.patch.object(tmuxmod, "session_exists", return_value=False), mock.patch.object(
        reconcile, "_running_sessions", return_value=["t-ghost"]
    ):
        result = reconcile.reconcile(
            cfg, start_missing=False, stop_extra=False, _start_fn=lambda *a, **k: None
        )
    assert result["started"] == []
    assert result["stopped"] == []


# --------------------------------------------------------------------------
# config mutation: add / remove / edit
# --------------------------------------------------------------------------


def test_add_agent_and_duplicate(tmp_path):
    cfg = load_swarm(tmp_path, AGENTS)
    new = reconcile.add_agent(cfg, "carol", "claude", "echo hi", "user", role="be helpful")
    assert "carol" in new.names()
    assert new.get("carol").type == "claude"
    assert new.get("carol").can_talk_to == ["user"]
    # The change landed on disk and round-trips through load().
    reloaded = cfgmod.load(cfg.path)
    assert "carol" in reloaded.names()
    try:
        reconcile.add_agent(cfg, "carol", "claude", "x", "user")
        assert False, "duplicate add should raise"
    except ValueError:
        pass


def test_add_agent_wildcard_and_workdir(tmp_path):
    cfg = load_swarm(tmp_path, AGENTS)
    new = reconcile.add_agent(
        cfg, "dave", "codex", "codex", "*", workdir=str(tmp_path / "dave-wd")
    )
    # "*" expands to every *other* configured agent (Pass 2 of config.load).
    assert set(new.get("dave").can_talk_to) == {"alice", "bob"}
    assert new.get("dave").workdir == tmp_path / "dave-wd"


def test_remove_agent_and_missing(tmp_path):
    cfg = load_swarm(tmp_path, AGENTS)
    new = reconcile.remove_agent(cfg, "bob")
    assert "bob" not in new.names()
    assert "alice" in new.names()
    try:
        reconcile.remove_agent(cfg, "ghost")
        assert False, "removing missing agent should raise"
    except ValueError:
        pass


def test_edit_agent_coercion(tmp_path):
    cfg = load_swarm(tmp_path, AGENTS)
    new = reconcile.edit_agent(
        cfg, "alice", can_talk_to="bob,user", boot_delay_ms="500", capture="pane"
    )
    assert new.get("alice").can_talk_to == ["bob", "user"]
    # boot_delay_ms is not a first-class Agent field, but the raw edit is honored
    # on re-load via load_raw: the value is stored as an int.
    raw = reconcile.load_raw(cfg.path)
    alice = next(a for a in raw["agents"] if a["name"] == "alice")
    assert alice["boot_delay_ms"] == 500
    assert alice["capture"] == "pane"
    try:
        reconcile.edit_agent(cfg, "ghost", role="x")
        assert False, "editing missing agent should raise"
    except ValueError:
        pass


# --------------------------------------------------------------------------
# YAML emitter round-trip (both loaders)
# --------------------------------------------------------------------------


def test_dump_roundtrip_via_yaml(tmp_path):
    cfg = load_swarm(tmp_path, AGENTS)
    raw = reconcile.load_raw(cfg.path)
    out = tmp_path / "roundtrip.yaml"
    reconcile.write_raw(out, raw)
    reloaded = reconcile.load_raw(out)
    assert reloaded == raw


def test_dump_emits_empty_collections_not_strings():
    # An empty list/dict must stay a collection, never become the string "[]"/"{}"
    # (which config would misinterpret). Regression guard.
    text = reconcile._dump({"a": [], "b": {}, "c": [1, 2], "d": {"e": 3}})
    assert "a: []" in text
    assert "b: {}" in text
    assert "c:" in text and "- 1" in text
    assert "d:" in text and "e: 3" in text


def test_roundtrip_config_with_empty_can_talk_to(tmp_path):
    # defaults.can_talk_to: [] must survive a write/read round-trip as a list.
    body = (
        "swarm:\n  name: x\n  root: ./w\n"
        "defaults:\n  can_talk_to: []\n"
        "agents:\n  - name: a\n    type: claude\n    can_talk_to: []\n"
        "    command: echo hi\n"
    )
    path = tmp_path / "agentainer.yaml"
    path.write_text(body)
    raw = reconcile.load_raw(path)
    out = tmp_path / "rt.yaml"
    reconcile.write_raw(out, raw)
    reloaded = reconcile.load_raw(out)
    assert reloaded["defaults"]["can_talk_to"] == []
    assert reloaded["agents"][0]["can_talk_to"] == []


def test_dump_roundtrip_via_minyaml(tmp_path):
    cfg = load_swarm(tmp_path, AGENTS)
    with mock.patch.object(reconcile, "have_yaml", return_value=False):
        raw = reconcile.load_raw(cfg.path)
        out = tmp_path / "rt-minyaml.yaml"
        reconcile.write_raw(out, raw)
        reloaded = reconcile.load_raw(out)
    assert reloaded == raw


def test_have_yaml_false_branch():
    with mock.patch.dict("sys.modules", {"yaml": None}):
        # Force ImportError on `import yaml` inside have_yaml.
        import builtins

        real_import = builtins.__import__

        def block_yaml(name, *a, **k):
            if name == "yaml" or name.startswith("yaml."):
                raise ImportError("blocked for test")
            return real_import(name, *a, **k)

        builtins.__import__ = block_yaml
        try:
            assert reconcile.have_yaml() is False
        finally:
            builtins.__import__ = real_import


# --------------------------------------------------------------------------
# CLI handlers
# --------------------------------------------------------------------------


def test_cli_add_invokes_reconcile(tmp_path):
    cfg = load_swarm(tmp_path, AGENTS)
    with mock.patch.object(
        tmuxmod, "session_exists", return_value=False
    ), mock.patch.object(reconcile, "_running_sessions", return_value=[]), mock.patch.object(
        reconcile, "reconcile", return_value={"started": ["carol"], "stopped": []}
    ):
        rc = reconcile.cmd_add(
            SimpleNamespace(
                config=str(cfg.path),
                name="carol",
                type="claude",
                command="echo hi",
                can_talk_to="user",
                role="hi",
                workdir=None,
            )
        )
    assert rc == 0
    # The agent was written to disk even though reconcile was mocked.
    assert "carol" in cfgmod.load(cfg.path).names()


def test_cli_remove_missing_returns_1(tmp_path):
    cfg = load_swarm(tmp_path, AGENTS)
    with mock.patch.object(tmuxmod, "session_exists", return_value=False):
        rc = reconcile.cmd_remove(SimpleNamespace(config=str(cfg.path), name="ghost"))
    assert rc == 1


def test_cli_remove_existing_stops_session(tmp_path):
    cfg = load_swarm(tmp_path, AGENTS)
    killed = []
    real_tmux = tmuxmod.tmux

    def fake_tmux(*args, **kw):
        if args and args[0] == "kill-session":
            killed.append(args)
        return mock.MagicMock(stdout="", stderr="", returncode=0)

    with mock.patch.object(tmuxmod, "session_exists", return_value=True), mock.patch.object(
        reconcile, "reconcile", return_value={"started": [], "stopped": []}
    ), mock.patch.object(tmuxmod, "tmux", side_effect=fake_tmux):
        rc = reconcile.cmd_remove(SimpleNamespace(config=str(cfg.path), name="bob"))
    assert rc == 0
    assert "bob" not in cfgmod.load(cfg.path).names()
    assert killed  # the session was killed before the config edit


def test_cli_edit_with_bad_set_and_empty(tmp_path):
    cfg = load_swarm(tmp_path, AGENTS)
    # No --set pairs: should warn and return 1.
    with mock.patch.object(reconcile, "reconcile", return_value={"started": [], "stopped": []}):
        rc = reconcile.cmd_edit(SimpleNamespace(config=str(cfg.path), name="alice", set=[]))
    assert rc == 1
    # A malformed pair is skipped, but a good pair still applies.
    with mock.patch.object(reconcile, "reconcile", return_value={"started": [], "stopped": []}):
        rc = reconcile.cmd_edit(
            SimpleNamespace(config=str(cfg.path), name="alice", set=["garbage", "role=updated"])
        )
    assert rc == 0
    raw = reconcile.load_raw(cfg.path)
    alice = next(a for a in raw["agents"] if a["name"] == "alice")
    assert alice["role"] == "updated"


def test_cli_reconcile_handler(tmp_path):
    cfg = load_swarm(tmp_path, AGENTS)
    with mock.patch.object(
        reconcile,
        "reconcile",
        return_value={
            "started": [],
            "stopped": [],
            "running": ["alice", "bob"],
            "missing": [],
            "extra": [],
        },
    ) as m:
        rc = reconcile.cmd_reconcile(SimpleNamespace(config=str(cfg.path)))
    assert rc == 0
    m.assert_called_once()


def test_main_dispatch_add(tmp_path):
    # End-to-end through main(): add an agent. launch_agent_full is stubbed so no
    # real tmux session is created; session_exists is forced so nothing is "missing".
    import cli

    cfg = load_swarm(tmp_path, AGENTS)
    with mock.patch.object(
        cli, "launch_agent_full", lambda *a, **k: None
    ), mock.patch.object(tmuxmod, "session_exists", return_value=True), mock.patch.object(
        reconcile, "_running_sessions", return_value=[]
    ):
        rc = cli.main(
            [
                "add",
                "carol",
                "--type",
                "claude",
                "--command",
                "echo hi",
                "-c",
                str(cfg.path),
            ]
        )
    assert rc == 0
    assert "carol" in cfgmod.load(cfg.path).names()


# --------------------------------------------------------------------------
# misc branches (scalar emitter, coercion, sys.path guard, _running_sessions)
# --------------------------------------------------------------------------


def test_scalar_renderer_variants():
    assert reconcile._scalar(None) == "null"
    assert reconcile._scalar(True) == "true"
    assert reconcile._scalar(False) == "false"
    assert reconcile._scalar("") == '""'
    assert reconcile._scalar(1.5) == "1.5"


def test_coerce_field_wildcard_and_bool():
    assert reconcile._coerce_field("can_talk_to", "*") == "*"
    assert reconcile._coerce_field("flag", "true") is True
    assert reconcile._coerce_field("flag", "false") is False
    assert reconcile._coerce_field("n", "42") == 42
    assert reconcile._coerce_field("ratio", "1.5") == 1.5
    assert reconcile._coerce_field("role", "plain text") == "plain text"


def test_add_agent_with_extra_kwargs(tmp_path):
    cfg = load_swarm(tmp_path, AGENTS)
    new = reconcile.add_agent(
        cfg, "erin", "claude", "echo hi", "user", role="r", resume_args="--foo"
    )
    assert "erin" in new.names()
    raw = reconcile.load_raw(cfg.path)
    erin = next(a for a in raw["agents"] if a["name"] == "erin")
    assert erin["resume_args"] == "--foo"


def test_parse_can_talk_to_edge():
    assert reconcile._parse_can_talk_to("") == []
    assert reconcile._parse_can_talk_to("*") == "*"
    assert reconcile._parse_can_talk_to("a, b") == ["a", "b"]


def test_running_sessions_lists_and_handles_error(tmp_path):
    cfg = load_swarm(tmp_path, AGENTS)
    cp = mock.MagicMock(stdout="t-alice\nt-ghost\n", stderr="", returncode=0)
    with mock.patch.object(tmuxmod, "tmux", return_value=cp):
        assert reconcile._running_sessions("t-") == ["t-alice", "t-ghost"]
    with mock.patch.object(tmuxmod, "tmux", side_effect=RuntimeError("no tmux")):
        assert reconcile._running_sessions("t-") == []


def test_sys_path_guard_inserts():
    """The lib/ -> sys.path guard must run for 100% coverage.

    It only fires when reconcile is imported with lib/ NOT already on sys.path,
    which depends on import order in the suite. Force it deterministically by
    loading the module source directly (its dependencies are already cached in
    sys.modules) with lib/ temporarily removed from sys.path, so the guard runs.
    """
    import importlib.util
    import sys

    lib_dir = str(reconcile._LIB)
    saved = list(sys.path)
    sys.path = [p for p in sys.path if p != lib_dir]
    try:
        spec = importlib.util.spec_from_file_location(
            "_reconcile_guard_probe", reconcile.__file__
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # runs module top; guard inserts lib -> line 39
    finally:
        sys.path[:] = saved
    assert mod._scalar(1) == "1"



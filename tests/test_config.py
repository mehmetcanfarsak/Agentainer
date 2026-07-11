"""Tests for lib/config.py: loading, validation, and the dataclasses.

Ported from v1's test_config.py and adapted to the v2 mail-model schema
(`role` in place of `first_prompt`, virtual `user`/`system` mailboxes, per-agent
`mail_dir`, periodic pings, and type<->command mismatch detection). Targets 100%
line coverage of lib/config.py and lib/minyaml.py.
"""

import pytest

import config
from config import ConfigError, SwarmConfig, load
from tests.conftest import load_config


# ------------------------------------------------------------------ success

def test_minimal_valid_config(tmp_path):
    cfg = load_config(
        "swarm: {name: t, root: ./ws, session_prefix: 't-'}\n"
        "defaults: {type: claude}\n"
        "agents:\n  - {name: A, command: 'true'}\n",
        tmp_path,
    )
    assert cfg.name == "t"
    assert len(cfg.agents) == 1
    assert cfg.agents[0].name == "A"
    assert cfg.agents[0].capture == "hook"  # claude defaults to hook
    assert cfg.agents[0].type == "claude"


def test_defaults_apply_to_agents(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude, boot_delay_ms: 123}\n"
        "agents:\n"
        "  - {name: A, command: 'true', can_talk_to: [B]}\n"
        "  - {name: B, command: 'true'}\n",
        tmp_path,
    )
    a = cfg.get("A")
    assert a.boot_delay_ms == 123
    assert a.can_talk_to == ["B"]


def test_capture_auto_resolves_from_type(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: gemini}\n"  # gemini -> capture pane
        "agents:\n  - {name: A, command: 'true'}\n",
        tmp_path,
    )
    assert cfg.get("A").capture == "pane"


def test_explicit_capture_values(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "agents:\n"
        "  - {name: A, command: 'true', type: gemini, capture: none}\n"
        "  - {name: B, command: 'true', capture: pane}\n"
        "  - {name: C, command: 'true', capture: hook}\n",
        tmp_path,
    )
    assert [a.capture for a in cfg.agents] == ["none", "pane", "hook"]


def test_capture_none_on_hook_type_auto_upgrades(tmp_path):
    # claude/codex agents carry a completion hook; capture:none would blind the
    # orchestrator, so it is auto-upgraded to the type's natural capture (hook).
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "agents:\n"
        "  - {name: A, command: 'true', type: claude, capture: none}\n"
        "  - {name: C, command: 'true', type: codex, capture: none}\n",
        tmp_path,
    )
    assert cfg.get("A").capture == "hook"
    assert cfg.get("C").capture == "hook"
    assert any("auto-upgraded to capture: hook" in w for w in cfg.warnings)


def test_capture_none_on_pane_type_stays_none(tmp_path):
    # gemini's natural capture is pane, so capture:none is NOT upgraded.
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "agents:\n  - {name: A, command: 'true', type: gemini, capture: none}\n",
        tmp_path,
    )
    assert cfg.get("A").capture == "none"
    assert not cfg.warnings
    # capture:none forces busy_check off (no turn signal).
    assert cfg.get("A").busy_check is False


def test_custom_agent_type(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "agent_types:\n"
        "  bot: {command: 'echo hi', capture: pane, boot_delay_ms: 11}\n"
        "agents:\n  - {name: A, type: bot}\n",
        tmp_path,
    )
    a = cfg.get("A")
    assert a.type == "bot"
    assert a.command == "echo hi"
    assert a.boot_delay_ms == 11


def test_command_from_type_default(tmp_path):
    # No explicit command: falls back to the built-in claude command.
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "agents:\n  - {name: A, type: claude}\n",
        tmp_path,
    )
    assert cfg.get("A").command == "claude --dangerously-skip-permissions"


def test_role_and_env_merge(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude, env: {A: '1'}}\n"
        "agent_types:\n"
        "  claude: {env: {B: '2'}}\n"
        "agents:\n"
        "  - {name: X, command: 'true', role: 'hello', env: {C: '3'}}\n",
        tmp_path,
    )
    x = cfg.get("X")
    assert x.role == "hello"
    assert x.env == {"A": "1", "B": "2", "C": "3"}


def test_role_deprecated_first_prompt_alias(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude}\n"
        "agents:\n  - {name: X, command: 'true', first_prompt: 'legacy'}\n",
        tmp_path,
    )
    x = cfg.get("X")
    assert x.role == "legacy"
    assert any("`first_prompt` is deprecated" in w for w in cfg.warnings)


def test_role_explicit_beats_first_prompt(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude}\n"
        "agents:\n  - {name: X, command: 'true', role: 'new', first_prompt: 'old'}\n",
        tmp_path,
    )
    assert cfg.get("X").role == "new"


def test_role_file(tmp_path):
    prompt = tmp_path / "p.txt"
    prompt.write_text("file role body")
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude}\n"
        f"agents:\n  - {{name: X, command: 'true', first_prompt_file: '{prompt}'}}\n",
        tmp_path,
    )
    assert cfg.get("X").role == "file role body"
    assert any("`first_prompt_file` is deprecated" in w for w in cfg.warnings)


def test_role_file_relative(tmp_path):
    (tmp_path / "p.txt").write_text("relative role")
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude}\n"
        "agents:\n  - {name: X, command: 'true', first_prompt_file: p.txt}\n",
        tmp_path,
    )
    assert cfg.get("X").role == "relative role"


def test_role_file_missing(tmp_path):
    with pytest.raises(ConfigError):
        load_config(
            "swarm: {root: ./ws}\n"
            "defaults: {type: claude}\n"
            "agents:\n  - {name: A, command: 'true', first_prompt_file: /nope/x}\n",
            tmp_path,
        )


def test_role_file_and_role_both_set_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(
            "swarm: {root: ./ws}\n"
            "defaults: {type: claude}\n"
            "agents:\n"
            "  - {name: A, command: 'true', role: hi, first_prompt_file: x}\n",
            tmp_path,
        )


def test_workdir_placeholder_and_explicit(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws, name: t}\n"
        "defaults: {type: claude, workdir: '{root}/agents/{name}'}\n"
        "agents:\n  - {name: X, command: 'true'}\n",
        tmp_path,
    )
    assert cfg.get("X").workdir == (tmp_path / "ws" / "agents" / "X")


def test_workdir_default_is_under_root(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude}\n"
        "agents:\n  - {name: X, command: 'true'}\n",
        tmp_path,
    )
    assert cfg.get("X").workdir == (tmp_path / "ws" / "X")


def test_default_root_is_workspace(tmp_path):
    cfg = load_config(
        "defaults: {type: claude}\n"
        "agents:\n  - {name: X, command: 'true'}\n",
        tmp_path,
    )
    assert cfg.root == (tmp_path / "workspace")


def test_default_name_uses_stem(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude}\n"
        "agents:\n  - {name: X, command: 'true'}\n",
        tmp_path,
    )
    assert cfg.name == "agentainer"  # path stem is agentainer.yaml


def test_default_session_prefix_empty(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude}\n"
        "agents:\n  - {name: X, command: 'true'}\n",
        tmp_path,
    )
    assert cfg.session_prefix == ""
    assert cfg.get("X").session == "X"


def test_explicit_boot_delay(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude}\n"
        "agents:\n  - {name: X, command: 'true', boot_delay_ms: 999}\n",
        tmp_path,
    )
    assert cfg.get("X").boot_delay_ms == 999


def test_can_talk_to_wildcard_expands(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude}\n"
        "agents:\n"
        "  - {name: A, command: 'true', can_talk_to: '*'}\n"
        "  - {name: B, command: 'true'}\n"
        "  - {name: C, command: 'true'}\n",
        tmp_path,
    )
    a = cfg.get("A")
    assert set(a.can_talk_to) == {"B", "C"}


def test_can_talk_to_user_allowed(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude}\n"
        "agents:\n  - {name: A, command: 'true', can_talk_to: [user, B]}\n"
        "  - {name: B, command: 'true'}\n",
        tmp_path,
    )
    assert "user" in cfg.get("A").can_talk_to


def test_can_talk_to_system_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load_config(
            "swarm: {root: ./ws}\n"
            "defaults: {type: claude}\n"
            "agents:\n  - {name: A, command: 'true', can_talk_to: [system]}\n",
            tmp_path,
        )


def test_reserved_name_user_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load_config(
            "swarm: {root: ./ws}\n"
            "agents:\n  - {name: user, command: 'true'}\n",
            tmp_path,
        )


def test_reserved_name_system_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load_config(
            "swarm: {root: ./ws}\n"
            "agents:\n  - {name: system, command: 'true'}\n",
            tmp_path,
        )


def test_type_command_mismatch_raises(tmp_path):
    # type: claude but the command actually launches codex => deadlock.
    with pytest.raises(ConfigError):
        load_config(
            "swarm: {root: ./ws}\n"
            "agents:\n  - {name: A, type: claude, command: 'codex --yolo'}\n",
            tmp_path,
        )


def test_type_command_mismatch_gemini_in_claude(tmp_path):
    with pytest.raises(ConfigError):
        load_config(
            "swarm: {root: ./ws}\n"
            "agents:\n  - {name: A, type: claude, command: 'foo gemini bar'}\n",
            tmp_path,
        )


def test_keyfree_mock_command_passes(tmp_path):
    # A command with no CLI token is allowed (mock agents are keyless).
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "agents:\n"
        "  - {name: A, type: claude, command: \"bash -c 'while true; do read x; done'\"}\n",
        tmp_path,
    )
    assert cfg.get("A").command.startswith("bash")


def test_custom_type_command_with_token_passes(tmp_path):
    # Custom (non-token) type: a command mentioning a CLI token is not a mismatch.
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "agent_types:\n"
        "  bot: {capture: pane}\n"
        "agents:\n  - {name: A, type: bot, command: 'claude --foo'}\n",
        tmp_path,
    )
    assert cfg.get("A").type == "bot"


def test_periodically_ping_parsed(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude, periodically_ping_seconds: 600}\n"
        "agents:\n"
        "  - {name: A, command: 'true', periodically_ping_seconds: 1800, periodically_ping_message: 'ping!'}\n",
        tmp_path,
    )
    a = cfg.get("A")
    assert a.periodically_ping_seconds == 1800
    assert a.periodically_ping_message == "ping!"
    # defaults propagate too
    cfg2 = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude}\n"
        "agents:\n  - {name: A, command: 'true'}\n",
        tmp_path,
    )
    assert cfg2.get("A").periodically_ping_seconds == 0
    assert cfg2.get("A").periodically_ping_message == ""


def test_mail_dir_default_is_workdir(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude}\n"
        "agents:\n  - {name: X, command: 'true'}\n",
        tmp_path,
    )
    a = cfg.get("X")
    assert a.mail_dir == a.workdir
    paths = cfg.mail_paths(a)
    assert paths.inbox == a.workdir / "inbox"


def test_mail_dir_global_and_per_agent_override(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude, mail_dir: ./mail}\n"
        "agents:\n"
        "  - {name: A, command: 'true', mail_dir: ./mail/a}\n"
        "  - {name: B, command: 'true'}\n",
        tmp_path,
    )
    a = cfg.get("A")
    b = cfg.get("B")
    assert a.mail_dir == (tmp_path / "mail" / "a")
    # B inherits the global default mail_dir (resolved relative to the config file's parent).
    assert b.mail_dir == (tmp_path / "mail")
    assert b.mail_dir != b.workdir


def test_mail_paths_distinct_workspace(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude}\n"
        "agents:\n"
        "  - {name: A, command: 'true', workdir: ./wa}\n"
        "  - {name: B, command: 'true', workdir: ./wb}\n",
        tmp_path,
    )
    a = cfg.get("A")
    paths = cfg.mail_paths(a)
    assert paths.inbox == a.mail_dir / "inbox"
    assert paths.outbox == a.mail_dir / "outbox"
    assert paths.read == a.mail_dir / "read"
    assert paths.sent == a.mail_dir / "sent"
    assert paths.failed == a.mail_dir / "failed"
    assert "A-" not in paths.inbox.name


def test_mail_paths_shared_workspace_namespaced(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude}\n"
        "agents:\n"
        "  - {name: A, command: 'true', workdir: ./shared}\n"
        "  - {name: B, command: 'true', workdir: ./shared}\n",
        tmp_path,
    )
    a = cfg.get("A")
    paths = cfg.mail_paths(a)
    assert paths.inbox == a.mail_dir / "A-inbox"
    assert paths.outbox == a.mail_dir / "A-outbox"
    assert paths.read == a.mail_dir / "A-read"
    assert paths.sent == a.mail_dir / "A-sent"
    assert paths.failed == a.mail_dir / "A-failed"


def test_swarmconfig_properties(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude}\n"
        "agents:\n  - {name: A, command: 'true'}\n",
        tmp_path,
    )
    assert cfg.runtime == cfg.root / ".agentainer"
    assert cfg.log_dir == cfg.runtime / "logs"
    assert cfg.queue_dir == cfg.runtime / "queue"
    assert cfg.run_dir == cfg.runtime / "run"
    assert cfg.sessions_file == cfg.runtime / "sessions.yaml"
    assert cfg.get("A").name == "A"
    assert cfg.names() == ["A"]


def test_get_unknown_agent_raises(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude}\n"
        "agents:\n  - {name: A, command: 'true'}\n",
        tmp_path,
    )
    with pytest.raises(ConfigError):
        cfg.get("Z")


def test_user_available_default_and_override(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude}\n"
        "agents:\n  - {name: A, command: 'true'}\n",
        tmp_path,
    )
    assert cfg.user_available is False
    cfg2 = load_config(
        "swarm: {root: ./ws, user_available: true}\n"
        "defaults: {type: claude}\n"
        "agents:\n  - {name: A, command: 'true'}\n",
        tmp_path,
    )
    assert cfg2.user_available is True


def test_ready_probe_and_create_workdir_overrides(tmp_path):
    # workdir is resolved relative to the config file's parent dir, so create it there.
    shared = tmp_path / "shared"
    shared.mkdir(parents=True)
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude}\n"
        "agents:\n"
        "  - {name: A, command: 'true', workdir: ./shared, ready_probe: false, create_workdir: false}\n",
        tmp_path,
    )
    a = cfg.get("A")
    assert a.ready_probe is False
    # workdir exists, so create_workdir:false is honoured without raising.
    assert a.create_workdir is False


def test_shared_workdir_warns(tmp_path):
    shared = tmp_path / "ws" / "shared"
    shared.mkdir(parents=True)
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude}\n"
        "agents:\n"
        "  - {name: A, command: 'true', workdir: ./shared}\n"
        "  - {name: B, command: 'true', workdir: ./shared}\n",
        tmp_path,
    )
    assert any("share the working directory" in w for w in cfg.warnings)


def test_supervise_override(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws, supervise: false, supervise_interval_ms: 42}\n"
        "defaults: {type: claude}\n"
        "agents:\n  - {name: A, command: 'true'}\n",
        tmp_path,
    )
    assert cfg.supervise is False
    assert cfg.supervise_interval_ms == 42


# ------------------------------------------------------------------- errors

def test_missing_file_raises():
    with pytest.raises(ConfigError):
        load("/nonexistent/path/to/agentainer.yaml")


def test_parse_error_raises(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("agents: [unclosed\n")  # invalid YAML
    with pytest.raises(ConfigError):
        load(path)


def test_top_level_not_mapping(tmp_path):
    with pytest.raises(ConfigError):
        load_config("- just\n- a\n- list\n", tmp_path)


def test_swarm_not_mapping(tmp_path):
    with pytest.raises(ConfigError):
        load_config("swarm: notamap\nagents: []\n", tmp_path)


def test_defaults_not_mapping(tmp_path):
    with pytest.raises(ConfigError):
        load_config("defaults: 5\nagents: []\n", tmp_path)


def test_agent_types_not_mapping(tmp_path):
    with pytest.raises(ConfigError):
        load_config("agent_types: {t: 'notmap'}\nagents: []\n", tmp_path)


def test_no_agents_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config("swarm: {root: ./ws}\nagents: []\n", tmp_path)


def test_agents_not_list(tmp_path):
    with pytest.raises(ConfigError):
        load_config("swarm: {root: ./ws}\nagents: {name: A}\n", tmp_path)


def test_agent_not_mapping(tmp_path):
    with pytest.raises(ConfigError):
        load_config("swarm: {root: ./ws}\nagents:\n  - 'notamap'\n", tmp_path)


def test_agent_missing_name(tmp_path):
    with pytest.raises(ConfigError):
        load_config("swarm: {root: ./ws}\nagents:\n  - {type: claude}\n", tmp_path)


def test_agent_bad_name(tmp_path):
    with pytest.raises(ConfigError):
        load_config(
            "swarm: {root: ./ws}\nagents:\n  - {name: 'a b', command: 'true'}\n", tmp_path
        )


def test_agent_duplicate_name(tmp_path):
    with pytest.raises(ConfigError):
        load_config(
            "swarm: {root: ./ws}\n"
            "defaults: {type: claude}\n"
            "agents:\n  - {name: A, command: 'true'}\n  - {name: A, command: 'true'}\n",
            tmp_path,
        )


def test_agent_unknown_type(tmp_path):
    with pytest.raises(ConfigError):
        load_config(
            "swarm: {root: ./ws}\nagents:\n  - {name: A, type: nope, command: 'true'}\n",
            tmp_path,
        )


def test_agent_no_command(tmp_path):
    with pytest.raises(ConfigError):
        load_config(
            "swarm: {root: ./ws}\n"
            "agent_types:\n"
            "  x: {capture: pane}\n"
            "agents:\n  - {name: A, type: x}\n",
            tmp_path,
        )


def test_agent_bad_capture(tmp_path):
    with pytest.raises(ConfigError):
        load_config(
            "swarm: {root: ./ws}\nagents:\n  - {name: A, command: 'true', capture: weird}\n",
            tmp_path,
        )


def test_can_talk_to_unknown_peer(tmp_path):
    with pytest.raises(ConfigError):
        load_config(
            "swarm: {root: ./ws}\n"
            "defaults: {type: claude}\n"
            "agents:\n  - {name: A, command: 'true', can_talk_to: [ghost]}\n",
            tmp_path,
        )


def test_can_talk_to_self(tmp_path):
    with pytest.raises(ConfigError):
        load_config(
            "swarm: {root: ./ws}\n"
            "defaults: {type: claude}\n"
            "agents:\n  - {name: A, command: 'true', can_talk_to: [A]}\n",
            tmp_path,
        )


def test_workdir_placeholder_unknown(tmp_path):
    with pytest.raises(ConfigError):
        load_config(
            "swarm: {root: ./ws}\n"
            "defaults: {type: claude}\n"
            "agents:\n  - {name: A, command: 'true', workdir: '{nope}/x'}\n",
            tmp_path,
        )


def test_workdir_not_a_directory(tmp_path):
    f = tmp_path / "ws" / "afile"
    f.parent.mkdir(parents=True)
    f.write_text("x")
    with pytest.raises(ConfigError):
        load_config(
            f"swarm: {{root: {tmp_path!r}}}\n"
            "defaults: {type: claude}\n"
            f"agents:\n  - {{name: A, command: 'true', workdir: {str(f)!r}}}\n",
            tmp_path,
        )


def test_workdir_missing_and_not_created(tmp_path):
    with pytest.raises(ConfigError):
        load_config(
            "swarm: {root: ./ws, create_workdirs: false}\n"
            "defaults: {type: claude}\n"
            "agents:\n  - {name: A, command: 'true', workdir: ./missing}\n",
            tmp_path,
        )


# --------------------------------------------------------------- helpers

def test_as_list():
    assert config._as_list(None, "x") == []
    assert config._as_list("a", "x") == ["a"]
    assert config._as_list(["a", 1], "x") == ["a", "1"]
    with pytest.raises(ConfigError):
        config._as_list({"a": 1}, "x")
    with pytest.raises(ConfigError):
        config._as_list(5, "x")


def test_as_bool():
    assert config._as_bool(None, True, "x") is True
    assert config._as_bool(True, False, "x") is True
    assert config._as_bool(False, True, "x") is False
    with pytest.raises(ConfigError):
        config._as_bool("yes", True, "x")


def test_as_str_map():
    assert config._as_str_map(None, "x") == {}
    assert config._as_str_map({"a": 1}, "x") == {"a": "1"}
    with pytest.raises(ConfigError):
        config._as_str_map([1], "x")


def test_parse_yaml_uses_installed_parser():
    assert config.parse_yaml("a: 1\n") == {"a": 1}


def test_minyaml_subset_parser():
    # The bundled fallback must parse the same shape when PyYAML is unavailable.
    import minyaml

    text = (
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude}\n"
        "agents:\n  - {name: A, command: 'true'}\n"
    )
    assert minyaml.load(text) == config.parse_yaml(text)


def test_send_enter_delay_defaults_and_override(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude}\n"
        "agents:\n  - {name: A, command: 'true'}\n",
        tmp_path,
    )
    assert cfg.send_delay_ms == 150
    assert cfg.enter_delay_ms == 250
    cfg2 = load_config(
        "swarm: {root: ./ws, send_delay_ms: 50, enter_delay_ms: 70}\n"
        "defaults: {type: claude}\n"
        "agents:\n  - {name: A, command: 'true'}\n",
        tmp_path,
    )
    assert cfg2.send_delay_ms == 50
    assert cfg2.enter_delay_ms == 70


# ------------------------------------------------------------------ telegram

def test_telegram_defaults_when_absent(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "defaults: {type: claude}\n"
        "agents:\n  - {name: A, command: 'true'}\n",
        tmp_path,
    )
    assert cfg.telegram.enabled is False
    assert cfg.telegram.mirror == "*"
    assert cfg.telegram.mirror_user is True
    assert cfg.telegram.mirror_system is False


def test_telegram_block_parsed(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "telegram: {enabled: true, bot_token: '1:x', chat_id: '9', mirror: [A, B], mirror_system: true}\n"
        "defaults: {type: claude}\n"
        "agents:\n  - {name: A, command: 'true'}\n  - {name: B, command: 'true'}\n",
        tmp_path,
    )
    assert cfg.telegram.enabled is True
    assert cfg.telegram.bot_token == "1:x"
    assert cfg.telegram.chat_id == "9"
    assert cfg.telegram.mirror == ["A", "B"]
    assert cfg.telegram.mirror_system is True


def test_telegram_mirror_all_keyword(tmp_path):
    cfg = load_config(
        "swarm: {root: ./ws}\n"
        "telegram: {mirror: all}\n"
        "defaults: {type: claude}\n"
        "agents:\n  - {name: A, command: 'true'}\n",
        tmp_path,
    )
    assert cfg.telegram.mirror == "*"


def test_telegram_bad_mirror_type(tmp_path):
    with pytest.raises(ConfigError):
        load_config(
            "swarm: {root: ./ws}\n"
            "telegram: {mirror: 5}\n"
            "defaults: {type: claude}\n"
            "agents:\n  - {name: A, command: 'true'}\n",
            tmp_path,
        )


def test_telegram_not_a_mapping(tmp_path):
    with pytest.raises(ConfigError):
        load_config(
            "swarm: {root: ./ws}\n"
            "telegram: nope\n"
            "defaults: {type: claude}\n"
            "agents:\n  - {name: A, command: 'true'}\n",
            tmp_path,
        )

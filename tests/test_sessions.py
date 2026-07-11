"""Tests for lib/sessions.py -- the session / resume machinery (v2 branding).

Ports the v1 resume-related cases from AgentSwarm/tests/test_swarm_lifecycle.py
and adapts them. Targets 100% line coverage of lib/sessions.py using only
mock agents (no tmux, no API keys).
"""

import json

import pytest

import sessions
from config import Agent


def make_agent(tmp_path, **kw) -> Agent:
    """Build an Agent with the required (non-default) fields filled in."""
    base = dict(
        name="A",
        type="claude",
        command="claude",
        workdir=tmp_path,
        session="A",
        capture="pane",
        boot_delay_ms=0,
        role="",
        can_talk_to=[],
        mail_dir=tmp_path,
    )
    base.update(kw)
    return Agent(**base)


# --------------------------------------------------------------------- yaml_scalar


def test_yaml_scalar_covers_all_branches():
    assert sessions.yaml_scalar(None) == "null"
    assert sessions.yaml_scalar(True) == "true"
    assert sessions.yaml_scalar(False) == "false"
    assert sessions.yaml_scalar(3) == "3"
    assert sessions.yaml_scalar(2.5) == "2.5"
    assert sessions.yaml_scalar("hi") == '"hi"'
    # escaping is preserved
    assert sessions.yaml_scalar('a"b') == '"a\\"b"'


# --------------------------------------------------------------------- read_sessions


def test_read_sessions_missing_file_is_empty(tmp_runtime):
    assert sessions.read_sessions(tmp_runtime) == {}


def test_read_sessions_corrupt_file_is_empty(tmp_runtime, monkeypatch):
    def boom(text):
        raise ValueError("boom")

    monkeypatch.setattr(sessions, "parse_yaml", boom)
    tmp_runtime.sessions_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_runtime.sessions_file.write_text("not used")
    assert sessions.read_sessions(tmp_runtime) == {}


def test_read_sessions_not_a_mapping_is_empty(tmp_runtime):
    tmp_runtime.sessions_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_runtime.sessions_file.write_text("- a\n- b\n")
    assert sessions.read_sessions(tmp_runtime) == {}


def test_read_sessions_no_agents_key_is_empty(tmp_runtime, monkeypatch):
    monkeypatch.setattr(sessions, "parse_yaml", lambda text: {"swarm": "x"})
    assert sessions.read_sessions(tmp_runtime) == {}


def test_read_sessions_roundtrip(tmp_runtime):
    sessions.write_sessions(
        tmp_runtime,
        {"A": {"session_id": "sess-9", "type": "claude",
               "workdir": "/tmp/x", "updated_at": "2020"}},
    )
    assert sessions.read_sessions(tmp_runtime) == {
        "A": {"session_id": "sess-9", "type": "claude",
              "workdir": "/tmp/x", "updated_at": "2020"}
    }


# --------------------------------------------------------------------- write_sessions


def test_write_sessions_empty(tmp_runtime):
    sessions.write_sessions(tmp_runtime, {})
    assert tmp_runtime.sessions_file.exists()
    # empty agents block round-trips back to {}
    assert sessions.read_sessions(tmp_runtime) == {}


def test_write_sessions_nested_empty_dict(tmp_runtime):
    # exercises the `{}` empty-dict emitter branch in yaml_dump
    sessions.write_sessions(tmp_runtime, {"A": {}})
    text = tmp_runtime.sessions_file.read_text()
    assert "A:" in text


# --------------------------------------------------------------------- record_session


def test_record_session_no_id_is_noop(tmp_runtime, tmp_path):
    agent = make_agent(tmp_path)
    sessions.record_session(tmp_runtime, agent, "")
    assert sessions.read_sessions(tmp_runtime) == {}


def test_record_session_adds_entry(tmp_runtime, tmp_path):
    agent = make_agent(tmp_path, type="claude")
    sessions.record_session(tmp_runtime, agent, "sess-1", extra="x")
    got = sessions.read_sessions(tmp_runtime)["A"]
    assert got["session_id"] == "sess-1"
    assert got["type"] == "claude"
    assert got["workdir"] == str(tmp_path)
    assert got["extra"] == "x"
    assert "updated_at" in got


def test_record_session_unchanged_skips_rewrite(tmp_runtime, tmp_path):
    agent = make_agent(tmp_path)
    sessions.record_session(tmp_runtime, agent, "sess-1")
    before = tmp_runtime.sessions_file.stat().st_mtime
    sessions.record_session(tmp_runtime, agent, "sess-1")  # same id -> early return
    after = tmp_runtime.sessions_file.stat().st_mtime
    assert before == after  # file not rewritten
    assert sessions.read_sessions(tmp_runtime)["A"]["session_id"] == "sess-1"


def test_record_session_new_id_updates_entry(tmp_runtime, tmp_path):
    agent = make_agent(tmp_path)
    sessions.record_session(tmp_runtime, agent, "sess-1")
    sessions.record_session(tmp_runtime, agent, "sess-2")
    assert sessions.read_sessions(tmp_runtime)["A"]["session_id"] == "sess-2"


# --------------------------------------------------------------------- codex_session


def test_codex_session_no_codex_dir(tmp_path):
    agent = make_agent(tmp_path)
    assert sessions.codex_session(agent) == (None, None)


def test_codex_session_no_rollouts(tmp_path):
    agent = make_agent(tmp_path)
    (agent.workdir / ".codex" / "sessions").mkdir(parents=True)
    assert sessions.codex_session(agent) == (None, None)


def test_codex_session_session_meta(tmp_path):
    agent = make_agent(tmp_path)
    sessions_dir = agent.workdir / ".codex" / "sessions"
    sessions_dir.mkdir(parents=True)
    newest = sessions_dir / "rollout-0001.jsonl"
    newest.write_text(
        json.dumps({"type": "session_meta",
                    "payload": {"session_id": "abc", "id": "ignored"}})
    )
    assert sessions.codex_session(agent) == ("abc", str(newest))


def test_codex_session_meta_without_id(tmp_path):
    agent = make_agent(tmp_path)
    sessions_dir = agent.workdir / ".codex" / "sessions"
    sessions_dir.mkdir(parents=True)
    newest = sessions_dir / "rollout-0001.jsonl"
    newest.write_text(json.dumps({"type": "session_meta", "payload": {}}))
    assert sessions.codex_session(agent) == (None, str(newest))


def test_codex_session_not_session_meta(tmp_path):
    agent = make_agent(tmp_path)
    sessions_dir = agent.workdir / ".codex" / "sessions"
    sessions_dir.mkdir(parents=True)
    newest = sessions_dir / "rollout-0001.jsonl"
    newest.write_text(json.dumps({"type": "other"}))
    assert sessions.codex_session(agent) == (None, str(newest))


def test_codex_session_corrupt_rollout(tmp_path):
    agent = make_agent(tmp_path)
    sessions_dir = agent.workdir / ".codex" / "sessions"
    sessions_dir.mkdir(parents=True)
    newest = sessions_dir / "rollout-0001.jsonl"
    newest.write_text("this is not json{\n")
    assert sessions.codex_session(agent) == (None, str(newest))


def test_codex_session_newest_of_many(tmp_path):
    agent = make_agent(tmp_path)
    sessions_dir = agent.workdir / ".codex" / "sessions"
    sessions_dir.mkdir(parents=True)
    older = sessions_dir / "rollout-0001.jsonl"
    newer = sessions_dir / "rollout-0002.jsonl"
    older.write_text(json.dumps({"type": "session_meta", "payload": {"session_id": "old"}}))
    newer.write_text(json.dumps({"type": "session_meta", "payload": {"session_id": "new"}}))
    older.touch()
    newer.touch()
    # mtime decides; make newer actually newer
    import os
    import time

    os.utime(older, (time.time() - 10, time.time() - 10))
    os.utime(newer, (time.time(), time.time()))
    assert sessions.codex_session(agent) == ("new", str(newer))


# --------------------------------------------------------------------- session_env


def test_session_env_merges(tmp_runtime, tmp_path):
    agent = make_agent(tmp_path, env={"FOO": "bar"}, can_talk_to=["B"])
    env = sessions.session_env(tmp_runtime, agent, {"EXTRA": "1"})
    assert env["AGENTAINER_AGENT"] == "A"
    assert env["AGENTAINER_SESSION"] == agent.session
    assert env["AGENTAINER_PEERS"] == "B"
    assert env["AGENTAINER_ROOT"] == str(tmp_runtime.root)
    assert env["AGENTAINER_HOME"] == str(sessions.AGENTAINER_HOME)
    assert env["AGENTAINER_NAME"] == tmp_runtime.name
    assert env["AGENTAINER_CONFIG"] == str(tmp_runtime.path)
    assert env["FOO"] == "bar"
    assert env["EXTRA"] == "1"
    # old branding must be gone
    assert "SWARM_AGENT" not in env
    assert "SWARM_ROOT" not in env


# --------------------------------------------------------------------- resume_command


def test_resume_command_via_resume_args(tmp_runtime, tmp_path):
    agent = make_agent(tmp_path, type="claude", command="claude",
                       resume_args="--resume {session_id}")
    assert sessions.resume_command(tmp_runtime, agent, "sess-9") == "claude --resume sess-9"


def test_resume_command_codex(tmp_runtime, tmp_path):
    agent = make_agent(tmp_path, type="codex", command="codex",
                       resume_args="resume {session_id}")
    assert sessions.resume_command(tmp_runtime, agent, "sess-9") == "codex resume sess-9"


def test_resume_command_via_resume_command(tmp_runtime, tmp_path):
    agent = make_agent(tmp_path, type="claude", command="claude",
                       resume_command="echo {session_id}")
    assert sessions.resume_command(tmp_runtime, agent, "sess-9") == "echo sess-9"


def test_resume_command_none_for_gemini(tmp_runtime, tmp_path):
    agent = make_agent(tmp_path, type="gemini", command="gemini")
    assert sessions.resume_command(tmp_runtime, agent, "sess-9") is None


def test_resume_command_none_for_hermes(tmp_runtime, tmp_path):
    agent = make_agent(tmp_path, type="hermes", command="hermes")
    assert sessions.resume_command(tmp_runtime, agent, "sess-9") is None


def test_resume_command_malformed_is_none(tmp_runtime, tmp_path):
    agent = make_agent(tmp_path, type="claude", command="claude",
                       resume_command="echo {nope}")
    assert sessions.resume_command(tmp_runtime, agent, "sess-9") is None

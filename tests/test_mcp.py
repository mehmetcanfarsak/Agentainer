"""100% line-coverage tests for lib/mcp.py (the Model Context Protocol surface).

MCP is the fourth control plane (CLI / UI / Telegram / MCP): a coding agent
monitors and manages every swarm via JSON-RPC 2.0. These tests drive the core
``dispatch`` and every tool handler directly, plus the stdio loop -- all offline
under ``mock_tmux`` (the autouse ``_isolate_state_dir`` fixture keeps the
registry in a throwaway dir).
"""

import io
import json

import pytest

import mail
import mcp
import registry
from support import load_swarm, mock_tmux


AGENTS = """
- name: alice
  role: hi
  can_talk_to: [user, bob]
- name: bob
  role: yo
  can_talk_to: [user]
"""


@pytest.fixture
def cfg(tmp_path):
    return load_swarm(tmp_path, AGENTS, name="demo")


@pytest.fixture
def swarms(cfg):
    return {cfg.name: cfg}


def _call(name, args=None, swarms=None):
    """Invoke a tool through the full JSON-RPC path and return the parsed result."""
    resp = mcp.dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": name, "arguments": args or {}}},
        swarms=swarms,
    )
    return resp["result"]


def _payload(result):
    """The structured payload from a successful tool result."""
    assert result["isError"] is False
    return result["structuredContent"]


# --------------------------------------------------------------------------
# JSON-RPC dispatch
# --------------------------------------------------------------------------


def test_initialize_reports_protocol_and_server():
    r = mcp.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    res = r["result"]
    assert res["protocolVersion"] == mcp.PROTOCOL_VERSION
    assert res["serverInfo"]["name"] == "agentainer"
    assert res["serverInfo"]["version"]  # non-empty
    assert res["capabilities"]["tools"] == {"listChanged": False}
    assert "instructions" in res


def test_ping():
    r = mcp.dispatch({"jsonrpc": "2.0", "id": 9, "method": "ping"})
    assert r == {"jsonrpc": "2.0", "id": 9, "result": {}}


def test_tools_list_shape():
    r = mcp.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = r["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"list_swarms", "swarm_status", "send_message", "up_swarm", "create_swarm"} <= names
    for t in tools:
        assert t["inputSchema"]["type"] == "object"
        # the internal _required marker never leaks into the public schema
        assert "_required" not in json.dumps(t["inputSchema"])


def test_notification_gets_no_response():
    assert mcp.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_not_jsonrpc():
    r = mcp.dispatch({"foo": "bar"})
    assert r["error"]["code"] == mcp.INVALID_REQUEST


def test_not_a_dict():
    r = mcp.dispatch("nope")
    assert r["error"]["code"] == mcp.INVALID_REQUEST


def test_missing_method_with_id():
    r = mcp.dispatch({"jsonrpc": "2.0", "id": 5})
    assert r["error"]["code"] == mcp.INVALID_REQUEST


def test_missing_method_as_notification():
    assert mcp.dispatch({"jsonrpc": "2.0"}) is None


def test_unknown_method():
    r = mcp.dispatch({"jsonrpc": "2.0", "id": 3, "method": "bogus"})
    assert r["error"]["code"] == mcp.METHOD_NOT_FOUND


def test_tools_call_missing_name():
    r = mcp.dispatch({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {}})
    assert r["error"]["code"] == mcp.INVALID_PARAMS


def test_tools_call_unknown_tool(swarms):
    res = _call("does_not_exist", swarms=swarms)
    assert res["isError"] is True
    assert "unknown tool" in res["content"][0]["text"]


def test_tool_result_text_mirrors_structured(swarms):
    res = _call("list_swarms", swarms=swarms)
    assert json.loads(res["content"][0]["text"]) == res["structuredContent"]


# --------------------------------------------------------------------------
# swarm resolution
# --------------------------------------------------------------------------


def test_resolve_auto_when_single(cfg):
    assert mcp._resolve({cfg.name: cfg}, None) is cfg


def test_resolve_requires_name_when_many(cfg, tmp_path):
    sub = tmp_path / "b"
    sub.mkdir()
    other = load_swarm(sub, AGENTS, name="two")
    with pytest.raises(mcp.McpError, match="missing required argument 'swarm'"):
        mcp._resolve({cfg.name: cfg, "two": other}, None)


def test_resolve_unknown_name(cfg):
    with pytest.raises(mcp.McpError, match="unknown swarm"):
        mcp._resolve({cfg.name: cfg}, "ghost")


def test_resolve_empty_registry():
    with pytest.raises(mcp.McpError, match="missing required argument 'swarm'"):
        mcp._resolve({}, None)


def test_agent_unknown(cfg):
    with pytest.raises(mcp.McpError, match="unknown agent"):
        mcp._agent(cfg, "ghost")


# --------------------------------------------------------------------------
# swarm provider normalisation
# --------------------------------------------------------------------------


def test_swarms_provider_dict(swarms):
    assert mcp._swarms(swarms) == swarms


def test_swarms_provider_callable(swarms):
    assert mcp._swarms(lambda: swarms) == swarms


def test_swarms_provider_none_uses_registry(cfg):
    registry.register(cfg.name, cfg.path)
    got = mcp._swarms(None)
    assert cfg.name in got


# --------------------------------------------------------------------------
# monitor tools
# --------------------------------------------------------------------------


def test_list_swarms(swarms):
    with mock_tmux(has_session=False):
        out = _payload(_call("list_swarms", swarms=swarms))
    assert out["swarms"][0]["name"] == "demo"
    assert out["swarms"][0]["total"] == 2
    assert out["swarms"][0]["running"] == 0


def test_list_swarms_counts_running_and_attention(cfg, swarms):
    (cfg.queue_dir / "user").mkdir(parents=True, exist_ok=True)
    (cfg.queue_dir / "user" / "m1").write_text("hi")
    with mock_tmux(has_session=True):
        out = _payload(_call("list_swarms", swarms=swarms))
    row = out["swarms"][0]
    assert row["running"] == 2
    assert row["attention"] == 1


def test_swarm_status(cfg, swarms, monkeypatch):
    a = cfg.get("alice")
    (cfg.mail_paths(a).inbox).mkdir(parents=True, exist_ok=True)
    (cfg.mail_paths(a).inbox / "msg").write_text("x")
    # Force one agent to look busy to exercise the busy branch.
    monkeypatch.setattr(mcp.turn, "busy_info",
                        lambda c, ag: {"age_s": 3} if ag.name == "alice" else None)
    with mock_tmux(has_session=True):
        out = _payload(_call("swarm_status", {"swarm": "demo"}, swarms=swarms))
    assert out["name"] == "demo"
    by = {ag["name"]: ag for ag in out["agents"]}
    assert by["alice"]["busy"] is True
    assert by["alice"]["unread"] == 1
    assert by["bob"]["busy"] is False


def test_read_inbox_empty(swarms):
    out = _payload(_call("read_inbox", {"agent": "alice"}, swarms=swarms))
    assert out == {"agent": "alice", "inbox": []}


def test_read_inbox_with_messages(cfg, swarms):
    inbox = cfg.mail_paths(cfg.get("bob")).inbox
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "0001.md").write_text("hello bob")
    out = _payload(_call("read_inbox", {"agent": "bob"}, swarms=swarms))
    assert out["inbox"] == [{"file": "0001.md", "text": "hello bob"}]


def test_read_inbox_unknown_agent(swarms):
    res = _call("read_inbox", {"agent": "ghost"}, swarms=swarms)
    assert res["isError"] is True
    assert "unknown agent" in res["content"][0]["text"]


def test_read_queue(cfg, swarms):
    q = cfg.queue_dir / "alice"
    q.mkdir(parents=True, exist_ok=True)
    (q / "0001.md").write_text("queued")
    out = _payload(_call("read_queue", {"agent": "alice"}, swarms=swarms))
    assert out["queue"] and out["queue"][0]["text"] == "queued"


def test_read_user_inbox_empty(swarms):
    out = _payload(_call("read_user_inbox", {"swarm": "demo"}, swarms=swarms))
    assert out == {"swarm": "demo", "inbox": []}


def test_read_user_inbox_with_mail(cfg, swarms):
    udir = cfg.queue_dir / "user"
    udir.mkdir(parents=True, exist_ok=True)
    (udir / "m1").write_text("for you")
    out = _payload(_call("read_user_inbox", {"swarm": "demo"}, swarms=swarms))
    assert out["inbox"][0]["text"] == "for you"


def test_agent_logs_missing_file(swarms):
    out = _payload(_call("agent_logs", {"agent": "alice"}, swarms=swarms))
    assert out == {"agent": "alice", "logs": []}


def test_agent_logs_reads_records_and_bad_lines(cfg, swarms):
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    (cfg.log_dir / "alice.jsonl").write_text('{"a":1}\n\nnot json\n')
    out = _payload(_call("agent_logs", {"agent": "alice", "n": 10}, swarms=swarms))
    assert {"a": 1} in out["logs"]
    assert {"raw": "not json"} in out["logs"]


def test_agent_logs_swarm_wide_and_bad_n(cfg, swarms):
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    (cfg.log_dir / "agentainer.jsonl").write_text('{"e":"boot"}\n')
    out = _payload(_call("agent_logs", {"n": "oops"}, swarms=swarms))
    assert out["logs"] == [{"e": "boot"}]
    assert out["agent"] is None


def test_capture_pane(swarms):
    with mock_tmux(pane="PANE-SNAPSHOT"):
        out = _payload(_call("capture_pane", {"agent": "alice"}, swarms=swarms))
    assert out["pane"] == "PANE-SNAPSHOT"


def test_read_config(cfg, swarms):
    out = _payload(_call("read_config", {"swarm": "demo"}, swarms=swarms))
    assert out["name"] == "demo"
    names = [a["name"] for a in out["config"]["agents"]]
    assert names == ["alice", "bob"]


# --------------------------------------------------------------------------
# manage tools
# --------------------------------------------------------------------------


def test_send_message(cfg, swarms, monkeypatch):
    sent = {}
    monkeypatch.setattr(mail, "send_as_user",
                        lambda c, to, text: sent.update(to=to, text=text))
    out = _payload(_call("send_message", {"agent": "alice", "text": "go"}, swarms=swarms))
    assert out == {"ok": True, "swarm": "demo", "to": "alice"}
    assert sent == {"to": "alice", "text": "go"}


def test_send_message_missing_text(swarms):
    res = _call("send_message", {"agent": "alice", "text": ""}, swarms=swarms)
    assert res["isError"] is True
    assert "text" in res["content"][0]["text"]


def test_set_availability(cfg, swarms):
    out = _payload(_call("set_availability", {"available": False}, swarms=swarms))
    assert out["available"] is False


def test_set_availability_non_bool(swarms):
    res = _call("set_availability", {"available": "yes"}, swarms=swarms)
    assert res["isError"] is True
    assert "boolean" in res["content"][0]["text"]


def test_start_agent(cfg, swarms, monkeypatch):
    monkeypatch.setattr(mcp.reconcile, "start_one", lambda c, n: True)
    out = _payload(_call("start_agent", {"agent": "alice"}, swarms=swarms))
    assert out == {"ok": True, "swarm": "demo", "agent": "alice", "started": True}


def test_stop_agent(cfg, swarms, monkeypatch):
    monkeypatch.setattr(mcp.reconcile, "stop_one", lambda c, n: False)
    out = _payload(_call("stop_agent", {"agent": "bob"}, swarms=swarms))
    assert out == {"ok": True, "swarm": "demo", "agent": "bob", "stopped": False}


def test_up_swarm(cfg, swarms, monkeypatch):
    monkeypatch.setattr(mcp.reconcile, "start_all", lambda c: list(c.agents))
    out = _payload(_call("up_swarm", {"swarm": "demo"}, swarms=swarms))
    assert out["started"] == ["alice", "bob"]


def test_down_swarm(cfg, swarms, monkeypatch):
    monkeypatch.setattr(mcp.reconcile, "stop_all", lambda c: ["alice"])
    out = _payload(_call("down_swarm", {"swarm": "demo"}, swarms=swarms))
    assert out["stopped"] == ["alice"]


def test_create_swarm():
    out = _payload(_call("create_swarm", {"name": "fresh"}, swarms={}))
    assert out["ok"] and out["name"] == "fresh"
    assert registry.entry("fresh") is not None


def test_create_swarm_missing_name():
    res = _call("create_swarm", {}, swarms={})
    assert res["isError"] is True
    assert "name" in res["content"][0]["text"]


def test_create_swarm_duplicate(cfg):
    registry.register(cfg.name, cfg.path)
    res = _call("create_swarm", {"name": "demo"}, swarms={})
    assert res["isError"] is True
    assert "already registered" in res["content"][0]["text"]


def test_add_agent(cfg, swarms):
    out = _payload(_call(
        "add_agent",
        {"agent": "demo", "swarm": "demo", "name": "carol", "type": "claude",
         "command": "echo hi", "can_talk_to": "user, alice"},
        swarms=swarms,
    ))
    assert out["name"] == "carol"
    import config as cfgmod
    reloaded = cfgmod.load(cfg.path)
    carol = reloaded.get("carol")
    assert set(carol.can_talk_to) == {"user", "alice"}


def test_add_agent_missing_required(swarms):
    res = _call("add_agent", {"name": "x", "type": "claude"}, swarms=swarms)
    assert res["isError"] is True
    assert "command" in res["content"][0]["text"]


def test_add_agent_duplicate(swarms):
    res = _call("add_agent",
                {"name": "alice", "type": "claude", "command": "echo"}, swarms=swarms)
    assert res["isError"] is True
    assert "already exists" in res["content"][0]["text"]


def test_add_agent_acl_list(cfg, swarms):
    _payload(_call("add_agent",
                   {"name": "dave", "type": "claude", "command": "echo",
                    "can_talk_to": ["user"]}, swarms=swarms))
    import config as cfgmod
    assert "dave" in {a.name for a in cfgmod.load(cfg.path).agents}


def test_remove_agent(cfg, swarms):
    out = _payload(_call("remove_agent", {"name": "bob"}, swarms=swarms))
    assert out["removed"] == "bob"
    import config as cfgmod
    assert "bob" not in {a.name for a in cfgmod.load(cfg.path).agents}


def test_remove_agent_missing_name(swarms):
    res = _call("remove_agent", {}, swarms=swarms)
    assert res["isError"] is True
    assert "name" in res["content"][0]["text"]


def test_remove_agent_unknown(swarms):
    res = _call("remove_agent", {"name": "ghost"}, swarms=swarms)
    assert res["isError"] is True
    assert "not found" in res["content"][0]["text"]


def test_tool_unexpected_exception_becomes_error(swarms, monkeypatch):
    # A bug in the core surfaces as a readable isError result, never a crash.
    monkeypatch.setattr(mcp.tmux, "session_exists",
                        lambda s: (_ for _ in ()).throw(RuntimeError("boom")))
    res = _call("list_swarms", swarms=swarms)
    assert res["isError"] is True
    assert "RuntimeError: boom" in res["content"][0]["text"]


# --------------------------------------------------------------------------
# stdio transport
# --------------------------------------------------------------------------


def test_serve_stdio_roundtrip(swarms):
    lines = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
        "",  # blank line skipped
        "not json",  # parse error -> error reply, loop continues
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),  # no reply
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}),
    ]
    fin = io.StringIO("\n".join(lines) + "\n")
    fout = io.StringIO()
    rc = mcp.serve_stdio(swarms=swarms, stdin=fin, stdout=fout)
    assert rc == 0
    out = [json.loads(l) for l in fout.getvalue().splitlines()]
    assert out[0]["id"] == 1 and "tools" in out[0]["result"]
    assert out[1]["error"]["code"] == mcp.PARSE_ERROR
    assert out[2] == {"jsonrpc": "2.0", "id": 2, "result": {}}
    # notification produced no line -> exactly 3 responses
    assert len(out) == 3


def test_serve_stdio_defaults_to_registry(cfg, monkeypatch):
    registry.register(cfg.name, cfg.path)
    fin = io.StringIO(json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "list_swarms"}}) + "\n")
    fout = io.StringIO()
    with mock_tmux(has_session=False):
        mcp.serve_stdio(stdin=fin, stdout=fout)
    payload = json.loads(json.loads(fout.getvalue())["result"]["content"][0]["text"])
    assert any(s["name"] == "demo" for s in payload["swarms"])

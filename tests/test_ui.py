"""100% line-coverage tests for lib/ui.py (the HTTP control plane, P2).

All exercised against mock tmux / mock supervisor -- no real sessions, no API
keys. The server is spun up in a background thread on a free port; we hit it
with stdlib ``urllib.request`` and assert JSON shapes + status codes.
"""

import json
import importlib
import sys
import threading
import time
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

import ui
from support import load_swarm, mock_tmux


AGENTS = """
- name: alice
  role: hi
  can_talk_to: [bob]
- name: bob
  role: ho
  can_talk_to: [alice]
"""


@pytest.fixture
def cfg(tmp_path):
    return load_swarm(tmp_path, AGENTS, name="demo")


def _get(handle, path, token=None, headers=None):
    url = f"http://127.0.0.1:{handle.port}{path}"
    if token is not None:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}token={token}"
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:  # noqa: BLE001 - we test status codes
        return e.code, e.read().decode()


def _post(handle, path, token, body):
    url = f"http://127.0.0.1:{handle.port}{path}?token={token}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:  # noqa: BLE001
        return e.code, e.read().decode()


# --------------------------------------------------------------------------
# auth + static
# --------------------------------------------------------------------------


def test_static_index_and_appjs_no_token(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        code, body = _get(h, "/")
        assert code == 200
        assert "<html" in body
        code, body = _get(h, "/app.js")
        assert code == 200
        assert "use strict" in body
        # index.html reachable under its real name too
        code, _ = _get(h, "/index.html")
        assert code == 200


def test_static_missing_asset_returns_404(cfg, tmp_path):
    empty = tmp_path / "empty_ui"
    empty.mkdir()
    with mock_tmux(), ui.run_server(
        cfg, "sekret", host="127.0.0.1", port=0, ui_dir=str(empty)
    ) as h:
        code, body = _get(h, "/")
        assert code == 404
        assert body == "not found"


def test_api_requires_token(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        # no token -> 401
        code, body = _get(h, "/api/status")
        assert code == 401
        assert json.loads(body)["error"] == "unauthorized"
        # unknown path WITH token -> 404 (not 401)
        code, _ = _get(h, "/api/nope", token="sekret")
        assert code == 404
        # unknown path WITHOUT token -> 401
        code, _ = _get(h, "/api/nope")
        assert code == 401


def test_bearer_header_auth_works(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        code, body = _get(
            h, "/api/agents", headers={"Authorization": "Bearer sekret"}
        )
        assert code == 200
        assert "agents" in json.loads(body)


def test_wrong_token_rejected(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        code, _ = _get(h, "/api/status", token="wrong")
        assert code == 401


# --------------------------------------------------------------------------
# status / agents
# --------------------------------------------------------------------------


def test_api_status_and_agents(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        code, body = _get(h, "/api/status", token="sekret")
        assert code == 200
        data = json.loads(body)
        assert data["name"] == "demo"
        assert isinstance(data["root"], str)
        assert data["supervisor_alive"] is False  # no pid file yet
        names = {a["name"] for a in data["agents"]}
        assert names == {"alice", "bob"}
        alice = next(a for a in data["agents"] if a["name"] == "alice")
        assert alice["running"] is True  # mock_tmux reports a live session
        assert alice["busy"] is False
        assert alice["queue_depth"] == 0
        assert alice["unread"] == 0
        assert alice["can_talk_to"] == ["bob"]

        code, body = _get(h, "/api/agents", token="sekret")
        data = json.loads(body)
        assert {a["name"] for a in data["agents"]} == {"alice", "bob"}
        assert data["agents"][0]["type"] == "claude"


def test_api_status_supervisor_absent(cfg):
    # Hide the supervisor module -> degraded to None, no crash.
    with mock_tmux(), mock.patch.dict(sys.modules, {"supervisor": None}):
        with ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
            code, body = _get(h, "/api/status", token="sekret")
            assert code == 200
            assert json.loads(body)["supervisor_alive"] is None


def test_api_status_with_queue_and_unread(cfg):
    a = cfg.get("alice")
    qd = cfg.queue_dir / "alice"
    qd.mkdir(parents=True, exist_ok=True)
    (qd / "m-1.txt").write_text("hello queue")
    inbox = cfg.mail_paths(a).inbox
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "m-2.txt").write_text("hello inbox")
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        data = json.loads(_get(h, "/api/status", token="sekret")[1])
        alice = next(x for x in data["agents"] if x["name"] == "alice")
        assert alice["queue_depth"] == 1
        assert alice["unread"] == 1


# --------------------------------------------------------------------------
# logs
# --------------------------------------------------------------------------


def _write_log(cfg, agent, lines):
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    p = cfg.log_dir / (f"{agent}.jsonl" if agent else "agentainer.jsonl")
    p.write_text("\n".join(lines) + "\n")


def test_api_logs_with_agent(cfg):
    _write_log(cfg, "alice", [
        json.dumps({"ts": "t1", "kind": "delivered"}),
        json.dumps({"ts": "t2", "kind": "route"}),
    ])
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        data = json.loads(_get(h, "/api/logs?agent=alice&n=10", token="sekret")[1])
        assert data["agent"] == "alice"
        assert len(data["logs"]) == 2
        assert data["logs"][0]["kind"] == "delivered"


def test_api_logs_without_agent(cfg):
    _write_log(cfg, None, [json.dumps({"kind": "global"})])
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        data = json.loads(_get(h, "/api/logs", token="sekret")[1])
        assert data["agent"] is None
        assert data["logs"][0]["kind"] == "global"


def test_api_logs_missing_file(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        data = json.loads(_get(h, "/api/logs?agent=ghost", token="sekret")[1])
        assert data["logs"] == []


def test_api_logs_bad_json_and_blank_lines(cfg):
    _write_log(cfg, "alice", [
        json.dumps({"kind": "ok"}),
        "",  # blank -> skipped
        "this is not json",  # bad -> kept as raw
        json.dumps({"kind": "ok2"}),
    ])
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        data = json.loads(_get(h, "/api/logs?agent=alice", token="sekret")[1])
        kinds = [r.get("kind") for r in data["logs"]]
        assert "ok" in kinds and "ok2" in kinds
        assert {"raw": "this is not json"} in data["logs"]


def test_api_logs_bad_n_param(cfg):
    _write_log(cfg, "alice", [json.dumps({"kind": "x"})])
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        data = json.loads(_get(h, "/api/logs?agent=alice&n=abc", token="sekret")[1])
        assert len(data["logs"]) == 1


# --------------------------------------------------------------------------
# inbox / queue
# --------------------------------------------------------------------------


def test_api_inbox(cfg):
    a = cfg.get("alice")
    inbox = cfg.mail_paths(a).inbox
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "m-1.txt").write_text("From: user\nTo: alice\n\ndo the thing")
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        data = json.loads(_get(h, "/api/inbox?agent=alice", token="sekret")[1])
        assert data["agent"] == "alice"
        assert len(data["inbox"]) == 1
        assert "do the thing" in data["inbox"][0]["text"]


def test_api_inbox_missing_and_unknown(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        code, _ = _get(h, "/api/inbox", token="sekret")
        assert code == 400
        code, _ = _get(h, "/api/inbox?agent=ghost", token="sekret")
        assert code == 404


def test_api_queue(cfg):
    qd = cfg.queue_dir / "bob"
    qd.mkdir(parents=True, exist_ok=True)
    (qd / "m-9.txt").write_text("first line of queued message\nsecond line")
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        data = json.loads(_get(h, "/api/queue?agent=bob", token="sekret")[1])
        assert data["agent"] == "bob"
        assert data["queue"][0]["text"] == "first line of queued message"


def test_api_queue_missing_and_unknown(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        code, _ = _get(h, "/api/queue", token="sekret")
        assert code == 400
        code, _ = _get(h, "/api/queue?agent=ghost", token="sekret")
        assert code == 404


# --------------------------------------------------------------------------
# pane (terminal snapshot)
# --------------------------------------------------------------------------


def test_api_pane_success(cfg):
    with mock_tmux(pane="agent@box:~/work# ls\nREADME.md  src/\n"), ui.run_server(
        cfg, "sekret", host="127.0.0.1", port=0
    ) as h:
        data = json.loads(_get(h, "/api/pane?agent=alice", token="sekret")[1])
        assert data["agent"] == "alice"
        assert "README.md" in data["pane"]


def test_api_pane_empty_when_session_down(cfg):
    # capture_pane returns "" when the session is down / tmux errors -- the UI
    # renders an empty pane rather than failing the request.
    with mock_tmux(has_session=False, returncode=1), ui.run_server(
        cfg, "sekret", host="127.0.0.1", port=0
    ) as h:
        data = json.loads(_get(h, "/api/pane?agent=alice", token="sekret")[1])
        assert data["agent"] == "alice"
        assert data["pane"] == ""


def test_api_pane_missing_and_unknown(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        code, _ = _get(h, "/api/pane", token="sekret")
        assert code == 400
        code, _ = _get(h, "/api/pane?agent=ghost", token="sekret")
        assert code == 404


# --------------------------------------------------------------------------
# send
# --------------------------------------------------------------------------


def test_api_send_success(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        code, body = _post(h, "/api/send", "sekret", {"to": "alice", "text": "hi!"})
        assert code == 200
        assert json.loads(body)["ok"] is True
        # verify it actually landed in alice's queue (one-at-a-time: inbox)
        inbox = cfg.mail_paths(cfg.get("alice")).inbox
        assert inbox.exists() and list(inbox.iterdir())


def test_api_send_requires_token(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        # POST without token -> 401
        url = f"http://127.0.0.1:{h.port}/api/send"
        req = urllib.request.Request(
            url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            urllib.request.urlopen(req)
            assert False, "expected 401"
        except urllib.error.HTTPError as e:
            assert e.code == 401


def test_api_send_invalid_json(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        url = f"http://127.0.0.1:{h.port}/api/send?token=sekret"
        req = urllib.request.Request(
            url, data=b"not json", headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req)
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 400


def test_api_send_missing_fields(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        code, _ = _post(h, "/api/send", "sekret", {})  # no to/text
        assert code == 400
        code, _ = _post(h, "/api/send", "sekret", {"to": "alice", "text": ""})
        assert code == 400
        code, _ = _post(h, "/api/send", "sekret", {"to": "", "text": "x"})
        assert code == 400


def test_api_send_unknown_agent(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        code, body = _post(h, "/api/send", "sekret", {"to": "ghost", "text": "hi"})
        assert code == 400
        assert "ghost" in json.loads(body)["error"]


def test_api_send_post_unknown_path_401_then_404(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        # POST to a real-looking but non-send path WITH token -> 404
        code, _ = _post(h, "/api/status", "sekret", {})
        assert code == 404


# --------------------------------------------------------------------------
# bind / host / port handling
# --------------------------------------------------------------------------


def test_default_bind_is_loopback_and_free_port_reported(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        assert h.port > 0
        assert "127.0.0.1" in h.url


def test_non_loopback_requires_token():
    # No tmux / no running server needed for the guard; build a minimal cfg.
    cfg2 = load_swarm(_tmp(), AGENTS)
    with pytest.raises(ValueError):
        # bind to all interfaces with NO token -> refuse
        ui.run_server(cfg2, "", host="0.0.0.0", port=0)


def test_non_loopback_with_token_binds(cfg):
    with mock_tmux(), ui.run_server(
        cfg, "sekret", host="0.0.0.0", port=0
    ) as h:
        assert h.port > 0


def _tmp():
    import tempfile

    d = Path(tempfile.mkdtemp())
    return d


def test_foreground_serve_branch(cfg):
    # Cover the background=False path: run serve_forever in another thread, then
    # stop it via the module-global handle from this thread. The handler code
    # itself is already exercised by every other (background) test, so here we
    # only need to enter and cleanly exit serve_forever.
    with mock_tmux():
        t = threading.Thread(
            target=lambda: ui.run_server(
                cfg, "sekret", host="127.0.0.1", port=0, background=False
            ),
            daemon=True,
        )
        t.start()
        # wait until the global handle is published, then let serve_forever start
        for _ in range(100):
            if ui._last_server is not None:
                break
            time.sleep(0.01)
        assert ui._last_server is not None
        time.sleep(0.05)  # ensure serve_forever() is actually running
        port = ui._last_server.server_address[1]
        assert port > 0
        # stop the foreground server (unblocks serve_forever, runs `return handle`)
        ui._last_server.shutdown()
        t.join(timeout=5)


# --------------------------------------------------------------------------
# mail-app: agent detail / contacts / thread
# --------------------------------------------------------------------------


def _stamp(frm, to, mid, body, t="2026-01-01T00:00:00+00:00"):
    return f"From: {frm}\nTo: {to}\nId: {mid}\nTime: {t}\n\n{body}"


def _put(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_api_agent_detail(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        data = json.loads(_get(h, "/api/agent?agent=alice", token="sekret")[1])
        a = data["agent"]
        assert a["name"] == "alice"
        assert a["role"] == "hi"
        assert a["can_talk_to"] == ["bob"]
        assert "command" in a and "workdir" in a and "session" in a
        # missing / unknown
        assert _get(h, "/api/agent", token="sekret")[0] == 400
        assert _get(h, "/api/agent?agent=ghost", token="sekret")[0] == 404


def test_api_thread_between_agents(cfg):
    a = cfg.get("alice")
    b = cfg.get("bob")
    # alice->bob lands (stamped) in bob's inbox; bob->alice in alice's inbox.
    _put(cfg.mail_paths(b).inbox / "m1.txt", _stamp("alice", "bob", "m-1", "hello bob", "2026-01-01T00:00:01+00:00"))
    _put(cfg.mail_paths(a).inbox / "m2.txt", _stamp("bob", "alice", "m-2", "hi alice", "2026-01-01T00:00:02+00:00"))
    # a stray subdirectory in an incoming dir must be skipped, not parsed.
    (cfg.mail_paths(a).inbox / "subdir").mkdir()
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        data = json.loads(_get(h, "/api/thread?agent=alice&peer=bob", token="sekret")[1])
        msgs = data["messages"]
        assert [m["id"] for m in msgs] == ["m-1", "m-2"]
        assert msgs[0]["direction"] == "out"  # alice -> bob
        assert msgs[1]["direction"] == "in"   # bob -> alice
        assert msgs[0]["body"].strip() == "hello bob"
        # both sit in an inbox -> delivery status is "delivered"
        assert msgs[0]["status"] == "delivered" and msgs[1]["status"] == "delivered"


def test_api_thread_dedups_by_id(cfg):
    b = cfg.get("bob")
    msg = _stamp("alice", "bob", "m-dup", "only once")
    _put(cfg.mail_paths(b).inbox / "a.txt", msg)
    _put(cfg.queue_dir / "bob" / "a.txt", msg)  # same id in two dirs
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        data = json.loads(_get(h, "/api/thread?agent=alice&peer=bob", token="sekret")[1])
        assert len(data["messages"]) == 1


def test_api_thread_user_and_system(cfg):
    a = cfg.get("alice")
    # alice -> user accumulates in the user queue; user -> alice in alice's inbox.
    _put(cfg.queue_dir / "user" / "u1.txt", _stamp("alice", "user", "m-u1", "for the user"))
    _put(cfg.mail_paths(a).inbox / "u2.txt", _stamp("user", "alice", "m-u2", "from the user"))
    # a system message to alice
    _put(cfg.mail_paths(a).read / "s1.txt", _stamp("system", "alice", "m-s1", "system notice"))
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        u = json.loads(_get(h, "/api/thread?agent=alice&peer=user", token="sekret")[1])
        assert {m["id"] for m in u["messages"]} == {"m-u1", "m-u2"}
        s = json.loads(_get(h, "/api/thread?agent=alice&peer=system", token="sekret")[1])
        assert [m["id"] for m in s["messages"]] == ["m-s1"]


def test_api_thread_missing_and_unknown(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        assert _get(h, "/api/thread?agent=alice", token="sekret")[0] == 400
        assert _get(h, "/api/thread?agent=ghost&peer=bob", token="sekret")[0] == 404
        assert _get(h, "/api/thread?agent=alice&peer=ghost", token="sekret")[0] == 404


def test_api_contacts(cfg):
    a = cfg.get("alice")
    # a stamped incoming from an unknown sender exercises the unknown-name branch
    # in _incoming_dirs (cfg.get raises -> []), and adds it as a contact.
    _put(cfg.mail_paths(a).inbox / "g.txt", _stamp("ghost", "alice", "m-g", "boo"))
    # an unread message from bob bumps bob's unread count
    _put(cfg.queue_dir / "alice" / "b.txt", _stamp("bob", "alice", "m-b", "queued hi"))
    # a stray subdirectory in an incoming dir must be skipped when discovering.
    (cfg.mail_paths(a).inbox / "subdir").mkdir()
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        data = json.loads(_get(h, "/api/contacts?agent=alice", token="sekret")[1])
        by = {c["name"]: c for c in data["contacts"]}
        assert by["bob"]["kind"] == "agent"
        assert by["bob"]["unread"] == 1
        assert by["bob"]["last_preview"] == "queued hi"
        assert "system" in by            # always reachable
        assert by["ghost"]["count"] == 1  # discovered from mail
        # an agent with NO mailbox dirs at all: contacts still lists its ACL +
        # system, and unread counting tolerates the missing dirs.
        bobc = json.loads(_get(h, "/api/contacts?agent=bob", token="sekret")[1])
        names = {c["name"] for c in bobc["contacts"]}
        assert "alice" in names and "system" in names
        assert all(c["unread"] == 0 for c in bobc["contacts"])
        # missing / unknown
        assert _get(h, "/api/contacts", token="sekret")[0] == 400
        assert _get(h, "/api/contacts?agent=ghost", token="sekret")[0] == 404


# --------------------------------------------------------------------------
# config / availability (GET)
# --------------------------------------------------------------------------


def test_api_config_get(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        data = json.loads(_get(h, "/api/config", token="sekret")[1])
        assert isinstance(data["swarm"], dict)
        assert {a["name"] for a in data["agents"]} == {"alice", "bob"}
        assert data["user_available"] is False
        assert "warnings" in data


def test_api_availability_get(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        data = json.loads(_get(h, "/api/availability", token="sekret")[1])
        assert data["available"] is False


# --------------------------------------------------------------------------
# type (direct pane input)
# --------------------------------------------------------------------------


def test_api_type_success(cfg):
    with mock_tmux(), mock.patch.object(ui.tmux, "paste_into", return_value=True):
        with ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
            code, body = _post(h, "/api/type", "sekret", {"agent": "alice", "text": "ls\n"})
            assert code == 200
            assert json.loads(body)["ok"] is True


def test_api_type_swarmerror(cfg):
    def boom(*a, **k):
        raise ui.tmux.SwarmError("session down")
    with mock_tmux(), mock.patch.object(ui.tmux, "paste_into", boom):
        with ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
            code, body = _post(h, "/api/type", "sekret", {"agent": "alice", "text": "x"})
            assert code == 400
            assert "session down" in json.loads(body)["error"]


def test_api_type_missing_and_unknown_and_badjson(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        assert _post(h, "/api/type", "sekret", {"text": "x"})[0] == 400   # no agent
        assert _post(h, "/api/type", "sekret", {"agent": "alice"})[0] == 400  # no text
        assert _post(h, "/api/type", "sekret", {"agent": "ghost", "text": "x"})[0] == 404
        # invalid json -> 400 (covers _json_body error branch)
        url = f"http://127.0.0.1:{h.port}/api/type?token=sekret"
        req = urllib.request.Request(url, data=b"nope", method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req)
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 400


# --------------------------------------------------------------------------
# config / availability (POST -> persist to YAML)
# --------------------------------------------------------------------------


def test_api_config_post_persists(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        code, body = _post(h, "/api/config", "sekret", {"swarm": {"busy_timeout_ms": 12345, "resume": True}})
        assert code == 200
        assert json.loads(body)["swarm"]["busy_timeout_ms"] == 12345
        # the YAML on disk was rewritten and reloads with the new value
        import config as cfgmod
        reloaded = cfgmod.load(cfg.path)
        assert reloaded.busy_timeout_ms == 12345
        assert reloaded.resume is True


def test_api_config_post_missing_and_invalid(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        assert _post(h, "/api/config", "sekret", {})[0] == 400          # no swarm
        assert _post(h, "/api/config", "sekret", {"swarm": {}})[0] == 400  # empty
        # a setting that fails validation on reload -> 400
        code, body = _post(h, "/api/config", "sekret", {"swarm": {"resume": "maybe"}})
        assert code == 400
        assert "error" in json.loads(body)


def test_api_availability_post(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        code, body = _post(h, "/api/availability", "sekret", {"available": True})
        assert code == 200 and json.loads(body)["available"] is True
        assert json.loads(_get(h, "/api/availability", token="sekret")[1])["available"] is True
        # persisted to YAML
        import config as cfgmod
        assert cfgmod.load(cfg.path).user_available is True
        # back to away
        assert _post(h, "/api/availability", "sekret", {"available": False})[0] == 200
        # bad payloads
        assert _post(h, "/api/availability", "sekret", {})[0] == 400
        assert _post(h, "/api/availability", "sekret", {"available": "yes"})[0] == 400


# --------------------------------------------------------------------------
# agent add / edit / remove (persist to YAML)
# --------------------------------------------------------------------------


def test_api_agent_add(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        code, body = _post(h, "/api/agent/add", "sekret", {
            "name": "carol", "type": "claude",
            "command": "claude --dangerously-skip-permissions",
            "can_talk_to": ["alice"], "role": "reviewer",
            "capture": "hook", "periodically_ping_seconds": 30,
        })
        assert code == 200 and json.loads(body)["name"] == "carol"
        import config as cfgmod
        assert "carol" in cfgmod.load(cfg.path).names()


def test_api_agent_add_bad(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        assert _post(h, "/api/agent/add", "sekret", {"name": "x"})[0] == 400  # no command
        assert _post(h, "/api/agent/add", "sekret", {"command": "claude"})[0] == 400  # no name
        # duplicate name -> add_agent raises -> 400
        code, body = _post(h, "/api/agent/add", "sekret",
                           {"name": "alice", "command": "claude"})
        assert code == 400
        assert "alice" in json.loads(body)["error"]


def test_api_agent_edit(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        code, _ = _post(h, "/api/agent/edit", "sekret",
                        {"name": "alice", "fields": {"role": "new role", "can_talk_to": ["bob"]}})
        assert code == 200
        import config as cfgmod
        assert cfgmod.load(cfg.path).get("alice").role == "new role"
        # bad: no fields
        assert _post(h, "/api/agent/edit", "sekret", {"name": "alice"})[0] == 400
        assert _post(h, "/api/agent/edit", "sekret", {"fields": {"role": "x"}})[0] == 400
        # edit that dangles an ACL reference -> reload fails -> 400
        code, body = _post(h, "/api/agent/edit", "sekret",
                           {"name": "alice", "fields": {"can_talk_to": ["ghost"]}})
        assert code == 400


def test_api_agent_remove(cfg, tmp_path):
    # A config with a standalone agent nothing references, so removal stays valid.
    sub = tmp_path / "trio"
    sub.mkdir()
    trio = load_swarm(sub, """
- name: alice
  role: hi
  can_talk_to: []
- name: solo
  role: alone
  can_talk_to: []
""", name="trio")
    with mock_tmux(), ui.run_server(trio, "sekret", host="127.0.0.1", port=0) as h:
        code, body = _post(h, "/api/agent/remove", "sekret", {"name": "solo"})
        assert code == 200 and json.loads(body)["name"] == "solo"
        import config as cfgmod
        assert "solo" not in cfgmod.load(trio.path).names()
        # unknown / missing
        assert _post(h, "/api/agent/remove", "sekret", {"name": "ghost"})[0] == 404
        assert _post(h, "/api/agent/remove", "sekret", {})[0] == 400


def test_api_agent_remove_dangling(cfg):
    # alice <-> bob reference each other; removing bob dangles alice's ACL -> 400.
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        code, body = _post(h, "/api/agent/remove", "sekret", {"name": "bob"})
        assert code == 400
        assert "error" in json.loads(body)


def _post_raw(h, path, token, raw_bytes):
    url = f"http://127.0.0.1:{h.port}{path}?token={token}"
    req = urllib.request.Request(url, data=raw_bytes, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def test_mutation_endpoints_reject_invalid_json(cfg):
    # Every mutation handler shares _json_body: bad JSON -> 400 (covers the
    # `if data is None: return` guard in each).
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        for path in ("/api/config", "/api/availability", "/api/agent/add",
                     "/api/agent/edit", "/api/agent/remove"):
            assert _post_raw(h, path, "sekret", b"not json") == 400


def test_post_unknown_path_404(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        assert _post(h, "/api/nope", "sekret", {})[0] == 404


# --------------------------------------------------------------------------
# telegram bridge endpoints
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_poller():
    """Keep the module-global poller from leaking between tests."""
    ui._tg_poller = None
    yield
    if ui._tg_poller is not None:
        try:
            ui._tg_poller.stop()
        except Exception:
            pass
        ui._tg_poller = None


class DummyPoller:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


def test_api_telegram_get_default_off(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        data = json.loads(_get(h, "/api/telegram", token="sekret")[1])
        assert data["enabled"] is False
        assert data["has_token"] is False
        assert data["polling"] is False
        assert set(data["agents"]) == {"alice", "bob"}


def test_api_telegram_post_enables_and_persists(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        code, body = _post(h, "/api/telegram", "sekret", {
            "enabled": True, "bot_token": "123:ABC", "chat_id": "999",
            "mirror": ["alice"], "mirror_user": True, "mirror_system": False,
        })
        assert code == 200
        assert json.loads(body)["enabled"] is True and json.loads(body)["has_token"] is True
        # persisted to YAML + reloadable
        import config as cfgmod
        rel = cfgmod.load(cfg.path)
        assert rel.telegram.enabled is True and rel.telegram.chat_id == "999"
        assert rel.telegram.mirror == ["alice"]
        # GET now reports enabled but never leaks the token
        got = json.loads(_get(h, "/api/telegram", token="sekret")[1])
        assert got["enabled"] is True and got["has_token"] is True
        assert "bot_token" not in got


def test_api_telegram_post_missing_and_invalid(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        assert _post(h, "/api/telegram", "sekret", {})[0] == 400  # nothing to set
        # an invalid mirror type fails config reload -> 400
        code, _ = _post(h, "/api/telegram", "sekret", {"mirror": 5})
        assert code == 400


def test_api_telegram_test_paths(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        # not configured -> 400
        assert _post(h, "/api/telegram/test", "sekret", {})[0] == 400
        # enable it
        _post(h, "/api/telegram", "sekret", {"enabled": True, "bot_token": "1:x", "chat_id": "9"})
        with mock.patch.object(ui.telegram, "send_message", return_value={"message_id": 1}):
            assert _post(h, "/api/telegram/test", "sekret", {"text": "hi"})[0] == 200
        # a TelegramError surfaces as 400
        def boom(*a, **k):
            raise ui.telegram.TelegramError("bad token")
        with mock.patch.object(ui.telegram, "send_message", boom):
            code, body = _post(h, "/api/telegram/test", "sekret", {})
            assert code == 400 and "bad token" in json.loads(body)["error"]


def test_api_telegram_poll_start_stop_and_restart(cfg):
    with mock_tmux(), mock.patch.object(ui.telegram, "start_poller", lambda c: DummyPoller()):
        with ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
            # run before configured -> 400
            assert _post(h, "/api/telegram/poll", "sekret", {"run": True})[0] == 400
            _post(h, "/api/telegram", "sekret", {"enabled": True, "bot_token": "1:x", "chat_id": "9"})
            # start polling
            code, body = _post(h, "/api/telegram/poll", "sekret", {"run": True})
            assert code == 200 and json.loads(body)["polling"] is True
            # a config POST while polling restarts the poller (covers restart branch)
            assert _post(h, "/api/telegram", "sekret", {"mirror_system": True})[0] == 200
            assert ui._tg_poller is not None
            # stop polling
            code, body = _post(h, "/api/telegram/poll", "sekret", {"run": False})
            assert code == 200 and json.loads(body)["polling"] is False


def test_shutdown_stops_running_poller(cfg):
    with mock_tmux(), mock.patch.object(ui.telegram, "start_poller", lambda c: DummyPoller()):
        h = ui.run_server(cfg, "sekret", host="127.0.0.1", port=0)
        _post(h, "/api/telegram", "sekret", {"enabled": True, "bot_token": "1:x", "chat_id": "9"})
        _post(h, "/api/telegram/poll", "sekret", {"run": True})
        poller = ui._tg_poller
        assert poller is not None
        h.shutdown()  # must stop the poller
        assert poller.stopped is True
        assert ui._tg_poller is None


def test_telegram_endpoints_reject_invalid_json(cfg):
    with mock_tmux(), ui.run_server(cfg, "sekret", host="127.0.0.1", port=0) as h:
        for path in ("/api/telegram", "/api/telegram/test", "/api/telegram/poll"):
            assert _post_raw(h, path, "sekret", b"not json") == 400


def test_server_handle_shutdown_and_url(cfg):
    with mock_tmux():
        h = ui.run_server(cfg, "sekret", host="127.0.0.1", port=0)
        assert h.url.startswith("http://")
        h.shutdown()


def test_ui_inserts_lib_path_when_missing():
    # Cover the sys.path.insert guard (line 54): load ui.py fresh with lib/
    # removed from sys.path so the guard actually runs.
    import importlib.util

    saved = sys.path[:]
    original = sys.modules.get("ui")
    sys.path = [p for p in sys.path if p != str(ui._LIB)]
    sys.modules.pop("ui", None)
    try:
        spec = importlib.util.spec_from_file_location(
            "ui", str(ui._LIB / "ui.py")
        )
        fresh = importlib.util.module_from_spec(spec)
        sys.modules["ui"] = fresh
        spec.loader.exec_module(fresh)
        assert str(ui._LIB) in sys.path
    finally:
        sys.path[:] = saved
        if original is not None:
            sys.modules["ui"] = original
        else:
            sys.modules.pop("ui", None)

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

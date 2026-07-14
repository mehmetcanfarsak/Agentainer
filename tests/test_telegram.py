"""100% line-coverage tests for lib/telegram.py (the optional Telegram bridge).

Every test mocks the single ``telegram._urlopen`` network seam -- no sockets, no
bot token, nothing to pay for. Covers: config gating, mirror-scope decisions, the
mirror-on-enqueue path (incl. reply-map capture), the getUpdates poller and reply
routing, and the background Poller loop (driven synchronously).
"""

import json
import sys
from urllib.parse import unquote_plus as urllib_unquote

import pytest

import telegram
import mail
from support import load_swarm


AGENTS = """
- name: alice
  role: hi
  can_talk_to: [bob, user]
- name: bob
  role: ho
  can_talk_to: [alice]
"""

TG = """
telegram:
  enabled: true
  bot_token: "123:ABC"
  chat_id: "999"
  mirror: "*"
"""


@pytest.fixture
def cfg(tmp_path):
    # Append a telegram block to the standard mock swarm config.
    c = load_swarm(tmp_path, AGENTS)
    c.path.write_text(c.path.read_text() + TG)
    import config as cfgmod
    c = cfgmod.load(c.path)
    for d in (c.runtime, c.log_dir, c.queue_dir, c.run_dir):
        d.mkdir(parents=True, exist_ok=True)
    return c


class FakeNet:
    """Records outbound calls and returns scripted responses per API method."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def __call__(self, url, data, timeout, headers=None):
        method = url.rsplit("/", 1)[-1]
        self.calls.append((method, data, timeout))
        r = self.responses.get(method, {"ok": True, "result": True})
        if callable(r):
            r = r(len(self.calls))
        if isinstance(r, Exception):
            raise r
        return json.dumps(r).encode()

    def methods(self):
        return [m for m, _, _ in self.calls]


@pytest.fixture
def net(monkeypatch):
    fake = FakeNet()
    monkeypatch.setattr(telegram, "_urlopen", fake)
    return fake


# --------------------------------------------------------------------------
# config gating + scope
# --------------------------------------------------------------------------


def test_is_enabled(cfg, tmp_path):
    assert telegram.is_enabled(cfg) is True
    cfg.telegram.bot_token = ""
    assert telegram.is_enabled(cfg) is False
    cfg.telegram.bot_token = "x"
    cfg.telegram.enabled = False
    assert telegram.is_enabled(cfg) is False
    # an object with no telegram attribute degrades to False
    assert telegram.is_enabled(object()) is False


def test_should_mirror_scopes(cfg):
    tg = cfg.telegram
    tg.mirror = "*"
    assert telegram._should_mirror(cfg, "alice", "bob") is True
    # user-addressed follows mirror_user
    tg.mirror_user = False
    assert telegram._should_mirror(cfg, "alice", "user") is False
    tg.mirror_user = True
    assert telegram._should_mirror(cfg, "alice", "user") is True
    # system follows mirror_system
    assert telegram._should_mirror(cfg, "system", "alice") is False
    tg.mirror_system = True
    assert telegram._should_mirror(cfg, "system", "alice") is True
    # selected-agents list
    tg.mirror = ["alice"]
    assert telegram._should_mirror(cfg, "alice", "bob") is True   # frm in set
    assert telegram._should_mirror(cfg, "carol", "alice") is True  # to in set
    assert telegram._should_mirror(cfg, "carol", "dave") is False
    # wildcard inside a list
    tg.mirror = ["*"]
    assert telegram._mirror_all(cfg) is True
    # _mirror_set on the "*" string form yields an empty set (all handled by
    # _mirror_all); exercised directly since _should_mirror short-circuits first.
    tg.mirror = "*"
    assert telegram._mirror_set(cfg) == set()


def test_inserts_lib_path_when_missing():
    # Cover the sys.path.insert guard: reload telegram with lib/ off sys.path.
    import importlib.util

    saved = sys.path[:]
    original = sys.modules.get("telegram")
    sys.path = [p for p in sys.path if p != str(telegram._LIB)]
    sys.modules.pop("telegram", None)
    try:
        spec = importlib.util.spec_from_file_location("telegram", str(telegram._LIB / "telegram.py"))
        fresh = importlib.util.module_from_spec(spec)
        sys.modules["telegram"] = fresh
        spec.loader.exec_module(fresh)
        assert str(telegram._LIB) in sys.path
    finally:
        sys.path[:] = saved
        sys.modules["telegram"] = original if original is not None else fresh


def test_format_mail_variants():
    assert telegram.format_mail("user", "alice", "hi").startswith("🧑")
    assert telegram.format_mail("system", "alice", "hi").startswith("⚙️")
    a = telegram.format_mail("alice", "bob", "hi")
    assert a.startswith("✉️") and "alice → bob" in a
    # user card gets the reply hint; long bodies are trimmed
    u = telegram.format_mail("alice", "user", "x" * (telegram.MAX_BODY + 50))
    assert "reply to this message" in u and u.endswith("…")


# --------------------------------------------------------------------------
# api_call
# --------------------------------------------------------------------------


def test_api_call_no_token(cfg):
    cfg.telegram.bot_token = ""
    with pytest.raises(telegram.TelegramError):
        telegram.api_call(cfg, "getMe", {})


def test_api_call_transport_error(cfg, monkeypatch):
    monkeypatch.setattr(telegram, "_urlopen", lambda *a: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(telegram.TelegramError):
        telegram.api_call(cfg, "getMe", {})


def test_api_call_bad_json(cfg, monkeypatch):
    monkeypatch.setattr(telegram, "_urlopen", lambda *a: b"not json")
    with pytest.raises(telegram.TelegramError):
        telegram.api_call(cfg, "getMe", {})


def test_api_call_not_ok(cfg, monkeypatch):
    monkeypatch.setattr(telegram, "_urlopen", lambda *a: json.dumps({"ok": False, "description": "nope"}).encode())
    with pytest.raises(telegram.TelegramError):
        telegram.api_call(cfg, "getMe", {})


def test_send_message_with_and_without_reply(cfg, net):
    telegram.send_message(cfg, "hi")
    telegram.send_message(cfg, "hi", reply_to=5)
    assert net.methods() == ["sendMessage", "sendMessage"]


# --------------------------------------------------------------------------
# mirror on enqueue
# --------------------------------------------------------------------------


def test_on_enqueued_disabled_is_noop(cfg, net):
    cfg.telegram.enabled = False
    telegram.on_enqueued(cfg, "bob", mail.stamp_message("hi", "alice", "bob", "m-1"), "m-1")
    assert net.calls == []


def test_on_enqueued_agent_to_agent(cfg, net):
    telegram.on_enqueued(cfg, "bob", mail.stamp_message("hi bob", "alice", "bob", "m-1"), "m-1")
    assert net.methods() == ["sendMessage"]
    # no reply-map for non-user recipients
    assert not (cfg.run_dir / "telegram.replymap.json").exists()


def test_on_enqueued_to_user_records_replymap(cfg, monkeypatch):
    monkeypatch.setattr(telegram, "_urlopen",
                        lambda *a: json.dumps({"ok": True, "result": {"message_id": 42}}).encode())
    telegram.on_enqueued(cfg, "user", mail.stamp_message("review?", "alice", "user", "m-9"), "m-9")
    rm = json.loads((cfg.run_dir / "telegram.replymap.json").read_text())
    assert rm["42"]["agent"] == "alice"


def test_on_enqueued_long_body_sends_attachment(cfg, net):
    long_body = "y" * (telegram.MAX_BODY + 500)
    telegram.on_enqueued(cfg, "bob", mail.stamp_message(long_body, "alice", "bob", "m-7"), "m-7")
    # inline card first, then the full body as a document attachment
    assert net.methods() == ["sendMessage", "sendDocument"]
    # the document upload carries the WHOLE body (not the trimmed card)
    doc_call = next(c for c in net.calls if c[0] == "sendDocument")
    assert long_body.encode("utf-8") in doc_call[1]


def test_on_enqueued_short_body_no_attachment(cfg, net):
    telegram.on_enqueued(cfg, "bob", mail.stamp_message("short", "alice", "bob", "m-8"), "m-8")
    assert net.methods() == ["sendMessage"]  # no overflow -> no attachment


def test_on_enqueued_attachment_failure_is_swallowed(cfg, monkeypatch):
    # sendMessage ok, sendDocument fails -> card still mirrored, error logged
    def net(url, data, timeout, headers=None):
        if url.rsplit("/", 1)[-1] == "sendDocument":
            raise OSError("upload down")
        return json.dumps({"ok": True, "result": {"message_id": 1}}).encode()
    monkeypatch.setattr(telegram, "_urlopen", net)
    telegram.on_enqueued(cfg, "bob", mail.stamp_message("z" * (telegram.MAX_BODY + 10),
                                                        "alice", "bob", "m-10"), "m-10")
    log = (cfg.log_dir / "agentainer.jsonl").read_text()
    assert "telegram-error" in log and "telegram-mirror" in log


def test_send_document_no_token(cfg):
    cfg.telegram.bot_token = ""
    with pytest.raises(telegram.TelegramError):
        telegram.send_document(cfg, "f.txt", b"data")


def test_send_document_with_caption_and_reply(cfg, net):
    telegram.send_document(cfg, "f.txt", b"hello", caption="c" * 2000, reply_to=5)
    method, data, _ = net.calls[0]
    assert method == "sendDocument"
    assert b'name="reply_to_message_id"' in data and b"5" in data
    assert b'name="caption"' in data  # caption included (and internally capped)


def test_send_document_bad_json(cfg, monkeypatch):
    monkeypatch.setattr(telegram, "_urlopen", lambda *a, **k: b"not json")
    with pytest.raises(telegram.TelegramError):
        telegram.send_document(cfg, "f.txt", b"data")


def test_send_document_not_ok(cfg, monkeypatch):
    monkeypatch.setattr(telegram, "_urlopen",
                        lambda *a, **k: json.dumps({"ok": False, "description": "nope"}).encode())
    with pytest.raises(telegram.TelegramError):
        telegram.send_document(cfg, "f.txt", b"data")


def test_on_enqueued_not_mirrored(cfg, net):
    cfg.telegram.mirror = ["bob"]  # alice->? not in scope
    telegram.on_enqueued(cfg, "carol", mail.stamp_message("x", "alice", "carol", "m-2"), "m-2")
    assert net.calls == []


def test_on_enqueued_swallows_errors(cfg, monkeypatch):
    monkeypatch.setattr(telegram, "_urlopen", lambda *a: (_ for _ in ()).throw(OSError("down")))
    # must not raise even though the network failed
    telegram.on_enqueued(cfg, "bob", mail.stamp_message("hi", "alice", "bob", "m-1"), "m-1")
    # the failure was logged
    log = (cfg.log_dir / "agentainer.jsonl").read_text()
    assert "telegram-error" in log


# --------------------------------------------------------------------------
# reply-map bounding + json helpers
# --------------------------------------------------------------------------


def test_record_reply_target_trims(cfg):
    for i in range(205):
        telegram._record_reply_target(cfg, i, "alice", f"m-{i}")
    data = json.loads((cfg.run_dir / "telegram.replymap.json").read_text())
    assert len(data) == 200
    assert "204" in data and "0" not in data  # oldest trimmed


def test_load_json_bad_file(cfg):
    p = cfg.run_dir / "telegram.offset.json"
    p.write_text("{ broken")
    assert telegram._load_offset(cfg) == 0  # invalid -> default


# --------------------------------------------------------------------------
# poller: getUpdates + reply routing
# --------------------------------------------------------------------------


def _updates(result):
    return {"getUpdates": {"ok": True, "result": result}, "sendMessage": {"ok": True, "result": {"message_id": 1}}}


def test_poll_once_disabled(cfg):
    cfg.telegram.enabled = False
    assert telegram.poll_once(cfg) == 0


def test_poll_once_routes_reply(cfg, monkeypatch):
    telegram._record_reply_target(cfg, 42, "alice", "m-1")
    upd = [{"update_id": 7, "message": {"message_id": 100, "chat": {"id": 999},
            "text": "please proceed", "reply_to_message": {"message_id": 42}}}]
    monkeypatch.setattr(telegram, "_urlopen", FakeNet(_updates(upd)))
    assert telegram.poll_once(cfg) == 1
    assert telegram._load_offset(cfg) == 8
    # routed to alice as user mail
    q = list((cfg.queue_dir / "alice").glob("*")) + list(cfg.mail_paths(cfg.get("alice")).inbox.glob("*"))
    assert any("please proceed" in f.read_text() for f in q)


def test_process_update_variants(cfg, net):
    # no message / no text -> ignored
    telegram._process_update(cfg, {"update_id": 1})
    telegram._process_update(cfg, {"message": {"chat": {"id": 999}}})
    # wrong chat -> ignored
    telegram._process_update(cfg, {"message": {"chat": {"id": 111}, "text": "hi"}})
    # /to unknown agent -> usage reply
    telegram._process_update(cfg, {"message": {"chat": {"id": 999}, "text": "/to ghost hello"}})
    # /to known agent -> routed
    telegram._process_update(cfg, {"message": {"chat": {"id": 999}, "text": "/to alice hello"}})
    # reply whose target is unknown -> acknowledged (not silently dropped)
    telegram._process_update(cfg, {"message": {"chat": {"id": 999}, "text": "hey",
                                               "reply_to_message": {"message_id": 12345}}})
    assert "sendMessage" in net.methods()  # usage + confirmations were sent


def test_process_update_plain_message_is_acknowledged(cfg, net):
    # a bare message (not a reply, not /to) must get a "received but not routed" ack
    telegram._process_update(cfg, {"message": {"chat": {"id": 999}, "text": "hello there"}})
    assert net.methods() == ["sendMessage"]
    body = net.calls[0][1].decode()
    assert "not routed" in urllib_unquote(body)


def test_process_update_unresolved_reply_is_acknowledged(cfg, net):
    # a reply whose target isn't in the reply-map gets the same ack, not silence
    telegram._process_update(cfg, {"message": {"chat": {"id": 999}, "text": "yes go ahead",
                                               "reply_to_message": {"message_id": 55555}}})
    assert net.methods() == ["sendMessage"]
    assert "not routed" in urllib_unquote(net.calls[0][1].decode())


def test_poll_once_bad_update_is_caught(cfg, monkeypatch):
    upd = [{"update_id": 3, "message": {"chat": {"id": 999}, "text": "x"}}]
    monkeypatch.setattr(telegram, "_urlopen", FakeNet(_updates(upd)))
    monkeypatch.setattr(telegram, "_process_update",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert telegram.poll_once(cfg) == 1  # error swallowed, offset advanced
    assert "telegram-error" in (cfg.log_dir / "agentainer.jsonl").read_text()


def test_route_user_reply_unknown_agent(cfg):
    assert telegram._route_user_reply(cfg, "ghost", "hi") is False


# --------------------------------------------------------------------------
# background Poller (driven synchronously -- no real threads/sockets)
# --------------------------------------------------------------------------


def test_poller_run_success_then_stop(cfg, monkeypatch):
    p = telegram.Poller(cfg)
    def once(c, long_poll=0):
        p._stop.set()   # end the loop after one pass
        return 0
    monkeypatch.setattr(telegram, "poll_once", once)
    p._run()  # returns once stop is set


def test_poller_run_exception_path(cfg, monkeypatch):
    p = telegram.Poller(cfg)
    monkeypatch.setattr(telegram, "poll_once",
                        lambda *a, **k: (_ for _ in ()).throw(telegram.TelegramError("net")))
    # make the error-backoff wait end the loop instead of sleeping
    monkeypatch.setattr(p._stop, "wait", lambda t: p._stop.set())
    p._run()


def test_start_poller_disabled_returns_none(cfg):
    cfg.telegram.enabled = False
    assert telegram.start_poller(cfg) is None


def test_start_poller_and_stop(cfg, monkeypatch):
    monkeypatch.setattr(telegram, "poll_once", lambda *a, **k: 0)
    handle = telegram.start_poller(cfg)
    assert handle is not None
    handle.stop()  # joins the daemon thread


def test_reloaded_returns_fresh_cfg(cfg):
    fresh = telegram._reloaded(cfg)
    assert fresh.telegram.enabled is True


# --------------------------------------------------------------------------
# command surface -- full control-plane parity (CLAUDE.md principle #7)
# --------------------------------------------------------------------------


@pytest.fixture
def empty_cfg(tmp_path):
    from support import write_config
    root = tmp_path / "ews"
    root.mkdir()
    p = write_config(
        tmp_path,
        f"swarm:\n  root: {root}\n  session_prefix: \"e-\"\n"
        "defaults: {type: claude}\nagents: []\n",
    )
    import config as cfgmod
    return cfgmod.load(p)


def _cmd(cfg, net, text):
    """Drive one text update through _process_update; return the last sent body."""
    telegram._process_update(cfg, {"message": {"chat": {"id": 999}, "text": text}})
    return urllib_unquote(net.calls[-1][1].decode()) if net.calls else ""


# ---- helpers ----


def test_send_chunked_empty_and_split(cfg, net):
    telegram._send_chunked(cfg, "")
    assert net.calls == []
    telegram._send_chunked(cfg, "x" * 8000)
    assert len(net.calls) == 3  # 3900 + 3900 + 200


def test_scalarize_and_kv_pairs():
    assert telegram._scalarize("true") is True
    assert telegram._scalarize("false") is False
    assert telegram._scalarize("42") == 42
    assert telegram._scalarize("hello") == "hello"
    assert telegram._kv_pairs("role=lead dev") == {"role": "lead dev"}
    assert telegram._kv_pairs("a=1 b=2") == {"a": "1", "b": "2"}


def test_dispatch_help_unknown_and_botname(cfg):
    assert telegram._dispatch_command(cfg, "/help").startswith("🤖")
    assert telegram._dispatch_command(cfg, "/").startswith("🤖")       # empty body -> help
    assert telegram._dispatch_command(cfg, "/HELP@mybot").startswith("🤖")  # @bot + case
    assert "unknown command /wat" in telegram._dispatch_command(cfg, "/wat foo")


def test_process_update_runs_command(cfg, net):
    telegram._process_update(cfg, {"message": {"chat": {"id": 999}, "text": "/help"}})
    assert net.methods() == ["sendMessage"]
    assert "control plane" in urllib_unquote(net.calls[0][1].decode())


def test_process_update_command_error_is_reported(cfg, net, monkeypatch):
    monkeypatch.setattr(telegram, "_dispatch_command",
                        lambda c, t: (_ for _ in ()).throw(RuntimeError("boom")))
    out = _cmd(cfg, net, "/status")
    assert "⚠️" in out and "boom" in out


# ---- lifecycle ----


def test_cmd_up(cfg, net, monkeypatch):
    monkeypatch.setattr(telegram.reconcile, "start_one", lambda c, n: True)
    assert "started alice" in _cmd(cfg, net, "/up alice")
    monkeypatch.setattr(telegram.reconcile, "start_one", lambda c, n: False)
    assert "already running" in _cmd(cfg, net, "/up alice")
    monkeypatch.setattr(telegram.reconcile, "start_all", lambda c: ["alice", "bob"])
    assert "started: alice, bob" in _cmd(cfg, net, "/up")
    monkeypatch.setattr(telegram.reconcile, "start_all", lambda c: [])
    assert "already running" in _cmd(cfg, net, "/up")


def test_cmd_down(cfg, net, monkeypatch):
    monkeypatch.setattr(telegram.reconcile, "stop_one", lambda c, n: True)
    assert "stopped alice" in _cmd(cfg, net, "/down alice")
    monkeypatch.setattr(telegram.reconcile, "stop_one", lambda c, n: False)
    assert "already down" in _cmd(cfg, net, "/down alice")
    monkeypatch.setattr(telegram.reconcile, "stop_all", lambda c: ["alice"])
    assert "stopped: alice" in _cmd(cfg, net, "/down")
    monkeypatch.setattr(telegram.reconcile, "stop_all", lambda c: [])
    assert "no agents were running" in _cmd(cfg, net, "/down")


def test_cmd_restart(cfg, net, monkeypatch):
    monkeypatch.setattr(telegram.reconcile, "stop_one", lambda c, n: True)
    monkeypatch.setattr(telegram.reconcile, "start_one", lambda c, n: True)
    assert "restarted alice" in _cmd(cfg, net, "/restart alice")
    monkeypatch.setattr(telegram.reconcile, "stop_all", lambda c: ["a"])
    monkeypatch.setattr(telegram.reconcile, "start_all", lambda c: ["alice", "bob"])
    assert "restarted: alice, bob" in _cmd(cfg, net, "/restart")
    monkeypatch.setattr(telegram.reconcile, "start_all", lambda c: [])
    assert "no agents to restart" in _cmd(cfg, net, "/restart")


def test_cmd_reconcile(cfg, net, monkeypatch):
    monkeypatch.setattr(telegram.reconcile, "reconcile",
                        lambda c: {"started": ["a"], "stopped": [], "running": ["a"]})
    assert "reconcile" in _cmd(cfg, net, "/reconcile")


# ---- observe ----


def test_cmd_status(cfg, net, monkeypatch):
    monkeypatch.setattr(telegram.tmux, "session_exists", lambda s: s.endswith("alice"))
    monkeypatch.setattr(telegram.turn, "busy_info", lambda c, a: {"x": 1} if a.name == "alice" else None)
    monkeypatch.setattr(telegram.mail, "queued_files", lambda c, n: [1, 2] if n == "alice" else [])
    out = _cmd(cfg, net, "/status")
    assert "🟢 alice" in out and "busy" in out and "2 queued" in out
    assert "⚪ bob" in out and "down" in out
    assert "you: away" in out
    cfg.user_available = True
    assert "you: available" in _cmd(cfg, net, "/status")


def test_cmd_agents(cfg, net):
    out = _cmd(cfg, net, "/agents")
    assert "alice" in out and "bob" in out
    cfg.agents = []
    assert "no agents configured" in _cmd(cfg, net, "/agents")


def test_cmd_inbox(cfg, net):
    assert "inbox empty" in _cmd(cfg, net, "/inbox alice")
    a = cfg.get("alice")
    inb = cfg.mail_paths(a).inbox
    inb.mkdir(parents=True, exist_ok=True)
    (inb / "m1").write_text("From: user\n\nhello there")
    out = _cmd(cfg, net, "/inbox alice")
    assert "inbox (1)" in out and "hello there" in out
    assert "usage: /inbox" in _cmd(cfg, net, "/inbox")


def test_cmd_queue(cfg, net, monkeypatch, tmp_path):
    assert "queue empty" in _cmd(cfg, net, "/queue alice")
    f = tmp_path / "q1"
    f.write_text("From: bob\n\nhi there")
    monkeypatch.setattr(telegram.mail, "queued_files", lambda c, n: [f])
    out = _cmd(cfg, net, "/queue alice")
    assert "queue (1)" in out and "From: bob" in out
    empty = tmp_path / "q2"
    empty.write_text("")
    monkeypatch.setattr(telegram.mail, "queued_files", lambda c, n: [empty])
    assert "queue (1)" in _cmd(cfg, net, "/queue alice")  # empty-head branch


def test_cmd_pane(cfg, net, monkeypatch):
    monkeypatch.setattr(telegram.tmux, "capture_pane", lambda c, a: "PANE TEXT")
    assert "PANE TEXT" in _cmd(cfg, net, "/pane alice")
    monkeypatch.setattr(telegram.tmux, "capture_pane", lambda c, a: "")
    assert "no live session" in _cmd(cfg, net, "/pane alice")


def test_cmd_logs(cfg, net):
    assert "no events logged yet" in _cmd(cfg, net, "/logs")
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    (cfg.log_dir / "agentainer.jsonl").write_text(
        json.dumps({"agent": "alice", "kind": "delivered", "from_": "user", "to": "alice"}) + "\n"
        + "not-json\n"
        + json.dumps({"agent": "bob", "kind": "nudge"}) + "\n"
    )
    out = _cmd(cfg, net, "/logs 5")
    assert "delivered" in out and "user → alice" in out and "nudge" in out
    out2 = _cmd(cfg, net, "/logs alice")
    assert "delivered" in out2 and "nudge" not in out2
    assert "no matching events" in _cmd(cfg, net, "/logs ghost")


def test_cmd_config(cfg, net):
    out = _cmd(cfg, net, "/config")
    assert "telegram: on" in out and "mirror=" in out


def test_cmd_config_no_telegram(cfg):
    cfg.telegram = None
    out = telegram._cmd_config(cfg, "")
    assert "telegram:" not in out and cfg.name in out


# ---- mail & user ----


def test_cmd_to(cfg, net):
    assert "delivered to alice" in _cmd(cfg, net, "/to alice hello there")
    assert "usage: /to" in _cmd(cfg, net, "/to")
    assert "usage: /to" in _cmd(cfg, net, "/to ghost hi")


def test_cmd_available_away(cfg, net, monkeypatch):
    calls = []
    monkeypatch.setattr(telegram.reconcile, "edit_swarm", lambda c, **k: calls.append(k) or c)
    monkeypatch.setattr(telegram.mail, "set_user_available", lambda c, v: calls.append(v))
    assert "available" in _cmd(cfg, net, "/available")
    assert "away" in _cmd(cfg, net, "/away")
    assert {"user_available": True} in calls and True in calls
    assert {"user_available": False} in calls and False in calls


# ---- drive a session ----


def test_cmd_type(cfg, net, monkeypatch):
    rec = []
    monkeypatch.setattr(telegram.tmux, "paste_into", lambda c, s, t: rec.append((s, t)))
    assert "typed into alice" in _cmd(cfg, net, "/type alice hello world")
    assert rec[0][1] == "hello world"
    assert "usage: /type" in _cmd(cfg, net, "/type alice")


def test_cmd_key(cfg, net, monkeypatch):
    monkeypatch.setattr(telegram.tmux, "send_key", lambda c, s, k: True)
    assert "sent Enter to alice" in _cmd(cfg, net, "/key alice Enter")
    assert "usage: /key" in _cmd(cfg, net, "/key alice")


def test_cmd_compact(cfg, net, monkeypatch):
    monkeypatch.setattr(telegram.tmux, "paste_into", lambda c, s, t: True)
    assert "compacted alice" in _cmd(cfg, net, "/compact alice")
    monkeypatch.setattr(telegram.tmux, "session_exists", lambda s: s.endswith("alice"))
    assert "compacted: alice" in _cmd(cfg, net, "/compact")
    monkeypatch.setattr(telegram.tmux, "session_exists", lambda s: False)
    assert "no running agents" in _cmd(cfg, net, "/compact")


def test_cmd_idle(cfg, net, monkeypatch):
    monkeypatch.setattr(telegram.turn, "mark_turn_finished", lambda c, n: None)
    monkeypatch.setattr(telegram.mail, "process_read_folder", lambda c, n: 0)
    monkeypatch.setattr(telegram.mail, "nudge", lambda c, n: True)
    monkeypatch.setattr(telegram.mail, "release_next", lambda c, n: True)
    assert "released queued mail" in _cmd(cfg, net, "/idle alice")
    monkeypatch.setattr(telegram.mail, "release_next", lambda c, n: False)
    assert "forced idle" in _cmd(cfg, net, "/idle bob")
    assert "usage: /idle" in _cmd(cfg, net, "/idle")


# ---- edit config ----


def test_cmd_add(cfg, net, monkeypatch):
    monkeypatch.setattr(telegram.reconcile, "add_agent", lambda c, n, t, cmd, talk: c)
    monkeypatch.setattr(telegram.reconcile, "reconcile", lambda c: {})
    assert "added carol" in _cmd(cfg, net, "/add carol claude echo hi there")
    assert "usage: /add" in _cmd(cfg, net, "/add carol claude")


def test_cmd_edit(cfg, net, monkeypatch):
    rec = {}
    monkeypatch.setattr(telegram.reconcile, "edit_agent", lambda c, n, **f: rec.update({n: f}) or c)
    monkeypatch.setattr(telegram.reconcile, "reconcile", lambda c: {})
    assert "edited alice" in _cmd(cfg, net, "/edit alice role=lead dev helper")
    assert rec["alice"]["role"] == "lead dev helper"       # single '=' keeps spaces
    _cmd(cfg, net, "/edit alice type=codex can_talk_to=bob,user")
    assert rec["alice"]["type"] == "codex"                  # multi-pair
    assert "usage: /edit" in _cmd(cfg, net, "/edit alice")


def test_cmd_remove(cfg, net, monkeypatch):
    monkeypatch.setattr(telegram.reconcile, "remove_agent", lambda c, n: c)
    monkeypatch.setattr(telegram.reconcile, "reconcile", lambda c: {})
    killed = []
    monkeypatch.setattr(telegram.tmux, "tmux", lambda *a, **k: killed.append(a))
    monkeypatch.setattr(telegram.tmux, "session_exists", lambda s: True)
    assert "removed alice" in _cmd(cfg, net, "/remove alice")
    assert killed  # kill-session issued when a session was running
    monkeypatch.setattr(telegram.tmux, "session_exists", lambda s: False)
    assert "removed bob" in _cmd(cfg, net, "/remove bob")
    assert "usage: /remove" in _cmd(cfg, net, "/remove")


def test_cmd_set(cfg, net, monkeypatch):
    rec = []
    monkeypatch.setattr(telegram.reconcile, "edit_swarm", lambda c, **f: rec.append(f) or c)
    assert "swarm updated" in _cmd(cfg, net, "/set supervise=true")
    assert rec[-1] == {"supervise": True}
    _cmd(cfg, net, "/set ready_timeout_ms=5000 resume=false")
    assert rec[-1] == {"ready_timeout_ms": 5000, "resume": False}
    _cmd(cfg, net, "/set name=myswarm")
    assert rec[-1] == {"name": "myswarm"}                   # str branch of _scalarize
    assert "usage: /set" in _cmd(cfg, net, "/set nope")


def test_cmd_mirror(cfg, net, monkeypatch):
    rec = []
    monkeypatch.setattr(telegram.reconcile, "edit_telegram", lambda c, **f: rec.append(f) or c)
    assert "scope set to *" in _cmd(cfg, net, "/mirror *")
    _cmd(cfg, net, "/mirror alice, bob")
    assert rec[-1] == {"mirror": ["alice", "bob"]}
    assert "usage: /mirror" in _cmd(cfg, net, "/mirror")


# ---- templates ----


def test_cmd_templates(cfg, net, monkeypatch, tmp_path):
    assert "templates:" in _cmd(cfg, net, "/templates")     # real examples/ dir
    monkeypatch.setattr(telegram, "_templates_dir", lambda: tmp_path / "nope")
    assert "no templates bundled" in _cmd(cfg, net, "/templates")


def test_cmd_apply_valid(empty_cfg, monkeypatch, tmp_path):
    tdir = tmp_path / "tpls"
    tdir.mkdir()
    (tdir / "demo.yaml").write_text(
        "agents:\n  - name: solo\n    type: claude\n    command: echo hi\n    can_talk_to: [user]\n"
    )
    monkeypatch.setattr(telegram, "_templates_dir", lambda: tdir)
    monkeypatch.setattr(telegram.reconcile, "reconcile", lambda c: {})
    out = telegram._dispatch_command(empty_cfg, "/apply demo")
    assert "applied demo" in out and "solo" in out


def test_cmd_apply_errors(cfg, empty_cfg):
    with pytest.raises(telegram.TelegramError):
        telegram._dispatch_command(cfg, "/apply demo")          # swarm already has agents
    with pytest.raises(telegram.TelegramError):
        telegram._dispatch_command(empty_cfg, "/apply nonexistent")  # unknown template
    with pytest.raises(telegram.TelegramError):
        telegram._dispatch_command(empty_cfg, "/apply")         # missing arg

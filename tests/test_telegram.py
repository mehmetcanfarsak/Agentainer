"""100% line-coverage tests for lib/telegram.py (the optional Telegram bridge).

Every test mocks the single ``telegram._urlopen`` network seam -- no sockets, no
bot token, nothing to pay for. Covers: config gating, mirror-scope decisions, the
mirror-on-enqueue path (incl. reply-map capture), the getUpdates poller and reply
routing, and the background Poller loop (driven synchronously).
"""

import json
import sys

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

    def __call__(self, url, data, timeout):
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
    # reply whose target is unknown -> falls through, no crash
    telegram._process_update(cfg, {"message": {"chat": {"id": 999}, "text": "hey",
                                               "reply_to_message": {"message_id": 12345}}})
    assert "sendMessage" in net.methods()  # usage + confirmations were sent


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

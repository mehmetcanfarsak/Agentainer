#!/usr/bin/env python3
"""Agentainer -- optional Telegram bridge (mirror mail out, route replies in).

This is a **fully optional** integration. When configured (a `telegram:` block
in ``agentainer.yaml`` with a bot token + chat id), it does two things:

  1. **Mirror out** -- whenever a message is delivered into an agent's (or the
     ``user``'s) queue, a copy is pushed to a Telegram chat, so a human watching
     their phone sees the swarm's mail traffic live. Which agents are mirrored is
     configurable (a list, or ``*`` for all); mail addressed to the ``user`` is
     always mirrored (so the human is reachable even while "away").
  2. **Route replies in** -- a long-poll loop reads Telegram updates; when the
     human **replies** (a Telegram message reply) to a mirrored piece of ``user``
     mail, the reply is routed back into the swarm as ``user`` mail to the
     original sender. A ``/to <agent> <text>`` command works too.

Hard invariants (see CLAUDE.md):
  * **Zero runtime dependencies.** stdlib ``urllib`` only -- no ``requests``, no
    telegram SDK. Every network call goes through the single ``_urlopen`` seam so
    tests can mock it with no sockets.
  * **Correctness never depends on the network.** The mirror is best-effort:
    ``on_enqueued`` wraps everything and swallows errors, so a down/slow Telegram
    can never wedge or delay-fail the mailroom. The mail is already durably
    queued before we ever touch the network.
  * No secrets are logged. The bot token lives only in the config + the request
    URL we build locally.

Branding: "swarm" is retired -- it's Agentainer everywhere (decision D21).
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import lock  # noqa: E402
import log  # noqa: E402


API_ROOT = "https://api.telegram.org"

# How long getUpdates holds the connection open (server-side long poll), and the
# socket timeout we allow on top of it. Kept small for the mirror path.
LONG_POLL_S = 25
MIRROR_TIMEOUT_S = 8

# Bodies are trimmed before mirroring so a huge message can't spam the chat.
MAX_BODY = 1200


class TelegramError(Exception):
    pass


# --------------------------------------------------------------------------
# the single network seam (mock THIS in tests -- nothing else touches sockets)
# --------------------------------------------------------------------------


def _urlopen(url: str, data: bytes | None, timeout: float) -> bytes:  # pragma: no cover - the socket boundary; mocked in every test
    """POST *data* (or GET when None) to *url*; return the raw response body.

    The ONE place this module opens a socket. Tests monkeypatch it.
    """
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed api.telegram.org host
        return resp.read()


def api_call(cfg, method: str, params: dict, timeout: float = MIRROR_TIMEOUT_S):
    """Call one Telegram Bot API *method* and return its ``result`` payload.

    Raises ``TelegramError`` on a transport error or an ``ok: false`` response.
    """
    token = cfg.telegram.bot_token
    if not token:
        raise TelegramError("no bot_token configured")
    url = f"{API_ROOT}/bot{token}/{method}"
    body = urllib.parse.urlencode(params).encode()
    try:
        raw = _urlopen(url, body, timeout)
    except Exception as exc:  # noqa: BLE001 - normalise every transport failure
        raise TelegramError(str(exc)) from exc
    try:
        payload = json.loads(raw.decode())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise TelegramError(f"bad response: {exc}") from exc
    if not payload.get("ok"):
        raise TelegramError(f"telegram error: {payload.get('description', 'unknown')}")
    return payload.get("result")


# --------------------------------------------------------------------------
# config helpers
# --------------------------------------------------------------------------


def is_enabled(cfg) -> bool:
    """True iff Telegram is switched on AND has the credentials to work."""
    tg = getattr(cfg, "telegram", None)
    return bool(tg and tg.enabled and tg.bot_token and tg.chat_id)


def _mirror_set(cfg) -> set:
    m = cfg.telegram.mirror
    if isinstance(m, str):
        return set()  # "*" handled separately by _mirror_all
    return set(m)


def _mirror_all(cfg) -> bool:
    m = cfg.telegram.mirror
    return m == "*" or (isinstance(m, list) and "*" in m)


def _should_mirror(cfg, frm: str, to: str) -> bool:
    """Decide whether a mail from *frm* to *to* should be pushed to Telegram."""
    tg = cfg.telegram
    if to == "user":
        return bool(tg.mirror_user)
    if frm == "system":
        return bool(tg.mirror_system)
    if _mirror_all(cfg):
        return True
    s = _mirror_set(cfg)
    return frm in s or to in s


# --------------------------------------------------------------------------
# reply map (Telegram message_id -> the agent whose mail it mirrored)
# --------------------------------------------------------------------------


def _replymap_path(cfg) -> Path:
    return cfg.run_dir / "telegram.replymap.json"


def _load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return default
    return default


def _write_atomic(path: Path, text: str) -> None:
    """Write *text* to *path* atomically, so a lock-free reader (the poller) sees
    either the old or the new file -- never a half-written one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _record_reply_target(cfg, tg_message_id, agent: str, msg_id: str) -> None:
    """Remember that Telegram message *tg_message_id* mirrored mail from *agent*,
    so a reply to it can be routed back as ``user`` mail.

    The read-modify-write is serialised with the cross-process file lock so two
    concurrent mirrors (e.g. a hook process and a ``send``) can't clobber each
    other's entry, and the write is atomic so the poller never reads a torn map.
    """
    with lock.file_lock(cfg, "telegram", "replymap"):
        path = _replymap_path(cfg)
        data = _load_json(path, {})
        data[str(tg_message_id)] = {"agent": agent, "msg_id": msg_id}
        # Keep the map bounded -- only the most recent 200 mirrored messages.
        if len(data) > 200:
            for k in sorted(data, key=lambda x: int(x))[:-200]:
                data.pop(k, None)
        _write_atomic(path, json.dumps(data))


def _reply_target(cfg, tg_message_id):
    return _load_json(_replymap_path(cfg), {}).get(str(tg_message_id))


# --------------------------------------------------------------------------
# header parsing (self-contained: don't import mail to avoid an import cycle)
# --------------------------------------------------------------------------


def _hdr(text: str, field: str):
    m = re.search(rf"^{re.escape(field)}:\s*(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else None


def _split_body(text: str) -> str:
    parts = text.split("\n\n", 1)
    return parts[1] if len(parts) > 1 else text


def format_mail(frm: str, to: str, body: str) -> str:
    """Human-readable one-message Telegram card (plain text, no markup)."""
    icon = "🧑" if frm == "user" else "⚙️" if frm == "system" else "✉️"
    body = body.strip()
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY] + " …"
    header = f"{icon} {frm} → {to}"
    if to == "user":
        header += "\n(reply to this message to answer as the user)"
    return f"{header}\n\n{body}"


# --------------------------------------------------------------------------
# outbound: mirror a freshly-queued message
# --------------------------------------------------------------------------


def send_message(cfg, text: str, reply_to=None):
    """Send *text* to the configured chat; return the sent message dict."""
    params = {"chat_id": cfg.telegram.chat_id, "text": text, "disable_web_page_preview": "true"}
    if reply_to is not None:
        params["reply_to_message_id"] = reply_to
    return api_call(cfg, "sendMessage", params)


def on_enqueued(cfg, recipient: str, text: str, msg_id: str) -> None:
    """Best-effort: mirror a just-queued stamped message to Telegram.

    Called from ``mail.enqueue`` for EVERY delivered message. Never raises -- the
    mail is already durably queued, so a Telegram failure is logged and dropped.
    """
    try:
        if not is_enabled(cfg):
            return
        frm = _hdr(text, "From") or "?"
        to = _hdr(text, "To") or recipient
        if not _should_mirror(cfg, frm, to):
            return
        sent = send_message(cfg, format_mail(frm, to, _split_body(text)))
        if to == "user" and isinstance(sent, dict) and sent.get("message_id") is not None:
            _record_reply_target(cfg, sent["message_id"], frm, msg_id)
        log.log_event(cfg, recipient, "telegram-mirror", from_=frm, to=to, id=msg_id)
    except Exception as exc:  # noqa: BLE001 - mirror is best-effort, never fatal
        try:
            log.log_event(cfg, recipient, "telegram-error", error=str(exc)[:200])
        except Exception:  # pragma: no cover - logging must never raise here
            pass


# --------------------------------------------------------------------------
# inbound: poll for updates and route replies back into the swarm
# --------------------------------------------------------------------------


def _offset_path(cfg) -> Path:
    return cfg.run_dir / "telegram.offset.json"


def _load_offset(cfg) -> int:
    return int(_load_json(_offset_path(cfg), {}).get("offset", 0))


def _save_offset(cfg, offset: int) -> None:
    _write_atomic(_offset_path(cfg), json.dumps({"offset": offset}))


def _route_user_reply(cfg, agent: str, text: str) -> bool:
    """Route a Telegram-originated reply into the swarm as ``user`` mail."""
    if agent not in cfg.names():
        return False
    import mail  # lazy: avoids the mail <-> telegram import cycle

    mail.send_as_user(cfg, agent, text)
    log.log_event(cfg, agent, "telegram-reply", from_="user")
    return True


def _process_update(cfg, upd: dict) -> None:
    """Handle one Telegram update: a reply routes back; ``/to`` routes explicitly."""
    msg = upd.get("message") or upd.get("channel_post")
    if not isinstance(msg, dict):
        return
    text = msg.get("text")
    if not text:
        return
    # Only accept input from the configured chat (a shared bot must not let
    # strangers drive the swarm). is_enabled guarantees chat_id is set.
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    if chat_id != str(cfg.telegram.chat_id):
        return

    reply = msg.get("reply_to_message")
    if isinstance(reply, dict):
        target = _reply_target(cfg, reply.get("message_id"))
        if target and _route_user_reply(cfg, target["agent"], text):
            send_message(cfg, f"✓ delivered to {target['agent']}")
            return

    if text.startswith("/to "):
        parts = text[4:].split(None, 1)
        if len(parts) == 2 and _route_user_reply(cfg, parts[0], parts[1]):
            send_message(cfg, f"✓ delivered to {parts[0]}")
        else:
            send_message(cfg, "usage: /to <agent> <message>  (unknown agent otherwise)")


def poll_once(cfg, long_poll: int = 0) -> int:
    """One getUpdates round; process each update; return how many were handled."""
    if not is_enabled(cfg):
        return 0
    params = {"offset": _load_offset(cfg), "timeout": long_poll}
    updates = api_call(cfg, "getUpdates", params, timeout=long_poll + MIRROR_TIMEOUT_S)
    n = 0
    for upd in updates or []:
        _save_offset(cfg, int(upd["update_id"]) + 1)
        try:
            _process_update(cfg, upd)
        except Exception as exc:  # noqa: BLE001 - one bad update can't kill the loop
            log.log_event(cfg, "user", "telegram-error", error=str(exc)[:200])
        n += 1
    return n


class Poller:
    """A background long-poll loop that routes Telegram replies into the swarm."""

    def __init__(self, cfg):
        self.cfg = cfg
        self._stop = threading.Event()
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.cfg = _reloaded(self.cfg)
                poll_once(self.cfg, long_poll=LONG_POLL_S)
            except Exception:  # noqa: BLE001 - a transient network error must not kill the loop
                self._stop.wait(3)

    def start(self) -> "Poller":
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


def _reloaded(cfg):
    """Re-read the config from disk so live edits (token/chat/enable) take effect.

    Best-effort: if the file is momentarily unreadable, keep the current config.
    """
    try:
        import config as cfgmod

        return cfgmod.load(cfg.path)
    except Exception:  # pragma: no cover - defensive; a bad edit keeps the old cfg
        return cfg


def start_poller(cfg):
    """Start a background reply poller if Telegram is enabled; else return None."""
    if not is_enabled(cfg):
        return None
    return Poller(cfg).start()

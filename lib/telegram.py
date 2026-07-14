#!/usr/bin/env python3
"""Agentainer -- optional Telegram bridge (mirror mail out, route replies in).

This is a **fully optional** integration. When configured (a `telegram:` block
in ``agentainer.yaml`` with a bot token + chat id), it does three things:

  1. **Mirror out** -- whenever a message is delivered into an agent's (or the
     ``user``'s) queue, a copy is pushed to a Telegram chat, so a human watching
     their phone sees the swarm's mail traffic live. Which agents are mirrored is
     configurable (a list, or ``*`` for all); mail addressed to the ``user`` is
     always mirrored (so the human is reachable even while "away"). A body too
     long for one inline card is trimmed on the card and the **full** text is
     additionally uploaded as a ``.txt`` attachment, so nothing is dropped.
  2. **Route replies in** -- a long-poll loop reads Telegram updates; when the
     human **replies** (a Telegram message reply) to a mirrored piece of ``user``
     mail, the reply is routed back into the swarm as ``user`` mail to the
     original sender.
  3. **Full control plane** -- slash commands give Telegram **parity with the CLI
     and the web UI** (CLAUDE.md principle #7): lifecycle (``/up`` ``/down``
     ``/restart`` ``/reconcile``), observability (``/status`` ``/inbox`` ``/queue``
     ``/pane`` ``/logs`` ``/config`` ``/agents``), mail/user (``/to`` ``/available``
     ``/away``), live-session driving (``/type`` ``/key`` ``/compact`` ``/idle``),
     and config editing (``/add`` ``/edit`` ``/remove`` ``/set`` ``/mirror``
     ``/templates`` ``/apply``). Each handler is a thin adapter over the shared,
     tested ``lib/`` core -- the same functions the CLI and UI call -- so the
     three surfaces cannot drift. ``/help`` lists them all.

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
import mail  # noqa: E402
import tmux  # noqa: E402
import turn  # noqa: E402
import reconcile  # noqa: E402


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


def _urlopen(url: str, data: bytes | None, timeout: float, headers: dict | None = None) -> bytes:  # pragma: no cover - the socket boundary; mocked in every test
    """POST *data* (or GET when None) to *url*; return the raw response body.

    The ONE place this module opens a socket. Tests monkeypatch it. *headers* is
    only needed for the multipart document upload (see ``send_document``); the
    plain ``sendMessage``/``getUpdates`` paths pass none.
    """
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    for key, val in (headers or {}).items():
        req.add_header(key, val)
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


def _multipart(fields: dict, filename: str, file_bytes: bytes) -> tuple[bytes, str]:
    """Encode *fields* + one file as ``multipart/form-data``.

    Returns ``(body, content_type)``. Stdlib only -- no ``requests``. The boundary
    is a fixed unlikely token; Telegram doesn't require it to be random and our
    text bodies never contain it.
    """
    boundary = "----AgentainerTgBoundary7f3a9c1e"
    out = bytearray()
    for name, value in fields.items():
        out += f"--{boundary}\r\n".encode()
        out += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        out += f"{value}\r\n".encode()
    out += f"--{boundary}\r\n".encode()
    out += f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'.encode()
    out += b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
    out += file_bytes + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def send_document(cfg, filename: str, file_bytes: bytes, caption: str = "", reply_to=None):
    """Upload *file_bytes* as a Telegram document (attachment); return the result.

    Used to carry the overflow of a long mail body that doesn't fit in a single
    inline card, so nothing is silently dropped.
    """
    token = cfg.telegram.bot_token
    if not token:
        raise TelegramError("no bot_token configured")
    fields = {"chat_id": str(cfg.telegram.chat_id)}
    if caption:
        fields["caption"] = caption[:1024]  # Telegram caps captions at 1024 chars
    if reply_to is not None:
        fields["reply_to_message_id"] = str(reply_to)
    body, content_type = _multipart(fields, filename, file_bytes)
    url = f"{API_ROOT}/bot{token}/sendDocument"
    try:
        raw = _urlopen(url, body, MIRROR_TIMEOUT_S, {"Content-Type": content_type})
    except Exception as exc:  # noqa: BLE001 - normalise every transport failure
        raise TelegramError(str(exc)) from exc
    try:
        payload = json.loads(raw.decode())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise TelegramError(f"bad response: {exc}") from exc
    if not payload.get("ok"):
        raise TelegramError(f"telegram error: {payload.get('description', 'unknown')}")
    return payload.get("result")


def on_enqueued(cfg, recipient: str, text: str, msg_id: str) -> None:
    """Best-effort: mirror a just-queued stamped message to Telegram.

    Called from ``mail.enqueue`` for EVERY delivered message. Never raises -- the
    mail is already durably queued, so a Telegram failure is logged and dropped.

    Long bodies: the inline card still shows the first ``MAX_BODY`` characters
    (as before), and the **full** body is additionally uploaded as a ``.txt``
    attachment (a reply to the card) so nothing is lost off the phone.
    """
    try:
        if not is_enabled(cfg):
            return
        frm = _hdr(text, "From") or "?"
        to = _hdr(text, "To") or recipient
        if not _should_mirror(cfg, frm, to):
            return
        body = _split_body(text)
        sent = send_message(cfg, format_mail(frm, to, body))
        sent_id = sent.get("message_id") if isinstance(sent, dict) else None
        if to == "user" and sent_id is not None:
            _record_reply_target(cfg, sent_id, frm, msg_id)
        # Overflow: the card was trimmed, so ship the full body as an attachment
        # (best-effort -- a failed upload must not lose the already-mirrored card).
        if len(body.strip()) > MAX_BODY:
            try:
                send_document(cfg, f"{msg_id}.txt", body.strip().encode("utf-8"),
                              caption=f"full message: {frm} → {to}", reply_to=sent_id)
            except Exception as exc:  # noqa: BLE001 - overflow upload is best-effort
                log.log_event(cfg, recipient, "telegram-error", error=str(exc)[:200])
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
    """Handle one Telegram update: a reply routes back; ``/to`` routes explicitly;
    anything else gets an acknowledgement so the human isn't left guessing whether
    the swarm picked it up."""
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

    if text.startswith("/"):
        # A slash command: run it against the shared lib/ core and reply. Errors
        # (unknown agent, bad usage, a rejected config edit) come back as a ⚠️
        # message so the human self-corrects in-band -- they can never kill the
        # poller (poll_once already guards, but we belt-and-brace here too).
        try:
            out = _dispatch_command(cfg, text)
        except Exception as exc:  # noqa: BLE001 - a bad command must never wedge the loop
            out = f"⚠️ {exc}"
        _send_chunked(cfg, out)
        return

    # Fell through: a plain message that isn't a reply we could route, and isn't a
    # command. Acknowledge it so the human doesn't assume it's being processed by
    # the swarm -- a bare message has no target, so tell them how to route one.
    send_message(cfg, "ℹ️ received, but not routed anywhere — a plain message has "
                      "no recipient. Reply to a mailed message to answer its "
                      "sender, use /to <agent> <message>, or /help for commands.")


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


# --------------------------------------------------------------------------
# command surface -- FULL control-plane parity with the CLI and the web UI
# (CLAUDE.md principle #7: everything doable in the UI/yaml is doable here).
#
# Each handler is a THIN adapter over the shared, tested lib/ core (reconcile,
# mail, tmux, turn) -- the same functions the CLI and HTTP UI call -- so the
# 100%-covered core stays the substance and the three surfaces can't drift.
# A handler returns the reply text (or raises; the dispatcher renders that as a
# ⚠️ message). Only the configured chat can reach here (checked in
# ``_process_update``), so these carry the same trust as the UI control plane.
# --------------------------------------------------------------------------


HELP = (
    "🤖 Agentainer — Telegram control plane\n"
    "\n"
    "Lifecycle:\n"
    "/status — swarm overview\n"
    "/up [agent] — start all / one\n"
    "/down [agent] — stop all / one\n"
    "/restart [agent] — restart all / one\n"
    "/reconcile — make running set match the config\n"
    "\n"
    "Mail & user:\n"
    "/to <agent> <msg> — send mail as the user (or reply to a mirrored message)\n"
    "/available — mark yourself available\n"
    "/away — mark yourself away\n"
    "\n"
    "Observe:\n"
    "/agents — list agents + ACLs\n"
    "/inbox <agent> — current inbox\n"
    "/queue <agent> — queued mail\n"
    "/pane <agent> — terminal snapshot\n"
    "/logs [agent] [n] — recent events\n"
    "/config — swarm + telegram settings\n"
    "\n"
    "Drive a session:\n"
    "/type <agent> <text> — type into the pane\n"
    "/key <agent> <Key> — send a control key (Enter, Escape, C-c, …)\n"
    "/compact [agent] — /compact one or all running agents\n"
    "/idle <agent> — force idle + drain queued mail\n"
    "\n"
    "Edit config:\n"
    "/add <name> <type> <command…> — add an agent (talks to user; edit for more)\n"
    "/edit <agent> <key>=<value> … — edit an agent\n"
    "/remove <agent> — remove an agent\n"
    "/set <key>=<value> … — swarm settings\n"
    "/mirror <*|a,b,…> — telegram mirror scope\n"
    "/templates — list starter templates\n"
    "/apply <template> — seed an EMPTY swarm from a template"
)


def _send_chunked(cfg, text: str) -> None:
    """Send *text*, split under Telegram's 4096-char per-message limit."""
    if not text:
        return
    limit = 3900
    for i in range(0, len(text), limit):
        send_message(cfg, text[i:i + limit])


def _arg(arg: str, usage: str) -> str:
    """Return a required stripped argument, or raise the *usage* hint."""
    a = arg.strip()
    if not a:
        raise TelegramError(usage)
    return a


def _scalarize(v: str):
    """Coerce a ``/set``/config string to bool/int where it clearly is one."""
    low = v.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    if re.fullmatch(r"-?\d+", v.strip()):
        return int(v.strip())
    return v.strip()


def _kv_pairs(rest: str) -> dict:
    """Parse ``key=value`` pairs. A single ``=`` keeps spaces in the value (so a
    one-field role/command edit works); multiple pairs split on whitespace."""
    fields = {}
    if rest.count("=") == 1:
        k, v = rest.split("=", 1)
        fields[k.strip()] = v.strip()
    else:
        for pair in rest.split():
            if "=" in pair:
                k, v = pair.split("=", 1)
                fields[k.strip()] = v
    return fields


# ---- lifecycle -----------------------------------------------------------


def _cmd_up(cfg, arg):
    if arg.strip():
        name = arg.strip()
        return f"▶️ started {name}" if reconcile.start_one(cfg, name) else f"{name} was already running"
    started = reconcile.start_all(cfg)
    return f"▶️ started: {', '.join(started)}" if started else "all agents already running"


def _cmd_down(cfg, arg):
    if arg.strip():
        name = arg.strip()
        return f"⏹ stopped {name}" if reconcile.stop_one(cfg, name) else f"{name} was already down"
    stopped = reconcile.stop_all(cfg)
    return f"⏹ stopped: {', '.join(stopped)}" if stopped else "no agents were running"


def _cmd_restart(cfg, arg):
    if arg.strip():
        name = arg.strip()
        reconcile.stop_one(cfg, name)
        reconcile.start_one(cfg, name)
        return f"🔄 restarted {name}"
    reconcile.stop_all(cfg)
    started = reconcile.start_all(cfg)
    return f"🔄 restarted: {', '.join(started)}" if started else "no agents to restart"


def _cmd_reconcile(cfg, arg):
    r = reconcile.reconcile(cfg)
    return (f"reconcile — started: {r['started'] or '—'} · "
            f"stopped: {r['stopped'] or '—'} · running: {r['running'] or '—'}")


# ---- observe -------------------------------------------------------------


def _cmd_status(cfg, arg):
    lines = [f"🐝 {cfg.name}"]
    for a in cfg.agents:
        running = tmux.session_exists(a.session)
        busy = running and turn.busy_info(cfg, a) is not None
        depth = len(mail.queued_files(cfg, a.name))
        dot = "🟢" if running else "⚪"
        state = "busy" if busy else ("idle" if running else "down")
        tail = f" · {depth} queued" if depth else ""
        lines.append(f"{dot} {a.name} [{a.type}] {state}{tail}")
    lines.append(f"you: {'available' if cfg.user_available else 'away'}")
    return "\n".join(lines)


def _cmd_agents(cfg, arg):
    if not cfg.agents:
        return "no agents configured — /templates then /apply <name>, or /add"
    return "\n".join(f"• {a.name} [{a.type}] → {a.can_talk_to}" for a in cfg.agents)


def _cmd_inbox(cfg, arg):
    a = cfg.get(_arg(arg, "usage: /inbox <agent>"))
    inbox = cfg.mail_paths(a).inbox
    files = sorted(f for f in inbox.iterdir() if f.is_file()) if inbox.exists() else []
    if not files:
        return f"{a.name}: inbox empty"
    out = [f"📥 {a.name} inbox ({len(files)}):"]
    out.extend("— " + f.read_text().strip() for f in files)
    return "\n\n".join(out)


def _cmd_queue(cfg, arg):
    a = cfg.get(_arg(arg, "usage: /queue <agent>"))
    files = mail.queued_files(cfg, a.name)
    if not files:
        return f"{a.name}: queue empty"
    out = [f"⏳ {a.name} queue ({len(files)}):"]
    for f in files:
        head = f.read_text().strip().splitlines()
        out.append("— " + (head[0] if head else ""))
    return "\n".join(out)


def _cmd_pane(cfg, arg):
    a = cfg.get(_arg(arg, "usage: /pane <agent>"))
    text = tmux.capture_pane(cfg, a) or "(no live session)"
    return f"🖥 {a.name}:\n{text}"


def _cmd_logs(cfg, arg):
    parts = arg.split()
    name = next((p for p in parts if not p.isdigit()), None)
    n = next((int(p) for p in parts if p.isdigit()), 15)
    path = cfg.log_dir / "agentainer.jsonl"
    if not path.exists():
        return "no events logged yet"
    recs = []
    for ln in path.read_text().splitlines():
        if ln.strip():
            try:
                recs.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
    if name:
        recs = [r for r in recs if name in (r.get("agent"), r.get("from_"), r.get("to"))]
    recs = recs[-n:]
    if not recs:
        return "no matching events"
    out = ["🪵 recent events:"]
    for r in recs:
        route = " ".join(x for x in (r.get("from_"), "→", r.get("to")) if x) if r.get("to") else ""
        out.append(f"• {r.get('kind', '?')} {route}".rstrip())
    return "\n".join(out)


def _cmd_config(cfg, arg):
    out = [
        f"⚙️ {cfg.name}",
        f"agents: {', '.join(cfg.names()) or '—'}",
        f"you: {'available' if cfg.user_available else 'away'}",
    ]
    tg = getattr(cfg, "telegram", None)
    if tg:
        out.append(
            f"telegram: {'on' if tg.enabled else 'off'} · mirror={tg.mirror} · "
            f"mirror_user={tg.mirror_user} · mirror_system={tg.mirror_system}"
        )
    return "\n".join(out)


# ---- mail & user ---------------------------------------------------------


def _cmd_to(cfg, arg):
    parts = arg.split(None, 1)
    if len(parts) == 2 and _route_user_reply(cfg, parts[0], parts[1]):
        return f"✓ delivered to {parts[0]}"
    return "usage: /to <agent> <message>  (unknown agent otherwise)"


def _cmd_available(cfg, arg):
    reconcile.edit_swarm(cfg, user_available=True)
    mail.set_user_available(cfg, True)
    return "🟢 you are now available — new user mail will be delivered"


def _cmd_away(cfg, arg):
    reconcile.edit_swarm(cfg, user_available=False)
    mail.set_user_available(cfg, False)
    return "🌙 you are now away — user mail will be held until you return"


# ---- drive a session -----------------------------------------------------


def _cmd_type(cfg, arg):
    parts = arg.split(None, 1)
    if len(parts) != 2:
        raise TelegramError("usage: /type <agent> <text>")
    a = cfg.get(parts[0])
    tmux.paste_into(cfg, a.session, parts[1])
    return f"⌨️ typed into {a.name}"


def _cmd_key(cfg, arg):
    parts = arg.split()
    if len(parts) != 2:
        raise TelegramError("usage: /key <agent> <Key>  (e.g. /key dev Enter)")
    a = cfg.get(parts[0])
    tmux.send_key(cfg, a.session, parts[1])
    return f"⌨️ sent {parts[1]} to {a.name}"


def _cmd_compact(cfg, arg):
    if arg.strip():
        a = cfg.get(arg.strip())
        tmux.paste_into(cfg, a.session, "/compact")
        return f"🗜 compacted {a.name}"
    done = []
    for a in cfg.agents:
        if tmux.session_exists(a.session):
            tmux.paste_into(cfg, a.session, "/compact")
            done.append(a.name)
    return f"🗜 compacted: {', '.join(done)}" if done else "no running agents to compact"


def _cmd_idle(cfg, arg):
    a = cfg.get(_arg(arg, "usage: /idle <agent>"))
    turn.mark_turn_finished(cfg, a.name)
    mail.process_read_folder(cfg, a.name)
    released = mail.release_next(cfg, a.name)
    mail.nudge(cfg, a.name)
    return f"💤 {a.name} forced idle" + (" (released queued mail)" if released else "")


# ---- edit config ---------------------------------------------------------


def _cmd_add(cfg, arg):
    parts = arg.split(None, 2)
    if len(parts) < 3:
        raise TelegramError("usage: /add <name> <type> <command…>")
    name, type_, command = parts
    new_cfg = reconcile.add_agent(cfg, name, type_, command, ["user"])
    reconcile.reconcile(new_cfg)
    return f"➕ added {name} [{type_}] (talks to: user) — use /edit for peers/role"


def _cmd_edit(cfg, arg):
    parts = arg.split(None, 1)
    if len(parts) < 2 or "=" not in parts[1]:
        raise TelegramError("usage: /edit <agent> <key>=<value> [key=value …]")
    fields = _kv_pairs(parts[1])
    new_cfg = reconcile.edit_agent(cfg, parts[0], **fields)
    reconcile.reconcile(new_cfg)
    return f"✏️ edited {parts[0]}: {', '.join(fields)}"


def _cmd_remove(cfg, arg):
    name = _arg(arg, "usage: /remove <agent>")
    a = cfg.get(name)  # raises for an unknown agent
    if tmux.session_exists(a.session):
        tmux.tmux("kill-session", "-t", f"={a.session}", check=False, capture=True)
    new_cfg = reconcile.remove_agent(cfg, name)
    reconcile.reconcile(new_cfg)
    return f"🗑 removed {name}"


def _cmd_set(cfg, arg):
    if "=" not in arg:
        raise TelegramError("usage: /set <key>=<value> [key=value …]")
    fields = {k: _scalarize(str(v)) for k, v in _kv_pairs(arg).items()}
    reconcile.edit_swarm(cfg, **fields)
    return f"⚙️ swarm updated: {', '.join(fields)}"


def _cmd_mirror(cfg, arg):
    spec = _arg(arg, "usage: /mirror <*|agent,agent,…>")
    mirror = "*" if spec == "*" else [p.strip() for p in spec.split(",") if p.strip()]
    reconcile.edit_telegram(cfg, mirror=mirror)
    return f"📡 mirror scope set to {mirror}"


# ---- templates -----------------------------------------------------------


def _templates_dir() -> Path:
    """The bundled ``examples/`` swarms shipped alongside ``lib/`` (same as the UI)."""
    return Path(__file__).resolve().parent.parent / "examples"


def _cmd_templates(cfg, arg):
    tdir = _templates_dir()
    names = sorted(p.stem for p in tdir.glob("*.yaml")) if tdir.exists() else []
    if not names:
        return "no templates bundled"
    body = "\n".join(f"• {n}" for n in names)
    return f"📦 templates:\n{body}\n\nseed an EMPTY swarm with /apply <name>"


def _cmd_apply(cfg, arg):
    name = _arg(arg, "usage: /apply <template>")
    if cfg.names():
        raise TelegramError("swarm already has agents; templates seed an empty swarm")
    tdir = _templates_dir()
    valid = {p.stem for p in tdir.glob("*.yaml")} if tdir.exists() else set()
    if name not in valid:
        raise TelegramError(f"unknown template {name!r}")
    raw = reconcile.load_raw(tdir / f"{name}.yaml")
    added = reconcile.apply_template(cfg, raw.get("agents") or [], raw.get("defaults"))
    import config as cfgmod  # lazy: mirror the UI's reload-after-mutate

    reconcile.reconcile(cfgmod.load(cfg.path))
    return f"📦 applied {name}: {', '.join(added)}"


def _cmd_help(cfg, arg):
    return HELP


_COMMANDS = {
    "help": _cmd_help, "start": _cmd_help,
    "status": _cmd_status, "agents": _cmd_agents,
    "up": _cmd_up, "down": _cmd_down, "restart": _cmd_restart, "reconcile": _cmd_reconcile,
    "to": _cmd_to, "available": _cmd_available, "away": _cmd_away,
    "inbox": _cmd_inbox, "queue": _cmd_queue, "pane": _cmd_pane,
    "logs": _cmd_logs, "config": _cmd_config,
    "type": _cmd_type, "key": _cmd_key, "compact": _cmd_compact, "idle": _cmd_idle,
    "add": _cmd_add, "edit": _cmd_edit, "remove": _cmd_remove,
    "set": _cmd_set, "mirror": _cmd_mirror,
    "templates": _cmd_templates, "apply": _cmd_apply,
}


def _dispatch_command(cfg, text: str) -> str:
    """Parse ``/cmd args`` and run its handler; return the reply text.

    Strips a ``@botname`` suffix (Telegram appends it to commands in groups) and
    is case-insensitive on the command word. Unknown commands hint at ``/help``.
    """
    body = text[1:].strip()
    if not body:
        return HELP
    head, _, rest = body.partition(" ")
    cmd = head.split("@", 1)[0].lower()
    handler = _COMMANDS.get(cmd)
    if handler is None:
        return f"unknown command /{cmd} — send /help for the list"
    return handler(cfg, rest.strip())


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


# --------------------------------------------------------------------------
# shared multi-swarm control-plane poller
#
# One `agentainer serve` manages every swarm, and they all share ONE Telegram
# bot (the global registry setting merged into each cfg). A single inbound poller
# therefore serves the whole machine: one offset (in the global state dir), one
# getUpdates loop, and per-update routing to the right swarm. Outbound mirror is
# unchanged -- each swarm still mirrors its own mail through the shared bot.
# --------------------------------------------------------------------------


def _control_offset_path() -> Path:
    import registry  # lazy: registry imports config; avoid an import cycle

    return registry.state_dir() / "telegram.offset.json"


def _load_control_offset() -> int:
    return int(_load_json(_control_offset_path(), {}).get("offset", 0))


def _save_control_offset(offset: int) -> None:
    _write_atomic(_control_offset_path(), json.dumps({"offset": offset}))


def _primary_cfg(swarms: dict):
    """The cfg the shared bot sends through + whose commands run by default.

    The Telegram "active swarm" (settable with ``/use``) when it is still present,
    else the first swarm. All swarms share the bot, so any cfg can send.
    """
    import registry

    name = registry.active_swarm()
    if name and name in swarms:
        return swarms[name]
    return next(iter(swarms.values()))


def _control_command(swarms: dict, text: str):
    """Handle the control-plane-only commands ``/swarms`` and ``/use``.

    Returns the reply text, or ``None`` when *text* is an ordinary per-swarm
    command (the caller dispatches that against the active swarm).
    """
    import registry

    head, _, rest = text[1:].strip().partition(" ")
    cmd = head.split("@", 1)[0].lower()
    if cmd == "swarms":
        active = registry.active_swarm()
        lines = [("→ " if n == active else "  ") + n for n in sorted(swarms)]
        return "swarms managed by this control plane:\n" + ("\n".join(lines) or "(none)")
    if cmd == "use":
        name = rest.strip()
        if name not in swarms:
            return f"unknown swarm {name!r} — send /swarms to list them"
        registry.set_active_swarm(name)
        return f"✓ active swarm is now {name!r} — /commands now target it"
    return None


def _process_control_update(swarms: dict, upd: dict) -> None:
    """Route one update to the right swarm: a reply to its sender, a control
    command (``/swarms`` ``/use``), or a per-swarm command against the active
    swarm; a plain message is acknowledged with how to route one."""
    msg = upd.get("message") or upd.get("channel_post")
    if not isinstance(msg, dict):
        return
    text = msg.get("text")
    if not text:
        return
    primary = _primary_cfg(swarms)
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    if chat_id != str(primary.telegram.chat_id):
        return

    # A reply may belong to ANY swarm -- find the one whose replymap holds it.
    reply = msg.get("reply_to_message")
    if isinstance(reply, dict):
        mid = reply.get("message_id")
        for cfg in swarms.values():
            target = _reply_target(cfg, mid)
            if target and _route_user_reply(cfg, target["agent"], text):
                send_message(cfg, f"✓ delivered to {target['agent']} [{cfg.name}]")
                return

    if text.startswith("/"):
        ctl = _control_command(swarms, text)
        if ctl is not None:
            _send_chunked(primary, ctl)
            return
        try:
            out = _dispatch_command(primary, text)
        except Exception as exc:  # noqa: BLE001 - a bad command must never wedge the loop
            out = f"⚠️ {exc}"
        _send_chunked(primary, f"[{primary.name}] {out}")
        return

    send_message(primary, "ℹ️ received, but not routed — reply to a mailed message to "
                          "answer its sender, use /to <agent> <message>, /use <swarm> "
                          "to switch swarms, or /help for commands.")


def control_poll_once(swarms: dict, long_poll: int = 0) -> int:
    """One shared getUpdates round across all *swarms*; returns updates handled."""
    if not swarms:
        return 0
    primary = _primary_cfg(swarms)
    if not is_enabled(primary):
        return 0
    params = {"offset": _load_control_offset(), "timeout": long_poll}
    updates = api_call(primary, "getUpdates", params, timeout=long_poll + MIRROR_TIMEOUT_S)
    n = 0
    for upd in updates or []:
        _save_control_offset(int(upd["update_id"]) + 1)
        try:
            _process_control_update(swarms, upd)
        except Exception as exc:  # noqa: BLE001 - one bad update can't kill the loop
            log.log_event(primary, "user", "telegram-error", error=str(exc)[:200])
        n += 1
    return n


class ControlPoller:
    """The shared inbound poller. ``provider()`` returns the live ``{name: cfg}``
    so swarms created/removed while serving are picked up on the next loop."""

    def __init__(self, provider):
        self._provider = provider
        self._stop = threading.Event()
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                control_poll_once(self._provider(), long_poll=LONG_POLL_S)
            except Exception:  # noqa: BLE001 - a transient network error must not kill the loop
                self._stop.wait(3)

    def start(self) -> "ControlPoller":
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


def start_control_poller(provider):
    """Start the shared multi-swarm poller if the shared bot is enabled; else None.

    *provider* is a zero-arg callable returning the current ``{name: cfg}`` set.
    """
    swarms = provider()
    if not swarms or not is_enabled(_primary_cfg(swarms)):
        return None
    return ControlPoller(provider).start()

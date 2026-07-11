#!/usr/bin/env python3
"""Agentainer -- the HTTP control plane / observability UI (P2).

This is the only module in the v2 rewrite that talks HTTP. It is a deliberately
THIN shell over the already-tested core modules (``config``, ``tmux``, ``turn``,
``mail``, ``supervisor``): every handler reads orchestrator state and never
re-implements routing / ACL / busy-tracking / queueing. The model/agent only
reads and writes files; this server only *reports* that state and lets a human
inject mail as the virtual ``user`` mailbox.

Hard invariants (see CLAUDE.md + ProjectPlan.md §24):
  * stdlib ``http.server`` only -- no framework, no build step, no deps.
  * Binds ``127.0.0.1`` by default. A token is REQUIRED for any non-loopback
    bind (see ``_is_loopback`` + the guard in ``run_server``).
  * The UI is a control plane: it can start processes / type into agents, so it
    MUST be bound to loopback unless a token is supplied.
  * Handlers are small and unit-testable so ``lib/ui.py`` can hit 100% coverage
    with mock agents (no real tmux sessions, no API keys).

The set of endpoints:

  GET /                       -> ui/index.html  (token-exempt, loads the page)
  GET /app.js                 -> ui/app.js      (token-exempt static asset)
  GET /api/status             -> swarm + per-agent status (token required)
  GET /api/agents             -> agent list (token required)
  GET /api/agent?agent=       -> one agent's full detail + live status
  GET /api/contacts?agent=    -> mail-app contact list for an agent (unread, last msg)
  GET /api/thread?agent=&peer= -> the full bidirectional thread between two mailboxes
  GET /api/logs?agent=&n=     -> last n JSONL log records (token required)
  GET /api/inbox?agent=       -> current inbox message(s) (token required)
  GET /api/queue?agent=       -> queued message(s) (token required)
  GET /api/pane?agent=        -> terminal snapshot of an agent's tmux pane (token required)
  GET /api/config             -> raw swarm settings + agents (for the editor)
  GET /api/availability       -> the user's receive-mail availability toggle
  POST /api/send              -> body {"to","text"} -> mail.send_as_user
  POST /api/type              -> body {"agent","text"} -> tmux.paste_into (direct pane input)
  POST /api/config            -> body {"swarm": {...}} -> persist swarm settings to YAML
  POST /api/availability      -> body {"available": bool} -> toggle + persist
  POST /api/agent/add         -> body {"name","type","command",...} -> add + persist
  POST /api/agent/edit        -> body {"name","fields": {...}} -> edit + persist
  POST /api/agent/remove      -> body {"name"} -> stop session + remove + persist

Every mutation rewrites ``agentainer.yaml`` (via lib/reconcile's stdlib emitter,
so the no-PyYAML path stays live) and swaps ``UIHandler.cfg`` for the reloaded
config so subsequent requests see the change.

Static assets are token-EXEMPT so the login page can load; every API call and
every POST requires the token (query param ``?token=`` or
``Authorization: Bearer <token>`` header).

Branding: "swarm" is retired -- it's Agentainer everywhere (decision D21).
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# The lib modules import one another by bare name (config, tmux, ...); the test
# harness and CLI put ``lib/`` on ``sys.path``. Make that true for this module
# too, so it is importable standalone (e.g. ``python3 -m lib.ui`` is not needed,
# but ``import ui`` must work from anywhere lib/ is on the path).
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import config  # noqa: E402
import mail  # noqa: E402
import reconcile  # noqa: E402
import telegram  # noqa: E402
import tmux  # noqa: E402
import turn  # noqa: E402

# supervisor is imported lazily inside _api_status so a checkout that lacks it
# (or a test that hides it) degrades gracefully instead of crashing the server.


# The directory holding the static UI assets (index.html, app.js). Defaults to
# the repo's ``ui/`` directory; ``run_server`` accepts an override (used by tests
# to point at a temp dir and exercise the missing-asset branch).
_DEFAULT_UI_DIR = _LIB.parent / "ui"

# Hosts we treat as loopback. Binding any OTHER host requires a token, because
# the UI can start processes and type into agents.
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1")

# Module-global handle to the most recently created server. Lets the foreground
# ``serve`` CLI command (and the foreground branch test) stop a server that is
# blocking in ``serve_forever`` from another thread.
_last_server = None

# Module-global Telegram reply poller (started/stopped from the UI). Only one
# runs per serve process; None when polling is off. ``ThreadingHTTPServer`` hands
# each request to its own thread, so all start/stop/restart of the poller goes
# through ``_tg_lock`` -- otherwise two concurrent poll requests could each see
# ``None`` and start (and leak) a second poller.
_tg_poller = None
_tg_lock = threading.Lock()


def _is_loopback(host: str) -> bool:
    """True iff *host* resolves to the local machine only (safe without a token)."""
    return host in _LOOPBACK_HOSTS


class ServerHandle:
    """A started UI server: know the real port + stop it cleanly.

    ``run_server`` returns one of these. Use ``.port`` (the real bound port, even
    when you passed ``port=0``) and ``.shutdown()`` to stop the background thread.
    """

    def __init__(self, server: ThreadingHTTPServer, port: int, thread):
        self.server = server
        self.port = port
        self.thread = thread

    @property
    def url(self) -> str:
        host = self.server.server_address[0]
        return f"http://{host}:{self.port}"

    def shutdown(self) -> None:
        """Stop the HTTP server and join its background thread (best effort)."""
        global _tg_poller
        with _tg_lock:
            if _tg_poller is not None:
                _tg_poller.stop()
                _tg_poller = None
        self.server.shutdown()
        if self.thread is not None:
            self.thread.join(timeout=5)

    def __enter__(self) -> "ServerHandle":
        return self

    def __exit__(self, *exc) -> None:
        self.shutdown()


class UIHandler(BaseHTTPRequestHandler):
    """Thin request handler over the core modules.

    ``cfg``, ``token`` and ``ui_dir`` are set as class attributes on
    ``UIHandler`` by ``run_server`` before the server starts accepting traffic.
    """

    cfg = None
    token = None
    ui_dir = None
    # Keep the test output quiet; the orchestrator has its own logs.
    protocol_version = "HTTP/1.0"

    # -- low-level responders -------------------------------------------------

    def log_message(self, format, *args):  # noqa: A002 - BaseHTTPRequestHandler API
        # Suppress the default stderr logging; tests assert on responses instead.
        pass

    def _send_json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, code: int, text: str, content_type: str) -> None:
        body = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- auth -----------------------------------------------------------------

    def _token_valid(self) -> bool:
        """Accept the token via ``?token=`` or ``Authorization: Bearer <token>``."""
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if "token" in qs and qs["token"][0] == self.token:
            return True
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            if auth[len("Bearer "):].strip() == self.token:
                return True
        return False

    def _auth_required(self) -> bool:
        """Static assets are token-exempt; everything else needs the token."""
        path = urlparse(self.path).path
        if path in ("/", "/index.html", "/app.js"):
            return False
        return True

    # -- routing --------------------------------------------------------------

    def do_GET(self) -> None:
        if self._auth_required() and not self._token_valid():
            self._send_json(401, {"error": "unauthorized"})
            return
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._serve_static("index.html", "text/html; charset=utf-8")
        elif path == "/app.js":
            self._serve_static("app.js", "text/javascript; charset=utf-8")
        elif path == "/api/status":
            self._api_status()
        elif path == "/api/agents":
            self._api_agents()
        elif path == "/api/agent":
            self._api_agent()
        elif path == "/api/contacts":
            self._api_contacts()
        elif path == "/api/thread":
            self._api_thread()
        elif path == "/api/logs":
            self._api_logs()
        elif path == "/api/inbox":
            self._api_inbox()
        elif path == "/api/queue":
            self._api_queue()
        elif path == "/api/pane":
            self._api_pane()
        elif path == "/api/config":
            self._api_config()
        elif path == "/api/availability":
            self._api_availability()
        elif path == "/api/telegram":
            self._api_telegram()
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length > 0 else b""
        if not self._token_valid():
            self._send_json(401, {"error": "unauthorized"})
            return
        path = urlparse(self.path).path
        if path == "/api/send":
            self._api_send(raw)
        elif path == "/api/type":
            self._api_type(raw)
        elif path == "/api/config":
            self._api_config_post(raw)
        elif path == "/api/availability":
            self._api_availability_post(raw)
        elif path == "/api/agent/add":
            self._api_agent_add(raw)
        elif path == "/api/agent/edit":
            self._api_agent_edit(raw)
        elif path == "/api/agent/remove":
            self._api_agent_remove(raw)
        elif path == "/api/telegram":
            self._api_telegram_post(raw)
        elif path == "/api/telegram/test":
            self._api_telegram_test(raw)
        elif path == "/api/telegram/poll":
            self._api_telegram_poll(raw)
        else:
            self._send_json(404, {"error": "not found"})

    # -- static ---------------------------------------------------------------

    def _serve_static(self, name: str, content_type: str) -> None:
        p = Path(self.ui_dir) / name
        if not p.exists():
            self._send_text(404, "not found", "text/plain")
            return
        self._send_text(200, p.read_text(), content_type)

    # -- API: status ----------------------------------------------------------

    def _supervisor_alive(self):
        """Lazily import supervisor so an absent module degrades to None."""
        try:
            import supervisor as _supervisor  # noqa: F401
        except Exception:
            return None
        return _supervisor.supervisor_alive(self.cfg)

    def _agent_status(self, a) -> dict:
        """The live status row for one agent (shared by /api/status + /api/agent)."""
        cfg = self.cfg
        mp = cfg.mail_paths(a)
        queue_dir = cfg.queue_dir / a.name
        queue_depth = (
            len([f for f in queue_dir.iterdir() if f.is_file()])
            if queue_dir.exists()
            else 0
        )
        inbox_dir = mp.inbox
        unread = (
            len([f for f in inbox_dir.iterdir() if f.is_file()])
            if inbox_dir.exists()
            else 0
        )
        return {
            "name": a.name,
            "type": a.type,
            "running": tmux.session_exists(a.session),
            "busy": turn.busy_info(cfg, a) is not None,
            "queue_depth": queue_depth,
            "unread": unread,
            "can_talk_to": a.can_talk_to,
        }

    def _api_status(self) -> None:
        cfg = self.cfg
        agents = [self._agent_status(a) for a in cfg.agents]
        self._send_json(
            200,
            {
                "name": cfg.name,
                "root": str(cfg.root),
                "user_available": bool(cfg.user_available),
                "supervisor_alive": self._supervisor_alive(),
                "agents": agents,
            },
        )

    # -- API: agents ----------------------------------------------------------

    def _api_agents(self) -> None:
        agents = [
            {"name": a.name, "type": a.type, "can_talk_to": a.can_talk_to}
            for a in self.cfg.agents
        ]
        self._send_json(200, {"agents": agents})

    # -- API: logs ------------------------------------------------------------

    def _api_logs(self) -> None:
        qs = parse_qs(urlparse(self.path).query)
        agent = qs.get("agent", [None])[0]
        try:
            n = int(qs.get("n", ["50"])[0])
        except ValueError:
            n = 50
        if agent:
            logfile = self.cfg.log_dir / f"{agent}.jsonl"
        else:
            logfile = self.cfg.log_dir / "agentainer.jsonl"
        records = []
        if logfile.exists():
            lines = logfile.read_text().splitlines()
            for line in lines[-n:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    records.append({"raw": line})
        self._send_json(200, {"agent": agent, "logs": records})

    # -- API: inbox -----------------------------------------------------------

    def _api_inbox(self) -> None:
        qs = parse_qs(urlparse(self.path).query)
        agent = qs.get("agent", [None])[0]
        if not agent:
            self._send_json(400, {"error": "missing agent"})
            return
        try:
            a = self.cfg.get(agent)
        except Exception:
            self._send_json(404, {"error": "unknown agent"})
            return
        mp = self.cfg.mail_paths(a)
        msgs = []
        if mp.inbox.exists():
            for f in sorted(mp.inbox.iterdir()):
                if f.is_file():
                    msgs.append({"file": f.name, "text": f.read_text()})
        self._send_json(200, {"agent": agent, "inbox": msgs})

    # -- API: queue -----------------------------------------------------------

    def _api_queue(self) -> None:
        qs = parse_qs(urlparse(self.path).query)
        agent = qs.get("agent", [None])[0]
        if not agent:
            self._send_json(400, {"error": "missing agent"})
            return
        try:
            self.cfg.get(agent)
        except Exception:
            self._send_json(404, {"error": "unknown agent"})
            return
        queue_dir = self.cfg.queue_dir / agent
        msgs = []
        if queue_dir.exists():
            for f in sorted(queue_dir.iterdir()):
                if f.is_file():
                    text = f.read_text()
                    first = text.splitlines()[0] if text.strip() else ""
                    msgs.append({"file": f.name, "text": first})
        self._send_json(200, {"agent": agent, "queue": msgs})

    # -- API: pane (terminal snapshot) ---------------------------------------

    def _api_pane(self) -> None:
        qs = parse_qs(urlparse(self.path).query)
        agent = qs.get("agent", [None])[0]
        if not agent:
            self._send_json(400, {"error": "missing agent"})
            return
        try:
            a = self.cfg.get(agent)
        except Exception:
            self._send_json(404, {"error": "unknown agent"})
            return
        # capture_pane returns "" when the session is down / tmux errors, so the
        # UI just renders an empty pane rather than failing the request.
        self._send_json(200, {"agent": agent, "pane": tmux.capture_pane(self.cfg, a)})

    # -- API: agent detail ----------------------------------------------------

    def _api_agent(self) -> None:
        qs = parse_qs(urlparse(self.path).query)
        name = qs.get("agent", [None])[0]
        if not name:
            self._send_json(400, {"error": "missing agent"})
            return
        try:
            a = self.cfg.get(name)
        except Exception:
            self._send_json(404, {"error": "unknown agent"})
            return
        d = self._agent_status(a)
        d.update(
            {
                "command": a.command,
                "role": a.role,
                "workdir": str(a.workdir),
                "capture": a.capture,
                "session": a.session,
                "periodically_ping_seconds": a.periodically_ping_seconds,
                "periodically_ping_message": a.periodically_ping_message,
            }
        )
        self._send_json(200, {"agent": d})

    # -- mail-app helpers (thread reconstruction) -----------------------------

    def _parse_msg(self, text: str) -> dict:
        """Parse a stamped mail file into ``{from,to,id,time,body}``.

        The model never writes headers -- the orchestrator stamps every routed
        message (``mail.stamp_message``), so every *received* copy carries them.
        """
        parts = text.split("\n\n", 1)
        body = parts[1] if len(parts) > 1 else text
        return {
            "from": mail._parse_header_field(text, "From"),
            "to": mail._parse_header_field(text, "To"),
            "id": mail._parse_header_field(text, "Id"),
            "time": mail._parse_header_field(text, "Time"),
            "body": body,
        }

    def _incoming_dirs(self, name: str) -> list:
        """``(dir, status)`` pairs holding STAMPED messages *received* by ``name``.

        Every routed message lands, stamped, in its recipient's queue/inbox/read
        (and, if force-archived, the archive), and the folder it currently sits in
        IS its delivery status: ``queued`` (waiting to be released) -> ``delivered``
        (presented, unread) -> ``read`` (done). ``user`` is virtual -- messages to
        it accumulate in its queue, which is effectively its inbox, so we label
        those ``delivered``. ``system`` never receives. Scanning the recipient side
        of BOTH parties reconstructs a full bidirectional thread with bodies (the
        sender's ``sent/`` copy is unstamped, so we ignore it).
        """
        cfg = self.cfg
        if name == "user":
            return [(cfg.queue_dir / "user", "delivered")]
        if name == "system":
            return []
        try:
            a = cfg.get(name)
        except Exception:
            return []
        mp = cfg.mail_paths(a)
        return [
            (cfg.queue_dir / name, "queued"),
            (mp.inbox, "delivered"),
            (mp.read, "read"),
            (cfg.runtime / "archive" / name, "archived"),
        ]

    def _collect_thread(self, a_name: str, b_name: str) -> list:
        """Every message exchanged between ``a_name`` and ``b_name``, time-sorted.

        Deduped by message Id (the same id appears once per recipient copy).
        ``direction`` is relative to ``a_name`` (``out`` = a->b, ``in`` = b->a);
        ``status`` is where the message currently sits (queued/delivered/read).
        """
        seen: dict = {}
        for d, status in self._incoming_dirs(a_name) + self._incoming_dirs(b_name):
            if not d.exists():
                continue
            for f in sorted(d.iterdir()):
                if not f.is_file() or f.name == "about.md":
                    continue
                try:
                    text = f.read_text()
                except OSError:  # pragma: no cover - defensive only
                    continue
                m = self._parse_msg(text)
                frm, to = m["from"], m["to"]
                if frm is None or to is None or {frm, to} != {a_name, b_name}:
                    continue
                m["direction"] = "out" if frm == a_name else "in"
                m["status"] = status
                key = m["id"] or f"{d}/{f.name}"
                seen.setdefault(key, m)
        return sorted(seen.values(), key=lambda m: (m["time"] or "", m["id"] or ""))

    def _unread_from(self, agent_name: str, peer: str) -> int:
        """How many messages from *peer* still sit unread (inbox/queue) for *agent*."""
        cfg = self.cfg
        try:
            a = cfg.get(agent_name)
        except Exception:  # pragma: no cover - callers pass known agents
            return 0
        mp = cfg.mail_paths(a)
        n = 0
        for d in (mp.inbox, cfg.queue_dir / agent_name):
            if not d.exists():
                continue
            for f in d.iterdir():
                if f.is_file() and f.name != "about.md":
                    if self._parse_msg(f.read_text())["from"] == peer:
                        n += 1
        return n

    # -- API: contacts (the mail-app folder list) -----------------------------

    def _api_contacts(self) -> None:
        qs = parse_qs(urlparse(self.path).query)
        name = qs.get("agent", [None])[0]
        if not name:
            self._send_json(400, {"error": "missing agent"})
            return
        try:
            a = self.cfg.get(name)
        except Exception:
            self._send_json(404, {"error": "unknown agent"})
            return
        # Candidates: the ACL (agents + maybe user), system (always reachable to
        # the agent), and anyone already seen in the agent's received mail.
        names = set(a.can_talk_to)
        names.add("system")
        names.discard(name)
        for d, _status in self._incoming_dirs(name):
            if not d.exists():
                continue
            for f in d.iterdir():
                if not f.is_file() or f.name == "about.md":
                    continue
                m = self._parse_msg(f.read_text())
                for who in (m["from"], m["to"]):
                    if who and who != name:
                        names.add(who)
        contacts = []
        for n in sorted(names):
            thread = self._collect_thread(name, n)
            last = thread[-1] if thread else None
            preview = ""
            if last and last["body"].strip():
                preview = last["body"].strip().splitlines()[0][:80]
            contacts.append(
                {
                    "name": n,
                    "kind": "user" if n == "user" else "system" if n == "system" else "agent",
                    "count": len(thread),
                    "unread": self._unread_from(name, n),
                    "last_time": last["time"] if last else None,
                    "last_preview": preview,
                }
            )
        self._send_json(200, {"agent": name, "contacts": contacts})

    # -- API: thread ----------------------------------------------------------

    def _api_thread(self) -> None:
        qs = parse_qs(urlparse(self.path).query)
        name = qs.get("agent", [None])[0]
        peer = qs.get("peer", [None])[0]
        if not name or not peer:
            self._send_json(400, {"error": "missing agent/peer"})
            return
        try:
            self.cfg.get(name)
        except Exception:
            self._send_json(404, {"error": "unknown agent"})
            return
        if peer not in ("user", "system") and peer not in self.cfg.names():
            self._send_json(404, {"error": "unknown peer"})
            return
        self._send_json(
            200,
            {"agent": name, "peer": peer, "messages": self._collect_thread(name, peer)},
        )

    # -- API: config (raw settings + agents for the editor) -------------------

    def _api_config(self) -> None:
        raw = reconcile.load_raw(self.cfg.path)
        self._send_json(
            200,
            {
                "path": str(self.cfg.path),
                "swarm": raw.get("swarm") or {},
                "defaults": raw.get("defaults") or {},
                "agents": raw.get("agents") or [],
                "user_available": bool(self.cfg.user_available),
                "warnings": list(self.cfg.warnings),
            },
        )

    def _api_availability(self) -> None:
        self._send_json(200, {"available": bool(self.cfg.user_available)})

    # -- API: send ------------------------------------------------------------

    def _api_send(self, raw: bytes) -> None:
        try:
            data = json.loads(raw.decode()) if raw else {}
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid json"})
            return
        to = data.get("to")
        text = data.get("text")
        if not to or not isinstance(text, str) or text == "":
            self._send_json(400, {"error": "missing to/text"})
            return
        try:
            mail.send_as_user(self.cfg, to, text)
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(200, {"ok": True, "to": to})

    # -- POST helpers ---------------------------------------------------------

    def _json_body(self, raw: bytes):
        """Decode a JSON request body; on error send 400 and return ``None``."""
        try:
            return json.loads(raw.decode()) if raw else {}
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid json"})
            return None

    # -- API: type (direct pane input, bypasses the mailroom) -----------------

    def _api_type(self, raw: bytes) -> None:
        data = self._json_body(raw)
        if data is None:
            return
        to = data.get("agent") or data.get("to")
        text = data.get("text")
        if not to or not isinstance(text, str) or text == "":
            self._send_json(400, {"error": "missing agent/text"})
            return
        try:
            a = self.cfg.get(to)
        except Exception:
            self._send_json(404, {"error": "unknown agent"})
            return
        try:
            ok = tmux.paste_into(self.cfg, a.session, text)
        except tmux.SwarmError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(200, {"ok": bool(ok), "agent": to})

    # -- API: config (persist swarm settings) ---------------------------------

    def _api_config_post(self, raw: bytes) -> None:
        data = self._json_body(raw)
        if data is None:
            return
        settings = data.get("swarm")
        if not isinstance(settings, dict) or not settings:
            self._send_json(400, {"error": "missing swarm settings"})
            return
        try:
            new_cfg = reconcile.edit_swarm(self.cfg, **settings)
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})
            return
        UIHandler.cfg = new_cfg
        self._send_json(200, {"ok": True, "swarm": reconcile.load_raw(new_cfg.path).get("swarm") or {}})

    # -- API: availability (toggle + persist) ---------------------------------

    def _api_availability_post(self, raw: bytes) -> None:
        data = self._json_body(raw)
        if data is None:
            return
        val = data.get("available")
        if not isinstance(val, bool):
            self._send_json(400, {"error": "missing available (bool)"})
            return
        # A validated bool always round-trips through the loader, so this never
        # produces an invalid config -- no error branch to guard.
        new_cfg = reconcile.edit_swarm(self.cfg, user_available=val)
        mail.set_user_available(new_cfg, val)
        UIHandler.cfg = new_cfg
        self._send_json(200, {"ok": True, "available": val})

    # -- API: agent add / edit / remove ---------------------------------------

    def _api_agent_add(self, raw: bytes) -> None:
        data = self._json_body(raw)
        if data is None:
            return
        name = data.get("name")
        command = data.get("command")
        if not name or not command:
            self._send_json(400, {"error": "missing name/command"})
            return
        can = data.get("can_talk_to")
        if can is None:
            can = []
        extra = {}
        for k in ("capture", "boot_delay_ms", "periodically_ping_seconds",
                  "periodically_ping_message"):
            if data.get(k) not in (None, ""):
                extra[k] = data[k]
        try:
            new_cfg = reconcile.add_agent(
                self.cfg,
                name,
                data.get("type") or "claude",
                command,
                can,
                role=data.get("role") or "",
                workdir=data.get("workdir"),
                **extra,
            )
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})
            return
        UIHandler.cfg = new_cfg
        mail.init_mailboxes(new_cfg)
        self._send_json(200, {"ok": True, "name": name})

    def _api_agent_edit(self, raw: bytes) -> None:
        data = self._json_body(raw)
        if data is None:
            return
        name = data.get("name")
        fields = data.get("fields")
        if not name or not isinstance(fields, dict) or not fields:
            self._send_json(400, {"error": "missing name/fields"})
            return
        # reconcile.edit_agent coerces each value from its str form; a list
        # can_talk_to must arrive as a comma string, or ``*`` for the wildcard.
        clean = {}
        for k, v in fields.items():
            clean[k] = ",".join(v) if k == "can_talk_to" and isinstance(v, list) else v
        try:
            new_cfg = reconcile.edit_agent(self.cfg, name, **clean)
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})
            return
        UIHandler.cfg = new_cfg
        mail.init_mailboxes(new_cfg)
        self._send_json(200, {"ok": True, "name": name})

    def _api_agent_remove(self, raw: bytes) -> None:
        data = self._json_body(raw)
        if data is None:
            return
        name = data.get("name")
        if not name:
            self._send_json(400, {"error": "missing name"})
            return
        if name not in self.cfg.names():
            self._send_json(404, {"error": "unknown agent"})
            return
        # Stop the session first so it isn't orphaned, then drop it from config.
        a = self.cfg.get(name)
        if tmux.session_exists(a.session):
            tmux.tmux("kill-session", "-t", f"={a.session}", check=False, capture=True)
        try:
            # Removing an agent that a peer still lists in can_talk_to leaves the
            # reloaded config invalid (dangling reference) -- surface that as 400.
            new_cfg = reconcile.remove_agent(self.cfg, name)
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})
            return
        UIHandler.cfg = new_cfg
        self._send_json(200, {"ok": True, "name": name})

    # -- API: telegram bridge -------------------------------------------------

    def _api_telegram(self) -> None:
        """Report the Telegram config (never the raw token) + poller state."""
        tg = self.cfg.telegram
        self._send_json(
            200,
            {
                "enabled": bool(tg.enabled),
                "has_token": bool(tg.bot_token),
                "chat_id": tg.chat_id,
                "mirror": tg.mirror,
                "mirror_user": bool(tg.mirror_user),
                "mirror_system": bool(tg.mirror_system),
                "polling": _tg_poller is not None,
                "agents": self.cfg.names(),
            },
        )

    def _api_telegram_post(self, raw: bytes) -> None:
        data = self._json_body(raw)
        if data is None:
            return
        fields = {}
        if "enabled" in data:
            fields["enabled"] = bool(data["enabled"])
        # Only overwrite the token when a fresh non-empty one is supplied, so the
        # editor can leave it blank to keep the stored secret.
        if data.get("bot_token"):
            fields["bot_token"] = str(data["bot_token"])
        if "chat_id" in data:
            fields["chat_id"] = str(data["chat_id"])
        if "mirror" in data:
            fields["mirror"] = data["mirror"]
        if "mirror_user" in data:
            fields["mirror_user"] = bool(data["mirror_user"])
        if "mirror_system" in data:
            fields["mirror_system"] = bool(data["mirror_system"])
        if not fields:
            self._send_json(400, {"error": "no telegram settings given"})
            return
        try:
            new_cfg = reconcile.edit_telegram(self.cfg, **fields)
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})
            return
        UIHandler.cfg = new_cfg
        # If a reply poller is running, restart it so it picks up the new config.
        global _tg_poller
        with _tg_lock:
            if _tg_poller is not None:
                _tg_poller.stop()
                _tg_poller = telegram.start_poller(new_cfg)
            polling = _tg_poller is not None
        self._send_json(
            200,
            {"ok": True, "enabled": bool(new_cfg.telegram.enabled),
             "has_token": bool(new_cfg.telegram.bot_token), "polling": polling},
        )

    def _api_telegram_test(self, raw: bytes) -> None:
        data = self._json_body(raw)
        if data is None:
            return
        if not telegram.is_enabled(self.cfg):
            self._send_json(400, {"error": "telegram is not enabled / not fully configured"})
            return
        try:
            telegram.send_message(self.cfg, data.get("text") or "✅ Agentainer test message")
        except telegram.TelegramError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(200, {"ok": True})

    def _api_telegram_poll(self, raw: bytes) -> None:
        data = self._json_body(raw)
        if data is None:
            return
        global _tg_poller
        run = bool(data.get("run"))
        if run and not telegram.is_enabled(self.cfg):
            self._send_json(400, {"error": "telegram is not enabled / not fully configured"})
            return
        with _tg_lock:
            if run:
                if _tg_poller is None:
                    _tg_poller = telegram.start_poller(self.cfg)
            elif _tg_poller is not None:
                _tg_poller.stop()
                _tg_poller = None
            polling = _tg_poller is not None
        self._send_json(200, {"ok": True, "polling": polling})


def run_server(
    cfg,
    token: str,
    host: str = "127.0.0.1",
    port: int = 0,
    background: bool = True,
    ui_dir=None,
):
    """Bind and serve the Agentainer UI control plane.

    Args:
      cfg:       a loaded ``SwarmConfig`` (the source of truth for state).
      token:     the auth token required for every API call / POST. May be empty
                 ONLY when *host* is loopback (127.0.0.1 / localhost / ::1).
      host:      bind interface. Defaults to ``127.0.0.1``. NEVER ``0.0.0.0``
                 without a token -- a non-loopback bind with an empty token raises
                 ``ValueError``.
      port:      port to bind. ``0`` (default) lets the OS pick a free port; the
                 real port is reported on the returned ``ServerHandle.port``.
      background: if True (default), serve in a daemon thread and return a
                 ``ServerHandle`` immediately (call ``.shutdown()`` to stop). If
                 False, block in ``serve_forever()`` and return the handle only
                 after the server is stopped (use ``ui._last_server.shutdown()``
                 from another thread, or the returned handle once it returns).
      ui_dir:    override the static-asset directory (defaults to ``<repo>/ui``).
                 Mainly for testing.

    Returns:
      ServerHandle with ``.port`` (real bound port) and ``.shutdown()``.
    """
    if not _is_loopback(host) and not token:
        raise ValueError("a token is required to bind to a non-loopback host")

    ui_path = Path(ui_dir) if ui_dir is not None else _DEFAULT_UI_DIR
    UIHandler.cfg = cfg
    UIHandler.token = token
    UIHandler.ui_dir = ui_path

    server = ThreadingHTTPServer((host, port), UIHandler)
    real_port = server.server_address[1]

    global _last_server
    _last_server = server

    handle = ServerHandle(server, real_port, None)
    if background:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        handle.thread = thread
        return handle

    # Foreground: block until shutdown() is called from another thread, then
    # fall through and return the handle.
    server.serve_forever()
    return handle

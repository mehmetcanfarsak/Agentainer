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
  GET /api/logs?agent=&n=     -> last n JSONL log records (token required)
  GET /api/inbox?agent=       -> current inbox message(s) (token required)
  GET /api/queue?agent=       -> queued message(s) (token required)
  GET /api/pane?agent=        -> terminal snapshot of an agent's tmux pane (token required)
  POST /api/send              -> body {"to","text"} -> mail.send_as_user

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
        elif path == "/api/logs":
            self._api_logs()
        elif path == "/api/inbox":
            self._api_inbox()
        elif path == "/api/queue":
            self._api_queue()
        elif path == "/api/pane":
            self._api_pane()
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length > 0 else b""
        if not self._token_valid():
            self._send_json(401, {"error": "unauthorized"})
            return
        if urlparse(self.path).path == "/api/send":
            self._api_send(raw)
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

    def _api_status(self) -> None:
        cfg = self.cfg
        agents = []
        for a in cfg.agents:
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
            agents.append(
                {
                    "name": a.name,
                    "type": a.type,
                    "running": tmux.session_exists(a.session),
                    "busy": turn.busy_info(cfg, a) is not None,
                    "queue_depth": queue_depth,
                    "unread": unread,
                    "can_talk_to": a.can_talk_to,
                }
            )
        self._send_json(
            200,
            {
                "name": cfg.name,
                "root": str(cfg.root),
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

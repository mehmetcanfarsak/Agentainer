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
  GET /api/templates          -> bundled example swarms (onboarding, empty swarm)
  GET /api/examples           -> bundled example swarms + full raw YAML (edit-before-create)
  GET /api/rate?window=       -> opt-in per-agent messages/min over last `window` min
  POST /api/send              -> body {"to","text"} -> mail.send_as_user
  POST /api/type              -> body {"agent","text"} -> tmux.paste_into (direct pane input)
  POST /api/key               -> body {"agent","key"} -> tmux.send_key (Escape, C-c, ...)
  POST /api/up                -> body {"agent"} -> reconcile.start_one (launch if down)
  POST /api/down              -> body {"agent"} -> reconcile.stop_one (kill session if up)
  POST /api/up_all            -> reconcile.start_all (launch every down agent; no body)
  POST /api/down_all          -> reconcile.stop_all (kill every running session; no body)
  POST /api/config            -> body {"swarm": {...}} -> persist swarm settings to YAML
  POST /api/availability      -> body {"available": bool} -> toggle + persist
  POST /api/agent/add         -> body {"name","type","command",...} -> add + persist
  POST /api/agent/edit        -> body {"name","fields": {...}} -> edit + persist
  POST /api/agent/remove      -> body {"name"} -> stop session + remove + persist
  POST /api/templates/apply   -> body {"name"} -> seed an empty swarm from a template
  POST /api/swarms/build      -> body {"name","agent_type"?,"command"?,"mode"?,"notes"?} -> open builder session
  POST /api/swarms/approve    -> body {"name"} -> validate the built config + up the swarm

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
from types import SimpleNamespace
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
import mcp  # noqa: E402
import reconcile  # noqa: E402
import registry  # noqa: E402
import scaffold  # noqa: E402
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


def _parse_raw_yaml(text: str) -> dict:
    """Parse an operator-edited YAML string into a config dict.

    Routed through ``reconcile.load_raw`` (a temp file) so the same PyYAML-or-
    minyaml reader as everywhere else is used -- keeping the no-PyYAML path live.
    Raises ``ValueError`` when the text does not parse to a mapping.
    """
    import os
    import tempfile

    fd, tmp = tempfile.mkstemp(suffix=".yaml")
    try:
        os.write(fd, text.encode())
        os.close(fd)
        data = reconcile.load_raw(tmp)
    finally:
        os.unlink(tmp)
    if not isinstance(data, dict):
        raise ValueError("YAML did not parse to a mapping")
    return data


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


class _SwarmNotFound(Exception):
    """A request named a ``?swarm=`` that isn't in the live control-plane set."""


class UIHandler(BaseHTTPRequestHandler):
    """Thin request handler over the core modules.

    One ``serve`` process is a **multi-swarm** control plane: ``swarms`` maps
    ``name -> SwarmConfig`` for every swarm this server manages, and each
    per-swarm request selects one via ``?swarm=<name>`` (falling back to
    ``default_swarm`` when omitted -- which keeps the single-swarm
    ``serve -c one.yaml`` behaviour). ``token`` and ``ui_dir`` are server-wide.
    ``run_server`` sets these class attributes before traffic starts.

    ``registry_backed`` is True when the live set is synced from the global
    ``lib/registry`` store (the whole-machine ``serve``); False for a server
    pinned to a single explicit config (back-compat / tests).
    """

    swarms: dict = {}
    default_swarm = None
    registry_backed = False
    token = None
    ui_dir = None
    # Keep the test output quiet; the orchestrator has its own logs.
    protocol_version = "HTTP/1.0"

    # -- swarm resolution (multi-swarm selector layer) ------------------------

    def _query_get(self, key: str):
        vals = parse_qs(urlparse(self.path).query).get(key)
        return vals[0] if vals else None

    def _swarm_key(self):
        """The swarm name this request targets: ``?swarm=`` or the default."""
        name = self._query_get("swarm")
        return type(self).default_swarm if name is None else name

    @property
    def cfg(self):
        """The ``SwarmConfig`` this request targets (raises ``_SwarmNotFound``).

        Every per-swarm handler reads ``self.cfg``; the selector is invisible to
        them. A 404 is produced centrally in ``do_GET``/``do_POST`` when the name
        is unknown.
        """
        key = self._swarm_key()
        swarms = type(self).swarms
        if key is not None and key in swarms:
            return swarms[key]
        raise _SwarmNotFound(key)

    def _set_cfg(self, new_cfg) -> None:
        """Write a mutated config back into the live set (handles a renamed swarm)."""
        cls = type(self)
        key = self._swarm_key()
        if key is not None:
            cls.swarms.pop(key, None)
        cls.swarms[new_cfg.name] = new_cfg
        if cls.default_swarm == key:
            cls.default_swarm = new_cfg.name

    def _refresh_swarms(self) -> dict:
        """Re-sync the live set from the global registry (when registry-backed).

        Config mutations commit to disk via ``reconcile``, so re-reading the
        registry keeps the dashboard authoritative and picks up swarms created or
        removed since the server started. A no-op for a single-config server.
        """
        cls = type(self)
        if cls.registry_backed:
            cls.swarms = registry.load_all()
            if cls.default_swarm not in cls.swarms:
                cls.default_swarm = next(iter(cls.swarms), None)
        return cls.swarms

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
        try:
            self._dispatch_get()
        except _SwarmNotFound as exc:
            self._send_json(404, {"error": f"unknown swarm: {exc.args[0]!r}"})

    def _dispatch_get(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._serve_static("index.html", "text/html; charset=utf-8")
        elif path == "/app.js":
            self._serve_static("app.js", "text/javascript; charset=utf-8")
        elif path == "/mcp":
            # Streamable-HTTP MCP is POST-only here (no server->client stream).
            self.send_response(405)
            self.send_header("Allow", "POST")
            self.end_headers()
        elif path == "/api/swarms":
            self._api_swarms()
        elif path == "/api/settings":
            self._api_settings()
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
        elif path == "/api/templates":
            self._api_templates()
        elif path == "/api/examples":
            self._api_examples()
        elif path == "/api/rate":
            self._api_rate()
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length > 0 else b""
        if not self._token_valid():
            self._send_json(401, {"error": "unauthorized"})
            return
        try:
            self._dispatch_post(raw)
        except _SwarmNotFound as exc:
            self._send_json(404, {"error": f"unknown swarm: {exc.args[0]!r}"})

    def _dispatch_post(self, raw: bytes) -> None:
        path = urlparse(self.path).path
        if path == "/mcp":
            self._api_mcp(raw)
        elif path == "/api/swarms/create":
            self._api_swarms_create(raw)
        elif path == "/api/swarms/up":
            self._api_swarms_up(raw)
        elif path == "/api/swarms/down":
            self._api_swarms_down(raw)
        elif path == "/api/swarms/register":
            self._api_swarms_register(raw)
        elif path == "/api/swarms/remove":
            self._api_swarms_remove(raw)
        elif path == "/api/swarms/build":
            self._api_swarms_build(raw)
        elif path == "/api/swarms/approve":
            self._api_swarms_approve(raw)
        elif path == "/api/settings":
            self._api_settings_post(raw)
        elif path == "/api/send":
            self._api_send(raw)
        elif path == "/api/type":
            self._api_type(raw)
        elif path == "/api/key":
            self._api_key(raw)
        elif path == "/api/up":
            self._api_up(raw)
        elif path == "/api/down":
            self._api_down(raw)
        elif path == "/api/up_all":
            self._api_up_all(raw)
        elif path == "/api/down_all":
            self._api_down_all(raw)
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
        elif path == "/api/templates/apply":
            self._api_templates_apply(raw)
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

    def _supervisor_module(self):
        """Import supervisor lazily; None if absent (a partially wired checkout)."""
        try:
            import supervisor as _supervisor
        except Exception:
            return None
        return _supervisor

    def _supervisor_alive(self, cfg=None):
        """Lazily import supervisor so an absent module degrades to None."""
        sup = self._supervisor_module()
        if sup is None:
            return None
        return sup.supervisor_alive(cfg if cfg is not None else self.cfg)

    def _swarm_summary(self, cfg) -> dict:
        """One dashboard row for *cfg* (running/total agents + attention)."""
        running = sum(1 for a in cfg.agents if tmux.session_exists(a.session))
        udir = cfg.queue_dir / "user"
        attention = (
            len([f for f in udir.iterdir() if f.is_file()]) if udir.exists() else 0
        )
        return {
            "name": cfg.name,
            "path": str(cfg.path),
            "root": str(cfg.root),
            "total": len(cfg.agents),
            "running": running,
            "attention": attention,
            "supervisor_alive": self._supervisor_alive(cfg),
        }

    # -- API: swarms (multi-swarm control plane) ------------------------------

    def _api_swarms(self) -> None:
        """Dashboard of every swarm this control plane manages."""
        swarms = self._refresh_swarms()
        out = sorted(
            (self._swarm_summary(cfg) for cfg in swarms.values()),
            key=lambda s: s["name"],
        )
        self._send_json(200, {"swarms": out, "default": type(self).default_swarm})

    def _resolve_swarm(self, data):
        """Look up the swarm named in a POST body; None (after 404) if unknown."""
        name = data.get("name") or data.get("swarm")
        swarms = self._refresh_swarms()
        if not name or name not in swarms:
            self._send_json(404, {"error": "unknown swarm"})
            return None, None
        return name, swarms[name]

    def _api_swarms_create(self, raw: bytes) -> None:
        data = self._json_body(raw)
        if data is None:
            return
        name = (data.get("name") or "").strip()
        if not name:
            self._send_json(400, {"error": "missing name"})
            return
        tpl = data.get("template") or None
        raw_cfg = data.get("raw") if isinstance(data.get("raw"), dict) else None
        # The UI may hand back a YAML *string* the operator edited inline; parse it
        # (via reconcile's stdlib-or-PyYAML reader) into a dict and treat it as raw.
        raw_yaml = data.get("raw_yaml")
        if raw_cfg is None and isinstance(raw_yaml, str) and raw_yaml.strip():
            try:
                raw_cfg = _parse_raw_yaml(raw_yaml)
            except Exception as exc:
                self._send_json(400, {"error": f"invalid YAML: {exc}"})
                return
        try:
            path = registry.create_swarm(
                name, root=data.get("root") or None, template=tpl, raw=raw_cfg
            )
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._refresh_swarms()
        self._send_json(200, {"ok": True, "name": name, "path": str(path)})

    def _api_swarms_up(self, raw: bytes) -> None:
        data = self._json_body(raw)
        if data is None:
            return
        name, cfg = self._resolve_swarm(data)
        if cfg is None:
            return
        try:
            import cli  # lazy: cli imports ui, so import it only at call time
            started = cli.up_config(cfg)
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._refresh_swarms()
        self._send_json(200, {"ok": True, "name": name, "started": [a.name for a in started]})

    def _api_swarms_down(self, raw: bytes) -> None:
        data = self._json_body(raw)
        if data is None:
            return
        name, cfg = self._resolve_swarm(data)
        if cfg is None:
            return
        stopped = reconcile.stop_all(cfg)
        sup = self._supervisor_module()
        if sup is not None:
            sup.stop_supervisor(cfg)
        self._send_json(200, {"ok": True, "name": name, "stopped": stopped})

    def _api_swarms_register(self, raw: bytes) -> None:
        data = self._json_body(raw)
        if data is None:
            return
        path = data.get("path")
        if not path:
            self._send_json(400, {"error": "missing path"})
            return
        try:
            cfg = config.load(path)  # validate before registering
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})
            return
        registry.register(cfg.name, cfg.path)
        self._refresh_swarms()
        self._send_json(200, {"ok": True, "name": cfg.name, "path": str(cfg.path)})

    def _api_swarms_remove(self, raw: bytes) -> None:
        """Forget a swarm (registry only; its config files are left on disk)."""
        data = self._json_body(raw)
        if data is None:
            return
        name = data.get("name") or data.get("swarm")
        if not name:
            self._send_json(400, {"error": "missing name"})
            return
        removed = registry.unregister(name)
        self._refresh_swarms()
        self._send_json(200, {"ok": True, "name": name, "removed": removed})

    # -- API: interactive builder (create a swarm by talking to an agent) ------

    def _api_swarms_build(self, raw: bytes) -> None:
        """Open the interactive builder session for a swarm (returns its tmux id)."""
        data = self._json_body(raw)
        if data is None:
            return
        name, cfg = self._resolve_swarm(data)
        if cfg is None:
            return
        atype = data.get("agent_type")
        command = data.get("command")
        # Let the operator pass a real alias directly; else fall back to the
        # builtin default for the chosen type (resolved inside open_builder_session).
        try:
            session = scaffold.open_builder_session(
                cfg,
                agent_type=atype,
                agent_command=command,
                mode=data.get("mode") or "adapt",
                notes=data.get("notes") or "",
            )
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(200, {"ok": True, "name": name, "session": session})

    def _api_swarms_approve(self, raw: bytes) -> None:
        """Validate a built swarm's config, close the builder, and ``up`` it."""
        data = self._json_body(raw)
        if data is None:
            return
        name = data.get("name") or data.get("swarm")
        if not name:
            self._send_json(400, {"error": "missing name"})
            return
        result = scaffold.approve_swarm(name)
        self._refresh_swarms()
        self._send_json(200 if result.get("ok") else 400, result)

    # -- API: global settings (shared Telegram etc.) --------------------------

    def _telegram_settings_view(self) -> dict:
        """The shared Telegram settings, minus the secret bot token."""
        tg = registry.global_telegram()
        configured = bool(tg.get("bot_token") and tg.get("chat_id"))
        view = {k: v for k, v in tg.items() if k != "bot_token"}
        view["has_token"] = bool(tg.get("bot_token"))
        return {
            "telegram": view,
            "telegram_configured": configured,
            "telegram_enabled": bool(tg.get("enabled")) and configured,
            "active_swarm": registry.active_swarm(),
        }

    def _api_settings(self) -> None:
        self._send_json(200, self._telegram_settings_view())

    def _api_settings_post(self, raw: bytes) -> None:
        data = self._json_body(raw)
        if data is None:
            return
        tg = data.get("telegram")
        if isinstance(tg, dict):
            registry.set_global_telegram(**tg)
        active = data.get("active_swarm")
        if active is not None:
            registry.set_active_swarm(active)
        self._send_json(200, dict(self._telegram_settings_view(), ok=True))

    def _pending_user_senders(self) -> set:
        """Names of agents whose mail to the ``user`` is still awaiting a reply.

        User-directed mail is enqueued into ``queue_dir/user`` and sits there
        until the operator reads it, so an agent named in that queue is one that
        is waiting on *you* -- the signal behind the ``attention`` state.
        """
        senders: set = set()
        udir = self.cfg.queue_dir / "user"
        if udir.exists():
            for f in udir.iterdir():
                if f.is_file():
                    senders.add(self._parse_msg(f.read_text())["from"])
        return senders

    def _agent_state(self, a, running: bool, busy_state, pending: set):
        """Collapse the raw signals into one truthful state + its working age.

        Priority: stopped > working > stalled > attention > waiting. ``stalled``
        is the anomaly ``busy_info`` hides -- a turn that has looked busy past
        ``busy_timeout_ms`` (the completion signal was lost), which would
        otherwise silently read as idle.
        """
        if not running:
            return "stopped", 0
        if busy_state is not None:
            return "working", int(busy_state.get("age_s", 0))
        if a.busy_check:
            st = turn.turn_state(self.cfg, a.name)
            if st.get("delivered", 0) > st.get("completed", 0):
                return "stalled", 0
        if a.name in pending:
            return "attention", 0
        return "waiting", 0

    def _agent_status(self, a, pending=None) -> dict:
        """The live status row for one agent (shared by /api/status + /api/agent)."""
        cfg = self.cfg
        if pending is None:
            pending = self._pending_user_senders()
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
        running = tmux.session_exists(a.session)
        busy_state = turn.busy_info(cfg, a)
        state, working_s = self._agent_state(a, running, busy_state, pending)
        return {
            "name": a.name,
            "type": a.type,
            "running": running,
            "busy": busy_state is not None,
            "state": state,
            "working_s": working_s,
            "awaiting_user": a.name in pending,
            "queue_depth": queue_depth,
            "unread": unread,
            "can_talk_to": a.can_talk_to,
        }

    def _api_status(self) -> None:
        cfg = self.cfg
        pending = self._pending_user_senders()
        agents = [self._agent_status(a, pending) for a in cfg.agents]
        udir = cfg.queue_dir / "user"
        attention = (
            len([f for f in udir.iterdir() if f.is_file()]) if udir.exists() else 0
        )
        self._send_json(
            200,
            {
                "name": cfg.name,
                "root": str(cfg.root),
                "user_available": bool(cfg.user_available),
                "supervisor_alive": self._supervisor_alive(),
                "attention": attention,
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
        msgs = []
        # FIFO (enqueue) order -- the order these will actually be delivered --
        # not random message-id filename order (see mail.queued_files).
        for f in mail.queued_files(self.cfg, agent):
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
        if agent == "builder":
            # The interactive swarm builder runs in its own session, not a
            # configured agent; capture_pane only reads ``.session``.
            probe = SimpleNamespace(session=scaffold.builder_session_name(self.cfg))
            self._send_json(200, {"agent": agent, "pane": tmux.capture_pane(self.cfg, probe)})
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

    # -- MCP (Model Context Protocol) transport -------------------------------

    def _mcp_swarms(self) -> dict:
        """The swarm set exposed to MCP: the live registry (whole-machine serve)
        or this server's single pinned config (``serve -c`` / tests)."""
        if type(self).registry_backed:
            return registry.load_all()
        return dict(type(self).swarms)

    def _api_mcp(self, raw: bytes) -> None:
        """``POST /mcp`` -- one JSON-RPC message in, one response out.

        Reuses the Bearer-token auth every other API call uses (checked in
        ``do_POST`` before dispatch). A notification (no ``id``) yields ``202``
        with an empty body, per the MCP streamable-HTTP transport.
        """
        try:
            data = json.loads(raw.decode()) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"jsonrpc": "2.0", "id": None,
                                  "error": {"code": mcp.PARSE_ERROR, "message": "parse error"}})
            return
        response = mcp.dispatch(data, swarms=self._mcp_swarms())
        if response is None:
            self.send_response(202)
            self.end_headers()
            return
        self._send_json(200, response)

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
        if to == "builder":
            session = scaffold.builder_session_name(self.cfg)
        else:
            try:
                session = self.cfg.get(to).session
            except Exception:
                self._send_json(404, {"error": "unknown agent"})
                return
        try:
            ok = tmux.paste_into(self.cfg, session, text)
        except tmux.SwarmError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(200, {"ok": bool(ok), "agent": to})

    def _api_key(self, raw: bytes) -> None:
        data = self._json_body(raw)
        if data is None:
            return
        to = data.get("agent") or data.get("to")
        key = data.get("key")
        if not to or not isinstance(key, str) or key == "":
            self._send_json(400, {"error": "missing agent/key"})
            return
        if to == "builder":
            session = scaffold.builder_session_name(self.cfg)
        else:
            try:
                session = self.cfg.get(to).session
            except Exception:
                self._send_json(404, {"error": "unknown agent"})
                return
        try:
            ok = tmux.send_key(self.cfg, session, key)
        except tmux.SwarmError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(200, {"ok": bool(ok), "agent": to, "key": key})

    def _api_up(self, raw: bytes) -> None:
        data = self._json_body(raw)
        if data is None:
            return
        name = data.get("agent") or data.get("name")
        if not name:
            self._send_json(400, {"error": "missing agent"})
            return
        if name not in self.cfg.names():
            self._send_json(404, {"error": "unknown agent"})
            return
        try:
            started = reconcile.start_one(self.cfg, name)
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(200, {"ok": True, "agent": name, "started": bool(started)})

    def _api_down(self, raw: bytes) -> None:
        data = self._json_body(raw)
        if data is None:
            return
        name = data.get("agent") or data.get("name")
        if not name:
            self._send_json(400, {"error": "missing agent"})
            return
        if name not in self.cfg.names():
            self._send_json(404, {"error": "unknown agent"})
            return
        try:
            stopped = reconcile.stop_one(self.cfg, name)
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(200, {"ok": True, "agent": name, "stopped": bool(stopped)})

    def _api_up_all(self, raw: bytes) -> None:
        """Start every configured-but-not-running agent (body ignored)."""
        try:
            started = reconcile.start_all(self.cfg)
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(200, {"ok": True, "started": started})

    def _api_down_all(self, raw: bytes) -> None:
        """Kill every running agent session (body ignored; config untouched)."""
        try:
            stopped = reconcile.stop_all(self.cfg)
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(200, {"ok": True, "stopped": stopped})

    # -- API: templates (bundled example swarms, for onboarding) ---------------

    def _templates_dir(self) -> Path:
        """The bundled ``examples/`` directory shipped alongside ``lib/``."""
        return Path(__file__).resolve().parent.parent / "examples"

    def _template_summary(self, path) -> str:
        """First meaningful ``# comment`` line of an example (its description).

        Skips decorative banners (``# ====``) and blank ``#`` lines so the card
        shows the real one-line blurb, not a row of separators.
        """
        try:
            for line in Path(path).read_text().splitlines():
                s = line.strip()
                if s.startswith("#"):
                    s = s.lstrip("#").strip()
                    if any(c.isalnum() for c in s):  # skip banners / blank comments
                        return s[:160]
                    continue
                if s:  # first content line before any real comment -> no summary
                    break
        except OSError:  # pragma: no cover - unreadable file degrades to ""
            pass
        return ""

    def _api_templates(self) -> None:
        tdir = self._templates_dir()
        out = []
        if tdir.exists():
            for p in sorted(tdir.glob("*.yaml")):
                try:
                    raw = reconcile.load_raw(p)
                except Exception:
                    continue
                swarm = raw.get("swarm") or {}
                title = swarm.get("name") or p.stem.replace("-", " ").replace("_", " ").title()
                out.append({
                    "name": p.stem,
                    "title": title,
                    "summary": self._template_summary(p),
                    "agents": len(raw.get("agents") or []),
                })
        self._send_json(200, {"templates": out})

    def _api_examples(self) -> None:
        """Like ``/api/templates`` but also returns each example's full YAML text.

        Lets the UI preview and let the operator edit an example before creating a
        swarm from it. ``/api/templates`` stays untouched for back-compat.
        """
        tdir = self._templates_dir()
        out = []
        if tdir.exists():
            for p in sorted(tdir.glob("*.yaml")):
                try:
                    text = p.read_text()
                    raw = reconcile.load_raw(p)
                except Exception:
                    continue
                swarm = raw.get("swarm") or {}
                title = swarm.get("name") or p.stem.replace("-", " ").replace("_", " ").title()
                out.append({
                    "name": p.stem,
                    "title": title,
                    "summary": self._template_summary(p),
                    "agents": len(raw.get("agents") or []),
                    "raw": text,
                })
        self._send_json(200, {"examples": out})

    def _api_templates_apply(self, raw: bytes) -> None:
        data = self._json_body(raw)
        if data is None:
            return
        name = data.get("name")
        if not name:
            self._send_json(400, {"error": "missing name"})
            return
        # Templates seed a fresh swarm; refuse if agents already exist.
        if self.cfg.names():
            self._send_json(400, {"error": "swarm already has agents"})
            return
        tdir = self._templates_dir()
        # Validate against the real listing so ``name`` can't escape the dir.
        valid = {p.stem for p in tdir.glob("*.yaml")} if tdir.exists() else set()
        if name not in valid:
            self._send_json(404, {"error": "unknown template"})
            return
        try:
            raw_tpl = reconcile.load_raw(tdir / f"{name}.yaml")
            added = reconcile.apply_template(
                self.cfg, raw_tpl.get("agents") or [], raw_tpl.get("defaults")
            )
            new_cfg = config.load(self.cfg.path)
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._set_cfg(new_cfg)
        mail.init_mailboxes(new_cfg)
        self._send_json(200, {"ok": True, "applied": name, "added": added})

    # -- API: rate (opt-in per-agent message throughput) ----------------------

    MESSAGE_KINDS = {"delivered", "user-send"}

    def _api_rate(self) -> None:
        from datetime import datetime, timedelta, timezone

        qs = parse_qs(urlparse(self.path).query)
        try:
            window = int(qs.get("window", ["5"])[0])
        except ValueError:
            window = 5
        if window <= 0:
            window = 5
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=window)
        counts: dict = {}
        total = 0
        logfile = self.cfg.log_dir / "agentainer.jsonl"
        if logfile.exists():
            for line in logfile.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("kind") not in self.MESSAGE_KINDS:
                    continue
                try:
                    ts = datetime.fromisoformat(rec["ts"])
                except (KeyError, ValueError):
                    continue
                if ts < cutoff:
                    continue
                agent = rec.get("agent") or "?"
                counts[agent] = counts.get(agent, 0) + 1
                total += 1
        rates = {a: round(n / window, 2) for a, n in counts.items()}
        self._send_json(200, {"window_min": window, "rates": rates, "total": total})

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
        self._set_cfg(new_cfg)
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
        self._set_cfg(new_cfg)
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
        for k in ("capture", "boot_delay_ms"):
            if data.get(k) not in (None, ""):
                extra[k] = data[k]
        # `pings` is a structured list of cron rules; pass it through so the
        # loader validates each cron (a bad one is surfaced as 400, not a no-op).
        if isinstance(data.get("pings"), list) and data["pings"]:
            extra["pings"] = data["pings"]
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
        self._set_cfg(new_cfg)
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
        self._set_cfg(new_cfg)
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
            # remove_agent also strips the agent from every peer's can_talk_to,
            # so this can't leave a dangling reference; any other failure is
            # surfaced as 400 with the config left intact (see reconcile._commit).
            new_cfg = reconcile.remove_agent(self.cfg, name)
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._set_cfg(new_cfg)
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
        self._set_cfg(new_cfg)
        # Receive replies defaults ON: keep the poller matched to the new config.
        # Stop any running poller, then (re)start it whenever Telegram is enabled --
        # so enabling the bridge from the UI starts listening immediately (no
        # separate toggle), a config change is picked up, and disabling stops it.
        global _tg_poller
        with _tg_lock:
            if _tg_poller is not None:
                _tg_poller.stop()
                _tg_poller = None
            if telegram.is_enabled(new_cfg):
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
    cfg=None,
    token: str = "",
    host: str = "127.0.0.1",
    port: int = 0,
    background: bool = True,
    ui_dir=None,
    swarms=None,
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
    if swarms is not None:
        # Registry-backed: one control plane over every swarm on the machine.
        UIHandler.swarms = dict(swarms)
        UIHandler.registry_backed = True
        UIHandler.default_swarm = next(iter(UIHandler.swarms), None)
    else:
        # A single explicit config: back-compat for ``serve -c one.yaml`` (and
        # the whole UI test suite, which passes one ``cfg``).
        UIHandler.swarms = {cfg.name: cfg} if cfg is not None else {}
        UIHandler.registry_backed = False
        UIHandler.default_swarm = cfg.name if cfg is not None else None
    UIHandler.token = token
    UIHandler.ui_dir = ui_path

    # "Receive replies" is ON by default: if Telegram is configured, start the
    # inbound poller as soon as we serve, so phone replies + slash commands work
    # without the operator having to flip a switch first. Stoppable any time from
    # the UI, and shutdown() stops it. A no-op when Telegram isn't enabled.
    #  * multi-swarm (registry-backed): ONE shared poller for the whole machine,
    #    reading the current registry each loop so new swarms are picked up.
    #  * single-config (serve -c / tests): the per-swarm poller, as before.
    global _tg_poller
    with _tg_lock:
        if _tg_poller is None:
            if swarms is not None:
                _tg_poller = telegram.start_control_poller(lambda: registry.load_all())
            elif cfg is not None and telegram.is_enabled(cfg):
                _tg_poller = telegram.start_poller(cfg)

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

"""Model Context Protocol (MCP) server for Agentainer.

This is the **fourth control-plane surface** (alongside CLI, UI, and Telegram):
it lets a *coding agent* monitor and manage every swarm on the machine over the
Model Context Protocol -- JSON-RPC 2.0 with the MCP method set (``initialize`` /
``tools/list`` / ``tools/call`` / ``ping``).

Per CLAUDE.md principle #7 this module is a **thin adapter** over the same tested
``lib/`` core the other surfaces use: each tool is a few lines calling
``mail`` / ``reconcile`` / ``tmux`` / ``turn`` / ``registry``. All the substance
(routing, ACL, lifecycle, scaffolding) stays in those 100%-covered modules.

Two transports carry this one core (see ``dispatch``):

* **stdio** -- ``agentainer mcp`` runs a line-delimited JSON-RPC loop on
  stdin/stdout. This is what a coding agent puts in its ``.mcp.json``; it needs
  no running server and operates over the whole ``registry``.
* **HTTP** -- ``POST /mcp`` on the existing ``serve`` control plane feeds request
  bodies straight into ``dispatch`` (reusing the Bearer-token auth).

Zero runtime dependencies: Python 3 stdlib only.
"""

from __future__ import annotations

import json
import sys

# The core modules each tool delegates to. Imported lazily-safe at module load
# (they are all stdlib-only themselves).
import mail
import reconcile
import registry
import tmux
import turn

# The MCP revision we implement. We advertise this from ``initialize``; if a
# client asks for a different one we still answer with ours (clients negotiate
# down / disconnect if incompatible -- our method set is stable across revisions).
PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "agentainer"


def _server_version() -> str:
    """Best-effort package version for ``serverInfo`` (never fatal)."""
    try:
        import cli

        return cli.read_version()
    except Exception:  # pragma: no cover - version read is best-effort
        return "0"


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class McpError(Exception):
    """A tool-level failure, surfaced to the agent as an ``isError`` result.

    Tool problems (unknown swarm, bad arguments, a failed action) are *not*
    JSON-RPC protocol errors -- MCP wants them returned as an ordinary tool
    result flagged ``isError`` so the model reads the message and self-corrects,
    exactly like the mailroom's ``system`` mail. Only malformed JSON-RPC itself
    uses the numeric error codes below.
    """


# JSON-RPC 2.0 standard error codes (protocol-level only).
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


# ---------------------------------------------------------------------------
# swarm resolution
# ---------------------------------------------------------------------------


def _swarms(provider) -> dict:
    """Normalise the swarm provider (dict | callable | None) to ``{name: cfg}``.

    ``None`` -> the global registry (``registry.load_all``), which is the right
    default for stdio and for a registry-backed ``serve``.
    """
    if provider is None:
        return registry.load_all()
    if callable(provider):
        return provider()
    return dict(provider)


def _resolve(swarms: dict, name):
    """Pick the swarm named *name*, or the only one if *name* is omitted."""
    if not name:
        if len(swarms) == 1:
            return next(iter(swarms.values()))
        raise McpError(
            "missing required argument 'swarm' (managing "
            f"{len(swarms)} swarms: {', '.join(sorted(swarms)) or 'none'})"
        )
    if name not in swarms:
        raise McpError(
            f"unknown swarm: {name!r} (known: {', '.join(sorted(swarms)) or 'none'})"
        )
    return swarms[name]


def _agent(cfg, name):
    try:
        return cfg.get(name)
    except Exception:
        raise McpError(f"unknown agent: {name!r} in swarm {cfg.name!r}")


def _count_files(d) -> int:
    return len([f for f in d.iterdir() if f.is_file()]) if d.exists() else 0


# ---------------------------------------------------------------------------
# tool registry
# ---------------------------------------------------------------------------

TOOLS: list = []


def tool(name: str, description: str, schema: dict):
    """Register a tool handler ``fn(swarms, args) -> dict|str`` with its schema."""

    def deco(fn):
        TOOLS.append(
            {
                "name": name,
                "description": description,
                "inputSchema": {
                    "type": "object",
                    "properties": schema,
                    "required": [
                        k for k, v in schema.items() if v.pop("_required", False)
                    ],
                },
                "handler": fn,
            }
        )
        return fn

    return deco


def _swarm_prop(required: bool = False) -> dict:
    return {
        "type": "string",
        "description": "swarm name (optional when only one swarm is managed)",
        "_required": required,
    }


def _agent_prop() -> dict:
    return {"type": "string", "description": "agent name", "_required": True}


# -- monitor (read-only) ----------------------------------------------------


@tool(
    "list_swarms",
    "List every swarm on the machine with a live summary (running/total agents, "
    "mail awaiting the user, and whether its supervisor is alive).",
    {},
)
def _t_list_swarms(swarms, args):
    out = []
    for cfg in swarms.values():
        running = sum(1 for a in cfg.agents if tmux.session_exists(a.session))
        out.append(
            {
                "name": cfg.name,
                "path": str(cfg.path),
                "root": str(cfg.root),
                "total": len(cfg.agents),
                "running": running,
                "attention": _count_files(cfg.queue_dir / "user"),
            }
        )
    return {"swarms": sorted(out, key=lambda s: s["name"])}


@tool(
    "swarm_status",
    "Full live status for one swarm: every agent's state (working/waiting/"
    "attention/stalled/stopped), unread inbox count, queue depth, and ACL.",
    {"swarm": _swarm_prop()},
)
def _t_swarm_status(swarms, args):
    cfg = _resolve(swarms, args.get("swarm"))
    agents = []
    for a in cfg.agents:
        running = tmux.session_exists(a.session)
        busy = turn.busy_info(cfg, a) is not None
        agents.append(
            {
                "name": a.name,
                "type": a.type,
                "running": running,
                "busy": busy,
                "unread": _count_files(cfg.mail_paths(a).inbox),
                "queue_depth": _count_files(cfg.queue_dir / a.name),
                "can_talk_to": a.can_talk_to,
            }
        )
    return {
        "name": cfg.name,
        "root": str(cfg.root),
        "user_available": bool(cfg.user_available),
        "attention": _count_files(cfg.queue_dir / "user"),
        "agents": agents,
    }


@tool(
    "read_inbox",
    "Read the message(s) currently sitting in an agent's inbox (what it will "
    "read on its next turn).",
    {"swarm": _swarm_prop(), "agent": _agent_prop()},
)
def _t_read_inbox(swarms, args):
    cfg = _resolve(swarms, args.get("swarm"))
    a = _agent(cfg, args.get("agent"))
    inbox = cfg.mail_paths(a).inbox
    msgs = []
    if inbox.exists():
        for f in sorted(inbox.iterdir()):
            if f.is_file():
                msgs.append({"file": f.name, "text": f.read_text()})
    return {"agent": a.name, "inbox": msgs}


@tool(
    "read_queue",
    "Read an agent's pending queue -- mail accepted for it but not yet released "
    "into its inbox, in delivery (FIFO) order.",
    {"swarm": _swarm_prop(), "agent": _agent_prop()},
)
def _t_read_queue(swarms, args):
    cfg = _resolve(swarms, args.get("swarm"))
    a = _agent(cfg, args.get("agent"))
    msgs = [
        {"file": p.name, "text": p.read_text()}
        for p in mail.queued_files(cfg, a.name)
    ]
    return {"agent": a.name, "queue": msgs}


@tool(
    "read_user_inbox",
    "Read mail the agents have sent to the user and that is awaiting a reply "
    "(the swarm's 'attention' items).",
    {"swarm": _swarm_prop()},
)
def _t_read_user_inbox(swarms, args):
    cfg = _resolve(swarms, args.get("swarm"))
    udir = cfg.queue_dir / "user"
    msgs = []
    if udir.exists():
        for f in sorted(udir.iterdir()):
            if f.is_file():
                msgs.append({"file": f.name, "text": f.read_text()})
    return {"swarm": cfg.name, "inbox": msgs}


@tool(
    "agent_logs",
    "Recent durable JSONL log records for an agent (omit 'agent' for the "
    "swarm-wide log). 'n' caps the number of records (default 50).",
    {
        "swarm": _swarm_prop(),
        "agent": {"type": "string", "description": "agent name (optional)"},
        "n": {"type": "integer", "description": "max records (default 50)"},
    },
)
def _t_agent_logs(swarms, args):
    cfg = _resolve(swarms, args.get("swarm"))
    agent = args.get("agent")
    try:
        n = int(args.get("n", 50))
    except (TypeError, ValueError):
        n = 50
    logfile = cfg.log_dir / (f"{agent}.jsonl" if agent else "agentainer.jsonl")
    records = []
    if logfile.exists():
        for line in logfile.read_text().splitlines()[-n:]:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records.append({"raw": line})
    return {"agent": agent, "logs": records}


@tool(
    "capture_pane",
    "Snapshot of an agent's live terminal (tmux pane) -- what it is showing "
    "right now.",
    {"swarm": _swarm_prop(), "agent": _agent_prop()},
)
def _t_capture_pane(swarms, args):
    cfg = _resolve(swarms, args.get("swarm"))
    a = _agent(cfg, args.get("agent"))
    return {"agent": a.name, "pane": tmux.capture_pane(cfg, a)}


@tool(
    "read_config",
    "The raw agentainer.yaml for a swarm as a structured object.",
    {"swarm": _swarm_prop()},
)
def _t_read_config(swarms, args):
    cfg = _resolve(swarms, args.get("swarm"))
    return {"name": cfg.name, "path": str(cfg.path), "config": reconcile.load_raw(cfg.path)}


# -- manage (write) ---------------------------------------------------------


@tool(
    "send_message",
    "Send a message to an agent as the user (goes through the mailroom exactly "
    "like a reply typed in the UI).",
    {
        "swarm": _swarm_prop(),
        "agent": _agent_prop(),
        "text": {"type": "string", "description": "message body", "_required": True},
    },
)
def _t_send_message(swarms, args):
    cfg = _resolve(swarms, args.get("swarm"))
    a = _agent(cfg, args.get("agent"))
    text = args.get("text")
    if not isinstance(text, str) or text == "":
        raise McpError("missing required argument 'text'")
    mail.send_as_user(cfg, a.name, text)
    return {"ok": True, "swarm": cfg.name, "to": a.name}


@tool(
    "set_availability",
    "Set whether the user is available. When away, agents are told to hold "
    "user-directed mail until you return.",
    {
        "swarm": _swarm_prop(),
        "available": {"type": "boolean", "description": "user availability", "_required": True},
    },
)
def _t_set_availability(swarms, args):
    cfg = _resolve(swarms, args.get("swarm"))
    val = args.get("available")
    if not isinstance(val, bool):
        raise McpError("argument 'available' must be a boolean")
    mail.set_user_available(cfg, val)
    return {"ok": True, "swarm": cfg.name, "available": val}


@tool(
    "start_agent",
    "Start one agent's tmux session (install hooks, launch its CLI, send its "
    "first prompt).",
    {"swarm": _swarm_prop(), "agent": _agent_prop()},
)
def _t_start_agent(swarms, args):
    cfg = _resolve(swarms, args.get("swarm"))
    a = _agent(cfg, args.get("agent"))
    started = reconcile.start_one(cfg, a.name)
    return {"ok": True, "swarm": cfg.name, "agent": a.name, "started": bool(started)}


@tool(
    "stop_agent",
    "Stop one agent's tmux session (its recorded conversation is kept for resume).",
    {"swarm": _swarm_prop(), "agent": _agent_prop()},
)
def _t_stop_agent(swarms, args):
    cfg = _resolve(swarms, args.get("swarm"))
    a = _agent(cfg, args.get("agent"))
    stopped = reconcile.stop_one(cfg, a.name)
    return {"ok": True, "swarm": cfg.name, "agent": a.name, "stopped": bool(stopped)}


@tool(
    "up_swarm",
    "Bring a whole swarm up: start every agent that isn't already running.",
    {"swarm": _swarm_prop()},
)
def _t_up_swarm(swarms, args):
    cfg = _resolve(swarms, args.get("swarm"))
    started = reconcile.start_all(cfg)
    return {"ok": True, "swarm": cfg.name, "started": [a.name for a in started]}


@tool(
    "down_swarm",
    "Bring a whole swarm down: stop every running agent (conversations kept).",
    {"swarm": _swarm_prop()},
)
def _t_down_swarm(swarms, args):
    cfg = _resolve(swarms, args.get("swarm"))
    stopped = reconcile.stop_all(cfg)
    return {"ok": True, "swarm": cfg.name, "stopped": stopped}


@tool(
    "create_swarm",
    "Scaffold and register a brand-new swarm. Optionally seed it from a bundled "
    "example template (see the 'name' fields from list_examples in the UI).",
    {
        "name": {"type": "string", "description": "new swarm name", "_required": True},
        "root": {"type": "string", "description": "workspace root (default ./workspace)"},
        "template": {"type": "string", "description": "example template to seed from"},
    },
)
def _t_create_swarm(swarms, args):
    name = args.get("name")
    if not name:
        raise McpError("missing required argument 'name'")
    try:
        path = registry.create_swarm(
            name, root=args.get("root"), template=args.get("template")
        )
    except Exception as exc:
        raise McpError(str(exc))
    return {"ok": True, "name": name, "path": str(path)}


@tool(
    "add_agent",
    "Add an agent to a swarm's config and initialise its mailbox. Bring it up "
    "with start_agent (or up_swarm).",
    {
        "swarm": _swarm_prop(),
        "name": {"type": "string", "description": "new agent name", "_required": True},
        "type": {"type": "string", "description": "claude|codex|gemini|hermes", "_required": True},
        "command": {"type": "string", "description": "shell command that launches the CLI", "_required": True},
        "can_talk_to": {"type": "string", "description": "comma-separated ACL, or '*' for all (default: user)"},
        "role": {"type": "string", "description": "standing role / first prompt"},
    },
)
def _t_add_agent(swarms, args):
    cfg = _resolve(swarms, args.get("swarm"))
    for req in ("name", "type", "command"):
        if not args.get(req):
            raise McpError(f"missing required argument {req!r}")
    acl = args.get("can_talk_to", "user")
    acl_list = [s.strip() for s in acl.split(",") if s.strip()] if isinstance(acl, str) else acl
    try:
        new_cfg = reconcile.add_agent(
            cfg,
            args["name"],
            args["type"],
            args["command"],
            acl_list,
            role=args.get("role", ""),
        )
    except Exception as exc:
        raise McpError(str(exc))
    mail.init_mailboxes(new_cfg)
    return {"ok": True, "swarm": cfg.name, "name": args["name"]}


@tool(
    "remove_agent",
    "Remove an agent from a swarm's config and stop its session.",
    {"swarm": _swarm_prop(), "name": {"type": "string", "description": "agent to remove", "_required": True}},
)
def _t_remove_agent(swarms, args):
    cfg = _resolve(swarms, args.get("swarm"))
    name = args.get("name")
    if not name:
        raise McpError("missing required argument 'name'")
    try:
        reconcile.remove_agent(cfg, name)
    except Exception as exc:
        raise McpError(str(exc))
    return {"ok": True, "swarm": cfg.name, "removed": name}


# ---------------------------------------------------------------------------
# JSON-RPC dispatch
# ---------------------------------------------------------------------------


def tool_specs() -> list:
    """The public ``tools/list`` payload (name/description/inputSchema)."""
    return [
        {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
        for t in TOOLS
    ]


def _find_tool(name):
    for t in TOOLS:
        if t["name"] == name:
            return t
    return None


def _call_tool(name, arguments, swarms) -> dict:
    """Run a tool, returning an MCP ``tools/call`` result (never raises McpError)."""
    t = _find_tool(name)
    if t is None:
        return _tool_error(f"unknown tool: {name!r}")
    try:
        result = t["handler"](swarms, arguments or {})
    except McpError as exc:
        return _tool_error(str(exc))
    except Exception as exc:  # a bug in the core surfaces as a readable tool error
        return _tool_error(f"{type(exc).__name__}: {exc}")
    text = result if isinstance(result, str) else json.dumps(result, default=str)
    out = {"content": [{"type": "text", "text": text}], "isError": False}
    if isinstance(result, dict):
        out["structuredContent"] = result
    return out


def _tool_error(msg: str) -> dict:
    return {"content": [{"type": "text", "text": msg}], "isError": True}


def _result(msg_id, payload):
    return {"jsonrpc": "2.0", "id": msg_id, "result": payload}


def _error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def dispatch(message: dict, swarms=None):
    """Handle one JSON-RPC message; return a response dict (or ``None`` for a
    notification, which carries no ``id`` and gets no reply).

    *swarms* is the swarm provider: a ``{name: cfg}`` dict, a zero-arg callable
    returning one, or ``None`` for the global registry.
    """
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, INVALID_REQUEST, "not a JSON-RPC 2.0 message")

    method = message.get("method")
    msg_id = message.get("id")
    is_notification = "id" not in message

    if not isinstance(method, str):
        return None if is_notification else _error(msg_id, INVALID_REQUEST, "missing method")

    # Notifications (initialized, cancelled, ...) require no response.
    if is_notification:
        return None

    if method == "initialize":
        return _result(
            msg_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": _server_version()},
                "instructions": (
                    "Agentainer control plane. Use list_swarms then swarm_status to "
                    "observe; send_message / start_agent / up_swarm etc. to manage. "
                    "Most tools take an optional 'swarm' name (required when more "
                    "than one swarm is managed)."
                ),
            },
        )
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": tool_specs()})
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        if not name:
            return _error(msg_id, INVALID_PARAMS, "missing tool name")
        return _result(msg_id, _call_tool(name, params.get("arguments") or {}, _swarms(swarms)))

    return _error(msg_id, METHOD_NOT_FOUND, f"unknown method: {method!r}")


# ---------------------------------------------------------------------------
# stdio transport
# ---------------------------------------------------------------------------


def serve_stdio(swarms=None, stdin=None, stdout=None) -> int:
    """Run the line-delimited JSON-RPC loop over stdin/stdout until EOF.

    This is the ``agentainer mcp`` transport a coding agent launches as a
    subprocess. Each line in is one JSON-RPC message; each response is one JSON
    line out. Parse errors reply with a JSON-RPC parse error and keep going.
    """
    fin = stdin if stdin is not None else sys.stdin
    fout = stdout if stdout is not None else sys.stdout
    for line in fin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _write(fout, _error(None, PARSE_ERROR, "parse error"))
            continue
        response = dispatch(message, swarms=swarms)
        if response is not None:
            _write(fout, response)
    return 0


def _write(fout, obj) -> None:
    fout.write(json.dumps(obj, default=str) + "\n")
    fout.flush()

# MCP — Manage Agentainer from a Coding Agent

Agentainer is an *agent-management* system, so it ships an **MCP (Model Context
Protocol) server**: the fourth control plane, alongside the [CLI](cli-reference.md),
the [UI](ui-guide.md), and the [Telegram bridge](telegram-bridge.md). It lets a
**coding agent** — Claude Code, Cursor, Codex, or anything that speaks MCP —
*monitor and manage every swarm on the machine* through a stable set of tools,
with no scraping and no bespoke API client.

This is a **permanent, maintained surface**. Every management capability
Agentainer gains lands here as an MCP tool, kept in lockstep with the CLI/UI/
Telegram (see [CLAUDE.md](../CLAUDE.md) principle #7).

## Two transports, one tool set

The exact same tools ([below](#tools)) are reachable two ways — pick whichever
your agent supports:

| Transport | How | When |
|-----------|-----|------|
| **stdio** | `agentainer mcp` | The agent launches Agentainer as a subprocess and talks JSON-RPC over stdin/stdout. **No running `serve` needed** — it operates directly over the global swarm [registry](multi-swarm.md). This is the usual `.mcp.json` setup. |
| **HTTP** | `POST /mcp` on a running `agentainer serve` | The agent talks to an already-running control plane over HTTP, reusing the same Bearer token as the UI. Good for a remote/shared control plane. |

Both are thin adapters over the same tested `lib/` core (`lib/mcp.py`), so they
behave identically.

## Quick start (stdio)

Add Agentainer to your coding agent's MCP config. For **Claude Code**, drop this
in `.mcp.json` (project) or your user MCP settings:

```json
{
  "mcpServers": {
    "agentainer": {
      "command": "agentainer",
      "args": ["mcp"]
    }
  }
}
```

That's it. The agent can now call `list_swarms`, `swarm_status`, `send_message`,
`up_swarm`, and the rest. Because stdio operates over the registry, every swarm
you have ever `up`'d (or created via `agentainer swarms create`) is visible — no
token, no port.

> Verify by hand: `echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | agentainer mcp`
> prints the tool catalog as one JSON line.

## Quick start (HTTP)

Start the control plane, then point an HTTP-transport MCP client at `/mcp`:

```bash
agentainer serve            # prints the URL + token on stderr
```

```json
{
  "mcpServers": {
    "agentainer": {
      "type": "http",
      "url": "http://127.0.0.1:<port>/mcp",
      "headers": { "Authorization": "Bearer <UI_TOKEN>" }
    }
  }
}
```

The `/mcp` endpoint requires the same token as every other API call (via the
`Authorization: Bearer` header or `?token=`). It is **POST-only** — a `GET /mcp`
returns `405` — because Agentainer does not push server→client notifications.

## Tools

Every tool takes an optional `swarm` name; it is **required only when more than
one swarm is managed** (with a single swarm it is inferred). Tool results come
back both as JSON text and as MCP `structuredContent`.

### Monitor (read-only)

| Tool | What it returns |
|------|-----------------|
| `list_swarms` | Every swarm + a live summary (running/total agents, mail awaiting the user). |
| `swarm_status` | Full status for one swarm: each agent's `running`/`busy` state, unread inbox count, queue depth, and ACL. |
| `read_inbox` | The message(s) currently in an agent's inbox (what it reads next turn). |
| `read_queue` | An agent's pending queue (accepted but not yet released), in delivery order. |
| `read_user_inbox` | Mail the agents sent to *you* that is awaiting a reply. |
| `agent_logs` | Recent durable JSONL log records for an agent (or the whole swarm). |
| `capture_pane` | A snapshot of an agent's live terminal. |
| `read_config` | The swarm's `agentainer.yaml` as a structured object. |

### Manage (write)

| Tool | Effect |
|------|--------|
| `send_message` | Send a message to an agent as the user (through the mailroom, exactly like the UI). |
| `set_availability` | Set whether you're available (agents hold user-directed mail while you're away). |
| `start_agent` / `stop_agent` | Start / stop one agent's tmux session (conversation kept for resume). |
| `up_swarm` / `down_swarm` | Bring a whole swarm up / down. |
| `create_swarm` | Scaffold and register a brand-new swarm, optionally from an example template. |
| `add_agent` / `remove_agent` | Add or remove an agent in a swarm's config. |

New capabilities are added here as they land on the other surfaces; this table is
the contract.

## Protocol details

- **JSON-RPC 2.0.** Implemented methods: `initialize`, `tools/list`,
  `tools/call`, `ping`. Notifications (e.g. `notifications/initialized`) are
  accepted and produce no response.
- **Protocol revision:** advertised from `initialize` (`protocolVersion`).
- **Tool errors are not protocol errors.** An unknown swarm, a bad argument, or a
  failed action comes back as an ordinary tool result flagged `isError: true`
  with a readable message — so the model reads it and self-corrects, exactly like
  the mailroom's `system` mail. Only malformed JSON-RPC uses the numeric error
  codes (`-32700` parse, `-32600` invalid request, `-32601` method not found,
  `-32602` invalid params).
- **stdio framing:** one JSON-RPC message per line in, one JSON line per response
  out. A malformed line yields a parse-error reply and the loop continues.
- **HTTP framing:** one JSON-RPC message per `POST /mcp`; a notification returns
  `202` with an empty body, a request returns `200` with the JSON-RPC response.

## Security

- **stdio** runs as a local subprocess with your own permissions — same trust
  boundary as running `agentainer` yourself.
- **HTTP** inherits the UI's model: bound to `127.0.0.1` by default, and a token
  is **mandatory** for any non-loopback bind (`ui.run_server` raises otherwise).
  Treat the token like a password — `/mcp` can start processes, edit configs, and
  send input to agents that may run with elevated permissions.
- The `can_talk_to` ACL is unchanged and still cooperative (not an OS boundary);
  MCP adds no new security boundary.

## How it fits together

`lib/mcp.py` is a thin JSON-RPC adapter: each tool is a few lines calling the
same `mail` / `reconcile` / `tmux` / `turn` / `registry` functions the CLI, UI,
and Telegram use. All the substance — routing, ACL, lifecycle, scaffolding —
stays in those 100%-covered modules, so the four surfaces can never drift.

See also: [Multi-Swarm Control Plane](multi-swarm.md) ·
[CLI Reference](cli-reference.md) · [UI Guide](ui-guide.md).

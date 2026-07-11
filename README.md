# Agentainer

**Zero-dependency multi-agent orchestrator with a file-based mail model.**

Agentainer launches AI coding agents (Claude Code, Codex, Gemini, Hermes) each in
its own `tmux` session and working directory, defined by a single
`agentainer.yaml`. They talk to each other only where the config's `can_talk_to`
ACL allows — by **reading a file** to receive and **writing a file** to send.

No runtime dependencies. No API keys required to exercise the mechanics. Agents
only need to read and write natural-language files; the orchestrator owns all the
hard logic (routing, ACL, message IDs, threading, read-state, queueing, retries,
availability, the durable log).

> v2 replaces v1's "tagged XML envelope emitted inside prose and scraped out of a
> TUI pane" — that was unreliable across LLMs. The file-based mail model works on
> nearly every tool-calling model, including weak ones.

---

## Install

```bash
git clone <repo> && cd Agentainer
npm install            # runs the postinstall dep check; no build step
npm link               # puts the `agentainer` bin on your PATH
agentainer --version
```

Once linked/installed, the command is just **`agentainer`**. `tmux` is the only
external runtime. `node` is required for the npm bin wrapper. PyYAML is used *if
present*; a bundled `minyaml` parser keeps everything working without it.

## Quick start (no API keys)

The bundled quickstart uses `bash` loop "mock agents" so you can watch the
mailroom route mail safely:

```bash
cp examples/quickstart.yaml my-swarm.yaml
agentainer up       -c my-swarm.yaml
agentainer status   -c my-swarm.yaml
agentainer send     -c my-swarm.yaml --to orchestrator "Build a CSV->Parquet CLI."
agentainer logs     -c my-swarm.yaml -f
agentainer down     -c my-swarm.yaml
```

To run real agents, swap each `command` for the actual CLI you installed and drop
`capture: none` so turns get detected.

## The model in one screen

An agent's entire world is **two verbs** (read a file, write a file) and **four
folders**:

| Folder | Meaning |
| --- | --- |
| `inbox/` | The **one** current unread message (orchestrator releases one at a time). |
| `outbox/<name>/` | Write a file here to send to `<name>`. Read `<name>/about.md` for a contact card. |
| `read/` | Move a handled message here (best-effort read receipt). |
| `sent/` | The agent's own record of delivered mail (orchestrator moves it here). |
| `failed/` | Mail bounced by the orchestrator (ACL violation, etc.), with a `system` explanation. |

When an agent **stops** with unread mail, the orchestrator sweeps its `outbox`
and pastes a **nudge** ("you have mail — read it, then move it to `read/`"),
re-injecting the protocol every time (including the allowed-recipient list).

## Commands

| Command | Purpose |
| --- | --- |
| `validate` | Resolve and print the config; launch nothing. |
| `up` | Create dirs + mailbox folders, install per-type turn-detection, open one tmux session per agent. |
| `down` | Tear the swarm down. |
| `restart` | Down then up. |
| `status` | Show agent/health summary. |
| `attach` | Attach to an agent's tmux session. |
| `send` | Send a `user` message into the swarm (`--to <agent>`). |
| `sessions` | List recorded tmux sessions (resume info). |
| `queue` | Show pending mail per agent. |
| `idle` | List agents currently idle. |
| `inbox` | Show current inbox message per agent. |
| `logs` | Tail the durable JSONL event log (`-f` to follow). |
| `hook` | Turn-completion entry point (called by `claude`/`codex` stop hooks). |
| `watch` | Live-watch the supervisor. |
| `supervise` | Run one (or the loop of) supervisor tick(s). |
| `user` | Toggle `user` availability. |
| `serve` | Serve the HTTP control-plane UI (observability + send-as-user) on `127.0.0.1`. |
| `add` | Add an agent to the config (YAML) and bring it up immediately. |
| `remove` | Remove an agent from the config and stop its session. |
| `edit` | Edit an agent's fields in the config (`-s key=value`, repeatable) and reconcile. |
| `reconcile` | Start agents missing from the running set / stop sessions no longer configured. |

See `llms.txt` for a machine-readable summary and `ProjectPlan.md` for the full
design (source of truth).

## Key invariants

- **Zero runtime dependencies, forever** — Python 3 + bash + tmux; bundled
  `minyaml` fallback; stdlib-only UI (P2+).
- **`can_talk_to` is cooperative, not an OS boundary** — enforced for
  well-behaved agents, documented honestly (Decision D15).
- **`user` / `system` are reserved virtual mailboxes.**
- **Liveness supervisor heartbeat retained** — no event-only redesign.
- **Durable JSONL event log is the source of truth** for history (TUIs keep no
  scrollback).
- **100% line coverage** via mock agents (bash loops, no API keys).

## The control-plane UI (P2–P3)

`agentainer serve -c my-swarm.yaml --port 8080 --token <secret>` starts a
**zero-dependency** web UI (stdlib `http.server` + a single vanilla-JS page, no
framework, no build step). It binds `127.0.0.1` by default; any non-loopback bind
requires a token. The UI shows swarm/agent status, per-agent logs, the current
inbox and queue, a **live terminal snapshot** of each agent's tmux pane, and lets
you inject mail as the `user` mailbox. The UI is a control plane, so keep it on
loopback unless a token is supplied.

## Dynamic reconcile (P4)

No need to tear the whole swarm down to change it:

```bash
agentainer add    dave --type codex --command "codex" --can-talk-to "alice,user" -c my-swarm.yaml
agentainer edit   alice -s can_talk_to="dave,user" -c my-swarm.yaml
agentainer remove bob -c my-swarm.yaml
agentainer reconcile -c my-swarm.yaml      # start missing agents, stop orphaned sessions
```

`add`/`remove`/`edit` rewrite `agentainer.yaml` with a stdlib-only YAML emitter
(works with **or without** PyYAML) and then `reconcile` the change into effect.

## Project status

- **P1 — Mail runtime (CLI-driven)**: ✅ done.
- **P2 — UI observability**: ✅ done.
- **P3 — terminal snapshot + send-from-UI**: ✅ done.
- **P4 — dynamic reconcile (`add`/`remove`/`edit`/`reconcile`)**: ✅ done.

100% line coverage across all core + UI + reconcile modules, driven entirely by
mock agents (no API keys).

## License

MIT.

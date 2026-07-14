# Agentainer CLI reference

Complete reference for every `agentainer` subcommand, derived from
`lib/cli.py` (`build_parser`). This is the authoritative command list for
Agentainer v2.

The command is `agentainer` once installed (`npm link`). `tmux` is the only
external runtime dependency; `node` is only needed for the npm bin wrapper.

## Contents

- [Global options & config resolution](#global-options--config-resolution)
- Everyday: [`validate`](#validate) · [`up`](#up) · [`down`](#down) ·
  [`restart`](#restart) · [`status`](#status) · [`attach`](#attach) ·
  [`send`](#send) · [`user`](#user) · [`sessions`](#sessions) ·
  [`queue`](#queue) · [`idle`](#idle) · [`inbox`](#inbox) · [`logs`](#logs)
- UI / control plane: [`serve`](#serve)
- Dynamic reconcile (P4): [`add`](#add) · [`remove`](#remove) ·
  [`edit`](#edit) · [`reconcile`](#reconcile)
- Lifecycle / state: [`remove-session`](#remove-session)
- Internal (not for direct use): [`hook`](#hook) · [`watch`](#watch) ·
  [`supervise`](#supervise)

---

## Global options & config resolution

These live on the top-level parser (before the subcommand):

| Flag | Default | Meaning |
| --- | --- | --- |
| `-v`, `--version` | — | Print the Agentainer version (read from `package.json`) and exit. |
| `-c`, `--config <path>` | see below | Path to the swarm YAML. |

**`-c/--config` is also accepted after almost every subcommand** (e.g.
`agentainer up -c my-swarm.yaml`). The subcommand-level `-c` overrides the
top-level one when given; if omitted, the top-level value is preserved.

**Default config resolution** (`default_config()`), in order:

1. `$AGENTAINER_CONFIG` if set.
2. `./agentainer.yaml` in the current directory if it exists.
3. `$AGENTAINER_HOME/agentainer.yaml` (the repo root) as a last resort.

**Config-first shorthand:** `agentainer some.yaml up` and `agentainer ./x.yaml`
both mean "run `up` with this config". If a `.yaml`/`.yml` path is the first
argument, it's pulled out and appended as `-c <path>`, defaulting the command to
`up`.

Related environment variables:

- `AGENTAINER_HOME` — repo root override (else `lib/..`).
- `AGENTAINER_CONFIG` — default config path.
- `AGENTAINER_AGENT` — the calling agent's name (used by `hook`, `send`,
  `inbox` when no name is passed).
- `AGENTAINER_UI_TOKEN` — default token for `serve`.
- `AGENTAINER_PROG` — overrides the program name shown in help.

---

# Everyday

## `validate`

**Purpose:** Parse the config and print the fully resolved swarm — launch
nothing.

```
agentainer validate [-c <config>] [--show-prompts]
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--show-prompts` | off | Also print each agent's `role` (the standing first prompt). |

Prints any config warnings, then the swarm name, root, and — per agent — the
command, workdir (with `exists` / `will be created` / `MISSING`), tmux session,
inbox/outbox paths, and ACL peers.

```bash
agentainer validate -c my-swarm.yaml --show-prompts
```

## `up`

**Purpose:** Create agent dirs + mailbox folders, install per-type
turn-detection, open one tmux session per agent, and deliver each agent's first
(standby) prompt.

```
agentainer up [-c <config>] [--only <a,b>] [--resume | --no-resume]
              [--restart] [--no-supervise]
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--only <a,b,...>` | all agents | Comma-separated subset of agents to start. |
| `--resume` | (see note) | Reattach each agent to the conversation recorded in `sessions.yaml`. |
| `--no-resume` | — | Start fresh conversations, ignoring recorded ones. |
| `--restart` | off | Kill and recreate sessions that already exist. |
| `--no-supervise` | off | Do not start the liveness supervisor. |

**Resume default:** resume is controlled by `swarm.resume` in the config (the
built-in default is on). `--resume`/`--no-resume` override it. Only an *explicit*
`--resume` warns when there is no recorded conversation; an implicit default
resume starts fresh silently. Claude and Codex resume via their native commands;
Gemini/Hermes have no resume bridge and start fresh with a warning.

On success it prints the attach hint and a ready-to-paste `serve` command (with a
freshly generated token).

```bash
agentainer up -c my-swarm.yaml
agentainer up -c my-swarm.yaml --only orchestrator,developer --no-resume
```

## `down`

**Purpose:** Kill the agents' tmux sessions (and, unless scoped, the supervisor).

```
agentainer down [-c <config>] [--only <a,b>]
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--only <a,b,...>` | all agents | Comma-separated subset to stop. When omitted, the supervisor is stopped too. |

```bash
agentainer down -c my-swarm.yaml
agentainer down -c my-swarm.yaml --only developer
```

## `restart`

**Purpose:** `down` then `up` (forces `--restart` so existing sessions are
recreated).

```
agentainer restart [-c <config>] [--only <a,b>] [--resume | --no-resume]
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--only <a,b,...>` | all agents | Subset to restart. |
| `--resume` | config default | Resume recorded conversations. |
| `--no-resume` | — | Start fresh conversations. |

```bash
agentainer restart -c my-swarm.yaml --only developer
```

## `status`

**Purpose:** Show, per agent, whether it's up/down, busy/idle, its queue depth,
unread count, and ACL peers, plus the supervisor state.

```
agentainer status [-c <config>]
```

No flags. Output columns per agent:
`name (type) up|down busy Ns|idle|untracked|- queue=N unread=N talks=peers`,
followed by `supervisor: alive|down`.

```bash
agentainer status -c my-swarm.yaml
```

## `attach`

**Purpose:** Attach your terminal to an agent's tmux session (execs `tmux
attach`).

```
agentainer attach [-c <config>] <agent>
```

| Positional | Meaning |
| --- | --- |
| `agent` | Agent name to attach to. Fails if it is not running. |

Detach with the usual tmux binding (`Ctrl-b d`).

```bash
agentainer attach -c my-swarm.yaml developer
```

## `send`

**Purpose:** Send a message into the swarm — as the virtual `user`, or
simulating another agent (which runs the real routing + ACL sweep).

```
agentainer send [-c <config>] --to <agent> [--from <sender>]
                [--file <path>] [message ... | -]
```

| Flag / positional | Default | Meaning |
| --- | --- | --- |
| `--to <agent>` | *(required)* | Recipient agent name. |
| `--from <sender>` | `$AGENTAINER_AGENT` else `user` | Sender name. |
| `--file <path>` | — | Read the message body from a file instead of argv/stdin. |
| `message ...` | — | Message text as positional words; `-` (or empty, non-TTY) reads stdin. |

**Behaviour:** if the sender is `user` (or not a configured agent name), the
message is delivered as the virtual user. If the sender *is* a configured agent,
the text is dropped into that agent's `outbox/<to>/` and the same on-stop sweep
the completion hook uses is run — so ACL enforcement and routing actually
execute.

```bash
agentainer send -c my-swarm.yaml --to orchestrator "Build a CSV->Parquet CLI."
agentainer send -c my-swarm.yaml --to developer --from orchestrator --file task.md
echo "hello" | agentainer send -c my-swarm.yaml --to orchestrator -
```

## `user`

**Purpose:** Manage the virtual `user` mailbox — toggle availability, read the
user's held mail, or send as the user.

```
agentainer user available   [-c <config>]
agentainer user away        [-c <config>]
agentainer user inbox       [-c <config>]
agentainer user send --to <agent> [--file <path>] [message ...] [-c <config>]
```

| Sub-subcommand | Meaning |
| --- | --- |
| `available` | Mark the user as available to receive mail. |
| `away` | Mark the user as away (mail is *held*, never bounced). |
| `inbox` | Print all mail queued for the user. |
| `send` | Deliver a message to `--to` as the user. |

`user send` flags:

| Flag / positional | Default | Meaning |
| --- | --- | --- |
| `--to <agent>` | *(required)* | Recipient. |
| `--file <path>` | — | Read the body from a file. |
| `message ...` | — | Message text (or `-`/stdin). |

```bash
agentainer user available -c my-swarm.yaml
agentainer user inbox -c my-swarm.yaml
agentainer user send --to orchestrator "ship it" -c my-swarm.yaml
```

## `sessions`

**Purpose:** Show each agent's recorded conversation id (used by `--resume`).

```
agentainer sessions [-c <config>] [--raw]
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--raw` | off | Print `sessions.yaml` verbatim instead of the formatted view. |

Conversation ids are written as each agent finishes its first turn. With nothing
recorded it says so and points at the `sessions.yaml` path.

```bash
agentainer sessions -c my-swarm.yaml
agentainer sessions -c my-swarm.yaml --raw
```

## `queue`

**Purpose:** Show (or clear) the messages waiting for an agent, plus its
busy/idle state.

```
agentainer queue [-c <config>] <agent> [--clear]
```

| Flag / positional | Default | Meaning |
| --- | --- | --- |
| `agent` | *(required)* | Agent whose queue to inspect. |
| `--clear` | off | Discard every queued message for that agent. |

```bash
agentainer queue -c my-swarm.yaml developer
agentainer queue -c my-swarm.yaml developer --clear
```

## `idle`

**Purpose:** Force an agent back to idle — the escape hatch when a
turn-completion capture never fired.

```
agentainer idle [-c <config>] <agent> [--no-drain]
```

| Flag / positional | Default | Meaning |
| --- | --- | --- |
| `agent` | *(required)* | Agent to mark idle. |
| `--no-drain` | off | Do **not** process the agent's `read/` folder after marking it idle. |

```bash
agentainer idle -c my-swarm.yaml developer
```

## `inbox`

**Purpose:** Print the current inbox message(s) for an agent.

```
agentainer inbox [-c <config>] [agent] [-n <count>]
```

| Flag / positional | Default | Meaning |
| --- | --- | --- |
| `agent` | `$AGENTAINER_AGENT` | Agent whose inbox to print; required if the env var is unset. |
| `-n`, `--tail <count>` | `5` | Accepted for symmetry with `logs` (inbox normally holds one message). |

```bash
agentainer inbox -c my-swarm.yaml developer
```

## `logs`

**Purpose:** Print (or follow) the durable JSONL event log — per agent or the
whole swarm.

```
agentainer logs [-c <config>] [agent] [-n <count>] [-f]
```

| Flag / positional | Default | Meaning |
| --- | --- | --- |
| `agent` | whole swarm | Restrict to one agent's log (`<agent>.jsonl`); omit for `agentainer.jsonl`. |
| `-n`, `--tail <count>` | `20` | Number of trailing records to print. |
| `-f`, `--follow` | off | Follow the log (execs `tail -f`; ignores `-n`). |

Each line is rendered as `ts agent kind detail` with an indented body.

```bash
agentainer logs -c my-swarm.yaml
agentainer logs -c my-swarm.yaml developer -n 50
agentainer logs -c my-swarm.yaml -f
```

---

# UI / control plane

## `serve`

**Purpose:** Serve the zero-dependency HTTP control-plane UI (mail-app
observability, send-from-UI, terminal snapshot, settings/agent editing).

```
agentainer serve [-c <config>] [--host <host>] [--port <port>] [--token <token>]
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--host <host>` | `127.0.0.1` | Bind host. |
| `--port <port>` | `0` (auto-assigned) | TCP port. |
| `--token <token>` | `$AGENTAINER_UI_TOKEN`, else a random hex token | Auth token for the UI. |

**Security (CLAUDE.md §18 invariant):** the UI is a *control plane* — it can
start processes, edit the config, and type into agents that may run
`--dangerously-skip-permissions` / `--yolo`. It therefore binds **`127.0.0.1` by
default**. Any non-loopback bind (e.g. `--host 0.0.0.0`) **requires a token**
(enforced inside `ui.run_server`). When no token is supplied one is generated and
printed to stderr along with the served URL. For a safe local-only run, omit
`--host`/`--token`.

```bash
# Safe local-only: loopback + auto port + generated token (printed to stderr)
agentainer serve -c my-swarm.yaml

# Remote bind — token is mandatory off-loopback (use a placeholder, never a real key)
agentainer serve -c my-swarm.yaml --host 0.0.0.0 --port 8000 --token <UI_TOKEN>
```

---

# Dynamic reconcile (P4)

These rewrite `agentainer.yaml` with the stdlib-only YAML emitter (works with or
without PyYAML) and reconcile the change into the running swarm.

## `add`

**Purpose:** Add an agent to the config and bring it up immediately.

```
agentainer add [-c <config>] <name> --type <type> --command <cmd>
               [--can-talk-to <acl>] [--role <text>] [--workdir <path>]
```

| Flag / positional | Default | Meaning |
| --- | --- | --- |
| `name` | *(required)* | New agent name. |
| `--type <type>` | *(required)* | Agent type: `claude` \| `codex` \| `gemini` \| `hermes`. |
| `--command <cmd>` | *(required)* | Shell command that launches the agent CLI. |
| `--can-talk-to <acl>` | `user` | Comma-separated ACL, or `*` for everyone. |
| `--role <text>` | `""` | Standing role / first prompt. |
| `--workdir <path>` | `<root>/<name>` | Working directory. |

```bash
agentainer add dave --type codex --command "codex" \
  --can-talk-to "alice,user" -c my-swarm.yaml
```

## `remove`

**Purpose:** Remove an agent from the config and stop its session.

```
agentainer remove [-c <config>] <name>
```

| Positional | Meaning |
| --- | --- |
| `name` | Agent to remove. |

```bash
agentainer remove bob -c my-swarm.yaml
```

## `edit`

**Purpose:** Edit an agent's fields in the config and reconcile.

```
agentainer edit [-c <config>] <name> -s key=value [-s key=value ...]
```

| Flag / positional | Default | Meaning |
| --- | --- | --- |
| `name` | *(required)* | Agent to edit. |
| `-s`, `--set key=value` | — | A field to set; repeatable for multiple fields. |

```bash
agentainer edit alice -s can_talk_to="dave,user" -c my-swarm.yaml
agentainer edit dave -s role="reviewer" -s can_talk_to="*" -c my-swarm.yaml
```

> `-s key=value` sets scalar (and simple comma-list) fields. Nested structures
> like a `pings:` schedule (a list of `message`/`cron`/`when_busy` mappings) are
> edited directly in `agentainer.yaml` — see
> [`configuration.md` §5 `pings`](configuration.md#pings).

## `reconcile`

**Purpose:** Start agents present in the config but not running, and stop
sessions no longer in the config — bringing the running set in line with the
YAML.

```
agentainer reconcile [-c <config>]
```

No flags.

```bash
agentainer reconcile -c my-swarm.yaml
```

---

# Lifecycle / state

## `remove-session`

**Purpose:** Delete all Agentainer-generated state so the next `up` starts
completely fresh.

```
agentainer remove-session [-c <config>]
```

No flags. Removes two categories of state (both gitignored, never shipped):

- The orchestrator runtime `.agentainer/` — `sessions.yaml` (conversation ids),
  the per-agent queue, turn state, the durable log, and the run dir.
- Each agent's five mailbox folders: `inbox/`, `outbox/`, `read/`, `sent/`,
  `failed/` (any in-flight mail).

It never touches the agent workspaces' own files (source code) or the config.
**It refuses while any agent — or the supervisor — is still running**
(pulling state from under a live agent corrupts it); run `down` first.

```bash
agentainer down -c my-swarm.yaml
agentainer remove-session -c my-swarm.yaml
```

---

# Internal (not for direct use)

These commands are driven by the orchestrator's own machinery. Documented here
for completeness; you should not normally run them by hand.

## `hook`

**Not for direct use.** Turn-completion entry point that an agent's installed
completion hook calls. It discovers which swarm/agent is calling, records the
conversation id (for Claude/Codex), sweeps the outbox, finishes the turn, and
releases/nudges recipients. It always returns 0 so it can never break the agent.

```
agentainer hook [-c <config>] <type> [payload] [--agent <name>]
```

| Flag / positional | Default | Meaning |
| --- | --- | --- |
| `type` | *(required)* | One of `claude`, `codex`, `generic`. Claude reads its JSON payload from stdin; Codex passes it as `payload`. |
| `payload` | — | JSON payload (Codex passes it as an argv string). |
| `--agent <name>` | detected from cwd/env | Override the detected agent name. |

## `watch`

**Not for direct use.** Pane-poller fallback for pane-capture agents
(`gemini`/`hermes`) whose CLI cannot call a program on turn completion. Polls the
tmux pane; when it stops changing for long enough, treats the turn as done. Exits
when the session disappears. Errors out for non-pane-capture agents.

```
agentainer watch [-c <config>] <agent>
```

| Positional | Meaning |
| --- | --- |
| `agent` | Agent whose pane to poll (must be `capture: pane` and running). |

## `supervise`

**Not for direct use.** The background liveness watchdog — the heartbeat the
event-driven core relies on. Reconciles stale-busy, dead, and silent-but-alive
agents on a timer.

```
agentainer supervise [-c <config>] [names ...]
```

| Positional | Default | Meaning |
| --- | --- | --- |
| `names ...` | all agents | Agents to watch. |

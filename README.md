# 🐝 Agentainer

**Zero-dependency multi-agent orchestrator with a file-based mail model.**

[![license: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![runtime deps: zero](https://img.shields.io/badge/deps-zero-blue.svg)](https://github.com/mehmetcanfarsak/Agentainer#readme)
[![platform: linux | macOS](https://img.shields.io/badge/platform-linux%20%7C%20macOS-lightgrey.svg)](https://github.com/mehmetcanfarsak/Agentainer#readme)
[![line coverage: 100%](https://img.shields.io/badge/coverage-100%25-brightgreen.svg)](https://github.com/mehmetcanfarsak/Agentainer#readme)

> **Formerly AgentSwarm.** The v1 project was renamed and rewritten around a
> file-based mail model — the old name still resolves to this repo.

Agentainer launches AI coding agents (Claude Code, Codex, Gemini, Hermes) each in
its own `tmux` session and working directory, defined by a single
`agentainer.yaml`. They talk to each other only where the config's `can_talk_to`
ACL allows — by **reading a file** to receive and **writing a file** to send.

No runtime dependencies. No API keys required to exercise the mechanics. Agents
only need to read and write natural-language files; the orchestrator owns all the
hard logic (routing, ACL, message IDs, threading, read-state, queueing, retries,
availability, the durable log).

<p align="center">
  <img src="https://raw.githubusercontent.com/mehmetcanfarsak/Agentainer/main/assets/banner.svg" alt="Agentainer banner: zero-dependency multi-agent orchestrator with a file-based mail model" width="720"/>
</p>

> v2 replaces v1's "tagged XML envelope emitted inside prose and scraped out of a
> TUI pane" — that was unreliable across LLMs. The file-based mail model works on
> nearly every tool-calling model, including weak ones.

---

## 📑 Contents

- [Install](#install)
- [Quick start (no API keys)](#quick-start-no-api-keys)
- [The model in one screen](#the-model-in-one-screen)
- [Scheduled pings (cron)](#scheduled-pings-cron)
- [Architecture](#architecture)
- [Commands](#commands)
- [Key invariants](#key-invariants)
- [The control-plane UI (P2–P3)](#the-control-plane-ui-p2p3)
- [Manage it from a coding agent (MCP)](#manage-it-from-a-coding-agent-mcp)
- [Dynamic reconcile (P4)](#dynamic-reconcile-p4)
- [Project status](#project-status)
- [FAQ](#faq)
- [License](#license)

---

## 📦 Install

One command, from npm:

```bash
npm install -g agentainer
agentainer --version
```

That's it — the command is now just **`agentainer`**, anywhere. The only thing
you need on the machine is **`tmux`** (the agents run inside it); `node` ships the
launcher. PyYAML is used *if present*, but a bundled `minyaml` parser keeps
everything working without it — nothing to `pip install`.

> Prefer to hack on the source? `git clone` the repo, then `npm install && npm
> link`. Everyday users don't need this.

## 🚀 Quick start (no API keys)

**The easiest way — open the control panel in your browser:**

```bash
agentainer serve
```

Open the `http://127.0.0.1:…` URL it prints, click **➕ New Swarm**, pick the
**`quickstart`** example, preview the config, and hit **Launch**. Creating swarms,
watching mail flow, editing settings — all point-and-click.

**Prefer the terminal?** Scaffold and start the same swarm from the CLI:

```bash
# scaffold the demo swarm from the bundled example and start it
agentainer swarms create demo --template quickstart --up

# it lives at ~/.agentainer/swarms/demo/agentainer.yaml — point commands at it:
CFG=~/.agentainer/swarms/demo/agentainer.yaml
agentainer status -c "$CFG"
agentainer send   -c "$CFG" --to orchestrator "Build a CSV->Parquet CLI."
agentainer logs   -c "$CFG" -f
agentainer swarms down demo
```

**Want to try the mechanics with zero API keys / zero spend?** The example's
`command:` lines launch real CLIs (`claude`, `codex`, `gemini`). Before you
launch, edit the config — in the UI or the YAML — and swap each `command:` for a
mock bash loop that just idles:

```yaml
command: "bash -c 'while true; do read -r l || sleep 1; done'"
```

That's exactly what the test suite runs, so you can watch the orchestrator accept
mail with no models involved. To go live later, swap the real CLIs back in.

<p align="center">
  <img src="https://raw.githubusercontent.com/mehmetcanfarsak/Agentainer/main/assets/demo.svg" alt="Terminal cast of the Agentainer quickstart: up, status, send, then logs showing the mailroom routing mail between mock agents" width="700"/>
</p>

## 📬 The model in one screen

An agent's entire world is **two verbs** (read a file, write a file) and **four
folders**:

| 📁 Folder | 📝 Meaning |
| --- | --- |
| 📥 `inbox/` | The **one** current unread message (orchestrator releases one at a time). |
| 📤 `outbox/<name>/` | Write a file here to send to `<name>`. Read `<name>/about.md` for a contact card. |
| 📖 `read/` | Move a handled message here (best-effort read receipt). |
| 📨 `sent/` | The agent's own record of delivered mail (orchestrator moves it here). |
| 🚫 `failed/` | Mail bounced by the orchestrator (ACL violation, etc.), with a `system` explanation. |

When an agent **stops** with unread mail, the orchestrator sweeps its `outbox`
and pastes a **nudge** ("you have mail — read it, then move it to `read/`"),
re-injecting the protocol every time (including the allowed-recipient list).

Agents can also be nudged on a schedule — see **[Scheduled pings](#scheduled-pings-cron)**
below.

## ⏰ Scheduled pings (cron)

Most swarms are *reactive*: they move when a message lands. But some work needs
to **happen at a time** — a morning digest, a standup, a health sweep, a Friday
publish. Give any agent (or `defaults:`) a **`pings:`** list and the orchestrator
nudges it on a schedule, delivering each rule's `message` as ordinary `system`
mail. The model does nothing new — it just reads the file it's handed.

```yaml
agents:
  - name: chief
    type: claude
    can_talk_to: [user]
    pings:
      # message + a standard 5-field cron (minute hour day-of-month month day-of-week)
      - message: "Working hours: rebuild the digest and send it to user."
        cron: "*/30 9-18 * * 1-5"          # every 30 min, 09:00-18:30, Mon-Fri
      - message: "Off-hours: only flag something genuinely urgent."
        cron: "0 20-23,0-7 * * *"          # hourly overnight (comma list crosses midnight)
        when_busy: queue                   # wait for a mid-turn agent instead of dropping
      - message: "Weekend check-in."
        cron: "0 12 * * sat,sun"           # noon Sat & Sun (3-letter day names)
```

- **Zero-dependency cron** ([`lib/cron.py`](lib/cron.py)) — `*`, `*/step`,
  `a-b` ranges, `a-b/step`, comma lists, and 3-letter month/day names
  (`jan`–`dec`, `sun`–`sat`; day-of-week `0`/`7` both Sunday); standard Vixie
  dom/dow OR rule. Evaluated in the **host's local time**.
- **`when_busy: skip` (default) | `queue`** — a rule that comes due while the
  agent is mid-turn is either dropped (so a busy mailbox never fills with stale
  pings) or made to wait. Use `skip` for high-frequency checks where the next
  tick makes a missed one moot; `queue` when the tick is the point.
- **Safe by construction** — cron is validated **fail-fast at config load**; at
  most one unhandled ping is outstanding per agent (no pile-up); and each rule
  fires at most once per matching minute.

Full field reference and cron table: [`docs/configuration.md`](docs/configuration.md#pings).
Ready-to-run showcases:

| Example | What it shows |
| --- | --- |
| [`scheduled-standup`](examples/scheduled-standup.yaml) · [guide](docs/use-cases/scheduled-standup.md) | A self-running async standup — different agents on different schedules. |
| [`daily-briefing`](examples/daily-briefing.yaml) · [guide](docs/use-cases/daily-briefing.md) | A morning digest, self-triggered on a weekday + weekend schedule. |
| [`ops-watchtower`](examples/ops-watchtower.yaml) · [guide](docs/use-cases/ops-watchtower.md) | High-frequency `*/15` monitoring with `skip` + overnight `queue`. |
| [`content-cadence`](examples/content-cadence.yaml) · [guide](docs/use-cases/content-cadence.md) | Cron as a *weekly calendar* — day-of-week and day-of-month scheduling. |
| [`data-quality-guardian`](examples/data-quality-guardian.yaml) · [guide](docs/use-cases/data-quality-guardian.md) | A self-driving monitor — business-hours `skip` sweep + overnight `queue` deep check. |
| [`cloud-cost-optimizer`](examples/cloud-cost-optimizer.yaml) · [guide](docs/use-cases/cloud-cost-optimizer.md) | FinOps cadence — a weekly cost-review ping (`when_busy: skip`). |
| [`chaos-game-day`](examples/chaos-game-day.yaml) · [guide](docs/use-cases/chaos-game-day.md) | Pre-approved, reversible fault injection on a schedule. |
| [`vuln-triage`](examples/vuln-triage.yaml) · [guide](docs/use-cases/vuln-triage.md) | Daily CVE scan → risk rank → patch plan. |
| [`fp-and-a-analyst`](examples/fp-and-a-analyst.yaml) · [guide](docs/use-cases/fp-and-a-analyst.md) | Monthly-close variance + forecast-narrative ping. |

The full catalog of runnable recipes — now 96 `examples/*.yaml`, each with a
walkthrough under [`docs/use-cases/`](docs/use-cases/) — is indexed in
[`docs/README.md`](docs/README.md).

## 🏗️ Architecture

<a href="https://raw.githubusercontent.com/mehmetcanfarsak/Agentainer/main/assets/architecture.svg">
  <img src="https://raw.githubusercontent.com/mehmetcanfarsak/Agentainer/main/assets/architecture.svg" alt="Agentainer architecture: agentainer.yaml drives a CLI core that opens one tmux session per agent; agents read and write mail files that the orchestrator routes" width="720"/>
</a>

<details>
<summary>Plain-text architecture (for terminals &amp; screen readers)</summary>

```
agentainer.yaml  ──▶  agentainer up  ──▶  one tmux session + workdir per agent
 (agents +                (CLI core              │
  can_talk_to)            + supervisor)           ▼
                          │                agents READ inbox/  WRITE outbox/<name>/
                          │                       │                │
                          │                       ▼                ▼
                          │                orchestrator routes the mail:
                          │                ACL check, message ID, read-state,
                          │                queue, durable JSONL log
                          ▼
                   liveness supervisor heartbeat
                   (reconciles stale-busy / dead / silent-but-alive)
```
</details>

## 🛠️ Commands

| 🛠️ Command | 📝 Purpose |
| --- | --- |
| 🔍 `validate` | Resolve and print the config; launch nothing. |
| 🆙 `up` | Create dirs + mailbox folders, install per-type turn-detection, open one tmux session per agent. |
| 🔌 `down` | Tear the swarm down. |
| 🔁 `restart` | Down then up. |
| 📡 `status` | Show agent/health summary. |
| 🔗 `attach` | Attach to an agent's tmux session. |
| ✉️ `send` | Send a `user` message into the swarm (`--to <agent>`). |
| 🗂️ `sessions` | List recorded tmux sessions (resume info). |
| ⏳ `queue` | Show pending mail per agent. |
| 😴 `idle` | List agents currently idle. |
| 📨 `inbox` | Show current inbox message per agent. |
| 📟 `logs` | Tail the durable JSONL event log (`-f` to follow). |
| 🪝 `hook` | Turn-completion entry point (called by `claude`/`codex` stop hooks). |
| 👀 `watch` | Live-watch the supervisor. |
| 💗 `supervise` | Run one (or the loop of) supervisor tick(s). |
| 👤 `user` | Toggle `user` availability. |
| 🌐 `serve` | Serve the **multi-swarm** mail-app control-plane UI (every swarm on the machine, create/launch swarms, settings, direct-to-pane) on `127.0.0.1`. |
| 🤖 `mcp` | Run the **MCP server** on stdin/stdout so a *coding agent* can monitor and manage every swarm (also at `POST /mcp` on `serve`). |
| 🐝 `swarms` | Manage every swarm on the machine: `list`, `create [--template]`, `register`, `remove`, `up`, `down`, `build` (coding-agent scaffolds it), `approve`, `use`. |
| ➕ `add` | Add an agent to the config (YAML) and bring it up immediately. |
| 🗑️ `remove` | Remove an agent from the config and stop its session. |
| ✏️ `edit` | Edit an agent's fields in the config (`-s key=value`, repeatable) and reconcile. |
| 🔧 `reconcile` | Start agents missing from the running set / stop sessions no longer configured. |

See `llms.txt` for a machine-readable summary and `ProjectPlan.md` for the full
design (source of truth).

<p align="center">
  <img src="https://raw.githubusercontent.com/mehmetcanfarsak/Agentainer/main/assets/screenshot-status.svg" alt="Terminal output of agentainer status and agentainer queue against a running mock swarm" width="700"/>
</p>

## 🔒 Key invariants

- 🪶 **Zero runtime dependencies, forever** — Python 3 + bash + tmux; bundled
  `minyaml` fallback; stdlib-only UI (P2+).
- 🤝 **`can_talk_to` is cooperative, not an OS boundary** — enforced for
  well-behaved agents, documented honestly (Decision D15).
- 📛 **`user` / `system` are reserved virtual mailboxes.**
- 💗 **Liveness supervisor heartbeat retained** — no event-only redesign.
- 🗄️ **Durable JSONL event log is the source of truth** for history (TUIs keep no
  scrollback).
- 🧪 **100% line coverage** via mock agents (bash loops, no API keys).

## 🖥️ The control-plane UI (P2–P3)

`agentainer serve` starts a **zero-dependency** web UI (stdlib `http.server` + a
single vanilla-JS page, no framework, no build step). It binds `127.0.0.1` by
default; any non-loopback bind requires a token. The UI is a control plane, so
keep it on loopback unless a token is supplied.

**One `serve` runs your whole machine.** It manages **every swarm at once** (a
global registry under `~/.agentainer/`, overridable with `$AGENTAINER_STATE_DIR`)
— no need to run a server per swarm or pass `-c`. Any swarm you `up` (from any
directory) auto-registers and appears in the dashboard. It runs **out of the
box**: land on an empty dashboard and create your first swarm in a few clicks;
sensible defaults everywhere, with the rarely-touched knobs tucked under
**"Configure Advanced Settings."**

- **Swarms dashboard** — a card per swarm (running/total agents, attention,
  Start-all / Stop-all, Open), plus a prominent **➕ New Swarm**. A header
  **swarm switcher** scopes every view to the selected swarm.
- **Create a swarm, three ways** — start from one of the **bundled example
  configs** (preview the YAML and **edit it yourself**), or **have a coding-agent
  build it for you**: pick a CLI (`claude`/`codex`/`gemini`/`hermes`), and
  Agentainer opens an **interactive tmux session** you talk to *in the browser*
  ("adapt this example…" or "design a swarm that…"). It writes the
  `agentainer.yaml`; click **Approve & Launch** to bring it up.
- **Agents overview** — a card per agent with its role, type, and live
  running/idle/busy/unread/queue status.
- **Mail view** — click an agent to open its correspondence. The left rail lists
  its contacts (the `user`, its peers, `system`) instead of mail folders; pick
  one and every message between the two is reconstructed into a threaded
  conversation (rendered as **markdown**), each with its **delivery status**
  (waiting → delivered → read). Reply to an agent **as the `user`** inline.
- **Activity + topology** — a global event timeline and a who-talks-to-whom graph.
- **Direct to the session** — watch each agent's **live tmux pane** and type
  straight into it (bypassing the mailroom) when you need to.
- **Availability toggle** — flip whether the `user` is available to receive mail
  (off by default: mail is *held*, never bounced) right from the top bar; the
  change is persisted to `agentainer.yaml`.
- **Settings & agents** — edit swarm settings and **add / edit / delete agents**
  from the UI; every change is written back to `agentainer.yaml` (via the same
  stdlib emitter, so it works with **or without** PyYAML).
- **Telegram bridge** *(optional, shared across all swarms)* — configure **one**
  bot token + chat id once (in Settings → Telegram; stored globally in
  `~/.agentainer/`) and **every** swarm shares it — a per-swarm `telegram:` block
  still overrides it for heavy users. It **mirrors** mail to your chat and lets
  you **drive any swarm from your phone**: reply to a mirrored message to answer
  its sender, run slash-commands (`/status`, `/to`, `/up`, …), list swarms with
  `/swarms`, and switch which swarm bare commands target with `/use <name>`. A
  first-login nudge reminds you to enable it so you can *"use this system
  everywhere."* Stdlib `urllib` only — no new dependency; best-effort so the
  network can never stall routing.

## 🤖 Manage it from a coding agent (MCP)

Agentainer *manages* coding agents, so it also lets a coding agent **manage
Agentainer** — over the **Model Context Protocol**. This is the fourth control
plane (CLI / UI / Telegram / **MCP**), a permanent first-class surface with full
parity: the same tools cover monitoring (`list_swarms`, `swarm_status`,
`read_inbox`, `agent_logs`, `capture_pane`, …) and management (`send_message`,
`up_swarm`/`down_swarm`, `start_agent`/`stop_agent`, `create_swarm`,
`add_agent`/`remove_agent`, `set_availability`).

Two transports, one tool set:

- **stdio** — `agentainer mcp`. No running `serve` needed; it works over the
  global swarm registry. Add it to your agent's MCP config (e.g. Claude Code's
  `.mcp.json`):

  ```json
  {"mcpServers": {"agentainer": {"command": "agentainer", "args": ["mcp"]}}}
  ```

- **HTTP** — `POST /mcp` on a running `agentainer serve`, reusing the UI's Bearer
  token.

It's plain JSON-RPC 2.0 (zero new dependencies), and — like the mailroom's
`system` mail — tool problems come back as readable `isError` results the model
self-corrects on. Full guide: **[docs/mcp.md](docs/mcp.md)**.

## 🔄 Dynamic reconcile (P4)

No need to tear the whole swarm down to change it:

```bash
agentainer add    dave --type codex --command "codex" --can-talk-to "alice,user" -c my-swarm.yaml
agentainer edit   alice -s can_talk_to="dave,user" -c my-swarm.yaml
agentainer remove bob -c my-swarm.yaml
agentainer reconcile -c my-swarm.yaml      # start missing agents, stop orphaned sessions
```

`add`/`remove`/`edit` rewrite `agentainer.yaml` with a stdlib-only YAML emitter
(works with **or without** PyYAML) and then `reconcile` the change into effect.

## 📊 Project status

- **P1 — Mail runtime (CLI-driven)**: ✅ done.
- **P2 — UI observability**: ✅ done.
- **P3 — terminal snapshot + send-from-UI**: ✅ done.
- **P4 — dynamic reconcile (`add`/`remove`/`edit`/`reconcile`)**: ✅ done.
- **P5 — multi-swarm control plane (one `serve`, global registry + shared settings)**: ✅ done.
- **P6 — guided swarm creation (examples, inline edit, coding-agent builder)**: ✅ done.
- **P7 — redesigned beginner-friendly UI (swarm switcher, dashboard, advanced-settings collapse)**: ✅ done.
- **P8 — shared Telegram across all swarms + CLI/UI/Telegram parity**: ✅ done.
- **P9 — MCP server (fourth control plane: a coding agent manages the swarms)**: ✅ done.

🎉 100% line coverage across all core + UI + reconcile modules, driven entirely by
mock agents (no API keys).

## ❓ FAQ

**🤖 What is Agentainer?**
Agentainer is a zero-dependency orchestrator that runs several AI coding agents
(Claude Code, Codex, Gemini, Hermes) at once, each in its own `tmux` session and
working directory. Instead of agents exchanging messages inside their chat prose,
they communicate through a **file-based mail model**: an agent receives mail by
reading a file in its `inbox/` and sends mail by writing a file into
`outbox/<name>/`. The orchestrator owns all the hard logic — routing, access
control, message IDs, retries, and the durable log — so the agents only deal with
plain natural-language files.

**🧩 Which AI coding agents does Agentainer support?**
Claude Code (`type: claude`), OpenAI Codex (`type: codex`), Google Gemini CLI
(`type: gemini`), and Hermes (`type: hermes`). Turn-completion detection is wired
per `type` (a Stop hook for Claude, a `notify` program for Codex, and pane polling
for Gemini/Hermes). Any CLI that can read and write files in a tmux pane can be
slotted in as a new `type`.

**🐳 Does Agentainer need Docker or any runtime dependencies?**
No. The entire runtime is Python 3, `bash`, and `tmux` — nothing to `pip install`.
PyYAML is used *if present*, but a bundled fallback parser (`minyaml`) keeps
everything working without it, and CI proves that no-PyYAML path. `node` is only
needed for the npm launcher, never at swarm runtime. Docker is optional and not
required.

**🤖 Can a coding agent monitor and manage Agentainer itself?**
Yes — that's what the built-in **MCP (Model Context Protocol) server** is for, and
it's a permanent first-class surface. Point any MCP-speaking agent (Claude Code,
Cursor, …) at Agentainer and it gets tools to observe (`list_swarms`,
`swarm_status`, `read_inbox`, `agent_logs`, `capture_pane`, …) and control
(`send_message`, `up_swarm`/`down_swarm`, `start_agent`/`stop_agent`,
`create_swarm`, `add_agent`/`remove_agent`, …) every swarm on the machine. Two
transports: run `agentainer mcp` (stdio — add `{"mcpServers":{"agentainer":
{"command":"agentainer","args":["mcp"]}}}` to your agent's config; no running
server needed), or `POST /mcp` on a running `agentainer serve` (reuses the UI
token). It's plain JSON-RPC 2.0 with zero new dependencies. See
[docs/mcp.md](docs/mcp.md).

**🚀 How do I run multiple coding agents together?**
Write one `agentainer.yaml` listing each agent, then run `agentainer up -c
my-swarm.yaml`. That creates the mailbox folders, installs per-type turn detection,
and opens one tmux session per agent. `agentainer status` shows the swarm;
`agentainer send --to <agent> "..."` injects a `user` message; `agentainer down`
tears it all down. The bundled `examples/quickstart.yaml` runs entirely on
key-free bash-loop mock agents so you can watch the mailroom route mail with no API
keys.

**💬 Can agents communicate with each other, and how is that controlled?**
Yes — by writing into each other's `outbox/<name>/` folders. Communication is
controlled by the `can_talk_to` access-control list in the config: an agent may
only deliver to names listed there (`"*"` means everyone). The ACL is enforced by
the orchestrator at routing time — a disallowed send is bounced back as a `system`
message and filed in `failed/`. It is cooperative, not an OS sandbox (an agent with
filesystem access *could* bypass it), so it's documented honestly rather than
presented as a security boundary.

**♻️ How do I resume a swarm after a crash or reboot?**
`agentainer up --resume -c my-swarm.yaml` reattaches recorded Claude and Codex
conversations via their native resume commands (`claude --resume <id>`,
`codex resume <id>`); Gemini and Hermes always start fresh with a warning, since
they have no resume bridge. Conversation ids are recorded in
`<root>/.agentainer/sessions.yaml` as each agent finishes its first turn.

**🛡️ Is it safe to let agents run unattended?**
The design assumes unattended operation. A liveness **supervisor heartbeat**
(reconciling stale-busy, dead, and silent-but-alive agents) is always running; a
per-agent health probe catches the "alive but silent" case the supervisor can't
otherwise see; and several safeguards prevent a wedged agent from stalling the
swarm — including auto-releasing the next message after N presentations and a
rate cap on runaway auto-exchanges. That said, agents can run
`--dangerously-skip-permissions`, so the control-plane UI binds `127.0.0.1` by
default and requires a token for any remote bind.

**⚖️ How is Agentainer different from just opening several terminals?**
With several terminals you manually paste prompts, watch for completion, and relay
messages by hand — and a model that forgets a step silently stalls. Agentainer
automates the whole loop: it releases exactly one inbox message at a time, detects
each turn's completion per `type`, sweeps outgoing mail on stop, enforces the
`can_talk_to` ACL, retries nudges, keeps a durable JSONL event log as the source of
truth, and runs a supervisor so one stuck agent can't wedge the rest. The models
only read and write files; all coordination is deterministic orchestrator code.

## 📜 License

MIT — see [LICENSE](LICENSE).

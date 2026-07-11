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
- [Architecture](#architecture)
- [Commands](#commands)
- [Key invariants](#key-invariants)
- [The control-plane UI (P2–P3)](#the-control-plane-ui-p2p3)
- [Dynamic reconcile (P4)](#dynamic-reconcile-p4)
- [Project status](#project-status)
- [FAQ](#faq)
- [License](#license)

---

## 📦 Install

```bash
git clone <repo> && cd Agentainer
npm install            # runs the postinstall dep check; no build step
npm link               # puts the `agentainer` bin on your PATH
agentainer --version
```

Once linked/installed, the command is just **`agentainer`**. `tmux` is the only
external runtime. `node` is required for the npm bin wrapper. PyYAML is used *if
present*; a bundled `minyaml` parser keeps everything working without it.

## 🚀 Quick start (no API keys)

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
| 🌐 `serve` | Serve the mail-app control-plane UI (threads, settings/agent editing, availability, direct-to-pane) on `127.0.0.1`. |
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

`agentainer serve -c my-swarm.yaml --port 8080 --token <secret>` starts a
**zero-dependency** web UI (stdlib `http.server` + a single vanilla-JS page, no
framework, no build step). It binds `127.0.0.1` by default; any non-loopback bind
requires a token. The UI is a control plane, so keep it on loopback unless a
token is supplied.

It is a **modern, mobile-friendly mail app** for the swarm:

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
- **Telegram bridge** *(optional)* — configure a bot token + chat id in Settings
  to **mirror** the swarm's mail (all agents or a selected set, plus your own
  mail) to a Telegram chat, and **reply from your phone**: a Telegram message
  reply routes back into the swarm as `user` mail. Stdlib `urllib` only — no new
  dependency; the mirror is best-effort so the network can never stall routing.

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

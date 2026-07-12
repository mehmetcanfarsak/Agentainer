# Getting Started with Agentainer

A short, practical walk-through for first-time users. By the end you'll have a
swarm of coding agents running in `tmux`, talking to each other through the
file-based mail model, and you'll know how to watch and stop it.

---

## 1. What Agentainer is

Agentainer is a **zero-dependency multi-agent orchestrator**. It launches coding
agents — Claude Code, Codex, Gemini, and Hermes — each in its own `tmux` session
and its own working directory, entirely described by a single `agentainer.yaml`.
Instead of agents passing messages inside their chat prose, they communicate
through a **file-based mail model**: an agent *receives* mail by reading a file
in its `inbox/`, and *sends* mail by writing a file into `outbox/<name>/`. Who is
allowed to talk to whom is gated by a `can_talk_to` access-control list in the
config.

The orchestrator owns all the hard logic — routing, ACL enforcement, message
IDs, threading, read-state, queueing, retries, availability, and the durable
event log — so the agents only ever deal with plain natural-language files. This
means it works on nearly every tool-calling model, including weak ones.

Runtime deps: **Python 3 + bash + tmux**. PyYAML is used if present, but a
bundled `minyaml` parser keeps everything working without it. No `pip install`.

---

## 2. Install / requirements

You need three things on your machine:

- **Python 3** — used by the orchestrator (`lib/cli.py`). No third-party packages.
- **`tmux`** — each agent runs in its own tmux session. Without it, every command
  except `validate` will fail.
- The **agent CLIs** you intend to use (e.g. `claude`, `codex`, `gemini`,
  `hermes`) — only if you're running real agents; the quickstart can run on
  key-free mock agents.

Agentainer itself has **no install step** that pulls dependencies. You invoke it
three equivalent ways:

```bash
# 1. From the repo, the ./agentainer launcher (resolves python3 for you):
./agentainer --help

# 2. Directly through the Python entry point:
python3 lib/cli.py --help

# 3. Via the npm wrapper (optional; node >= 16 for the bin only, never at runtime):
npm install      # runs a dependency check; no build step
npm link         # puts `agentainer` on your PATH
agentainer --version
```

`--version` prints the version from `package.json` (currently `2.0.0`). Run
`agentainer --help` any time to see the full subcommand list.

> The repo also ships `examples/` with ready-to-run swarms; `agentainer.example.yaml`
> is a minimal template you can copy to `agentainer.yaml`.

---

## 3. Your first swarm

Start from the bundled example. It uses key-free "mock agents" (bash loops) so
you can watch the mailroom route mail with **no API keys at all**:

```bash
cp examples/quickstart.yaml my-swarm.yaml
```

Here is what `examples/quickstart.yaml` says, top to bottom:

```yaml
swarm:
  name: quickstart
  root: ./quickstart-workspace      # where all workdirs + mailboxes live

defaults:
  capture: none                     # mock agents don't fire a turn-completion hook
  can_talk_to: []                   # tightened per agent below

agents:
  - name: orchestrator
    type: claude                     # claude | codex | gemini | hermes
    can_talk_to: "*"                 # talks to everyone
    command: "claude --dangerously-skip-permissions"
    role: "You are the orchestrator. Wait for the user's task, then delegate."

  - name: researcher
    type: gemini
    can_talk_to: [orchestrator, developer]
    capture: pane
    command: "gemini --yolo"

  - name: developer
    type: codex
    can_talk_to: [orchestrator, reviewer]
    command: "codex --yolo"

  - name: reviewer
    type: claude
    can_talk_to: [developer]
    command: "claude --dangerously-skip-permissions"
```

Field by field:

- **`swarm.name`** — a label for the swarm; also used to name tmux sessions.
- **`swarm.root`** — the base directory under which every agent's `workdir` and
  mailbox live (created automatically). You can use an absolute path or one
  relative to the config file.
- **`defaults`** — values applied to every agent unless the agent overrides them.
  - **`defaults.capture`** — how the orchestrator detects that an agent finished a
    turn: `hook` (Claude/Codex call a stop hook), `pane` (Gemini/Hermes are polled),
    `none`, or `auto` (the type's natural default). Mock agents use `none`.
  - **`defaults.can_talk_to`** — the default ACL. Each agent overrides it.
- **An agent** is a mapping with:
  - **`name`** — unique; also the tmux session name and mailbox directory name.
    `user` and `system` are reserved and cannot be used.
  - **`type`** — one of `claude`, `codex`, `gemini`, `hermes`. This selects how
    turn-completion is detected.
  - **`command`** — the shell command that launches this agent's CLI. **It must
    launch the same CLI that `type` implies** — `command: "claude ..."` with
    `type: claude`, `command: "codex ..."` with `type: codex`, etc. If they
    mismatch, the turn-completion signal never fires and the agent hangs forever
    ("silent deadlock"). `up` detects this and refuses to start (a `ConfigError`),
    so you never hit it by accident. A mock command (a bash loop) contains none of
    these CLI tokens and is always allowed.
  - **`can_talk_to`** — the access-control list: the names this agent may deliver
    mail to. `"*"` means everyone else; `user` is the operator's virtual mailbox;
    `system` is forbidden as a recipient.
  - **`role`** — the standing first-prompt / instructions the orchestrator pastes
    into the agent's session on first boot. (The old `first_prompt` name is
    deprecated.)

To run **real** agents instead of mocks, swap each `command` for the actual CLI
you installed and drop `capture: none` (let it default per type) so turns are
detected. Never put a real API key in a `command` string in a committed file —
use a shell alias or environment variable, and in docs use a placeholder like
`<your-openrouter-key>`.

Before launching anything, sanity-check the config (this launches nothing):

```bash
agentainer validate -c my-swarm.yaml
```

---

## 4. Bring it up

```bash
./agentainer up -c my-swarm.yaml
```

Under the hood, `up` does all of this for you:

1. **Resolves and validates** the config (catching the `type`/`command` mismatch
   described above).
2. **Creates each agent's workdir** plus its four mailbox folders (`inbox/`,
   `outbox/<name>/`, `read/`, `sent/`, and `failed/`).
3. **Installs per-type turn-detection** so the orchestrator knows when an agent
   stops: a **Stop hook** for `claude`, a `notify` program for `codex`, and
   **pane polling** for `gemini`/`hermes`.
4. **Opens one tmux session per agent** and pastes its `role` prompt as the first
   message.
5. With a real model attached, the agent reads its `inbox/` and begins working;
   the orchestrator releases further messages one at a time as each is handled.

When `up` finishes, it prints a couple of helpful hints, including exactly how to
launch the control-plane UI. The safe form binds **`127.0.0.1`** only:

```bash
agentainer serve -c my-swarm.yaml --port 8000
```

If you want to reach the UI from another machine you must also pass a **`--token`**
— a token is required for any non-loopback (`0.0.0.0`) bind, because the UI is a
control plane that can type into agent sessions. (See `ui-guide.md`.)

> Tip: `up` **resumes** recorded conversations by default. To start fresh, pass
> `up --no-resume`. See §7 and `sessions-and-resume.md`.

---

## 5. The mail model in plain English

An agent's entire world is **two verbs** — read a file, write a file — across
**four folders** in its mail dir:

| Folder | Meaning |
| --- | --- |
| `inbox/` | The **one** current unread message. The orchestrator only ever releases one at a time. |
| `outbox/<name>/` | Write a file here to send to `<name>`. Read `<name>/about.md` first — it's a contact card telling the agent who `<name>` is. |
| `read/` | Move a handled message here (a best-effort "I processed it" receipt). |
| `sent/` | The agent's own record of mail that was delivered (the orchestrator moves it here). |

`failed/` holds mail the orchestrator refused (almost always an ACL violation),
with a `system` explanation inside.

The orchestrator does the bookkeeping: when an agent **stops** with outgoing mail,
it **sweeps `outbox/`**, checks each recipient against `can_talk_to`, delivers the
message, and drops a `system` bounce into `failed/` (and the sender's `inbox/`)
if the ACL says no. If an agent stops with an unread `inbox/` message, it pastes a
**nudge** ("you have mail — read it, then move it to `read/`") — re-injecting the
protocol and the allowed-recipient list every time, so a forgetful model can't
wedge the swarm. A deeper explanation lives in `mail-model.md`.

---

## 6. Watching it work

Once the swarm is up, use these read-only commands to observe it:

```bash
./agentainer status -c my-swarm.yaml        # which agents are running + health
./agentainer attach -c my-swarm.yaml developer   # tmux-attach into an agent's session
./agentainer logs   -c my-swarm.yaml -f      # tail the durable JSONL event log (follow)
./agentainer inbox  -c my-swarm.yaml         # print each agent's current inbox message
```

A few more you'll reach for:

```bash
./agentainer queue  -c my-swarm.yaml developer   # pending mail for an agent
./agentainer idle   -c my-swarm.yaml developer   # list idle agents (or force one idle)
./agentainer send   -c my-swarm.yaml --to orchestrator "Build a CSV->Parquet CLI."
./agentainer sessions -c my-swarm.yaml           # recorded conversation ids (resume info)
```

`attach` drops you into the agent's live tmux pane; type `Ctrl-b d` to detach
without killing it. `logs -f` is the source of truth for history — TUIs keep no
scrollback.

---

## 7. Stopping

To tear the swarm down:

```bash
./agentainer down -c my-swarm.yaml
```

This kills the tmux sessions. By default, **`up` later resumes** the recorded
conversations — `claude` and `codex` are reattached via their native resume
commands; `gemini`/`hermes` start fresh with a warning, since they have no resume
bridge. Conversation ids are recorded in `<root>/.agentainer/sessions.yaml`.

To get a **completely clean start** — wiping runtime state and all mailboxes so
the next `up` finds nothing to resume — run:

```bash
./agentainer remove-session -c my-swarm.yaml
```

Then `up` again and you're brand new. (`restart` is just `down` + `up`.)

---

## 8. Where to go next

- `configuration.md` — the full `agentainer.yaml` schema (every field, per-agent
  overrides, `agent_types`, `telegram:`, tuning knobs).
- `cli-reference.md` — every subcommand and flag, with examples.
- `mail-model.md` — the file-based mail model in depth (headers, threading,
  nudges, `failed/`, read receipts).
- `ui-guide.md` — the `serve` control-plane UI (threads, settings, agent editing,
  live pane, Telegram bridge), and the loopback/token binding rule.
- `sessions-and-resume.md` — how resume works, `sessions.yaml`, and `remove-session`.
- `telegram-bridge.md` — mirror the swarm's mail to a Telegram chat and reply
  from your phone.
- `use-cases/` — worked examples and recipes for common multi-agent setups.

When in doubt, `agentainer <command> --help` is authoritative for the flags, and
`ProjectPlan.md` is the design record.

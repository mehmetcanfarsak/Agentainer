# Use case: surviving a reboot (down then up restores each agent's conversation)

You run a long-lived Agentainer swarm on a workstation or a small server. It
does real work — research, code changes, reviews — across several agents that
each hold a running conversation with their coding-agent CLI (Claude Code,
Codex, …). Then the machine reboots: an OS update, a power loss, or you simply
ran `agentainer down` for the night. When you come back, you want every agent
to **pick up its conversation exactly where it left off**, not wake up blank and
have to be re-briefed by hand.

That is the default behaviour. This document explains why it works, what
survives, what does not, and the one sharp edge that can silently defeat it.

> Cross-reference: the full mechanics of `sessions.yaml`, `resume_command` /
> `resume_args`, and `remove-session` live in
> [`sessions-and-resume.md`](../sessions-and-resume.md). This page is the
> practical "my machine rebooted" walkthrough.

---

## 1. The scenario

Picture a three-agent swarm: an `orchestrator` that triages work, a `developer`
that edits code, and a `reviewer` that checks the diffs. They have been running
for two days. Each agent holds a multi-turn conversation — the developer has
30 turns of tool calls behind it, the reviewer remembers the style rules you
told it on turn one.

The laptop installs a kernel update and reboots overnight. `tmux` is gone, so
every agent's pane is dead. But the disk is intact. In the morning you want:

```
$ agentainer up -c my-swarm.yaml
# ...orchestrator resumes its conversation
# ...developer resumes its 30-turn conversation
# ...reviewer resumes its conversation
# nobody needs re-briefing; the in-flight mail is still in the folders
```

Agentainer is designed for exactly this. **Resume is the default.** You do not
need any special flag on the second `up`.

---

## 2. What persists vs what doesn't

Two classes of state exist, and only one of them is durable across a reboot.

### Survives on disk (durable)

| State | Where | Notes |
| --- | --- | --- |
| Each agent's **conversation id** | `<root>/.agentainer/sessions.yaml` | Written by the orchestrator after each agent finishes its first turn (see `record_session`). This is the bridge that makes resume possible. |
| The **agent workspaces** (source code, notes) | each agent's `workdir` | Never touched by Agentainer lifecycle commands. |
| The **mail folders** (inbox/outbox/read/sent/failed) | inside each `workdir` (or a custom `mail_dir`) | In-flight mail survives. An agent that was mid-task still has its last prompt waiting in `inbox/`. |
| Your **config** (`agentainer.yaml`) | wherever you keep it | Untouched. |
| The durable **JSONL event log** | `<root>/.agentainer/logs/*.jsonl` | History of everything that happened. Fullscreen TUIs keep no scrollback, so this is the only recovery path for history. |

`sessions.yaml` is keyed by agent name and stores, per agent: `session_id`,
`type`, `workdir`, and `updated_at`. The orchestrator reads it on every `up`
when resume is on.

### Does NOT survive (lost on reboot)

| State | Why |
| --- | --- |
| The **tmux sessions** themselves | `tmux` is an in-memory server. A reboot kills it; all panes die. This is expected and harmless — the conversation lives in the CLI's own transcript (the `session_id`), not in tmux. |
| Orchestrator **runtime memory** (per-agent queue, turn state, run dir) | Rebuilt fresh on the next `up`. The queue's pending mail is also mirrored in the durable mail folders, so nothing is lost. |
| The **liveness supervisor** process | It's a normal OS process; the reboot ends it. It is **not** auto-restarted. You re-run `up` to bring it back (see §6). |

The mental model: **tmux is just the window; the conversation is the `session_id`;
the disk holds the `session_id`.** Rebooting breaks the window, not the
conversation.

---

## 3. The happy path

### Step 1 — the swarm is running (first launch, ever)

On the very first `up`, there is no `sessions.yaml` yet, so resume is
**implicitly a no-op and stays silent**: every agent starts a fresh conversation
and, after its first turn completes, the orchestrator records its `session_id`
into `.agentainer/sessions.yaml`. You see nothing about resume — that's by
design (a default first launch must not nag).

### Step 2 — down (or a reboot)

```
$ agentainer down -c my-swarm.yaml        # orderly shutdown, kills tmux panes
# ...or the machine just rebooted...
```

`down` stops the tmux sessions (and the supervisor), but **never deletes**
`sessions.yaml` or the mail folders. A reboot does the same thing, more
abruptly.

### Step 3 — back up, resume is automatic

```
$ agentainer up -c my-swarm.yaml
:: my-swarm: orchestrator: resuming conversation a1b2c3d4-...
:: my-swarm: developer: resuming conversation e5f6g7h8-...
:: my-swarm: reviewer: resuming conversation i9j0k1l2-...
:: swarm 'my-swarm' is up with 3 agent(s)
```

Because `swarm.resume` defaults to **true** (config.py: `SwarmConfig.resume =
True`), each agent is reattached to the conversation id recorded in
`sessions.yaml`. The mail that was sitting in `inbox/` is still there, so an
agent with unfinished business picks it up.

### Step 4 — confirm with `sessions`

```
$ agentainer sessions -c my-swarm.yaml
/.../my-swarm/workspace/.agentainer/sessions.yaml

  orchestrator (claude)
      conversation: a1b2c3d4-...
      last seen:    2026-07-12T02:14:33+00:00
  developer (claude)
      conversation: e5f6g7h8-...
      last seen:    2026-07-12T02:15:01+00:00
  reviewer (claude)
      conversation: i9j0k1l2-...
      last seen:    2026-07-12T02:14:58+00:00
```

`agentainer sessions --raw` prints the raw YAML. If an agent shows `-`
(no row), it simply has not finished a turn yet — its `session_id` is not
recorded, so its next `up` will start it fresh and quiet.

---

## 4. The shell-wrapped command caveat (read this before you rely on resume)

Resume works by **appending a `--resume <session_id>`-style flag to the
agent's `command`**. For a normal command like `claude
--dangerously-skip-permissions`, that's fine: the built-in `resume_args` for
`claude` is `--resume {session_id}`, and it's simply concatenated.

But if your `command` is **wrapped in a shell**, the flag gets *swallowed by the
shell* instead of reaching the CLI:

```yaml
agents:
  - name: developer
    type: claude
    # WRONG for resume: the --resume flag never reaches chy3.
    command: bash -ic 'chy3'
```

`bash -ic 'chy3'` runs `chy3` and then treats everything after the quoted string
as bash's own positional arguments — `chy3` never sees `--resume`. On the next
`up`, resume "succeeds" (no error) but the agent **silently starts a brand-new
conversation**, losing all its context. This is the single easiest way to
believe resume is working and have it quietly not.

### The fix: `resume_command`

`resume_command` is an **exact recipe** that wins over `resume_args`. You write
the full resume invocation yourself, with `{session_id}` interpolated in the
right place:

```yaml
agents:
  - name: developer
    type: claude
    command: bash -ic 'chy3'
    # CORRECT: the resume flag is inside the shell command, where chy3 sees it.
    resume_command: "bash -ic 'chy3 --resume {session_id}'"
```

When `resume_command` is set, the orchestrator uses it verbatim (substituting
`{session_id}`) instead of appending `resume_args` to `command`. Both
`{session_id}` and `{command}` are available for interpolation.

> **Rule of thumb:** if `command` contains `bash`/`sh`/`zsh` or an alias/shell
> function (anything where you can't just *append* a flag and have the CLI see
> it), you MUST provide `resume_command`. See
> [`sessions-and-resume.md`](../sessions-and-resume.md) for the full precedence
> rules (`resume_command` → `resume_args` → no recipe → fresh conversation) and
> the per-type built-ins (`claude --resume {session_id}`, `codex resume
> {session_id}`; `gemini`/`hermes` have no recoverable session id and always
> start fresh — they warn and continue).

---

## 5. Starting clean instead (no resume)

Resume is the default, but you can opt out at three levels, from most-local to
most-destructive.

### a) One-off: `--no-resume` on `up` or `restart`

```
$ agentainer up -c my-swarm.yaml --no-resume
$ agentainer restart -c my-swarm.yaml --no-resume
```

This starts fresh conversations **for this launch only** — it does not delete
`sessions.yaml`. The next plain `up` would resume again from the (now updated)
ids, because the agents immediately record new `session_id`s as they turn.

### b) Persistent default: `swarm.resume: false`

```yaml
swarm:
  name: my-swarm
  resume: false      # every `up` starts fresh unless --resume is passed
```

This flips the default for the whole config. Useful for swarms that are meant to
be stateless scratch work.

### c) Nuclear: `remove-session`

```
$ agentainer remove-session -c my-swarm.yaml
```

This deletes **every** piece of Agentainer-generated state: the `.agentainer/`
runtime (`sessions.yaml` with the conversation ids, queue, turn state, durable
log, run dir) **and** each agent's five mailbox folders. After this, the next
`up` finds no recorded conversations and starts fresh everywhere.

It **refuses to run while any agent (or the supervisor) is still alive** —
pulling state out from under a live agent corrupts it. Run `down` first:

```
$ agentainer down -c my-swarm.yaml
$ agentainer remove-session -c my-swarm.yaml
:: removed Agentainer session data (8 path(s)):
::   /.../my-swarm/workspace/.agentainer
::   /.../my-swarm/workspace/developer/inbox
::   ...
```

`remove-session` never touches the agents' own workspace files (source code) or
the config. Use it only when you genuinely want to discard everything Agentainer
knew.

---

## 6. Operational tips

- **Keep the config and workdirs on a persistent disk.** `sessions.yaml` lives
  under `<root>/.agentainer/` where `root` is `swarm.root` (default
  `./workspace`). If `root` (or the config) sits on a tmpfs / ephemeral volume,
  resume has nothing to read after a reboot. A normal local filesystem is fine;
  a network/USB disk is fine too — just make sure it's mounted before `up`.

- **`remove-session` is a discard, not a restart.** It's the escape hatch from
  the default-resume behaviour (and handy when a conversation has gone
  irredeemably sideways). Reach for it only when you truly want to throw away
  all recorded conversations and in-flight mail. A plain `down` + `up` already
  gives you a "stop and come back later" cycle without losing anything.

- **The liveness supervisor does not auto-restart across reboots.** The
  supervisor is an ordinary OS process that the reboot kills, like tmux. Nothing
  re-spawns it for you — **you re-run `up`** and `up` starts the supervisor
  again (`agentainer up` with `swarm.supervise: true`, the default). If you
  expect a swarm to be "always on" after power loss, the recovery action is a
  cron `@reboot` job (or a systemd service) that runs `agentainer up -c
  <cfg>` — not something the supervisor does on its own.

- **A mid-task agent is fine.** Because mail folders persist, an agent that was
  partway through a job still has its last message in `inbox/` after reboot; it
  resumes its conversation *and* finds the work waiting. No re-briefing needed.

- **`sessions` is your health check.** After any reboot-and-up, run
  `agentainer sessions` to confirm every agent has the expected
  `conversation` id and `last seen` timestamp. An agent stuck at `-` means it
  never recorded a session (likely a wrapped command without `resume_command` —
  see §4, or a type like `gemini`/`hermes` that can't resume).

---

## TL;DR

Resume is **on by default** (`swarm.resume: true`). `down` or a reboot kills the
tmux panes but leaves `sessions.yaml` and the mail folders on disk; the next
plain `up` reattaches every agent to its recorded conversation. Only two things
can break it: a **shell-wrapped `command` without `resume_command`** (flag
swallowed → silent fresh start), and **deleting the state yourself**
(`remove-session`, or `--no-resume` if you asked for it). To start clean, prefer
`--no-resume` or `swarm.resume: false`; reserve `remove-session` for when you
mean to discard everything. And remember: after a reboot **you** re-run `up` to
bring back both the agents and the supervisor.

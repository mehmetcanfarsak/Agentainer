# Agentainer v2 — Configuration Reference (`agentainer.yaml`)

This document is the authoritative reference for `agentainer.yaml`. It is generated
from the schema enforced by [`lib/config.py`](../lib/config.py) — the `Agent`,
`SwarmConfig`, and `TelegramConfig` dataclasses — and cross-checked against
[`agentainer.example.yaml`](../agentainer.example.yaml) and the runs under
[`examples/`](../examples/). Anything not described here does not exist; field
names and defaults are taken verbatim from the dataclasses.

If you want a commented, key-free starting point, copy
[`agentainer.example.yaml`](../agentainer.example.yaml):

```bash
cp agentainer.example.yaml my-swarm.yaml
agentainer up -c my-swarm.yaml
```

> **Secrets.** Never commit API keys or bot tokens. Treat every `command:` as
> sensitive (it may embed keys via shell aliases) and `telegram.bot_token` as a
> credential. Examples below use `<placeholder>`.

---

## Table of contents

1. [Top-level layout](#1-top-level-layout)
2. [`swarm:` block](#2-swarm-block)
3. [`defaults:` block](#3-defaults-block)
4. [`agents:` list](#4-agents-list)
5. [Per-agent field reference](#5-per-agent-field-reference)
6. [`telegram:` block](#6-telegram-block)
7. [Naming rules & reserved names](#7-naming-rules--reserved-names)
8. [Path placeholders](#8-path-placeholders)
9. [Worked examples](#9-worked-examples)
10. [Common mistakes](#10-common-mistakes)

---

## 1. Top-level layout

`agentainer.yaml` is a single YAML mapping with up to four top-level keys:

| Key | Required | Type | Purpose |
|-----|----------|------|---------|
| `swarm` | yes | mapping | Swarm-wide settings (name, root, timing, resume). |
| `defaults` | no | mapping | Values inherited by every agent unless the agent overrides them. |
| `agents` | yes | list | One to many agent definitions (at least one required). |
| `telegram` | no | mapping | Optional Telegram bridge (off unless `enabled: true`). |

There is also an advanced, optional `agent_types:` mapping (see
[§5 extension note](#extension-agent_types)) for registering custom agent CLIs.

Paths in `swarm.root`, `defaults.workdir`, per-agent `workdir`, and `mail_dir`
are **resolved relative to the directory containing the config file** unless they
are absolute. `~` is expanded. See [§8](#8-path-placeholders).

---

## 2. `swarm:` block

All fields are optional; the table gives each field's type and its **built-in
default** (from `SwarmConfig`). Unset values fall back to the defaults below.

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `name` | string | the config file's stem (e.g. `my-swarm` for `my-swarm.yaml`) | Human label in `status` / logs. |
| `root` | path | `./workspace` (relative to the config file) | Disposable working directory. Each agent gets `<root>/<name>/` unless its `workdir` says otherwise. |
| `resume` | bool | **`true`** | If `true`, `up` reattaches each agent to the conversation recorded in `.agentainer/sessions.yaml`. See [§4 resume](#resume-default-on). |
| `session_prefix` | string | `""` (empty) | Prefix prepended to every tmux session name. The agent's tmux session is `<prefix><name>`. |
| `create_workdirs` | bool | `true` | Swarm-level default for agent `create_workdir` when neither the agent nor `defaults` set it. |
| `supervise` | bool | `true` | Run the liveness supervisor (heartbeat) at `up`. Do not disable — the event-driven core needs it (v1 broke without it). |
| `supervise_interval_ms` | int (ms) | `15000` | How often the supervisor reconciles agents (stale-busy/dead detection, pings, retries). |
| `enter_delay_ms` | int (ms) | `250` | Delay after sending the Enter key before the next paste step. |
| `send_delay_ms` | int (ms) | `150` | Delay between characters/tokens when pasting a message into a pane (`capture_pane`/`paste_into`). |
| `ready_timeout_ms` | int (ms) | `60000` | Give up waiting for an agent's input prompt (ready) after this long. |
| `busy_timeout_ms` | int (ms) | `900000` | Mark a silent agent idle after this long with no turn-completion signal. |
| `user_available` | bool | `false` | Whether the human's virtual `user` mailbox starts "available". If `false`, the human is "away" until you run `agentainer user available`. |
| `pane_idle_ms` | int (ms) | `2500` | Idle threshold for pane-polling turn detection (capture: `pane`). |
| `pane_poll_ms` | int (ms) | `700` | How often the supervisor polls the pane for changes. |
| `pane_scrollback` | int (lines | `400` | Pane scrollback size used when diffing for turn completion. |
| `tmux_history_limit` | int (lines) | `50000` | `tmux` history limit for agent sessions. |
| `tmux_mouse` | bool | `true` | Enable tmux mouse support for agent panes. |

### Resume default: ON

Resume is **on by default** — `swarm.resume` defaults to `true`, and `up` will
reattach each agent to its previously recorded conversation. You can opt out three
ways (first wins):

- `swarm.resume: false` in the config, **or**
- `agentainer up --no-resume` (CLI flag overrides the config), **or**
- `agentainer remove-session` to wipe the recorded conversation ids so `up` starts
  fresh.

Only an *explicit* `up --resume` warns when there is no recorded conversation; the
implicit default-resume path starts fresh silently. Claude and Codex resume via
their native commands; Gemini/Hermes have no resume bridge and start fresh (with a
warning) because no session id is recoverable from a scraped pane.

---

## 3. `defaults:` block

`defaults` is an optional mapping of values that every agent inherits **unless the
agent sets the same field itself**. Agent-level values override `defaults`;
`defaults` overrides the built-in per-type fallback. (For `env`, values are
*merged* — see below.)

| Field | Type | Agent default if unset | Notes |
|-------|------|------------------------|-------|
| `type` | string | `claude` | Agent CLI family: `claude` \| `codex` \| `gemini` \| `hermes`. |
| `command` | string | the built-in command for the resolved `type` | The exact CLI to launch. Must launch a CLI matching `type` (see [§10](#10-common-mistakes)). |
| `capture` | `none`/`auto`/`hook`/`pane` | `auto` (resolves per type) | How turn-completion is detected. See [§5 capture](#capture). |
| `can_talk_to` | list or `"*"` | `[]` | Default ACL. Per-agent overrides replace it (no merge). |
| `role` | string | `""` | Standing instructions. `first_prompt` is a deprecated alias (warns). |
| `workdir` | path | `<root>/<name>` | Per-agent working directory (supports placeholders, see §8). |
| `mail_dir` | path | the agent's `workdir` | Base for the four mailbox folders. See [§5 mail_dir](#mail_dir). |
| `pings` | list | `[]` (none) | Cron-scheduled pings (per-rule `message` + `cron` + `when_busy`). An agent's own `pings` replaces the default list (no merge). See [§5 pings](#pings). |
| `env` | mapping | `{}` | Extra environment variables (merged with type defaults, then agent `env`). |
| `create_workdir` | bool | swarm `create_workdirs` (default `true`) | Create the workdir if missing. |
| `ready_probe` | bool | `true` | Per-agent health probe for the "silent but alive" case. |
| `busy_check` | bool | `true` (forced off when capture is `none`) | Track busy/idle from turn signals. |
| `resume_args` | string | per-type (`"--resume {session_id}"` for claude, `"resume {session_id}"` for codex) | Appended to `command` on resume. |
| `resume_command` | string | per-agent `None` | Full resume recipe; **wins** over `resume_args`. See [§5 resume_command](#resume_command). |

**`env` merge order:** `defaults.env` → `agent_types.<type>.env` → per-agent
`env` (later keys override earlier ones; all values are coerced to strings).

---

## 4. `agents:` list

`agents` is a **required list** with at least one entry. Each entry is a mapping.
The two required fields are `name` and `type` (or `type` inherited from
`defaults`); everything else has a default.

Agent field resolution (agent value → `defaults` → built-in per-type fallback):

- `command`: agent `command` → type's built-in command → error if neither exists.
- `capture`: agent `capture` → `defaults.capture` → `"auto"` → resolves to the
  type's natural capture (`hook` for claude/codex, `pane` otherwise).
- `workdir`: agent `workdir` (with placeholders) → `defaults.workdir` →
  `<root>/<name>`.
- `mail_dir`: agent `mail_dir` → `defaults.mail_dir` → the resolved `workdir`.
- `boot_delay_ms`: agent → `defaults` → type's `boot_delay_ms` → `5000`.
- `role`: agent `role` → `first_prompt` (deprecated) → `""`.
- `can_talk_to`, `pings`, `env`, `create_workdir`,
  `ready_probe`, `busy_check`, `resume_args`, `resume_command`: agent →
  `defaults` → built-in. (`pings` is replaced wholesale — an agent's list does
  not merge with the `defaults` list.)

---

## 5. Per-agent field reference

### `name` — required
The agent's identifier. Used as its tmux session suffix (`<prefix><name>`), as a
directory name, and as the recipient key in `can_talk_to`/outbox folders.

- Must match `^[A-Za-z0-9_][A-Za-z0-9_-]*$` (letters, digits, `_`, `-`; must not
  start with a digit or `-`).
- Must be unique across the `agents` list.
- **Cannot be `user` or `system`** (reserved virtual mailboxes — see §7).

### `type` — string, default `"claude"`
One of:

| `type` | Built-in `command` | Natural `capture` | `resume_args` | Notes |
|--------|--------------------|-------------------|---------------|-------|
| `claude` | `claude --dangerously-skip-permissions` | `hook` | `--resume {session_id}` | Stop-hook turn detection. |
| `codex` | `codex --yolo` | `hook` | `resume {session_id}` | `notify` program turn detection. |
| `gemini` | `gemini --yolo` | `pane` | *(none)* | Pane polling; no resume bridge. |
| `hermes` | `hermes` | `pane` | *(none)* | Pane polling; no resume bridge. |

You may override the `command` and `resume_args` per agent. Custom types can be
registered under `agent_types:` (see extension note below).

### `command` — string, default = built-in command for `type`
The exact shell command that launches the agent CLI. **It must launch the same CLI
as `type` implies.** At `up`, Agentainer scans `command` for the CLI tokens
`claude`/`codex`/`gemini`/`hermes`; if `command` launches a *different* one than
`type`, load fails with a hard error. This is the v1 "type ↔ command mismatch =
deadlock" footgun, now caught at config-load time. (Key-free mock commands like
`bash -c 'while true; do read ...'` contain none of those tokens and pass.)

### `capture` — `none`/`auto`/`hook`/`pane`
How the orchestrator learns a turn finished.

- `hook` — the CLI calls an external program on turn completion (claude Stop hook,
  codex `notify`). Most reliable.
- `pane` — poll the tmux pane and diff it (gemini/hermes, or any CLI without a
  hook).
- `auto` — resolves to the type's natural capture (`hook` for claude/codex, `pane`
  otherwise). This is the default.
- `none` — no turn detection (mock agents that never "finish").

**Auto-upgrade:** `capture: none` on a hook-backed type (claude/codex) is
automatically upgraded to `captur: hook` at load time, because `none` would strip
the agent's only turn-completion signal and leave the orchestrator blind to a
silent turn (which can wedge the whole swarm). A warning is emitted.

### `role` — string, default `""`
The agent's standing instructions — its persona and what it should do. This is the
v2 field. `first_prompt` (and `first_prompt_file`) are **deprecated aliases** and
emit warnings; `first_prompt_file` reads the file's text into `role`. You may not
set both `role`/`first_prompt` and `first_prompt_file`.

### `can_talk_to` — list of names, or `"*"`
The access-control whitelist (the "who may I write to" ACL). It is **cooperative,
not OS isolation** (Decision D15): agents have filesystem access and *could* write
straight into another inbox, bypassing `outbox/`; it is enforced for well-behaved
agents and documented honestly, not a security boundary.

- A list enumerates allowed recipients: real agent names, plus `"user"` (the human
  virtual mailbox). `"system"` is **never** allowed (it is orchestrator-only) — a
  config that lists `system` fails to load. An agent may not list itself.
- `"*"` expands to every *other* real agent (not `user`, not `system`, not itself).
- The orchestrator creates an `outbox/<name>/` folder for each allowed peer, and
  reads `outbox/<name>/about.md` (a contact card) so the agent knows who is there.

### `workdir` — path, default `<root>/<name>`
The agent's working directory. Supports placeholders (§8). If `create_workdir` is
`false` and the directory does not exist, load fails. If two agents resolve to the
same `workdir`, a warning is emitted (they can overwrite each other's files and
interleave git commits); mailboxes are then auto-namespaced (§6 note).

### `mail_dir` — path, default = the agent's `workdir`
Base directory for the four mailbox folders (`inbox/`, `outbox/`, `read/`,
`sent/`, plus `failed/`). Override to keep mail out of a git-tracked workspace, or
to centralize all mailboxes. Supports placeholders (§8).

**Shared-workspace namespacing (Plan §16):** if two agents share one `workdir`
(and therefore the same default `mail_dir`), every folder is prefixed with
`<name>-` (`alice-inbox/`, `alice-outbox/`, …) to avoid collisions. This
prefixing is **invisible to the model** — every nudge and first-prompt states the
agent's exact computed paths, so custom `mail_dir` and prefixing just work.

### `pings` — list of cron-scheduled pings {#pings}

`pings` lets an agent be nudged with a `system` message on a **cron schedule** —
different messages at different times (working hours vs. nights vs. weekends, say),
each on its own cron expression. It replaces the old fixed-cadence ping fields
(`periodically_ping_seconds` / `periodically_ping_message`), which have been
removed.

The delivery guards still apply to every rule:

- **idle-only by default** — a rule that comes due while the agent is busy is
  skipped, unless it opts in with `when_busy: queue`.
- **no pile-up** — a pending/unread ping suppresses the next one.
- **due-this-minute** — a rule fires at most once per matching wall-clock minute.

It is an **optional list**, settable per-agent **or** under `defaults:`. An
agent's own `pings` **fully replaces** the `defaults` list (it does not merge);
omit `pings` on an agent to inherit the default list unchanged.

#### Schema — each list entry is a mapping

| Key | Required | Type | Default | Meaning |
|-----|----------|------|---------|---------|
| `message` | **yes** | string | — | The ping text, delivered to the agent as a `system` message. |
| `cron` | **yes** | string | — | A standard 5-field cron expression: `minute hour day-of-month month day-of-week`. |
| `when_busy` | no | `skip` \| `queue` | `skip` | What to do if the rule comes due while the agent is mid-turn. `skip` = drop this firing (keeps a busy agent's mailbox from filling with stale pings). `queue` = enqueue it anyway, so it is waiting when the turn ends. |

#### Cron syntax

The bundled parser ([`lib/cron.py`](../lib/cron.py), zero-dependency) supports
just enough of standard Vixie-style cron:

| Field | Position | Allowed values |
|-------|----------|----------------|
| minute | 1st | `0`–`59` |
| hour | 2nd | `0`–`23` |
| day-of-month | 3rd | `1`–`31` |
| month | 4th | `1`–`12`, or 3-letter names `jan`–`dec` |
| day-of-week | 5th | `0`–`6` with **Sunday = 0** (`7` is also Sunday), or 3-letter names `sun`–`sat` |

Each field accepts:

- `*` — any value.
- `*/step` — every `step`th value across the field's range (e.g. `*/30` in the
  minute field = every 30 minutes).
- `a-b` — an inclusive range.
- `a-b/step` — a range with a step.
- comma lists — e.g. `1,15,30`, and combinations like `20-23,0-7`.

Names (months and days-of-week) are **case-insensitive** (`Mon`, `mon`, `MON`
all work). Ranges/lists over names work too (`mon-fri`, `sat,sun`).

**Day-of-month vs. day-of-week (the standard Vixie-cron rule):** when **both**
`day-of-month` and `day-of-week` are restricted (neither is `*`), a time matches
if **either** matches. When only one is restricted, only that one must match.

> **Local-time caveat.** Cron schedules are evaluated in the **host's local
> time** — there is deliberately no timezone field and no timezone database
> (zero dependencies). A rule like `0 9 * * *` fires at 9am *on the server*. If
> the machine's clock or timezone changes, your schedules move with it.

#### Semantics & guards

- **Due once per minute.** A rule is "due" when its cron matches the current
  local minute **and** it has not already fired for that wall-clock minute. Each
  rule is deduped independently, so it fires **at most once per matching minute**
  even though the supervisor ticks more often.
- **No pile-up (global).** At most **one** unhandled ping is outstanding at a
  time across **all** of an agent's rules — while a previous ping is still
  unread, the next is suppressed.
- **Overlap resolution.** If multiple rules are due in the same minute, the
  **first *deliverable* rule in list order wins**. A `when_busy: skip` rule that
  is blocked because the agent is busy is *passed over* (without being marked
  fired), so a later rule can still deliver — and the skipped rule can still
  fire later in the same minute if the turn ends in time.
- **Delivery.** The ping arrives as a `system` message (like a bounce or ack),
  so the model self-corrects in-band with no new concept.

#### Validation is fail-fast

A malformed `pings` raises a **config error at load** (naming the agent), so a
bad schedule is caught immediately — never a silent no-op. You get an error if
`pings` is not a list, an entry is not a mapping, `message` is missing, `cron`
is missing, the cron expression is invalid (wrong field count, out-of-range
value, bad range/step), or `when_busy` is anything other than `skip`/`queue`.

#### Example

```yaml
agents:
  - name: dev
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: "Working hours: triage the review queue, then wait for mail."
        cron: "*/30 9-18 * * 1-5"      # every 30 min, 9am-6pm, Mon-Fri (local time)
      - message: "Off-hours: only flag anything genuinely on fire."
        cron: "0 20-23,0-7 * * *"       # top of each hour, evenings + overnight
        when_busy: queue
      - message: "Weekend check-in — anything blocked?"
        cron: "0 12 * * sat,sun"        # noon on weekends
```

### `env` — mapping, default `{}`
Extra environment variables for the agent process. Merged in order:
`defaults.env` → `agent_types.<type>.env` → per-agent `env`. All values are
coerced to strings.

### `create_workdir` — bool, default `true`
If `true`, Agentainer creates the `workdir` (and `mail_dir`) when missing. If
`false` and the directory does not exist, load fails (create it yourself or allow
Agentainer to).

### `ready_probe` — bool, default `true`
Enable the per-agent health probe for the "silent but alive" case the supervisor
otherwise cannot catch. Turn it off only if you have an external liveness signal.

### `busy_check` — bool, default `true`
Track the agent's busy/idle state from turn-completion signals. **Forced off
automatically when `capture` is `none`** (no signal exists, so the agent is always
treated as accepting mail). Set `false` to disable busy tracking even with
capture enabled.

### `resume_args` — string, default per-type
Appended to `command` on `up --resume` after formatting `{session_id}`. Built-ins:
`"--resume {session_id}"` (claude), `"resume {session_id}"` (codex). Gemini/Hermes
have none. If you set `resume_command`, that wins instead.

### `resume_command` — string, default `None` {#resume_command}
A **complete** resume recipe that **wins over `resume_args`**. It is formatted with
two placeholders: `{session_id}` (the recorded conversation id) and `{command}`
(the agent's `command`).

**Why it matters (critical for shell-wrapped commands):** if your `command` is a
shell wrapper such as `bash -ic 'chy3'`, then `resume_args` would be appended as
`bash -ic 'chy3' --resume <id>` — and the `--resume` flag is **swallowed by bash**,
never reaching `chy3`. The agent starts a fresh conversation and the recorded
session is lost. For any wrapped command, set `resume_command` to the full recipe:

```yaml
- name: dev
  type: claude
  command: "bash -ic 'chy3'"
  resume_command: "bash -ic 'chy3 --resume {session_id}'"
```

> Extension: `agent_types:`
>
> You are not limited to the four built-ins. Register a custom type under
> `agent_types:` with any subset of `command`, `capture`, `boot_delay_ms`,
> `resume_args`, `resume_command`, `env`. `agentainer.example.yaml`'s key-free
> `bash` mocks are effectively anonymous commands; for a repeatable custom CLI,
> declare it once under `agent_types:` and reference `type: <yourtype>` in agents.

---

## 6. `telegram:` block

Optional. Off unless `enabled: true`. When enabled, the orchestrator mirrors mail
to a Telegram chat over HTTPS (stdlib only, no dependency), and a Telegram reply to
a mirrored message routes back into the swarm as `user` mail.

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `enabled` | bool | `false` | Master switch for the bridge. |
| `bot_token` | string | `""` | Bot credential from @BotFather. **Secret — never commit.** Use `<placeholder>`. |
| `chat_id` | string | `""` | Chat/user id to send to and accept replies from (from @userinfobot). |
| `mirror` | `"*"` / `"all"` / list | `"*"` | `"*"` mirrors every agent's mail; a list mirrors only those named agents. |
| `mirror_user` | bool | `true` | Also mirror mail addressed to `user`, so the human stays reachable even while "away". |
| `mirror_system` | bool | `false` | Mirror `system` pings/bounces too (noisy — off by default). |

`mirror` normalization: `*`/`all`/unset → `"*"`; a bare string becomes a
one-element list; a list is passed through.

```yaml
telegram:
  enabled: true
  bot_token: "<placeholder>"      # from @BotFather; keep secret
  chat_id: "<placeholder>"        # from @userinfobot
  mirror: "*"                     # or a list: [orchestrator, developer]
  mirror_user: true               # default
  mirror_system: false            # default
```

The UI's Settings panel is the easiest place to fill these in.

---

## 7. Naming rules & reserved names

- **`user` and `system` are reserved virtual mailboxes**, never real agents. An
  agent may not be named `user` or `system` — load fails.
- **`user`/`system` cannot be claimed as recipients** by an agent. An agent *may*
  list `user` in `can_talk_to` (to message the human), but listing `system` is a
  hard config error (`system` is orchestrator-only).
- Agent `name` must match `^[A-Za-z0-9_][A-Za-z0-9_-]*$` and be unique.
- `swarm.name` is cosmetic; if omitted it defaults to the config file stem.

---

## 8. Path placeholders

In `workdir` and `mail_dir` you may use these placeholders; they are substituted
per agent before the path is resolved relative to the config file's directory:

| Placeholder | Expands to |
|-------------|------------|
| `{name}` | the agent's name |
| `{root}` | the resolved `swarm.root` |
| `{swarm}` | the swarm name |
| `{type}` | the agent's `type` |

Example: `workdir: "{root}/agents/{name}"` puts each agent under
`<root>/agents/<name>/`. Unknown placeholders are a config error. `~` is expanded,
and absolute paths skip the config-relative resolution.

---

## 9. Worked examples

### Minimal real swarm (orchestrator + two workers)

```yaml
swarm:
  name: trio
  root: ./trio-workspace

defaults:
  capture: none          # key-free mocks don't fire a hook; swap for real CLIs
  can_talk_to: []

agents:
  - name: orchestrator
    type: claude
    can_talk_to: [developer, reviewer, user]
    command: "claude --dangerously-skip-permissions"
    role: "You are the orchestrator. Wait for the user's task, then delegate."

  - name: developer
    type: codex
    can_talk_to: [orchestrator]
    command: "codex --yolo"
    role: "You are a developer. Implement what the orchestrator asks."

  - name: reviewer
    type: claude
    can_talk_to: [developer]
    command: "claude --dangerously-skip-permissions"
    role: "You are a reviewer. Read the developer's output and critique it."
```

### Shell-wrapped command needing `resume_command`

```yaml
swarm:
  name: wrapped
  root: ./wrapped-workspace
  resume: true            # default; reattaches recorded conversations

agents:
  - name: dev
    type: claude
    can_talk_to: "*"
    command: "bash -ic 'chy3'"          # the alias launches claude
    resume_command: "bash -ic 'chy3 --resume {session_id}'"   # CRITICAL
    mail_dir: "/var/mail/{name}"         # keep mail off the workspace
    pings:
      - cron: "*/10 * * * *"             # every 10 minutes
        message: "Any progress to report? Reply, or stay quiet."
    env:
      OPENROUTER_API_KEY: "<placeholder>"
```

### Centralized mail + custom timing

```yaml
swarm:
  name: tuned
  root: ./tuned-workspace
  supervise_interval_ms: 30000
  ready_timeout_ms: 30000
  busy_timeout_ms: 120000
  user_available: false
  session_prefix: "prod-"

defaults:
  mail_dir: ./all-mail          # every agent's mail under ./all-mail (namespaced if shared)
  capture: pane
  create_workdir: true

agents:
  - name: a
    type: gemini
    can_talk_to: [b]
    command: "gemini --yolo"
  - name: b
    type: hermes
    can_talk_to: [a]
    command: "hermes"
```

---

## 10. Common mistakes

1. **type ↔ command mismatch (hard error, silent deadlock in v1).** A `command`
   that launches a *different* CLI than `type` implies never fires its
   turn-completion signal and pins the agent "busy" forever. Agentainer now refuses
   to load such a config. Keep `command` aligned with `type` (or use a custom
   `agent_types:` entry).

   ```yaml
   # WRONG — type says claude but command launches codex
   - name: x
     type: claude
     command: "codex --yolo"     # load fails
   ```

2. **Forgetting `resume_command` for shell-wrapped commands.** Appending
   `resume_args` to `bash -ic 'chy3'` is swallowed by bash, so the recorded session
   is lost on resume. Always set `resume_command` with the full recipe when the
   `command` is a wrapper. (See §5 `resume_command`.)

3. **Binding `serve` to `0.0.0.0` without a token.** The UI is a control plane that
   can start processes and type into agents running
   `--dangerously-skip-permissions`/`--yolo`. It binds `127.0.0.1` by default. Any
   non-loopback bind (`--host 0.0.0.0`) **requires a token** (enforced in
   `ui.run_server`). On a remote bind, always pass `--token <UI_TOKEN>` (a
   placeholder, never a real key) and keep it confidential.

4. **Listing `system` in `can_talk_to`.** `system` is orchestrator-only and can
   never be a recipient — load fails. Use `user` if you mean the human.

5. **Naming an agent `user` or `system`.** Both are reserved virtual mailboxes; the
   config refuses to load.

6. **`capture: none` on claude/codex silently becoming `hook`.** This is not a
   mistake you can make — it is auto-corrected with a warning, because `none` would
   blind the orchestrator. If you truly want no turn detection, use a `pane`-type
   agent (gemini/hermes) or a key-free mock.

7. **Two agents sharing a `workdir`.** Allowed, but warned: they can overwrite each
   other's files and interleave git commits. Mailboxes are auto-namespaced so mail
   does not collide, but consider distinct `workdir`s.

---

*Source of truth: `lib/config.py` (`Agent`, `SwarmConfig`, `TelegramConfig`).
Defaults shown above are the dataclass defaults; per-agent and `defaults:` values
override them as described in §3–§5.*

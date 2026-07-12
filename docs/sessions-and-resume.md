# Sessions, resume-by-default, and `remove-session`

Agentainer v2 keeps a conversation alive across restarts. When you run
`agentainer up`, each agent is **reattached to the conversation it was last in**
by default — no flag needed. This guide explains how that works, how to opt out,
how to wipe state for a clean start, and one sharp edge involving shell-wrapped
agent commands.

All behaviour described here is sourced from the runtime itself:

- `lib/config.py` — the `SwarmConfig.resume` default and the `sessions_file` path
- `lib/sessions.py` — `read_sessions`, `write_sessions`, `record_session`, `resume_command`
- `lib/cli.py` — `cmd_up` (resume block), `cmd_hook`, `cmd_sessions`, `cmd_remove_session`

---

## 1. Resume is the default

Since v2, **`up` reattaches each agent to the conversation recorded in
`.agentainer/sessions.yaml`**. You do not pass any flag to get this.

The contract is:

- A **first launch** (nothing recorded yet for an agent) starts a fresh
  conversation and is **silent** — no warning, no nag. This is the common case
  the first time you bring a swarm up.
- An **explicit** `up --resume` when there is nothing recorded for an agent
  prints a warning:

  ```
  coordinator: no recorded conversation; starting a fresh one
  ```

  That warning only appears because you *asked* for a resume. The implicit
  default path stays quiet so routine restarts don't spam.

Resume is controlled by two independent switches that combine as follows
(`lib/cli.py`, `cmd_up`):

```python
explicit_resume = args.resume is True
resume = cfg.resume if args.resume is None else args.resume
recorded = sessions.read_sessions(cfg) if resume else {}
```

So the config flag (`swarm.resume`) is the default, and `--resume` /
`--no-resume` on the command line overrides it. `explicit_resume` is *only*
true when you typed `--resume` yourself, and it is the sole trigger for the
"no recorded conversation" warning.

---

## 2. How it works

### Recording the conversation id

Every time an agent finishes a turn, the installed completion hook calls
`agentainer hook`. For `claude` that is the Stop hook; for `codex` it is the
`notify` program. In `cmd_hook`:

- **claude** — the hook reads the JSON Claude emits on stdin, which includes
  `session_id`, and calls
  `sessions.record_session(cfg, agent, payload.get("session_id"), transcript=...)`.
- **codex** — the `agent-turn-complete` payload carries `session_id`, recorded
  the same way.

`record_session` (`lib/sessions.py`) merges the id into `sessions.yaml` under a
file lock (hooks write concurrently, so writes are atomic via a temp file +
`os.replace`). It also stores `type`, `workdir`, and `updated_at`, and skips a
rewrite if the id is unchanged — so the file is not churned on every single
turn.

Codex is a special case: it does not hand its id to the notify program, so
`codex_session()` instead finds the newest rollout file under
`workdir/.codex/sessions` and reads `session_id` from its `session_meta`
record.

### The sessions.yaml file

`sessions_file` is `cfg.runtime / "sessions.yaml"`, i.e.
`<root>/.agentainer/sessions.yaml`. It maps each agent name to its
`session_id`:

```yaml
# Agentainer session state -- written automatically as agents work.
swarm: research
config: /path/to/agentainer.yaml
updated_at: 2026-07-12T10:02:14+00:00
agents:
  coordinator:
    session_id: "1a2b3c4d-..."
    type: claude
    workdir: /path/to/research-workspace/coordinator
    updated_at: 2026-07-12T10:02:14+00:00
  researcher:
    session_id: "9f8e7d6c-..."
    type: claude
    workdir: /path/to/research-workspace/researcher
    updated_at: 2026-07-12T10:01:59+00:00
```

A corrupt or missing file is treated gracefully — `read_sessions` returns `{}`
and the agent simply starts fresh (this never stops the swarm).

### Reattaching on `up`

For each selected agent, `cmd_up` looks up `recorded[agent.name]["session_id"]`.
If present, it calls:

```python
resume_cmd = sessions.resume_command(cfg, agent, session_id)
```

`resume_command` (`lib/sessions.py`) decides *how* the agent is relaunched:

1. **If the agent has a `resume_command` recipe**, it wins — the recipe is
   formatted with `{session_id}` (and `{command}`) and returned verbatim. This is
   the path you need for shell-wrapped commands (see §5).
2. **Otherwise, if the agent has `resume_args`**, they are appended to the
   agent's `command`: `f"{agent.command} {agent.resume_args.format(session_id=...)}"`.
   The built-in agents carry these defaults:
   - `claude` → `--resume {session_id}`
   - `codex` → `resume {session_id}`
3. **Otherwise `None`** is returned and `cmd_up` warns:

   ```
   coordinator: type 'gemini' has no resume recipe (set resume_args or
   resume_command); starting a fresh conversation
   ```

   `gemini` and `hermes` are pane-captured types from which no session id can be
   scraped, so they have no resume recipe and always restart fresh. This is
   expected, not an error.

When `resume_cmd` is non-`None`, the agent is launched *without* re-sending the
first prompt (it already has its standing context from the resumed
conversation); `cmd_up` prints `resuming conversation <id>`.

### Inspecting state: `agentainer sessions`

```
agentainer sessions
```

prints one block per agent:

```
  coordinator (claude)
      conversation: 1a2b3c4d-...
      last seen:    2026-07-12T10:02:14+00:00
  reviewer: -
```

Agents with no recording show `-`. With no conversations recorded at all it
prints a note explaining they are written after each agent's first turn.

```
agentainer sessions --raw
```

prints the entire `sessions.yaml` verbatim. Useful when you want to copy a real
`session_id` or confirm exactly what is stored.

---

## 3. Opting out of resume

There are three independent ways to start **fresh** conversations instead of
reattaching.

### (a) Config flag — `swarm.resume: false`

In `agentainer.yaml`:

```yaml
swarm:
  name: research
  root: ./research-workspace
  resume: false
```

This makes `up` (and `restart`) start fresh for every agent, every time, unless
you override on the command line. Note: this only changes launch behaviour. The
conversation ids are still *recorded* as agents work; they just aren't used to
reattach.

### (b) Command line — `up --no-resume`

```
agentainer up --no-resume
```

This overrides the config flag for a single invocation. `--resume` is also
accepted (it forces resume even if `swarm.resume: false`), and is the only way
to surface the "no recorded conversation" warning for an agent that has nothing
yet.

`restart` accepts the same `--resume` / `--no-resume` flags.

### (c) Wipe state — `remove-session`

See §4. This deletes the recordings entirely, so the next `up` finds nothing and
starts fresh. Use it when you want a clean slate for all agents.

---

## 4. `remove-session`

```
agentainer remove-session
```

This is the escape hatch from default-resume: it deletes **every piece of
Agentainer-generated state** for the swarm, so the next `up` finds no recorded
conversations and starts fresh for every agent.

### What it removes

Two categories of state (both gitignored, never shipped — see `CLAUDE.md`):

1. **The orchestrator runtime `.agentainer/`** at `<root>/.agentainer/`:
   - `sessions.yaml` (the conversation ids)
   - the per-agent message queue
   - turn/busy state
   - the durable JSONL event log
   - the `run/` dir
2. **Each agent's five mailbox folders** — `inbox/`, `outbox/`, `read/`,
   `sent/`, `failed/` — wherever `mail_paths` resolves them (including any
   shared-workspace namespace prefix). Any in-flight mail is discarded.

### What it never touches

- **Agent workspaces' own files** (your source code, git checkouts, anything the
  agent produced in its `workdir` outside the five mailbox folders).
- **The `agentainer.yaml` config.**
- **The conversation history inside the agent CLI itself** (e.g. Claude/Codex's
  own transcript store). `remove-session` only deletes Agentainer's *pointer* to
  that history; the underlying CLI session may still exist until the CLI prunes
  it. This is why `remove-session` means "start fresh next time" — not "erase the
  model's memory of prior turns from the provider."

### It refuses to run on a live swarm

`remove-session` refuses (and prints a `die` message) if any agent tmux session
is still running, or if the liveness supervisor is alive:

```
coordinator is still running -- run `down` first, then `remove-session`
```

or:

```
the liveness supervisor is still running -- run `down` first
```

Pulling state out from under a live agent corrupts it, so the correct order is:

```
agentainer down
agentainer remove-session
agentainer up          # starts fresh for all agents
```

If there is nothing to remove, it reports `nothing to remove -- the swarm is
already clean` and exits 0.

---

## 5. The shell-wrapper pitfall (IMPORTANT)

This is the one place resume silently fails. **Read this if any agent's
`command` launches the CLI through a shell.**

### The problem

Suppose an agent's command is a shell wrapper — for example a single-quote alias
to a CLI that lives in an interactive shell (`chy3` is a shell alias for
`claude`, defined in `~/.bashrc`):

```yaml
command: "bash -ic 'chy3'"
```

`resume_command` falls through to the `resume_args` path, which *appends* the
flag to the command:

```
bash -ic 'chy3' --resume <session_id>
```

The `--resume <session_id>` lands **after** the `bash -c '...'` argument. To
`bash`, that trailing text is not part of the script string — it is either
ignored or swallowed, and it **never reaches the `chy3`/`claude` CLI**. The agent
launches a *brand-new* conversation, the resume is silently lost, and the next
`up` records a different id. No error is printed; the symptom is just "my context
reset."

Plain commands do **not** have this problem:

```yaml
command: "claude --dangerously-skip-permissions"   # resumes fine, no extra config
command: "codex --yolo"                             # resumes fine, no extra config
```

because the built-in `resume_args` (`--resume {session_id}` / `resume
{session_id}`) append directly onto a real CLI invocation and land where the CLI
expects them.

### The fix: a per-agent `resume_command`

Set `resume_command` so the flag is **inside** the `-c` string, formatted with
`{session_id}`:

```yaml
command: "bash -ic 'chy3'"
resume_command: "bash -ic 'chy3 --resume {session_id}'"
```

`resume_command` is formatted with both `{session_id}` and `{command}`, so you
can also reference the original command to avoid duplication:

```yaml
command: "bash -ic 'chy3'"
resume_command: "bash -ic 'chy3 --resume {session_id}'"
```

When `resume_command` is present, `resume_command()` returns it verbatim and the
flag is placed where the wrapped CLI actually parses it. Resume then works.

### Broken vs. fixed, side by side

**Broken** — flag is outside the `-c` string, swallowed by bash:

```yaml
agents:
  - name: coordinator
    type: claude
    command: "bash -ic 'chy3'"          # BUG: --resume is appended after the quote
    # (no resume_command)                #  => bash -ic 'chy3' --resume <id>  (lost)
    role: "You are the coordinator."
```

**Fixed** — flag is inside the `-c` string:

```yaml
agents:
  - name: coordinator
    type: claude
    command: "bash -ic 'chy3'"
    resume_command: "bash -ic 'chy3 --resume {session_id}'"   # flag reaches the CLI
    role: "You are the coordinator."
```

You can set `resume_command` per agent or under `defaults:` to apply it to every
agent that shares the wrapper. The live
`/tmp/agentainer_test/agentainer.yaml` swarm uses exactly this pattern for all
three of its `bash -ic 'chy3'` agents.

> Note: a `resume_command` recipe that is malformed (missing `{session_id}`, bad
> format) is caught and produces a warning: *"resume recipe is malformed …
> starting a fresh conversation"* — so a typo there also silently disables resume
> rather than crashing.

---

## 6. Troubleshooting: "`up` didn't restore my session"

Work through these in order:

1. **Is the id actually recorded?** Run `agentainer sessions --raw` and confirm
   the agent has a `session_id` under `agents.<name>`. If not, the agent has
   never finished a turn under the hook (e.g. `capture: none` means no
   Stop/notify hook fired, so nothing was recorded — see the note below).
2. **Is the command a shell wrapper without `resume_command`?** If `command` is
   `bash -ic '…'` or similar and you have not set `resume_command`, the
   `--resume` flag is being swallowed by bash (§5). Add the per-agent
   `resume_command`.
3. **Did you pass `--no-resume`, or set `swarm.resume: false`?** Either opts out
   of reattach. Re-run `agentainer up` without `--no-resume`, or remove the
   config flag.
4. **Is the agent a pane-captured type with no resume recipe?** `gemini` and
   `hermes` cannot yield a session id, so they always start fresh by design.
5. **Did `remove-session` run?** That wipes `sessions.yaml`; the next `up` has
   nothing to reattach to (expected, by design).
6. **Did the CLI itself reject the id?** `resume_command` may have produced a
   syntactically valid but stale id. Try `agentainer remove-session` then
   `agentainer up` for a guaranteed-clean start.

> **Note on `capture: none` and recording.** The conversation id is recorded by
> the completion hook (`agentainer hook`, called from a `claude` Stop hook or a
> `codex` notify program). An agent configured with `capture: none` has no
> turn-completion signal, so `record_session` never runs for it and there is
> nothing to resume. If you want resume on a `claude`/`codex` agent, keep
> `capture` at its type default (`hook`/`auto`) rather than `none`.

---

## Quick reference

| Goal | Command |
| --- | --- |
| Bring the swarm up (resume by default) | `agentainer up` |
| Force resume and warn if nothing recorded | `agentainer up --resume` |
| Start fresh this once | `agentainer up --no-resume` |
| Disable resume for all `up`s | set `swarm.resume: false` in config |
| Show recorded conversation ids | `agentainer sessions` |
| Dump `sessions.yaml` verbatim | `agentainer sessions --raw` |
| Wipe all Agentainer state, clean start | `agentainer down` then `agentainer remove-session` |

State removed by `remove-session`: orchestrator runtime `.agentainer/` and each
agent's five mailbox folders. Never removed: workspace source files, `agentainer.yaml`.

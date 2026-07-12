# Use case: the research swarm

A concrete, end-to-end walkthrough of the shipped `examples/research.yaml` swarm —
a three-agent pipeline where a **coordinator** breaks a question into tasks, a
**researcher** investigates, and a **reviewer** critiques the findings before they
go back to the human. It's the canonical "delegate → do the work → check the work"
loop, wired entirely through Agentainer's file-based mail model.

Everything below is based on the actual contents of `examples/research.yaml` and
the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you supply the coding-CLI
commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The topology

```
        research X
  user ─────────────▶ coordinator ──────────────▶ researcher
        (answer)  ◀──────────┐                        │
                             │                        │ findings
                          critique                    ▼
                       reviewer  ◀─────────────────  (reports to reviewer)
```

Three agents, one directed flow:

1. **`user` → `coordinator`** — you send the research question.
2. **`coordinator` → `researcher`** — the coordinator breaks it into tasks and
   delegates.
3. **`researcher` → `reviewer`** — the researcher investigates and reports its
   findings to the reviewer (not back to the coordinator).
4. **`reviewer` → `coordinator`** — the reviewer critiques the work and reports
   back to the coordinator.
5. **`coordinator` → `user`** — the coordinator returns the vetted answer to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. An agent can only deliver to names on its own list; anything
else is bounced back as a `system` message and filed in `failed/` (see §7).

---

## 2. The config, explained

Here is `examples/research.yaml` in full:

```yaml
# 🔬 Research swarm — a coordinator briefs a researcher, who hands off to a reviewer.
# Real agents: the `command:` lines launch the actual CLIs. For a key-free demo, swap each for a mock bash loop.
swarm:
  name: research
  root: ./research-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: coordinator
    type: claude
    can_talk_to: [researcher, reviewer, user]
    command: "claude --dangerously-skip-permissions"
    role: "You are the coordinator. Break the user's question into tasks and delegate."
  - name: researcher
    type: gemini
    can_talk_to: [coordinator, reviewer]
    capture: pane
    command: "gemini --yolo"
    role: "You are the researcher. Investigate and report findings to the reviewer."
  - name: reviewer
    type: codex
    can_talk_to: [coordinator]
    command: "codex --yolo"
    role: "You are the reviewer. Critique the researcher's work and report to the coordinator."
```

Field by field:

### `swarm`
- **`name: research`** — the swarm's name (shows up in `status`, logs, sessions).
- **`root: ./research-workspace`** — the parent directory for the agents' working
  directories and mailboxes. Each agent gets `research-workspace/<name>/` as its
  workdir (created on `up`), and its mailbox folders live alongside. Orchestrator
  state goes under `research-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, and it's ultimately keyed off each
  agent's `type`. For `claude` and `codex`, whose CLIs support a completion
  **hook**, setting `capture: none` is a footgun — so the config loader *upgrades*
  it back to `hook` and prints a warning at `up`
  (`capture: none on a claude agent gives the orchestrator no way to detect turn
  completion; using the type's default: capture: hook.`). Net effect here:
  coordinator and reviewer use their hook; the researcher overrides to `pane`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `coordinator` (type: `claude`)
- **`can_talk_to: [researcher, reviewer, user]`** — the coordinator is the hub:
  it can delegate to the researcher, ping the reviewer, and it is the **only agent
  that can talk to `user`**. That last part matters — keep the human-facing surface
  to a single agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the coordinator waits for your question instead of
  proactively mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `researcher` (type: `gemini`)
- **`can_talk_to: [coordinator, reviewer]`** — can report to the reviewer and
  answer the coordinator. Note it **cannot** talk to `user` — its work always flows
  through review first.
- **`capture: pane`** — Gemini's CLI can't call a completion program, so
  Agentainer detects "turn done" by **polling the tmux pane** until it stops
  changing. (This is why the researcher explicitly overrides the `none` default.)
- **`command: "gemini --yolo"`** — placeholder launch command.
- **`role`** — "investigate and report findings to the reviewer."

### `reviewer` (type: `codex`)
- **`can_talk_to: [coordinator]`** — the reviewer only reports upward to the
  coordinator. It deliberately cannot reach the researcher directly or the `user`.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "critique the researcher's work and report to the coordinator."
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### What's *not* in this config
- **No `periodically_ping_seconds`.** None of the three agents has a periodic ping
  configured, so no agent is auto-nudged on a timer while idle — the pipeline is
  purely event-driven off real mail. (If you wanted the coordinator to poke a slow
  researcher, you'd add `periodically_ping_seconds: 300` to it.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/research.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for coordinator/reviewer).
2. Creates the runtime dirs (`research-workspace/.agentainer/…`: log, queue, run,
   sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: the coordinator gets
   `outbox/researcher/`, `outbox/reviewer/`, `outbox/user/`; the researcher gets
   `outbox/coordinator/`, `outbox/reviewer/`; the reviewer gets
   `outbox/coordinator/`.
4. **Installs per-type turn detection** — the Claude Stop hook for the coordinator,
   the Codex `notify` hook for the reviewer, and (for the pane-captured researcher)
   arranges pane polling.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'research' is up with 3 agent(s)
:: attach with:  tmux attach -t <coordinator-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/research.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only bind. See the `README.md` "control-plane UI" section.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole pipeline route mail with no API keys — the mechanics are identical.

---

## 4. Drive a question

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the coordinator's final answer as mail (rather
than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/research.yaml
```

This rewrites the `user` contact card in the coordinator's `outbox/user/about.md`
to `Status: available`, so the coordinator sees you're reachable. (While away, mail
to you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the question into the swarm, addressed to the coordinator:

```bash
./agentainer send --to coordinator "Research the trade-offs of Parquet vs. ORC for analytics."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the coordinator, then — because
the inbox was empty — **released into `inbox/`** and the coordinator is **nudged**
(the protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§5), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **coordinator receives the task.** It reads `inbox/`, decides on the research
   tasks, and writes a delegation file into `outbox/researcher/`. When its turn
   ends, the orchestrator sweeps the outbox, routes the message to the researcher,
   and nudges the researcher.
2. **researcher investigates.** It reads its inbox, does the work, writes findings
   into `outbox/reviewer/`. On stop, that routes to the reviewer.
3. **reviewer critiques.** It reads the findings, writes a critique into
   `outbox/coordinator/`. On stop, that routes back to the coordinator.
4. **coordinator finalizes.** It reads the critique and writes the vetted answer
   into `outbox/user/`. On stop, that's delivered to your `user` mailbox (you'll
   see it with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a question, the agents just sit in standby (that's the point
> of the standby prompt). The pipeline only moves when real mail arrives — this
> swarm has no periodic pings to self-start it.

---

## 5. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/research.yaml
```

```
swarm: research   root: ./research-workspace
  coordinator (claude) up idle queue=0 unread=0 talks=researcher, reviewer, user
  researcher (gemini) up idle queue=0 unread=1 talks=coordinator, reviewer
  reviewer (codex) up idle queue=0 unread=0 talks=coordinator
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/research.yaml          # whole swarm, last 20
./agentainer logs -c examples/research.yaml -f        # follow live
./agentainer logs researcher -c examples/research.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox researcher -c examples/research.yaml
```

Prints the one released message (headers + body), or `researcher: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue researcher -c examples/research.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach researcher -c examples/research.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 6. Resume after a stop

Tear the swarm down when you're done:

```bash
./agentainer down -c examples/research.yaml
```

Bring it back later and **conversations resume by default**:

```bash
./agentainer up -c examples/research.yaml     # resume is the default
```

On `up`, Agentainer reads `research-workspace/.agentainer/sessions.yaml` (written
as each agent finished its first turn) and reattaches the recorded conversations
via each type's native resume: `claude --resume <id>` for the coordinator, `codex
resume <id>` for the reviewer. The researcher (`gemini`) has no resume bridge, so
it starts a **fresh** conversation with a warning — its pipeline role is
stateless-per-task anyway. A resumed agent is *not* re-sent the standby prompt (its
prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/research.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md) and
the reboot walkthrough in
[`use-cases/resume-after-reboot.md`](./resume-after-reboot.md).

---

## 7. Tips & footguns

- **Keep the coordinator the only `user`-facing agent.** In this config only the
  coordinator lists `user` in `can_talk_to`. That gives you a single point of
  contact and a clean funnel: raw findings always pass through review before they
  reach you. If the researcher tries to mail `user` directly, the orchestrator
  bounces it (ACL) and drops a `system` note in the researcher's inbox explaining
  who it *can* message — the model self-corrects in-band.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually fires**
  — a `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't
  launch Claude) means completion never triggers and the agent pins "busy" forever.
  `status` showing an agent `busy` for a long time with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **Force-idle if a pane-captured agent's turn never registers.** The researcher
  uses pane polling; if its capture never fires you can nudge the state along:
  ```bash
  ./agentainer idle researcher -c examples/research.yaml
  ```

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/research.yaml
  ./agentainer remove-session -c examples/research.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

- **Availability shapes the ending.** If `user` is **away** when the coordinator
  finishes, your final answer is *held* (with a `system` "the user is away" ack to
  the coordinator) rather than lost — read it later with `agentainer user inbox` or
  flip yourself available and it's delivered.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`cli-reference.md`](../cli-reference.md) — every subcommand and flag.
- `examples/quickstart.yaml` — the key-free mock-agent starter.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

# Use case: the test-generation factory

A concrete, end-to-end walkthrough of the shipped `examples/test-factory.yaml`
swarm — a **spec_reader** hub turns a feature spec into a test plan, two parallel
**writers** produce the tests, and a **coverage** agent reviews the result before
it reaches the human. It's the canonical "plan → do in parallel → check the work"
loop, wired entirely through Agentainer's file-based mail model, and a natural
fit for shops that want test coverage to keep pace with a fast-moving codebase.

Everything below is based on the actual contents of `examples/test-factory.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you supply the
coding-CLI commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The topology

```
        unit_writer  ─┐
                      ├──▶  spec_reader  ◀──▶  coverage  ──▶  user
        integration_writer ─┘
```

Four agents, one directed flow:

1. **`user` → `spec_reader`** — you send a feature spec, or a repo + a feature
   name ("generate tests for the new rate-limiter in `<repo>`").
2. **`spec_reader` → `unit_writer`** and **`spec_reader` → `integration_writer`**
   — the spec_reader splits the spec into two disjoint briefs (unit cases vs.
   integration/e2e flows) and delegates them in parallel.
3. **`unit_writer` → `spec_reader`** and **`integration_writer` → `spec_reader`**
   — each writer returns a summary of the files it produced.
4. **`coverage`** reads the generated tests and reports gaps back to
   **`spec_reader`**, and a human-readable verdict to **`user`**.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. An agent can only deliver to names on its own list; anything
else is bounced back as a `system` message and filed in `failed/` (see §7).

### The ACL at a glance

| Agent | type | May talk to |
|---|---|---|
| `spec_reader` | claude | `unit_writer`, `integration_writer`, `coverage`, `user` |
| `unit_writer` | codex | `spec_reader` |
| `integration_writer` | codex | `spec_reader` |
| `coverage` | claude | `spec_reader`, `user` |

Note who **can't** talk to `user`: only `spec_reader` and `coverage`. The two
writers are pure executors — their output always flows back through the
spec_reader (and the coverage reviewer), never straight to you.

---

## 2. The config, explained

Here is `examples/test-factory.yaml` in full:

```yaml
# 🧪 Test-generation factory -- a spec_reader hub turns a feature spec into a
# test plan, parallel writers produce the tests, and a coverage agent reviews
# them before they reach the human.
swarm:
  name: test-factory
  root: ./test-factory-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: spec_reader
    type: claude
    can_talk_to: [unit_writer, integration_writer, coverage, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the SPEC READER and the planning hub ... (see the file for the full brief)
  - name: unit_writer
    type: codex
    can_talk_to: [spec_reader]
    command: "codex --yolo"
    workdir: "{root}/tests-repo"
    role: |
      You are the UNIT TEST WRITER ... (see the file for the full brief)
  - name: integration_writer
    type: codex
    can_talk_to: [spec_reader]
    command: "codex --yolo"
    workdir: "{root}/tests-repo"
    role: |
      You are the INTEGRATION / E2E TEST WRITER ... (see the file for the full brief)
  - name: coverage
    type: claude
    can_talk_to: [spec_reader, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the COVERAGE REVIEWER ... (see the file for the full brief)
```

Field by field:

### `swarm`
- **`name: test-factory`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./test-factory-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent gets
  `test-factory-workspace/<name>/` as its workdir (created on `up`), except the
  two writers, which share `test-factory-workspace/tests-repo/`. Orchestrator
  state goes under `test-factory-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, and it's ultimately keyed off each
  agent's `type`. For `claude` and `codex`, whose CLIs support a completion
  **hook**, setting `capture: none` is a footgun — so the config loader *upgrades*
  it back to `hook` and prints a warning at `up`
  (`capture: none on a claude agent gives the orchestrator no way to detect turn
  completion; using the type's default: capture: hook`). Net effect here: all
  four agents use their hook.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `spec_reader` (type: `claude`)
- **`can_talk_to: [unit_writer, integration_writer, coverage, user]`** — the hub:
  it is the only planner and the only agent that can both delegate the work and
  talk to the `user`. That last part matters — keep the human-facing surface to a
  small set of agents (here, spec_reader and coverage) so raw test output always
  passes through review first.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity: read the spec, decide *what* to test, split
  it into two briefs, delegate. On `up` this becomes the agent's first prompt,
  wrapped in a **standby notice** ("no task yet — don't send anything, you'll be
  notified"), so the spec_reader waits for your spec instead of proactively
  mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `unit_writer` (type: `codex`)
- **`can_talk_to: [spec_reader]`** — can only report back to the hub. It cannot
  reach `integration_writer` or `user`, which keeps the two writers from
  negotiating scope between themselves (the spec_reader owns the split).
- **`workdir: "{root}/tests-repo"`** — the writer runs **inside the repo/checkout
  under test** so the tests it writes land in the real tree. It shares this
  workdir with `integration_writer` (see §3). On `up`, `from __future__` resolves
  `{root}` to `test-factory-workspace`, so the path becomes
  `test-factory-workspace/tests-repo/`. **This directory is not created
  automatically** unless it exists or you let Agentainer create it — for a dry run
  leave it and Agentainer makes a scratch dir; to test against real code, point
  both writers' `workdir` at your checkout (see
  [`custom-workspace.md`](../use-cases/custom-workspace.md)).
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `integration_writer` (type: `codex`)
- **`can_talk_to: [spec_reader]`** — identical ACL to the unit writer; peers that
  never coordinate.
- **`workdir: "{root}/tests-repo"`** — the **same** workdir as `unit_writer`, so
  the two suites live together in one checkout.
- **`command: "codex --yolo"`** — placeholder launch command.

### `coverage` (type: `claude`)
- **`can_talk_to: [spec_reader, user]`** — the reviewer reports upward to the
  hub and outward to the `user`. It deliberately cannot reach the writers directly
  (it reviews their files, not their mailboxes).
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → a **Stop hook** (installed at `up`).

### What's *not* in this config
- **No `pings`.** None of the four agents has a periodic ping
  configured, so no agent is auto-nudged on a timer while idle — the pipeline is
  purely event-driven off real mail. (If you wanted the spec_reader to poke a
  slow writer, you'd add a `pings` cron rule to it.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 3. Shared workdir & mailbox namespacing

The two writers intentionally share `test-factory-workspace/tests-repo/`. This is
what lets the unit and integration suites live in the same checkout — but it also
means the writers co-write one directory. When the config loads, Agentainer counts
how many agents resolve to each `workdir`; any workdir shared by two or more
agents is recorded, and `SwarmConfig.mail_paths()` applies a `<name>-` prefix to
every one of that agent's five mailbox folders so they never collide:

```
tests-repo/
  unit_writer-inbox/         integration_writer-inbox/
  unit_writer-outbox/        integration_writer-outbox/
  unit_writer-read/          integration_writer-read/
  unit_writer-sent/          integration_writer-sent/
  unit_writer-failed/        integration_writer-failed/
  # ...your actual project files live here too, unprefixed
```

The prefix is **orchestrator-internal bookkeeping** — the model never sees,
computes, or reasons about it. Every nudge and first prompt hands the agent the
already-computed absolute paths, so a shared workdir is indistinguishable from a
private one from the model's point of view. (This is why the shared-workdir
warning at `up` is informational, not an error — the config is wired correctly.)

For the full story, including per-agent `workdir` and `mail_dir` knobs and how to
point an agent at an existing repo, see
[`custom-workspace.md`](../use-cases/custom-workspace.md).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/test-factory.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the `capture: none → hook` upgrades and
   the shared-workdir notice.
2. Creates the runtime dirs (`test-factory-workspace/.agentainer/…`: log, queue,
   run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. For the shared workdir,
   those folders are **namespaced** (`unit_writer-inbox/`, etc.) as described in
   §3.
4. **Installs per-type turn detection** — the Claude Stop hook for spec_reader and
   coverage, and the Codex `notify` hook for the two writers, each installed at
   `up`.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'test-factory' is up with 4 agent(s)
:: attach with:  tmux attach -t <spec_reader-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/test-factory.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only bind — the UI **defaults to `127.0.0.1`** and never binds
`0.0.0.0` without an explicit opt-in token (see CLAUDE.md §18 and the
`README.md` "control-plane UI" section).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole factory route mail with no API keys — the mechanics are identical.

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the coverage verdict as mail (rather than have
it held), turn yourself available first:

```bash
./agentainer user available -c examples/test-factory.yaml
```

This rewrites the `user` contact card in `coverage`'s and `spec_reader`'s
`outbox/user/about.md` to `Status: available`, so they see you're reachable.
(While away, mail to you is *held* and the sender gets a `system` ack — nothing
bounces.)

Now send the spec into the swarm, addressed to the spec_reader:

```bash
./agentainer send --to spec_reader "Generate tests for the new rate-limiter in <repo>."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the spec_reader, then — because
the inbox was empty — **released into `inbox/`** and the spec_reader is
**nudged** (the protocol is re-pasted into its pane, including its allowed-
recipient list).

### The mail flowing

Watching the log (§6), you'll see the pipeline advance one turn at a time. The
spec_reader's turn fans out to two writers in parallel:

1. **spec_reader receives the spec.** It reads `inbox/`, decides what to test, and
   writes two briefs — one into `outbox/unit_writer/`, one into
   `outbox/integration_writer/` — plus a scope note to `outbox/coverage/`. When its
   turn ends, the orchestrator sweeps the outbox and nudges both writers.
2. **the two writers run in parallel.** Each reads its inbox, writes its tests into
   the shared `tests-repo/` checkout, and writes a summary back into
   `outbox/spec_reader/`. Their turns complete independently; the orchestrator
   routes each summary back to the hub.
3. **coverage reviews.** It reads the generated test files and writes gap notes to
   `outbox/spec_reader/`, then a human-readable verdict to `outbox/user/`. On stop,
   that's delivered to your `user` mailbox (you'll see it with `agentainer user
   inbox`, or in the UI).
4. **the spec_reader can act on the coverage notes** — if coverage flagged a gap,
   the hub can re-brief a writer, and the loop tightens.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/test-factory.yaml
```

```
swarm: test-factory   root: ./test-factory-workspace
  spec_reader (claude) up idle queue=0 unread=0 talks=unit_writer, integration_writer, coverage, user
  unit_writer (codex) up idle queue=0 unread=1 talks=spec_reader
  integration_writer (codex) up idle queue=0 unread=1 talks=spec_reader
  coverage (claude) up idle queue=0 unread=0 talks=spec_reader, user
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/test-factory.yaml          # whole swarm, last 20
./agentainer logs -c examples/test-factory.yaml -f        # follow live
./agentainer logs coverage -c examples/test-factory.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox unit_writer -c examples/test-factory.yaml
```

Prints the one released message (headers + body), or `unit_writer: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue unit_writer -c examples/test-factory.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach unit_writer -c examples/test-factory.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Resume after a stop

Tear the swarm down when you're done:

```bash
./agentainer down -c examples/test-factory.yaml
```

Bring it back later and **conversations resume by default**:

```bash
./agentainer up -c examples/test-factory.yaml     # resume is the default
```

On `up`, Agentainer reads `test-factory-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for
spec_reader and coverage, `codex resume <id>` for the two writers. The writers
share a workdir but keep **separate** session ids, so their resumes don't collide.
A resumed agent is *not* re-sent the standby prompt (its prior context is
restored) — handy when a coverage review sends the hub back for a second pass.

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/test-factory.yaml
```

For the full story, see
[`sessions-and-resume.md`](../sessions-and-resume.md) and the reboot
walkthrough in [`use-cases/resume-after-reboot.md`](./resume-after-reboot.md).

---

## 8. Iterate on the output

The factory is a loop, not a one-shot. When `coverage` flags a gap (a branch never
exercised, a test that asserts nothing), that verdict lands in the spec_reader's
inbox. The spec_reader can re-brief the relevant writer with the missing cases,
and the suite tightens over a few passes — no human in the middle. To inspect what
was actually generated, attach to a writer or just look in the shared
`tests-repo/` checkout:

```bash
./agentainer attach integration_writer -c examples/test-factory.yaml
```

If a writer seems stuck, check that its **turn detection actually fires** — a
`type`/`command` mismatch (e.g. a `codex` agent whose `command` doesn't launch
Codex) means completion never triggers and the agent pins "busy" forever. `status`
showing a writer `busy` for a long time with `unread` mail is the tell.

---

## 9. Tips & footguns

- **Keep the `user`-facing surface small.** Only `spec_reader` and `coverage`
  list `user` in `can_talk_to`. That gives you a single, reviewed funnel: raw test
  output always passes through the spec_reader's plan and the coverage review
  before it reaches you. If a writer tries to mail `user` directly, the
  orchestrator bounces it (ACL) and drops a `system` note in its inbox explaining
  who it *can* message — the model self-corrects in-band.

- **The two writers share a workdir by design.** That's how their suites co-exist
  in one checkout — but it also means they can overwrite each other's files. The
  spec_reader's briefs should keep their assignments disjoint (unit vs.
  integration, and ideally separate files/folders). The shared-workdir warning at
  `up` is expected, not an error.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **Availability shapes the ending.** If `user` is **away** when `coverage`
  finishes, your verdict is *held* (with a `system` "the user is away" ack to
  coverage) rather than lost — read it later with `agentainer user inbox` or flip
  yourself available and it's delivered.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/test-factory.yaml
  ./agentainer remove-session -c examples/test-factory.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

---

## 10. Customize

The factory is a template — bend it to your shop:

- **Add a `fuzz` agent.** Drop in a fifth agent (e.g. `fuzz`, `type: hermes`,
  `can_talk_to: [spec_reader]`) that runs property-based / mutation tests against
  the same `tests-repo/` workdir, and add `fuzz` to the spec_reader's
  `can_talk_to`. One more arrow out of the hub; no other change needed.

- **Swap models per role.** The writers are `codex` and the planners are `claude`
  here, but any of the four `type`s (`claude`, `codex`, `gemini`, `hermes`) work as
  long as `command` launches the matching CLI. Want `gemini` writers instead?
  Change `type` and `command` together — a `type`/`command` mismatch is rejected at
  `up` (it would silently wedge the agent). See
  [`multi-llm-swarm.md`](../use-cases/multi-llm-swarm.md) for mixing providers.

- **Tune the ACL.** Tighten further (e.g. make `coverage` report *only* to
  `spec_reader`, and let the hub summarize to `user`) or loosen (let `coverage`
  re-brief a writer directly by adding it to coverage's `can_talk_to`). The
  orchestrator enforces whatever you write. See
  [`delegation-pipeline.md`](../use-cases/delegation-pipeline.md) for patterns.

- **Point at a real repo.** Edit both writers' `workdir` to your checkout
  (`workdir: ../my-app`) and set `create_workdir: false` so Agentainer refuses
  rather than clobber it. See
  [`custom-workspace.md`](../use-cases/custom-workspace.md).

- **Add a periodic nudge.** If a long spec makes the hub go quiet, add
  a `pings` cron rule to `spec_reader` so it's gently reminded to
  keep moving.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the file-based mail model in full.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming conversations.
- [`delegation-pipeline.md`](../use-cases/delegation-pipeline.md) — hub-and-spoke patterns.
- [`multi-llm-swarm.md`](../use-cases/multi-llm-swarm.md) — mixing coding-CLI providers.
- [`custom-workspace.md`](../use-cases/custom-workspace.md) — shared workdirs and mailbox namespacing.
- `examples/test-factory.yaml` — the config this walkthrough is built from.

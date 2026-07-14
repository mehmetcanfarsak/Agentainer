# Use case: Research → Plan → Implement pipeline

A concrete, end-to-end walkthrough of the shipped
`examples/rpi-pipeline.yaml` swarm — a linear **RPI handoff loop** where an
orchestrator sequences three specialists so each stage hands a structured
artifact to the next: a **researcher** gathers facts/options/constraints and
writes a research brief, a **planner** turns that brief into a concrete
step-by-step plan with tradeoffs, and an **implementer** executes the plan and
reports what was built and what changed. The orchestrator is the only agent that
talks to you, and it guarantees each handoff artifact is complete before the
pipeline advances to the next stage.

Everything below is based on the actual contents of
`examples/rpi-pipeline.yaml` and the shipped CLI (`lib/cli.py`) and mailroom
(`lib/mail.py`). To run it *for real* you supply the coding-CLI commands (or
swap them for mock bash loops), but the mechanics — routing, ACL,
turn-completion — need no API keys to understand.

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Builders who have a goal but want the *discovery, design, and execution* done by
separate heads so each one stays honest about its own job: a researcher who must
not write code, a planner who must not execute, and an implementer who must not
redesign the approach. The swarm encodes the discipline that makes a handoff
safe — a single orchestrator owns the sequence and refuses to advance on an
empty or off-target artifact — while the specialists do the actual typing.

It is deliberately a **linear pipeline behind a single human-facing hub**, not a
free-for-all: every request and every deliverable passes through the
orchestrator, so the goal has exactly one authority and the specialists never
compare notes directly (which is what keeps the researcher's "what's possible"
from leaking into the planner's "what we'll do" or the implementer's "what I
actually shipped"). Swapping a specialist onto a different model is a
one-`type`-change.

---

## 2. The topology

```
            goal
  user ───────────────▶ orchestrator        (the hub: talks to researcher, planner, implementer, user)
         (final)   ◀──────┬──────────────────────────────┐
                          │ research brief                │ plan
                          ▼                               ▼
                      researcher ──brief──▶ planner ──plan──▶ implementer
                      (codex)              (gemini)          (claude)
                                              │ built + changed │
                                              └─────────────────┘
               all arrows route THROUGH the orchestrator;
               specialists never talk to each other directly.
```

Four agents, one directed flow:

1. **`user` → `orchestrator`** — you send the goal.
2. **`orchestrator` → `researcher`** — the orchestrator mails a precise research
   brief request (what to find out, what options to compare, what constraints
   matter).
3. **`researcher` → `orchestrator`** — the researcher returns a structured
   `RESEARCH BRIEF` (questions, options + pro/con, constraints/risks, a
   recommended direction).
4. **`orchestrator` → `planner`** (with the brief) — the orchestrator verifies
   the brief is complete, then hands it to the planner to produce an ordered
   `IMPLEMENTATION PLAN` with tradeoffs and a definition of done.
5. **`planner` → `orchestrator`** — the plan returns to the orchestrator, who
   verifies it is actionable and consistent.
6. **`orchestrator` → `implementer`** (with the plan) — the orchestrator hands
   off the plan to execute.
7. **`implementer` → `orchestrator`** — the implementer reports what was built /
   changed and whether the definition of done was met.
8. **`orchestrator` → `user`** — the orchestrator checks the deliverable against
   the original goal and writes a short final summary to you, then stops.

The routing above is *enforced* by each agent's `can_talk_to` list. An agent can
only deliver to names on its own list; anything else is bounced back as a
`system` message and filed in `failed/`. Notably, `researcher`, `planner`, and
`implementer` **never** talk to `user` directly, and **never** talk to each
other — only the orchestrator does. (See §7.)

---

## 3. The config, explained

Here is `examples/rpi-pipeline.yaml` in full:

```yaml
swarm:
  name: rpi-pipeline
  root: ./rpi-pipeline-workspace

defaults:
  capture: none              # upgraded to the type's hook for claude/codex at up
  can_talk_to: []           # tightened per agent below

agents:
  - name: orchestrator
    type: claude
    can_talk_to: [researcher, planner, implementer, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the RPI orchestrator. You own the RESEARCH → PLAN → IMPLEMENT
      pipeline end to end. When the user gives you a goal, you kick off stage 1
      by mailing the researcher a precise research brief request ... When the
      researcher returns a research brief, you verify it is complete and
      substantive, then hand it to the planner ... When the planner returns the
      plan, you verify it is actionable and internally consistent, then hand it
      to the implementer ... When the implementer reports back, you check the
      deliverable against the original goal, write a short final summary to the
      user, and stop. You never do the research, planning, or implementation
      yourself — you sequence the specialists and guarantee each handoff
      artifact is present and complete before advancing ... If any stage returns
      an empty or off-target artifact, send it back with specific correction
      instructions rather than advancing.

      MAILBOX: read new mail in inbox/, act, then move it to read/. To reply,
      write a file into outbox/<name>/ (read outbox/<name>/about.md first) and
      finish your turn. You may message: researcher, planner, implementer, user.

  - name: researcher
    type: codex
    can_talk_to: [orchestrator]
    command: "codex --yolo"
    role: |
      You are the RESEARCHER in an RPI pipeline ... gather the facts, enumerate
      the realistic options, surface the constraints and risks, and write a
      single structured RESEARCH BRIEF addressed back to the orchestrator. The
      brief must clearly separate: (1) the question/goal, (2) the options with a
      one-line pro/con for each, (3) the hard constraints and risks, and (4) a
      recommended direction ... Do not write a plan or write code — research
      only.

      MAILBOX: read new mail in inbox/, act, then move it to read/. To reply,
      write a file into outbox/<name>/ (read outbox/<name>/about.md first) and
      finish your turn. You may message: orchestrator.

  - name: planner
    type: gemini
    can_talk_to: [orchestrator]
    command: "gemini --yolo"
    role: |
      You are the PLANNER in an RPI pipeline ... turn [the brief] into a single
      concrete, ordered IMPLEMENTATION PLAN addressed back to the orchestrator.
      The plan must list the steps in execution order, state the concrete
      tradeoffs chosen at each decision point (and what was rejected and why),
      name the files or components to create or change, and end with an explicit
      definition of done ... Do not write or execute code — plan only.

      MAILBOX: read new mail in inbox/, act, then move it to read/. To reply,
      write a file into outbox/<name>/ (read outbox/<name>/about.md first) and
      finish your turn. You may message: orchestrator.

  - name: implementer
    type: claude
    can_talk_to: [orchestrator]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the IMPLEMENTER in an RPI pipeline ... execute that plan and then
      write a single IMPLEMENTATION REPORT addressed back to the orchestrator.
      The report must state exactly what you built or changed (files
      created/edited, commands run), confirm whether the plan's definition of
      done was met, and flag anything you deviated from or could not complete and
      why. Follow the plan's ordered steps; do not redesign the approach ...

      MAILBOX: read new mail in inbox/, act, then move it to read/. To reply,
      write a file into outbox/<name>/ (read outbox/<name>/about.md first) and
      finish your turn. You may message: orchestrator.
```

Field by field:

### `swarm`
- **`name: rpi-pipeline`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./rpi-pipeline-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent's workdir defaults to
  `rpi-pipeline-workspace/<name>` (`orchestrator`, `researcher`, `planner`,
  `implementer`), so all four are **private and unprefixed** (no shared workdir
  here — see the note below). Orchestrator state goes under
  `rpi-pipeline-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default capture mode. The loader *upgrades* this to
  the type's natural hook for `claude`/`codex` at `up` (see Turn detection
  below); `gemini` falls back to pane polling. So `none` here is "use the
  type's default," not "detect nothing."
- **`can_talk_to: []`** — the default ACL is "talk to no one." Every agent
  below states its own list explicitly, so this default is just a safe floor.

### `orchestrator` (type: `claude`)
- **`can_talk_to: [researcher, planner, implementer, user]`** — the orchestrator
  is the hub: it delegates to the three specialists and is the **only agent that
  can talk to `user`**. That last part matters — keep the human-facing surface to
  a single agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command, e.g. a
  shell alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity: sequence the specialists, **verify each
  handoff artifact before advancing**, and send any empty/off-target artifact
  back for correction. On `up` this becomes the agent's first prompt, wrapped in
  a **standby notice** ("no task yet — don't send anything, you'll be notified"),
  so the orchestrator waits for your goal instead of proactively mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `researcher` (type: `codex`)
- **`can_talk_to: [orchestrator]`** — the researcher only reports back to the
  orchestrator. It deliberately cannot reach the planner, the implementer, or the
  `user`; the brief flows one way in, one way out.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "produce the structured `RESEARCH BRIEF` (questions, options +
  pro/con, constraints/risks, recommended direction); research only — no plan,
  no code."
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `planner` (type: `gemini`)
- **`can_talk_to: [orchestrator]`** — the planner only reports the plan back to
  the orchestrator. It cannot reach the researcher, the implementer, or the
  `user`.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **`role`** — "turn the brief into a single ordered `IMPLEMENTATION PLAN` with
  explicit tradeoffs (what was chosen / rejected and why), named files/components,
  and a definition of done; plan only — no code."
- **Turn detection:** `gemini` has no completion hook, so it falls back to
  **pane polling** (the supervisor watches its pane for turn end). The `capture:
  none` default is overridden by the type's natural mode here.

### `implementer` (type: `claude`)
- **`can_talk_to: [orchestrator]`** — the implementer only reports the
  implementation report back to the orchestrator. It cannot reach the researcher,
  the planner, or the `user`.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **`role`** — "execute the plan and write a single `IMPLEMENTATION REPORT`
  (what was built/changed, whether the definition of done was met, and any
  deviations); follow the ordered steps; don't redesign unless a step is
  impossible."
- **Turn detection:** `claude` → Stop hook.

### The shared-workdir note (not triggered here)

Unlike the data-pipeline builder, **no agent in this swarm shares a workdir** —
all four resolve to distinct `rpi-pipeline-workspace/<name>` directories, so no
mailbox namespacing happens and nothing needs the `<name>-` prefix. If you later
give two agents the same `workdir:`, `mail_paths()` would namespace their
folders automatically (the model never sees it). For the full treatment see
[`custom-workspace.md`](./custom-workspace.md).

### ACL enforcement

The `can_talk_to` lists are the *only* thing that lets an agent deliver mail.
The `outbox/<peer>/about.md` contact card is the ACL made visible: the
orchestrator gets `outbox/researcher/`, `outbox/planner/`, `outbox/implementer/`,
`outbox/user/`; each specialist gets exactly `outbox/orchestrator/`. If a
specialist tries to write straight to `user` (or to a peer), the orchestrator
bounces it as a `system` message and drops it in `failed/` — the model
self-corrects in-band (see Tips). This is **cooperative, not OS isolation**:
agents have filesystem access and *could* write into another inbox, so it is
documented honestly and enforced for well-behaved agents. (Decision D15 in
`ProjectPlan.md`.)

### What's *not* in this config
- **No `pings`.** The swarm is purely event-driven off real mail — it only moves
  when you send a goal. (Add a `pings:` block to the orchestrator if you want a
  stale-pipeline nag; see [`configuration.md`](../configuration.md).)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **No `workdir` overrides.** All four agents keep their distinct default
  directories.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/rpi-pipeline.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings.
2. Creates the runtime dirs (`rpi-pipeline-workspace/.agentainer/…`: log, queue,
   run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The orchestrator gets
   four `outbox/` peers; each specialist gets exactly one (`outbox/orchestrator/`).
   The `outbox/<peer>/about.md` contact card *is* the ACL made visible.
4. **Installs per-type turn detection** — the Claude Stop hook for `orchestrator`
   and `implementer`, the Codex `notify` hook for `researcher`, and pane polling
   for the `gemini` `planner`.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the pipeline.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'rpi-pipeline' is up with 4 agent(s)
:: attach with:  tmux attach -t <orchestrator-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/rpi-pipeline.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents, so it must **never** be exposed on `0.0.0.0` without a token.
See [`ui-guide.md`](../ui-guide.md).

> **Real-CLI but placeholder launch strings:** the config ships `claude`,
> `codex`, and `gemini` commands, so the swarm routes *real* mail with no mock
> loops — but the launch strings are placeholders. Substitute your own command
> (e.g. a shell alias that carries your API key). To watch the mechanics for free,
> swap each `command:` for a mock bash loop and the routing is identical.

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the orchestrator's final summary as mail
(rather than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/rpi-pipeline.yaml
```

This rewrites the `user` contact card in the orchestrator's `outbox/user/about.md`
to `Status: available`, so the orchestrator sees you're reachable. (While away,
mail to you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the goal into the swarm, addressed to the orchestrator:

```bash
./agentainer send --to orchestrator -c examples/rpi-pipeline.yaml \
  "Build a CLI that converts CSV to JSON, streaming large files."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the orchestrator, then — because
the inbox was empty — **released into `inbox/`** and the orchestrator is
**nudged** (the protocol is re-pasted into its pane, including its allowed-
recipient list).

### The mail flowing

Watching the log (§6), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **orchestrator receives the goal.** It reads `inbox/`, writes a research brief
   request into `outbox/researcher/`. On stop, that routes to the researcher.
2. **researcher writes the brief.** It reads its inbox, writes the structured
   `RESEARCH BRIEF`, and reports back into `outbox/orchestrator/`. On stop, that
   routes to the orchestrator, who **verifies it is complete and substantive**
   before advancing.
3. **orchestrator briefs the planner.** It writes the brief into
   `outbox/planner/` with instructions for a plan + tradeoffs + definition of
   done. On stop, that routes to the planner.
4. **planner writes the plan.** It reads its inbox, writes the ordered
   `IMPLEMENTATION PLAN`, and reports back into `outbox/orchestrator/`. On stop,
   the orchestrator **verifies it is actionable and consistent**.
5. **orchestrator hands off to the implementer.** It writes the plan into
   `outbox/implementer/` with execution instructions. On stop, that routes to the
   implementer.
6. **implementer builds.** It reads its inbox, executes the plan, and writes the
   `IMPLEMENTATION REPORT` (what was built/changed, definition-of-done met?,
   deviations) into `outbox/orchestrator/`. On stop, that routes back.
7. **orchestrator finalizes.** It checks the deliverable against the original
   goal, writes the short final summary into `outbox/user/`, and stops. On stop,
   that's delivered to your `user` mailbox (visible with `agentainer user inbox`,
   or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion. If any
stage returns an empty or off-target artifact, the orchestrator sends it back
with correction instructions rather than advancing — the pipeline is self-
correcting.

> If you *don't* send a goal, the agents just sit in standby (that's the point of
> the standby prompt). The pipeline only moves when real mail arrives — this
> swarm has no periodic pings to self-start it.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/rpi-pipeline.yaml
```

```
swarm: rpi-pipeline   root: ./rpi-pipeline-workspace
  orchestrator (claude) up idle queue=0 unread=0 talks=researcher, planner, implementer, user
  researcher    (codex)  up idle queue=0 unread=1 talks=orchestrator
  planner       (gemini) up idle queue=0 unread=0 talks=orchestrator
  implementer   (claude) up idle queue=0 unread=0 talks=orchestrator
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/rpi-pipeline.yaml          # whole swarm, last 20
./agentainer logs -c examples/rpi-pipeline.yaml -f        # follow live
./agentainer logs researcher -c examples/rpi-pipeline.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox orchestrator -c examples/rpi-pipeline.yaml
```

Prints the one released message (headers + body), or `orchestrator: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue orchestrator -c examples/rpi-pipeline.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach researcher -c examples/rpi-pipeline.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom
— handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or (more directly)
by sending notes into an agent's inbox.

- **Send a clarification to the orchestrator.** Realized the CLI must also handle
  TSV? `./agentainer send --to orchestrator -c examples/rpi-pipeline.yaml
  "Also accept tab-separated input; re-brief the researcher on the delimiter
  requirement."` The orchestrator relays the change down the chain.
- **Ask the implementer for the evidence.** `./agentainer send --to orchestrator
  ... "Have the implementer list the exact files it created."` — the orchestrator
  forwards it.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different goal), tear it down:

```bash
./agentainer down -c examples/rpi-pipeline.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/rpi-pipeline.yaml     # resume is the default
```

On `up`, Agentainer reads `rpi-pipeline-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the
orchestrator and implementer, `codex resume <id>` for the researcher, and the
gemini planner's resume path. A resumed agent is *not* re-sent the standby
prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/rpi-pipeline.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `researcher: type: claude` (or `hermes`/`gemini`) to put research on a
  different model than the orchestrator.
- `planner: type: codex` if you want the plan authored by Codex instead of Gemini
  — and note the turn-detection mode changes with it (`notify` hook instead of
  pane polling).
- `implementer: type: gemini` to move execution onto Gemini (back to pane
  polling).
- Remember: `gemini`/`hermes` need pane polling (no completion hook), so their
  `capture` falls back to `pane` automatically; `claude`/`codex` use their hooks.

### Add a reviewer / QA stage
Once the implementer reports back, you may want a separate reviewer before the
final summary. Add a fifth agent and wire it into the chain:

```yaml
  - name: reviewer
    type: claude
    can_talk_to: [orchestrator]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the IMPLEMENTATION REVIEWER. The orchestrator hands you the
      implementer's report. Critique it against the plan's definition of done,
      flag gaps or deviations, and report a verdict back to outbox/orchestrator/.
      You never write or change code yourself.
```

Then insert `reviewer` into the orchestrator's `can_talk_to` so it can be briefed
between the implementer and the final summary.

### Tune the ACL
- To let the `implementer` escalate straight to `user` (not only via the
  orchestrator), add `user` to its `can_talk_to`. Mind that this widens the
  human-facing surface; the doc's convention keeps the orchestrator the sole
  `user` contact.
- To keep the researchers/planner/implementer strictly siloed (already the case
  here), leave each `can_talk_to: [orchestrator]` — that's the "specialists
  never compare notes" guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md)
  for mixing model families safely.

---

## 10. Tips & footguns

- **Keep the orchestrator the only `user`-facing agent.** Only the orchestrator
  lists `user` in `can_talk_to`. That gives you a single funnel: raw research,
  plans, and implementation reports always pass through review before they reach
  you. If a specialist tries to mail `user` directly, the orchestrator bounces it
  (ACL) and drops a `system` note in their inbox explaining who they *can*
  message — the model self-corrects in-band.

- **Specialists can't shortcut to each other.** `researcher`, `planner`, and
  `implementer` each list only `orchestrator`. This is what keeps the pipeline
  linear and the artifacts clean — the researcher's "what's possible" never
  leaks past the orchestrator into the planner's "what we'll do." If you want a
  researcher→planner direct line, you *must* add it to both `can_talk_to` lists;
  the orchestrator won't be able to enforce the completeness check on that hop.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch (e.g. a `claude` agent whose `command`
  doesn't launch Claude) means completion never triggers and the agent pins
  "busy" forever. `status` showing an agent `busy` for a long time with `unread`
  mail is the tell. For the `gemini` planner this means confirming pane polling is
  actually observing its pane.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **The orchestrator's verify step is the quality gate.** Its role explicitly
  refuses to advance on an empty or off-target artifact and sends it back with
  correction instructions. If the pipeline "loops" between the orchestrator and a
  specialist, that's the gate working — read the log to see what the orchestrator
  found missing.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/rpi-pipeline.yaml
  ./agentainer remove-session -c examples/rpi-pipeline.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

- **Availability shapes the ending.** If `user` is **away** when the orchestrator
  finishes, your final summary is *held* (with a `system` "the user is away" ack
  to the orchestrator) rather than lost — read it later with
  `agentainer user inbox` or flip yourself available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- [`cli-reference.md`](../cli-reference.md) — every subcommand, including `validate`.
- [`configuration.md`](../configuration.md) — `pings:`, `capture:`, and the full field reference.
- `examples/rpi-pipeline.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

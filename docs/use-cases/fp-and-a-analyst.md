# Use case: FP&A analyst

A concrete, end-to-end walkthrough of the shipped
`examples/fp-and-a-analyst.yaml` swarm — a finance-variance + forecast-narrative
assembly line that turns a pair of ledgers (or a plain-English finance question)
into a CFO-ready memo a non-accountant can act on. A **controller** hub takes the
request from you, delegates the math to a **variance-analyst**, the story to a
**narrative-writer**, and routes the assembled draft through a **reviewer** that
sanity-gates the numbers-and-story before anything reaches the human. The
reviewer is the last word — the human never sees a memo it has not cleared.

Everything below is based on the actual contents of
`examples/fp-and-a-analyst.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Finance teams, founders, and operators who need a monthly (or ad-hoc) read on
what happened to the money without doing the variance math and the memo
themselves. The swarm encodes the discipline that makes a finance writeup
trustworthy — one owner of the human-facing surface, a numeric analyst who never
invents the story, a writer who never invents the numbers, and a reviewer who
checks that the two actually agree before anyone sees them.

It is deliberately a **hub-and-spoke**, not a free-for-all: every request and
every deliverable passes through the controller, so the point where the numbers
meet the story (and where the reviewer's gate sits) lives in exactly one place.
Swapping in a real `forecast-analyst` agent (see `examples/forecast-analyst.yaml`)
or adding a second reviewer is a few lines of config.

---

## 2. The topology

```
          user
            |
         controller                 (the hub: talks to all three specialists + user)
          /    |    \
   variance-   narrative-   reviewer
   analyst     writer       (the SANITY GATE -- clears the memo before user sees it)
   (codex)     (gemini)
```

Four agents, one directed flow:

1. **`user` → `controller`** — you send the actuals ledger + budget/forecast
   ledger (as files, a paste, or a location to read), or a plain-English finance
   question.
2. **`controller` → `variance-analyst`** — the controller sends both ledgers plus
   the period/entity scope and asks for a by-account actual-vs-budget variance
   (dollar + percent deltas, the sign explained, the biggest drivers ranked).
3. **`variance-analyst` → `controller`** — the variance table comes back.
4. **`controller` → `narrative-writer`** — the controller hands over the variance
   table and asks for a CFO-ready memo (headline, what drove it, what to watch,
   the forecast call).
5. **`narrative-writer` → `controller`** — the memo comes back.
6. **`controller` → `reviewer`** — the controller assembles the table + memo into
   one draft and routes it to the reviewer. The reviewer is the **sanity gate**:
   it checks that every figure in the narrative matches the variance table, the
   signs are right, the story follows the data, and the forecast call is honest.
   It replies `CLEAR` or `BOUNCE` (with specific defects).
7. **`reviewer` → `controller`** — on `BOUNCE`, the controller re-delegates the
   fix (back to variance-analyst and/or narrative-writer) and re-routes until the
   reviewer signs off. On `CLEAR`, the controller writes the final memo.
8. **`controller` → `user`** — the cleared CFO memo is delivered to you.

The routing above is *enforced* by each agent's `can_talk_to` list. The three
specialists **never** talk to `user` (or to each other) — only the controller
does. If a specialist tried to mail `user` directly, the orchestrator bounces it
as a `system` message and files it in `failed/`.

---

## 3. The config, explained

Here is `examples/fp-and-a-analyst.yaml` in full (role bodies abbreviated with
`...` for readability; the structure, names, ACLs, commands, and `pings` are
exact):

```yaml
swarm:
  name: fp-and-a-analyst
  root: ./fp-and-a-analyst-workspace

defaults:
  capture: none              # claude/codex are auto-upgraded to their hook at up
  can_talk_to: []            # tightened per agent below

agents:
  - name: controller
    type: claude
    can_talk_to: [variance-analyst, narrative-writer, reviewer, user]
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: |
          Monthly close is here. Pull the latest actuals ledger and the
          budget/forecast ledger from the user (or from the standard ledger/
          location), run the full VARIANCE-ANALYST -> NARRATIVE-WRITER ->
          REVIEWER loop, and post the cleared CFO memo to user. If either ledger
          is missing, ask the user for it before delegating.
        cron: "0 8 1 * *"             # 08:00 on the 1st of every month
        when_busy: skip
    role: |
      You are the CONTROLLER and the only agent who talks to the human (user). ...
      (1) read the ledgers, ask ONE clarifying question if scope is ambiguous;
       (2) delegate to VARIANCE-ANALYST; (3) delegate to NARRATIVE-WRITER;
       (4) assemble the draft and route to REVIEWER -- the sanity gate -- and
       re-route until it CLEARs; (5) only then post the final memo to user. ...

  - name: variance-analyst
    type: codex
    can_talk_to: [controller]
    command: "codex --yolo"
    role: |
      You are the VARIANCE-ANALYST. Compute a clean by-account actual-vs-budget
      variance ... dollar + percent deltas, sign explained, top drivers ranked ...
      Do NOT write the narrative. Report ONLY to the controller. ...

  - name: narrative-writer
    type: gemini
    can_talk_to: [controller]
    command: "gemini --yolo"
    role: |
      You are the NARRATIVE-WRITER. Turn the variance table into a plain-language
      CFO memo ... headline, drivers, what to watch, forecast call ... Never cite
      a figure not in the table. Report ONLY to the controller. ...

  - name: reviewer
    type: claude
    can_talk_to: [controller]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the REVIEWER -- the SANITY GATE. Check numbers match, sign correct,
      story follows data, call is honest, legible ... reply CLEAR or BOUNCE.
      The human must NEVER see a memo you have not signed off. Report ONLY to
      the controller. ...
```

Field by field:

### `swarm`
- **`name: fp-and-a-analyst`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./fp-and-a-analyst-workspace`** — the parent directory for the
  agents' working directories and mailboxes. Each agent's workdir defaults to
  `fp-and-a-analyst-workspace/<name>` (controller, variance-analyst,
  narrative-writer, reviewer), and orchestrator state goes under
  `fp-and-a-analyst-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless it overrides them.
- **`capture: none`** — the default turn-detection mode. At `up`, the loader
  **auto-upgrades** this for `claude` and `codex` to their natural hook (the
  `validate` run prints three warnings confirming it — see §3 turn-detection
  below). It is a safe floor; every agent states its own `can_talk_to`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Each agent below
  states its own list explicitly.

### `controller` (type: `claude`)
- **`can_talk_to: [variance-analyst, narrative-writer, reviewer, user]`** — the
  controller is the hub and the **only agent that can talk to `user`**. That last
  part is the whole point: keep the human-facing surface to one agent and put the
  reviewer's gate in front of it.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command, e.g. a
  shell alias. Treat command strings as sensitive; they may embed keys.)
- **`pings:`** — the controller carries the swarm's only scheduled ping (see
  §3 *The pings/cron*).
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at
  `up`; the `capture: none` default is auto-upgraded to hook here).

### `variance-analyst` (type: `codex`)
- **`can_talk_to: [controller]`** — reports the variance table back to the
  controller and nowhere else. It cannot reach the user, the writer, or the
  reviewer directly.
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`
  (the `capture: none` default auto-upgrades to the notify hook).

### `narrative-writer` (type: `gemini`)
- **`can_talk_to: [controller]`** — receives the variance table from the
  controller and returns the memo to the controller only. It never touches the
  user.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **Turn detection:** `gemini` has no completion hook, so it relies on **pane
  polling** — the supervisor watches its pane for turn completion. (This is why
  the `capture: none` default needs no upgrade for gemini; only claude/codex get
  the auto-hook warnings.)

### `reviewer` (type: `claude`)
- **`can_talk_to: [controller]`** — the gate lives behind the controller: the
  reviewer only ever talks to the controller, replying `CLEAR` or `BOUNCE`. It
  cannot reach the user, so its verdict is always relayed through the hub.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook (auto-upgraded from `capture: none`).

### ACL enforcement

The ACL is **cooperative, not OS isolation** (Decision D15): agents have
filesystem access and *could* write straight into another inbox, but the
orchestrator only ever *releases* and *routes* mail between names on the
sender's `can_talk_to` list. Anything addressed outside that list is bounced back
as a `system` message filed in `failed/`, so a model that forgets the rule
self-corrects in-band. Here that means the three specialists can *only* reach the
controller, and only the controller can reach `user` — the reviewer's gate is
structurally guaranteed to sit between the draft and the human.

### Per-type turn detection

Turn-completion detection is the system clock (the stop → sweep → route → release
→ nudge loop). It is **per `type`**:
- `claude` (`controller`, `reviewer`) → **Stop hook** — fires when Claude finishes
  a turn.
- `codex` (`variance-analyst`) → **`notify` hook** — fires when Codex finishes.
- `gemini` (`narrative-writer`) → **pane polling** — the supervisor reads the
  pane to decide the turn ended.

A `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
Claude) means completion never fires and the agent pins "busy" forever — which is
why `command` must launch the same CLI family `type` implies.

### The pings / cron

Only the **controller** has a `pings:` block, and it has exactly one entry:

```yaml
pings:
  - message: |
      Monthly close is here. Pull the latest actuals ledger and the
      budget/forecast ledger ... run the full VARIANCE-ANALYST ->
      NARRATIVE-WRITER -> REVIEWER loop, and post the cleared CFO memo to user.
      If either ledger is missing, ask the user for it before delegating.
    cron: "0 8 1 * *"             # 08:00 on the 1st of every month
    when_busy: skip
```

- **`cron: "0 8 1 * *"`** — fires at **08:00 on the 1st of every month** (right
  after books close), injecting the monthly-close prompt into the controller's
  inbox as a nudge.
- **`when_busy: skip`** — if the controller is mid-turn (a live ad-hoc question),
  the ping is **skipped** rather than queued on top of the in-flight work. This is
  what keeps a scheduled close from piling onto a mid-month query.

This is the one piece of self-starting behavior in the swarm; everything else is
event-driven off your mail. See [`configuration.md`](../configuration.md) for the
full `pings:` / `cron:` / `when_busy` grammar.

### What's *not* in this config
- **No `workdir` overrides.** All four agents get the default
  `fp-and-a-analyst-workspace/<name>`, so no mailbox namespacing is needed
  (each agent owns its directory). For the shared-workdir case, see
  [`custom-workspace.md`](./custom-workspace.md).
- **No per-agent `capture` overrides.** The `defaults: capture: none` is
  auto-upgraded to the type's hook for claude/codex; gemini uses pane polling.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/fp-and-a-analyst.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the three `capture: none` auto-upgrade
   warnings for the claude/codex agents.
2. Creates the runtime dirs (`fp-and-a-analyst-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/
   about.md` contact card *is* the ACL made visible: the controller gets
   `outbox/variance-analyst/`, `outbox/narrative-writer/`, `outbox/reviewer/`,
   `outbox/user/`; each specialist gets only `outbox/controller/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `controller`
   and `reviewer`, the Codex `notify` hook for `variance-analyst`; the gemini
   agent is covered by pane polling.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents (and drives gemini's pane polling) so one stuck agent can't
   wedge the swarm.

At the end, `up` prints attach and **`serve`** hints. The `serve` line gives you
the mail-app control-plane UI (threads, live panes, send-as-user, availability
toggle). Drop `--host`/`--token` for the safe loopback-only `127.0.0.1` bind —
the UI can start processes, edit config, and type into agents that may run with
elevated permissions, so it must **never** be exposed on `0.0.0.0` without a
token. See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole variance→narrative→review loop route mail with no API keys — the
> mechanics are identical.

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the controller's finished CFO memo as mail
(rather than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/fp-and-a-analyst.yaml
```

This rewrites the `user` contact card in the controller's `outbox/user/about.md`
to `Status: available`, so the controller sees you're reachable. (While away, mail
to you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the ledgers (or a finance question) into the swarm, addressed to the
controller:

```bash
./agentainer send --to controller -c examples/fp-and-a-analyst.yaml \
  "Attached: actuals Q2 (ledger/actuals.csv) and budget/forecast \
   (ledger/budget.csv). Walk me through the variance and give me a \
   CFO-ready read on the half."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the controller, then — because
the inbox was empty — **released into `inbox/`** and the controller is **nudged**
(the protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the finance loop advance one turn at a time.
Each arrow is a `stop → sweep → route → release → nudge` cycle:

1. **controller receives the ledgers.** It reads `inbox/`, asks its one
   clarifying question if scope is ambiguous, then writes a delegation into
   `outbox/variance-analyst/`. On stop, that routes to the variance-analyst.
2. **variance-analyst computes the variance.** It reads its inbox, computes the
   by-account table, and reports back into `outbox/controller/`. On stop, that
   routes to the controller.
3. **controller briefs the writer.** It writes the variance table into
   `outbox/narrative-writer/`. On stop, that routes to the narrative-writer.
4. **narrative-writer drafts the memo.** It reads its inbox, writes the CFO memo,
   and reports back into `outbox/controller/`. On stop, that routes to the
   controller.
5. **controller assembles the draft and routes to the reviewer.** The controller
   writes the combined draft into `outbox/reviewer/`. On stop, that routes to the
   reviewer.
6. **reviewer gates it.** It reads the draft and replies `CLEAR` or `BOUNCE`
   (with specific defects) into `outbox/controller/`. On `BOUNCE`, the controller
   re-delegates the fix and re-routes until the reviewer signs off. On `CLEAR`,
   the controller writes the final memo into `outbox/user/`. On stop, that's
   delivered to your `user` mailbox.
7. **you get the cleared memo** — visible with `agentainer user inbox`, or in the
   UI.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion. If you
never send anything, the agents just sit in standby (the monthly-close ping is
the only thing that self-starts the loop).

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/fp-and-a-analyst.yaml
```

```
swarm: fp-and-a-analyst   root: ./fp-and-a-analyst-workspace
  controller       (claude) up idle queue=0 unread=0 talks=variance-analyst, narrative-writer, reviewer, user
  variance-analyst (codex)  up idle queue=0 unread=1 talks=controller
  narrative-writer (gemini) up idle queue=0 unread=0 talks=controller
  reviewer         (claude) up idle queue=0 unread=0 talks=controller
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/fp-and-a-analyst.yaml          # whole swarm, last 20
./agentainer logs -c examples/fp-and-a-analyst.yaml -f        # follow live
./agentainer logs reviewer -c examples/fp-and-a-analyst.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox controller -c examples/fp-and-a-analyst.yaml
```

Prints the one released message (headers + body), or `controller: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue controller -c examples/fp-and-a-analyst.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach reviewer -c examples/fp-and-a-analyst.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or by sending notes
into an agent's inbox.

- **Send a clarification to the controller.** Realized the period is accrual, not
  cash? `./agentainer send --to controller -c examples/fp-and-a-analyst.yaml
  "Re-brief the variance-analyst: scope is accrual basis, entity = US only."` The
  controller relays the change down the chain and re-routes the draft past the
  reviewer.
- **Ask the reviewer what it bounced.** `./agentainer inbox controller` (or the
  UI) shows the `BOUNCE` note the controller received — which sentence, which
  number, what's wrong — so you can see the gate doing its job.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/fp-and-a-analyst.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/fp-and-a-analyst.yaml     # resume is the default
```

On `up`, Agentainer reads `fp-and-a-analyst-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the
controller and reviewer, `codex resume <id>` for the variance-analyst, and the
gemini session via its recorded id. A resumed agent is *not* re-sent the standby
prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/fp-and-a-analyst.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a dedicated `forecast-analyst`
`examples/forecast-analyst.yaml` ships as a sibling that pushes the forecast
further. To fold it into this swarm, add a fifth agent that the controller can
brief after the variance is in:

```yaml
  - name: forecast-analyst
    type: codex
    can_talk_to: [controller]
    command: "codex --yolo"
    role: |
      You are the FORECAST-ANALYST. Given the cleared variance table, project the
      full-year trajectory (on track / at risk / off plan) with the explicit
      assumptions behind the call. Report ONLY to the controller.
```

Then add `forecast-analyst` to the controller's `can_talk_to` so it can be
briefed, and have the controller fold the forecast call into the reviewer draft.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `variance-analyst: type: claude` (or `hermes`/`gemini`) to put the math on a
  different model than the controller.
- `narrative-writer: type: claude` if you want the memo on Claude while keeping
  gemini out.
- Remember: `gemini`/`hermes` rely on **pane polling** (no completion hook), so
  they don't need (and shouldn't be given) a hook-based `capture`.

### Tune the ACL
- To let the `reviewer` escalate straight to `user` (not only via the controller),
  add `user` to its `can_talk_to`. Mind that this widens the human-facing surface
  and bypasses the controller's single-funnel guarantee — the doc's convention
  keeps the controller the sole `user` contact so the gate always sits in front.
- To make a specialist unreachable from anyone but the controller (already the
  case here), leave its `can_talk_to: [controller]` — that's the one-place-owns-
  the-gate guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md)
  for mixing model families safely.

### Tune the monthly ping
- Change `cron:` to fire on your close calendar (e.g. weekly: `"0 8 * * 1"`).
- Switch `when_busy:` from `skip` to `queue` if you'd rather the close wait
  behind a live query than be dropped. See [`configuration.md`](../configuration.md).

---

## 10. Tips & footguns

- **Keep the controller the only `user`-facing agent.** Only the controller lists
  `user` in `can_talk_to`. That gives you a single funnel: raw variance tables and
  memo drafts always pass through the reviewer's gate before they reach you. If a
  specialist tries to mail `user` directly, the orchestrator bounces it (ACL) and
  drops a `system` note in their inbox explaining who they *can* message — the
  model self-corrects in-band.

- **The reviewer's `BOUNCE` is the feature, not a failure.** A bounced draft
  means the numbers and the story disagreed (a cited figure wasn't in the table,
  or a sign was misread) and the gate caught it. The controller re-delegates and
  re-routes until `CLEAR`. Don't "fix" this by widening ACLs — the loop is how
  the human stays protected.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch (e.g. a `claude` agent whose `command`
  doesn't launch Claude, or a `gemini` agent whose pane never settles) means
  completion never triggers and the agent pins "busy" forever. `status` showing
  an agent `busy` for a long time with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops — relevant if a specialist and the
  controller chatter past the gate.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/fp-and-a-analyst.yaml
  ./agentainer remove-session -c examples/fp-and-a-analyst.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files (the ledgers you dropped in)
  or your config.

- **Availability shapes the ending.** If `user` is **away** when the controller
  finishes, your CFO memo is *held* (with a `system` "the user is away" ack to the
  controller) rather than lost — read it later with `agentainer user inbox` or
  flip yourself available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions (`--dangerously-skip-permissions`,
  `--yolo`).

- **The monthly ping self-starts, but `when_busy: skip` can drop it.** If a live
  query is in flight at 08:00 on the 1st, the close ping is silently skipped
  rather than queued. If you rely on the monthly memo, either keep `user` quiet
  around close, or switch `when_busy` to `queue`.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- `examples/fp-and-a-analyst.yaml` — the config this walkthrough is built on.
- `examples/forecast-analyst.yaml` — a sibling example that pushes the forecast further.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

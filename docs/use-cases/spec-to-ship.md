# Use case: Spec-to-ship pipeline

A concrete, end-to-end walkthrough of the shipped
`examples/spec-to-ship.yaml` swarm — the full **SPEC → BUILD → TEST →
REVIEW → RELEASE-GATE** pipeline as one hub-and-spoke swarm. A single
**shipmaster** takes a vague product idea from you, sequences five specialist
stages in order, and is the ONLY agent that reports back to you. The
**release-gate** is the final authority: it ships only when the spec is tight,
every acceptance criterion has a passing test, and the reviewer approves —
otherwise it bounces the work back with named reasons.

Everything below is based on the actual contents of
`examples/spec-to-ship.yaml` and the shipped CLI (`lib/cli.py`) and mailroom
(`lib/mail.py`). No API keys are needed to understand the mechanics; to run it
*for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md)
> first, then [`mail-model.md`](../mail-model.md) for the four-folders recap.
> The one-line version: an agent **reads a file** to receive mail and **writes a
> file** to send it; the orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Product-minded builders, solo founders, and platform teams who want a
*disciplined* path from a loose idea to a gated, shippable build without
personally doing (or babysitting) every stage. The swarm encodes the engineering
discipline that keeps shipping safe: a single conductor who never writes code
themselves, four specialists who each own one stage, and a release-gate that is
explicitly forbidden from softening a bounce "to avoid friction."

It is deliberately a **hub-and-spoke**, not a free-for-all: every artifact
passes through the shipmaster, and the four specialists only ever report to the
shipmaster. That keeps the artifact chain (spec → build → test report → review
→ verdict) intact and gives you exactly one human-facing surface. It is distinct
from the narrower examples it borrows stages from — `examples/product-spec.yaml`
(just a spec writer), `examples/tdd-pingpong.yaml` (just red/green),
`examples/pr-review-gate.yaml` (just reviewers), and `examples/software-company.yaml`
(a standing org). Here the shipmaster sequences all five stages end-to-end.

---

## 2. The topology

```
          user
            │  (spec / idea)
            ▼
        shipmaster  ◀─────────────────────────────────┐
        (hub)       │                                   │
      ┌─────┼─────┬──────────┐                          │
      ▼     ▼     ▼          ▼                           │
 spec-  builder tester   reviewer   (all report back)  │
 writer                                              release-gate
      │     │     │          │            │            │
      └─────┴─────┴──────────┴────────────┘            │
            (shipmaster fans the artifact forward)      │
            release-gate: APPROVE ──▶ shipmaster ─▶ user│
                           : BOUNCE  ───────────────────┘
```

Six agents, one directed flow:

1. **`user` → `shipmaster`** — you send a vague idea or spec.
2. **`shipmaster` → `spec-writer`** — the idea becomes a tight, testable spec.
3. **`shipmaster` → `builder`** (with the spec) — the implementation.
4. **`shipmaster` → `tester`** (with the spec + build) — a pass/fail test report.
5. **`shipmaster` → `reviewer`** (with spec + build + test report) — a
   quality/risk review of the diff.
6. **`shipmaster` → `release-gate`** (with all of the above) — the verdict.
   **APPROVE** routes back to you via shipmaster; **BOUNCE** carries named
   reasons back to the responsible stage and the affected stages re-run.

The routing above is *enforced* by each agent's `can_talk_to` list. The four
specialists **never** talk to `user` — only `shipmaster` does — and they can't
talk to each other, so the artifact always flows through the hub.

---

## 3. The config, explained

Here is `examples/spec-to-ship.yaml` in full (role bodies trimmed for the
explanatory copy; the shipped file has the unabridged prompts):

```yaml
swarm:
  name: spec-to-ship
  root: ./spec-to-ship-workspace

defaults:
  capture: none              # loader auto-upgrades claude/codex to their hook
  can_talk_to: []           # tightened per agent below

agents:
  - name: shipmaster
    type: claude
    can_talk_to: [spec-writer, builder, tester, reviewer, release-gate, user]
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: |
          Working-hours check: if a build is mid-pipeline ... chase it. If
          nothing is in flight, do nothing and wait for the next idea.
        cron: "0 10-17 * * 1-5"       # top of the hour, 10:00-17:59, Mon-Fri
    role: |
      You are SHIPMASTER, the hub and conductor ... Run the pipeline IN ORDER:
      1. SPEC → spec-writer  2. BUILD → builder  3. TEST → tester
      4. REVIEW → reviewer  5. GATE → release-gate. Never skip a stage. ...

  - name: spec-writer
    type: codex
    can_talk_to: [shipmaster]
    command: "codex --yolo"
    role: |
      You are SPEC-WRITER, stage 1 ... Turn the idea into a tight, testable spec.
      Do NOT write code. Report back to shipmaster.

  - name: builder
    type: claude
    can_talk_to: [shipmaster]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are BUILDER, stage 2 ... Implement the spec; do NOT write the tests.

  - name: tester
    type: codex
    can_talk_to: [shipmaster]
    command: "codex --yolo"
    role: |
      You are TESTER, stage 3 ... Write/rig the tests, run them, report
      PASS/FAIL per criterion. Fix code to make them pass.

  - name: reviewer
    type: gemini
    capture: pane             # gemini detects turns by pane polling
    can_talk_to: [shipmaster]
    command: "gemini --yolo"
    role: |
      You are REVIEWER, stage 4 ... Review the DIFF; give APPROVE /
      CHANGES-REQUESTED with file:line findings. Do NOT edit code.

  - name: release-gate
    type: claude
    can_talk_to: [shipmaster]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are RELEASE-GATE, stage 5 and final authority. APPROVE only when (a)
      spec tight+testable, (b) every criterion has a PASSING test, (c) reviewer
      APPROVE. Otherwise BOUNCE with named reasons + the stage to fix.
```

Field by field:

### `swarm`
- **`name: spec-to-ship`** — the swarm's name (shows up in `status`, logs, sessions).
- **`root: ./spec-to-ship-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent gets its *own* workdir
  (`spec-to-ship-workspace/shipmaster`, `.../spec-writer`, `.../builder`,
  `.../tester`, `.../reviewer`, `.../release-gate`) — **no shared workdir here**,
  so no mailbox namespacing is needed. Orchestrator state goes under
  `spec-to-ship-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless an agent overrides them.
- **`capture: none`** — the loader auto-upgrades `claude`/`codex` to their
  natural hook (see turn detection below), so this just says "don't force a
  global override." `gemini`/`hermes` have no hook and must set `capture: pane`.
- **`can_talk_to: []`** — the default ACL is "talk to no one." Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `shipmaster` (type: `claude`)
- **`can_talk_to: [spec-writer, builder, tester, reviewer, release-gate, user]`**
  — the hub: it delegates to all four specialists and is the **only agent that
  can talk to `user`**. The gate's verdict is delivered to you *through*
  shipmaster, never directly.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command (substitute your own alias; treat command strings as sensitive — they
  may embed keys).
- **`role`** — the standing conductor identity. On `up` this becomes the first
  prompt, wrapped in a **standby notice**, so shipmaster waits for your idea
  instead of proactively mailing peers.
- **`pings`** — a single **working-hours nudge**: `cron: "0 10-17 * * 1-5"`
  fires at the top of every hour, 10:00–17:59, Monday–Friday. Its message tells
  a mid-pipeline shipmaster which stage is outstanding and to chase it; if
  nothing is in flight it does nothing. Because `when_busy` defaults to `skip`,
  the nudge is *dropped* if shipmaster is mid-turn — so it never interrupts an
  active stage hand-off.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `spec-writer` (type: `codex`)
- **`can_talk_to: [shipmaster]`** — reports the finished spec only to shipmaster;
  cannot reach the other specialists or `user`.
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `builder` (type: `claude`)
- **`can_talk_to: [shipmaster]`** — sends the implementation only to shipmaster.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder.
- **Turn detection:** `claude` → Stop hook.

### `tester` (type: `codex`)
- **`can_talk_to: [shipmaster]`** — sends the test report only to shipmaster.
- **`command: "codex --yolo"`** — placeholder.
- **Turn detection:** `codex` → `notify` hook.

### `reviewer` (type: `gemini`, `capture: pane`)
- **`can_talk_to: [shipmaster]`** — sends the review only to shipmaster.
- **`command: "gemini --yolo"`** — placeholder.
- **`capture: pane`** — required because Gemini has no completion hook; the
  orchestrator detects turn-end by **polling the tmux pane** instead.
- **Turn detection:** `gemini` → pane polling.

### `release-gate` (type: `claude`)
- **`can_talk_to: [shipmaster]`** — the verdict goes only to shipmaster, which
  fans an APPROVE out to you or a BOUNCE back to the responsible stage.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder.
- **Turn detection:** `claude` → Stop hook.

### ACL enforcement (how the spokes stay isolated)

The `can_talk_to` lists are the whole routing contract. When a specialist tries
to deliver to anyone not on its list — say `builder` writing into
`outbox/release-gate/` — the orchestrator **bounces** it as a `system` message
filed in `failed/`, and drops an in-band note in the sender's inbox naming the
recipients it *is* allowed to message, so the model self-corrects. The four
specialists each list only `[shipmaster]`, so cross-stage chatter is impossible
by construction; only `shipmaster` holds the full fan-out. (The ACL is
cooperative, not OS isolation — agents with filesystem access *could* write
straight into another inbox, but well-behaved agents go through `outbox/`. See
[`mail-model.md`](../mail-model.md).)

### What's *not* in this config
- **No shared `workdir`.** Unlike a shared-repo pipeline, every agent is
  private; shipmaster is responsible for carrying the artifact forward in each
  hand-off message, so the "same truth" is maintained by mail, not by a shared
  directory.
- **Only one `pings`.** The working-hours nudge on `shipmaster` is the only
  timer; the pipeline otherwise moves purely on real mail. (Add a ping to a
  specialist if you want a per-stage nag.)
- **No `user` availability in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §5).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/spec-to-ship.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings.
2. Creates the runtime dirs (`spec-to-ship-workspace/.agentainer/…`: log, queue,
   run, sessions).
3. **Initializes the mailboxes** — for each of the six agents, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/about.md`
   contact card *is* the ACL made visible: `shipmaster` gets `outbox/spec-writer/`,
   `outbox/builder/`, `outbox/tester/`, `outbox/reviewer/`, `outbox/release-gate/`,
   `outbox/user/`; each specialist gets only `outbox/shipmaster/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `shipmaster`,
   `builder`, and `release-gate`; the Codex `notify` hook for `spec-writer` and
   `tester`; pane polling is armed for `reviewer`.
5. **Opens one tmux session per agent**, `cd`'d into its own workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the pipeline.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'spec-to-ship' is up with 6 agent(s)
:: attach with:  tmux attach -t <shipmaster-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/spec-to-ship.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents that may run elevated, so it must **never** be exposed on
`0.0.0.0` without a token. See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the full five-stage pipeline route mail with no API keys — the mechanics are
> identical.

---

## 5. Drive a spec

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the shipmaster's shippable result as mail
(rather than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/spec-to-ship.yaml
```

This rewrites the `user` contact card in shipmaster's `outbox/user/about.md` to
`Status: available`. (While away, mail to you is *held* and the sender gets a
`system` ack — nothing bounces.)

Now send the idea into the swarm, addressed to the hub:

```bash
./agentainer send --to shipmaster -c examples/spec-to-ship.yaml \
  "Idea: a CLI that turns meeting notes into a calendar — extracts events, \
   attendees, and times, and writes them to an .ics file."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped
`From: user` with a fresh id, enqueued for shipmaster, then — because the inbox
was empty — **released into `inbox/`** and shipmaster is **nudged** (the
protocol is re-pasted, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **shipmaster sequences SPEC.** It reads `inbox/`, writes the idea into
   `outbox/spec-writer/`. On stop, that routes to spec-writer.
2. **spec-writer returns a tight spec.** It writes it into `outbox/shipmaster/`.
   On stop, that routes back to shipmaster.
3. **shipmaster delegates BUILD → builder.** It forwards the spec into
   `outbox/builder/`; the build returns to shipmaster on stop.
4. **shipmaster delegates TEST → tester** (spec + build). The test report returns
   to shipmaster on stop.
5. **shipmaster delegates REVIEW → reviewer** (spec + build + test report). The
   review returns to shipmaster on stop.
6. **shipmaster delegates GATE → release-gate** (everything). On **APPROVE**,
   shipmaster writes the shippable result into `outbox/user/`; on **BOUNCE**, it
   forwards the named reasons to the responsible stage(s), re-runs them, and
   re-submits to release-gate until it ships.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send an idea, the agents just sit in standby. The only timer is
> shipmaster's working-hours ping, which only *chases* an in-flight build — it
> never invents work on its own.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/spec-to-ship.yaml
```

```
swarm: spec-to-ship   root: ./spec-to-ship-workspace
  shipmaster   (claude) up idle queue=0 unread=0 talks=spec-writer, builder, tester, reviewer, release-gate, user
  spec-writer  (codex)  up idle queue=0 unread=1 talks=shipmaster
  builder      (claude) up idle queue=0 unread=0 talks=shipmaster
  tester       (codex)  up idle queue=0 unread=0 talks=shipmaster
  reviewer     (gemini) up idle queue=0 unread=0 talks=shipmaster
  release-gate (claude) up idle queue=0 unread=0 talks=shipmaster
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct the five-stage run):

```bash
./agentainer logs -c examples/spec-to-ship.yaml          # whole swarm, last 20
./agentainer logs -c examples/spec-to-ship.yaml -f        # follow live
./agentainer logs release-gate -c examples/spec-to-ship.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
plus `ping` events for shipmaster's working-hours nudge, one JSONL line each.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox shipmaster -c examples/spec-to-ship.yaml
```

Prints the one released message (headers + body), or `shipmaster: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue shipmaster -c examples/spec-to-ship.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach release-gate -c examples/spec-to-ship.yaml
```

Detach with the usual tmux `Ctrl-b d`. Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.

---

## 7. Iterate on the result

The first pass rarely nails it — especially if release-gate bounces it. Because
every message is natural-language mail, you can steer the swarm mid-flight
through the `user` mailbox, and a bounce already routes corrections back to the
right stage automatically.

- **Clarify the spec.** Realized the calendar CLI should dedupe by event UID?
  `./agentainer send --to shipmaster -c examples/spec-to-ship.yaml "Require a
  stable event UID so re-imports don't duplicate; re-brief spec-writer."` The
  shipmaster relays the change down the chain and re-runs the affected stages.
- **React to a BOUNCE.** When release-gate bounces (e.g. "criterion 3 has no
  passing test, builder must fix"), the shipmaster forwards that to `tester`
  and/or `builder`; you just watch the re-run from `logs -f`.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send
  as `user`, toggle `user` availability, and watch panes live — useful when you
  want to nudge a specific stage without guessing its name.

When you're happy with the shipped build (or want to try a different framing),
tear it down:

```bash
./agentainer down -c examples/spec-to-ship.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/spec-to-ship.yaml     # resume is the default
```

On `up`, Agentainer reads `spec-to-ship-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for
`shipmaster`/`builder`/`release-gate`, `codex resume <id>` for `spec-writer`/
`tester`, and the pane-polled `gemini` session for `reviewer`. A resumed agent
is *not* re-sent the standby prompt (its prior context is restored) — so a
half-finished pipeline picks up where it left off.

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/spec-to-ship.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a parallel security reviewer
If you want a dedicated security pass before the gate, add a seventh agent and
give `shipmaster` one more spoke:

```yaml
  - name: sec-reviewer
    type: gemini
    capture: pane
    can_talk_to: [shipmaster]
    command: "gemini --yolo"
    role: |
      You are the SECURITY REVIEWER. shipmaster hands you the spec + build.
      Review for input/data hazards, authz, and secrets handling; return
      APPROVE / CHANGES-REQUESTED. Do NOT edit code.
```

Then add `sec-reviewer` to shipmaster's `can_talk_to`. The gate's `(c)` condition
can be extended to require its approval too.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- Put `spec-writer` on `claude` (or `hermes`) to vary the model from the builder.
- Put `builder` on `codex` if you'd rather implement with Codex while the spec
  stays on Claude.
- Remember: `gemini`/`hermes` need `capture: pane` (pane polling) since they
  have no completion hook.

### Tune the ACL
- To let `release-gate` escalate straight to `user` (not only via shipmaster),
  add `user` to its `can_talk_to`. Mind that this widens the human-facing surface;
  the doc's convention keeps shipmaster the sole `user` contact so the gate's
  verdict is always framed by the conductor.
- To make a stage reachable from another specialist (e.g. `tester` → `builder`
  for a tight fix loop), add the name — but note this breaks the pure hub-and-
  spoke and is *not* what this config does by default.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md)
  for mixing model families safely.

### Tune the ping
shipmaster's `cron: "0 10-17 * * 1-5"` is top-of-hour on weekdays. Change it to
`"*/30 9-20 * * *"` for a half-hourly, 09:00–20:59 check, or drop the `pings:`
block entirely if you want a strictly event-driven swarm. Add `when_busy: fire`
if you'd rather the nudge interrupt an active hand-off (not recommended — it can
split a stage message).

---

## 10. Tips & footguns

- **Keep shipmaster the only `user`-facing agent.** Only shipmaster lists `user`
  in `can_talk_to`. That gives you one funnel: raw drafts, test verdicts, and
  bounce reasons all pass through the conductor before they reach you. If a
  specialist tries to mail `user` directly, the orchestrator bounces it (ACL) and
  drops a `system` note in their inbox naming who they *can* message — the model
  self-corrects in-band.

- **The artifact chain is mail, not a shared dir.** Unlike a shared-repo
  pipeline, each agent has its own workdir and there's no namespacing to worry
  about — but that means shipmaster must *carry* the latest spec + build + test
  report + review in every hand-off. If a stage seems to re-ask for context that
  was already produced, check `shipmaster`'s outbox writes include the full chain
  (its role instructs exactly this).

- **Watch the stop → nudge loop.** The whole clock runs on turn completion.
  `claude`/`codex` fire via hooks; `reviewer` is `gemini` and depends on **pane
  polling** — if reviewer seems stuck `busy` with `unread` mail, suspect its
  pane-detection (it has no hook, so a misconfigured `capture` is the usual
  culprit). A `type`/`command` mismatch on any agent means completion never
  triggers and it pins "busy" forever.

- **The gate won't soften.** release-gate's role forbids it from bouncing-softly
  "to avoid friction." If it keeps bouncing, that's the system working — read the
  named reasons in its mail and let shipmaster re-run the named stage. Don't
  paper over a real failing test by editing the gate's prompt.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is best-effort, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived
  so the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s)
  to kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/spec-to-ship.yaml
  ./agentainer remove-session -c examples/spec-to-ship.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches your config.

- **Availability shapes the ending.** If `user` is **away** when shipmaster
  finishes, your shippable result is *held* (with a `system` "the user is away"
  ack to shipmaster) rather than lost — read it later with
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
- [`pr-review-gate.md`](./pr-review-gate.md) — the narrower reviewers-only swarm.
- [`product-spec.md`](./product-spec.md) — the narrower spec-writer-only swarm.
- `examples/spec-to-ship.yaml` — the config this walkthrough is built on.
- `examples/tdd-pingpong.yaml`, `examples/software-company.yaml` — related
  single-stage and standing-org swarms.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

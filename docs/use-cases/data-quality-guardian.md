# Use case: Data-quality guardian

A concrete, end-to-end walkthrough of the shipped
`examples/data-quality-guardian.yaml` swarm — a self-driving data-quality
monitor that wakes on a schedule, runs validation checks over your watched
datasets, and escalates to the human **only** when something genuinely fails.
A **guardian hub** owns the clock and the human-facing surface, a **profiler**
runs the validation battery on a schedule, and an **alerter** drafts the
incident for you only on confirmed failures. Routine "all green" runs stay
silent.

Everything below is based on the actual contents of
`examples/data-quality-guardian.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics;
to run it *for real* you supply the coding-CLI commands (or swap them for mock
bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md)
> first, then the four-folders recap in the repo `README.md`. The one-line
> version: an agent **reads a file** to receive mail and **writes a file** to
> send it; the orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Data engineers, analytics engineers, and platform teams who already own
datasets/pipelines and want a standing watchdog that doesn't need a human to
press "go" every time. The swarm encodes the discipline that makes monitoring
bearable — a single hub that owns the clock and the human surface, a profiler
that only reports findings (never severity), and an alerter that only drafts
when handed a real failure — so you get paged on signal and left alone on
silence.

It is deliberately a **hub-and-spoke with a timer**: the `pings:` rules own the
clock, so you `up` the swarm, flip yourself available, and walk away. Unlike
the [data-pipeline-builder](./data-pipeline-builder.md) assembly line, this
swarm needs **no human `send`** to do its job — the schedules are the driver.

---

## 2. The topology

```
          profiler ───┐
                      ├── guardian ── user   (posts ONLY real failures)
          alerter  ───┘
```

Three agents, one directed flow:

1. **`pings:` → `guardian`** — a business-hours sweep (`skip` so ticks don't
   stack) and an overnight deep run (`queue` so it's never dropped) nudge the
   guardian to start a sweep.
2. **`guardian` → `profiler`** — the guardian asks the profiler to run the
   validation battery (schema conformance, freshness, null-rate,
   distribution-shift, referential integrity).
3. **`profiler` → `guardian`** — the profiler reports per dataset `PASS` or the
   exact failing rule with numbers. It never talks to `user`.
4. **`guardian` → `alerter`** (on a real failure) — the guardian hands the exact
   finding to the alerter and asks for a drafted incident.
5. **`alerter` → `guardian`** — the alerter returns a tight incident (what broke,
   blast radius, when, one recommended next step). It never talks to `user`.
6. **`guardian` → `user`** — only if a genuine failure exists, the guardian
   escalates the incident. On green runs it **stays silent**.

The routing above is *enforced* by each agent's `can_talk_to` list. The
`profiler` and `alerter` **never** reach `user` — only the `guardian` does —
so the human-facing surface is exactly one agent. Anything addressed outside an
agent's list is bounced back as a `system` message and filed in `failed/`.

---

## 3. The config, explained

Here is `examples/data-quality-guardian.yaml` in full:

```yaml
swarm:
  name: data-quality-guardian
  root: ./data-quality-guardian-workspace

defaults:
  capture: none              # claude/codex are auto-upgraded to their hook at up
  can_talk_to: []            # tightened per agent below

agents:
  - name: guardian
    type: claude
    can_talk_to: [profiler, alerter, user]
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: |
          Data-quality sweep. If you are idle, ask profiler to run the validation
          battery over the watched datasets ... If profiler reports a genuine
          failure, hand it to alerter to draft the incident for user. If
          everything is green, STAY SILENT -- do not message the user ...
        cron: "*/30 9-17 * * 1-5"        # every 30 min, 09:00-17:30, Mon-Fri
      - message: |
          Overnight deep data-quality run. Ask profiler to execute the full
          validation battery ... Page user ONLY if something genuinely fails ...
        cron: "0 3 * * *"               # 03:00 every day
        when_busy: queue
    role: |
      You are the GUARDIAN of a self-driving data-quality monitor. You own the
      clock and you are the ONLY agent that talks to the human (user) ...

  - name: profiler
    type: codex
    can_talk_to: [guardian]
    command: "codex --yolo"
    pings:
      - message: |
          Nightly deep validation: execute the full battery the daytime sweeps
          skip -- distribution-shift detection ..., referential integrity ...,
          extended freshness window. Report concretely to guardian ...
        cron: "30 2 * * *"              # 02:30 every day
        when_busy: queue
    role: |
      You are the PROFILER. When the guardian asks for a validation run (or the
      nightly ping fires), execute the data-quality battery ...

  - name: alerter
    type: claude
    can_talk_to: [guardian]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the ALERTER. When the guardian hands you a confirmed failure from
      the profiler, draft a tight incident for the user ...
```

Field by field:

### `swarm`
- **`name: data-quality-guardian`** — the swarm's name (shows up in `status`,
  logs, sessions).
- **`root: ./data-quality-guardian-workspace`** — the parent directory for the
  agents' working directories and mailboxes. Each agent's workdir defaults to
  `data-quality-guardian-workspace/<name>`; orchestrator state goes under
  `data-quality-guardian-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.
- **`capture: none`** — a placeholder; the loader auto-upgrades `claude`/
  `codex` agents to their natural capture mode at `up` (you saw the `--auto-
  upgraded` warnings during `validate`). It's left in to document intent and to
  keep the file key-free-friendly.

### `guardian` (type: `claude`)
- **`can_talk_to: [profiler, alerter, user]`** — the guardian is the hub and the
  **only agent that can talk to `user`**. The `profiler`/`alerter` can only
  reach the guardian, so every escalation passes through one filter.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command, e.g. a
  shell alias. Treat command strings as sensitive; they may embed keys.)
- **`pings`** — two scheduled nudges (see below). This is the clock.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at
  `up`).

### `profiler` (type: `codex`)
- **`can_talk_to: [guardian]`** — the profiler only reports back to the
  guardian. It cannot reach `alerter` or `user`; findings flow
  guardian → alerter, never profiler → alerter directly.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`pings`** — its own nightly deep-validation tick (below). The profiler
  carries the heaviest check on a different cadence than the guardian's sweeps.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `alerter` (type: `claude`)
- **`can_talk_to: [guardian]`** — the alerter only reports back to the guardian;
  it never reaches `user` and has **no `pings`** (it is purely reactive, only
  working when the guardian hands it a confirmed failure).
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook.

### The `pings:` cron clock (the whole point)

Each `pings` entry is a `message` + a `cron` expression (+ optional
`when_busy`). `cron` is standard 5-field `minute hour day-of-month month
day-of-week`, evaluated in the **host's local time**. Three rules are live here:

1. **`guardian` business-hours sweep** — `*/30 9-17 * * 1-5` (every 30 minutes,
   09:00–17:30, Mon–Fri). `when_busy` is omitted, so it defaults to **`skip`**:
   if the guardian is still writing up a failure when the next half-hour comes
   due, that tick is **dropped** — only the freshest sweep matters, and routine
   green checks stay silent either way.
2. **`guardian` overnight deep run** — `0 3 * * *` (03:00 daily).
   `when_busy: queue`: the nightly deep battery is heavy and each run matters, so
   a tick that arrives mid-incident **waits its turn** rather than being dropped.
3. **`profiler` nightly deep validation** — `30 2 * * *` (02:30 daily).
   `when_busy: queue`: a deep run that comes due while the profiler is still
   executing a prior one **queues** behind it.

Two footguns the YAML's own header flags: an **overnight window is a comma
list in the hour field** (`18-23,0-7`), never a descending range; and
`when_busy: skip` drops a mid-turn tick whereas `queue` waits for it. See
[`configuration.md`](../configuration.md) for the full cron/pings grammar.

### What's *not* in this config
- **No shared `workdir`.** All three agents get private dirs
  (`guardian/`, `profiler/`, `alerter/`), so there is no mailbox namespacing —
  this is simpler than the pipeline builder's shared repo.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — escalations addressed to you are *held* (never bounced) until you
  flip it on (see §5).
- **`alerter` has no `pings`.** It is purely reactive; incident drafting stays
  tied to real profiler findings, never to routine green checks.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/data-quality-guardian.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the `capture: none` auto-upgrade
   warnings for `guardian`/`profiler`/`alerter`.
2. Creates the runtime dirs
   (`data-quality-guardian-workspace/.agentainer/…`: log, queue, run, sessions).
3. **Initializes the mailboxes** — the five folders `inbox/ outbox/ read/
   sent/ failed/` for each agent, plus an `outbox/<peer>/` folder **for each
   allowed recipient**. The `outbox/<peer>/about.md` contact card *is* the ACL
   made visible: the `guardian` gets `outbox/profiler/`, `outbox/alerter/`,
   `outbox/user/`; the `profiler` gets only `outbox/guardian/`; the `alerter`
   gets only `outbox/guardian/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `guardian`
   and `alerter`, and the Codex `notify` hook for `profiler`.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles
   stale/dead/silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'data-quality-guardian' is up with 3 agent(s)
:: attach with:  tmux attach -t <guardian-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/data-quality-guardian.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents, so it must **never** be exposed on `0.0.0.0` without a token.
See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole clock fire and route mail with no API keys — the mechanics are
> identical, including the cron pings.

---

## 5. Drive it (or just let it run)

This swarm is **self-driving** — the `pings:` rules own the clock, so you don't
have to `send` anything for it to do its job. But two human actions matter:

**Make yourself reachable for escalations.** The `user` mailbox defaults to
**away**, which means a drafted incident addressed to you is *held* (never
lost) until you flip it on:

```bash
./agentainer user available -c examples/data-quality-guardian.yaml
```

This rewrites `user`'s contact card in the `guardian`'s `outbox/user/about.md`
to `Status: available`. While away, incident mail to you is *held* with a
`system` ack — nothing bounces. For a watchdog you intend to leave running, set
this once after `up`.

**Send a manual override (optional).** If you want to trigger an off-schedule
sweep or change what "watched datasets" means, send to the hub:

```bash
./agentainer send --to guardian -c examples/data-quality-guardian.yaml \
  "Run an immediate full validation battery and page me with anything that \
   fails — I just shipped a schema change to the orders table."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped
`From: user` with a fresh id, enqueued for the `guardian`, then — because its
inbox was empty — **released into `inbox/`** and the guardian is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing (after a ping fires)

1. **A `pings:` tick nudges the guardian.** It reads `inbox/`, asks `profiler`
   to run the battery (writing into `outbox/profiler/`), and stops.
2. **profiler runs the checks.** It reads its inbox, executes the battery, and
   reports `PASS` or the exact failing rule with numbers into
   `outbox/guardian/`. On stop, that routes to the guardian.
3. **guardian decides.** Green → **stays silent** (no mail to `user`). Real
   failure → writes the finding into `outbox/alerter/` asking for an incident.
4. **alerter drafts.** It reads its inbox, drafts the incident, and reports back
   into `outbox/guardian/`. On stop, that routes to the guardian.
5. **guardian escalates.** It writes the incident into `outbox/user/`. On stop,
   that's delivered to your `user` mailbox (visible with `agentainer user inbox`
   or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires each hop off turn completion.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/data-quality-guardian.yaml
```

```
swarm: data-quality-guardian   root: ./data-quality-guardian-workspace
  guardian (claude) up idle queue=0 unread=1 talks=profiler, alerter, user
  profiler (codex)  up idle queue=0 unread=0 talks=guardian
  alerter  (claude) up idle queue=0 unread=0 talks=guardian
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/data-quality-guardian.yaml          # whole swarm, last 20
./agentainer logs -c examples/data-quality-guardian.yaml -f        # follow live
./agentainer logs profiler -c examples/data-quality-guardian.yaml # just one agent
```

You'll see `ping`, `delivered`, `route`, `read`, `read-receipt`, `bounce`, etc.
— one JSONL line per event. A `ping` line is your proof the clock fired; a
`bounce` would mean an agent tried to mail outside its ACL.

**A specific inbox** — what an agent is currently looking at:

```bash
./agentainer inbox guardian -c examples/data-quality-guardian.yaml
```

Prints the one released message (the ping or a routing result), or `guardian:
inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue guardian -c examples/data-quality-guardian.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux
session:

```bash
./agentainer attach guardian -c examples/data-quality-guardian.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the
mailroom — handy for un-sticking an agent, but the mail model is the normal
path.)

**The user mailbox** — read escalations that landed while you were available:

```bash
./agentainer user inbox -c examples/data-quality-guardian.yaml
```

---

## 7. Iterate on the result

Because every message is natural-language mail, you can steer the swarm mid-
flight through the `user` mailbox or by sending notes into an agent's inbox.

- **Send a clarification to the guardian.** "Treat `null-rate` on the `events`
  table as a warning, not a failure." The guardian relays the change down to the
  profiler on the next sweep.
- **Ask the alerter for more detail.** `./agentainer send --to guardian -c
  examples/data-quality-guardian.yaml "When the alerter drafts, always include
  the last-good-check timestamp."` — the guardian forwards it.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send
  as `user`, toggle `user` availability, and watch panes live — useful when you
  want to nudge a specific agent without guessing its name.

When you're done (or want to stop the clock), tear it down:

```bash
./agentainer down -c examples/data-quality-guardian.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/data-quality-guardian.yaml     # resume is the default
```

On `up`, Agentainer reads `data-quality-guardian-workspace/.agentainer/
sessions.yaml` (written as each agent finished its first turn) and reattaches
the recorded conversations via each type's native resume: `claude --resume <id>`
for `guardian`/`alerter`, `codex resume <id>` for `profiler`. A resumed agent is
*not* re-sent the standby prompt (its prior context is restored), and the
`pings:` schedules resume immediately.

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/data-quality-guardian.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Change what gets watched / checked
The "watched datasets" and the five checks (schema conformance, freshness,
null-rate, distribution-shift, referential integrity) live in the `profiler`'s
`role` text. Edit that block to add/remove checks or point at different tables;
the guardian and alerter roles are agnostic to the check list. Remember the
`profiler` carries its *own* nightly deep-validation `pings` message — keep that
in sync if you change the battery.

### Change the cadence
Edit the `cron` fields. To make the business-hours sweep fire hourly instead of
every 30 minutes, change `*/30 9-17 * * 1-5` to `0 9-17 * * 1-5`. To add a
weekend sweep, add a second `pings` entry to the `guardian`. Keep overnight
windows as a **comma list** in the hour field, never a descending range, and
remember `when_busy: skip` drops a mid-turn tick while `queue` waits for it.

### Add a `monitoring`/`dashboard` agent
If you want a fourth agent that also consumes guardian findings (e.g. to push to
Slack rather than to `user`), add it with `can_talk_to: [guardian]` and add its
name to the guardian's `can_talk_to`. The guardian stays the sole `user`
contact unless you deliberately widen that.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command`
mismatch wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `profiler: type: claude` to run validation on Claude instead of Codex.
- `alerter: type: codex` (`codex --yolo`) if you want incident drafting on Codex
  — its turn detection becomes the `notify` hook.
- Remember: `gemini`/`hermes` need `capture: pane` (pane polling) since they
  have no completion hook; for those you'd set `capture: pane` explicitly
  instead of letting `none` auto-upgrade.

### Tune the ACL
- To let the `alerter` escalate straight to `user` (not only via the guardian),
  add `user` to its `can_talk_to`. Mind that this widens the human-facing
  surface; the doc's convention keeps the guardian the sole `user` contact.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md)
  for mixing model families safely.

---

## 10. Tips & footguns

- **Keep the guardian the only `user`-facing agent.** Only the guardian lists
  `user` in `can_talk_to`. That gives you a single funnel: raw profiler findings
  are filtered by the guardian before they reach you, and incidents are always
  drafted by the alerter. If the `profiler` or `alerter` tried to mail `user`
  directly, the orchestrator bounces it (ACL) and drops a `system` note in their
  inbox explaining who they *can* message — the model self-corrects in-band.

- **Silence is the feature, not a bug.** On green runs the guardian is told to
  *stay silent* — no mail to `user`. If you're not seeing escalations, first
  confirm via `logs -f` that a `ping` actually fired and a `profiler` `FAIL`
  was reported; don't assume the swarm is dead just because your inbox is quiet.

- **`when_busy` is the difference between "dropped" and "waited".** The
  business-hours sweep uses `skip` (default) so overlapping ticks don't stack —
  on a busy guardian the freshest sweep wins. The overnight and nightly deep runs
  use `queue` so a heavy run is never skipped. If you accidentally set `skip` on
  the 03:00 deep run, a guardian mid-incident would silently miss its nightly
  battery.

- **Overnight windows are comma lists, not ranges.** `18-23,0-7` is correct;
  `23-7` is a descending range and won't do what you expect. The YAML header
  calls this out explicitly.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch (e.g. a `claude` agent whose `command`
  doesn't launch Claude) means completion never triggers and the agent pins
  "busy" forever. `status` showing an agent `busy` for a long time with `unread`
  mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge
  the swarm: mail moved to `read/` is just a best-effort receipt, and a message
  shown `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-
  archived so the queue advances. There's also a per-pair runaway cap (≤20
  messages / 60s) to kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/data-quality-guardian.yaml
  ./agentainer remove-session -c examples/data-quality-guardian.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches your watched datasets or your config.

- **Availability shapes the ending.** If `user` is **away** when the guardian
  escalates, your incident is *held* (with a `system` "the user is away" ack to
  the guardian) rather than lost — read it later with `agentainer user inbox` or
  flip yourself available and it's delivered. For a watchdog you leave running,
  set `user available` once after `up`.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into
  agents that may run with elevated permissions.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`configuration.md`](../configuration.md) — the full `pings:`/`cron` grammar.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- [`cli-reference.md`](../cli-reference.md) — every subcommand, including `logs -f` and `user inbox`.
- `examples/data-quality-guardian.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14; cron/pings coverage).

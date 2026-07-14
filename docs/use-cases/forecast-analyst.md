# Use case: Forecast analyst

A concrete, end-to-end walkthrough of the shipped
`examples/forecast-analyst.yaml` swarm — a time-series forecasting assembly line
that turns a messy series + horizon + business context into a defensible
forecast. A **forecaster hub** takes the request and coordinates three
specialists: **prep** cleans the series and picks a baseline, **modeler** fits
point **and** interval forecasts, and **reviewer** is a sanity gate that must
clear every number before it reaches you. The forecaster delivers the signed-off
forecast back.

Everything below is based on the actual contents of
`examples/forecast-analyst.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`../getting-started.md`](../getting-started.md)
> first, then the four-folders recap in the repo `README.md`. The one-line
> version: an agent **reads a file** to receive mail and **writes a file** to
> send it; the orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Analysts, demand/inventory planners, RevOps and finance teams, and anyone who
needs a forecast they can actually defend in a meeting — not a single magic
number from one model. The swarm encodes the discipline that makes forecasts
trustworthy: the data is cleaned by someone who isn't also fitting the model,
the winning model has to beat a baseline, the output always ships with
intervals, and an independent reviewer signs off on plausibility and caveats
before the human ever sees it.

It is deliberately a **hub-and-spoke**, not a free-for-all: every request and
every deliverable passes through the forecaster, so the human-facing surface has
exactly one owner and the sanity gate is structurally unavoidable. Swapping in a
different model for any spoke is a one-`type` change.

---

## 2. The topology

```
          user
            |
        forecaster                  (the hub: talks to prep, modeler, reviewer, user)
         /    |    \
      prep  modeler  reviewer       (spokes: report ONLY to forecaster)
```

Four agents, one gated flow:

1. **`user` → `forecaster`** — you send the series + horizon + business context.
2. **`forecaster` → `prep`** — the forecaster hands over the raw series and asks
   for a cleaned series, the detected seasonality/trend/stationarity, and a
   recommended baseline to judge the modeler against.
3. **`prep` → `forecaster`** — the prep returns the cleaned diagnostics + baseline.
4. **`forecaster` → `modeler`** (with prep's cleaned output + baseline) — fit one
   or more models and return point **and** interval forecasts over the horizon.
5. **`modeler` → `forecaster`** — the modeler returns the forecasts; the forecaster
   assembles them with prep's baseline into one draft.
6. **`forecaster` → `reviewer`** — the assembled draft is routed to the sanity
   gate. The reviewer either replies **CLEAR** or **BOUNCE** (with defects).
7. **`reviewer` → `forecaster`** (only) — on BOUNCE, the forecaster re-delegates
   to prep/modeler and re-routes until the reviewer clears it.
8. **`forecaster` → `user`** — only after the reviewer clears it does the final
   forecast (point + interval + winning model + caveats) reach you.

The routing above is *enforced* by each agent's `can_talk_to` list. Notably,
`prep`, `modeler`, and `reviewer` **never** talk to `user` directly — only the
forecaster does — and the reviewer is the last word on whether a number is safe
to ship.

---

## 3. The config, explained

Here is `examples/forecast-analyst.yaml` in full:

```yaml
swarm:
  name: forecast-analyst
  root: ./forecast-analyst-workspace

defaults:
  capture: none              # claude/codex are auto-upgraded to their hook at up
  can_talk_to: []            # tightened per agent below

agents:
  - name: forecaster
    type: claude
    can_talk_to: [prep, modeler, reviewer, user]
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: |
          Daily forecast refresh. If the user originally asked for a rolling
          horizon, pull the latest data window and re-run the PREP -> MODELER ->
          REVIEWER loop, bumping the horizon forward by one period. Otherwise,
          just note whether the assumptions (trend, seasonality, the loyalty-
          program level shift) still hold, and post any material change to user.
        cron: "0 7 * * *"             # 07:00 every day
    role: |
      You are the FORECASTER and the only agent who talks to the human (user)...
      (1) send series to PREP; (2) send cleaned output to MODELER for point +
      interval forecasts; (3) assemble + route to REVIEWER (the SANITY GATE --
      it MUST clear the forecast before you post to user; re-route on BOUNCE);
      (4) only then write the final forecast to outbox/user/.

  - name: prep
    type: codex
    can_talk_to: [forecaster]
    command: "codex --yolo"
    role: |
      You are the PREP analyst. Clean the series, detect seasonality/trend/
      stationarity, flag level shifts, recommend a baseline (with in-sample fit)
      for MODELER to beat. Report ONLY to forecaster.

  - name: modeler
    type: gemini
    can_talk_to: [forecaster]
    command: "gemini --yolo"
    role: |
      You are the MODELER. Fit >=2 model families and return POINT forecasts AND
      interval forecasts (explicit coverage, e.g. 80/95%) over the horizon,
      noting which beat PREP's baseline and why. Point-only forecasts are not
      shippable. Report ONLY to forecaster.

  - name: reviewer
    type: claude
    can_talk_to: [forecaster]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the REVIEWER -- the SANITY GATE. Check for impossible values, a
      plausible trend, sane intervals (not widened to hide a bad point), and
      caveats stated BEFORE user sees anything. Reply CLEAR or BOUNCE. The human
      must NEVER see a forecast you have not signed off. Report ONLY to forecaster.
```

Field by field:

### `swarm`
- **`name: forecast-analyst`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./forecast-analyst-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent gets its own default workdir
  (`forecast-analyst-workspace/forecaster`, `/prep`, `/modeler`, `/reviewer`), so
  **there is no shared workdir here** (contrast with `data-pipeline-builder`,
  where two agents share a repo). Orchestrator state goes under
  `forecast-analyst-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the floor is "no turn-completion capture." `up` then
  auto-upgrades any agent whose type has a completion hook (see per-agent notes).
- **`can_talk_to: []`** — the default ACL is "talk to no one." Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `forecaster` (type: `claude`)
- **`can_talk_to: [prep, modeler, reviewer, user]`** — the forecaster is the hub:
  it delegates to the three specialists and is the **only agent that can talk to
  `user`**. Keep the human-facing surface to a single agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`pings`** — a single daily ping at `cron: "0 7 * * *"` (07:00 local). When it
  fires, the forecaster is asked to re-run the PREP→MODELER→REVIEWER loop (rolling
  horizon) or just re-check the assumptions. `when_busy` defaults to
  `"skip"`, so a tick that lands mid-turn is **dropped** rather than piling onto
  the forecaster's mailbox (see §3 "What's not in this config").
- **Turn detection:** `claude` → a **Stop hook**. With `capture: none` in
  `defaults`, `up` auto-upgrades this to `capture: hook`.
- **`role`** — the standing identity, wrapped in a **standby notice** on `up` so
  the forecaster waits for your spec instead of proactively mailing peers.

### `prep` (type: `codex`)
- **`can_talk_to: [forecaster]`** — prep only reports back to the forecaster. It
  cannot reach the modeler, the reviewer, or the `user`; the baseline is owned by
  one place.
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → a `notify` program (its hook). Auto-upgraded from
  `none` to `hook` at `up`.

### `modeler` (type: `gemini`)
- **`can_talk_to: [forecaster]`** — the modeler reports its point/interval
  forecasts only to the forecaster and cannot reach the `user`, the prep, or the
  reviewer directly.
- **`command: "gemini --yolo"`** — placeholder launch command. Gemini has **no
  completion hook**, so its natural turn-detection mode is **pane polling**
  (`capture: pane`).
- **Turn detection (read this):** `defaults.capture: none` auto-upgrades only
  *hook-type* agents (`claude`/`codex`) to their hook. Gemini is a *pane* type,
  so the upgrade does **not** apply and the modeler would stay `capture: none` —
  which disables its turn-completion signal and **wedges the swarm** (its replies
  to the forecaster never get swept/routed). **Add `capture: pane` to the modeler
  block** so the pane watcher can detect when it finishes (see §10).

### `reviewer` (type: `claude`)
- **`can_talk_to: [forecaster]`** — the reviewer is the sanity gate and only
  reports its CLEAR/BOUNCE verdict back to the forecaster. It deliberately cannot
  reach the `user` or any spoke directly.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook (auto-upgraded from `none`).

### What's *not* in this config
- **No shared workdir.** All four agents resolve to distinct default workdirs, so
  there is no mailbox namespacing and no shared codebase on disk. (If you later
  want prep+modeler to share a data directory, see
  [`custom-workspace.md`](./custom-workspace.md).)
- **No explicit `when_busy` on the ping.** It defaults to `"skip"`, so a daily
  ping that lands while the forecaster is mid-turn is dropped (no pile-up). Set
  `when_busy: queue` on the rule if you'd rather have it waiting.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **No `capture: pane` override on the modeler** in the shipped file — see the
  footgun in §10; you must add it for the swarm to function end-to-end.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/forecast-analyst.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the modeler
   capture note above — fix it before relying on the swarm).
2. Creates the runtime dirs (`forecast-analyst-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/
   about.md` contact card *is* the ACL made visible: the forecaster gets
   `outbox/prep/`, `outbox/modeler/`, `outbox/reviewer/`, `outbox/user/`; prep
   gets only `outbox/forecaster/`; modeler and reviewer likewise only reach the
   forecaster.
4. **Installs per-type turn detection** — the Claude Stop hook for `forecaster`
   and `reviewer`, the Codex `notify` hook for `prep`, and (once you add it)
   pane polling for the `gemini` modeler.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm (and so the modeler's
   missing capture is at least surfaced as a `silent-but-alive` event).

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'forecast-analyst' is up with 4 agent(s)
:: attach with:  tmux attach -t <forecaster-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/forecast-analyst.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents, so it must **never** be exposed on `0.0.0.0` without a token.
See [`../ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole forecast route mail with no API keys — the mechanics are identical.

---

## 5. Drive a forecast

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the forecaster's signed-off forecast as mail
(rather than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/forecast-analyst.yaml
```

This rewrites the `user` contact card in the forecaster's `outbox/user/about.md`
to `Status: available`, so the forecaster sees you're reachable. (While away,
mail to you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the series + horizon + context into the swarm, addressed to the
forecaster:

```bash
./agentainer send --to forecaster -c examples/forecast-analyst.yaml \
  "Series = weekly online sales, 2022-01 through 2025-06. Horizon = next 12 \
   weeks. Context: a loyalty-program launch mid-March 2025 likely lifted the \
   level; we care about inventory planning, not just accuracy."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the forecaster, then — because
the inbox was empty — **released into `inbox/`** and the forecaster is **nudged**
(the protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the forecast advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **forecaster receives the spec.** It reads `inbox/`, and writes a cleaning
   request into `outbox/prep/`. On stop, that routes to prep.
2. **prep cleans + diagnoses.** It reads its inbox, writes the cleaned series +
   seasonality/trend/stationarity + a recommended baseline, and reports back into
   `outbox/forecaster/`. On stop, that routes to the forecaster.
3. **forecaster briefs the modeler.** It writes prep's output + baseline into
   `outbox/modeler/`. On stop, that routes to the modeler.
4. **modeler fits point + interval forecasts.** It reads its inbox, fits ≥2 model
   families, and returns the point/interval forecasts into `outbox/forecaster/`.
   *(This hop only completes if the modeler has `capture: pane` — see §10.)* On
   completion, that routes to the forecaster.
5. **forecaster assembles + routes to reviewer.** It combines prep's baseline with
   the modeler's forecasts into one draft and writes it into `outbox/reviewer/`.
   On stop, that routes to the reviewer.
6. **reviewer gates it.** It replies **CLEAR** (the forecast is safe) or
   **BOUNCE** (with specific defects). On a BOUNCE, the forecaster re-delegates
   to prep/modeler and re-routes until the reviewer clears it — the human never
   sees a forecast that hasn't passed.
7. **forecaster finalizes.** Only after a CLEAR does it write the final forecast
   (point + interval, winning model, stated caveats) into `outbox/user/`. On stop,
   that's delivered to your `user` mailbox (visible with `agentainer user inbox`,
   or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a spec, the agents just sit in standby (that's the point of
> the standby prompt). The swarm also has a daily 07:00 ping to the forecaster to
> re-check stale forecasts, but it won't invent work on its own otherwise.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/forecast-analyst.yaml
```

```
swarm: forecast-analyst   root: ./forecast-analyst-workspace
  forecaster (claude) up idle queue=0 unread=0 talks=prep, modeler, reviewer, user
  prep       (codex)  up idle queue=0 unread=1 talks=forecaster
  modeler    (gemini) up idle queue=0 unread=0 talks=forecaster
  reviewer   (claude) up idle queue=0 unread=0 talks=forecaster
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/forecast-analyst.yaml          # whole swarm, last 20
./agentainer logs -c examples/forecast-analyst.yaml -f        # follow live
./agentainer logs modeler -c examples/forecast-analyst.yaml  # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
and — if you forgot the modeler fix — `silent-but-alive`, etc.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox forecaster -c examples/forecast-analyst.yaml
```

Prints the one released message (headers + body), or `forecaster: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue modeler -c examples/forecast-analyst.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach reviewer -c examples/forecast-analyst.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom
— handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or (more directly)
by sending notes into an agent's inbox.

- **Send a clarification to the forecaster.** Realized the horizon should be
  daily, not weekly? `./agentainer send --to forecaster -c examples/forecast-analyst.yaml
  "Re-run with a weekly→daily re-aggregation; keep the 12-period horizon."` The
  forecaster relays the change down the chain and re-routes through the reviewer.
- **Ask the reviewer for the evidence.** `./agentainer send --to forecaster ...
  "Have the reviewer spell out which interval-coverage check failed."` — the
  forecaster forwards it.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/forecast-analyst.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/forecast-analyst.yaml     # resume is the default
```

On `up`, Agentainer reads `forecast-analyst-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the
forecaster and reviewer, `codex resume <id>` for prep, and the gemini modeler's
resume where supported. A resumed agent is *not* re-sent the standby prompt (its
prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/forecast-analyst.yaml
```

For the full story, see [`../sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`../cli-reference.md`](../cli-reference.md)):
- `prep: type: hermes` or `claude` to put cleaning on a different model than the
  forecaster.
- `modeler: type: codex` or `claude` — but remember to keep its `capture`
  consistent with the type (a `codex`/`claude` modeler gets the hook automatically;
  a `gemini`/`hermes` modeler needs `capture: pane`).
- See [`multi-llm-swarm.md`](./multi-llm-swarm.md) for mixing model families
  safely.

### Add a monitoring / alerting agent
Once a forecast ships, you may want someone watching it. Add a fifth agent that
can read the forecaster's deliverable and owns alerting:

```yaml
  - name: monitoring
    type: claude
    can_talk_to: [forecaster, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the FORECAST MONITOR. Once the forecaster delivers a signed-off
      forecast, define the freshness/SLA (when is it "stale"?) and the alerting
      for material misses vs. actuals, and report the runbook to outbox/user/.
      You never fit or clean data.
```
Then add `monitoring` to the forecaster's `can_talk_to` so it can be briefed.

### Tune the ACL
- To let the `reviewer` escalate straight to `user` (not only via the forecaster),
  add `user` to its `can_talk_to`. Mind that this widens the human-facing surface;
  the doc's convention keeps the forecaster the sole `user` contact.
- To make prep/modeler unreachable from anyone but the forecaster (already the
  case here), leave their `can_talk_to: [forecaster]` — that's the one-gate-owns-
  the-forecast guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing.

---

## 10. Tips & footguns

- **The reviewer gate is structural, not optional.** Only the forecaster lists
  `user` in `can_talk_to`, and the forecaster's role forbids posting to `user`
  until the reviewer returns **CLEAR**. If prep/modeler/reviewer try to mail
  `user` directly, the orchestrator bounces it (ACL) and drops a `system` note in
  their inbox explaining who they *can* message — the model self-corrects in-band.

- **The `gemini` modeler needs `capture: pane` (as shipped, it will wedge).**
  `defaults.capture: none` is auto-upgraded to `hook` only for `claude`/`codex`
  agents; gemini is a *pane*-type agent with no completion hook, so it keeps
  `capture: none`. With no turn-completion signal, the supervisor logs it as
  `silent-but-alive` and its replies to the forecaster are **never swept or
  routed** — the forecast stalls at step 4. Fix it by adding one line to the
  modeler block:
  ```yaml
  - name: modeler
    type: gemini
    capture: pane          # <-- gemini has no completion hook; poll the pane
    can_talk_to: [forecaster]
    command: "gemini --yolo"
  ```
  (`status` showing the modeler `busy` forever with `unread` mail, or a
  `silent-but-alive` event in the log, is the tell.)

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch (e.g. a `claude` agent whose `command`
  doesn't launch Claude, or a `gemini` agent without `capture: pane`) means
  completion never triggers and the agent pins "busy" forever.

- **The daily ping is dropped mid-turn.** The forecaster's `0 7 * * *` ping uses
  the default `when_busy: skip`, so if the forecaster is mid-forecast at 07:00
  the ping is skipped rather than queued. That's usually what you want (no stale
  ping stacking on a live draft); set `when_busy: queue` on the rule if you'd
  rather it wait.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/forecast-analyst.yaml
  ./agentainer remove-session -c examples/forecast-analyst.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches your config.

- **Availability shapes the ending.** If `user` is **away** when the forecaster
  finishes, your signed-off forecast is *held* (with a `system` "the user is away"
  ack to the forecaster) rather than lost — read it later with
  `agentainer user inbox` or flip yourself available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions.

---

### See also

- [`../getting-started.md`](../getting-started.md) — install and first swarm.
- [`../mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`../sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`../cli-reference.md`](../cli-reference.md) — every subcommand, including `send`/`user`/`logs`.
- [`../configuration.md`](../configuration.md) — `capture`, `pings`/`when_busy`, ACL semantics.
- [`../ui-guide.md`](../ui-guide.md) — the `serve` mail-app control plane.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- `examples/forecast-analyst.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

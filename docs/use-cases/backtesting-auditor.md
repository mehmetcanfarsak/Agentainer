# Use case: Backtesting auditor

A concrete, end-to-end walkthrough of the shipped
`examples/backtesting-auditor.yaml` swarm — an independent validation line that
takes a **submitted trading-strategy backtest** and tells you whether to trust
it. An **audit-lead** hub coordinates three independent validators — a
**reproducer** that re-runs the strategy on clean data, a **bias-detector** that
hunts for look-ahead / survivorship / parameter-snooping, and a **stats-reviewer**
that checks the deflated-Sharpe and significance — and writes a single **verdict**
(PASS / PASS-WITH-CAVEATS / FAIL) to you. The validators never compare notes with
each other; each forms an independent view and only the audit-lead reconciles them.

Everything below is based on the actual contents of
`examples/backtesting-auditor.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 0. Safety — paper / simulated only

This swarm audits **backtests**. It runs on historical or simulated data and
**never** places orders, connects to a broker, or trades live capital. The
deliverable is **educational research, NOT financial advice**: a passed backtest
is not a prediction of future performance and is not a recommendation to invest.
The audit-lead's verdict must carry this disclaimer. The reproducer is confined to
the `submission/` + `data/` folders and must not reach any account or broker. This
is a hard scope, not a suggestion — keep the audit on simulated ground.

---

## 1. Who this is for

Quant researchers, strategy authors, and anyone handed a backtest they did not
build and have to trust. The swarm encodes the discipline that makes an audit
credible — one owner of the verdict, an independent re-run that does not take the
author's numbers on faith, a bias scan that looks for *how the test was built*
wrong, and a stats check that asks whether the edge is even real. It is deliberately
a **hub-and-spoke**: every submission and every finding passes through the
audit-lead, so the three independent views are reconciled in exactly one place and
the human only ever sees the collated judgment.

It is distinct from `examples/quant-factor-miner.yaml` (which *mines* candidate
factors); this swarm *independently validates* a backtest someone already submitted.

---

## 2. The topology

```
          user
            |
        audit-lead              (the hub: talks to all three validators + user)
         /    |    \
  reproducer bias-detector stats-reviewer   (each forms an INDEPENDENT view; reports only to audit-lead)
  (codex)    (claude)      (gemini)
```

Four agents, one directed flow:

1. **`user` → `audit-lead`** — you send the submission folder (strategy code, spec,
   reported metrics, data) and ask for an audit.
2. **`audit-lead` → `reproducer`** — the audit-lead sends the spec + code + data and
   asks for an independent re-run on clean, point-in-time data with the *realized*
   metrics (return, Sharpe, drawdown, turnover).
3. **`reproducer` → `audit-lead`** — the realized metrics come back, compared line by
   line against what the author reported.
4. **`audit-lead` → `bias-detector`** — the audit-lead sends the spec + code + data
   and asks it to scan for look-ahead, survivorship, and parameter snooping.
5. **`bias-detector` → `audit-lead`** — the bias findings come back.
6. **`audit-lead` → `stats-reviewer`** — the audit-lead sends the reported metrics +
   the reproducer's realized metrics and asks for deflated-Sharpe, multiple-testing,
   and significance checks.
7. **`stats-reviewer` → `audit-lead`** — the stats verdict comes back.
8. **`audit-lead` → `user`** — the audit-lead reconciles the three independent views
   into one verdict (PASS / PASS-WITH-CAVEATS / FAIL + the specific defects), opens
   with the educational-not-advice disclaimer, and delivers it to you.

The routing above is *enforced* by each agent's `can_talk_to` list. The three
validators **never** talk to `user` (or to each other) — only the audit-lead does.
If a validator tried to mail `user` directly, the orchestrator bounces it as a
`system` message and files it in `failed/`.

---

## 3. The config, explained

Here is `examples/backtesting-auditor.yaml` in full (role bodies abbreviated with
`...` for readability; the structure, names, ACLs, and commands are exact):

```yaml
swarm:
  name: backtesting-auditor
  root: ./backtesting-auditor-workspace

defaults:
  capture: none              # claude/codex are auto-upgraded to their hook at up
  can_talk_to: []            # tightened per agent below

agents:
  - name: audit-lead
    type: claude
    can_talk_to: [reproducer, bias-detector, stats-reviewer, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the AUDIT-LEAD and the only agent who talks to the human (user). ...
      (1) read the submission, ask ONE clarifying question if a load-bearing piece
       is missing; (2) delegate to REPRODUCER for an independent re-run;
       (3) delegate to BIAS-DETECTOR for look-ahead / survivorship / snooping;
       (4) delegate to STATS-REVIEWER for deflated-Sharpe + significance;
       (5) reconcile the three into ONE verdict (PASS / PASS-WITH-CAVEATS / FAIL)
       with the disclaimer, and deliver to user. ...

  - name: reproducer
    type: codex
    can_talk_to: [audit-lead]
    command: "codex --yolo"
    role: |
      You are the REPRODUCER. Re-run the strategy from spec on a clean, point-in-time
      dataset; report REALIZED metrics and the gap vs the author's reported numbers
      ... Do NOT assess bias or significance. Stay in submission/ + data/, never a
      broker/account. Report ONLY to the audit-lead. ...

  - name: bias-detector
    type: claude
    can_talk_to: [audit-lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the BIAS-DETECTOR. Scan the spec/code/data for look-ahead bias,
      survivorship bias, and parameter snooping / overfitting; state the mechanism,
      where it lives, and how material. Do NOT re-run or do significance math.
      Report ONLY to the audit-lead. ...

  - name: stats-reviewer
    type: gemini
    can_talk_to: [audit-lead]
    command: "gemini --yolo"
    role: |
      You are the STATS-REVIEWER. Given reported + reproduced metrics, run
      deflated-Sharpe, multiple-testing / p-hacking correction, and significance /
      robustness checks; be explicit about uncertainty. Do NOT re-run or hunt bias.
      Report ONLY to the audit-lead. ...
```

Field by field:

### `swarm`
- **`name: backtesting-auditor`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./backtesting-auditor-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent's workdir defaults to
  `backtesting-auditor-workspace/<name>` (audit-lead, reproducer, bias-detector,
  stats-reviewer), and orchestrator state goes under
  `backtesting-auditor-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless it overrides them.
- **`capture: none`** — the default turn-detection mode. At `up`, the loader
  **auto-upgrades** this for `claude` and `codex` to their natural hook (the
  `validate` run prints the auto-upgrade warnings — see per-type turn detection
  below). It is a safe floor; every agent states its own `can_talk_to`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Each agent below
  states its own list explicitly.

### `audit-lead` (type: `claude`)
- **`can_talk_to: [reproducer, bias-detector, stats-reviewer, user]`** — the
  audit-lead is the hub and the **only agent that can talk to `user`**. That last
  part is the whole point: keep the human-facing verdict to one agent and put the
  reconciliation in front of it.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`;
  the `capture: none` default is auto-upgraded to hook here).

### `reproducer` (type: `codex`)
- **`can_talk_to: [audit-lead]`** — reports the realized metrics back to the
  audit-lead and nowhere else. It cannot reach the user, the bias-detector, or the
  stats-reviewer directly.
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`
  (the `capture: none` default auto-upgrades to the notify hook).

### `bias-detector` (type: `claude`)
- **`can_talk_to: [audit-lead]`** — receives the spec/code/data from the audit-lead
  and returns its bias findings to the audit-lead only. It never touches the user.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook (auto-upgraded from `capture: none`).

### `stats-reviewer` (type: `gemini`)
- **`can_talk_to: [audit-lead]`** — receives the reported + realized metrics from
  the audit-lead and returns the stats verdict to the audit-lead only. It never
  touches the user.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **Turn detection:** `gemini` has no completion hook, so it relies on **pane
  polling** — the supervisor watches its pane for turn completion. (This is why the
  `capture: none` default needs no upgrade for gemini; only claude/codex get the
  auto-hook warnings.)

### ACL enforcement

The ACL is **cooperative, not OS isolation** (Decision D15): agents have filesystem
access and *could* write straight into another inbox, but the orchestrator only
ever *releases* and *routes* mail between names on the sender's `can_talk_to` list.
Anything addressed outside that list is bounced back as a `system` message filed in
`failed/`, so a model that forgets the rule self-corrects in-band. Here that means
the three validators can *only* reach the audit-lead, and only the audit-lead can
reach `user` — the reconciliation (and the verdict's single funnel) is structurally
guaranteed.

### Per-type turn detection

Turn-completion detection is the system clock (the stop → sweep → route → release →
nudge loop). It is **per `type`**:
- `claude` (`audit-lead`, `bias-detector`) → **Stop hook** — fires when Claude
  finishes a turn.
- `codex` (`reproducer`) → **`notify` hook** — fires when Codex finishes.
- `gemini` (`stats-reviewer`) → **pane polling** — the supervisor reads the pane to
  decide the turn ended.

A `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
Claude) means completion never fires and the agent pins "busy" forever — which is
why `command` must launch the same CLI family `type` implies.

### What's *not* in this config
- **No `workdir` overrides.** All four agents get the default
  `backtesting-auditor-workspace/<name>`, so no mailbox namespacing is needed (each
  agent owns its directory). For the shared-workdir case, see
  [`custom-workspace.md`](./custom-workspace.md).
- **No per-agent `capture` overrides.** The `defaults: capture: none` is
  auto-upgraded to the type's hook for claude/codex; gemini uses pane polling.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **No `pings:` block.** The audit is event-driven off your submission — there is
  no scheduled self-start here (unlike some finance examples). Add one if you want
  a periodic re-audit of a tracked strategy; see
  [`configuration.md`](../configuration.md).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/backtesting-auditor.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config.
2. Creates the runtime dirs (`backtesting-auditor-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/`
   `about.md` contact card *is* the ACL made visible: the audit-lead gets
   `outbox/reproducer/`, `outbox/bias-detector/`, `outbox/stats-reviewer/`,
   `outbox/user/`; each validator gets only `outbox/audit-lead/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `audit-lead` and
   `bias-detector`, the Codex `notify` hook for `reproducer`; the gemini agent is
   covered by pane polling.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents (and drives gemini's pane polling) so one stuck agent can't wedge
   the swarm.

At the end, `up` prints attach and **`serve`** hints. The `serve` line gives you the
mail-app control-plane UI (threads, live panes, send-as-user, availability toggle).
Drop `--host`/`--token` for the safe loopback-only `127.0.0.1` bind — the UI can
start processes, edit config, and type into agents that may run with elevated
permissions, so it must **never** be exposed on `0.0.0.0` without a token. See
[`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch the
> whole reproduce→bias→stats→verdict loop route mail with no API keys — the
> mechanics are identical.

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the audit-lead's verdict as mail (rather than
have it held), turn yourself available first:

```bash
./agentainer user available -c examples/backtesting-auditor.yaml
```

This rewrites the `user` contact card in the audit-lead's `outbox/user/about.md` to
`Status: available`, so the audit-lead sees you're reachable. (While away, mail to
you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the submission into the swarm, addressed to the audit-lead. Put the
strategy code, spec, reported metrics, and data somewhere the agents can read
(e.g. drop them in the audit-lead's workdir under `submission/`), then:

```bash
./agentainer send --to audit-lead -c examples/backtesting-auditor.yaml \
  "Backtest to audit is in submission/ (strategy.py, spec.md, \
   reported_metrics.csv, data/). Reproduce it on clean point-in-time data, \
   hunt for look-ahead / survivorship / parameter snooping, and check the \
   deflated-Sharpe and significance. Deliver a verdict."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the audit-lead, then — because the
inbox was empty — **released into `inbox/`** and the audit-lead is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the audit advance one turn at a time. Each arrow
is a `stop → sweep → route → release → nudge` cycle:

1. **audit-lead receives the submission.** It reads `inbox/`, asks its one
   clarifying question if a load-bearing piece is missing, then writes delegations
   into `outbox/reproducer/`, `outbox/bias-detector/`, and `outbox/stats-reviewer/`.
   On stop, those route to the validators.
2. **reproducer re-runs the strategy.** It reads its inbox, runs on clean
   point-in-time data, and reports the *realized* metrics (and the gap vs the
   author's numbers) back into `outbox/audit-lead/`. On stop, that routes to the
   audit-lead.
3. **bias-detector scans.** It reads its inbox, reports look-ahead / survivorship /
   snooping findings, and reports back into `outbox/audit-lead/`. On stop, that
   routes to the audit-lead.
4. **stats-reviewer checks significance.** It reads its inbox (reported + realized
   metrics), runs deflated-Sharpe + multiple-testing + significance, and reports
   back into `outbox/audit-lead/`. On stop, that routes to the audit-lead.
5. **audit-lead reconciles and writes the verdict.** It combines the three
   independent views into one verdict — PASS / PASS-WITH-CAVEATS / FAIL, with the
   specific defects and the educational-not-advice disclaimer — and writes it into
   `outbox/user/`. On stop, that's delivered to your `user` mailbox.
6. **you get the verdict** — visible with `agentainer user inbox`, or in the UI.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion. If you
never send a submission, the agents just sit in standby.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/backtesting-auditor.yaml
```

```
swarm: backtesting-auditor   root: ./backtesting-auditor-workspace
  audit-lead    (claude) up idle queue=0 unread=0 talks=reproducer, bias-detector, stats-reviewer, user
  reproducer    (codex)  up idle queue=0 unread=1 talks=audit-lead
  bias-detector (claude) up idle queue=0 unread=0 talks=audit-lead
  stats-reviewer(gemini) up idle queue=0 unread=0 talks=audit-lead
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/backtesting-auditor.yaml          # whole swarm, last 20
./agentainer logs -c examples/backtesting-auditor.yaml -f        # follow live
./agentainer logs stats-reviewer -c examples/backtesting-auditor.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox audit-lead -c examples/backtesting-auditor.yaml
```

Prints the one released message (headers + body), or `audit-lead: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue audit-lead -c examples/backtesting-auditor.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach reproducer -c examples/backtesting-auditor.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

Because every message is natural-language mail, you can steer the audit mid-flight.

- **Send a clarification to the audit-lead.** Realized the data was split-adjusted?
  `./agentainer send --to audit-lead -c examples/backtesting-auditor.yaml "Re-brief
  the reproducer: use split- AND dividend-adjusted prices, point-in-time."` The
  audit-lead relays the change and re-routes the verdict.
- **Ask why a verdict came back FAIL.** `./agentainer inbox audit-lead` (or the UI)
  shows the three validators' reports the audit-lead reconciled — which bias fired,
  what the deflated-Sharpe said, where the reproduction diverged.
- **Re-audit after a fix.** If the author patches the strategy, drop the new
  `submission/` and re-send; the whole independent loop runs again from clean.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live.

When you're done:

```bash
./agentainer down -c examples/backtesting-auditor.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/backtesting-auditor.yaml     # resume is the default
```

On `up`, Agentainer reads
`backtesting-auditor-workspace/.agentainer/sessions.yaml` (written as each agent
finished its first turn) and reattaches the recorded conversations via each type's
native resume: `claude --resume <id>` for the audit-lead and bias-detector,
`codex resume <id>` for the reproducer, and the gemini session via its recorded id.
A resumed agent is *not* re-sent the standby prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/backtesting-auditor.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a scheduled re-audit
Unlike the finance examples, this swarm has no `pings:` block — the audit is one
submission at a time. To re-audit a tracked strategy on a calendar, give the
audit-lead a ping:

```yaml
  - name: audit-lead
    type: claude
    can_talk_to: [reproducer, bias-detector, stats-reviewer, user]
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: |
          Re-audit the tracked strategy in submission/ against the latest
          point-in-time data: reproduce, scan for bias, check the deflated-Sharpe,
          and post an updated verdict to user.
        cron: "0 9 * * 1"             # 09:00 every Monday
        when_busy: skip
    role: |
      ...
```

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `bias-detector: type: claude` (or `gemini`/`hermes`) to put the bias scan on a
  different model than the audit-lead.
- `reproducer: type: hermes` if you want the re-run engine on Hermes while keeping
  codex out.
- Remember: `gemini`/`hermes` rely on **pane polling** (no completion hook), so they
  don't need (and shouldn't be given) a hook-based `capture`.

### Tune the ACL
- To let a validator escalate straight to `user` (e.g. the stats-reviewer flagging a
  fatal p-hacking case), add `user` to its `can_talk_to`. Mind that this widens the
  human-facing surface and bypasses the audit-lead's single-funnel guarantee — the
  doc's convention keeps the audit-lead the sole `user` contact so the verdict is
  always one reconciled view.
- To make a validator unreachable from anyone but the audit-lead (already the case
  here), leave its `can_talk_to: [audit-lead]` — that's the one-place-owns-the-verdict
  guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader discussion
  of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md) for
  mixing model families safely.

### Lock the scope tighter
The reproducer's role already confines it to `submission/` + `data/` and forbids any
broker/account/live-capital reach. If you run with real market-data credentials in
the `command`, keep the agents on disposable, simulated datasets and never grant
network egress to a trading venue — the educational-not-advice line is a real
boundary, not boilerplate.

---

## 10. Tips & footguns

- **Keep the audit-lead the only `user`-facing agent.** Only the audit-lead lists
  `user` in `can_talk_to`. That gives you a single funnel: the three independent
  validator views are always reconciled by one agent before they reach you. If a
  validator tried to mail `user` directly, the orchestrator bounces it (ACL) and
  drops a `system` note in their inbox explaining who they *can* message — the model
  self-corrects in-band.

- **The reconciliation is the feature, not a bottleneck.** Three independent views
  (reproduced numbers, bias scan, stats) can disagree — and the disagreement is the
  signal. The audit-lead is told to *name* the conflict rather than paper over it,
  so a "PASS" means all three lined up, not that two were silently dropped.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an agent
  stops, its outbox is swept, mail is routed, recipients are released and nudged. If
  an agent seems stuck, check that its **turn detection actually fires** — a
  `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
  Claude, or a `gemini` agent whose pane never settles) means completion never
  triggers and the agent pins "busy" forever. `status` showing an agent `busy` for a
  long time with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops — relevant if a validator and the audit-lead
  chatter past the verdict.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime + mailboxes)
  and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/backtesting-auditor.yaml
  ./agentainer remove-session -c examples/backtesting-auditor.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files (the submission you dropped in)
  or your config.

- **Availability shapes the ending.** If `user` is **away** when the audit-lead
  finishes, your verdict is *held* (with a `system` "the user is away" ack to the
  audit-lead) rather than lost — read it later with `agentainer user inbox` or flip
  yourself available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions (`--dangerously-skip-permissions`, `--yolo`).

- **This is paper/simulated research only.** The verdict is educational, not
  financial advice. Never point the reproducer at a broker or live capital, and
  keep the educational-not-advice disclaimer in the audit-lead's verdict.

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- `examples/backtesting-auditor.yaml` — the config this walkthrough is built on.
- `examples/quant-factor-miner.yaml` — a sibling that *mines* factors (distinct from
  this swarm's job of *validating* a submitted backtest).
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

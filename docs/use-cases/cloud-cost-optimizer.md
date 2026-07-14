# Use case: Cloud cost optimizer

A concrete, end-to-end walkthrough of the shipped
`examples/cloud-cost-optimizer.yaml` swarm — a four-agent FinOps team that
hunts cloud waste and right-sizes your bill without touching anything
customer-facing. A **claude optimizer** hub owns the human-facing surface and
the workflow; it delegates to a **scanner** (idle / orphaned resources), a
**rightsizer** (sizing + commitments), and a **risk-checker** that gates every
recommendation before it reaches you. The optimizer also runs a weekly
cost-review ping so a fresh savings ledger keeps accruing even when you aren't
actively sending work.

Everything below is based on the actual contents of
`examples/cloud-cost-optimizer.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Platform / FinOps / cloud-ops engineers who want a disciplined way to find waste
and right-size spend without risking a production customer path. The swarm
encodes the discipline that makes cost work safe — a single owner of the
workflow, a read-only scanner that never touches infrastructure, a rightsizer
that backs every claim with utilization evidence, and a conservative
risk-checker that gates anything that could reach a customer. The agents do the
typing; you get a ranked, risk-tagged savings plan.

It is deliberately a **hub-and-spoke**, not a free-for-all: every piece of work
and every deliverable passes through the optimizer, so the savings plan has
exactly one authority and no recommendation ships to you without risk sign-off.

---

## 2. The topology

```
          user
            |
         optimizer                (the hub: talks to scanner, rightsizer, risk-checker, user)
          /   |   \
   scanner  rightsizer  risk-checker
   (codex)   (gemini)    (claude)
```

Four agents, one directed flow:

1. **`user` → `optimizer`** — you send a cloud account / bill context (read-only
   inventory export, last month's bill, utilization dump).
2. **`optimizer` → `scanner`** — the optimizer asks for the waste inventory
   (idle, over-provisioned, orphaned resources).
3. **`optimizer` → `rightsizer`** — the optimizer asks for sizing /
   commitment recommendations, backed by utilization evidence.
4. **`scanner` → `optimizer`** and **`rightsizer` → `optimizer`** — both report
   back to the hub (they never talk to each other or to `user`).
5. **`optimizer` → `risk-checker`** — the optimizer merges the waste + sizing
   into one plan and sends it for gating.
6. **`risk-checker` → `optimizer`** — each item is tagged CLEAR / RAMP REQUIRED
   / HUMAN DECISION / BLOCK.
7. **`optimizer` → `user`** — only the risk-cleared items (plus anything flagged
   for human decision) are delivered as a ranked savings plan.

The routing above is *enforced* by each agent's `can_talk_to` list. The three
specialists **never** talk to `user` directly — only the optimizer does. The
optimizer also fires a **weekly cost-review ping** (Monday 09:30, host local
time; skipped if it's mid-turn) so the ledger stays current between your asks.

---

## 3. The config, explained

Here is `examples/cloud-cost-optimizer.yaml` in full:

```yaml
swarm:
  name: cloud-cost-optimizer
  root: ./cloud-cost-optimizer-workspace

defaults:
  capture: none              # claude/codex are auto-upgraded to their hook at up
  can_talk_to: []            # tightened per agent below

agents:
  - name: optimizer
    type: claude
    can_talk_to: [scanner, rightsizer, risk-checker, user]
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: |
          Weekly cost review. Ask scanner for any newly idle / over-provisioned
          resources, unattached volumes, or old snapshots since last week; ask
          rightsizer for the latest right-size / commitment recommendations with
          utilization evidence; ask risk-checker to re-gate anything customer-
          facing. Fold it into a one-page savings ledger and post it to user. If
          you are already mid-turn, ignore this tick.
        cron: "30 9 * * 1"          # 09:30 every Monday
    role: |
      You are the OPTIMIZER of a cloud-cost FinOps swarm. You are the only agent
      that talks to the human (user) and you own the workflow ... (delegate to
      scanner / rightsizer / risk-checker, reconcile, gate via risk-checker, then
      report a ranked savings plan to user) ...

  - name: scanner
    type: codex
    can_talk_to: [optimizer]
    command: "codex --yolo"
    role: |
      You are the SCANNER ... a read-only detective: find idle / over-provisioned
      compute, orphaned storage (unattached EBS, aged snapshots), idle network
      gear, and zombie managed services. Do NOT modify anything. ...

  - name: rightsizer
    type: gemini
    can_talk_to: [optimizer]
    command: "gemini --yolo"
    role: |
      You are the RIGHTSIZER ... turn waste findings into evidence-backed sizing
      and commitment recommendations (Savings Plans / reserved instances), with a
      monthly $ delta and a confidence level. Mark anything near a customer path
      "NEEDS RISK REVIEW". You only advise. ...

  - name: risk-checker
    type: claude
    can_talk_to: [optimizer]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the RISK-CHECKER ... the gate: every recommendation must clear you
      (CLEAR / RAMP REQUIRED / HUMAN DECISION / BLOCK) before it reaches user.
      You never modify infrastructure; you only judge. ...
```

Field by field:

### `swarm`
- **`name: cloud-cost-optimizer`** — the swarm's name (shows up in `status`,
  logs, sessions).
- **`root: ./cloud-cost-optimizer-workspace`** — the parent directory for the
  agents' working directories and mailboxes. Each agent's workdir defaults to
  `<root>/<name>` (so `optimizer`, `scanner`, `rightsizer`, `risk-checker` each
  get their own folder). Orchestrator state goes under
  `<root>/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — a baseline that says "don't capture." The loader
  auto-upgrades this to the type's **hook** capture for `claude`/`codex`
  (emitting a warning), so those two families still fire their turn-completion
  signal. **`gemini` is *not* auto-upgraded** (see the footgun in §10) — it
  needs `capture: pane` and `capture: none` leaves it with no completion signal.
- **`can_talk_to: []`** — the default ACL is "talk to no one." Every agent
  below states its own list explicitly, so this is just a safe floor.

### `optimizer` (type: `claude`)
- **`can_talk_to: [scanner, rightsizer, risk-checker, user]`** — the optimizer
  is the hub: it delegates to the three specialists and is the **only agent that
  can talk to `user`**. Keep the human-facing surface to this single agent.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command, e.g. a
  shell alias. Treat command strings as sensitive; they may embed keys.)
- **`pings`** — *one* schedule: a weekly cost-review tick. `cron: "30 9 * * 1"`
  is standard 5-field `minute hour day-of-month month day-of-week` in the host's
  **local** time (09:30 every Monday). `when_busy` is unset, so it defaults to
  `skip` — if the tick lands while the optimizer is mid-turn building a savings
  plan, the tick is dropped rather than stacking; next Monday's review is fresh
  enough. The message text re-delegates to all three spokes and asks for a
  one-page ledger posted to `user`.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at
  `up`); `defaults: capture: none` is auto-upgraded to `hook` with a warning.

### `scanner` (type: `codex`)
- **`can_talk_to: [optimizer]`** — the scanner only reports back to the
  optimizer. It deliberately cannot reach the rightsizer, risk-checker, or the
  `user`; the waste inventory has exactly one reader.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "find idle / over-provisioned compute, orphaned storage, idle
  network gear, and zombie managed services; give resource id, monthly $, and
  utilization proof; read-only only — never modify."
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`;
  `defaults: capture: none` is auto-upgraded to `hook` with a warning.

### `rightsizer` (type: `gemini`)
- **`can_talk_to: [optimizer]`** — the rightsizer only reports back to the
  optimizer. It cannot reach the `user` or the other spokes.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **`role`** — "turn waste findings into instance/node right-sizing, fleet-mix,
  and commitment (Savings Plans / reserved) recommendations, each with a monthly
  $ delta, utilization evidence, and a confidence level; flag anything near a
  customer path 'NEEDS RISK REVIEW'."
- **Turn detection:** `gemini` has **no** completion hook, so it relies on **pane
  polling**. ⚠️ As shipped, `defaults: capture: none` is *not* auto-upgraded for
  gemini, so this agent gets **`capture: none`** and emits no completion signal —
  see the footgun in §10.

### `risk-checker` (type: `claude`)
- **`can_talk_to: [optimizer]`** — the risk-checker only reports back to the
  optimizer. It is the gate; nothing bypasses it to reach `user`.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **`role`** — "for each plan item decide CLEAR / RAMP REQUIRED / HUMAN DECISION
  / BLOCK with a one-line reason; be the conservative voice; never modify
  infrastructure, only judge."
- **Turn detection:** `claude` → Stop hook (auto-upgraded from `capture: none`).

### How the ACL is enforced

`can_talk_to` is cooperative, not OS isolation. When an agent writes a file into
`outbox/<name>/`, the orchestrator checks `<name>` against the sender's list. A
target not on the list is **bounced** as a `system` message and filed in
`failed/` — the model self-corrects in-band without learning any new concept.
Because the spokes only list `optimizer` and only the optimizer lists `user`, the
three specialists can never mail you directly, and none of them can mail each
other; every path funnels through the hub. (Decision D15: this is enforced for
well-behaved agents and documented honestly, not a security boundary.)

### What's *not* in this config
- **No shared workdirs.** Unlike the data-pipeline swarm, every agent here has
  its own private working directory, so no mailbox namespacing is needed. (If you
  later share a workdir, see [`custom-workspace.md`](./custom-workspace.md).)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it
  on (see §4).
- **No per-spoke pings.** Only the optimizer carries a schedule; the spokes are
  purely event-driven off real mail (or the optimizer's delegated work).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/cloud-cost-optimizer.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the `capture: none` auto-upgrade
   warnings for the three `claude`/`codex` agents (`rightsizer` gets no warning —
   and no capture; see §10).
2. Creates the runtime dirs (`cloud-cost-optimizer-workspace/.agentainer/…`:
   log, queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The optimizer gets
   `outbox/scanner/`, `outbox/rightsizer/`, `outbox/risk-checker/`,
   `outbox/user/`; each spoke gets just `outbox/optimizer/`. The
   `outbox/<peer>/about.md` contact card *is* the ACL made visible.
4. **Installs per-type turn detection** — the Claude Stop hook for `optimizer`
   and `risk-checker`, and the Codex `notify` hook for `scanner`. (The gemini
   `rightsizer` has no capture installed — see §10.)
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles
   stale/dead/silent agents so one stuck agent can't wedge the swarm. It also
   arms the optimizer's Monday 09:30 ping schedule.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'cloud-cost-optimizer' is up with 4 agent(s)
:: attach with:  tmux attach -t <optimizer-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/cloud-cost-optimizer.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents, so it must **never** be exposed on `0.0.0.0` without a token.
See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole cost loop route mail (and fire the weekly ping) with no API keys —
> the mechanics are identical.

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the optimizer's ranked savings plan as mail
(rather than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/cloud-cost-optimizer.yaml
```

This rewrites the `user` contact card in the optimizer's `outbox/user/about.md`
to `Status: available`, so the optimizer sees you're reachable. (While away, mail
to you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send a cloud account / bill context into the swarm, addressed to the
optimizer:

```bash
./agentainer send --to optimizer -c examples/cloud-cost-optimizer.yaml \
  "Here is our last AWS bill export + a read-only inventory dump. Find $X/mo in \
   savings without touching prod customer paths. Report back."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the optimizer, then — because
the inbox was empty — **released into `inbox/`** and the optimizer is **nudged**
(the protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the cost loop advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **optimizer receives the context.** It reads `inbox/`, breaks the ask into
   pieces, and writes delegations into `outbox/scanner/` and `outbox/rightsizer/`.
   On stop, those route to the spokes.
2. **scanner inventories waste; rightsizer sizes it.** Each reads its inbox and
   reports back into `outbox/optimizer/`. On stop, both route to the hub.
3. **optimizer merges and hands to risk-checker.** It folds the waste +
   recommendations into one plan and writes it into `outbox/risk-checker/`. On
   stop, that routes to the gate.
4. **risk-checker gates.** It tags each item CLEAR / RAMP REQUIRED / HUMAN
   DECISION / BLOCK and reports back into `outbox/optimizer/`. On stop, that
   routes to the hub.
5. **optimizer finalizes.** It keeps only the risk-cleared items (plus anything
   flagged for human decision) and writes the ranked savings plan into
   `outbox/user/`. On stop, that's delivered to your `user` mailbox (visible with
   `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion. (The
gemini `rightsizer`'s turn completion won't fire automatically — see §10 — so
that leg may need the idle escape hatch.)

> If you *don't* send a context, the agents sit in standby — and the optimizer's
> Monday 09:30 ping still fires, so the ledger keeps accruing on its own.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/cloud-cost-optimizer.yaml
```

```
swarm: cloud-cost-optimizer   root: ./cloud-cost-optimizer-workspace
  optimizer    (claude) up idle queue=0 unread=1 talks=scanner, rightsizer, risk-checker, user
  scanner      (codex)  up idle queue=0 unread=0 talks=optimizer
  rightsizer   (gemini) up idle queue=0 unread=0 talks=optimizer
  risk-checker (claude) up idle queue=0 unread=0 talks=optimizer
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/cloud-cost-optimizer.yaml            # whole swarm, last 20
./agentainer logs -c examples/cloud-cost-optimizer.yaml -f         # follow live
./agentainer logs rightsizer -c examples/cloud-cost-optimizer.yaml  # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
plus `ping` events for the Monday tick, etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox optimizer -c examples/cloud-cost-optimizer.yaml
```

Prints the one released message (headers + body), or `optimizer: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue optimizer -c examples/cloud-cost-optimizer.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach scanner -c examples/cloud-cost-optimizer.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom
— handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or (more directly)
by sending notes into an agent's inbox.

- **Tighten the scope.** `./agentainer send --to optimizer -c examples/cloud-cost-optimizer.yaml
  "Ignore dev/staging — I only care about prod and the data-lake accounts."` The
  optimizer re-briefs the spokes.
- **Ask for the evidence behind one item.** `./agentainer send --to optimizer ...
  "Have the rightsizer show the CPU/RU numbers behind the RDS downsize."` — the
  optimizer forwards it.
- **Override a risk call.** `./agentainer send --to optimizer ... "I'll accept
  the HUMAN DECISION item for the idle Redis — ramp it 25%/day."` — the optimizer
  folds your decision into the plan.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send
  as `user`, toggle `user` availability, and watch panes live — useful when you
  want to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/cloud-cost-optimizer.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/cloud-cost-optimizer.yaml     # resume is the default
```

On `up`, Agentainer reads
`cloud-cost-optimizer-workspace/.agentainer/sessions.yaml` (written as each agent
finished its first turn) and reattaches the recorded conversations via each type's
native resume: `claude --resume <id>` for the optimizer and risk-checker,
`codex resume <id>` for the scanner, and the gemini session for the rightsizer. A
resumed agent is *not* re-sent the standby prompt (its prior context is
restored). Your pending savings plan and the optimizer's ping schedule come back
with it.

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/cloud-cost-optimizer.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Fix the rightsizer's turn detection (recommended)
As shipped, `rightsizer` is `type: gemini` and inherits `defaults: capture: none`,
which leaves it with **no** completion signal. Add an explicit per-agent capture
so its turn completion actually fires:

```yaml
  - name: rightsizer
    type: gemini
    capture: pane              # gemini has no hook; use pane polling
    can_talk_to: [optimizer]
    command: "gemini --yolo"
    role: |
      ...
```

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `scanner: type: claude` if you want the waste inventory on Claude while the hub
  stays Codex-free.
- `risk-checker: type: codex` if you prefer a different gate model.
- Remember: `gemini`/`hermes` need `capture: pane` (pane polling) since they
  have no completion hook — don't leave them on `capture: none`.

### Tune the ACL
- To let the `risk-checker` escalate a HUMAN DECISION item straight to `user` (not
  only via the optimizer), add `user` to its `can_talk_to`. Mind that this widens
  the human-facing surface; the doc's convention keeps the optimizer the sole
  `user` contact.
- To make a spoke unreachable from anyone but the optimizer (already the case
  here), leave its `can_talk_to: [optimizer]` — that's the one-place-owns-the-
  workflow guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md)
  for mixing model families safely (this swarm already mixes claude + codex +
  gemini).

### Change the ping schedule
The optimizer's `pings[].cron` is host-local 5-field cron. To review every
Friday at 18:00 instead: `cron: "0 18 * * 5"`. To make a due tick *wait* behind
a mid-turn instead of being skipped, add `when_busy: queue` to the ping entry.

---

## 10. Tips & footguns

- **Keep the optimizer the only `user`-facing agent.** Only the optimizer lists
  `user` in `can_talk_to`. That gives you a single funnel: raw waste findings and
  sizing math always pass through reconciliation *and* risk gating before they
  reach you. If a spoke tries to mail `user` directly, the orchestrator bounces it
  (ACL) and drops a `system` note in their inbox explaining who they *can* message
  — the model self-corrects in-band.

- **⚠️ The gemini `rightsizer` has NO turn-detection as shipped.** `defaults:
  capture: none` is auto-upgraded to `hook` for `claude`/`codex`, but **not** for
  `gemini` — gemini has no completion hook and relies on pane polling, so it needs
  an explicit `capture: pane`. Without it, when the rightsizer finishes a turn the
  orchestrator never gets a completion signal, its outbox is never swept, and the
  cost loop wedges at the sizing step. Two fixes: add `capture: pane` to the
  rightsizer (recommended), or change it to `type: claude`/`codex`. `status`
  showing the rightsizer `busy` with `unread` mail is the tell. You can also force
  it back to idle with `./agentainer idle rightsizer -c examples/cloud-cost-optimizer.yaml`.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch (e.g. a `claude` agent whose `command`
  doesn't launch Claude) means completion never triggers and the agent pins
  "busy" forever.

- **The weekly ping is in host-local time and skips mid-turn.** `cron: "30 9 * *
  1"` means 09:30 *local* on the machine running `agentainer`. Because
  `when_busy` defaults to `skip`, a tick that lands while the optimizer is mid-turn
  is dropped (not queued) — next Monday's review is fresh enough. Set `when_busy:
  queue` on the ping if you'd rather the review wait its turn.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/cloud-cost-optimizer.yaml
  ./agentainer remove-session -c examples/cloud-cost-optimizer.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches your config.

- **Availability shapes the ending.** If `user` is **away** when the optimizer
  finishes, your savings plan is *held* (with a `system` "the user is away" ack to
  the optimizer) rather than lost — read it later with `agentainer user inbox` or
  flip yourself available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions (`--dangerously-skip-permissions` /
  `--yolo`).

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely (this swarm uses claude + codex + gemini).
- [`configuration.md`](../configuration.md) — full config reference, including `pings` / `cron` and `capture`.
- [`cli-reference.md`](../cli-reference.md) — every subcommand (`up`, `send`, `status`, `logs`, `idle`, `user`, …).
- `examples/cloud-cost-optimizer.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

# Use case: Portfolio rebalancer

A concrete, end-to-end walkthrough of the shipped
`examples/portfolio-rebalancer.yaml` swarm — a strategic/tactical
**asset-allocation rebalancing** assembly line that turns a policy mandate + a
current book into a **risk-approved order list**, with a human (you) in control
at every step. A **portfolio-manager** hub takes the rebalance mandate from you,
delegates the target weights to an **allocation-strategist**, the drift math to a
**drift-monitor**, and the order construction to a **trade-constructor**, then
routes the assembled plan through a **risk-gate** that approves or rejects it
before anything reaches you. The risk-gate is the last word — you never see an
order list it has not cleared.

> ⚠️ **PAPER / SIMULATED ONLY. This is educational, not financial advice.** The
> swarm produces a *simulated* rebalancing plan against a described/CSV book. It
> never places live orders, touches a broker, or moves real money. Do not trade
> on its output. Nothing here is a recommendation to buy or sell any security.

Everything below is based on the actual contents of
`examples/portfolio-rebalancer.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Portfolio operators, advisors, and curious learners who want a **disciplined
rebalancing process** without hand-rolling the math every time: one owner of the
human-facing surface, a specialist who proposes targets, a specialist who
measures drift, a specialist who builds the orders, and a risk-gate that checks
the plan against the mandate before you ever see it.

It is deliberately a **hub-and-spoke**, not a free-for-all: every mandate and
every deliverable passes through the PM, so the point where the plan meets the
risk check (and where the gate sits) lives in exactly one place. Swapping in a
real `market-screener` agent or adding a second risk layer is a few lines of
config.

> This is **not** the same shape as `trading-firm` (a single-trade execution
> desk), `risk-guard` (per-trade execution limits), or `dividend-income-strategist`
> (building an income portfolio). It is **portfolio-level** rebalancing: computing
> target *allocations* across sleeves and the orders that close the drift.

---

## 2. The topology

```
          user
            |
            pm                      (the hub: talks to all four specialists + user)
       /    |    |    \
 allocation- drift-  risk-gate  trade-
 strategist  monitor           constructor
  (claude)   (codex)  (gemini)  (codex)
  (targets)  (drift) (the LIMIT (orders)
                          CHECK)
```

Five agents, one directed flow:

1. **`user` → `pm`** — you send the rebalance *mandate* (the policy
   asset-allocation band, any allowed tactical tilt, the binding Hard limits)
   plus the current book (as a file, a paste, or a location to read), or a plain
   rebalance instruction ("rebalance to policy for Q3").
2. **`pm` → `allocation-strategist`** — the PM sends the mandate + book and asks
   for TARGET weights per asset class (strategic band + tactical tilt, each with a
   one-line rationale and the implied drift).
3. **`allocation-strategist` → `pm`** — the target weights come back.
4. **`pm` → `drift-monitor`** — the PM hands over the current book + the proposed
   target and asks for a per-sleeve DRIFT report (current vs. target, the delta,
   which sleeves breach the band and by how much).
5. **`drift-monitor` → `pm`** — the drift report comes back.
6. **`pm` → `trade-constructor`** — the PM sends the book + target + drift report
   and asks for the concrete rebalance ORDER list (buy/sell side, size, round-trip
   cash needed), sized to close the drift.
7. **`trade-constructor` → `pm`** — the order list comes back.
8. **`pm` → `risk-gate`** — the PM assembles target + drift report + order list
   into one draft and routes it to the risk-gate. The risk-gate is the **limit
   check**: it verifies mandate compliance, concentration, trade size, liquidity,
   and self-consistency. It replies `APPROVE` or `REJECT` (with specific
   defects).
9. **`risk-gate` → `pm`** — on `REJECT`, the PM re-delegates the fix (back to the
   specialists) and re-routes until the risk-gate signs off. On `APPROVE`, the PM
   writes the final list.
10. **`pm` → `user`** — the risk-approved order list is delivered to you.

The routing above is *enforced* by each agent's `can_talk_to` list. The four
specialists **never** talk to `user` (or to each other) — only the PM does. If a
specialist tried to mail `user` directly, the orchestrator bounces it as a
`system` message and files it in `failed/`.

---

## 3. The config, explained

Here is `examples/portfolio-rebalancer.yaml` in full (role bodies abbreviated with
`...` for readability; the structure, names, ACLs, commands, and `pings` are
exact):

```yaml
swarm:
  name: portfolio-rebalancer
  root: ./portfolio-rebalancer-workspace

defaults:
  capture: none              # claude/codex are auto-upgraded to their hook at up
  can_talk_to: []            # tightened per agent below

agents:
  - name: pm
    type: claude
    can_talk_to: [allocation-strategist, drift-monitor, risk-gate, trade-constructor, user]
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: |
          Weekly rebalance is due. Pull the current book ... run the full
          ALLOCATION-STRATEGIST -> DRIFT-MONITOR -> TRADE-CONSTRUCTOR loop, route
          the proposed target + order list to RISK-GATE for approval, and post the
          risk-approved order list to user. ...
        cron: "0 9 * * 1"             # 09:00 every Monday
        when_busy: skip
    role: |
      You are the PORTFOLIO MANAGER (PM) and the only agent who talks to the human
      (user). ... compute NOTHING yourself; present the final order list only after
      the risk-gate clears it. PAPER / SIMULATED only ... Run it like this:
       (1) read the mandate + book, ask ONE clarifying question if scope is
       ambiguous; (2) delegate to ALLOCATION-STRATEGIST; (3) delegate to
       DRIFT-MONITOR; (4) delegate to TRADE-CONSTRUCTOR; (5) assemble the draft and
       route to RISK-GATE -- the limit check -- and re-route until it APPROVES;
       (6) only then post the final order list to user. ...

  - name: allocation-strategist
    type: claude
    can_talk_to: [pm]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the ALLOCATION-STRATEGIST. Propose TARGET weights per asset class ...
      strategic band + tactical tilt, each with rationale and implied drift ...
      Do NOT compute the drift. Do NOT build the orders. Report ONLY to the PM. ...

  - name: drift-monitor
    type: codex
    can_talk_to: [pm]
    command: "codex --yolo"
    role: |
      You are the DRIFT-MONITOR. Compute a per-sleeve DRIFT report ... current vs.
      target, the delta, which sleeves breach the band ... Do NOT propose targets.
      Do NOT build orders. Report ONLY to the PM. ...

  - name: risk-gate
    type: gemini
    can_talk_to: [pm]
    command: "gemini --yolo"
    role: |
      You are the RISK-GATE -- the LIMIT CHECK. Check mandate compliance,
      concentration, trade size, liquidity, self-consistency ... reply APPROVE or
      REJECT. The human must NEVER see an order list you have not signed off.
      Report ONLY to the PM. PAPER / SIMULATED -- checking a plan, never
      authorizing a live trade. ...

  - name: trade-constructor
    type: codex
    can_talk_to: [pm]
    command: "codex --yolo"
    role: |
      You are the TRADE-CONSTRUCTOR. Build the concrete rebalance ORDER list ...
      buy/sell side, size, round-trip cash ... sized to close the drift ... Do NOT
      propose targets. Do NOT judge limits. Report ONLY to the PM. PAPER / SIMULATED
      -- building a plan, never placing a live order. ...
```

Field by field:

### `swarm`
- **`name: portfolio-rebalancer`** — the swarm's name (shows up in `status`,
  logs, sessions).
- **`root: ./portfolio-rebalancer-workspace`** — the parent directory for the
  agents' working directories and mailboxes. Each agent's workdir defaults to
  `portfolio-rebalancer-workspace/<name>` (pm, allocation-strategist,
  drift-monitor, risk-gate, trade-constructor), and orchestrator state goes under
  `portfolio-rebalancer-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless it overrides them.
- **`capture: none`** — the default turn-detection mode. At `up`, the loader
  **auto-upgrades** this for `claude` and `codex` to their natural hook (the
  `validate` run prints three warnings confirming it — see §3 turn-detection
  below). It is a safe floor; every agent states its own `can_talk_to`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Each agent below
  states its own list explicitly.

### `pm` (type: `claude`)
- **`can_talk_to: [allocation-strategist, drift-monitor, risk-gate, trade-constructor, user]`**
  — the PM is the hub and the **only agent that can talk to `user`**. That last
  part is the whole point: keep the human-facing surface to one agent and put the
  risk-gate in front of it.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command, e.g. a
  shell alias. Treat command strings as sensitive; they may embed keys, such as
  market-data or broker aliases.)
- **`pings:`** — the PM carries the swarm's only scheduled ping (see §3 *The
  pings/cron*).
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at
  `up`; the `capture: none` default is auto-upgraded to hook here).

### `allocation-strategist` (type: `claude`)
- **`can_talk_to: [pm]`** — proposes target weights back to the PM and nowhere
  else. It cannot reach the user, the drift-monitor, the risk-gate, or the
  trade-constructor directly.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook (auto-upgraded from `capture: none`).

### `drift-monitor` (type: `codex`)
- **`can_talk_to: [pm]`** — reports the drift table back to the PM only. It
  cannot reach the user or any other spoke.
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`
  (the `capture: none` default auto-upgrades to the notify hook).

### `risk-gate` (type: `gemini`)
- **`can_talk_to: [pm]`** — the gate lives behind the PM: the risk-gate only ever
  talks to the PM, replying `APPROVE` or `REJECT`. It cannot reach the user, so
  its verdict is always relayed through the hub.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **Turn detection:** `gemini` has no completion hook, so it relies on **pane
  polling** — the supervisor watches its pane for turn completion. (This is why
  the `capture: none` default needs no upgrade for gemini; only claude/codex get
  the auto-hook warnings.)

### `trade-constructor` (type: `codex`)
- **`can_talk_to: [pm]`** — builds the order list back to the PM only. It cannot
  reach the user or any other spoke.
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → `notify` hook (auto-upgraded from `capture: none`).

### ACL enforcement

The ACL is **cooperative, not OS isolation** (Decision D15): agents have
filesystem access and *could* write straight into another inbox, but the
orchestrator only ever *releases* and *routes* mail between names on the
sender's `can_talk_to` list. Anything addressed outside that list is bounced back
as a `system` message filed in `failed/`, so a model that forgets the rule
self-corrects in-band. Here that means the four specialists can *only* reach the
PM, and only the PM can reach `user` — the risk-gate's approval is structurally
guaranteed to sit between the draft and the human.

### Per-type turn detection

Turn-completion detection is the system clock (the stop → sweep → route → release
→ nudge loop). It is **per `type`**:
- `claude` (`pm`, `allocation-strategist`) → **Stop hook** — fires when Claude
  finishes a turn.
- `codex` (`drift-monitor`, `trade-constructor`) → **`notify` hook** — fires when
  Codex finishes.
- `gemini` (`risk-gate`) → **pane polling** — the supervisor reads the pane to
  decide the turn ended.

A `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
Claude) means completion never fires and the agent pins "busy" forever — which is
why `command` must launch the same CLI family `type` implies.

### The pings / cron

Only the **PM** has a `pings:` block, and it has exactly one entry:

```yaml
pings:
  - message: |
      Weekly rebalance is due. Pull the current book ... run the full
      ALLOCATION-STRATEGIST -> DRIFT-MONITOR -> TRADE-CONSTRUCTOR loop, route
      the proposed target + order list to RISK-GATE for approval, and post the
      risk-approved order list to user. ...
    cron: "0 9 * * 1"             # 09:00 every Monday
    when_busy: skip
```

- **`cron: "0 9 * * 1"`** — fires at **09:00 every Monday**, injecting the
  weekly-rebalance prompt into the PM's inbox as a nudge.
- **`when_busy: skip`** — if the PM is mid-turn (a live ad-hoc mandate), the ping
  is **skipped** rather than queued on top of the in-flight work. This is what
  keeps a scheduled rebalance from piling onto a live user request.

This is the one piece of self-starting behavior in the swarm; everything else is
event-driven off your mail. See [`configuration.md`](../configuration.md) for the
full `pings:` / `cron:` / `when_busy` grammar.

### What's *not* in this config
- **No `workdir` overrides.** All five agents get the default
  `portfolio-rebalancer-workspace/<name>`, so no mailbox namespacing is needed
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
./agentainer up -c examples/portfolio-rebalancer.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the auto-upgrade warnings for the
   claude/codex agents.
2. Creates the runtime dirs (`portfolio-rebalancer-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/`
   `about.md` contact card *is* the ACL made visible: the PM gets
   `outbox/allocation-strategist/`, `outbox/drift-monitor/`, `outbox/risk-gate/`,
   `outbox/trade-constructor/`, `outbox/user/`; each specialist gets only
   `outbox/pm/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `pm` and
   `allocation-strategist`, the Codex `notify` hook for `drift-monitor` and
   `trade-constructor`; the gemini agent is covered by pane polling.
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
> the whole target→drift→orders→risk-gate loop route mail with no API keys — the
> mechanics are identical. Command strings may embed market-data or broker
> aliases; treat them as sensitive.

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the PM's finished order list as mail (rather
than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/portfolio-rebalancer.yaml
```

This rewrites the `user` contact card in the PM's `outbox/user/about.md` to
`Status: available`, so the PM sees you're reachable. (While away, mail to you is
*held* and the sender gets a `system` ack — nothing bounces.)

Now send the mandate + book into the swarm, addressed to the PM:

```bash
./agentainer send --to pm -c examples/portfolio-rebalancer.yaml \
  "Rebalance mandate for Q3: policy is 60/30/10 equity/credit/cash, tactical \
   tilt up to +5% equity allowed. Current book is in book/positions.csv. Run \
   the full loop and bring me the risk-approved order list."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the PM, then — because the inbox
was empty — **released into `inbox/`** and the PM is **nudged** (the protocol is
re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the rebalance loop advance one turn at a time.
Each arrow is a `stop → sweep → route → release → nudge` cycle:

1. **PM receives the mandate.** It reads `inbox/`, asks its one clarifying
   question if scope is ambiguous, then writes a delegation into
   `outbox/allocation-strategist/`. On stop, that routes to the
   allocation-strategist.
2. **allocation-strategist proposes targets.** It reads its inbox, proposes target
   weights with rationale, and reports back into `outbox/pm/`. On stop, that
   routes to the PM.
3. **PM briefs the drift-monitor.** It writes the book + target into
   `outbox/drift-monitor/`. On stop, that routes to the drift-monitor.
4. **drift-monitor computes drift.** It reads its inbox, writes the per-sleeve
   drift report, and reports back into `outbox/pm/`. On stop, that routes to the
   PM.
5. **PM briefs the trade-constructor.** It writes the book + target + drift report
   into `outbox/trade-constructor/`. On stop, that routes to the
   trade-constructor.
6. **trade-constructor builds orders.** It reads its inbox, writes the rebalance
   order list, and reports back into `outbox/pm/`. On stop, that routes to the PM.
7. **PM assembles the draft and routes to the risk-gate.** The PM writes the
   combined draft into `outbox/risk-gate/`. On stop, that routes to the risk-gate.
8. **risk-gate gates it.** It reads the draft and replies `APPROVE` or `REJECT`
   (with specific defects) into `outbox/pm/`. On `REJECT`, the PM re-delegates the
   fix and re-routes until the risk-gate signs off. On `APPROVE`, the PM writes
   the final order list into `outbox/user/`. On stop, that's delivered to your
   `user` mailbox.
9. **you get the risk-approved list** — visible with `agentainer user inbox`, or
   in the UI.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion. If you
never send anything, the agents just sit in standby (the Monday ping is the only
thing that self-starts the loop).

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/portfolio-rebalancer.yaml
```

```
swarm: portfolio-rebalancer   root: ./portfolio-rebalancer-workspace
  pm                (claude) up idle queue=0 unread=0 talks=allocation-strategist, drift-monitor, risk-gate, trade-constructor, user
  allocation-strategist (claude) up idle queue=0 unread=1 talks=pm
  drift-monitor     (codex)  up idle queue=0 unread=0 talks=pm
  risk-gate         (gemini) up idle queue=0 unread=0 talks=pm
  trade-constructor (codex)  up idle queue=0 unread=0 talks=pm
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/portfolio-rebalancer.yaml          # whole swarm, last 20
./agentainer logs -c examples/portfolio-rebalancer.yaml -f        # follow live
./agentainer logs risk-gate -c examples/portfolio-rebalancer.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox pm -c examples/portfolio-rebalancer.yaml
```

Prints the one released message (headers + body), or `pm: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue pm -c examples/portfolio-rebalancer.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach risk-gate -c examples/portfolio-rebalancer.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or by sending notes
into an agent's inbox.

- **Send a clarification to the PM.** Realized the policy band is 55/35/10, not
  60/30/10? `./agentainer send --to pm -c examples/portfolio-rebalancer.yaml
  "Re-brief the allocation-strategist: policy band is 55/35/10 equity/credit/cash,
  no tactical tilt this quarter."` The PM relays the change down the chain and
  re-routes the draft past the risk-gate.
- **Ask the risk-gate what it rejected.** `./agentainer inbox pm` (or the UI)
  shows the `REJECT` note the PM received — which sleeve, which limit, what's
  wrong — so you can see the gate doing its job.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/portfolio-rebalancer.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/portfolio-rebalancer.yaml     # resume is the default
```

On `up`, Agentainer reads
`portfolio-rebalancer-workspace/.agentainer/sessions.yaml` (written as each agent
finished its first turn) and reattaches the recorded conversations via each type's
native resume: `claude --resume <id>` for the PM and allocation-strategist, `codex
resume <id>` for the drift-monitor and trade-constructor, and the gemini session
via its recorded id. A resumed agent is *not* re-sent the standby prompt (its prior
context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/portfolio-rebalancer.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a market-data / screener spoke
`examples/market-screener.yaml` ships as a sibling for idea generation. To fold a
screener into this swarm, add a sixth agent the PM can brief before the
allocation pass:

```yaml
  - name: market-screener
    type: gemini
    can_talk_to: [pm]
    command: "gemini --yolo"
    role: |
      You are the MARKET-SCREENER. Given the mandate, surface the 2-3 tactical
      tilts worth considering (with evidence) for the allocation-strategist to
      weigh. Report ONLY to the pm.
```

Then add `market-screener` to the PM's `can_talk_to` so it can be briefed.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `allocation-strategist: type: codex` to put the target math on a different model
  than the PM.
- `risk-gate: type: claude` if you want the gate on Claude while keeping gemini
  out.
- Remember: `gemini`/`hermes` rely on **pane polling** (no completion hook), so
  they don't need (and shouldn't be given) a hook-based `capture`.

### Tune the ACL
- To let the `risk-gate` escalate straight to `user` (not only via the PM), add
  `user` to its `can_talk_to`. Mind that this widens the human-facing surface and
  bypasses the PM's single-funnel guarantee — the doc's convention keeps the PM the
  sole `user` contact so the gate always sits in front.
- To make a specialist unreachable from anyone but the PM (already the case here),
  leave its `can_talk_to: [pm]` — that's the one-place-owns-the-gate guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md)
  for mixing model families safely.

### Tune the weekly ping
- Change `cron:` to fire on your rebalance cadence (e.g. daily: `"0 17 * * *"`).
- Switch `when_busy:` from `skip` to `queue` if you'd rather the rebalance wait
  behind a live mandate than be dropped. See [`configuration.md`](../configuration.md).

---

## 10. Tips & footguns

- **Keep the PM the only `user`-facing agent.** Only the PM lists `user` in
  `can_talk_to`. That gives you a single funnel: raw target weights, drift
  reports, and order lists always pass through the risk-gate before they reach
  you. If a specialist tries to mail `user` directly, the orchestrator bounces it
  (ACL) and drops a `system` note in their inbox explaining who they *can* message
  — the model self-corrects in-band.

- **The risk-gate's `REJECT` is the feature, not a failure.** A rejected plan
  means something breached a limit (a concentration ceiling, an oversized single
  trade, a liquidity gap, or an order that doesn't close the drift) and the gate
  caught it. The PM re-delegates and re-routes until `APPROVE`. Don't "fix" this
  by widening ACLs — the loop is how the human stays protected.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch (e.g. a `claude` agent whose `command`
  doesn't launch Claude, or a `gemini` agent whose pane never settles) means
  completion never triggers and the agent pins "busy" forever. `status` showing an
  agent `busy` for a long time with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops — relevant if a specialist and the PM
  chatter past the gate.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/portfolio-rebalancer.yaml
  ./agentainer remove-session -c examples/portfolio-rebalancer.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files (the book you dropped in) or
  your config.

- **Availability shapes the ending.** If `user` is **away** when the PM finishes,
  your order list is *held* (with a `system` "the user is away" ack to the PM)
  rather than lost — read it later with `agentainer user inbox` or flip yourself
  available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions (`--dangerously-skip-permissions`,
  `--yolo`).

- **This is paper / simulated — enforce that yourself.** The swarm never places a
  live order, touches a broker, or moves real money, and every agent is instructed
  to say so. Do not wire it to a live account or treat its output as financial
  advice. The risk-gate checks a *plan*; it does not authorize a *trade*.

- **The weekly ping self-starts, but `when_busy: skip` can drop it.** If a live
  mandate is in flight at 09:00 Monday, the rebalance ping is silently skipped
  rather than queued. If you rely on the weekly plan, either keep `user` quiet
  around the ping, or switch `when_busy` to `queue`.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- `examples/portfolio-rebalancer.yaml` — the config this walkthrough is built on.
- `examples/red-team-blue-team.yaml` — another hub-and-spoke with a gate (the
  adversarial security sibling).
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

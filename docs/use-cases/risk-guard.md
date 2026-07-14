# Use case: Risk guard

A concrete, end-to-end walkthrough of the shipped
`examples/risk-guard.yaml` swarm — a **standalone risk harness that uses the
Agentainer ACL *as a control plane***. A **trader** proposes trades; a
**risk-officer** enforces *hard* limits the trader cannot override and is the
**only** agent that talks to the human; a **compliance** agent audit-logs every
approved and rejected order. The whole point of the swarm is that the trader is
**structurally unable to bypass the risk-officer** — its `can_talk_to` lists
*only* the risk-officer, so the orchestrator physically cannot deliver trader
mail to `user`, to compliance, or to any "exchange".

> ⚠️ **PAPER / SIMULATED ONLY.** This is an **educational** harness, **not
> financial advice**. No order ever reaches a real brokerage — "executed" orders
> go to a simulated blotter (`blotter/book.csv`). By default no real brokerage
> keys are present, and the model never self-approves a money move: every order
> needs a human (`user`) approval routed through the risk-officer before it is
> logged. Trading is risky; nothing here is a recommendation to trade.

Everything below is based on the actual contents of `examples/risk-guard.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you supply the
coding-CLI commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md)
> first, then the four-folders recap in the repo `README.md`. The one-line
> version: an agent **reads a file** to receive mail and **writes a file** to
> send it; the orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Anyone who wants to *see the ACL do real work* as a control plane, or who wants
a teaching artifact for "how do you keep an agent from doing something it
shouldn't." The risk-officer/trader/compliance trio is a clean, legible
demonstration of the design note in `ProjectPlan.md`: **the `can_talk_to` ACL is
a control plane, not just a nicety.** It also works as an introductory trading
*simulator* — propose hypothetical trades, watch hard limits reject the sloppy
ones, and practice human-in-the-loop approval against a simulated book.

It deliberately does **not** overlap with the financial *analysis* examples
(`fp-and-a-analyst`, `forecast-analyst`, `sql-analyst`, `data-analyst`,
`cloud-cost-optimizer`, `competitive-intel`, `research`,
`white-paper-research`) — those produce *insight*; this one enforces *limits*
and proves the trader cannot escape them. It also stays clear of the parallel
trading-desk topics (trading-firm, equity-research, earnings-call-analyst,
macro-strategy-desk, credit-ratings-monitor, quant-factor-miner,
backtesting-auditor, market-screener, news-sentiment-desk,
regulatory-disclosure-tracker, options-strategist, portfolio-rebalancer,
dividend-income-strategist, crypto-onchain-research, prediction-market-scout) —
those are research/desk *simulations*; this is a *guardrail harness* whose
novelty is the ACL-as-enforcement, with limits hard-coded in the role.

---

## 2. The topology

```
                 user
                  |
            risk-officer                 (HUB / control plane: the ONLY agent that
               /      \                    talks to user; enforces the hard limits)
          trader    compliance           (spokes: each talks ONLY to risk-officer)
          (codex)   (claude)
```

Three agents, one directed, *ACL-enforced* flow:

1. **`trader` → `risk-officer`** — the trader proposes a trade (instrument, side,
   size, price, stop, rationale). It **cannot** address anyone else; its
   `can_talk_to` is `[risk-officer]` only.
2. **`risk-officer` checks the proposal** against the hard limits it holds
   (max position size, max notional, per-name concentration, stop-loss, forbidden
   instruments). A limit breach is an automatic **REJECT** — no human involved.
3. **`risk-officer` → `compliance`** — every proposal (and every eventual outcome)
   is forwarded to compliance for the append-only audit log.
4. **`risk-officer` → `user`** — only proposals that *pass* the limits are
   forwarded to you as an **APPROVAL REQUEST**. The human's `APPROVE`/`REJECT` is
   final; the risk-officer never self-approves.
5. **`user` → `risk-officer`** — you reply `APPROVE` or `REJECT`.
6. **`risk-officer` → `trader` + `compliance`** — on `APPROVE`, the fill is logged
   to the simulated `blotter/book.csv` and the trader is told FILLED; on `REJECT`,
   the trader is told NOT filled and compliance marks it `REJECTED-HUMAN`.

The routing above is *enforced* by each agent's `can_talk_to` list. The trader
**never** talks to `user` or to `compliance` — only to the risk-officer. If the
trader tried to mail `user` directly, the orchestrator bounces it as a `system`
message and files it in `failed/`. There is no configuration path by which a
proposal reaches the human without first clearing the risk-officer's limits.

---

## 3. The config, explained

Here is `examples/risk-guard.yaml` in full (role bodies abbreviated with `...`
for readability; the structure, names, ACLs, and commands are exact):

```yaml
swarm:
  name: risk-guard
  root: ./risk-guard-workspace

defaults:
  capture: none              # claude/codex are auto-upgraded to their hook at up
  can_talk_to: []            # tightened per agent below

agents:
  - name: risk-officer
    type: claude
    can_talk_to: [trader, compliance, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the RISK OFFICER and the control plane ... You enforce HARD limits
      that a trader CANNOT override ... MAX POSITION SIZE 1,000 shares; MAX NOTIONAL
      $50,000; PER-NAME CONCENTRATION 15%; STOP-LOSS <= 8% adverse; FORBIDDEN
      instruments (options/futures/leveraged ETFs/crypto) ... A limit breach is an
      automatic REJECT, no human involved. A passing proposal goes to compliance
      for the log AND to user as an APPROVAL REQUEST; on APPROVE you log the fill
      to the simulated blotter. You never self-approve. ...

  - name: trader
    type: codex
    can_talk_to: [risk-officer]
    command: "codex --yolo"
    role: |
      You are the TRADER. You PROPOSE trades only ... Your can_talk_to lists ONLY
      the risk-officer; the orchestrator bounces anything else. Propose plain cash
      equities only, <= 1,000 shares and <= $50,000 notional, with a stop <= 8%
      adverse. Never claim an order executed -- only the risk-officer confirms
      fills, after human approval. ...

  - name: compliance
    type: claude
    can_talk_to: [risk-officer]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the COMPLIANCE audit logger ... The risk-officer is the only agent
      that messages you. Keep an append-only compliance/audit.md of every order
      event: PROPOSED, APPROVED-BY-HUMAN, REJECTED-BY-LIMIT, REJECTED-BY-HUMAN.
      The audit trail is the point. ...
```

Field by field:

### `swarm`
- **`name: risk-guard`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./risk-guard-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent's workdir defaults to
  `risk-guard-workspace/<name>` (risk-officer, trader, compliance), and
  orchestrator state goes under `risk-guard-workspace/.agentainer/` (never commit
  it). The simulated blotter lives at `risk-guard-workspace/blotter/book.csv` and
  the audit log at `risk-guard-workspace/compliance/audit.md` (the agents' own
  working files, not orchestrator state).

### `defaults`
Applied to every agent unless it overrides them.
- **`capture: none`** — the default turn-detection mode. At `up`, the loader
  **auto-upgrades** this for `claude` and `codex` to their natural hook (the
  `validate` run prints warnings confirming it). It is a safe floor; every agent
  states its own `can_talk_to`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Each agent below
  states its own list explicitly.

### `risk-officer` (type: `claude`)
- **`can_talk_to: [trader, compliance, user]`** — the risk-officer is the hub and
  the **only agent that can talk to `user`**. That single fact is the whole
  control plane: the trader can only ever reach it, so every proposal and every
  approval request funnels through one place that holds the hard limits.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command (substitute your own; treat `command` strings as sensitive — they may
  embed brokerage keys via shell aliases).
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`;
  the `capture: none` default is auto-upgraded to hook here).

### `trader` (type: `codex`)
- **`can_talk_to: [risk-officer]`** — this is the load-bearing line. The trader
  can address *exactly one* name. It cannot reach `user`, cannot reach
  `compliance`, and cannot reach any exchange; the orchestrator has nowhere to
  deliver such mail, so the ACL physically prevents the bypass. The trader can
  only **propose**.
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`
  (the `capture: none` default auto-upgrades to the notify hook).

### `compliance` (type: `claude`)
- **`can_talk_to: [risk-officer]`** — the audit logger only ever talks to the
  risk-officer. It never reaches the trader or the user, so the log stays a clean
  third-party record of what the risk-officer forwarded.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook (auto-upgraded from `capture: none`).

### ACL enforcement — the control plane

The ACL is **cooperative, not OS isolation** (Decision D15): agents have
filesystem access and *could* write straight into another inbox, but the
orchestrator only ever *releases* and *routes* mail between names on the
sender's `can_talk_to` list. Anything addressed outside that list is bounced back
as a `system` message filed in `failed/`, so a model that forgets the rule
self-corrects in-band. Here it does something stronger than convenience — it
**removes the capability**: the trader's list has exactly one entry, so there is
no address it could even attempt that reaches the human or an exchange. The
risk-officer is the sole funnel, and it holds the hard limits in its role text,
so a trader proposal cannot become a fill without (a) clearing every limit and
(b) a human `APPROVE`.

### Per-type turn detection

Turn-completion detection is the system clock (the stop → sweep → route → release
→ nudge loop). It is **per `type`**:
- `claude` (`risk-officer`, `compliance`) → **Stop hook** — fires when Claude
  finishes a turn.
- `codex` (`trader`) → **`notify` hook** — fires when Codex finishes.

A `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
Claude) means completion never fires and the agent pins "busy" forever — which is
why `command` must launch the same CLI family `type` implies.

### What's *not* in this config
- **No `pings:` block.** Unlike the FP&A example, this harness is fully
  event-driven: it waits for your proposals and approvals. Add a `pings:` to the
  risk-officer if you want a standing "reconcile the simulated book" nudge.
- **No per-agent `capture` overrides.** The `defaults: capture: none` is
  auto-upgraded to the type's hook for claude/codex.
- **No real brokerage keys.** The simulated blotter is a CSV the risk-officer
  appends to; no `command` here connects to a live venue. If you wire a real
  brokerage alias into `command`, treat that string as a secret and never commit
  it.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/risk-guard.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the `capture: none` auto-upgrade
   warnings for the claude/codex agents.
2. Creates the runtime dirs (`risk-guard-workspace/.agentainer/…`: log, queue,
   run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/`
   `about.md` contact card *is* the ACL made visible: the risk-officer gets
   `outbox/trader/`, `outbox/compliance/`, `outbox/user/`; the trader gets only
   `outbox/risk-officer/`; compliance gets only `outbox/risk-officer/`. The
   trader literally has no `outbox/user/` to write into — that is the enforcement
   made tangible.
4. **Installs per-type turn detection** — the Claude Stop hook for `risk-officer`
   and `compliance`, the Codex `notify` hook for `trader`.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints. The `serve` line gives you
the mail-app control-plane UI. Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents that may run with elevated permissions, so it must **never** be
exposed on `0.0.0.0` without a token. See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole propose → risk-check → approve → blotter loop route mail with no API
> keys — the mechanics are identical.

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* approval requests and fills as mail (rather
than have them held), turn yourself available first:

```bash
./agentainer user available -c examples/risk-guard.yaml
```

This rewrites the `user` contact card in the risk-officer's `outbox/user/about.md`
to `Status: available`, so the risk-officer sees you're reachable. (While away,
mail to you is *held* and the risk-officer gets a `system` ack — nothing bounces.)

Now send a trade proposal into the swarm, addressed to the **trader**:

```bash
./agentainer send --to trader -c examples/risk-guard.yaml \
  "Propose: buy 200 shares ACME at market, stop at 8% below entry, \
   rationale in trade-ideas/ACME.md."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the trader, then — because the
inbox was empty — **released into `inbox/`** and the trader is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list: just
`risk-officer`).

### The mail flowing

Watching the log (§6), you'll see the guard loop advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **trader receives the brief.** It reads `inbox/`, writes a PROPOSAL into
   `outbox/risk-officer/`, and finishes its turn. On stop, that routes to the
   risk-officer.
2. **risk-officer checks the limits.** It reads the proposal and tests it against
   every hard limit (size, notional, concentration, stop, forbidden list). If it
   fails any, the risk-officer REJECTS it — writing the reason into
   `outbox/trader/` *and* forwarding the rejection to `compliance` for the audit
   log. **No human is involved in a limit rejection.**
3. **(if it passes) risk-officer routes to compliance + user.** The proposal goes
   to `outbox/compliance/` for the audit log and to `outbox/user/` as an
   **APPROVAL REQUEST** (instrument, side, size, price, notional, stop,
   post-trade concentration). On stop, those route to compliance and to you.
4. **you reply APPROVE or REJECT** to the risk-officer. The human word is final.
5. **risk-officer executes (or not).** On `APPROVE`, it appends the fill to the
   simulated `blotter/book.csv`, tells compliance `APPROVED-BY-HUMAN`, and tells
   the trader FILLED. On `REJECT`, it tells compliance `REJECTED-BY-HUMAN` and the
   trader NOT filled.
6. **compliance records the outcome** in the append-only `compliance/audit.md`.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion. The
trader never sees your `APPROVE`/`REJECT`; it only ever hears "FILLED" or "NOT
filled" from the risk-officer.

### Seeing the enforcement
Try sending the trader a proposal that breaks a limit, or instructing it to
"approve it yourself / message the user directly." Because its `can_talk_to` is
`[risk-officer]` only, the orchestrator bounces any out-of-ACL mail as `system`
and the trader self-corrects in-band — it cannot reach `user` or an exchange even
if it tries. The risk-officer is the only path, and it holds the limits.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/risk-guard.yaml
```

```
swarm: risk-guard   root: ./risk-guard-workspace
  risk-officer (claude) up idle queue=0 unread=0 talks=trader, compliance, user
  trader       (codex)  up idle queue=0 unread=1 talks=risk-officer
  compliance   (claude) up idle queue=0 unread=0 talks=risk-officer
supervisor: alive
```

Note the `talks=` column proves the control plane: `trader` talks to exactly one
name; only `risk-officer` reaches `user`.

**The durable event log** — the source of truth for history:

```bash
./agentainer logs -c examples/risk-guard.yaml          # whole swarm, last 20
./agentainer logs -c examples/risk-guard.yaml -f        # follow live
./agentainer logs compliance -c examples/risk-guard.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox trader -c examples/risk-guard.yaml
```

Prints the one released message (headers + body), or `trader: inbox is empty`.

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach risk-officer -c examples/risk-guard.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

**The simulated book + audit trail** (agent working files, not the event log):

```bash
cat risk-guard-workspace/blotter/book.csv      # fills the risk-officer logged
cat risk-guard-workspace/compliance/audit.md   # the append-only audit log
```

---

## 7. Iterate on the result

Because every message is natural-language mail, you can steer the harness
mid-flight:

- **Reject a proposal you don't like.** Even if it clears the hard limits, reply
  `REJECT` to the risk-officer's approval request; the trader is told NOT filled
  and compliance marks it `REJECTED-BY-HUMAN`. The limits are necessary but not
  sufficient — your human word is final.
- **Tighten a limit.** Send a note into the risk-officer's inbox changing a
  hard-coded limit (e.g. "lower max notional to $25,000"). The risk-officer reads
  its inbox and applies it on the next proposal. (The limits live in its role
  text; a note is how you adjust them live without editing the file.)
- **Ask compliance what it logged.** `./agentainer inbox risk-officer` (or the UI)
  shows the audit-forward the risk-officer sent — every proposed order and how it
  resolved.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live.

When you're done, tear it down:

```bash
./agentainer down -c examples/risk-guard.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/risk-guard.yaml     # resume is the default
```

On `up`, Agentainer reads `risk-guard-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the
risk-officer and compliance, `codex resume <id>` for the trader. A resumed agent
is *not* re-sent the standby prompt (its prior context is restored). Pass
`--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/risk-guard.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a `pings:` reconciler
The risk-officer currently waits for your input. Add a standing reconciliation
nudge so the simulated book is summarized periodically:

```yaml
  - name: risk-officer
    type: claude
    can_talk_to: [trader, compliance, user]
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: |
          Summarize the simulated book: total notional, per-name concentration
          (flag anything over 15%), open stops, and any pending approvals. Post
          the summary to user.
        cron: "0 9 * * 1-5"          # 09:00 on weekdays
        when_busy: skip
```

### Tighten or loosen the hard limits
The limits are hard-coded in the risk-officer's `role:` (1,000 shares / $50,000
notional / 15% concentration / 8% stop / forbidden instruments). Edit that text
directly — or send the risk-officer a live note mid-run (see §7). Keep the trader
unable to override them: the trader's `can_talk_to` stays `[risk-officer]`, so it
has no channel to relax a limit itself.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family:
- `trader: type: claude` (or `hermes`/`gemini`) to put proposals on a different
  model than the risk-officer.
- `compliance: type: codex` if you want the audit logger on Codex.
- Remember: `gemini`/`hermes` rely on **pane polling** (no completion hook), so
  they don't need (and shouldn't be given) a hook-based `capture`.

### Tune the ACL
The control-plane property lives entirely in the three `can_talk_to` lists:
- `trader: [risk-officer]` — the load-bearing line. **Do not add `user` or
  `compliance` here** or the trader can bypass the risk-officer (that defeats the
  harness's entire point).
- `compliance: [risk-officer]` — keeps the audit log a clean third-party record.
  Adding `user` would let compliance message you directly; that's a reasonable
  variant but widens the human-facing surface.
- `risk-officer: [trader, compliance, user]` — the only agent that reaches you.
  This is the funnel that guarantees every proposal clears the limits before you
  see it.

See [`delegation-pipeline.md`](./delegation-pipeline.md) for broader hub-and-spoke
routing patterns, and [`red-team-blue-team.md`](./red-team-blue-team.md) for the
other shipped "ACL-as-control-plane" showcase (a range-control hub that prevents
two adversary spokes from coordinating directly).

### Wire a real venue (advanced — NOT recommended for the default harness)
The simulated blotter is a CSV the risk-officer appends to. To make fills real you
would put a real brokerage alias in `command` — **treat that string as a secret,
never commit it, and understand the PAPER/SIMULATED guardrails below no longer
apply.** The ACL control plane still holds: the trader still cannot reach the
venue, because it can still only address the risk-officer.

---

## 10. Tips & footguns

- **The trader's `can_talk_to: [risk-officer]` is the feature.** It is not a
  suggestion; it is the ACL making the bypass *impossible*. If you widen it (add
  `user`, add `compliance`), you have removed the control plane and the harness no
  longer demonstrates what it's for. Keep it to one name.

- **Limit rejections need no human.** A proposal that breaks a hard limit is
  rejected by the risk-officer alone and logged by compliance as
  `REJECTED-BY-LIMIT`. Only *passing* proposals reach you as `APPROVAL REQUEST`.
  Don't expect to "approve" a trade the limits already killed.

- **Human-in-the-loop is mandatory for a fill.** Even a proposal that clears every
  limit is not executed until you reply `APPROVE`. The model never self-approves a
  money move; the risk-officer only logs to `blotter/book.csv` after your word.
  This is the core safety property — keep `user` away-gated behind the
  risk-officer.

- **PAPER / SIMULATED ONLY.** No order in this default harness touches a real
  brokerage. The "blotter" is a local CSV. If you add real keys to `command`, the
  ACL still prevents the trader from self-executing, but the paper/simulated
  disclaimer no longer holds — do that only with full understanding and never
  commit secrets. This is educational, not financial advice.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch means completion never triggers and the
  agent pins "busy" forever. `status` showing an agent `busy` for a long time with
  `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/risk-guard.yaml
  ./agentainer remove-session -c examples/risk-guard.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files (your `trade-ideas/` notes, the
  blotter, the audit log) or your config.

- **`command` strings are sensitive.** They may embed brokerage keys via shell
  aliases. Don't print or commit them.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions (`--dangerously-skip-permissions`,
  `--yolo`).

- **Availability shapes the ending.** If `user` is **away** when the risk-officer
  forwards an approval request, your request is *held* (with a `system` "the user
  is away" ack to the risk-officer) rather than lost — read it later with
  `agentainer user inbox` or flip yourself available and it's delivered.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`red-team-blue-team.md`](./red-team-blue-team.md) — the sibling "ACL-as-control-plane" showcase.
- `examples/risk-guard.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14; the ACL as a
  control plane is a recurring design note).

# Use case: Options strategist

A concrete, end-to-end walkthrough of the shipped
`examples/options-strategist.yaml` swarm — a **defined-risk options strategy
assembly line** that turns a plain-English mandate ("I'm mildly bullish into
earnings, cap my downside, ~$400 budget") into a greeks-checked, vol-aware,
risk-gated strategy a non-trader can understand. An **options-lead** hub takes
the request from you, delegates the structure to a **strategy-builder**, the
greeks/payoff to a **greeks-calculator**, the vol read to a **vol-analyst**, and
routes the assembled proposal through a **risk-gate** that enforces
*defined-risk only* before anything reaches the human. The risk desk is the last
word — the human never sees a strategy it has not cleared (or explicitly approved
with a naked component disclosed).

> **Educational only — not financial advice.** This swarm is a teaching/analysis
> tool. It runs on **paper / simulated** positions only. It does **not** place
> orders, connect to a broker, or manage real money. Nothing here is a
> recommendation to buy or sell any security. Options are risky; some structures
> can lose more than the premium. Consult a licensed professional before trading.

Everything below is based on the actual contents of
`examples/options-strategist.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Retail traders, students, and operators who want to *understand* an options
strategy — its greeks, its payoff, its vol sensitivity, and exactly how much they
can lose — without wiring up a pricing library or a broker. The swarm encodes the
discipline that makes options safe to look at: one owner of the human-facing
surface, a builder who only ever proposes defined-risk structures, a calculator
that checks the math, a vol desk that checks cheapness, and a **risk gate** that
blocks uncapped-loss positions unless you explicitly sign off.

It is deliberately a **hub-and-spoke**, not a free-for-all: every request and
every deliverable passes through options-lead, so the point where the strategy
meets the risk gate lives in exactly one place. (This is **derivatives/options**-
specific — distinct from the equities desk in `examples/trading-firm.yaml`, which
trades shares rather than constructing option spreads.)

---

## 2. The topology

```
        greeks-calculator --\
        strategy-builder  --\
        vol-analyst       --> options-lead <--> user
        risk-gate         --/        (the COMPLIANCE DESK -- clears before user sees it)
```

Five agents, one directed flow:

1. **`user` → `options-lead`** — you send a mandate: a view on the underlying
   (bullish/bearish/neutral), the event ("into earnings"), a risk budget (max net
   debit or a max-loss ceiling), and/or "show me a defined-risk way to express X".
2. **`options-lead` → `strategy-builder`** — options-lead sends the view + budget +
   event and asks for a concrete structure: the legs (call/put, long/short,
   strike, expiry), the net debit/credit, max gain, max loss, and break-even(s).
3. **`strategy-builder` → `options-lead`** — the structure comes back (defaulting
   to defined-risk spreads; if a naked variant is the only fit, it flags it).
4. **`options-lead` → `greeks-calculator`** — options-lead sends the proposed
   structure and asks for the full greeks at the current underlying and at +/-5%,
   +/-10%, and at expiry, plus the payoff in words (this is the math check).
5. **`greeks-calculator` → `options-lead`** — the greeks + payoff profile come back,
   and it explicitly names any uncapped-loss leg.
6. **`options-lead` → `vol-analyst`** — options-lead sends the structure and asks
   whether it is cheap or rich to fair vol (net long/short vega, event vol risk).
7. **`vol-analyst` → `options-lead`** — the vol read comes back.
8. **`options-lead` → `risk-gate`** — options-lead assembles the structure + greeks
   + vol read into one proposal and routes it to risk-gate. Risk-gate enforces
   **defined-risk only**: a finite max loss passes; a naked short (uncapped loss)
   is **BLOCKED** unless options-lead has forwarded an explicit `user` approval
   for that specific component. It replies `CLEAR` or `BLOCK`/`BOUNCE`.
9. **`risk-gate` → `options-lead`** — on `BLOCK` (no approval), options-lead
   re-briefs strategy-builder for a defined-risk variant and re-routes until
   risk-gate `CLEAR`s. On `CLEAR`, options-lead writes the final strategy.
10. **`options-lead` → `user`** — the gated strategy (max loss, max gain,
    break-even(s), greeks summary, vol read, and the educational disclaimer) is
    delivered to you.

The routing above is *enforced* by each agent's `can_talk_to` list. The four
specialists **never** talk to `user` (or to each other) — only options-lead does.
If a specialist tried to mail `user` directly, the orchestrator bounces it as a
`system` message and files it in `failed/`.

---

## 3. The config, explained

Here is `examples/options-strategist.yaml` in full (role bodies abbreviated with
`...` for readability; the structure, names, ACLs, and commands are exact):

```yaml
swarm:
  name: options-strategist
  root: ./options-strategist-workspace

defaults:
  capture: none              # claude/codex are auto-upgraded to their hook at up
  can_talk_to: []            # tightened per agent below

agents:
  - name: options-lead
    type: claude
    can_talk_to: [greeks-calculator, strategy-builder, vol-analyst, risk-gate, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the OPTIONS-LEAD and the only agent who talks to the human (user). ...
      (1) read the mandate, ask ONE clarifying question if scope is ambiguous;
       (2) delegate to STRATEGY-BUILDER; (3) delegate to GREEKS-CALCULATOR;
       (4) delegate to VOL-ANALYST; (5) assemble the proposal and route to
       RISK-GATE -- the compliance desk -- and re-route until it CLEARs (or the
       user explicitly approves a disclosed naked component); (6) only then post
       the final strategy to user. Always include the educational disclaimer. ...

  - name: greeks-calculator
    type: codex
    can_talk_to: [options-lead]
    command: "codex --yolo"
    role: |
      You are the GREEKS-CALCULATOR. Compute the greeks and payoff profile
      NUMERICALLY ... delta/gamma/vega/theta/rho at current and +/- moves, payoff
      at key underlying prices, max gain/loss, break-even(s) ... explicitly name
      any UNCAPPED-loss leg. Report ONLY to options-lead. ...

  - name: strategy-builder
    type: gemini
    can_talk_to: [options-lead]
    command: "gemini --yolo"
    role: |
      You are the STRATEGY-BUILDER. Construct a concrete, named DEFINED-RISK
      structure ... legs, net debit/credit, max gain/loss, break-even(s) ... if a
      naked short is the only fit, flag it as out-of-policy without approval.
      Report ONLY to options-lead. ...

  - name: vol-analyst
    type: claude
    can_talk_to: [options-lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the VOL-ANALYST. Read the vol context and say whether the structure
      is cheap/rich to fair vol ... net long/short vega, event vol risk, skew.
      Report ONLY to options-lead. ...

  - name: risk-gate
    type: gemini
    can_talk_to: [options-lead]
    command: "gemini --yolo"
    role: |
      You are the RISK-GATE -- the COMPLIANCE DESK. DEFINED-RISK ONLY: finite max
      loss passes; a NAKED SHORT (uncapped loss) is BLOCKED unless options-lead
      forwards an EXPLICIT user approval ... reply CLEAR or BLOCK/BOUNCE. The human
      must NEVER see a strategy you have not cleared or approved-with-disclosure.
      Report ONLY to options-lead. ...
```

Field by field:

### `swarm`
- **`name: options-strategist`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./options-strategist-workspace`** — the parent directory for the
  agents' working directories and mailboxes. Each agent's workdir defaults to
  `options-strategist-workspace/<name>` (options-lead, greeks-calculator,
  strategy-builder, vol-analyst, risk-gate), and orchestrator state goes under
  `options-strategist-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless it overrides them.
- **`capture: none`** — the default turn-detection mode. At `up`, the loader
  **auto-upgrades** this for `claude` and `codex` to their natural hook (the
  `validate` run prints warnings confirming it). It is a safe floor; every agent
  states its own `can_talk_to`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Each agent below
  states its own list explicitly. **No per-agent `capture` is set** — the
  `defaults: capture: none` is auto-upgraded for `claude`/`codex`, and `gemini`
  agents use pane polling.

### `options-lead` (type: `claude`)
- **`can_talk_to: [greeks-calculator, strategy-builder, vol-analyst, risk-gate, user]`**
  — options-lead is the hub and the **only agent that can talk to `user`**. That
  last part is the whole point: keep the human-facing surface to one agent and put
  the risk gate in front of it.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys, including a
  broker/screening alias.)
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`;
  the `capture: none` default is auto-upgraded to hook here).

### `greeks-calculator` (type: `codex`)
- **`can_talk_to: [options-lead]`** — reports the greeks + payoff back to
  options-lead and nowhere else. It cannot reach the user, the builder, the vol
  desk, or the risk gate directly.
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`
  (the `capture: none` default auto-upgrades to the notify hook).

### `strategy-builder` (type: `gemini`)
- **`can_talk_to: [options-lead]`** — receives the mandate from options-lead and
  returns the structure to options-lead only. It never touches the user.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **Turn detection:** `gemini` has no completion hook, so it relies on **pane
  polling** — the supervisor watches its pane for turn completion.

### `vol-analyst` (type: `claude`)
- **`can_talk_to: [options-lead]`** — receives the structure from options-lead and
  returns the vol read to options-lead only. It never touches the user.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook (auto-upgraded from `capture: none`).

### `risk-gate` (type: `gemini`)
- **`can_talk_to: [options-lead]`** — the gate lives behind options-lead: it only
  ever talks to options-lead, replying `CLEAR` or `BLOCK`/`BOUNCE`. It cannot
  reach the user, so its verdict is always relayed through the hub.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **Turn detection:** `gemini` → pane polling.
- **The defined-risk policy:** `BLOCK` any naked short (uncapped loss) **unless**
  options-lead forwards an explicit `user` approval for that specific component. On
  such an approval it may `CLEAR`, but requires options-lead to disclose the
  uncapped loss prominently to the user.

### ACL enforcement

The ACL is **cooperative, not OS isolation** (Decision D15): agents have
filesystem access and *could* write straight into another inbox, but the
orchestrator only ever *releases* and *routes* mail between names on the sender's
`can_talk_to` list. Anything addressed outside that list is bounced back as a
`system` message filed in `failed/`, so a model that forgets the rule
self-corrects in-band. Here that means the four specialists can *only* reach
options-lead, and only options-lead can reach `user` — the risk gate is
structurally guaranteed to sit between the draft and the human.

### Per-type turn detection

Turn-completion detection is the system clock (the stop → sweep → route → release
→ nudge loop). It is **per `type`**:
- `claude` (`options-lead`, `vol-analyst`) → **Stop hook** — fires when Claude
  finishes a turn.
- `codex` (`greeks-calculator`) → **`notify` hook** — fires when Codex finishes.
- `gemini` (`strategy-builder`, `risk-gate`) → **pane polling** — the supervisor
  reads the pane to decide the turn ended.

A `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
Claude) means completion never fires and the agent pins "busy" forever — which is
why `command` must launch the same CLI family `type` implies.

### What's *not* in this config
- **No `workdir` overrides.** All five agents get the default
  `options-strategist-workspace/<name>`, so no mailbox namespacing is needed. For
  the shared-workdir case, see [`custom-workspace.md`](./custom-workspace.md).
- **No per-agent `capture` overrides.** The `defaults: capture: none` is
  auto-upgraded to the type's hook for claude/codex; gemini uses pane polling.
- **No `pings:` block.** Unlike some other swarms, this one has no scheduled
  cron — options work is event-driven off your mandate. Add a `pings:` to any
  agent (e.g. a Friday "scan for defined-risk earnings plays") if you want
  self-starting behavior; see [`configuration.md`](../configuration.md).
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/options-strategist.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the `capture: none` auto-upgrade
   warnings for the claude/codex agents.
2. Creates the runtime dirs (`options-strategist-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/`
   `about.md` contact card *is* the ACL made visible: options-lead gets
   `outbox/greeks-calculator/`, `outbox/strategy-builder/`, `outbox/vol-analyst/`,
   `outbox/risk-gate/`, `outbox/user/`; each specialist gets only
   `outbox/options-lead/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `options-lead`
   and `vol-analyst`, the Codex `notify` hook for `greeks-calculator`; the gemini
   agents are covered by pane polling.
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
> the whole build→greeks→vol→gate loop route mail with no API keys — the
> mechanics are identical.

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* options-lead's finished strategy as mail
(rather than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/options-strategist.yaml
```

This rewrites the `user` contact card in options-lead's `outbox/user/about.md`
to `Status: available`, so options-lead sees you're reachable. (While away, mail
to you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send your mandate into the swarm, addressed to options-lead:

```bash
./agentainer send --to options-lead -c examples/options-strategist.yaml \
  "I'm mildly bullish on XYZ into earnings but want to cap my downside. \
   Net debit budget ~$400/contract. Show me a defined-risk structure."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for options-lead, then — because the
inbox was empty — **released into `inbox/`** and options-lead is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The naked-short approval path

If a strategy you actually want requires a naked short (for example a short put
you're willing to take assignment on), the risk gate will **BLOCK** it by default.
You can override by sending an explicit approval to options-lead, naming the exact
component:

```bash
./agentainer send --to options-lead -c examples/options-strategist.yaml \
  "I APPROVE the naked short put leg in the prior proposal. I understand the \
   uncapped downside and accept assignment risk. Proceed and disclose it clearly."
```

options-lead forwards that verbatim to risk-gate; risk-gate may then `CLEAR` but
requires options-lead to disclose the uncapped loss prominently in the strategy
it posts to you. **Without your explicit approval, the gate stays closed** — that
is the safety invariant.

### The mail flowing

Watching the log (§6), you'll see the strategy loop advance one turn at a time.
Each arrow is a `stop → sweep → route → release → nudge` cycle:

1. **options-lead receives the mandate.** It reads `inbox/`, asks its one
   clarifying question if scope is ambiguous, then writes a delegation into
   `outbox/strategy-builder/`. On stop, that routes to the strategy-builder.
2. **strategy-builder proposes the structure.** It reads its inbox, builds a
   defined-risk spread, and reports back into `outbox/options-lead/`. On stop,
   that routes to options-lead.
3. **options-lead briefs the greeks-calculator.** It writes the structure into
   `outbox/greeks-calculator/`. On stop, that routes to the greeks-calculator.
4. **greeks-calculator returns the profile.** It reads its inbox, computes the
   greeks + payoff (naming any uncapped leg), and reports back into
   `outbox/options-lead/`. On stop, that routes to options-lead.
5. **options-lead briefs the vol-analyst.** It writes the structure into
   `outbox/vol-analyst/`. On stop, that routes to the vol-analyst.
6. **vol-analyst returns the vol read.** It reads its inbox and reports back into
   `outbox/options-lead/`. On stop, that routes to options-lead.
7. **options-lead assembles the proposal and routes to risk-gate.** It writes the
   combined proposal into `outbox/risk-gate/`. On stop, that routes to risk-gate.
8. **risk-gate gates it.** It reads the proposal and replies `CLEAR` or
   `BLOCK`/`BOUNCE` into `outbox/options-lead/`. On `BLOCK` (no approval),
   options-lead re-briefs strategy-builder for a defined-risk variant and
   re-routes until risk-gate `CLEAR`s. On `CLEAR`, options-lead writes the final
   strategy into `outbox/user/`. On stop, that's delivered to your `user` mailbox.
9. **you get the gated strategy** — visible with `agentainer user inbox`, or in
   the UI — including max loss, max gain, break-even(s), the greeks summary, the
   vol read, and the "educational, not financial advice" caveat.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/options-strategist.yaml
```

```
swarm: options-strategist   root: ./options-strategist-workspace
  options-lead       (claude) up idle queue=0 unread=0 talks=greeks-calculator, strategy-builder, vol-analyst, risk-gate, user
  greeks-calculator  (codex)  up idle queue=0 unread=1 talks=options-lead
  strategy-builder   (gemini) up idle queue=0 unread=0 talks=options-lead
  vol-analyst        (claude) up idle queue=0 unread=0 talks=options-lead
  risk-gate          (gemini) up idle queue=0 unread=0 talks=options-lead
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/options-strategist.yaml          # whole swarm, last 20
./agentainer logs -c examples/options-strategist.yaml -f        # follow live
./agentainer logs risk-gate -c examples/options-strategist.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox options-lead -c examples/options-strategist.yaml
```

Prints the one released message (headers + body), or `options-lead: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue options-lead -c examples/options-strategist.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach risk-gate -c examples/options-strategist.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails the exact structure. Because every message is
natural-language mail, you can steer the swarm mid-flight through the `user`
mailbox or by sending notes into an agent's inbox.

- **Send a clarification to options-lead.** Realized you're neutral, not bullish?
  `./agentainer send --to options-lead -c examples/options-strategist.yaml "Re-
  brief the builder: I'm actually neutral into the print, prefer an iron condor
  if it fits the budget."` options-lead relays the change down the chain and
  re-routes the proposal past the risk gate.
- **Ask the risk gate what it blocked.** `./agentainer inbox options-lead` (or the
  UI) shows the `BLOCK` note options-lead received — which leg was uncapped, what
  defined-risk alternative to use — so you can see the gate doing its job.
- **Approve a naked component.** See §5's approval path: an explicit, named
  approval unlocks the gate for that one component, with mandatory disclosure.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want a different framing), tear it down:

```bash
./agentainer down -c examples/options-strategist.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/options-strategist.yaml     # resume is the default
```

On `up`, Agentainer reads
`options-strategist-workspace/.agentainer/sessions.yaml` (written as each agent
finished its first turn) and reattaches the recorded conversations via each type's
native resume: `claude --resume <id>` for options-lead and vol-analyst,
`codex resume <id>` for greeks-calculator, and the gemini sessions (strategy-
builder, risk-gate) via their recorded ids. A resumed agent is *not* re-sent the
standby prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/options-strategist.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a scheduled scan
Unlike the FP&A example, this swarm ships with no `pings:`. To make it self-start
(e.g. a Friday "scan for defined-risk earnings plays"), add a `pings:` block to
options-lead:

```yaml
  - name: options-lead
    type: claude
    can_talk_to: [greeks-calculator, strategy-builder, vol-analyst, risk-gate, user]
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: |
          It's Friday. Scan the week's earnings calendar and propose up to three
          DEFINED-RISK structures for next week, running each through the full
          builder -> greeks -> vol -> risk-gate loop. Post the cleared ones to user.
        cron: "0 9 * * 5"             # 09:00 every Friday
        when_busy: skip
```

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `greeks-calculator: type: claude` (or `hermes`/`gemini`) to put the math on a
  different model than the builder.
- `risk-gate: type: claude` if you want the compliance desk on Claude while keeping
  gemini out.
- Remember: `gemini`/`hermes` rely on **pane polling** (no completion hook), so
  they don't need (and shouldn't be given) a hook-based `capture`.

### Tune the ACL
- To let the `risk-gate` escalate straight to `user` (not only via options-lead),
  add `user` to its `can_talk_to`. Mind that this widens the human-facing surface
  and bypasses options-lead's single-funnel guarantee — the doc's convention keeps
  options-lead the sole `user` contact so the gate always sits in front.
- To make a specialist unreachable from anyone but options-lead (already the case
  here), leave its `can_talk_to: [options-lead]` — that's the one-place-owns-the-
  gate guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader discussion
  of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md) for
  mixing model families safely.

### Tighten the risk policy
The defined-risk rule lives entirely in `risk-gate`'s role. To make it stricter
(e.g. "no short vega into a known vol event without approval"), edit the
risk-gate `role:` — the orchestrator enforces delivery, but the *policy* is words
the model follows, so the gate's instructions are the policy.

---

## 10. Tips & footguns

- **Keep options-lead the only `user`-facing agent.** Only options-lead lists
  `user` in `can_talk_to`. That gives you a single funnel: raw structures and greeks
  always pass through the risk gate before they reach you. If a specialist tried
  to mail `user` directly, the orchestrator bounces it (ACL) and drops a `system`
  note in their inbox explaining who they *can* message — the model self-corrects
  in-band.

- **The risk gate's `BLOCK` is the feature, not a failure.** A blocked strategy
  means it had an uncapped-loss leg (or blew the budget) and the gate caught it.
  options-lead re-briefs the builder and re-routes until `CLEAR`. Don't "fix" this
  by widening ACLs — the loop is how the human stays protected. The *only* way
  past a naked-short block is your explicit, named approval (see §5).

- **This is paper/simulated only.** The swarm never places orders or connects to a
  broker. Treat every output as an educational analysis, not a trade ticket. The
  "educational, not financial advice" caveat in options-lead's role is part of the
  deliverable — don't strip it.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch means completion never triggers and the
  agent pins "busy" forever. `status` showing an agent `busy` for a long time with
  `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **`command` strings are sensitive.** They may embed a broker/screening alias or
  API key. Don't print or commit them.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime + mailboxes)
  and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/options-strategist.yaml
  ./agentainer remove-session -c examples/options-strategist.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

- **Availability shapes the ending.** If `user` is **away** when options-lead
  finishes, your strategy is *held* (with a `system` "the user is away" ack to
  options-lead) rather than lost — read it later with `agentainer user inbox` or
  flip yourself available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions (`--dangerously-skip-permissions`,
  `--yolo`).

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- `examples/options-strategist.yaml` — the config this walkthrough is built on.
- `examples/trading-firm.yaml` — a sibling that trades *equities* (shares), not options.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

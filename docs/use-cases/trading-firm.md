# Use case: Trading firm (simulated desk)

A concrete, end-to-end walkthrough of the shipped
`examples/trading-firm.yaml` swarm — a **TradingAgents-style, PAPER-ONLY
trading desk** that runs the full research → debate → trade → risk-gate loop as
file-based mail. A **portfolio-manager** hub takes your mandate and is the *only*
agent that reaches `user`; four analysts work **in parallel**; two researchers
**debate** their output; a **trader** composes the order; and a **risk-manager**
gate enforces **hard limits** and requires **human approval** before anything is
logged to a *simulated* blotter. Nothing here moves real money.

Everything below is based on the actual contents of
`examples/trading-firm.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> **Disclaimer:** this is educational, not financial advice. The swarm connects
> to no live market feed and no real brokerage by default; every "execution" is a
> line in `blotter/simulated-blots.csv` labelled **PAPER / SIMULATED**. Do not
> wire in real keys or live orders without understanding your compliance
> obligations.

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

People who want to *simulate* a structured, multi-perspective trading process —
quant-curious developers, finance students, and operators who want to see how a
fund might decompose "should we trade X?" into fundamentals, sentiment, macro,
technical, a bull/bear contest, a sized decision, and a risk gate — **without
touching a brokerage or risking a cent**. The swarm encodes the discipline that
keeps a simulated desk honest: parallel specialist analysis, an adversarial
debate, a trader that synthesizes but cannot approve, and a risk-manager whose
limits the trader cannot override.

It is deliberately a **hub-and-spoke with a two-tier funnel**: every research
note and every order passes through exactly one path (down through
risk-manager → trader → researchers → analysts, and back up the same edges), and
the risk gate sits structurally between the trader and the human. Swapping in a
real data source (or, against advice, a broker) is your responsibility — the
config ships key-free and paper-only.

---

## 2. The topology

```
                 user
                  ^
                  | (approved order only -- human must still say GO)
          portfolio-manager              (HUB: the ONLY user-facing agent)
                  |
          risk-manager                   (the GATE: hard limits + human approval)
                  |
              trader                    (composes timing + magnitude)
             /       \
     bull-researcher  bear-researcher    (DEBATE the analysts' output)
             \       /
        (four analysts in PARALLEL, never talk to each other)
        fundamentals-  sentiment-  news-  technical-analyst
          analyst      analyst    analyst
```

Nine agents, one directed flow:

1. **`user` → `portfolio-manager`** — you send a mandate (a ticker/idea, a risk
   budget, constraints) — or the market-open ping kicks the PM off.
2. **`portfolio-manager` → `risk-manager`** — the PM hands the mandate down with
   "route this to the research team for analysis." The PM cannot reach anyone
   else, by design.
3. **`risk-manager` → `trader`** — as the downstream router, risk-manager passes
   the mandate to the trader and tells it to engage the researchers + analysts.
4. **`trader` → `bull-researcher` + `bear-researcher`** — the trader kicks off the
   debate and asks the two researchers to gather the parallel analysis.
5. **`bull/bear-researcher` → four analysts** — each researcher briefs the four
   analysts (fundamentals, sentiment, news, technical) in parallel. The analysts
   report **only** back to the two researchers, and **never to each other**.
6. **analysts → `bull/bear-researcher`** — four parallel writeups land.
7. **`bull-researcher` ⇄ `bear-researcher`** — the structured bull/bear contest;
   when it converges, both send the consolidated case to the `trader`.
8. **`trader` → `risk-manager`** — the trader composes the decision (timing,
   direction, size, stop) and sends it up. The trader **cannot** self-approve and
   **cannot** reach `user`.
9. **`risk-manager` gates it** — checks position size / max notional / per-name
   concentration / stop-loss. On pass, forwards the **approved** order to
   `portfolio-manager` (still requiring human GO before simulated execution). On
   any breach, **BOUNCE**s back to the trader with the specific limit broken.
10. **`risk-manager` → `portfolio-manager`** — the approved order arrives.
11. **`portfolio-manager` → `user`** — the PM presents the order; on your explicit
    GO it is logged to the simulated blotter and reported back.

The routing above is *enforced* by each agent's `can_talk_to` list. The analysts,
researchers, trader, and risk-manager **never** talk to `user` — only the PM does.
If any of them tried to mail `user` directly, the orchestrator bounces it as a
`system` message and files it in `failed/`.

---

## 3. The config, explained

Here is `examples/trading-firm.yaml` in full (role bodies abbreviated with
`...` for readability; the structure, names, ACLs, commands, and `pings` are
exact):

```yaml
swarm:
  name: trading-firm
  root: ./trading-firm-workspace

defaults:
  capture: none              # claude/codex are auto-upgraded to their hook at up
  can_talk_to: []            # tightened per agent below

agents:
  - name: portfolio-manager
    type: claude
    can_talk_to: [risk-manager, user]
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: |
          MARKET OPEN. Send the day's watchlist / mandate to risk-manager ...
        cron: "0 9 * * 1-5"            # 09:00 Mon-Fri
        when_busy: queue
      - message: |
          MARKET CLOSE. Request the end-of-day summary from risk-manager ...
        cron: "0 16 * * 1-5"           # 16:00 Mon-Fri
        when_busy: queue
    role: |
      You are the PORTFOLIO-MANAGER and the ONLY agent that talks to the human
      (user) ... APPROVE or REJECT the final order; require HUMAN approval before
      any simulated execution ... You may message: risk-manager, user. ...

  - name: fundamentals-analyst
    type: codex
    can_talk_to: [bull-researcher, bear-researcher]
    command: "codex --yolo"
    role: |
      You are the FUNDAMENTALS-ANALYST ... run in PARALLEL, never talk to the
      other analysts ... report ONLY to bull-researcher and bear-researcher. ...

  - name: sentiment-analyst
    type: gemini
    can_talk_to: [bull-researcher, bear-researcher]
    command: "gemini --yolo"
    role: |
      You are the SENTIMENT-ANALYST ... news + social mood read ... report ONLY to
      bull-researcher and bear-researcher. ...

  - name: news-analyst
    type: codex
    can_talk_to: [bull-researcher, bear-researcher]
    command: "codex --yolo"
    role: |
      You are the NEWS-ANALYST ... macro / geopolitical impact ... report ONLY to
      bull-researcher and bear-researcher. ...

  - name: technical-analyst
    type: gemini
    can_talk_to: [bull-researcher, bear-researcher]
    command: "gemini --yolo"
    role: |
      You are the TECHNICAL-ANALYST ... MACD / RSI / patterns ... report ONLY to
      bull-researcher and bear-researcher. ...

  - name: bull-researcher
    type: claude
    can_talk_to: [bear-researcher, trader, fundamentals-analyst, sentiment-analyst, news-analyst, technical-analyst]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the BULL-RESEARCHER ... the CASE FOR the trade ... structured contest
      with bear-researcher ... report the consolidated bull case to the trader.
      You may message: bear-researcher, trader, fundamentals-analyst,
      sentiment-analyst, news-analyst, technical-analyst. ...

  - name: bear-researcher
    type: claude
    can_talk_to: [bull-researcher, trader, fundamentals-analyst, sentiment-analyst, news-analyst, technical-analyst]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the BEAR-RESEARCHER ... the CASE AGAINST the trade ... structured
      contest with bull-researcher ... report the consolidated bear case to the
      trader. You may message: bull-researcher, trader, fundamentals-analyst,
      sentiment-analyst, news-analyst, technical-analyst. ...

  - name: trader
    type: codex
    can_talk_to: [bull-researcher, bear-researcher, risk-manager]
    command: "codex --yolo"
    role: |
      You are the TRADER ... compose timing + magnitude from the debate ... CANNOT
      self-approve, CANNOT reach user ... send the decision to risk-manager, resubmit
      if bounced. You may message: bull-researcher, bear-researcher, risk-manager. ...

  - name: risk-manager
    type: gemini
    can_talk_to: [trader, portfolio-manager]
    command: "gemini --yolo"
    role: |
      You are the RISK-MANAGER -- the GATE ... enforce HARD limits the trader cannot
      override ... forward approved orders to portfolio-manager (human GO still
      required) ... BOUNCE over-limit orders back to the trader. You may message:
      trader, portfolio-manager. ...
```

Field by field:

### `swarm`
- **`name: trading-firm`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./trading-firm-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent's workdir defaults to
  `trading-firm-workspace/<name>`, and orchestrator state goes under
  `trading-firm-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless it overrides them.
- **`capture: none`** — the default turn-detection mode. At `up`, the loader
  **auto-upgrades** this for `claude` and `codex` to their natural hook (the
  `validate` run prints warnings confirming it). It is a safe floor; every agent
  states its own `can_talk_to`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Each agent below
  states its own list explicitly.

### `portfolio-manager` (type: `claude`)
- **`can_talk_to: [risk-manager, user]`** — the PM is the hub and the **only
  agent that can talk to `user`**. That is the whole point: keep the
  human-facing surface to one agent, and put the risk gate in front of it. The PM
  can *only* reach `risk-manager` downward, so the mandate always flows through
  the gate.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command (substitute your own; treat as sensitive — it may embed keys).
- **`pings:`** — the PM carries the swarm's two scheduled pings (market-open and
  market-close; see §3 *The pings/cron*).
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at
  `up`; the `capture: none` default is auto-upgraded to hook here).

### The four analysts (`fundamentals`/`news` = `codex`, `sentiment`/`technical` = `gemini`)
- **`can_talk_to: [bull-researcher, bear-researcher]`** — each analyst reports
  its parallel writeup **only** to the two researchers and **never to another
  analyst** (so the four run independently and cannot cross-contaminate). None can
  reach the trader, risk-manager, PM, or user.
- **`command:`** — placeholder launch commands (`codex --yolo` / `gemini --yolo`).
- **Turn detection:** `codex` → `notify` hook (auto-upgraded); `gemini` → **pane
  polling** (the supervisor watches its pane for turn completion).

### `bull-researcher` / `bear-researcher` (type: `claude`)
- **`can_talk_to: [bear-researcher/ bull-researcher, trader, fundamentals-analyst, sentiment-analyst, news-analyst, technical-analyst]`** —
  each researcher can debate its counterpart, receive the four analysts' notes,
  and hand the consolidated case to the `trader`. Neither can reach
  `risk-manager`, `portfolio-manager`, or `user` — the debate's output is carried
  upward by the trader.
- **Turn detection:** `claude` → Stop hook (auto-upgraded from `capture: none`).

### `trader` (type: `codex`)
- **`can_talk_to: [bull-researcher, bear-researcher, risk-manager]`** — the
  synthesis step. It composes the decision from the debate and sends it **up** to
  `risk-manager`. It **cannot** reach `portfolio-manager` or `user`, so it can
  never self-approve or reach the human.
- **Turn detection:** `codex` → `notify` hook (auto-upgraded).

### `risk-manager` (type: `gemini`)
- **`can_talk_to: [trader, portfolio-manager]`** — the gate. It receives the
  trader's decision, enforces limits, and forwards **approved** orders to the PM
  (which is the only agent that can reach `user`). It can also receive a mandate
  from the PM and route it down to the trader. It **cannot** reach the analysts,
  researchers, or user directly.
- **Turn detection:** `gemini` → **pane polling**.

### ACL enforcement

The ACL is **cooperative, not OS isolation** (Decision D15): agents have
filesystem access and *could* write straight into another inbox, but the
orchestrator only ever *releases* and *routes* mail between names on the
sender's `can_talk_to` list. Anything addressed outside that list is bounced back
as a `system` message filed in `failed/`, so a model that forgets the rule
self-corrects in-band. Here that means:

- The analysts can *only* reach the two researchers — they can never skip the
  debate or reach the human.
- The researchers can *only* reach the trader (and each other) — the debate can't
  short-circuit to `user`.
- The trader can *only* reach `risk-manager` upward — it **can never self-approve
  or reach `user`**.
- Only `risk-manager` and `portfolio-manager` form the chain to `user`, and the
  risk gate structurally sits between the trader's decision and the human.

### Per-type turn detection

Turn-completion detection is the system clock (the stop → sweep → route → release
→ nudge loop). It is **per `type`**:
- `claude` (`portfolio-manager`, `bull-researcher`, `bear-researcher`) → **Stop
  hook** — fires when Claude finishes a turn.
- `codex` (`fundamentals-analyst`, `news-analyst`, `trader`) → **`notify` hook** —
  fires when Codex finishes.
- `gemini` (`sentiment-analyst`, `technical-analyst`, `risk-manager`) → **pane
  polling** — the supervisor reads the pane to decide the turn ended.

A `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
Claude) means completion never fires and the agent pins "busy" forever — which is
why `command` must launch the same CLI family `type` implies.

### The pings / cron

Only the **portfolio-manager** has a `pings:` block, and it has **two** entries —
the market-open kickoff and the market-close summary:

```yaml
pings:
  - message: |
      MARKET OPEN. Send the day's watchlist / mandate to risk-manager with a
      note to route it to the research team for analysis. ...
    cron: "0 9 * * 1-5"            # 09:00 Mon-Fri
    when_busy: queue
  - message: |
      MARKET CLOSE. Request the end-of-day summary ... from risk-manager and
      post it to user.
    cron: "0 16 * * 1-5"           # 16:00 Mon-Fri
    when_busy: queue
```

- **`cron: "0 9 * * 1-5"`** — fires at **09:00 Monday–Friday**, injecting the
  "market open, start the loop" prompt into the PM's inbox as a nudge.
- **`cron: "0 16 * * 1-5"`** — fires at **16:00 Monday–Friday**, asking the PM to
  pull the EOD summary from `risk-manager` and post it to `user`.
- **`when_busy: queue`** — if the PM is mid-turn (a live intraday question), the
  ping is **queued** behind the in-flight work rather than dropped, so the
  open/close cycle never silently disappears under a busy desk.

These are the only pieces of self-starting behavior; everything else is
event-driven off your mail. See [`configuration.md`](../configuration.md) for the
full `pings:` / `cron:` / `when_busy` grammar.

### What's *not* in this config
- **No `workdir` overrides.** All nine agents get the default
  `trading-firm-workspace/<name>`, so no mailbox namespacing is needed (each agent
  owns its directory). For the shared-workdir case, see
  [`custom-workspace.md`](./custom-workspace.md).
- **No per-agent `capture` overrides.** The `defaults: capture: none` is
  auto-upgraded to the type's hook for claude/codex; gemini uses pane polling.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **No real keys or feeds.** The swarm is paper-only by construction; the
  `risk-manager` and `portfolio-manager` roles explicitly label output
  SIMULATED.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/trading-firm.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the `capture: none` auto-upgrade
   warnings for the claude/codex agents (portfolio-manager, bull-researcher,
   bear-researcher, fundamentals-analyst, news-analyst, trader).
2. Creates the runtime dirs (`trading-firm-workspace/.agentainer/…`: log, queue,
   run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/`
   `about.md` contact card *is* the ACL made visible: the PM gets
   `outbox/risk-manager/` and `outbox/user/`; each analyst gets only
   `outbox/bull-researcher/` and `outbox/bear-researcher/`; the researchers get
   `outbox/{the-other-researcher, trader, all-four-analysts}/`; the trader gets
   `outbox/{bull-researcher, bear-researcher, risk-manager}/`; risk-manager gets
   `outbox/{trader, portfolio-manager}/`.
4. **Installs per-type turn detection** — the Claude Stop hook for the claude
   agents, the Codex `notify` hook for the codex agents; the gemini agents are
   covered by pane polling.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents (and drives gemini's pane polling) so one stuck agent can't
   wedge the desk.

At the end, `up` prints attach and **`serve`** hints. The `serve` line gives you
the mail-app control-plane UI (threads, live panes, send-as-user, availability
toggle). Drop `--host`/`--token` for the safe loopback-only `127.0.0.1` bind —
the UI can start processes, edit config, and type into agents that may run with
elevated permissions, so it must **never** be exposed on `0.0.0.0` without a
token. See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole analyst→debate→trader→risk→PM loop route mail with no API keys — the
> mechanics are identical.

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the PM's approved order / EOD summary as mail
(rather than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/trading-firm.yaml
```

This rewrites the `user` contact card in the PM's `outbox/user/about.md` to
`Status: available`, so the PM sees you're reachable. (While away, mail to you is
*held* and the sender gets a `system` ack — nothing bounces.)

Now send a mandate into the swarm, addressed to the portfolio-manager:

```bash
./agentainer send --to portfolio-manager -c examples/trading-firm.yaml \
  "Mandate for the day: evaluate a starter position in TICKER XYZ. Risk budget \
   is 1% of book, max notional 50k, stop-loss 4%. Begin the loop."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the PM, then — because the inbox
was empty — **released into `inbox/`** and the PM is **nudged** (the protocol is
re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the desk advance one turn at a time. Each arrow
is a `stop → sweep → route → release → nudge` cycle:

1. **PM receives the mandate.** It reads `inbox/`, then writes the mandate into
   `outbox/risk-manager/` (the only downstream path) with "route this to the
   research team." On stop, that routes to risk-manager.
2. **risk-manager routes down.** It forwards the mandate to `trader` (as the
   research-team kickoff). On stop, that routes to the trader.
3. **trader kicks off the debate.** It writes to `bull-researcher` and
   `bear-researcher` asking for the consolidated case. On stop, those route.
4. **researchers brief the analysts in parallel.** Each researcher writes to all
   four analysts. On stop, the four analysts each receive their brief (in
   parallel, never seeing each other's mail).
5. **analysts report back.** Each writes its writeup into `outbox/bull-researcher/`
   and `outbox/bear-researcher/`. On stop, they route.
6. **bull ⇄ bear debate.** The two researchers exchange rebuttals, then each sends
   the consolidated case to `trader`. On stop, those route.
7. **trader composes the decision.** It writes the sized order into
   `outbox/risk-manager/`. On stop, that routes to risk-manager.
8. **risk-manager gates it.** It checks the hard limits. On a breach, it **BOUNCE**s
   back to `trader` with the specific limit; the trader resubmits within limits. On
   pass, it forwards the **approved** order to `portfolio-manager`. On stop, that
   routes.
9. **PM presents to you.** The PM writes the approved order into `outbox/user/`.
   On your explicit GO, it logs the order to `blotter/simulated-blots.csv` as
   PAPER / SIMULATED and reports back. On stop, that's delivered to your `user`
   mailbox.
10. **you get the approved order / EOD summary** — visible with `agentainer user
    inbox`, or in the UI.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion. If you
never send anything, the agents just sit in standby (the two PM pings are the only
things that self-start the loop).

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/trading-firm.yaml
```

```
swarm: trading-firm   root: ./trading-firm-workspace
  portfolio-manager (claude) up idle queue=0 unread=0 talks=risk-manager, user
  risk-manager      (gemini)  up idle queue=0 unread=1 talks=trader, portfolio-manager
  trader            (codex)   up idle queue=0 unread=0 talks=bull-researcher, bear-researcher, risk-manager
  bull-researcher   (claude)  up idle queue=0 unread=0 talks=bear-researcher, trader, fundamentals-analyst, sentiment-analyst, news-analyst, technical-analyst
  bear-researcher   (claude)  up idle queue=0 unread=0 talks=bull-researcher, trader, fundamentals-analyst, sentiment-analyst, news-analyst, technical-analyst
  fundamentals-analyst (codex) up idle queue=0 unread=0 talks=bull-researcher, bear-researcher
  sentiment-analyst (gemini) up idle queue=0 unread=0 talks=bull-researcher, bear-researcher
  news-analyst      (codex)   up idle queue=0 unread=0 talks=bull-researcher, bear-researcher
  technical-analyst (gemini)  up idle queue=0 unread=0 talks=bull-researcher, bear-researcher
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/trading-firm.yaml          # whole swarm, last 20
./agentainer logs -c examples/trading-firm.yaml -f        # follow live
./agentainer logs trader -c examples/trading-firm.yaml    # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event. Watch for `bounce` lines from `risk-manager` —
that's the gate doing its job.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox risk-manager -c examples/trading-firm.yaml
```

Prints the one released message (headers + body), or `risk-manager: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue trader -c examples/trading-firm.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach bull-researcher -c examples/trading-firm.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the desk mid-flight through the `user` mailbox or by sending notes
into an agent's inbox.

- **Send a revised mandate to the PM.** Tightened the risk budget?
  `./agentainer send --to portfolio-manager -c examples/trading-firm.yaml "Revise:
  max notional 30k, stop-loss 3%."` The PM relays it down through risk-manager to
  the trader, which resizes and re-runs the gate.
- **See why risk-manager bounced an order.** `./agentainer inbox trader` (or the
  UI) shows the BOUNCE note the trader received — which limit broke and what to
  fix — so you can see the gate working.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/trading-firm.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/trading-firm.yaml     # resume is the default
```

On `up`, Agentainer reads `trading-firm-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the
claude agents, `codex resume <id>` for the codex agents, and the gemini sessions
via their recorded ids. A resumed agent is *not* re-sent the standby prompt (its
prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/trading-firm.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add an options / derivatives lens
The four analysts are fundamentals, sentiment, news, technical. To add a fifth
parallel specialist (e.g. an options-structure read), add an agent that reports
**only** to the two researchers — mirroring the existing analysts:

```yaml
  - name: options-analyst
    type: codex
    can_talk_to: [bull-researcher, bear-researcher]
    command: "codex --yolo"
    role: |
      You are the OPTIONS-ANALYST. Given a ticker/idea, produce a SIMULATED read on
      skew, term structure, and an illustrative option structure (label it
      paper-only). Run in PARALLEL, never talk to the other analysts, and report
      ONLY to bull-researcher and bear-researcher.
```

Then add `options-analyst` to both researchers' `can_talk_to` so they can brief it.

### Change the gate's limits
The hard limits (position size, max notional, per-name concentration, stop-loss)
live in the `risk-manager` role body and in your mandate. Tighten or loosen them
by editing the role text and/or the mandate you send; the gate re-checks every
order the trader submits, and BOUNCEs anything over limit.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `fundamentals-analyst: type: claude` (or `gemini`/`hermes`) to put the math on a
  different model than the PM.
- `risk-manager: type: claude` if you want the gate on Claude while keeping gemini
  out — remember claude/codex use a hook, gemini/hermes use pane polling.

### Tune the ACL
- To let the `trader` escalate straight to `user` (bypassing the risk gate) is
  **strongly discouraged** — it defeats the hard-limit guarantee that makes this
  swarm safe. The doc's convention keeps `user` reachable only from the PM, and
  the PM only from `risk-manager`, so every order passes the gate.
- To make the analysts reachable from the PM directly, add them to the PM's
  `can_talk_to` — but that short-circuits the debate, so leave the funnel intact
  unless you have a reason.

### Tune the daily pings
- Change `cron:` to your trading-calendar hours (the examples use 09:00 / 16:00
  Mon–Fri).
- Switch `when_busy:` from `queue` to `skip` if you'd rather a busy desk drop the
  open/close nudge than queue it. See [`configuration.md`](../configuration.md).

---

## 10. Tips & footguns

- **Keep `user` reachable from only the PM, and the PM reachable from only
  risk-manager.** That two-hop funnel is what guarantees the risk gate sits
  between every order and the human. If the `trader` (or anyone downstream) tried
  to mail `user` directly, the orchestrator bounces it (ACL) and drops a `system`
  note in their inbox explaining who they *can* message — the model self-corrects
  in-band.

- **The risk-manager's BOUNCE is the feature, not a failure.** A bounced order
  means the trader tried to size or structure past a hard limit (notional,
  concentration, stop). The trader resubmits within limits and the loop continues.
  Don't "fix" this by widening ACLs to let the trader reach the PM — the loop is
  how the human stays protected.

- **Human approval is required before "execution."** Even an APPROVED order only
  becomes a simulated blotter line after *you* (the `user`) say GO. The PM will
  not log it on its own. This keeps the human in the loop at the last possible
  step.

- **PAPER / SIMULATED ONLY, always.** No live market feed, no brokerage keys in
  this file, every blotter entry labelled "PAPER / SIMULATED -- not a real
  order." If you wire in a real data source or broker, you take on all compliance
  and risk — that is outside this example's scope.

- **`command` strings are sensitive.** They're placeholders that may embed API
  keys via shell aliases. Don't print or commit them. The `defaults: capture:
  none` floor plus the per-type auto-hook keeps turn detection correct without you
  touching `capture`.

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
  kill "thanks!/you're welcome!" loops — relevant if the bull/bear researchers
  chatter past the debate.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/trading-firm.yaml
  ./agentainer remove-session -c examples/trading-firm.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

- **Availability shapes the ending.** If `user` is **away** when the PM finishes,
  your approved order / EOD summary is *held* (with a `system` "the user is away"
  ack to the PM) rather than lost — read it later with `agentainer user inbox` or
  flip yourself available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions (`--dangerously-skip-permissions`,
  `--yolo`).

- **The market pings self-start, but `when_busy: queue` can pile them.** With
  `queue`, an open ping that lands during a live intraday turn waits behind the
  in-flight work rather than being dropped. If you'd rather a busy desk *skip* the
  open/close nudge than stack it, switch `when_busy` to `skip`.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- `examples/trading-firm.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

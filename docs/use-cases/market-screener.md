# Use case: Market screener

A concrete, end-to-end walkthrough of the shipped
`examples/market-screener.yaml` swarm — a screen-and-rank pipeline that takes a
universe plus your filter and ranking criteria, loads the data, drops the names
that fail, scores and ranks the survivors, and hands you a flagged watchlist with
a plain-language brief. A **screener-lead** hub takes your request, fans it out to
four specialists (**universe-loader**, **screen-engine**, **ranker**,
**reporter**), and returns one ranked watchlist. The lead is the only human-facing
agent; every specialist reports only to it.

Everything below is based on the actual contents of
`examples/market-screener.yaml` and the shipped CLI (`lib/cli.py`) and mailroom
(`lib/mail.py`). No API keys are needed to understand the mechanics; to run it
*for real* you supply the coding-CLI commands (or swap them for mock bash loops).

> ⚠️ **PAPER / SIMULATED ONLY.** This swarm screens and ranks — it does **not**
> trade, place orders, manage a portfolio, or connect to a broker. It is
> **educational, not financial advice**: every flag is a lead for *your own*
> research, not a recommendation to buy or sell. No output here should be the
> basis of a real investment decision.

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Investors, analysts, and DIY screeners who want a disciplined, repeatable
**screen → rank → flag** pass over a universe without doing the data pull and the
filter math themselves. The swarm encodes the discipline that makes a screen
trustworthy — one owner of the human-facing surface, a loader that never
fabricates a missing number, a screen-engine that shows its keep/drop reasoning,
a ranker whose scores are auditable, and a reporter that states the blind spots
and the "not advice" line.

It is deliberately a **hub-and-spoke**, not a free-for-all: every request and
every deliverable passes through the screener-lead, so the point where the
universe meets the filter meets the ranking lives in exactly one place. Swapping
in a dedicated factor miner (see `examples/quant-factor-miner.yaml`) or a
backtest auditor (see `examples/backtesting-auditor.yaml`) is a few lines of
config — but this swarm *screens a universe*, it does not mine or validate
factors.

---

## 2. The topology

```
   universe-loader  ----\
   screen-engine    ----->-- screener-lead <--> user
   ranker           -----/
   reporter         ----/
```

Five agents, one directed flow:

1. **`user` → `screener-lead`** — you send a screen: the universe (a named index, a
   watchlist file, or a ticker list), the filters (value / momentum / quality
   thresholds or natural-language criteria), the ranking blend, and how many to
   flag.
2. **`screener-lead` → `universe-loader`** — the lead sends the universe (and any
   field list) and asks for the prices / fundamentals / fields for every name.
3. **`universe-loader` → `screener-lead`** — the data comes back, with per-name
   gaps and an as-of timestamp.
4. **`screener-lead` → `screen-engine`** — the lead hands over the universe data
   plus the filters and asks for the survivors (with a keep/drop reason each).
5. **`screen-engine` → `screener-lead`** — the survivors come back.
6. **`screener-lead` → `ranker`** — the lead sends the survivors plus the ranking
   blend and asks for a scored, ranked list (top to bottom).
7. **`ranker` → `screener-lead`** — the ranked list comes back, components shown.
8. **`screener-lead` → `reporter`** — the lead sends the ranked list plus the flag
   count and asks for the plain-language brief (flags + shape + blind spots +
   disclaimer).
9. **`reporter` → `screener-lead`** — the brief comes back.
10. **`screener-lead` → `user`** — the final ranked watchlist + brief is delivered
    to you.

The routing above is *enforced* by each agent's `can_talk_to` list. The four
specialists **never** talk to `user` (or to each other) — only the screener-lead
does. If a specialist tried to mail `user` directly, the orchestrator bounces it
as a `system` message and files it in `failed/`.

---

## 3. The config, explained

Here is `examples/market-screener.yaml` in full (role bodies abbreviated with
`...` for readability; the structure, names, ACLs, commands, and `pings` are
exact):

```yaml
swarm:
  name: market-screener
  root: ./market-screener-workspace

defaults:
  capture: none              # claude/codex are auto-upgraded to their hook at up
  can_talk_to: []            # tightened per agent below

agents:
  - name: screener-lead
    type: claude
    can_talk_to: [universe-loader, screen-engine, ranker, reporter, user]
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: |
          Pre-market screen time. Re-run the most recent screen you were briefed
          on ... pull fresh data through UNIVERSE-LOADER -> SCREEN-ENGINE ->
          RANKER -> REPORTER, and deliver the refreshed ranked watchlist + brief to
          user. If no screen has ever been briefed, ask the user for one before
          delegating.
        cron: "0 9 * * 1-5"         # 09:00 Monday-Friday (pre-market window)
        when_busy: queue
    role: |
      You are the SCREENER-LEAD and the ONLY agent who talks to the human (user). ...
      (1) read the screen, ask ONE clarifying question if scope is ambiguous;
       (2) delegate to UNIVERSE-LOADER; (3) delegate to SCREEN-ENGINE; (4) delegate
       to RANKER; (5) delegate to REPORTER; (6) only then post the ranked watchlist
       + brief to user. ...

  - name: universe-loader
    type: codex
    can_talk_to: [screener-lead]
    command: "codex --yolo"
    role: |
      You are the UNIVERSE-LOADER. Load the prices / fundamentals / fields for the
      named universe ... flag missing/stale data as MISSING, never fabricate ...
      Report ONLY to the screener-lead. ...

  - name: screen-engine
    type: gemini
    can_talk_to: [screener-lead]
    command: "gemini --yolo"
    role: |
      You are the SCREEN-ENGINE. Apply the filters, return PASS/DROP per name with
      the reason and the value vs. threshold ... handle MISSING data by dropping and
      saying so ... Report ONLY to the screener-lead. ...

  - name: ranker
    type: claude
    can_talk_to: [screener-lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the RANKER. Score and rank the survivors from the stated blend, SHOW
      the components per name, tie-break deterministically ... Report ONLY to the
      screener-lead. ...

  - name: reporter
    type: gemini
    can_talk_to: [screener-lead]
    command: "gemini --yolo"
    role: |
      You are the REPORTER. Turn the ranked list into a plain-language brief: the
      top-N flags with a one-line why, the screen's shape, the blind spots, and the
      "educational, not advice" disclaimer ... Report ONLY to the screener-lead. ...
```

Field by field:

### `swarm`
- **`name: market-screener`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./market-screener-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent's workdir defaults to
  `market-screener-workspace/<name>` (screener-lead, universe-loader,
  screen-engine, ranker, reporter), and orchestrator state goes under
  `market-screener-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless it overrides them.
- **`capture: none`** — the default turn-detection mode. At `up`, the loader
  **auto-upgrades** this for `claude` and `codex` to their natural hook (the
  `validate` run prints the upgrade notices — see §3 turn-detection below). It is a
  safe floor; every agent states its own `can_talk_to`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Each agent below
  states its own list explicitly.

### `screener-lead` (type: `claude`)
- **`can_talk_to: [universe-loader, screen-engine, ranker, reporter, user]`** — the
  lead is the hub and the **only agent that can talk to `user`**. That last part is
  the whole point: keep the human-facing surface to one agent, and keep every
  screen's data pull, filter, ranking, and brief funneled through it.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed market-data / API
  keys.)
- **`pings:`** — the lead carries the swarm's only scheduled ping (see §3 *The
  pings/cron*).
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`;
  the `capture: none` default is auto-upgraded to hook here).

### `universe-loader` (type: `codex`)
- **`can_talk_to: [screener-lead]`** — returns the loaded data to the lead and
  nowhere else. It cannot reach the user, the screen-engine, the ranker, or the
  reporter directly.
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`
  (the `capture: none` default auto-upgrades to the notify hook).

### `screen-engine` (type: `gemini`)
- **`can_talk_to: [screener-lead]`** — receives the universe data + filters from
  the lead and returns the survivors to the lead only. It never touches the user.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **Turn detection:** `gemini` has no completion hook, so it relies on **pane
  polling** — the supervisor watches its pane for turn completion. (This is why the
  `capture: none` default needs no upgrade for gemini; only claude/codex get the
  auto-hook notices.)

### `ranker` (type: `claude`)
- **`can_talk_to: [screener-lead]`** — receives the survivors + blend from the lead
  and returns the ranked list to the lead only. It cannot reach the user.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook (auto-upgraded from `capture: none`).

### `reporter` (type: `gemini`)
- **`can_talk_to: [screener-lead]`** — receives the ranked list + flag count from
  the lead and returns the brief to the lead only. It never touches the user.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **Turn detection:** `gemini` → pane polling (no upgrade needed).

### ACL enforcement

The ACL is **cooperative, not OS isolation** (Decision D15): agents have
filesystem access and *could* write straight into another inbox, but the
orchestrator only ever *releases* and *routes* mail between names on the sender's
`can_talk_to` list. Anything addressed outside that list is bounced back as a
`system` message filed in `failed/`, so a model that forgets the rule self-corrects
in-band. Here that means the four specialists can *only* reach the screener-lead,
and only the lead can reach `user` — the human-facing surface is structurally
single-funnel.

### Per-type turn detection

Turn-completion detection is the system clock (the stop → sweep → route → release
→ nudge loop). It is **per `type`**:
- `claude` (`screener-lead`, `ranker`) → **Stop hook** — fires when Claude finishes
  a turn.
- `codex` (`universe-loader`) → **`notify` hook** — fires when Codex finishes.
- `gemini` (`screen-engine`, `reporter`) → **pane polling** — the supervisor reads
  the pane to decide the turn ended.

A `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
Claude) means completion never fires and the agent pins "busy" forever — which is
why `command` must launch the same CLI family `type` implies.

### The pings / cron

Only the **screener-lead** has a `pings:` block, and it has exactly one entry:

```yaml
pings:
  - message: |
      Pre-market screen time. Re-run the most recent screen you were briefed on
      (the universe + the filters + the ranking blend + how many to flag), pull
      fresh data through UNIVERSE-LOADER -> SCREEN-ENGINE -> RANKER -> REPORTER,
      and deliver the refreshed ranked watchlist + brief to user. If no screen has
      ever been briefed, ask the user for one before delegating.
    cron: "0 9 * * 1-5"         # 09:00 Monday-Friday (pre-market window)
    when_busy: queue
```

- **`cron: "0 9 * * 1-5"`** — fires at **09:00 every weekday** (the pre-market
  window), injecting the pre-market prompt into the screener-lead's inbox as a
  nudge so the ranked watchlist is ready when you sit down.
- **`when_busy: queue`** — if the lead is mid-turn (a live ad-hoc screen request),
  the ping is **queued** behind the in-flight work rather than dropped. This is what
  keeps a scheduled pre-market run from being lost under a live query (the
  opposite of the fp-and-a swarm's `skip`).

This is the one piece of self-starting behavior in the swarm; everything else is
event-driven off your mail. See [`configuration.md`](../configuration.md) for the
full `pings:` / `cron:` / `when_busy` grammar.

### What's *not* in this config
- **No `workdir` overrides.** All five agents get the default
  `market-screener-workspace/<name>`, so no mailbox namespacing is needed (each
  agent owns its directory). For the shared-workdir case, see
  [`custom-workspace.md`](./custom-workspace.md).
- **No per-agent `capture` overrides.** The `defaults: capture: none` is
  auto-upgraded to the type's hook for claude/codex; gemini uses pane polling.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **No trading / broker / execution agent.** By design — the swarm screens and
  ranks only. See §10 for the guardrail rationale.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/market-screener.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the auto-upgrade notices for the
   claude/codex agents.
2. Creates the runtime dirs (`market-screener-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/`
   about.md contact card *is* the ACL made visible: the screener-lead gets
   `outbox/universe-loader/`, `outbox/screen-engine/`, `outbox/ranker/`,
   `outbox/reporter/`, `outbox/user/`; each specialist gets only
   `outbox/screener-lead/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `screener-lead`
   and `ranker`, the Codex `notify` hook for `universe-loader`; the gemini agents
   are covered by pane polling.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents (and drives gemini's pane polling) so one stuck agent can't wedge
   the swarm.

At the end, `up` prints attach and **`serve`** hints. The `serve` line gives you
the mail-app control-plane UI (threads, live panes, send-as-user, availability
toggle). Drop `--host`/`--token` for the safe loopback-only `127.0.0.1` bind — the
UI can start processes, edit config, and type into agents that may run with
elevated permissions, so it must **never** be exposed on `0.0.0.0` without a token.
See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole load → screen → rank → report loop route mail with no API keys — the
> mechanics are identical.

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the screener-lead's finished watchlist as mail
(rather than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/market-screener.yaml
```

This rewrites the `user` contact card in the screener-lead's `outbox/user/about.md`
to `Status: available`, so the lead sees you're reachable. (While away, mail to you
is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the screen into the swarm, addressed to the screener-lead:

```bash
./agentainer send --to screener-lead -c examples/market-screener.yaml \
  "Screen the S&P 500 for: P/E < 20, 6-month momentum > 0, ROE > 15%. \
   Rank by a quality+momentum blend (60/40) and flag the top 10 with a one-line why."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the screener-lead, then — because
the inbox was empty — **released into `inbox/`** and the lead is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the screen advance one turn at a time. Each arrow
is a `stop → sweep → route → release → nudge` cycle:

1. **screener-lead receives the screen.** It reads `inbox/`, asks its one clarifying
   question if scope is ambiguous, then writes a delegation into
   `outbox/universe-loader/`. On stop, that routes to the universe-loader.
2. **universe-loader pulls the data.** It reads its inbox, loads the fields for the
   universe, flags missing/stale data as MISSING, and reports back into
   `outbox/screener-lead/`. On stop, that routes to the lead.
3. **screener-lead briefs the screen-engine.** It writes the universe data + filters
   into `outbox/screen-engine/`. On stop, that routes to the screen-engine.
4. **screen-engine filters.** It reads its inbox, returns PASS/DROP per name with
   the reason, and reports the survivors back into `outbox/screener-lead/`. On stop,
   that routes to the lead.
5. **screener-lead briefs the ranker.** It writes the survivors + blend into
   `outbox/ranker/`. On stop, that routes to the ranker.
6. **ranker scores and ranks.** It reads its inbox, returns the ranked list with
   score components shown, and reports back into `outbox/screener-lead/`. On stop,
   that routes to the lead.
7. **screener-lead briefs the reporter.** It writes the ranked list + flag count
   into `outbox/reporter/`. On stop, that routes to the reporter.
8. **reporter writes the brief.** It reads its inbox, writes the top-N flags + shape
   + blind spots + disclaimer, and reports back into `outbox/screener-lead/`. On
   stop, that routes to the lead, which writes the final watchlist + brief into
   `outbox/user/`. On stop, that's delivered to your `user` mailbox.
9. **you get the ranked watchlist** — visible with `agentainer user inbox`, or in
   the UI.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion. If you
never send anything, the agents just sit in standby (the pre-market ping is the
only thing that self-starts the loop).

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/market-screener.yaml
```

```
swarm: market-screener   root: ./market-screener-workspace
  screener-lead    (claude) up idle queue=0 unread=0 talks=universe-loader, screen-engine, ranker, reporter, user
  universe-loader  (codex)  up idle queue=0 unread=1 talks=screener-lead
  screen-engine    (gemini) up idle queue=0 unread=0 talks=screener-lead
  ranker           (claude) up idle queue=0 unread=0 talks=screener-lead
  reporter         (gemini) up idle queue=0 unread=0 talks=screener-lead
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/market-screener.yaml          # whole swarm, last 20
./agentainer logs -c examples/market-screener.yaml -f        # follow live
./agentainer logs reporter -c examples/market-screener.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox screener-lead -c examples/market-screener.yaml
```

Prints the one released message (headers + body), or `screener-lead: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue screener-lead -c examples/market-screener.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach reporter -c examples/market-screener.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or by sending notes
into an agent's inbox.

- **Send a clarification to the screener-lead.** Realized the blend should be
  70/30, not 60/40? `./agentainer send --to screener-lead -c
  examples/market-screener.yaml "Re-rank with quality 70% / momentum 30% and flag
  the top 15."` The lead relays the change down the chain.
- **Ask the screen-engine what it dropped.** `./agentainer inbox screener-lead` (or
  the UI) shows the screen-engine's PASS/DROP reasoning the lead received — which
  names failed which filter and why — so you can see the screen doing its job.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want to
  nudge a specific agent without guessing its name.

When you're happy (or want to try a different screen), tear it down:

```bash
./agentainer down -c examples/market-screener.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/market-screener.yaml     # resume is the default
```

On `up`, Agentainer reads `market-screener-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the
screener-lead and ranker, `codex resume <id>` for the universe-loader, and the
gemini sessions via their recorded ids. A resumed agent is *not* re-sent the
standby prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/market-screener.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a factor miner / backtest auditor
This swarm *screens a universe*; it does not mine or validate the factors
themselves. To push further, fold in siblings that ship alongside it:
- `examples/quant-factor-miner.yaml` — mines and tests the factors your blend uses
  (quality, momentum) before you bake them into the ranker's blend.
- `examples/backtesting-auditor.yaml` — checks whether a screen/blend historically
  held up, so the ranker's weights aren't cargo-culted.

Add either as a spoke the screener-lead can brief (its `can_talk_to:
[screener-lead]`), and have the lead consult it before finalizing the blend.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `ranker: type: codex` (or `hermes`) to put the scoring on a different model than
  the lead.
- `screen-engine: type: claude` if you want the keep/drop reasoning on Claude.
- Remember: `gemini`/`hermes` rely on **pane polling** (no completion hook), so
  they don't need (and shouldn't be given) a hook-based `capture`.

### Tune the ACL
- To let the `reporter` escalate straight to `user` (not only via the lead), add
  `user` to its `can_talk_to`. Mind that this widens the human-facing surface and
  bypasses the lead's single-funnel guarantee — the doc's convention keeps the lead
  the sole `user` contact.
- To make a specialist unreachable from anyone but the lead (already the case here),
  leave its `can_talk_to: [screener-lead]` — that's the one-place-owns-the-surface
  guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader discussion
  of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md) for
  mixing model families safely.

### Tune the pre-market ping
- Change `cron:` to your window (e.g. after-market: `"0 16 * * 1-5"`).
- Switch `when_busy:` from `queue` to `skip` if you'd rather a live query drop the
  scheduled run than wait behind it. See [`configuration.md`](../configuration.md).

---

## 10. Tips & footguns

- **Keep the screener-lead the only `user`-facing agent.** Only the lead lists
  `user` in `can_talk_to`. That gives you a single funnel: raw data pulls, filter
  reasoning, and ranked lists all pass through the lead before they reach you. If a
  specialist tried to mail `user` directly, the orchestrator bounces it (ACL) and
  drops a `system` note in their inbox explaining who they *can* message — the model
  self-corrects in-band.

- **This swarm does not trade.** There is deliberately no broker / execution agent
  and no order-placing capability in the config. It screens, ranks, and flags — the
  output is a *lead for your own research*, not a buy/sell signal, and the reporter
  is instructed to carry the "educational, not financial advice" disclaimer on
  every brief. Treat any flag as a starting point for due diligence, never as a
  recommendation.

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
  kill "thanks!/you're welcome!" loops — relevant if a specialist and the lead
  chatter past the screen.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/market-screener.yaml
  ./agentainer remove-session -c examples/market-screener.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files (the watchlist you dropped in)
  or your config.

- **Availability shapes the ending.** If `user` is **away** when the lead finishes,
  your watchlist is *held* (with a `system` "the user is away" ack to the lead)
  rather than lost — read it later with `agentainer user inbox` or flip yourself
  available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions (`--dangerously-skip-permissions`,
  `--yolo`).

- **`command` strings may embed keys.** Market-data / API keys can sit in a shell
  alias behind `command`. Treat them as sensitive — don't print or commit them.

- **The pre-market ping self-starts, and `when_busy: queue` preserves it.** Unlike
  a `skip` ping, a queued pre-market run waits behind a live query rather than being
  dropped — so the weekday 09:00 screen still lands, just a little later if you were
  mid-request.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- `examples/market-screener.yaml` — the config this walkthrough is built on.
- `examples/quant-factor-miner.yaml` — sibling that mines/validates the factors this
  screen uses (this swarm screens, it does not mine).
- `examples/backtesting-auditor.yaml` — sibling that audits a screen/blend's
  historical track record.
- ProjectPlan.md — the design source of truth (mail model §4–§14).

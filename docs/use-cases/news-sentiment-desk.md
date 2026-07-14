# Use case: News sentiment desk

A concrete, end-to-end walkthrough of the shipped
`examples/news-sentiment-desk.yaml` swarm — a news + social **sentiment desk** that
aggregates breaking news and retail chatter (StockTwits/Reddit), fuses them into a
per-name sentiment read, and surfaces alerts above a threshold. A **desk-chief**
hub takes the watchlist from you, fans out to a **news-aggregator**, a
**social-listener**, a **sentiment-scorer**, and an **alert-writer**, and relays
the finished read back to you. The desk-chief is the *only* agent that talks to the
human.

Everything below is based on the actual contents of
`examples/news-sentiment-desk.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> **This is an observational/alerting desk, not a trading system.** It produces
> sentiment reads and alerts only. It does not trade, does not place orders, and
> does not give buy/sell advice. Output is **educational, not financial advice**.

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Traders, investors, and market watchers who want a reusable, hands-off read on
what the *news* and *retail social* are saying about a watchlist — without
manually refreshing a dozen feeds. The swarm encodes the discipline that keeps a
sentiment desk honest: one owner of the human-facing surface, a news specialist
that never scores, a social specialist that never scores, a scorer that fuses
both and flags conflicts, and an alert-writer that keeps every output framed as
observation (never advice).

It is deliberately a **hub-and-spoke**, not a free-for-all: every feed and every
alert passes through the desk-chief, so the point where news meets social meets a
score lives in exactly one agent. Swapping in a `forecast-analyst` or a
`macro-strategy-desk` peer (sibling examples) or adding a second scorer is a few
lines of config.

---

## 2. The topology

```
   news-aggregator  ---\
   social-listener   ---\
                         >-- desk-chief <--> user
   sentiment-scorer  ---/
   alert-writer      ---/
```

Five agents, one directed flow:

1. **`user` → `desk-chief`** — you send a watchlist (tickers / themes) or a
   question ("what's the mood on NVDA, TSLA, SPY today?").
2. **`desk-chief` → `news-aggregator`** + **`desk-chief` → `social-listener`** —
   the desk-chief fans out the watchlist in parallel: the aggregator pulls a
   normalized news digest (headlines, source, time, why-it-matters) and the
   listener reads the retail chatter layer (StockTwits + Reddit volume/theme
   momentum).
3. **`news-aggregator` → `desk-chief`** and **`social-listener` → `desk-chief`** —
   both feeds return to the hub.
4. **`desk-chief` → `sentiment-scorer`** — the desk-chief hands *both* feeds to
   the scorer and asks for a per-name label (bullish/bearish/neutral) with a
   confidence and the evidence, explicitly flagging news-vs-social conflicts.
5. **`sentiment-scorer` → `desk-chief`** — the fused scores come back.
6. **`desk-chief` → `alert-writer`** — the desk-chief sends the scores + an alerts
   threshold to the alert-writer, which emits only crosses-threshold alerts plus a
   one-paragraph desk note, both carrying the "not financial advice" caveat.
7. **`alert-writer` → `desk-chief`** — the alerts come back; the desk-chief
   assembles the read and writes it to `outbox/user/`.
8. **`desk-chief` → `user`** — the sentiment read + alerts are delivered to you.

The routing above is *enforced* by each agent's `can_talk_to` list. The four
specialists **never** talk to `user` (or to each other) — only the desk-chief
does. If a specialist tried to mail `user` directly, the orchestrator bounces it
as a `system` message and files it in `failed/`.

---

## 3. The config, explained

Here is `examples/news-sentiment-desk.yaml` in full (role bodies abbreviated with
`...` for readability; the structure, names, ACLs, commands, and `pings` are
exact):

```yaml
swarm:
  name: news-sentiment-desk
  root: ./news-sentiment-desk-workspace

defaults:
  capture: none              # claude/codex are auto-upgraded to their hook at up
  can_talk_to: []            # tightened per agent below

agents:
  - name: desk-chief
    type: claude
    can_talk_to: [news-aggregator, social-listener, sentiment-scorer, alert-writer, user]
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: |
          Intraday refresh: pull the current watchlist ... run the full
          NEWS-AGGREGATOR -> SOCIAL-LISTENER -> SENTIMENT-SCORER ->
          ALERT-WRITER loop, and post the updated sentiment read + any new
          alerts to user. If you have no watchlist yet, skip and wait.
        cron: "*/30 * * * *"        # every 30 minutes, all day
        when_busy: queue
    role: |
      You are the DESK-CHIEF and the only agent who talks to the human (user). ...
      EDUCATIONAL, NOT FINANCIAL ADVICE -- never trade, never buy/sell direction,
      never imply certainty. (1) read the watchlist, ask for one if none;
       (2) fan out to NEWS-AGGREGATOR + SOCIAL-LISTENER; (3) fuse via
       SENTIMENT-SCORER; (4) alert via ALERT-WRITER (keep the caveat);
       (5) write the read to user. ...

  - name: news-aggregator
    type: codex
    can_talk_to: [desk-chief]
    command: "codex --yolo"
    role: |
      You are the NEWS-AGGREGATOR. Produce a NORMALIZED news digest per name --
      headlines, source, timestamp, why-it-matters, contradictions -- do NOT score.
      Report ONLY to the desk-chief. ...

  - name: social-listener
    type: gemini
    can_talk_to: [desk-chief]
    command: "gemini --yolo"
    role: |
      You are the SOCIAL-LISTENER. Read retail chatter (StockTwits/Reddit): volume
      signal, theme/meme momentum, loud vs. meaningful. Do NOT score. Report ONLY
      to the desk-chief. ...

  - name: sentiment-scorer
    type: claude
    can_talk_to: [desk-chief]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the SENTIMENT-SCORER. FUSE news + social into a per-name label
      (bullish/bearish/neutral) with confidence + evidence; flag conflicts. Weak
      signal -- be honest about uncertainty. Report ONLY to the desk-chief. ...

  - name: alert-writer
    type: gemini
    can_talk_to: [desk-chief]
    command: "gemini --yolo"
    role: |
      You are the ALERT-WRITER. From the scores + threshold, write only
      crosses-threshold alerts + a one-paragraph desk note, both carrying
      "educational, not financial advice". Do NOT compute sentiment. Report ONLY
      to the desk-chief. ...
```

Field by field:

### `swarm`
- **`name: news-sentiment-desk`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./news-sentiment-desk-workspace`** — the parent directory for the
  agents' working directories and mailboxes. Each agent's workdir defaults to
  `news-sentiment-desk-workspace/<name>` (desk-chief, news-aggregator,
  social-listener, sentiment-scorer, alert-writer), and orchestrator state goes
  under `news-sentiment-desk-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless it overrides them.
- **`capture: none`** — the default turn-detection mode. At `up`, the loader
  **auto-upgrades** this for `claude` and `codex` to their natural hook (the
  `validate` run prints three warnings confirming it — see §3 turn-detection
  below). It is a safe floor; every agent states its own `can_talk_to`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Each agent below
  states its own list explicitly.

### `desk-chief` (type: `claude`)
- **`can_talk_to: [news-aggregator, social-listener, sentiment-scorer, alert-writer, user]`**
  — the desk-chief is the hub and the **only agent that can talk to `user`**. That
  is the whole point: keep the human-facing surface to one agent and keep the
  news-meets-social-meets-score fusion in front of it.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`pings:`** — the desk-chief carries the swarm's only scheduled ping (see §3
  *The pings/cron*).
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`;
  the `capture: none` default is auto-upgraded to hook here).

### `news-aggregator` (type: `codex`)
- **`can_talk_to: [desk-chief]`** — returns the normalized digest to the
  desk-chief and nowhere else. It cannot reach the user, the listener, the scorer,
  or the alert-writer directly.
- **`command: "codex --yolo"`** — placeholder launch command. May embed news-API
  keys via a shell alias; treat as sensitive.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`
  (the `capture: none` default auto-upgrades to the notify hook).

### `social-listener` (type: `gemini`)
- **`can_talk_to: [desk-chief]`** — reads retail chatter from the watchlist and
  returns the momentum read to the desk-chief only. It never touches the user.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **Turn detection:** `gemini` has no completion hook, so it relies on **pane
  polling** — the supervisor watches its pane for turn completion. (This is why
  the `capture: none` default needs no upgrade for gemini; only claude/codex get
  the auto-hook warnings.)

### `sentiment-scorer` (type: `claude`)
- **`can_talk_to: [desk-chief]`** — receives the fused feeds from the desk-chief
  and returns per-name scores to the desk-chief only. It cannot reach the user, so
  its verdict is always relayed through the hub.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook (auto-upgraded from `capture: none`).

### `alert-writer` (type: `gemini`)
- **`can_talk_to: [desk-chief]`** — turns scores + threshold into alerts and a
  desk note for the desk-chief only. It never touches the user.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **Turn detection:** `gemini` → pane polling.

### ACL enforcement

The ACL is **cooperative, not OS isolation** (Decision D15): agents have
filesystem access and *could* write straight into another inbox, but the
orchestrator only ever *releases* and *routes* mail between names on the
sender's `can_talk_to` list. Anything addressed outside that list is bounced back
as a `system` message filed in `failed/`, so a model that forgets the rule
self-corrects in-band. Here that means the four specialists can *only* reach the
desk-chief, and only the desk-chief can reach `user` — the human-facing surface is
structurally guaranteed to be a single funnel.

### Per-type turn detection

Turn-completion detection is the system clock (the stop → sweep → route → release
→ nudge loop). It is **per `type`**:
- `claude` (`desk-chief`, `sentiment-scorer`) → **Stop hook** — fires when Claude
  finishes a turn.
- `codex` (`news-aggregator`) → **`notify` hook** — fires when Codex finishes.
- `gemini` (`social-listener`, `alert-writer`) → **pane polling** — the supervisor
  reads the pane to decide the turn ended.

A `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
Claude) means completion never fires and the agent pins "busy" forever — which is
why `command` must launch the same CLI family `type` implies.

### The pings / cron

Only the **desk-chief** has a `pings:` block, and it has exactly one entry:

```yaml
pings:
  - message: |
      Intraday refresh: pull the current watchlist (or the last one the user
      gave you), run the full NEWS-AGGREGATOR -> SOCIAL-LISTENER ->
      SENTIMENT-SCORER -> ALERT-WRITER loop, and post the updated sentiment
      read + any new alerts to user. If you have no watchlist yet, skip and
      wait for the user's first prompt.
    cron: "*/30 * * * *"        # every 30 minutes, all day
    when_busy: queue
```

- **`cron: "*/30 * * * *"`** — fires **every 30 minutes, around the clock**,
  injecting the intraday-refresh prompt into the desk-chief's inbox as a nudge, so
  the human gets a sentiment check-in even with no prompt.
- **`when_busy: queue`** — if the desk-chief is mid-turn (a live question),
  the ping is **queued** behind the in-flight work rather than dropped — this keeps
  a scheduled refresh from being lost during an active conversation.

This is the one piece of self-starting behavior in the swarm; everything else is
event-driven off your mail. See [`configuration.md`](../configuration.md) for the
full `pings:` / `cron:` / `when_busy` grammar.

### What's *not* in this config
- **No `workdir` overrides.** All five agents get the default
  `news-sentiment-desk-workspace/<name>`, so no mailbox namespacing is needed
  (each agent owns its directory). For the shared-workdir case, see
  [`custom-workspace.md`](./custom-workspace.md).
- **No per-agent `capture` overrides.** The `defaults: capture: none` is
  auto-upgraded to the type's hook for claude/codex; gemini uses pane polling.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **No trading/sizing logic.** By design the desk observes and alerts only; there
  is no order, position, or buy/sell vocabulary anywhere in the agent roles.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/news-sentiment-desk.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the auto-upgrade warnings for the
   claude/codex agents (three of the five get a hook; the two gemini agents use
   pane polling).
2. Creates the runtime dirs (`news-sentiment-desk-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/`
   about.md contact card *is* the ACL made visible: the desk-chief gets
   `outbox/news-aggregator/`, `outbox/social-listener/`, `outbox/sentiment-scorer/`,
   `outbox/alert-writer/`, `outbox/user/`; each specialist gets only
   `outbox/desk-chief/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `desk-chief` and
   `sentiment-scorer`, the Codex `notify` hook for `news-aggregator`; the two gemini
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
> the whole news→social→score→alert loop route mail with no API keys — the
> mechanics are identical. Note this also means no real feeds; the mock agents
> exercise the *routing*, not the sentiment.

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the desk-chief's sentiment read as mail (rather
than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/news-sentiment-desk.yaml
```

This rewrites the `user` contact card in the desk-chief's `outbox/user/about.md`
to `Status: available`, so the desk-chief sees you're reachable. (While away, mail
to you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send a watchlist (or a question) into the swarm, addressed to the desk-chief:

```bash
./agentainer send --to desk-chief -c examples/news-sentiment-desk.yaml \
  "Watchlist for today: NVDA, TSLA, SPY. Give me the intraday sentiment read \
   and flag anything above threshold."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the desk-chief, then — because
the inbox was empty — **released into `inbox/`** and the desk-chief is **nudged**
(the protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the sentiment loop advance one turn at a time.
Each arrow is a `stop → sweep → route → release → nudge` cycle:

1. **desk-chief receives the watchlist.** It reads `inbox/`, and if no watchlist is
   set asks for one; otherwise it writes two delegations into `outbox/news-aggregator/`
   and `outbox/social-listener/`. On stop, those route to the spokes.
2. **news-aggregator + social-listener return.** Each reads its inbox, produces its
   feed (normalized digest / chatter momentum), and reports back into
   `outbox/desk-chief/`. On stop, those route to the desk-chief.
3. **desk-chief briefs the scorer.** It writes both feeds into
   `outbox/sentiment-scorer/`. On stop, that routes to the sentiment-scorer.
4. **sentiment-scorer fuses.** It reads its inbox, returns per-name labels +
   confidence + evidence (flagging conflicts) into `outbox/desk-chief/`. On stop,
   that routes to the desk-chief.
5. **desk-chief briefs the alert-writer.** It writes the scores + threshold into
   `outbox/alert-writer/`. On stop, that routes to the alert-writer.
6. **alert-writer emits alerts.** It reads its inbox, writes crosses-threshold
   alerts + a desk note (with the "not financial advice" caveat) into
   `outbox/desk-chief/`. On stop, that routes to the desk-chief.
7. **desk-chief delivers the read.** It assembles the read + alerts into one message
   and writes it into `outbox/user/`. On stop, that's delivered to your `user`
   mailbox.
8. **you get the sentiment read** — visible with `agentainer user inbox`, or in the
   UI.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion. If you
never send anything, the agents just sit in standby (the 30-minute ping is the
only thing that self-starts the loop).

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/news-sentiment-desk.yaml
```

```
swarm: news-sentiment-desk   root: ./news-sentiment-desk-workspace
  desk-chief       (claude) up idle queue=0 unread=0 talks=news-aggregator, social-listener, sentiment-scorer, alert-writer, user
  news-aggregator  (codex)  up idle queue=0 unread=1 talks=desk-chief
  social-listener  (gemini) up idle queue=0 unread=0 talks=desk-chief
  sentiment-scorer (claude) up idle queue=0 unread=0 talks=desk-chief
  alert-writer     (gemini) up idle queue=0 unread=0 talks=desk-chief
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/news-sentiment-desk.yaml          # whole swarm, last 20
./agentainer logs -c examples/news-sentiment-desk.yaml -f        # follow live
./agentainer logs sentiment-scorer -c examples/news-sentiment-desk.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox desk-chief -c examples/news-sentiment-desk.yaml
```

Prints the one released message (headers + body), or `desk-chief: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue desk-chief -c examples/news-sentiment-desk.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach social-listener -c examples/news-sentiment-desk.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the desk mid-flight through the `user` mailbox or by sending notes
into an agent's inbox.

- **Send a clarification to the desk-chief.** Realized you want a tighter threshold
  or a wider watchlist? `./agentainer send --to desk-chief -c examples/news-sentiment-desk.yaml
  "Add AMD to the watchlist and raise the alert threshold to confidence >= 0.7."`
  The desk-chief relays the change down the chain on the next loop.
- **Ask the scorer what it saw.** `./agentainer inbox desk-chief` (or the UI) shows
  the per-name scores and evidence the desk-chief received — so you can audit why
  an alert fired.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/news-sentiment-desk.yaml
```

---

## 8. Resume after a stop

Bringing the desk back later resumes conversations by default:

```bash
./agentainer up -c examples/news-sentiment-desk.yaml     # resume is the default
```

On `up`, Agentainer reads `news-sentiment-desk-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the
desk-chief and sentiment-scorer, `codex resume <id>` for the news-aggregator, and
the gemini sessions via their recorded ids. A resumed agent is *not* re-sent the
standby prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/news-sentiment-desk.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This desk is a starting point. A few common adjustments:

### Add a macro / fundamental peer
Siblings `examples/macro-strategy-desk.yaml` (fundamental cross-asset) and
`examples/forecast-analyst.yaml` push the analysis further. To fold a fundamental
read into this desk, add a sixth agent the desk-chief can brief after the score:

```yaml
  - name: fundamental-analyst
    type: codex
    can_talk_to: [desk-chief]
    command: "codex --yolo"
    role: |
      You are the FUNDAMENTAL-ANALYST. Given the desk-chief's watchlist, summarize
      the per-name fundamental picture (valuation, catalyst calendar, earnings
      setup) so the desk-chief can contrast it with the sentiment read. Report ONLY
      to the desk-chief.
```

Then add `fundamental-analyst` to the desk-chief's `can_talk_to` so it can be
briefed.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `sentiment-scorer: type: codex` (or `hermes`/`gemini`) to put the fusion on a
  different model than the desk-chief.
- `alert-writer: type: claude` if you want the alerts on Claude while keeping
  gemini only for listening.
- Remember: `gemini`/`hermes` rely on **pane polling** (no completion hook), so
  they don't need (and shouldn't be given) a hook-based `capture`.

### Tune the ACL
- To let the `alert-writer` escalate straight to `user` (not only via the
  desk-chief), add `user` to its `can_talk_to`. Mind that this widens the
  human-facing surface and bypasses the desk-chief's single-funnel guarantee — the
  doc's convention keeps the desk-chief the sole `user` contact.
- To make a specialist unreachable from anyone but the desk-chief (already the
  case here), leave its `can_talk_to: [desk-chief]` — that's the one-place-owns-
  the-surface guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader discussion
  of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md) for
  mixing model families safely.

### Tune the intraday ping
- Change `cron:` to a different cadence (e.g. hourly: `"0 * * * *"`, or market
  hours only: `"*/30 13-20 * * 1-5"` for US session).
- Switch `when_busy:` from `queue` to `skip` if you'd rather a refresh be dropped
  than wait behind a live question. See [`configuration.md`](../configuration.md).

---

## 10. Tips & footguns

- **Keep the desk-chief the only `user`-facing agent.** Only the desk-chief lists
  `user` in `can_talk_to`. That gives you a single funnel: raw feeds always pass
  through the scorer and the alert-writer (with the "not financial advice" caveat)
  before they reach you. If a specialist tries to mail `user` directly, the
  orchestrator bounces it (ACL) and drops a `system` note in their inbox explaining
  who they *can* message — the model self-corrects in-band.

- **Sentiment is a weak signal — treat it that way.** News and social are noisy;
  the scorer is told to be honest about uncertainty and to flag news-vs-social
  conflicts rather than average them into a false neutral. Never size a position off
  an alert alone. The desk is built to *observe and surface*, not to *decide*.

- **No trading vocabulary by design.** There is no order, position, or buy/sell
  language anywhere in the agent roles. If you find yourself wanting the desk to
  "execute", that's out of scope — this is paper/simulated observation only.

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
  kill "thanks!/you're welcome!" loops — relevant if a specialist and the
  desk-chief chatter past the gate.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/news-sentiment-desk.yaml
  ./agentainer remove-session -c examples/news-sentiment-desk.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files (the feeds you dropped in) or
  your config.

- **Availability shapes the ending.** If `user` is **away** when the desk-chief
  finishes, your sentiment read is *held* (with a `system` "the user is away" ack to
  the desk-chief) rather than lost — read it later with `agentainer user inbox` or
  flip yourself available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions (`--dangerously-skip-permissions`,
  `--yolo`).

- **`command` may embed keys.** The news/social APIs your real commands call can
  sit behind shell aliases that embed keys. Treat every `command` string as
  sensitive — do not print or commit it.

- **The intraday ping self-starts; `when_busy: queue` can pile up.** If a live
  question is in flight at a :00/:30 boundary, the refresh waits behind it rather
  than dropping. If you'd rather never stack refreshes, switch `when_busy` to
  `skip`.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- `examples/news-sentiment-desk.yaml` — the config this walkthrough is built on.
- `examples/macro-strategy-desk.yaml` — a sibling that adds a fundamental cross-asset read.
- `examples/forecast-analyst.yaml` — a sibling that pushes a forward projection further.
- ProjectPlan.md — the design source of truth (mail model §4–§14).

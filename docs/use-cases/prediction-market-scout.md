# Use case: Prediction-market scout

A concrete, end-to-end walkthrough of the shipped
`examples/prediction-market-scout.yaml` swarm — a prediction-market (e.g.
Polymarket) copy-trading **research** desk that tracks markets, hunts
mispricings vs. base rates, summarizes sentiment, and drafts **PAPER-only**
positions for your review. A **scout-lead** hub takes the request from you,
fans out to a **market-watcher** (live prices), a **base-rate-analyst** (the
reference probability), a **sentiment-reader** (the crowd narrative), and a
**position-advisor** that drafts gated paper positions. The position-advisor is
human-gated: it returns drafts to scout-lead, which relays them to you as
clearly-labelled PAPER suggestions that require your explicit approval.

Everything below is based on the actual contents of
`examples/prediction-market-scout.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> **Educational, not financial advice.** This swarm is PAPER / SIMULATED only. It
> never places real orders and ships with no real prediction-market keys by
> default. Nothing it produces is a trade, a recommendation to trade, or anything
> other than simulated research for your own review.

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Researchers, hobbyists, and the curious who want a disciplined read on what a
prediction market is *implying* versus what a cold base rate would say — without
touching real money. The swarm encodes the discipline that makes a prediction-
market read trustworthy: one owner of the human-facing surface, a price fetcher
that never invents base rates, a base-rate analyst that never reads the price, a
sentiment reader that never conflates narrative with data, and a position engine
that is **gated** — it drafts PAPER-only positions and can never reach the human
directly, so nothing labeled a "position" reaches you without scout-lead's
gating and your approval.

It is deliberately a **hub-and-spoke**, not a free-for-all: every request and
every deliverable passes through scout-lead, so the point where the four views
meet (and where the human-gate sits) lives in exactly one place. Swapping in a
real `news-sentiment-desk` agent (see `examples/news-sentiment-desk.yaml`) or
adding a second analyst is a few lines of config.

---

## 2. The topology

```
          market-watcher --\
                            >-- scout-lead <--> user
         base-rate-analyst -/
          sentiment-reader -/
          position-advisor -/
```

Five agents, one directed flow:

1. **`user` → `scout-lead`** — you send a research question ("what's mispriced on
   Polymarket around the election?", "summarize sentiment on <topic>", "draft
   paper positions").
2. **`scout-lead` → `market-watcher`** — scout-lead asks for the active, relevant
   markets and their current prices (market IDs/links, implied %, volume).
3. **`market-watcher` → `scout-lead`** — the price sheet comes back.
4. **`scout-lead` → `base-rate-analyst`** — scout-lead asks for the reference
   probability (base rate) per market, with the reference class and confidence.
5. **`base-rate-analyst` → `scout-lead`** — the base rates come back, with the
   price-vs-base-rate gap sized.
6. **`scout-lead` → `sentiment-reader`** — scout-lead asks for a short sentiment
   summary per market (lean, bull/bear story), kept separate from hard data.
7. **`sentiment-reader` → `scout-lead`** — the sentiment read comes back.
8. **`scout-lead` → `position-advisor`** — scout-lead assembles the three views and
   asks for PAPER-ONLY draft positions. The position-advisor is the **human gate**:
   it drafts paper stakes, labels each DISCLAIMED, and returns them to scout-lead.
   It **cannot** mail `user`.
9. **`position-advisor` → `scout-lead`** — on return, scout-lead relays the gated
   (paper) proposals to `user` as clearly-labelled PAPER suggestions requiring
   explicit human approval, carrying the "educational, not financial advice" line.
   On nothing else does mail reach you.

The routing above is *enforced* by each agent's `can_talk_to` list. The four
specialists **never** talk to `user` (or to each other) — only scout-lead does.
If a specialist tried to mail `user` directly, the orchestrator bounces it as a
`system` message and files it in `failed/`.

---

## 3. The config, explained

Here is `examples/prediction-market-scout.yaml` in full (role bodies abbreviated
with `...` for readability; the structure, names, ACLs, commands, and `pings`
are exact):

```yaml
swarm:
  name: prediction-market-scout
  root: ./prediction-market-scout-workspace

defaults:
  capture: none              # claude/codex are auto-upgraded to their hook at up
  can_talk_to: []            # tightened per agent below

agents:
  - name: scout-lead
    type: claude
    can_talk_to: [market-watcher, base-rate-analyst, sentiment-reader, position-advisor, user]
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: |
          Morning scan: pull the most active prediction markets and current
          prices from market-watcher, the latest base rates from base-rate-analyst,
          and the sentiment read from sentiment-reader. Find the biggest price-vs-
          base-rate gaps and ask position-advisor for PAPER-ONLY draft positions.
          Relay the gated (paper) proposals to user with the educational
          disclaimer. If a market feed is missing, ask the user what to use.
        cron: "0 7 * * *"             # 07:00 every day
        when_busy: skip
    role: |
      You are the SCOUT-LEAD and the ONLY agent who talks to the human (user). ...
      (1) read the question, ask ONE clarifying question if scope is ambiguous;
       (2) delegate prices to MARKET-WATCHER; (3) delegate base rates to
       BASE-RATE-ANALYST; (4) delegate sentiment to SENTIMENT-READER;
       (5) assemble the three views and ask POSITION-ADVISOR for PAPER-ONLY drafts;
       (6) relay gated paper proposals to user with the disclaimer, never implying
       the swarm can or did trade. ...

  - name: market-watcher
    type: codex
    can_talk_to: [scout-lead]
    command: "codex --yolo"
    role: |
      You are the MARKET-WATCHER. Poll the configured prediction-market platform
      for active markets + current prices ... market ID/link, implied %, volume ...
      Flag thin/illiquid markets. Do NOT compute base rates, write sentiment, or
      draft positions. If no real API key is configured, say so -- never fabricate
      prices. Report ONLY to scout-lead. ...

  - name: base-rate-analyst
    type: claude
    can_talk_to: [scout-lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the BASE-RATE-ANALYST. Compute the reference probability per outcome
      from historical frequencies / base rates, NOT the live price ... cite the
      reference class and confidence, size the price-vs-base-rate gap ... Do NOT
      poll live markets, write sentiment, or draft positions. Report ONLY to
      scout-lead. ...

  - name: sentiment-reader
    type: gemini
    can_talk_to: [scout-lead]
    command: "gemini --yolo"
    role: |
      You are the SENTIMENT-READER. Summarize the narrative / sentiment per market
      ... lean, bull/bear story, mood shift ... clearly separated from hard
      price/base-rate data. Never invent "the market says X" without a source.
      Report ONLY to scout-lead. ...

  - name: position-advisor
    type: claude
    can_talk_to: [scout-lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the POSITION-ADVISOR -- the GATED proposal engine. Draft PAPER-ONLY /
      SIMULATED positions where price diverges from base rate ... side, simulated
      stake %, edge thesis, key risk ... label each DISCLAIMED, state you are NOT
      transacting, flag thin-liquidity as "paper only". You CANNOT mail user --
      return drafts to scout-lead. Report ONLY to scout-lead. ...
```

Field by field:

### `swarm`
- **`name: prediction-market-scout`** — the swarm's name (shows up in `status`,
  logs, sessions).
- **`root: ./prediction-market-scout-workspace`** — the parent directory for the
  agents' working directories and mailboxes. Each agent's workdir defaults to
  `prediction-market-scout-workspace/<name>` (scout-lead, market-watcher,
  base-rate-analyst, sentiment-reader, position-advisor), and orchestrator state
  goes under `prediction-market-scout-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless it overrides them.
- **`capture: none`** — the default turn-detection mode. At `up`, the loader
  **auto-upgrades** this for `claude` and `codex` to their natural hook (the
  `validate` run prints three warnings confirming it — see §3 turn-detection
  below). It is a safe floor; every agent states its own `can_talk_to`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Each agent below
  states its own list explicitly.

### `scout-lead` (type: `claude`)
- **`can_talk_to: [market-watcher, base-rate-analyst, sentiment-reader, position-advisor, user]`** —
  scout-lead is the hub and the **only agent that can talk to `user`**. That last
  part is the whole point: keep the human-facing surface to one agent and put the
  position gate in front of it.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`pings:`** — scout-lead carries the swarm's only scheduled ping (see §3 *The
  pings/cron*).
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`;
  the `capture: none` default is auto-upgraded to hook here).

### `market-watcher` (type: `codex`)
- **`can_talk_to: [scout-lead]`** — reports the price sheet back to scout-lead and
  nowhere else. It cannot reach the user, the analysts, or the advisor directly.
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`
  (the `capture: none` default auto-upgrades to the notify hook).

### `base-rate-analyst` (type: `claude`)
- **`can_talk_to: [scout-lead]`** — returns the reference probabilities to
  scout-lead only. It never touches the user or the price feed.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook (auto-upgraded from `capture: none`).

### `sentiment-reader` (type: `gemini`)
- **`can_talk_to: [scout-lead]`** — receives the markets from scout-lead and
  returns the sentiment read to scout-lead only. It never touches the user.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **Turn detection:** `gemini` has no completion hook, so it relies on **pane
  polling** — the supervisor watches its pane for turn completion. (This is why
  the `capture: none` default needs no upgrade for gemini; only claude/codex get
  the auto-hook warnings.)

### `position-advisor` (type: `claude`)
- **`can_talk_to: [scout-lead]`** — the gate lives behind scout-lead: the advisor
  only ever talks to scout-lead, returning PAPER-ONLY drafts. It cannot reach
  `user`, so its proposals are always relayed (and disclaimed) through the hub.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook (auto-upgraded from `capture: none`).

### ACL enforcement

The ACL is **cooperative, not OS isolation** (Decision D15): agents have
filesystem access and *could* write straight into another inbox, but the
orchestrator only ever *releases* and *routes* mail between names on the
sender's `can_talk_to` list. Anything addressed outside that list is bounced back
as a `system` message filed in `failed/`, so a model that forgets the rule
self-corrects in-band. Here that means the four specialists can *only* reach
scout-lead, and only scout-lead can reach `user` — the position gate is
structurally guaranteed to sit between the draft and the human.

### Per-type turn detection

Turn-completion detection is the system clock (the stop → sweep → route → release
→ nudge loop). It is **per `type`**:
- `claude` (`scout-lead`, `base-rate-analyst`, `position-advisor`) → **Stop hook**
  — fires when Claude finishes a turn.
- `codex` (`market-watcher`) → **`notify` hook** — fires when Codex finishes.
- `gemini` (`sentiment-reader`) → **pane polling** — the supervisor reads the
  pane to decide the turn ended.

A `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
Claude) means completion never fires and the agent pins "busy" forever — which is
why `command` must launch the same CLI family `type` implies.

### The pings / cron

Only **scout-lead** has a `pings:` block, and it has exactly one entry:

```yaml
pings:
  - message: |
      Morning scan: pull the most active prediction markets and current
      prices from market-watcher, the latest base rates from base-rate-analyst,
      and the sentiment read from sentiment-reader. Find the biggest price-vs-
      base-rate gaps and ask position-advisor for PAPER-ONLY draft positions.
      Relay the gated (paper) proposals to user with the educational
      disclaimer. If a market feed is missing, ask the user what to use.
    cron: "0 7 * * *"             # 07:00 every day
    when_busy: skip
```

- **`cron: "0 7 * * *"`** — fires at **07:00 every day**, injecting the morning
  scan prompt into scout-lead's inbox as a nudge (before the trading day).
- **`when_busy: skip`** — if scout-lead is mid-turn (a live ad-hoc question), the
  ping is **skipped** rather than queued on top of the in-flight work. This keeps a
  scheduled scan from piling onto a live query.

This is the one piece of self-starting behavior in the swarm; everything else is
event-driven off your mail. See [`configuration.md`](../configuration.md) for the
full `pings:` / `cron:` / `when_busy` grammar.

### What's *not* in this config
- **No `workdir` overrides.** All five agents get the default
  `prediction-market-scout-workspace/<name>`, so no mailbox namespacing is needed
  (each agent owns its directory). For the shared-workdir case, see
  [`custom-workspace.md`](./custom-workspace.md).
- **No per-agent `capture` overrides.** The `defaults: capture: none` is
  auto-upgraded to the type's hook for claude/codex; gemini uses pane polling.
- **No real prediction-market keys.** The swarm is PAPER/simulated by default;
  `market-watcher` is told to report (not fabricate) if no feed is configured.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/prediction-market-scout.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the three `capture: none` auto-upgrade
   warnings for the claude/codex agents.
2. Creates the runtime dirs (`prediction-market-scout-workspace/.agentainer/…`:
   log, queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/`
   `about.md` contact card *is* the ACL made visible: scout-lead gets
   `outbox/market-watcher/`, `outbox/base-rate-analyst/`, `outbox/sentiment-reader/`,
   `outbox/position-advisor/`, `outbox/user/`; each specialist gets only
   `outbox/scout-lead/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `scout-lead`,
   `base-rate-analyst`, `position-advisor`; the Codex `notify` hook for
   `market-watcher`; the gemini agent is covered by pane polling.
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
> the whole price→base-rate→sentiment→paper-position loop route mail with no API
> keys — the mechanics are identical. `market-watcher` will report (not fabricate)
> when no feed is configured.

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* scout-lead's gated paper proposals as mail
(rather than have them held), turn yourself available first:

```bash
./agentainer user available -c examples/prediction-market-scout.yaml
```

This rewrites the `user` contact card in scout-lead's `outbox/user/about.md` to
`Status: available`, so scout-lead sees you're reachable. (While away, mail to you
is *held* and the sender gets a `system` ack — nothing bounces.)

Now send your research question into the swarm, addressed to scout-lead:

```bash
./agentainer send --to scout-lead -c examples/prediction-market-scout.yaml \
  "What's mispriced on Polymarket around the US election? Show me markets where \
   the price looks far from the base rate, and draft paper positions."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for scout-lead, then — because the
inbox was empty — **released into `inbox/`** and scout-lead is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the research loop advance one turn at a time.
Each arrow is a `stop → sweep → route → release → nudge` cycle:

1. **scout-lead receives the question.** It reads `inbox/`, asks its one clarifying
   question if scope is ambiguous, then writes a delegation into
   `outbox/market-watcher/`. On stop, that routes to the market-watcher.
2. **market-watcher fetches prices.** It reads its inbox, returns the active
   markets + current prices, and reports back into `outbox/scout-lead/`. On stop,
   that routes to scout-lead.
3. **scout-lead briefs the base-rate-analyst.** It writes the markets into
   `outbox/base-rate-analyst/`. On stop, that routes to the base-rate-analyst.
4. **base-rate-analyst returns reference probabilities.** It reads its inbox, sizes
   the price-vs-base-rate gap, and reports back into `outbox/scout-lead/`. On stop,
   that routes to scout-lead.
5. **scout-lead briefs the sentiment-reader.** It writes the markets into
   `outbox/sentiment-reader/`. On stop, that routes to the sentiment-reader.
6. **sentiment-reader drafts the narrative read.** It reads its inbox and reports
   back into `outbox/scout-lead/`. On stop, that routes to scout-lead.
7. **scout-lead assembles the views and briefs the position-advisor.** It writes
   the combined sheet into `outbox/position-advisor/` with a PAPER-ONLY instruction.
   On stop, that routes to the position-advisor.
8. **position-advisor gates it.** It reads the sheet and returns PAPER-ONLY draft
   positions (side, simulated stake %, edge thesis, risk, DISCLAIMED) into
   `outbox/scout-lead/`. It cannot reach `user`. On stop, that routes to scout-lead.
9. **scout-lead relays the gated paper proposals to `user`.** It writes them into
   `outbox/user/` as clearly-labelled PAPER suggestions requiring your explicit
   approval, carrying the "educational, not financial advice" line. On stop, that's
   delivered to your `user` mailbox.
10. **you get the gated paper proposals** — visible with `agentainer user inbox`,
    or in the UI. No real order was placed; nothing transacted.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion. If you
never send anything, the agents just sit in standby (the morning ping is the only
thing that self-starts the loop).

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/prediction-market-scout.yaml
```

```
swarm: prediction-market-scout   root: ./prediction-market-scout-workspace
  scout-lead       (claude) up idle queue=0 unread=0 talks=market-watcher, base-rate-analyst, sentiment-reader, position-advisor, user
  market-watcher   (codex)  up idle queue=0 unread=1 talks=scout-lead
  base-rate-analyst(claude) up idle queue=0 unread=0 talks=scout-lead
  sentiment-reader (gemini) up idle queue=0 unread=0 talks=scout-lead
  position-advisor (claude) up idle queue=0 unread=0 talks=scout-lead
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/prediction-market-scout.yaml          # whole swarm, last 20
./agentainer logs -c examples/prediction-market-scout.yaml -f        # follow live
./agentainer logs position-advisor -c examples/prediction-market-scout.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox scout-lead -c examples/prediction-market-scout.yaml
```

Prints the one released message (headers + body), or `scout-lead: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue scout-lead -c examples/prediction-market-scout.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach position-advisor -c examples/prediction-market-scout.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or by sending notes
into an agent's inbox.

- **Send a clarification to scout-lead.** Realized you meant a different platform
  or topic? `./agentainer send --to scout-lead -c examples/prediction-market-scout.yaml
  "Re-scope to Polymarket crypto markets only, exclude politics."` scout-lead
  relays the change down the chain.
- **Ask why a proposal was gated a certain way.** `./agentainer inbox scout-lead`
  (or the UI) shows the position-advisor's paper draft and its edge thesis, so you
  can see the gate doing its job — and the disclaimer riding along with it.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're done (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/prediction-market-scout.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/prediction-market-scout.yaml     # resume is the default
```

On `up`, Agentainer reads
`prediction-market-scout-workspace/.agentainer/sessions.yaml` (written as each
agent finished its first turn) and reattaches the recorded conversations via each
type's native resume: `claude --resume <id>` for the claude agents, `codex resume
<id>` for the market-watcher, and the gemini session via its recorded id. A
resumed agent is *not* re-sent the standby prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/prediction-market-scout.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a news feed
`examples/news-sentiment-desk.yaml` ships as a sibling that pushes live news
sentiment further. To fold it in, add a sixth agent that scout-lead can brief
alongside `sentiment-reader`:

```yaml
  - name: news-desk
    type: gemini
    can_talk_to: [scout-lead]
    command: "gemini --yolo"
    role: |
      You are the NEWS-DESK. Given the markets scout-lead sends, pull recent
      headlines driving each and summarize the catalyst. Report ONLY to scout-lead.
```

Then add `news-desk` to scout-lead's `can_talk_to` so it can be briefed.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `market-watcher: type: claude` (or `hermes`/`gemini`) to put price-fetching on a
  different model than scout-lead.
- `sentiment-reader: type: claude` if you want the narrative read on Claude while
  keeping gemini out.
- Remember: `gemini`/`hermes` rely on **pane polling** (no completion hook), so
  they don't need (and shouldn't be given) a hook-based `capture`.

### Tune the ACL
- To let the `position-advisor` escalate straight to `user` (not only via
  scout-lead), add `user` to its `can_talk_to`. **Do not do this** — it bypasses
  scout-lead's single-funnel guarantee and the human gate, so a paper draft could
  reach you without the disclaimer. The doc's convention keeps scout-lead the sole
  `user` contact.
- To make a specialist unreachable from anyone but scout-lead (already the case
  here), leave its `can_talk_to: [scout-lead]` — that's the one-place-owns-the-gate
  guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md)
  for mixing model families safely.

### Tune the morning ping
- Change `cron:` to fire on your own schedule (e.g. twice daily: `"0 7,19 * * *"`).
- Switch `when_busy:` from `skip` to `queue` if you'd rather the scan wait behind a
  live query than be dropped. See [`configuration.md`](../configuration.md).

---

## 10. Tips & footguns

- **Keep scout-lead the only `user`-facing agent.** Only scout-lead lists `user` in
  `can_talk_to`. That gives you a single funnel: raw price sheets, base rates, and
  sentiment always pass through the human gate before they reach you as proposals.
  If a specialist tried to mail `user` directly, the orchestrator bounces it (ACL)
  and drops a `system` note in their inbox explaining who they *can* message — the
  model self-corrects in-band.

- **The position-advisor's `can_talk_to: [scout-lead]` is the feature, not a
  limitation.** A draft that reaches you without scout-lead's gating and the
  disclaimer is a bug. scout-lead relays, labels PAPER, and carries the
  "educational, not financial advice" line. Don't "fix" this by widening ACLs — the
  gate is how the human stays protected.

- **This swarm is PAPER / SIMULATED. Always.** It never places real orders and
  ships with no real prediction-market keys by default. `market-watcher` is
  instructed to report (not fabricate) when no feed is configured. If you wire in a
  real key, you are doing so outside this design — treat `command` strings as
  sensitive (they may embed keys) and never let the swarm autonomously transact.

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
  kill "thanks!/you're welcome!" loops — relevant if a specialist and scout-lead
  chatter past the gate.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/prediction-market-scout.yaml
  ./agentainer remove-session -c examples/prediction-market-scout.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

- **Availability shapes the ending.** If `user` is **away** when scout-lead
  finishes, your gated paper proposals are *held* (with a `system` "the user is
  away" ack to scout-lead) rather than lost — read them later with
  `agentainer user inbox` or flip yourself available and they're delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions (`--dangerously-skip-permissions`,
  `--yolo`).

- **The morning ping self-starts, but `when_busy: skip` can drop it.** If a live
  query is in flight at 07:00, the scan ping is silently skipped rather than
  queued. If you rely on the daily scan, either keep `user` quiet around then, or
  switch `when_busy` to `queue`.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- `examples/prediction-market-scout.yaml` — the config this walkthrough is built on.
- `examples/news-sentiment-desk.yaml` — a sibling example the swarm can be extended with.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

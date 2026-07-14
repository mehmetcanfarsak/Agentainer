# Use case: Dividend income strategist

A concrete, end-to-end walkthrough of the shipped
`examples/dividend-income-strategist.yaml` swarm — a dividend / income *investing
research* pipeline (paper and simulated only) that screens dividend payers, stress-
tests payout **sustainability**, and assembles a diversified **income portfolio**,
all relayed to you by a single hub. A **income-lead** hub takes the brief from you
and coordinates three specialists: a **dividend-screener** (the universe filter),
a **sustainability-analyst** (the payout-durability gate), and a **portfolio-
builder** (the diversified model portfolio). The hub is the only agent that talks
to you, so every holding that reaches your screen has passed through its funnel.

Everything below is based on the actual contents of
`examples/dividend-income-strategist.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> **Paper / simulated only — not financial advice.** This swarm produces *research*
> and a *model portfolio* for education. It never places trades, wires money, or
> connects to a brokerage. Verify every figure against a primary source and consult
> a licensed fiduciary before acting on any output. The hub attaches this
> disclaimer to every portfolio it delivers.

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Income-focused individual investors, retirees planning a dividend stream, and
anyone building an income *research* habit without a paid terminal. The swarm
encodes the discipline that makes an income portfolio trustworthy — one owner of
the human-facing surface, a screener that only filters (never judges durability),
a sustainability analyst that only gates (never picks weights), and a builder that
only assembles the model portfolio. The hub relays the finished research with the
disclaimer attached.

It is deliberately a **hub-and-spoke**, not a free-for-all: every brief and every
deliverable passes through the income-lead, so the single point where the screen
meets the durability verdict (and where the disclaimer is attached) lives in
exactly one place. Swapping in a real data-provider alias or adding a second
screener is a few lines of config.

---

## 2. The topology

```
       dividend-     sustainability-    portfolio-
        screener      analyst            builder
            \             |                /
             \            |               /
              >-- income-lead <--> user
```

Four agents, one directed flow:

1. **`user` → `income-lead`** — you send the brief: the universe (US-listed? a
   watchlist? an index?), the minimum yield, the desired portfolio size, and any
   sector constraints.
2. **`income-lead` → `dividend-screener`** — the hub sends the criteria and asks
   for a shortlist of names clearing the yield/quality screen, with the raw
   metrics used.
3. **`dividend-screener` → `income-lead`** — the shortlist comes back.
4. **`income-lead` → `sustainability-analyst`** — the hub hands over the shortlist
   and asks for a payout-durability verdict on each name (payout ratio, FCF cover,
   aristocrat status, red flags).
5. **`sustainability-analyst` → `income-lead`** — the `SAFE / WATCH / UNSAFE`
   verdicts come back; the hub drops or flags any `UNSAFE` name.
6. **`income-lead` → `portfolio-builder`** — the cleared shortlist goes to the
   builder, who returns a diversified income portfolio (target yield, sector
   spread, position sizing, the income-vs-risk trade-off).
7. **`portfolio-builder` → `income-lead`** — the model portfolio comes back.
8. **`income-lead` → `user`** — the hub assembles the portfolio and delivers it to
   you **with the prominent "paper / simulated, educational, not financial advice"
   disclaimer attached**.

The routing above is *enforced* by each agent's `can_talk_to` list. The three
specialists **never** talk to `user` (or to each other) — only the income-lead
does. If a specialist tried to mail `user` directly, the orchestrator bounces it
as a `system` message and files it in `failed/`.

---

## 3. The config, explained

Here is `examples/dividend-income-strategist.yaml` in full (role bodies abbreviated
with `...` for readability; the structure, names, ACLs, commands, and `pings` are
exact):

```yaml
swarm:
  name: dividend-income-strategist
  root: ./dividend-income-strategist-workspace

defaults:
  capture: none              # claude/codex are auto-upgraded to their hook at up
  can_talk_to: []            # tightened per agent below

agents:
  - name: income-lead
    type: claude
    can_talk_to: [dividend-screener, sustainability-analyst, portfolio-builder, user]
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: |
          Quarterly income review is here. Re-screen the dividend-payer universe
          ... have SUSTAINABILITY-ANALYST re-check payout durability ... ask
          PORTFOLIO-BUILDER to refresh the diversified income portfolio. Deliver
          the updated model portfolio to user with the disclaimer attached. ...
        cron: "0 9 1 1,4,7,10 *"      # 09:00 on the 1st of Jan/Apr/Jul/Oct
        when_busy: skip
    role: |
      You are the INCOME-LEAD and the ONLY agent who talks to the human (user). ...
      (1) read the brief, ask ONE clarifying question if scope is ambiguous;
       (2) delegate the screen to DIVIDEND-SCREENER; (3) delegate the durability
       check to SUSTAINABILITY-ANALYST and treat its verdict as a filter;
       (4) delegate the assembly to PORTFOLIO-BUILDER; (5) deliver the model
       portfolio to user WITH the prominent paper/simulated disclaimer. ...

  - name: dividend-screener
    type: codex
    can_talk_to: [income-lead]
    command: "codex --yolo"
    role: |
      You are the DIVIDEND-SCREENER. Given the brief, produce a shortlist clearing
      the yield/quality screen ... report yield, DPS, price, payout frequency, raw
      criteria met, rank by yield+quality, flag disqualifiers, state data source ...
      Do NOT assess durability or build a portfolio. Report ONLY to income-lead. ...

  - name: sustainability-analyst
    type: gemini
    can_talk_to: [income-lead]
    command: "gemini --yolo"
    role: |
      You are the SUSTAINABILITY-ANALYST. Given the shortlist, stress-test payout
      durability ... payout ratio, FCF cover, track record (aristocrat status),
      red flags ... verdict SAFE / WATCH / UNSAFE per name ... Do NOT screen or
      build. Report ONLY to income-lead. ...

  - name: portfolio-builder
    type: claude
    can_talk_to: [income-lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the PORTFOLIO-BUILDER. Given the sustainability-cleared shortlist,
      assemble a diversified income portfolio ... target yield, sector spread,
      position sizing, income-vs-risk trade-off, monitor list ... exclude UNSAFE
      names. Do NOT screen or assess durability. Report ONLY to income-lead. ...
```

Field by field:

### `swarm`
- **`name: dividend-income-strategist`** — the swarm's name (shows up in `status`,
  logs, sessions).
- **`root: ./dividend-income-strategist-workspace`** — the parent directory for
  the agents' working directories and mailboxes. Each agent's workdir defaults to
  `dividend-income-strategist-workspace/<name>` (income-lead, dividend-screener,
  sustainability-analyst, portfolio-builder), and orchestrator state goes under
  `dividend-income-strategist-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless it overrides them.
- **`capture: none`** — the default turn-detection mode. At `up`, the loader
  **auto-upgrades** this for `claude` and `codex` to their natural hook (the
  `validate` run prints three warnings confirming it — see §3 turn-detection
  below). It is a safe floor; every agent states its own `can_talk_to`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Each agent below
  states its own list explicitly.

### `income-lead` (type: `claude`)
- **`can_talk_to: [dividend-screener, sustainability-analyst, portfolio-builder, user]`**
  — the hub is the **only agent that can talk to `user`**. That last part is the
  whole point: keep the human-facing surface to one agent and attach the
  paper/simulated disclaimer in exactly one place.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a data-
  provider shell alias. Treat command strings as sensitive; they may embed keys.)
- **`pings:`** — the hub carries the swarm's only scheduled ping (see §3 *The
  pings/cron*).
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`;
  the `capture: none` default is auto-upgraded to hook here).

### `dividend-screener` (type: `codex`)
- **`can_talk_to: [income-lead]`** — returns the shortlist to the hub and nowhere
  else. It cannot reach the user, the analyst, or the builder directly.
- **`command: "codex --yolo"`** — placeholder launch command (swap for a data-
  provider alias if you want live screen data; treat it as sensitive).
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`
  (the `capture: none` default auto-upgrades to the notify hook).

### `sustainability-analyst` (type: `gemini`)
- **`can_talk_to: [income-lead]`** — receives the shortlist from the hub and
  returns the durability verdict to the hub only. It never touches the user.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **Turn detection:** `gemini` has no completion hook, so it relies on **pane
  polling** — the supervisor watches its pane for turn completion. (This is why
  the `capture: none` default needs no upgrade for gemini; only claude/codex get
  the auto-hook warnings.)

### `portfolio-builder` (type: `claude`)
- **`can_talk_to: [income-lead]`** — the assembled model portfolio only ever goes
  back to the hub, who attaches the disclaimer and relays it to you. The builder
  cannot reach the user directly, so the disclaimer is structurally guaranteed to
  sit in front of every portfolio you receive.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook (auto-upgraded from `capture: none`).

### ACL enforcement

The ACL is **cooperative, not OS isolation** (Decision D15): agents have
filesystem access and *could* write straight into another inbox, but the
orchestrator only ever *releases* and *routes* mail between names on the sender's
`can_talk_to` list. Anything addressed outside that list is bounced back as a
`system` message filed in `failed/`, so a model that forgets the rule self-
corrects in-band. Here that means the three specialists can *only* reach the hub,
and only the hub can reach `user` — the disclaimer lives in exactly one place.

### Per-type turn detection

Turn-completion detection is the system clock (the stop → sweep → route → release
→ nudge loop). It is **per `type`**:
- `claude` (`income-lead`, `portfolio-builder`) → **Stop hook** — fires when Claude
  finishes a turn.
- `codex` (`dividend-screener`) → **`notify` hook** — fires when Codex finishes.
- `gemini` (`sustainability-analyst`) → **pane polling** — the supervisor reads the
  pane to decide the turn ended.

A `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
Claude) means completion never fires and the agent pins "busy" forever — which is
why `command` must launch the same CLI family `type` implies.

### The pings / cron

Only the **income-lead** has a `pings:` block, and it has exactly one entry:

```yaml
pings:
  - message: |
      Quarterly income review is here. Re-screen the dividend-payer universe ...
      have SUSTAINABILITY-ANALYST re-check payout durability ... ask
      PORTFOLIO-BUILDER to refresh the diversified income portfolio. Deliver the
      updated model portfolio to user with the disclaimer attached. If any input
      data is missing, ask the user for it before delegating.
    cron: "0 9 1 1,4,7,10 *"      # 09:00 on the 1st of Jan/Apr/Jul/Oct
    when_busy: skip
```

- **`cron: "0 9 1 1,4,7,10 *"`** — fires at **09:00 on the 1st of January, April,
  July, and October** (right after earnings season), injecting the quarterly review
  prompt into the hub's inbox as a nudge.
- **`when_busy: skip`** — if the hub is mid-turn (a live ad-hoc brief), the ping is
  **skipped** rather than queued on top of the in-flight work. This keeps a
  scheduled review from piling onto a live question.

This is the one piece of self-starting behavior in the swarm; everything else is
event-driven off your mail. See [`configuration.md`](../configuration.md) for the
full `pings:` / `cron:` / `when_busy` grammar.

### What's *not* in this config
- **No `workdir` overrides.** All four agents get the default
  `dividend-income-strategist-workspace/<name>`, so no mailbox namespacing is
  needed (each agent owns its directory). For the shared-workdir case, see
  [`custom-workspace.md`](./custom-workspace.md).
- **No per-agent `capture` overrides.** The `defaults: capture: none` is
  auto-upgraded to the type's hook for claude/codex; gemini uses pane polling.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **No brokerage / trading integration.** By design: the swarm is research only
  and never executes. The disclaimer is attached by the hub, not the file schema.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/dividend-income-strategist.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the three `capture: none` auto-upgrade
   warnings for the claude/codex agents.
2. Creates the runtime dirs (`dividend-income-strategist-workspace/.agentainer/…`:
   log, queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/`
   `about.md` contact card *is* the ACL made visible: the hub gets
   `outbox/dividend-screener/`, `outbox/sustainability-analyst/`,
   `outbox/portfolio-builder/`, `outbox/user/`; each specialist gets only
   `outbox/income-lead/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `income-lead`
   and `portfolio-builder`, the Codex `notify` hook for `dividend-screener`; the
   gemini agent is covered by pane polling.
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
> the whole screen → sustainability → portfolio loop route mail with no API keys —
> the mechanics are identical.

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the hub's finished model portfolio as mail
(rather than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/dividend-income-strategist.yaml
```

This rewrites the `user` contact card in the hub's `outbox/user/about.md` to
`Status: available`, so the hub sees you're reachable. (While away, mail to you is
*held* and the sender gets a `system` ack — nothing bounces.)

Now send the brief into the swarm, addressed to the income-lead:

```bash
./agentainer send --to income-lead -c examples/dividend-income-strategist.yaml \
  "Screen for US-listed dividend payers yielding > 3%, assess the top 10 for \
   payout sustainability, then build a 12-20 name income portfolio targeting \
   ~5% yield with sector diversification."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the hub, then — because the inbox
was empty — **released into `inbox/`** and the hub is **nudged** (the protocol is
re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the income loop advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **income-lead receives the brief.** It reads `inbox/`, asks its one clarifying
   question if scope is ambiguous, then writes a delegation into
   `outbox/dividend-screener/`. On stop, that routes to the screener.
2. **dividend-screener produces the shortlist.** It reads its inbox, filters the
   universe, and reports the shortlist back into `outbox/income-lead/`. On stop,
   that routes to the hub.
3. **income-lead briefs the analyst.** It writes the shortlist into
   `outbox/sustainability-analyst/`. On stop, that routes to the analyst.
4. **sustainability-analyst gates durability.** It reads its inbox, returns
   `SAFE / WATCH / UNSAFE` verdicts, and reports back into `outbox/income-lead/`.
   On stop, that routes to the hub, which drops/flags any `UNSAFE` name.
5. **income-lead briefs the builder.** It writes the cleared shortlist into
   `outbox/portfolio-builder/`. On stop, that routes to the builder.
6. **portfolio-builder assembles the model portfolio.** It reads its inbox, writes
   the diversified portfolio, and reports back into `outbox/income-lead/`. On stop,
   that routes to the hub.
7. **income-lead delivers to you.** It assembles the portfolio, attaches the
   PROMINENT "paper / simulated, educational, not financial advice" disclaimer,
   and writes it into `outbox/user/`. On stop, that's delivered to your `user`
   mailbox.
8. **you get the model portfolio** — visible with `agentainer user inbox`, or in
   the UI.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion. If you
never send anything, the agents just sit in standby (the quarterly ping is the
only thing that self-starts the loop).

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/dividend-income-strategist.yaml
```

```
swarm: dividend-income-strategist   root: ./dividend-income-strategist-workspace
  income-lead          (claude) up idle queue=0 unread=0 talks=dividend-screener, sustainability-analyst, portfolio-builder, user
  dividend-screener    (codex)  up idle queue=0 unread=1 talks=income-lead
  sustainability-analyst (gemini) up idle queue=0 unread=0 talks=income-lead
  portfolio-builder    (claude) up idle queue=0 unread=0 talks=income-lead
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/dividend-income-strategist.yaml          # whole swarm, last 20
./agentainer logs -c examples/dividend-income-strategist.yaml -f        # follow live
./agentainer logs sustainability-analyst -c examples/dividend-income-strategist.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox income-lead -c examples/dividend-income-strategist.yaml
```

Prints the one released message (headers + body), or `income-lead: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue income-lead -c examples/dividend-income-strategist.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach sustainability-analyst -c examples/dividend-income-strategist.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails the mandate. Because every message is natural-language
mail, you can steer the swarm mid-flight through the `user` mailbox or by sending
notes into an agent's inbox.

- **Send a clarification to the hub.** Realized you wanted REITs excluded?
  `./agentainer send --to income-lead -c examples/dividend-income-strategist.yaml
  "Exclude REITs from the screen — re-run the sustainability check and rebuild."
  The hub relays the change down the chain and re-routes the portfolio.
- **Ask why a name was dropped.** `./agentainer inbox income-lead` (or the UI)
  shows the `UNSAFE` verdict the hub received from the sustainability-analyst — the
  payout ratio or FCF-cover figure behind the call — so you can see the gate doing
  its job.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/dividend-income-strategist.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/dividend-income-strategist.yaml     # resume is the default
```

On `up`, Agentainer reads
`dividend-income-strategist-workspace/.agentainer/sessions.yaml` (written as each
agent finished its first turn) and reattaches the recorded conversations via each
type's native resume: `claude --resume <id>` for the hub and portfolio-builder,
`codex resume <id>` for the screener, and the gemini session via its recorded id.
A resumed agent is *not* re-sent the standby prompt (its prior context is
restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/dividend-income-strategist.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a dedicated data-provider agent
If you want live screen data rather than estimates, add a fifth agent that the hub
briefs first to pull yields/FCF from a provider (via a shell-alias `command` that
embeds the provider token — treat it as sensitive):

```yaml
  - name: data-fetcher
    type: codex
    can_talk_to: [income-lead]
    command: "codex --yolo"   # alias wrapping your provider token
    role: |
      You are the DATA-FETCHER. Given the hub's criteria, pull current yield,
      payout ratio, and FCF-cover figures from the configured provider for the
      named universe, with source + date for every figure. Report ONLY to the
      income-lead.
```

Then add `data-fetcher` to the hub's `can_talk_to` so it can be briefed before the
screener.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `dividend-screener: type: claude` (or `gemini`/`hermes`) to put the screen on a
  different model than the hub.
- `sustainability-analyst: type: claude` if you want the durability gate on Claude
  while keeping gemini out.
- Remember: `gemini`/`hermes` rely on **pane polling** (no completion hook), so
  they don't need (and shouldn't be given) a hook-based `capture`.

### Tune the ACL
- To let the `portfolio-builder` escalate straight to `user` (not only via the
  hub), add `user` to its `can_talk_to`. Mind that this widens the human-facing
  surface and bypasses the hub's single-funnel guarantee — the doc's convention
  keeps the hub the sole `user` contact so the disclaimer always sits in front.
- To make a specialist unreachable from anyone but the hub (already the case here),
  leave its `can_talk_to: [income-lead]` — that's the one-place-owns-the-disclaimer
  guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader discussion
  of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md) for
  mixing model families safely.

### Tune the quarterly ping
- Change `cron:` to fire on your review calendar (e.g. monthly: `"0 9 1 * *"`).
- Switch `when_busy:` from `skip` to `queue` if you'd rather the review wait behind
  a live brief than be dropped. See [`configuration.md`](../configuration.md).

---

## 10. Tips & footguns

- **Keep the hub the only `user`-facing agent.** Only the hub lists `user` in
  `can_talk_to`. That gives you a single funnel: raw shortlists and model
  portfolios always pass through the hub (and the disclaimer) before they reach
  you. If a specialist tried to mail `user` directly, the orchestrator bounces it
  (ACL) and drops a `system` note in their inbox explaining who they *can* message
  — the model self-corrects in-band.

- **The sustainability verdict is the feature, not a suggestion.** An `UNSAFE`
  name the hub drops is the gate working — a high yield backed by an uncovered
  payout is exactly the trap an income portfolio should avoid. Don't "fix" this by
  widening ACLs; the screen→gate→build loop is how the human stays protected.

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
  kill "thanks!/you're welcome!" loops — relevant if a specialist and the hub
  chatter past the gate.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/dividend-income-strategist.yaml
  ./agentainer remove-session -c examples/dividend-income-strategist.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files (your watchlists) or your
  config.

- **Availability shapes the ending.** If `user` is **away** when the hub finishes,
  your model portfolio is *held* (with a `system` "the user is away" ack to the
  hub) rather than lost — read it later with `agentainer user inbox` or flip
  yourself available and it's delivered.

- **This is paper / simulated — enforce the boundary.** The swarm never trades. If
  you wire a `command` to a real brokerage, you have left the designed safety
  envelope; that is outside this example's scope and outside the disclaimer's
  protection. Keep research and execution separate.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions (`--dangerously-skip-permissions`,
  `--yolo`).

- **The quarterly ping self-starts, but `when_busy: skip` can drop it.** If a live
  brief is in flight at 09:00 on a quarter-start, the review ping is silently
  skipped rather than queued. If you rely on the quarterly review, either keep
  `user` quiet around the date, or switch `when_busy` to `queue`.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- `examples/dividend-income-strategist.yaml` — the config this walkthrough is built on.
- `examples/fp-and-a-analyst.yaml` — a sibling finance example (variance analysis,
  not income/dividend research).
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

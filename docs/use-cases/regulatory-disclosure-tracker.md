# Use case: Regulatory disclosure tracker

A concrete, end-to-end walkthrough of the shipped
`examples/regulatory-disclosure-tracker.yaml` swarm — a paper/simulated-only
watch desk that polls the SEC for live corporate disclosures (13F holdings, Form
4 insider transactions, 8-K material events, Reg FD breakdowns) and flags the
notable ones for a human to review. A **tracker-lead** hub takes the scope from
you, delegates the polling to an **edgar-watcher** (13F / 8-K) and an
**insider-watcher** (Form 4), routes the raw filings to a
**disclosure-summarizer** for a plain read and to a **flagger** that scores each
one NOTABLE / NOISE. The flagger is the gate — the human never sees a flag it has
not scored.

> **Paper / simulated only. Educational, not financial advice.** This swarm reads
> public SEC filings and summarizes them for review. It does **not** trade, advise
> on investments, or take any market action. No agent has order-entry or brokerage
> access by design. Every flag is informational and must carry a "for review only
> — not financial advice" note.

Everything below is based on the actual contents of
`examples/regulatory-disclosure-tracker.yaml` and the shipped CLI (`lib/cli.py`)
and mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics;
to run it *for real* you supply the coding-CLI commands (or swap them for mock
bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Compliance-minded researchers, investor-relations watchers, and anyone who wants
a calm, scored read on public company disclosures without babysitting EDGAR
themselves. The swarm encodes the discipline that makes a disclosure feed
trustworthy — one owner of the human-facing surface, watchers that only *read*
public data and never decide meaning, a summarizer that never scores, and a
flagger that decides NOTABLE vs NOISE before anything reaches the human. The
flagger is the last word — the human never sees a flag it has not scored.

It is deliberately a **hub-and-spoke**, not a free-for-all: every request and
every flag passes through tracker-lead, so the point where a raw filing becomes
a scored flag (and where the flagger's gate sits) lives in exactly one place.
Swapping in a `credit-ratings-monitor` agent (see
`examples/credit-ratings-monitor.yaml` — *issuer deterioration*, a different
concern) or adding a second summarizer is a few lines of config.

---

## 2. The topology

```
       edgar-watcher --\
       insider-watcher --\
                         >-- tracker-lead <--> user
  disclosure-summarizer --/
       flagger ---------/
```

Five agents, one directed flow:

1. **`user` → `tracker-lead`** — you send the scope: the tickers (or CIK numbers)
   and the form types to watch (13F, Form 4, 8-K, Reg FD).
2. **`tracker-lead` → `edgar-watcher`** — polls SEC EDGAR for new 13F and 8-K
   filings for the named issuers; returns raw filing pointers (form type, date,
   URL, one-line "what changed").
3. **`tracker-lead` → `insider-watcher`** — pulls Form 4 insider transactions
   (buys/sells, who, size) for the named issuers; returns them raw.
4. **`edgar-watcher` / `insider-watcher` → `tracker-lead`** — the raw filings come
   back.
5. **`tracker-lead` → `disclosure-summarizer`** — the tracker-lead routes a raw
   filing for a plain-English read (what happened, why it could matter, caveats).
6. **`disclosure-summarizer` → `tracker-lead`** — the read comes back.
7. **`tracker-lead` → `flagger`** — the tracker-lead routes the same filing (plus
   the summarizer's read) for a NOTABLE / NOISE / UNCLEAR verdict. The flagger is
   the **gate**: it decides what is worth the human's attention.
8. **`flagger` → `tracker-lead`** — on NOTABLE, the tracker-lead writes the final
   flag (summary + verdict) to `outbox/user/`. On NOISE, it is dropped unless the
   user asked to see noise. On UNCLEAR, the tracker-lead re-delegates.
9. **`tracker-lead` → `user`** — the scored, summarized flag is delivered to you.

The routing above is *enforced* by each agent's `can_talk_to` list. The four
specialists **never** talk to `user` (or to each other) — only tracker-lead does.
If a specialist tried to mail `user` directly, the orchestrator bounces it as a
`system` message and files it in `failed/`.

---

## 3. The config, explained

Here is `examples/regulatory-disclosure-tracker.yaml` in full (role bodies
abbreviated with `...` for readability; the structure, names, ACLs, commands, and
`pings` are exact):

```yaml
swarm:
  name: regulatory-disclosure-tracker
  root: ./regulatory-disclosure-tracker-workspace

defaults:
  capture: none              # claude/codex are auto-upgraded to their hook at up
  can_talk_to: []            # tightened per agent below

agents:
  - name: tracker-lead
    type: claude
    can_talk_to: [edgar-watcher, insider-watcher, disclosure-summarizer, flagger, user]
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: |
          It is the daily pre-market disclosure sweep. For the tickers in scope,
          run the full EDGAR-WATCHER (13F / 8-K) + INSIDER-WATCHER (Form 4) loop,
          route notable filings to DISCLOSURE-SUMMARIZER for a plain read and to
          FLAGGER for a NOTABLE/noise verdict, and post the scored, summarized
          flags to user. If the scope list is missing, ask the user for the
          tickers and form types before delegating.
        cron: "0 6 * * *"             # 06:00 every day (pre-market)
        when_busy: skip
    role: |
      You are the TRACKER-LEAD and the ONLY agent who talks to the human (user). ...
      (1) read the scope, ask ONE clarifying question if it is ambiguous;
       (2) delegate to EDGAR-WATCHER (13F / 8-K); (3) delegate to INSIDER-WATCHER
       (Form 4); (4) route raw filings to DISCLOSURE-SUMMARIZER and to FLAGGER --
       the gate -- and re-route until the FLAGGER scores NOTABLE; (5) only then
       post the scored flag to user. PAPER / SIMULATED ONLY -- not financial
       advice. ...

  - name: edgar-watcher
    type: codex
    can_talk_to: [tracker-lead]
    command: "codex --yolo"
    role: |
      You are the EDGAR-WATCHER. Poll public SEC EDGAR for new 13F and 8-K
      filings for the scoped issuers; return raw filing pointers ... READ public
      data only -- never submit filings, never trade. Report ONLY to
      tracker-lead. ...

  - name: insider-watcher
    type: gemini
    can_talk_to: [tracker-lead]
    command: "gemini --yolo"
    role: |
      You are the INSIDER-WATCHER. Pull Form 4 insider transactions (buys/sells,
      who, size) from public SEC EDGAR for the scoped issuers; return them raw ...
      READ public data only. Report ONLY to tracker-lead. ...

  - name: disclosure-summarizer
    type: claude
    can_talk_to: [tracker-lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the DISCLOSURE-SUMMARIZER. Turn a raw filing into a plain-English
      read -- headline, what happened, why it could matter, caveats ... Never cite
      a figure not in the filing. Do NOT score notability. Report ONLY to
      tracker-lead. For review only -- not financial advice. ...

  - name: flagger
    type: gemini
    can_talk_to: [tracker-lead]
    command: "gemini --yolo"
    role: |
      You are the FLAGGER -- the GATE. Given a raw filing + the summarizer's read,
      reply NOTABLE (with severity) / NOISE / UNCLEAR ... be consistent across
      issuers. Do NOT write the summary. Report ONLY to tracker-lead. ...
```

Field by field:

### `swarm`
- **`name: regulatory-disclosure-tracker`** — the swarm's name (shows up in
  `status`, logs, sessions).
- **`root: ./regulatory-disclosure-tracker-workspace`** — the parent directory for
  the agents' working directories and mailboxes. Each agent's workdir defaults to
  `regulatory-disclosure-tracker-workspace/<name>` (tracker-lead, edgar-watcher,
  insider-watcher, disclosure-summarizer, flagger), and orchestrator state goes
  under `regulatory-disclosure-tracker-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless it overrides them.
- **`capture: none`** — the default turn-detection mode. At `up`, the loader
  **auto-upgrades** this for `claude` and `codex` to their natural hook (the
  `validate` run prints the relevant warnings confirming it). It is a safe floor;
  every agent states its own `can_talk_to`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Each agent below
  states its own list explicitly.

### `tracker-lead` (type: `claude`)
- **`can_talk_to: [edgar-watcher, insider-watcher, disclosure-summarizer, flagger, user]`**
  — the tracker-lead is the hub and the **only agent that can talk to `user`**.
  That last part is the whole point: keep the human-facing surface to one agent
  and put the flagger's gate in front of it.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys, e.g. an SEC
  EDGAR access key.)
- **`pings:`** — the tracker-lead carries the swarm's only scheduled ping (see §3
  *The pings/cron*).
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`;
  the `capture: none` default is auto-upgraded to hook here).

### `edgar-watcher` (type: `codex`)
- **`can_talk_to: [tracker-lead]`** — polls EDGAR and reports raw filings back to
  the tracker-lead and nowhere else. It cannot reach the user, the other watcher,
  the summarizer, or the flagger directly.
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`
  (the `capture: none` default auto-upgrades to the notify hook).

### `insider-watcher` (type: `gemini`)
- **`can_talk_to: [tracker-lead]`** — pulls Form 4 data and returns it to the
  tracker-lead only. It never touches the user.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **Turn detection:** `gemini` has no completion hook, so it relies on **pane
  polling** — the supervisor watches its pane for turn completion.

### `disclosure-summarizer` (type: `claude`)
- **`can_talk_to: [tracker-lead]`** — receives a raw filing from the tracker-lead
  and returns the plain read to the tracker-lead only. It never touches the user
  and never scores notability.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook (auto-upgraded from `capture: none`).

### `flagger` (type: `gemini`)
- **`can_talk_to: [tracker-lead]`** — the gate lives behind the tracker-lead: the
  flagger only ever talks to the tracker-lead, replying NOTABLE / NOISE / UNCLEAR.
  It cannot reach the user, so its verdict is always relayed through the hub.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **Turn detection:** `gemini` → pane polling (no completion hook).

### ACL enforcement

The ACL is **cooperative, not OS isolation** (Decision D15): agents have
filesystem access and *could* write straight into another inbox, but the
orchestrator only ever *releases* and *routes* mail between names on the sender's
`can_talk_to` list. Anything addressed outside that list is bounced back as a
`system` message filed in `failed/`, so a model that forgets the rule
self-corrects in-band. Here that means the four specialists can *only* reach the
tracker-lead, and only the tracker-lead can reach `user` — the flagger's gate is
structurally guaranteed to sit between the raw filing and the human.

### Per-type turn detection

Turn-completion detection is the system clock (the stop → sweep → route → release
→ nudge loop). It is **per `type`**:
- `claude` (`tracker-lead`, `disclosure-summarizer`) → **Stop hook** — fires when
  Claude finishes a turn.
- `codex` (`edgar-watcher`) → **`notify` hook** — fires when Codex finishes.
- `gemini` (`insider-watcher`, `flagger`) → **pane polling** — the supervisor
  reads the pane to decide the turn ended.

A `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
Claude) means completion never fires and the agent pins "busy" forever — which is
why `command` must launch the same CLI family `type` implies.

### The pings / cron

Only the **tracker-lead** has a `pings:` block, and it has exactly one entry:

```yaml
pings:
  - message: |
      It is the daily pre-market disclosure sweep. For the tickers in scope,
      run the full EDGAR-WATCHER (13F / 8-K) + INSIDER-WATCHER (Form 4) loop,
      route notable filings to DISCLOSURE-SUMMARIZER for a plain read and to
      FLAGGER for a NOTABLE/noise verdict, and post the scored, summarized
      flags to user. If the scope list is missing, ask the user for the
      tickers and form types before delegating.
    cron: "0 6 * * *"             # 06:00 every day (pre-market)
    when_busy: skip
```

- **`cron: "0 6 * * *"`** — fires at **06:00 every day** (pre-market, before the
  open), injecting the daily-sweep prompt into the tracker-lead's inbox as a
  nudge.
- **`when_busy: skip`** — if the tracker-lead is mid-turn (a live ad-hoc scope
  request), the ping is **skipped** rather than queued on top of the in-flight
  work. This keeps a scheduled sweep from piling onto a live question.

This is the one piece of self-starting behavior in the swarm; everything else is
event-driven off your mail. See [`configuration.md`](../configuration.md) for the
full `pings:` / `cron:` / `when_busy` grammar.

### What's *not* in this config
- **No `workdir` overrides.** All five agents get the default
  `regulatory-disclosure-tracker-workspace/<name>`, so no mailbox namespacing is
  needed. For the shared-workdir case, see [`custom-workspace.md`](./custom-workspace.md).
- **No per-agent `capture` overrides.** The `defaults: capture: none` is
  auto-upgraded to the type's hook for claude/codex; gemini uses pane polling.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **No trading / order-entry / brokerage command anywhere.** The watchers only
  *read* public SEC EDGAR; nothing in this config can act on a market. Paper /
  simulated only, by design.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/regulatory-disclosure-tracker.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the `capture: none` auto-upgrade
   warnings for the claude/codex agents.
2. Creates the runtime dirs
   (`regulatory-disclosure-tracker-workspace/.agentainer/…`: log, queue, run,
   sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/`
   `about.md` contact card *is* the ACL made visible: the tracker-lead gets
   `outbox/edgar-watcher/`, `outbox/insider-watcher/`,
   `outbox/disclosure-summarizer/`, `outbox/flagger/`, `outbox/user/`; each
   specialist gets only `outbox/tracker-lead/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `tracker-lead`
   and `disclosure-summarizer`, the Codex `notify` hook for `edgar-watcher`; the
   gemini agents are covered by pane polling.
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
> the whole poll → summarize → flag loop route mail with no API keys — the
> mechanics are identical. (The real watchers would read SEC EDGAR; the mock
> versions just exercise the mail flow.)

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the tracker-lead's scored flags as mail (rather
than have them held), turn yourself available first:

```bash
./agentainer user available -c examples/regulatory-disclosure-tracker.yaml
```

This rewrites the `user` contact card in the tracker-lead's `outbox/user/about.md`
to `Status: available`, so the tracker-lead sees you're reachable. (While away,
mail to you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the scope (the tickers and form types to watch) into the swarm,
addressed to the tracker-lead:

```bash
./agentainer send --to tracker-lead -c examples/regulatory-disclosure-tracker.yaml \
  "Track AAPL, MSFT, NVDA, and TSLA. Watch 13F, Form 4, 8-K, and Reg FD. \
   Flag anything notable and summarize it for me."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the tracker-lead, then — because
the inbox was empty — **released into `inbox/`** and the tracker-lead is
**nudged** (the protocol is re-pasted into its pane, including its allowed-recipient
list).

### The mail flowing

Watching the log (§6), you'll see the disclosure loop advance one turn at a time.
Each arrow is a `stop → sweep → route → release → nudge` cycle:

1. **tracker-lead receives the scope.** It reads `inbox/`, asks its one clarifying
   question if scope is ambiguous, then writes delegations into
   `outbox/edgar-watcher/` and `outbox/insider-watcher/`. On stop, those route to
   the watchers.
2. **edgar-watcher polls EDGAR; insider-watcher pulls Form 4.** Each reads its
   inbox, returns raw filing pointers into `outbox/tracker-lead/`. On stop, those
   route back to the tracker-lead.
3. **tracker-lead briefs the summarizer.** It writes a raw filing into
   `outbox/disclosure-summarizer/`. On stop, that routes to the summarizer.
4. **disclosure-summarizer drafts the read.** It reads its inbox, writes the plain
   read, and reports back into `outbox/tracker-lead/`. On stop, that routes to the
   tracker-lead.
5. **tracker-lead briefs the flagger.** It writes the filing + read into
   `outbox/flagger/` (or forwards them in one mail). On stop, that routes to the
   flagger.
6. **flagger gates it.** It reads the filing and replies NOTABLE (with severity) /
   NOISE / UNCLEAR into `outbox/tracker-lead/`. On NOTABLE, the tracker-lead
   writes the final flag (summary + verdict, "for review only — not financial
   advice") into `outbox/user/`. On NOISE, it is dropped unless you asked to see
   noise. On UNCLEAR, the tracker-lead re-delegates. On stop, the flag is
   delivered to your `user` mailbox.
7. **you get the scored flag** — visible with `agentainer user inbox`, or in the
   UI.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion. If you
never send anything, the agents just sit in standby (the daily 06:00 ping is the
only thing that self-starts the loop).

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/regulatory-disclosure-tracker.yaml
```

```
swarm: regulatory-disclosure-tracker   root: ./regulatory-disclosure-tracker-workspace
  tracker-lead          (claude) up idle queue=0 unread=0 talks=edgar-watcher, insider-watcher, disclosure-summarizer, flagger, user
  edgar-watcher         (codex)  up idle queue=0 unread=1 talks=tracker-lead
  insider-watcher       (gemini) up idle queue=0 unread=0 talks=tracker-lead
  disclosure-summarizer (claude) up idle queue=0 unread=0 talks=tracker-lead
  flagger               (gemini) up idle queue=0 unread=0 talks=tracker-lead
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/regulatory-disclosure-tracker.yaml          # whole swarm, last 20
./agentainer logs -c examples/regulatory-disclosure-tracker.yaml -f        # follow live
./agentainer logs flagger -c examples/regulatory-disclosure-tracker.yaml   # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox tracker-lead -c examples/regulatory-disclosure-tracker.yaml
```

Prints the one released message (headers + body), or `tracker-lead: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue tracker-lead -c examples/regulatory-disclosure-tracker.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach flagger -c examples/regulatory-disclosure-tracker.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails the scope. Because every message is natural-language
mail, you can steer the swarm mid-flight through the `user` mailbox or by sending
notes into an agent's inbox.

- **Send a scope change to the tracker-lead.** Realized you want 13F + Form 4 only
  (drop 8-K)? `./agentainer send --to tracker-lead -c examples/regulatory-disclosure-tracker.yaml
  "Drop 8-K and Reg FD from the scope; keep 13F and Form 4 for these tickers."`
  The tracker-lead relays the change down to the watchers.
- **Ask the flagger what it scored.** `./agentainer inbox tracker-lead` (or the UI)
  shows the NOTABLE / NOISE / UNCLEAR verdict the tracker-lead received — so you
  can see the gate doing its job.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different scope), tear it down:

```bash
./agentainer down -c examples/regulatory-disclosure-tracker.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/regulatory-disclosure-tracker.yaml     # resume is the default
```

On `up`, Agentainer reads
`regulatory-disclosure-tracker-workspace/.agentainer/sessions.yaml` (written as
each agent finished its first turn) and reattaches the recorded conversations via
each type's native resume: `claude --resume <id>` for tracker-lead and
disclosure-summarizer, `codex resume <id>` for edgar-watcher, and the gemini
sessions (insider-watcher, flagger) via their recorded ids. A resumed agent is
*not* re-sent the standby prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/regulatory-disclosure-tracker.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Track more form types / add a ratings feed
- Add `DEF 14A` (proxy) or `S-1` (registration) to the scope in the tracker-lead's
  role and in your `send` scope. The watchers already poll EDGAR, so new form
  types are just a scope change.
- To fold in *issuer deterioration* (a distinct concern from disclosure
  tracking), add a `credit-ratings-monitor` agent per
  `examples/credit-ratings-monitor.yaml` with `can_talk_to: [tracker-lead]`, and
  add it to the tracker-lead's `can_talk_to` so ratings changes can be surfaced
  alongside disclosures.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):

- `edgar-watcher: type: claude` (or `hermes`/`gemini`) to put the polling on a
  different model than the flagger.
- `flagger: type: claude` if you want the gate on Claude while keeping gemini out.
- Remember: `gemini`/`hermes` rely on **pane polling** (no completion hook), so
  they don't need (and shouldn't be given) a hook-based `capture`.

### Tune the ACL
- To let the `flagger` escalate straight to `user` (not only via the tracker-lead),
  add `user` to its `can_talk_to`. Mind that this widens the human-facing surface
  and bypasses the tracker-lead's single-funnel guarantee — the doc's convention
  keeps the tracker-lead the sole `user` contact so the gate always sits in front.
- To make a specialist unreachable from anyone but the tracker-lead (already the
  case here), leave its `can_talk_to: [tracker-lead]` — that's the one-place-owns-
  the-gate guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader discussion
  of hub-and-spoke routing.

### Tune the daily ping
- Change `cron:` to fire on your own calendar (e.g. twice daily:
  `"0 6,15 * * *"` for pre-market and midday sweeps).
- Switch `when_busy:` from `skip` to `queue` if you'd rather the sweep wait behind
  a live request than be dropped. See [`configuration.md`](../configuration.md).

---

## 10. Tips & footguns

- **Keep the tracker-lead the only `user`-facing agent.** Only the tracker-lead
  lists `user` in `can_talk_to`. That gives you a single funnel: raw filings and
  summaries always pass through the flagger's gate before they reach you. If a
  specialist tries to mail `user` directly, the orchestrator bounces it (ACL) and
  drops a `system` note in their inbox explaining who they *can* message — the
  model self-corrects in-band.

- **The flagger's NOTABLE/NOISE is the feature, not a failure.** A NOISE verdict
  means the filing was routine and the gate kept it off your desk. The
  tracker-lead drops it (unless you asked to see noise) so you only see scored,
  relevant disclosures. Don't "fix" this by widening ACLs — the loop is how the
  human stays protected from noise.

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
  tracker-lead chatter past the gate.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/regulatory-disclosure-tracker.yaml
  ./agentainer remove-session -c examples/regulatory-disclosure-tracker.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files (the scope you dropped in) or
  your config.

- **Availability shapes the ending.** If `user` is **away** when the tracker-lead
  finishes, your flag is *held* (with a `system` "the user is away" ack to the
  tracker-lead) rather than lost — read it later with `agentainer user inbox` or
  flip yourself available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions (`--dangerously-skip-permissions`,
  `--yolo`).

- **The daily ping self-starts, but `when_busy: skip` can drop it.** If a live
  request is in flight at 06:00, the sweep ping is silently skipped rather than
  queued. If you rely on the daily sweep, either keep `user` quiet around the
  sweep, or switch `when_busy` to `queue`.

- **Paper / simulated only — educational, not financial advice.** This swarm reads
  public SEC filings and summarizes them for human review. It does not trade,
  advise, or act on markets. Treat any `command` that embeds an SEC EDGAR access
  key (or any key) as **sensitive** — never print or commit it. The watchers only
  *read*; nothing in this config can submit a filing or place an order.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- `examples/regulatory-disclosure-tracker.yaml` — the config this walkthrough is built on.
- `examples/credit-ratings-monitor.yaml` — a sibling example for *issuer
  deterioration* (distinct from disclosure tracking).
- `examples/compliance-mapper.yaml` — a sibling example that maps compliance
  *requirements* (distinct from tracking live SEC *disclosures*).
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

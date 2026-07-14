# Use case: Credit ratings monitor

A concrete, end-to-end walkthrough of the shipped
`examples/credit-ratings-monitor.yaml` swarm — an issuer credit-risk + debt-
covenant watch desk that turns a watchlist of names into one consolidated,
flagged list of deteriorating issuers. A **credit-lead** hub takes the watchlist
from you, fans it out to three specialists that each watch a different
deterioration signal — **rating-watcher** (agency actions + spread widening),
**covenant-checker** (leverage / interest-coverage breaches), and
**default-risk-modeler** (probability-of-default) — then reconciles their flags
into a single ranked list before anything reaches the human. The lead is the only
agent that talks to you, so every flag is de-duplicated and evidence-backed.

> **Educational / simulated only — not financial advice.** This swarm reads
> PUBLIC or SIMULATED data and produces model opinions, not a credit rating you
> can rely on and not a trade trigger. Nothing here executes or recommends a
> transaction.

Everything below is based on the actual contents of
`examples/credit-ratings-monitor.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Credit committees, portfolio managers, and fixed-income researchers who need a
repeatable daily/weekly read on whether any name in a watchlist is deteriorating
— without manually polling three disconnected feeds (rating agencies, covenant
packages, and a PD model) and reconciling them by hand. The swarm encodes the
discipline that makes a credit watch trustworthy: one owner of the human-facing
surface, three independent signal specialists that never see each other's output,
and a lead that de-duplicates the flags and attaches the evidence behind every
name so nothing reaches you as a bare assertion.

It is deliberately a **hub-and-spoke**, not a free-for-all: every watchlist and
every deliverable passes through the credit-lead, so the point where three
signals become one ranked list lives in exactly one place. Swapping in a
fourth specialist (e.g. a recovery-analysis agent) or a real market-data agent is
a few lines of config.

---

## 2. The topology

```
       rating-watcher ----\
                           >-- credit-lead <--> user
      covenant-checker ---/
    default-risk-modeler --/
```

Four agents, one directed flow:

1. **`user` → `credit-lead`** — you send the watchlist of issuers (as a file,
   paste, or location to read) and ask for deteriorating-credit flags.
2. **`credit-lead` → `rating-watcher`** — the lead sends the watchlist and asks
   for agency rating actions (downgrades, negative watches, outlook cuts) plus
   spread widening vs. each name's own history and its peer group.
3. **`rating-watcher` → `credit-lead`** — the rating signals come back.
4. **`credit-lead` → `covenant-checker`** — the lead sends the watchlist and the
   capital-structure / covenant terms and asks which leverage / interest-coverage
   covenants are tripped or approaching breach.
5. **`covenant-checker` → `credit-lead`** — the covenant headroom math comes back.
6. **`credit-lead` → `default-risk-modeler`** — the lead sends the watchlist and
   asks for a probability-of-default read per name and which credits are
   deteriorating.
7. **`default-risk-modeler` → `credit-lead`** — the PD estimates come back.
8. **`credit-lead` reconciles** the three returns into ONE consolidated list of
   deteriorating issuers: de-duplicated (a name flagged by two specialists is one
   entry), ranked by severity, with the evidence from each specialist attached,
   and any disagreement called out. Then it writes the list to `outbox/user/`.
9. **`credit-lead` → `user`** — the consolidated, clearly-labeled
   "educational / simulated, not advice" list is delivered to you.

The routing above is *enforced* by each agent's `can_talk_to` list. The three
specialists **never** talk to `user` (or to each other) — only the credit-lead
does. If a specialist tried to mail `user` directly, the orchestrator bounces it
as a `system` message and files it in `failed/`.

---

## 3. The config, explained

Here is `examples/credit-ratings-monitor.yaml` in full (role bodies abbreviated
with `...` for readability; the structure, names, ACLs, commands, and `pings`
are exact):

```yaml
swarm:
  name: credit-ratings-monitor
  root: ./credit-ratings-monitor-workspace

defaults:
  capture: none              # claude/codex are auto-upgraded to their hook at up
  can_talk_to: []            # tightened per agent below

agents:
  - name: credit-lead
    type: claude
    can_talk_to: [rating-watcher, covenant-checker, default-risk-modeler, user]
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: |
          Morning credit sweep. Take the current watchlist (watchlist/issuers.csv,
          or the latest the user sent), run the full RATING-WATCHER ->
          COVENANT-CHECKER -> DEFAULT-RISK-MODELER sweep, reconcile the flags into
          one consolidated list of deteriorating issuers (with the evidence behind
          each), and post it to user. If the watchlist is missing, ask the user for
          it before delegating.
        cron: "0 7 * * 1-5"          # 07:00 Mon-Fri, before the desk opens
        when_busy: skip
    role: |
      You are the CREDIT-LEAD and the only agent who talks to the human (user). ...
      EDUCATIONAL, simulated research desk -- not investment advice, not a trade
      trigger ... (1) read the watchlist, ask ONE clarifying question if scope is
      ambiguous; (2) delegate to RATING-WATCHER; (3) delegate to COVENANT-CHECKER;
       (4) delegate to DEFAULT-RISK-MODELER; (5) reconcile the three returns into
       ONE consolidated, ranked, evidence-backed list; (6) only then post it to
      user (labeled not-advice). ...

  - name: rating-watcher
    type: codex
    can_talk_to: [credit-lead]
    command: "codex --yolo"
    role: |
      You are the RATING-WATCHER. Track agency rating actions + spread widening for
      each name, PUBLIC/SIMULATED data only, cite every claim ... Do NOT estimate PD
      or read covenants. Report ONLY to the credit-lead. ...

  - name: covenant-checker
    type: gemini
    can_talk_to: [credit-lead]
    command: "gemini --yolo"
    role: |
      You are the COVENANT-CHECKER. Monitor leverage / interest-coverage covenants,
      headroom math, tripped vs approaching breach ... Do NOT opine on ratings or PD.
      Report ONLY to the credit-lead. ...

  - name: default-risk-modeler
    type: claude
    can_talk_to: [credit-lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the DEFAULT-RISK-MODELER. Estimate 1-year PD per name, flag
      deteriorating credits, state confidence ... PUBLIC/SIMULATED inputs only. Do
      NOT track agency actions or read covenants. Report ONLY to the credit-lead. ...
```

Field by field:

### `swarm`
- **`name: credit-ratings-monitor`** — the swarm's name (shows up in `status`,
  logs, sessions).
- **`root: ./credit-ratings-monitor-workspace`** — the parent directory for the
  agents' working directories and mailboxes. Each agent's workdir defaults to
  `credit-ratings-monitor-workspace/<name>` (credit-lead, rating-watcher,
  covenant-checker, default-risk-modeler), and orchestrator state goes under
  `credit-ratings-monitor-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless it overrides them.
- **`capture: none`** — the default turn-detection mode. At `up`, the loader
  **auto-upgrades** this for `claude` and `codex` to their natural hook (the
  `validate` run prints three warnings confirming it — see §3 turn-detection
  below). It is a safe floor; every agent states its own `can_talk_to`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Each agent below
  states its own list explicitly.

### `credit-lead` (type: `claude`)
- **`can_talk_to: [rating-watcher, covenant-checker, default-risk-modeler, user]`**
  — the lead is the hub and the **only agent that can talk to `user`**. That last
  part is the whole point: keep the human-facing surface to one agent so the
  three independent signals are always reconciled into one ranked list before you
  see them.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command, e.g. a
  shell alias. Treat command strings as sensitive; they may embed keys.)
- **`pings:`** — the lead carries the swarm's only scheduled ping (see §3 *The
  pings/cron*).
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at
  `up`; the `capture: none` default is auto-upgraded to hook here).

### `rating-watcher` (type: `codex`)
- **`can_talk_to: [credit-lead]`** — reports rating signals back to the lead and
  nowhere else. It cannot reach the user, the covenant-checker, or the modeler.
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`
  (the `capture: none` default auto-upgrades to the notify hook).

### `covenant-checker` (type: `gemini`)
- **`can_talk_to: [credit-lead]`** — receives the watchlist + covenant terms from
  the lead and returns the headroom math to the lead only. It never touches the
  user or the other spokes.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **Turn detection:** `gemini` has no completion hook, so it relies on **pane
  polling** — the supervisor watches its pane for turn completion. (This is why
  the `capture: none` default needs no upgrade for gemini; only claude/codex get
  the auto-hook warnings.)

### `default-risk-modeler` (type: `claude`)
- **`can_talk_to: [credit-lead]`** — the PD read lives behind the lead: the
  modeler only ever talks to the lead. It cannot reach the user, so its estimate
  is always relayed through the hub and reconciled against the other two signals.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook (auto-upgraded from `capture: none`).

### ACL enforcement

The ACL is **cooperative, not OS isolation** (Decision D15): agents have
filesystem access and *could* write straight into another inbox, but the
orchestrator only ever *releases* and *routes* mail between names on the
sender's `can_talk_to` list. Anything addressed outside that list is bounced back
as a `system` message filed in `failed/`, so a model that forgets the rule
self-corrects in-band. Here that means the three specialists can *only* reach the
credit-lead, and only the lead can reach `user` — the single-funnel guarantee
means no single signal (a spread move, a covenant trip, a PD jump) reaches you
without the lead's reconciliation.

### Per-type turn detection

Turn-completion detection is the system clock (the stop → sweep → route → release
→ nudge loop). It is **per `type`**:
- `claude` (`credit-lead`, `default-risk-modeler`) → **Stop hook** — fires when
  Claude finishes a turn.
- `codex` (`rating-watcher`) → **`notify` hook** — fires when Codex finishes.
- `gemini` (`covenant-checker`) → **pane polling** — the supervisor reads the pane
  to decide the turn ended.

A `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
Claude) means completion never fires and the agent pins "busy" forever — which is
why `command` must launch the same CLI family `type` implies.

### The pings / cron

Only the **credit-lead** has a `pings:` block, and it has exactly one entry:

```yaml
pings:
  - message: |
      Morning credit sweep. Take the current watchlist (watchlist/issuers.csv,
      or the latest the user sent), run the full RATING-WATCHER ->
      COVENANT-CHECKER -> DEFAULT-RISK-MODELER sweep, reconcile the flags into
      one consolidated list of deteriorating issuers (with the evidence behind
      each), and post it to user. If the watchlist is missing, ask the user for
      it before delegating.
    cron: "0 7 * * 1-5"          # 07:00 Mon-Fri, before the desk opens
    when_busy: skip
```

- **`cron: "0 7 * * 1-5"`** — fires at **07:00 Monday–Friday** (before the desk
  opens), injecting the morning-sweep prompt into the lead's inbox as a nudge.
- **`when_busy: skip`** — if the lead is mid-turn (a live ad-hoc query), the ping
  is **skipped** rather than queued on top of the in-flight work. This is what
  keeps a scheduled sweep from piling onto a live question.

This is the one piece of self-starting behavior in the swarm; everything else is
event-driven off your mail. See [`configuration.md`](../configuration.md) for the
full `pings:` / `cron:` / `when_busy` grammar.

### What's *not* in this config
- **No `workdir` overrides.** All four agents get the default
  `credit-ratings-monitor-workspace/<name>`, so no mailbox namespacing is needed
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
./agentainer up -c examples/credit-ratings-monitor.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the three `capture: none` auto-upgrade
   warnings for the claude/codex agents.
2. Creates the runtime dirs (`credit-ratings-monitor-workspace/.agentainer/…`:
   log, queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/`
   about.md contact card *is* the ACL made visible: the lead gets
   `outbox/rating-watcher/`, `outbox/covenant-checker/`,
   `outbox/default-risk-modeler/`, `outbox/user/`; each specialist gets only
   `outbox/credit-lead/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `credit-lead`
   and `default-risk-modeler`, the Codex `notify` hook for `rating-watcher`; the
   gemini agent is covered by pane polling.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified") — the role re-states the educational / not-advice framing and the
   data-source boundary.
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents (and drives gemini's pane polling) so one stuck agent can't
   wedge the swarm.

> **Data-source safety:** the lead and every specialist are briefed to use ONLY
> PUBLIC or SIMULATED data and to never touch a brokerage account or execution
> API. Feed the swarm public filings, agency press releases, simulated spread
> series, or your own labeled history — never live credentials.

At the end, `up` prints attach and **`serve`** hints. The `serve` line gives you
the mail-app control-plane UI (threads, live panes, send-as-user, availability
toggle). Drop `--host`/`--token` for the safe loopback-only `127.0.0.1` bind —
the UI can start processes, edit config, and type into agents that may run with
elevated permissions, so it must **never** be exposed on `0.0.0.0` without a
token. See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole rating→covenant→PD→reconcile loop route mail with no API keys — the
> mechanics are identical.

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the lead's consolidated deteriorating-issuer
list as mail (rather than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/credit-ratings-monitor.yaml
```

This rewrites the `user` contact card in the lead's `outbox/user/about.md` to
`Status: available`, so the lead sees you're reachable. (While away, mail to you
is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the watchlist into the swarm, addressed to the lead:

```bash
./agentainer send --to credit-lead -c examples/credit-ratings-monitor.yaml \
  "Watchlist attached in watchlist/issuers.csv -- 12 names, IG + HY. Run the \
   full RATING-WATCHER -> COVENANT-CHECKER -> DEFAULT-RISK-MODELER sweep and flag \
   any deteriorating issuer with the evidence."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the lead, then — because the
inbox was empty — **released into `inbox/`** and the lead is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the credit sweep advance one turn at a time.
Each arrow is a `stop → sweep → route → release → nudge` cycle:

1. **credit-lead receives the watchlist.** It reads `inbox/`, asks its one
   clarifying question if scope is ambiguous, then writes a delegation into
   `outbox/rating-watcher/`. On stop, that routes to the rating-watcher.
2. **rating-watcher tracks agency actions + spreads.** It reads its inbox, writes
   the rating/spread signals, and reports back into `outbox/credit-lead/`. On
   stop, that routes to the lead.
3. **credit-lead briefs the covenant-checker.** It writes the watchlist +
   covenant terms into `outbox/covenant-checker/`. On stop, that routes to the
   covenant-checker.
4. **covenant-checker does the headroom math.** It reads its inbox, writes the
   tripped/approaching-breach findings, and reports back into
   `outbox/credit-lead/`. On stop, that routes to the lead.
5. **credit-lead briefs the default-risk-modeler.** It writes the watchlist into
   `outbox/default-risk-modeler/`. On stop, that routes to the modeler.
6. **default-risk-modeler estimates PD.** It reads its inbox, writes the PD read
   and the deteriorating list, and reports back into `outbox/credit-lead/`. On
   stop, that routes to the lead.
7. **credit-lead reconciles.** It de-duplicates the three returns into one
   ranked, evidence-backed list and writes the consolidated, clearly-labeled
   "educational / simulated, not advice" list into `outbox/user/`. On stop, that's
   delivered to your `user` mailbox.
8. **you get the consolidated list** — visible with `agentainer user inbox`, or in
   the UI.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion. If you
never send anything, the agents just sit in standby (the daily morning ping is
the only thing that self-starts the loop).

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/credit-ratings-monitor.yaml
```

```
swarm: credit-ratings-monitor   root: ./credit-ratings-monitor-workspace
  credit-lead        (claude) up idle queue=0 unread=0 talks=rating-watcher, covenant-checker, default-risk-modeler, user
  rating-watcher     (codex)  up idle queue=0 unread=1 talks=credit-lead
  covenant-checker   (gemini) up idle queue=0 unread=0 talks=credit-lead
  default-risk-modeler (claude) up idle queue=0 unread=0 talks=credit-lead
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/credit-ratings-monitor.yaml          # whole swarm, last 20
./agentainer logs -c examples/credit-ratings-monitor.yaml -f        # follow live
./agentainer logs rating-watcher -c examples/credit-ratings-monitor.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox credit-lead -c examples/credit-ratings-monitor.yaml
```

Prints the one released message (headers + body), or `credit-lead: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue credit-lead -c examples/credit-ratings-monitor.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach covenant-checker -c examples/credit-ratings-monitor.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails the framing. Because every message is natural-language
mail, you can steer the swarm mid-flight through the `user` mailbox or by sending
notes into an agent's inbox.

- **Send a clarification to the lead.** Realized the watchlist should be HY-only?
  `./agentainer send --to credit-lead -c examples/credit-ratings-monitor.yaml
  "Re-scope to high-yield names only and add a cross-default flag to the
  covenant sweep."` The lead relays the change down the chain and re-reconciles.
- **Ask why a name was flagged.** `./agentainer inbox credit-lead` (or the UI)
  shows the consolidated list with each specialist's evidence — so you can see
  which signal drove a flag and where two specialists agreed or diverged.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're done (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/credit-ratings-monitor.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/credit-ratings-monitor.yaml     # resume is the default
```

On `up`, Agentainer reads `credit-ratings-monitor-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the
credit-lead and default-risk-modeler, `codex resume <id>` for the rating-watcher,
and the gemini session via its recorded id. A resumed agent is *not* re-sent the
standby prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/credit-ratings-monitor.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a fourth specialist (recovery / liquidity)
To fold in a recovery-analysis or liquidity agent, add a fifth agent the lead can
brief after the three core signals are in:

```yaml
  - name: recovery-analyst
    type: gemini
    can_talk_to: [credit-lead]
    command: "gemini --yolo"
    role: |
      You are the RECOVERY-ANALYST. Given the lead's watchlist, estimate senior/
      subordinated recovery per name from the capital structure and collateral the
      lead supplies (PUBLIC/SIMULATED data only). Report ONLY to the credit-lead.
```

Then add `recovery-analyst` to the lead's `can_talk_to` so it can be briefed, and
have the lead fold recovery into the consolidated list.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `rating-watcher: type: claude` (or `hermes`) to put the agency/spread tracking on
  a different model than codex.
- `default-risk-modeler: type: codex` if you'd rather the PD math on Codex.
- Remember: `gemini`/`hermes` rely on **pane polling** (no completion hook), so
  they don't need (and shouldn't be given) a hook-based `capture`.

### Tune the ACL
- To let a specialist escalate straight to `user` (not only via the lead), add
  `user` to its `can_talk_to`. Mind that this widens the human-facing surface and
  bypasses the lead's single-funnel guarantee — the doc's convention keeps the
  lead the sole `user` contact so every flag is reconciled before you see it.
- To make a specialist unreachable from anyone but the lead (already the case
  here), leave its `can_talk_to: [credit-lead]` — that's the one-place-owns-the-
  funnel guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md)
  for mixing model families safely.

### Tune the morning ping
- Change `cron:` to fire on your desk calendar (e.g. `0 7 * * *` for every day, or
  a specific pre-close time).
- Switch `when_busy:` from `skip` to `queue` if you'd rather the sweep wait behind
  a live query than be dropped. See [`configuration.md`](../configuration.md).

---

## 10. Tips & footguns

- **Keep the lead the only `user`-facing agent.** Only the lead lists `user` in
  `can_talk_to`. That gives you a single funnel: raw rating, covenant, and PD
  signals always pass through the lead's reconciliation before they reach you, so
  nothing shows up as a bare, un-evidenced flag. If a specialist tried to mail
  `user` directly, the orchestrator bounces it (ACL) and drops a `system` note in
  their inbox explaining who they *can* message — the model self-corrects in-band.

- **The reconciliation step is the feature, not overhead.** A name flagged by two
  specialists is one entry with two evidence trails; a name where signals disagree
  is called out, not averaged away. Don't "fix" this by widening ACLs — the
  de-duplication and disagreement surfacing is how the human stays protected from a
  single noisy signal.

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
  chatter past the gate.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/credit-ratings-monitor.yaml
  ./agentainer remove-session -c examples/credit-ratings-monitor.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files (the watchlist you dropped in)
  or your config.

- **Availability shapes the ending.** If `user` is **away** when the lead finishes,
  your consolidated list is *held* (with a `system` "the user is away" ack to the
  lead) rather than lost — read it later with `agentainer user inbox` or flip
  yourself available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions (`--dangerously-skip-permissions`,
  `--yolo`).

- **Educational / not-advice is load-bearing.** Both the header comment and the
  lead's role state that this is a simulated research desk producing model
  opinions, not a credit rating or a trade trigger, and that every deliverable is
  labeled accordingly. Keep that framing; do not feed the swarm real brokerage
  credentials or an execution API.

- **The morning ping self-starts, but `when_busy: skip` can drop it.** If a live
  query is in flight at 07:00, the sweep ping is silently skipped rather than
  queued. If you rely on the morning list, either keep `user` quiet around the
  sweep, or switch `when_busy` to `queue`.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- `examples/credit-ratings-monitor.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

# Use case: Quant factor miner

A concrete, end-to-end walkthrough of the shipped
`examples/quant-factor-miner.yaml` swarm — an **evolutionary factor-discovery
pipeline** that turns a research hypothesis into factors (written in a small DSL),
backtests each on a TRAIN/TEST split, critiques the result for overfitting, then
**mutates / cross-overs / selects** the survivors generation after generation.
A **strategist** hub owns the idea→evolve loop and is the only agent that talks
to you. The **backtester** is the single enforcer of the train/test split: it
holds the TEST window hidden from every other agent, so factors are trained and
selected on TRAIN only and *cannot silently overfit the out-of-sample window*.

Everything below is based on the actual contents of
`examples/quant-factor-miner.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> ⚠️ **Paper / simulated only — educational, not financial advice.** This swarm
> must run on synthetic or clearly-labeled historical data, with no live orders
> and no real money. Factors that look great in-sample routinely fail
> out-of-sample. The output is research, not a trade.

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Quant-curious researchers, hobbyist systematic traders, and students who want to
*discover and evolve* new alpha factors without hand-rolling the whole
hypothesis→code→backtest→critique→mutate loop themselves. The swarm encodes the
discipline that makes factor research honest: one owner of the human-facing
surface, a coder that only ever sees TRAIN, a backtester that hides TEST, a critic
that gates overfitting, and a mutator that evolves on TRAIN RankIC alone.

It is deliberately a **hub-and-spoke**, not a free-for-all: every hypothesis and
every deliverable passes through the strategist, so the one place where factors
meet the gate (and where the train/test boundary is held) lives in exactly one
agent. Swapping in a dedicated `backtesting-auditor` to independently validate a
final factor (see `examples/backtesting-auditor.yaml`) is a few lines of config.

---

## 2. The topology

```
        factor-coder --\
        backtester    ---> strategist <--> user
        critic        ---/
        mutator      --/
```

Five agents, one directed evolutionary loop:

1. **`user` → `strategist`** — you send a research hypothesis (and, optionally, a
   pointer to labeled/synthetic data). The strategist may ask one clarifying
   question about universe, horizon, or label.
2. **`strategist` → `factor-coder`** — the strategist sends the hypothesis and
   asks for a factor in the agreed DSL, **trained ONLY on the TRAIN window**.
3. **`factor-coder` → `strategist`** — the factor code comes back (TRAIN-only).
4. **`strategist` → `backtester`** — the strategist hands over the factor. The
   backtester runs it with the **TEST window HIDDEN** and reports RankIC / returns
   / turnover **on TRAIN only**.
5. **`backtester` → `strategist`** — the train-only result comes back (or a LEAK
   flag if it spotted future/TEST reference).
6. **`strategist` → `critic`** — the strategist routes the factor + the train-only
   result. The critic is the **sanity gate**: it checks overfitting risk,
   economic intuition, and RankIC significance, and replies `CLEAR` or `BOUNCE`
   (with specifics).
7. **`critic` → `strategist`** — on `BOUNCE`, the strategist re-delegates the fix
   and re-routes until the critic signs off. On `CLEAR`, it proceeds to mutate.
8. **`strategist` → `mutator`** — the strategist sends the cleared factor + its
   TRAIN RankIC. The mutator **mutates / crossover / selects** by TRAIN RankIC and
   returns the survivors + the next generation's candidates.
9. **`mutator` → `strategist`** — the new generation seeds loop step 2 again.
10. After N generations, **`strategist` → `user`** — the convergence report: top
    factors by RankIC, what survived, what died, the economic story, and the
    explicit note that TEST was never touched.

The routing above is *enforced* by each agent's `can_talk_to` list. The four
specialists **never** talk to `user` (or to each other) — only the strategist
does. The train/test boundary is *enforced* by the backtester holding TEST;
`factor-coder` and `mutator` are structurally unable to reach it.

---

## 3. The config, explained

Here is `examples/quant-factor-miner.yaml` in full (role bodies abbreviated with
`...` for readability; the structure, names, ACLs, and commands are exact):

```yaml
swarm:
  name: quant-factor-miner
  root: ./quant-factor-miner-workspace

defaults:
  capture: none              # claude/codex are auto-upgraded to their hook at up
  can_talk_to: []            # tightened per agent below

agents:
  - name: strategist
    type: claude
    can_talk_to: [factor-coder, backtester, critic, mutator, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the STRATEGIST -- the only agent who talks to the human (user) and
      the owner of the evolutionary factor-discovery loop ... Run it like this:
       (1) read the user's hypothesis; ask ONE clarifying question if scope is
       ambiguous; (2) delegate to FACTOR-CODER (TRAIN only, never TEST);
       (3) delegate to BACKTESTER (TEST hidden, train-only RankIC back);
       (4) route to CRITIC -- the sanity gate -- and re-route until CLEAR;
       (5) send the cleared factor to MUTATOR to evolve by RankIC; (6) loop and
       write the final convergence report to user. ...

  - name: factor-coder
    type: codex
    can_talk_to: [strategist]
    command: "codex --yolo"
    role: |
      You are the FACTOR-CODER ... write the factor in the agreed DSL, TRAIN only,
      never receive/request/compute TEST ... Report ONLY to the strategist. ...

  - name: backtester
    type: gemini
    can_talk_to: [strategist]
    command: "gemini --yolo"
    role: |
      You are the BACKTESTER -- the ENFORCER of the train/test split ... RUN on
      TRAIN only, report RankIC on TRAIN, HIDE TEST, flag any leakage ... Report
      ONLY to the strategist. ...

  - name: critic
    type: claude
    can_talk_to: [strategist]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the CRITIC -- the SANITY GATE ... overfitting, economic intuition,
      RankIC significance, leakage ... reply CLEAR or BOUNCE ... Report ONLY to the
      strategist. ...

  - name: mutator
    type: codex
    can_talk_to: [strategist]
    command: "codex --yolo"
    role: |
      You are the MUTATOR -- mutate / crossover / select by TRAIN RankIC only,
      never see TEST ... Report ONLY to the strategist. ...
```

Field by field:

### `swarm`
- **`name: quant-factor-miner`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./quant-factor-miner-workspace`** — the parent directory for the
  agents' working directories and mailboxes. Each agent's workdir defaults to
  `quant-factor-miner-workspace/<name>` (strategist, factor-coder, backtester,
  critic, mutator), and orchestrator state goes under
  `quant-factor-miner-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless it overrides them.
- **`capture: none`** — the default turn-detection mode. At `up`, the loader
  **auto-upgrades** this for `claude` and `codex` to their natural hook (the
  `validate` run prints warnings confirming it). It is a safe floor; every agent
  states its own `can_talk_to`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Each agent below
  states its own list explicitly.

### `strategist` (type: `claude`) — the hub
- **`can_talk_to: [factor-coder, backtester, critic, mutator, user]`** — the hub
  and the **only agent that can talk to `user`**. That last part is the whole
  point: keep the human-facing surface to one agent and own the evolutionary loop.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`;
  the `capture: none` default is auto-upgraded to hook here).

### `factor-coder` (type: `codex`)
- **`can_talk_to: [strategist]`** — receives the hypothesis and returns the
  factor code to the strategist only. It cannot reach `user`, the backtester, the
  critic, or the mutator directly, and it never receives TEST.
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `backtester` (type: `gemini`) — the split enforcer
- **`can_talk_to: [strategist]`** — receives the factor from the strategist and
  returns train-only results to the strategist only. It is the **sole owner of
  TEST**; it never forwards TEST anywhere.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **Turn detection:** `gemini` has no completion hook, so it relies on **pane
  polling** — the supervisor watches its pane for turn completion.

### `critic` (type: `claude`)
- **`can_talk_to: [strategist]`** — the gate lives behind the strategist: it only
  ever talks to the strategist, replying `CLEAR` or `BOUNCE`. It cannot reach the
  user, so its verdict is always relayed through the hub.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook (auto-upgraded from `capture: none`).

### `mutator` (type: `codex`)
- **`can_talk_to: [strategist]`** — receives cleared factors + TRAIN RankIC and
  returns the evolved survivors to the strategist only. It selects **only on
  TRAIN RankIC** and never sees TEST.
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → `notify` hook (auto-upgraded from `capture: none`).

### ACL enforcement

The ACL is **cooperative, not OS isolation** (Decision D15): agents have
filesystem access and *could* write straight into another inbox, but the
orchestrator only ever *releases* and *routes* mail between names on the sender's
`can_talk_to` list. Anything addressed outside that list is bounced back as a
`system` message filed in `failed/`, so a model that forgets the rule
self-corrects in-band. Here that means the four specialists can *only* reach the
strategist, and only the strategist can reach `user` — the critic's gate is
structurally guaranteed to sit between each factor and the human.

### Per-type turn detection

Turn-completion detection is the system clock (the stop → sweep → route → release
→ nudge loop). It is **per `type`**:
- `claude` (`strategist`, `critic`) → **Stop hook** — fires when Claude finishes a
  turn.
- `codex` (`factor-coder`, `mutator`) → **`notify` hook** — fires when Codex
  finishes.
- `gemini` (`backtester`) → **pane polling** — the supervisor reads the pane to
  decide the turn ended.

A `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
Claude) means completion never fires and the agent pins "busy" forever — which is
why `command` must launch the same CLI family `type` implies.

### The train/test boundary (the load-bearing invariant)

Neither `capture` nor the ACL touches the data windows — that's the backtester's
job, written into its role and reinforced by the strategist's loop discipline:
- `factor-coder` is told, in its role, **TRAIN only, never TEST**.
- `backtester` is told it **HOLDS TEST** and returns **train-only** RankIC, and to
  flag any leakage it spots.
- `mutator` selects **only on TRAIN RankIC**, never TEST.

Because the backtester is the only agent permitted to hold the test window, and
the strategist never asks anyone for a TEST number, the in-sample loop cannot
cheat. A real out-of-sample check is a deliberate, separate, out-of-band step the
human (or a pinned schedule) performs once — it is *not* part of the evolving
loop.

### What's *not* in this config
- **No `workdir` overrides.** All five agents get the default
  `quant-factor-miner-workspace/<name>`, so no mailbox namespacing is needed. For
  the shared-workdir case, see [`custom-workspace.md`](./custom-workspace.md).
- **No per-agent `capture` overrides.** The `defaults: capture: none` is
  auto-upgraded to the type's hook for claude/codex; gemini uses pane polling.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on.
- **No `pings:` in this swarm.** The evolve loop is event-driven off your mail;
  the strategist runs generations only when you send a hypothesis. Add a `pings:`
  block to a generation if you want it self-starting (see
  [`configuration.md`](../configuration.md)).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/quant-factor-miner.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the auto-upgrade warnings for the
   claude/codex agents.
2. Creates the runtime dirs (`quant-factor-miner-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/`
   `about.md` contact card *is* the ACL made visible: the strategist gets
   `outbox/factor-coder/`, `outbox/backtester/`, `outbox/critic/`,
   `outbox/mutator/`, `outbox/user/`; each specialist gets only
   `outbox/strategist/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `strategist`
   and `critic`, the Codex `notify` hook for `factor-coder` and `mutator`; the
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
> the whole hypothesis→factor→backtest→critique→mutate loop route mail with no API
> keys — the mechanics are identical. Keep the data synthetic.

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the strategist's convergence report as mail
(rather than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/quant-factor-miner.yaml
```

This rewrites the `user` contact card in the strategist's `outbox/user/about.md`
to `Status: available`, so the strategist sees you're reachable. (While away, mail
to you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send a hypothesis into the swarm, addressed to the strategist:

```bash
./agentainer send --to strategist -c examples/quant-factor-miner.yaml \
  "Hypothesis: low-volatility stocks earn a premium. Mine factors that capture \
   'earned volatility not yet priced in' and show me the best 3 by RankIC after \
   5 generations. Train window: 2010-2019, synthetic universe in data/train/."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the strategist, then — because
the inbox was empty — **released into `inbox/`** and the strategist is **nudged**
(the protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the evolve loop advance one turn at a time.
Each arrow is a `stop → sweep → route → release → nudge` cycle; TEST is never in
any of them:

1. **strategist receives the hypothesis.** It reads `inbox/`, asks its one
   clarifying question if scope is ambiguous, then writes a delegation into
   `outbox/factor-coder/`. On stop, that routes to the factor-coder.
2. **factor-coder writes the factor (TRAIN only).** It reads its inbox, writes the
   DSL factor (no TEST reference), and reports back into `outbox/strategist/`. On
   stop, that routes to the strategist.
3. **strategist briefs the backtester.** It writes the factor into
   `outbox/backtester/`. On stop, that routes to the backtester.
4. **backtester runs TRAIN, hides TEST.** It reads its inbox, runs the factor on
   TRAIN, reports RankIC / returns / turnover on TRAIN (or a LEAK flag), with TEST
   untouched, and reports back into `outbox/strategist/`. On stop, that routes to
   the strategist.
5. **strategist routes to the critic.** It writes the factor + train-only result
   into `outbox/critic/`. On stop, that routes to the critic.
6. **critic gates it.** It reads the draft and replies `CLEAR` or `BOUNCE` (with
   specifics) into `outbox/strategist/`. On `BOUNCE`, the strategist re-delegates
   the fix and re-routes until the critic signs off. On `CLEAR`, it proceeds.
7. **strategist sends to the mutator.** It writes the cleared factor + TRAIN RankIC
   into `outbox/mutator/`. On stop, that routes to the mutator.
8. **mutator evolves (TRAIN RankIC only).** It reads its inbox, mutates /
   crossover / selects by TRAIN RankIC, and returns survivors + next-gen candidates
   into `outbox/strategist/`. On stop, that routes to the strategist.
9. **loop until N generations done**, then the strategist writes the convergence
   report into `outbox/user/`. On stop, that's delivered to your `user` mailbox.
10. **you get the report** — visible with `agentainer user inbox`, or in the UI.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion. If you
never send a hypothesis, the agents just sit in standby.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/quant-factor-miner.yaml
```

```
swarm: quant-factor-miner   root: ./quant-factor-miner-workspace
  strategist    (claude) up idle queue=0 unread=0 talks=factor-coder, backtester, critic, mutator, user
  factor-coder  (codex)  up idle queue=0 unread=1 talks=strategist
  backtester    (gemini) up idle queue=0 unread=0 talks=strategist
  critic        (claude) up idle queue=0 unread=0 talks=strategist
  mutator       (codex)  up idle queue=0 unread=0 talks=strategist
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/quant-factor-miner.yaml          # whole swarm, last 20
./agentainer logs -c examples/quant-factor-miner.yaml -f        # follow live
./agentainer logs backtester -c examples/quant-factor-miner.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event. Watch for a `LEAK` note from the backtester if a
factor tried to touch TEST.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox strategist -c examples/quant-factor-miner.yaml
```

Prints the one released message (headers + body), or `strategist: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue strategist -c examples/quant-factor-miner.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach backtester -c examples/quant-factor-miner.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or by sending notes
into an agent's inbox.

- **Send a better hypothesis to the strategist.** Realized you wanted a
  neutralized factor? `./agentainer send --to strategist -c
  examples/quant-factor-miner.yaml "Add sector-neutralization to the next
  generation and re-run 5 generations."` The strategist relays the change down
  the chain and re-routes past the critic.
- **Ask the critic what it bounced.** `./agentainer inbox strategist` (or the UI)
  shows the `BOUNCE` note the strategist received — which defect, what to fix — so
  you can see the gate doing its job.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/quant-factor-miner.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/quant-factor-miner.yaml     # resume is the default
```

On `up`, Agentainer reads
`quant-factor-miner-workspace/.agentainer/sessions.yaml` (written as each agent
finished its first turn) and reattaches the recorded conversations via each type's
native resume: `claude --resume <id>` for the strategist and critic, `codex resume
<id>` for the factor-coder and mutator, and the gemini session via its recorded
id. A resumed agent is *not* re-sent the standby prompt (its prior context is
restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/quant-factor-miner.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a dedicated out-of-sample validator
This swarm deliberately keeps TEST hidden from the evolving loop. To get an honest
out-of-sample read on the final survivor, add a `backtesting-auditor` agent (see
`examples/backtesting-auditor.yaml`) that the strategist can brief **once** at the
end, with the TEST window released to it alone:

```yaml
  - name: out-of-sample-auditor
    type: claude
    can_talk_to: [strategist]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the OUT-OF-SAMPLE AUDITOR. The strategist releases the held-out TEST
      window to you ONCE, after evolution. Backtest the final survivor on TEST and
      report the TEST RankIC vs. the TRAIN RankIC the critic cleared -- no
      re-tuning allowed. Report ONLY to the strategist.
```

Then add `out-of-sample-auditor` to the strategist's `can_talk_to`. This keeps the
auditor *outside* the loop so it can't be overfit against.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `factor-coder: type: claude` (or `hermes`/`gemini`) to put the coding on a
  different model than the strategist.
- `backtester: type: claude` if you want the split enforcer on Claude while keeping
  gemini out.
- Remember: `gemini`/`hermes` rely on **pane polling** (no completion hook), so
  they don't need (and shouldn't be given) a hook-based `capture`.

### Tune the ACL
- To let the `critic` escalate straight to `user`, add `user` to its `can_talk_to`.
  Mind that this widens the human-facing surface and bypasses the strategist's
  single-funnel guarantee — the doc's convention keeps the strategist the sole
  `user` contact so the gate always sits in front.
- To make a specialist unreachable from anyone but the strategist (already the
  case here), leave its `can_talk_to: [strategist]` — that's the one-place-owns-
  the-loop guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md)
  for mixing model families safely.

### Add a self-starting generation
This swarm is event-driven (it evolves only when you send a hypothesis). To make
it self-start, add a `pings:` block to the strategist with a `cron:` schedule and
a `when_busy` policy — see [`configuration.md`](../configuration.md).

---

## 10. Tips & footguns

- **Keep the strategist the only `user`-facing agent.** Only the strategist lists
  `user` in `can_talk_to`. That gives you a single funnel: raw factors and
  reports always pass through the critic's gate before they reach you. If a
  specialist tried to mail `user` directly, the orchestrator bounces it (ACL) and
  drops a `system` note in their inbox explaining who they *can* message — the
  model self-corrects in-band.

- **The critic's `BOUNCE` is the feature, not a failure.** A bounced factor means
  the RankIC looked like overfitting, the intuition was weak, or significance was
  absent — and the gate caught it. The strategist re-delegates and re-routes until
  `CLEAR`. Don't "fix" this by widening ACLs — the loop is how the human stays
  protected.

- **The train/test split is enforced by the backtester, not the config.** The
  YAML can't cryptographically hide a file from an agent — the guarantee lives in
  the backtester's role (it HOLDS TEST and returns TRAIN-only numbers) plus the
  strategist's discipline (it never asks for a TEST number). If you extend this
  swarm, do NOT add a path that lets `factor-coder` or `mutator` read the test
  window, or you've silently broken the one invariant that stops overfitting.

- **Watch for the `LEAK` flag.** If the backtester ever reports a factor references
  TEST or future labels, treat it as a hard stop — the strategist should reject
  that factor, not mutate it forward. A leak that slips into the mutator pollutes
  every descendant.

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

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/quant-factor-miner.yaml
  ./agentainer remove-session -c examples/quant-factor-miner.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files (the data you dropped in) or
  your config.

- **Availability shapes the ending.** If `user` is **away** when the strategist
  finishes, your convergence report is *held* (with a `system` "the user is away"
  ack to the strategist) rather than lost — read it later with
  `agentainer user inbox` or flip yourself available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions (`--dangerously-skip-permissions`,
  `--yolo`).

- **Paper / simulated only — not financial advice.** Run on synthetic or
  clearly-labeled historical data; no live orders, no real money. In-sample RankIC
  is not a promise about the future. The output is research, not a trade.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- `examples/quant-factor-miner.yaml` — the config this walkthrough is built on.
- `examples/backtesting-auditor.yaml` — a sibling that independently validates a
  submitted backtest (a complementary, not overlapping, job).
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

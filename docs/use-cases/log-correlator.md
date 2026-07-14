# Use case: Log correlator

A concrete, end-to-end walkthrough of the shipped
`examples/log-correlator.yaml` swarm — a four-agent investigation that turns
"our checkout 500s for user X" into a pinpointed root cause. A **correlator
hub** takes your trace id or symptom, fans work out to three specialists, and
reassembles the answer: an **ingester** pulls the per-service spans, a
**timeliner** merges them into one correct cross-service timeline, and a
**hypothesizer** names the likeliest failing service and fix, ranked by
evidence. The cross-service timeline is the product — it turns "every service
says it's fine" into a root cause.

Everything below is based on the actual contents of
`examples/log-correlator.yaml` and the shipped CLI (`lib/cli.py`) and mailroom
(`lib/mail.py`). No API keys are needed to understand the mechanics; to run it
*for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md)
> first, then the four-folders recap in the repo `README.md`. The one-line
> version: an agent **reads a file** to receive mail and **writes a file** to
> send it; the orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

On-call engineers, SREs, and backend/platform teams who get paged on a failing
request and need a fast, structured teardown of what happened *across* services
— without manually tailing N dashboards or pasting three services' logs into one
window. The swarm encodes the discipline that makes a postmortem honest: one
agent owns the investigation, one agent gathers raw spans, one agent builds the
shared timeline, and one agent reasons over that timeline and ranks causes by
evidence — none of them overlap, so the chronology stays uncontested.

It is deliberately a **hub-and-spoke**, not a free-for-all: every trace id and
every deliverable passes through the correlator, so the timeline has exactly one
authority. The spokes never talk to each other — only back up to the correlator
— which is what keeps the merged order correct.

---

## 2. The topology

```
            ingester
                |
   timeliner --- correlator --- user
                |
           hypothesizer
```

Four agents, one directed flow:

```
   user → correlator          you send a trace id or a symptom
   correlator → ingester      the hub asks for the raw per-service spans
   ingester → correlator      returns the extracts, grouped by service
   correlator → timeliner     forwards the extracts to be merged
   timeliner → correlator     returns the ordered cross-service timeline
   correlator → hypothesizer  forwards the timeline to be reasoned over
   hypothesizer → correlator  returns the ranked root-cause hypotheses
   correlator → user          delivers the timeline + top hypothesis to you
```

The flow is a strict **pipeline through the hub** — `ingester`, `timeliner`, and
`hypothesizer` each only ever report back to `correlator`, never to a sibling or
to `user`. That is the whole point: the cross-service timeline is built by one
agent (the timeliner) from one set of inputs (the ingester's extracts), so no
two agents can disagree about ordering.

The routing above is *enforced* by each agent's `can_talk_to` list. An agent can
only deliver to names on its own list; anything else is bounced back as a
`system` message and filed in `failed/`. Notably, the three spokes **never**
talk to `user` directly — only the correlator does.

---

## 3. The config, explained

Here is `examples/log-correlator.yaml` in full:

```yaml
swarm:
  name: log-correlator
  root: ./log-correlator-workspace

defaults:
  capture: none              # mock agents don't fire a turn-completion hook;
                             # for real claude/codex/gemini agents this auto-upgrades
  can_talk_to: []            # tightened per agent below

agents:
  - name: correlator
    type: claude
    can_talk_to: [ingester, timeliner, hypothesizer, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the CORRELATOR. You are the single brain that owns the diagnosis.
      ... (takes the trace id, fans work to the three spokes, reassembles) ...

  - name: ingester
    type: codex
    can_talk_to: [correlator]
    command: "codex --yolo"
    role: |
      You are the INGESTER. Given a trace id, pull the relevant spans/log lines
      for THAT trace from each named service, grouped by service, verbatim where
      it matters. You do not diagnose. ...

  - name: timeliner
    type: gemini
    can_talk_to: [correlator]
    command: "gemini --yolo"
    role: |
      You are the TIMELINER. Merge the per-service extracts into ONE correct
      cross-service timeline, sorted by timestamp, with per-hop latency and the
      labeled failure point(s). You do not speculate on cause. ...

  - name: hypothesizer
    type: claude
    can_talk_to: [correlator]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the HYPOTHESIZER. Read the timeline, pinpoint the first failed or
      incomplete step, rank candidate root causes by evidence, name the fix for
      the top one, and say what the timeline does NOT prove. ...
```

Field by field:

### `swarm`
- **`name: log-correlator`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./log-correlator-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent's workdir defaults to
  `log-correlator-workspace/<name>` (`…/correlator`, `…/ingester`,
  `…/timeliner`, `…/hypothesizer`) — all **private**, none shared. Orchestrator
  state goes under `log-correlator-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — a safe floor so the config validates and runs cleanly
  with mock bash-loop agents (which have no turn-completion hook). For *real*
  `claude`/`codex`/`gemini` agents the loader auto-upgrades this to the type's
  natural capture mode (see Turn detection below), so nothing has to be set
  per-agent to make it work live.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `correlator` (type: `claude`)
- **`can_talk_to: [ingester, timeliner, hypothesizer, user]`** — the hub: it
  delegates to the three specialists and is the **only agent that can talk to
  `user`**. Keep the human-facing surface to a single agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command, e.g. a
  shell alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the correlator waits for your trace id instead of
  proactively mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at
  `up`).

### `ingester` (type: `codex`)
- **`can_talk_to: [correlator]`** — the ingester only reports back to the
  correlator. It deliberately cannot reach the timeliner, the hypothesizer, or
  the `user`; raw-span gathering is owned by one place and fed upward.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "given a trace id (or a symptom + the named services), pull the
  relevant spans/log lines for THAT trace from each named service, return them
  grouped by service with the service name and a timestamp on every line, and
  say so explicitly for any service that has nothing for the trace." It gathers
  and labels; it does not diagnose.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `timeliner` (type: `gemini`)
- **`can_talk_to: [correlator]`** — the timeliner only reports back to the
  correlator. It never sees the other spokes, so the merged chronology has a
  single author.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **`role`** — "merge the per-service extracts into ONE correct cross-service
  timeline, sorted by timestamp (not service-local order); for each hop show
  service/event/per-step latency/the success-or-failure; mark the failure point;
  flag gaps as 'incomplete / timed out'." It builds the timeline; it does *not*
  speculate on cause (that's the hypothesizer's job).
- **Turn detection:** `gemini` → **pane polling** (no completion hook exists for
  Gemini, so the supervisor watches the pane for a settled prompt). This is the
  one spoke whose `type` needs a polling capture rather than a hook.

### `hypothesizer` (type: `claude`)
- **`can_talk_to: [correlator]`** — the hypothesizer only reports back to the
  correlator. It never contacts the other spokes or `user` directly.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **`role`** — "read the timeline end to end, pinpoint the first failed/incomplete
  step, rank candidate root causes by how well the evidence fits (with a
  one-line rationale and confidence each), name the specific fix for the top one
  and the confirming evidence, and state what the timeline does NOT prove." It
  reasons over the timeliner's product; it does not re-gather logs.
- **Turn detection:** `claude` → Stop hook.

### ACL enforcement, concretely

The `can_talk_to` lists are **cooperative**, not OS isolation: the model is told
its allowed recipients and the orchestrator only delivers to names on that list.
If, say, the `ingester` tried to write into `outbox/timeliner/`, the orchestrator
would bounce it as a `system` message and drop a note in the ingester's inbox
explaining who it *can* message — the model self-corrects in-band. The visible
form of the ACL is the `outbox/<peer>/about.md` contact card the orchestrator
plants for each allowed recipient: the correlator gets
`outbox/ingester/`, `outbox/timeliner/`, `outbox/hypothesizer/`, `outbox/user/`;
each spoke gets only `outbox/correlator/`. Read
[`mail-model.md`](../mail-model.md) for the full routing story.

### What's *not* in this config
- **No `workdir` overrides.** Every agent gets its own default directory
  (`log-correlator-workspace/<name>`), so there is no shared-workdir namespacing
  to worry about — unlike the data-pipeline builder, none of these agents share
  a folder. (If you wanted two agents to co-edit a runbook, see
  [`custom-workspace.md`](./custom-workspace.md).)
- **No `capture` set per agent.** `defaults.capture: none` is overridden by the
  loader's per-type natural mode for real agents (claude→Stop hook,
  codex→notify, gemini→pane polling), so every turn-completion signal fires and
  the stop→sweep→route→nudge clock keeps running.
- **No `pings`.** The swarm is purely event-driven off real mail — it only moves
  when you send a trace id. (Add a `pings:` schedule to the correlator if you
  want a stale-investigation nag.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/log-correlator.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings.
2. Creates the runtime dirs (`log-correlator-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The correlator gets
   `outbox/ingester/`, `outbox/timeliner/`, `outbox/hypothesizer/`,
   `outbox/user/` (plus each `about.md` contact card); each spoke gets only
   `outbox/correlator/`. Because every workdir is private, no mailbox namespacing
   is needed.
4. **Installs per-type turn detection** — the Claude Stop hook for `correlator`
   and `hypothesizer`, the Codex `notify` hook for `ingester`, and pane-polling
   watch for the Gemini `timeliner`.
5. **Opens one tmux session per agent**, `cd`'d into its private workdir, running
   its `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the investigation.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'log-correlator' is up with 4 agent(s)
:: attach with:  tmux attach -t <correlator-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/log-correlator.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents that may run `--dangerously-skip-permissions`/`--yolo`, so it
must **never** be exposed on `0.0.0.0` without a token. See
[`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole investigation route mail with no API keys — the mechanics are
> identical.

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the correlator's final report as mail (rather
than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/log-correlator.yaml
```

This rewrites the `user` contact card in the correlator's `outbox/user/about.md`
to `Status: available`, so the correlator sees you're reachable. (While away,
mail to you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the trace id (or a symptom + the named services) into the swarm,
addressed to the correlator:

```bash
./agentainer send -c examples/log-correlator.yaml --to correlator \
  "Trace id 4f2a9c fails: /checkout returns 500 for user 8812. Services: api, \
   payments, orders, inventory. What broke?"
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the correlator, then — because
the inbox was empty — **released into `inbox/`** and the correlator is
**nudged** (the protocol is re-pasted into its pane, including its allowed-
recipient list).

### The mail flowing

Watching the log (§6), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **correlator receives the trace id.** It reads `inbox/`, confirms it has the
   trace id and the service list (or asks you for the missing bits), and writes a
   delegation into `outbox/ingester/`. On stop, that routes to the ingester.
2. **ingester pulls the spans.** It reads its inbox, fetches the lines/spans that
   carry the trace id from each named service (saying so explicitly for any
   service with nothing), and reports back into `outbox/correlator/`. On stop,
   that routes to the correlator.
3. **correlator briefs the timeliner.** It forwards the grouped extracts into
   `outbox/timeliner/`. On stop, that routes to the timeliner.
4. **timeliner builds the timeline.** It reads its inbox, merges everything into
   one timestamp-sorted cross-service timeline with per-hop latency and a labeled
   failure point, and reports back into `outbox/correlator/`. On stop, that
   routes to the correlator.
5. **correlator briefs the hypothesizer.** It forwards the timeline into
   `outbox/hypothesizer/`. On stop, that routes to the hypothesizer.
6. **hypothesizer ranks causes.** It reads the timeline, ranks candidate root
   causes by evidence, names the fix for the top one, and reports back into
   `outbox/correlator/`. On stop, that routes to the correlator.
7. **correlator finalizes.** It writes the final report — the ordered timeline,
   the failure point, and the top hypothesis with its fix and confidence — into
   `outbox/user/`. On stop, that's delivered to your `user` mailbox (visible with
   `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion. If a
spoke's output is missing or contradictory, the correlator goes back to *that*
spoke, not to you.

> If you *don't* send a trace id, the agents just sit in standby (that's the
> point of the standby prompt). The investigation only moves when real mail
> arrives — this swarm has no periodic pings to self-start it.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/log-correlator.yaml
```

```
swarm: log-correlator   root: ./log-correlator-workspace
  correlator  (claude) up idle queue=0 unread=1 talks=ingester, timeliner, hypothesizer, user
  ingester    (codex)  up idle queue=0 unread=0 talks=correlator
  timeliner   (gemini) up idle queue=0 unread=0 talks=correlator
  hypothesizer (claude) up idle queue=0 unread=0 talks=correlator
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/log-correlator.yaml            # whole swarm, last 20
./agentainer logs -c examples/log-correlator.yaml -f          # follow live
./agentainer logs timeliner -c examples/log-correlator.yaml   # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event, including which spoke each hop landed on.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox correlator -c examples/log-correlator.yaml
```

Prints the one released message (headers + body), or `correlator: inbox is
empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue correlator -c examples/log-correlator.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux
session:

```bash
./agentainer attach timeliner -c examples/log-correlator.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom
— handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or (more directly)
by sending notes into an agent's inbox.

- **Send a clarification to the correlator.** Realized you gave a symptom, not a
  trace id, and it needs the service list?
  `./agentainer send --to correlator -c examples/log-correlator.yaml "Also pull
  inventory and the api-gateway edge; the trace may span both."` The correlator
  relays the change down the chain to the ingester.
- **Ask for the evidence behind a hypothesis.** `./agentainer send --to
  correlator -c examples/log-correlator.yaml "Have the hypothesizer show which
  timeline line proves the payments timeout."` — the correlator forwards it.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send
  as `user`, toggle `user` availability, and watch panes live — useful when you
  want to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/log-correlator.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/log-correlator.yaml     # resume is the default
```

On `up`, Agentainer reads `log-correlator-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the
correlator and hypothesizer, `codex resume <id>` for the ingester, and the
Gemini timeliner via its pane-resume. A resumed agent is *not* re-sent the
standby prompt (its prior context is restored — so the correlator still knows
the trace id it was mid-investigating).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/log-correlator.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a `remediator` agent
Once the hypothesizer names the fix, you may want someone to actually apply it.
Add a fifth agent that can read the correlator's report and owns the change:

```yaml
  - name: remediator
    type: claude
    can_talk_to: [correlator, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the REMEDIATOR. Once the correlator delivers the top hypothesis and
      its fix, implement the config/code change in the affected service's repo and
      report the diff + how to verify to outbox/user/. You never gather logs.
```
Then add `remediator` to the correlator's `can_talk_to` so it can be briefed.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `ingester: type: claude` (or `hermes`) to put span-gathering on a different
  model than the correlator.
- `hypothesizer: type: codex` if you want the root-cause reasoning on Codex while
  the correlator stays Claude.
- Remember: `gemini`/`hermes` need pane polling (capture auto-upgrades to
  `pane` since they have no completion hook) — the timeliner is already the
  Gemini example here.

### Tune the ACL
- To let the `hypothesizer` escalate straight to `user` (not only via the
  correlator), add `user` to its `can_talk_to`. Mind that this widens the
  human-facing surface; the doc's convention keeps the correlator the sole
  `user` contact.
- To keep the spokes strictly one-way (already the case here), leave each
  `can_talk_to: [correlator]` — that's the guarantee the timeline has a single
  author.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md)
  for mixing model families safely (this swarm already mixes claude + codex +
  gemini).

---

## 10. Tips & footguns

- **Keep the correlator the only `user`-facing agent.** Only the correlator lists
  `user` in `can_talk_to`. That gives you a single funnel: raw spans, the
  timeline, and ranked hypotheses always pass through review before they reach
  you. If a spoke tries to mail `user` directly, the orchestrator bounces it
  (ACL) and drops a `system` note in its inbox explaining who it *can* message —
  the model self-corrects in-band.

- **The timeline is the product — protect its single authorship.** Because only
  the timeliner merges spans and only the correlator talks to the timeliner, no
  two agents can disagree about ordering. Don't be tempted to let the ingester
  talk to the timeliner directly; that bypasses the hub and lets raw extracts
  skip the correlator's review.

- **Watch the stop → nudge loop, especially for the Gemini spoke.** The whole
  clock runs on turn completion: an agent stops, its outbox is swept, mail is
  routed, recipients are released and nudged. The `timeliner` is detected by
  **pane polling** (Gemini has no completion hook), so its "stopped" signal is a
  settled prompt rather than a hook fire — if it seems stuck while `unread` mail
  sits in its inbox, that's the thing to check. A `type`/`command` mismatch (e.g.
  a `claude` agent whose `command` doesn't launch Claude) means completion never
  triggers and the agent pins "busy" forever. `status` showing an agent `busy`
  for a long time with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/log-correlator.yaml
  ./agentainer remove-session -c examples/log-correlator.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches your config.

- **Availability shapes the ending.** If `user` is **away** when the correlator
  finishes, your final report is *held* (with a `system` "the user is away" ack
  to the correlator) rather than lost — read it later with
  `agentainer user inbox` or flip yourself available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely (this swarm already does claude + codex + gemini).
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing (not needed here; all workdirs are private).
- `examples/log-correlator.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

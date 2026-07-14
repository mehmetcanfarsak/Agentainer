# Use case: Chaos game-day

A concrete, end-to-end walkthrough of the shipped
`examples/chaos-game-day.yaml` swarm — an adversarial **chaos-engineering
drill** run as a controlled game-day. A **gamemaster** runs a pre-approved
scenario, delegates fault injection to an **attacker**, has an **observer**
record what breaks versus what holds, and a **writer** turns the outcome into a
decision-grade game-day report. The whole exercise is cage-bounded: the
attacker may only ever inject faults the gamemaster has pre-authorized as
reversible, the observer only ever reports to the gamemaster, and every spoke
routes back through the gamemaster — so no fault lands without the gamemaster's
explicit go.

Everything below is based on the actual contents of
`examples/chaos-game-day.yaml` and the shipped CLI (`lib/cli.py`) and mailroom
(`lib/mail.py`). No API keys are needed to understand the mechanics; to run it
*for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md)
> first, then the four-folders recap in [`mail-model.md`](../mail-model.md). The
> one-line version: an agent **reads a file** to receive mail and **writes a
> file** to send it; the orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

SRE, platform, and reliability teams — and any owner of a service who wants to
*find* the weakness before production does. The swarm encodes the discipline
that makes a game-day safe rather than a live incident: a single owner of the
scenario and the abort (the gamemaster), an attacker that is a **caged hand**
(only pre-approved, reversible faults, one at a time, on an explicit "GO"), an
observer that tells the truth about what it sees, and a writer that turns raw
observation into a remediation list — while the agents do the actual typing.

It is deliberately **hub-and-spoke**, not a free-for-all: the attacker,
observer, and writer **never** talk to each other and **never** talk to `user`
directly. Every fault and every finding passes through the gamemaster, so no
fault is injected and no conclusion is delivered without the gamemaster's
authority. Swapping in a real `user`-facing report channel (see §9) is a
one-line config change.

---

## 2. The topology

```
      attacker --\
                 >-- gamemaster <--> user
      observer --/
                 \
      writer  ----/
```

Four agents, one directed flow:

1. **`user` → `gamemaster`** — you send the game-day trigger ("run Game Day #3;
   the scenario is in `SCENARIO.md`").
2. **`gamemaster` → `attacker`** — the gamemaster briefs the attacker with ONE
   reversible fault and sends the explicit `GO`.
3. **`gamemaster` → `observer`** — the gamemaster tells the observer what fault
   just went live so it knows what to watch for.
4. **`attacker` → `gamemaster`** — the attacker reports the fault is live (or
   refuses, if the request broke the caged-hand rules), then waits.
5. **`observer` → `gamemaster`** — the observer reports what broke, degraded,
   held, and how long recovery took (it never messages the attacker or `user`).
6. **`gamemaster` → `writer`** — once the system has recovered (or the
   gamemaster aborted), the gamemaster tells the writer to compile the report.
7. **`writer` → `gamemaster`** → **`gamemaster` → `user`** — the finished
   `GAMEDAY-REPORT.md` flows back to you, always via the gamemaster.

The routing above is *enforced* by each agent's `can_talk_to` list. An agent can
only deliver to names on its own list; anything else is bounced back as a
`system` message and filed in `failed/` (see §7). Notably, `attacker`,
`observer`, and `writer` **never** talk to `user` directly — only the gamemaster
does.

---

## 3. The config, explained

Here is `examples/chaos-game-day.yaml` (trimmed to the live fields):

```yaml
swarm:
  name: chaos-game-day
  root: ./chaos-game-day-workspace

defaults:
  capture: none              # mock agents fire no turn-completion hook; real
                             # claude/codex agents get auto-upgraded back to `hook`
  can_talk_to: []            # tightened per agent below

agents:
  - name: gamemaster
    type: claude
    can_talk_to: [attacker, observer, writer, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the GAME MASTER. ... you orchestrate: you own the scenario, the
      sequence, the blast-radius limit, and the abort. ... (1) read SCENARIO.md;
      (2) brief the attacker ONE fault at a time, GO only reversible faults in
      radius; (3) tell the observer what to watch; (4) hold the abort; (5) when
      recovered, tell the writer to compile and deliver to the user. ...

  - name: attacker
    type: codex
    can_talk_to: [gamemaster]
    command: "codex --yolo"
    role: |
      You are the ATTACKER -- a caged one. Run exactly the fault the gamemaster
      sends, on its GO, only reversible faults within the blast radius. ... When
      the gamemaster says ROLL BACK / ABORT, undo the fault immediately. ...

  - name: observer
    type: gemini
    can_talk_to: [gamemaster]
    command: "gemini"
    role: |
      You are the OBSERVER. Watch what happens and tell the truth. ... Keep a
      running OBSERVATIONS.md ... report broke / held / surprising / not-detected
      to the gamemaster. Do not editorialize or propose fixes. ...

  - name: writer
    type: claude
    can_talk_to: [gamemaster]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the WRITER. Turn the gamemaster's scenario and the observer's raw
      observations into GAMEDAY-REPORT.md: hypothesis, what failed, surprises,
      what held, a remediation list, open questions. Deliver to the gamemaster.
      ...
```

Field by field:

### `swarm`
- **`name: chaos-game-day`** — the swarm's name (shows in `status`, logs,
  sessions).
- **`root: ./chaos-game-day-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent defaults to its own private
  subdir (`chaos-game-day-workspace/gamemaster`, `.../attacker`, etc.); there is
  no shared `workdir` in this swarm, so mailboxes are *not* namespaced.
  Orchestrator state goes under `chaos-game-day-workspace/.agentainer/` (never
  commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode is "none". For
  `claude`/`codex` agents this is **auto-upgraded** to `hook` at load time (see
  per-agent notes). For the `gemini` observer it stays `none` — and that has a
  real consequence, explained under `observer` below.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent
  below states its own list explicitly, so this default is just a safe floor.

### `gamemaster` (type: `claude`)
- **`can_talk_to: [attacker, observer, writer, user]`** — the gamemaster is the
  **only hub**: it is the sole agent that can talk to `user`, and it alone can
  brief the three spokes. Keep the human-facing surface to one agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command, e.g. a
  shell alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no game-day yet — don't send
  anything, you'll be notified"), so the gamemaster waits for your trigger
  instead of mailing its spokes on boot.
- **Turn detection:** `claude` + `capture: none` → **auto-upgraded to `hook`**
  (a **Stop hook**, installed automatically at `up`). When the gamemaster stops,
  its outbox is swept and routed.

### `attacker` (type: `codex`)
- **`can_talk_to: [gamemaster]`** — the attacker only ever reports back to the
  gamemaster. It cannot reach the observer, the writer, or `user`; the fault
  narrative has exactly one authority.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "caged hand": run only the fault the gamemaster sends, on its
  `GO`, only reversible faults within the stated blast radius; `ROLL BACK` /
  `ABORT` on command; refuse any request that breaks the rules.
- **Turn detection:** `codex` + `capture: none` → **auto-upgraded to `hook`**
  (a **`notify` program**, installed at `up`). On stop, its outbox is swept.

### `observer` (type: `gemini`)
- **`can_talk_to: [gamemaster]`** — the observer only reports to the gamemaster.
  It never talks to the attacker, the writer, or `user`; clean evidence in,
  clean evidence out.
- **`command: "gemini"`** — placeholder launch command.
- **`role`** — "watch and tell the truth": keep `OBSERVATIONS.md`, report broke
  / held / surprising / not-detected, flag silent failures. No fixes.
- **Turn detection — read this carefully.** `gemini`'s *natural* capture is
  `pane` (pane polling), but the `defaults: capture: none` line forces it to
  `none`. Unlike `claude`/`codex`, the loader does **not** auto-upgrade `none`
  for a pane-type agent, so this observer ends up with **no turn-completion
  signal**. The orchestrator treats it as **"silent-but-alive"**: the liveness
  supervisor still delivers its inbound mail and nudges it, but its *outbound*
  (the observations it writes to `outbox/gamemaster/`) is only swept and routed
  on a turn-complete — which `capture: none` never fires on its own. **For a
  hands-off run with a real Gemini observer, set `capture: pane` on this agent
  and run `agentainer watch observer` after `up`** (that launches the pane
  poller, which calls `on_stop` when the pane goes idle and sweeps the
  observer's outbox to the gamemaster). See §9 and Tips.

### `writer` (type: `claude`)
- **`can_talk_to: [gamemaster]`** — the writer only reports to the gamemaster,
  who forwards the finished report to `user`. It never messages `user` directly.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **`role`** — "synthesize, don't invent": produce `GAMEDAY-REPORT.md` from the
  scenario + the observer's observations — hypothesis, failures, surprises,
  wins, a severity-tagged remediation list, open questions.
- **Turn detection:** `claude` + `capture: none` → **auto-upgraded to `hook`**
  (Stop hook).

### The ACL, made visible
`init_mailboxes` creates an `outbox/<peer>/` folder **for each allowed
recipient**, and writes an `outbox/<peer>/about.md` contact card into it. That
card *is* the ACL made visible: the gamemaster gets `outbox/attacker/`,
`outbox/observer/`, `outbox/writer/`, `outbox/user/`; each spoke gets only
`outbox/gamemaster/`. If a spoke tries to write to anyone not on its list (say
the attacker writes to `outbox/user/`), the orchestrator **bounces** it as a
`system` message and files the file in `failed/` — the model self-corrects
in-band. The `can_talk_to` ACL is cooperative, not OS isolation (see Tips).

### What's *not* in this config
- **No `pings`.** The swarm is purely event-driven off real mail — it only
  moves when you trigger a game-day. (Add a ping to the gamemaster if you want a
  "did we ever run the scheduled game-day?" nag.)
- **No shared `workdir`.** Each agent has its own private directory, so mailboxes
  are created un-namespaced (`inbox/ outbox/ read/ sent/ failed/`). For the
  shared-workdir treatment see [`custom-workspace.md`](./custom-workspace.md).
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it
  on (see §4).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/chaos-game-day.yaml
```

What `up` does (see `cmd_up` / `up_config` in `lib/cli.py`):

1. Loads and validates the config; prints the three `capture: none → hook`
   auto-upgrade warnings for `gamemaster`/`attacker`/`writer`.
2. Creates the runtime dirs (`chaos-game-day-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, plus an `outbox/<peer>/` folder **for
   each allowed recipient** (the ACL made visible). The gamemaster gets four
   peer folders; each spoke gets exactly one (`outbox/gamemaster/`).
4. **Installs per-type turn detection** — the Claude Stop hook for `gamemaster`
   and `writer`, and the Codex `notify` hook for `attacker`. (The `observer`
   stays `capture: none` — see §3; if you set it to `pane`, launch
   `agentainer watch observer` afterwards.)
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and `serve` hints. The `serve` line gives you the
mail-app control-plane UI (threads, live panes, send-as-user, availability
toggle). Drop `--host`/`--token` for the safe loopback-only `127.0.0.1` bind —
the UI can start processes, edit config, and type into agents, so it must
**never** be exposed on `0.0.0.0` without a token. See
[`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole drill route mail with no API keys — the mechanics are identical.
> (With mock commands the observer is still `capture: none`; see §3 for how to
> drive its outbound.)

Before the game-day, create `chaos-game-day-workspace/gamemaster/SCENARIO.md`
listing the only faults approved for this run, each tagged reversible/
irreversible with its blast radius — the gamemaster's role tells it to read that
file first.

---

## 5. Drive a game-day

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the finished `GAMEDAY-REPORT.md` as mail
(rather than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/chaos-game-day.yaml
```

This rewrites the `user` contact card in the gamemaster's `outbox/user/about.md`
to `Status: available`. (While away, mail to you is *held* and the sender gets a
`system` ack — nothing bounces.)

Now trigger the drill, addressed to the gamemaster:

```bash
./agentainer send --to gamemaster -c examples/chaos-game-day.yaml \
  "Run Game Day #3: payment-svc failover. The approved scenario is in SCENARIO.md."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped
`From: user` with a fresh id, enqueued for the gamemaster, then — because its
inbox was empty — **released into `inbox/`** and the gamemaster is **nudged**
(the protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the drill advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **gamemaster reads the trigger.** It reads `SCENARIO.md`, picks the first
   reversible fault, writes a brief + `GO` into `outbox/attacker/` and a
   "watch for fault X" note into `outbox/observer/`. On stop, both route.
2. **attacker injects; observer watches.** The attacker reports the fault live
   (and waits for the next instruction, never stacking faults); the observer
   starts `OBSERVATIONS.md`. On each stop, their mail routes to the gamemaster.
3. **gamemaster holds the abort.** If a fault escapes radius, the gamemaster
   orders `ROLL BACK` / `ABORT` into `outbox/attacker/`; the attacker undoes it.
4. **gamemaster closes the loop.** Once the observer reports recovery (or the
   gamemaster aborts), the gamemaster tells the writer to compile, and the
   writer drops `GAMEDAY-REPORT.md` into `outbox/gamemaster/`.
5. **gamemaster delivers to you.** On stop, that routes to your `user` mailbox
   (visible with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion. With
the observer on `capture: none`, its step (2/3) is the one that needs the `watch`
poller (or a manual `idle`/stop) to route — see §3 and Tips.

> If you *don't* send a trigger, the agents just sit in standby. The drill only
> moves when real mail arrives — this swarm has no periodic pings to self-start.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/chaos-game-day.yaml
```

```
swarm: chaos-game-day   root: ./chaos-game-day-workspace
  gamemaster (claude) up idle queue=0 unread=0 talks=attacker, observer, writer, user
  attacker   (codex)   up idle queue=0 unread=1 talks=gamemaster
  observer   (gemini)  up idle queue=0 unread=0 talks=gamemaster
  writer     (claude)  up idle queue=0 unread=0 talks=gamemaster
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/chaos-game-day.yaml            # whole swarm, last 20
./agentainer logs -c examples/chaos-game-day.yaml -f          # follow live
./agentainer logs observer -c examples/chaos-game-day.yaml   # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
and the supervisor's `silent-but-alive` event for the `capture: none` observer.

**A specific inbox** — what an agent is currently looking at:

```bash
./agentainer inbox gamemaster -c examples/chaos-game-day.yaml
```

Prints the one released message (headers + body), or `gamemaster: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue gamemaster -c examples/chaos-game-day.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach attacker -c examples/chaos-game-day.yaml
```

Detach with `Ctrl-b d`. (Typing into a pane bypasses the mailroom — handy for
un-sticking an agent, but the mail model is the normal path.)

**The artifacts on disk** — the gamemaster's `SCENARIO.md`, the observer's
`OBSERVATIONS.md`, and the writer's `GAMEDAY-REPORT.md` live in each agent's
workdir (`chaos-game-day-workspace/<agent>/`). Inspect them directly as the drill
unfolds.

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or by sending notes
into an agent's inbox.

- **Re-scope a fault.** Realized the blast radius was too wide?
  `./agentainer send --to gamemaster -c examples/chaos-game-day.yaml "Abort fault
  #2 and re-run with radius limited to the payments namespace."` The gamemaster
  relays the change down the chain.
- **Ask the observer for the evidence.** `./agentainer send --to gamemaster ...
  "Have the observer attach the detection lag for the failover."` — forwarded to
  the observer.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send
  as `user`, toggle `user` availability, and watch panes live — useful when you
  want to nudge a specific spoke without guessing its name.

When you're done (or want to try a different scenario), tear it down:

```bash
./agentainer down -c examples/chaos-game-day.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/chaos-game-day.yaml     # resume is the default
```

On `up`, Agentainer reads `chaos-game-day-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the
gamemaster and writer, `codex resume <id>` for the attacker. (The gemini
observer has no resume recipe, so it starts fresh — fine, since its role is
stateless watching.) A resumed agent is *not* re-sent the standby prompt.

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/chaos-game-day.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Give the observer a real turn signal
As noted in §3, the shipped `observer` is `gemini` + `capture: none`, which never
auto-routes its outbound. For a hands-off run with a real Gemini observer, set
pane capture and start the poller:

```yaml
  - name: observer
    type: gemini
    capture: pane             # was: none (the defaults line)
    can_talk_to: [gamemaster]
    command: "gemini"
    role: |
      ... (unchanged) ...
```

```bash
./agentainer up -c examples/chaos-game-day.yaml
./agentainer watch observer -c examples/chaos-game-day.yaml   # launches the pane poller
```

The poller watches the observer's pane; when it goes idle it calls `on_stop`,
sweeping the observer's outbox and routing `OBSERVATIONS.md`'s summary to the
gamemaster — no manual step needed. (Gemini/Hermes have no completion hook, so
`pane` polling is their only automatic mode; see
[`configuration.md`](../configuration.md).)

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `attacker: type: claude` to put fault injection on Claude instead of Codex.
- `writer: type: codex` if you'd rather the report authoring run on Codex.
- Remember: `gemini`/`hermes` need `capture: pane` (and `agentainer watch`) since
  they have no completion hook.

### Widen or tighten the ACL
- To let the `writer` deliver the report straight to `user` (not only via the
  gamemaster), add `user` to its `can_talk_to`. Mind that this widens the
  human-facing surface; the shipped convention keeps the gamemaster the sole
  `user` contact.
- To keep the attacker from even *seeing* a path to `user`, leave its
  `can_talk_to: [gamemaster]` — that's the caged-hand guarantee. If it tries
  anyway, the orchestrator bounces the mail and drops a `system` note in its
  inbox explaining who it *can* message.

For broader hub-and-spoke routing patterns see
[`delegation-pipeline.md`](./delegation-pipeline.md), and for mixing model
families safely see [`multi-llm-swarm.md`](./multi-llm-swarm.md). The finished
report is a close cousin of [`postmortem.md`](./postmortem.md) — handy reading
when you tune the writer's remediation format.

---

## 10. Tips & footguns

- **Keep the gamemaster the only `user`-facing agent.** Only the gamemaster lists
  `user` in `can_talk_to`. That gives you a single funnel: raw fault reports and
  the game-day writeup always pass through review before they reach you. If a
  spoke tries to mail `user` directly, the orchestrator bounces it (ACL) and
  drops a `system` note in its inbox explaining who it *can* message — the model
  self-corrects in-band.

- **The `capture: none` observer will not auto-route its outbound.** This is the
  one gotcha in the shipped config. `gamemaster`/`attacker`/`writer` are
  `claude`/`codex`, so `capture: none` is auto-upgraded to `hook` and their mail
  routes the moment they stop. The `observer` is `gemini`, and the loader does
  **not** auto-upgrade `none` for a pane-type agent — so it has no
  turn-completion signal and its observations sit in `outbox/gamemaster/` until
  something sweeps them. Fix: set `capture: pane` and run `agentainer watch
  observer` (or, in a pinch, `agentainer idle observer` / `down` then `up` to
  force a turn-finish). If the gamemaster seems stuck waiting on the observer,
  that's the tell.

- **The ACL is cooperative, not OS isolation.** Agents have filesystem access and
  *could* write straight into another inbox, bypassing `outbox/`. Enforced for
  well-behaved agents; documented honestly; not a security boundary. The caged-
  hand rules in the attacker's `role` are *instructions to a model*, not a
  technical control — review the attacker's actions, and keep the blast-radius
  limit real (a staging environment, not prod).

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. A `type`/`command` mismatch (e.g. a `claude` agent whose `command`
  doesn't launch Claude) means completion never triggers and the agent pins
  "busy" forever. `status` showing an agent `busy` for a long time with `unread`
  mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/chaos-game-day.yaml
  ./agentainer remove-session -c examples/chaos-game-day.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches `SCENARIO.md`/`OBSERVATIONS.md`/`GAMEDAY-REPORT.md` in
  the agents' workdirs or your config.

- **Availability shapes the ending.** If `user` is **away** when the gamemaster
  finishes, your game-day report is *held* (with a `system` "the user is away"
  ack to the gamemaster) rather than lost — read it later with
  `agentainer user inbox` or flip yourself available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`configuration.md`](../configuration.md) — `capture`, `can_talk_to`, `pings`.
- [`cli-reference.md`](../cli-reference.md) — every subcommand, including `watch`.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- [`postmortem.md`](./postmortem.md) — the report format the writer produces.
- `examples/chaos-game-day.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

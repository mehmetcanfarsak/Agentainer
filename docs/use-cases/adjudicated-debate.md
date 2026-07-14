# Use case: Adjudicated debate

A concrete, end-to-end walkthrough of the shipped
`examples/adjudicated-debate.yaml` swarm — a multi-model deliberation where a
**moderator** runs structured argument/rebuttal rounds between a **pro** advocate
(Codex) and a **con** advocate (Gemini), and a neutral **judge** (Claude) renders
a reasoned verdict by weight of argument. The moderator is the only agent that
talks to you: it frames your motion, relays each side's case to the other, and
hands the compiled transcript to the judge, then returns the verdict.

Everything below is based on the actual contents of
`examples/adjudicated-debate.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Anyone who wants a *balanced* deliberation rather than a single-model answer —
policy questions, "should we / shouldn't we" product calls, ethics or design
trade-offs, or anywhere you want two opposing cases argued by *different* model
families and weighed by a third. The swarm encodes the discipline that makes a
debate fair: a neutral hub that never argues a side itself, advocates locked to
opposite stances on different providers (so you don't get one model's priors
twice), and a judge that only ever sees the *compiled* transcript — never the raw
debate or the motion in isolation, so it can't be subtly steered.

It is deliberately a **strict hub**: `pro` and `con` never talk to each other
directly, and only the `moderator` talks to `user`. That keeps the relay honest
(the moderator is the one who frames, passes rebuttals, and compiles) and keeps
the human-facing surface to a single agent. (Contrast with
`examples/debate.yaml`, a plain 2-agent back-and-forth with no judge and no
structured rounds.)

---

## 2. The topology

```
            user
             │  "Motion: <question>" / verdict
        ┌────▼─────┐
        │moderator │  (the hub: frames motion, runs the rounds, relays verdict)
        └────┬─────┘
     FOR  /   │   \  AGAINST
   ┌─────────▼┐ │ ┌──────▼─────────┐
   │   pro    │ │ │      con       │   both route ONLY through moderator
   │ (codex)  │◀┼─▶│   (gemini)     │
   └─────────┬┘ │ └──────▲─────────┘
             │  │ transcript
         ┌───▼──▼───┐     │ verdict
         │  judge   │─────┘
         │ (claude) │
         └──────────┘
```

Four agents, one directed flow:

1. **`user` → `moderator`** — you send the motion (a single yes/no or framed
   question).
2. **`moderator` → `pro`** (IN FAVOR) **and `moderator` → `con`** (AGAINST) — the
   moderator frames the motion, fixes the round rules, and opens round 1 with both
   advocates.
3. **`pro` → `moderator`** and **`con` → `moderator`** — each reports its argument
   *only* to the moderator. The moderator relays each side's case to the other as
   the next round's rebuttal prompt, so pro and con never exchange mail directly.
4. **Rounds repeat** for the agreed number of rounds (default 3), the moderator
   keeping a running transcript tagged by side and round.
5. **`moderator` → `judge`** — when the rounds are done, the moderator compiles the
   full transcript and sends it to the judge with the judging criterion ("better
   weight of argument, not mere correctness").
6. **`judge` → `moderator`** — the judge reads both sides and renders a reasoned
   decision on which side carried the better case and *why*; it never sees the raw
   debate or the motion alone.
7. **`moderator` → `user`** — the moderator relays the verdict verbatim plus a
   one-line motion summary.

The routing above is *enforced* by each agent's `can_talk_to` list. `pro` and
`con` list only `moderator`; `judge` lists only `moderator`; `moderator` lists
`pro, con, judge, user`. Anything outside the list is bounced back as a `system`
message (see §7).

---

## 3. The config, explained

Here is `examples/adjudicated-debate.yaml` in full:

```yaml
swarm:
  name: adjudicated-debate
  root: ./adjudicated-debate-workspace

defaults:
  capture: none              # loader auto-upgrades claude/codex/gemini to their hooks
  can_talk_to: []           # tightened per agent below

agents:
  - name: moderator
    type: claude
    can_talk_to: [pro, con, judge, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the MODERATOR of an adjudicated debate. You run the whole contest;
      you never argue a side yourself. When the user drops a MOTION ... FRAME it
      neutrally ... RUN the rounds ... ADJUDICATE by compiling the transcript and
      sending it to judge ... relay the verdict to the user.
      You are the ONLY agent allowed to talk to the user. pro, con, and judge each
      report back ONLY to you. Never let pro and con talk to each other directly.

  - name: pro
    type: codex
    can_talk_to: [moderator]
    command: "codex --yolo"
    role: |
      You are PRO, the advocate who argues IN FAVOR of the debate motion. You run
      on OpenAI Codex ... build the strongest, most honest case FOR the motion and
      explicitly rebut whatever CON argument the moderator forwarded ... You talk
      ONLY to the moderator.

  - name: con
    type: gemini
    can_talk_to: [moderator]
    command: "gemini --yolo"
    role: |
      You are CON, the advocate who argues AGAINST the debate motion. You run on
      Google Gemini ... build the strongest, most honest case AGAINST the motion
      and explicitly rebut whatever PRO argument the moderator forwarded ... You
      talk ONLY to the moderator.

  - name: judge
    type: claude
    can_talk_to: [moderator]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are JUDGE, the neutral decider ... You receive ONE message from the
      moderator: the fully compiled transcript ... Render a REASONED DECISION:
      state which side carried the better case, then explain WHY by weighing the
      actual arguments ... Send your verdict ONLY to the moderator.
```

Field by field:

### `swarm`
- **`name: adjudicated-debate`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./adjudicated-debate-workspace`** — the parent directory for each
  agent's working directory and mailboxes. With no `workdir` override, the
  loader gives each agent its own subdir: `adjudicated-debate-workspace/moderator`,
  `.../pro`, `.../con`, `.../judge`. Orchestrator state goes under
  `adjudicated-debate-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — a *floor* of "no capture". The config loader
  (`lib/config.py`) auto-upgrades this per `type`: `claude` → Stop-hook capture,
  `codex` → `notify` capture, `gemini` → pane-polling capture. So even though the
  file says `none`, each agent ends up on its type's natural turn-detection mode
  (see the per-agent notes). This is the same behaviour the loader gives when
  `capture` is omitted and left as `auto`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `moderator` (type: `claude`)
- **`can_talk_to: [pro, con, judge, user]`** — the only agent allowed to reach
  `user`, and the sole relay between the advocates and the judge. It frames the
  motion, runs the rounds, and compiles the transcript.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command (substitute your own, e.g. a shell alias). Treat command strings as
  sensitive; they may embed keys.
- **`role`** — the standing identity. On `up` this becomes the first prompt,
  wrapped in a **standby notice** ("no motion yet — wait"), so the moderator
  holds until you send one.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `pro` (type: `codex`)
- **`can_talk_to: [moderator]`** — pro can reach *only* the moderator. It never
  messages `con` or `user`; every argument is relayed by the hub.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "argue IN FAVOR, rebut the CON argument the moderator forwards, stay
  strictly on side, report back to the moderator."
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `con` (type: `gemini`)
- **`can_talk_to: [moderator]`** — con can reach *only* the moderator. Symmetric
  with pro: it argues AGAINST and never talks to pro or user directly.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **`role`** — "argue AGAINST, rebut the PRO argument the moderator forwards, stay
  strictly on side, report back to the moderator."
- **Turn detection:** `gemini` → **pane polling** (`capture: pane`). Gemini has no
  completion hook, so the supervisor watches its tmux pane for the turn to finish
  rather than receiving a hook signal. This is the auto-upgrade applied to the
  `none` default.

### `judge` (type: `claude`)
- **`can_talk_to: [moderator]`** — the judge sees *only* the moderator's compiled
  transcript, never the raw pro/con mail and never the motion in isolation. That
  separation is what keeps its verdict un-steered.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **`role`** — "read the compiled transcript, weigh the actual arguments, state
  which side carried the better case and *why*, acknowledge the strongest point of
  the losing side, send the verdict only to the moderator."
- **Turn detection:** `claude` → Stop hook.

### ACL enforcement (how the hub stays honest)

The `can_talk_to` lists are **cooperative, not OS isolation** — agents have
filesystem access and *could* write into another inbox directly, but well-behaved
agents route through `outbox/<name>/`, and the orchestrator only *releases* a
message into an inbox if the sender is on the recipient's allowed list. If `pro`
ever wrote straight into `con`'s inbox, the orchestrator would flag the
out-of-band write. If `pro` tried to *send* to `user` through the mailroom, it is
**bounced**: the message lands in `failed/`, and a `system` note explaining its
allowed recipients is filed in `pro`'s inbox so the model self-corrects in-band.
The `outbox/<peer>/about.md` contact card *is* the ACL made visible — `pro` gets
only `outbox/moderator/`; the moderator gets `outbox/pro/`, `outbox/con/`,
`outbox/judge/`, `outbox/user/`.

### What's *not* in this config
- **No `pings`.** The swarm is purely event-driven off real mail — it only moves
  when you send a motion. There is no cron schedule; the rounds are driven by the
  moderator's role logic and `stop → sweep → route → release → nudge` cycles.
- **No `workdir` overrides.** All four agents get their own subdir under `root`, so
  no mailbox namespacing is needed (contrast with shared-workdir swarms — see
  [`custom-workspace.md`](./custom-workspace.md)).
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — the verdict addressed to you is *held* (never bounced) until you flip
  it on (see §4).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/adjudicated-debate.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (none structural here).
2. Creates the runtime dirs (`adjudicated-debate-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. So `moderator` gets
   `outbox/pro/`, `outbox/con/`, `outbox/judge/`, `outbox/user/`; `pro`, `con`, and
   `judge` each get just `outbox/moderator/`. Each `outbox/<peer>/about.md` is the
   contact card stating who that peer is and whether they're available.
4. **Installs per-type turn detection** — the Claude Stop hook for `moderator` and
   `judge`, the Codex `notify` hook for `pro`, and pane polling for `con` (gemini).
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the rounds.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'adjudicated-debate' is up with 4 agent(s)
:: attach with:  tmux attach -t <moderator-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/adjudicated-debate.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents, so it must **never** be exposed on `0.0.0.0` without a token.
See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole relay route mail with no API keys — the mechanics are identical.

---

## 5. Drive a motion

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the verdict as mail (rather than have it held),
turn yourself available first:

```bash
./agentainer user available -c examples/adjudicated-debate.yaml
```

This rewrites the `user` contact card in the moderator's `outbox/user/about.md`
to `Status: available`, so the moderator sees you're reachable. (While away, mail
to you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the motion into the swarm, addressed to the moderator:

```bash
./agentainer send --to moderator -c examples/adjudicated-debate.yaml \
  "Resolved: cities should ban private cars from their centers."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the moderator, then — because the
inbox was empty — **released into `inbox/`** and the moderator is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list:
`pro, con, judge, user`).

### The mail flowing

Watching the log (§6), you'll see the debate advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **moderator receives the motion.** It reads `inbox/`, frames it neutrally, fixes
   the round rules (default 3 rounds), and writes round-1 briefs into
   `outbox/pro/` and `outbox/con/` — each told its side and to report back only to
   the moderator. On stop, both route.
2. **pro and con argue round 1.** Each reads its inbox and writes its case into
   `outbox/moderator/`. On stop, both route back to the moderator.
3. **moderator relays + rebuts.** It writes each side's argument into the other's
   `outbox/` as the next round prompt, so pro rebuts con and con rebuts pro. This
   repeats for the agreed rounds, the moderator keeping a tagged transcript.
4. **moderator adjudicates.** When the rounds are complete, it compiles the full
   transcript and writes it into `outbox/judge/` with the judging criterion. On
   stop, that routes to the judge.
5. **judge renders the verdict.** It reads the transcript and writes its reasoned
   decision into `outbox/moderator/`. On stop, that routes back.
6. **moderator relays to you.** It writes the verdict (plus a one-line motion
   summary) into `outbox/user/`. On stop, that's delivered to your `user` mailbox
   (visible with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a motion, the agents just sit in standby (that's the point of
> the standby prompt). The debate only moves when real mail arrives — this swarm
> has no periodic pings to self-start it.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/adjudicated-debate.yaml
```

```
swarm: adjudicated-debate   root: ./adjudicated-debate-workspace
  moderator (claude) up idle queue=0 unread=0 talks=pro, con, judge, user
  pro       (codex)  up idle queue=0 unread=1 talks=moderator
  con       (gemini) up idle queue=0 unread=0 talks=moderator
  judge     (claude) up idle queue=0 unread=0 talks=moderator
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct who argued what):

```bash
./agentainer logs -c examples/adjudicated-debate.yaml          # whole swarm, last 20
./agentainer logs -c examples/adjudicated-debate.yaml -f        # follow live
./agentainer logs pro -c examples/adjudicated-debate.yaml       # just one advocate
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what an agent is currently looking at:

```bash
./agentainer inbox moderator -c examples/adjudicated-debate.yaml
```

Prints the one released message (headers + body), or `moderator: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue moderator -c examples/adjudicated-debate.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach con -c examples/adjudicated-debate.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first verdict may not be the framing you wanted. Because every message is
natural-language mail, you can steer mid-flight:

- **Clarify the motion.** `./agentainer send --to moderator -c examples/adjudicated-debate.yaml
  "Keep it to a 2-round sprint and judge strictly on cost."` The moderator adjusts
  the round rules and relays accordingly.
- **Ask the judge for its reasoning.** `./agentainer send --to moderator ... "Have
  the judge spell out why it weighted pro's evidence over con's."` — the moderator
  forwards to the judge and relays the reply.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge the moderator without guessing its name.

When you're done, tear it down:

```bash
./agentainer down -c examples/adjudicated-debate.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/adjudicated-debate.yaml     # resume is the default
```

On `up`, Agentainer reads `adjudicated-debate-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the
moderator and judge, `codex resume <id>` for pro, and Gemini's resume for con. A
resumed agent is *not* re-sent the standby prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/adjudicated-debate.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Change the number of rounds
The default (3) lives in the moderator's `role` text ("decide the number of rounds
(default 3)"). Edit the moderator's `role:` to fix a different default, or just
tell the moderator via `send` at runtime ("run 5 rounds"). The moderator owns the
round budget; the advocates simply obey the brief they're given each round.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `pro: type: hermes` or `con: type: claude` to shuffle which provider argues which
  side — the design intends *different* families per side, but nothing enforces it.
- `judge: type: codex` if you want the verdict on a different model than the hub.
- Remember: `gemini`/`hermes` need pane polling (their auto-upgrade of `none`),
  since they have no completion hook; `claude`/`codex` use hooks.

### Tune the ACL
- To let the `judge` speak to `user` directly (not only via the moderator), add
  `user` to its `can_talk_to`. Mind that this widens the human-facing surface; the
  design keeps the moderator the sole `user` contact so the verdict is always
  relayed with the motion summary.
- To let `pro` and `con` argue *at each other* without the moderator as middleman,
  add each to the other's `can_talk_to` — but that breaks the strict-hub guarantee
  this swarm is built on, and the judge's "compiled transcript only" separation.
- See [`multi-llm-swarm.md`](./multi-llm-swarm.md) for mixing model families
  safely, and [`delegation-pipeline.md`](./delegation-pipeline.md) for broader
  hub-and-spoke routing patterns.

---

## 10. Tips & footguns

- **Keep the moderator the only `user`-facing agent.** Only the moderator lists
  `user` in `can_talk_to`. That gives you a single funnel: the verdict always
  passes through the hub's framing. If `pro`, `con`, or `judge` tried to mail
  `user` directly, the orchestrator bounces it (ACL) and drops a `system` note in
  their inbox explaining who they *can* message — the model self-corrects in-band.

- **The judge never sees the raw debate.** The moderator compiles the transcript;
  the judge's `can_talk_to: [moderator]` plus its role ("you receive ONE message:
  the compiled transcript") keeps it from being steered by the motion alone or by
  out-of-band mail. Don't break this by adding `pro`/`con` to the judge's list.

- **`pro`/`con` never talk to each other.** Their ACL is `[moderator]` only; the
  relay that produces rebuttals is the moderator's job. The separation is what
  stops them from forming a consensus off-book. (It's cooperative, not OS
  isolation — see the ACL note in §3 — so it relies on the agents following their
  roles, which the nudged protocol reinforces every turn.)

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If the debate seems stuck, check that **turn detection actually fires** —
  a `type`/`command` mismatch (e.g. a `gemini` agent whose `command` doesn't
  launch Gemini) means completion never triggers and the agent pins "busy"
  forever. `con` in particular relies on **pane polling**, so its pane must be the
  live Gemini session for the supervisor to see the turn end. `status` showing an
  agent `busy` for a long time with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops — relevant if your advocates get chatty.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/adjudicated-debate.yaml
  ./agentainer remove-session -c examples/adjudicated-debate.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches your config.

- **Availability shapes the ending.** If `user` is **away** when the moderator
  finishes, your verdict is *held* (with a `system` "the user is away" ack to the
  moderator) rather than lost — read it later with `agentainer user inbox` or flip
  yourself available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- `examples/adjudicated-debate.yaml` — the config this walkthrough is built on.
- `examples/debate.yaml` — the simpler 2-agent variant (no judge, no structured rounds).
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

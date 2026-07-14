# Use case: the candidate screening swarm

A concrete, end-to-end walkthrough of the shipped `examples/candidate-screen.yaml`
swarm — a **coordinator** that runs a structured technical *and* behavioral screen
for a candidate and returns one scored hire/no-hire summary. It's the canonical
"sequence the work → two independent interviewers score → reconcile into one
recommendation" loop, wired entirely through Agentainer's file-based mail model.

Everything below is based on the actual contents of
`examples/candidate-screen.yaml` and the shipped CLI (`lib/cli.py`) and mailroom
(`lib/mail.py`). No API keys are needed to understand the mechanics; to run it
*for real* you supply the coding-CLI commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The topology

```
              scheduler
                  |
   technical --- coordinator --- scorer --- user
                  |
              behavioral
```

Five agents, one directed flow:

1. **`user` → `coordinator`** — you send the candidate and the role.
2. **`coordinator` → `scheduler`** — the coordinator drafts the question set and
   timeline with the scheduler.
3. **`coordinator` → `technical`** and **`coordinator` → `behavioral`** — the two
   rounds run **separately and in parallel** from the same candidate brief, so
   each scorer sees the role fresh (they never talk to each other).
4. **`technical` / `behavioral` → `coordinator`** — each returns a scored write-up.
5. **`coordinator` → `scorer`** — the coordinator bundles both write-ups and sends
   them to the scorer.
6. **`scorer` → `user`** (via the coordinator is possible too) — the scorer merges
   the two scores into one recommendation for the human.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. An agent can only deliver to names on its own list; anything
else is bounced back as a `system` message and filed in `failed/` (see §7).

> **Decision support, not a decision.** This swarm is a hiring *aid*. The scorer's
> summary ends with a recommendation and a confidence level, but a human makes the
> call. Nothing here auto-rejects a candidate.

---

## 2. The config, explained

Here is `examples/candidate-screen.yaml` in full:

```yaml
# 🧑‍💼 Candidate screen -- run a structured technical + behavioral interview and
# return one scored hire/no-hire summary. (key-free: swap each command for a real CLI)
swarm:
  name: screen
  root: ./screen-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: coordinator
    type: claude
    can_talk_to: [scheduler, technical, behavioral, scorer, user]
    command: "claude --dangerously-skip-permissions"
    role: "You run the screen: sequence the rounds, brief the interviewers, collect scores, pass the summary to the user."
  - name: scheduler
    type: claude
    can_talk_to: [coordinator]
    command: "claude --dangerously-skip-permissions"
    role: "Draft the question set + timeline + rubric dimensions for the role; write PLAN.md; report to the coordinator."
  - name: technical
    type: codex
    can_talk_to: [coordinator]
    command: "codex --yolo"
    role: "Run the coding/technical round; score each rubric dimension 1-5; report a scored write-up to the coordinator."
  - name: behavioral
    type: claude
    can_talk_to: [coordinator]
    command: "claude --dangerously-skip-permissions"
    role: "Run the culture/behavioral round; score each rubric dimension 1-5; report a scored write-up to the coordinator."
  - name: scorer
    type: claude
    can_talk_to: [coordinator, user]
    command: "claude --dangerously-skip-permissions"
    role: "Merge the two write-ups into one hire/no-hire summary with a recommendation + confidence; send it to the user."
```

Field by field:

### `swarm`
- **`name: screen`** — the swarm's name (shows up in `status`, logs, sessions).
- **`root: ./screen-workspace`** — the parent directory for the agents' working
  directories and mailboxes. Each agent gets `screen-workspace/<name>/` as its
  workdir (created on `up`), and its mailbox folders live alongside. Orchestrator
  state goes under `screen-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, and it's ultimately keyed off each
  agent's `type`. For `claude` and `codex`, whose CLIs support a completion
  **hook**, setting `capture: none` is a footgun — so the config loader *upgrades*
  it back to `hook` and prints a warning at `up`
  (`capture: none on a claude agent gives the orchestrator no way to detect turn
  completion; using the type's default: capture: hook.`). Net effect here: every
  agent uses its hook, because all five are real CLIs that support one.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `coordinator` (type: `claude`)
- **`can_talk_to: [scheduler, technical, behavioral, scorer, user]`** — the hub.
  It can talk to the planner, both interviewers, the scorer, **and the `user`** so
  it can hand the final summary along. The interviewers and scheduler can *only*
  reach the coordinator; the scorer can reach the coordinator *and* the user.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no candidate yet — don't send
  anything, you'll be notified"), so the coordinator waits for your brief instead
  of proactively mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `scheduler` (type: `claude`)
- **`can_talk_to: [coordinator]`** — a leaf: it only answers the coordinator. It
  drafts a reusable question set, timebox, and rubric dimensions for the role and
  writes them to `PLAN.md`.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook.

### `technical` (type: `codex`)
- **`can_talk_to: [coordinator]`** — runs the coding/technical round and reports a
  scored write-up back to the coordinator. It cannot reach the behavioral
  interviewer, the scorer, or the `user`, so the two scores stay independent.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "probe real depth, score each rubric dimension 1-5 with a one-line
  justification, report a scored write-up."
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `behavioral` (type: `claude`)
- **`can_talk_to: [coordinator]`** — runs the culture/behavioral round and reports
  a scored write-up back to the coordinator. Same isolation as `technical`.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook.

### `scorer` (type: `claude`)
- **`can_talk_to: [coordinator, user]`** — the only agent besides the coordinator
  that can reach the `user`. It merges the two write-ups into one recommendation
  and sends the summary straight to the human.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook.

### What's *not* in this config
- **No `pings`.** None of the five agents has a periodic ping
  configured, so no agent is auto-nudged on a timer while idle — the screen is
  purely event-driven off real mail. (If you wanted the coordinator to poke a slow
  interviewer, you'd add a `pings` cron rule to it.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/candidate-screen.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for all five agents).
2. Creates the runtime dirs (`screen-workspace/.agentainer/…`: log, queue, run,
   sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: the coordinator gets
   `outbox/scheduler/`, `outbox/technical/`, `outbox/behavioral/`,
   `outbox/scorer/`, `outbox/user/`; the technical agent gets
   `outbox/coordinator/`; the scorer gets `outbox/coordinator/` and
   `outbox/user/`.
4. **Installs per-type turn detection** — the Claude Stop hook for the four
   `claude` agents, the Codex `notify` hook for the `technical` agent.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'screen' is up with 5 agent(s)
:: attach with:  tmux attach -t <coordinator-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/candidate-screen.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only bind (the UI binds `127.0.0.1` by default — never expose it to
`0.0.0.0` without a token; see CLAUDE.md §18). See the `README.md`
"control-plane UI" section.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole screen route mail with no API keys — the mechanics are identical.

---

## 4. Drive a screen

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the scorer's summary as mail (rather than have
it held), turn yourself available first:

```bash
./agentainer user available -c examples/candidate-screen.yaml
```

This rewrites the `user` contact card in the scorer's and coordinator's
`outbox/user/about.md` to `Status: available`, so the scorer sees you're reachable.
(While away, mail to you is *held* and the sender gets a `system` ack — nothing
bounces.)

Now send the candidate brief into the swarm, addressed to the coordinator:

```bash
./agentainer send --to coordinator "Screen Jane for a senior backend role; stack is Go + Postgres."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the coordinator, then — because
the inbox was empty — **released into `inbox/`** and the coordinator is **nudged**
(the protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§5), you'll see the screen advance one turn at a time. Each arrow
is a `stop → sweep → route → release → nudge` cycle:

1. **coordinator receives the brief.** It reads `inbox/`, restates the candidate
   and role, and asks the scheduler for a plan. When its turn ends, the
   orchestrator sweeps the outbox, routes the message to the scheduler, and nudges
   it.
2. **scheduler returns PLAN.md.** It reads the request, drafts the question set +
   timeline + rubric, writes to `outbox/coordinator/`. On stop, that routes back to
   the coordinator.
3. **coordinator briefs both interviewers.** It writes a brief into
   `outbox/technical/` and `outbox/behavioral/` (separate messages, same context)
   and finishes its turn. Both are swept and nudged.
4. **the two rounds run independently.** `technical` does its coding round and
   writes a scored write-up to `outbox/coordinator/`; `behavioral` does its round
   and writes its scored write-up to `outbox/coordinator/`. Neither can see the
   other's score.
5. **coordinator bundles and sends to scorer.** It reads both write-ups, sends them
   together to `outbox/scorer/`. On stop, that routes to the scorer.
6. **scorer returns the summary.** It merges the scores, notes agreement/conflict,
   and writes one hire/no-hire recommendation into `outbox/user/`. On stop, that's
   delivered to your `user` mailbox (you'll see it with `agentainer user inbox`, or
   in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a brief, the agents just sit in standby (that's the point of
> the standby prompt). The screen only moves when real mail arrives — this swarm
> has no periodic pings to self-start it.

---

## 5. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/candidate-screen.yaml
```

```
swarm: screen   root: ./screen-workspace
  coordinator (claude) up idle queue=0 unread=1 talks=scheduler, technical, behavioral, scorer, user
  scheduler    (claude) up idle queue=0 unread=0 talks=coordinator
  technical    (codex)  up idle queue=0 unread=0 talks=coordinator
  behavioral   (claude) up idle queue=0 unread=0 talks=coordinator
  scorer       (claude) up idle queue=0 unread=0 talks=coordinator, user
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/candidate-screen.yaml          # whole swarm, last 20
./agentainer logs -c examples/candidate-screen.yaml -f        # follow live
./agentainer logs technical -c examples/candidate-screen.yaml  # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox coordinator -c examples/candidate-screen.yaml
```

Prints the one released message (headers + body), or `coordinator: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue coordinator -c examples/candidate-screen.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach technical -c examples/candidate-screen.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 6. Resume after a stop

Tear the swarm down when you're done:

```bash
./agentainer down -c examples/candidate-screen.yaml
```

Bring it back later and **conversations resume by default**:

```bash
./agentainer up -c examples/candidate-screen.yaml     # resume is the default
```

On `up`, Agentainer reads `screen-workspace/.agentainer/sessions.yaml` (written as
each agent finished its first turn) and reattaches the recorded conversations via
each type's native resume: `claude --resume <id>` for the four `claude` agents,
`codex resume <id>` for the `technical` agent. A resumed agent is *not* re-sent the
standby prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/candidate-screen.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md) and
the reboot walkthrough in [`use-cases/resume-after-reboot.md`](./resume-after-reboot.md).

---

## 7. Tips & footguns

- **Keep the coordinator and scorer the only `user`-facing agents.** Only they list
  `user` in `can_talk_to`. The coordinator handles the final hand-off; the scorer
  can deliver the summary straight to you. The interviewers never see the human, so
  raw candidate answers don't leak past review. If an interviewer tried to mail
  `user` directly, the orchestrator bounces it (ACL) and drops a `system` note in
  its inbox explaining who it *can* message — the model self-corrects in-band.

- **The interviewers must stay independent.** `technical` and `behavioral` are both
  leaf nodes that can only reach the coordinator. That's deliberate: it stops one
  round's score from contaminating the other. Don't add `technical → behavioral`
  (or vice-versa) unless you *want* leakage — it defeats the point.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually fires** —
  a `type`/`command` mismatch (e.g. a `codex` agent whose `command` doesn't launch
  Codex) means completion never triggers and the agent pins "busy" forever.
  `status` showing an agent `busy` for a long time with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down             -c examples/candidate-screen.yaml
  ./agentainer remove-session   -c examples/candidate-screen.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

- **Availability shapes the ending.** If `user` is **away** when the scorer
  finishes, your summary is *held* (with a `system` "the user is away" ack to the
  scorer) rather than lost — read it later with `agentainer user inbox` or flip
  yourself available and it's delivered.

---

## 8. Customize

This swarm is a starting point. A few small edits change its shape:

- **Add a `takehome` agent.** Want a coding-take-home round in the middle? Add an
  agent (e.g. a `codex` "takehome" interviewer) whose `can_talk_to: [coordinator]`,
  brief it to set the task and later review the submission, and give the coordinator
  `takehome` in its `can_talk_to`. The hub sequences it between the brief and the
  scorer — no other agent needs to know it exists.

- **Swap models.** Any agent's `type` can be `claude`, `codex`, `gemini`, or
  `hermes` (with a `command` that launches that CLI). Run `behavioral` on `gemini`
  and keep `technical` on `codex`, or mix in `hermes` — just keep `command` matched
  to `type` or the mismatch detector fails `up`.

- **Tune the ACL.** Tighten so the scorer *only* reaches the `user` (not the
  coordinator) for extra isolation, or widen so the coordinator can also deliver the
  summary if the scorer is offline. Wider ACL = more relays; tighter = more
  funneled. The orchestrator bounces anything off-list either way.

- **Add periodic pings.** If an interviewer is slow, add a `pings` cron rule to that agent so the orchestrator nudges it on a timer instead of waiting for
  you to notice.

- **Persist the rubric.** The scheduler writes `PLAN.md` in its workdir; point its
  `workdir` at a shared folder (or read it from the coordinator) if you want the
  question set to live across screens.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders, read/write verbs, and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming agents after a stop.
- [`use-cases/delegation-pipeline.md](./delegation-pipeline.md) — the hub-and-spoke pattern this screen is built on.
- [`use-cases/multi-llm-swarm.md](./multi-llm-swarm.md) — mixing `claude`/`codex`/`gemini`/`hermes` in one swarm.
- `examples/quickstart.yaml` — the key-free mock-agent starter.

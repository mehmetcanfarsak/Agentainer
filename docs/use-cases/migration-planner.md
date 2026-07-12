# Use case: the migration planner swarm

A concrete, end-to-end walkthrough of the shipped `examples/migration-planner.yaml`
swarm — a four-agent hub-and-spoke where a **lead** takes a one-line migration
goal from a human, an **assessor** inventories the risks, a **planner** writes the
step-by-step cutover runbook, a **rollback** agent writes the fallback plan, and
the lead delivers a reconciled *plan + rollback* pair back to the human. It's the
"turn a scary migration into a reviewed, reversible plan" loop, wired entirely
through Agentainer's file-based mail model.

**Who this is for:** SREs and on-call engineers staring down a database version
bump or a cloud provider move; platform and infra teams who need a cutover runbook
*and* an abort plan that actually fit together; anyone who has to write a go/no-go
doc and wants the risk register, the runbook, and the rollback drafted in parallel
and reconciled in one place.

Everything below is based on the actual contents of `examples/migration-planner.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you supply the coding-CLI
commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The topology

```
        migration goal
  user ───────────────────▶ lead ◀───────────────▶ assessor
        (plan + rollback) ◀───┼───────────────────  (risk register)
                              │
                              ├───────────────────▶ planner
                              │                      (cutover runbook)
                              │
                              └───────────────────▶ rollback
                                                     (fallback plan)
```

Four agents, one hub. The `lead` is the only agent that talks to the human; the
three specialists talk only to the `lead`:

1. **`user` → `lead`** — you send the one-line migration goal.
2. **`lead` → `assessor`** — the lead restates it as a brief and asks for risks
   *first*.
3. **`assessor` → `lead`** — the risk register comes back.
4. **`lead` → `planner`** and **`lead` → `rollback`** — the lead forwards the
   brief + risks; the planner writes the cutover runbook, the rollback agent
   writes the fallback plan.
5. **`planner` → `lead`**, **`rollback` → `lead`** — both drafts return.
6. **`lead` → `user`** — the lead reconciles the two documents (every irreversible
   cutover step must have a matching abort path) and delivers both, with a go/no-go
   recommendation on top.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. An agent can only deliver to names on its own list; anything
else is bounced back as a `system` message and filed in `failed/` (see §7).

```
lead      can_talk_to: [assessor, planner, rollback, user]   ← the hub
assessor  can_talk_to: [lead]
planner   can_talk_to: [lead]
rollback  can_talk_to: [lead]
```

The specialists deliberately **cannot talk to each other or to `user`**. The
rollback plan referencing the planner's runbook doesn't require them to talk —
the lead brokers the hand-off (§4). Keeping the human-facing surface to one agent
means you get one reconciled deliverable, not three racing drafts.

---

## 2. The config, explained

Here is `examples/migration-planner.yaml`, field by field (the roles are trimmed
here for space — read the file for the full standing instructions).

### `swarm`
- **`name: migration`** — the swarm's name (shows up in `status`, logs, sessions).
- **`root: ./migration-workspace`** — the parent directory for the agents' working
  directories and mailboxes. Each agent gets `migration-workspace/<name>/` as its
  workdir (created on `up`), and its mailbox folders live alongside. Orchestrator
  state goes under `migration-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, and it's ultimately keyed off each
  agent's `type`. All four agents here are `claude`, whose CLI supports a
  completion **hook** — so `capture: none` is a footgun, and the config loader
  *upgrades* it back to `hook` with a warning at `up`. Net effect: every agent
  gets its Stop hook.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `lead` (type: `claude`) — the hub
- **`can_talk_to: [assessor, planner, rollback, user]`** — the lead can brief all
  three specialists and it is the **only agent that can talk to `user`**. That
  last part matters — keep the human-facing surface to a single agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity: restate the goal as a brief, brief the
  assessor first, forward brief + risks to planner and rollback, reconcile the two
  documents, deliver both to the user with a go/no-go on top. On `up` this becomes
  the agent's first prompt, wrapped in a **standby notice** ("no task yet — don't
  send anything, you'll be notified"), so the lead waits for your goal instead of
  proactively mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).
- **The MAILBOX reminder** at the end of the role is the hub-agent convention:
  read `inbox/`, act, move to `read/`; to send, write into `outbox/<name>/` after
  reading `outbox/<name>/about.md`, then finish the turn. It's restated in every
  nudge too, so a forgetful model always has the protocol in front of it.

### `assessor` (type: `claude`)
- **`can_talk_to: [lead]`** — reports the risk register only to the lead.
- **`role`** — inventory breaking changes, incompatible extensions/drivers,
  replication implications, data-size-vs-downtime, and irreversible steps; rank
  the risks; turn missing facts into OPEN QUESTIONS rather than guessing.

### `planner` (type: `claude`)
- **`can_talk_to: [lead]`** — reports the cutover runbook only to the lead.
- **`role`** — write the ordered runbook the on-call engineer follows at 2am:
  pre-flight, stand-up + data load, validation gates, the cutover switch,
  post-cutover verification, cleanup. Every step gets an owner, a concrete action,
  an expected result, and a proceed/hold checkpoint; **irreversible** steps are
  marked (the rollback agent depends on this).

### `rollback` (type: `claude`)
- **`can_talk_to: [lead]`** — reports the fallback plan only to the lead.
- **`role`** — make the migration reversible: per phase, the abort trigger, the
  exact steps back to a known-good state, the recovery-time and data-loss window,
  and how to verify the rollback worked. Special attention to the planner's
  irreversible steps — where "roll back" really means "restore from backup and
  replay", say so honestly.

### What's *not* in this config
- **No `periodically_ping_seconds`.** No agent is auto-nudged on a timer while
  idle — the pipeline is purely event-driven off real mail. (If you wanted the
  lead to poke a slow planner, you'd add `periodically_ping_seconds: 300` to it.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **All four agents are `claude`.** This is a single-model swarm on purpose (a
  planning task where consistent reasoning matters). To mix models — say a `codex`
  planner for its shell fluency — see §8 and
  [`multi-llm-swarm.md`](./multi-llm-swarm.md).

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/migration-planner.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for all four agents).
2. Creates the runtime dirs (`migration-workspace/.agentainer/…`: log, queue, run,
   sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: the lead gets
   `outbox/assessor/`, `outbox/planner/`, `outbox/rollback/`, `outbox/user/`;
   each specialist gets just `outbox/lead/`.
4. **Installs per-type turn detection** — the Claude Stop hook for all four agents.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'migration' is up with 4 agent(s)
:: attach with:  tmux attach -t <lead-session>
:: you can use the UI with:  agentainer serve -c examples/migration-planner.yaml
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). By default it binds **`127.0.0.1`** (loopback
only) — pass `--host`/`--token` only for a deliberate remote bind. See
[`../ui-guide.md`](../ui-guide.md) and the `README.md` "control-plane UI" section.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole pipeline route mail with no API keys — the mechanics are identical.

---

## 4. Drive a migration goal

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the lead's final plan as mail (rather than have
it held), turn yourself available first:

```bash
./agentainer user available -c examples/migration-planner.yaml
```

This rewrites the `user` contact card in the lead's `outbox/user/about.md` to
`Status: available`, so the lead sees you're reachable. (While away, mail to you is
*held* and the sender gets a `system` ack — nothing bounces.)

Now send the goal into the swarm, addressed to the lead. Be specific — the more of
the brief you supply up front, the fewer OPEN QUESTIONS bounce back:

```bash
./agentainer send --to lead \
  "Postgres 12 -> 16 on AWS RDS, 400GB, one primary + two read replicas, \
   PgBouncer in front, Rails app, <30min downtime budget, PCI in scope."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the lead, then — because the inbox
was empty — **released into `inbox/`** and the lead is **nudged** (the protocol is
re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§5), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **lead receives the goal.** It reads `inbox/`, writes a tightened brief into
   `outbox/assessor/`, and finishes its turn. The orchestrator sweeps the outbox,
   routes to the assessor, and nudges it.
2. **assessor returns risks.** It reads its inbox, writes the risk register into
   `outbox/lead/`. On stop, that routes back to the lead.
3. **lead fans out.** It reads the risks and writes *two* files — one into
   `outbox/planner/` and one into `outbox/rollback/` (brief + risks each). On
   stop, both route out and both specialists are nudged. (The lead brokers the
   planner→rollback dependency by briefing rollback with the planner's runbook
   once it lands — the specialists never talk directly.)
4. **planner and rollback draft.** Each reads its inbox, writes its document into
   `outbox/lead/`. On stop, each routes back to the lead.
5. **lead reconciles and finalizes.** With both drafts in hand, it checks that
   every irreversible cutover step has a matching abort path, writes the combined
   plan + rollback + go/no-go into `outbox/user/`. On stop, that's delivered to
   your `user` mailbox (you'll see it with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a goal, the agents just sit in standby (that's the point of
> the standby prompt). The pipeline only moves when real mail arrives — this swarm
> has no periodic pings to self-start it.

---

## 5. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/migration-planner.yaml
```

```
swarm: migration   root: ./migration-workspace
  lead (claude) up idle queue=0 unread=0 talks=assessor, planner, rollback, user
  assessor (claude) up idle queue=0 unread=1 talks=lead
  planner (claude) up idle queue=0 unread=0 talks=lead
  rollback (claude) up idle queue=0 unread=0 talks=lead
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/migration-planner.yaml           # whole swarm, last 20
./agentainer logs -c examples/migration-planner.yaml -f         # follow live
./agentainer logs assessor -c examples/migration-planner.yaml   # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox planner -c examples/migration-planner.yaml
```

Prints the one released message (headers + body), or `planner: inbox is empty`.

**Queue depth** — mail waiting behind the one released message (useful when the
lead has fanned out and a specialist is still busy):

```bash
./agentainer queue lead -c examples/migration-planner.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach lead -c examples/migration-planner.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 6. Iterate on the plan

Migration planning is rarely one-shot — the first go/no-go usually surfaces an
open question or an unacceptable data-loss window. Because `user` talks to the
`lead`, you just reply into the same thread:

```bash
./agentainer send --to lead \
  "Downtime budget is hard at 15min, not 30. Re-do the cutover under that and \
   tell me if a logical-replication cutover beats a dump/restore here."
```

The lead re-briefs the planner (and, since the runbook changed, rollback again),
reconciles, and returns an updated pair. Everything is one turn at a time, so you
can watch each hop in `logs -f`. This is the same delegate → do → reconcile loop
covered in [`delegation-pipeline.md`](./delegation-pipeline.md).

### Resume after a stop

Tear the swarm down when you're done for the day:

```bash
./agentainer down -c examples/migration-planner.yaml
```

Bring it back later and **conversations resume by default**:

```bash
./agentainer up -c examples/migration-planner.yaml     # resume is the default
```

On `up`, Agentainer reads `migration-workspace/.agentainer/sessions.yaml` (written
as each agent finished its first turn) and reattaches the recorded conversations
via each type's native resume — `claude --resume <id>` for all four agents here.
A resumed agent is *not* re-sent the standby prompt (its prior context, including
the half-finished plan, is restored). Pass `--no-resume` to force everyone fresh.
Inspect what's recorded with `agentainer sessions -c …`. For the full story, see
[`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 7. Tips & footguns

- **Keep the lead the only `user`-facing agent.** In this config only the lead
  lists `user` in `can_talk_to`. That gives you a single point of contact and a
  clean funnel: the cutover plan and the rollback plan are always reconciled before
  they reach you. If the planner tries to mail `user` directly, the orchestrator
  bounces it (ACL) and drops a `system` note in the planner's inbox explaining who
  it *can* message — the model self-corrects in-band.

- **Brief the assessor first, rollback last.** The role instructions sequence the
  fan-out deliberately: risks before plans, and rollback after the planner's
  runbook exists, so the fallback plan can cover the actual irreversible steps. If
  the lead skips ahead and briefs everyone at once, the rollback plan won't line
  up with the runbook — send the lead a nudge to re-order.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually fires**
  — a `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't
  launch Claude) means completion never triggers and the agent pins "busy" forever.
  `status` showing an agent `busy` for a long time with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **Force-idle a wedged agent.** If a turn never registers you can nudge the state
  along:
  ```bash
  ./agentainer idle planner -c examples/migration-planner.yaml
  ```

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/migration-planner.yaml
  ./agentainer remove-session -c examples/migration-planner.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

- **Availability shapes the ending.** If `user` is **away** when the lead finishes,
  your final plan is *held* (with a `system` "the user is away" ack to the lead)
  rather than lost — read it later with `agentainer user inbox` or flip yourself
  available and it's delivered.

---

## 8. Customize

- **Add a `cost_estimator`.** A migration go/no-go usually needs a price tag —
  new instance class, extra storage during dual-run, data-transfer egress. Add a
  fifth agent, give the lead reach to it, and keep it lead-only:
  ```yaml
    - name: cost_estimator
      type: claude
      can_talk_to: [lead]
      command: "claude --dangerously-skip-permissions"
      role: |
        You are the COST ESTIMATOR. Given the brief and the cutover runbook,
        estimate the one-off migration cost (dual-running old + new, extra
        storage/IOPS, data egress) and the steady-state delta after cutover.
        Show your assumptions. Report only to the lead by writing to outbox/lead/.
  ```
  Then extend the lead's ACL: `can_talk_to: [assessor, planner, rollback, cost_estimator, user]`,
  and add "brief the cost_estimator once the runbook is settled" to its role.

- **Swap models per role.** All four agents are `claude` here for consistent
  reasoning, but nothing stops you mixing. A `codex` planner (`type: codex`,
  `command: "codex --yolo"`) is fluent in shell/`pg_dump` incantations; a `gemini`
  assessor (`type: gemini`, `command: "gemini --yolo"`, `capture: pane`) widens
  the coverage. Just keep `type` and `command` consistent or the turn signal never
  fires (§7). See [`multi-llm-swarm.md`](./multi-llm-swarm.md).

- **Tune the ACL for your safety posture.** The default is maximally funneled. If
  you *want* the planner and rollback agent to iterate directly (faster, but the
  lead loses oversight of the hand-off), add each other to their lists:
  `planner … can_talk_to: [lead, rollback]` and `rollback … can_talk_to: [lead, planner]`.
  Conversely, to lock things down further, drop `user` from the lead until you've
  reviewed a draft in the UI, then add it back. The ACL is cooperative, not OS
  isolation — it shapes well-behaved agents; it is not a security boundary.

- **Point agents at a real repo.** By default each agent gets an empty workdir.
  To let the assessor read your actual migration scripts or schema, give it a
  shared or existing `workdir` — see [`custom-workspace.md`](./custom-workspace.md).

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders, routing, ACL, read state.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — how resume works.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — the delegate → do →
  reconcile pattern this swarm is built on.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing claude/codex/gemini/hermes.
- `examples/quickstart.yaml` — the key-free mock-agent starter.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

# Use case: the legacy refactoring / modernization planner

A concrete, end-to-end walkthrough of the shipped `examples/refactor-planner.yaml`
swarm — a four-agent **analyze → plan → implement → test** pipeline for modernizing
a legacy codebase *safely*. A single human-facing `analyst` studies the old code,
briefs a `planner`, and then reviews the `implementer`'s diffs **together with** a
`tester` that pins behavior before anything moves. It is the canonical
"understand it, design a minimal change, migrate one slice at a time, prove it
still works" loop, wired entirely through Agentainer's file-based mail model.

Everything below is based on the actual contents of
`examples/refactor-planner.yaml` and the shipped CLI (`lib/cli.py`) and mailroom
(`lib/mail.py`). No API keys are needed to understand the mechanics; to run it
*for real* you supply the coding-CLI commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in [`mail-model.md`](../mail-model.md). The one-line
> version: an agent **reads a file** to receive mail and **writes a file** to send
> it; the orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The topology

```
          ┌─────────┐
   user ─▶│ analyst │◀── reviews implementer diffs (with the tester)
          └────┬────┘
        ┌──────┼───────┐
        ▼      ▼       ▼
    planner  implementer  tester
    (design) (migrate)   (characterize)
```

Four agents, one star:

1. **`user` → `analyst`** — you hand over a legacy target (a module, a service, a
   client) and what "modernized" should mean.
2. **`analyst` → `planner`** — the analyst characterizes risk/couplings and briefs
   the planner on the minimal target design.
3. **`planner` → `analyst`** — the planner returns sequenced boundaries + interfaces
   that preserve behavior; the analyst sanity-checks them.
4. **`analyst` → `implementer`** — each slice is delegated with its exact interface
   contract + acceptance criteria.
5. **`implementer` → `analyst`** — the implementer migrates one slice and reports a
   summary back.
6. **`tester` → `analyst`** — the tester characterizes each slice *before* the
   change and reports, with the analyst, whether behavior still holds after.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. The planner, implementer and tester **cannot** reach each
other directly; everything routes through the analyst. An agent that tries to mail
an unauthorized peer is bounced back as a `system` message filed in `failed/` (see
§7). This keeps the modernization coordinated through one accountable hub instead
of being negotiated in three places at once.

---

## 2. The config, explained

Here is `examples/refactor-planner.yaml` in full (header + agents, abridged roles):

```yaml
# 🧭 Legacy refactoring / modernization planner -- analyze → plan → implement → test.
#   cp examples/refactor-planner.yaml my-refactor.yaml
#   agentainer up     -c my-refactor.yaml
#   agentainer send   -c my-refactor.yaml --to analyst "Plan a safe migration ..."
#   agentainer down   -c my-refactor.yaml
# All four agents SHARE one workdir (the legacy codebase); mail is auto-namespaced.
swarm:
  name: refactor
  root: ./refactor-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: analyst
    type: claude
    can_talk_to: [planner, implementer, tester, user]
    command: "claude --dangerously-skip-permissions"
    workdir: ./legacy-repo
    create_workdir: true
    role: "The human-facing planner of record. Characterize risk, brief the planner, review implementer diffs with the tester."
  - name: planner
    type: claude
    can_talk_to: [analyst]
    command: "claude --dangerously-skip-permissions"
    workdir: ./legacy-repo
    create_workdir: true
    role: "Propose the smallest sequenced target design that preserves behavior. Write DESIGN.md."
  - name: implementer
    type: codex
    can_talk_to: [analyst]
    command: "codex --yolo"
    workdir: ./legacy-repo
    create_workdir: true
    role: "Migrate ONE slice at a time to the contract the analyst gave. Report summaries to the analyst."
  - name: tester
    type: codex
    can_talk_to: [analyst]
    command: "codex --yolo"
    workdir: ./legacy-repo
    create_workdir: true
    role: "Characterize each slice before it changes; report pass/fail to the analyst with the diff review."
```

Field by field:

### `swarm`
- **`name: refactor`** — the swarm's name (shows up in `status`, logs, sessions).
- **`root: ./refactor-workspace`** — the parent directory for per-swarm
  orchestrator state. Each agent's `workdir` is set explicitly below to
  `./legacy-repo` (relative to this config file), so the agents operate inside the
  codebase being modernized rather than in scratch dirs under `root`. Orchestrator
  state still goes under `refactor-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, and it's ultimately keyed off each
  agent's `type`. For `claude` (`analyst`, `planner`) and `codex` (`implementer`,
  `tester`), whose CLIs support a completion **hook**, `capture: none` is a
  footgun — so the config loader *upgrades* it back to `hook` and prints a warning
  at `up`. Net effect here: all four agents use their hook. (Set
  `capture: none` deliberately only on a mock/gemini-hermes-style pane agent.)
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent states
  its own list explicitly, so this default is just a safe floor.

### Shared workdir + auto-namespacing (the important bit)
All four agents set **the same `workdir: ./legacy-repo`**. That is deliberate:
they must edit the *same* checkout so the planner's `DESIGN.md`, the implementer's
migrated slices, and the tester's tests all live in one place. But four agents
sharing one directory would collide on their `inbox/`/`outbox/`/… folders — so
Agentainer **auto-namespaces** them. On load, `SwarmConfig.__post_init__` counts
agents per resolved workdir; any workdir shared by 2+ agents is recorded, and
`mail_paths(agent)` prefixes every mailbox folder with `<name>-`. On disk you get:

```
legacy-repo/
  analyst-inbox/   analyst-outbox/   analyst-read/   analyst-sent/   analyst-failed/
  planner-inbox/   planner-outbox/   …               planner-sent/   planner-failed/
  implementer-inbox/ …               implementer-sent/ …             …
  tester-inbox/    …                 tester-sent/    …               …
  <your real legacy source files, unprefixed>
```

The prefix is **orchestrator-internal bookkeeping** — the model never sees it.
Every nudge and first prompt is handed the already-computed absolute paths
(`analyst` sees `…/legacy-repo/analyst-inbox`, etc.), so a weak model can't get
the prefix wrong. See [`custom-workspace.md`](./custom-workspace.md) for the full
treatment. (`create_workdir: true` spins up the dir for a dry run; point it at your
real checkout and set `create_workdir: false` so Agentainer refuses rather than
silently creating an empty dir.)

### `analyst` (type: `claude`, the hub)
- **`can_talk_to: [planner, implementer, tester, user]`** — the analyst is the
  **only** agent that may talk to `user`, and the only one the spokes report to.
  That single-funnel property is what keeps the modernization coordinated.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command (substitute your own alias). Treat command strings as sensitive; they
  may embed keys.
- **`role`** — carries the standing runbook (brief the planner, delegate one slice
  at a time, review diffs *with* the tester) **plus a short MAILBOX reminder**
  telling it to read `inbox/`, move handled mail to `read/`, and write to
  `outbox/<name>/` finishing its turn. The reminder is re-pasted on every nudge.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `planner` (type: `claude`)
- **`can_talk_to: [analyst]`** — proposes the minimal, sequenced target design
  (boundaries, interfaces) preserving behavior, and writes it to `DESIGN.md`. It
  deliberately cannot reach the implementer or tester; the analyst gates all flow.
- **Turn detection:** `claude` → Stop hook.

### `implementer` (type: `codex`)
- **`can_talk_to: [analyst]`** — migrates one slice at a time to the contract the
  analyst gave, and sends summaries back to the analyst (never to the tester or
  planner directly).
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `tester` (type: `codex`)
- **`can_talk_to: [analyst]`** — writes characterization/regression tests around
  each slice **before and with** the change, and reports the verdict to the analyst
  (so the analyst reviews the diff *with* the tester's read on correctness). It
  does not change production code itself.
- **Turn detection:** `codex` → `notify` hook.

### What's *not* in this config
- **No `pings`.** None of the four agents is auto-nudged on a
  timer; the pipeline is purely event-driven off real mail. (If you wanted the
  analyst to poke a slow implementer, add a `pings` cron rule to it.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/refactor-planner.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for all four agents, and the shared-workdir
   notice that `analyst/planner/implementer/tester` share `./legacy-repo`).
2. Creates the runtime dirs (`refactor-workspace/.agentainer/…`: log, queue, run,
   sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, auto-prefixed with `<name>-` because the
   workdir is shared. The per-agent queue and an `outbox/<peer>/` folder **for each
   allowed recipient** are created. That folder's `about.md` contact card *is* the
   ACL made visible: the analyst gets `outbox/planner/`, `outbox/implementer/`,
   `outbox/tester/`, `outbox/user/`; the spokes each get just `outbox/analyst/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `analyst` and
   `planner`, the Codex `notify` hook for `implementer` and `tester`.
5. **Opens one tmux session per agent**, `cd`'d into the shared `./legacy-repo`
   workdir, running its `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified"), including the exact computed mailbox paths so the model knows where
   to read/write despite the namespacing.
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'refactor' is up with 4 agent(s)
:: attach with:  tmux attach -t <analyst-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/refactor-planner.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only bind — the control plane can type into agents that may run
`--dangerously-skip-permissions`, so it binds `127.0.0.1` by default and only
exposes remotely behind a token (see [`ui-guide.md`](../ui-guide.md) and CLAUDE.md
§18). See the `README.md` "control-plane UI" section.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole analyze→plan→implement→test pipeline route mail with no API keys — the
> mechanics are identical.

---

## 4. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the analyst's final sign-off as mail (rather
than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/refactor-planner.yaml
```

This rewrites the `user` contact card in the analyst's `outbox/user/about.md` to
`Status: available`, so the analyst sees you're reachable. (While away, mail to you
is *held* and the analyst gets a `system` ack — nothing bounces.)

Now hand the legacy target to the analyst:

```bash
./agentainer send -c examples/refactor-planner.yaml --to analyst \
  "Plan a safe migration of the old payments module to the new client."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the analyst, then — because the
inbox was empty — **released into `analyst-inbox/`** and the analyst is **nudged**
(the protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§5), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **analyst receives the target.** It reads `analyst-inbox/`, explores the legacy
   code, characterizes couplings/risk, and writes a brief + acceptance list into
   `outbox/planner/`. On stop, the orchestrator sweeps and routes to the planner.
2. **planner designs.** It reads its inbox, writes `DESIGN.md` (boundaries,
   interfaces, slice order) and reports back to `outbox/analyst/`. On stop, that
   routes to the analyst.
3. **analyst delegates a slice.** It hands the implementer the contract +
   acceptance criteria via `outbox/implementer/`. The tester is asked to
   characterize that slice first, via `outbox/tester/`.
4. **implementer migrates; tester verifies.** The implementer writes the migrated
   slice and a summary into `outbox/analyst/`; the tester writes tests and a
   pass/fail verdict into `outbox/analyst/`. The analyst reviews the diff *with*
   the tester's verdict before approving the next slice.
5. **analyst finalizes.** Once acceptance criteria hold, the analyst writes the
   vetted outcome into `outbox/user/`. On stop, that's delivered to your `user`
   mailbox (visible via `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a target, the agents just sit in standby (that's the point of
> the standby prompt). The pipeline only moves when real mail arrives.

---

## 5. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/refactor-planner.yaml
```

```
swarm: refactor   root: ./refactor-workspace
  analyst (claude) up idle queue=0 unread=1 talks=planner, implementer, tester, user
  planner (claude) up idle queue=0 unread=0 talks=analyst
  implementer (codex) up idle queue=0 unread=0 talks=analyst
  tester (codex) up idle queue=0 unread=0 talks=analyst
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/refactor-planner.yaml          # whole swarm, last 20
./agentainer logs -c examples/refactor-planner.yaml -f        # follow live
./agentainer logs planner -c examples/refactor-planner.yaml   # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox implementer -c examples/refactor-planner.yaml
```

Prints the one released message (headers + body), or `implementer: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue tester -c examples/refactor-planner.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach analyst -c examples/refactor-planner.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 6. Resume after a stop

Tear the swarm down when you're done:

```bash
./agentainer down -c examples/refactor-planner.yaml
```

Bring it back later and **conversations resume by default**:

```bash
./agentainer up -c examples/refactor-planner.yaml     # resume is the default
```

On `up`, Agentainer reads `refactor-workspace/.agentainer/sessions.yaml` (written
as each agent finished its first turn) and reattaches the recorded conversations
via each type's native resume: `claude --resume <id>` for the analyst and planner,
`codex resume <id>` for the implementer and tester. A resumed agent is *not*
re-sent the standby prompt — its prior context (the brief, the `DESIGN.md`, the
in-flight slice) is restored, which is exactly what you want mid-modernization.

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/refactor-planner.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 7. Tips & footguns

- **Keep the analyst the only `user`-facing agent.** Only the analyst lists `user`
  in `can_talk_to`. That gives you a single point of contact and a clean funnel:
  raw diffs and test verdicts always pass through the analyst's review before they
  reach you. If an implementer or tester tries to mail `user` directly, the
  orchestrator bounces it (ACL) and drops a `system` note in that agent's inbox
  explaining who it *can* message — the model self-corrects in-band.

- **The spokes can't coordinate around the analyst's back.** `planner`,
  `implementer`, and `tester` each list only `analyst`. This is a *cooperative* ACL
  (see CLAUDE.md footgun) — they have filesystem access and could write straight
  into another's inbox, but well-behaved agents stay on the mail model. The value
  of the constraint is keeping the human's modernization plan coherent, not OS
  isolation.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually fires**
  — a `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't
  launch Claude) means completion never triggers and the agent pins "busy" forever.
  `status` showing an agent `busy` for a long time with `unread` mail is the tell.
  The shared workdir is also flagged at `up` with a reminder that a shared git
  checkout will interleave commits — coordinate through mail.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down              -c examples/refactor-planner.yaml
  ./agentainer remove-session    -c examples/refactor-planner.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It **never** touches the agents' source files or your config, so your
  `./legacy-repo` checkout is safe.

- **Availability shapes the ending.** If `user` is **away** when the analyst
  finishes, your final sign-off is *held* (with a `system` "the user is away" ack
  to the analyst) rather than lost — read it later with `agentainer user inbox` or
  flip yourself available and it's delivered.

---

## 8. Customize

- **Add a `doc` agent that captures the migration as it happens.** Drop in a fifth
  role that only talks to the analyst and keeps `MIGRATION.md` honest — what
  changed and what a caller must do differently. Give it `can_talk_to: [analyst]`
  and the same `workdir: ./legacy-repo`, and the mail folders are auto-namespaced
  (`doc-inbox/`, …) without any extra config.

- **Swap the models.** Every `type`/`command` pair is independent. Want Gemini to
  do the characterization, or Hermes to plan? Change `type:`/`command:` per agent
  (keep them matching — `gemini --yolo`, `hermes`), and set `capture: pane` for
  pane-polling types. A multi-vendor setup is exactly the
  [`multi-llm-swarm.md`](../multi-llm-swarm.md) scenario.

- **Tune the ACL.** The star here is strict by design. If you trust the implementer
  and tester to coordinate directly (e.g. the tester hands a failing test straight
  to the implementer), add `tester` to the implementer's `can_talk_to` and
  vice-versa — just remember the analyst stops being the single funnel for that
  pair. See [`delegation-pipeline.md`](../delegation-pipeline.md) for the
  trade-offs of flatter vs. hubbed graphs.

- **Point at your real codebase.** Replace `workdir: ./legacy-repo` (all four) with
  the path to your actual legacy checkout — relative to this config file, or
  absolute / `~`-expanded — and set `create_workdir: false` so Agentainer refuses
  rather than creating an empty dir. Namespacing still kicks in automatically. Full
  detail in [`custom-workspace.md`](./custom-workspace.md).

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four-folders model, end to end.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resume mid-migration.
- [`delegation-pipeline.md`](../delegation-pipeline.md) — hubbed vs. flat graphs.
- [`multi-llm-swarm.md`](../multi-llm-swarm.md) — mixing Claude/Codex/Gemini/Hermes.
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + namespacing.
- `examples/refactor-planner.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

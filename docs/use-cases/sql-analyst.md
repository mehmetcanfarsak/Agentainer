# Use case: SQL analyst

A concrete, end-to-end walkthrough of the shipped
`examples/sql-analyst.yaml` swarm — a four-agent hub-and-spoke that gives a
non-SQL human trustworthy, warehouse-backed answers. A **hub analyst** owns the
relationship with you; three specialists do the dangerous work: an
**entity-mapper** maps the question to the right tables/columns/joins, a
**sql-writer** writes and runs the query, and a **verifier** checks the number
is plausible and flags entity-mapping mistakes and double-counting *before* the
answer reaches you. The answer is not delivered to you until the verifier signs
off.

Everything below is based on the actual contents of
`examples/sql-analyst.yaml` and the shipped CLI (`lib/cli.py`) and mailroom
(`lib/mail.py`). No API keys are needed to understand the mechanics; to run it
*for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Analysts, operators, founders, and anyone who needs to ask the company data
warehouse real business questions but does not write SQL — and does not want to
trust an unverified number. The swarm encodes the discipline that makes
self-service analytics safe: a single owner of the human relationship, a
specialist whose *only* job is to map the question to the correct entity (the
#1 source of wrong answers), a writer who runs the query, and a second
independent guardrail that refuses to let a bad number reach you.

It is deliberately a **hub-and-spoke**, not a free-for-all: every question and
every answer passes through the analyst, so the contract with you has exactly
one authority and the two guardrails (entity-mapping, then verification) are
enforced in sequence. The spokes never talk to each other and only the analyst
reaches `user`.

---

## 2. The topology

```
          user
            |
         analyst            (the hub: talks to all three specialists + user)
          /   |   \
   entity-mapper sql-writer verifier
                  \______/
         (both read the SAME schema.md snapshot that
          entity-mapper keeps in its workdir)
```

Four agents, one directed flow:

1. **`user` → `analyst`** — you send a plain-English business question.
2. **`analyst` → `entity-mapper`** — the analyst forwards the question (with the
   business definition) and asks for the table/column/join mapping.
3. **`entity-mapper` → `analyst`** — returns the entities, grain, columns, and
   date/scope filter (and flags any ambiguity it can't resolve).
4. **`analyst` → `sql-writer`** (with the mapping) — the writer reads the shared
   `schema.md`, writes the query, and pastes the **raw** result back to the
   analyst.
5. **`analyst` → `verifier`** (with question + mapping + raw result) — the
   verifier checks for entity-mapping mistakes, double-counting, plausibility,
   and scope match. If it signs off, the analyst writes the plain-English answer
   to `outbox/user/`; if it flags a problem, the analyst loops the mapper/writer
   back to fix it.
6. **`analyst` → `user`** — the verified answer reaches you.

The routing above is *enforced* by each agent's `can_talk_to` list. The spokes
(`entity-mapper`, `sql-writer`, `verifier`) can only deliver to `analyst`; only
the `analyst` lists `user`. Anything addressed outside an agent's list is bounced
back as a `system` message and filed in `failed/` (see §7).

---

## 3. The config, explained

Here is `examples/sql-analyst.yaml` in full (role bodies condensed for space):

```yaml
swarm:
  name: sql-analyst
  root: ./sql-analyst-workspace

defaults:
  capture: none
  can_talk_to: []

agents:
  - name: analyst
    type: claude
    can_talk_to: [entity-mapper, sql-writer, verifier, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the ANALYST and the ONLY agent the user talks to. ... you coordinate
      three specialists in strict order: entity-mapper, then sql-writer, then
      verifier; the answer is NOT delivered to the user until the verifier signs
      off. ... You may message: entity-mapper, sql-writer, verifier, user.

  - name: entity-mapper
    type: codex
    can_talk_to: [analyst]
    command: "codex --yolo"
    role: |
      You are the ENTITY-MAPPER -- the first and most important guardrail. ... you
      own the living schema snapshot at schema.md in your workdir and return a
      tight mapping (entities, joins, grain, columns, date/scope filter,
      ambiguity). You report only to the analyst.
    pings:
      - message: |
          Schema freshness check: re-read the warehouse catalog and update your
          schema.md snapshot. ... Report the diff to analyst.
        cron: "13 6 * * 1"        # 06:13 every Monday
        when_busy: skip

  - name: sql-writer
    type: codex
    can_talk_to: [analyst]
    command: "codex --yolo"
    role: |
      You are the SQL-WRITER. Given a precise entity mapping, write the warehouse
      query and paste back the RAW result (number/rows, not prose). Read the
      entity-mapper's shared schema.md first. ... You report only to the analyst.

  - name: verifier
    type: gemini
    can_talk_to: [analyst]
    command: "gemini --yolo"
    role: |
      You are the VERIFIER -- the second and final guardrail. Check (1) entity-
      mapping mistakes, (2) double-counting / fan-out, (3) plausibility, (4) scope
      match. Sign off with "VERIFIED" or send the problem back to the analyst. You
      never write the answer and never talk to the user or other spokes; you
      report only to the analyst.
```

Field by field:

### `swarm`
- **`name: sql-analyst`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./sql-analyst-workspace`** — the parent directory for each agent's
  working directory and mailbox. The four agents default to
  `sql-analyst-workspace/{analyst,entity-mapper,sql-writer,verifier}` (each is
  private — there is no shared workdir here). Orchestrator state goes under
  `sql-analyst-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless overridden.
- **`capture: none`** — the floor. The config *asks* for no capture, but the
  loader auto-upgrades it per type where a hook is required (see §"Turn
  detection" below) — so you don't silently lose the turn-completion signal.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `analyst` (type: `claude`)
- **`can_talk_to: [entity-mapper, sql-writer, verifier, user]`** — the analyst is
  the hub and the **only agent that can talk to `user`**. Keep the human-facing
  surface to a single agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the analyst waits for your question instead of
  proactively mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (auto-upgraded from the
  `none` default at `up`).

### `entity-mapper` (type: `codex`)
- **`can_talk_to: [analyst]`** — the mapper only reports to the analyst. It
  deliberately cannot reach the writer, the verifier, or `user`; the mapping is
  owned by one place and the analyst decides when it's shared downstream.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — owns the living `schema.md` snapshot (tables, columns, keys,
  grain, gotchas like soft-deletes/currency/timezone) and returns a tight
  mapping; it does **not** write the full query and does **not** contact the
  user or other spokes.
- **Turn detection:** `codex` → a `notify` program (its hook), auto-upgraded from
  the `none` default at `up`.
- **`pings`** — a *weekly* self-nudge (`cron: "13 6 * * 1"`, 06:13 every Monday,
  `when_busy: skip`) reminding the mapper to re-read the warehouse catalog and
  refresh `schema.md`. `skip` means it never interrupts a live mapping request
  (the verifier and writer read this file, so a stale schema is the silent killer
  of correct mappings). See §"Pings / cron" below.

### `sql-writer` (type: `codex`)
- **`can_talk_to: [analyst]`** — reports the raw query + result only to the
  analyst; cannot reach `user` or the other spokes.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — reads the mapper's shared `schema.md`, writes correct, efficient
  SQL (mind the grain, soft-deletes, timezone/status filters), and returns the
  query *and* the raw result rows. It does not interpret the number — that's the
  verifier's job.
- **Turn detection:** `codex` → `notify` hook (auto-upgraded).

### `verifier` (type: `gemini`)
- **`can_talk_to: [analyst]`** — the verifier reports its verdict only to the
  analyst; it never writes the answer and never reaches `user` or the other
  spokes.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **`role`** — the second guardrail: checks entity-mapping mistakes, double-
  counting / fan-out joins, plausibility against business invariants, and scope
  match; signs off with "VERIFIED" or sends the problem back to the analyst.
- **Turn detection:** `gemini` → **pane polling** (`capture: none` is *native*
  for `gemini`, so it is **not** auto-upgraded — unlike `claude`/`codex`, gemini
  has no completion hook and is detected by polling its pane).

### ACL enforcement (a cooperative boundary)

`can_talk_to` is the wiring that makes the hub-and-spoke real. The orchestrator
*renders* it as filesystem structure: at `up`, each agent only gets an
`outbox/<peer>/` folder (with an `about.md` contact card) for names on its own
list — the analyst gets `outbox/{entity-mapper,sql-writer,verifier,user}/`,
while each spoke gets exactly `outbox/analyst/`. When a model writes a message
for a recipient, the mailroom checks the sender's ACL; an address outside the
list is rejected, bounced as a `system` note explaining who the agent *can*
message, and filed under `failed/` — so a forgetful or over-eager model
self-corrects in-band. This is **cooperative, not OS isolation** (agents have
filesystem access and could in principle write straight into another inbox); it's
documented honestly as such. For the broader picture see
[`delegation-pipeline.md`](./delegation-pipeline.md).

### What's *not* in this config
- **No shared workdir.** Every agent has its own private directory. The
  `schema.md` hand-off is done *through mail*: the mapper keeps it in its own
  workdir and the writer/verifier read it at the path the analyst (or the file
  itself, referenced in their roles) points them to — no on-disk namespacing is
  needed here. (If you *did* share a workdir, see
  [`custom-workspace.md`](./custom-workspace.md) for the mailbox-namespacing
  mechanics.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **One `pings` block, on `entity-mapper` only.** The other three agents are
  purely event-driven off real mail.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/sql-analyst.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the `auto-upgraded to capture: hook`
   warnings for the three `claude`/`codex` agents (the `gemini` verifier keeps
   `capture: none`).
2. Creates the runtime dirs (`sql-analyst-workspace/.agentainer/…`: log, queue,
   run, sessions).
3. **Initializes the mailboxes** — for each agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **only for each allowed recipient**. (The analyst's
   `outbox/` thus has four peer folders; each spoke has exactly one.)
4. **Installs per-type turn detection** — the Claude Stop hook for `analyst`, the
   Codex `notify` hook for `entity-mapper`/`sql-writer`, and pane polling
   scheduled for the `gemini` verifier.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'sql-analyst' is up with 4 agent(s)
:: attach with:  tmux attach -t <analyst-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/sql-analyst.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents, so it must **never** be exposed on `0.0.0.0` without a token.
See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole analyst→mapper→writer→verifier→user loop route mail with no API keys
> — the mechanics are identical.

---

## 5. Drive a question

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the analyst's verified answer as mail (rather
than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/sql-analyst.yaml
```

This rewrites the `user` contact card in the analyst's `outbox/user/about.md` to
`Status: available`, so the analyst sees you're reachable. (While away, mail to
you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send your business question into the swarm, addressed to the analyst:

```bash
./agentainer send --to analyst -c examples/sql-analyst.yaml \
  "How many paying customers signed up in Q2, and what was their total spend?"
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the analyst, then — because the
inbox was empty — **released into `inbox/`** and the analyst is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **analyst receives the question.** It reads `inbox/`, and (if ambiguous about
   scope/grain/time window) asks you one clarifying question, otherwise sends the
   question to `entity-mapper`. On stop, that routes to the mapper.
2. **entity-mapper returns the mapping.** It reads its inbox, updates/reads its
   living `schema.md`, and writes back the entities, grain, columns, and date
   filter (flagging ambiguity). On stop, that routes to the analyst.
3. **analyst briefs the sql-writer.** It forwards the mapping to `sql-writer`,
   which reads the mapper's `schema.md`, runs the query, and pastes the raw
   result back to the analyst. On stop, that routes to the analyst.
4. **analyst briefs the verifier.** It sends the question + mapping + raw result
   to `verifier`. The verifier checks for entity-mapping mistakes, double-
   counting, plausibility, and scope, and either signs off ("VERIFIED") or sends
   the problem back. On stop, that routes to the analyst.
5. **analyst finalizes.** If verified, it writes a plain-English answer to
   `outbox/user/` stating the number, what it counts, the date window, and
   caveats. If the verifier flagged a problem, the analyst loops the mapper and/
   or writer back to fix it before answering. On stop, the answer is delivered to
   your `user` mailbox (visible with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> The **weekly ping** is the one self-starting event: at 06:13 every Monday the
> `entity-mapper` is nudged to refresh `schema.md`. Because `when_busy: skip`, it
> never interrupts a live mapping request. If the mapper is mid-task at that
> minute, the ping is skipped rather than queued behind it.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/sql-analyst.yaml
```

```
swarm: sql-analyst   root: ./sql-analyst-workspace
  analyst       (claude) up idle queue=0 unread=0 talks=entity-mapper, sql-writer, verifier, user
  entity-mapper (codex)  up idle queue=0 unread=1 talks=analyst
  sql-writer    (codex)  up idle queue=0 unread=0 talks=analyst
  verifier      (gemini) up idle queue=0 unread=0 talks=analyst
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/sql-analyst.yaml           # whole swarm, last 20
./agentainer logs -c examples/sql-analyst.yaml -f         # follow live
./agentainer logs verifier -c examples/sql-analyst.yaml   # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
`ping`, etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox analyst -c examples/sql-analyst.yaml
```

Prints the one released message (headers + body), or `analyst: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue analyst -c examples/sql-analyst.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach verifier -c examples/sql-analyst.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom
— handy for un-sticking an agent, but the mail model is the normal path.)

**The schema snapshot** — inspect the `entity-mapper`'s `schema.md` in
`sql-analyst-workspace/entity-mapper/schema.md` to see the catalog the writer and
verifier are reasoning against.

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or (more directly)
by sending notes into an agent's inbox.

- **Send a clarification to the analyst.** Realized "paying customer" means
  `status = 'active'` not `created`, or the window is fiscal-Q2 not calendar-Q2?
  `./agentainer send --to analyst -c examples/sql-analyst.yaml "Count only
  customers with status='active' at period end; use fiscal Q2 (Apr–Jun)."`. The
  analyst relays the correction to the mapper/writer/verifier.
- **Ask the verifier to show its work.** `./agentainer send --to analyst ...
  "Have the verifier cite the exact row counts it checked."` — the analyst
  forwards it.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send
  as `user`, toggle `user` availability, and watch panes live — useful when you
  want to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/sql-analyst.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/sql-analyst.yaml     # resume is the default
```

On `up`, Agentainer reads `sql-analyst-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the
analyst, `codex resume <id>` for the entity-mapper and sql-writer, and Gemini's
resume for the verifier. A resumed agent is *not* re-sent the standby prompt
(its prior context — including the analyst's running chain of reasoning — is
restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/sql-analyst.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Tighten or widen the ACL
- To let the **verifier** escalate straight to `user` when it finds a serious
  problem (instead of always going through the analyst), add `user` to its
  `can_talk_to`. Mind that this widens the human-facing surface; the doc's
  convention keeps the analyst the sole `user` contact so every answer is gated
  by the hub.
- To stop the spokes from even *being reachable* by name, narrow their lists (no
  change needed here — they already only list `analyst`).
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for the broader
  hub-and-spoke routing discussion, and [`multi-llm-swarm.md`](./multi-llm-swarm.md)
  for mixing model families safely.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `analyst: type: codex` (or `hermes`/`gemini`) to put the hub on a different
  model than the specialists.
- `verifier: type: claude` if you want the final guardrail on Claude — but then
  *its* capture auto-upgrades to the Stop hook, and only `gemini`/`hermes` keep
  `capture: none` (pane polling) since they have no completion hook.
- Remember: `gemini`/`hermes` need pane polling; `claude` → Stop hook; `codex` →
  `notify` hook. The loader protects you from `none` on the hook types by
  auto-upgrading.

### Change the cadence of the schema ping
The `entity-mapper` ping is `cron: "13 6 * * 1"` (Mondays 06:13) with
`when_busy: skip`. For a faster-moving warehouse, tighten the cron (e.g.
`"13 6 * * *"` daily); for a quieter one, leave it weekly. Keep `when_busy: skip`
so a refresh never lands in the middle of an active mapping.

### Tune the guards
The verifier's four checks (entity-mapping, double-counting, plausibility, scope)
live in its `role`. Add domain-specific invariants (e.g. "revenue must be positive",
"Q2 total ≤ annual total") directly into that role text to make the guardrail
stricter for your warehouse. See [`configuration.md`](../configuration.md) for
the full field reference.

---

## 10. Tips & footguns

- **Keep the analyst the only `user`-facing agent.** Only the analyst lists `user`
  in `can_talk_to`. That gives you a single funnel: raw query results and
  verifier verdicts always pass through the hub's review before they reach you.
  If a spoke tries to mail `user` directly, the orchestrator bounces it (ACL) and
  drops a `system` note in their inbox explaining who they *can* message — the
  model self-corrects in-band.

- **The `schema.md` snapshot is the silent killer.** Both the writer and verifier
  reason off the mapper's `schema.md`. If it's stale (renamed table, new
  soft-delete flag), every downstream answer can be wrong *and plausible*. That's
  exactly why the weekly `when_busy: skip` ping exists — don't delete it, and
  treat a mapper that's been silent for weeks with suspicion.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch (e.g. a `claude` agent whose `command`
  doesn't launch Claude) means completion never triggers and the agent pins
  "busy" forever. `status` showing an agent `busy` for a long time with `unread`
  mail is the tell. The `gemini` verifier is detected by *pane polling*, so it
  doesn't need (and shouldn't get) a hook.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/sql-analyst.yaml
  ./agentainer remove-session -c examples/sql-analyst.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

- **Availability shapes the ending.** If `user` is **away** when the analyst
  finishes, your verified answer is *held* (with a `system` "the user is away" ack
  to the analyst) rather than lost — read it later with
  `agentainer user inbox` or flip yourself available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions (`--dangerously-skip-permissions`,
  `--yolo`).

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- [`cli-reference.md`](../cli-reference.md) — every subcommand and flag.
- [`configuration.md`](../configuration.md) — the full field reference.
- `examples/sql-analyst.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

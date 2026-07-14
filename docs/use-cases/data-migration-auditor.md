# Use case: Data migration auditor

A concrete, end-to-end walkthrough of the shipped
`examples/data-migration-auditor.yaml` swarm — a four-agent fidelity audit of a
*completed* data migration (source vs. target after an ETL/DB move). An
**audit-lead** takes your source→target brief, delegates the two independent
comparisons — a **reconciler** (counts / checksums / keys) and a
**transform-checker** (did the rules actually apply?) — then hands the
consolidated findings to a **reporter** who writes the cut-over verdict. Unlike a
migration *plan*, this swarm verifies the data actually moved correctly: nothing
dropped, duplicated, or corrupted, and transforms applied as intended.

Everything below is based on the actual contents of
`examples/data-migration-auditor.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Data engineers, analytics engineers, and platform/DBA teams who have just run
(or inherited) a data migration and need a *structured, defensible* answer to
"did it land correctly?" — without manually diffing millions of rows themselves.
The swarm encodes the discipline that makes an audit trustworthy: an independent
reconciliation (did the records survive?) separated from transform verification
(did the rules apply?), a single lead who sequences and reconciles the two, and a
reporter who turns the two technical reports into a skimmable cut-over verdict
for a human decision-maker.

It is deliberately a **hub-and-spoke** with a hard rule the config enforces: the
reconciler and the transform-checker **never talk to each other**. Letting them
compare notes directly invites double-counting divergence or mis-attributing a
finding (was it dropped, or merely unit-converted?). Both report only to the
audit-lead, who owns the single coherent story. Only the audit-lead and the
reporter may reach you.

---

## 2. The topology

```
          user
            |
        reporter              (writes the verdict; may also ask the lead)
            |
        audit-lead            (the hub: talks to reconciler, transform-checker, reporter, user)
          /        \
  reconciler   transform-checker
   (counts,      (rules applied
    checksums,    correctly?)
    keys)

  reconciler ─────┐
  transform ──────┼──▶ audit-lead ──▶ reporter ──▶ user
  checker         │         ▲              │
                  └─────────┴──────────────┘   (reporter can ask lead for clarification)
```

Four agents, one directed flow:

1. **`user` → `audit-lead`** — you send the source + target descriptions, expected
   row counts, schema map, and the transformation rules that were *supposed* to
   apply.
2. **`audit-lead` → `reconciler` and `audit-lead` → `transform-checker`** — the
   lead fans the same source/target spec out to both specialists in parallel, each
   with a clear, non-overlapping scope.
3. **`reconciler` → `audit-lead`** — counts, checksums, key overlaps, and field
   remaps: *did the records make it across intact?*
4. **`transform-checker` → `audit-lead`** — per-rule PASS/FAIL/PARTIAL with
   before→after examples: *were the transformations correct?*
5. **`audit-lead` → `reporter`** — the lead consolidates both reports (resolving
   any conflict between them) and asks for the final fidelity verdict.
6. **`reporter` → `user`** — the verdict (SAFE TO CUT OVER / CUT OVER WITH CAUTION
   / DO NOT CUT OVER) plus the top risks and remediation plan.

The routing above is *enforced* by each agent's `can_talk_to` list. The
reconciler and transform-checker can each address **only** the audit-lead — never
each other, never the reporter, never you. The reporter can reach the audit-lead
(and ask for clarification) and you. Only the audit-lead and the reporter list
`user` in their ACL. Anything out of bounds is bounced back as a `system` message
and filed in `failed/` (see §7).

---

## 3. The config, explained

Here is `examples/data-migration-auditor.yaml` in full:

```yaml
swarm:
  name: data-migration-auditor
  root: ./data-migration-auditor-workspace

defaults:
  capture: none              # mock agents don't fire a turn-completion hook
  can_talk_to: []           # tightened per agent below

agents:
  - name: audit-lead
    type: claude
    can_talk_to: [reconciler, transform-checker, reporter, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the AUDIT LEAD for a completed DATA MIGRATION. A human sends you the
      source and target descriptions ... You run the whole fidelity audit and are
      the only agent that talks to the human. You do NOT do the comparison yourself
      -- you sequence it and synthesize the results. ...
      Run it: (1) acknowledge the source + target to the human briefly; (2) send
      the source/target specs and schema map to BOTH reconciler and
      transform-checker in parallel with clear scope; (3) wait for both; (4) send
      the consolidated findings to reporter and ask for the fidelity verdict; (5)
      forward the reporter's report to the user. ...

  - name: reconciler
    type: codex
    can_talk_to: [audit-lead]
    command: "codex --yolo"
    role: |
      You are the RECONCILER. ... measure whether the records made it across
      intact: (1) ROW COUNTS; (2) CHECKSUMS; (3) KEY OVERLAPS; (4) FIELD-LEVEL
      TRANSFORMS. Classify every finding as DROPPED / DUPLICATED / CORRUPTED /
      MISMATCHED and quantify it with counts and example keys. ...

  - name: transform-checker
    type: gemini
    can_talk_to: [audit-lead]
    command: "gemini --dangerously-skip-permissions"
    role: |
      You are the TRANSFORM CHECKER. ... verify the transformation rules were
      applied correctly on the target. Do NOT count rows or hunt for dropped data
      (that is reconciler's job) ... For each rule: sample source rows, compute the
      expected transformed value yourself, and compare against the target value;
      report PASS / FAIL / PARTIAL with concrete before→after examples. ...

  - name: reporter
    type: claude
    can_talk_to: [audit-lead, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the REPORT AUTHOR. ... write the FINAL DATA FIDELITY report for a
      human reader ... Structure it: Executive summary (overall verdict ...), a
      findings table (defect class, severity, count, example keys, owning
      subsystem), the transform-compliance summary, and a recommended remediation /
      re-run plan ... When the report is final, send it to the user. ...
```

Field by field:

### `swarm`
- **`name: data-migration-auditor`** — the swarm's name (shows up in `status`,
  logs, sessions).
- **`root: ./data-migration-auditor-workspace`** — the parent directory for the
  agents' working directories and mailboxes. Each agent's workdir defaults to
  `<root>/<name>` (so `data-migration-auditor-workspace/audit-lead`,
  `.../reconciler`, etc.). Orchestrator state goes under
  `data-migration-auditor-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.
- **`capture: none`** — see the turn-detection note below; this opt-out matters
  and is partly auto-corrected by the loader.

### `audit-lead` (type: `claude`)
- **`can_talk_to: [reconciler, transform-checker, reporter, user]`** — the lead is
  the hub and the **only agent that can both receive the human's brief and deliver
  the verdict**. It fans out the spec, waits for both specialists, and forwards the
  consolidated findings to the reporter.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the lead waits for your brief instead of proactively
  mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `reconciler` (type: `codex`)
- **`can_talk_to: [audit-lead]`** — the reconciler reports *only* to the lead. It
  cannot reach the transform-checker, the reporter, or you — this is the
  "independent comparison" guarantee. It measures record fidelity (counts,
  checksums, key overlaps, field remaps) and quantifies DROPPED / DUPLICATED /
  CORRUPTED / MISMATCHED with example keys.
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `transform-checker` (type: `gemini`)
- **`can_talk_to: [audit-lead]`** — reports *only* to the lead, never the
  reconciler. It judges transform *correctness* (units, encodings, timezone
  shifts, precision loss, schema mappings), not row counts. That separation of
  concerns is exactly why the two never talk directly.
- **`command: "gemini --dangerously-skip-permissions"`** — placeholder launch
  command. Note the model family is deliberately different from the reconciler's
  (`codex`) and the lead/reporter's (`claude`) — see
  [`multi-llm-swarm.md`](./multi-llm-swarm.md).
- **Turn detection:** `gemini` → **pane polling** (it has no completion hook). See
  the capture note below.

### `reporter` (type: `claude`)
- **`can_talk_to: [audit-lead, user]`** — the reporter owns the human-facing
  verdict. It can ask the lead for clarification (if the consolidated findings are
  missing or contradictory) and is the *other* agent allowed to reach `user`.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook.

### ACL enforcement (the part that makes the audit honest)

`can_talk_to` is the orchestrator's routing gate. When an agent writes a file into
`outbox/<name>/`, the orchestrator checks `<name>` against the sender's list
*before* releasing it. If it's not on the list, the message is bounced as a
`system` note ("you may only message: audit-lead") and filed under the sender's
`failed/`. So:
- If the **reconciler** tried to mail the transform-checker or you directly, it's
  bounced in-band (the model self-corrects from the system note).
- If the **transform-checker** tried to mail the reporter, same thing.
- Only the **audit-lead** and **reporter** can reach `user`.

This is **cooperative, not OS isolation** (Decision D15): agents have filesystem
access and *could* theoretically write into another inbox, but the mailroom is the
normal, enforced path. The `outbox/<peer>/about.md` contact card *is* the ACL made
visible — the reconciler only ever gets `outbox/audit-lead/`; the lead gets
`outbox/reconciler/`, `outbox/transform-checker/`, `outbox/reporter/`,
`outbox/user/`. See [`mail-model.md`](../mail-model.md) and
[`delegation-pipeline.md`](./delegation-pipeline.md).

### Per-type turn detection — and this config's `capture: none`

The orchestrator's clock runs on *turn completion*: an agent stops → its outbox is
swept → mail is routed → recipients are released and nudged. What counts as
"stopped" is per `type`:
- **`claude`** → a **Stop hook** (installed by `up`; fires when Claude finishes its
  turn).
- **`codex`** → a **`notify` program** (its hook), installed at `up`.
- **`gemini` / `hermes`** → **pane polling** (no completion hook exists, so the
  supervisor watches the pane).

This config sets **`defaults.capture: none`** (a convenience for the key-free mock
demo, where a bash loop just exits). The loader handles it as follows:
- For **`claude`/`codex`** agents (audit-lead, reconciler, reporter), `capture:
  none` is *auto-upgraded back to `hook`*, because silencing their only completion
  signal would blind the orchestrator and could wedge the swarm. `validate`/`up`
  prints a warning for each, e.g. *"agent 'audit-lead': capture: none on a claude
  agent gives the orchestrator no turn-completion signal — auto-upgraded to
  capture: hook."*
- For the **`gemini`** agent (transform-checker), `capture: none` is **left as
  `none`** — there is no hook to fall back on, so pane polling is genuinely
  disabled. That's fine for the mock bash-loop demo, but **for a real gemini run
  you should set `capture: pane` explicitly** on that agent (or drop the
  `defaults.capture: none`) so the orchestrator notices when it finishes. A
  silent-but-alive gemini agent is the one case the liveness supervisor can't
  otherwise catch — exactly the footgun called out in `ProjectPlan.md` §24.

In practice, for real CLIs you'll usually just remove `capture: none` from
`defaults` and let each type pick its natural mode. See
[`cli-reference.md`](../cli-reference.md) for the `validate`-time warnings and
[`configuration.md`](../configuration.md) for the `capture` field.

### What's *not* in this config
- **No `pings`.** The swarm is purely event-driven off your brief — it only moves
  when you send a spec. (If you want a "still waiting on the audit" nag, add a ping
  to `audit-lead`; see [`configuration.md`](../configuration.md).)
- **No shared `workdir`.** Unlike the pipeline builder, every agent gets its own
  private directory, so no mailbox namespacing is needed. (`audit-lead`,
  `reconciler`, `transform-checker`, `reporter` each own `<root>/<name>/`.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/data-migration-auditor.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the `capture: none` auto-upgrade
   warnings for the claude/codex agents (and notes the gemini agent keeps `none`).
2. Creates the runtime dirs (`data-migration-auditor-workspace/.agentainer/…`:
   log, queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders `inbox/
   outbox/ read/ sent/ failed/`, the per-agent queue, and an `outbox/<peer>/`
   folder **for each allowed recipient**. The `outbox/<peer>/about.md` contact
   card *is* the ACL made visible: the lead gets `outbox/reconciler/`,
   `outbox/transform-checker/`, `outbox/reporter/`, `outbox/user/`; the reconciler
   gets only `outbox/audit-lead/`; the reporter gets `outbox/audit-lead/` and
   `outbox/user/`; etc.
4. **Installs per-type turn detection** — the Claude Stop hook for `audit-lead` and
   `reporter`, and the Codex `notify` hook for `reconciler` (the gemini agent is
   left with `capture: none` unless you changed it).
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'data-migration-auditor' is up with 4 agent(s)
:: attach with:  tmux attach -t <audit-lead-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/data-migration-auditor.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents, so it must **never** be exposed on `0.0.0.0` without a token.
See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole audit route mail with no API keys — the mechanics are identical.

---

## 5. Drive the audit

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the reporter's verdict as mail (rather than
have it held), turn yourself available first:

```bash
./agentainer user available -c examples/data-migration-auditor.yaml
```

This rewrites the `user` contact card in the reporter's `outbox/user/about.md` to
`Status: available`, so the reporter sees you're reachable. (While away, mail to
you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the source→target brief into the swarm, addressed to the audit-lead. The
example's header suggests:

```bash
./agentainer send -c examples/data-migration-auditor.yaml --to audit-lead \
  "Source: postgres://src/orders (1.2M rows). Target: postgres://dst/orders. \
   Schema map attached in ./schema.yaml."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the lead, then — because the
inbox was empty — **released into `inbox/`** and the lead is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the audit advance one turn at a time. The arrows
are `stop → sweep → route → release → nudge` cycles:

1. **audit-lead receives the brief.** It acknowledges you, then writes the spec +
   schema map into `outbox/reconciler/` *and* `outbox/transform-checker/` in
   parallel (each with a clear, non-overlapping scope). On stop, both route.
2. **reconciler counts & checksums.** It measures record fidelity (DROPPED /
   DUPLICATED / CORRUPTED / MISMATCHED with example keys) and writes its report
   into `outbox/audit-lead/`. On stop, that routes to the lead.
3. **transform-checker verifies rules.** It produces per-rule PASS/FAIL/PARTIAL
   with before→after examples and writes into `outbox/audit-lead/`. On stop, that
   routes to the lead. *(With the shipped `capture: none`, this hop only advances
   once you give gemini a turn-completion signal — i.e. set `capture: pane` for a
   real run, per §3.)*
4. **audit-lead consolidates.** It reconciles any conflict between the two
   (e.g. reconciler finds dropped rows *and* transform-checker finds a
   unit-conversion mismatch) into one story, and forwards the merged findings into
   `outbox/reporter/`, asking for the verdict. On stop, that routes.
5. **reporter writes the verdict.** It produces the fidelity report (SAFE TO CUT
   OVER / CUT OVER WITH CAUTION / DO NOT CUT OVER, findings table, remediation
   plan) and writes it into `outbox/user/`. On stop, that's delivered to your
   `user` mailbox (visible with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a brief, the agents just sit in standby (that's the point of
> the standby prompt). The audit only moves when real mail arrives — this swarm has
> no periodic pings to self-start it.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/data-migration-auditor.yaml
```

```
swarm: data-migration-auditor   root: ./data-migration-auditor-workspace
  audit-lead          (claude) up idle queue=0 unread=1 talks=reconciler, transform-checker, reporter, user
  reconciler          (codex)   up idle queue=0 unread=0 talks=audit-lead
  transform-checker   (gemini)  up idle queue=0 unread=0 talks=audit-lead
  reporter            (claude)  up idle queue=0 unread=0 talks=audit-lead, user
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/data-migration-auditor.yaml          # whole swarm, last 20
./agentainer logs -c examples/data-migration-auditor.yaml -f        # follow live
./agentainer logs reconciler -c examples/data-migration-auditor.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox audit-lead -c examples/data-migration-auditor.yaml
```

Prints the one released message (headers + body), or `audit-lead: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue audit-lead -c examples/data-migration-auditor.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach transform-checker -c examples/data-migration-auditor.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or (more directly)
by sending notes into an agent's inbox.

- **Send a clarification to the lead.** Realized the schema map lives at a
  different path, or the "expected row count" was wrong? `./agentainer send --to
  audit-lead -c examples/data-migration-auditor.yaml "The true source count is
  1.18M, not 1.2M; re-brief the reconciler."` The lead relays the change down the
  chain — you don't touch the specialists directly (and the ACL wouldn't let you).
- **Ask the reporter for the evidence.** `./agentainer send --to audit-lead ...
  "Have the reporter attach the failing-row example keys from the reconciler's
  report."` — the lead forwards it.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/data-migration-auditor.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/data-migration-auditor.yaml     # resume is the default
```

On `up`, Agentainer reads `data-migration-auditor-workspace/.agentainer/
sessions.yaml` (written as each agent finished its first turn) and reattaches the
recorded conversations via each type's native resume: `claude --resume <id>` for
the lead and reporter, `codex resume <id>` for the reconciler, and the gemini
session for the transform-checker. A resumed agent is *not* re-sent the standby
prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/data-migration-auditor.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Turn on real gemini turn-detection
As noted in §3, the shipped config leaves the gemini agent with `capture: none`.
For a real gemini run, give it pane polling explicitly:

```yaml
  - name: transform-checker
    type: gemini
    capture: pane
    can_talk_to: [audit-lead]
    command: "gemini --dangerously-skip-permissions"
    role: |
      ...
```

(Or simply delete `capture: none` from `defaults` and let every type pick its
natural mode.)

### Add a `db-query` specialist
If you want the reconciler to actually *run* SQL rather than reason about counts,
add a thin specialist that owns the source/target connections and returns
aggregates — then route the lead's spec to it instead of the reconciler doing it
by hand:

```yaml
  - name: db-query
    type: codex
    can_talk_to: [audit-lead]
    command: "codex --yolo"
    role: |
      You are the DB QUERY agent. Given connection strings and a query, return
      aggregates (counts, checksums, key sets). You never judge correctness --
      you just fetch numbers for the reconciler.
```

Then add `db-query` to the audit-lead's `can_talk_to` and have the lead brief it
alongside the reconciler.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- Put the reconciler on `claude` (or `hermes`) instead of `codex`.
- Put the reporter on `codex` if you want verdict authoring on a different model
  than the lead.
- Remember: `gemini`/`hermes` need `capture: pane` (pane polling) since they have
  no completion hook.

### Tune the ACL
- To let the **reporter** escalate *findings* straight to you without a lead
  round-trip, it already can (`user` is in its list). To also let the reconciler
  or transform-checker reach the reporter directly, add `reporter` to their
  `can_talk_to` — but that breaks the "specialists never compare notes" rule, so
  don't, unless you've thought through the double-count risk.
- The hard guarantee — reconciler and transform-checker *only* talk to the lead —
  is exactly their `can_talk_to: [audit-lead]`. Keep it tight.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md)
  for mixing model families safely.

---

## 10. Tips & footguns

- **Keep the two specialists apart.** Only the audit-lead lists both
  `reconciler` and `transform-checker`; the specialists list only `audit-lead`. If
  either tried to mail the other (or you, or the reporter), the orchestrator
  bounces it (ACL) and drops a `system` note in its inbox explaining who it *can*
  message — the model self-corrects in-band. The separation exists so a dropped-row
  finding and a unit-conversion finding aren't conflated into one ambiguous
  "divergence."

- **The `capture: none` default is a demo convenience, not a recommendation.**
  The loader silently re-enables the Stop/`notify` hook for the claude/codex
  agents (with a warning), but the **gemini** agent genuinely gets no
  turn-completion signal. For real runs, set `capture: pane` on gemini or drop the
  default — otherwise the transform-checker's turn may not advance the swarm.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch (e.g. a `claude` agent whose `command`
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
  ./agentainer down           -c examples/data-migration-auditor.yaml
  ./agentainer remove-session -c examples/data-migration-auditor.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files in
  `data-migration-auditor-workspace/` or your config.

- **Availability shapes the ending.** If `user` is **away** when the reporter
  finishes, your verdict is *held* (with a `system` "the user is away" ack to the
  reporter) rather than lost — read it later with `agentainer user inbox` or flip
  yourself available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- `examples/data-migration-auditor.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14; invariants §24).

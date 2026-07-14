# Use case: Compliance mapper

A concrete, end-to-end walkthrough of the shipped
`examples/compliance-mapper.yaml` swarm — a four-agent compliance team that maps
a pile of controls/policies (or a plain-text system description) onto the major
frameworks: SOC 2, GDPR, HIPAA, and ISO 27001. A **mapper** hub receives your
input, delegates to a **framework-expert** (control-ID matching), a
**gap-finder** (missing/weak controls + remediation), and a **reporter** that
assembles the coverage matrix, the gap list, and the remediation plan — then the
mapper delivers the consolidated report back to you.

Everything below is based on the actual contents of
`examples/compliance-mapper.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom. No API keys are needed to understand the mechanics; to run it *for
real* you supply the coding-CLI commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Security/compliance leads, GRC analysts, and engineering managers who need a
repeatable, evidence-traceable way to go from "here are our controls / here's
our system" to a framework coverage matrix without doing every lookup
themselves. The swarm encodes the discipline that makes a compliance report
trustworthy — a single owner of the synthesis (the mapper), a control-catalog
specialist who only matches evidence to specific control IDs, an adversarial
gap-finder who only looks for what's missing, and a reporter who reconciles the
two and writes the deliverable.

It is deliberately a **hub-and-spoke**, not a free-for-all: every request and
every deliverable passes through the mapper, so the coverage matrix has exactly
one authority. The framework-expert, gap-finder, and reporter never talk to each
other — they each report only to the mapper, who sequences the work and
reconciles any contradictions between the two analyses.

---

## 2. The topology

```
          user
            |
          mapper                          (the hub: talks to framework, gapfinder, reporter, user)
           /    |    \
      framework gapfinder reporter
           \     |     /   (each reports back ONLY to mapper; never to each other, never to user)
            \    |    /
             mapper ──▶ user
```

Four agents, one directed flow:

1. **`user` → `mapper`** — you send either a control list / policy set or a system
   description and the framework(s) you care about.
2. **`mapper` → `framework`** — the mapper hands the input to the framework-expert
   and asks it to map the evidence to specific control IDs per framework.
3. **`mapper` → `gapfinder`** — in parallel, the mapper hands the *same* input to
   the gap-finder and asks which required controls are absent or thinly
   evidenced, with remediation.
4. **`mapper` → `reporter`** — when both return, the mapper forwards the
   control-ID mapping *and* the gap list to the reporter and asks for the
   consolidated coverage matrix + gaps + remediation plan.
5. **`reporter` → `mapper`** — the deliverable comes back to the hub.
6. **`mapper` → `user`** — the mapper reconciles, de-duplicates, and forwards the
   finished report to you.

The routing above is *enforced* by each agent's `can_talk_to` list. An agent can
only deliver to names on its own list; anything else is bounced back as a
`system` message and filed in `failed/` (see §7). Notably, `framework`,
`gapfinder`, and `reporter` **never** talk to `user` directly — only the mapper
does.

---

## 3. The config, explained

Here is `examples/compliance-mapper.yaml` in full (roles condensed for the map;
the file carries the full standing prompts):

```yaml
swarm:
  name: compliance-mapper
  root: ./compliance-mapper-workspace

defaults:
  capture: none              # real agents still fire their turn-completion signal
  can_talk_to: []            # tightened per agent below

agents:
  - name: mapper
    type: claude
    can_talk_to: [framework, gapfinder, reporter, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the COMPLIANCE MAPPER -- the orchestrating hub ... you delegate the
      analysis and synthesize, you do NOT do the control analysis yourself ...
      run: (1) acknowledge to user; (2) send input to framework to map evidence
      to control IDs; (3) in parallel send the same input to gapfinder for gaps +
      remediation; (4) on both returns, send both to reporter for the consolidated
      matrix; (5) forward the deliverable to user. ...

  - name: framework
    type: codex
    can_talk_to: [mapper]
    command: "codex --yolo"
    role: |
      You are the FRAMEWORK-EXPERT ... map the provided evidence to SPECIFIC
      control IDs (SOC 2 Trust Services Criteria, GDPR articles, HIPAA Security
      Rule implementation specs, ISO 27001 Annex A) ... mark ambiguous as
      "partial" ... do not invent control IDs ... return a structured mapping
      (control ID, title, evidence, status). You only ever talk to the mapper.

  - name: gapfinder
    type: gemini
    can_talk_to: [mapper]
    command: "gemini --yolo"
    role: |
      You are the GAP-FINDER ... identify which REQUIRED controls of the named
      framework(s) are MISSING or weakly evidenced; for each gap give the control
      ID, why it's thin, and a concrete prioritized remediation. Do not re-map
      satisfied controls. You only ever talk to the mapper.

  - name: reporter
    type: claude
    can_talk_to: [mapper]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the REPORTER ... produce ONE document: (1) COVERAGE MATRIX per
      framework (control ID, satisfied/partial/missing, evidence); (2) GAPS
      (missing-or-weak, prioritized); (3) REMEDIATION PLAN (ordered steps).
      Resolve contradictions between the two inputs explicitly. You only ever talk
      to the mapper.
```

Field by field:

### `swarm`
- **`name: compliance-mapper`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./compliance-mapper-workspace`** — the parent directory for the agents'
  working directories and mailboxes, resolved relative to the config file (so it
  lands under `examples/compliance-mapper-workspace/`). Each agent's workdir
  defaults to `<root>/<name>`. Orchestrator state goes under
  `<root>/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — a defensive floor. The loader's safety net **auto-upgrades**
  `claude` and `codex` agents to their natural hook-based capture (see the
  turn-detection note below), so setting `none` here doesn't leave them
  signal-less. `gemini` keeps pane polling. You normally don't need to set this —
  it's explicit in the example to show the loader's auto-upgrade in action.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `mapper` (type: `claude`)
- **`can_talk_to: [framework, gapfinder, reporter, user]`** — the mapper is the
  hub: it delegates to the three specialists and is the **only agent that can
  talk to `user`**. That last part matters — keep the human-facing surface to a
  single agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command, e.g. a
  shell alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity: acknowledge to the user, fan out to
  `framework` and `gapfinder` in parallel, collect, forward both to `reporter`,
  and deliver the result. On `up` this becomes the agent's first prompt, wrapped
  in a **standby notice** ("no task yet — don't send anything, you'll be
  notified"), so the mapper waits for your input instead of mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`;
  the `capture: none` default is auto-upgraded to `hook`).

### `framework` (type: `codex`)
- **`can_talk_to: [mapper]`** — the framework-expert only reports back to the
  mapper; it cannot reach the gap-finder, the reporter, or the `user`.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "match the provided evidence to specific control IDs per framework,
  mark ambiguities `partial`, never invent control IDs, return a structured
  mapping."
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`
  (the `capture: none` default is auto-upgraded to `hook`).

### `gapfinder` (type: `gemini`)
- **`can_talk_to: [mapper]`** — reports only to the mapper; never to the other
  agents or `user`.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **`role`** — "find which REQUIRED controls are missing or weakly evidenced, give
  the control ID, the reason, and a prioritized remediation; don't re-map
  satisfied controls."
- **Turn detection:** `gemini` → **pane polling** (`capture: none` is the correct
  pane-polling mode for gemini/hermes, so it is *not* auto-upgraded — the
  orchestrator polls the pane to learn when the turn finished).

### `reporter` (type: `claude`)
- **`can_talk_to: [mapper]`** — receives the two analyses from the mapper and
  returns the consolidated deliverable to the mapper only.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **`role`** — "produce one document: coverage matrix, gaps, remediation plan;
  resolve contradictions between the two inputs explicitly; skimmable tables and
  bullets."
- **Turn detection:** `claude` → Stop hook (auto-upgraded from `none`).

### Per-type turn detection (the system clock)
The whole stop→sweep→route→release→nudge cycle hangs off *turn completion*. The
loader picks the detection mode by `type`: `claude` installs a **Stop hook**,
`codex` installs a **notify** hook, and `gemini`/`hermes` fall back to **pane
polling**. Here the `defaults: capture: none` is deliberately overridden for the
three hook-capable agents by the auto-upgrade, while the `gemini` gapfinder keeps
pane polling. Get this right or completion never fires and an agent pins "busy"
forever (see Tips).

### What's *not* in this config
- **No `workdir` overrides.** Each agent gets a private `<root>/<name>` directory;
  there is no shared working directory here, so no mailbox namespacing is needed.
  (If you later want two agents to share a workspace, see
  [`custom-workspace.md`](./custom-workspace.md).)
- **No `pings`.** The swarm is purely event-driven off real mail — it only moves
  when you send an input. (Add a `pings:` entry to the mapper if you want a
  stale-engagement reminder.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 4. Run it

From the repo root (the example's own header suggests copying it first so you can
edit freely):

```bash
cp examples/compliance-mapper.yaml my-compliance.yaml
./agentainer up -c examples/compliance-mapper.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the `capture: none → hook` auto-upgrade
   warnings for the `claude`/`codex` agents (these are benign — the loader is
   restoring the natural turn signal).
2. Creates the runtime dirs (`compliance-mapper-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/
   about.md` contact card *is* the ACL made visible: the mapper gets
   `outbox/framework/`, `outbox/gapfinder/`, `outbox/reporter/`, `outbox/user/`;
   the three specialists each get just `outbox/mapper/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `mapper` and
   `reporter`, the Codex `notify` hook for `framework`, and (for `gapfinder`) the
   pane-poller that watches the gemini pane.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'compliance-mapper' is up with 4 agent(s)
:: attach with:  tmux attach -t <mapper-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/compliance-mapper.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents that may run with elevated permissions, so it must **never** be
exposed on `0.0.0.0` without a token. See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole mapping route mail with no API keys — the mechanics are identical.

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the mapper's finished report as mail (rather
than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/compliance-mapper.yaml
```

This rewrites the `user` contact card in the mapper's `outbox/user/about.md` to
`Status: available`, so the mapper sees you're reachable. (While away, mail to
you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send your controls / system description into the swarm, addressed to the
mapper:

```bash
./agentainer send --to mapper -c examples/compliance-mapper.yaml \
  "Map these controls to SOC 2 + GDPR: we run SSO (Okta) for all staff, encrypt \
   data at rest with AWS KMS, have a documented incident-response runbook, and \
   retain access logs for 12 months. Our system stores EU customer PII in a \
   Frankfurt Postgres."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the mapper, then — because the
inbox was empty — **released into `inbox/`** and the mapper is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the mapping advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **mapper receives the input.** It reads `inbox/`, acknowledges you, and writes
   two delegations in parallel: one into `outbox/framework/` (map evidence to
   control IDs), one into `outbox/gapfinder/` (find missing/weak controls). On
   stop, both route.
2. **framework matches control IDs.** It reads its inbox, returns a structured
   mapping (control ID, title, evidence, status) into `outbox/mapper/`. On stop,
   that routes to the mapper.
3. **gapfinder surfaces gaps.** It reads its inbox, returns the missing/weak
   controls + remediation into `outbox/mapper/`. On stop, that routes to the
   mapper.
4. **mapper briefs the reporter.** It writes *both* the control-ID mapping and the
   gap list into `outbox/reporter/` and asks for the consolidated matrix. On stop,
   that routes.
5. **reporter delivers.** It reads its inbox, writes the coverage matrix + gaps +
   remediation plan into `outbox/mapper/`. On stop, that routes back.
6. **mapper finalizes.** It reconciles the two analyses (de-dup, resolve
   contradictions, ensure no control was double-analyzed) and writes the finished
   report into `outbox/user/`. On stop, that's delivered to your `user` mailbox
   (visible with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send input, the agents just sit in standby (that's the point of
> the standby prompt). The swarm only moves when real mail arrives — this swarm
> has no periodic pings to self-start it.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/compliance-mapper.yaml
```

```
swarm: compliance-mapper   root: ./compliance-mapper-workspace
  mapper    (claude) up idle queue=0 unread=0 talks=framework, gapfinder, reporter, user
  framework (codex)  up idle queue=0 unread=1 talks=mapper
  gapfinder (gemini) up idle queue=0 unread=0 talks=mapper
  reporter  (claude) up idle queue=0 unread=0 talks=mapper
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/compliance-mapper.yaml          # whole swarm, last 20
./agentainer logs -c examples/compliance-mapper.yaml -f        # follow live
./agentainer logs framework -c examples/compliance-mapper.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox mapper -c examples/compliance-mapper.yaml
```

Prints the one released message (headers + body), or `mapper: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue mapper -c examples/compliance-mapper.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach framework -c examples/compliance-mapper.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom
— handy for un-sticking an agent, but the mail model is the normal path.)

**The workspaces** — each agent's analysis files (intermediate notes, the
reporter's final matrix) live in `compliance-mapper-workspace/<name>/`. Inspect
them there; the mailboxes are just the coordination layer.

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or (more directly)
by sending notes into an agent's inbox.

- **Send a clarification to the mapper.** Realized you only care about HIPAA, not
  ISO? `./agentainer send --to mapper -c examples/compliance-mapper.yaml "Drop
  ISO 27001 — re-brief framework and gapfinder for HIPAA only."` The mapper
  relays the change.
- **Ask for the evidence trail.** `./agentainer send --to mapper ... "Have the
  reporter footnote each matrix row with the exact control ID + where the
  evidence came from."` — the mapper forwards it.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/compliance-mapper.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/compliance-mapper.yaml     # resume is the default
```

On `up`, Agentainer reads `compliance-mapper-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the
mapper and reporter, `codex resume <id>` for the framework, and `gemini`'s
resume for the gapfinder. A resumed agent is *not* re-sent the standby prompt
(its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/compliance-mapper.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Mix the model families differently
This example already mixes three: `claude` (mapper, reporter), `codex`
(framework), `gemini` (gapfinder). You can rebalance — e.g. put the gap-finder on
`claude` and the reporter on `codex`. The `type` selects both the CLI family and
the turn-detection mode; `command` must launch that same family (a `type`/
`command` mismatch wedges the agent — see [`cli-reference.md`](../cli-reference.md)).
Remember: `gemini`/`hermes` need pane polling (their natural `capture` mode),
while `claude`/`codex` use hooks. For the safe way to combine families, see
[`multi-llm-swarm.md`](./multi-llm-swarm.md).

### Add a peer-review / auditor agent
If you want the report independently checked before it reaches you, add a fifth
agent that can read the reporter's output and owns a sanity pass:

```yaml
  - name: auditor
    type: claude
    can_talk_to: [mapper, reporter]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the COMPLIANCE AUDITOR. The mapper sends you the reporter's
      consolidated deliverable; verify every control ID is real, every gap is
      backed by a requirement, and the remediation plan is ordered. Report
      findings back to outbox/mapper/. You never analyze raw evidence yourself.
```
Then add `auditor` to the mapper's `can_talk_to` so it can be briefed.

### Tune the ACL
- To let the `reporter` deliver straight to `user` (not only via the mapper), add
  `user` to its `can_talk_to`. Mind that this widens the human-facing surface;
  the doc's convention keeps the mapper the sole `user` contact.
- To keep the framework-expert / gap-finder strictly one-way (already the case
  here), leave their `can_talk_to: [mapper]` — that's the "no agent analyzes the
  same control twice, and nobody talks behind the mapper's back" guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing, and [`configuration.md`](../configuration.md)
  for the full schema reference.

---

## 10. Tips & footguns

- **Keep the mapper the only `user`-facing agent.** Only the mapper lists `user`
  in `can_talk_to`. That gives you a single funnel: raw control matches and gap
  lists always pass through synthesis and conflict-reconciliation before they
  reach you. If the framework, gapfinder, or reporter tries to mail `user`
  directly, the orchestrator bounces it (ACL) and drops a `system` note in their
  inbox explaining who they *can* message — the model self-corrects in-band.

- **The framework/gapfinder/reporter never talk to each other by design.** The
  mapper is the only crossroads, which is what guarantees no control gets analyzed
  twice and that the two analyses are reconciled in one place. Don't "help" by
  adding `framework: can_talk_to: [gapfinder]` — you'd break the de-dup guarantee
  in the mapper's role.

- **`capture: none` in defaults is fine here because of the auto-upgrade.** The
  loader restores hook-based capture (`claude` Stop hook, `codex` notify hook)
  for the three hook-capable agents, so turn completion still fires. The `gemini`
  gapfinder keeps pane polling, which is its correct mode. If you swap an agent to
  a `command` that doesn't match its `type`, completion never triggers and the
  agent pins "busy" forever — `status` showing an agent `busy` for a long time
  with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/compliance-mapper.yaml
  ./agentainer remove-session -c examples/compliance-mapper.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source/analysis files in
  `compliance-mapper-workspace/<name>/` or your config.

- **Availability shapes the ending.** If `user` is **away** when the mapper
  finishes, your coverage report is *held* (with a `system` "the user is away" ack
  to the mapper) rather than lost — read it later with `agentainer user inbox` or
  flip yourself available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions (`--dangerously-skip-permissions` /
  `--yolo`).

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families (claude/codex/gemini) safely.
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- [`cli-reference.md`](../cli-reference.md) — every subcommand, including `user` and `serve`.
- `examples/compliance-mapper.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

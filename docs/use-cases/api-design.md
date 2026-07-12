# Use case: the API design & build swarm

A concrete, end-to-end walkthrough of the shipped `examples/api-design.yaml` swarm —
a five-agent pipeline that turns an API *goal* from a human into a *built, documented*
HTTP API. A **lead** hub sequences four specialists: a **spec** author lists the
resources and endpoints, a **designer** defines the request/response contracts, an
**implementer** builds the handlers in a shared repo, and **docs** writes the OpenAPI
document plus curl examples. The lead reviews the result and delivers a summary back
to the human. The whole thing is wired through Agentainer's file-based mail model.

Everything below is based on the actual contents of `examples/api-design.yaml` and
the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are needed
to understand the mechanics; to run it *for real* you supply the coding-CLI commands
(or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) and
> [`mail-model.md`](../mail-model.md) first. The one-line version: an agent **reads
> a file** to receive mail and **writes a file** to send it; the orchestrator owns
> all routing, ACL, IDs, and state. For the shared-repo part of this swarm, see
> [`custom-workspace.md`](./custom-workspace.md).

---

## 0. Who this is for

- **Backend engineers** who want a repeatable way to go from "we need an API for X"
  to a working service without hand-rolling the spec/contract/impl/docs handoffs
  every time, and who want each stage to be a separate, inspectable conversation.
- **Tech leads / API product owners** who want to set the goal and the acceptance
  bar once, then watch four specialists produce spec → contracts → handlers → docs
  in order, with a single point of contact (`lead`) and a single review gate
  (the lead's delivery to `user`).
- **Platform / developer-experience teams** who care about the *documentation*
  being produced from the *real* handlers (docs reads the implementer's code in the
  shared repo, not a wish-list), so the OpenAPI and examples stay honest.

If you only need a single researcher loop, see [`use-cases/research-swarm.md`](./research-swarm.md);
if you want agents of *different* model families in one swarm, see
[`use-cases/multi-llm-swarm.md`](./multi-llm-swarm.md).

---

## 1. The topology

```
            API goal (a product need, not a spec)
  user ─────────────────────────────────────────────▶ lead
        (final summary)   ◀──────────┐               │ sequences the 4 specialists
                                    │               │
                            spec ◀──┴── designer ◀── implementer ◀── docs
                         endpoint   contracts     handlers      OpenAPI +
                         list                      (api-repo)    curl examples
                          │          │             │             │
                          └──────────┴── ask 1 hop back ────────┘   (question a
                                            (only to the prior stage / lead)   contract)
```

Five agents, one forward build flow with a controlled back-channel:

1. **`user` → `lead`** — you send the API goal (a product need, constraints, scope).
2. **`lead` → `spec`** — the lead restates the goal and asks for the resource model
   and endpoint list.
3. **`spec` → `designer`** (copied to `lead`) — the endpoint list becomes the contract
   work.
4. **`designer` → `implementer`** (copied to `lead`) — the contracts become the build
   brief.
5. **`implementer` → `docs`** (copied to `lead`) — the handlers, in the shared
   `api-repo`, become the thing to document.
6. **`docs` → `lead`** — the OpenAPI doc + examples are written; docs reports a summary.
7. **`lead` → `user`** — the lead reviews against the acceptance list and delivers the
   final summary to you.

The flow above isn't a suggestion — it's *enforced* by each agent's `can_talk_to`
list. An agent can only deliver to names on its own list; anything else is bounced
back as a `system` message and filed in `failed/` (see §7). Note the **back-channel
discipline**: the next stage may only ask the *one* stage before it (and the lead),
so contracts get questioned by implementer, never by docs directly to spec, and the
human is reachable only through `lead`.

---

## 2. The config, explained

Here is `examples/api-design.yaml` in full (the `command:` lines are placeholders —
swap in your own launch strings):

```yaml
# 🔌 API design & build swarm — lead hub, spec -> designer -> implementer -> docs.
swarm:
  name: api-design
  root: ./api-design-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: lead
    type: claude
    can_talk_to: [spec, designer, implementer, docs, user]
    command: "claude --dangerously-skip-permissions"
    role: "You are the API LEAD and the only human-facing agent. Sequence the four specialists and deliver the final summary to the user."
  - name: spec
    type: claude
    can_talk_to: [lead, designer]
    command: "claude --dangerously-skip-permissions"
    role: "You are the API SPEC author. List the resources, fields and endpoints (METHOD + path + one line). Write ENDPOINTS.md, then send to designer and copy lead."
  - name: designer
    type: claude
    can_talk_to: [lead, spec, implementer]
    command: "claude --dangerously-skip-permissions"
    role: "You are the API CONTRACT DESIGNER. Define request/response bodies, status codes and the error envelope for each endpoint. Write CONTRACTS.md, then send to implementer and copy lead."
  - name: implementer
    type: codex
    can_talk_to: [lead, designer, docs]
    command: "codex --yolo"
    workdir: "{root}/api-repo"
    role: "You are the API IMPLEMENTER. Build the handlers from CONTRACTS.md in the shared api-repo. Summarize to docs (and copy lead) when a unit runs."
  - name: docs
    type: claude
    can_talk_to: [lead, implementer]
    command: "claude --dangerously-skip-permissions"
    workdir: "{root}/api-repo"
    role: "You are the API TECHNICAL WRITER. From CONTRACTS.md and the handlers in api-repo, write openapi.yaml + curl examples. Report the summary to lead."
```

Field by field:

### `swarm`
- **`name: api-design`** — the swarm's name (shows up in `status`, logs, sessions).
- **`root: ./api-design-workspace`** — the parent directory for the agents' working
  directories and mailboxes. Each agent gets a workdir under it (created on `up`),
  and orchestrator state goes under `api-design-workspace/.agentainer/` (never commit
  it). `spec`, `designer`, and `lead` get the default `root/<name>` workdir;
  `implementer` and `docs` override to the **shared** `root/api-repo` (see §2b).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture` is
  how Agentainer knows a turn finished, and it's ultimately keyed off each agent's
  `type`. For `claude` and `codex`, whose CLIs support a completion **hook**, setting
  `capture: none` is a footgun — so the config loader *upgrades* it back to `hook`
  and prints a warning at `up`. Net effect here: `lead`, `spec`, `designer`, `docs`
  (claude) and `implementer` (codex) all use their hook.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `lead` (type: `claude`)
- **`can_talk_to: [spec, designer, implementer, docs, user]`** — the lead is the hub
  and the **only agent that can talk to `user`**. That keeps the human-facing surface
  to a single agent and funnels the final delivery through one review gate.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch command
  (substitute your own, e.g. a shell alias; treat command strings as sensitive — they
  may embed keys).
- **`role`** — the standing identity; on `up` it becomes the agent's first prompt,
  wrapped in a **standby notice** ("no task yet — don't send anything, you'll be
  notified"), so the lead waits for your goal.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `spec` (type: `claude`)
- **`can_talk_to: [lead, designer]`** — can go forward to `designer` and report back
  to `lead`, but cannot reach `implementer`/`docs`/`user` directly. It owns only the
  resource model + endpoint list (no bodies, no status codes).
- **`role`** — "list the resources, fields and endpoints; write `ENDPOINTS.md`."

### `designer` (type: `claude`)
- **`can_talk_to: [lead, spec, implementer]`** — forward to `implementer`, back to
  `spec` (to question a missing/ambiguous endpoint), and to `lead`. It deliberately
  cannot reach `docs`, so the contract is settled *before* documentation starts.
- **`role`** — "define request/response bodies, status codes, error envelope; write
  `CONTRACTS.md`."

### `implementer` (type: `codex`)
- **`can_talk_to: [lead, designer, docs]`** — builds from the contract, reports to
  `docs` (so docs can read the real handlers), and may ask `designer` a contract
  question; reports to `lead`.
- **`workdir: "{root}/api-repo"`** — shares the repo workdir with `docs` (see §2b).
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → a `notify` hook (installed at `up`).

### `docs` (type: `claude`)
- **`can_talk_to: [lead, implementer]`** — reads the handlers in the shared repo and
  reports to `lead`; may ask `implementer` for clarification (e.g. a handler that
  diverged from the contract). It **cannot** reach `spec`/`designer`/`user`.
- **`workdir: "{root}/api-repo"`** — the same shared repo as `implementer` (see §2b).
- **`role`** — "write `openapi.yaml` + curl examples from `CONTRACTS.md` and the real
  handlers; report the summary to `lead`."

### §2b. The shared workdir (implementer + docs)

`implementer` and `docs` both set `workdir: "{root}/api-repo"`, so they run inside
the **same** directory (your service repo, or a scratch checkout created at `up`).
This is the key design choice of the swarm: docs documents the **real** handlers,
not the contract on paper.

Two things the orchestrator handles so you don't have to:

1. **Mailbox namespacing.** Because two agents share a workdir, the config loader
   (`lib/config.py`, `SwarmConfig.mail_paths`) prefixes each mailbox folder with the
   agent's name — `implementer-inbox/`, `docs-inbox/`, `implementer-outbox/`,
   `docs-outbox/`, etc. — so their `inbox/`, `outbox/`, `read/`, `sent/`, `failed/`
   folders never collide. The model never sees this; every nudge/first-prompt is
   handed the exact computed path.
2. **File ownership.** The shared repo is for *code* and *docs*; the mailbox folders
   (namespaced) are for *mail*. The two agents must not clobber each other's files:
   `implementer` writes source, `docs` writes `openapi.yaml` + README/docs. The roles
   say so explicitly. (There is also a `up` warning about the shared workdir, because
   a shared git checkout interleaves the two agents' commits — fine for a scratch repo,
   something to know about for a real one.)

To point both at your real service repo, set each `workdir` to the same absolute path
(or a path that resolves to the same directory). For the full story, see
[`custom-workspace.md`](./custom-workspace.md).

### What's *not* in this config
- **No `periodically_ping_seconds`.** The pipeline is purely event-driven off real
  mail — `lead` moves only when you send a goal, and each stage advances when the
  prior one's mail arrives. No agent self-starts on a timer.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **No `broadcast`.** Removed in v2; the lead sequences the work explicitly.

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/api-design.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the `capture: none →
   hook` upgrade for the claude/codex agents, and the shared-workdir note for
   `implementer`/`docs`).
2. Creates the runtime dirs (`api-design-workspace/.agentainer/…`: log, queue, run,
   sessions) and the two workdirs (`api-design-workspace/{lead,spec,designer}` and the
   shared `api-design-workspace/api-repo`).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. For `implementer` and `docs`
   those folders are *namespaced* (`implementer-inbox/`, `docs-outbox/`, …) because
   they share a workdir. Each `outbox/<peer>/about.md` contact card *is* the ACL made
   visible.
4. **Installs per-type turn detection** — the Claude Stop hook for `lead`, `spec`,
   `designer`, `docs`; the Codex `notify` hook for `implementer`.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'api-design' is up with 5 agent(s)
:: attach with:  tmux attach -t <lead-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/api-design.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). For safety the UI **binds `127.0.0.1` by default**
— only opt into a remote bind (`--host 0.0.0.0`) with a `--token`, and never expose it
without one. See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch the
> whole pipeline route mail with no API keys — the mechanics are identical.

---

## 4. Drive a goal

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the lead's final summary as mail (rather than have
it held), turn yourself available first:

```bash
./agentainer user available -c examples/api-design.yaml
```

This rewrites the `user` contact card in the lead's `outbox/user/about.md` to
`Status: available`, so the lead sees you're reachable. (While away, mail to you is
*held* and the sender gets a `system` ack — nothing bounces.)

Now send the API goal into the swarm, addressed to the lead:

```bash
./agentainer send --to lead "Design a REST API for a URL shortener with links, per-link hit counts, and users. Use token auth, version under /v1, and paginate list endpoints."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the lead, then — because the inbox
was empty — **released into `inbox/`** and the lead is **nudged** (the protocol is
re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§5), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **lead receives the goal.** It restates the scope and sends the brief to `spec`. On
   stop, that routes to `spec` and `lead` is nudged back into standby.
2. **spec lists resources/endpoints.** It writes `ENDPOINTS.md`, mails `designer`
   (copies `lead`). On stop, that routes to `designer`.
3. **designer defines contracts.** It writes `CONTRACTS.md`, mails `implementer`
   (copies `lead`). On stop, that routes to `implementer`.
4. **implementer builds handlers.** In the shared `api-repo`, it implements the
   routes and mails `docs` a summary (copies `lead`). On stop, that routes to `docs`.
5. **docs writes the OpenAPI + examples.** From `CONTRACTS.md` and the real handlers
   in `api-repo`, it writes `openapi.yaml` + curl examples and reports the summary to
   `lead`. On stop, that routes to `lead`.
6. **lead delivers to you.** It reviews against the acceptance list and writes the
   final summary into `outbox/user/`. On stop, that's delivered to your `user`
   mailbox (see it with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox message
at a time and fires the next hop off each agent's turn completion. If a stage has a
*question* for the prior stage (e.g. implementer finds an impossible contract), it mails
that one stage + the lead; the answer flows forward again.

> If you *don't* send a goal, the agents just sit in standby (that's the point of the
> standby prompt). The pipeline only moves when real mail arrives.

---

## 5. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/api-design.yaml
```

```
swarm: api-design   root: ./api-design-workspace
  lead (claude)     up idle queue=0 unread=0 talks=spec, designer, implementer, docs, user
  spec (claude)     up idle queue=0 unread=1 talks=lead, designer
  designer (claude) up idle queue=0 unread=0 talks=lead, spec, implementer
  implementer (codex) up busy queue=0 unread=1 talks=lead, designer, docs
  docs (claude)     up idle queue=0 unread=0 talks=lead, implementer
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/api-design.yaml            # whole swarm, last 20
./agentainer logs -c examples/api-design.yaml -f          # follow live
./agentainer logs implementer -c examples/api-design.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`, etc. —
one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox spec -c examples/api-design.yaml
```

Prints the one released message (headers + body), or `spec: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue spec -c examples/api-design.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session,
e.g. to watch the implementer write handlers:

```bash
./agentainer attach implementer -c examples/api-design.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.) You can also
peek at the shared repo:

```bash
ls api-design-workspace/api-repo        # handlers + openapi.yaml side by side
```

---

## 6. Resume after a stop

Tear the swarm down when you're done:

```bash
./agentainer down -c examples/api-design.yaml
```

Bring it back later and **conversations resume by default**:

```bash
./agentainer up -c examples/api-design.yaml     # resume is the default
```

On `up`, Agentainer reads `api-design-workspace/.agentainer/sessions.yaml` (written as
each agent finished its first turn) and reattaches the recorded conversations via each
type's native resume: `claude --resume <id>` for the claude agents, `codex resume
<id>` for `implementer`. A resumed agent is *not* re-sent the standby prompt (its
prior context is restored) — so the lead remembers the goal it was mid-flight on.

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/api-design.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 7. Iterate and customize

### Drive an iteration
Mid-build, say you want to change pagination. You don't need to restart — send the
change to the lead and it will re-route it to the right stage:

```bash
./agentainer send --to lead "Switch pagination to cursor-based instead of page numbers; tell designer to update the contract and have docs reflect it."
```

The lead re-briefs `designer`; `designer` updates `CONTRACTS.md` and re-briefs
`implementer`; `implementer` updates the handlers and re-briefs `docs`. The backward
ACL means the change propagates forward only, never forks into parallel edits.

### Add a `security` reviewer
Want an auth/security pass before the lead delivers? Insert a reviewer between
`designer` and `implementer` (or between `docs` and `lead`), and widen the ACLs:

```yaml
  - name: security
    type: claude
    can_talk_to: [lead, designer, implementer]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the SECURITY REVIEWER. Read CONTRACTS.md and the handlers. Check auth
      coverage, input validation, injection surface, and error-message leakage. Send
      findings to implementer (with lead copied). You may message the agents in your
      can_talk_to.
```

Then adjust: `lead.can_talk_to` gains `security`; `designer.can_talk_to` gains
`security`; `implementer.can_talk_to` gains `security` (and you may drop `docs` from
`implementer` if you want security to gate the handoff to docs). A new agent in
`can_talk_to` automatically gets an `outbox/<name>/` folder at `up`.

### Swap models
Every `type` is independent. To run the whole pipeline on one family, change `type` and
its matching `command` (`type` must name the CLI the `command` launches, or `up` errors
with a mismatch — see footguns). E.g. make `designer` and `docs` `gemini`:

```yaml
  - name: designer
    type: gemini
    capture: pane          # gemini has no completion hook; pane polling detects turns
    can_talk_to: [lead, spec, implementer]
    command: "gemini --yolo"
    role: "..."
```

For a swarm that mixes families across *all* agents, see
[`use-cases/multi-llm-swarm.md`](./multi-llm-swarm.md). For a pure delegation loop
(one hub fanning out to workers) see
[`use-cases/delegation-pipeline.md`](./delegation-pipeline.md).

### Tune the ACL
The graph is intentionally narrow: each stage forwards to exactly one next stage and
may only ask the one before it. To loosen it (e.g. let `docs` read `designer`'s
`CONTRACTS.md` directly rather than via `implementer`), add `designer` to
`docs.can_talk_to`. To tighten it, remove a name. The `outbox/<peer>/` folders are
recomputed from `can_talk_to` at `up`, so the on-disk contact cards always match the
ACL. Remember: `user` and `system` are reserved — only `lead` may list `user`, and no
agent may ever list `system`.

---

## 8. Tips & footguns

- **Keep `lead` the only `user`-facing agent.** Only `lead` lists `user` in
  `can_talk_to`. That gives you a single point of contact and a clean funnel: the
  final delivery passes through one review gate. If `docs` tried to mail `user`
  directly, the orchestrator bounces it (ACL) and drops a `system` note explaining who
  it *can* message — the model self-corrects in-band.

- **The shared repo is for code/docs, not mail.** `implementer` and `docs` share
  `api-repo`, and their *mailboxes* are namespaced automatically. The two must still
  respect file ownership (source vs. `openapi.yaml`/README) — the roles spell it out.
  On a *real* shared git checkout, expect their commits to interleave; that's a
  known `up` warning, not a bug.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an agent
  stops, its outbox is swept, mail is routed, recipients are released and nudged. If
  an agent seems stuck, check that its **turn detection actually fires** — a
  `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
  Claude) means completion never triggers and the agent pins "busy" forever. `status`
  showing an agent `busy` for a long time with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so the
  queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to kill
  "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime + mailboxes) and
  start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/api-design.yaml
  ./agentainer remove-session -c examples/api-design.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down` first.
  It never touches the agents' source files, the shared `api-repo` code, or your config.

- **Availability shapes the ending.** If `user` is **away** when the lead finishes, your
  final summary is *held* (with a `system` "the user is away" ack to the lead) rather
  than lost — read it later with `agentainer user inbox`, or flip yourself available and
  it's delivered.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing/ACL work.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resume after `down`.
- [`use-cases/delegation-pipeline.md`](./delegation-pipeline.md) — one hub, many workers.
- [`use-cases/multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families.
- [`use-cases/custom-workspace.md`](./custom-workspace.md) — shared workdirs & mailbox namespacing.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

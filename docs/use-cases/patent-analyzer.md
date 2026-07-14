# Use case: Patent analyzer

A concrete, end-to-end walkthrough of the shipped
`examples/patent-analyzer.yaml` swarm — a four-agent patent-landscape /
prior-art search that turns an invention description or a competitor patent a
human pastes in into a first-pass landscape brief. A **lead** fans the
invention out to a **searcher** (prior art), a **landscape-mapper** (who owns
what / infringement risk), and a **reporter** that merges their findings into
one decision-ready brief. The lead delivers the consolidated brief back to you.

Everything below is based on the actual contents of
`examples/patent-analyzer.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Inventors, founders, and IP / strategy people who want a structured first read
on a patent question before paying a registered attorney. The swarm encodes a
safe division of labor: one lead owns the human interface and the merged brief,
a searcher owns prior-art novelty, a landscape-mapper owns competitive
ownership and infringement exposure, and a reporter owns synthesis — so the
technical and competitive findings never get conflated and you get one brief
instead of three disconnected answers.

It is deliberately a **hub-and-spoke**: every submission and every deliverable
passes through the lead, so the consolidated brief has exactly one author and
the human-facing surface is a single agent. The three specialists never talk to
each other — if they did, they'd re-derive the same patent set and step on each
other's findings. This is decision-support, **not legal advice** (the swarm is
told this in every brief it writes).

---

## 2. The topology

```
          searcher ─────┐
   landscape-mapper ──┼──▶ patent-lead ──▶ user
        reporter ─────┘          ▲            (lead fans the invention to all
                                 │             three, then returns the merged
                                 └── all three report findings back to lead
```

Four agents, one directed flow:

1. **`user` → `patent-lead`** — you send the invention text (or a file path) plus
   context: who you are (applicant / competitor-watcher), the technical field,
   and what you want to know (novelty / freedom-to-operate / white-space /
   competitive surveillance).
2. **`patent-lead` → `searcher` + `patent-lead` → `landscape-mapper`** — the lead
   restates the field + what's wanted and broadcasts the same invention text to
   both in parallel.
3. **`searcher` → `patent-lead`** and **`landscape-mapper` → `patent-lead`** —
   each returns its findings (prior-art table / ownership map) only to the lead.
4. **`patent-lead` → `reporter`** — once both technical inputs are in, the lead
   forwards their **merged** findings (with the original invention text) to the
   reporter and asks for the brief.
5. **`reporter` → `patent-lead`** — the reporter returns ONE consolidated brief.
6. **`patent-lead` → `user`** — the lead reviews it for internal consistency and
   forwards the brief to you, ending with the standing "not legal advice" note.

The routing above is *enforced* by each agent's `can_talk_to` list. The three
specialists list **only `patent-lead`**, so they physically cannot mail each
other or the `user`; anything else is bounced back as a `system` message and
filed in `failed/` (see §7). Notably, `searcher`, `landscape-mapper`, and
`reporter` **never** talk to `user` directly — only the lead does.

---

## 3. The config, explained

Here is `examples/patent-analyzer.yaml` in full:

```yaml
swarm:
  name: patent-analyzer
  root: ./patent-analyzer-workspace

defaults:
  capture: none              # claude/codex/gemini are auto-upgraded to their hook at up
  can_talk_to: []           # tightened per agent below

agents:
  - name: patent-lead
    type: claude
    can_talk_to: [searcher, landscape-mapper, reporter, user]
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: "Check your inbox/ for any new invention submissions or competitor patents waiting to be analyzed."
        cron: "*/30 * * * *"
        when_busy: skip
    role: |
      You are the PATENT ANALYSIS LEAD. ... (fan the invention out; merge the
      searcher + landscape-mapper findings; forward the brief to user) ...

  - name: searcher
    type: codex
    can_talk_to: [patent-lead]
    command: "codex --yolo"
    role: |
      You are the PATENT SEARCHER. ... (find prior art, extract novelty vs. prior
      art as a structured prior-art table) ...

  - name: landscape-mapper
    type: gemini
    can_talk_to: [patent-lead]
    command: "gemini --yolo"
    role: |
      You are the PATENT LANDSCAPE MAPPER. ... (who owns what, white-space,
      infringement / FTO exposure) ...

  - name: reporter
    type: claude
    can_talk_to: [patent-lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the PATENT LANDSCAPE REPORTER. ... (synthesize ONE brief from the
      merged searcher + landscape-mapper findings; end with the legal-disclaimer
      note) ...
```

Field by field:

### `swarm`
- **`name: patent-analyzer`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./patent-analyzer-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent's workdir defaults to
  `patent-analyzer-workspace/<name>` (all four are private, unprefixed).
  Orchestrator state goes under `patent-analyzer-workspace/.agentainer/` (never
  commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default capture mode is "none", but the loader
  **auto-upgrades each agent to its type's natural hook at `up`** (see the
  per-type turn-detection notes below). You don't set `capture:` per agent for
  the three shipped CLIs — the comment documents that this is intentional.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent
  below states its own list explicitly, so this default is just a safe floor.

### `patent-lead` (type: `claude`)
- **`can_talk_to: [searcher, landscape-mapper, reporter, user]`** — the lead is
  the hub: it fans the invention to the three specialists and is the **only
  agent that can talk to `user`**. Keeping the human-facing surface to a single
  agent is the convention that guarantees one consolidated brief (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command, e.g. a
  shell alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity: acknowledge the submission, restate the
  field + what's wanted, broadcast the invention to `searcher` and
  `landscape-mapper` in parallel, then once both report back forward their merged
  technical findings to `reporter`, and when the reporter returns the brief,
  review it and forward ONE consolidated landscape brief to `user`. On `up` this
  becomes the agent's first prompt, wrapped in a **standby notice** ("no task
  yet — don't send anything, you'll be notified"), so the lead waits for your
  submission instead of proactively mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at
  `up`).
- **`pings`** — the lead has a single periodic ping (see the pings note below).

### `searcher` (type: `codex`)
- **`can_talk_to: [patent-lead]`** — the searcher only reports back to the lead.
  It deliberately cannot reach `landscape-mapper`, `reporter`, or `user`; prior
  art stays separate from competitive ownership.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "find the closest prior-art references and, for each, the
  reference (number / title / assignee / jurisdiction + a source note), what it
  teaches, the specific element(s) it does or does NOT anticipate, and a plain-
  English novelty verdict; flag elements with no prior art as white-space." Writes
  findings to `outbox/patent-lead/` as a structured prior-art table.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `landscape-mapper` (type: `gemini`)
- **`can_talk_to: [patent-lead]`** — reports only to the lead. It never touches
  the searcher's novelty work or the reporter's brief; competitive ownership is
  its sole lane.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **`role`** — "map the competitive patent space: major assignees and clusters,
  crowded vs. open (white-space), claims overlapping our invention (infringement
  / FTO exposure), litigated/asserted patents nearby, with a per-risk severity
  read (blocking / watch / low)." Writes its landscape map to
  `outbox/patent-lead/`.
- **Turn detection:** `gemini` → **pane polling** (`capture: pane`, auto-applied
  at `up` since gemini has no completion hook — unlike claude/codex, the
  supervisor infers "stopped" from the pane going quiet).

### `reporter` (type: `claude`)
- **`can_talk_to: [patent-lead]`** — the reporter only ever talks to the lead. It
  does no new searching; it synthesizes the merged findings the lead hands it.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **`role`** — "produce ONE landscape brief: executive summary (open vs. crowded,
  headline risk), Prior-Art section (reference → teaches → our differentiator →
  novelty strength), Competitive-Ownership section (assignees, clusters, white-
  space), Risks section (top infringement / FTO exposures ranked by severity),
  Next-Steps list. Reconcile any disagreement between searcher and mapper and
  flag it. End with the standing 'not legal advice' note." Writes the brief to
  `outbox/patent-lead/`.
- **Turn detection:** `claude` → Stop hook.

### ACL enforcement — how "can only talk to the lead" is real

The `can_talk_to` list is the orchestrator's routing ACL, not a suggestion. When
an agent writes to `outbox/<name>/`, the mailroom checks `<name>` against the
sender's list before the message is ever released to a recipient's `inbox/`. A
message to anyone not on the list is **bounced**: the orchestrator drops a
`system` note into the sender's own inbox explaining who they *can* message
("you may message: patent-lead"), and the undeliverable mail is filed in
`failed/`. So if `searcher` tried to mail `reporter` or `user` directly, it
self-corrects in-band — it never reaches the forbidden recipient. This is
cooperative, not OS isolation (an agent with raw filesystem access *could* write
straight into another inbox), so it's documented honestly, not a security
boundary. For the broader routing model see
[`mail-model.md`](../mail-model.md) and [`delegation-pipeline.md`](./delegation-pipeline.md).

### Per-type turn detection — the system clock

Everything moves off each agent's turn completion (stop → sweep → route →
release → nudge). Each `type` has a different completion signal, and the
orchestrator installs the matching detection at `up`:

| type | completion signal | installed at `up` |
|------|-------------------|-------------------|
| `claude` (`patent-lead`, `reporter`) | Stop hook files a sentinel when the CLI exits | Claude Stop hook |
| `codex` (`searcher`) | `notify` program fires on turn end | Codex `notify` hook |
| `gemini` (`landscape-mapper`) | pane goes quiet (no streaming output for a beat) | pane polling (auto `capture: pane`) |

If a `type`/`command` mismatch happens — e.g. a `claude` agent whose `command`
doesn't actually launch Claude — the matching completion signal never fires and
the agent pins "busy" forever (a silent deadlock). `status` showing an agent
`busy` for a long time *with* unread mail is the tell. See
[`cli-reference.md`](../cli-reference.md).

### The `pings` — a periodic nudge, not a self-starter

`patent-lead` is the only agent with a `pings:` block:

```yaml
    pings:
      - message: "Check your inbox/ for any new invention submissions or competitor patents waiting to be analyzed."
        cron: "*/30 * * * *"
        when_busy: skip
```

- **`cron: "*/30 * * * *"`** — standard cron: fires **every 30 minutes**.
- **`when_busy: skip`** — if the lead is mid-turn (busy) when the cron fires, the
  nudge is skipped rather than interrupting a live turn (which would risk
  corrupting it).
- The nudge re-pastes the protocol — including the allowed-recipient list — into
  the lead's pane, exactly like a normal nudge, so a forgetful model doesn't
  lose the thread of who it may message.

The other three agents have **no** pings — they are purely event-driven and only
move when the lead mails them. This matches the swarm's purpose: it runs on your
submissions, and the ping just reminds the lead to check for ones that arrived
while it was idle.

### What's *not* in this config
- **No `workdir` overrides.** All four agents have private, unprefixed
  workdirs (`patent-analyzer-workspace/<name>`); there's no shared-repo case
  here like the data-pipeline builder, so no mailbox namespacing is needed. (If
  you did share a workdir, see [`custom-workspace.md`](./custom-workspace.md).)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **No `capture` overrides per agent.** `defaults.capture: none` plus the auto-
  upgrade means each type gets its natural mode (Stop hook / notify / pane) at
  `up`, so the clock keeps running without manual `capture:` lines.
- **No cross-specialist links.** The absence of `landscape-mapper`/`searcher`/
  `reporter` in each other's `can_talk_to` is the entire point — they can't
  re-derive the same patents or contradict each other out-of-band.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/patent-analyzer.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings.
2. Creates the runtime dirs (`patent-analyzer-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/about.md`
   contact card *is* the ACL made visible: the lead gets `outbox/searcher/`,
   `outbox/landscape-mapper/`, `outbox/reporter/`, `outbox/user/`; the searcher
   gets only `outbox/patent-lead/`; etc.
4. **Installs per-type turn detection** — the Claude Stop hook for `patent-lead`
   and `reporter`, the Codex `notify` hook for `searcher`, and pane polling for
   `landscape-mapper` (the gemini auto-upgrade).
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'patent-analyzer' is up with 4 agent(s)
:: attach with:  tmux attach -t <patent-lead-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/patent-analyzer.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents, so it must **never** be exposed on `0.0.0.0` without a token.
See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole landscape flow route mail with no API keys — the mechanics are
> identical.

---

## 5. Drive a submission

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the lead's consolidated brief as mail (rather
than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/patent-analyzer.yaml
```

This rewrites the `user` contact card in the lead's `outbox/user/about.md` to
`Status: available`, so the lead sees you're reachable. (While away, mail to you
is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the invention into the swarm, addressed to the lead:

```bash
./agentainer send -c examples/patent-analyzer.yaml --to patent-lead \
  "Analyze this invention: <paste description or a path>. We are the APPLICANT; field: <tech area>."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the lead, then — because the
inbox was empty — **released into `inbox/`** and the lead is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list:
`searcher, landscape-mapper, reporter, user`).

### The mail flowing

Watching the log (§6), you'll see the analysis advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **lead receives the submission.** It reads `inbox/`, acknowledges you, and
   writes the invention text + field context into `outbox/searcher/` and
   `outbox/landscape-mapper/` (in parallel).
2. **searcher + landscape-mapper work.** Each reads its inbox, writes its findings
   (prior-art table / ownership map) into `outbox/patent-lead/`. On each stop,
   that routes back to the lead.
3. **lead briefs the reporter.** Once both are in, the lead merges their technical
   findings with the original invention text and writes them into
   `outbox/reporter/`. On stop, that routes to the reporter.
4. **reporter drafts the brief.** It reads its inbox, synthesizes ONE brief, and
   writes it to `outbox/patent-lead/`. On stop, that routes back to the lead.
5. **lead delivers to you.** The lead reviews the brief for internal consistency,
   then forwards it to `outbox/user/`. On stop, that's delivered to your `user`
   mailbox (visible with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> The lead's `*/30` ping only fires if it's idle for a stretch; it `skip`s while
> busy, so it never interrupts an active analysis. It's a "did new mail arrive?"
> reminder, not a self-starter.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/patent-analyzer.yaml
```

```
swarm: patent-analyzer   root: ./patent-analyzer-workspace
  patent-lead      (claude) up idle queue=0 unread=1 talks=searcher, landscape-mapper, reporter, user
  searcher         (codex)  up idle queue=0 unread=0 talks=patent-lead
  landscape-mapper (gemini) up idle queue=0 unread=0 talks=patent-lead
  reporter         (claude) up idle queue=0 unread=0 talks=patent-lead
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/patent-analyzer.yaml          # whole swarm, last 20
./agentainer logs -c examples/patent-analyzer.yaml -f        # follow live
./agentainer logs searcher -c examples/patent-analyzer.yaml  # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox patent-lead -c examples/patent-analyzer.yaml
```

Prints the one released message (headers + body), or `patent-lead: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue patent-lead -c examples/patent-analyzer.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach landscape-mapper -c examples/patent-analyzer.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom
— handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or (more directly)
by sending notes into an agent's inbox.

- **Send a clarification to the lead.** Realized you're the *competitor-watcher*,
  not the applicant, or the field is narrower than you said?
  `./agentainer send -c examples/patent-analyzer.yaml --to patent-lead "We are the
  COMPETITOR-WATCHER; the field is lithium-solid-state cells, focus on FTO vs.
  assignee X."` The lead re-briefs the specialists.
- **Ask for a deeper cut.** `./agentainer send -c examples/patent-analyzer.yaml
  --to patent-lead "Have the reporter rank the top-3 FTO risks explicitly and name
  the blocking patent for each."` — the lead forwards it to the reporter.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/patent-analyzer.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/patent-analyzer.yaml     # resume is the default
```

On `up`, Agentainer reads `patent-analyzer-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for
`patent-lead` and `reporter`, `codex resume <id>` for `searcher`, and gemini's
pane-based resume for `landscape-mapper`. A resumed agent is *not* re-sent the
standby prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/patent-analyzer.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a `counsel` hand-off agent
If you want the brief to route to a final pre-attorney reviewer, add a fifth
agent that can only read the lead's deliverable:

```yaml
  - name: counsel
    type: claude
    can_talk_to: [patent-lead, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the PRE-FILING COUNSEL REVIEWER. When the lead delivers a brief,
      sanity-check the FTO/infringement framing and the "next steps" against the
      standing disclaimer, then forward an annotated brief to outbox/user/. You
      do no new searching.
```
Then add `counsel` to the lead's `can_talk_to` so it can be briefed.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `searcher: type: claude` (or `hermes`) to put prior-art search on a different
  model than the lead.
- `reporter: type: gemini` to have the brief drafted on Gemini while the lead
  stays Claude.
- Remember: `gemini`/`hermes` need pane polling (auto `capture: pane` since they
  have no completion hook) — already the case for `landscape-mapper`.

### Tune the ACL
- To let the `reporter` escalate a finding straight to `user` (not only via the
  lead), add `user` to its `can_talk_to`. Mind that this widens the human-facing
  surface; the doc's convention keeps the lead the sole `user` contact.
- To lock the specialists even tighter (already the case here), leave each at
  `can_talk_to: [patent-lead]` — that's the one-place-owns-the-merge guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md)
  for mixing model families safely.

### Add a second ping
If you want the searcher to also be nagged about stale submissions, give it a
`pings:` block like the lead's. Note `when_busy: skip` is the safe default so a
ping never interrupts a live search.

---

## 10. Tips & footguns

- **Keep the lead the only `user`-facing agent.** Only the lead lists `user` in
  `can_talk_to`. That gives you a single funnel: raw prior-art and landscape
  findings always pass through review and merge before they reach you as one
  brief. If a specialist tries to mail `user` directly, the orchestrator bounces
  it (ACL) and drops a `system` note in their inbox explaining who they *can*
  message — the model self-corrects in-band.

- **The three specialists never talk to each other by design.** `searcher`,
  `landscape-mapper`, and `reporter` list only `patent-lead`. If you accidentally
  add one specialist to another's `can_talk_to`, they'll start re-deriving the
  same patent set and the merged brief can double-count references. The ACL is the
  guardrail — keep it tight.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch (e.g. a `gemini` agent whose `command`
  doesn't launch Gemini) means completion never triggers and the agent pins
  "busy" forever. `status` showing an agent `busy` for a long time with `unread`
  mail is the tell. Also remember gemini's completion is *inferred from pane
  quietness*, so a chatty long-running search can look "busy" until it actually
  stops streaming.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **The lead's ping `skip`s while busy.** `when_busy: skip` means the every-30-min
  nudge won't interrupt an active analysis — good — but it also means if the lead
  is *stuck* busy (see the deadlock footgun above), the ping won't fire either.
  Use `status` to tell "idle + ping fired" from "pinned busy".

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/patent-analyzer.yaml
  ./agentainer remove-session -c examples/patent-analyzer.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches your config.

- **Availability shapes the ending.** If `user` is **away** when the lead
  finishes, your consolidated brief is *held* (with a `system` "the user is away"
  ack to the lead) rather than lost — read it later with
  `agentainer user inbox` or flip yourself available and it's delivered.

- **This is decision-support, not legal advice.** The config bakes the standing
  "not legal advice — have a registered patent attorney review before filing"
  note into the lead's and reporter's roles, and the reporter ends every brief
  with it. Treat the output as a first-pass read to decide what to escalate to
  counsel, never as a patent opinion or FTO clearance.

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
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- [`configuration.md`](../configuration.md) — every config field, including `pings:` / `cron`.
- [`cli-reference.md`](../cli-reference.md) — `up` / `send` / `status` / `logs` / `user` / `sessions`.
- [`ui-guide.md`](../ui-guide.md) — the `serve` mail-app control plane.
- [`legal-contract-review.md`](./legal-contract-review.md) — another single-lead, counsel-facing workflow.
- [`competitive-intel.md`](./competitive-intel.md) — adjacent competitive-landscape analysis.
- [`research-swarm.md`](./research-swarm.md) — a broader multi-agent research topology.
- [`white-paper-research.md`](./white-paper-research.md) — synthesis-heavy writing workflow.
- `examples/patent-analyzer.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14, pings/cron).

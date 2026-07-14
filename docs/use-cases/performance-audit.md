# Use case: the performance audit swarm

A concrete, end-to-end walkthrough of the shipped `examples/performance-audit.yaml`
swarm — a four-agent team where a **lead** briefs a **frontend_perf** auditor and
a **backend_perf** auditor *in parallel*, then a **reporter** merges both sets of
measurements into one prioritized fix list for the human. It's the canonical
"profile two layers, then rank the fixes by impact" loop, wired entirely through
Agentainer's file-based mail model.

This is written for **frontend and backend developers and SREs** who want a
repeatable way to point a couple of coding agents at a slow site — a live URL, a
repo, or both — and get back an actionable, evidence-backed list of what to fix
first. It is deliberately distinct from the security-audit and accessibility-audit
swarms: same hub-and-spokes shape, different question (*where does the time go?*).

Everything below is based on the actual contents of `examples/performance-audit.yaml`
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
  frontend_perf ─┐
                 ├──▶ lead ──▶ reporter ──▶ user
  backend_perf ──┘        ▲            │
                         └────────────┘   (reporter can ask lead for clarification)
```

Four agents, one hub-and-spokes flow with a parallel fan-out:

1. **`user` → `lead`** — you send the target (URL and/or repo path + stack note).
2. **`lead` → `frontend_perf`** *and* **`lead` → `backend_perf`** — the lead briefs
   both auditors in parallel, each on its own layer.
3. **`frontend_perf` → `lead`** and **`backend_perf` → `lead`** — each reports its
   measurements back up to the lead (never to each other).
4. **`lead` → `reporter`** — the lead hands both measurement sets to the reporter.
5. **`reporter` → `user`** — the reporter delivers the merged, prioritized fix list
   to you (and can ask the lead for clarification along the way).

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. An agent can only deliver to names on its own list; anything
else is bounced back as a `system` message and filed in `failed/` (see §7).

The reason `frontend_perf` and `backend_perf` **cannot** talk to each other is
deliberate: they profile different layers and would only duplicate numbers or argue
over whose fix comes first. Keeping the merge in one place (the reporter, via the
lead) means the ranking is decided once, with both layers' evidence on the table.

---

## 2. The config, explained

Here is `examples/performance-audit.yaml`, field by field (the full file has longer
`role` blocks — this walkthrough summarizes them).

### `swarm`
- **`name: performance-audit`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./performance-audit-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent gets
  `performance-audit-workspace/<name>/` as its workdir (created on `up`), with its
  mailbox folders alongside. Orchestrator state goes under
  `performance-audit-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode, appropriate for the
  key-free mock demo. **But note:** `capture` is how Agentainer knows a turn
  finished, and it's keyed off each agent's `type`. For `claude` and `codex`, whose
  CLIs support a completion **hook**, `capture: none` is a footgun — so the config
  loader *upgrades* it back to `hook` and prints a warning at `up`. Net effect: all
  four agents here use their native hook once you run real CLIs.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `lead` (type: `claude`)
- **`can_talk_to: [frontend_perf, backend_perf, reporter, user]`** — the lead is
  the hub: it briefs both auditors, hands off to the reporter, and is **one of only
  two agents that can talk to `user`**.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity: sequence the audit, brief both layers in
  parallel, de-duplicate cross-layer findings, and make sure every claimed slowdown
  has a *measured number* behind it. On `up` this becomes the agent's first prompt,
  wrapped in a **standby notice** ("no task yet — wait until notified"), so the lead
  waits for your target instead of proactively mailing peers.
- **`role` ends with the MAILBOX reminder** — the read-inbox / move-to-`read/` /
  write-to-`outbox/<name>/` protocol, re-pasted on every nudge.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `frontend_perf` (type: `claude`)
- **`can_talk_to: [lead]`** — reports only upward to the lead; cannot reach the
  backend auditor or the `user` directly.
- **`role`** — the client-side brief: Core Web Vitals (LCP, INP, CLS) + TTFB, the
  JS/CSS bundle (size, biggest modules, unused/duplicate code, code-splitting),
  render-blocking resources, unoptimized/unsized images and fonts, long main-thread
  tasks, and static-asset caching/CDN. Every finding carries a measured number, the
  user-visible symptom, evidence, and an estimated win.
- **Turn detection:** `claude` → Stop hook.

### `backend_perf` (type: `codex`)
- **`can_talk_to: [lead]`** — reports only upward to the lead.
- **`command: "codex --yolo"`** — placeholder launch command for Codex.
- **`role`** — the server-side brief: slow/unindexed queries, N+1 patterns and
  missing pagination, caching gaps, hot-path API latency (p50/p95/p99), sync work
  that should be async, connection-pool/concurrency limits, and chatty endpoints.
  Each finding: severity by latency impact, `file:line` evidence, measured or
  estimated cost, and a one-line remediation hint.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `reporter` (type: `claude`)
- **`can_talk_to: [lead, user]`** — the second (and only other) agent that can
  reach `user`. It merges both auditors' findings and delivers the final list, but
  can loop back to the lead for clarification rather than guessing.
- **`role`** — merge into ONE prioritized table sorted by impact-to-effort (not by
  layer), fold cross-layer duplicates into a single row, and close with a "quick
  wins vs. deeper work" split. Ends with the MAILBOX reminder.
- **Turn detection:** `claude` → Stop hook.

### What's *not* in this config
- **No `pings`.** No agent is auto-nudged on a timer; the
  pipeline is purely event-driven off real mail. (If you wanted the lead to poke a
  slow auditor, you'd add a `pings` cron rule to it.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/performance-audit.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for the claude/codex agents).
2. Creates the runtime dirs (`performance-audit-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's `about.md`
   contact card *is* the ACL made visible: the lead gets `outbox/frontend_perf/`,
   `outbox/backend_perf/`, `outbox/reporter/`, `outbox/user/`; each auditor gets
   only `outbox/lead/`; the reporter gets `outbox/lead/` and `outbox/user/`.
4. **Installs per-type turn detection** — the Claude Stop hook for lead /
   frontend_perf / reporter, the Codex `notify` hook for backend_perf.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'performance-audit' is up with 4 agent(s)
:: attach with:  tmux attach -t <lead-session>
:: you can use the UI with:  agentainer serve -c examples/performance-audit.yaml --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). It **binds `127.0.0.1` by default** — keep it
loopback-only unless you deliberately opt into a remote bind with a token (the UI
is a control plane that can type into `--yolo` agents; see
[`remote-access.md`](./remote-access.md)). The headless CLI stays fully functional
without it.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole pipeline route mail with no API keys — the mechanics are identical.

---

## 4. Drive an audit

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the reporter's final list as mail (rather than
have it held), turn yourself available first:

```bash
./agentainer user available -c examples/performance-audit.yaml
```

This rewrites the `user` contact card in the lead's and reporter's `outbox/user/`
to `Status: available`. (While away, mail to you is *held* and the sender gets a
`system` ack — nothing bounces.)

Now send the target into the swarm, addressed to the lead:

```bash
./agentainer send --to lead \
  "Audit https://shop.example.com ; repo at /srv/shop (Next.js + Postgres). Staging is fine to load-test."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the lead, then — because the inbox
was empty — **released into `inbox/`** and the lead is **nudged** (the protocol is
re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§5), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **lead receives the target.** It reads `inbox/`, then writes two briefs — one
   into `outbox/frontend_perf/`, one into `outbox/backend_perf/`. When its turn
   ends, the orchestrator sweeps the outbox, routes *both* messages, and nudges
   both auditors.
2. **the auditors profile in parallel.** Each reads its inbox, does its layer's
   measurements, and writes a report into `outbox/lead/`. On each stop, that routes
   back to the lead — the lead's inbox releases them one at a time.
3. **lead consolidates.** Once both reports are in, the lead writes the combined
   material into `outbox/reporter/`. On stop, that routes to the reporter.
4. **reporter finalizes.** It reads both sets, merges and ranks them, and writes the
   prioritized fix list into `outbox/user/`. On stop, that's delivered to your
   `user` mailbox (you'll see it with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a target, the agents just sit in standby (that's the point of
> the standby prompt). The pipeline only moves when real mail arrives.

---

## 5. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/performance-audit.yaml
```

```
swarm: performance-audit   root: ./performance-audit-workspace
  lead (claude) up idle queue=0 unread=0 talks=frontend_perf, backend_perf, reporter, user
  frontend_perf (claude) up idle queue=0 unread=1 talks=lead
  backend_perf (codex) up idle queue=0 unread=1 talks=lead
  reporter (claude) up idle queue=0 unread=0 talks=lead, user
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/performance-audit.yaml            # whole swarm, last 20
./agentainer logs -c examples/performance-audit.yaml -f          # follow live
./agentainer logs backend_perf -c examples/performance-audit.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox frontend_perf -c examples/performance-audit.yaml
```

Prints the one released message (headers + body), or `frontend_perf: inbox is
empty`.

**Queue depth** — mail waiting behind the one released message (useful on the lead,
which receives from both auditors):

```bash
./agentainer queue lead -c examples/performance-audit.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach backend_perf -c examples/performance-audit.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 6. Iterate on the audit

An audit is rarely one-shot. Because `user` and `reporter` both stay up, you can
keep the conversation going:

- **Ask for a deeper cut.** After the first list lands, reply straight back to the
  lead: `./agentainer send --to lead "Re-profile the checkout route specifically —
  it's the money path."` The lead re-briefs whichever auditor(s) it needs.
- **Chase a single number.** If the reporter's table has a row you doubt, send the
  lead a follow-up and it will ask that auditor to re-measure rather than re-running
  the whole swarm.
- **Fix, then re-run.** Apply a fix (add an index, code-split a bundle), then send
  the same target again — the fresh numbers show whether the estimated win was real.
  Conversations resume by default (see below), so the agents keep their context
  across a `down`/`up`.

---

## 7. Resume after a stop

Tear the swarm down when you're done:

```bash
./agentainer down -c examples/performance-audit.yaml
```

Bring it back later and **conversations resume by default**:

```bash
./agentainer up -c examples/performance-audit.yaml     # resume is the default
```

On `up`, Agentainer reads `performance-audit-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the three
Claude agents, `codex resume <id>` for backend_perf. A resumed agent is *not*
re-sent the standby prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/performance-audit.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 8. Customize

- **Add a `load-tester`.** For real latency numbers under load, add a fourth spoke
  that drives traffic (e.g. a `codex`/`hermes` agent that runs `k6`/`wrk` against a
  staging URL) and reports throughput/p99 back to the lead:
  ```yaml
    - name: load_tester
      type: codex
      can_talk_to: [lead]
      command: "codex --yolo"
      role: |
        You are the LOAD TESTER. Given a staging URL and the hot routes the lead
        names, run a short, bounded load test and report requests/sec, error rate,
        and p50/p95/p99 latency per route. NEVER load-test production. Write results
        to outbox/lead/.
  ```
  Then add `load_tester` to the lead's `can_talk_to`. The reporter now has measured
  latency-under-load to rank against, not just static analysis.

- **Swap models per layer.** The `type`/`command` pairs are independent. Point
  `backend_perf` at `gemini` (`type: gemini`, `command: "gemini --yolo"`,
  `capture: pane`) if you prefer it for code reading, or make `frontend_perf` a
  `codex` agent. Just keep `type` and `command` launching the **same** CLI — a
  mismatch means the turn-completion signal never fires and the agent hangs (the
  loader catches the obvious cases at `up`).

- **Tune the ACL.** The shape is intentionally strict. To let the reporter pull a
  clarifying measurement straight from an auditor (skipping the lead), add
  `frontend_perf`/`backend_perf` to the reporter's `can_talk_to` and add `reporter`
  to each auditor's list. Loosen deliberately: the whole point of routing everything
  through the lead is that the merge and de-duplication happen once, in one place.

---

## 9. Tips & footguns

- **Keep human contact to the lead and reporter.** Only those two list `user` in
  `can_talk_to`. If an auditor tries to mail `user` directly, the orchestrator
  bounces it (ACL) and drops a `system` note in the auditor's inbox explaining who
  it *can* message — the model self-corrects in-band.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually fires**
  — a `type`/`command` mismatch (e.g. a `codex` agent whose `command` doesn't launch
  Codex) means completion never triggers and the agent pins "busy" forever.
  `status` showing an agent `busy` for a long time with `unread` mail is the tell.

- **Insist on measured numbers.** The lead's role explicitly rejects hunches — a
  "this feels slow" finding with no number is worse than useless in a ranked list.
  If an auditor hands up a claim without a measurement, bounce it back through the
  lead before the reporter ranks it.

- **Force-idle if a pane-captured agent's turn never registers.** If you swap an
  agent to `gemini`/`hermes` (pane capture) and its turn never registers, nudge the
  state along:
  ```bash
  ./agentainer idle backend_perf -c examples/performance-audit.yaml
  ```

- **`remove-session` to reset.** To wipe all Agentainer state (runtime + mailboxes)
  and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/performance-audit.yaml
  ./agentainer remove-session -c examples/performance-audit.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

- **Availability shapes the ending.** If `user` is **away** when the reporter
  finishes, your final fix list is *held* (with a `system` "the user is away" ack
  to the reporter) rather than lost — read it later with `agentainer user inbox` or
  flip yourself available and it's delivered.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — how resume is recorded.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — the hub-and-spokes
  delegation pattern this swarm is built on.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing claude/codex/gemini/hermes
  agents in one swarm.

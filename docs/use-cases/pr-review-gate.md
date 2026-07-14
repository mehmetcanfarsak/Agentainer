# Use case: the parallel PR review gate

A concrete, end-to-end walkthrough of the shipped `examples/pr-review-gate.yaml`
swarm — a **parallel review gate** where several specialist reviewers audit the
*same* pull request at the same time, and a **synthesizer** merges their verdicts
into one prioritized review for the human. It is the canonical "fan out → many
read → merge" loop, wired entirely through Agentainer's file-based mail model.

Everything below is based on the actual contents of
`examples/pr-review-gate.yaml` and the shipped CLI (`lib/cli.py`) and mailroom
(`lib/mail.py`). No API keys are needed to understand the mechanics; to run it
*for real* you supply the coding-CLI commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Why parallel specialists beat one reviewer

A single reviewer reads a diff once, through one lens, in one sitting. Real
reviews are **multi-axial**: a security hole, an N+1 query, and a naming mess can
all live in the same 200-line diff, and one person rarely holds all three
perspectives at full strength at once. Worse, a single reviewer's verdict is one
opinion — there's no second pass to catch what the first missed.

`pr-review-gate` attacks that by running **three independent reviewers over the
identical context in parallel**:

- **`security`** hunts auth/input/secret/data-risk defects.
- **`performance`** hunts N+1s, allocations, complexity, and regressions.
- **`style`** hunts readability, naming, convention, and weak tests.

Because they run at the same time, the wall-clock cost of a triple review is
roughly the cost of one (the slowest specialist), not three. And because each is
narrowly scoped, each stays sharp instead of spreading thin. The catch with
parallel reviews is always *reconciliation*: three separate reports are three
things to read and three chances to conflict. That's what the **`synthesizer`**
is for — it is a dedicated agent whose only job is to merge the three critiques
into **one** review: dedupe overlaps, rank by severity, and pin each finding to
the reviewer who raised it. You get a single, actionable verdict (ship /
ship-with-fixes / block) instead of three unrelated essays.

This is deliberately different from [`examples/code-review.yaml`](../code-review.yaml),
which is also a "hub" swarm but a **single reviewer** checking several developers'
slices. There, one reviewer voice keeps a multi-developer stream consistent. Here,
several specialist voices review *one* artifact and a separate agent reconciles
them. Same hub idea, different shape — fan-out-then-merge, not fan-in-from-many.

---

## 2. The topology

```
        user
          │  "Review PR #123: <diff or repo+branch>"
          ▼
        triage  (hub: fans the PR out, collects the critiques)
          ├──▶ security  ──┐
          ├──▶ performance ┤   all three run in PARALLEL
          └──▶ style  ─────┤
                          ▼
                   triage  (gathers all three)
                          │  forwards the bundle
                          ▼
                    synthesizer ──▶ user   (one merged, prioritized review)
```

The arrows are **enforced** by each agent's `can_talk_to` list — the ACL is the
topology:

| Agent        | Type   | `can_talk_to`                        | Role                          |
|--------------|--------|--------------------------------------|-------------------------------|
| `triage`     | claude | `security, performance, style, synthesizer, user` | hub: fan-out + collect |
| `security`   | codex  | `triage`                             | security audit only           |
| `performance`| codex  | `triage`                             | perf audit only               |
| `style`      | claude | `triage`                             | readability/convention/tests  |
| `synthesizer`| claude | `triage, user`                       | merge → one review → human    |

Two things to notice:

1. **Only `triage` and `synthesizer` may reach `user`.** The human-facing surface
   is exactly two agents: `triage` (intake) and `synthesizer` (output). The
   specialists never talk to you directly — their work always flows back through
   `triage` first.
2. **The specialists only talk to `triage`.** They can't see each other's
   critiques, so they never negotiate or contradict *in transit* — any
   overlap/conflict is resolved deliberately by the `synthesizer`, not by the
   reviewers arguing. `triage` is the only convergence point before the merge.

If a specialist tried to mail `user` or another specialist, the orchestrator
**bounces** it (ACL) and drops a `system` note in its inbox explaining who it can
message — the model self-corrects in-band (see §7).

---

## 3. The config, explained

Here is `examples/pr-review-gate.yaml` in full:

```yaml
# 🛡️ PR review gate -- several SPECIALIST reviewers run in PARALLEL, then a
# SYNTHESIZER merges their verdicts into ONE prioritized review.
#   cp examples/pr-review-gate.yaml my-pr-gate.yaml
#   agentainer up   -c my-pr-gate.yaml
#   agentainer send -c my-pr-gate.yaml --to triage "Review PR #123: <diff or repo+branch>"
#   agentainer down -c my-pr-gate.yaml
swarm:
  name: pr-review-gate
  root: ./pr-review-gate-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: triage
    type: claude
    can_talk_to: [security, performance, style, synthesizer, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are TRIAGE, the hub of a parallel PR review gate. ...
  - name: security
    type: codex
    can_talk_to: [triage]
    command: "codex --yolo"
    role: |
      You are the SECURITY reviewer. ...
  - name: performance
    type: codex
    can_talk_to: [triage]
    command: "codex --yolo"
    role: |
      You are the PERFORMANCE reviewer. ...
  - name: style
    type: claude
    can_talk_to: [triage]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the STYLE reviewer. ...
  - name: synthesizer
    type: claude
    can_talk_to: [triage, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the SYNTHESIZER. ...
```

### `swarm`
- **`name: pr-review-gate`** — shows up in `status`, logs, sessions.
- **`root: ./pr-review-gate-workspace`** — parent dir for each agent's workdir
  and mailboxes. Each agent gets `pr-review-gate-workspace/<name>/` as its
  workdir (created on `up`); its mailbox folders live alongside. Orchestrator
  state goes under `pr-review-gate-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless overridden.
- **`capture: none`** — the default turn-detection mode. But `capture` is how
  Agentainer knows a turn finished, keyed off each agent's `type`. For `claude`
  and `codex`, whose CLIs support a completion **hook**, `capture: none` is a
  footgun — so the config loader *upgrades* it back to `hook` and prints a
  warning at `up`. Net effect here: all five agents run their hook (claude → Stop
  hook, codex → `notify` program). `gemini`/`hermes` would use pane polling.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent states
  its own list explicitly, so this is just a safe floor.

### `triage` (type: `claude`)
- **`can_talk_to: [security, performance, style, synthesizer, user]`** — the only
  agent that can both fan the PR out *and* reach the `synthesizer` and `user`.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command; treat command
  strings as sensitive, they may embed keys.)
- **`role`** — the standing identity: read the PR from `user`, fan it out to all
  three specialists in parallel, collect their critiques, then forward the bundle
  to `synthesizer` unchanged. On `up` this becomes the first prompt, wrapped in a
  **standby notice** so `triage` waits for your PR instead of proactively mailing
  peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `security` / `performance` / `style` (codex / claude)
- Each lists **`can_talk_to: [triage]`** — it can only report back to the hub.
- `security` and `performance` are `codex` (their `notify` hook); `style` is
  `claude` (Stop hook).
- Each `role` scopes the reviewer to **one lane** and says *report back to triage,
  do not edit the PR, cite file:line*. The narrow lane is what keeps the parallel
  reviews sharp and what makes the `synthesizer`'s job mechanical.

### `synthesizer` (type: `claude`)
- **`can_talk_to: [triage, user]`** — it receives the bundle from `triage` and is
  the **second** agent allowed to reach `user`. Its output is the deliverable.
- **`role`** — merge the three critiques into one prioritized review: dedupe,
  rank by severity (blocker > major > minor > nit), attribute each item to its
  reviewer, and lead with a one-line verdict (ship / ship-with-fixes / block).

### `triage`'s scheduled nudge (`pings:`)
The gate is fundamentally **event-driven** — it moves when you send a PR — but
`triage` carries one `pings:` rule so a review can't quietly stall on a slow
specialist:

```yaml
    pings:
      - message: |
          Working-hours check: if a PR is mid-review and one specialist is
          lagging, note which lane is outstanding and chase it. If nothing is in
          flight, do nothing and wait for the next PR.
        cron: "0 10-17 * * 1-5"       # top of the hour, 10:00-17:59, Mon-Fri
```

Each `pings:` entry is a `message` plus a 5-field `cron`
(`minute hour day-of-month month day-of-week`, in the host's local time). This
one fires at the top of every hour from 10:00 to 17:59 on weekdays, injecting a
`system` nudge that tells `triage` to chase any lagging lane. `when_busy`
defaults to `skip`, so the nudge is **dropped if it comes due mid-turn** — it
never interrupts an active fan-out or merge. If you'd rather it fire round the
clock, widen the hour field (`* * * *` → `0 * * * *`); if you want it to wait
rather than skip when busy, add `when_busy: queue`.

### What's *not* in this config
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — the synthesizer's final review is *held* (never bounced) until you
  flip it on (see §4).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/pr-review-gate.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the `capture: none → hook` upgrade
   warnings for all five agents.
2. Creates the runtime dirs (`pr-review-gate-workspace/.agentainer/…`).
3. **Initializes the mailboxes** — for every agent, the five folders `inbox/
   outbox/ read/ sent/ failed/`, the per-agent queue, and an `outbox/<peer>/`
   folder **for each allowed recipient**. That folder's `about.md` contact card
   *is* the ACL made visible: `triage` gets `outbox/security/`,
   `outbox/performance/`, `outbox/style/`, `outbox/synthesizer/`, `outbox/user/`;
   each specialist gets `outbox/triage/`; `synthesizer` gets `outbox/triage/` and
   `outbox/user/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `triage`,
   `style`, `synthesizer`; the Codex `notify` hook for `security`, `performance`.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck reviewer can't wedge the gate.

At the end, `up` prints attach and **`serve`** hints. The `serve` line gives you
the mail-app control-plane UI (threads, live panes, send-as-user, availability
toggle). Drop `--host`/`--token` for the safe loopback-only bind
(`127.0.0.1`, the default — never `0.0.0.0` without an explicit opt-in token;
see CLAUDE.md §18). See the `README.md` "control-plane UI" section.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole fan-out and merge route mail with no API keys — the mechanics are
> identical.

---

## 5. Drive a review

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the synthesized review as mail (rather than
have it held), turn yourself available first:

```bash
./agentainer user available -c examples/pr-review-gate.yaml
```

This rewrites the `user` contact card in `synthesizer`'s `outbox/user/about.md`
to `Status: available`, so the synthesizer sees you're reachable. (While away,
mail to you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the PR into the swarm, addressed to `triage`:

```bash
./agentainer send --to triage "Review PR #123: https://github.com/org/repo/pull/123"
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for `triage`, then — because its
inbox was empty — **released into `inbox/`** and `triage` is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the gate advance in two waves:

1. **Intake.** `triage` reads `inbox/`, copies the same PR context into
   `outbox/security/`, `outbox/performance/`, `outbox/style/`, and finishes its
   turn. The orchestrator sweeps its outbox and **releases all three** into the
   specialists' inboxes (they were empty), nudging each.
2. **Parallel review.** The three specialists each read their copy of the PR,
   write their lane-specific critique into `outbox/triage/`, and finish. Their
   turns end; the orchestrator routes each critique back to `triage`, releasing
   them one at a time.
3. **Gather.** `triage` accumulates the three critiques in its `inbox/`. When it
   has all three, it forwards the verbatim bundle into `outbox/synthesizer/` and
   finishes. That routes to `synthesizer`.
4. **Merge.** `synthesizer` reads the bundle, writes the merged, prioritized
   review into `outbox/user/`, and finishes. That's delivered to your `user`
   mailbox (see it with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion. The
parallelism is real: steps 2's three reviews run on overlapping turns, so the
gate's latency is roughly the slowest specialist plus the merge, not three serial
reviews.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/pr-review-gate.yaml
```

```
swarm: pr-review-gate   root: ./pr-review-gate-workspace
  triage (claude) up idle queue=0 unread=0 talks=security, performance, style, synthesizer, user
  security (codex) up busy queue=0 unread=1 talks=triage
  performance (codex) up busy queue=0 unread=1 talks=triage
  style (claude) up busy queue=0 unread=1 talks=triage
  synthesizer (claude) up idle queue=0 unread=0 talks=triage, user
supervisor: alive
```

Note the three specialists all show `unread=1` at once — that's the fan-out, live.

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/pr-review-gate.yaml          # whole swarm, last 20
./agentainer logs -c examples/pr-review-gate.yaml -f        # follow live
./agentainer logs synthesizer -c examples/pr-review-gate.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what an agent is currently looking at:

```bash
./agentainer inbox triage -c examples/pr-review-gate.yaml
```

Prints the one released message (headers + body), or `triage: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue triage -c examples/pr-review-gate.yaml
```

**Attach to a live pane** — watch (or type into) an agent's tmux session:

```bash
./agentainer attach synthesizer -c examples/pr-review-gate.yaml
```

Detach with `Ctrl-b d`. Typing into a pane bypasses the mailroom — handy for
un-sticking an agent, but the mail model is the normal path.

---

## 7. Tips & footguns

- **Two human-facing agents, on purpose.** Only `triage` and `synthesizer` list
  `user` in `can_talk_to`. That gives you a clean intake (PR in) and a clean
  output (review out), with all specialist noise filtered in between. If a
  specialist tries to mail `user` directly, the orchestrator **bounces** it
  (ACL) and drops a `system` note in its inbox explaining who it *can* message —
  the model self-corrects in-band.

- **The specialists never see each other's work.** That's a feature: it means no
  reviewer moderates its findings to match another's, and any conflict is
  resolved *deliberately* by the `synthesizer`. If you'd rather they coordinate,
  you'd widen their ACL — but then you lose the independent-verdict property.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion. A
  `type`/`command` mismatch (e.g. a `codex` agent whose `command` launches `claude`)
  means completion never triggers and the agent pins "busy" forever. `status`
  showing an agent `busy` for a long time with `unread` mail is the tell. Validate
  the topology first with `agentainer validate -c examples/pr-review-gate.yaml`.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  gate: mail moved to `read/` is best-effort, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. A per-pair runaway cap (≤20 messages / 60s) kills "thanks!"
  loops between `triage` and a specialist.

- **`triage` must wait for ALL THREE critiques.** Its role says to gather security,
  performance, and style before forwarding. If one specialist is wedged, the
  synthesizer never gets a bundle — `status`/`logs` will show which specialist is
  stuck. Fix or remove it (see §8) and re-run, or nudge its turn:
  ```bash
  ./agentainer idle <stuck-specialist> -c examples/pr-review-gate.yaml
  ```

- **Availability shapes the ending.** If `user` is **away** when `synthesizer`
  finishes, your merged review is *held* (with a `system` "the user is away" ack to
  `synthesizer`) rather than lost — read it later with `agentainer user inbox` or
  flip yourself available and it's delivered.

---

## 8. Customize

The gate is a small config — easy to reshape.

**Add another specialist reviewer** (e.g. `i18n`, `docs`). Add the agent with
`can_talk_to: [triage]` and a lane-scoped `role`, then add its name to `triage`'s
`can_talk_to` list so the fan-out includes it. `triage` will forward the bundle
to `synthesizer` as before; tell `synthesizer` in its `role` that a fourth
critique is now part of the merge.

```yaml
  - name: i18n
    type: codex
    can_talk_to: [triage]
    command: "codex --yolo"
    role: |
      You are the I18N reviewer. Audit for hardcoded strings, missing locale
      fallbacks, and pluralization bugs only. Cite file:line. Report back to
      triage by writing into outbox/triage/. Do not edit the PR.
```

**Swap models.** Any `type` ∈ `claude, codex, gemini, hermes` works, and
`command` must launch that same CLI. E.g. make `security` a `gemini` reviewer
(`command: "gemini --yolo"`, `capture: pane` since gemini polls the pane) or run
every agent on `hermes`. The hub/merge logic doesn't care which model each lane
uses — that's the point of the file mail model.

**Tune the ACL.** Want the `synthesizer` to pull the PR itself rather than receive
a `triage` bundle? Add `triage` to nothing new but give `synthesizer` the PR
directly — i.e. add `synthesizer` to `triage`'s rights (already there) and have
`triage` forward the *original PR* plus critiques. Want specialists to escalate a
blocker straight to `synthesizer`? Add `synthesizer` to each specialist's
`can_talk_to` — but that breaks the "independent verdicts" property, so prefer
routing through `triage`/`synthesizer`.

**Change the verdict shape.** The severity ranking (blocker > major > minor > nit)
and the "ship / ship-with-fixes / block" rubric live in `synthesizer`'s `role` —
edit them to match your team's review policy.

---

## 9. Resume after a stop

Tear the gate down when done:

```bash
./agentainer down -c examples/pr-review-gate.yaml
```

Bring it back later and **conversations resume by default**:

```bash
./agentainer up -c examples/pr-review-gate.yaml     # resume is the default
```

On `up`, Agentainer reads `pr-review-gate-workspace/.agentainer/sessions.yaml`
and reattaches each recorded conversation via its type's native resume: `claude
--resume <id>` for `triage`/`style`/`synthesizer`, `codex resume <id>` for
`security`/`performance`. A resumed agent is *not* re-sent the standby prompt (its
prior context is restored). Pass `--no-resume` to force everyone fresh. Inspect
what's recorded with:

```bash
./agentainer sessions -c examples/pr-review-gate.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md) and
the reboot walkthrough in
[`use-cases/resume-after-reboot.md`](./resume-after-reboot.md).

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and why the orchestrator
  owns all state.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resume conversations
  across restarts.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — the user → hub → worker
  pattern this gate extends into fan-out-then-merge.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing claude/codex/gemini/hermes
  in one swarm (every agent here can be a different model).
- `examples/pr-review-gate.yaml` — the config this guide walks through.
- `examples/code-review.yaml` — the *single*-reviewer sibling swarm, for contrast.

# Use case: Multi-provider review

A concrete, end-to-end walkthrough of the shipped
`examples/multi-provider-review.yaml` swarm — Agentainer's flagship
cross-model orchestration demo. You drop one PR into a single **lead** hub;
it fans the same PR out to **three reviewers running on three different
providers** (Anthropic Claude, OpenAI Codex, Google Gemini), each with a
**non-overlapping lens** (correctness / security / design) so their findings
don't duplicate, collects the three independent reviews, de-duplicates overlaps,
and merges them into one consolidated review for you. The reviewers never see
each other's output — each views the PR fresh.

Everything below is based on the actual contents of
`examples/multi-provider-review.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops). `agentainer validate` proves the routing without launching anything.

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Engineers and tech leads who want a **non-redundant** PR review that surfaces how
different models' strengths catch different classes of bug. Rather than three
humans (or three identical models) saying the same thing, this swarm gives you
three *independent* readouts — Claude on logic/edge-cases, Codex on
security/error-handling/tests, Gemini on design/clarity/docs — and a lead that
synthesizes them into one verdict (ship / ship-with-fixes / block) with each
finding tagged by the provider that surfaced it.

It is deliberately a **hub-and-spoke** with *blind* reviewers: every review and
every deliverable passes through the lead, and the three reviewers never talk to
each other, so the merged review has exactly one authority and the lenses don't
bleed into one another. This is distinct from `examples/code-review.yaml` (a
single-hub, same-model review) and `examples/pr-review-gate.yaml` (same-model
parallel specialists) — here the differentiator is the **provider *and* the
lens**, not just the topic lane.

---

## 2. The topology

```
          user
            │  "Review PR #123: <diff>"
            ▼
          lead                       (the hub: fans the PR out, collects the three reviews)
         /    |    \
  claude-reviewer  codex-reviewer  gemini-reviewer
   (Anthropic)      (OpenAI)        (Google)        ← all three run in PARALLEL, blind to each other
         \    |    /
          └───┴───┘   (each reports ONLY back to lead)
            │
          lead   (gathers all three, dedupes, merges)  ──▶  user
            (one consolidated, non-redundant review)
```

Four agents, one directed flow:

1. **`user` → `lead`** — you send the PR (a diff, a repo+branch, or a link).
2. **`lead` → `claude-reviewer` / `codex-reviewer` / `gemini-reviewer`** — the lead
   fans the *same* PR out to all three at once, telling each its lens. They run
   in parallel, each in its own pane.
3. **`claude-reviewer` / `codex-reviewer` / `gemini-reviewer` → `lead`** — each
   writes *only* to the lead's outbox (they never see one another's output).
4. **`lead` → `user`** — once all three reviews arrive, the lead de-dupes
   overlapping findings, groups by severity, tags each item with its provider,
   and delivers the merged review to you.

The routing above is *enforced* by each agent's `can_talk_to` list. The three
reviewers can reach **only** the lead; only the lead can reach `user`. If a
reviewer tried to mail another reviewer or you directly, the orchestrator bounces
it and files it in `failed/` (see §3). That keeps the merge single-owned and the
reviewers blind.

---

## 3. The config, explained

Here is `examples/multi-provider-review.yaml` in full (role bodies trimmed with
`…` for length; the shipped file has the complete text):

```yaml
swarm:
  name: multi-provider-review
  root: ./multi-provider-review-workspace

defaults:
  capture: none              # loader auto-upgrades claude/codex/gemini to their hooks
  can_talk_to: []           # tightened per agent below

agents:
  - name: lead
    type: claude
    can_talk_to: [claude-reviewer, codex-reviewer, gemini-reviewer, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are LEAD, the hub of a multi-provider PR review. You do not review the
      code yourself; you orchestrate three reviewers that run on DIFFERENT models.
      … FAN IT OUT to each reviewer with its lens; WAIT until all three reviews
      arrive; MERGE them into ONE consolidated review, dedupe overlaps, group by
      severity (blocker > major > minor > nit), and note WHICH provider surfaced
      each item. Lead with a one-line verdict. You are the ONLY agent allowed to
      talk to the user. …

  - name: claude-reviewer
    type: claude
    can_talk_to: [lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the CLAUDE reviewer, running on Anthropic Claude. Your lens is
      CORRECTNESS, LOGIC, and EDGE CASES only. … cite file:line, the precise
      defect, and the smallest correct fix. … review FRESH and independently; …
      report back to lead by writing a file into outbox/lead/. …

  - name: codex-reviewer
    type: codex
    can_talk_to: [lead]
    command: "codex --yolo"
    role: |
      You are the CODEX reviewer, running on OpenAI Codex. Your lens is SECURITY,
      ERROR HANDLING, and TESTS only. … do NOT cover correctness/logic or
      readability/design; another reviewer handles those. … report back to lead. …

  - name: gemini-reviewer
    type: gemini
    can_talk_to: [lead]
    command: "gemini --yolo"
    role: |
      You are the GEMINI reviewer, running on Google Gemini. Your lens is DESIGN,
      CLARITY, READABILITY, and DOCUMENTATION only. … do NOT cover correctness/
      logic or security/tests. … report back to lead by writing a file into
      outbox/lead/. …
```

Field by field:

### `swarm`
- **`name: multi-provider-review`** — the swarm's name (shows up in `status`,
  logs, sessions).
- **`root: ./multi-provider-review-workspace`** — the parent directory for the
  agents' working directories and mailboxes. Each agent gets its own workdir
  (`multi-provider-review-workspace/lead`, `…/claude-reviewer`, etc.); no workdir
  is shared, so no mailbox namespacing is needed. Orchestrator state goes under
  `multi-provider-review-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default capture mode. Read the **turn-detection**
  note below — this is the subtle part of this config.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this is just a safe floor.

### `lead` (type: `claude`)
- **`can_talk_to: [claude-reviewer, codex-reviewer, gemini-reviewer, user]`** —
  the lead is the hub: it fans the PR to the three reviewers and is the **only
  agent that can talk to `user`**. Keep the human-facing surface to a single
  agent.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command, e.g. a
  shell alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the lead's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the lead waits for your PR instead of proactively
  mailing peers. It is told to fan out, wait for *all three*, then merge.
- **Turn detection:** `claude` → a **Stop hook**. The `capture: none` default is
  **auto-upgraded to `capture: hook`** with a validation warning (see below).

### `claude-reviewer` (type: `claude`)
- **`can_talk_to: [lead]`** — reports *only* to the lead. Cannot reach the other
  reviewers or `user`; its lens is correctness/logic/edge-cases.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch.
- **Turn detection:** `claude` → Stop hook (auto-upgraded from `capture: none`).

### `codex-reviewer` (type: `codex`)
- **`can_talk_to: [lead]`** — reports *only* to the lead. Its lens is
  security/error-handling/tests.
- **`command: "codex --yolo"`** — placeholder launch.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.
  The `capture: none` default is **auto-upgraded to `capture: hook`** with a
  warning.

### `gemini-reviewer` (type: `gemini`)
- **`can_talk_to: [lead]`** — reports *only* to the lead. Its lens is
  design/clarity/readability/docs.
- **`command: "gemini --yolo"`** — placeholder launch.
- **Turn detection — the footgun:** `gemini` is a *pane-polling* type (`capture:
  pane` by default), **not** a hook type. The `defaults: capture: none` is
  **only** auto-upgraded for hook-capable types (claude/codex). So
  `gemini-reviewer` keeps **`capture: none`** — and the orchestrator has **no
  turn-completion signal** for it. See "The `capture=none` note" below; this is
  the single sharp edge of an otherwise clean config.

### ACL enforcement (how the spokes stay blind)

`can_talk_to` is the orchestrator's routing gate, checked in `lib/mail.py:
route_outbound` on every send. The expanded `outbox/<peer>/about.md` contact card
*is* the ACL made visible: the lead gets `outbox/claude-reviewer/`,
`outbox/codex-reviewer/`, `outbox/gemini-reviewer/`, `outbox/user/`; each reviewer
gets exactly one folder — `outbox/lead/`. If a reviewer writes a file into a
folder it isn't allowed to use, the message is moved to `failed/` and a `system`
note is dropped in the sender's inbox explaining who it *may* message — the model
self-corrects in-band. The result: the three reviewers are structurally unable to
talk to each other or to you; only the lead merges. (This is *cooperative*, not
OS isolation — see [`configuration.md`](../configuration.md).)

### The `capture=none` note (reads carefully before running)

`agentainer validate` prints three warnings and one silent asymmetry:

```
!! agent 'lead': capture: none on a claude agent … auto-upgraded to capture: hook.
!! agent 'claude-reviewer': capture: none on a claude agent … auto-upgraded to capture: hook.
!! agent 'codex-reviewer': capture: none on a codex agent … auto-upgraded to capture: hook.
```

The claude/codex agents are now correctly hook-driven. But `gemini-reviewer`
stays `capture: none` — there is **no warning** because the loader only upgrades
hook-capable types. With `capture= none`:

- The orchestrator can't tell when Gemini finishes a turn, so it classifies the
  agent **`silent-but-alive`** (logged once by the supervisor) and the automatic
  outbox sweep *does not fire*. Gemini's review would sit in
  `gemini-reviewer/outbox/lead/` and **never route back to the lead on its own.**
- **Fix it one of two ways:** (a) set `capture: pane` on `gemini-reviewer`
  (recommended — pane polling is Gemini's natural completion signal), or
  (b) leave it and manually drive the sweep with `agentainer hook gemini-reviewer`
  after Gemini finishes — that invokes the mailroom sweep (`mail.on_stop`) and
  routes its review home. (The `agentainer idle <name>` escape hatch only
  releases *queued inbound* mail; it does **not** sweep the outbox, so `hook` is
  the right tool here.)

This config ships `capture: none` deliberately to demonstrate the silent-but-
alive health probe (see §6/§10), but for a hands-off run, add
`capture: pane` to `gemini-reviewer`.

### What's *not* in this config
- **No `pings`.** The swarm is purely event-driven off real mail — it only moves
  when you send a PR. (Add a `pings:` block to `lead` if you want a stale-review
  nag.)
- **No shared `workdir`.** Each agent has its own directory, so no mailbox
  namespacing and no shared-file coordination concerns (unlike
  [`custom-workspace.md`](./custom-workspace.md)).
- **`user` availability is not set in the file.** The `user` mailbox defaults to
  **away** — the lead's final merged review is *held* (never bounced) until you
  flip yourself available (see §5).

---

## 4. Run it

From the repo root:

```bash
agentainer up -c examples/multi-provider-review.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the three `capture: none → hook`
   warnings for the claude/codex agents (and silently leaves `gemini-reviewer` at
   `capture: none` — see §3).
2. Creates the runtime dirs (`multi-provider-review-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The lead gets four
   peer folders; each reviewer gets exactly one (`outbox/lead/`). The
   `outbox/<peer>/about.md` contact card *is* the ACL made visible.
4. **Installs per-type turn detection** — the Claude Stop hook for `lead` and
   `claude-reviewer`, and the Codex `notify` hook for `codex-reviewer`.
   (`gemini-reviewer` gets none — by design / see §3.)
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'multi-provider-review' is up with 4 agent(s)
:: attach with:  tmux attach -t lead
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/multi-provider-review.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents, so it must **never** be exposed on `0.0.0.0` without a token.
See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole fan-out/merge route mail with no API keys — the mechanics are
> identical.

---

## 5. Drive a PR review

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the lead's merged review as mail (rather than
have it held), turn yourself available first:

```bash
agentainer user available -c examples/multi-provider-review.yaml
```

This rewrites the `user` contact card in the lead's `outbox/user/about.md` to
`Status: available`, so the lead sees you're reachable. (While away, mail to you
is *held* and the lead gets a `system` ack — nothing bounces.)

Now send the PR into the swarm, addressed to the lead:

```bash
agentainer send -c examples/multi-provider-review.yaml --to lead \
  "Review PR #123: git diff origin/main...origin/feature/login (or point me at repo+branch)"
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the lead, then — because the
inbox was empty — **released into `inbox/`** and the lead is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the review advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **lead receives the PR.** It reads `inbox/`, fans the *same* PR out to all
   three reviewers via `outbox/claude-reviewer/`, `outbox/codex-reviewer/`,
   `outbox/gemini-reviewer/`, then waits.
2. **the three reviewers run in parallel, blind.** Each reads its inbox, writes
   its lens-specific review into `outbox/lead/`, and finishes its turn. For the
   claude/codex reviewers the hook fires and routes their review to the lead
   automatically; for `gemini-reviewer` the sweep needs a manual `agentainer hook
   gemini-reviewer` (see §3) unless you set `capture: pane`.
3. **lead collects, dedupes, merges.** Once all three reviews are in its inbox,
   the lead groups findings by severity, tags each with its provider, and writes
   the one-line verdict + consolidated review into `outbox/user/`. On stop, that's
   delivered to your `user` mailbox.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion (modulo
the Gemini footgun).

> If you *don't* send a PR, the agents just sit in standby (that's the point of
> the standby prompt). The swarm has no periodic pings to self-start it.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, the ACL, and
the resolved capture mode:

```bash
agentainer status -c examples/multi-provider-review.yaml
```

```
swarm: multi-provider-review   root: ./multi-provider-review-workspace
  lead           (claude) up idle queue=0 unread=0 talks=claude-reviewer, codex-reviewer, gemini-reviewer, user
  claude-reviewer (claude) up idle queue=0 unread=1 talks=lead
  codex-reviewer  (codex)  up idle queue=0 unread=0 talks=lead
  gemini-reviewer (gemini) up idle queue=0 unread=0 talks=lead   capture=none
supervisor: alive
```

Note `gemini-reviewer` shows `capture=none` — that's your cue it won't
auto-route (see §3). In the event log you'll also see a one-time
`silent-but-alive` event for it.

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
agentainer logs -c examples/multi-provider-review.yaml          # whole swarm, last 20
agentainer logs -c examples/multi-provider-review.yaml -f        # follow live
agentainer logs lead -c examples/multi-provider-review.yaml      # just the lead
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
`silent-but-alive`, etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
agentainer inbox lead -c examples/multi-provider-review.yaml
```

Prints the one released message (headers + body), or `lead: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
agentainer queue lead -c examples/multi-provider-review.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
agentainer attach gemini-reviewer -c examples/multi-provider-review.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or by sending notes
into an agent's inbox.

- **Ask the lead for a sharper merge.** `agentainer send --to lead -c
  examples/multi-provider-review.yaml "Drop the nits; re-sort blockers first and
  quote the exact file:line for each."` The lead re-merges from the three reviews
  it already holds.
- **Re-task one lens.** `agentainer send --to lead … "Have codex-reviewer also
  check for missing rate-limit handling."` — the lead forwards the narrower ask to
  that one reviewer.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
agentainer down -c examples/multi-provider-review.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
agentainer up -c examples/multi-provider-review.yaml     # resume is the default
```

On `up`, Agentainer reads `multi-provider-review-workspace/.agentainer/
sessions.yaml` and reattaches the recorded conversations via each type's native
resume: `claude --resume <id>` for `lead` and `claude-reviewer`, `codex resume
<id>` for `codex-reviewer`. A resumed agent is *not* re-sent the standby prompt
(its prior context is restored).

**Gemini caveat:** `gemini` has **no resume recipe** — no session id is
recoverable from a scraped pane, so `gemini-reviewer` always starts fresh even on
a resume. That's fine here (its review is stateless), but note it won't remember
prior PR context across an `up`. Pass `--no-resume` to force everyone fresh.
Inspect what's recorded with:

```bash
agentainer sessions -c examples/multi-provider-review.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Fix Gemini's capture (recommended)
Add the natural completion signal so the review routes itself:

```yaml
  - name: gemini-reviewer
    type: gemini
    capture: pane            # pane polling — Gemini's natural turn signal
    can_talk_to: [lead]
    command: "gemini --yolo"
    role: |
      … (unchanged) …
```

### Add a fourth provider
Drop in another blind reviewer on a different model — e.g. `hermes` — give it a
*lens* distinct from the other three, add it to the lead's `can_talk_to`, and have
it `can_talk_to: [lead]`. Remember: `hermes` is pane-polling (`capture: pane`),
like Gemini — set it explicitly so it auto-routes.

### Change the lenses
The non-overlap is what stops the reviews duplicating. Keep the three (or four)
lenses disjoint and the lead's merge step will stay clean. Overlapping lenses =
redundant findings the lead has to dedupe by hand.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- Put `claude-reviewer` on `codex` (or `hermes`/`gemini`) to move the correctness
  lens onto a different model.
- Remember: `gemini`/`hermes` need `capture: pane` since they have no completion
  hook; `claude`/`codex` use a hook.

### Tune the ACL
- Only the lead lists `user` in `can_talk_to` — that's what keeps the human-facing
  surface to one funnel. To let, say, `codex-reviewer` escalate a critical
  security blocker straight to you, add `user` to its `can_talk_to` (mind that
  this widens the surface).
- The reviewers' `can_talk_to: [lead]` is what keeps them blind to each other.
  Don't add peer-to-peer links unless you *want* them to collaborate (that breaks
  the "fresh, independent read" guarantee).
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for hub-and-spoke
  routing patterns and [`multi-llm-swarm.md`](./multi-llm-swarm.md) for mixing
  model families safely. For the related single-model examples, see
  `examples/code-review.yaml` and `examples/pr-review-gate.yaml`.

---

## 10. Tips & footguns

- **The Gemini `capture=none` edge is the one to know.** `defaults: capture: none`
  auto-upgrades *only* claude/codex (hook types) to their hooks — `gemini-reviewer`
  keeps `capture: none`, gets no completion signal, and is marked `silent-but-
  alive`. Its review won't auto-route to the lead. Either set `capture: pane` on
  it, or run `agentainer hook gemini-reviewer` after it finishes to force the
  sweep. (`agentainer idle gemini-reviewer` only releases queued *inbound* mail —
  it does *not* sweep the outbox, so it won't deliver the review.)

- **Keep the lead the only `user`-facing agent.** Only the lead lists `user` in
  `can_talk_to`. If a reviewer tries to mail `user` directly, the orchestrator
  bounces it (ACL) and drops a `system` note in its inbox explaining who it *can*
  message — the model self-corrects in-band.

- **The reviewers stay blind by design.** Their `can_talk_to: [lead]` means they
  literally cannot see each other's output. That's the point — three independent
  readouts, merged by one authority. Don't add peer links casually.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch means completion never triggers and the
  agent pins "busy" forever. `status` showing an agent `busy` for a long time with
  `unread` mail is the tell. (`gemini-reviewer` with `capture=none` looks `idle`,
  not `busy` — the tell there is its review sitting undelivered in
  `gemini-reviewer/outbox/lead/`.)

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  agentainer down           -c examples/multi-provider-review.yaml
  agentainer remove-session -c examples/multi-provider-review.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches your config or the agents' source files.

- **Availability shapes the ending.** If `user` is **away** when the lead
  finishes, your merged review is *held* (with a `system` "the user is away" ack to
  the lead) rather than lost — read it later with `agentainer user inbox` or flip
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
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- [`configuration.md`](../configuration.md) — `can_talk_to`, `capture`, `defaults`.
- `examples/multi-provider-review.yaml` — the config this walkthrough is built on.
- `examples/code-review.yaml`, `examples/pr-review-gate.yaml` — related single/parallel review swarms.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14, capture §8).

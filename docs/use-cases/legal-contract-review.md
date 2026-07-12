# Use case: the legal contract review swarm

A concrete, end-to-end walkthrough of the shipped
`examples/legal-contract-review.yaml` swarm — a four-agent pipeline where a
**lead** receives a contract from the human, fans it out in parallel to three
reviewers — a **clauses** extractor, a **risk** flagger, and a **compliance**
checker — and merges their findings into a single redline summary that goes back
to the human. It's the canonical "delegate → analyze in parallel → synthesize"
loop, wired entirely through Agentainer's file-based mail model.

Everything below is based on the actual contents of
`examples/legal-contract-review.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> ⚖️ **Decision-support, not legal advice.** This swarm produces a first-pass
> redline to help a human decide what to escalate to a qualified attorney. It does
> not replace one, and nothing it emits is a legal opinion. That caveat is baked
> into the lead's role and the final summary it returns.

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The topology

```
        contract + which-side-we're-on
  user ───────────────────────────────────────▶ lead
                                                 │
                          ┌──────────────────────┼──────────────────────┐
                          ▼                      ▼                      ▼
                      clauses                 risk                compliance
                  (extract & summarize)  (flag unfavorable/   (regulatory &
                  (key clauses)            ambiguous terms)     liability exposure)
                          │                      │                      │
                          └──────────────────────┼──────────────────────┘
                                                 ▼
                                              lead merges
                                          into ONE redline
                                                 │
                                                 ▼
                                              user
```

Four agents, one fan-out-then-merge flow:

1. **`user` → `lead`** — you paste the contract (text or a path) plus context:
   which side we are on, deal value, term, anything you're worried about.
2. **`lead` → `clauses` / `risk` / `compliance`** — the lead sends the *same*
   contract text + our-side context to all three reviewers **in parallel**.
3. **`clauses` / `risk` / `compliance` → `lead`** — each returns its slice
   (extraction / risk flags / compliance exposure). They never talk to each
   other — that avoids three agents re-summarizing the same document and
   stepping on each other.
4. **`lead` → `user`** — the lead merges the three slices into one redline
   summary (bottom line + clause table + ranked red flags + compliance exposure)
   and sends it to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. An agent can only deliver to names on its own list; anything
else is bounced back as a `system` message and filed in `failed/` (see §7).

---

## 2. The config, explained

Here is `examples/legal-contract-review.yaml` in full:

```yaml
# 📄 Legal contract review -- a lead runs a first-pass review of a contract a
# human pastes in: extract the key clauses, flag unfavorable/ambiguous terms,
# check regulatory & liability exposure, then return one redline summary.
#
#   cp examples/legal-contract-review.yaml my-review.yaml
#   agentainer up   -c my-review.yaml
#   agentainer send -c my-review.yaml --to lead "Review this MSA: <paste text or a path>. We are the CUSTOMER; 12-month term."
#   agentainer down -c my-review.yaml
#
# Shape: LEAD is the hub. clauses / risk / compliance never talk to each other;
# they report only to LEAD, who fans the contract out, then merges into a redline.
# Only LEAD reaches the human.
#
# ⚖️  Decision-support, NOT legal advice.
#
# Real agents: commands launch the actual CLIs. For a key-free demo, swap each for a mock bash loop.
swarm:
  name: legal-contract-review
  root: ./legal-contract-review-workspace

defaults:
  capture: none
  can_talk_to: []

agents:
  - name: lead
    type: claude
    can_talk_to: [clauses, risk, compliance, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the LEAD CONTRACT REVIEWER. A human sends you a contract plus
      context (which side we are on, value, term, concerns). Fan the SAME
      contract text + context to clauses, risk, and compliance in parallel;
      when all three report back, merge them into ONE redline summary for the
      human (bottom line; clause table; ranked red flags; compliance exposure).
      Always end with: "Decision-support only -- not legal advice; have a
      qualified attorney review before signing." Send the summary to the user.
      MAILBOX: read inbox/, act, move to read/; to send write outbox/<name>/,
      read outbox/<name>/about.md first.

  - name: clauses
    type: claude
    can_talk_to: [lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the CLAUSE EXTRACTOR. Given the contract text and which side we are
      on, inventory the KEY clauses (type, plain-English summary, section ref);
      call out any key clause that is missing. Do not judge favorability or check
      regulations. Write the inventory to outbox/lead/.

  - name: risk
    type: claude
    can_talk_to: [lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the RISK REVIEWER. Flag terms UNFAVORABLE / ONE-SIDED / AMBIGUOUS
      against us: uncapped or asymmetric liability, broad indemnities we owe,
      auto-renewal, unilateral change/termination, vague terms, one-way
      confidentiality, overreaching IP. For each: severity, clause ref, why it
      hurts us, a concrete suggested redline. Write findings to outbox/lead/.

  - name: compliance
    type: claude
    can_talk_to: [lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the COMPLIANCE & LIABILITY REVIEWER. Assess REGULATORY and
      LIABILITY exposure (data-protection/GDPR-ish, industry duties if implied,
      liability cap & indemnity structure, insurance, audit rights, governing law
      & dispute resolution). For each: exposure, clause ref, materiality, what to
      require. Note where a specialist attorney should confirm. Write to
      outbox/lead/.
```

*(The shipped file keeps the full, detailed role text — the excerpt above is
trimmed for the page; copy the file, don't retype it.)*

Field by field:

### `swarm`
- **`name: legal-contract-review`** — the swarm's name (shows up in `status`,
  logs, sessions).
- **`root: ./legal-contract-review-workspace`** — the parent directory for the
  agents' working directories and mailboxes. Each agent gets
  `legal-contract-review-workspace/<name>/` as its workdir (created on `up`), and
  its mailbox folders live alongside. Orchestrator state goes under
  `legal-contract-review-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, and it's ultimately keyed off each
  agent's `type`. For `claude` (and `codex`), whose CLIs support a completion
  **hook**, setting `capture: none` is a footgun — so the config loader
  *upgrades* it back to `hook` and prints a warning at `up`
  (`capture: none on a claude agent gives the orchestrator no way to detect turn
  completion; using the type's default: capture: hook.`). Net effect here: all
  four agents are `claude`, so they all use their Stop hook. (If you swap one to
  `gemini`/`hermes`, those types capture via **pane polling**, which you'd set
  explicitly with `capture: pane`.)
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `lead` (type: `claude`)
- **`can_talk_to: [clauses, risk, compliance, user]`** — the lead is the hub: it
  can delegate to all three reviewers and is the **only agent that can talk to
  `user`**. Keep the human-facing surface to a single agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command, e.g. a
  shell alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no contract yet — don't send
  anything, you'll be notified"), so the lead waits for your document instead of
  proactively mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at
  `up`).

### `clauses` (type: `claude`)
- **`can_talk_to: [lead]`** — the extractor only reports up to the lead. It
  deliberately cannot reach the other reviewers or the `user`.
- **`role`** — "inventory the key clauses (type, plain-English summary, section
  reference); flag any key clause that's missing. Do not judge favorability; do
  not check regulations." Separation of concerns keeps the three reviews from
  overlapping.

### `risk` (type: `claude`)
- **`can_talk_to: [lead]`** — reports only to the lead.
- **`role`** — "flag terms unfavorable/one-sided/ambiguous *against us*: uncapped
  liability, broad indemnities we owe, auto-renewal, unilateral
  change/termination, vague terms, one-way confidentiality, overreaching IP." For
  each finding: severity, clause ref, why it hurts us, a concrete suggested
  redline.

### `compliance` (type: `claude`)
- **`can_talk_to: [lead]`** — reports only to the lead.
- **`role`** — "assess regulatory & liability exposure: data-protection
  (GDPR/CCPA-style), industry-specific duties if implied, the liability-cap and
  indemnity structure, insurance, audit rights, governing law & dispute
  resolution." For each: the exposure, the clause reference, materiality, and
  what to require; note where a specialist attorney should confirm.

### What's *not* in this config
- **No `periodically_ping_seconds`.** None of the four agents has a periodic ping
  configured, so no agent is auto-nudged on a timer while idle — the pipeline is
  purely event-driven off real mail. (If you wanted the lead to poke a slow
  reviewer, you'd add `periodically_ping_seconds: 300` to the `lead`.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **The three reviewers are intentionally non-communicating.** They all receive
  the same contract from the lead and all report back to the lead; none can
  message another. That keeps a single source of truth (the lead) for what the
  contract says, so the merged redline doesn't contain three divergent
  re-readings.

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/legal-contract-review.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for all four `claude` agents).
2. Creates the runtime dirs
   (`legal-contract-review-workspace/.agentainer/…`: log, queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The lead gets
   `outbox/clauses/`, `outbox/risk/`, `outbox/compliance/`, `outbox/user/`; each
   reviewer gets `outbox/lead/`.
4. **Installs per-type turn detection** — the Claude Stop hook for every agent
   (all four are `claude`).
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'legal-contract-review' is up with 4 agent(s)
:: attach with:  tmux attach -t <lead-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/legal-contract-review.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only bind — `127.0.0.1` is the default, never `0.0.0.0`, per the
control-plane UI rules. See the `README.md` "control-plane UI" section.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole fan-out-merge route mail with no API keys — the mechanics are
> identical.

---

## 4. Drive a review

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the lead's final redline as mail (rather than
have it held), turn yourself available first:

```bash
./agentainer user available -c examples/legal-contract-review.yaml
```

This rewrites the `user` contact card in the lead's `outbox/user/about.md` to
`Status: available`, so the lead sees you're reachable. (While away, mail to you
is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the contract into the swarm, addressed to the lead. Paste the text
directly, or hand it a path — and always state **which side we are on**, because
the reviewers judge everything relative to that:

```bash
./agentainer send --to lead "Review this Master Services Agreement (full text below). We are the CUSTOMER. 12-month initial term, ~$240k, auto-renews. We're worried about the indemnity and the data-processing addendum. <paste contract text, or: see /path/to/msa.txt>"
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the lead, then — because the
inbox was empty — **released into `inbox/`** and the lead is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§5), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **lead receives the contract.** It reads `inbox/`, restates which side we're
   on to you briefly, then writes the *same* contract text + context into
   `outbox/clauses/`, `outbox/risk/`, and `outbox/compliance/` — three messages
   released as each reviewer becomes free.
2. **the three reviewers analyze in parallel.** Each reads its inbox, does its
   slice (extract / flag / check), and writes its findings into
   `outbox/lead/`. On stop, each routes back to the lead.
3. **lead merges.** It reads all three findings, de-duplicates overlapping items,
   ranks the red-flag terms by severity, assembles the clause table and
   compliance summary, appends the standing "decision-support, not legal advice"
   note, and writes the redline into `outbox/user/`. On stop, that's delivered to
   your `user` mailbox (you'll see it with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a contract, the agents just sit in standby (that's the
> point of the standby prompt). The pipeline only moves when real mail arrives —
> this swarm has no periodic pings to self-start it.

---

## 5. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/legal-contract-review.yaml
```

```
swarm: legal-contract-review   root: ./legal-contract-review-workspace
  lead (claude) up idle queue=0 unread=0 talks=clauses, risk, compliance, user
  clauses (claude) up idle queue=0 unread=1 talks=lead
  risk (claude) up idle queue=0 unread=1 talks=lead
  compliance (claude) up idle queue=0 unread=1 talks=lead
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/legal-contract-review.yaml          # whole swarm, last 20
./agentainer logs -c examples/legal-contract-review.yaml -f        # follow live
./agentainer logs compliance -c examples/legal-contract-review.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox clauses -c examples/legal-contract-review.yaml
```

Prints the one released message (headers + body), or `clauses: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue lead -c examples/legal-contract-review.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach lead -c examples/legal-contract-review.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom
— handy for un-sticking an agent, but the mail model is the normal path.)

---

## 6. Resume after a stop

Tear the swarm down when you're done:

```bash
./agentainer down -c examples/legal-contract-review.yaml
```

Bring it back later and **conversations resume by default**:

```bash
./agentainer up -c examples/legal-contract-review.yaml     # resume is the default
```

On `up`, Agentainer reads
`legal-contract-review-workspace/.agentainer/sessions.yaml` (written as each
agent finished its first turn) and reattaches the recorded conversations via
`claude --resume <id>` for each `claude` agent. A resumed agent is *not* re-sent
the standby prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/legal-contract-review.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 7. Tips & footguns

- **Keep the lead the only `user`-facing agent.** In this config only the lead
  lists `user` in `can_talk_to`. That gives you a single point of contact and a
  clean funnel: raw clause/risk/compliance notes always pass through the lead's
  merge before they reach you. If a reviewer tries to mail `user` directly, the
  orchestrator bounces it (ACL) and drops a `system` note in that reviewer's
  inbox explaining who it *can* message — the model self-corrects in-band.

- **Always state which side you're on when you `send`.** The entire review is
  relative to that — "auto-renewal with a 60-day notice" is a trap if we're the
  customer and a win if we're the vendor. The lead's role re-states it to the
  reviewers, but you should put it in your `send` so it's in the original message
  they all receive.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch (e.g. a `claude` agent whose `command`
  doesn't launch Claude) means completion never triggers and the agent pins
  "busy" forever. `status` showing an agent `busy` for a long time with `unread`
  mail is the tell.

- **It's decision-support, not a lawyer.** The lead's role ends every redline
  with the "not legal advice — have a qualified attorney review" note, and you
  should treat the output as triage: a ranked list of what to escalate, not a
  substitute for counsel. The compliance agent is explicitly told to flag where
  a specialist should confirm.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived
  so the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s)
  to kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down                  -c examples/legal-contract-review.yaml
  ./agentainer remove-session       -c examples/legal-contract-review.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

- **Availability shapes the ending.** If `user` is **away** when the lead
  finishes, your redline is *held* (with a `system` "the user is away" ack to the
  lead) rather than lost — read it later with `agentainer user inbox` or flip
  yourself available and it's delivered.

---

## 8. Customize

- **Add a `negotiator`.** Drop in a fifth agent that only the lead can talk to and
  that can reply to the lead (and, if you want it customer-facing, `user`):
  ```yaml
  - name: negotiator
    type: claude
    can_talk_to: [lead, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the NEGOTIATOR. Given the lead's redline, draft the specific
      proposed edits / fallback positions to send back to the counterparty, and
      the talking points for each. Report drafts to the lead; when the lead
      approves, you may send the final position to the user.
  ```
  Then extend `lead`'s `can_talk_to` to include `negotiator`. Now the lead can
  hand approved redlines to a dedicated drafting agent instead of writing
  counter-proposals itself.

- **Swap models per role.** The four are all `claude` here, but you can mix and
  match the four supported CLIs — `claude`, `codex`, `gemini`, `hermes` — as long
  as each `command` launches the CLI its `type` implies (a mismatch silently
  deadlocks the agent). For example, make `compliance` a `codex` agent with
  `command: "codex --yolo"` and `capture: hook`, or a `gemini` agent with
  `command: "gemini --yolo"` and `capture: pane`. See the
  [multi-LLM swarm](./multi-llm-swarm.md) use case for why you might split work
  across models.

- **Tune the ACL.** The current graph is a strict hub: reviewers can't see each
  other. If you'd rather have `risk` and `compliance` cross-check each other,
  add `compliance` to `risk`'s `can_talk_to` (and vice versa) — but then the lead
  is no longer the single source of truth for "what the contract says," so
  expect some re-summarization. For the general pattern, see
  [delegation-pipeline.md](./delegation-pipeline.md).

- **Long contracts / many documents.** For very large contracts, point the lead
  at a file path instead of pasting, and have each reviewer read the path from
  its workdir. The mail model only carries short instructions; the heavy
  document stays on disk.

- **Periodic pings.** If a reviewer tends to run long, add
  `periodically_ping_seconds: 300` to the `lead` so it nudges slow reviewers
  automatically instead of waiting silently.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — picking conversations
  back up after a reboot.
- [delegation-pipeline.md](./delegation-pipeline.md) — the hub-and-spoke pattern
  this swarm is built on.
- [multi-llm-swarm.md](./multi-llm-swarm.md) — splitting a pipeline across the
  four supported CLIs.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

# Use case: Literature review

A concrete, end-to-end walkthrough of the shipped
`examples/literature-review.yaml` swarm — a five-agent hub-and-spoke that turns
a human's research question into a cited, conflict-aware synthesis. A
**review-lead** takes the question and fans it out to two **searchers** (who find
and summarize papers with every claim tagged to a source), a **synthesizer** (who
weaves the findings into one narrative and surfaces agreements, conflicts, and
gaps), and a **citation-mapper** (who builds the citation graph and audits that
no claim is unsourced). The lead delivers the finished review back to you. The
enforced discipline everywhere: **no claim without a source, and conflicts are
surfaced — never buried.**

Everything below is based on the actual contents of
`examples/literature-review.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Researchers, research-ops engineers, medical/clinical evidence reviewers, and
anyone who needs a defensible literature synthesis across many papers without
doing every search, read, and cross-check themselves. The swarm encodes the
discipline that makes a review trustworthy — a single owner of the question, two
independent searchers covering disjoint slices of the search space, a synthesizer
whose job is honesty about disagreement, and an auditor whose only job is to
prove every claim traces to a named source.

It is deliberately a **hub-and-spoke**, not a free-for-all: every question and
every deliverable passes through the review-lead, so the final review has exactly
one authority and the human-facing surface is a single agent. Swapping in a real
`prism`/`critic` agent (see §7) is a one-line config change.

---

## 2. The topology

```
          you (user)
              │  research question
              ▼
          review-lead  ───────┬──────────────┬──────────────┐
          (the hub)           ▼              ▼              ▼
              ▲          searcher_a     synthesizer   citation-mapper
              │  (brief)  │             │             │
              │           ▼             ▼             ▼
              └───────────────────────────────────────────┘
                  (all findings return only to review-lead)
```

Five agents, one directed flow:

1. **`user` → `review-lead`** — you send the research question.
2. **`review-lead` → `searcher_a` / `searcher_b`** — the lead decomposes the
   question into bounded search themes and delegates one theme at a time.
3. **`searcher_a` / `searcher_b` → `review-lead`** — each returns sourced
   summaries (claim + DOI/arXiv ID). They never talk to each other or to the
   synthesizer directly.
4. **`review-lead` → `synthesizer`** (with the compiled summaries) — the lead
   bundles the sourced findings and hands them to the synthesizer.
5. **`synthesizer` → `review-lead`** — the integrated narrative, with agreements,
   conflicts, and gaps labelled.
6. **`review-lead` → `citation-mapper`** (with the narrative + underlying
   summaries) — the auditor builds the citation graph and flags any unsourced
   claim back to the lead.
7. **`review-lead` → `user`** — once the audit passes, the lead delivers the
   finished review (answer, conflicts, gaps, citation list) to you.

The routing above is *enforced* by each agent's `can_talk_to` list. An agent can
only deliver to names on its own list; anything else is bounced back as a
`system` message and filed in `failed/` (see §7). Notably, the two searchers, the
synthesizer, and the citation-mapper **never** talk to `user` directly — only the
review-lead does.

---

## 3. The config, explained

Here is `examples/literature-review.yaml` in full:

```yaml
swarm:
  name: literature-review
  root: ./literature-review-workspace

defaults:
  capture: none              # claude/codex auto-upgrade to their hook at `up`
  can_talk_to: []            # tightened per agent below

agents:
  - name: review-lead
    type: claude
    can_talk_to: [searcher_a, searcher_b, synthesizer, citation-mapper, user]
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: |
          Weekly literature-review checkpoint: if an open question from the human
          is still in flight, send each outstanding spoke its next brief, collect
          what has come back, and report a one-paragraph status to the user. If
          you are mid-synthesis, queue this and run it when you are free.
        cron: "0 9 * * mon"          # 09:00 every Monday (host local time)
        when_busy: queue
    role: |
      You are the REVIEW-LEAD and the hub of a scientific literature-review team.
      ... (decompose the question, delegate one theme per spoke, assemble the
      final review that surfaces conflicts and gaps, never let an unsourced claim
      through) ...

  - name: searcher_a
    type: codex
    can_talk_to: [review-lead]
    command: "codex --yolo"
    role: |
      You are SEARCHER A, a systematic-paper finder and summarizer. ... (find
      papers for one theme, cite every claim to its source, mark unsourceable
      claims "[UNSOURCED]") ...

  - name: searcher_b
    type: codex
    can_talk_to: [review-lead]
    command: "codex --yolo"
    role: |
      You are SEARCHER B ... covering a DIFFERENT slice of the search space than
      searcher_a. ... (same disciplined sourcing discipline) ...

  - name: synthesizer
    type: gemini
    can_talk_to: [review-lead]
    capture: pane
    command: "gemini --yolo"
    role: |
      You are the SYNTHESIZER. ... (weave the sourced summaries into one narrative,
      explicitly label AGREE / CONFLICT / UNKNOWN, reproduce source tags inline) ...

  - name: citation-mapper
    type: claude
    can_talk_to: [review-lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the CITATION-MAPPER and source-auditor. ... (build the citation
      graph, audit every claim for a named source, flag "[NEEDS SOURCE]" back to
      the lead — do not fix the narrative yourself) ...
```

Field by field:

### `swarm`
- **`name: literature-review`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./literature-review-workspace`** — the parent directory for the
  agents' working directories and mailboxes. Each agent's workdir defaults to
  `literature-review-workspace/<name>` (all five are **private** — no shared
  workdir in this swarm, so no mailbox namespacing is needed). Orchestrator
  state goes under `literature-review-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — a floor value; the loader *auto-upgrades* `claude` and
  `codex` agents to their natural completion hook at `up` (see per-agent turn
  detection). `gemini` has no completion hook, so the synthesizer must set
  `capture: pane` explicitly (it does, below).
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `review-lead` (type: `claude`)
- **`can_talk_to: [searcher_a, searcher_b, synthesizer, citation-mapper, user]`**
  — the lead is the hub: it delegates to the four spokes and is the **only agent
  that can talk to `user`**. That last part matters — keep the human-facing
  surface to a single agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`pings`** — a single weekly checkpoint. The `cron: "0 9 * * mon"` fires a
  progress-nudge every Monday at 09:00 host local time; `when_busy: queue` means
  the nudge is **held in the lead's queue** if it's mid-synthesis and released
  only when the lead is free (so it never interrupts a live turn). See §5.
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the lead waits for your question instead of
  proactively mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `searcher_a` / `searcher_b` (type: `codex`)
- **`can_talk_to: [review-lead]`** — each searcher only reports back to the lead.
  They cannot reach each other, the synthesizer, the citation-mapper, or the
  `user`; findings are attributable because every brief goes through one place.
- **`command: "codex --yolo"`** — placeholder launch command. The two searchers
  cover *disjoint* slices of the search space (the role text says so
  explicitly), so they don't duplicate coverage.
- **`role`** — "find papers for one theme, summarize each as claim + evidence +
  stated limitations, cite every claim to its source (title/authors/year/DOI or
  arXiv ID), and mark anything you can't source as `[UNSOURCED]`." The unbearable
  discipline of the swarm lives in this instruction — the synthesizer and
  citation-mapper downstream depend on it.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `synthesizer` (type: `gemini`)
- **`can_talk_to: [review-lead]`** — the synthesizer reports the integrated
  narrative only to the lead; it can't reach the searchers or `user` directly.
- **`capture: pane`** — **explicit**, because `gemini` has no completion hook.
  The orchestrator detects turn-completion by **polling the pane** for a settled
  state. (This is exactly why `defaults.capture: none` auto-upgrades only
  `claude`/`codex`, not `gemini`.)
- **`command: "gemini --yolo"`** — placeholder launch command.
- **`role`** — "weave the sourced summaries into one narrative that answers the
  question; explicitly label AGREE / CONFLICT / UNKNOWN; reproduce the source tags
  inline; do not invent consensus."
- **Turn detection:** `gemini` → **pane polling**.

### `citation-mapper` (type: `claude`)
- **`can_talk_to: [review-lead]`** — the auditor reports the graph + the
  `[NEEDS SOURCE]` list only to the lead; it never touches the narrative or
  talks to the `user`.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **`role`** — "build the citation/relationship graph; audit every claim for a
  named, real source; flag unsourced or vaguely-attributed claims back to the
  lead with the exact sentence. Verification only — don't fix it."
- **Turn detection:** `claude` → Stop hook.

### The ACL enforcement note (important)

`can_talk_to` is *cooperative, not OS isolation* (Decision D15). Agents have
filesystem access and *could* write straight into another inbox, bypassing
`outbox/`. For well-behaved agents it's enforced: the orchestrator only ever
*releases* a message into an inbox for a recipient on the sender's list, and the
`outbox/<peer>/about.md` contact card is the ACL made visible — `searcher_a` only
gets `outbox/review-lead/`; the lead gets `outbox/searcher_a/`, `outbox/searcher_b/`,
`outbox/synthesizer/`, `outbox/citation-mapper/`, `outbox/user/`. If a spoke tries
to mail outside its list, the orchestrator **bounces** it as a `system` message
and files the original in `failed/`. Documented honestly; it's a routing
convention, not a security boundary. See [`delegation-pipeline.md`](./delegation-pipeline.md).

### What's *not* in this config
- **No shared workdirs.** All five agents have distinct, private directories, so
  no mailbox namespacing is needed. (If you added a `critic` that shares a
  workspace with the synthesizer, see [`custom-workspace.md`](./custom-workspace.md)
  for the namespacing that would kick in.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **One ping, not a fleet.** Only the review-lead has a `pings:` entry; the
  spokes are purely event-driven off the lead's briefs.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/literature-review.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings.
2. Creates the runtime dirs (`literature-review-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/about.md`
   contact card *is* the ACL made visible: the lead gets five `outbox/<peer>/`
   folders; each spoke gets exactly one (`outbox/review-lead/`).
4. **Installs per-type turn detection** — the Claude Stop hook for `review-lead`
   and `citation-mapper`, the Codex `notify` hook for `searcher_a`/`searcher_b`,
   and **pane polling** for the `gemini` `synthesizer` (no hook to install).
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'literature-review' is up with 5 agent(s)
:: attach with:  tmux attach -t <review-lead-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/literature-review.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents, so it must **never** be exposed on `0.0.0.0` without a token.
See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole fan-out route mail with no API keys — the mechanics are identical.

---

## 5. Drive the question

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the lead's finished review as mail (rather than
have it held), turn yourself available first:

```bash
./agentainer user available -c examples/literature-review.yaml
```

This rewrites the `user` contact card in the lead's `outbox/user/about.md` to
`Status: available`, so the lead sees you're reachable. (While away, mail to you
is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the research question into the swarm, addressed to the review-lead:

```bash
./agentainer send --to review-lead -c examples/literature-review.yaml \
  "Review the evidence on [topic]. What does the literature say, where do studies \
   conflict, and what is still unknown?"
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the lead, then — because the
inbox was empty — **released into `inbox/`** and the lead is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the review advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **review-lead receives the question.** It reads `inbox/`, decomposes it into
   bounded themes, and writes one delegation into `outbox/searcher_a/` and one
   into `outbox/searcher_b/`. On each stop, those route to the searchers.
2. **searchers return sourced summaries.** Each reads its inbox, writes claim+source
   summaries, and reports back into `outbox/review-lead/`. On stop, both route to
   the lead.
3. **review-lead briefs the synthesizer.** It bundles the compiled summaries into
   `outbox/synthesizer/`. On stop, that routes to the synthesizer.
4. **synthesizer integrates.** It reads its inbox, weaves the narrative (labelling
   AGREE/CONFLICT/UNKNOWN), and reports back into `outbox/review-lead/`. Because
   `gemini` uses pane polling, the orchestrator detects its stop by watching the
   pane settle.
5. **review-lead briefs the citation-mapper.** It hands over the narrative + the
   underlying summaries into `outbox/citation-mapper/`. On stop, that routes.
6. **citation-mapper audits.** It builds the citation graph and writes any
   `[NEEDS SOURCE]` flags into `outbox/review-lead/`. On stop, that routes to the
   lead — who may re-brief the synthesizer on the gap, then finalize.
7. **review-lead finalizes.** It assembles the answer + conflicts + gaps +
   citation list and writes it into `outbox/user/`. On stop, that's delivered to
   your `user` mailbox (visible with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

### The weekly ping

The review-lead has a `pings:` entry: every Monday at 09:00 host time, a
checkpoint message is enqueued. Because it's `when_busy: queue`, if the lead is
mid-turn the nudge waits in its queue and is released only when the lead goes
idle — so the ping never interrupts live synthesis, but it keeps a stalled review
moving by prompting the next set of briefs and a one-paragraph status to `user`.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/literature-review.yaml
```

```
swarm: literature-review   root: ./literature-review-workspace
  review-lead   (claude) up idle queue=0 unread=0 talks=searcher_a, searcher_b, synthesizer, citation-mapper, user
  searcher_a    (codex)  up idle queue=0 unread=0 talks=review-lead
  searcher_b    (codex)  up idle queue=0 unread=0 talks=review-lead
  synthesizer   (gemini) up idle queue=0 unread=0 talks=review-lead
  citation-mapper (claude) up idle queue=0 unread=0 talks=review-lead
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/literature-review.yaml          # whole swarm, last 20
./agentainer logs -c examples/literature-review.yaml -f        # follow live
./agentainer logs synthesizer -c examples/literature-review.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
`ping` (for the Monday checkpoint), etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox review-lead -c examples/literature-review.yaml
```

Prints the one released message (headers + body), or `review-lead: inbox is empty`.

**Queue depth** — mail waiting behind the one released message (e.g. a held ping):

```bash
./agentainer queue review-lead -c examples/literature-review.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach searcher_a -c examples/literature-review.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or by sending notes
into an agent's inbox.

- **Send a narrowing to the lead.** Realized the question is too broad?
  `./agentainer send --to review-lead -c examples/literature-review.yaml "Focus
  searcher_a on randomized trials only; searcher_b on observational cohorts."`
  The lead re-briefs the spokes.
- **Ask the citation-mapper for the evidence.** `./agentainer send --to review-lead
  ... "Have the citation-mapper list every [NEEDS SOURCE] flag with its sentence."`
  — the lead forwards it.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/literature-review.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/literature-review.yaml     # resume is the default
```

On `up`, Agentainer reads `literature-review-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the
review-lead and citation-mapper, `codex resume <id>` for the two searchers, and
`gemini`'s resume for the synthesizer. A resumed agent is *not* re-sent the
standby prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/literature-review.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a `critic` / `prism` agent
You may want a second opinion on the synthesis. Add a sixth agent that can read
the lead's deliverable and owns dissent:

```yaml
  - name: critic
    type: claude
    can_talk_to: [review-lead, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the REVIEW CRITIC. Once the lead delivers a draft review, attack its
      weakest inference, name the studies that would change the conclusion, and
      report the dissent to outbox/review-lead/ and, if severe, outbox/user/. You
      never write the review itself.
```
Then add `critic` to the lead's `can_talk_to` so it can be briefed. Mind that
adding `user` to its list widens the human-facing surface (see Tips).

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `searcher_a: type: gemini` (with `capture: pane`) to put one search on a
  different model than `searcher_b`.
- `citation-mapper: type: codex` if you want the audit on Codex.
- Every `gemini`/`hermes` agent **must** set `capture: pane` (pane polling) since
  they have no completion hook — the synthesizer already does this.

### Tune the ACL
- To let the `synthesizer` escalate a conflict straight to `user` (not only via the
  lead), add `user` to its `can_talk_to`. Mind that this widens the human-facing
  surface; the doc's convention keeps the lead the sole `user` contact.
- To keep the searchers strictly isolated from each other (already the case here),
  leave their `can_talk_to: [review-lead]` — that's the one-place-owns-the-question
  guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md)
  for mixing model families safely (this swarm already mixes claude/codex/gemini).

### Adjust the ping
The weekly checkpoint is a starting point. Change `cron: "0 9 * * mon"` to any
valid cron expression, or set `when_busy: drop` instead of `queue` if you'd
rather skip a checkpoint that lands during a live turn than defer it.

---

## 10. Tips & footguns

- **Keep the lead the only `user`-facing agent.** Only the lead lists `user` in
  `can_talk_to`. That gives you a single funnel: raw search drafts and the
  synthesizer's narrative always pass through review before they reach you. If a
  spoke tries to mail `user` directly, the orchestrator bounces it (ACL) and drops
  a `system` note in its inbox explaining who it *can* message — the model
  self-corrects in-band.

- **The sourcing discipline is the product.** The whole "no claim without a
  source" guarantee rests on the searchers' role text (`[UNSOURCED]`) and the
  citation-mapper's audit (`[NEEDS SOURCE]`). If you lighten either role, the
  downstream auditor has nothing to verify. Treat those two instructions as the
  load-bearing part of the config, not flavor text.

- **The `gemini` synthesizer must use pane polling.** Unlike `claude` (Stop hook)
  and `codex` (`notify` hook), `gemini` has no completion signal, so `capture: pane`
  is **required** for its turn to ever be detected. Forgetting it pins the
  synthesizer "busy" forever — `status` showing `synthesizer busy` with unread
  mail is the tell. `claude`/`codex` are fine with `defaults.capture: none` because
  the loader auto-upgrades them.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch (e.g. a `codex` agent whose `command`
  doesn't launch Codex) means completion never triggers and the agent pins
  "busy" forever.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down             -c examples/literature-review.yaml
  ./agentainer remove-session   -c examples/literature-review.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

- **Availability shapes the ending.** If `user` is **away** when the lead
  finishes, your review is *held* (with a `system` "the user is away" ack to the
  lead) rather than lost — read it later with `agentainer user inbox` or flip
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
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely (this swarm uses claude/codex/gemini together).
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing (not needed here, all private).
- `examples/literature-review.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

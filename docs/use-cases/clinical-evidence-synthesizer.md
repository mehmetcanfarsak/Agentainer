# Use case: Clinical evidence synthesizer

A concrete, end-to-end walkthrough of the shipped
`examples/clinical-evidence-synthesizer.yaml` swarm — a four-agent mini
systematic-review team that turns a PICO therapy question (Population,
Intervention, Comparator, Outcome) into an honest evidence brief. A **synthesis
lead** takes the question and delegates to an **extractor** (trial-level data),
a **grader** (risk-of-bias / GRADE certainty), and a **writer** that assembles a
brief whose headline is a strength-of-evidence call, not a sales pitch. The lead
delivers the finished brief back to you and is the only agent that talks to you.

Everything below is based on the actual contents of
`examples/clinical-evidence-synthesizer.yaml` and the shipped CLI (`lib/cli.py`)
and mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics;
to run it *for real* you supply the coding-CLI commands (or swap them for mock
bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Clinicians, medical writers, systematic reviewers, and anyone who needs a
structured read on "does intervention X help condition Y, and how sure are we?"
— without doing the extraction, grading, and drafting themselves. The swarm
encodes the discipline that keeps an evidence brief honest: extraction and
methodological grading are **separate** agents (so one model can't quietly
upgrade its own certainty), and a writer is instructed, by role, to flag weak and
conflicting evidence prominently rather than launder it.

It is deliberately a **hub-and-spoke**, not a free-for-all: every request and
every deliverable passes through the synthesis lead, so there is exactly one
authority that can overstate a result to you — and that authority is instructed
never to. Swapping in a real `librarian`/`search` agent (see §7) is a one-line
config change.

---

## 2. The topology

```
          user
            │
     synthesis-lead                  (the hub: talks to extractor, grader, writer, user)
      /       |       \
  extractor  grader   writer
   (codex)   (gemini)  (claude)
     │          │         │
     └──────────┴─────────┘   (all report only to synthesis-lead)
```

Four agents, one directed flow:

1. **`user` → `synthesis-lead`** — you send a PICO question.
2. **`synthesis-lead` → `extractor`** — the lead asks for trial-level data
   (design, sample sizes, effect sizes with CIs, bias signals) pulled from the
   literature the extractor can reach.
3. **`synthesis-lead` → `grader`** — in parallel, the lead asks for a
   risk-of-bias / GRADE certainty assessment of the same study set.
4. **`extractor` / `grader` → `synthesis-lead`** — both report back only to the
   lead; they cannot see each other or you.
5. **`synthesis-lead` → `writer`** — once both reports are in, the lead forwards
   the extracted data + quality assessment and asks for the assembled brief.
6. **`writer` → `synthesis-lead`** — the writer returns the brief (with explicit
   strength-of-evidence callouts).
7. **`synthesis-lead` → `user`** — the lead reviews for overstatement, then
   delivers the final brief to you.

The routing above is *enforced* by each agent's `can_talk_to` list. An agent can
only deliver to names on its own list; anything else is bounced back as a
`system` message and filed in `failed/` (see §7). Notably, `extractor`, `grader`,
and `writer` **never** talk to `user` directly — only the lead does — and they
never talk to each other.

---

## 3. The config, explained

Here is `examples/clinical-evidence-synthesizer.yaml` in full:

```yaml
swarm:
  name: clinical-evidence-synthesizer
  root: ./clinical-evidence-synthesizer-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: synthesis-lead
    type: claude
    can_talk_to: [extractor, grader, writer, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the SYNTHESIS LEAD for a clinical evidence team. ... (run the
      hub: take the PICO question, delegate to extractor + grader in parallel,
      collect both, brief the writer, review for overstatement, present to user)
      ...

  - name: extractor
    type: codex
    can_talk_to: [synthesis-lead]
    command: "codex --yolo"
    role: |
      You are the EXTRACTOR, a clinical-trials data specialist. ... (report each
      study's design, sample sizes per arm, effect size + CI, p values, follow-up,
      explicit bias signals; source every number; never invent statistics) ...

  - name: grader
    type: gemini
    can_talk_to: [synthesis-lead]
    command: "gemini --yolo"
    role: |
      You are the GRADER, a methodological quality assessor. ... (Cochrane RoB /
      ROBINS-I per study, heterogeneity, GRADE-style certainty: high / moderate /
      low / very low; be conservative, never upgrade beyond the methods) ...

  - name: writer
    type: claude
    can_talk_to: [synthesis-lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the WRITER. ... (assemble the brief: PICO question, included
      studies, findings per outcome with effect sizes + CIs, quality assessment,
      bottom line, STRENGTH OF EVIDENCE rating, explicit "where the evidence is
      weak" callouts; never overstate) ...
```

Field by field:

### `swarm`
- **`name: clinical-evidence-synthesizer`** — the swarm's name (shows up in
  `status`, logs, sessions).
- **`root: ./clinical-evidence-synthesizer-workspace`** — the parent directory
  for the agents' working directories and mailboxes. Each agent's workdir
  defaults to `…-workspace/<name>` (e.g. `…-workspace/extractor`). Orchestrator
  state goes under `…-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default capture mode is "do not capture at all."
  Each agent below is auto-handled by the loader: `claude`/`codex` agents with
  `capture: none` are **auto-upgraded to `hook`** (see per-type turn detection
  below). The `gemini` grader stays at `none` and relies on the supervisor's
  silent-but-alive probe (see the footgun in §10).
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent
  below states its own list explicitly, so this default is just a safe floor.

### `synthesis-lead` (type: `claude`)
- **`can_talk_to: [extractor, grader, writer, user]`** — the lead is the hub: it
  delegates to the three specialists and is the **only agent that can talk to
  `user`**. That last part matters — keep the human-facing surface to a single
  agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command, e.g. a
  shell alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the lead waits for your PICO question instead of
  proactively mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`;
  the `capture: none` default is auto-upgraded to `hook`).

### `extractor` (type: `codex`)
- **`can_talk_to: [synthesis-lead]`** — the extractor only reports back to the
  lead. It deliberately cannot reach the grader, the writer, or the `user`.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "extract structured trial-level data from the literature you can
  access; source every number; never invent statistics."
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`;
  the `capture: none` default is auto-upgraded to `hook`.

### `grader` (type: `gemini`)
- **`can_talk_to: [synthesis-lead]`** — the grader only reports its certainty
  assessment back to the lead; it cannot see the other agents or you.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **`role`** — "assign a risk-of-bias rating per study, flag heterogeneity, and
  state overall GRADE certainty (high / moderate / low / very low); be
  conservative."
- **Turn detection:** `gemini` has **no completion hook**; its natural capture is
  **pane polling**, but here `capture: none` leaves it with no automatic
  signal — the supervisor's "silent-but-alive" health probe + stale-busy timeout
  recover routing (see §10). For immediate detection, set `capture: pane` on this
  agent.

### `writer` (type: `claude`)
- **`can_talk_to: [synthesis-lead]`** — the writer only reports the brief back to
  the lead; it cannot reach the `user` or the other specialists.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **`role`** — "assemble the evidence brief; structure it as PICO → studies →
  findings per outcome → quality assessment → bottom line → STRENGTH OF EVIDENCE
  → weak-evidence callouts; never overstate."
- **Turn detection:** `claude` → Stop hook (the `capture: none` default is
  auto-upgraded to `hook`).

### ACL enforcement

Every agent's `can_talk_to` is the **only** thing that limits where its mail is
delivered. When the lead writes into `outbox/extractor/`, `outbox/grader/`, or
`outbox/writer/`, the orchestrator accepts it; when the extractor, grader, or
writer write anywhere *but* `outbox/synthesis-lead/`, the orchestrator **bounces**
the message as a `system` note in the sender's inbox explaining who they *can*
message. This is cooperative, not OS isolation (agents share filesystem access —
see `ProjectPlan.md` / the footguns below), and it is made visible to the model
through the `outbox/<peer>/about.md` contact cards. The practical guarantee: only
`synthesis-lead` can ever put mail in *your* `user` inbox.

### What's *not* in this config
- **No `pings`.** The swarm is purely event-driven off real mail — it only moves
  when you send a PICO question. (Add a `pings:` schedule on `synthesis-lead` if
  you want a "stale review" nag.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **No shared workdir.** All four agents get distinct working directories, so no
  mailbox namespacing is needed (unlike a shared-repo swarm). See
  [`custom-workspace.md`](./custom-workspace.md).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/clinical-evidence-synthesizer.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (here, the `capture: none`
   → `hook` auto-upgrades for the two `claude` and the one `codex` agent).
2. Creates the runtime dirs
   (`clinical-evidence-synthesizer-workspace/.agentainer/…`: log, queue, run,
   sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/
   about.md` contact card *is* the ACL made visible: the lead gets
   `outbox/extractor/`, `outbox/grader/`, `outbox/writer/`, `outbox/user/`; the
   extractor gets only `outbox/synthesis-lead/`; etc.
4. **Installs per-type turn detection** — the Claude Stop hook for `synthesis-lead`
   and `writer`, and the Codex `notify` hook for `extractor`. (The `gemini` grader
   has no hook; the supervisor covers it — see §10.)
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'clinical-evidence-synthesizer' is up with 4 agent(s)
:: attach with:  tmux attach -t <synthesis-lead-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/clinical-evidence-synthesizer.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents, so it must **never** be exposed on `0.0.0.0` without a token.
See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole review route mail with no API keys — the mechanics are identical.

---

## 5. Drive a PICO question

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the lead's finished brief as mail (rather than
have it held), turn yourself available first:

```bash
./agentainer user available -c examples/clinical-evidence-synthesizer.yaml
```

This rewrites the `user` contact card in the lead's `outbox/user/about.md` to
`Status: available`, so the lead sees you're reachable. (While away, mail to you
is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the PICO question into the swarm, addressed to the lead:

```bash
./agentainer send --to synthesis-lead -c examples/clinical-evidence-synthesizer.yaml \
  "For adults with treatment-resistant depression (P), does adjunctive
   augmentation with aripiprazole (I) compared with a different
   second-line antidepressant (C) improve remission at 12 weeks (O)?"
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the lead, then — because the
inbox was empty — **released into `inbox/`** and the lead is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the review advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **lead receives the question.** It reads `inbox/`, splits the work, and writes
   two delegations — extraction into `outbox/extractor/`, grading into
   `outbox/grader/`. On stop, both route in parallel to the specialists.
2. **extractor pulls trial data.** It reads its inbox, reports structured
   per-study data (design, arms, effect sizes + CIs, bias signals), and writes
   back into `outbox/synthesis-lead/`. On stop, that routes to the lead.
3. **grader assesses certainty.** It reads its inbox, assigns RoB ratings and a
   GRADE certainty, and writes back into `outbox/synthesis-lead/`. (See §10 for
   how its completion is detected.) On resolution, that routes to the lead.
4. **lead briefs the writer.** It forwards the extractor's data + grader's
   assessment and writes the brief request into `outbox/writer/`. On stop, that
   routes to the writer.
5. **writer assembles the brief.** It writes the structured evidence brief into
   `outbox/synthesis-lead/`. On stop, that routes to the lead.
6. **lead finalizes.** It reviews for overstatement and writes the final brief
   into `outbox/user/`. On stop, that's delivered to your `user` mailbox (visible
   with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a question, the agents just sit in standby (that's the
> point of the standby prompt). The review only moves when real mail arrives —
> this swarm has no periodic pings to self-start it.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/clinical-evidence-synthesizer.yaml
```

```
swarm: clinical-evidence-synthesizer   root: ./clinical-evidence-synthesizer-workspace
  synthesis-lead (claude) up idle queue=0 unread=0 talks=extractor, grader, writer, user
  extractor     (codex)  up idle queue=0 unread=0 talks=synthesis-lead
  grader        (gemini) up idle queue=0 unread=1 talks=synthesis-lead
  writer        (claude) up idle queue=0 unread=0 talks=synthesis-lead
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/clinical-evidence-synthesizer.yaml          # whole swarm, last 20
./agentainer logs -c examples/clinical-evidence-synthesizer.yaml -f        # follow live
./agentainer logs grader -c examples/clinical-evidence-synthesizer.yaml   # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
and for the grader a `silent-but-alive` transition — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox synthesis-lead -c examples/clinical-evidence-synthesizer.yaml
```

Prints the one released message (headers + body), or
`synthesis-lead: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue synthesis-lead -c examples/clinical-evidence-synthesizer.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach grader -c examples/clinical-evidence-synthesizer.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

**The brief** — when the lead delivers, the finished evidence brief lands in your
`user` mailbox; read it with `agentainer user inbox` or in the `serve` UI.

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or (more directly)
by sending notes into an agent's inbox.

- **Send a clarification to the lead.** Realized you wanted comparator = placebo,
  not a second antidepressant? `./agentainer send --to synthesis-lead -c
  examples/clinical-evidence-synthesizer.yaml "Use placebo as the comparator;
  re-brief the extractor and grader."` The lead relays the change down the chain.
- **Ask the extractor for a missing statistic.** `./agentainer send --to
  synthesis-lead -c ... "Have the extractor add attrition numbers per arm."` — the
  lead forwards it.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/clinical-evidence-synthesizer.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/clinical-evidence-synthesizer.yaml     # resume is the default
```

On `up`, Agentainer reads
`clinical-evidence-synthesizer-workspace/.agentainer/sessions.yaml` (written as
each agent finished its first turn) and reattaches the recorded conversations via
each type's native resume: `claude --resume <id>` for `synthesis-lead` and
`writer`, `codex resume <id>` for `extractor`, and (where supported) the `gemini`
session for `grader`. A resumed agent is *not* re-sent the standby prompt (its
prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/clinical-evidence-synthesizer.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a `librarian` / `search` agent
The extractor currently does its own searching. If you want a dedicated searcher
that owns the query strategy (PRISMA-style), add a fifth agent and wire it in:

```yaml
  - name: librarian
    type: codex
    can_talk_to: [synthesis-lead]
    command: "codex --yolo"
    role: |
      You are the LIBRARIAN. Given a PICO question from the synthesis lead, run
      the literature search, return a deduplicated study list with citations, and
      never write the extraction yourself.
```

Then have the lead brief `librarian` first and pass its study list to `extractor`
and `grader`.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `extractor: type: claude` or `hermes` to put extraction on a different model
  than the lead.
- `grader: type: codex` if you want GRADE grading on Codex — but note that
  `codex`/`claude` get `capture: hook` (immediate), whereas `gemini`/`hermes`
  need explicit `capture: pane` to avoid the silent-but-alive lag (§10).
- See [`multi-llm-swarm.md`](./multi-llm-swarm.md) for mixing model families
  safely, and the broader hub-and-spoke discussion in
  [`delegation-pipeline.md`](./delegation-pipeline.md).

### Tune the ACL
- To let the `writer` escalate a caveat straight to `user` (not only via the
  lead), add `user` to its `can_talk_to`. Mind that this widens the human-facing
  surface; the doc's convention keeps the lead the sole `user` contact.
- The `extractor`/`grader`/`writer` each listing only `synthesis-lead` is the
  one-place-owns-the-contract guarantee — keep it unless you have a reason.

---

## 10. Tips & footguns

- **Keep the lead the only `user`-facing agent.** Only `synthesis-lead` lists
  `user` in `can_talk_to`. That gives you a single funnel: raw extractions,
  certainty grades, and the brief always pass through review before they reach
  you. If the extractor, grader, or writer tries to mail `user` directly, the
  orchestrator bounces it (ACL) and drops a `system` note in their inbox
  explaining who they *can* message — the model self-corrects in-band.

- **The `gemini` grader and `capture: none`.** This config sets
  `defaults: capture: none`. The loader **auto-upgrades** `claude` (Stop hook) and
  `codex` (notify hook) agents to `hook`, so `synthesis-lead`, `extractor`, and
  `writer` fire their turn-completion signal immediately and mail routes the
  instant they stop. `gemini` has no completion hook, though — its natural capture
  is **pane polling**, and `capture: none` leaves it with *no* automatic signal.
  The liveness supervisor still recovers it via the **silent-but-alive** health
  probe + a stale-busy timeout (`mark_turn_finished` after `busy_timeout_ms`), so
  the grader's reply *does* eventually route — but only after a delay, and the
  log shows a `silent-but-alive` event. **Fix:** set `capture: pane` on the
  grader for immediate detection (see [`multi-llm-swarm.md`](./multi-llm-swarm.md)),
  or accept the supervisor fallback.

- **Honesty is a role instruction, not an enforced invariant.** The "never
  overstate / flag weak evidence" duty lives in the roles, not in code. A weak or
  non-compliant model can still launder a conclusion — the ACL and the
  lead-review step are your structural backstops, not a guarantee that every
  brief is clinically sound. Treat the output as a draft to verify, not as
  medical advice.

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
  ./agentainer down           -c examples/clinical-evidence-synthesizer.yaml
  ./agentainer remove-session -c examples/clinical-evidence-synthesizer.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files in
  `clinical-evidence-synthesizer-workspace/` or your config.

- **Availability shapes the ending.** If `user` is **away** when the lead
  finishes, your evidence brief is *held* (with a `system` "the user is away" ack
  to the lead) rather than lost — read it later with `agentainer user inbox` or
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
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families (and `capture: pane` for gemini/hermes).
- `examples/clinical-evidence-synthesizer.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

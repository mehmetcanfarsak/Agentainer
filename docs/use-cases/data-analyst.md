# Use case: Data analyst

A concrete, end-to-end walkthrough of the shipped `examples/data-analyst.yaml`
swarm — the classic "business analyst handed two spreadsheets" pattern. A
**coordinator** takes a dataset description and a question from you, then fans
the work out to a **profiler** (describes the shape of the data), an **analyst**
(find the insights that answer the question), and a **reporter** (writes the
plain-language insight report). The coordinator hands you the synthesis back.

Everything below is based on the actual contents of
`examples/data-analyst.yaml` and the shipped CLI (`lib/cli.py`) and mailroom
(`lib/mail.py`). No API keys are needed to understand the mechanics; to run it
*for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Analysts, operators, and anyone who gets handed raw files (CSVs, spreadsheets,
JSON exports) and a fuzzy question — "what drives churn?", "which segment should
we target?", "what's surprising in this data?" — and wants the *discipline* of a
real analysis without doing every step themselves. The swarm encodes the order
that makes analysis trustworthy: profile the data first (so you're not drawing
conclusions from a misread column), find insights from the profile, then report
in plain language with the evidence attached.

It is deliberately a **hub-and-spoke**: every file, every intermediate result,
and every final report passes through the coordinator, so the human-facing
answer has exactly one author. The profiler/analyst/reporter never talk to each
other or to you directly — only up to the coordinator.

---

## 2. The topology

```
          user
            |
         coordinator               (the hub: talks to profiler, analyst, reporter, user)
          /    |    \
     profiler analyst  reporter
        (spokes talk only to coordinator)
```

Four agents, one directed flow:

1. **`user` → `coordinator`** — you drop raw data file(s) into the coordinator's
   workspace and ask a question ("I dropped `customers.csv` and `usage.csv`. What
   drives churn?").
2. **`coordinator` → `profiler`** — the coordinator emails the file paths and asks
   for a profile (types, nulls, distributions, outliers, correlations).
3. **`profiler` → `coordinator`** — the profiler returns the data profile. Because
   `profiler` can only talk to `coordinator`, this lands with the hub.
4. **`coordinator` → `analyst`** — the coordinator forwards the question, the file
   paths, and the profile, and asks for the actual insights.
5. **`analyst` → `coordinator`** — the analyst returns an evidence-backed findings
   list. (See the `capture` footgun in §10 — the analyst's turn detection needs
   care.)
6. **`coordinator` → `reporter`** — the coordinator forwards the question, the
   profile, and the findings, and asks for the plain-language report.
7. **`reporter` → `coordinator`** — the reporter returns the report.
8. **`coordinator` → `user`** — the coordinator synthesizes the report into a short
   answer to your question and emails you.

The routing above is *enforced* by each agent's `can_talk_to` list. An agent can
only deliver to names on its own list; anything else is bounced back as a
`system` message and filed in `failed/` (see §7). Notably, `profiler`, `analyst`,
and `reporter` **never** talk to `user` directly — only the coordinator does.

---

## 3. The config, explained

Here is `examples/data-analyst.yaml` in full:

```yaml
swarm:
  name: data-analyst
  root: ./data-analyst-workspace

defaults:
  capture: none
  can_talk_to: []

agents:
  - name: coordinator
    type: claude
    can_talk_to: [profiler, analyst, reporter, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the DATA COORDINATOR for an analytics desk. You are the single hub
      the human operator talks to. ... (read the raw files, then profile ->
      analyze -> report, each as a self-contained delegation) ...

  - name: profiler
    type: codex
    can_talk_to: [coordinator]
    command: "codex --yolo"
    role: |
      You are the DATA PROFILER. ... (column names, types, null rates,
      distributions, outliers, correlations) ... Do NOT draw business conclusions.

  - name: analyst
    type: gemini
    can_talk_to: [coordinator]
    command: "gemini --yolo"
    role: |
      You are the INSIGHT ANALYST. ... (find the drivers/segments/relationships
      that answer the question; call out limitations) ... Do not write the report.

  - name: reporter
    type: claude
    can_talk_to: [coordinator]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the INSIGHT REPORTER. ... (headline answer, 3-5 findings, charts
      described in text, prioritized recommendations) ... Do not re-run analysis.
```

Field by field:

### `swarm`
- **`name: data-analyst`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./data-analyst-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent's workdir defaults to
  `data-analyst-workspace/<name>` (coordinator, profiler, analyst, reporter — four
  **separate** directories). Orchestrator state goes under
  `data-analyst-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode is "none". This is
  **deliberately overridden per type at load time** (see the per-agent notes and
  the critical footgun in §10): `claude`/`codex` agents that *have* a completion
  hook are auto-upgraded to `capture: hook`; `gemini`/`hermes` stay at `none`
  unless you set `capture: pane`. In this swarm that means the **gemini analyst
  lands at `capture: none`** and needs a fix.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `coordinator` (type: `claude`)
- **`can_talk_to: [profiler, analyst, reporter, user]`** — the coordinator is the
  hub: it delegates to the three specialists and is the **only agent that can
  talk to `user`**. That last part matters — keep the human-facing surface to a
  single agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the coordinator waits for your data drop + question
  instead of proactively mailing peers. The role spells out the full
  profile → analyze → report workflow.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).
  The `defaults: capture: none` is auto-upgraded to `hook` for this agent — you'll
  see a `!!` warning at `validate`/`up` time, which is expected.

### `profiler` (type: `codex`)
- **`can_talk_to: [coordinator]`** — the profiler only reports back to the
  coordinator. It cannot reach the analyst, the reporter, or the `user`; the data
  shape is owned by one place before insights are drawn.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "describe the shape of the data" (types, nulls, distributions,
  outliers, correlations) and explicitly **do not** draw business conclusions.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.
  The `capture: none` default is auto-upgraded to `hook`.

### `analyst` (type: `gemini`)
- **`can_talk_to: [coordinator]`** — the analyst reports findings only to the
  coordinator. It cannot reach the `user` or the other spokes directly.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **`role`** — "find the actual insights/patterns that answer the question, tied
  to evidence, with caveats" and explicitly **do not** write the report prose.
- **Turn detection: `capture: none` — UNFIXED IN THIS CONFIG.** Gemini has no
  completion hook, so `defaults: capture: none` is *not* auto-upgraded (the
  upgrade only applies to hook-capable types). The analyst therefore has **no
  turn-completion signal** and will show as a "silent-but-alive" agent. You must
  add `capture: pane` to it — see §10.

### `reporter` (type: `claude`)
- **`can_talk_to: [coordinator]`** — the reporter returns the report only to the
  coordinator. It cannot reach the `user` directly.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **`role`** — "write a plain-language, skimmable insight report: headline answer,
  3-5 findings, charts described in text, prioritized recommendations" and
  explicitly **do not** re-run analysis.
- **Turn detection:** `claude` → Stop hook (auto-upgraded from `capture: none`).

### What's *not* in this config
- **No shared workdir.** All four agents have their own directory, so no mailbox
  namespacing is needed (compare the [`data-pipeline-builder.md`](./data-pipeline-builder.md)
  example, where two codex agents share a repo). You drop the raw data files into
  the **coordinator's** workdir (`data-analyst-workspace/coordinator/`); the
  coordinator reads them directly off disk and passes paths to the profiler in
  mail.
- **No `pings`.** The swarm is purely event-driven off real mail — it only moves
  when you drop data and ask a question.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 4. Run it

From the repo root:

```bash
agentainer up -c examples/data-analyst.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the `capture` upgrade warnings for
   `coordinator`/`profiler`/`reporter` (and, importantly, **no** warning for the
   `analyst` — its `capture: none` is left as-is; fix it per §10).
2. Creates the runtime dirs (`data-analyst-workspace/.agentainer/…`: log, queue,
   run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/about.md`
   contact card *is* the ACL made visible: the coordinator gets `outbox/profiler/`,
   `outbox/analyst/`, `outbox/reporter/`, `outbox/user/`; each spoke gets only
   `outbox/coordinator/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `coordinator`
   and `reporter`, and the Codex `notify` hook for `profiler`. The gemini `analyst`
   gets **no** capture (see §10).
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'data-analyst' is up with 4 agent(s)
:: attach with:  tmux attach -t <coordinator-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/data-analyst.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents, so it must **never** be exposed on `0.0.0.0` without a token.
See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole analysis route mail with no API keys — the mechanics are identical.
> (Do the same for the gemini `analyst` so you can see the silent-but-alive
> behavior in §10 without a Gemini key.)

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the coordinator's synthesized answer as mail
(rather than have it held), turn yourself available first:

```bash
agentainer user available -c examples/data-analyst.yaml
```

This rewrites the `user` contact card in the coordinator's `outbox/user/about.md`
to `Status: available`, so the coordinator sees you're reachable. (While away,
mail to you is *held* and the sender gets a `system` ack — nothing bounces.)

Now **drop your raw data files** into the coordinator's workspace, then send the
question into the swarm, addressed to the coordinator:

```bash
# 1) put the files where the coordinator can read them
cp customers.csv usage.csv examples/data-analyst-workspace/coordinator/

# 2) email the coordinator
agentainer send --to coordinator -c examples/data-analyst.yaml \
  "I dropped customers.csv and usage.csv. What drives churn? What should we look at?"
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the coordinator, then — because
the inbox was empty — **released into `inbox/`** and the coordinator is **nudged**
(the protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the analysis advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **coordinator receives the question.** It reads the dropped files, emails
   `profiler` with the paths + a profiling request. On stop (Claude Stop hook),
   that routes to the profiler.
2. **profiler profiles.** It reads its inbox, writes the data profile, and reports
   back into `outbox/coordinator/`. On stop (Codex `notify` hook), that routes to
   the coordinator.
3. **coordinator briefs the analyst.** It forwards the question + paths + profile
   into `outbox/analyst/`. On stop, that routes to the analyst — **unless the
   analyst's `capture` is still `none`** (see §10): then the reply is *not*
   auto-routed and the coordinator waits.
4. **analyst finds insights.** It writes an evidence-backed findings list to
   `outbox/coordinator/`. (With the fix in §10, on stop this routes to the
   coordinator; without it, you must nudge manually.)
5. **coordinator briefs the reporter.** It forwards the question + profile +
   findings into `outbox/reporter/`. On stop, that routes to the reporter.
6. **reporter writes the report.** It writes the plain-language report to
   `outbox/coordinator/`. On stop (Claude Stop hook), that routes to the
   coordinator.
7. **coordinator synthesizes and replies.** It reads the report, writes a short
   answer into `outbox/user/`, and on stop that's delivered to your `user` mailbox
   (visible with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion (for the
agents that have one).

> If you *don't* send a question, the agents just sit in standby (that's the point
> of the standby prompt). The analysis only moves when real mail arrives — this
> swarm has no periodic pings to self-start it.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
agentainer status -c examples/data-analyst.yaml
```

```
swarm: data-analyst   root: ./data-analyst-workspace
  coordinator (claude) up idle queue=0 unread=0 talks=profiler, analyst, reporter, user
  profiler    (codex)   up idle queue=0 unread=1 talks=coordinator
  analyst     (gemini)  up idle queue=0 unread=0 talks=coordinator
  reporter    (claude)  up idle queue=0 unread=0 talks=coordinator
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
agentainer logs -c examples/data-analyst.yaml          # whole swarm, last 20
agentainer logs -c examples/data-analyst.yaml -f        # follow live
agentainer logs analyst -c examples/data-analyst.yaml  # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
agentainer inbox coordinator -c examples/data-analyst.yaml
```

Prints the one released message (headers + body), or `coordinator: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
agentainer queue coordinator -c examples/data-analyst.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
agentainer attach analyst -c examples/data-analyst.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

**The data + report** — your raw files live in
`data-analyst-workspace/coordinator/`, and the reporter's finished report is
written into `data-analyst-workspace/reporter/` (inspect it there, or read the
coordinator's reply to you in the UI / `agentainer user inbox`).

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or (more directly)
by sending notes into an agent's inbox.

- **Send a clarification to the coordinator.** Realized the question is about
  *revenue* churn, not logo churn? `agentainer send --to coordinator -c examples/data-analyst.yaml
  "By churn I mean revenue churn, not customer count. Re-brief the profiler and
  analyst."` The coordinator relays the change down the chain.
- **Ask the reporter for the evidence.** `agentainer send --to coordinator ... "Have
  the reporter attach the numbers behind each finding."` — the coordinator forwards
  it.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
agentainer down -c examples/data-analyst.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
agentainer up -c examples/data-analyst.yaml     # resume is the default
```

On `up`, Agentainer reads `data-analyst-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the
coordinator and reporter, `codex resume <id>` for the profiler, and `gemini`
resume for the analyst. A resumed agent is *not* re-sent the standby prompt (its
prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
agentainer sessions -c examples/data-analyst.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Fix the analyst's `capture` (do this first)
The shipped config's `defaults: capture: none` leaves the gemini `analyst` with
no turn-completion signal. Add an explicit `capture: pane` to the analyst so the
orchestrator can poll its pane for completion:

```yaml
  - name: analyst
    type: gemini
    capture: pane                # <-- add this; gemini has no completion hook
    can_talk_to: [coordinator]
    command: "gemini --yolo"
    role: |
      ...
```

(See §10 for why this matters and how to spot it in `status`/`logs`.)

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- Put the `profiler` on `claude` instead of `codex` if you prefer Claude for
  profiling.
- Put the `reporter` on `gemini` — but then *it* needs `capture: pane` (gemini has
  no hook).
- Remember: `gemini`/`hermes` need `capture: pane` (pane polling) since they have
  no completion hook.

### Add a visualization agent
If you want the reporter's "charts described in text" actually rendered, add a
fifth agent that can read the reporter's report and emit image files:

```yaml
  - name: visualizer
    type: codex
    can_talk_to: [coordinator]
    command: "codex --yolo"
    role: |
      You are the VISUALIZER. You receive the reporter's report, build the charts
      it describes (as PNGs in your workdir), and hand the file paths back to
      outbox/coordinator/. You never interpret the data.
```

Then add `visualizer` to the coordinator's `can_talk_to` so it can be briefed.

### Tune the ACL
- To let the `reporter` escalate straight to `user` (not only via the coordinator),
  add `user` to its `can_talk_to`. Mind that this widens the human-facing surface;
  the doc's convention keeps the coordinator the sole `user` contact.
- To make the `profiler` unreachable from anyone but the coordinator (already the
  case here), leave its `can_talk_to: [coordinator]` — that's the one-place-owns-
  the-profile guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md)
  for mixing model families safely.

---

## 10. Tips & footguns

- **The `analyst`'s `capture: none` is the #1 footgun here.** `defaults: capture: none`
  is auto-upgraded to `hook` for the `claude` (coordinator, reporter) and `codex`
  (profiler) agents — you'll see `!!` warnings at `up` and that's expected. But the
  **gemini `analyst` has no completion hook, so the loader leaves it at `none`.**
  Result: the orchestrator has *no* turn-completion signal for the analyst, it
  shows as **silent-but-alive**, and its reply to the coordinator is *not*
  auto-routed — the coordinator waits forever. Fix: add `capture: pane` to the
  analyst (see §9). Until you do, you'll see the analyst finish its turn in its
  pane but the mail never advances; `agentainer nudge analyst` (or a manual `send`)
  is the stopgap. The supervisor logs a one-time `silent-but-alive` transition for
  it.

- **Keep the coordinator the only `user`-facing agent.** Only the coordinator lists
  `user` in `can_talk_to`. That gives you a single funnel: raw profiles and insight
  drafts always pass through review before they reach you. If the profiler,
  analyst, or reporter tries to mail `user` directly, the orchestrator bounces it
  (ACL) and drops a `system` note in their inbox explaining who they *can* message
  — the model self-corrects in-band.

- **Drop the data in the coordinator's workdir, not a spoke's.** The coordinator is
  the only agent whose `role` tells it to *read raw files off disk*; the spokes
  receive paths in mail. If you put `customers.csv` in `data-analyst-workspace/profiler/`,
  the coordinator can't see it and will email `user` to say the file is missing
  (exactly as its role instructs).

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch (e.g. a `gemini` agent whose `command`
  doesn't launch Gemini) means completion never triggers and the agent pins
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
  agentainer down           -c examples/data-analyst.yaml
  agentainer remove-session -c examples/data-analyst.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the raw data files you dropped in the coordinator's
  workdir or your config.

- **Availability shapes the ending.** If `user` is **away** when the coordinator
  finishes, your synthesized answer is *held* (with a `system` "the user is away"
  ack to the coordinator) rather than lost — read it later with
  `agentainer user inbox` or flip yourself available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions. See [`ui-guide.md`](../ui-guide.md).

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`data-pipeline-builder.md`](./data-pipeline-builder.md) — a sibling hub-and-spoke
  swarm (with a shared workdir) for contrast.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- `examples/data-analyst.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

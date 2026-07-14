# Use case: Experiment analyst

A concrete, end-to-end walkthrough of the shipped
`examples/experiment-analyst.yaml` swarm — four agents that turn an experiment
brief plus its results into a defensible **ship / hold / kill** decision memo.
A **hub analyst** takes the brief, a **statistician** quantifies the effect
(significance, power, CIs, effect size), a **guardrail-checker** audits the
*experiment itself* for invalidity (peeking, sample-ratio mismatch, novelty
effects, short duration) and can **veto** a bad call before it ships, and a
**reporter** writes the decision memo. The analyst delivers the memo back to you.

Everything below is based on the actual contents of
`examples/experiment-analyst.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Growth, product, and data scientists who run A/B tests or causal experiments and
want the analysis discipline applied *consistently* — a dedicated statistician,
a mandatory guardrail gate that catches invalid experiments, and a clean memo —
without doing every step themselves. The swarm encodes the discipline that
prevents wrong calls from shipping: the guardrail check is a **hard gate**, the
statistician analyzes the numbers *as given* (it never covers for a broken
experiment), and a single hub owns the human-facing verdict.

It is deliberately **hub-and-spoke with a mandatory guardrail gate**. Every
brief and every deliverable passes through the analyst; the spokes (statistician,
guardrail, reporter) never talk to each other or to you. That keeps the verdict
writes-through-one-authority and makes "ship on a failed guardrail"
architecturally hard.

---

## 2. The topology

```
            brief + data
   user ───────────────▶ analyst ─────┬────────────▶ statistician
        (final memo)   ◀──────────────┼──┬──────────▶ guardrail
                                      │  │
                                      └──┴──────────▶ reporter
```

Four agents, one directed flow:

1. **`user` → `analyst`** — you send the brief + the observed results (hypothesis,
   metric, assignment/randomization config, per-arm sample sizes, the numbers,
   and guardrail-relevant context: run dates, how many times it was peeked at,
   whether assignment was re-weighted).
2. **`analyst` → `statistician` and `analyst` → `guardrail`** — the analyst fans
   the brief out to **both** in parallel: the statistician gets the numbers, the
   guardrail gets the experiment setup. Neither is blocked on the other.
3. **`statistician` → `analyst`** — returns p-values, power, CIs, effect size.
   **`guardrail` → `analyst`** — returns PASS/FAIL with specifics.
4. **`analyst` → `reporter`** — once both returns are in, the analyst synthesizes
   (statistics + guardrail verdict + recommended call) and hands it to the
   reporter.
5. **`reporter` → `analyst`** — the reporter writes the memo and returns it.
6. **`analyst` → `user`** — the analyst delivers the memo and states its call.

The routing above is *enforced* by each agent's `can_talk_to` list. The
spokes **never** talk to `user` or to each other — only the analyst does. If a
spoke tries to reach outside its list, the orchestrator bounces it as a `system`
message and files it in `failed/`.

---

## 3. The config, explained

Here is `examples/experiment-analyst.yaml` in full (role bodies condensed):

```yaml
swarm:
  name: experiment-analyst
  root: ./experiment-analyst-workspace

defaults:
  capture: none              # upgraded to the type's hook for claude/codex/gemini at up
  can_talk_to: []           # tightened per agent below

agents:
  - name: analyst
    type: claude
    can_talk_to: [statistician, guardrail, reporter, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the EXPERIMENT ANALYST and the only human-facing agent. ... (drives
      the brief to a ship/hold/kill call; never computes stats or audits validity
      itself) ... Fan the brief to BOTH statistician and guardrail in parallel;
      when both return, synthesize — if guardrail FAILS the only safe calls are
      hold or kill; hand the synthesis to reporter; deliver the memo to user.
      Never ship on a failed guardrail. ...

  - name: statistician
    type: codex
    can_talk_to: [analyst]
    command: "codex --yolo"
    role: |
      You are the STATISTICIAN. Given the observed numbers + sample sizes +
      randomization config, compute the metric-appropriate significance test,
      p-value, effect size + CI, and post-hoc power. ... Do NOT judge whether the
      experiment was set up correctly — that is the guardrail's job; analyze the
      numbers exactly as given. ...

  - name: guardrail
    type: gemini
    can_talk_to: [analyst]
    command: "gemini --yolo"
    role: |
      You are the GUARDRAIL-CHECKER. Audit the EXPERIMENT ITSELF: (1) peeking /
      multiple looks; (2) sample-ratio mismatch (SRM) via chi-square on
      assignment counts; (3) novelty / primacy effects; (4) insufficient
      duration. Give PASS/FAIL with specifics; if you FAIL it, say plainly the
      safe decisions are hold or kill only. ...

  - name: reporter
    type: claude
    can_talk_to: [analyst]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the REPORTER. Write the decision memo from the analyst's synthesis
      (statistics + guardrail verdict + recommended call). Memo order: (1) DECISION
      (SHIP/HOLD/KILL up front); (2) WHAT WE TESTED; (3) EVIDENCE; (4) GUARDRAIL;
      (5) CAVEATS. If guardrail FAILED, decision MUST be HOLD or KILL. ...
```

Field by field:

### `swarm`
- **`name: experiment-analyst`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./experiment-analyst-workspace`** — parent directory for the agents'
  working directories and mailboxes. Each agent's workdir defaults to
  `experiment-analyst-workspace/<name>` (no shared workdirs here, unlike some
  swarms). Orchestrator state goes under
  `experiment-analyst-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the comment notes this is *upgraded to the type's native
  hook at `up`* (`claude` → Stop hook, `codex` → `notify` hook, `gemini` → pane
  polling). So despite the literal `none`, each agent still gets its natural
  turn-completion signal; the stop → sweep → route → nudge clock keeps running.
  (This is a deliberate, loader-handled override — see `lib/config.py`.)
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `analyst` (type: `claude`)
- **`can_talk_to: [statistician, guardrail, reporter, user]`** — the analyst is
  the hub and the **only agent that can talk to `user`**. That last part matters:
  keep the human-facing surface to a single agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the analyst waits for your brief instead of proactively
  mailing peers. It is instructed to fan out to *both* spokes in parallel and to
  treat a guardrail FAIL as a hard veto on `ship`.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `statistician` (type: `codex`)
- **`can_talk_to: [analyst]`** — the statistician only reports back to the
  analyst. It cannot reach the guardrail, the reporter, or the `user`; each
  specialty stays in its lane and the analyst is the single synthesis point.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "compute the metric-appropriate significance test, p-value, effect
  size + CI, and post-hoc power; call out underpowering. Do NOT judge experiment
  validity — that's the guardrail's job."
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `guardrail` (type: `gemini`)
- **`can_talk_to: [analyst]`** — the guardrail only reports its verdict to the
  analyst. It cannot reach the statistician, the reporter, or the `user`; its
  PASS/FAIL is fed into the analyst's synthesis, never shipped around it.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **`role`** — the four-mode audit: peeking/multiple-looks, SRM (chi-square on
  assignment counts), novelty/primacy effects, and insufficient duration. Returns
  PASS/FAIL with per-issue severity; on FAIL, states plainly that only `hold` or
  `kill` are safe.
- **Turn detection:** `gemini` → **pane polling** (`capture: pane`, upgraded at
  `up`) — gemini has no completion hook, so the supervisor watches its pane.

### `reporter` (type: `claude`)
- **`can_talk_to: [analyst]`** — the reporter only returns the memo to the
  analyst. It never recomputes statistics or re-audits validity; it turns the
  analyst's synthesis into a decision-ready memo with a fixed section order, and
  hard-wires "if guardrail FAILED, the decision MUST be HOLD or KILL."
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **`role`** — "write the memo: DECISION (up front) → WHAT WE TESTED → EVIDENCE →
  GUARDRAIL → CAVEATS. Be concise, lead with the decision, never overstate
  certainty."
- **Turn detection:** `claude` → Stop hook.

### What's *not* in this config
- **No `workdir` overrides.** Each agent gets its own
  `experiment-analyst-workspace/<name>` directory, so there's no shared-workdir
  mailbox namespacing (see [`custom-workspace.md`](./custom-workspace.md) for
  when that *does* kick in).
- **No `pings`.** The swarm is purely event-driven off real mail — it only moves
  when you send a brief. (Add a ping to the analyst if you want a stale-experiment
  nag, but note pings don't change the ACL; the analyst still gates everything.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/experiment-analyst.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings.
2. Creates the runtime dirs (`experiment-analyst-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/about.md`
   contact card *is* the ACL made visible: the analyst gets
   `outbox/statistician/`, `outbox/guardrail/`, `outbox/reporter/`,
   `outbox/user/`; each spoke gets only `outbox/analyst/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `analyst` and
   `reporter`, the Codex `notify` hook for `statistician`, and pane polling for the
   `gemini` `guardrail`.
5. **Opens one tmux session per agent**, `cd`'d into its own workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'experiment-analyst' is up with 4 agent(s)
:: attach with:  tmux attach -t <analyst-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/experiment-analyst.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents, so it must **never** be exposed on `0.0.0.0` without a token.
See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole fan-out route mail with no API keys — the mechanics are identical.

---

## 5. Drive a brief

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the analyst's memo as mail (rather than have it
held), turn yourself available first:

```bash
./agentainer user available -c examples/experiment-analyst.yaml
```

This rewrites the `user` contact card in the analyst's `outbox/user/about.md` to
`Status: available`, so the analyst sees you're reachable. (While away, mail to
you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the experiment brief and results into the swarm, addressed to the analyst:

```bash
./agentainer send --to analyst -c examples/experiment-analyst.yaml \
  "Brief: we shipped a new checkout button to 50% of traffic for 7 days. \
   Primary metric: conversion rate. Control n=12,400 conv=620; Treatment n=12,150 \
   conv=672. Assignment was 50/50 by user_id hash. We looked at the result 3 times \
   before the 7-day stop. Decide: ship / hold / kill."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the analyst, then — because the
inbox was empty — **released into `inbox/`** and the analyst is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the analysis advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **analyst receives the brief.** It reads `inbox/`, restates the decision
   question, and writes two delegations: one into `outbox/statistician/` (ask for
   p-value, power, CIs, effect size) and one into `outbox/guardrail/` (ask for a
   pass/fail validity verdict). On stop, both route in parallel.
2. **statistician and guardrail work.** The statistician reads its inbox, computes
   the numbers *as given*, and reports into `outbox/analyst/`. The guardrail reads
   its inbox, runs its four-mode audit, and also reports into `outbox/analyst/`. On
   each stop, mail routes back to the analyst — they don't wait on each other.
3. **analyst synthesizes.** It reads both returns. If guardrail **FAILS**, the
   only safe calls are `hold`/`kill` no matter how pretty the p-value; if guardrail
   **PASSES** and the effect is significant with adequate power, it weighs ship vs.
   hold. It writes the synthesis into `outbox/reporter/`. On stop, that routes.
4. **reporter writes the memo.** It reads its inbox, produces the DECISION →
   WHAT WE TESTED → EVIDENCE → GUARDRAIL → CAVEATS memo (forcing HOLD/KILL on a
   failed guardrail), and returns it into `outbox/analyst/`. On stop, that routes.
5. **analyst delivers.** It reads the memo and writes it into `outbox/user/`. On
   stop, that's delivered to your `user` mailbox (visible with `agentainer user
   inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a brief, the agents just sit in standby (that's the point of
> the standby prompt). The analysis only moves when real mail arrives — this swarm
> has no periodic pings to self-start it.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/experiment-analyst.yaml
```

```
swarm: experiment-analyst   root: ./experiment-analyst-workspace
  analyst      (claude) up idle queue=0 unread=0 talks=statistician, guardrail, reporter, user
  statistician (codex)  up idle queue=0 unread=1 talks=analyst
  guardrail    (gemini) up idle queue=0 unread=0 talks=analyst
  reporter     (claude) up idle queue=0 unread=0 talks=analyst
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/experiment-analyst.yaml            # whole swarm, last 20
./agentainer logs -c examples/experiment-analyst.yaml -f          # follow live
./agentainer logs statistician -c examples/experiment-analyst.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox analyst -c examples/experiment-analyst.yaml
```

Prints the one released message (headers + body), or `analyst: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue analyst -c examples/experiment-analyst.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach statistician -c examples/experiment-analyst.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or (more directly)
by sending notes into an agent's inbox.

- **Send a clarification to the analyst.** Realized you forgot to mention a
  re-weighting? `./agentainer send --to analyst -c examples/experiment-analyst.yaml
  "Assignment was re-weighted 3 days in after a logging bug — tell guardrail."` The
  analyst re-fans the correction down to the guardrail.
- **Ask the statistician for the evidence.** `./agentainer send --to analyst ...
  "Have the statistician attach the power curve."` — the analyst forwards it.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/experiment-analyst.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/experiment-analyst.yaml     # resume is the default
```

On `up`, Agentainer reads `experiment-analyst-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the
analyst and reporter, `codex resume <id>` for the statistician, and the gemini
equivalent for the guardrail. A resumed agent is *not* re-sent the standby prompt
(its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/experiment-analyst.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a `data-engineer` to pull the numbers
If the brief you send is a pointer to a warehouse rather than the raw counts, add
a fifth agent that the analyst can brief to fetch the numbers and hand them to the
statistician:

```yaml
  - name: data-engineer
    type: codex
    can_talk_to: [analyst, statistician]
    command: "codex --yolo"
    role: |
      You are the DATA ENGINEER. Given a warehouse pointer from the analyst, pull
      the per-arm sample sizes and observed conversions, and hand them to the
      statistician as structured numbers. You never interpret the result.
```
Then add `data-engineer` to the analyst's `can_talk_to`.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `statistician: type: claude` (or `hermes`/`gemini`) to put the math on a
  different model than the analyst.
- `guardrail: type: codex` if you'd rather the validity audit run on Codex (which
  uses the `notify` hook) — but remember a `gemini`/`hermes` agent needs its pane
  polling, which is what this config uses for the guardrail.
- See [`multi-llm-swarm.md`](./multi-llm-swarm.md) for mixing model families
  safely, and [`configuration.md`](../configuration.md) for the full field
  reference.

### Tune the ACL
- To let the `reporter` escalate a memo straight to `user` (not only via the
  analyst), add `user` to its `can_talk_to`. Mind that this widens the
  human-facing surface; the doc's convention keeps the analyst the sole `user`
  contact so every memo passes through the synthesis review.
- To give the `statistician` and `guardrail` no path to the `user` at all
  (already the case here), leave their `can_talk_to: [analyst]` — that's the
  one-place-owns-the-verdict guarantee.
- The guardrail's veto lives in the *role text* ("Never ship on a failed
  guardrail"), not in the ACL. If you want a hard, code-level block so the analyst
  literally cannot deliver a `ship` on a failed guardrail, that's a logic change —
  today the discipline is enforced by the analyst's instructions, kept honest by
  the reporter's "decision MUST be HOLD or KILL" mandate. See
  [`delegation-pipeline.md`](./delegation-pipeline.md) for hub-and-spoke routing
  patterns.

---

## 10. Tips & footguns

- **Keep the analyst the only `user`-facing agent.** Only the analyst lists `user`
  in `can_talk_to`. That gives you a single funnel: raw statistics and guardrail
  verdicts always pass through synthesis review before they reach you. If a spoke
  tries to mail `user` directly, the orchestrator bounces it (ACL) and drops a
  `system` note in its inbox explaining who it *can* message — the model
  self-corrects in-band.

- **The guardrail is a soft gate by design, but a hard one by role.** A failed
  guardrail does not *technically* prevent the analyst from writing `ship` — the
  veto is enforced by the analyst's and reporter's role text, not by a code check.
  Trust the discipline (the reporter is told the decision MUST be HOLD/KILL), and
  read the memo's GUARDRAIL section yourself before acting on a SHIP.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch (e.g. a `gemini` agent whose `command`
  doesn't launch Gemini) means completion never triggers and the agent pins
  "busy" forever. The `gemini` guardrail in particular relies on pane polling, so
  a non-Gemini command there is the classic silent stall. `status` showing an
  agent `busy` for a long time with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/experiment-analyst.yaml
  ./agentainer remove-session -c examples/experiment-analyst.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

- **Availability shapes the ending.** If `user` is **away** when the analyst
  finishes, your memo is *held* (with a `system` "the user is away" ack to the
  analyst) rather than lost — read it later with `agentainer user inbox` or flip
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
- `examples/experiment-analyst.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

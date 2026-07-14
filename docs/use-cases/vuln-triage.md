# Use case: Vulnerability triage

A concrete, end-to-end walkthrough of the shipped
`examples/vuln-triage.yaml` swarm — a four-agent security assembly line that
turns a CVE / dependency scan (or a repo to scan) into a context-aware risk rank
and a patch plan. A **triager hub** takes the report from you, delegates to a
**ranker** (exploitable-in-our-context vs raw CVSS), a **patch-planner**, and a
**reporter**, and owns all contact with the human. The triager delivers the
finished remediation plan back to you.

Everything below is based on the actual contents of
`examples/vuln-triage.yaml` and the shipped CLI (`lib/cli.py`) and mailroom
(`lib/mail.py`). No API keys are needed to understand the mechanics; to run it
*for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Security owners, eng leads, and platform teams who get a wall of `npm audit` /
`pip-audit` / Trivy findings and need a disciplined way to answer the only
question that matters: *which of these can actually hurt us, in this codebase,
right now?* The swarm encodes the discipline that makes triage trustworthy —
risks are ranked by **contextual reachability**, not CVSS theater; the remediation
path is separated from the risk call; and the final plan is written for a human
decision-maker with explicit FIX-NOW / SCHEDULE / ACCEPT-RISK dispositions.

It is deliberately a **hub-and-spoke**, not a free-for-all: every scan and every
deliverable passes through the triager, so the human-facing summary has exactly
one authority and no two specialists analyze the same finding twice. Swapping in
a `scanner` agent (see §9) or a real dependency-scanner step is a one-line config
change.

---

## 2. The topology

```
          user
            |
         triager                 (the hub: talks to ranker, patch-planner, reporter, user)
          /    |    \
   ranker  patch-planner  reporter
               \______/
         (spokes may ONLY reply to triager)
```

Four agents, one directed flow:

1. **`user` → `triager`** — you send a scan report (a file or pasted text listing
   advisories, packages, versions) or a repo path to scan.
2. **`triager` → `ranker`** — the triager forwards the raw scan + repo path and
   asks for the context-aware ranking (reachable vs theoretical).
3. **`ranker` → `triager`** — the ranker returns a ranked list with evidence
   `file:line` and a context-risk tier.
4. **`triager` → `patch-planner`** (with the ranking) — asks for the upgrade
   path, breaking changes, and safe sequencing.
5. **`patch-planner` → `triager`** — returns the step-by-step upgrade plan.
6. **`triager` → `reporter`** (with *both* the ranking and the plan) — asks for
   the final prioritized remediation plan.
7. **`reporter` → `triager`** — returns the plan; the triager reconciles any
   conflicts and **forwards it to `user`**.

The routing above is *enforced* by each agent's `can_talk_to` list. The spokes
(`ranker`, `patch-planner`, `reporter`) **never** talk to each other or to `user`
directly — only the triager does. Anything else is bounced back as a `system`
message and filed in `failed/` (see §7).

---

## 3. The config, explained

Here is `examples/vuln-triage.yaml` in full (comments elided):

```yaml
swarm:
  name: vuln-triage
  root: ./vuln-triage-workspace

defaults:
  capture: none              # overridden per agent below to match each real CLI
  can_talk_to: []           # tightened per agent below

agents:
  - name: triager
    type: claude
    capture: hook
    can_talk_to: [ranker, patch-planner, reporter, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the VULNERABILITY TRIAGER. ... the ONLY agent that talks to the
      human; your specialists report back only to you ...
    pings:
      - message: |
          Triage cadence: if no scan has landed in your inbox in the last 24h,
          send the user a one-line note asking whether a fresh dependency scan is
          ready (and where it lives) ...
        cron: "0 9 * * *"            # daily at 09:00 host local time
        when_busy: skip

  - name: ranker
    type: codex
    capture: hook
    can_talk_to: [triager]
    command: "codex --yolo"
    role: |
      You are the CONTEXT-AWARE RISK RANKER (codex). ... trace the dependency from
      an entry point down to the vulnerable call site; cite file:line ...

  - name: patch-planner
    type: gemini
    capture: pane
    can_talk_to: [triager]
    command: "gemini --yolo"
    role: |
      You are the PATCH PLANNER (gemini). ... target version(s), breaking changes,
      transitive-vuln handling, safe order to apply ...

  - name: reporter
    type: claude
    capture: hook
    can_talk_to: [triager]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the REMEDIATION REPORTER (claude). ... executive summary, prioritized
      table, ACCEPT-RISK justifications, safe-sequencing checklist ...
```

Field by field:

### `swarm`
- **`name: vuln-triage`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./vuln-triage-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent's workdir defaults to
  `vuln-triage-workspace/<name>` (so `vuln-triage-workspace/triager`,
  `.../ranker`, `.../patch-planner`, `.../reporter`). Orchestrator state goes
  under `vuln-triage-workspace/.agentainer/` (never commit it). Note all four
  agents have distinct workdirs here, so no mailbox namespacing is needed (see
  the shared-workdir note in [`custom-workspace.md`](./custom-workspace.md)).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the loader's safe floor. Each agent below overrides it to
  the mode that matches its real CLI, so turn completion actually fires.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent
  states its own list explicitly, so this is just a safe floor.

### `triager` (type: `claude`)
- **`can_talk_to: [ranker, patch-planner, reporter, user]`** — the triager is the
  hub: it delegates to the three specialists and is the **only agent that can
  talk to `user`**. That last part matters — keep the human-facing surface to a
  single agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command, e.g. a
  shell alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the triager waits for your scan instead of proactively
  mailing peers. The role is explicit about sequencing: ranker → patch-planner →
  reporter → user, and about never having two agents analyze the same finding.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).
- **`pings`:** one daily cron (`0 9 * * *`, host local time) whose message asks
  the user for a fresh scan if none has landed in 24h. `when_busy: skip` means a
  nudge is only posted when the triager is idle, so the cadence never interrupts
  an in-flight triage.

### `ranker` (type: `codex`)
- **`can_talk_to: [triager]`** — the ranker only reports back to the triager. It
  deliberately cannot reach the patch-planner, the reporter, or the `user`; the
  risk call is owned by one place.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "trace each finding to its actual call site, classify reachability
  (unauthenticated / authenticated / test-only / not-reachable), give a CONTEXT
  risk plus the raw CVSS, and call out over-reported findings. Do NOT propose
  fixes — that's patch-planner's job."
- **Turn detection:** `codex` with `capture: hook` (its turn-completion signal,
  configured to the hook mode in this swarm).

### `patch-planner` (type: `gemini`)
- **`can_talk_to: [triager]`** — the patch-planner reports only to the triager;
  it cannot reach the ranker, the reporter, or the `user`.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **`role`** — "given the ranking + repo path, propose the REMEDIATION PATH, not
  the risk call: target versions, breaking changes with `file:line`, transitive
  handling (upgrade / pin / drop), and safe ordering. Do NOT re-rank risk and do
  NOT write the final report — that's reporter's job."
- **Turn detection:** `gemini` → **pane polling** (`capture: pane`). Gemini (and
  `hermes`) have no completion hook, so the supervisor polls the pane to learn
  when a turn ends. This is why this agent explicitly sets `capture: pane`.

### `reporter` (type: `claude`)
- **`can_talk_to: [triager]`** — the reporter only reports back to the triager.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **`role`** — "given BOTH the ranking and the upgrade plan, write the FINAL
  prioritized remediation plan for a human: executive summary, prioritized table
  with FIX-NOW / SCHEDULE / ACCEPT-RISK, ACCEPT-RISK justifications, and a safe-
  sequencing checklist. If input is missing or the specialists disagree, ask the
  triager rather than guessing."
- **Turn detection:** `claude` → Stop hook.

### The ACL, made concrete

The `can_talk_to` lists are the whole security story of this swarm:

| agent        | may deliver to              | may NOT reach                     |
|--------------|-----------------------------|-----------------------------------|
| `triager`    | ranker, patch-planner, reporter, user | (everyone it needs)        |
| `ranker`     | triager                     | patch-planner, reporter, user     |
| `patch-planner` | triager                  | ranker, reporter, user            |
| `reporter`   | triager                     | ranker, patch-planner, user       |

Because the spokes can only reply to the hub, a context-ranking from `ranker`
cannot skip the triager and land in your inbox, and `patch-planner` cannot
rewrite the risk call. The orchestrator enforces this on every send: mail to any
name not on the sender's list is bounced as a `system` message, so a model that
forgets the protocol self-corrects in-band. The ACL is cooperative (filesystem
isolation is not the boundary — see [`mail-model.md`](../mail-model.md)), but for
well-behaved agents it's exactly what keeps the handoffs clean.

### Per-type turn detection (why `capture` differs)

The clock of the swarm is turn-completion:
`stop → sweep → route → release → nudge`. Each `type` has a *natural* signal the
orchestrator listens for, and a `type`/`command` mismatch wedges the agent
("busy" forever). Here:

- **`claude`** (`triager`, `reporter`) → **Stop hook** — a hook the orchestrator
  installs at `up` fires when the Claude turn ends.
- **`codex`** (`ranker`) → **hook** — configured to the hook mode for this swarm.
- **`gemini`** (`patch-planner`) → **pane polling** (`capture: pane`) — no
  completion hook exists, so the supervisor polls the pane.

If you swapped in a `hermes` or another `gemini`, remember it also needs
`capture: pane`. See [`multi-llm-swarm.md`](./multi-llm-swarm.md) for mixing
model families safely and [`cli-reference.md`](../cli-reference.md) for the
mismatch-detection the orchestrator runs at `up`.

### What's *not* in this config
- **No shared `workdir`.** All four agents have distinct workdirs, so the loader
  does no mailbox namespacing — each agent's `inbox/ outbox/ read/ sent/ failed/`
  sit in its own folder, unprefixed.
- **Only `triager` has `pings`.** The spokes are pure event-driven off the
  triager's brief; only the hub carries a daily "is a new scan ready?" nudge.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/vuln-triage.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings.
2. Creates the runtime dirs (`vuln-triage-workspace/.agentainer/…`: log, queue,
   run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/
   about.md` contact card *is* the ACL made visible: the triager gets
   `outbox/ranker/`, `outbox/patch-planner/`, `outbox/reporter/`, `outbox/user/`;
   each spoke gets only `outbox/triager/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `triager` and
   `reporter`, the codex hook for `ranker`, and pane polling for `patch-planner`.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.
8. **Registers the triager's daily ping** (`0 9 * * *`, `when_busy: skip`).

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'vuln-triage' is up with 4 agent(s)
:: attach with:  tmux attach -t <triager-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/vuln-triage.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents, so it must **never** be exposed on `0.0.0.0` without a token.
See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole triage route mail with no API keys — the mechanics are identical.

---

## 5. Drive a scan

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the triager's finished remediation plan as
mail (rather than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/vuln-triage.yaml
```

This rewrites the `user` contact card in the triager's `outbox/user/about.md` to
`Status: available`, so the triager sees you're reachable. (While away, mail to
you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the scan into the swarm, addressed to the triager. Per the example's own
header, you point it at a scan file and the repo to analyze:

```bash
./agentainer send -c examples/vuln-triage.yaml --to triager \
  "Triage the scan at scans/deps-2026-07-14.json (repo at ./src)."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the triager, then — because the
inbox was empty — **released into `inbox/`** and the triager is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the triage advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **triager receives the scan.** It reads `inbox/`, acknowledges to you briefly,
   and writes the raw scan + repo path into `outbox/ranker/`. On stop, that routes
   to the ranker.
2. **ranker produces the context-aware ranking.** It reads its inbox, traces each
   finding to its call site (`file:line`), and writes the ranked list into
   `outbox/triager/`. On stop, that routes back to the triager.
3. **triager briefs the patch-planner.** It forwards the ranking into
   `outbox/patch-planner/`. On stop, that routes to the patch-planner.
4. **patch-planner drafts the upgrade plan.** It reads its inbox, proposes the
   remediation path, and reports back into `outbox/triager/`. On stop, that
   routes to the triager.
5. **triager briefs the reporter.** It sends BOTH the ranking and the plan into
   `outbox/reporter/`. On stop, that routes to the reporter.
6. **reporter writes the final plan.** It reads its inbox, writes the prioritized
   remediation plan, and sends it to `outbox/triager/`. On stop, that routes to
   the triager.
7. **triager finalizes and delivers.** It reconciles any conflict (e.g. ranker
   says "not reachable" but patch-planner forces a major bump) and writes the plan
   into `outbox/user/`. On stop, that's delivered to your `user` mailbox (visible
   with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a scan, the agents just sit in standby (that's the point of
> the standby prompt). The only thing that can self-start the swarm is the
> triager's daily ping — and that merely *asks* you for a scan; it doesn't invent
> one.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/vuln-triage.yaml
```

```
swarm: vuln-triage   root: ./vuln-triage-workspace
  triager       (claude) up idle queue=0 unread=0 talks=ranker, patch-planner, reporter, user
  ranker        (codex)  up idle queue=0 unread=1 talks=triager
  patch-planner (gemini) up idle queue=0 unread=0 talks=triager
  reporter      (claude) up idle queue=0 unread=0 talks=triager
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/vuln-triage.yaml          # whole swarm, last 20
./agentainer logs -c examples/vuln-triage.yaml -f        # follow live
./agentainer logs ranker -c examples/vuln-triage.yaml    # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox triager -c examples/vuln-triage.yaml
```

Prints the one released message (headers + body), or `triager: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue triager -c examples/vuln-triage.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach ranker -c examples/vuln-triage.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom
— handy for un-sticking an agent, but the mail model is the normal path.)

**The workspaces** — each agent's repo context lives in its own workdir under
`vuln-triage-workspace/` (`triager/`, `ranker/`, `patch-planner/`,
`reporter/`). The ranker's cited `file:line` evidence should point into your
actual `./src` repo (the path you passed in the scan), which the agents read but
which is not itself a swarm workdir.

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or (more directly)
by sending notes into an agent's inbox.

- **Send a clarification to the triager.** Realized a finding is reachable from an
  unauthenticated endpoint you forgot to mention? `./agentainer send -c
  examples/vuln-triage.yaml --to triager "The /api/webhook route takes untrusted
  input with no auth — treat any vuln reachable from it as unreachable-AUTH."` The
  triager relays the change down the chain.
- **Ask the ranker for the evidence.** `./agentainer send -c
  examples/vuln-triage.yaml --to triager "Have the ranker attach the file:line for
  the critical item."` — the triager forwards it.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/vuln-triage.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/vuln-triage.yaml     # resume is the default
```

On `up`, Agentainer reads `vuln-triage-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the
triager and reporter, `codex resume <id>` for the ranker, and the gemini
equivalent for the patch-planner. A resumed agent is *not* re-sent the standby
prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/vuln-triage.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a `scanner` agent
If you'd rather the swarm *produce* the scan than wait for you to paste one, add
a fifth agent that runs your dependency scanner and reports the raw findings to
the triager:

```yaml
  - name: scanner
    type: claude
    can_talk_to: [triager]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the DEPENDENCY SCANNER. Given a repo path, run the project's
      dependency scanner (npm audit / pip-audit / trivy) and write the raw
      advisory list to outbox/triager/. You do NOT rank or plan fixes.
```
Then add `scanner` to the triager's `can_talk_to` so it can be briefed, and
consider adding a daily `ping` to the triager that says "tell scanner to run."

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `ranker: type: claude` (or `hermes`/`gemini`) to put risk-ranking on a different
  model than the triager.
- `patch-planner: type: codex` if you want the upgrade-planning on Codex.
- Remember: `gemini`/`hermes` need `capture: pane` (pane polling) since they have
  no completion hook — the patch-planner already does this.

### Tune the ACL
- To let the `reporter` escalate straight to `user` (not only via the triager),
  add `user` to its `can_talk_to`. Mind that this widens the human-facing
  surface; the doc's convention keeps the triager the sole `user` contact.
- To make a spoke unreachable from anyone but the triager (already the case here),
  leave its `can_talk_to: [triager]` — that's the one-place-owns-the-call
  guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md)
  for mixing model families safely.

---

## 10. Tips & footguns

- **Keep the triager the only `user`-facing agent.** Only the triager lists `user`
  in `can_talk_to`. That gives you a single funnel: raw rankings, upgrade plans,
  and test verdicts always pass through review before they reach you. If a spoke
  tries to mail `user` directly, the orchestrator bounces it (ACL) and drops a
  `system` note in their inbox explaining who they *can* message — the model
  self-corrects in-band.

- **Context over CVSS is the whole point.** The triager's role explicitly says a
  CVSS-9.0 with no reachable code path is NOT fix-now, while a CVSS-6.5 reachable
  from an unauthenticated endpoint may be. The ranker does the reachability work
  and the reporter reflects it in the disposition column — don't let the spokes
  drift back into parroting CVSS.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch (e.g. a `claude` agent whose `command`
  doesn't launch Claude) means completion never triggers and the agent pins
  "busy" forever. `status` showing an agent `busy` for a long time with `unread`
  mail is the tell. `patch-planner` is the one to watch here, since it relies on
  pane polling rather than a hook.

- **The daily ping won't nag mid-triage.** The triager's `pings` entry uses
  `when_busy: skip`, so the "is a fresh scan ready?" note is only posted when the
  triager is idle. You won't get pinged in the middle of an active run.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/vuln-triage.yaml
  ./agentainer remove-session -c examples/vuln-triage.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches your scanned `./src` repo or your config.

- **Availability shapes the ending.** If `user` is **away** when the triager
  finishes, your remediation plan is *held* (with a `system` "the user is away"
  ack to the triager) rather than lost — read it later with
  `agentainer user inbox` or flip yourself available and it's delivered.

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
- `examples/vuln-triage.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

# Use case: the accessibility (WCAG) audit swarm

A concrete, end-to-end walkthrough of the shipped `examples/accessibility-audit.yaml`
swarm — a **lead** that scopes and sequences a web accessibility review, three
**principle auditors** (perceivable / operable / understandable) who each check
*one* WCAG POUR pillar, and a **reporter** that merges their findings into a
conformance report for the human. It's the canonical "delegate → audit in parallel
→ check the work → report" loop, wired entirely through Agentainer's file-based
mail model.

This is **distinct from `examples/security-audit.yaml`** (and its
[`security-audit.md`](./security-audit.md) sibling): that swarm hunts *security*
vulnerabilities — OWASP/STRIDE, injection, secrets, auth flaws. This swarm checks
*accessibility* conformance — WCAG 2.2 Levels A/AA and ADA equivalence —
alt text, color contrast, keyboard operability, focus order, form labels, and
error handling. Different domain, same orchestration pattern.

Everything below is based on the actual contents of `examples/accessibility-audit.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you supply the coding-CLI
commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state. The companion
> [`mail-model.md`](../mail-model.md) explains the folder layout in depth.

---

## 1. Who this is for

- **Front-end / full-stack developers** shipping a site or web app and needing a
  structured accessibility pass before launch — without manually tabbing every
  page or memorizing all 50 WCAG 2.2 success criteria.
- **Accessibility (a11y) specialists** who want a first-pass triage mapped to the
  exact success criteria, so they can spend their expert time on judgment calls
  (ARIA correctness, screen-reader behavior) rather than mechanical checking.
- **Engineering / design leads** who need a skimmable conformance report (read:
  "what breaks for disabled users, by severity, with remediation") to prioritize
  fixes or to feed a VPAT / ADA posture statement.
- **QA teams** adding an accessibility gate to a release pipeline (point the lead
  at a staging URL and a repo; get a report).

The principle split mirrors the WCAG **POUR** model (Perceivable, Operable,
Understandable, Robust). This swarm covers the three *human-facing* principles;
Robustness is intentionally out of scope here (see §7 for how to add it).

---

## 2. The topology

```
          accessibility X
   user ─────────────────────▶ lead ──▶ reporter ──▶ user
   (target URL/repo)          (hub)│      (merges   (final
                                ▲    │       findings) report)
                                │    │
        perceivable ───────────┤    │
        operable ──────────────┼────┘   (reporters ask lead for clarification)
        understandable ────────┘
```

Four agents, one directed flow:

1. **`user` → `lead`** — you send the target (a live URL and/or a repo path) plus
   a one-line description of the framework and key flows.
2. **`lead` → `perceivable` / `operable` / `understandable`** — the lead fans the
   target out to all three principle auditors at once, each told to stay in its lane.
3. **each auditor → `lead`** — they report their findings back independently (in
   parallel; they never talk to each other or to `user`).
4. **`lead` → `reporter`** — the lead forwards the consolidated findings and asks
   for the final report.
5. **`reporter` → `lead` → `user`** — the reporter can ask the lead to clarify,
   then the lead delivers the finished report to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. An agent can only deliver to names on its own list; anything
else is bounced back as a `system` message and filed in `failed/` (see §7).

### The ACL, concretely

| agent          | type   | can_talk_to                                  | may reach user? |
|----------------|--------|----------------------------------------------|-----------------|
| `lead`         | claude | perceivable, operable, understandable, reporter, user | **yes** |
| `perceivable`  | claude | lead                                         | no |
| `operable`     | claude | lead                                         | no |
| `understandable`| claude | lead                                         | no |
| `reporter`     | claude | lead, user                                   | **yes** |

Why this shape: the three auditors each own one WCAG principle, so parallelizing
them is faster than serial and avoids one auditor quietly covering another's lane.
They report only to `lead` so the lead is the single place that de-duplicates
overlaps (e.g. a missing `lang` attribute is a 3.1.1 *and* a 3.2 issue) and
reconciles contradictory verdicts. Only `lead` and `reporter` touch the human —
`reporter` is the only one that sends the *final* report, keeping the user-facing
surface to one clean artifact.

---

## 3. The config, explained

Here is `examples/accessibility-audit.yaml` in full (abridged role text):

```yaml
# Accessibility (WCAG) audit -- a lead orchestrates a WCAG 2.2 AA review of a
# URL/repo, split across the three human-facing POUR principles, then a reporter
# writes the conformance report.
swarm:
  name: accessibility-audit
  root: ./accessibility-audit-workspace
defaults:
  capture: none              # mock agents don't fire a turn-completion hook
  can_talk_to: []           # tightened per agent below
agents:
  - name: lead
    type: claude
    can_talk_to: [perceivable, operable, understandable, reporter, user]
    command: "claude --dangerously-skip-permissions"
    role: "You are the LEAD ACCESSIBILITY AUDITOR. A human sends you a target ...
           you run the whole WCAG 2.2 AA audit and are the only agent that talks
           to the human. ... send the URL + repo path to all three principle
           auditors ... forward the consolidated findings to reporter ... forward
           reporter's report to the user."
  - name: perceivable
    type: claude
    can_talk_to: [lead]
    command: "claude --dangerously-skip-permissions"
    role: "You are the PERCEIVABLE auditor -- WCAG Principle 1 only ... alt text,
           captions, semantic structure, contrast (1.4.3), reflow (1.4.10) ..."
  - name: operable
    type: claude
    can_talk_to: [lead]
    command: "claude --dangerously-skip-permissions"
    role: "You are the OPERABLE auditor -- WCAG Principle 2 only ... keyboard
           (2.1.1), focus order (2.4.3), focus visible (2.4.7), target size
           (2.5.8) ..."
  - name: understandable
    type: claude
    can_talk_to: [lead]
    command: "claude --dangerously-skip-permissions"
    role: "You are the UNDERSTANDABLE auditor -- WCAG Principle 3 only ...
           language (3.1.1), on focus/input (3.2.1/2), error identification
           (3.3.1), labels (3.3.2) ..."
  - name: reporter
    type: claude
    can_talk_to: [lead, user]
    command: "claude --dangerously-skip-permissions"
    role: "You are the REPORT AUTHOR. Given the consolidated findings ... write the
           FINAL WCAG 2.2 AA conformance report ... map every finding to its exact
           success criterion ... when final, send it to outbox/user/."
```

Field by field (mirrors the patterns in
[`getting-started.md`](../getting-started.md) and
[`configuration.md`](../configuration.md)):

### `swarm`
- **`name: accessibility-audit`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./accessibility-audit-workspace`** — parent directory for the agents'
  working directories and mailboxes. Each agent gets
  `accessibility-audit-workspace/<name>/` as its workdir (created on `up`), and its
  mailbox folders live alongside. Orchestrator state goes under
  `accessibility-audit-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, and it's keyed off each agent's `type`.
  For `claude` agents whose CLI supports a completion **Stop hook**, setting
  `capture: none` is a footgun — so the config loader *upgrades* it back to `hook`
  and prints a warning at `up` (`capture: none on a claude agent gives the
  orchestrator no way to detect turn completion; using the type's default: capture:
  hook.`). Net effect here: all five agents use the `claude` hook.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `lead` (type: `claude`)
- **`can_talk_to: [perceivable, operable, understandable, reporter, user]`** — the
  hub: it can fan the target out to the three auditors, ping the reporter, and is
  **one of only two agents allowed to talk to `user`**. That last part matters —
  keep the human-facing surface to a single entry (the lead) plus the single
  exit (the reporter's report).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity: scope the audit, fan out to the three lanes,
  collect, forward to the reporter, deliver the report. On `up` this becomes the
  agent's first prompt, wrapped in a **standby notice** ("no task yet — don't send
  anything, you'll be notified"), so the lead waits for your target instead of
  proactively mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `perceivable` / `operable` / `understandable` (type: `claude`)
- Each lists **`can_talk_to: [lead]`** — they can only report upward. They cannot
  reach each other or the `user`; their work always flows back through the lead.
- Each **`role`** is scoped to *exactly one* WCAG principle and explicitly told to
  **stay in its lane** ("Do NOT audit keyboard or forms" / "Do NOT audit contrast
  or keyboard operability" / "Do NOT audit contrast or keyboard operability"). This
  is what keeps the parallel audit from double-covering components.
- **`command`** — placeholder `claude` launch commands. (For a heterogeneous
  swarm you could make each a different `type`, e.g. `operable` as `codex`; see §7.)

### `reporter` (type: `claude`)
- **`can_talk_to: [lead, user]`** — the reporter only reports up to the lead and,
  once the report is final, to the `user`. It deliberately cannot reach the
  auditors directly (no re-delegation loops).
- **`role`** — "merge the three principle findings into one prioritized WCAG 2.2 AA
  report mapped to exact success criteria; if anything is missing, ask the lead."

### What's *not* in this config
- **No `periodically_ping_seconds`.** None of the agents has a periodic ping, so
  no agent is auto-nudged on a timer while idle — the audit is purely event-driven
  off real mail. (If you wanted the lead to poke a slow auditor, add
  `periodically_ping_seconds: 300` to it.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **No `robust` agent.** This swarm covers Perceivable/Operable/Understandable
  (the three human-facing POUR pillars). Adding a fourth `robust` auditor for
  Principle 4 (parsing, name/role/value, status messages) is a customization — see
  §7.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/accessibility-audit.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for all five `claude` agents).
2. Creates the runtime dirs
   (`accessibility-audit-workspace/.agentainer/…`: log, queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: the lead gets
   `outbox/perceivable/`, `outbox/operable/`, `outbox/understandable/`,
   `outbox/reporter/`, `outbox/user/`; each auditor gets `outbox/lead/`; the
   reporter gets `outbox/lead/`, `outbox/user/`.
4. **Installs per-type turn detection** — the Claude Stop hook for all five agents.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'accessibility-audit' is up with 5 agent(s)
:: attach with:  tmux attach -t <lead-session>
:: you can use the UI with:  agentainer serve --host 127.0.0.1 -c examples/accessibility-audit.yaml --port 8000
```

The `serve` line starts the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Note the **`--host 127.0.0.1`** bind — the UI
is a control plane that can start processes and type into agents, so it binds the
loopback interface by default and never `0.0.0.0`; for a remote bind you must
opt in *and* pass a `--token`. See [`ui-guide.md`](../ui-guide.md) and
[`cli-reference.md`](../cli-reference.md). Drop `--host`/`--token` for the safe
loopback-only bind.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole pipeline route mail with no API keys — the mechanics are identical.

---

## 5. Drive an audit

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the reporter's final report as mail (rather
than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/accessibility-audit.yaml
```

This rewrites the `user` contact card in the lead's and reporter's `outbox/user/about.md`
to `Status: available`, so they see you're reachable. (While away, mail to you is
*held* and the sender gets a `system` ack — nothing bounces.)

Now send the target into the swarm, addressed to the lead:

```bash
./agentainer send --to lead "Audit https://example.com/checkout — it's a React SPA; repo at ./web. Key flows: add-to-cart, multi-step checkout, account settings. WCAG 2.2 AA."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the lead, then — because the
inbox was empty — **released into `inbox/`** and the lead is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the audit advance one turn at a time. Each arrow
is a `stop → sweep → route → release → nudge` cycle:

1. **lead receives the target.** It reads `inbox/`, sends the URL + repo + flows to
   all three auditors (three `outbox/<name>/` files), then stops. The orchestrator
   sweeps the lead's outbox and releases one message to each auditor.
2. **the three auditors run in parallel.** Each reads its inbox, audits its WCAG
   principle by reading the actual markup/CSS/components, and writes findings into
   `outbox/lead/`. Because they never talk to each other, there's no ping-pong —
   just three independent audits landing in the lead's inbox.
3. **lead consolidates.** It reads all three reports, de-duplicates overlaps, and
   writes the merged findings into `outbox/reporter/`. On stop, that's routed to the
   reporter.
4. **reporter writes the report.** It reads the consolidated findings, builds the
   WCAG 2.2 AA conformance report, and — because `user` is available — writes it
   into `outbox/user/`. On stop, that's delivered to your `user` mailbox.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a target, the agents just sit in standby (that's the point
> of the standby prompt). The audit only moves when real mail arrives — this swarm
> has no periodic pings to self-start it.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/accessibility-audit.yaml
```

```
swarm: accessibility-audit   root: ./accessibility-audit-workspace
  lead (claude) up idle queue=0 unread=0 talks=perceivable, operable, understandable, reporter, user
  perceivable (claude) up idle queue=0 unread=1 talks=lead
  operable (claude) up idle queue=0 unread=1 talks=lead
  understandable (claude) up idle queue=0 unread=1 talks=lead
  reporter (claude) up idle queue=0 unread=0 talks=lead, user
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/accessibility-audit.yaml            # whole swarm, last 20
./agentainer logs -c examples/accessibility-audit.yaml -f          # follow live
./agentainer logs perceivable -c examples/accessibility-audit.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox perceivable -c examples/accessibility-audit.yaml
```

Prints the one released message (headers + body), or `perceivable: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue perceivable -c examples/accessibility-audit.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach lead -c examples/accessibility-audit.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.) The same
live view is available in the `serve` UI under `127.0.0.1`.

For the broader command set, see [`cli-reference.md`](../cli-reference.md). The
[`delegation-pipeline.md`](./delegation-pipeline.md) and
[`multi-llm-swarm.md`](./multi-llm-swarm.md) use cases show the same
fan-out-then-merge mechanics in different domains.

---

## 7. Tips & footguns

- **Keep the lead and reporter the only `user`-facing agents.** In this config only
  `lead` and `reporter` list `user` in `can_talk_to`. That gives you a single
  entry (target in) and a single exit (report out). If an auditor tries to mail
  `user` directly, the orchestrator bounces it (ACL) and drops a `system` note in
  the auditor's inbox explaining who it *can* message — the model self-corrects
  in-band.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually fires**
  — a `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't
  launch Claude) means completion never triggers and the agent pins "busy" forever.
  `status` showing an agent `busy` for a long time with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/accessibility-audit.yaml
  ./agentainer remove-session -c examples/accessibility-audit.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

- **Availability shapes the ending.** If `user` is **away** when the reporter
  finishes, your final report is *held* (with a `system` "the user is away" ack to
  the reporter) rather than lost — read it later with `agentainer user inbox` or
  flip yourself available and it's delivered.

---

## 8. Iterate on the findings

The audit is a loop, not a one-shot. Common follow-ups:

- **Re-scope a lane.** The lead's fan-out told `perceivable` to check checkout
  pages; if you also care about the marketing landing page, *send the lead a new
  message* naming the extra URL/flow. The lead re-delegates without you touching
  the other two auditors.
- **Ask for a deeper cut on one principle.** Reply to the reporter (via
  `agentainer send --to reporter "Expand the operable findings with exact tab
  sequences through checkout, including the modal."`) — the reporter can ask the
  lead to have `operable` re-audit if needed.
- **Clarify a contradictory verdict.** If the lead flags a conflict between what
  `perceivable` and `understandable` reported, it messages the relevant auditor
  for a second pass; you stay out of the loop until the report lands.

Because the reporter maps every finding to a specific WCAG 2.2 success criterion
and level (A/AA), you can hand the report straight to developers as a fix list, or
fold it into a VPAT/ADA posture.

---

## 9. Customize

The example is a starting point. A few common variants:

**Add a `contrast` specialist (finer-grained perceivable).** Some teams want color
and contrast pulled out of the general perceivable lane into its own agent that can
actually compute ratios. Drop a fifth agent into the `agents:` list:

```yaml
  - name: contrast
    type: claude
    can_talk_to: [lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the CONTRAST auditor. Given the URL/repo, compute the contrast ratio
      of every text/background pair and every UI component against its background
      (WCAG 1.4.3, 1.4.11) and flag any below 4.5:1 (text) / 3:1 (large text and
      UI/graphics). Give the measured ratio and the exact failing selector. Report to
      outbox/lead/.
```
and add `contrast` to the lead's `can_talk_to`. (You could likewise add a `robust`
agent for WCAG Principle 4 — parsing, name/role/value, status messages — to cover
all four POUR pillars.)

**Swap models per lane.** The example uses `claude` for every agent. To run a
heterogeneous LLM swarm, change an agent's `type` and matching `command` — e.g.
make `operable` a `codex` agent (`type: codex`, `command: "codex --yolo"`) or
`underable` a `gemini` agent (`type: gemini`, `command: "gemini --yolo"`,
`capture: pane`). The ACL and mail routing are type-agnostic; only turn detection
differs per `type` (hook vs. pane polling). See
[`multi-llm-swarm.md`](./multi-llm-swarm.md).

**Tune the ACL.** By default the three auditors can *only* reach `lead`. If you
want the reporter to pull raw evidence straight from an auditor when the lead's
consolidation is thin, add that auditor's name to the reporter's `can_talk_to`.
Keep in mind each added edge is a new place mail can flow — narrower ACLs make the
report more predictable.

**Point at a different bar.** The lead defaults to WCAG 2.2 Level AA. For a U.S.
federal/Section 508 or an EU EN 301 549 context, just say so in your `send` to the
lead — the auditors and reporter follow the stated bar.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how delivery works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resume agents after a
  reboot.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — the fan-out/merge pattern
  in another domain.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing claude/codex/gemini/hermes
  in one swarm.
- `examples/accessibility-audit.yaml` — the config this walkthrough is built on.

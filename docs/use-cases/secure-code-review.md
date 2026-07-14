# Use case: Secure code review

A concrete, end-to-end walkthrough of the shipped
`examples/secure-code-review.yaml` swarm — a SpecterOps-style multi-agent secure
review that **splits *finding* from *confirming*** so you don't drown in false
positives. A **finder** proposes candidate vulnerabilities, an independent
**verifier** confirms or rejects each one with proof (the exact code path + a
concrete trigger), and a **lead** sequences the two-stage review and writes the
final findings table to you. The finder and verifier don't talk to each other —
no shared "consensus" that launders a wrong idea.

Everything below is based on the actual contents of
`examples/secure-code-review.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Security engineers, reviewers, and dev teams who want a structured, defensible
code review that separates *hypothesizing a bug* from *proving a bug* — the
single most effective trick for crushing false-positive noise. The finder is
allowed to be broad and even "low confidence"; the verifier is the hard gate
that kills anything it can't reproduce with a concrete code path. The lead keeps
the human-facing surface to one funnel.

It is deliberately a **hub-and-spoke with a wall between the two specialists**:
every candidate and every verdict passes through the lead, and the finder and
verifier can *never* message each other. That wall is what stops a wrong idea
from hardening into "the agents agree, so it must be real."

---

## 2. The topology

```
  finder ──┐
           ├──▶ lead ──▶ user
  verifier ┘   ▲  ▲
               │  └──(lead sends finder's candidates to verifier)
               └────(verifier returns confirm/reject + severity to lead)
```

Three agents, one directed flow:

1. **`user` → `lead`** — you send a diff, a PR, or a pointer to a repo/branch.
2. **`lead` → `finder`** — the lead hands the target to the finder and asks for a
   **candidate vulnerability list** (one entry per item: hypothesis, class,
   file:line, why-it-matters). The finder proposes only — it never grades.
3. **`finder` → `lead`** — the candidate list comes back.
4. **`lead` → `verifier`** — the lead forwards the *same* target plus the finder's
   candidate list to the verifier and asks for a verdict on **each**: CONFIRMED
   (with exact path + trigger) or REJECTED (with why it can't be reproduced),
   plus a severity.
5. **`verifier` → `lead`** — the structured verdict arrives; the verifier never
   invents findings of its own, it only judges the finder's list.
6. **`lead` → `user`** — the lead de-dupes, drops all REJECTED, sorts confirmed by
   severity, and writes the final findings table to your `user` mailbox.

The routing above is *enforced* by each agent's `can_talk_to` list. The finder
and verifier literally cannot address one another — there is **no path** for
them to form a shared opinion.

---

## 3. The config, explained

Here is `examples/secure-code-review.yaml` in full (agent `role:` prose
condensed for space — the file has the complete prompts):

```yaml
swarm:
  name: secure-code-review
  root: ./secure-code-review-workspace

defaults:
  capture: none              # mock agents don't fire a turn-completion hook
  can_talk_to: []           # tightened per agent below

agents:
  - name: lead
    type: claude
    can_talk_to: [finder, verifier, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the LEAD SECURE-CODE-REVIEW ORCHESTRATOR. ... (the only agent that
      talks to the human; you sequence finder + verifier and synthesize the
      verdict) ...

  - name: finder
    type: codex
    can_talk_to: [lead]
    command: "codex --yolo"
    role: |
      You are the FINDER, stage one. HUNT for real security issues (injection,
      broken access control/IDOR, SSRF, crypto misuse, unsafe deserialization,
      secrets-in-code). ... (output is a candidate list only; you never confirm
      or grade; you never talk to verifier or the human) ...

  - name: verifier
    type: gemini
    can_talk_to: [lead]
    command: "gemini --yolo"
    role: |
      You are the VERIFIER, stage two. Take the finder's candidate list + the
      original target and CONFIRM or REJECT each with proof. ... (you never
      invent findings of your own; REJECT anything you can't reproduce; assign
      severity) ...
```

Field by field:

### `swarm`
- **`name: secure-code-review`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./secure-code-review-workspace`** — the parent for the agents' working
  directories and mailboxes. Each agent gets its **own** private workdir
  (`secure-code-review-workspace/lead`, `/finder`, `/verifier`); there's no
  shared source checkout here, so no mailbox namespacing is needed (see the note
  in §9 vs. [`custom-workspace.md`](./custom-workspace.md)). Orchestrator state
  goes under `secure-code-review-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.
- **`capture: none`** — see the turn-detection note below. In short: this is a
  safe default for key-free mock demos, and the loader auto-upgrades
  `claude`/`codex` back to their hooks — but a real `gemini` verifier needs this
  line removed (or its own `capture: pane`).

### `lead` (type: `claude`)
- **`can_talk_to: [finder, verifier, user]`** — the hub. It delegates to the two
  specialists and is the **only agent that can talk to `user`**. The finder and
  verifier are kept off the human entirely.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity: acknowledge the target, brief the finder
  for a candidate list, forward that list to the verifier for verdicts, then
  consolidate and write the findings table to `outbox/user/`. On `up` this
  becomes the agent's first prompt, wrapped in a **standby notice** so the lead
  waits for your spec.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `finder` (type: `codex`)
- **`can_talk_to: [lead]`** — the finder only reports back to the lead. It
  **cannot** reach the verifier or the `user`; that wall is the design's whole
  point (no groupthink).
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "hunt, produce a candidate list only (hypothesis / class /
  file:line / why-it-matters), mark shaky items 'low confidence', never claim an
  issue is exploitable." Broad and honest is the goal; grading is someone else's
  job.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `verifier` (type: `gemini`)
- **`can_talk_to: [lead]`** — the verifier only reports back to the lead. It
  **cannot** reach the finder or the `user`.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **`role`** — "take finder's candidate list + the original target; for each,
  CONFIRMED (exact path + concrete trigger + severity) or REJECTED (why it can't
  be reproduced). You never invent findings of your own."
- **Turn detection:** `gemini` → **pane polling** (`capture: pane`). See the
  caveat in **What's *not* in this config** below — the `defaults.capture: none`
  line affects this agent specifically.

### The ACL is the product

This swarm's value *is* its ACL. Because `finder` and `verifier` each list only
`[lead]`, there is no `outbox/<other-specialist>/` folder for either of them — a
model that tries to mail across the wall gets the message bounced back as a
`system` note ("you may message: lead") and self-corrects in-band. The lead is
the sole cross-link: it receives candidates from the finder and relays them to
the verifier, but the two never convene. That keeps a wrong hypothesis from
becoming "the agents agree." The ACL is cooperative, not OS isolation (an agent
with filesystem access *could* write straight into a peer's inbox) — documented
honestly, enforced for well-behaved agents. See
[`mail-model.md`](../mail-model.md).

### What's *not* in this config

- **`capture` and the `gemini` caveat.** `defaults.capture: none` is the
  safe/key-free mock default. The loader *auto-upgrades* `claude` (lead) and
  `codex` (finder) back to their natural `hook` capture — so those two keep
  firing turn-completion signals and you'll see a one-line warning at `up`.
  **`gemini` (verifier) has no completion hook**, so the auto-upgrade does *not*
  kick in: with `capture: none` the verifier gets **no** pane polling and the
  orchestrator is blind to its turns. For a *real* run with a gemini verifier,
  **delete the `capture: none` default** (or add `capture: pane` to the verifier
  agent) so it polls. If you leave it as-is for a live gemini, the verifier's
  verdicts won't route until it's nudged manually.
- **No shared `workdir`.** All three agents have private workspaces, so no
  mailbox namespacing is in play (unlike the pipeline builder's shared `repo`).
- **No `pings`.** The swarm is purely event-driven off real mail — it only moves
  when you send a target. (Add a ping to `lead` if you want a stale-review nag.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §5).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/secure-code-review.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings — including the
   `capture: none` auto-upgrade note for `lead`/`finder` (see §3).
2. Creates the runtime dirs (`secure-code-review-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **only for each allowed recipient**. The ACL is made
   visible as the contact cards: `lead` gets `outbox/finder/`, `outbox/verifier/`,
   `outbox/user/`; `finder` and `verifier` each get only `outbox/lead/`. There is
   no `outbox/verifier/` under `finder/`, and vice versa — the wall is physical
   on disk.
4. **Installs per-type turn detection** — the Claude Stop hook for `lead`, the
   Codex `notify` hook for `finder`, and (once you clear `capture: none`) pane
   polling for `verifier`.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles
   stale/dead/silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and `serve` hints:

```
:: swarm 'secure-code-review' is up with 3 agent(s)
:: attach with:  tmux attach -t <lead-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/secure-code-review.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents, so it must **never** be exposed on `0.0.0.0` without a token.
See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop (and keep
> `capture: none`) and you can watch the whole find→confirm→report loop route
> mail with no API keys — the mechanics are identical.

---

## 5. Drive the review

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the lead's final findings table as mail
(rather than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/secure-code-review.yaml
```

This rewrites the `user` contact card in the lead's `outbox/user/about.md` to
`Status: available`, so the lead sees you're reachable. (While away, mail to you
is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the target into the swarm, addressed to the lead:

```bash
./agentainer send -c examples/secure-code-review.yaml --to lead \
  "Review this PR diff: /path/to/repo (branch: feature/auth) — focus on the \
   session and payment handlers."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the lead, then — because the
inbox was empty — **released into `inbox/`** and the lead is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list:
finder, verifier, user).

### The mail flowing

Watching the log (§6), you'll see the two-stage review advance one turn at a
time. Each arrow is a `stop → sweep → route → release → nudge` cycle:

1. **lead receives the target.** It reads `inbox/`, acknowledges you briefly, and
   writes a brief into `outbox/finder/` asking for a candidate list (hypothesis,
   class, file:line, why-it-matters). On stop, that routes to the finder.
2. **finder hunts and proposes.** It reads its inbox, reads the actual code, and
   writes the candidate list back into `outbox/lead/`. On stop, that routes to the
   lead. It never confirms or grades.
3. **lead briefs the verifier.** It forwards the diff + the finder's candidate
   list into `outbox/verifier/` and asks for a verdict on *each*. On stop, that
   routes to the verifier.
4. **verifier judges.** It independently reads the cited code, and writes back a
   per-candidate verdict (CONFIRMED with exact path + trigger + severity, or
   REJECTED with the reason). On stop, that routes to the lead. It never invents
   findings of its own.
5. **lead consolidates.** It de-dupes, drops all REJECTED, sorts confirmed by
   severity, and writes the final findings table into `outbox/user/`. On stop,
   that's delivered to your `user` mailbox (visible with `agentainer user inbox`,
   or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion. The
finder and verifier never see each other's mail; only the lead does.

> If you *don't* send a target, the agents just sit in standby (that's the point
> of the standby prompt). The review only moves when real mail arrives — this
> swarm has no periodic pings to self-start it.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/secure-code-review.yaml
```

```
swarm: secure-code-review   root: ./secure-code-review-workspace
  lead     (claude) up idle queue=0 unread=0 talks=finder, verifier, user
  finder   (codex)  up idle queue=0 unread=1 talks=lead
  verifier (gemini) up idle queue=0 unread=0 talks=lead
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct the flow):

```bash
./agentainer logs -c examples/secure-code-review.yaml            # whole swarm, last 20
./agentainer logs -c examples/secure-code-review.yaml -f         # follow live
./agentainer logs verifier -c examples/secure-code-review.yaml   # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox lead -c examples/secure-code-review.yaml
```

Prints the one released message (headers + body), or `lead: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue lead -c examples/secure-code-review.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux
session:

```bash
./agentainer attach verifier -c examples/secure-code-review.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom
— handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or by sending notes
into an agent's inbox.

- **Push back on the verdict.** `./agentainer send -c examples/secure-code-review.yaml
  --to lead "The verifier REJECTED the IDOR candidate — have it re-check the
  tenant-scoping middleware at auth/middleware.py:42; the guard only runs on GET."`
  The lead forwards the escalation to the verifier for a final ruling.
- **Widen the finder's scope.** `./agentainer send -c examples/secure-code-review.yaml
  --to lead "Also have the finder look at the webhook signature verification."`
  The lead relays it down to the finder (not to the verifier).
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/secure-code-review.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/secure-code-review.yaml     # resume is the default
```

On `up`, Agentainer reads `secure-code-review-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the lead,
`codex resume <id>` for the finder, and (where supported) the verifier's resume
for gemini. A resumed agent is *not* re-sent the standby prompt (its prior
context — including the target and the in-flight verdicts — is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/secure-code-review.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Hand the finder and verifier the same source checkout

Right now each agent has a private workdir. If you want them reviewing the *same*
clone (so the finder's `file:line` citations match what the verifier opens), give
both a `workdir:` pointing at one shared dir — e.g. add `workdir:
./secure-code-review-workspace/repo` to `finder` and `verifier`. The loader then
namespaces their mailboxes (`finder-inbox/`, `verifier-inbox/`, …) so the on-disk
layout stays unambiguous; the models never see the prefix. See
[`custom-workspace.md`](./custom-workspace.md).

### Swap models

The `type` selects both the CLI family and the turn-detection mode. The shipped
config already mixes three families — `lead` on `claude`, `finder` on `codex`,
`verifier` on `gemini` — which is itself a [`multi-llm-swarm.md`](./multi-llm-swarm.md)
pattern: different models for hunt vs. judge reduces correlated blind spots.
- `gemini`/`hermes` need `capture: pane` (pane polling) since they have no
  completion hook — and remember to clear the `defaults.capture: none` line (see
  §3).
- `finder: type: claude` (or `hermes`) to put hunting on a different model than
  the lead.
- Any `type`/`command` mismatch wedges the agent (no turn-completion signal). See
  [`cli-reference.md`](../cli-reference.md).

### Tune the ACL

- To let the `finder` or `verifier` escalate straight to `user` (not only via the
  lead), add `user` to its `can_talk_to`. Mind that this widens the human-facing
  surface and, more importantly, **breaks the finder↔verifier wall** only if you
  also add one of them to the other's list — don't, unless you mean to.
- To add a third specialist (e.g. a `remediator` that writes the fix) keep it
  spoke-only: `can_talk_to: [lead]`. See
  [`delegation-pipeline.md`](./delegation-pipeline.md) for hub-and-spoke routing
  patterns.

---

## 10. Tips & footguns

- **Keep the finder/verifier wall intact.** Only `lead` lists `user` *and* only
  `lead` lists both specialists. That gives you a single funnel and a verifier
  that judges the finder's list without ever co-authoring it. If the finder tries
  to mail `verifier` (or vice versa), the orchestrator bounces it (ACL) and drops
  a `system` note in its inbox explaining who it *can* message — the model
  self-corrects in-band.

- **The `capture: none` default is a mock default, not a free lunch.** It's safe
  for key-free demos and auto-upgrades for `claude`/`codex`, but a real `gemini`
  verifier with `capture: none` gets no pane polling, so its verdicts won't route
  until manually nudged. For any live gemini run, drop the line or set
  `capture: pane` on the verifier. A `lead`/`finder` sitting `busy` with `unread`
  mail is the tell that turn detection isn't firing (usually a `type`/`command`
  mismatch). See [`configuration.md`](../configuration.md).

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If the chain stalls at the verifier, check its `capture` mode first.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/secure-code-review.yaml
  ./agentainer remove-session -c examples/secure-code-review.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files in
  `secure-code-review-workspace/` or your config.

- **Availability shapes the ending.** If `user` is **away** when the lead
  finishes, your findings table is *held* (with a `system` "the user is away" ack
  to the lead) rather than lost — read it later with `agentainer user inbox` or
  flip yourself available and it's delivered.

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
- [`ui-guide.md`](../ui-guide.md) — the mail-app control plane.
- [`cli-reference.md`](../cli-reference.md) — every subcommand.
- [`configuration.md`](../configuration.md) — the full config schema.
- Related secure-review swarms: [`security-audit.md`](./security-audit.md),
  [`pr-review-gate.md`](./pr-review-gate.md), [`vuln-triage.md`](./vuln-triage.md).
- `examples/secure-code-review.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

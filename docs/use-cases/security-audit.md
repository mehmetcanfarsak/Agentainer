# Use case: the application security audit swarm

A concrete, end-to-end walkthrough of the shipped `examples/security-audit.yaml`
swarm — a **lead** auditor orchestrates an application security review of a
codebase: a **recon** agent maps the attack surface, a **static** agent reviews
the code for OWASP-top-10 style defects, a **threatmodel** agent builds a
STRIDE-style threat model from their output, and a **reporter** writes the final
findings report to you. It's the canonical "delegate → do the work → synthesize →
report" loop, wired entirely through Agentainer's file-based mail model.

Everything below is based on the actual contents of
`examples/security-audit.yaml` and the shipped CLI (`lib/cli.py`) and mailroom
(`lib/mail.py`). No API keys are needed to understand the mechanics; to run it
*for real* you supply the coding-CLI commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md)
> first, then the four-folders recap in the repo `README.md`. The one-line
> version: an agent **reads a file** to receive mail and **writes a file** to send
> it; the orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The topology

```
            recon ─────┐
                       │
            static ────┼──▶ lead ──▶ reporter ──▶ user
                       │                 ▲     │
            threatmodel┘                 └─────┘
                  (reporter can ask lead for clarification)
```

Five agents, one directed flow:

1. **`user` → `lead`** — you send the target repo and a one-line description.
2. **`lead` → `recon`** — the lead asks recon to map the attack surface.
3. **`lead` → `static`** — the lead hands recon's map to static for code review.
4. **`lead` → `threatmodel`** — the lead sends both the map and the findings to
   threatmodel to build the STRIDE model.
5. **`lead` → `reporter`** — the lead forwards the consolidated material; the
   reporter writes the report and sends it to **`user`**.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. `recon`, `static`, and `threatmodel` can *only* talk to
`lead`; only `lead` and `reporter` may reach `user`. An agent that tries to mail
a peer it isn't allowed to is bounced back as a `system` message and filed in
`failed/` (see §7).

---

## 2. The config, explained

Here is `examples/security-audit.yaml` in full:

```yaml
# 🛡️ Security audit -- a lead orchestrates an application security review of a
# codebase/repo: recon the attack surface, run static analysis, build a threat
# model, then write the report.
# Real agents: commands launch the actual CLIs (claude / codex / gemini / hermes). For a key-free demo, swap each `command` for a mock bash loop.
swarm:
  name: security-audit
  root: ./security-audit-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: lead
    type: claude
    can_talk_to: [recon, static, threatmodel, reporter, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the LEAD SECURITY AUDITOR. ...
  - name: recon
    type: claude
    can_talk_to: [lead]
    command: "claude --dangerously-skip-permissions"
    role: "You are the RECON AGENT. Map the attack surface ..."
  - name: static
    type: codex
    can_talk_to: [lead]
    command: "codex --yolo"
    role: "You are the STATIC ANALYSIS AGENT. Review the code for OWASP-top-10 ..."
  - name: threatmodel
    type: claude
    can_talk_to: [lead]
    command: "claude --dangerously-skip-permissions"
    role: "You are the THREAT MODELER. Build a STRIDE-style threat model ..."
  - name: reporter
    type: claude
    can_talk_to: [lead, user]
    command: "claude --dangerously-skip-permissions"
    role: "You are the REPORT AUTHOR. Write the final report to the user ..."
```

Field by field:

### `swarm`
- **`name: security-audit`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./security-audit-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent gets
  `security-audit-workspace/<name>/` as its workdir (created on `up`), and its
  mailbox folders live alongside. Orchestrator state goes under
  `security-audit-workspace/.agentainer/` (never commit it).

  > **Repo access note:** the agents that *read your code* (`recon`, `static`) and
  > the one that *synthesizes* it (`threatmodel`) need to reach the target repo.
  > The simplest setup is to put the target repo somewhere under the swarm `root`
  > (or a shared parent) and pass its path in your `send` message. Alternatively,
  > point an agent's `workdir` at the repo with the `workdir:` field — see
  > [`custom-workspace.md`](../use-cases/custom-workspace.md). The agents never
  > assume the path; you tell them exactly where it is.

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, and it's ultimately keyed off each
  agent's `type`. For `claude` and `codex`, whose CLIs support a completion
  **hook**, setting `capture: none` is a footgun — so the config loader
  *upgrades* it back to `hook` and prints a warning at `up`
  (`capture: none on a claude agent gives the orchestrator no way to detect turn
  completion; using the type's default: capture: hook.`). Net effect here: all
  five agents use their hook.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent
  below states its own list explicitly, so this default is just a safe floor.

### `lead` (type: `claude`)
- **`can_talk_to: [recon, static, threatmodel, reporter, user]`** — the lead is
  the hub: it can delegate to the three analysis agents, brief the reporter, and
  it is (with the reporter) **one of only two agents that can talk to `user`**.
  Keep the human-facing surface tight — raw findings always flow through the lead
  and the reporter, never straight from an analyst to you.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command, e.g. a
  shell alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the lead's standing identity: acknowledge the target, then sequence
  recon → static → threatmodel → reporter, de-duplicating as it goes. On `up`
  this becomes the agent's first prompt, wrapped in a **standby notice** ("no task
  yet — don't send anything, you'll be notified"), so the lead waits for your
  target instead of proactively mailing peers. The role includes a **MAILBOX**
  reminder of the read/write protocol.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at
  `up`).

### `recon` (type: `claude`)
- **`can_talk_to: [lead]`** — can only report back to the lead. It maps the
  attack surface (endpoints, auth, data flows, deps) and cites `file:line`, but
  deliberately does *not* review code for defects — that's `static`'s job, so the
  two never overlap.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.

### `static` (type: `codex`)
- **`can_talk_to: [lead]`** — can only report back to the lead. It reviews the
  code for OWASP-top-10 style defects (auth, input handling, secrets,
  injection) with `file:line` evidence and a severity per finding.
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `threatmodel` (type: `claude`)
- **`can_talk_to: [lead]`** — can only report back to the lead. It builds a
  STRIDE-style threat model anchored to the recon map and the static findings, so
  its output is grounded in evidence the other two actually produced.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.

### `reporter` (type: `claude`)
- **`can_talk_to: [lead, user]`** — the reporter writes the final report and is
  the **only analysis-adjacent agent allowed to reach `user`**. If something from
  the lead is missing or contradictory, it asks the lead for clarification rather
  than guessing. Its `role` includes the **MAILBOX** reminder.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.

### What's *not* in this config
- **No `periodically_ping_seconds`.** None of the agents has a periodic ping
  configured, so no agent is auto-nudged on a timer while idle — the pipeline is
  purely event-driven off real mail. (If you wanted the lead to poke a slow
  analyst, you'd add `periodically_ping_seconds: 300` to it.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/security-audit.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for all five agents).
2. Creates the runtime dirs (`security-audit-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: the lead gets
   `outbox/recon/`, `outbox/static/`, `outbox/threatmodel/`, `outbox/reporter/`,
   `outbox/user/`; each analyst gets `outbox/lead/`; the reporter gets
   `outbox/lead/`, `outbox/user/`.
4. **Installs per-type turn detection** — the Claude Stop hook for lead, recon,
   threatmodel, and reporter, plus the Codex `notify` hook for static.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'security-audit' is up with 5 agent(s)
:: attach with:  tmux attach -t <lead-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/security-audit.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only bind (`127.0.0.1`). See the `README.md` "control-plane UI" section
and [`ui-guide.md`](../ui-guide.md). **Never bind the UI to `0.0.0.0` without a
token** — it can type into agents that may run
`--dangerously-skip-permissions`/`--yolo` (CLAUDE.md §18).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole pipeline route mail with no API keys — the mechanics are identical.

---

## 4. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the final report as mail (rather than have it
held), turn yourself available first:

```bash
./agentainer user available -c examples/security-audit.yaml
```

This rewrites the `user` contact card in the reporters' `outbox/user/about.md` to
`Status: available`, so the reporter sees you're reachable. (While away, mail to
you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the target into the swarm, addressed to the lead:

```bash
./agentainer send --to lead "Audit the repo at /srv/repos/flask-api; it's a Flask API with Postgres."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the lead, then — because the
inbox was empty — **released into `inbox/`** and the lead is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§5), you'll see the audit advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **lead receives the target.** It acknowledges you, then writes a delegation
   into `outbox/recon/` with the repo path + description. On stop, that routes to
   recon and recon is nudged.
2. **recon maps the attack surface.** It reads the repo, writes its map into
   `outbox/lead/`. On stop, that routes back to the lead.
3. **lead hands the map to static.** It writes a delegation (map + focus areas)
   into `outbox/static/`. On stop, that routes to static.
4. **static reviews the code.** It writes OWASP findings into `outbox/lead/`. On
   stop, that routes back to the lead.
5. **lead hands map + findings to threatmodel.** It writes both into
   `outbox/threatmodel/`. On stop, that routes to threatmodel.
6. **threatmodel builds the STRIDE model.** It writes the model into
   `outbox/lead/`. On stop, that routes back to the lead.
7. **lead forwards everything to reporter.** It writes the consolidated material
   into `outbox/reporter/`. On stop, that routes to the reporter.
8. **reporter writes the report and sends it to you.** It writes the final report
   into `outbox/user/`. On stop, that's delivered to your `user` mailbox (you'll
   see it with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a target, the agents just sit in standby (that's the point
> of the standby prompt). The audit only moves when real mail arrives — this swarm
> has no periodic pings to self-start it.

---

## 5. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/security-audit.yaml
```

```
swarm: security-audit   root: ./security-audit-workspace
  lead (claude) up idle queue=0 unread=0 talks=recon, static, threatmodel, reporter, user
  recon (claude) up idle queue=0 unread=1 talks=lead
  static (codex) up idle queue=0 unread=0 talks=lead
  threatmodel (claude) up idle queue=0 unread=0 talks=lead
  reporter (claude) up idle queue=0 unread=0 talks=lead, user
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/security-audit.yaml          # whole swarm, last 20
./agentainer logs -c examples/security-audit.yaml -f        # follow live
./agentainer logs static -c examples/security-audit.yaml    # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox lead -c examples/security-audit.yaml
```

Prints the one released message (headers + body), or `lead: inbox is empty`.

**The human-facing report** — once the reporter finishes, read what landed in your
mailbox:

```bash
./agentainer user inbox -c examples/security-audit.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach static -c examples/security-audit.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom
— handy for un-sticking an agent, but the mail model is the normal path.)

---

## 6. Iterate

The audit is a judgment call, not a fixed script — and the lead is built to be
steered:

- **Refine the brief mid-flight.** After recon reports, you can send the lead a
  follow-up (`agentainer send --to lead "..."`) narrowing scope — e.g. "ignore
  the admin panel, focus on the public auth endpoints." The lead folds it into its
  sequencing.
- **Ask for specifics.** If the reporter's report is too high-level, send the
  lead a nudge: "have static re-check the JWT verification path with file:line."
  The lead re-delegates without you touching the analysts directly.
- **Recover a stuck agent.** If an agent pins `busy` with unread mail (a
  tell that its turn-detection didn't fire), check for a `type`/`command` mismatch
  and, if needed, force-idle it to advance the queue:
  ```bash
  ./agentainer idle static -c examples/security-audit.yaml
  ```
  See the footguns in §7.

---

## 7. Tips & footguns

- **Keep the lead and reporter the only `user`-facing agents.** Only `lead` and
  `reporter` list `user` in `can_talk_to`. That gives you a single report funnel:
  raw findings always pass through synthesis before they reach you. If an analyst
  tries to mail `user` directly, the orchestrator bounces it (ACL) and drops a
  `system` note in its inbox explaining who it *can* message — the model
  self-corrects in-band.

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

- **Give the analysts the repo.** `recon` and `static` read *your* code, so their
  workdir or the path you pass in `send` must put the target within reach. A
  sandbox that can't see the source produces an empty attack surface. See
  [`custom-workspace.md`](../use-cases/custom-workspace.md).

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down             -c examples/security-audit.yaml
  ./agentainer remove-session  -c examples/security-audit.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

---

## 8. Customize

- **Add a `secrets` / `dep-scan` agent.** Drop a sixth agent into `agents:` that
  scans for hardcoded credentials and vulnerable dependencies, give it
  `can_talk_to: [lead]`, and add `secrets` (and/or `depscan`) to the lead's
  `can_talk_to` so the lead can sequence it alongside recon/static. The lead's
  role already says "de-duplicate and flag conflicts," so it'll fold the extra
  findings in without further changes.

- **Swap models.** Change any agent's `type`/`command` to another supported CLI —
  `gemini --yolo` (pane-captured) or `hermes`. For a `gemini`/`hermes` agent,
  set `capture: pane` on that agent (they have no completion hook). The lead and
  reporter are the strongest "reasoning" roles, so keep `claude` there if you
  want a single strong model driving the narrative; let the analysts be the
  cheaper CLIs. See [`multi-llm-swarm.md`](../use-cases/multi-llm-swarm.md).

- **Tune the ACL.** The current graph is strict hub-and-spoke: analysts never talk
  to each other. If you want `static` to push its findings straight to
  `threatmodel` (skipping a hop through the lead), add `threatmodel` to `static`'s
  `can_talk_to` — but the lead's "de-duplicate" role assumes it sees everything,
  so expect to adjust the lead's sequencing if you loosen spokes.

- **Parallelize recon + static.** For a large repo, you could run two `recon`
  agents against different areas (add `recon_b`, `can_talk_to: [lead]`) and let
  the lead merge — same pattern as the bug-hunt swarm. See
  [`delegation-pipeline.md`](../use-cases/delegation-pipeline.md) for the
  fan-out/fan-in pattern.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the file-based mail model in depth.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — conversations resume by
  default; what's recorded and how to reset.
- [`delegation-pipeline.md`](../use-cases/delegation-pipeline.md) — the
  delegate → do → report pattern this swarm is built on.
- [`multi-llm-swarm.md`](../use-cases/multi-llm-swarm.md) — mixing Claude/Codex/
  Gemini/Hermes in one swarm.
- [`custom-workspace.md`](../use-cases/custom-workspace.md) — pointing an agent's
  `workdir` at your real repo so it can read the source.

# Use case: the data-pipeline builder swarm

A concrete, end-to-end walkthrough of the shipped
`examples/data-pipeline-builder.yaml` swarm — a four-agent assembly line that
turns a human's "source → destination" spec into a working ETL pipeline. A
**hub architect** takes the request, a **designer** lays out the DAG + target
schema, an **implementer** writes the pipeline code, and a **tester** writes the
data-quality / contract tests. The architect delivers the finished pipeline back
to you.

Everything below is based on the actual contents of
`examples/data-pipeline-builder.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Data engineers, analytics engineers, and platform teams who want a structured
way to go from a loose source→destination ask to a tested, idempotent pipeline
without doing every step themselves. The swarm encodes the discipline that makes
pipelines safe — a single owner of the contract, a designer who fixes the
schema before code is written, and a tester who decides whether the output is
trustworthy — while the agents do the actual typing.

It is deliberately a **hub-and-spoke**, not a free-for-all: every request and
every deliverable passes through the architect, so the contract has exactly one
authority. Swapping in a real `monitoring` agent (see §7) is a one-line config
change.

---

## 2. The topology

```
          user
            |
         architect                 (the hub: talks to designer, implementer, tester, user)
          /    |    \
   designer  implementer  tester
               \______/
         (share ONE repo working directory)
```

Four agents, one directed flow:

1. **`user` → `architect`** — you send the source→destination spec.
2. **`architect` → `designer`** — the architect restates the spec and asks for
   the DAG + target schema + contracts.
3. **`designer` → `architect`** — the designer returns the design (`DESIGN.md`).
4. **`architect` → `implementer`** (with the design) **and `architect` → `tester`**
   (with the contracts) — the build and test work is kicked off in parallel, but
   both report back to the architect.
5. **`tester` ↔ `implementer`** — found a defect, the tester tells the
   implementer; the implementer fixes and re-reports. Both always route through
   the architect's purview.
6. **`architect` → `user`** — once the data-quality checks pass, the architect
   delivers the finished pipeline summary to you.

The routing above is *enforced* by each agent's `can_talk_to` list. An agent can
only deliver to names on its own list; anything else is bounced back as a
`system` message and filed in `failed/` (see §7). Notably, `designer`,
`implementer`, and `tester` **never** talk to `user` directly — only the
architect does.

---

## 3. The config, explained

Here is `examples/data-pipeline-builder.yaml` in full:

```yaml
swarm:
  name: data-pipeline
  root: ./pipeline-workspace
defaults:
  can_talk_to: []            # tightened per agent below
agents:
  - name: architect
    type: claude
    can_talk_to: [designer, implementer, tester, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the DATA PIPELINE ARCHITECT and the only agent who talks to the
      user. ... (restates the spec, coordinates designer/implementer/tester) ...

  - name: designer
    type: claude
    can_talk_to: [architect]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the PIPELINE DESIGNER. ... (DAG + target schema + contracts) ...

  - name: implementer
    type: codex
    can_talk_to: [architect, tester]
    command: "codex --yolo"
    workdir: ./pipeline-workspace/repo
    role: |
      You are the PIPELINE IMPLEMENTER. Build the ETL pipeline ... in your shared
      repo working directory. ...

  - name: tester
    type: codex
    can_talk_to: [architect, implementer]
    command: "codex --yolo"
    workdir: ./pipeline-workspace/repo
    role: |
      You are the DATA-QUALITY / CONTRACT TESTER. Write the tests ...
```

Field by field:

### `swarm`
- **`name: data-pipeline`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./pipeline-workspace`** — the parent directory for the agents'
  working directories and mailboxes. The architect's and designer's workdirs
  default to `pipeline-workspace/architect` and `pipeline-workspace/designer`;
  the implementer and tester **share** `pipeline-workspace/repo` (see the
  shared-workdir note below). Orchestrator state goes under
  `pipeline-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent
  below states its own list explicitly, so this default is just a safe floor.

### `architect` (type: `claude`)
- **`can_talk_to: [designer, implementer, tester, user]`** — the architect is the
  hub: it delegates to the three specialists and is the **only agent that can
  talk to `user`**. That last part matters — keep the human-facing surface to a
  single agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command, e.g. a
  shell alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the architect waits for your spec instead of
  proactively mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `designer` (type: `claude`)
- **`can_talk_to: [architect]`** — the designer only reports back to the
  architect. It deliberately cannot reach the implementer, the tester, or the
  `user`; the contract is owned by one place.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **`role`** — "produce the DAG + target schema + contracts, write them to
  `DESIGN.md`, and name the assertions the tester should enforce."
- **Turn detection:** `claude` → Stop hook.

### `implementer` (type: `codex`)
- **`can_talk_to: [architect, tester]`** — the implementer reports progress to
  the architect and hands off to the tester, but cannot reach the `user` or the
  designer directly.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`workdir: ./pipeline-workspace/repo`** — the implementer shares the
  **same** working directory as the tester (the pipeline codebase). Both agents'
  mailboxes are therefore auto-namespaced with a `<name>-` prefix (see the
  shared-workdir note below).
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `tester` (type: `codex`)
- **`can_talk_to: [architect, implementer]`** — the tester reports verdicts to
  the architect and defects to the implementer, but cannot reach the `user` or
  the designer directly.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`workdir: ./pipeline-workspace/repo`** — shares the implementer's repo
  working directory; mailboxes auto-namespaced alongside the implementer's.
- **Turn detection:** `codex` → `notify` hook.

### The shared-workdir note (important)

`implementer` and `tester` both resolve to `pipeline-workspace/repo`. The config
loader (`lib/config.py:SwarmConfig.__post_init__`) counts agents per workdir and
flags any directory shared by two or more; `mail_paths()` then prefixes every
mailbox folder with the agent's name, so the on-disk layout becomes:

```
pipeline-workspace/
  repo/
    implementer-inbox/  implementer-outbox/  implementer-read/  implementer-sent/  implementer-failed/
    tester-inbox/       tester-outbox/       tester-read/       tester-sent/       tester-failed/
    <the actual pipeline source + tests>     # unprefixed, shared
  architect/   (inbox outbox read sent failed)   <- private, unprefixed
  designer/    (inbox outbox read sent failed)   <- private, unprefixed
```

This namespacing is **orchestrator-internal**: the model never sees or computes
it. Every nudge and first prompt hands the agent its *exact* computed paths
(`implementer` sees `.../repo/implementer-inbox`, `tester` sees
`.../repo/tester-inbox`), so a shared workspace is indistinguishable from a
private one from the model's point of view. The project source itself is shared
on disk — so the two agents coordinate through mail and don't clobber each
other's files. The `can_talk_to` ACL stays cooperative, not OS isolation. For the
full treatment see [`custom-workspace.md`](./custom-workspace.md).

### What's *not* in this config
- **No `capture` overrides.** `architect` and `designer` are `claude` (Stop-hook
  capture); `implementer` and `tester` are `codex` (`notify` hook). The loader's
  default `capture` would be `auto` → the type's natural mode, so everything
  fires its turn-completion signal and the stop→sweep→route→nudge clock keeps
  running. (If you swapped in a `gemini` agent you'd add `capture: pane`.)
- **No `pings`.** The swarm is purely event-driven off real
  mail — it only moves when you send a spec. (Add a ping to the architect if you
  want a stale-pipeline nag.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/data-pipeline-builder.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the shared-
   workdir note for `implementer`/`tester`).
2. Creates the runtime dirs (`pipeline-workspace/.agentainer/…`: log, queue, run,
   sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. Because `implementer`
   and `tester` share a workdir, their folders are created *namespaced*
   (`implementer-inbox/` etc.). The `outbox/<peer>/about.md` contact card *is*
   the ACL made visible: the architect gets `outbox/designer/`,
   `outbox/implementer/`, `outbox/tester/`, `outbox/user/`; the implementer gets
   `outbox/architect/`, `outbox/tester/`; etc.
4. **Installs per-type turn detection** — the Claude Stop hook for `architect` and
   `designer`, and the Codex `notify` hook for `implementer` and `tester`.
5. **Opens one tmux session per agent**, `cd`'d into its workdir (the two codex
   agents both land in `pipeline-workspace/repo`), running its `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'data-pipeline' is up with 4 agent(s)
:: attach with:  tmux attach -t <architect-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/data-pipeline-builder.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents, so it must **never** be exposed on `0.0.0.0` without a token.
See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole pipeline route mail with no API keys — the mechanics are identical.

---

## 5. Drive a spec

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the architect's finished-pipeline summary as
mail (rather than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/data-pipeline-builder.yaml
```

This rewrites the `user` contact card in the architect's `outbox/user/about.md`
to `Status: available`, so the architect sees you're reachable. (While away, mail
to you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the source→destination spec into the swarm, addressed to the architect:

```bash
./agentainer send --to architect -c examples/data-pipeline-builder.yaml \
  "Load daily Stripe payout CSVs from s3://acme-raw/stripe/ into a Postgres \
   'payouts' table; dedupe on payout_id; partition by settlement date."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the architect, then — because
the inbox was empty — **released into `inbox/`** and the architect is **nudged**
(the protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **architect receives the spec.** It reads `inbox/`, restates it as a spec +
   acceptance list, and writes a delegation into `outbox/designer/`. On stop,
   that routes to the designer.
2. **designer lays out the DAG + schema.** It reads its inbox, writes
   `DESIGN.md`, and reports back into `outbox/architect/`. On stop, that routes to
   the architect.
3. **architect briefs builder + tester.** It writes the design into
   `outbox/implementer/` and the contracts into `outbox/tester/`. On stop, both
   route in parallel.
4. **implementer builds; tester tests.** The implementer writes the pipeline in
   the shared repo and hands off to the tester via `outbox/tester/`; the tester
   writes the data-quality tests, runs them, and reports defects back to the
   implementer (or escalates to the architect). On each stop, mail routes onward.
5. **architect finalizes.** Once the checks pass, it reads the verdict and writes
   the finished-pipeline summary into `outbox/user/`. On stop, that's delivered to
   your `user` mailbox (visible with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a spec, the agents just sit in standby (that's the point of
> the standby prompt). The pipeline only moves when real mail arrives — this
> swarm has no periodic pings to self-start it.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/data-pipeline-builder.yaml
```

```
swarm: data-pipeline   root: ./pipeline-workspace
  architect (claude) up idle queue=0 unread=0 talks=designer, implementer, tester, user
  designer  (claude) up idle queue=0 unread=1 talks=architect
  implementer (codex) up idle queue=0 unread=0 talks=architect, tester
  tester    (codex) up idle queue=0 unread=0 talks=architect, implementer
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/data-pipeline-builder.yaml          # whole swarm, last 20
./agentainer logs -c examples/data-pipeline-builder.yaml -f        # follow live
./agentainer logs implementer -c examples/data-pipeline-builder.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox architect -c examples/data-pipeline-builder.yaml
```

Prints the one released message (headers + body), or `architect: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue architect -c examples/data-pipeline-builder.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach designer -c examples/data-pipeline-builder.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom
— handy for un-sticking an agent, but the mail model is the normal path.)

**The shared repo** — the pipeline the agents are building lives in
`pipeline-workspace/repo/` (created for `implementer`/`tester`; the architect and
designer can be given the path if you want them to read it). Inspect the produced
`DESIGN.md`, the pipeline source, and the test suite there.

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or (more directly)
by sending notes into an agent's inbox.

- **Send a clarification to the architect.** Realized the grain is per-payout not
  per-file? `./agentainer send --to architect -c examples/data-pipeline-builder.yaml
  "Settlement date is the payout's settlement_date, not the file date; re-brief
  the designer."` The architect relays the change down the chain.
- **Ask the tester for the evidence.** `./agentainer send --to architect ... "Have
  the tester attach the failing-row counts."` — the architect forwards it.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/data-pipeline-builder.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/data-pipeline-builder.yaml     # resume is the default
```

On `up`, Agentainer reads `pipeline-workspace/.agentainer/sessions.yaml` (written
as each agent finished its first turn) and reattaches the recorded conversations
via each type's native resume: `claude --resume <id>` for the architect and
designer, `codex resume <id>` for the implementer and tester. A resumed agent is
*not* re-sent the standby prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/data-pipeline-builder.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a `monitoring` agent
Once the pipeline ships, you may want someone watching it. Add a fifth agent that
can read the architect's deliverable and owns alerting:

```yaml
  - name: monitoring
    type: claude
    can_talk_to: [architect, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the PIPELINE MONITOR. Once the architect delivers a pipeline,
      define the freshness/SLA and alerting for it (when is it "late", what
      anomalies matter), and report the runbook to outbox/user/. You never write
      pipeline code.
```
Then add `monitoring` to the architect's `can_talk_to` so it can be briefed.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `designer: type: codex` (or `hermes`/`gemini`) to put design on a different
  model than the architect.
- `tester: type: claude` if you want the test authoring on Claude while the
  implementer stays Codex.
- Remember: `gemini`/`hermes` need `capture: pane` (pane polling) since they
  have no completion hook.

### Tune the ACL
- To let the `tester` escalate straight to `user` (not only via the architect),
  add `user` to its `can_talk_to`. Mind that this widens the human-facing
  surface; the doc's convention keeps the architect the sole `user` contact.
- To make the designer unreachable from anyone but the architect (already the
  case here), leave its `can_talk_to: [architect]` — that's the one-place-owns-
  the-contract guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md)
  for mixing model families safely.

---

## 10. Tips & footguns

- **Keep the architect the only `user`-facing agent.** Only the architect lists
  `user` in `can_talk_to`. That gives you a single funnel: raw pipeline drafts
  and test verdicts always pass through review before they reach you. If the
  implementer or tester tries to mail `user` directly, the orchestrator bounces
  it (ACL) and drops a `system` note in their inbox explaining who they *can*
  message — the model self-corrects in-band.

- **The shared repo is shared for *files*, not just mail.** Namespacing only
  separates the *mailbox* folders. The pipeline source in `pipeline-workspace/
  repo/` is shared on disk by `implementer` and `tester`, so they can overwrite
  each other's edits and interleave commits in a shared git checkout. Coordinate
  through mail; don't assume isolation. (See [`custom-workspace.md`](./custom-workspace.md).)

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
  ./agentainer down           -c examples/data-pipeline-builder.yaml
  ./agentainer remove-session -c examples/data-pipeline-builder.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files in `pipeline-workspace/repo/`
  or your config.

- **Availability shapes the ending.** If `user` is **away** when the architect
  finishes, your pipeline summary is *held* (with a `system` "the user is away"
  ack to the architect) rather than lost — read it later with
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
- `examples/data-pipeline-builder.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

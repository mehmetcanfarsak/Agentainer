# Use case: Grant writer

A concrete, end-to-end walkthrough of the shipped
`examples/grant-writer.yaml` swarm — a four-agent grant-desk that turns a
human's research idea plus funder guidelines into a funding-ready proposal. A
**PI-advisor hub** takes your brief, a **writer** drafts the sections, a
**reviewer-sim** plays the skeptical grant reviewer and returns a scored
critique, and a **polisher** revises against that critique before the finished
proposal is delivered back to you. The whole adversarial review loop runs
through one owner so drafts never drift — and the YAML validates clean
(`config ok:`).

Everything below is based on the actual contents of
`examples/grant-writer.yaml` and the shipped CLI (`lib/cli.py`) and mailroom
(`lib/mail.py`). No API keys are needed to understand the mechanics; to run it
*for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

PIs, founders, and research leads who need to turn a loose idea and a funder's
call into a structured, defensible proposal without doing every section
themselves. The swarm encodes the discipline that makes proposals fundable — a
single owner of the scope, a drafter who stays inside the page/budget limits, a
reviewer who scores the weak spots adversarially, and a reviser who closes them
without inflating claims — while the agents do the actual writing.

It is deliberately a **hub-and-spoke**, not a free-for-all: the writer,
reviewer-sim, and polisher each report *only* to the PI-advisor. That guarantees
one authority owns the finished proposal and is the only agent that talks to
you. Distinct from its neighbours: [`academic-coauthor.md`](./academic-coauthor.md)
co-writes a paper *with* you; [`white-paper-research.md`](./white-paper-research.md)
produces a marketing/explainer white paper; [`rfp-response.md`](./rfp-response.md)
answers a vendor procurement request. This swarm drafts a **funding proposal**
and runs it through a simulated adversarial peer review with a scored critique.

---

## 2. The topology

```
          user
            | idea + funder guidelines
         pi_advisor                 (the hub: talks to writer, reviewer_sim, polisher, user)
          /    |    \
     writer  reviewer_sim  polisher
   (each spoke talks ONLY to the advisor)
```

Four agents, one directed flow:

1. **`user` → `pi_advisor`** — you send the research idea and the funder's
   constraints (agency, program, page limit, budget ceiling, deadlines, review
   criteria).
2. **`pi_advisor` → `writer`** — the advisor restates the idea + constraints as a
   one-page `SCOPE.md` brief and asks for a full draft (`PROPOSAL.md`).
3. **`writer` → `pi_advisor`** — the draft returns; the advisor forwards it to the
   reviewer.
4. **`pi_advisor` → `reviewer_sim`** — the advisor sends the draft + `SCOPE.md`
   and asks for a scored critique (`CRITIQUE.md`).
5. **`reviewer_sim` → `pi_advisor`** — the critique returns; the advisor forwards
   the draft + critique to the polisher.
6. **`pi_advisor` → `polisher`** — the advisor sends the draft + critique and asks
   for a revised proposal (`PROPOSAL_v2.md`) that closes the fatal weak spots.
7. **`polisher` → `pi_advisor`** — the revised proposal returns; the advisor
   sanity-checks it against `SCOPE.md` and the page/budget limits, then sends the
   finished proposal to **`user`**.

Note the spokes never talk to each other — the "review loop" is the advisor
relaying the draft and critique between them. That single-pivot design is what
keeps the draft from drifting. The routing above is *enforced* by each agent's
`can_talk_to` list (see §3); anything off-list is bounced back as a `system`
message and filed in `failed/`.

---

## 3. The config, explained

Here is `examples/grant-writer.yaml` (roles abbreviated for readability; the file
ships the full standing identities):

```yaml
swarm:
  name: grant-writer
  root: ./grant-writer-workspace
defaults:
  capture: none              # claude/codex/gemini auto-upgrade to their hook at `up`
  can_talk_to: []            # default ACL is "talk to no one"; opened per agent
agents:
  - name: pi_advisor
    type: claude
    can_talk_to: [writer, reviewer_sim, polisher, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the PI-ADVISOR, the hub ... you own the finished proposal and are
      the ONLY agent who talks to the user ... build a SCOPE.md, delegate to
      writer/reviewer_sim/polisher, and deliver the final proposal to user.

  - name: writer
    type: codex
    can_talk_to: [pi_advisor]
    command: "codex --yolo"
    role: |
      You are the WRITER ... draft Significance / Innovation / Approach / Budget
      Narrative into PROPOSAL.md, staying inside the page/budget limits ...
      report back only to the pi_advisor.

  - name: reviewer_sim
    type: gemini
    can_talk_to: [pi_advisor]
    command: "gemini --yolo"
    role: |
      You are the REVIEWER-SIM, a skeptical grant reviewer ... score each review
      criterion, name the 3-5 weakest spots, flag page/budget/scope violations,
      and state the fundable revision for each ... write CRITIQUE.md ... do NOT
      rewrite the proposal.

  - name: polisher
    type: claude
    can_talk_to: [pi_advisor]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the POLISHER ... revise PROPOSAL.md against CRITIQUE.md into
      PROPOSAL_v2.md, closing every fatal/serious weak spot without inflating
      claims ... report back only to the pi_advisor.
```

Field by field:

### `swarm`
- **`name: grant-writer`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./grant-writer-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent's workdir defaults to
  `grant-writer-workspace/<name>` (e.g. `grant-writer-workspace/writer`).
  Orchestrator state goes under `grant-writer-workspace/.agentainer/` (never
  commit it).

### `defaults`
Applied to every agent unless it overrides them.
- **`capture: none`** — the baseline. At `up`, the loader **auto-upgrades each
  agent to its type's natural capture mode** (see the per-agent turn-detection
  notes below), so completion signals fire and the stop→sweep→route→nudge clock
  keeps running. You don't have to set `capture` per agent.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `pi_advisor` (type: `claude`)
- **`can_talk_to: [writer, reviewer_sim, polisher, user]`** — the advisor is the
  hub: it delegates to the three specialists and is the **only agent that can
  talk to `user`**. That single human-facing surface is the point — keep raw
  drafts and critiques filtered through one owner (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command, e.g. a
  shell alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the advisor waits for your idea instead of proactively
  mailing peers. The role names the `SCOPE.md` brief and the exact four-step loop
  (brief→writer, draft→reviewer, draft+critique→polisher, polish→user).
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `writer` (type: `codex`)
- **`can_talk_to: [pi_advisor]`** — the writer only reports back to the advisor.
  It cannot reach the reviewer, the polisher, or the `user`; the draft is owned
  by one place.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "produce `PROPOSAL.md` with Significance, Innovation, Approach,
  and a Budget Narrative that fits the page limit and budget ceiling from
  `SCOPE.md`; if the idea can't fit, say so rather than cutting rigor."
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `reviewer_sim` (type: `gemini`)
- **`can_talk_to: [pi_advisor]`** — the reviewer only reports the critique back
  to the advisor. It cannot rewrite the proposal or reach any other agent.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **`role`** — "play the skeptical funder reviewer; score each stated review
  criterion, rank the 3-5 weakest spots, flag any page/budget/scope violations,
  and state the fundable revision for each; write `CRITIQUE.md`. Do NOT rewrite
  the proposal."
- **Turn detection:** `gemini` has no completion hook, so it uses **pane
  polling** (the auto-upgraded `capture: pane` mode). The supervisor polls the
  pane for the turn-end signal.

### `polisher` (type: `claude`)
- **`can_talk_to: [pi_advisor]`** — the polisher only reports the revised
  proposal back to the advisor; it must not negotiate with the reviewer or the
  writer (it flags factual contradictions to the advisor instead).
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **`role`** — "revise `PROPOSAL.md` against `CRITIQUE.md` into `PROPOSAL_v2.md`,
  closing every fatal/serious weak spot without inflating claims or drifting
  outside the page limit or budget ceiling; keep what the reviewer rated strong."
- **Turn detection:** `claude` → Stop hook.

### ACL enforcement (how the hub stays a hub)

The `can_talk_to` lists are the whole enforcement story, and they're
**cooperative, not OS isolation** (Decision D15). The orchestrator only ever
releases a sender's mail into the `outbox/<recipient>/` folders named on its own
list; an attempt to address anyone else is bounced as a `system` message and
filed in `failed/`. So when `writer` finishes `PROPOSAL.md`, it can write only
into `outbox/pi_advisor/` — the reviewer and polisher are simply not in its
filesystem. That's why a forgetful model can't sidestep the loop: the folders it
can see *are* the ACL. (A determined agent with raw filesystem access could
technically write another inbox, but well-behaved agents — and the file-model
nudges that re-state the recipient list every turn — stay on the graph.) For the
broader routing discussion see
[`delegation-pipeline.md`](./delegation-pipeline.md).

### What's *not* in this config
- **No shared workdir.** All four agents get their own private
  `grant-writer-workspace/<name>/` directory; their mailbox folders
  (`inbox/ outbox/ read/ sent/ failed/`) are created unprefixed. So there's no
  namespacing wrinkle here — contrast [`custom-workspace.md`](./custom-workspace.md),
  which covers the shared-workdir case. The only cross-agent artifact is the
  `SCOPE.md` / `PROPOSAL.md` / `CRITIQUE.md` handoff the advisor relays by mail.
- **No `pings`.** The swarm is purely event-driven off real mail — it only moves
  when you send an idea. (Add a `pings:` schedule to `pi_advisor` if you want a
  stale-proposal nag.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/grant-writer.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints `config ok:` and any warnings.
2. Creates the runtime dirs (`grant-writer-workspace/.agentainer/…`: log, queue,
   run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/about.md`
   contact card *is* the ACL made visible: the advisor gets
   `outbox/writer/`, `outbox/reviewer_sim/`, `outbox/polisher/`, `outbox/user/`;
   each spoke gets only `outbox/pi_advisor/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `pi_advisor`
   and `polisher`, the Codex `notify` hook for `writer`, and pane polling for
   `reviewer_sim` (the `gemini` `capture: pane` mode).
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'grant-writer' is up with 4 agent(s)
:: attach with:  tmux attach -t <pi_advisor-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/grant-writer.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents, so it must **never** be exposed on `0.0.0.0` without a token.
See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole review loop route mail with no API keys — the mechanics are
> identical.

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the finished proposal as mail (rather than
have it held), turn yourself available first:

```bash
./agentainer user available -c examples/grant-writer.yaml
```

This rewrites the `user` contact card in the advisor's `outbox/user/about.md` to
`Status: available`, so the advisor sees you're reachable. (While away, mail to
you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the idea + funder guidelines into the swarm, addressed to the
PI-advisor:

```bash
./agentainer send --to pi_advisor -c examples/grant-writer.yaml \
  "Idea: low-cost edge sensors for early wildfire detection. Funder: NSF SBIR \
   Phase I, 12pp, $275k."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the advisor, then — because the
inbox was empty — **released into `inbox/`** and the advisor is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the proposal advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **pi_advisor receives the idea.** It reads `inbox/`, builds `SCOPE.md`, and
   writes a delegation into `outbox/writer/`. On stop, that routes to the writer.
2. **writer drafts.** It reads its inbox, writes `PROPOSAL.md`, and reports back
   into `outbox/pi_advisor/`. On stop, that routes to the advisor.
3. **pi_advisor briefs the reviewer.** It writes the draft + `SCOPE.md` into
   `outbox/reviewer_sim/`. On stop, that routes to the reviewer-sim.
4. **reviewer_sim critiques.** It reads its inbox, writes `CRITIQUE.md`, and
   reports back into `outbox/pi_advisor/`. On stop, that routes to the advisor.
5. **pi_advisor briefs the polisher.** It writes the draft + critique into
   `outbox/polisher/`. On stop, that routes to the polisher.
6. **polisher revises.** It reads its inbox, writes `PROPOSAL_v2.md`, and reports
   back into `outbox/pi_advisor/`. On stop, that routes to the advisor.
7. **pi_advisor delivers.** It sanity-checks against `SCOPE.md` and the page/
   budget limits, then writes the finished proposal into `outbox/user/`. On stop,
   that's delivered to your `user` mailbox (visible with `agentainer user inbox`,
   or in the UI).

If the reviewer still flags a fatal flaw, the advisor loops the polish step again
before delivering. You don't relay anything by hand — the orchestrator releases
exactly one inbox message at a time and fires the next hop off each agent's turn
completion.

> If you *don't* send an idea, the agents just sit in standby (that's the point of
> the standby prompt). The review loop only moves when real mail arrives — this
> swarm has no periodic pings to self-start it.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/grant-writer.yaml
```

```
swarm: grant-writer   root: ./grant-writer-workspace
  pi_advisor (claude) up idle queue=0 unread=0 talks=writer, reviewer_sim, polisher, user
  writer     (codex)  up idle queue=0 unread=1 talks=pi_advisor
  reviewer_sim (gemini) up idle queue=0 unread=0 talks=pi_advisor
  polisher    (claude) up idle queue=0 unread=0 talks=pi_advisor
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/grant-writer.yaml            # whole swarm, last 20
./agentainer logs -c examples/grant-writer.yaml -f          # follow live
./agentainer logs polisher -c examples/grant-writer.yaml   # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox pi_advisor -c examples/grant-writer.yaml
```

Prints the one released message (headers + body), or `pi_advisor: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue pi_advisor -c examples/grant-writer.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach writer -c examples/grant-writer.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom
— handy for un-sticking an agent, but the mail model is the normal path.)

**The artifacts** — the proposal handoffs live in each agent's workdir:
`grant-writer-workspace/pi_advisor/SCOPE.md`,
`grant-writer-workspace/writer/PROPOSAL.md`,
`grant-writer-workspace/reviewer_sim/CRITIQUE.md`, and
`grant-writer-workspace/polisher/PROPOSAL_v2.md`. Inspect them as the loop
progresses.

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or (more directly)
by sending notes into an agent's inbox.

- **Send a clarification to the advisor.** Realized the budget ceiling is
  actually $250k, not $275k? `./agentainer send --to pi_advisor -c examples/grant-writer.yaml
  "Lower the budget ceiling to $250k in SCOPE.md and have the writer tighten the
  Budget Narrative."` The advisor relays the change down the loop.
- **Ask the reviewer for the evidence.** `./agentainer send --to pi_advisor ... "Have
  the reviewer_sim justify its lowest score in one sentence."` — the advisor
  forwards it.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/grant-writer.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/grant-writer.yaml     # resume is the default
```

On `up`, Agentainer reads `grant-writer-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for
`pi_advisor` and `polisher`, `codex resume <id>` for `writer`, and pane-restored
context for `reviewer_sim`. A resumed agent is *not* re-sent the standby prompt
(its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/grant-writer.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a second reviewer (dual-anonymous review)
A single reviewer-sim can be biased. Add a second, independent one and let the
advisor reconcile the two critiques:

```yaml
  - name: reviewer_sim_2
    type: gemini
    can_talk_to: [pi_advisor]
    command: "gemini --yolo"
    role: |
      You are a SECOND skeptical grant reviewer ... same CRITIQUE.md scoring job
      as the first, but assume the other reviewer may have missed a fatal flaw.
```
Then add `reviewer_sim_2` to the advisor's `can_talk_to`.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `writer: type: claude` (or `hermes`) to put drafting on a different model than
  the advisor.
- `reviewer_sim: type: codex` if you want the critique authoring on Codex instead
  of Gemini.
- Remember: `gemini`/`hermes` need pane polling (the auto-upgraded `capture:
  pane`), since they have no completion hook.

For safe mixing of model families across the spokes, see
[`multi-llm-swarm.md`](./multi-llm-swarm.md).

### Tune the ACL
- To let the `polisher` escalate a contradiction straight to `user` (instead of
  via the advisor), add `user` to its `can_talk_to`. Mind that this widens the
  human-facing surface; the doc's convention keeps the advisor the sole `user`
  contact.
- To enforce an even stricter gate, drop `user` from the advisor's `can_talk_to`
  temporarily while you debug the loop — nothing reaches you until you put it
  back.
- See [`configuration.md`](../configuration.md) for the full field reference, and
  [`delegation-pipeline.md`](./delegation-pipeline.md) for hub-and-spoke routing
  patterns.

---

## 10. Tips & footguns

- **Keep the advisor the only `user`-facing agent.** Only `pi_advisor` lists
  `user` in `can_talk_to`. That gives you a single funnel: raw drafts and scored
  critiques always pass through the hub's review before they reach you. If the
  writer or polisher tries to mail `user` directly, the orchestrator bounces it
  (ACL) and drops a `system` note in their inbox explaining who they *can*
  message — the model self-corrects in-band.

- **The review loop is relayed, not direct.** The header comment's
  "writer → reviewer_sim → polisher → advisor" is shorthand — in the actual ACL
  none of the spokes can address each other. The loop only works because the
  advisor forwards each artifact. If you ever add a direct spoke-to-spoke edge,
  you change the ownership guarantee in §2; do it deliberately.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. The `reviewer_sim` is the easy one to get wrong — `gemini` has no
  completion hook, so it relies on **pane polling**. If the reviewer seems stuck
  `busy` with `unread` mail, check that pane polling is actually detecting its
  turn end (a `type`/`command` mismatch would wedge it — see
  [`cli-reference.md`](../cli-reference.md)).

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/grant-writer.yaml
  ./agentainer remove-session -c examples/grant-writer.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' proposal artifacts in
  `grant-writer-workspace/` or your config.

- **Availability shapes the ending.** If `user` is **away** when the advisor
  finishes, your proposal is *held* (with a `system` "the user is away" ack to the
  advisor) rather than lost — read it later with `agentainer user inbox` or flip
  yourself available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions (`--dangerously-skip-permissions`,
  `--yolo`).

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- [`configuration.md`](../configuration.md) — the full field reference.
- [`academic-coauthor.md`](./academic-coauthor.md), [`white-paper-research.md`](./white-paper-research.md),
  [`rfp-response.md`](./rfp-response.md) — neighbouring writing swarms this one is distinct from.
- `examples/grant-writer.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

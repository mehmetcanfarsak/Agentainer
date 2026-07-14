# Use case: the comparison guide writer

A concrete, end-to-end walkthrough of the shipped
`examples/comparison-guide-writer.yaml` swarm — a three-agent research desk that
turns an **"X vs Y" request into a published buying guide**: an
`options_researcher` hub gathers the verifiable facts on two or more comparable
options, fans the same fact set out to a `guide_writer` (the "X vs Y" narrative
that helps a reader **choose**) and a `comparison_table_builder` (the side-by-side
spec/price table), then reconciles both into one guide.

Everything below is based on the actual contents of
`examples/comparison-guide-writer.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 0. Search intent — what people Google, and how this swarm answers it

This swarm is built for decision-stage queries where someone is *choosing between
two or more comparable options* and wants help deciding:

- **"X vs Y comparison"** / **"X vs Y: which is better?"** — e.g. "Notion vs
  Obsidian", "Figma vs Sketch", "Rust vs Go", "iPhone vs Pixel".
- **"best X vs Y comparison"** / **"X vs Y buying guide"** — a reader who wants the
  verdict *and* the reasoning, not just a spec dump.
- **"X vs Y for <use case>"** — "Notion vs Obsidian for personal knowledge
  management", "Figma vs Sketch for a two-person startup".
- **"X vs Y vs Z"** — three or more options (the hub handles any number ≥ 2).

What this swarm reliably produces: a top-line verdict, a head-to-head narrative on
the axes that matter, and a scannable comparison table — the three things that
answer a "which should I buy/pick" query in one place.

**How it differs from the two neighbouring use cases** (so you pick the right one):

| Swarm | What it produces | Who it's for |
| --- | --- | --- |
| `affiliate-product-reviews` | an honest **single-product** review + an affiliate comparison table of that one product vs its rivals | an affiliate/publisher monetising one product |
| `competitive-intel` | a **sales battlecard** on competitor **companies** (positioning, pricing, how-to-win) | a sales/PM team |
| **`comparison-guide-writer`** | a reader-facing **"X vs Y" buying guide** that helps a person **choose between two+ comparable options** | a blogger/editorial desk, a buyer's-guide site |

The sharp differentiators: this swarm (a) treats **both/all options symmetrically**
as equals to be compared, not one product to be reviewed; (b) writes for a
**reader deciding what to buy/pick**, not for a salesperson beating a competitor;
and (c) **reconciles prose + table** so they never disagree.

---

## 1. The topology

```
            you (user)
                │  "X vs Y" request
                ▼
        options_researcher  <--> everyone   (the hub; the only user-facing agent)
            /            \
           ▼              ▼
      guide_writer   comparison_table_builder
     (X vs Y prose)   (side-by-side table)
   ...the writer and table builder never talk to each other; only the
   options_researcher talks to the user.
```

Three agents, one directed flow:

1. **`user` → `options_researcher`** — you send the "X vs Y" request.
2. **`options_researcher` → `guide_writer`** + **`comparison_table_builder`** — the
   researcher gathers facts, then sends the *same* confirmed fact set to both.
3. **`guide_writer` → `options_researcher`** — the narrative returns to the hub.
4. **`comparison_table_builder` → `options_researcher`** — the table returns to the
   hub.
5. **`options_researcher` → `user`** — the hub reconciles prose + table and sends
   the finished buying guide back to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. An agent can only deliver to names on its own list; anything
else is bounced back as a `system` message and filed in `failed/` (see §7).

---

## 2. The config, explained

Here is `examples/comparison-guide-writer.yaml` in full:

```yaml
# ⚖️  Comparison guide writer -- a research desk that turns "X vs Y" into a buying guide.
swarm:
  name: comparison-guide-writer
  root: ./comparison-guide-writer-workspace

defaults:
  capture: none
  can_talk_to: []

agents:
  - name: options_researcher
    type: claude
    can_talk_to: [guide_writer, comparison_table_builder, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the OPTIONS RESEARCHER, the hub of a comparison-guide desk ...
  - name: guide_writer
    type: codex
    can_talk_to: [options_researcher]
    command: "codex --yolo"
    role: |
      You are the GUIDE WRITER. Using ONLY the confirmed facts ... write the
      "X vs Y" buying-guide narrative ...
  - name: comparison_table_builder
    type: codex
    can_talk_to: [options_researcher]
    command: "codex --yolo"
    role: |
      You are the COMPARISON TABLE BUILDER. Using ONLY the researcher's confirmed
      facts, build a scannable side-by-side comparison table ...
```

(See the file for the full `role:` text — every agent's standing instructions.)

Field by field:

### `swarm`
- **`name: comparison-guide-writer`** — the swarm's name (shows up in `status`,
  logs, sessions).
- **`root: ./comparison-guide-writer-workspace`** — the parent directory for the
  agents' working directories and mailboxes. Each agent gets
  `comparison-guide-writer-workspace/<name>/` as its workdir (created on `up`),
  and its mailbox folders live alongside. Orchestrator state goes under
  `comparison-guide-writer-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture` is
  how Agentainer knows a turn finished. For `claude` and `codex`, whose CLIs
  support a completion **hook**, setting `capture: none` is a footgun — so the
  config loader *upgrades* it back to `hook` and prints a warning at `up`
  (`capture: none on a claude agent gives the orchestrator no way to detect turn
  completion; using the type's default: capture: hook.`). Net effect here: all
  three agents use their hook.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `options_researcher` (type: `claude`)
- **`can_talk_to: [guide_writer, comparison_table_builder, user]`** — the hub: it
  delegates to both writers and is the **only agent that can talk to `user`**.
  The writer and table builder deliberately cannot reach each other or the user.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code.
  (Placeholder — substitute your own launch command. Treat command strings as
  sensitive; they may embed keys.)
- **`role`** — the standing identity: do the fact-finding in `FACTS.md`, fan the
  same fact set to both writers, then reconcile and deliver the guide. On `up` it
  becomes the agent's first prompt, wrapped in a **standby notice** so the
  researcher waits for your request.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `guide_writer` (type: `codex`)
- **`can_talk_to: [options_researcher]`** — the writer only reports back to the
  hub. It cannot reach the table builder or the `user`.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "write the X vs Y narrative that helps a reader choose", in
  `GUIDE.md`.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `comparison_table_builder` (type: `codex`)
- **`can_talk_to: [options_researcher]`** — the table builder only reports back to
  the hub, on the same one-way edge as the writer.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "build the side-by-side comparison table", in `COMPARISON.md`.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### What's *not* in this config
- **No shared workdir.** Each agent has its own directory, so the prose and the
  table are drafted independently and merged by the hub — no file collisions.
- **No `pings`.** The desk is event-driven: it only moves when
  you send an "X vs Y" request. (If a writer went quiet, add
  a `pings` cron rule to it.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — the finished guide is *held* (never bounced) until you flip it on
  (see §4).

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/comparison-guide-writer.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for all three agents).
2. Creates the runtime dirs
   (`comparison-guide-writer-workspace/.agentainer/…`: log, queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: the researcher gets
   `outbox/guide_writer/`, `outbox/comparison_table_builder/`, `outbox/user/`;
   each writer gets `outbox/options_researcher/`.
4. **Installs per-type turn detection** — the Claude Stop hook for the
   options_researcher, the Codex `notify` hooks for the writers.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'comparison-guide-writer' is up with 3 agent(s)
:: attach with:  tmux attach -t options_researcher
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/comparison-guide-writer.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only bind. See the `README.md` "control-plane UI" section.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole desk route mail with no API keys — the mechanics are identical.

---

## 4. Drive a request

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the finished guide as mail (rather than have it
held), turn yourself available first:

```bash
./agentainer user available -c examples/comparison-guide-writer.yaml
```

This rewrites the `user` contact card in the researcher's `outbox/user/about.md`
to `Status: available`. (While away, mail to you is *held* and the sender gets a
`system` ack — nothing bounces.)

Now send the "X vs Y" request into the swarm, addressed to the hub:

```bash
./agentainer send --to options_researcher "Write a buying guide: Notion vs Obsidian for personal knowledge management."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the researcher, then — because
the inbox was empty — **released into `inbox/`** and the researcher is **nudged**
(the protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§5), you'll see the desk advance one turn at a time. Each arrow
is a `stop → sweep → route → release → nudge` cycle:

1. **options_researcher receives the task.** It reads `inbox/`, researches both
   options into `FACTS.md`, and writes the *same* confirmed fact set into
   `outbox/guide_writer/` and `outbox/comparison_table_builder/`. When its turn
   ends, the orchestrator sweeps the outbox, routes both messages, and nudges both
   writers.
2. **guide_writer drafts the narrative.** It reads its inbox, writes `GUIDE.md`,
   and writes it back into `outbox/options_researcher/`. On stop, that routes to
   the hub.
3. **comparison_table_builder drafts the table.** It reads its inbox, writes
   `COMPARISON.md`, and writes it back into `outbox/options_researcher/`. On stop,
   that routes to the hub.
4. **options_researcher reconciles and finalizes.** It reads both drafts, makes
   sure the prose and table agree, assembles the guide (verdict up top, narrative,
   then table), and writes it into `outbox/user/`. On stop, that's delivered to
   your `user` mailbox (you'll see it with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a request, the agents just sit in standby. The desk only
> moves when real mail arrives — it has no periodic pings to self-start it.

---

## 5. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/comparison-guide-writer.yaml
```

```
swarm: comparison-guide-writer   root: ./comparison-guide-writer-workspace
  options_researcher (claude) up idle queue=0 unread=1 talks=guide_writer, comparison_table_builder, user
  guide_writer (codex) up idle queue=0 unread=0 talks=options_researcher
  comparison_table_builder (codex) up idle queue=0 unread=0 talks=options_researcher
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback):

```bash
./agentainer logs -c examples/comparison-guide-writer.yaml          # whole swarm, last 20
./agentainer logs -c examples/comparison-guide-writer.yaml -f        # follow live
./agentainer logs guide_writer -c examples/comparison-guide-writer.yaml # just one agent
```

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox guide_writer -c examples/comparison-guide-writer.yaml
```

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue guide_writer -c examples/comparison-guide-writer.yaml
```

**Attach to a live pane** — watch (or type into) an agent's tmux session:

```bash
./agentainer attach options_researcher -c examples/comparison-guide-writer.yaml
```

Detach with `Ctrl-b d`. (Typing into a pane bypasses the mailroom — handy for
un-sticking an agent, but the mail model is the normal path.)

---

## 6. Resume after a stop

Tear the swarm down when you're done:

```bash
./agentainer down -c examples/comparison-guide-writer.yaml
```

Bring it back later and **conversations resume by default**:

```bash
./agentainer up -c examples/comparison-guide-writer.yaml     # resume is the default
```

On `up`, Agentainer reads
`comparison-guide-writer-workspace/.agentainer/sessions.yaml` and reattaches the
recorded conversations via each type's native resume: `claude --resume <id>` for
the researcher, `codex resume <id>` for the writers. A resumed agent is *not*
re-sent the standby prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/comparison-guide-writer.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md) and
the reboot walkthrough in
[`use-cases/resume-after-reboot.md`](./resume-after-reboot.md).

---

## 7. Tips & footguns

- **Keep the options_researcher the only `user`-facing agent.** Only the hub lists
  `user` in `can_talk_to`. That gives you a single point of contact and a clean
  funnel: both drafts pass through the hub's reconciliation before they reach you.
  If a writer tried to mail `user` directly, the orchestrator bounces it (ACL) and
  drops a `system` note explaining who it *can* message — the model self-corrects
  in-band.

- **The writer and table builder never talk to each other — that's the point.**
  Because both only report to the hub, the researcher is the one place that holds
  the agreed fact set, so the prose and the table can't drift apart. Don't open a
  writer↔builder edge; it would let two drafts disagree with no referee.

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
  ./agentainer down                 -c examples/comparison-guide-writer.yaml
  ./agentainer remove-session       -c examples/comparison-guide-writer.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

- **Availability shapes the ending.** If `user` is **away** when the researcher
  finishes, your guide is *held* (with a `system` "the user is away" ack) rather
  than lost — read it later with `agentainer user inbox` or flip yourself
  available and it's delivered.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`cli-reference.md`](../cli-reference.md) — every subcommand and flag.
- `examples/comparison-guide-writer.yaml` — the config this page documents.
- `examples/affiliate-product-reviews.yaml` — single-product review desk (the
  neighbour to NOT confuse this with).
- `examples/competitive-intel.yaml` — competitor-company battlecard (the other
  neighbour).
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

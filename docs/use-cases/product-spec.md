# Use case: the product-spec → tickets → build swarm

A concrete, end-to-end walkthrough of the shipped `examples/product-spec.yaml`
swarm — a four-agent pipeline where a **PM** (`pm`) turns a one-line idea into a
spec, a **splitter** breaks that spec into ordered, independently-testable
tickets, an **implementer** builds them, and a **reviewer** checks each ticket
against the acceptance list before the PM calls it shipped. It's the canonical
"idea → engineered tickets → working build" loop, wired entirely through
Agentainer's file-based mail model.

Everything below is based on the actual contents of `examples/product-spec.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you supply the
coding-CLI commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

- **Product managers** who have an idea and want it turned into discrete,
  testable work items without hand-writing the tickets themselves.
- **Founders / solo builders** who need a first working slice of a product from a
  sentence of intent ("a CLI that converts CSV to Parquet with streaming").
- **Tech leads** who want a repeatable spec → tickets → review pipeline their
  team can drive from a single prompt, with quality gated by a reviewer before
  anything is declared done.

The PM owns the definition of done; the reviewer is the gate. You stay out of the
implementation details and only ever talk to one agent (`pm`) — exactly like
briefing a real product team.

---

## 2. The topology

```
        "CSV -> Parquet CLI"
  user ─────────▶ pm ─────────▶ splitter ─────────▶ implementer
         (spec)   hub   (tickets)        (build)
                    ▲                                            │
                    │                                            │ code
                    │                                            ▼
                    └────────────── reviewer ◀──────────────────┘
                            (reports each ticket to pm)
```

Four agents, one directed flow:

1. **`user` → `pm`** — you send the raw idea ("a CLI that converts CSV to Parquet
   with streaming").
2. **`pm` → `splitter`** — the PM writes a one-paragraph spec + acceptance list
   and delegates it.
3. **`splitter` → `implementer`** — the splitter breaks the spec into ordered,
   independently-testable tickets and hands them over.
4. **`implementer` → `reviewer`** — the implementer builds each ticket and sends
   it for review (and may ask the PM/splitter on ambiguity).
5. **`reviewer` → `pm`** — the reviewer checks the ticket against acceptance and
   reports the verdict.
6. **`pm` → `user`** — once acceptance is fully met, the PM writes the final
   summary back to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. An agent can only deliver to names on its own list; anything
else is bounced back as a `system` message and filed in `failed/` (see §7).

### The ACL, in one table

| agent         | type   | can talk to              | role                          |
|---------------|--------|--------------------------|-------------------------------|
| `pm`          | claude | splitter, implementer, reviewer, user | hub: spec + ship decision |
| `splitter`    | claude | pm, implementer          | spec → ordered tickets        |
| `implementer` | codex  | pm, splitter, reviewer   | builds tickets                |
| `reviewer`    | claude | pm, implementer          | checks tickets vs acceptance  |

Note the **single human-facing surface**: only `pm` lists `user`. The
implementer and reviewer can talk to *each other* on a FAIL (so a fix lands
without a second hop), but neither can reach you directly — raw build output
always passes through PM review first.

---

## 3. The config, explained

Here is `examples/product-spec.yaml` in full:

```yaml
# 🧭 Product spec → tickets → build swarm -- a PM turns an idea into engineered
# tickets and a working build. The pm hub takes a one-line idea from the human,
# writes a one-paragraph spec + acceptance list, hands it to the splitter, which
# breaks it into ordered, independently-testable tickets for the implementer; a
# reviewer checks each ticket against acceptance and reports back to pm.
swarm:
  name: product
  root: ./product-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: pm
    type: claude
    can_talk_to: [splitter, implementer, reviewer, user]
    command: "claude --dangerously-skip-permissions"
    role: "You are the PRODUCT MANAGER. ... (spec + ship decision) ..."
  - name: splitter
    type: claude
    can_talk_to: [pm, implementer]
    command: "claude --dangerously-skip-permissions"
    role: "You are the SPLITTER. ... (spec -> ordered tickets) ..."
  - name: implementer
    type: codex
    can_talk_to: [pm, splitter, reviewer]
    command: "codex --yolo"
    role: "You are the IMPLEMENTER. ... (builds tickets) ..."
  - name: reviewer
    type: claude
    can_talk_to: [pm, implementer]
    command: "claude --dangerously-skip-permissions"
    role: "You are the REVIEWER. ... (checks tickets vs acceptance) ..."
```

Field by field:

### `swarm`
- **`name: product`** — the swarm's name (shows up in `status`, logs, sessions).
- **`root: ./product-workspace`** — the parent directory for the agents' working
  directories and mailboxes. Each agent gets `product-workspace/<name>/` as its
  workdir (created on `up`), and its mailbox folders live alongside. Orchestrator
  state goes under `product-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, and it's ultimately keyed off each
  agent's `type`. For `claude` and `codex`, whose CLIs support a completion
  **hook**, setting `capture: none` is a footgun — so the config loader *upgrades*
  it back to `hook` and prints a warning at `up`
  (`capture: none on a claude agent gives the orchestrator no way to detect turn
  completion; using the type's default: capture: hook.`). Net effect here:
  `pm`, `splitter`, and `reviewer` (claude) use their hook; `implementer` (codex)
  uses its `notify` hook.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `pm` (type: `claude`)
- **`can_talk_to: [splitter, implementer, reviewer, user]`** — the PM is the hub:
  it delegates to the splitter, can check in with the implementer or reviewer,
  and is the **only agent that can talk to `user`**. That last part matters —
  keep the human-facing surface to a single agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity: turn the idea into a one-paragraph spec +
  acceptance list, send to the splitter, own the ship/no-ship call. On `up` this
  becomes the agent's first prompt, wrapped in a **standby notice** ("no task
  yet — don't send anything, you'll be notified"), so the PM waits for your idea
  instead of proactively mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).
- **MAILBOX reminder:** the role ends with the standard mailbox protocol note —
  read `inbox/`, move handled mail to `read/`, send via `outbox/<name>/` after
  reading `about.md`, finish the turn after writing.

### `splitter` (type: `claude`)
- **`can_talk_to: [pm, implementer]`** — brokers the spec ↔ build: it answers
  only the PM (who owns scope) and briefs the implementer.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch.
- **`role`** — "break the spec into ordered, independently-testable tickets."
- **Turn detection:** `claude` → Stop hook.

### `implementer` (type: `codex`)
- **`can_talk_to: [pm, splitter, reviewer]`** — can ask the PM or splitter on
  ambiguity and send its build to the reviewer. It can *not* reach `user`.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "build the tickets in order; send each to the reviewer; never
  declare the whole product done."
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `reviewer` (type: `claude`)
- **`can_talk_to: [pm, implementer]`** — reports every verdict to the PM (who owns
  ship/no-ship), and may copy the implementer on a FAIL so the fix lands directly.
  It can *not* reach `user`.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch.
- **`role`** — "check each ticket against acceptance; PASS or FAIL with a
  file:line fix note; always tell pm."

### What's *not* in this config
- **No `periodically_ping_seconds`.** None of the four agents has a periodic ping
  configured, so no agent is auto-nudged on a timer while idle — the pipeline is
  purely event-driven off real mail. (If you wanted the PM to poke a slow
  implementer, you'd add `periodically_ping_seconds: 300` to it.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/product-spec.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for `pm`/`splitter`/`reviewer`).
2. Creates the runtime dirs (`product-workspace/.agentainer/…`: log, queue, run,
   sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: `pm` gets
   `outbox/splitter/`, `outbox/implementer/`, `outbox/reviewer/`, `outbox/user/`;
   `splitter` gets `outbox/pm/`, `outbox/implementer/`; the implementer gets
   `outbox/pm/`, `outbox/splitter/`, `outbox/reviewer/`; the reviewer gets
   `outbox/pm/`, `outbox/implementer/`.
4. **Installs per-type turn detection** — the Claude Stop hooks for `pm`,
   `splitter`, and `reviewer`, and the Codex `notify` hook for the implementer.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'product' is up with 4 agent(s)
:: attach with:  tmux attach -t <pm-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/product-spec.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only bind (UI binds `127.0.0.1` by default — see CLAUDE.md §18 / the
`README.md` "control-plane UI" section). Never bind `0.0.0.0` without a token.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole pipeline route mail with no API keys — the mechanics are identical.

---

## 5. Drive an idea

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the PM's final "shipped" summary as mail
(rather than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/product-spec.yaml
```

This rewrites the `user` contact card in the PM's `outbox/user/about.md` to
`Status: available`, so the PM sees you're reachable. (While away, mail to you is
*held* and the sender gets a `system` ack — nothing bounces.)

Now send the idea into the swarm, addressed to the PM:

```bash
./agentainer send --to pm "I want a CLI that converts CSV to Parquet with streaming."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the PM, then — because the inbox
was empty — **released into `inbox/`** and the PM is **nudged** (the protocol is
re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **pm receives the idea.** It reads `inbox/`, writes a one-paragraph spec +
   acceptance list, and writes that into `outbox/splitter/`. On stop, the
   orchestrator sweeps the outbox, routes it to the splitter, and nudges it.
2. **splitter makes tickets.** It reads the spec, breaks it into ordered,
   independently-testable tickets, and writes the list into `outbox/implementer/`.
   On stop, that routes to the implementer.
3. **implementer builds.** It reads the tickets, builds them in order, and writes
   each completed ticket into `outbox/reviewer/`. (If a ticket is ambiguous, it
   first writes to `outbox/pm/` or `outbox/splitter/`.) On stop, each routes to
   the reviewer.
4. **reviewer checks.** It reads each ticket, checks the code against acceptance,
   and writes a PASS/FAIL verdict into `outbox/pm/` (and may copy
   `outbox/implementer/` on a FAIL). On stop, that routes to the PM.
5. **pm finalizes.** It reads the verdicts; on full acceptance it writes the
   shipped summary into `outbox/user/`. On stop, that's delivered to your `user`
   mailbox (you'll see it with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send an idea, the agents just sit in standby (that's the point
> of the standby prompt). The pipeline only moves when real mail arrives — this
> swarm has no periodic pings to self-start it.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/product-spec.yaml
```

```
swarm: product   root: ./product-workspace
  pm (claude) up idle queue=0 unread=0 talks=splitter, implementer, reviewer, user
  splitter (claude) up idle queue=0 unread=1 talks=pm, implementer
  implementer (codex) up idle queue=0 unread=0 talks=pm, splitter, reviewer
  reviewer (claude) up idle queue=0 unread=0 talks=pm, implementer
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/product-spec.yaml          # whole swarm, last 20
./agentainer logs -c examples/product-spec.yaml -f        # follow live
./agentainer logs reviewer -c examples/product-spec.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox implementer -c examples/product-spec.yaml
```

Prints the one released message (headers + body), or `implementer: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue implementer -c examples/product-spec.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach pm -c examples/product-spec.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the build

- **Read the reviewer's verdicts.** They're the quality gate. In the UI or via
  `agentainer inbox pm`, look for the reviewer's PASS/FAIL messages; a FAIL comes
  with a `file:line` fix note. The PM relays a precise fix request to the
  implementer, not a vague "try again."
- **Mid-flight clarification.** If the implementer or reviewer hits a genuinely
  ambiguous ticket, they'll mail the PM (or splitter) — you'll see it in
  `logs`. You can jump in with `agentainer send --to pm "the streaming mode must
  not buffer more than N rows"` and the PM folds it into the spec.
- **Change the scope, not the code.** Want a different output format, or a
  progress bar? Send it to the PM. Let the PM re-delegate; don't hand-edit an
  agent's workdir.

---

## 8. Resume after a stop

Tear the swarm down when you're done:

```bash
./agentainer down -c examples/product-spec.yaml
```

Bring it back later and **conversations resume by default**:

```bash
./agentainer up -c examples/product-spec.yaml     # resume is the default
```

On `up`, Agentainer reads `product-workspace/.agentainer/sessions.yaml` (written
as each agent finished its first turn) and reattaches the recorded conversations
via each type's native resume: `claude --resume <id>` for `pm`/`splitter`/
`reviewer`, `codex resume <id>` for the implementer. A resumed agent is *not*
re-sent the standby prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/product-spec.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Tips & footguns

- **Keep the PM the only `user`-facing agent.** Only the PM lists `user` in
  `can_talk_to`. That gives you a single point of contact and a clean funnel: raw
  build output always passes through review before it reaches you. If the
  implementer tries to mail `user` directly, the orchestrator bounces it (ACL)
  and drops a `system` note in its inbox explaining who it *can* message — the
  model self-corrects in-band.

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
  ./agentainer down           -c examples/product-spec.yaml
  ./agentainer remove-session -c examples/product-spec.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

- **Availability shapes the ending.** If `user` is **away** when the PM finishes,
  your "shipped" summary is *held* (with a `system` "the user is away" ack to the
  PM) rather than lost — read it later with `agentainer user inbox` or flip
  yourself available and it's delivered.

---

## 10. Customize

The four-agent shape is a starting point. Common variations:

### Add a `designer` agent (UX / interface spec before build)
Insert ahead of the implementer so the splitter's tickets include an interface
contract:

```yaml
  - name: designer
    type: claude
    can_talk_to: [pm, splitter, implementer]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the DESIGNER. Given the pm's spec, define the user-facing interface
      (CLI flags, input/output shapes, error messages) the implementer builds
      against. Send the contract to the splitter so it flows into the tickets.
```
Update the splitter's `can_talk_to` to include `designer`, and the implementer's
to include `designer` so it can read the contract. The PM stays the only
`user`-facing hub.

### Add a `qa` agent (independent test author)
Mirror the reviewer but focused on writing/running tests rather than reviewing
diffs against acceptance:

```yaml
  - name: qa
    type: codex
    can_talk_to: [pm, implementer, reviewer]
    command: "codex --yolo"
    role: "You are QA. Write and run tests for each ticket; report pass/fail to the pm."
```

### Swap models (multi-LLM)
Every `type` is independent. To run the splitter on Gemini instead of Claude,
change its `type` to `gemini` and its `command` to `gemini --yolo`, and set
`capture: pane` (Gemini has no completion hook). The orchestrator handles the
mixed capture modes transparently — see
[`multi-llm-swarm.md`](../multi-llm-swarm.md).

### Tune the ACL
- Tighten: drop `implementer → reviewer` and force every verdict through the PM.
- Loosen for speed: let `reviewer` talk *only* to `pm` (drop `implementer`) so
  fixes always round-trip through the PM's scope authority.
- Remember `system` is a reserved orchestrator mailbox and can never be a
  recipient; `user` is the only virtual mailbox an agent may address.

### Periodic ping for slow builds
If a long build stalls, add to the PM or implementer:
```yaml
    periodically_ping_seconds: 300
    periodically_ping_message: "still building? reply with current ticket status."
```

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing/ACL work.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming conversations.
- [`delegation-pipeline.md`](../delegation-pipeline.md) — the hub-and-spoke pattern.
- [`multi-llm-swarm.md`](../multi-llm-swarm.md) — mixing claude/codex/gemini/hermes.
- `examples/software-company.yaml` — a larger product-team variant.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

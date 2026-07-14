# Use case: the sales-call coach

A concrete, end-to-end walkthrough of the shipped `examples/sales-coach.yaml`
swarm — a live objection-handling drill where **you are the sales rep**. A
**coach** takes your product and a scenario and runs the practice; a
**roleplayer** plays the prospect and throws real objections at you; a **scorer**
grades each of your replies against a rubric; and the coach folds it all into a
debrief that comes back to you.

It's the "put a human in the loop and make them better at something" pattern —
turn-based practice with a critic — wired entirely through Agentainer's
file-based mail model. Unlike the fully-autonomous pipelines, **the human is a
first-class participant here**: you send the replies the prospect reacts to.

**Who this is for:** individual sales reps rehearsing a hard call before they
make it, SDR/AE onboarding, and enablement teams who want a repeatable,
low-stakes drill (cold outbound, competitive displacement, renewal saves,
price pushback) that runs without booking a manager's time.

Everything below is based on the actual contents of `examples/sales-coach.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you supply the coding-CLI
commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in [`mail-model.md`](../mail-model.md). The one-line
> version: an agent **reads a file** to receive mail and **writes a file** to send
> it; the orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The topology

```
   product + scenario
 user (the rep) ─────────────▶ coach ──────────────▶ roleplayer
        ▲   ▲                   │  \                  (the prospect)
        │   │  objections       │   \                      │
        │   └───────────────────┼────────────────────────┘
        │   your replies ──────▶│    \                 (throws objections
        │                       │     \                 at the rep, replies
        │       debrief         │      ▼                 to the rep)
        └───────────────────────┘   scorer
                                (grades your replies,
                                 reports to the coach)
```

Three agents plus you, one drill loop:

1. **`user` → `coach`** — you send the product and the scenario ("coach me on a
   cold call to a VP of Sales who already uses a competitor").
2. **`coach` → `roleplayer`** — the coach briefs the prospect persona and the
   objections to raise, then kicks off the role-play.
3. **`roleplayer` → `user`** — the prospect opens by addressing *you*. You reply.
4. **`user` → `roleplayer`** — you handle the objection (this is the practice).
5. **`coach` → `scorer` → `coach`** — the coach forwards your replies to the
   scorer, who grades them on a rubric and reports back.
6. **`coach` → `user`** — when the drill wraps, the coach sends you a debrief.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. An agent can only deliver to names on its own list; anything
else is bounced back as a `system` message and filed in `failed/` (see §7).

**The key design choice:** the **roleplayer talks to you**, but the **scorer
never does**. You get an uninterrupted, in-character conversation with the
prospect; the grading happens quietly in the background and only surfaces in the
coach's debrief. One voice — the coach — owns the human-facing surface for setup
and wrap-up.

---

## 2. The config, explained

Here is `examples/sales-coach.yaml`, field by field. (See the file for the full
`role` text — abbreviated here.)

```yaml
swarm:
  name: sales-coach
  root: ./sales-coach-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: coach
    type: claude
    can_talk_to: [roleplayer, scorer, user]
    command: "claude --dangerously-skip-permissions"
    role: "You are the SALES COACH running an objection-handling drill... "
  - name: roleplayer
    type: claude
    can_talk_to: [coach, user]
    command: "claude --dangerously-skip-permissions"
    role: "You are the PROSPECT in a sales role-play... "
  - name: scorer
    type: claude
    can_talk_to: [coach]
    command: "claude --dangerously-skip-permissions"
    role: "You are the SALES SCORER... report only to the coach."
```

### `swarm`
- **`name: sales-coach`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./sales-coach-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent gets
  `sales-coach-workspace/<name>/` as its workdir (created on `up`), and its
  mailbox folders live alongside. Orchestrator state goes under
  `sales-coach-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, keyed off each agent's `type`. All
  three agents here are `claude`, whose CLI supports a completion **hook**, so
  setting `capture: none` is a footgun — the config loader *upgrades* it back to
  `hook` and prints a warning at `up`
  (`capture: none on a claude agent gives the orchestrator no turn-completion
  signal -- auto-upgraded to capture: hook.`). Net effect: all three use their
  Stop hook. Leave `capture: none` in place for a **key-free mock demo** (bash
  loops don't fire a hook), or delete it once you're running real Claude CLIs.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `coach` (type: `claude`)
- **`can_talk_to: [roleplayer, scorer, user]`** — the coach is the hub: it briefs
  the roleplayer, forwards your replies to the scorer, and it is **the only agent
  that talks to `user` for setup and debrief**. Keep the human-facing control
  surface funnelled through one agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command, e.g. a
  shell alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity: restate the scenario, brief the prospect,
  forward replies to the scorer, and write the final debrief. On `up` this becomes
  the agent's first prompt, wrapped in a **standby notice** ("no task yet — don't
  send anything, you'll be notified"), so the coach waits for your scenario
  instead of proactively mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `roleplayer` (type: `claude`)
- **`can_talk_to: [coach, user]`** — the prospect talks to *you* (the rep) and can
  check in with the coach. It deliberately **cannot** reach the scorer — the
  person you're selling to has no idea they're being graded, which keeps the
  role-play honest.
- **`role`** — "you are the prospect… stay in character, raise objections, make
  the rep earn the next step." It's told **not to break character to give advice**
  — coaching is someone else's job.
- **Turn detection:** `claude` → Stop hook.

### `scorer` (type: `claude`)
- **`can_talk_to: [coach]`** — the scorer only reports upward to the coach. It
  cannot reach you or the roleplayer, so grading never leaks into the live call.
- **`role`** — a five-point rubric (discovery, objection handling, value-not-
  features, control, tone), a per-turn score, and one concrete rewrite of the
  weakest sentence.
- **Turn detection:** `claude` → Stop hook.

### Why all three are `claude`
This swarm is intentionally single-model so you can start with one CLI/one key.
Nothing stops you mixing types — the roleplayer is a great candidate for a
different model to vary the "voice" of the prospect (see §7 and
[`multi-llm-swarm.md`](./multi-llm-swarm.md)).

### What's *not* in this config
- **No `pings`.** No agent is auto-nudged on a timer — the
  drill is purely event-driven off real mail (yours and the agents'). A live
  practice shouldn't have a background timer poking the prospect mid-thought.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you (the prospect's objections, the debrief) is
  *held* until you flip it on (see §4). For an interactive drill you'll want to be
  **available**.

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/sales-coach.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for all three claude agents).
2. Creates the runtime dirs (`sales-coach-workspace/.agentainer/…`: log, queue,
   run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: the coach gets
   `outbox/roleplayer/`, `outbox/scorer/`, `outbox/user/`; the roleplayer gets
   `outbox/coach/`, `outbox/user/`; the scorer gets `outbox/coach/`.
4. **Installs per-type turn detection** — the Claude Stop hook for each agent.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'sales-coach' is up with 3 agent(s)
:: attach with:  tmux attach -t <coach-session>
:: you can use the UI with:  agentainer serve -c examples/sales-coach.yaml
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). It **binds `127.0.0.1` by default** — only
add `--host`/`--token` for a deliberate remote bind (see
[`remote-access.md`](./remote-access.md)). The headless CLI below is fully
functional on its own.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole drill route mail with no API keys — the mechanics are identical, the
> "prospect" just won't say anything interesting.

---

## 4. Drive a drill

Because the prospect's objections and the coach's debrief come to you *as mail*,
turn yourself **available** first (the `user` mailbox defaults to away):

```bash
./agentainer user available -c examples/sales-coach.yaml
```

This rewrites the `user` contact card in the coach's and roleplayer's
`outbox/user/about.md` to `Status: available`, so they know you're reachable.
(While away, mail to you is *held* and the sender gets a `system` ack — nothing
bounces.)

Now kick off the drill by sending the coach your product and scenario:

```bash
./agentainer send -c examples/sales-coach.yaml --to coach \
  "Product: Acme CRM, \$99/seat/mo. Scenario: cold outbound to a VP of Sales at
   a 200-person SaaS company who already uses a competitor. I'll be the rep --
   set it up and have the prospect open."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the coach, then — because the
inbox was empty — **released into `inbox/`** and the coach is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The drill flowing

Each hop is a `stop → sweep → route → release → nudge` cycle:

1. **coach sets up.** It reads `inbox/`, restates the scenario, and writes a
   persona + objection brief into `outbox/roleplayer/`. On stop, that routes to
   the roleplayer.
2. **the prospect opens.** The roleplayer reads its brief and writes its opening
   line into `outbox/user/` — addressed to *you*. On stop, it's delivered to your
   `user` mailbox.
3. **you read it and reply — this is the practice.** Read the objection…

   ```bash
   ./agentainer user inbox -c examples/sales-coach.yaml
   ```

   …then answer the prospect **directly**:

   ```bash
   ./agentainer send -c examples/sales-coach.yaml --to roleplayer \
     "Totally fair -- switching costs are real. Before I pitch anything, what's
      the one thing your current tool makes harder than it should be?"
   ```

4. **grading happens quietly.** The coach (watching the thread) forwards your
   reply to the `scorer`, who writes a rubric score into `outbox/coach/`. You
   never see this mid-drill — it feeds the debrief.
5. **the prospect reacts.** The roleplayer pushes back or softens realistically
   and writes its next line to `outbox/user/`. Back to step 3 — go a few rounds.
6. **wrap up.** When the call reaches a natural end (you book the next step, or
   the prospect declines), or you tell the coach to stop, the coach aggregates the
   scorer's grades and writes a **debrief** to `outbox/user/`: what worked, the
   single highest-leverage fix, and one line to try next time.

You can also send setup notes or "let's stop here" to the coach at any time with
`--to coach`. You drive exactly two addresses: **`roleplayer`** for the live call,
**`coach`** for setup and wrap-up. The orchestrator releases one inbox message at
a time and fires each hop off turn completion — you never relay anything by hand.

---

## 5. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/sales-coach.yaml
```

```
swarm: sales-coach   root: ./sales-coach-workspace
  coach (claude) up idle queue=0 unread=0 talks=roleplayer, scorer, user
  roleplayer (claude) up idle queue=0 unread=1 talks=coach, user
  scorer (claude) up idle queue=0 unread=0 talks=coach
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct the whole call afterward — useful for
enablement review):

```bash
./agentainer logs -c examples/sales-coach.yaml           # whole swarm, last 20
./agentainer logs -c examples/sales-coach.yaml -f         # follow live
./agentainer logs roleplayer -c examples/sales-coach.yaml # just the prospect
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent (or you) is currently looking at:

```bash
./agentainer inbox scorer -c examples/sales-coach.yaml   # a peek at the grades
./agentainer user inbox   -c examples/sales-coach.yaml   # the prospect's line to you
```

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue coach -c examples/sales-coach.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach roleplayer -c examples/sales-coach.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 6. Iterate: run another drill

The strength of practice is repetition. To run a fresh scenario, just send the
coach a new setup — same session, new drill:

```bash
./agentainer send -c examples/sales-coach.yaml --to coach \
  "New scenario: inbound demo follow-up. They loved the product but 'need to
   check with finance.' Coach me on the multi-threading / champion play."
```

The coach re-briefs the roleplayer with the new persona and you go again. Because
the scorer keeps a consistent rubric turn to turn, your debriefs are comparable
across scenarios — you can watch your objection-handling score climb.

To run the *same* scenario cold (fresh context, no memory of your last attempt),
tear down and reset session state:

```bash
./agentainer down           -c examples/sales-coach.yaml
./agentainer remove-session -c examples/sales-coach.yaml
./agentainer up             -c examples/sales-coach.yaml
```

Or just bring it back later and **conversations resume by default** — the coach,
prospect, and scorer remember the last drill:

```bash
./agentainer up -c examples/sales-coach.yaml     # resume is the default
```

On `up`, Agentainer reads `sales-coach-workspace/.agentainer/sessions.yaml` and
reattaches each agent's conversation via `claude --resume <id>`. Pass
`--no-resume` to force everyone fresh. See
[`sessions-and-resume.md`](../sessions-and-resume.md) for the full story.

---

## 7. Customize

- **Add an `objection-bank` agent.** For a harder, less improvised drill, add a
  fourth `claude` agent that owns a curated list of the toughest objections for
  your product and feeds them to the coach:

  ```yaml
    - name: objection-bank
      type: claude
      can_talk_to: [coach]
      command: "claude --dangerously-skip-permissions"
      role: |
        You hold the objection bank for Acme CRM. When the coach asks, hand back
        3-5 real objections for the given scenario, ranked hardest-first, each
        with the trap the rep usually falls into. You never talk to the rep or
        the prospect.
  ```

  Then add `objection-bank` to the coach's `can_talk_to` so it can pull from the
  bank when briefing the roleplayer. (Point its `role` at a real file of your
  team's objections for maximum realism.)

- **Swap models to vary the prospect's voice.** The prospect feels more real when
  it's a different model from the coach. Change the roleplayer to another type and
  match its `command`:

  ```yaml
    - name: roleplayer
      type: gemini
      capture: pane          # gemini has no completion hook -- poll the pane
      command: "gemini --yolo"
      can_talk_to: [coach, user]
  ```

  Remember: `type` must match what `command` launches, or turn detection never
  fires and the agent hangs (the config loader catches obvious mismatches at
  `up`). See [`multi-llm-swarm.md`](./multi-llm-swarm.md).

- **Tune the ACL for a different drill shape.** The graph *is* the pedagogy:
  - Let the **prospect grade itself out of role** by adding `scorer` to the
    roleplayer's `can_talk_to` — usually a bad idea (it leaks grading into the
    call), which is exactly why the shipped config forbids it.
  - Add a **manager** agent that only the coach talks to, for a
    coach-of-the-coach layer (like the reviewer in
    [`delegation-pipeline.md`](./delegation-pipeline.md)).
  - Keep **`user` on exactly the agents you want to hear from** — here that's the
    coach (setup/debrief) and the roleplayer (the live call). Drop the roleplayer
    from your `--to` habits and everything funnels through the coach instead.

- **Rewrite the rubric.** The scorer's five criteria live entirely in its `role`.
  Swap them for your team's methodology (MEDDIC, SPIN, Challenger) and the debriefs
  reshape themselves — no code change.

---

## 8. Tips & footguns

- **Keep the coach the primary `user`-facing agent.** The coach owns setup and the
  debrief; the roleplayer only reaches you *in character*. If the scorer ever
  tries to mail `user` directly, the orchestrator bounces it (ACL) and drops a
  `system` note in the scorer's inbox explaining who it *can* message — the model
  self-corrects in-band.

- **Be available, or your drill stalls politely.** If `user` is **away** when the
  prospect opens, its line is *held* (with a `system` "the user is away" ack to
  the roleplayer) rather than lost. For a live back-and-forth, run
  `agentainer user available` first, and read the prospect's line with
  `agentainer user inbox`.

- **Reply to the right address.** Answer the prospect with `--to roleplayer`;
  send scenario setup and "let's wrap" with `--to coach`. Mailing the coach when
  you meant the prospect just means the coach reads your handling instead of the
  buyer reacting to it.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually fires**
  — a `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't
  launch Claude) means completion never triggers and the agent pins "busy" forever.
  `status` showing an agent `busy` for a long time with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  drill: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill a "still there?/yep still here" loop between the coach and the scorer.

- **`remove-session` to reset for a cold re-run.** To wipe all Agentainer state
  (runtime + mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/sales-coach.yaml
  ./agentainer remove-session -c examples/sales-coach.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches your config.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — how a drill survives a
  `down`/`up`.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — the hub-and-critic
  pattern this swarm is built on.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model types (e.g. a
  different-voiced prospect).

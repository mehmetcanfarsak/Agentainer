# Use case: the game design swarm

A concrete, end-to-end walkthrough of the shipped `examples/game-design.yaml`
swarm — a **director** takes a one-line game pitch from you and runs a
four-discipline design team (**worldbuilder**, **mechanicist**, **writer**,
**balancer**), then assembles their work into a single design document and
delivers it back to you. It's the canonical "one hub coordinates specialists,
one funnel faces the human" loop, wired entirely through Agentainer's
file-based mail model.

This is for **game designers** sketching a concept before committing a team to
it, and for **hobbyists / game-jam solo devs** who want a full design pass —
world, systems, content, and numbers — from a single pitch. Everything below is
based on the actual contents of `examples/game-design.yaml`, the shipped CLI
(`lib/cli.py`), and the mailroom (`lib/mail.py`). No API keys are needed to
understand the mechanics; to run it *for real* you supply the coding-CLI
commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md)
> first, then the four-folders recap in the repo `README.md`. The one-line
> version: an agent **reads a file** to receive mail and **writes a file** to
> send it; the orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The topology

```
        game pitch
  user ─────────────▶ director ──────┬──────────────▶ worldbuilder  (WORLD.md)
        (design doc) ◀───────┐       ├──────────────▶ mechanicist   (MECHANICS.md)
                             │       ├──────────────▶ writer        (NARRATIVE.md)
                        assemble     └──────────────▶ balancer      (BALANCE.md)
                        DESIGN.md      ◀── each reports back to the director ──┘
```

A hub-and-spoke, one directed flow:

1. **`user` → `director`** — you send the game pitch.
2. **`director` → `worldbuilder` / `mechanicist`** — the director restates the
   pitch as creative pillars and grounds setting + systems in the same vision
   first.
3. **`director` → `writer` / `balancer`** — once world and mechanics settle, the
   director briefs the writer (content on top of them) and the balancer (numbers
   for the loop).
4. **specialists → `director`** — every discipline reports its piece back to the
   director, never to each other.
5. **`director` → `user`** — the director assembles the four pieces into one
   `DESIGN.md` and delivers it to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. An agent can only deliver to names on its own list; anything
else is bounced back as a `system` message and filed in `failed/` (see §7).

---

## 2. The config, explained

Here is `examples/game-design.yaml`, walked field by field.

### `swarm`
- **`name: gamedesign`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./gamedesign-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent gets
  `gamedesign-workspace/<name>/` as its workdir (created on `up`), and its
  mailbox folders live alongside. Orchestrator state goes under
  `gamedesign-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** every
  agent here is `type: claude`, whose CLI supports a completion **hook**, so
  setting `capture: none` is a footgun — the config loader *upgrades* it back to
  `hook` and prints a warning at `up`
  (`capture: none on a claude agent gives the orchestrator no turn-completion
  signal -- auto-upgraded to capture: hook.`). Net effect: every agent uses its
  Stop hook. (Leaving `capture: none` in `defaults` is what keeps the swarm
  key-free if you swap the commands for mock bash loops, which fire no hook.)
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `director` (type: `claude`)
- **`can_talk_to: [worldbuilder, mechanicist, writer, balancer, user]`** — the
  director is the hub: it can brief all four specialists, and it is the **only
  agent that can talk to `user`**. That last part matters — keep the
  human-facing surface to a single agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command, e.g. a
  shell alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity: set the creative pillars, sequence the
  disciplines (world + mechanics first, then content + balance), resolve
  cross-discipline conflicts itself, and assemble the final `DESIGN.md`. On `up`
  this becomes the agent's first prompt, wrapped in a **standby notice** ("no
  task yet — don't send anything, you'll be notified"), so the director waits for
  your pitch instead of proactively mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at
  `up`).

### `worldbuilder` (type: `claude`)
- **`can_talk_to: [director]`** — reports only to the director; cannot reach the
  other specialists or the `user`.
- **`role`** — turn the pillars into a usable setting in `WORLD.md`: geography
  and mood, factions and their stakes, a short history, and tone rules. The role
  is deliberately concrete ("name places, factions and stakes the writer can
  hang quests on"), because vague lore is what stalls the downstream disciplines.

### `mechanicist` (type: `claude`)
- **`can_talk_to: [director]`** — reports only to the director.
- **`role`** — design the core gameplay in `MECHANICS.md`: the moment-to-moment
  loop, the player's verbs, the rules connecting them, and failure/reward states,
  described concretely enough that the balancer can put numbers on it and the
  writer can build quests around the verbs.

### `writer` (type: `claude`)
- **`can_talk_to: [director]`** — reports only to the director.
- **`role`** — write the content players move through in `NARRATIVE.md`: the
  story arc, concrete quests (each tied to a mechanic and a named place), key
  characters, and sample dialogue. Every quest must be playable with the
  mechanicist's verbs and set in the worldbuilder's places — if it isn't, ask the
  director rather than inventing.

### `balancer` (type: `claude`)
- **`can_talk_to: [director]`** — reports only to the director.
- **`role`** — make the systems fair and satisfying in `BALANCE.md`: difficulty
  curve, economy, progression, and the actual numbers behind them, plus flagged
  exploits and grind walls. It escalates systemic problems to the director
  instead of quietly rewriting the mechanicist's rules.

### What's *not* in this config
- **No `periodically_ping_seconds`.** No agent is auto-nudged on a timer while
  idle — the pipeline is purely event-driven off real mail. (If you wanted the
  director to poke a slow specialist, you'd add `periodically_ping_seconds: 300`
  to it.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — the finished design doc is *held* (never bounced) until you flip it
  on (see §4).
- **Every specialist talks only to the director.** They never form pairwise
  channels, which is the whole point: design decisions reconcile in one place.

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/game-design.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints warnings (including the
   `capture: none → hook` upgrade for all five agents).
2. Creates the runtime dirs (`gamedesign-workspace/.agentainer/…`: log, queue,
   run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: the director gets
   `outbox/worldbuilder/`, `outbox/mechanicist/`, `outbox/writer/`,
   `outbox/balancer/`, `outbox/user/`; each specialist gets only
   `outbox/director/`.
4. **Installs per-type turn detection** — the Claude Stop hook for all five
   agents.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles
   stale/dead/silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'gamedesign' is up with 5 agent(s)
:: attach with:  tmux attach -t <director-session>
:: you can use the UI with:  agentainer serve -c examples/game-design.yaml
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). It **binds `127.0.0.1` by default** — safe
loopback only; a remote bind is opt-in and requires a token. See the `README.md`
"control-plane UI" section.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole pipeline route mail with no API keys — the mechanics are identical.

---

## 4. Drive a pitch

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. To *receive* the director's finished design doc as mail (rather than
have it held), turn yourself available first:

```bash
./agentainer user available -c examples/game-design.yaml
```

This rewrites the `user` contact card in the director's `outbox/user/about.md` to
`Status: available`, so the director sees you're reachable. (While away, mail to
you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the pitch into the swarm, addressed to the director:

```bash
./agentainer send --to director "A cozy deep-sea salvage game where you rebuild a sunken town."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the director, then — because the
inbox was empty — **released into `inbox/`** and the director is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§5), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **director receives the pitch.** It reads `inbox/`, derives creative pillars,
   and writes briefs into `outbox/worldbuilder/` and `outbox/mechanicist/`. On
   stop, the orchestrator sweeps the outbox, routes each message, and nudges both
   specialists.
2. **worldbuilder + mechanicist work.** Each reads its inbox, writes its file
   (`WORLD.md` / `MECHANICS.md`), and reports back into `outbox/director/`. On
   stop, those route to the director.
3. **director briefs downstream.** With world and mechanics in hand, it writes
   briefs into `outbox/writer/` and `outbox/balancer/`. They produce
   `NARRATIVE.md` and `BALANCE.md` and report back.
4. **director finalizes.** It reads the four pieces, assembles `DESIGN.md`, and
   writes it into `outbox/user/`. On stop, that's delivered to your `user`
   mailbox (you'll see it with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a pitch, the agents just sit in standby (that's the point
> of the standby prompt). The pipeline only moves when real mail arrives — this
> swarm has no periodic pings to self-start it.

---

## 5. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/game-design.yaml
```

```
swarm: gamedesign   root: ./gamedesign-workspace
  director (claude) up idle queue=0 unread=0 talks=worldbuilder, mechanicist, writer, balancer, user
  worldbuilder (claude) up idle queue=0 unread=1 talks=director
  mechanicist (claude) up idle queue=0 unread=1 talks=director
  writer (claude) up idle queue=0 unread=0 talks=director
  balancer (claude) up idle queue=0 unread=0 talks=director
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/game-design.yaml            # whole swarm, last 20
./agentainer logs -c examples/game-design.yaml -f          # follow live
./agentainer logs worldbuilder -c examples/game-design.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox mechanicist -c examples/game-design.yaml
```

Prints the one released message (headers + body), or `mechanicist: inbox is
empty`.

**Queue depth** — mail waiting behind the one released message (the director will
build one here when several specialists report back close together):

```bash
./agentainer queue director -c examples/game-design.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach director -c examples/game-design.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom
— handy for un-sticking an agent, but the mail model is the normal path.)

---

## 6. Iterate on the design

The design doc is rarely right on the first pass — this swarm is built to revise.
Because conversations **resume by default**, you keep the whole team's context
across rounds. After the director delivers `DESIGN.md`, just send another pitch-
shaped message with your notes:

```bash
./agentainer send --to director "Love the salvage loop, but the economy feels grindy and the town has no antagonist. Revise."
```

The director re-briefs only the disciplines that need to change — here the
balancer (economy) and the worldbuilder/writer (antagonist) — reconciles the
edits, and delivers an updated doc. Each specialist still remembers its own file,
so a revision is a diff, not a rewrite.

Tear the swarm down when you're done and bring it back later with context intact:

```bash
./agentainer down -c examples/game-design.yaml
./agentainer up   -c examples/game-design.yaml     # resume is the default
```

On `up`, Agentainer reads `gamedesign-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via `claude --resume <id>`. A resumed agent is *not* re-sent the
standby prompt — its prior context is restored. Pass `--no-resume` to force
everyone fresh, and inspect what's recorded with `agentainer sessions`. For the
full story see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 7. Customize

- **Add a `level_designer`.** Once mechanics and world exist, a fifth specialist
  that turns them into concrete spaces (encounters, pacing beats, the shape of
  the first hour) slots in cleanly. Add it as another `type: claude` agent with
  `can_talk_to: [director]`, add `level_designer` to the director's
  `can_talk_to`, and extend the director's role to brief it after the writer.
  The hub-and-spoke shape is unchanged — you're adding one more spoke.

  ```yaml
    - name: level_designer
      type: claude
      can_talk_to: [director]
      command: "claude --dangerously-skip-permissions"
      role: |
        You are the LEVEL DESIGNER. Turn the world and mechanics into concrete
        playable spaces: the first-hour layout, encounter pacing, and where each
        mechanic is introduced. Write it in LEVELS.md. Report to the director.
  ```

- **Swap models per discipline.** Nothing requires every agent to be `claude`.
  Point a discipline at another CLI by changing its `type` **and** `command`
  together (the loader rejects a `type`/`command` mismatch, which would otherwise
  hang the agent — see §7 footgun below). For example, run the balancer on Codex
  and the writer on Gemini:

  ```yaml
    - name: balancer
      type: codex
      command: "codex --yolo"
      # ...
    - name: writer
      type: gemini
      capture: pane          # gemini has no completion hook; poll the pane
      command: "gemini --yolo"
  ```

  A mixed swarm is a first-class use case — see
  [`multi-llm-swarm.md`](./multi-llm-swarm.md).

- **Tune the ACL.** The default here is strict hub-and-spoke. If you *want* two
  disciplines to collaborate directly — say the writer and worldbuilder trading
  lore without round-tripping through the director — add each to the other's
  `can_talk_to`. Do this deliberately: every extra edge is one more channel that
  can drift out of sync, which is exactly what the hub shape prevents. Keep
  `user` on the director alone so the human always has a single point of contact.

---

## 8. Tips & footguns

- **Keep the director the only `user`-facing agent.** Only the director lists
  `user` in `can_talk_to`, giving you a single point of contact and a clean
  funnel: raw discipline output always passes through the director's assembly
  before it reaches you. If a specialist tries to mail `user` directly, the
  orchestrator bounces it (ACL) and drops a `system` note in its inbox explaining
  who it *can* message — the model self-corrects in-band.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch (e.g. a `claude` agent whose `command`
  doesn't launch Claude) means completion never triggers and the agent pins
  "busy" forever. `status` showing an agent `busy` for a long time with `unread`
  mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  the auto-archive number of times without being handled is auto-archived so the
  queue advances. There's also a per-pair runaway cap to kill "thanks!/you're
  welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/game-design.yaml
  ./agentainer remove-session -c examples/game-design.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' design files or your config.

- **Availability shapes the ending.** If `user` is **away** when the director
  finishes, your design doc is *held* (with a `system` "the user is away" ack to
  the director) rather than lost — read it later with `agentainer user inbox` or
  flip yourself available and it's delivered.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and the routing rules
  this swarm rides on.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — how iterate-and-resume
  keeps the team's context across rounds.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — the general
  hub-delegates-to-specialists pattern this swarm is an instance of.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — running each discipline on a
  different model.

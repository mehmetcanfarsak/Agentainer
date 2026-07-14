# Use case: the press-release wire

A concrete, end-to-end walkthrough of the shipped `examples/press-release-wire.yaml`
swarm — a three-agent pipeline where a **news desk** hub takes an announcement
from you and fans it out to a **draft writer** (the press release) and a
**distribution strategist** (the media list + tailored pitches), then assembles
both into one wire packet. It's the canonical "announce → draft → distribute →
package" loop, wired entirely through Agentainer's file-based mail model.

Everything below is based on the actual contents of `examples/press-release-wire.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you supply the coding-CLI
commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The topology

```
       announcement
  user ───────────────▶ news_desk  ───────▶ draft_writer
         (wire packet)     (hub)     ◀───────  press release draft
                             │
                             ├──────────────▶ distribution_strategist
                             ◀──────────────  media list + tailored pitches
```

Three agents, one hub-and-spoke flow:

1. **`user` → `news_desk`** — you send the announcement.
2. **`news_desk` → `draft_writer`** — the desk briefs the writer on angle + facts.
3. **`news_desk` → `distribution_strategist`** — the desk briefs the strategist
   in parallel with the same angle + audience.
4. **`draft_writer` → `news_desk`** — the press release draft comes back.
5. **`distribution_strategist` → `news_desk`** — the media list + pitches come back.
6. **`news_desk` → `user`** — the desk assembles the wire packet and delivers it.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. The two specialists can only talk to the desk, never to each
other and never to `user`; the desk is the only agent facing the human.

---

## 2. The config, explained

Here is `examples/press-release-wire.yaml` in full:

```yaml
# =============================================================================
# 📰 Press-release wire -- a news desk turns an announcement into a press
# release draft plus a media distribution list and tailored pitches.
#
#   cp examples/press-release-wire.yaml my-wire.yaml
#   agentainer up    -c my-wire.yaml
#   agentainer send  -c my-wire.yaml --to news_desk "We just open-sourced our agent orchestrator. Ship a release."
#   agentainer down  -c my-wire.yaml
#
# Key-free: swap each `command` for a mock bash loop and the whole wire routes
# mail with NO API keys. As written, every `command` launches a real CLI.
# =============================================================================

swarm:
  name: press-release-wire
  root: ./press-release-wire-workspace

defaults:
  capture: none              # claude/codex auto-upgrade to their hook at `up`
  can_talk_to: []            # tightened per agent below

agents:
  - name: news_desk
    type: claude
    can_talk_to: [draft_writer, distribution_strategist, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the NEWS DESK -- the hub of a press-release wire. ...
      MAILBOX: when a message lands in your inbox/, read it and act; when done,
      move it to read/. To send, write a file into outbox/<name>/ (read
      outbox/<name>/about.md first ...) and finish your turn. You may only
      message the agents in your can_talk_to.

  - name: draft_writer
    type: claude
    can_talk_to: [news_desk]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the DRAFT WRITER. ... Write a publish-ready press release ...

  - name: distribution_strategist
    type: codex
    can_talk_to: [news_desk]
    command: "codex --yolo"
    role: |
      You are the DISTRIBUTION STRATEGIST. ... Build a media distribution plan ...
```

Field by field (mirroring the research swarm):

### `swarm`
- **`name: press-release-wire`** — the swarm's name (shows up in `status`, logs, sessions).
- **`root: ./press-release-wire-workspace`** — parent dir for the agents' workdirs
  and mailboxes. Each agent gets `press-release-wire-workspace/<name>/`; orchestrator
  state goes under `press-release-wire-workspace/.agentainer/` (never commit it).

### `defaults`
- **`capture: none`** — the default turn-detection mode. `claude` and `codex`
  support a completion **hook**, so the loader *upgrades* `none` back to `hook`
  and prints a warning at `up`. Net effect: all three agents use their hook.
- **`can_talk_to: []`** — the safe default ACL floor; every agent states its own list.

### `news_desk` (type: `claude`, the HUB)
- **`can_talk_to: [draft_writer, distribution_strategist, user]`** — the desk is the
  hub: it can brief both specialists and is the **only agent that can talk to
  `user`**. Keep the human-facing surface to a single agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code (placeholder;
  substitute your own launch command; treat command strings as sensitive).
- **`role`** — the standing identity, ending in the **MAILBOX** reminder so the hub
  knows exactly where to read/write and that it may only message its `can_talk_to`.

### `draft_writer` (type: `claude`)
- **`can_talk_to: [news_desk]`** — can only report upward to the desk. It writes
  `PRESS_RELEASE.md` (headline, lede, dateline, boilerplate, media contact).
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `distribution_strategist` (type: `codex`)
- **`can_talk_to: [news_desk]`** — likewise reports only to the desk. It writes
  `MEDIA_PLAN.md`: a distribution list of 8-15 matched outlets + a tailored pitch
  per outlet (no generic blast).
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### What's *not* in this config
- **No `pings`.** The wire is purely event-driven off real
  mail — no agent self-starts on a timer.
- **No `user` availability set.** The `user` mailbox defaults to **away** — mail
  to you is *held* (never bounced) until you flip it on (see §4).

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/press-release-wire.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the `capture: none → hook` upgrades.
2. Creates the runtime dirs (`press-release-wire-workspace/.agentainer/…`).
3. **Initializes the mailboxes** — the five folders `inbox/ outbox/ read/ sent/
   failed/` per agent, plus an `outbox/<peer>/` for each allowed recipient (the
   desk gets `outbox/draft_writer/`, `outbox/distribution_strategist/`,
   `outbox/user/`; each specialist gets `outbox/news_desk/`).
4. **Installs per-type turn detection** — Claude Stop hooks for the desk and
   writer, Codex `notify` hook for the strategist.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints. The `serve` line gives you
the mail-app control-plane UI (threads, live panes, send-as-user, availability
toggle). Drop `--host`/`--token` for the safe loopback-only (`127.0.0.1`) bind.
See the `README.md` "control-plane UI" section.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole wire route mail with no API keys — the mechanics are identical.

---

## 4. Drive an announcement

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the final wire packet as mail, turn yourself
available first:

```bash
./agentainer user available -c examples/press-release-wire.yaml
```

Now send the announcement into the swarm, addressed to the desk:

```bash
./agentainer send --to news_desk "We just open-sourced Agentainer, a zero-dependency multi-agent orchestrator. Ship a release."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped
`From: user` + a fresh id, enqueued for the desk, then — because the inbox was
empty — **released into `inbox/`** and the desk is **nudged** (the protocol is
re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§5), you'll see the hub fan out and gather:

1. **news_desk receives the announcement.** It writes a brief into
   `outbox/draft_writer/` and a parallel brief into `outbox/distribution_strategist/`.
   On stop, both are routed and the two specialists are nudged.
2. **draft_writer writes the release.** It reads its inbox, writes `PRESS_RELEASE.md`,
   and writes the draft to `outbox/news_desk/`. On stop, that routes back to the desk.
3. **distribution_strategist builds the plan.** It writes `MEDIA_PLAN.md` and the
   tailored pitches to `outbox/news_desk/`. On stop, that routes back to the desk.
4. **news_desk assembles the packet.** It reviews draft + plan, requests a fix
   from the writer if needed, then writes the combined wire packet to `outbox/user/`.
   On stop, that's delivered to your `user` mailbox.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

---

## 5. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/press-release-wire.yaml
```

```
swarm: press-release-wire   root: ./press-release-wire-workspace
  news_desk (claude) up idle queue=0 unread=0 talks=draft_writer, distribution_strategist, user
  draft_writer (claude) up idle queue=0 unread=1 talks=news_desk
  distribution_strategist (codex) up idle queue=0 unread=1 talks=news_desk
supervisor: alive
```

**The durable event log** — the source of truth for history:

```bash
./agentainer logs -c examples/press-release-wire.yaml          # whole swarm, last 20
./agentainer logs -c examples/press-release-wire.yaml -f        # follow live
./agentainer logs draft_writer -c examples/press-release-wire.yaml
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`, etc.

```bash
./agentainer inbox news_desk -c examples/press-release-wire.yaml   # what the desk sees
./agentainer queue draft_writer -c examples/press-release-wire.yaml
./agentainer attach draft_writer -c examples/press-release-wire.yaml  # watch a live pane
```

---

## 6. Resume after a stop

```bash
./agentainer down -c examples/press-release-wire.yaml
./agentainer up   -c examples/press-release-wire.yaml     # resume is the default
```

On `up`, Agentainer reads `press-release-wire-workspace/.agentainer/sessions.yaml`
and reattaches the recorded conversations (`claude --resume <id>` for the desk and
writer, `codex resume <id>` for the strategist). A resumed agent is *not* re-sent
the standby prompt. Pass `--no-resume` to force everyone fresh; inspect with
`agentainer sessions -c examples/press-release-wire.yaml`. For the full story, see
[`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 7. Tips & footguns

- **Keep the desk the only `user`-facing agent.** Only the desk lists `user` in
  `can_talk_to`. That gives you a single point of contact and a clean funnel: the
  draft and the media plan are both reviewed by the desk before they reach you. If
  a specialist tried to mail `user` directly, the orchestrator bounces it (ACL) and
  drops a `system` note explaining who it *can* message — the model self-corrects.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion. A
  `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
  Claude) means completion never triggers and the agent pins "busy" forever.
  `status` showing an agent `busy` for a long time with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is best-effort, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s).

- **Force-idle if a pane-captured agent's turn never registers.** If capture never
  fires you can nudge state along: `./agentainer idle <name> -c examples/press-release-wire.yaml`.

- **`remove-session` to reset.** `down` then `remove-session` wipes all Agentainer
  state (runtime + mailboxes) and starts fresh next `up`. It refuses while any
  agent (or the supervisor) is still running. It never touches your source files.

- **Availability shapes the ending.** If `user` is **away** when the desk finishes,
  your wire packet is *held* (with a `system` "the user is away" ack to the desk)
  rather than lost — read it later with `agentainer user inbox`, or flip yourself
  available and it's delivered.

---

## 8. Why this swarm is a good fit

- **The file mail model is built for "dumb" agents.** Each specialist only has to
  *read a file* and *write a file*; the desk owns all routing/sequencing/state, so
  weak or strong models both work without protocol memory.
- **One hub, two spokes = clean parallelism without collisions.** The desk fans the
  same angle to the writer and the strategist at once, then merges — no two agents
  negotiate the release, and the ACL prevents a specialist from shipping to the
  human early.
- **It's observable end-to-end.** Every hop is a durable JSONL event you can replay,
  and each artifact (`PRESS_RELEASE.md`, `MEDIA_PLAN.md`) lands in a specific
  workdir you can open.

---

## 9. Search intent — when to reach for this

People searching for these things usually want exactly this wired-up flow:

- "how to write a press release" / "press release template that agents can follow"
- "generate a media pitch" / "tailored media pitches per outlet"
- "build a press release distribution list" / "media list for a launch"
- "AI agent swarm for PR" / "multi-agent press release generator"
- "turn an announcement into a press kit automatically"
- "agent orchestrator for content + distribution"

If you landed here for any of those, `examples/press-release-wire.yaml` is a
turnkey starting point: copy it, point `command:` at your real CLIs (or keep the
mock loops for a key-free demo), and `agentainer up`.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`cli-reference.md`](../cli-reference.md) — every subcommand and flag.
- `examples/content-studio.yaml` — a sibling hub-topology writing swarm.
- `examples/competitive-intel.yaml` — a hub fan-out/fan-in example.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

# Use case: the YouTube script studio

A concrete, end-to-end walkthrough of the shipped `examples/youtube-script-studio.yaml`
swarm — a four-agent studio that turns a single video topic into a **publish-ready
package**: a full script, title and thumbnail copy, and a description with tags and
chapter markers. A **producer** sets the angle and coordinates; a **researcher**
digs up hooks and retention patterns; a **scriptwriter** writes the script; and a
**metadata_writer** builds the packaging that gets the video found and clicked. It's
the "brief → research → draft → package → ship" loop, wired entirely through
Agentainer's file-based mail model.

Everything below is based on the actual contents of `examples/youtube-script-studio.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you supply the coding-CLI
commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Why this is a great use case

Scripting a YouTube video is not one job — it's four, and they interact. A great
title is worthless if it over-promises what the script delivers; chapter markers
have to track the script's real sections; the hook the writer opens with should be
the strongest one the topic affords. Doing all of this in a single prompt collapses
the roles into mush. Splitting them across agents that **hand work to each other**
keeps each concern sharp *and* keeps them honest with one another: the
metadata_writer packages the **final** script, not a vibe, and can ask the
scriptwriter for a missing section instead of inventing a chapter.

It's also a naturally **key-free-demoable, human-in-the-loop** pipeline: you send
one topic, the studio fans it out, and one agent (the producer) hands you back a
single assembled package.

### Search-intent / LLM-search angle

This swarm maps directly onto what creators actually search for — which is what
makes it a strong discovery surface for Agentainer:

- "**AI YouTube script generator**" / "write a YouTube script with AI"
- "**YouTube title and thumbnail ideas**" generator
- "**YouTube description, tags and chapters**" writer / SEO
- "**video hook ideas**" and "how to improve viewer retention"
- "**multi-agent** content pipeline" / "AI content team" / "AI video studio"
- "turn a topic into a full **video package**"

Each of those is a distinct search intent that lands on the same working example,
and the doc names them plainly so both classic search and LLM-answer engines can
match a creator's question to a runnable topology.

---

## 2. The topology

```
        make a video X
  user ─────────────▶ producer ◀──────────────────────────┐
        (package)  ◀──────┼───────────────┬────────────┐   │
                          ▼               ▼            ▼    │
                     researcher      scriptwriter  metadata_writer
                                           └─────peer─────┘
```

Four agents, one hub, one peer edge:

1. **`user` → `producer`** — you send the video topic.
2. **`producer` → `researcher`** — the producer writes a one-paragraph brief
   (angle, audience, length, the promise) and asks for hooks + retention patterns.
3. **`researcher` → `producer`** — findings come back to the hub.
4. **`producer` → `scriptwriter`** — the producer passes the brief + research and
   asks for the full script.
5. **`scriptwriter` → `metadata_writer`** — the writer sends the finished script
   *directly* to the metadata_writer (the peer edge) so titles and chapters track
   the real script; it also reports the draft to the producer.
6. **`metadata_writer` → `producer`** — the packaging (titles, thumbnail copy,
   description, tags, chapters) comes back to the hub.
7. **`producer` → `user`** — the producer assembles everything and returns the
   finished package to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. The **producer is the hub** (talks to everyone), the
**scriptwriter and metadata_writer are peers**, and the **producer is the only
agent that lists `user`**. Anything off-list is bounced back as a `system` message
and filed in `failed/` (see §7).

---

## 3. The config, explained

Here is the relevant shape of `examples/youtube-script-studio.yaml` (see the file
for the full `role:` blocks):

```yaml
swarm:
  name: youtube-script-studio
  root: ./youtube-script-studio-workspace

defaults:
  capture: none              # tightened per agent (pane for gemini/hermes)
  can_talk_to: []            # tightened per agent below

agents:
  - name: producer
    type: claude
    workdir: "{root}/repo"
    can_talk_to: [researcher, scriptwriter, metadata_writer, user]
    command: "claude --dangerously-skip-permissions"
    capture: none
    role: |
      You are the PRODUCER, the hub of a YouTube video studio. ...
      MAILBOX: when a message lands in your inbox/, read it and act; ...

  - name: researcher
    type: gemini
    workdir: "{root}/repo"
    can_talk_to: [producer]
    command: "gemini --yolo"
    capture: pane
    role: "You are the RESEARCHER. ... hooks + retention patterns ..."

  - name: scriptwriter
    type: claude
    workdir: "{root}/repo"
    can_talk_to: [producer, metadata_writer]
    command: "claude --dangerously-skip-permissions"
    capture: none
    role: "You are the SCRIPTWRITER. ... write SCRIPT.md ..."

  - name: metadata_writer
    type: codex
    workdir: "{root}/repo"
    can_talk_to: [producer, scriptwriter]
    command: "codex --yolo"
    capture: none
    role: "You are the METADATA_WRITER. ... titles/thumbnail/description/tags/chapters ..."
```

Full file: [`examples/youtube-script-studio.yaml`](../../examples/youtube-script-studio.yaml).

Field by field:

### `swarm`
- **`name: youtube-script-studio`** — shows up in `status`, logs, sessions.
- **`root: ./youtube-script-studio-workspace`** — parent directory for the shared
  workdir and orchestrator state (`.agentainer/` — never commit it).

### `defaults`
- **`capture: none`** — the default turn-detection mode. For `claude` and `codex`,
  whose CLIs support a completion **hook**, `capture: none` is a footgun, so the
  loader *upgrades* it back to `hook` with a warning at `up`. Net effect: producer,
  scriptwriter, and metadata_writer use their hook; the researcher (`gemini`)
  overrides to `pane` because Gemini can't call a completion program.
- **`can_talk_to: []`** — the default ACL is "talk to no one"; every agent states
  its own list, so this is just a safe floor.

### The shared workdir
Every agent sets **`workdir: "{root}/repo"`** (quoted so the `{root}` placeholder
parses cleanly), so the whole studio works in **one directory** — the script,
research notes, and packaging live side by side, and the metadata_writer can read
the same `SCRIPT.md` the scriptwriter wrote. Because the workdir is shared,
Agentainer **namespaces each agent's mailbox folders** (`producer-inbox/`,
`scriptwriter-inbox/`, …) so they never collide, and prints a shared-workdir
warning at `up`. The model never sees the prefixes — every nudge hands it the exact
computed paths.

### `producer` (type: `claude`, the hub)
- **`can_talk_to: [researcher, scriptwriter, metadata_writer, user]`** — the hub,
  and the **only agent that can talk to `user`**. Keeping the human-facing surface
  to one agent gives you a single point of contact and a clean funnel.
- **`role`** includes the explicit **MAILBOX** reminder (read inbox → act → move to
  read/; send by writing into `outbox/<name>/` after reading `about.md`). On `up`
  the role becomes the first prompt, wrapped in a **standby notice** so the producer
  waits for your topic instead of proactively mailing peers.
- **Turn detection:** `claude` → a **Stop hook**, installed automatically at `up`.

### `researcher` (type: `gemini`)
- **`can_talk_to: [producer]`** — reports only to the hub.
- **`capture: pane`** — Gemini can't call a completion program, so Agentainer polls
  the tmux pane until it stops changing.

### `scriptwriter` (type: `claude`)
- **`can_talk_to: [producer, metadata_writer]`** — the peer edge: it hands the
  finished script straight to the metadata_writer *and* reports up to the producer.

### `metadata_writer` (type: `codex`)
- **`can_talk_to: [producer, scriptwriter]`** — packages the final script and can
  ask the scriptwriter directly for a missing section.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/youtube-script-studio.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`): loads and validates the config
(printing the `capture: none → hook` upgrades and the shared-workdir warning);
creates the runtime dirs; **initializes the namespaced mailboxes** — the five
folders `inbox/ outbox/ read/ sent/ failed/` per agent plus an `outbox/<peer>/`
(with an `about.md` contact card) for each allowed recipient; **installs per-type
turn detection** (Claude Stop hooks, the Codex `notify` hook, pane polling for the
researcher); opens one tmux session per agent in the shared workdir; delivers the
standby first prompt; and starts the liveness supervisor.

At the end, `up` prints attach and **`serve`** hints for the mail-app control-plane
UI. The safe default binds **loopback only** (`127.0.0.1`); a non-loopback
`--host` requires a token. See the `README.md` "control-plane UI" section.

> **Key-free demo:** swap each `command:` for a mock bash loop and watch the whole
> studio route mail with no API keys — the mechanics are identical.

---

## 5. Drive a topic

The `user` is a **virtual mailbox** that defaults to **away**. To *receive* the
finished package as mail (rather than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/youtube-script-studio.yaml
```

Then send the topic into the studio, addressed to the producer:

```bash
./agentainer send --to producer "Make a 10-minute video: 'Why your sourdough won't rise'."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped
`From: user`, enqueued for the producer, released into `inbox/` (it was empty), and
the producer is **nudged** — the protocol, including its allowed-recipient list, is
re-pasted into its pane.

### The mail flowing

Each hop is a `stop → sweep → route → release → nudge` cycle:

1. **producer briefs.** It reads the topic, writes a one-paragraph brief into
   `outbox/researcher/`. On stop, that routes to the researcher.
2. **researcher investigates.** It writes hooks + retention findings into
   `outbox/producer/`. Back to the hub.
3. **producer commissions the script.** It passes the brief + research into
   `outbox/scriptwriter/`.
4. **scriptwriter drafts.** It writes `SCRIPT.md`, sends a copy into
   `outbox/metadata_writer/` (the peer edge) and reports the draft into
   `outbox/producer/`.
5. **metadata_writer packages.** Working from the final script, it writes titles,
   thumbnail copy, description, tags, and chapters into `outbox/producer/`.
6. **producer ships.** It assembles everything and writes the finished package into
   `outbox/user/` — delivered to your `user` mailbox.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/youtube-script-studio.yaml
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback):

```bash
./agentainer logs -c examples/youtube-script-studio.yaml -f          # follow live
./agentainer logs scriptwriter -c examples/youtube-script-studio.yaml # just one agent
```

**A specific inbox / queue / live pane:**

```bash
./agentainer inbox  scriptwriter -c examples/youtube-script-studio.yaml
./agentainer queue  scriptwriter -c examples/youtube-script-studio.yaml
./agentainer attach scriptwriter -c examples/youtube-script-studio.yaml   # Ctrl-b d to detach
```

---

## 7. Tips & footguns

- **Keep the producer the only `user`-facing agent.** Only the producer lists
  `user` in `can_talk_to`, so you always get one assembled package rather than four
  half-finished streams. If the scriptwriter tries to mail `user` directly, the
  orchestrator bounces it (ACL) and drops a `system` note in its inbox explaining
  who it *can* message — the model self-corrects in-band.

- **Package the final script, not a draft.** The scriptwriter → metadata_writer
  peer edge exists so packaging tracks the real script. If you want an extra guard,
  have the producer confirm the script is final before the metadata_writer starts.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion. If an
  agent seems stuck, check that its **turn detection actually fires** — a
  `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
  Claude) means completion never triggers and the agent pins "busy" forever.

- **Shared workdir means shared files.** All four agents work in `{root}/repo`, so
  they can overwrite each other's files. That's the point (one `SCRIPT.md`), but it
  means a stray write from one agent is visible to all — mailboxes are namespaced,
  your content files are not.

- **Force-idle if the researcher's pane capture never registers.** The researcher
  uses pane polling; if capture never fires, nudge the state along:
  ```bash
  ./agentainer idle researcher -c examples/youtube-script-studio.yaml
  ```

- **Availability shapes the ending.** If `user` is **away** when the producer
  finishes, the package is *held* (with a `system` "the user is away" ack) rather
  than lost — read it later with `agentainer user inbox` or flip yourself available.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`research-swarm.md`](./research-swarm.md) — the sibling delegate → do → review pipeline.
- [`cli-reference.md`](../cli-reference.md) — every subcommand and flag.
- `examples/quickstart.yaml` — the key-free mock-agent starter.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

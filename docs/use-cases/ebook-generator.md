# Use case: the ebook generator swarm

A concrete, end-to-end walkthrough of the shipped `examples/ebook-generator.yaml`
swarm — a four-agent pipeline that turns a topic into a **self-published
nonfiction ebook**. An **outliner** designs the book's structure, a
**chapter_writer** drafts each chapter, an **editor** revises for clarity and
correctness, and a **formatter** assembles the publish-ready manuscript. It's the
canonical "outline → write → edit → format" book-production loop, wired entirely
through Agentainer's file-based mail model.

If you've ever searched for how to **write an ebook with AI agents**,
**self-publish a book from an outline**, or run a **multi-agent writing pipeline**,
this is that pipeline — no framework, no build step, no API keys required to
understand the mechanics.

Everything below is based on the actual contents of `examples/ebook-generator.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). To run it *for
real* you supply the coding-CLI commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Why this swarm is great

Book production is naturally a **pipeline with a single owner**. One mind should
hold the book's structure and voice; the grunt work of drafting, editing, and
formatting fans out and comes back. That maps perfectly onto a hub-and-spoke
mail topology:

- **One structural owner.** The outliner is the only agent that sees the whole
  book and the only one that talks to you — so the table of contents stays
  coherent instead of drifting as chapters accumulate.
- **Separation of concerns.** Drafting, editing, and formatting are different
  skills with different failure modes; giving each its own agent keeps a draft's
  first-pass roughness from contaminating the final format pass.
- **One chapter at a time.** The outliner commissions a single chapter brief per
  turn, so long books don't blow up context or interleave half-finished drafts.
- **Auditable handoffs.** Every draft, revision, and format pass is a mail file
  in an `outbox/` and a durable JSONL log line — you can reconstruct exactly how
  the manuscript evolved.

---

## 2. The topology

```
        write an ebook on X
  user ─────────────────────▶ outliner ───────────────▶ chapter_writer
        (finished ebook)  ◀────── │  ▲                       │
                                  │  └───── draft ───────────┘
                                  │
                                  ├──── draft ────▶ editor ──── revision ───┐
                                  │  ◀─────────────────────────────────────┘
                                  │
                                  └── manuscript ─▶ formatter ── ebook ─────┐
                                     ◀──────────────────────────────────────┘
```

Four agents, one directed flow with the outliner as the hub:

1. **`user` → `outliner`** — you send the book topic.
2. **`outliner` → `chapter_writer`** — the outliner sends one chapter brief and
   collects the draft.
3. **`outliner` → `editor`** — each finished draft goes to the editor, who
   returns a revision.
4. **`outliner` → `formatter`** — once every chapter is edited, the full
   manuscript goes to the formatter for assembly.
5. **`outliner` → `user`** — the outliner delivers the finished ebook to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. The three spokes list only `outliner`; they **cannot** reach
each other or `user`. Anything off-list is bounced back as a `system` message and
filed in `failed/` (see §7).

---

## 3. The config, explained

Here is `examples/ebook-generator.yaml` in full (see the file itself:
[`examples/ebook-generator.yaml`](../../examples/ebook-generator.yaml)):

```yaml
swarm:
  name: ebook-generator
  root: ./ebook-generator-workspace

defaults:
  capture: none              # keyed off `type`; hook-capable CLIs auto-upgrade
  can_talk_to: []            # tightened per agent below

agents:
  - name: outliner
    type: claude
    can_talk_to: [chapter_writer, editor, formatter, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the OUTLINER, the hub and editor-in-chief of a small ebook team.
      ...
  - name: chapter_writer
    type: codex
    can_talk_to: [outliner]
    command: "codex --yolo"
    role: |
      You are the CHAPTER WRITER. Given one chapter brief from the outliner ...
  - name: editor
    type: claude
    can_talk_to: [outliner]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the EDITOR. Given a chapter draft from the outliner ...
  - name: formatter
    type: gemini
    can_talk_to: [outliner]
    capture: pane
    command: "gemini --yolo"
    role: |
      You are the FORMATTER. Given the full edited manuscript ...
```

Field by field:

### `swarm`
- **`name: ebook-generator`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./ebook-generator-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent gets
  `ebook-generator-workspace/<name>/` as its workdir (created on `up`), with its
  mailbox folders alongside. Orchestrator state goes under
  `ebook-generator-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless overridden.
- **`capture: none`** — the default turn-detection mode. But `capture` is how
  Agentainer knows a turn finished, and it's keyed off each agent's `type`. For
  `claude` and `codex`, whose CLIs support a completion **hook**, `capture: none`
  is a footgun — so the loader *upgrades* it back to `hook` and warns at `up`.
  Net effect: the outliner, chapter_writer and editor use their hook; the
  formatter overrides to `pane`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent states
  its own list, so this default is just a safe floor.

### `outliner` (type: `claude`)
- **`can_talk_to: [chapter_writer, editor, formatter, user]`** — the outliner is
  the hub: it commissions the writer, routes drafts to the editor, sends the
  manuscript to the formatter, and is the **only agent that can talk to `user`**.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command or a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the first prompt,
  wrapped in a **standby notice**, so the outliner waits for your topic. The role
  ends with the **HUB MAILBOX reminder** (read `inbox/`, act, move to `read/`;
  send by writing into `outbox/<name>/`).
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `chapter_writer` (type: `codex`)
- **`can_talk_to: [outliner]`** — reports only to the outliner; cannot reach the
  editor, formatter, or `user`.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "write one chapter draft at a time from the brief."
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `editor` (type: `claude`)
- **`can_talk_to: [outliner]`** — returns revisions only to the outliner.
- **`role`** — "revise for clarity, flow, correctness; return a changelog."

### `formatter` (type: `gemini`)
- **`can_talk_to: [outliner]`** — returns the assembled ebook to the outliner.
- **`capture: pane`** — Gemini's CLI can't call a completion program, so
  Agentainer detects "turn done" by **polling the tmux pane** until it stops
  changing. (This is why the formatter overrides the `none` default.)
- **`command: "gemini --yolo"`** — placeholder launch command.
- **`role`** — "assemble title page, TOC, headings; don't rewrite prose."

### What's *not* in this config
- **No `pings`.** No agent is auto-nudged on a timer — the
  pipeline is purely event-driven off real mail.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §5).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/ebook-generator.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for outliner/chapter_writer/editor).
2. Creates the runtime dirs (`ebook-generator-workspace/.agentainer/…`).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: the outliner gets
   `outbox/chapter_writer/`, `outbox/editor/`, `outbox/formatter/`,
   `outbox/user/`; each spoke gets only `outbox/outliner/`.
4. **Installs per-type turn detection** — Claude Stop hooks for the outliner and
   editor, the Codex `notify` hook for the chapter_writer, and pane polling for
   the formatter.
5. **Opens one tmux session per agent**, `cd`'d into its workdir.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints. The `serve` line gives you
the mail-app control-plane UI (threads, live panes, send-as-user, availability
toggle). By default the UI binds **`127.0.0.1`** (loopback only); pass
`--host`/`--token` only for an intentional remote bind. See the `README.md`
"control-plane UI" section.

> **Key-free demo:** swap each `command:` for a mock bash loop and watch the whole
> pipeline route mail with no API keys — the mechanics are identical.

---

## 5. Drive a book

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. To *receive* the finished ebook as mail (rather than have it held), turn
yourself available first:

```bash
./agentainer user available -c examples/ebook-generator.yaml
```

Now send the topic into the swarm, addressed to the outliner:

```bash
./agentainer send --to outliner "Write a short ebook on home composting for beginners."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the outliner, then — because the
inbox was empty — **released into `inbox/`** and the outliner is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **outliner plans.** It reads `inbox/`, drafts the premise and chapter outline,
   confirms scope, then writes the first chapter brief into
   `outbox/chapter_writer/`. On stop, that routes to the writer.
2. **chapter_writer drafts.** It reads its brief, writes the chapter, and returns
   the draft to `outbox/outliner/`.
3. **outliner routes to editing.** It sends the draft to `outbox/editor/`.
4. **editor revises.** It returns the revised chapter (plus changelog) to
   `outbox/outliner/`. Steps 1–4 repeat per chapter.
5. **outliner assembles.** With all chapters edited, it sends the full manuscript
   to `outbox/formatter/`.
6. **formatter publishes.** It assembles the title page, TOC and clean Markdown,
   and returns the ebook to `outbox/outliner/`.
7. **outliner delivers.** It writes the finished ebook into `outbox/user/`. On
   stop, that's delivered to your `user` mailbox (`agentainer user inbox`, or the
   UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a topic, the agents sit in standby. The pipeline only moves
> when real mail arrives — this swarm has no periodic pings to self-start it.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/ebook-generator.yaml
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback):

```bash
./agentainer logs -c examples/ebook-generator.yaml           # whole swarm
./agentainer logs -c examples/ebook-generator.yaml -f        # follow live
./agentainer logs chapter_writer -c examples/ebook-generator.yaml
```

**A specific inbox / queue / live pane:**

```bash
./agentainer inbox  editor    -c examples/ebook-generator.yaml
./agentainer queue  chapter_writer -c examples/ebook-generator.yaml
./agentainer attach formatter -c examples/ebook-generator.yaml   # Ctrl-b d to detach
```

---

## 7. Search intent — what people ask, and how this answers it

- **"How do I write an ebook with AI?"** — Send one topic to the outliner; it
  produces the outline and drives drafting, editing, and formatting end to end.
- **"Multi-agent book writing pipeline"** — This is a four-agent
  outline→write→edit→format pipeline with enforced routing.
- **"Self-publish a book from an outline"** — The formatter emits publish-ready
  Markdown with a title page and TOC that convert to EPUB/PDF.
- **"How to structure an AI writing team"** — Hub-and-spoke: one structural owner
  (the outliner), specialists on the spokes, no sideways chatter.
- **"AI ghostwriter / chapter generator"** — The chapter_writer drafts one
  chapter per brief so long books don't blow up context.

---

## 8. Tips & footguns

- **Keep the outliner the only `user`-facing agent.** Only the outliner lists
  `user` in `can_talk_to`. That gives you a single point of contact and guarantees
  raw drafts pass through editing and formatting before they reach you. If a spoke
  tries to mail `user` directly, the orchestrator bounces it (ACL) and drops a
  `system` note explaining who it *can* message — the model self-corrects in-band.

- **One chapter at a time.** The outliner's role tells it to commission a single
  chapter brief per turn. This keeps drafts from interleaving and keeps each
  agent's context small on long books.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion. If an
  agent seems stuck, check that its **turn detection actually fires** — a
  `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
  Claude) means completion never triggers and the agent pins "busy" forever.
  `status` showing an agent `busy` for a long time with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** Mail moved to `read/` is a
  best-effort receipt; a message shown `AUTO_ARCHIVE_PRESENTATIONS` (5) times
  without being handled is auto-archived so the queue advances. A per-pair runaway
  cap (≤20 messages / 60s) kills "thanks!/you're welcome!" loops.

- **Force-idle if the pane-captured formatter's turn never registers.** The
  formatter uses pane polling; if its capture never fires you can nudge state:
  ```bash
  ./agentainer idle formatter -c examples/ebook-generator.yaml
  ```

- **Availability shapes the ending.** If `user` is **away** when the outliner
  finishes, your finished ebook is *held* (with a `system` "the user is away" ack)
  rather than lost — read it later with `agentainer user inbox` or flip yourself
  available and it's delivered.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`cli-reference.md`](../cli-reference.md) — every subcommand and flag.
- [`use-cases/research-swarm.md`](./research-swarm.md) — the delegate→do→review pipeline.
- `examples/quickstart.yaml` — the key-free mock-agent starter.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

# Use case: meeting notes & action-items swarm

A concrete, end-to-end walkthrough of the shipped `examples/meeting-notes.yaml`
swarm — a **fan-out / fan-in pipeline** that turns a messy meeting transcript or
rough notes into one clean packet: structured notes, a tight executive summary,
and a decisions + action-items list with owners and due dates. You paste the raw
text at the **chief**, three specialists each work the same material in parallel,
and the chief collates their outputs into a single deliverable addressed back to
**you**.

Everything below is based on the actual contents of `examples/meeting-notes.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you point each `command`
at a coding-CLI (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state. For the general delegation
> pattern this swarm is a flavour of, see
> [`./delegation-pipeline.md`](./delegation-pipeline.md).

---

## 1. The topology

```
          raw transcript / rough notes
   user ───────────────────────────▶ chief ──┬──▶ transcriber   (clean structured notes)
        ◀───────────────────────            ├──▶ summarizer     (tight executive summary)
             final packet (collated)         └──▶ actionizer     (decisions + action items)
                                               │       │       │
                                               ▼       ▼       ▼
                                              chief   (collects all three → user)
```

Four agents, one directed flow:

1. **`user` → `chief`** — you paste the raw transcript or your rough notes.
2. **`chief` → `transcriber | summarizer | actionizer`** — the chief fans the
   *same* raw text out to all three specialists in parallel, each with a one-line
   instruction naming what to return.
3. **`transcriber | summarizer | actionizer` → `chief`** — each specialist returns
   its piece to the chief (never to each other, never to you).
4. **`chief` → `user`** — the chief collates the three pieces into one packet and
   delivers it to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. The three specialists can only address the chief, and only the
chief can address `user`; anything else is bounced back as a `system` message and
filed in `failed/` (see §7).

**Why a fan-out rather than a chain?** Transcribing, summarizing, and extracting
action items are independent reads of the *same* source. Running them in parallel
is faster and keeps each specialist focused on one job (a transcriber that's also
summarizing tends to drop detail). The chief is the only place that needs the whole
picture, so it's the only place the three views are stitched together.

---

## 2. The config, explained

Here is `examples/meeting-notes.yaml` in full:

```yaml
# 📝 Meeting notes & action-items -- paste raw transcript in, get a clean packet out.
swarm:
  name: meeting-notes
  root: ./meeting-notes-workspace
defaults:
  capture: none              # claude/codex auto-upgrade to their hook at `up`
  can_talk_to: []
agents:
  - name: chief
    type: claude
    can_talk_to: [transcriber, summarizer, actionizer, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the CHIEF OF STAFF running a meeting write-up. ...
      (briefs transcriber / summarizer / actionizer in parallel, then
       collates their replies into one packet and delivers it to user)
      MAILBOX: ... you may only message transcriber, summarizer, actionizer, user.

  - name: transcriber
    type: claude
    can_talk_to: [chief]
    command: "claude --dangerously-skip-permissions"
    role: "Clean the raw text into structured, readable notes. Do not summarize."

  - name: summarizer
    type: claude
    can_talk_to: [chief]
    command: "claude --dangerously-skip-permissions"
    role: "Write a tight executive summary (3-6 sentences + key outcomes)."

  - name: actionizer
    type: claude
    can_talk_to: [chief]
    command: "claude --dangerously-skip-permissions"
    role: "Extract DECISIONS and ACTION ITEMS with owners + due dates."
```

(The full `role:` text for each agent is in the file; the snippets above are the
load-bearing parts.)

### `swarm`
- **`name: meeting-notes`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./meeting-notes-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent gets
  `meeting-notes-workspace/<name>/` as its workdir (created on `up`), and its
  mailbox folders live alongside. Orchestrator state goes under
  `meeting-notes-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, and it's ultimately keyed off each
  agent's `type`. All four agents here are `claude`, whose CLI supports a
  completion **hook**, so the config loader *upgrades* `capture: none` back to
  `hook` and prints a warning at `up` (`capture: none on a claude agent gives the
  orchestrator no way to detect turn completion; using the type's default:
  capture: hook.`). Net effect: every agent is driven by its Stop hook. If you
  changed an agent's `type` to `gemini`/`hermes`, you'd set `capture: pane` on it
  (those types have no completion hook and are detected by polling the pane).
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `chief` (type: `claude`, the hub)
- **`can_talk_to: [transcriber, summarizer, actionizer, user]`** — the chief is
  the **only agent that can talk to `user`**, and the only one the specialists can
  reply to. That makes it the single funnel for both incoming raw material and the
  outgoing packet.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the chief waits for your transcript instead of
  proactively mailing peers. Its instructions are explicit about the fan-out (send
  the *same* raw text to all three) and the fan-in (wait for all three, then
  collate in the order Summary → Notes → Decisions → Action Items, and deliver to
  `user`). The role also embeds the **MAILBOX** reminder: read the inbox, move it
  to `read/`, and write outgoing mail into `outbox/<name>/` after reading that
  peer's `about.md`.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `transcriber` (type: `claude`)
- **`can_talk_to: [chief]`** — can report only to the chief. It deliberately can't
  reach `user` or the other specialists, so the chief stays the single source of
  truth for the packet.
- **`role`** — "clean the raw text into structured, readable notes; do not
  summarize; do not invent; attribute when clear; mark [inaudible]." This keeps the
  notes faithful to the source so the summarizer and actionizer can rely on the
  chief's collation rather than re-deriving it.
- **Turn detection:** `claude` → Stop hook.

### `summarizer` (type: `claude`)
- **`can_talk_to: [chief]`** — reports only to the chief.
- **`role`** — "tight executive summary, 3-6 sentences + at most 5 bullets, lead
  with decisions and next steps, stay faithful." Reads the *raw* material, not the
  transcriber's notes — so a summarization weakness can't compound a transcription
  error.
- **Turn detection:** `claude` → Stop hook.

### `actionizer` (type: `claude`)
- **`can_talk_to: [chief]`** — reports only to the chief.
- **`role`** — "extract DECISIONS (what was settled, by whom if stated) and ACTION
  ITEMS as `- [owner] task -- due <date>`; never invent an owner or date — use
  `[unassigned]` / `due: TBD` when unknown." This is the part people most want out
  of a meeting, so it has the strictest instruction about not fabricating
  accountability.
- **Turn detection:** `claude` → Stop hook.

### What's *not* in this config
- **No `pings`.** The pipeline is purely event-driven off real
  mail — the chief only acts when your transcript arrives, and the specialists only
  act when the chief briefs them. (If you wanted to chase a slow specialist, you'd
  add a `pings` cron rule to it.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — the final packet addressed to you is *held* (never bounced) until you
  flip it on (see §4).
- **No `telegram:` block.** The Telegram bridge is off by default; enable it under
  `telegram:` if you want the packet mirrored to a chat (see `telegram-bridge.md`).

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/meeting-notes.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the `capture: none → hook` upgrade
   warnings (one per `claude` agent).
2. Creates the runtime dirs (`meeting-notes-workspace/.agentainer/…`: log, queue,
   run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's `about.md`
   contact card *is* the ACL made visible: the chief gets
   `outbox/transcriber/`, `outbox/summarizer/`, `outbox/actionizer/`,
   `outbox/user/`; each specialist gets `outbox/chief/`.
4. **Installs per-type turn detection** — the Claude Stop hook for all four agents.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'meeting-notes' is up with 4 agent(s)
:: attach with:  tmux attach -t <chief-session>
:: you can use the UI with:  agentainer serve -c examples/meeting-notes.yaml --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). By default it binds **`127.0.0.1`** — never
`0.0.0.0` — so it's only reachable from this machine; to expose it remotely you
must opt in with `--host` **and** a `--token` (the UI can type into agents that may
run `--dangerously-skip-permissions`, so it's a control plane, not a toy). See
[`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole pipeline route mail with no API keys — the mechanics are identical.

---

## 4. Drive a meeting write-up

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the final packet as mail (rather than have it
held), turn yourself available first:

```bash
./agentainer user available -c examples/meeting-notes.yaml
```

This rewrites the `user` contact card in the chief's `outbox/user/about.md` to
`Status: available`, so the chief sees you're reachable. (While away, mail to you
is *held* and the sender gets a `system` ack — nothing bounces.)

Now paste the raw material into the swarm, addressed to the chief:

```bash
./agentainer send --to chief -c examples/meeting-notes.yaml -- "Q3 planning, 2026-07-09.
Priya: we are shipping the new billing API on Aug 1. Diego: I'll own the migration
script, need it by July 25. (crosstalk) um, also we decided to drop the CSV export
feature -- too few users. Priya: yes, deprecate it end of Q3. Mara: can someone
write the changelog? Diego: I'll do it with the migration. Open question: do we
need a rollback plan? Left for next week."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the chief, then — because the
inbox was empty — **released into `inbox/`** and the chief is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§5), you'll see the fan-out then the fan-in, each arrow a
`stop → sweep → route → release → nudge` cycle:

1. **chief receives the raw text.** It reads `inbox/`, forwards the *same* text to
   `outbox/transcriber/`, `outbox/summarizer/`, and `outbox/actionizer/` (each with
   a one-line instruction). When its turn ends, the orchestrator sweeps the outbox,
   routes the three messages, and nudges all three specialists.
2. **the three specialists run in parallel.** Each reads its inbox, does its job,
   and writes its piece into `outbox/chief/`. On each stop, that routes back to the
   chief. They don't wait on each other — the chief collects as they land.
3. **chief collates and delivers.** Once all three replies are in, the chief reads
   them, stitches them into one packet (Summary → Notes → Decisions → Action Items),
   and writes it into `outbox/user/`. On stop, that's delivered to your `user`
   mailbox (visible with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a transcript, the agents just sit in standby (that's the
> point of the standby prompt). The pipeline only moves when real mail arrives.

---

## 5. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/meeting-notes.yaml
```

```
swarm: meeting-notes   root: ./meeting-notes-workspace
  chief (claude) up idle queue=0 unread=0 talks=transcriber, summarizer, actionizer, user
  transcriber (claude) up idle queue=0 unread=1 talks=chief
  summarizer (claude) up idle queue=0 unread=1 talks=chief
  actionizer (claude) up idle queue=0 unread=1 talks=chief
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/meeting-notes.yaml          # whole swarm, last 20
./agentainer logs -c examples/meeting-notes.yaml -f        # follow live
./agentainer logs chief -c examples/meeting-notes.yaml     # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox chief -c examples/meeting-notes.yaml
```

Prints the one released message (headers + body), or `chief: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue chief -c examples/meeting-notes.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach transcriber -c examples/meeting-notes.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

**Read your delivered packet** (once the chief has finished):

```bash
./agentainer user inbox -c examples/meeting-notes.yaml
```

---

## 6. Resume after a stop

Tear the swarm down when you're done:

```bash
./agentainer down -c examples/meeting-notes.yaml
```

Bring it back later and **conversations resume by default**:

```bash
./agentainer up -c examples/meeting-notes.yaml     # resume is the default
```

On `up`, Agentainer reads `meeting-notes-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via `claude --resume <id>`. A resumed agent is *not* re-sent the
standby prompt (its prior context is restored) — convenient if you're iterating on
the same recurring meeting. For the full story, see
[`sessions-and-resume.md`](../sessions-and-resume.md) and the reboot walkthrough in
[`use-cases/resume-after-reboot.md`](./resume-after-reboot.md).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/meeting-notes.yaml
```

---

## 7. Tips & footguns

- **Keep the chief the only `user`-facing agent.** Only the chief lists `user` in
  `can_talk_to`. That gives you a single funnel: the packet is assembled in one
  place and you get exactly one deliverable. If a specialist tried to mail `user`
  directly, the orchestrator bounces it (ACL) and drops a `system` note in the
  specialist's inbox explaining who it *can* message — the model self-corrects
  in-band.

- **`user` away = packet held, not lost.** If you forget to run `user available`
  before the chief finishes, the packet is *held* with a `system` "the user is away"
  ack to the chief. Run `user available` and it's delivered; or read it later with
  `agentainer user inbox`. Nothing is dropped.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion. If an
  agent seems stuck `busy` with `unread` mail in `status`, suspect its turn
  detection never fired — almost always a `type`/`command` mismatch (a `claude`
  agent whose `command` doesn't actually launch Claude means completion never
  triggers and the agent pins "busy" forever). The config here keeps `type:
  claude` matched to `command: "claude …"`, so the Stop hook fires correctly.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **Force-idle if a stop never registers.** If a `claude` agent's Stop hook didn't
  fire and the turn looks pinned, nudge the state along:
  ```bash
  ./agentainer idle transcriber -c examples/meeting-notes.yaml
  ```

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/meeting-notes.yaml
  ./agentainer remove-session -c examples/meeting-notes.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

---

## 8. Customize it

This swarm is a starting point. A few common variations:

**Add a `translator` for non-English meetings.** If your transcripts mix
languages, add a fifth specialist that normalizes the raw text to English before
the others read it, and have the chief route through it first:

```yaml
  - name: translator
    type: claude
    can_talk_to: [chief]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are a TRANSLATOR. Given meeting material in any language, produce an
      English version faithful to meaning and speaker. Preserve names and numbers.
      Send the translation to the chief by writing to outbox/chief/.
```
and change the chief's `can_talk_to` to include `translator`, and its role to
"translate first if needed, then brief the other three from the translated text."

**Swap models to spread the work across vendors.** The specialists are
independent, so you can mix types freely (see
[`./multi-llm-swarm.md`](./multi-llm-swarm.md)):

```yaml
  - name: summarizer
    type: gemini             # gemini has no completion hook...
    capture: pane            # ...so poll the pane for turn completion
    can_talk_to: [chief]
    command: "gemini --yolo"
    role: "Write a tight executive summary ..."
```
When you change `type`, keep `command` matched to it (`gemini → "gemini …"`,
`codex → "codex …"`, `hermes → "hermes"`) or the turn-completion signal never
fires and the agent wedges.

**Tune the ACL for a "no human in the loop" variant.** If you want the packet to
land in a shared wiki or task tracker instead of your `user` mailbox, you can add
a `publisher` agent that the chief talks to in place of (or in addition to) `user`,
and point that agent's `role` at writing the file. The ACL stays a star through the
chief:

```
chief ──▶ transcriber / summarizer / actionizer ──▶ chief ──▶ publisher
```

**Batch many meetings.** Send more than one transcript; the chief handles them
one at a time (the inbox is the queue — the orchestrator releases exactly one
message at a time), so you won't get two packets interleaved.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and the routing rules.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resume conversations.
- [`./delegation-pipeline.md`](./delegation-pipeline.md) — the user → hub →
  workers → hub → user pattern this swarm is built on.
- [`./multi-llm-swarm.md`](./multi-llm-swarm.md) — running mixed-model teams.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

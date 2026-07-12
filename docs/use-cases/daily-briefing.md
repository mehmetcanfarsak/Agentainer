# Use case: the daily briefing swarm

A concrete, end-to-end walkthrough of the shipped `examples/daily-briefing.yaml`
swarm — a small team that assembles a **morning digest** for you from multiple
sources. A **chief** hub takes your topics/interests, delegates *gathering* to a
**newsgatherer**, *condensing* to a **summarizer**, and *formatting* to a
**writer**, then delivers the finished Markdown digest to you. It's the canonical
"one brain coordinates several specialists" loop, wired entirely through
Agentainer's file-based mail model — and it can self-trigger a daily refresh.

Everything below is based on the actual contents of
`examples/daily-briefing.yaml` and the shipped CLI (`lib/cli.py`) and mailroom
(`lib/mail.py`). No API keys are needed to understand the mechanics; to run it
*for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The problem: why a swarm beats one agent for a digest

A daily briefing looks trivial, but a single agent doing it all tends to produce
one of two failures:

- **Shallow gathering.** One agent asked to "brief me on AI news and my calendar"
  will often skim the first few results and pad the rest, because *gathering*,
  *condensing*, and *writing* are three different skills competing for the same
  context window. The gathering gets short-changed.
- **Inconsistent shape.** Without a dedicated formatter, every digest looks
  different, so the human can't skim it the same way each morning.

The swarm splits the job by **competence**, not by prompt-engineering tricks:

1. **`chief`** holds the *standing brief* (your topics/interests) and the
   assembly logic — the one place that knows what "the digest" is supposed to be.
2. **`newsgatherer`** does nothing but **research with its own tools/web**, and
   returns well-sourced raw items.
3. **`summarizer`** does nothing but **compress** each item to 2-3 bullets,
   keeping the source/link.
4. **`writer`** does nothing but **format** clean, skimmable Markdown.

Each agent has a tiny, unambiguous job, so each does it well — and the human only
ever talks to `chief`. That single-point-of-contact rule also means the digest is
*assembled in one place*: no two agents independently deciding what "the digest"
looks like.

---

## 2. The topology

```
         newsgatherer
              |
         summarizer --- chief --- writer
                                |
                              user
```

Four agents, one directed flow:

1. **`user` → `chief`** — you send your topics/interests ("AI news and my calendar").
2. **`chief` → `newsgatherer`** — the chief delegates the gathering with the exact topics.
3. **`newsgatherer` → `chief`** — returns raw, sourced items.
4. **`chief` → `summarizer`** — delegates condensing.
5. **`summarizer` → `chief`** — returns 2-3 bullet items with sources.
6. **`chief` → `writer`** — delegates assembly.
7. **`writer` → `chief`** — returns the formatted Markdown.
8. **`chief` → `user`** — delivers the digest.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. The three spokes (`newsgatherer`, `summarizer`, `writer`)
can only deliver to `chief`; only `chief` may deliver to `user`. Anything else is
bounced back as a `system` message and filed in `failed/` (see §7).

---

## 3. The config, explained

Here is `examples/daily-briefing.yaml` in full (header trimmed for brevity):

```yaml
swarm:
  name: briefing
  root: ./briefing-workspace

defaults:
  capture: none              # claude/codex are auto-upgraded to their hook at up
  can_talk_to: []

agents:
  - name: chief
    type: claude
    can_talk_to: [newsgatherer, summarizer, writer, user]
    command: "claude --dangerously-skip-permissions"
    periodically_ping_seconds: 86400
    periodically_ping_message: |
      It is time for your scheduled daily briefing refresh. If you have no other
      pending task, gather fresh items on the standing topics (your last
      instructions from the user) and rebuild the digest, then deliver it to
      user. If you are already mid-task, ignore this and continue.
    role: |
      You are the CHIEF of a personal daily-briefing service. ...
      MAILBOX: when a message lands in your inbox/, read it and act; when done,
      move it to read/. To send, write a file into outbox/<name>/ ...

  - name: newsgatherer
    type: claude
    can_talk_to: [chief]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the NEWSGATHERER. ... Treat this as research: use your own tools
      and the web to find current items ...
      MAILBOX: ... write a file into outbox/chief/ ...

  - name: summarizer
    type: claude
    can_talk_to: [chief]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the SUMMARIZER. ... condense each into 2-3 tight bullets ...
      MAILBOX: ... write a file into outbox/chief/ ...

  - name: writer
    type: codex
    can_talk_to: [chief]
    command: "codex --yolo"
    role: |
      You are the WRITER. ... assemble a clean, readable Markdown morning digest.
      MAILBOX: ... write a file into outbox/chief/ ...
```

Field by field:

### `swarm`
- **`name: briefing`** — the swarm's name (shows up in `status`, logs, sessions).
- **`root: ./briefing-workspace`** — the parent directory for the agents' working
  directories and mailboxes. Each agent gets `briefing-workspace/<name>/` as its
  workdir (created on `up`), and its mailbox folders live alongside. Orchestrator
  state goes under `briefing-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, and it's ultimately keyed off each
  agent's `type`. For `claude` and `codex`, whose CLIs support a completion
  **hook**, setting `capture: none` is a footgun — so the config loader *upgrades*
  it back to `hook` and prints a warning at `up`. Net effect here: all four agents
  use their natural hook (`claude` → Stop hook, `codex` → `notify` program).
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `chief` (type: `claude`)
- **`can_talk_to: [newsgatherer, summarizer, writer, user]`** — the chief is the
  hub: it can delegate to every spoke, and it is the **only agent that can talk to
  `user`**. That last part matters — keep the human-facing surface to a single
  agent (see Tips).
- **`periodically_ping_seconds: 86400`** — a *self-trigger*. Every 24h of idle
  time the orchestrator injects the `periodically_ping_message` into the chief's
  queue as a `system` message, so the chief can rebuild the digest without you
  doing anything. It is **idle-only** (skipped while the chief is busy), and
  **no-pile-up** (skipped if an unhandled ping is already queued, and only fires
  if at least 86400s have elapsed since the last ping) — so the digest refreshes
  on a cadence without ever stacking up reminders. The chief decides what to do
  with it (rebuild, or ignore if mid-task).
- **`periodically_ping_message`** — the text the chief receives on each ping. It
  tells the chief to use its *standing topics* (the topics you last sent) and
  rebuild the digest, but to ignore the ping if it's already working.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the chief waits for your topics instead of spamming
  spokes. It includes the **MAILBOX** reminder: read `inbox/`, act, move to
  `read/`, and send by writing into `outbox/<name>/` (reading `about.md` first).

### `newsgatherer` (type: `claude`)
- **`can_talk_to: [chief]`** — can only report back to the chief.
- **`role`** — "Treat this as research: use your own tools and the web to find
  current items." It explicitly gathers *raw, sourced* items and hands them to the
  chief — it does not summarize. The role reminds it to write into
  `outbox/chief/`.

### `summarizer` (type: `claude`)
- **`can_talk_to: [chief]`** — can only report back to the chief.
- **`role`** — "condense each into 2-3 tight bullets … preserve the source/link."
  It compresses, it does not editorialize, and it returns to the chief.

### `writer` (type: `codex`)
- **`can_talk_to: [chief]`** — can only report back to the chief.
- **`command: "codex --yolo"`** — placeholder launch command for Codex.
- **`role`** — "assemble a clean, readable Markdown morning digest … no preamble,
  no sign-off." The only agent responsible for presentation.

### What's *not* in this config
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4). This keeps the digest from being "delivered into the void" before you
  are ready to read it.
- **No shared workdirs.** Each agent gets its own `briefing-workspace/<name>/`,
  so their files can't collide.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/daily-briefing.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for all four agents).
2. Creates the runtime dirs (`briefing-workspace/.agentainer/…`: log, queue, run,
   sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: the chief gets
   `outbox/newsgatherer/`, `outbox/summarizer/`, `outbox/writer/`,
   `outbox/user/`; each spoke gets `outbox/chief/`.
4. **Installs per-type turn detection** — the Claude Stop hook for the three
   `claude` agents and the Codex `notify` hook for `writer`.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'briefing' is up with 4 agent(s)
:: attach with:  tmux attach -t <chief-session>
:: you can use the UI with:  agentainer serve --host 127.0.0.1 -c examples/daily-briefing.yaml --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only bind — the UI **defaults to `127.0.0.1`**, never `0.0.0.0`, per the
control-plane safety rule. See the `README.md` "control-plane UI" section.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole pipeline route mail with no API keys — the mechanics are identical.

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the chief's digest as mail (rather than have it
held), turn yourself available first:

```bash
./agentainer user available -c examples/daily-briefing.yaml
```

This rewrites the `user` contact card in the chief's `outbox/user/about.md` to
`Status: available`, so the chief sees you're reachable. (While away, mail to you
is *held* and the sender gets a `system` ack — nothing bounces.)

Now send your topics into the swarm, addressed to the chief:

```bash
./agentainer send --to chief "Brief me on AI news and my calendar."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the chief, then — because the
inbox was empty — **released into `inbox/`** and the chief is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **chief receives the topics.** It reads `inbox/`, restates them as a brief, and
   writes a delegation into `outbox/newsgatherer/`. On stop, that routes to the
   newsgatherer and nudges it.
2. **newsgatherer gathers.** It reads its inbox, does the research with its own
   tools/web, writes sourced raw items into `outbox/chief/`. On stop, that routes
   back to the chief.
3. **chief delegates condensing.** It writes a delegation into
   `outbox/summarizer/`. On stop, that routes to and nudges the summarizer.
4. **summarizer condenses.** It writes 2-3 bullet items (with sources) into
   `outbox/chief/`. On stop, back to the chief.
5. **chief delegates assembly.** It writes a delegation into `outbox/writer/`. On
   stop, that routes to and nudges the writer.
6. **writer formats.** It writes the Markdown digest into `outbox/chief/`. On
   stop, back to the chief.
7. **chief delivers.** It writes the digest into `outbox/user/`. On stop, that's
   delivered to your `user` mailbox (you'll see it with `agentainer user inbox`,
   or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send topics, the agents just sit in standby (that's the point of
> the standby prompt). But because `chief` has `periodically_ping_seconds: 86400`,
> after 24h of idle it will be nudged to rebuild the digest on its own standing
> topics — handy for a hands-off morning routine.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/daily-briefing.yaml
```

```
swarm: briefing   root: ./briefing-workspace
  chief (claude) up idle queue=0 unread=0 talks=newsgatherer, summarizer, writer, user
  newsgatherer (claude) up idle queue=0 unread=1 talks=chief
  summarizer (claude) up idle queue=0 unread=0 talks=chief
  writer (codex) up idle queue=0 unread=0 talks=chief
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/daily-briefing.yaml          # whole swarm, last 20
./agentainer logs -c examples/daily-briefing.yaml -f        # follow live
./agentainer logs chief -c examples/daily-briefing.yaml    # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
`ping`, etc. — one JSONL line per event. (The `ping` event is what the
`periodically_ping_seconds` self-trigger produces.)

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox chief -c examples/daily-briefing.yaml
```

Prints the one released message (headers + body), or `chief: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue chief -c examples/daily-briefing.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach chief -c examples/daily-briefing.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Resume after a stop

Tear the swarm down when you're done:

```bash
./agentainer down -c examples/daily-briefing.yaml
```

Bring it back later and **conversations resume by default**:

```bash
./agentainer up -c examples/daily-briefing.yaml     # resume is the default
```

On `up`, Agentainer reads `briefing-workspace/.agentainer/sessions.yaml` (written
as each agent finished its first turn) and reattaches the recorded conversations
via each type's native resume: `claude --resume <id>` for the three `claude`
agents, `codex resume <id>` for `writer`. A resumed agent is *not* re-sent the
standby prompt (its prior context — including your standing topics — is restored).
This is exactly why the self-trigger works: the chief remembers the topics across
restarts.

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/daily-briefing.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md) and
the reboot walkthrough in
[`use-cases/resume-after-reboot.md`](./resume-after-reboot.md).

---

## 8. Tips & footguns

- **Keep `chief` the only `user`-facing agent.** Only `chief` lists `user` in
  `can_talk_to`. That gives you a single point of contact and a clean funnel: raw
  items always pass through gathering → summarizing → writing before they reach
  you. If a spoke tries to mail `user` directly, the orchestrator bounces it (ACL)
  and drops a `system` note in the spoke's inbox explaining who it *can* message —
  the model self-corrects in-band.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually fires**
  — a `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't
  launch Claude) means completion never triggers and the agent pins "busy" forever.
  `status` showing an agent `busy` for a long time with `unread` mail is the tell.

- **The daily ping is idle-only and non-piling.** `periodically_ping_seconds`
  only injects a `system` ping while the chief is idle, skips if a prior ping is
  still unhandled, and only fires if the full interval has elapsed. So you get a
  once-a-day refresh, not a pile of "refresh!" reminders. Don't set it too low
  (e.g. 60s) unless you want an aggressive re-nudge loop.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **Availability shapes the ending.** If `user` is **away** when the chief
  finishes, your digest is *held* (with a `system` "the user is away" ack to the
  chief) rather than lost — read it later with `agentainer user inbox` or flip
  yourself available and it's delivered.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down            -c examples/daily-briefing.yaml
  ./agentainer remove-session  -c examples/daily-briefing.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

---

## 9. Customize

The four-agent shape is a starting point. Common variations:

- **Add a `calendar` gatherer.** Drop in a fourth spoke that reads your calendar
  (e.g. via its own tooling) and reports free/busy + upcoming events to `chief`:
  ```yaml
  - name: calendar
    type: claude
    can_talk_to: [chief]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the CALENDAR agent. Report the human's upcoming events for today
      and tomorrow (title, time, location/link) and any conflicts. Hand the list
      to the chief.
  ```
  Then add `calendar` to `chief`'s `can_talk_to` so the chief can delegate to it.

- **Add a `finance` gatherer.** Same pattern for market moves / portfolio /
  expense alerts — another `can_talk_to: [chief]` spoke the chief can pull in.

- **Swap models.** Every agent is independently typed: make `summarizer` a
  `gemini` agent, or `writer` a `claude` agent, by changing `type` and its
  `command` *together* (a `type`/`command` mismatch wedges the agent — see Tips).
  This is the whole point of a [`multi-llm-swarm.md`](./multi-llm-swarm.md): pick
  the right tool per job.

- **Tune the ACL.** Want `newsgatherer` to hand items straight to `summarizer`
  (skipping a chief hop)? Add `summarizer` to its `can_talk_to` — but remember the
  chief is still the only `user` contact, so the final digest still flows through
  `chief`. Tighter is usually better for a digest; wider only if a spoke genuinely
  needs it.

- **Set the ping cadence.** `periodically_ping_seconds: 86400` is daily. For a
  weekday-morning feel you might run the swarm only on weekdays via cron and drop
  the ping, or set `43200` (every 12h) if you want a midday refresh too. Keep it
  idle-only and you'll never get pile-up.

- **Change what "the digest" is.** Everything the chief knows about your interests
  lives in the chief's `role` plus the topics you last `send`. Edit the chief's
  `role` to pin standing topics ("always include AI news, weather, and my top
  calendar item") so even the self-triggered refresh stays on-target.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resume across restarts.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — the chief→spoke→chief loop pattern.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing claude/codex/gemini/hermes per job.
- `examples/daily-briefing.yaml` — the config this walkthrough is built from.

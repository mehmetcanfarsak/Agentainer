# Use case: the podcast production swarm

A concrete, end-to-end walkthrough of the shipped `examples/podcast-production.yaml`
swarm — a five-agent production line where a **host** takes an episode topic from
you, a **researcher** gathers the facts and angles, a **scriptwriter** turns them
into a spoken script, **shownotes** builds the episode page, and **promo** cuts the
social clips. The host assembles all of it and hands the finished package back to
you. It's the canonical "one producer, a pipeline of specialists" loop, wired
entirely through Agentainer's file-based mail model.

If you make a show — solo, interview, or a marketing team spinning up a branded
podcast — this is the shape of the work: one topic in, a publishable package out,
with each craft handled by an agent that only knows its own job. No API keys are
needed to understand the mechanics; to run it *for real* you supply the coding-CLI
commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then [`mail-model.md`](../mail-model.md). The one-line version: an agent
> **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The topology

```
        episode topic
  user ───────────────▶ host ──────────────▶ researcher
        (package)   ◀────┐ │ │ \                  │
                         │ │ │  \                  │ research memo
                         │ │ │   \                 ▼
                         │ │ │    └────────▶ scriptwriter
                         │ │ │                     │
                         │ │ └──── shownotes ◀─────┘ (final script)
                         │ └────── promo    ◀─────┘ (final script)
                         └──── assembled package
```

Five agents, one directed flow with the host at the hub:

1. **`user` → `host`** — you send the episode topic (angle, length, guest).
2. **`host` → `researcher`** — the host writes an episode brief and delegates the
   research.
3. **`researcher` → `host`** (and → `scriptwriter`) — facts, angles, and guest
   questions come back; the researcher can also answer the scriptwriter's direct
   follow-ups.
4. **`host` → `scriptwriter`** — the host forwards the brief + research; the
   scriptwriter writes the script and returns it.
5. **`host` → `shownotes`** and **`host` → `promo`** — once the script is
   approved, both work from the *final* cut in parallel.
6. **`host` → `user`** — the host assembles script + show notes + promo into one
   package and delivers it to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. An agent can only deliver to names on its own list; anything
else is bounced back as a `system` message and filed in `failed/` (see §7).

---

## 2. The config, explained

Here is `examples/podcast-production.yaml`, agent by agent (the file itself has the
full `role` text and the ASCII map in comments):

```yaml
swarm:
  name: podcast
  root: ./podcast-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: host          # type: claude — can_talk_to: [researcher, scriptwriter, shownotes, promo, user]
  - name: researcher    # type: claude — can_talk_to: [host, scriptwriter]
  - name: scriptwriter  # type: claude — can_talk_to: [host, shownotes]
  - name: shownotes     # type: claude — can_talk_to: [host]
  - name: promo         # type: claude — can_talk_to: [host]
```

### `swarm`
- **`name: podcast`** — the swarm's name (shows up in `status`, logs, sessions).
- **`root: ./podcast-workspace`** — the parent directory for the agents' working
  directories and mailboxes. Each agent gets `podcast-workspace/<name>/` as its
  workdir (created on `up`), with its five mailbox folders alongside. Orchestrator
  state goes under `podcast-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless overridden.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, and it's keyed off each agent's `type`.
  Every agent here is `claude`, whose CLI supports a completion **hook**, so
  `capture: none` is a footgun — the config loader *upgrades* it back to `hook`
  and prints a warning at `up`. Net effect: all five agents use their Stop hook.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent states
  its own list explicitly, so this default is just a safe floor.

### `host` (type: `claude`)
- **`can_talk_to: [researcher, scriptwriter, shownotes, promo, user]`** — the host
  is the hub: it delegates to all four specialists and is the **only agent that can
  talk to `user`**. That single human-facing surface is deliberate (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity: producer who restates the topic as a brief,
  sequences the pipeline, and assembles the package. On `up` this becomes the
  agent's first prompt, wrapped in a **standby notice** ("no task yet — don't send
  anything, you'll be notified"), so the host waits for your topic instead of
  proactively mailing peers. It also carries the **MAILBOX** reminder (read
  inbox/, act, move to read/, write to outbox/<name>/ to send).
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `researcher` (type: `claude`)
- **`can_talk_to: [host, scriptwriter]`** — reports back to the host, and can
  answer the scriptwriter's direct factual follow-ups along the production line. It
  **cannot** reach `user` — its work always flows through the host.
- **`role`** — produce a research memo: key points, the counterintuitive angle,
  sourced facts, and ordered guest questions; flag what it could not verify.

### `scriptwriter` (type: `claude`)
- **`can_talk_to: [host, shownotes]`** — returns the script to the host and shares
  the *final* script with shownotes so timestamps match the real cut.
- **`role`** — write for the ear: cold-open hook, segment outline to length,
  spoken script, guest cues, CTA close, and rough segment timings.

### `shownotes` (type: `claude`)
- **`can_talk_to: [host]`** — reports only to the host.
- **`role`** — build the episode page: summary, chapter timestamps, honest
  resource links, guest bio.

### `promo` (type: `claude`)
- **`can_talk_to: [host]`** — reports only to the host.
- **`role`** — cut social promotion: pull-quote clips with timecodes, title +
  teaser, per-platform captions, hashtags.

### What's *not* in this config
- **No `periodically_ping_seconds`.** No agent is auto-nudged on a timer while
  idle — the pipeline is purely event-driven off real mail. (If you wanted the
  host to poke a slow researcher, you'd add `periodically_ping_seconds: 300`.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/podcast-production.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for all five agents).
2. Creates the runtime dirs (`podcast-workspace/.agentainer/…`: log, queue, run,
   sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: the host gets
   `outbox/researcher/`, `outbox/scriptwriter/`, `outbox/shownotes/`,
   `outbox/promo/`, `outbox/user/`; the researcher gets `outbox/host/`,
   `outbox/scriptwriter/`; and so on.
4. **Installs per-type turn detection** — the Claude Stop hook for every agent.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints. The `serve` line gives you
the mail-app control-plane UI (threads, live panes, send-as-user, availability
toggle). By default it binds **`127.0.0.1`** only (loopback) — you must opt into a
remote bind with `--host` and a `--token`. See the `README.md` "control-plane UI"
section and [`remote-access.md`](./remote-access.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole pipeline route mail with no API keys — the mechanics are identical.

---

## 4. Drive an episode

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. To *receive* the host's finished package as mail (rather than have it
held), turn yourself available first:

```bash
./agentainer user available -c examples/podcast-production.yaml
```

This rewrites the `user` contact card in the host's `outbox/user/about.md` to
`Status: available`. (While away, mail to you is *held* and the sender gets a
`system` ack — nothing bounces.)

Now send the topic into the swarm, addressed to the host:

```bash
./agentainer send --to host "Episode: how open-source funding actually works. 35 min, guest = a maintainer. Tone: curious, not preachy."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the host, then — because the
inbox was empty — **released into `inbox/`** and the host is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§5), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **host receives the topic.** It reads `inbox/`, writes a one-paragraph episode
   brief into `outbox/researcher/`. On stop, that routes to the researcher.
2. **researcher investigates.** Reads the brief, writes the research memo into
   `outbox/host/`. On stop, back to the host.
3. **host briefs the writer.** Forwards brief + memo into `outbox/scriptwriter/`.
4. **scriptwriter drafts.** Writes the script into `outbox/host/`; the host
   approves, then the script is shared with shownotes and promo.
5. **shownotes + promo work in parallel** off the final script, each returning to
   `outbox/host/`.
6. **host assembles + delivers.** It combines script + show notes + promo into one
   package written to `outbox/user/`. On stop, that's delivered to your `user`
   mailbox (see it with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a topic, the agents just sit in standby. The pipeline only
> moves when real mail arrives — this swarm has no periodic pings to self-start it.

---

## 5. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/podcast-production.yaml
```

```
swarm: podcast   root: ./podcast-workspace
  host (claude) up idle queue=0 unread=0 talks=researcher, scriptwriter, shownotes, promo, user
  researcher (claude) up idle queue=0 unread=1 talks=host, scriptwriter
  scriptwriter (claude) up idle queue=0 unread=0 talks=host, shownotes
  shownotes (claude) up idle queue=0 unread=0 talks=host
  promo (claude) up idle queue=0 unread=0 talks=host
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/podcast-production.yaml            # whole swarm, last 20
./agentainer logs -c examples/podcast-production.yaml -f         # follow live
./agentainer logs scriptwriter -c examples/podcast-production.yaml   # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox researcher -c examples/podcast-production.yaml
```

Prints the one released message (headers + body), or `researcher: inbox is empty`.

**Queue depth** — mail waiting behind the one released message (useful once the
host fans out to shownotes and promo):

```bash
./agentainer queue host -c examples/podcast-production.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach scriptwriter -c examples/podcast-production.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 6. Resume after a stop

Tear the swarm down when you're done:

```bash
./agentainer down -c examples/podcast-production.yaml
```

Bring it back later and **conversations resume by default**:

```bash
./agentainer up -c examples/podcast-production.yaml     # resume is the default
```

On `up`, Agentainer reads `podcast-workspace/.agentainer/sessions.yaml` (written
as each agent finished its first turn) and reattaches the recorded conversations
via `claude --resume <id>` for each agent — handy across a multi-day production
where the research lands one day and the script the next. A resumed agent is *not*
re-sent the standby prompt (its prior context is restored). Pass `--no-resume` to
force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/podcast-production.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md) and
the reboot walkthrough in
[`use-cases/resume-after-reboot.md`](./resume-after-reboot.md).

---

## 7. Iterate on an episode

Podcasts are rarely one-and-done — you'll want another pass. Because the host is
your single point of contact, iteration is just more mail to the host:

```bash
./agentainer send --to host "The cold open is flat — punch it up, and cut segment 3 to keep us under 35 min."
```

The host reads it, decides who needs to redo what (usually the scriptwriter, then
a fresh pass from shownotes and promo off the new cut), and re-runs just that leg
of the pipeline. Each specialist keeps its conversation context (resume), so
"tighten the questions" lands on a researcher that already knows the episode.

---

## 8. Customize

- **Add a `transcriber`.** Drop in a sixth agent that turns the final script (or a
  recorded audio transcript you paste into its inbox) into a clean, speaker-labeled
  transcript for accessibility and SEO:
  ```yaml
    - name: transcriber
      type: claude
      can_talk_to: [host]
      command: "claude --dangerously-skip-permissions"
      role: |
        You are the TRANSCRIBER. From the final script or a raw transcript,
        produce a clean, speaker-labeled, timestamped transcript with light
        copy-editing (remove filler, fix obvious mis-hearings) but no
        paraphrasing. Send it back to the host.
  ```
  Then add `transcriber` to the host's `can_talk_to`, and re-run `up`.

- **Swap models per role (multi-LLM).** Every agent here is `claude`, but the mail
  model is CLI-agnostic. Point research at a model with strong web tools, keep the
  script on your best prose model, and run promo cheap:
  ```yaml
    - name: researcher
      type: gemini
      capture: pane           # gemini has no completion hook — poll the pane
      command: "gemini --yolo"
    - name: promo
      type: codex
      command: "codex --yolo"
  ```
  Mind the turn-detection footgun: `type` and `command` must launch the *same* CLI,
  and `gemini`/`hermes` need `capture: pane`. See
  [`multi-llm-swarm.md`](./multi-llm-swarm.md) for the mechanics.

- **Tune the ACL.** The default is a strict hub-and-spoke. Loosen or tighten it to
  taste:
  - Let shownotes ask the scriptwriter directly (add `scriptwriter` to shownotes'
    `can_talk_to` and vice-versa) so timestamp questions skip the host.
  - Keep `user` on the host **only** — resist giving promo a direct line to you, or
    you lose the single-funnel review.
  Anything not on an agent's list bounces back as a `system` note explaining who it
  *can* reach, so a tightened ACL is self-documenting.

---

## 9. Tips & footguns

- **Keep the host the only `user`-facing agent.** Only the host lists `user` in
  `can_talk_to`. That gives you one point of contact and a clean funnel: raw
  research and unpolished clips always pass through the producer before they reach
  you. If a specialist tries to mail `user` directly, the orchestrator bounces it
  (ACL) and drops a `system` note in its inbox explaining who it *can* message —
  the model self-corrects in-band.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually fires**
  — a `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't
  launch Claude) means completion never triggers and the agent pins "busy" forever.
  `status` showing an agent `busy` for a long time with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  the max number of times without being handled is auto-archived so the queue
  advances. There's also a per-pair runaway cap to kill "thanks!/you're welcome!"
  loops between two agents.

- **Availability shapes the ending.** If `user` is **away** when the host finishes,
  your finished package is *held* (with a `system` "the user is away" ack to the
  host) rather than lost — read it later with `agentainer user inbox` or flip
  yourself available and it's delivered.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resume across a multi-day
  production.
- [`use-cases/delegation-pipeline.md`](./delegation-pipeline.md) — the hub-and-
  spoke delegation pattern this swarm is built on.
- [`use-cases/multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing claude/codex/
  gemini/hermes in one swarm.

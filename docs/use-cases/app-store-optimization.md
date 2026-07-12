# Use case: the app store optimization (ASO) swarm

A concrete, end-to-end walkthrough of the shipped `examples/app-store-optimization.yaml`
swarm — a four-agent pipeline that turns an app pitch into a ready-to-paste
**App Store (iOS)** and **Play Store (Android)** listing. A **keyword researcher**
builds the keyword strategy, then hands vetted terms to three specialists: a
**metadata writer** (title, subtitle, keyword field), a **screenshot copywriter**,
and a **description writer**. It's the canonical ASO workflow — *research keywords
→ write the store surfaces that rank and convert* — wired entirely through
Agentainer's file-based mail model.

If you've ever searched "**app store keywords**", "**ASO tools**", "**how to
write an App Store title**", or "**increase app downloads**", this is that job
done by a coordinated set of agents instead of a spreadsheet and four browser
tabs. Everything below is based on the actual contents of
`examples/app-store-optimization.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Why this makes a great swarm

ASO is naturally a **fan-out from one source of truth**. Every store surface —
the 30-character title, the keyword field, the screenshot captions, the long
description — has to stay anchored to *the same* keyword set, or you get a listing
that ranks for one thing and converts for another. That's exactly what a
hub-and-spoke mail topology enforces:

- **One keyword strategy, three consumers.** The researcher writes `KEYWORDS.md`
  once; each writer builds a different surface from it and reports coverage back.
- **Each surface has different, unforgiving rules.** iOS name ≤30 chars, subtitle
  ≤30, keyword field ≤100 chars comma-separated; screenshot copy must land in ~6
  words; the description's first two lines are the only text shown before "more".
  Splitting the work lets each agent specialize in one rulebook.
- **Coverage is checkable.** Because writers report which keywords they used, the
  researcher can bounce a draft that dropped a priority term — a real
  quality gate, not a vibe.

---

## 2. The topology

```
                       user
                         │  "optimize the listing for <app>"
                         ▼
              keyword_researcher   (the hub — owns KEYWORDS.md)
                 /       │       \
                ▼        ▼        ▼
        metadata   screenshot   description
         _writer   _copywriter   _writer
        (title/    (caption/    (long store
         subtitle/  overlay      description)
         keywords)  copy)
                 \       │       /
                  └── report coverage ──▶ keyword_researcher ──▶ user
```

Four agents, one directed flow:

1. **`user` → `keyword_researcher`** — you send the app, audience, and category.
2. **`keyword_researcher` → each writer** — the researcher builds the ranked
   keyword set and delegates a tailored brief to the metadata writer, screenshot
   copywriter, and description writer.
3. **each writer → `keyword_researcher`** — every writer reports its draft (and
   which keywords it covered) back to the researcher, never to each other.
4. **`keyword_researcher` → `user`** — the researcher checks coverage, requests
   revisions where a priority term is missing, then returns the assembled listing
   to you.

The routing isn't a suggestion — it's *enforced* by each agent's `can_talk_to`
list. The writers can only deliver to the researcher; anything else is bounced
back as a `system` message and filed in `failed/` (see the research swarm's §7 for
the bounce mechanics, which are identical here).

---

## 3. The config, explained

Here is the shape of `examples/app-store-optimization.yaml` (see the file for the
full `role:` blocks):

```yaml
swarm:
  name: aso
  root: ./aso-workspace

defaults:
  capture: none            # tightened per agent below
  can_talk_to: []          # deny-by-default ACL; each agent opts in explicitly

agents:
  - name: keyword_researcher
    type: claude
    can_talk_to: [metadata_writer, screenshot_copywriter, description_writer, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the KEYWORD RESEARCHER and the hub of an ASO team. ...

  - name: metadata_writer
    type: claude
    can_talk_to: [keyword_researcher]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the STORE METADATA WRITER. ... iOS name (<=30), subtitle (<=30),
      keyword field (<=100, comma-separated) + Play title/short description. ...

  - name: screenshot_copywriter
    type: claude
    can_talk_to: [keyword_researcher]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the SCREENSHOT COPYWRITER. First 3-5 screenshots, benefit-led. ...

  - name: description_writer
    type: claude
    can_talk_to: [keyword_researcher]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the DESCRIPTION WRITER. Strong first two lines, scannable body. ...
```

> Full file: [`examples/app-store-optimization.yaml`](../../examples/app-store-optimization.yaml)

Field by field:

### `swarm`
- **`name: aso`** — the swarm's name (shows up in `status`, logs, sessions).
- **`root: ./aso-workspace`** — the parent directory for the agents' working
  directories and mailboxes. Each agent gets `aso-workspace/<name>/` as its
  workdir (created on `up`), with its mailbox folders alongside. Orchestrator state
  goes under `aso-workspace/.agentainer/` (never commit it).

### `defaults`
- **`capture: none`** — the default turn-detection mode. **But note:** for
  `claude` agents, whose CLI supports a completion **hook**, `capture: none` is a
  footgun, so the config loader *upgrades* it back to `hook` and prints a warning
  at `up`. Net effect: all four agents use their Claude Stop hook.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent states
  its own list explicitly, so this default is a safe floor.

### `keyword_researcher` (type: `claude`)
- **`can_talk_to: [metadata_writer, screenshot_copywriter, description_writer, user]`**
  — the hub. It delegates to all three writers and is the **only agent that can
  talk to `user`**, keeping a single human-facing surface.
- **`role`** — owns the keyword strategy (`KEYWORDS.md`), tailors a brief per
  writer, and gates on keyword coverage before returning the listing.

### `metadata_writer`, `screenshot_copywriter`, `description_writer` (type: `claude`)
- **`can_talk_to: [keyword_researcher]`** — each writer reports **only** upward to
  the researcher. They can't reach each other or the `user` directly, so every
  surface flows through the coverage check.
- **`role`** — one store surface each: character-limited metadata for both stores;
  benefit-led captions for the first 3–5 screenshots; the long description (written
  for humans, keyword-woven for Play Store indexing).

### What's *not* in this config
- **No `periodically_ping_seconds`.** No agent is auto-nudged on a timer — the
  pipeline is purely event-driven off real mail.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on.

---

## 4. Run it & drive a brief

From the repo root:

```bash
./agentainer up -c examples/app-store-optimization.yaml
```

`up` loads and validates the config (printing the `capture: none → hook` upgrade
warnings), creates the runtime dirs, initializes each agent's five mailbox folders
plus an `outbox/<peer>/` for every allowed recipient, installs the Claude Stop
hook per agent, opens one tmux session per agent, delivers the standby first
prompt, and starts the liveness supervisor.

Turn yourself available so you *receive* the finished listing as mail:

```bash
./agentainer user available -c examples/app-store-optimization.yaml
```

Now send the brief into the swarm, addressed to the hub:

```bash
./agentainer send --to keyword_researcher \
  "Optimize the listing for our habit-tracking app 'Streaky'. Audience: iOS + Android, US English, students & young professionals."
```

### The mail flowing

Each hop is a `stop → sweep → route → release → nudge` cycle:

1. **keyword_researcher receives the brief.** It reads `inbox/`, builds the ranked
   keyword set in `KEYWORDS.md`, and writes a tailored brief into
   `outbox/metadata_writer/`, `outbox/screenshot_copywriter/`, and
   `outbox/description_writer/`. On stop, the orchestrator sweeps the outbox, routes
   each message, and nudges the three writers.
2. **the writers work in parallel.** The metadata writer produces `METADATA.md`
   (title/subtitle/keyword field for both stores, with character counts), the
   screenshot copywriter produces `SCREENSHOTS.md`, and the description writer
   produces `DESCRIPTION.md`. Each writes its draft into
   `outbox/keyword_researcher/` and finishes its turn, routing the report back.
3. **keyword_researcher checks coverage.** It reads each report; if a priority
   term was dropped, it writes a revision request back to that writer. Once
   satisfied, it assembles the full listing and writes it into `outbox/user/`.
4. **you receive the listing.** On stop, it's delivered to your `user` mailbox
   (`agentainer user inbox`, or the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

---

## 5. Observe

```bash
./agentainer status -c examples/app-store-optimization.yaml   # who's up, queue, unread, ACL
./agentainer logs   -c examples/app-store-optimization.yaml -f # durable JSONL event log, live
./agentainer inbox  metadata_writer -c examples/app-store-optimization.yaml
./agentainer attach description_writer -c examples/app-store-optimization.yaml
```

The durable log is the source of truth for history (tmux keeps no scrollback) —
you'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
one JSONL line per event.

The mail-app **control-plane UI** (threads, live panes, send-as-user,
availability toggle) is one command:

```bash
./agentainer serve -c examples/app-store-optimization.yaml
```

By default it binds **`127.0.0.1`** (loopback only) — safe. Only add
`--host 0.0.0.0` with a `--token` if you deliberately need remote access; the UI
is a control plane that can start processes and type into agents.

---

## 6. Search intent this swarm serves

- **"How do I write an App Store title / subtitle that ranks?"** — the
  metadata_writer front-loads the highest-intent keyword within the 30-char limit
  and never repeats a term across name, subtitle, and the keyword field.
- **"What goes in the iOS keyword field?"** — a comma-separated, ≤100-char list
  with no wasted spaces and no words already used in the visible fields.
- **"How do I pick app store keywords?"** — the keyword_researcher's ranked set of
  head terms, long-tail phrases, and competitor terms, each scored for relevance
  and difficulty.
- **"What should my app screenshots say?"** — benefit-led captions for the first
  3–5 panels, with the first caption landing the core value in isolation (it shows
  in search results).
- **"How do I write an app description that converts?"** — a strong first two
  lines (the only pre-"more" text), a scannable feature list, and keywords woven in
  naturally for Play Store indexing.

---

## 7. Tips & footguns

- **Keep the researcher the only `user`-facing agent.** Only the keyword_researcher
  lists `user` in `can_talk_to`, giving you a single point of contact and a clean
  funnel: every surface passes the coverage check before it reaches you. If a
  writer tries to mail `user` directly, the orchestrator bounces it (ACL) and drops
  a `system` note explaining who it *can* message — the model self-corrects in-band.

- **Character limits are the whole game — verify them.** The metadata_writer states
  a character count next to each field, but treat that as a claim to check: paste
  the fields into App Store Connect / Play Console, which enforce the real limits.

- **Watch the stop → nudge loop.** The clock runs on turn completion: an agent
  stops, its outbox is swept, mail is routed, recipients are nudged. If an agent
  seems stuck, confirm its turn detection fires — a `type`/`command` mismatch means
  completion never triggers and the agent pins "busy" forever.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: `read/` moves are best-effort receipts, a message shown too many times is
  auto-archived so the queue advances, and a per-pair runaway cap kills
  "thanks!/you're welcome!" loops.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`use-cases/research-swarm.md`](./research-swarm.md) — the delegate → do → review pipeline.
- [`cli-reference.md`](../cli-reference.md) — every subcommand and flag.
- `examples/quickstart.yaml` — the key-free mock-agent starter.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

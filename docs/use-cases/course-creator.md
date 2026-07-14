# Use case: the online course creator swarm

A concrete, end-to-end walkthrough of the shipped `examples/course-creator.yaml`
swarm — a **director** hub that takes a course topic from a human, briefs four
specialist agents (an outliner, a lesson writer, a quiz maker, and a workbook
author), and assembles their pieces into a finished, shippable course. It's the
"delegate → do the work in parallel → assemble the deliverable" loop, wired
entirely through Agentainer's file-based mail model.

Everything below is based on the actual contents of
`examples/course-creator.yaml` and the shipped CLI (`lib/cli.py`) and mailroom
(`lib/mail.py`). No API keys are needed to understand the mechanics; to run it
*for real* you supply the coding-CLI commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md)
> first, then the four-folders recap in [`mail-model.md`](../mail-model.md). The
> one-line version: an agent **reads a file** to receive mail and **writes a
> file** to send it; the orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

The course-creator swarm is built for anyone who needs to **turn a topic into a
structured course without writing every piece by hand**:

- **Course creators / infopreneurs** who have the expertise but not the hours to
  draft modules, lessons, quizzes and worksheets one at a time.
- **Corporate trainers / L&D teams** who need a first-draft onboarding or
  compliance course from an internal SME brief, fast.
- **Educators** who want a skeleton + lesson content + assessments generated from
  a syllabus so they can spend their time editing, not starting from a blank page.
- **Agencies** producing multi-course catalogs where each course follows the same
  outline → lessons → quiz → exercises shape.

The point is *parallel specialist drafting with a single consistent spine*: the
director owns the brief and the outline, the four producers each own one artifact
type, and nothing reaches the human until the director has assembled and
reconciled the lot.

---

## 2. The topology

```
              user  (you, virtual mailbox)
                │  send "Build a course on <topic>"
                ▼
            ┌─────────┐
            │ director │  ◀── the hub: briefs each producer, assembles, delivers
            └─────────┘
              │   │   │   │
    ┌─────────┘   │   │   └─────────┐
    ▼             ▼   ▼             ▼
 outliner    lesson_writer    quiz_maker    workbook
 (outline)   (lesson prose)   (assessments) (exercises)
```

Five agents, one directed flow:

1. **`user` → `director`** — you send the course topic.
2. **`director` → `outliner`** — the director restates it as a brief and asks for
   the module/lesson outline.
3. **`director` → `lesson_writer` / `quiz_maker` / `workbook`** — the director fans
   the outline out to the three remaining producers so they build in parallel.
4. Each producer **→ `director`** — they return their artifact and the director
   collects all four.
5. **`director` → `user`** — the director assembles everything into a single
   `COURSE.md` and delivers the finished course to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. The producers can only reply to the director; the director is
the **only** agent allowed to talk to `user`. Anything outside that graph is
bounced back as a `system` message and filed in `failed/` (see §7).

---

## 3. The config, explained

Here is `examples/course-creator.yaml` in full:

```yaml
# 🎓 Online course creator — a director hub briefs four specialist agents.
swarm:
  name: course
  root: ./course-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: director
    type: claude
    can_talk_to: [outliner, lesson_writer, quiz_maker, workbook, user]
    command: "claude --dangerously-skip-permissions"
    role: "You are the DIRECTOR ... assemble the finished course into COURSE.md ..."
  - name: outliner
    type: claude
    can_talk_to: [director]
    command: "claude --dangerously-skip-permissions"
    role: "You are the OUTLINER. Produce the module/lesson outline in OUTLINE.md."
  - name: lesson_writer
    type: claude
    can_talk_to: [director]
    command: "claude --dangerously-skip-permissions"
    role: "You are the LESSON WRITER. Draft the prose in LESSONS.md."
  - name: quiz_maker
    type: claude
    can_talk_to: [director]
    command: "claude --dangerously-skip-permissions"
    role: "You are the QUIZ MAKER. Write per-module assessments in QUIZZES.md."
  - name: workbook
    type: claude
    can_talk_to: [director]
    command: "claude --dangerously-skip-permissions"
    role: "You are the WORKBOOK author. Write exercises in WORKBOOK.md."
```

(Every `role` is a full standing-instructions paragraph in the file; only a
condensed hint is shown above. Read the YAML for the exact wording the agents see.)

Field by field:

### `swarm`
- **`name: course`** — the swarm's name (shows up in `status`, logs, sessions).
- **`root: ./course-workspace`** — the parent directory for the agents' working
  directories and mailboxes. Each agent gets `course-workspace/<name>/` as its
  workdir (created on `up`), and its mailbox folders live alongside. Orchestrator
  state goes under `course-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, and for `claude` the CLI supports a
  completion **hook**, so setting `capture: none` on a claude agent is a footgun —
  the config loader *upgrades* it back to `hook` and prints a warning at `up`
  (`capture: none on a claude agent gives the orchestrator no way to detect turn
  completion; using the type's default: capture: hook.`). Net effect here: all
  five agents run their Stop hook.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `director` (type: `claude`)
- **`can_talk_to: [outliner, lesson_writer, quiz_maker, workbook, user]`** — the
  director is the hub: it briefs the four producers and is the **only agent that
  can talk to `user`**. That last part matters — keep the human-facing surface to
  a single agent.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the director waits for your topic instead of
  proactively mailing peers. The role tells it to assemble all four artifacts
  into `COURSE.md` and deliver to `user`.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `outliner` / `lesson_writer` / `quiz_maker` / `workbook` (all type: `claude`)
- Each lists **`can_talk_to: [director]`** — the producers are spokes. They can
  only reply to the director; they cannot reach each other or `user`. This keeps
  the outline as the single source of truth: if a lesson drifts from the
  structure, the director sends it back to the outliner rather than letting a
  producer self-restructure the course.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  commands (same CLI across all four; swap for `codex`/`gemini`/`hermes` to
  mix models — see §8).
- **`role`** — each is a tightly-scoped craft: outliner writes `OUTLINE.md`,
  lesson_writer writes `LESSONS.md`, quiz_maker writes `QUIZZES.md`, workbook
  writes `WORKBOOK.md`. Each is told to reply to the director with a summary +
  the artifact path.
- **Turn detection:** all `claude` → Stop hook.

### What's *not* in this config
- **No `pings`.** None of the five agents has a periodic ping,
  so no agent is auto-nudged on a timer while idle — the pipeline is purely
  event-driven off real mail. (If you wanted the director to poke a slow
  producer, you'd add a `pings` cron rule to it.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **No `telegram:` block.** The Telegram bridge is opt-in; add it if you want
  `user` mail mirrored to a chat (see [`../telegram-bridge.md`](../telegram-bridge.md)).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/course-creator.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for all five claude agents).
2. Creates the runtime dirs (`course-workspace/.agentainer/…`: log, queue, run,
   sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: the director gets
   `outbox/outliner/`, `outbox/lesson_writer/`, `outbox/quiz_maker/`,
   `outbox/workbook/`, `outbox/user/`; each producer gets `outbox/director/`.
4. **Installs per-type turn detection** — the Claude Stop hook for all five
   agents.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'course' is up with 5 agent(s)
:: attach with:  tmux attach -t <director-session>
:: you can use the UI with:  agentainer serve --host 127.0.0.1 -c examples/course-creator.yaml --port 8000
```

> The `serve` line gives you the mail-app control-plane UI (threads, live panes,
> send-as-user, availability toggle). For a real-key config, keep the safe
> loopback bind above — **never** `0.0.0.0` unless you also pass `--token` and
> accept the exposure. See the [`../ui-guide.md`](../ui-guide.md) control-plane
> notes.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole pipeline route mail with no API keys — the mechanics are identical.

---

## 5. Drive a course

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the director's finished course as mail (rather
than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/course-creator.yaml
```

This rewrites the `user` contact card in the director's `outbox/user/about.md` to
`Status: available`, so the director sees you're reachable. (While away, mail to
you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the topic into the swarm, addressed to the director:

```bash
./agentainer send --to director "Build a beginner course on personal finance: budgeting, saving, debt, and investing. Audience: young adults with no finance background. 4 modules."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the director, then — because the
inbox was empty — **released into `inbox/`** and the director is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **director receives the topic.** It reads `inbox/`, writes a brief to
   `outbox/outliner/`. On stop, that routes to the outliner and nudges it.
2. **outliner drafts the skeleton.** It writes `OUTLINE.md` and replies to the
   director. On stop, that routes back to the director.
3. **director fans out.** It forwards the outline to `outbox/lesson_writer/`,
   `outbox/quiz_maker/`, and `outbox/workbook/` — all three start in parallel.
4. **the three producers build.** Each reads its inbox, writes its artifact
   (`LESSONS.md` / `QUIZZES.md` / `WORKBOOK.md`), and replies to the director. As
   each finishes, its mail routes back to the director and is nudged.
5. **director assembles.** Once all four pieces are in, it merges them into
   `COURSE.md` and writes the finished course to `outbox/user/`. On stop, that's
   delivered to your `user` mailbox (see it with `agentainer user inbox`, or in
   the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a topic, the agents just sit in standby (that's the point of
> the standby prompt). The pipeline only moves when real mail arrives — this swarm
> has no periodic pings to self-start it.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/course-creator.yaml
```

```
swarm: course   root: ./course-workspace
  director (claude) up idle queue=0 unread=1 talks=outliner, lesson_writer, quiz_maker, workbook, user
  outliner (claude) up idle queue=0 unread=0 talks=director
  lesson_writer (claude) up idle queue=0 unread=0 talks=director
  quiz_maker (claude) up idle queue=0 unread=0 talks=director
  workbook (claude) up idle queue=0 unread=0 talks=director
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/course-creator.yaml          # whole swarm, last 20
./agentainer logs -c examples/course-creator.yaml -f        # follow live
./agentainer logs lesson_writer -c examples/course-creator.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox director -c examples/course-creator.yaml
```

Prints the one released message (headers + body), or `director: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue director -c examples/course-creator.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach director -c examples/course-creator.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

**Read your finished course** — once the director delivers:

```bash
./agentainer user inbox -c examples/course-creator.yaml
```

or open `course-workspace/director/COURSE.md` directly.

---

## 7. Tips & footguns

- **Keep the director the only `user`-facing agent.** In this config only the
  director lists `user` in `can_talk_to`. That gives you a single point of contact
  and a clean funnel: raw artifacts always pass through assembly before they reach
  you. If a producer tries to mail `user` directly, the orchestrator bounces it
  (ACL) and drops a `system` note in the producer's inbox explaining who it *can*
  message — the model self-corrects in-band.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually fires**
  — a `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't
  launch Claude) means completion never triggers and the agent pins "busy" forever.
  `status` showing an agent `busy` for a long time with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **The outline is the spine.** If two artifacts disagree (a quiz tests a lesson
  that doesn't exist, or the workbook references a module the outliner dropped),
  that's a director-level reconciliation job — the director is told to send the
  producer back to the outliner rather than patch it inline. Treat `OUTLINE.md`
  as the contract every other artifact is graded against.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down               -c examples/course-creator.yaml
  ./agentainer remove-session     -c examples/course-creator.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

- **Availability shapes the ending.** If `user` is **away** when the director
  finishes, your finished course is *held* (with a `system` "the user is away" ack
  to the director) rather than lost — read it later with `agentainer user inbox`
  or flip yourself available and it's delivered.

---

## 8. Customize

This swarm is a starting point. Three common variations:

- **Add a `video-script` agent.** Drop in a sixth agent that turns each lesson
  into a shooting script, and add it to the director's `can_talk_to` (and give it
  `can_talk_to: [director]`). The director then fans the outline to five producers
  instead of four and merges a `SCRIPTS.md` into `COURSE.md`:
  ```yaml
  - name: video_script
    type: claude
    can_talk_to: [director]
    command: "claude --dangerously-skip-permissions"
    role: "You are the VIDEO SCRIPT writer. Given OUTLINE.md and LESSONS.md, write a short shooting script per lesson in SCRIPTS.md ..."
  ```

- **Swap models per agent.** Change any agent's `type`/`command` to spread the
  work across LLMs — e.g. make `outliner` a `gemini` agent and the quiz maker a
  `codex` agent. Remember a `type`/`command` mismatch wedges the agent, and
  pane-captured types (`gemini`/`hermes`) have no resume bridge. See
  [`multi-llm-swarm.md`](./multi-llm-swarm.md) for the full multi-model recipe.

- **Tune the ACL.** To let the lesson_writer and quiz_maker stay consistent
  directly (skipping a director round-trip), add `quiz_maker` to the lesson_writer's
  `can_talk_to` — but keep `user` off every producer's list so the director stays
  the sole speaker for the human. Re-tighten toward the hub-and-spoke if pieces
  start to diverge.

For the deeper delegation pattern this swarm is built on, read
[`delegation-pipeline.md`](./delegation-pipeline.md).

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how mail flows.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resume after a restart.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — the hub-and-spoke pattern.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing Claude/Codex/Gemini/Hermes.
- `examples/quickstart.yaml` — the key-free mock-agent starter.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

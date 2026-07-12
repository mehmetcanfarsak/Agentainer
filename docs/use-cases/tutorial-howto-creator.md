# Use case: the tutorial / how-to creator swarm

A concrete, end-to-end walkthrough of the shipped `examples/tutorial-howto-creator.yaml`
swarm — a four-agent team that turns a plain task ("how to set up SSH keys for
GitHub") into a **publish-ready how-to guide**. A **task analyzer** breaks the
task down, a **step writer** produces the ordered step-by-step instructions, a
**screenshot / script writer** drafts the visuals brief, and a **publisher**
assembles the final Markdown. It's the canonical "analyze → write the steps →
brief the screenshots → publish" loop, wired entirely through Agentainer's
file-based mail model.

If you write software tutorials, onboarding docs, knowledge-base articles, or
"step by step guide" content — and you want an AI team that plans the guide,
writes tested steps, specifies the screenshots, and hands you clean Markdown —
this is the swarm to copy.

Everything below is based on the actual contents of
`examples/tutorial-howto-creator.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md)
> first, then the four-folders recap in the repo `README.md`. The one-line
> version: an agent **reads a file** to receive mail and **writes a file** to
> send it; the orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The topology

```
                              user
                               │ "write a how-to for X"
                               ▼
                         task_analyzer  (hub)
                        /      │       \
                       ▼       ▼        ▼
              step_writer  screenshot_   publisher
                           script_writer
```

Four agents, one hub-and-spoke flow driven by the analyzer:

1. **`user` → `task_analyzer`** — you send the task to document.
2. **`task_analyzer` → `step_writer`** — the analyzer restates it as a goal +
   starting point + "done when…" list and asks for the ordered steps.
3. **`step_writer` → `task_analyzer`** — the tested, numbered steps come back.
4. **`task_analyzer` → `screenshot_script_writer`** — the steps go out for a
   visuals brief (what to capture, where to annotate, captions/alt text).
5. **`screenshot_script_writer` → `task_analyzer`** — the shot list returns.
6. **`task_analyzer` → `publisher`** — steps + visuals brief go to the publisher.
7. **`publisher` → `task_analyzer`** — `GUIDE.md`, publish-ready Markdown.
8. **`task_analyzer` → `user`** — the finished guide comes back to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. The three specialists talk **only** to `task_analyzer`; they
never talk to each other, so the guide is assembled in one place instead of three
half-guides drifting apart. Anything an agent tries to send outside its list is
bounced back as a `system` message and filed in `failed/` (see §7).

---

## 2. The config, explained

Here is `examples/tutorial-howto-creator.yaml` in excerpt (the full file lives at
[`../../examples/tutorial-howto-creator.yaml`](../../examples/tutorial-howto-creator.yaml)):

```yaml
swarm:
  name: tutorial-howto-creator
  root: ./tutorial-howto-creator-workspace

defaults:
  capture: none              # tightened per agent; hook-types auto-upgrade at up
  can_talk_to: []            # default ACL is "talk to no one"; set per agent

agents:
  - name: task_analyzer
    type: claude
    can_talk_to: [step_writer, screenshot_script_writer, publisher, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the TASK ANALYZER, the hub of a how-to writing team. ...
      (1) restate the task as a goal + starting point + "done when..." list,
      send it to step_writer; (2) forward the steps to screenshot_script_writer;
      (3) hand steps + visuals brief to publisher; (4) return the guide to user.

  - name: step_writer
    type: claude
    can_talk_to: [task_analyzer]
    command: "claude --dangerously-skip-permissions"
    role: "You are the STEP WRITER. Produce numbered, tested step-by-step ..."

  - name: screenshot_script_writer
    type: gemini
    can_talk_to: [task_analyzer]
    capture: pane
    command: "gemini --yolo"
    role: "You are the SCREENSHOT / SCRIPT WRITER. Draft the visuals brief ..."

  - name: publisher
    type: codex
    can_talk_to: [task_analyzer]
    command: "codex --yolo"
    role: "You are the PUBLISHER. Assemble one publish-ready Markdown GUIDE.md ..."
```

Field by field:

### `swarm`
- **`name: tutorial-howto-creator`** — the swarm's name (shows up in `status`,
  logs, sessions).
- **`root: ./tutorial-howto-creator-workspace`** — the parent directory for the
  agents' working directories and mailboxes. Each agent gets
  `tutorial-howto-creator-workspace/<name>/` as its workdir (created on `up`),
  and its mailbox folders live alongside. Orchestrator state goes under
  `tutorial-howto-creator-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, and it's ultimately keyed off each
  agent's `type`. For `claude` and `codex`, whose CLIs support a completion
  **hook**, setting `capture: none` is a footgun — so the config loader *upgrades*
  it back to `hook` and prints a warning at `up`. Net effect here: task_analyzer,
  step_writer and publisher use their hook; the screenshot_script_writer
  overrides to `pane`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `task_analyzer` (type: `claude`) — the hub
- **`can_talk_to: [step_writer, screenshot_script_writer, publisher, user]`** —
  the analyzer is the hub: it delegates to all three specialists and is the
  **only agent that can talk to `user`**. Keep the human-facing surface to a
  single agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command, e.g. a
  shell alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity + the HUB MAILBOX reminder. On `up` this
  becomes the agent's first prompt, wrapped in a **standby notice** ("no task yet
  — don't send anything, you'll be notified"), so the analyzer waits for your
  task instead of proactively mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `step_writer` (type: `claude`)
- **`can_talk_to: [task_analyzer]`** — reports only to the hub. It cannot reach
  the other specialists or the `user` directly.
- **`role`** — produce the numbered, tested step-by-step instructions: exact
  commands/clicks, prerequisites up front, common pitfalls, and a verification
  step at the end.

### `screenshot_script_writer` (type: `gemini`)
- **`can_talk_to: [task_analyzer]`** — reports only to the hub.
- **`capture: pane`** — Gemini's CLI can't call a completion program, so
  Agentainer detects "turn done" by **polling the tmux pane** until it stops
  changing. (This is why it explicitly overrides the `none` default.)
- **`role`** — draft the visuals brief: what to capture per step, the region to
  highlight, the annotation, a caption/alt text, and a narration line for a
  screen recording. It describes visuals only; it never rewrites the steps.

### `publisher` (type: `codex`)
- **`can_talk_to: [task_analyzer]`** — reports only to the hub.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — assemble one publish-ready `GUIDE.md`: title, summary,
  prerequisites, numbered steps with image placeholders + alt text, verification,
  and a short troubleshooting/FAQ. It flags wrong steps rather than silently
  editing the technical content.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### What's *not* in this config
- **No `periodically_ping_seconds`.** No agent is auto-nudged on a timer while
  idle — the pipeline is purely event-driven off real mail.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/tutorial-howto-creator.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for the three hook-type agents).
2. Creates the runtime dirs (`tutorial-howto-creator-workspace/.agentainer/…`).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: the analyzer gets
   `outbox/step_writer/`, `outbox/screenshot_script_writer/`, `outbox/publisher/`,
   `outbox/user/`; each specialist gets just `outbox/task_analyzer/`.
4. **Installs per-type turn detection** — the Claude Stop hook for the analyzer
   and step_writer, the Codex `notify` hook for the publisher, and pane polling
   for the screenshot_script_writer.
5. **Opens one tmux session per agent**, `cd`'d into its workdir.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles
   stale/dead/silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints. The `serve` line gives you
the mail-app control-plane UI (threads, live panes, send-as-user, availability
toggle). It **binds `127.0.0.1` by default** — drop nothing for the safe
loopback-only bind; a remote bind is opt-in and requires a token. See the
`README.md` "control-plane UI" section.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole pipeline route mail with no API keys — the mechanics are identical.

---

## 4. Drive a task

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. To *receive* the finished guide as mail (rather than have it held),
turn yourself available first:

```bash
./agentainer user available -c examples/tutorial-howto-creator.yaml
```

Now send the task into the swarm, addressed to the analyzer:

```bash
./agentainer send --to task_analyzer \
  "Write a how-to: set up SSH keys for GitHub on macOS."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the analyzer, then — because the
inbox was empty — **released into `inbox/`** and the analyzer is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§5), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **task_analyzer receives the task.** It reads `inbox/`, restates it as a
   goal + starting point + "done when…" list, and writes a brief into
   `outbox/step_writer/`. On stop, that routes to the step_writer.
2. **step_writer writes the steps.** It produces the numbered instructions and
   sends a summary into `outbox/task_analyzer/`.
3. **task_analyzer forwards the steps** into `outbox/screenshot_script_writer/`.
4. **screenshot_script_writer drafts the visuals brief** and returns it into
   `outbox/task_analyzer/`.
5. **task_analyzer hands steps + visuals** to `outbox/publisher/`.
6. **publisher assembles `GUIDE.md`** and sends a summary with the path into
   `outbox/task_analyzer/`.
7. **task_analyzer finalizes** and writes the guide into `outbox/user/` — delivered
   to your `user` mailbox (see it with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

---

## 5. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/tutorial-howto-creator.yaml
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/tutorial-howto-creator.yaml           # whole swarm
./agentainer logs -c examples/tutorial-howto-creator.yaml -f         # follow live
./agentainer logs step_writer -c examples/tutorial-howto-creator.yaml # one agent
```

**A specific inbox / queue / live pane:**

```bash
./agentainer inbox  publisher    -c examples/tutorial-howto-creator.yaml
./agentainer queue  task_analyzer -c examples/tutorial-howto-creator.yaml
./agentainer attach step_writer  -c examples/tutorial-howto-creator.yaml
```

Detach from a pane with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses
the mailroom — handy for un-sticking an agent, but the mail model is the normal
path.)

---

## 6. Resume after a stop

Tear the swarm down when you're done, then bring it back — **conversations
resume by default**:

```bash
./agentainer down -c examples/tutorial-howto-creator.yaml
./agentainer up   -c examples/tutorial-howto-creator.yaml   # resume is the default
```

On `up`, Agentainer reattaches recorded conversations via each type's native
resume: `claude --resume <id>` for the analyzer and step_writer, `codex resume
<id>` for the publisher. The screenshot_script_writer (`gemini`) has no resume
bridge, so it starts a **fresh** conversation with a warning — its per-task role
is stateless anyway. Pass `--no-resume` to force everyone fresh. See
[`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 7. Search intent — who this swarm is for

This swarm maps directly onto the things people search for when they need
documentation help:

- **"how to write a step-by-step tutorial"** — the step_writer produces exactly
  ordered, tested steps with verification, which is the backbone of any how-to.
- **"step by step guide template / how-to format"** — the publisher's `GUIDE.md`
  (title → summary → prerequisites → numbered steps → verification → FAQ) is a
  reusable how-to template.
- **"how to add screenshots to a tutorial" / "screenshot annotation guide"** —
  the screenshot_script_writer specifies what to capture, where to annotate, and
  the caption/alt text per step.
- **"turn a task into documentation" / "AI tutorial generator"** — the analyzer
  takes a one-line task and coordinates a full guide, hands-free.
- **"knowledge base article workflow" / "onboarding doc pipeline"** — the
  hub-and-spoke keeps one authoritative assembler so articles stay consistent.

---

## 8. Tips & footguns

- **Keep the analyzer the only `user`-facing agent.** Only `task_analyzer` lists
  `user` in `can_talk_to`. That gives you a single point of contact and a clean
  funnel: raw steps and visuals always pass through the hub before they reach you.
  If a specialist tries to mail `user` directly, the orchestrator bounces it (ACL)
  and drops a `system` note in that agent's inbox explaining who it *can* message
  — the model self-corrects in-band.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion. If an
  agent seems stuck, check that its **turn detection actually fires** — a
  `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
  Claude) means completion never triggers and the agent pins "busy" forever.
  `status` showing an agent `busy` for a long time with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s).

- **Force-idle a pane-captured agent.** The screenshot_script_writer uses pane
  polling; if its capture never fires, nudge the state along:
  ```bash
  ./agentainer idle screenshot_script_writer -c examples/tutorial-howto-creator.yaml
  ```

- **`remove-session` to reset.** To wipe all Agentainer state and start every
  conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/tutorial-howto-creator.yaml
  ./agentainer remove-session -c examples/tutorial-howto-creator.yaml
  ```

---

### See also

- [`research-swarm.md`](./research-swarm.md) — the delegate → do → review sibling.
- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`cli-reference.md`](../cli-reference.md) — every subcommand and flag.
- `examples/quickstart.yaml` — the key-free mock-agent starter.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

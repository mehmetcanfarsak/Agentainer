# Use case: the content studio / article factory

A concrete, end-to-end walkthrough of the shipped `examples/content-studio.yaml`
swarm — a four-agent **content pipeline** where an **editor-in-chief** takes a
topic from you, briefs a **researcher**, hands the facts to a **writer**, routes
the draft to an **seo** specialist, and ships the final copy back to you. It's
the canonical "human brief → gather → draft → optimize → approve" loop, wired
entirely through Agentainer's file-based mail model.

Everything below is based on the actual contents of `examples/content-studio.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you supply the
coding-CLI commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The topology

```
          topic
   user ─────────▶ editor ◀──┬──▶ researcher  (facts + sources)
          (final)   hub      ├──▶ writer      (drafts the article)
                             └──▶ seo         (keywords, meta, headings)
   ...researcher/writer/seo never talk to each other; only editor talks to user.
```

Four agents, one directed flow:

1. **`user` → `editor`** — you send the topic ("Write a 1200-word guide to X").
2. **`editor` → `researcher`** — the editor turns your topic into a brief and
   delegates the facts.
3. **`researcher` → `editor`** — the researcher returns a findings note.
4. **`editor` → `writer`** — the editor forwards the brief + facts to the writer.
5. **`writer` → `editor`** — the writer returns a draft.
6. **`editor` → `seo`** — the editor routes the draft to the seo specialist.
7. **`seo` → `editor`** — the seo specialist returns the optimized draft.
8. **`editor` → `user`** — the editor reviews and ships the final copy to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. The researcher, writer and seo can each only deliver back to
the editor; only the editor can reach `user`. Anything else is bounced back as a
`system` message and filed in `failed/` (see §7).

---

## 2. The config, explained

Here is `examples/content-studio.yaml` in full:

```yaml
# 📝 Content studio -- an editor-in-chief runs an article factory: research,
# drafting and SEO all funnel through one editor who owns publish-ready copy.
#
#   cp examples/content-studio.yaml my-studio.yaml
#   agentainer up    -c my-studio.yaml
#   agentainer send  -c my-studio.yaml --to editor "Write a 1200-word guide to composting at home."
#   agentainer down  -c my-studio.yaml
# ...
swarm:
  name: content-studio
  root: ./content-studio-workspace

defaults:
  capture: none
  can_talk_to: []

agents:
  - name: editor
    type: claude
    can_talk_to: [researcher, writer, seo, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the EDITOR-IN-CHIEF of a content studio. ...

  - name: researcher
    type: claude
    can_talk_to: [editor]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the RESEARCHER. ...

  - name: writer
    type: claude
    can_talk_to: [editor]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the WRITER. ...

  - name: seo
    type: codex
    can_talk_to: [editor]
    command: "codex --yolo"
    role: |
      You are the SEO SPECIALIST. ...
```

(The full `role` text is in `examples/content-studio.yaml`; only the shape is
shown here. Field by field:)

### `swarm`
- **`name: content-studio`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./content-studio-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent gets
  `content-studio-workspace/<name>/` as its workdir (created on `up`), and its
  mailbox folders live alongside. Orchestrator state goes under
  `content-studio-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, and it's ultimately keyed off each
  agent's `type`. For `claude` and `codex`, whose CLIs support a completion
  **hook**, setting `capture: none` is a footgun — so the config loader *upgrades*
  it back to `hook` and prints a warning at `up`
  (`capture: none on a claude agent gives the orchestrator no way to detect turn
  completion; using the type's default: capture: hook.`). Net effect here: all
  four agents end up with their natural capture (hook for the three claude
  agents, hook for the codex seo agent).
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `editor` (type: `claude`)
- **`can_talk_to: [researcher, writer, seo, user]`** — the editor is the hub: it
  can brief the researcher and writer, route to seo, and it is the **only agent
  that can talk to `user`**. That last part matters — keep the human-facing
  surface to a single agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the editor's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"). The role spells out the four-step pipeline (brief →
  research → write → seo → deliver) and embeds a **MAILBOX reminder**: read
  `inbox/`, move handled mail to `read/`, write to `outbox/<name>/` (after
  reading `about.md`), and finish the turn.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `researcher` (type: `claude`)
- **`can_talk_to: [editor]`** — reports only to the editor. It cannot reach the
  writer, seo, or `user` directly.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch.
- **`role`** — "gather the facts, data, quotes and sources … Produce a tight
  findings note … Do not write the article." So the researcher stays a fact
  provider and never drifts into drafting.

### `writer` (type: `claude`)
- **`can_talk_to: [editor]`** — reports only to the editor.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch.
- **`role`** — "Draft the article from the editor's brief and the researcher's
  findings … write it to `DRAFT.md`." Only states facts backed by the research;
  asks the editor rather than inventing gap-fillers.

### `seo` (type: `codex`)
- **`can_talk_to: [editor]`** — reports only to the editor. (This is the one
  non-claude agent — codex — to show a mixed-LLM pipeline in a content context.)
- **`command: "codex --yolo"`** — placeholder launch.
- **`role`** — "Take the writer's draft and make it findable … propose a primary
  keyword … title tag and meta description … H1 and H2/H3 outline." Flags
  keyword stuffing rather than adding it.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### What's *not* in this config
- **No `pings`.** None of the four agents has a periodic ping
  configured, so no agent is auto-nudged on a timer while idle — the studio is
  purely event-driven off real mail. (If you wanted the editor to poke a slow
  writer, you'd add a `pings` cron rule to it.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/content-studio.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for all four agents).
2. Creates the runtime dirs (`content-studio-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: the editor gets
   `outbox/researcher/`, `outbox/writer/`, `outbox/seo/`, `outbox/user/`; the
   researcher/writer/seo each get only `outbox/editor/`.
4. **Installs per-type turn detection** — the Claude Stop hook for the editor,
   researcher and writer, and the Codex `notify` hook for the seo agent.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the studio.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'content-studio' is up with 4 agent(s)
:: attach with:  tmux attach -t <editor-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/content-studio.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only bind — the UI binds `127.0.0.1` by default and never `0.0.0.0`.
See the `README.md` "control-plane UI" section and
[`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop (e.g.
> `bash -c 'while true; do read x; touch outbox/*/ready 2>/dev/null; done'`) and
> you can watch the whole studio route mail with no API keys — the mechanics are
> identical.

---

## 4. Drive a topic as the human

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the editor's final copy as mail (rather than
have it held), turn yourself available first:

```bash
./agentainer user available -c examples/content-studio.yaml
```

This rewrites the `user` contact card in the editor's `outbox/user/about.md` to
`Status: available`, so the editor sees you're reachable. (While away, mail to
you is *held* and the editor gets a `system` ack — nothing bounces.)

Now send the topic into the studio, addressed to the editor:

```bash
./agentainer send --to editor "Write a 1200-word guide to composting at home for beginner gardeners."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the editor, then — because the
inbox was empty — **released into `inbox/`** and the editor is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§5), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **editor receives the topic.** It reads `inbox/`, restates it as a brief, and
   writes a delegation file into `outbox/researcher/`. When its turn ends, the
   orchestrator sweeps the outbox, routes the brief to the researcher, and nudges
   the researcher.
2. **researcher gathers facts.** It reads its inbox, writes a findings note into
   `outbox/editor/`. On stop, that routes back to the editor.
3. **editor forwards to the writer.** It reads the findings and writes a
   brief-plus-facts file into `outbox/writer/`. On stop, that routes to the
   writer.
4. **writer drafts.** It reads its inbox, writes `DRAFT.md`-style content into
   `outbox/editor/`. On stop, that routes back to the editor.
5. **editor routes to seo.** It writes the draft into `outbox/seo/`. On stop,
   that routes to the seo agent.
6. **seo optimizes.** It writes the optimized draft + change list into
   `outbox/editor/`. On stop, that routes back to the editor.
7. **editor finalizes.** It reviews, optionally requests one fix round, then
   writes the final copy into `outbox/user/`. On stop, that's delivered to your
   `user` mailbox (you'll see it with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a topic, the agents just sit in standby (that's the point
> of the standby prompt). The studio only moves when real mail arrives — this
> swarm has no periodic pings to self-start it.

---

## 5. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/content-studio.yaml
```

```
swarm: content-studio   root: ./content-studio-workspace
  editor (claude) up idle queue=0 unread=1 talks=researcher, writer, seo, user
  researcher (claude) up idle queue=0 unread=0 talks=editor
  writer (claude) up idle queue=0 unread=0 talks=editor
  seo (codex) up idle queue=0 unread=0 talks=editor
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/content-studio.yaml          # whole swarm, last 20
./agentainer logs -c examples/content-studio.yaml -f        # follow live
./agentainer logs writer -c examples/content-studio.yaml    # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox editor -c examples/content-studio.yaml
```

Prints the one released message (headers + body), or `editor: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue editor -c examples/content-studio.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach editor -c examples/content-studio.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom
— handy for un-sticking an agent, but the mail model is the normal path.)

---

## 6. Resume after a stop

Tear the studio down when you're done:

```bash
./agentainer down -c examples/content-studio.yaml
```

Bring it back later and **conversations resume by default**:

```bash
./agentainer up -c examples/content-studio.yaml     # resume is the default
```

On `up`, Agentainer reads `content-studio-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the
editor/researcher/writer, `codex resume <id>` for the seo agent. A resumed agent
is *not* re-sent the standby prompt (its prior context is restored), so an
in-progress article keeps its thread. Pass `--no-resume` to force everyone fresh.
Inspect what's recorded with:

```bash
./agentainer sessions -c examples/content-studio.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md) and
the reboot walkthrough in [`use-cases/resume-after-reboot.md`](./resume-after-reboot.md).

---

## 7. Tips & footguns

- **Keep the editor the only `user`-facing agent.** In this config only the editor
  lists `user` in `can_talk_to`. That gives you a single point of contact and a
  clean funnel: raw research and drafts always pass through the editor before
  they reach you. If the writer tries to mail `user` directly, the orchestrator
  bounces it (ACL) and drops a `system` note in the writer's inbox explaining who
  it *can* message — the model self-corrects in-band.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch (e.g. a `claude` agent whose `command`
  doesn't launch Claude) means completion never triggers and the agent pins
  "busy" forever. `status` showing an agent `busy` for a long time with `unread`
  mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  studio: mail moved to `read/` is just a best-effort receipt, and a message
  shown `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is
  auto-archived so the queue advances. There's also a per-pair runaway cap
  (≤20 messages / 60s) to kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/content-studio.yaml
  ./agentainer remove-session -c examples/content-studio.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

- **Availability shapes the ending.** If `user` is **away** when the editor
  finishes, your final copy is *held* (with a `system` "the user is away" ack to
  the editor) rather than lost — read it later with `agentainer user inbox` or
  flip yourself available and it's delivered.

---

## 8. Customize

The studio is a starter — a few common forks:

- **Add a `factchecker`.** A fifth agent that the editor routes the seo output to
  before final delivery, to catch unsourced or wrong claims:
  ```yaml
  - name: factchecker
    type: claude
    can_talk_to: [editor]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the FACT-CHECKER. Given the optimized draft, verify every factual
      claim against the researcher's sources; flag anything unverified,
      contradictory, or stale, and return a short list of required fixes to the
      editor. Do not rewrite the article.
  ```
  Add `factchecker` to the editor's `can_talk_to`, so the editor's `role` step
  (6) becomes: send to `seo`, then to `factchecker`, then deliver.

- **Add a `social` agent for snippets.** A downstream agent that turns publish-
  ready copy into a batch of social posts / a newsletter blurb, again talking
  only to the editor.

- **Swap models.** The seo agent is already `codex`; set `researcher`/`writer` to
  `gemini` (with `capture: pane`) or `hermes` to compare writing styles, or run
  the whole studio on one model. Turn detection follows `type` automatically. See
  [`multi-llm-swarm.md`](../multi-llm-swarm.md).

- **Tune the ACL.** Want the writer to see research directly (faster, less
  chatter)? Add `researcher` to the writer's `can_talk_to` — but then you lose the
  editor's single choke-point on the draft's factual base, so only do it for
  trusted pipelines. The ACL is the contract; tighten or loosen deliberately.

- **Add periodic pings.** If you hand the editor a big topic and one specialist
  goes quiet, add a `pings` cron rule to that specialist so the
  orchestrator nudges it on a timer instead of waiting on a stuck turn.

- **Run the delegation pattern more generally.** The editor-as-hub is the
  classic "manager" shape; see [`delegation-pipeline.md`](../delegation-pipeline.md)
  for the reusable pattern and how to add/remove workers.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and the routing rules.
- [`delegation-pipeline.md`](../delegation-pipeline.md) — the editor-hub pattern in depth.
- [`multi-llm-swarm.md`](../multi-llm-swarm.md) — mixing claude / codex / gemini / hermes.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming conversations after a stop.
- [`cli-reference.md`](../cli-reference.md) — every subcommand and flag.

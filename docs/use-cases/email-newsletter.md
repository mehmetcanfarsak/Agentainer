# Use case: the email newsletter swarm

A concrete, end-to-end walkthrough of the shipped `examples/email-newsletter.yaml`
swarm — a four-agent pipeline where an **editor** is handed a theme and cadence by
the human, a **curator** picks the stories and links, a **writer** drafts the
issue, a **proofreader** checks tone and accuracy, and the editor delivers a
send-ready draft back to you. It's the canonical "direct → do the work → check
the work → ship" loop, wired entirely through Agentainer's file-based mail model.

Everything below is based on the actual contents of
`examples/email-newsletter.yaml` and the shipped CLI (`lib/cli.py`) and mailroom
(`lib/mail.py`). No API keys are needed to understand the mechanics; to run it
*for real* you supply the coding-CLI commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in [`mail-model.md`](../mail-model.md). The one-line
> version: an agent **reads a file** to receive mail and **writes a file** to send
> it; the orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The topology

```
          theme + cadence
   user ──────────────────▶ editor ◀──┬──▶ curator
          (final draft) ◀──  hub       ├──▶ writer
                                       └──▶ proofreader
```

Four agents, one directed flow:

1. **`user` → `editor`** — you give the theme and cadence (what it's about, how
   often it ships, roughly how long, who reads it).
2. **`editor` → `curator`** — the editor turns that into a brief and delegates the
   story picks.
3. **`curator` → `editor`** — the curator returns a ranked line-up of stories and
   links (not the issue prose).
4. **`editor` → `writer`** — the editor passes the brief + picks to the writer.
5. **`writer` → `editor`** — the writer returns a drafted issue.
6. **`editor` → `proofreader`** — the editor sends the draft for a tone/accuracy
   proof pass.
7. **`proofreader` → `editor`** — the proofreader returns the checked draft plus a
   change list.
8. **`editor` → `user`** — the editor delivers the final send-ready issue to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. `curator`, `writer` and `proofreader` can each talk **only**
to `editor`; only `editor` can reach `user`. Anything aimed at a forbidden
recipient is bounced back as a `system` message and filed in `failed/` (see §7).

```
   editor  <-->  user, curator, writer, proofreader   (the hub)
   curator <-->  editor
   writer  <-->  editor
   proofreader <--> editor
```

---

## 2. The config, explained

Here is `examples/email-newsletter.yaml` in full:

```yaml
# 📰 Email newsletter -- an editor runs an issue factory: story curation,
# drafting and a proof pass all funnel through one editor who owns the
# send-ready issue.
swarm:
  name: email-newsletter
  root: ./newsletter-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: editor
    type: claude
    can_talk_to: [curator, writer, proofreader, user]
    command: "claude --dangerously-skip-permissions"
    role: "You are the EDITOR ... the only one who declares an issue done. ..."
  - name: curator
    type: claude
    can_talk_to: [editor]
    command: "claude --dangerously-skip-permissions"
    role: "You are the CURATOR. Choose the stories, links and talking points ..."
  - name: writer
    type: claude
    can_talk_to: [editor]
    command: "claude --dangerously-skip-permissions"
    role: "You are the WRITER. Draft the newsletter issue ... write it to ISSUE.md."
  - name: proofreader
    type: claude
    can_talk_to: [editor]
    command: "claude --dangerously-skip-permissions"
    role: "You are the PROOFREADER. Check tone, facts and links before send ..."
```

Field by field:

### `swarm`
- **`name: email-newsletter`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./newsletter-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent gets `newsletter-workspace/<name>/`
  as its workdir (created on `up`), and its mailbox folders live alongside.
  Orchestrator state goes under `newsletter-workspace/.agentainer/` (never commit
  it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, and it's ultimately keyed off each
  agent's `type`. For `claude` agents, whose CLI supports a completion **hook**,
  setting `capture: none` is a footgun — so the config loader *upgrades* it back to
  `hook` and prints a warning at `up` (`capture: none on a claude agent gives the
  orchestrator no way to detect turn completion; using the type's default:
  capture: hook.`). Net effect here: all four agents use the Claude Stop hook.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `editor` (type: `claude`)
- **`can_talk_to: [curator, writer, proofreader, user]`** — the editor is the hub:
  it can delegate to the curator, ping the writer and proofreader, and it is the
  **only agent that can talk to `user`**. That last part matters — keep the
  human-facing surface to a single agent (see §7).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity, including the four-step run loop and the
  required `MAILBOX:` reminder (read `inbox/`, move to `read/`, write to
  `outbox/<name>/`, finish your turn). On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** so the editor waits for your theme
  instead of mailing peers proactively.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `curator` (type: `claude`)
- **`can_talk_to: [editor]`** — can only report back up to the editor. It has no
  line to `user` or to the writer, so picks always flow through edit first.
- **`role`** — "choose the stories, links and talking points; return a ranked
  line-up, not the prose."
- **Turn detection:** `claude` → Stop hook.

### `writer` (type: `claude`)
- **`can_talk_to: [editor]`** — drafts only for the editor's review.
- **`role`** — "draft the issue from the brief + line-up, write it to `ISSUE.md`,
  include a subject line and a preview/preheader."
- **Turn detection:** `claude` → Stop hook.

### `proofreader` (type: `claude`)
- **`can_talk_to: [editor]`** — returns the checked draft only to the editor.
- **`role`** — "check tone, accuracy and links; fix small things inline, flag
  anything that changes meaning for the editor."
- **Turn detection:** `claude` → Stop hook.

### What's *not* in this config
- **No `pings`.** None of the four agents has a periodic ping
  configured, so no agent is auto-nudged on a timer while idle — the pipeline is
  purely event-driven off real mail. (If you wanted the editor to poke a slow
  writer, you'd add a `pings` cron rule to it.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/email-newsletter.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for all four agents).
2. Creates the runtime dirs (`newsletter-workspace/.agentainer/…`: log, queue,
   run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: the editor gets
   `outbox/curator/`, `outbox/writer/`, `outbox/proofreader/`, `outbox/user/`; the
   curator gets `outbox/editor/`; and so on.
4. **Installs per-type turn detection** — the Claude Stop hook for all four agents.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'email-newsletter' is up with 4 agent(s)
:: attach with:  tmux attach -t <editor-session>
:: you can use the UI with:  agentainer serve --host 127.0.0.1 -c examples/email-newsletter.yaml --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). By default it binds **`127.0.0.1` only** — safe
for local use; never pass `--host 0.0.0.0` without also passing `--token` (see the
UI control-plane note in the repo `README.md`). See also
[`delegation-pipeline.md`](./delegation-pipeline.md) for the general pattern this
swarm follows.

> **Key-free demo:** swap each `command:` for a mock bash loop (`bash -c 'while
> true; do read x; done'`) and you can watch the whole pipeline route mail with no
> API keys — the mechanics are identical.

---

## 4. Drive an issue

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the editor's final issue as mail (rather than
have it held), turn yourself available first:

```bash
./agentainer user available -c examples/email-newsletter.yaml
```

This rewrites the `user` contact card in the editor's `outbox/user/about.md` to
`Status: available`, so the editor sees you're reachable. (While away, mail to you
is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the theme and cadence into the swarm, addressed to the editor:

```bash
./agentainer send --to editor "This week's theme: local AI tooling. Weekly, ships Friday, ~800 words, dev audience."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the editor, then — because the
inbox was empty — **released into `inbox/`** and the editor is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§5), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **editor receives the theme.** It reads `inbox/`, restates it as a one-paragraph
   brief, and writes a delegation into `outbox/curator/`. On stop, that routes to
   the curator and nudges it.
2. **curator picks stories.** It reads its inbox, returns a ranked line-up into
   `outbox/editor/`. On stop, that routes back to the editor.
3. **editor briefs the writer.** It passes the brief + picks into `outbox/writer/`.
   On stop, the writer is nudged.
4. **writer drafts.** It writes `ISSUE.md` and returns the draft into
   `outbox/editor/`. On stop, that routes back to the editor.
5. **editor sends for proof.** It writes the draft into `outbox/proofreader/`. On
   stop, the proofreader is nudged.
6. **proofreader checks.** It returns the proofed draft + a change list into
   `outbox/editor/`. On stop, that routes back to the editor.
7. **editor finalizes.** It reviews, optionally requests one fix round, then writes
   the send-ready issue into `outbox/user/`. On stop, that's delivered to your
   `user` mailbox (you'll see it with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a theme, the agents just sit in standby (that's the point of
> the standby prompt). The pipeline only moves when real mail arrives — this swarm
> has no periodic pings to self-start it.

---

## 5. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/email-newsletter.yaml
```

```
swarm: email-newsletter   root: ./newsletter-workspace
  editor (claude) up idle queue=0 unread=0 talks=curator, writer, proofreader, user
  curator (claude) up idle queue=0 unread=1 talks=editor
  writer (claude) up idle queue=0 unread=0 talks=editor
  proofreader (claude) up idle queue=0 unread=0 talks=editor
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/email-newsletter.yaml            # whole swarm, last 20
./agentainer logs -c examples/email-newsletter.yaml -f         # follow live
./agentainer logs writer -c examples/email-newsletter.yaml     # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox editor -c examples/email-newsletter.yaml
```

Prints the one released message (headers + body), or `editor: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue editor -c examples/email-newsletter.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach editor -c examples/email-newsletter.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 6. Resume after a stop

Tear the swarm down when you're done:

```bash
./agentainer down -c examples/email-newsletter.yaml
```

Bring it back later and **conversations resume by default**:

```bash
./agentainer up -c examples/email-newsletter.yaml     # resume is the default
```

On `up`, Agentainer reads `newsletter-workspace/.agentainer/sessions.yaml` (written
as each agent finished its first turn) and reattaches the recorded conversations
via Claude's native resume (`claude --resume <id>`). A resumed agent is *not*
re-sent the standby prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/email-newsletter.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 7. Tips & footguns

- **Keep the editor the only `user`-facing agent.** In this config only the editor
  lists `user` in `can_talk_to`. That gives you a single point of contact and a
  clean funnel: raw picks and drafts always pass through one editorial pass before
  they reach you. If the curator tries to mail `user` directly, the orchestrator
  bounces it (ACL) and drops a `system` note in the curator's inbox explaining who
  it *can* message — the model self-corrects in-band.

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

- **Availability shapes the ending.** If `user` is **away** when the editor
  finishes, your final issue is *held* (with a `system` "the user is away" ack to
  the editor) rather than lost — read it later with `agentainer user inbox` or
  flip yourself available and it's delivered.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down            -c examples/email-newsletter.yaml
  ./agentainer remove-session -c examples/email-newsletter.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

---

## 8. Customize

- **Add a subject-line tester.** Drop a fifth agent, e.g. `subjectline`, that the
  editor also lists in `can_talk_to`, and that lists only `editor` back. Give it a
  role like "propose 5 A/B subject lines for the issue, ranked by open-rate
  instinct" — now the editor gets a subject menu before send.

- **Swap models.** The four agents are all `claude` here, but `type` can be any of
  `claude`, `codex`, `gemini`, `hermes`. For example make `writer` a `gemini`
  (remember `gemini` needs `capture: pane`, which the loader will use automatically
  since the default is `none` → no, set `capture: pane` explicitly) or `curator` a
  `codex`. Keep `command` matched to `type` or the turn never completes — see
  [multi-llm-swarm.md](./multi-llm-swarm.md) for the full mixed-CLI recipe.

- **Tune the ACL.** Want the proofreader to also sanity-check the curator's picks?
  Add `curator` to `proofreader`'s `can_talk_to` and `proofreader` to `curator`'s.
  Remember the graph is the contract: every name must exist, no agent may list
  itself, and `system` is never a valid recipient.

- **Make it periodic.** Add a weekly `pings` rule (e.g. `cron: "0 9 * * mon"`) with a
  `message` like "Draft this week's issue from the last brief" to
  the `editor` so the swarm self-starts each cycle (you'd still feed it a fresh
  theme as needed).

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — bringing a swarm back.
- [delegation-pipeline.md](./delegation-pipeline.md) — the general hub-and-spoke pattern.
- [multi-llm-swarm.md](./multi-llm-swarm.md) — mixing claude/codex/gemini/hermes in one swarm.

# Use case: the LinkedIn ghostwriter swarm

Need **LinkedIn post ideas** on tap, or want to **ghostwrite LinkedIn** content at
a steady cadence without hiring an agency? This is a concrete, end-to-end
walkthrough of the shipped `examples/linkedin-ghostwriter.yaml` swarm — a
three-agent content team that turns one brief into a week of posts. A
**content_curator** curates topics from your expertise, a **post_writer** drafts
each post in your voice, and an **engagement_editor** optimizes the hooks and
builds an editorial calendar before anything reaches you. It's the classic
"strategy → draft → polish → schedule" loop, wired entirely through Agentainer's
file-based mail model.

Everything below is based on the actual contents of
`examples/linkedin-ghostwriter.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## Why this is a great fit for a swarm

Ghostwriting is not one job — it's three that keep tripping over each other when
one person (or one prompt) does them all:

- **Curation** needs distance: which of your ideas are actually post-worthy?
- **Drafting** needs immersion: get into the voice and write the thing.
- **Hook + calendar** needs an editor's eye: what makes someone stop scrolling,
  and what order should the week run in?

Splitting them across three agents with a strict hub-and-spoke ACL means the
**voice stays consistent** (everything funnels through the curator), the writer
isn't also second-guessing the schedule, and the editor sharpens hooks without
rewriting your argument. You get separation of concerns *and* a single owner.

---

## 1. The topology

```
        brief
  user ─────────────▶ content_curator ──────────▶ post_writer
        (posts +  ◀────────┐    │                    │ drafts
         calendar)         │    │                     ▼
                           │    └────────────▶ engagement_editor
                        return                  (hooks + calendar)
                           ◀───────────────────────┘
```

Three agents, one hub:

1. **`user` → `content_curator`** — you send the brief (expertise, audience,
   goal).
2. **`content_curator` → `post_writer`** — the curator picks topics and delegates
   each one to be drafted.
3. **`content_curator` → `engagement_editor`** — finished drafts go to the editor
   for hook optimization and scheduling.
4. **`engagement_editor` → `content_curator`** — the editor returns polished posts
   plus the updated calendar.
5. **`content_curator` → `user`** — the curator returns the finished week to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. The **post_writer and engagement_editor never talk to each
other**, and only the **content_curator can talk to `user`**. Anything else is
bounced back as a `system` message and filed in `failed/`.

---

## 2. The config, explained

Here is `examples/linkedin-ghostwriter.yaml` (roles abbreviated — see the file for
the full standing instructions):

```yaml
swarm:
  name: linkedin-ghostwriter
  root: ./linkedin-ghostwriter-workspace

defaults:
  capture: none
  can_talk_to: []

agents:
  - name: content_curator
    type: claude
    can_talk_to: [post_writer, engagement_editor, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the CONTENT CURATOR, the hub ... (turns the brief into 5-8 topics,
      delegates drafting, routes drafts for polish, is the only route to `user`).

  - name: post_writer
    type: codex
    can_talk_to: [content_curator]
    command: "codex --yolo"
    role: |
      You are the POST WRITER ... (drafts a complete post per topic in the
      client's voice: opening line, tight body, takeaway, light CTA).

  - name: engagement_editor
    type: claude
    can_talk_to: [content_curator]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the ENGAGEMENT EDITOR ... (hook optimization with 2-3 variants per
      post + an editorial calendar in CALENDAR.md).
```

📄 **Full config:** [`examples/linkedin-ghostwriter.yaml`](../../examples/linkedin-ghostwriter.yaml)

Field by field:

### `swarm`
- **`name: linkedin-ghostwriter`** — shows up in `status`, logs, sessions.
- **`root: ./linkedin-ghostwriter-workspace`** — the parent directory for the
  agents' working directories and mailboxes. Each agent gets
  `linkedin-ghostwriter-workspace/<name>/` as its workdir (created on `up`), and
  its mailbox folders live alongside. Orchestrator state goes under
  `linkedin-ghostwriter-workspace/.agentainer/` (never commit it).

### `defaults`
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, keyed off each agent's `type`. For
  `claude` and `codex`, whose CLIs support a completion **hook**, `capture: none`
  is a footgun — so the loader *upgrades* it back to `hook` and prints a warning
  at `up`. Net effect: all three agents here use their native hook.
- **`can_talk_to: []`** — the ACL floor is "talk to no one"; every agent states
  its own list explicitly.

### `content_curator` (type: `claude`)
- **`can_talk_to: [post_writer, engagement_editor, user]`** — the hub: it
  delegates to the writer, routes drafts to the editor, and is the **only agent
  that can talk to `user`**. Keep the human-facing surface to a single agent.
- **`command`** — launches Claude Code in its tmux pane (placeholder — substitute
  your own launch command or shell alias; command strings may embed keys, treat
  them as sensitive).
- **`role`** — the standing identity, delivered as the first prompt wrapped in a
  **standby notice** so the curator waits for your brief instead of proactively
  mailing peers. Includes the HUB MAILBOX reminder: it relays both ways.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `post_writer` (type: `codex`)
- **`can_talk_to: [content_curator]`** — reports only upward. It cannot reach the
  editor or the `user` directly; drafts always go back through the curator.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — draft one complete post per topic in the client's voice.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `engagement_editor` (type: `claude`)
- **`can_talk_to: [content_curator]`** — returns polished posts + calendar only to
  the curator.
- **`role`** — hook optimization (2-3 variants per post) plus the editorial
  calendar in `CALENDAR.md`; sharpen, don't rewrite the argument.

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/linkedin-ghostwriter.yaml
```

`up` loads and validates the config (printing the `capture: none → hook`
upgrades), creates the runtime dirs, initializes every mailbox (the five folders
`inbox/ outbox/ read/ sent/ failed/` plus an `outbox/<peer>/` with an `about.md`
contact card **for each allowed recipient**), installs per-type turn detection,
opens one tmux session per agent `cd`'d into its workdir, delivers the standby
first prompt, and starts the liveness supervisor.

At the end it prints attach and **`serve`** hints. The `serve` line gives you the
mail-app control-plane UI (threads, live panes, send-as-user, availability
toggle). It **binds `127.0.0.1` by default** — the safe loopback-only bind;
`--host`/`--token` are opt-in for remote access.

> **Key-free demo:** swap each `command:` for a mock bash loop and watch the whole
> pipeline route mail with no API keys — the mechanics are identical.

---

## 4. Drive a brief

The `user` is a **virtual mailbox** that defaults to **away**. To *receive* the
curator's finished week as mail (rather than have it held), turn yourself
available first:

```bash
./agentainer user available -c examples/linkedin-ghostwriter.yaml
```

Now send the brief into the swarm, addressed to the curator:

```bash
./agentainer send --to content_curator \
  "I'm a staff engineer who mentors juniors. Ghostwrite a week of LinkedIn posts \
   on career growth for early-career devs. Audience: junior engineers."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped
`From: user`, enqueued for the curator, then — because the inbox was empty —
**released into `inbox/`** and the curator is **nudged** (the protocol, including
its allowed-recipient list, is re-pasted into its pane).

### The mail flowing

Each hop is a `stop → sweep → route → release → nudge` cycle:

1. **content_curator receives the brief.** It writes a positioning note + a topic
   list, confirms scope (or proceeds), and writes a topic into
   `outbox/post_writer/`. On stop, that routes to the writer.
2. **post_writer drafts.** It reads the topic, writes a full draft into
   `outbox/content_curator/`. On stop, back to the curator.
3. **content_curator routes for polish.** It forwards drafts into
   `outbox/engagement_editor/`. On stop, to the editor.
4. **engagement_editor optimizes.** It tests hook variants, updates `CALENDAR.md`,
   and writes the results into `outbox/content_curator/`.
5. **content_curator finalizes.** It assembles the posts + calendar into
   `outbox/user/` — delivered to your `user` mailbox (`agentainer user inbox`, or
   the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each turn completion.

---

## 5. Observe

```bash
./agentainer status -c examples/linkedin-ghostwriter.yaml   # who's up/idle/busy, queue, unread, ACL
./agentainer logs   -c examples/linkedin-ghostwriter.yaml -f # follow the durable JSONL event log
./agentainer inbox  post_writer -c examples/linkedin-ghostwriter.yaml   # one agent's current message
./agentainer attach engagement_editor -c examples/linkedin-ghostwriter.yaml  # watch/type into a pane
```

The event log is the source of truth for history (tmux keeps no scrollback) —
you'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
one JSONL line per event.

---

## 6. Resume after a stop

```bash
./agentainer down -c examples/linkedin-ghostwriter.yaml
./agentainer up   -c examples/linkedin-ghostwriter.yaml   # resume is the default
```

On `up`, Agentainer reattaches recorded conversations via each type's native
resume: `claude --resume <id>` for the curator and editor, `codex resume <id>` for
the writer. A resumed agent is *not* re-sent the standby prompt. Pass
`--no-resume` to force everyone fresh; inspect what's recorded with
`agentainer sessions`.

---

## 7. Search intent — what people come here for

If you searched for any of these, this swarm is the answer:

- **"ghostwrite LinkedIn posts"** / **"AI LinkedIn ghostwriter"** — the whole
  point: a brief in, a week of on-voice posts out.
- **"LinkedIn post ideas"** / **"content ideas for LinkedIn"** — the
  content_curator's job is turning your expertise into 5-8 post-worthy topics.
- **"LinkedIn hook generator"** / **"how to write a LinkedIn hook"** — the
  engagement_editor tests 2-3 hook variants per post for the "...see more" fold.
- **"LinkedIn content calendar"** / **"editorial calendar tool"** — the editor
  maintains `CALENDAR.md` spacing themes across the week.
- **"batch write LinkedIn content"** / **"LinkedIn content at scale"** — one brief
  fans out into a full week in a single pipeline.

---

## 8. Tips & footguns

- **Keep the curator the only `user`-facing agent.** Only the content_curator
  lists `user` in `can_talk_to`. That gives you a single point of contact and one
  consistent voice. If the writer tries to mail `user` directly, the orchestrator
  bounces it (ACL) and drops a `system` note explaining who it *can* message — the
  model self-corrects in-band.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion. If an
  agent seems stuck, check its turn detection actually fires — a `type`/`command`
  mismatch (e.g. a `claude` agent whose `command` doesn't launch Claude) means
  completion never triggers and the agent pins "busy" forever. `status` showing a
  long `busy` with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is a best-effort receipt, a message shown 5 times
  without being handled is auto-archived, and a per-pair runaway cap (≤20 msgs /
  60s) kills "thanks!/you're welcome!" loops.

- **Availability shapes the ending.** If `user` is **away** when the curator
  finishes, your finished week is *held* (with a `system` ack to the curator)
  rather than lost — read it later with `agentainer user inbox` or flip yourself
  available.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`use-cases/research-swarm.md`](./research-swarm.md) — the delegate → do → review
  pipeline this mirrors.
- [`cli-reference.md`](../cli-reference.md) — every subcommand and flag.
- `examples/quickstart.yaml` — the key-free mock-agent starter.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

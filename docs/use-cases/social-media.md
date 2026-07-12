# Use case: the social media content swarm

A concrete, end-to-end walkthrough of the shipped `examples/social-media.yaml`
swarm — a **strategist** hub runs a content pipeline where a **copywriter**
writes the posts, a **visual** agent writes the image/video prompts, and a
**compliance** reviewer signs off before anything reaches the human. It's the
delegation-pipeline pattern (hub + spokes) applied to marketing content: one
human, one hub, three specialists, one compliance gate.

Everything below is based on the actual contents of `examples/social-media.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you supply the coding-CLI
commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state. The same pattern in
> depth: [`mail-model.md`](../mail-model.md).

---

## 1. The topology

```
            campaign / goal
  user ───────────────────▶ strategist ◀──┬──▶ copywriter  (posts, threads, hooks)
         (flag / ok)         hub (writes    ├──▶ visual       (image/video prompts)
                              brief, routes  └──▶ compliance   (approve / flag)
                              to compliance)
                                  │
                                  └── bundled copy+visual ──▶ compliance
                                                                   │  approve
                                                                   ▼
                                                              strategist ─▶ user
                                                                   │  hard flag
                                                                   ▼
                                                                  user
```

Four agents, one directed flow:

1. **`user` → `strategist`** — you send the campaign goal (topic, tone, platforms, volume).
2. **`strategist` → `copywriter` / `strategist` → `visual`** — the strategist
   sets the angle and briefs *both* in parallel. They never talk to each other,
   so the angle stays consistent.
3. **`strategist` → `compliance`** — the strategist bundles the copy + visual
   prompts into one package and routes it to compliance.
4. **`compliance` → `strategist`** — compliance approves or flags (with reasons).
   A hard brand/safety problem may also be raised straight to **`user`**.
5. **`strategist` → `user`** — on approval, the strategist delivers a clean
   "ready to publish" summary to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. An agent can only deliver to names on its own list; anything
else is bounced back as a `system` message and filed in `failed/` (see §7 and
[`delegation-pipeline.md`](../use-cases/delegation-pipeline.md#5-the-acl-bounce)).

---

## 2. Who this is for

- **Marketers & social media managers** — brief a run of posts once and get
  copy + matching visual prompts back, already checked for brand and platform
  rules, without juggling three tools.
- **Founders & small teams** — you have no dedicated content or design person;
  the swarm produces draft posts and image prompts you can paste straight into
  LinkedIn, X, etc.
- **Comms / brand teams** — the compliance agent is a standing gate so nothing
  off-brand or unsafe reaches the public, and the human only gets paged on real
  problems.
- **Anyone running a multi-LLM content line** — each role can run a *different*
  model (see §8 and [`multi-llm-swarm.md`](../use-cases/multi-llm-swarm.md));
  the strategist stays the single brand voice.

---

## 3. The config, explained

Here is `examples/social-media.yaml` in full:

```yaml
# 📱 Social media content swarm -- a STRATEGIST runs a content pipeline.
# Strategist hub: copywriter (posts), visual (prompts), compliance (approve/flag).
swarm:
  name: social-media
  root: ./social-media-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: strategist
    type: claude
    can_talk_to: [copywriter, visual, compliance, user]
    command: "claude --dangerously-skip-permissions"
    role: "You are the STRATEGIST. Set the angle from the human's goal, brief the copywriter and visual in parallel, then bundle their work and route it to compliance. Approve -> summarize to user; flag -> fix or escalate to user."
  - name: copywriter
    type: claude
    can_talk_to: [strategist]
    command: "claude --dangerously-skip-permissions"
    role: "You are the COPYWRITER. From the strategist's brief, write platform-tailored posts (hook, caption, thread). Return them to the strategist."
  - name: visual
    type: claude
    can_talk_to: [strategist]
    command: "claude --dangerously-skip-permissions"
    role: "You are the VISUAL agent. From the brief (and copy when available), write image/video generation prompts matching the copy and brand. Return them to the strategist."
  - name: compliance
    type: claude
    can_talk_to: [strategist, user]
    command: "claude --dangerously-skip-permissions"
    role: "You are the COMPLIANCE reviewer. Check brand voice, platform rules, and safety of the bundled copy+visual prompts. Approve, or flag with concrete fixes. Return to strategist; raise hard problems to user."
```

Field by field:

### `swarm`
- **`name: social-media`** — the swarm's name (shows up in `status`, logs, sessions).
- **`root: ./social-media-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent gets
  `social-media-workspace/<name>/` as its workdir (created on `up`), and its
  mailbox folders live alongside. Orchestrator state goes under
  `social-media-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, and it's ultimately keyed off each
  agent's `type`. For `claude` agents, whose CLI supports a completion **hook**,
  setting `capture: none` is a footgun — so the config loader *upgrades* it back
  to `hook` and prints a warning at `up`
  (`capture: none on a claude agent gives the orchestrator no way to detect turn
  completion; using the type's default: capture: hook.`). Net effect here: all
  four agents upgrade to their hook. In a key-free mock demo this is harmless
  (a bash loop never fires a hook anyway).
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent
  below states its own list explicitly, so this default is just a safe floor.

### `strategist` (type: `claude`)
- **`can_talk_to: [copywriter, visual, compliance, user]`** — the strategist is
  the hub: it briefs the two creators, routes to compliance, and is the **only
  agent that can talk to `user`**. Keeping human contact to one agent means one
  clean "ready to publish" message, not three specialist streams paging you.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command, e.g. a
  shell alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the strategist waits for your goal instead of
  proactively mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).
- **MAILBOX reminder:** the full `role` in the YAML tells the strategist the
  exact protocol — read `inbox/`, act, move to `read/`; to send, write a file
  into `outbox/<name>/` (read `outbox/<name>/about.md` first) and finish the
  turn. The orchestrator re-injects this on every nudge, so a forgetful model
  can't wedge the swarm.

### `copywriter` / `visual` (type: `claude`)
- **`can_talk_to: [strategist]`** — each creator talks *only* to the hub. They
  never address each other or `user`, so the brief stays the single source of
  truth and there's no accidental cross-talk between words and pictures.
- **`command`** — placeholder Claude launch commands; swap for mocks to go key-free.

### `compliance` (type: `claude`)
- **`can_talk_to: [strategist, user]`** — the reviewer reports its verdict to
  the strategist, and may *also* raise a hard brand/safety problem directly to
  `user`. That second edge is deliberate: compliance is the one spoke allowed to
  page the human, but only on a real problem — not for routine approvals.
- **MAILBOX reminder:** same protocol as the hub — read `inbox/`, act, write to
  `outbox/<name>/`, finish the turn.

### What's *not* in this config
- **No `periodically_ping_seconds`.** None of the agents is auto-nudged on a
  timer while idle — the pipeline is purely event-driven off real mail. (If you
  wanted the strategist to poke a quiet copywriter, add
  `periodically_ping_seconds: 300` to it.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §5).
- **All four are `claude`** in the example for simplicity; see §8 for mixing
  models.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/social-media.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for all four agents).
2. Creates the runtime dirs (`social-media-workspace/.agentainer/…`: log, queue,
   run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: the strategist gets
   `outbox/copywriter/`, `outbox/visual/`, `outbox/compliance/`,
   `outbox/user/`; compliance gets `outbox/strategist/`, `outbox/user/`; the
   creators each get `outbox/strategist/`.
4. **Installs per-type turn detection** — the Claude Stop hook for all four agents.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'social-media' is up with 4 agent(s)
:: attach with:  tmux attach -t <strategist-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/social-media.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). The UI is a **control plane** that can start
processes and type into agents, so by default it binds `127.0.0.1` — never
expose `0.0.0.0` without a token. Drop `--host`/`--token` for the safe
loopback-only bind (see `README.md` "control-plane UI").

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole pipeline route mail with no API keys — the mechanics are identical.

---

## 5. Drive a campaign

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the strategist's "ready to publish" summary
as mail (rather than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/social-media.yaml
```

This rewrites the `user` contact card in the strategist's `outbox/user/about.md`
to `Status: available`. (While away, mail to you is *held* and the sender gets a
`system` ack — nothing bounces.)

Now send the campaign goal into the swarm, addressed to the strategist:

```bash
./agentainer send --to strategist "Launch a 5-post series on our new API, friendly tone, LinkedIn + X."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the strategist, then — because
the inbox was empty — **released into `inbox/`** and the strategist is **nudged**
(the protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **strategist receives the goal.** It reads `inbox/`, sets the angle, writes
   two parallel briefs into `outbox/copywriter/` and `outbox/visual/`. On stop,
   both are swept and routed, and both specialists are nudged.
2. **copywriter + visual work in parallel.** Each reads its inbox, does the work,
   writes its output into `outbox/strategist/`. On stop, both route back to the
   strategist.
3. **strategist bundles.** It reads both replies, writes a single package into
   `outbox/compliance/`. On stop, that routes to compliance.
4. **compliance reviews.** It reads the package, writes an approve (or flag) note
   into `outbox/strategist/` — and may raise a hard problem straight to
   `outbox/user/`. On stop, those route back.
5. **strategist finalizes.** On approval, it writes the "ready to publish"
   summary into `outbox/user/`. That's delivered to your `user` mailbox (visible
   with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a goal, the agents just sit in standby (that's the point of
> the standby prompt). The pipeline only moves when real mail arrives — this swarm
> has no periodic pings to self-start it.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/social-media.yaml
```

```
swarm: social-media   root: ./social-media-workspace
  strategist (claude) up idle queue=0 unread=0 talks=copywriter, visual, compliance, user
  copywriter (claude) up busy queue=0 unread=1 talks=strategist
  visual      (claude) up busy queue=0 unread=1 talks=strategist
  compliance  (claude) up idle queue=0 unread=0 talks=strategist, user
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/social-media.yaml          # whole swarm, last 20
./agentainer logs -c examples/social-media.yaml -f        # follow live
./agentainer logs compliance -c examples/social-media.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox strategist -c examples/social-media.yaml
```

Prints the one released message (headers + body), or `strategist: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue compliance -c examples/social-media.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach compliance -c examples/social-media.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate

- **Tighten a weak post.** If compliance flags a post, the strategist re-briefs
  just the `copywriter` leg with the fix list — the `visual` prompt stands. The
  brief is the single source of truth, so a fix doesn't require re-running the
  whole run.
- **Change the platform mix.** Send a new goal to the strategist ("add an
  Instagram carousel, drop X") and the pipeline re-fans to the same agents with a
  different brief — no config change needed for different campaigns.
- **The ACL bounce saves you.** If `copywriter` ever tried to mail `compliance`
  or `user` directly, the orchestrator bounces it (ACL) and drops a `system`
  note in its inbox explaining who it *can* message — the model self-corrects
  in-band (see [`delegation-pipeline.md`](../use-cases/delegation-pipeline.md#5-the-acl-bounce)).
- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. A `type`/`command` mismatch means completion never triggers and the
  agent pins "busy" forever; `status` showing `busy` for a long time with
  `unread` mail is the tell.
- **Resume is on by default.** Tear down with `agentainer down` and bring it back
  with `agentainer up` — each agent reattaches its recorded conversation. See
  [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 8. Customize

**Add a `translator` for i18n.** Drop a spoke that only talks to the strategist,
and let the strategist fan the approved copy to it before delivery:

```yaml
  - name: translator
    type: gemini
    can_talk_to: [strategist]
    capture: pane
    command: "gemini --yolo"
    role: |
      You are the TRANSLATOR. Render the approved copy the strategist sends you
      into natural, idiomatic <target language>, then return it to the
      strategist (write a file into outbox/strategist/). Match the brand tone.
```

Then add `translator` to the strategist's `can_talk_to`. (This mirrors the
fan-out pattern in `examples/localization.yaml`.)

**Swap models per role** (see [`multi-llm-swarm.md`](../use-cases/multi-llm-swarm.md)).
The example uses `claude` for every role, but each `type`/`command` is
independent — e.g. run the `visual` prompt-writer on `gemini` and keep the
strategist on `claude`:

```yaml
  - name: visual
    type: gemini
    can_talk_to: [strategist]
    capture: pane
    command: "gemini --yolo"
    role: "You are the VISUAL agent. ..."
```

`gemini`/`hermes` use pane polling (`capture: pane`), so their turn completion
is detected by watching the tmux pane — make sure to set `capture: pane` on them
(or the loader upgrades the default correctly per type).

**Tune the ACL.** The example keeps `user` on exactly the strategist (plus the
compliance edge for hard flags). If you want compliance *only* to talk to the
strategist, drop `user` from its `can_talk_to`. Remember: `user` is a reserved
virtual mailbox — never name a real agent `user` or `system`.

**Make the human reachable by default.** Set `user_available: true` under `swarm:`
if you'd rather the `user` mailbox start open (mail delivered immediately instead
of held until `agentainer user available`).

---

## 9. Tips & footguns

- **Keep the strategist the only routine `user`-facing agent.** Only it lists
  `user` for normal delivery; compliance's `user` edge is reserved for hard
  flags. That gives you one clean inbox and a clean funnel: raw copy always
  passes through compliance before it reaches you.
- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived
  so the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s)
  to kill "thanks!/you're welcome!" loops.
- **Compliance is the one safe `user` edge.** Don't widen `user` to the creators
  — you'd get raw, unreviewed drafts paging you. Let the strategist be the only
  routine human channel.
- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/social-media.yaml
  ./agentainer remove-session -c examples/social-media.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.
- **Availability shapes the ending.** If `user` is **away** when the strategist
  finishes, your "ready to publish" summary is *held* (with a `system` ack to the
  strategist) rather than lost — read it later with `agentainer user inbox` or
  flip yourself available and it's delivered.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resume after a stop.
- [`delegation-pipeline.md`](../use-cases/delegation-pipeline.md) — the hub-and-spoke pattern this builds on.
- [`multi-llm-swarm.md`](../use-cases/multi-llm-swarm.md) — mixing claude/codex/gemini/hermes in one swarm.
- `examples/social-media.yaml` — the config referenced throughout this guide.

# Use case: the competitive-intel swarm

A concrete, end-to-end walkthrough of the shipped `examples/competitive-intel.yaml`
swarm — a **fan-out/fan-in** team where an **analyst** hub assigns one researcher
per competitor, three **researchers** each profile a single competitor in
parallel, and a **writer** merges the profiles into a decision-ready battlecard for
the human. It's the canonical "split the work N ways → gather it back → synthesize"
loop, wired entirely through Agentainer's file-based mail model.

Everything below is based on the actual contents of `examples/competitive-intel.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you supply the coding-CLI
commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

- **Product marketing (PMM).** You maintain battlecards and need them refreshed
  whenever a competitor ships or repricing hits. This swarm turns "one messy
  afternoon of tabs" into a repeatable pipeline.
- **Strategy / competitive-intelligence teams.** You track a set of rivals across
  fixed axes and want the profiles built independently so one loud competitor
  doesn't color the analysis of the others.
- **Founders and early PMs.** You're sizing up the field before a launch or a
  fundraise and want a fast, structured "us vs. them" you can act on.

The shape of the problem is always the same: several **independent** research
tasks that must be done to the *same rubric*, then folded into *one* artifact.
Fan-out keeps the profiles clean; fan-in produces the deliverable.

---

## 2. The topology

```
          you (user)
              │  market + competitor list
              ▼
           analyst ──────────────┬───────────────┬───────────────┐
          (the hub)              ▼               ▼               ▼
              ▲            researcher_a    researcher_b    researcher_c
              │ (findings) │               │               │
              └────────────┴───────────────┴───────────────┘
              │  merged findings
              ▼
            writer ───────▶ you (user)   (the battlecard)
```

Five agents, one directed flow:

1. **`user` → `analyst`** — you send the market and the competitor list.
2. **`analyst` → `researcher_a` / `researcher_b` / `researcher_c`** — the analyst
   assigns each researcher exactly one competitor and briefs them on the axes.
3. **`researcher_*` → `analyst`** — each researcher profiles its competitor and
   reports back to the analyst (never to each other, never to the writer).
4. **`analyst` → `writer`** — once every profile is in, the analyst forwards the
   consolidated set to the writer.
5. **`writer` → `user`** — the writer merges the profiles into a battlecard and
   delivers it to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. An agent can only deliver to names on its own list; anything
else is bounced back as a `system` message and filed in `failed/` (see §7).

### The ACL, at a glance

| agent          | `can_talk_to`                                       | faces the human? |
| -------------- | --------------------------------------------------- | ---------------- |
| `analyst`      | `researcher_a, researcher_b, researcher_c, writer, user` | yes         |
| `researcher_a` | `analyst`                                            | no               |
| `researcher_b` | `analyst`                                            | no               |
| `researcher_c` | `analyst`                                            | no               |
| `writer`       | `analyst, user`                                      | yes              |

Two agents face `user`: the **analyst** (to relay status / clarify the brief) and
the **writer** (to deliver the finished battlecard). Everyone else reports only
inward to the hub.

---

## 3. The config, explained

Here is `examples/competitive-intel.yaml`, abbreviated to the structure (the full
`role` prompts are in the file):

```yaml
swarm:
  name: intel
  root: ./intel-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: analyst
    type: claude
    can_talk_to: [researcher_a, researcher_b, researcher_c, writer, user]
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: "Monday refresh: re-run the standing competitor set and deliver an updated battlecard."
        cron: "0 9 * * mon"       # 09:00 every Monday (day name)
    role: |
      You are the COMPETITIVE INTELLIGENCE ANALYST and the hub ...
  - name: researcher_a
    type: claude
    can_talk_to: [analyst]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are a COMPETITOR RESEARCHER. The analyst assigns you exactly ONE ...
  - name: researcher_b
    type: claude
    can_talk_to: [analyst]
    command: "claude --dangerously-skip-permissions"
    role: |  # same rubric, different competitor
  - name: researcher_c
    type: claude
    can_talk_to: [analyst]
    command: "claude --dangerously-skip-permissions"
    role: |  # same rubric, different competitor
  - name: writer
    type: claude
    can_talk_to: [analyst, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the BATTLECARD WRITER. The analyst hands you the full set ...
```

Field by field:

### `swarm`
- **`name: intel`** — the swarm's name (shows up in `status`, logs, sessions).
- **`root: ./intel-workspace`** — the parent directory for the agents' working
  directories and mailboxes. Each agent gets `intel-workspace/<name>/` as its
  workdir (created on `up`), with its mailbox folders alongside. Orchestrator state
  lives under `intel-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless overridden.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer learns a turn finished, and it's keyed off each agent's `type`.
  For `claude`, whose CLI supports a completion **hook**, `capture: none` is a
  footgun — so the config loader *upgrades* it back to `hook` and prints a warning
  at `up`. Since every agent here is `claude`, all five get their Stop hook. (Swap
  any agent to a mock bash loop for a key-free demo and `capture: none` stays as-is,
  because a mock has no hook to install.)
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent states
  its own list explicitly, so this default is just a safe floor.

### `analyst` (type: `claude`)
- **`can_talk_to: [researcher_a, researcher_b, researcher_c, writer, user]`** — the
  hub: it briefs all three researchers, hands off to the writer, and can reach the
  human to clarify the brief or relay status.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first prompt,
  wrapped in a **standby notice** ("no task yet — don't send anything, you'll be
  notified"), so the analyst waits for your brief instead of proactively mailing
  peers. The role also carries a **MAILBOX** reminder that restates the two verbs
  and four folders — the model never has to remember the protocol.
- **`pings:`** — a single **weekly cron refresh**: `0 9 * * mon` fires a `system`
  message at 09:00 every Monday (the `mon` day-name; `1` works too), telling the
  analyst to re-run the *standing* competitor set from your last brief and deliver
  a fresh battlecard — so the intel doesn't quietly go stale between asks. The rule
  uses the default `when_busy: skip`, so if the analyst is already mid-analysis
  when Monday rolls around, the refresh is dropped rather than stacked onto a live
  run. (Each `pings` entry is just a `message` + a 5-field `cron`, evaluated in the
  host's local time.)
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `researcher_a` / `researcher_b` / `researcher_c` (type: `claude`)
- **`can_talk_to: [analyst]`** — each researcher can only report upward to the
  analyst. They **cannot** reach each other or the writer, which is the whole point:
  three independent profiles built to the same rubric, with no cross-contamination.
- **`role`** — identical across the three: "profile exactly ONE competitor" on the
  five axes (positioning, pricing, strengths, weaknesses, recent moves), mark
  unverifiable claims as assumptions, report to `outbox/analyst/`. The *assignment*
  (which competitor) is not hard-coded in the config — the analyst hands it out at
  runtime, so the same three researchers work for any market.

### `writer` (type: `claude`)
- **`can_talk_to: [analyst, user]`** — receives the consolidated profiles from the
  analyst and delivers the finished battlecard to the human. It can ask the analyst
  for a missing axis, but it never talks to the researchers directly.
- **`role`** — "merge the profiles into one decision-ready battlecard": a per-
  competitor summary line, a comparison table across the shared axes, and a "how to
  win / watch out" section with concrete talking points. Keep every claim traceable;
  surface disagreements rather than averaging them.

### What's *not* in this config
- **No cron pings on the researchers or the writer.** Only the `analyst` self-
  triggers (the weekly Monday refresh above); the rest of the pipeline is purely
  event-driven off real mail. (If you wanted to poke a slow researcher on a timer,
  you'd give *it* its own `pings:` rule.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §5).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/competitive-intel.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for all five claude agents).
2. Creates the runtime dirs (`intel-workspace/.agentainer/…`: log, queue, run,
   sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's `about.md`
   contact card *is* the ACL made visible: the analyst gets `outbox/researcher_a/`,
   `outbox/researcher_b/`, `outbox/researcher_c/`, `outbox/writer/`, `outbox/user/`;
   each researcher gets only `outbox/analyst/`; the writer gets `outbox/analyst/`
   and `outbox/user/`.
4. **Installs per-type turn detection** — the Claude Stop hook for each agent.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'intel' is up with 5 agent(s)
:: attach with:  tmux attach -t <analyst-session>
:: you can use the UI with:  agentainer serve -c examples/competitive-intel.yaml
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). By default it **binds `127.0.0.1`** (loopback
only) — a remote bind requires an explicit `--host` and a token. See the `README.md`
"control-plane UI" section and [`remote-access.md`](./remote-access.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch the
> whole fan-out route mail with no API keys — the mechanics are identical.

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want the writer's battlecard delivered as mail (rather than held),
turn yourself available first:

```bash
./agentainer user available -c examples/competitive-intel.yaml
```

This rewrites the `user` contact card in the analyst's and writer's `outbox/user/`
so they see you're reachable. (While away, mail to you is *held* and the sender gets
a `system` ack — nothing bounces.)

Now send the brief into the swarm, addressed to the analyst:

```bash
./agentainer send --to analyst \
  "Compare us vs CompetitorA, CompetitorB, CompetitorC in the API-monitoring space."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the analyst, then — because the
inbox was empty — **released into `inbox/`** and the analyst is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **analyst receives the brief.** It reads `inbox/`, assigns competitors
   one-to-one, and writes three briefs — one each into `outbox/researcher_a/`,
   `outbox/researcher_b/`, `outbox/researcher_c/`. When its turn ends, the
   orchestrator sweeps the outbox, routes all three messages, and nudges each
   researcher. **This is the fan-out** — three agents now have mail to work in
   parallel.
2. **each researcher profiles its competitor.** It reads its inbox, does the work,
   and writes a profile into `outbox/analyst/`. On stop, that routes back to the
   analyst. The three come back independently, in whatever order they finish.
3. **analyst gathers the profiles.** Each returning profile lands in the analyst's
   inbox one at a time (the inbox *is* the queue). The analyst tracks who's reported
   and who's outstanding; when all three are in, it forwards the consolidated set to
   `outbox/writer/`. **This is the fan-in.**
4. **writer synthesizes.** It reads the profiles and writes the finished battlecard
   into `outbox/user/`. On stop, that's delivered to your `user` mailbox (read it
   with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a brief, the agents just sit in standby (that's the point of
> the standby prompt). The pipeline only moves when real mail arrives — this swarm
> has no periodic pings to self-start it.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/competitive-intel.yaml
```

```
swarm: intel   root: ./intel-workspace
  analyst (claude) up idle queue=0 unread=0 talks=researcher_a, researcher_b, researcher_c, writer, user
  researcher_a (claude) up busy queue=0 unread=1 talks=analyst
  researcher_b (claude) up busy queue=0 unread=1 talks=analyst
  researcher_c (claude) up busy queue=0 unread=1 talks=analyst
  writer (claude) up idle queue=0 unread=0 talks=analyst, user
supervisor: alive
```

That snapshot — three researchers `busy` with `unread=1` while the analyst and
writer sit `idle` — is exactly what a healthy fan-out looks like mid-run.

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/competitive-intel.yaml           # whole swarm, last 20
./agentainer logs -c examples/competitive-intel.yaml -f        # follow live
./agentainer logs researcher_a -c examples/competitive-intel.yaml  # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox researcher_a -c examples/competitive-intel.yaml
```

Prints the one released message (headers + body), or
`researcher_a: inbox is empty`.

**Queue depth** — mail waiting behind the one released message (watch the analyst's
queue climb as profiles return):

```bash
./agentainer queue analyst -c examples/competitive-intel.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach analyst -c examples/competitive-intel.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate & tips

- **Keep the human-facing surface small.** Only `analyst` and `writer` list `user`.
  That gives you a clean funnel: raw profiles always pass through the analyst and
  the synthesis step before anything reaches you. If a researcher tries to mail
  `user` directly, the orchestrator bounces it (ACL) and drops a `system` note in
  the researcher's inbox explaining who it *can* message — the model self-corrects
  in-band.

- **Fan-out shows up as parallel `busy`.** The tell that the split worked is three
  researchers `busy` at once (§6). If only one moves at a time, check that the
  analyst actually wrote three separate files into three different `outbox/<name>/`
  folders — one file per recipient.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an agent
  stops, its outbox is swept, mail is routed, recipients are released and nudged. If
  an agent seems stuck, check that its **turn detection actually fires** — a
  `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
  Claude) means completion never triggers and the agent pins "busy" forever.
  `status` showing an agent `busy` for a long time with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **Availability shapes the ending.** If `user` is **away** when the writer finishes,
  your battlecard is *held* (with a `system` "the user is away" ack to the writer)
  rather than lost — read it later with `agentainer user inbox` or flip yourself
  available and it's delivered.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime + mailboxes)
  and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/competitive-intel.yaml
  ./agentainer remove-session -c examples/competitive-intel.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

---

## 8. Customize

- **Add more competitors (widen the fan-out).** Copy a researcher block, give it a
  unique lowercase name (`researcher_d`), keep `can_talk_to: [analyst]`, and add it
  to the analyst's `can_talk_to`. The analyst assigns it a competitor at runtime;
  no other change is needed. The pattern scales to as many rivals as you can afford
  panes for. (For a *lot* of competitors, run in batches rather than one giant
  fan-out — the analyst's inbox is still a one-at-a-time queue on the way back in.)

- **Swap models per role.** Nothing requires all-`claude`. Make the researchers
  `gemini` for cheap breadth and keep the writer `claude` for the synthesis:
  ```yaml
  - name: researcher_a
    type: gemini
    capture: pane          # gemini has no completion hook — poll the pane
    command: "gemini --yolo"
    can_talk_to: [analyst]
  ```
  Mixing engines is a first-class pattern — see
  [`multi-llm-swarm.md`](../multi-llm-swarm.md) for the trade-offs and the
  `capture: pane` detail. Treat `command` strings as sensitive; they may embed keys.

- **Tune the ACL.** Want the writer to interview researchers directly for a missing
  axis? Add `writer` to each researcher's `can_talk_to` and `researcher_*` to the
  writer's. Want a second synthesis pass? Insert an `editor` agent between `writer`
  and `user`. Every edge you add or remove is one line in a `can_talk_to` list, and
  it's enforced, not advisory.

- **Sharpen the rubric.** The five axes live in the researchers' `role` prompts.
  Editing them there (add "integrations" or "customer sentiment", say) changes what
  every researcher gathers — and the writer's table should list the same axes so
  fan-in stays aligned.

- **Automate refreshes.** Point the swarm at a `custom-workspace` so profiles
  persist between runs (see [`custom-workspace.md`](./custom-workspace.md)), then
  re-send the brief on a schedule to keep the battlecard current.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the two-verbs / four-folders model in full.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — tear down and resume the
  swarm without losing context.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — the hub-and-spoke
  delegation pattern this swarm builds on.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing claude/codex/gemini/hermes
  across roles.
- [`research-swarm.md`](./research-swarm.md) — the sibling "delegate → do → review"
  linear pipeline.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

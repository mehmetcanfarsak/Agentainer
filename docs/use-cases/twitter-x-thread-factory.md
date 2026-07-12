# Use case: the Twitter/X thread factory

A concrete, end-to-end walkthrough of the shipped `examples/twitter-x-thread-factory.yaml`
swarm — a three-agent content pipeline that turns a bare topic into a polished,
**hooked X/Twitter thread**. An **idea generator** mines the topic for the single
most scroll-stopping angle, a **thread writer** drafts the full thread, and a
**hook optimizer** rewrites the opening tweet — the one tweet most people ever
read — into high-tension variants. It's the "come up with the idea → write it →
sharpen the hook" loop, wired entirely through Agentainer's file-based mail model.

If you've ever searched for **X thread ideas**, how to write a **viral tweet
hook**, or a repeatable **Twitter thread generator** workflow, this is that
workflow expressed as cooperating agents instead of a single prompt — each stage
is a specialist, and the routing between them is enforced, not hoped for.

Everything below is based on the actual contents of `examples/twitter-x-thread-factory.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you supply the coding-CLI
commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Why this is a great multi-agent use case

Writing a thread that actually gets read is three different jobs, and they fight
each other when one model tries to do them at once:

- **Ideation** wants divergence — chase the contrarian, counter-intuitive angle.
- **Drafting** wants structure — one idea per tweet, momentum from line to line.
- **Hook optimization** wants ruthless compression — the first tweet is a
  standalone ad for the other nine.

Splitting them into three agents with a strict hand-off means the hook optimizer
never gets to dilute the idea, the writer never ships an unoptimized first tweet,
and the idea generator stays the single quality gate that decides what reaches
you. That separation of concerns is exactly what the mail model makes cheap.

---

## 2. The topology

```
              user
               │  topic
               ▼
          idea_generator   ── premise ──▶ thread_writer
        (hub, user-facing)                     │
               ▲                               │ draft
               │ finished thread               ▼
               └──────────────────────  hook_optimizer
                                        (3 sharper hooks ──▶ writer)
```

Three agents, one directed loop:

1. **`user` → `idea_generator`** — you send a topic or audience.
2. **`idea_generator` → `thread_writer`** — the hub distills the topic into one
   crisp thread *premise* (the promise + the audience) and delegates the draft.
3. **`thread_writer` → `hook_optimizer`** — the writer drafts the full thread and
   asks the optimizer for a sharper opening tweet.
4. **`hook_optimizer` → `thread_writer`** — the optimizer returns 3 high-tension
   hook variants; the writer folds the best one in.
5. **`thread_writer` → `idea_generator`** — the finished thread goes back to the
   hub for assembly.
6. **`idea_generator` → `user`** — the hub returns the polished thread to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. The `idea_generator` is the **hub** and the only agent that
can reach `user`; the writer and optimizer talk to each other and back to the hub,
but never to you directly. Anything off-graph is bounced as a `system` message and
filed in `failed/` (see the research walkthrough §7 for the bounce mechanics).

---

## 3. The config

Here is `examples/twitter-x-thread-factory.yaml` in full (see the file:
[`examples/twitter-x-thread-factory.yaml`](../../examples/twitter-x-thread-factory.yaml)):

```yaml
swarm:
  name: twitter-x-thread-factory
  root: ./twitter-x-thread-factory-workspace

defaults:
  capture: none              # mock agents don't fire a turn-completion hook
  can_talk_to: []            # tightened per agent below

agents:
  - name: idea_generator
    type: claude
    can_talk_to: [thread_writer, hook_optimizer, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the IDEA GENERATOR and the hub of a Twitter/X thread factory. You
      are the only agent who talks to the user. ...

  - name: thread_writer
    type: codex
    can_talk_to: [idea_generator, hook_optimizer]
    command: "codex --yolo"
    role: |
      You are the THREAD WRITER. Given a thread premise from the idea_generator,
      draft a complete X/Twitter thread ...

  - name: hook_optimizer
    type: gemini
    can_talk_to: [idea_generator, thread_writer]
    capture: pane            # gemini can't fire a completion hook; poll the pane
    command: "gemini --yolo"
    role: |
      You are the HOOK OPTIMIZER. You obsess over tweet #1 -- the only tweet most
      people ever read. ...
```

(The `role:` blocks are trimmed here — read the file for the full standing
instructions, including each agent's MAILBOX reminder.)

Field by field:

### `swarm`
- **`name: twitter-x-thread-factory`** — shows up in `status`, logs, sessions.
- **`root: ./twitter-x-thread-factory-workspace`** — the parent for each agent's
  working directory and mailbox. Each agent gets
  `twitter-x-thread-factory-workspace/<name>/` as its workdir (created on `up`).
  Orchestrator state lives under `…-workspace/.agentainer/` (never commit it).

### `defaults`
- **`capture: none`** — the default turn-detection mode. For `claude`/`codex`,
  whose CLIs support a completion **hook**, the loader *upgrades* this back to
  `hook` and prints a warning at `up` (you'll see it for `idea_generator` and
  `thread_writer`). The `hook_optimizer` overrides to `pane`.
- **`can_talk_to: []`** — the ACL floor is "talk to no one"; every agent states
  its own list explicitly.

### `idea_generator` (type: `claude`)
- **`can_talk_to: [thread_writer, hook_optimizer, user]`** — the hub. It can
  delegate to both specialists and it is the **only agent that can talk to
  `user`**, so the human-facing surface stays a single funnel.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `thread_writer` (type: `codex`)
- **`can_talk_to: [idea_generator, hook_optimizer]`** — drafts, collaborates with
  the optimizer, reports finished work upward. It **cannot** reach `user`.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `hook_optimizer` (type: `gemini`)
- **`can_talk_to: [idea_generator, thread_writer]`** — sends its hook variants
  back to the writer; reachable by the hub.
- **`capture: pane`** — Gemini's CLI can't call a completion program, so
  Agentainer detects "turn done" by **polling the tmux pane** until it stops
  changing (hence the explicit override of the `none` default).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/twitter-x-thread-factory.yaml
```

`up` loads and validates the config (printing the `capture: none → hook`
upgrades), creates the runtime dirs, initializes each agent's five mailbox folders
(`inbox/ outbox/ read/ sent/ failed/`) plus an `outbox/<peer>/` for each allowed
recipient, installs per-type turn detection, opens one tmux session per agent, and
delivers the standby first prompt. It then prints attach and **`serve`** hints.

> The `serve` UI binds **`127.0.0.1` by default** — loopback only. Add
> `--host 0.0.0.0 --token <generated>` **only** if you deliberately want a remote
> bind; the UI is a control plane that can type into agents. See the `README.md`
> "control-plane UI" section.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole pipeline route mail with no API keys — the mechanics are identical.

---

## 5. Drive a topic

The `user` is a **virtual mailbox** that defaults to **away**. To *receive* the
finished thread as mail rather than have it held, make yourself available first:

```bash
./agentainer user available -c examples/twitter-x-thread-factory.yaml
```

Then send a topic into the swarm, addressed to the hub:

```bash
./agentainer send --to idea_generator "Topic: what I learned bootstrapping a SaaS to \$10k MRR, for indie founders."
```

### The mail flowing

Each hop is a `stop → sweep → route → release → nudge` cycle — you relay nothing
by hand:

1. **idea_generator distills the topic** into one thread premise and writes it to
   `outbox/thread_writer/`. On stop, it routes to the writer.
2. **thread_writer drafts** the full thread (hook + body + CTA) and writes it to
   `outbox/hook_optimizer/`, asking for a sharper opening.
3. **hook_optimizer rewrites tweet #1** into 3 tension-loaded variants with a
   recommendation and writes them to `outbox/thread_writer/`.
4. **thread_writer folds in the best hook** and sends the finished thread to
   `outbox/idea_generator/`.
5. **idea_generator assembles and returns** the polished thread to `outbox/user/`
   — read it with `agentainer user inbox`, or in the UI.

> If you don't send a topic, the agents sit in standby — this swarm has no
> periodic pings, so the pipeline only moves on real mail.

---

## 6. Observe

```bash
./agentainer status -c examples/twitter-x-thread-factory.yaml   # who's up, queue depth, unread, ACL
./agentainer logs   -c examples/twitter-x-thread-factory.yaml -f # durable event log, follow live
./agentainer inbox thread_writer -c examples/twitter-x-thread-factory.yaml
./agentainer attach hook_optimizer -c examples/twitter-x-thread-factory.yaml   # detach with Ctrl-b d
```

The JSONL event log is the source of truth for history (tmux keeps no scrollback):
you'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`.

---

## 7. Search intent — what people come here looking for

This swarm answers a cluster of very common creator queries:

- **"X thread ideas" / "Twitter thread topics"** → the `idea_generator` turns any
  seed topic into one sharp, scroll-stopping premise.
- **"how to write a viral tweet hook" / "best Twitter hooks"** → the
  `hook_optimizer` is a dedicated specialist that produces 3 ranked opening-tweet
  variants using curiosity gaps, specificity, and concrete payoffs.
- **"Twitter thread generator" / "AI thread writer"** → the full pipeline drafts a
  complete, per-tweet-structured thread, not a blob of text.
- **"thread hook formula" / "how to start a Twitter thread"** → captured in the
  optimizer's role: lead with a bold claim or specificity, open a curiosity gap,
  promise a payoff, cut every wasted word.
- **"content repurposing workflow" / "multi-agent content pipeline"** → a
  reusable template: clone it and re-point the roles at LinkedIn posts, newsletters,
  or YouTube titles.

---

## 8. Tips & footguns

- **Keep the `idea_generator` the only `user`-facing agent.** It's the single
  quality gate and funnel; the writer and optimizer never reach you directly, so
  nothing half-baked escapes review. Off-graph sends bounce with a `system` note
  telling the agent who it *can* message — the model self-corrects in-band.

- **The hook is the product.** Most readers judge a thread on tweet #1 alone. The
  `hook_optimizer` exists precisely so the opening tweet is never an afterthought —
  if your threads underperform, iterate on this agent's role first.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion. If an
  agent seems stuck, confirm its **turn detection actually fires** — a
  `type`/`command` mismatch (a `claude` agent whose `command` doesn't launch
  Claude) means completion never triggers; `status` showing `busy` for a long time
  with `unread` mail is the tell. (Agentainer detects this mismatch at `up`.)

- **Force-idle a pane-captured agent if needed.** The `hook_optimizer` uses pane
  polling; if its capture never registers, nudge the state along:
  ```bash
  ./agentainer idle hook_optimizer -c examples/twitter-x-thread-factory.yaml
  ```

- **Repurpose the template.** The shape (hub + drafter + optimizer) generalizes:
  swap the roles to produce LinkedIn carousels, cold-email sequences, or blog
  outlines. The routing and mail plumbing stay identical.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`use-cases/research-swarm.md`](./research-swarm.md) — the canonical
  delegate → do → review pipeline.
- [`cli-reference.md`](../cli-reference.md) — every subcommand and flag.
- `examples/quickstart.yaml` — the key-free mock-agent starter.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

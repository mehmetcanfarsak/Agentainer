# Use case: the user → orchestrator → developer delegation pipeline

This guide describes the headline Agentainer v2 pattern — a single human driving
a team of coding agents through **one hub agent**, the *orchestrator*. It is the
topology proven end-to-end against the live `chy3` model: the full
`user → orchestrator → developer → orchestrator → user` mail loop — including the
ACL bounce — runs with the real model doing file I/O and tool use.

Everything below is grounded in the actual runtime. The orchestrator owns all
routing, the ACL, read-state, queueing and the durable log (`lib/mail.py`);
the human drives it from the CLI (`lib/cli.py`) or the UI. The model only ever
reads and writes natural-language files.

---

## 1. The pattern

You (the human) are the `user` virtual mailbox. You send a task to **one** agent
— the orchestrator — and it is the only agent that lists `user` in its
`can_talk_to`. The orchestrator:

1. **Decomposes** your request into a plan and discrete tasks.
2. **Delegates** each task by writing a file into `outbox/<worker>/` — e.g.
   `outbox/developer/`, `outbox/researcher/`, `outbox/reviewer/`.
3. **Waits**. Its turn ends the moment it writes the outgoing mail; the agents
   reply on their own turns.
4. **Synthesizes** the workers' replies into a final answer and writes it back
   to `outbox/user/` (or holds it for you if you are away — see §3/§4).

The workers only ever talk back to the orchestrator. They never contact each
other and never contact `user` directly. The orchestrator is the hub; everyone
else is a spoke.

```
        user  (you, virtual mailbox)
          │  send "build X"
          ▼
      ┌──────────────┐
      │ orchestrator │  ← the only agent with `user` in can_talk_to
      └──────────────┘
       │          │          │
       ▼          ▼          ▼
   developer   researcher   reviewer     ← each lists only [orchestrator]
       │          │          │
       └──────────┴──────────┘
                     │  replies
                     ▼
                 orchestrator
                     │  final answer
                     ▼
                   user
```

Why this is the file mail model in one picture: every delegation is
*orchestrator writes a file → orchestrator's turn ends → that agent stops later
→ orchestrator sweeps its outbox → routes the reply back*. See `mail.on_stop`
and `mail.route_outbound` in `lib/mail.py`.

---

## 2. Why this topology

- **Small human surface.** You send one message to one agent and read one final
  answer. You never have to know the worker names, their workdirs, or the order
  of operations — the orchestrator sequences the work.
- **The orchestrator enforces the protocol.** Because every outbound message is
  routed by `mail.route_outbound`, the hub is where the ACL, message IDs,
  threading and read-state live. Workers receive a clean, pre-stamped,
  one-at-a-time `inbox/` message and reply into their `outbox/orchestrator/`
  folder; they never have to do bookkeeping.
- **Workers are isolated behind the ACL.** A worker can only write into the
  folders the orchestrator provisioned for it (`outbox/orchestrator/`, plus any
  peers you explicitly allow). It physically cannot address `user` unless you
  put `user` in its `can_talk_to` — which, by design, you do not.
- **It degrades gracefully.** If a forgetful worker never moves a handled
  message to `read/`, the orchestrator auto-archives it after 5 presentations
  (`mail.AUTO_ARCHIVE_PRESENTATIONS`) and advances the queue, so a non-compliant
  model can never wedge the swarm.

---

## 3. Wiring it (YAML)

Three rules define the hub-and-spoke graph:

1. The orchestrator lists every worker **and** `user` in `can_talk_to`.
2. Each worker lists only `[orchestrator]` (plus any peer you want, e.g. a
   reviewer it can hand off to).
3. `user` is a **reserved virtual mailbox** — never a real agent name. The
   config loader rejects an agent literally named `user` or `system`.

Validated config excerpt:

```yaml
swarm:
  name: myteam
  root: ./workspace

defaults:
  can_talk_to: []          # tightened per agent below

agents:
  - name: orchestrator
    type: claude
    # The hub: may reach every worker AND the human.
    can_talk_to: [developer, researcher, reviewer, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the orchestrator. Break the user's request into tasks, delegate
      each to the right worker via outbox/<name>/, then synthesize their replies
      into a single answer and send it back to outbox/user/.

  - name: developer
    type: codex
    # A spoke: only talks back to the hub.
    can_talk_to: [orchestrator]
    command: "codex --yolo"
    role: "You are the developer. Implement what the orchestrator asks."

  - name: researcher
    type: gemini
    capture: pane
    can_talk_to: [orchestrator]
    command: "gemini --yolo"
    role: "You are the researcher. Investigate and report to the orchestrator."

  - name: reviewer
    type: claude
    can_talk_to: [orchestrator]
    command: "claude --dangerously-skip-permissions"
    role: "You are the reviewer. Critique the developer's work for the orchestrator."
```

Notes on the config contract (from `lib/config.py`):

- **`user_available` defaults to `false`.** Unless you set it (or run
  `agentainer user available`), human-bound mail is **held, never bounced** —
  the sender gets a `system` ack ("Delivered — the user is away and may respond
  later") and your reply is deferred until you open the mailbox. You will not
  lose mail by being away.
- **`can_talk_to` is a strict whitelist.** The special value `"*"` means
  "everyone except self"; the validator rejects `system` as a recipient and
  rejects unknown agent names, so a typo fails fast at `up`/`validate`, not
  silently at runtime.
- The **presence of `outbox/<name>/` *is* the ACL**: `mail.init_mailboxes`
  creates a folder (and a `about.md` contact card) only for names actually in
  `can_talk_to`. A worker literally has no `outbox/user/` directory to write to.

Validate before launching:

```bash
./agentainer validate -c myteam.yaml
```

---

## 4. Driving it as the human

All commands accept `-c <cfg>` (or set `AGENTAINER_CONFIG`). Short forms:

**Open the human mailbox** (flips `user_available` to true; rewrites every
agent's `outbox/user/about.md` status to `available`):

```bash
./agentainer user available -c myteam.yaml
```

**Send a task to the orchestrator** (you are the `user` sender):

```bash
./agentainer send --to orchestrator "Build a URL shortener with an API + web UI."
# or, equivalently:
./agentainer user send --to orchestrator "Build a URL shortener ..."
```

The message lands in the orchestrator's queue; the supervisor (or its
turn-completion hook) releases it into `inbox/`, pastes a nudge, and the
orchestrator starts working.

**Watch it happen:**

```bash
./agentainer status -c myteam.yaml        # who's up, busy, queue depth, unread
./agentainer logs   -c myteam.yaml -f      # durable JSONL event log (route/acl/bounce/…)
./agentainer inbox  -c myteam.yaml orchestrator   # the orchestrator's current message
```

**Read the reply** (the orchestrator's final answer waits in the `user` queue):

```bash
./agentainer user inbox -c myteam.yaml
```

**Close up** when you're done (mail from workers is held + acked until you come
back):

```bash
./agentainer user away -c myteam.yaml
```

From the **UI** the same operations exist: `user available`/`away`, the user
inbox panel, and a "send from user" composer. The UI is the `user` mailbox's
"terminal" (ProjectPlan §11) — sending from the UI and sending `user` mail are
the same code path, differing only by sender identity.

---

## 5. The ACL bounce

Suppose a worker (or the orchestrator, by mistake) tries to mail someone outside
its `can_talk_to`. The orchestrator catches it in `mail.route_outbound` *before
any mail moves*:

- If `recipient` is not in `cfg.get(sender).can_talk_to`, the orchestrator:
  1. Drops a `system` mail into the **sender's** queue:
     *"Your message to `<recipient>` couldn't be sent — you can message:
     `<allowed list>`."*
  2. Moves the offending outbox file into the sender's `failed/` folder.
  3. Logs a `bounce` event (`reason="acl"`).
- `system` itself is never a valid recipient — addressing it triggers the same
  bounce-and-file treatment.
- A per-pair runaway cap (`RUNAWAY_CAP = 20` per 60 s) rate-limits "thanks! /
  you're welcome!" loops instead of bouncing them.

**What the agent sees:** its turn ends, it is nudged with the `system` error
("you can message: orchestrator"), and its `failed/` folder now contains the
message it tried to send. It self-corrects *in-band* on its next turn — no new
concept, no crash (ProjectPlan §11: "errors come back as mail").

**Honest limitation (Decision D15).** The ACL is **cooperative, not OS
isolation**. Agents are coding agents with filesystem access and *could* write
straight into another agent's `inbox/`, bypassing `outbox/`. The ACL is enforced
for well-behaved agents that route through `outbox/`; it is documented plainly
and is not a security boundary. Real isolation, if ever needed, must come from
OS-level per-workspace users/permissions, not the mail model.

---

## 6. Worked example (adapted from `examples/research.yaml`)

`examples/research.yaml` is the smallest shipped topology that exercises this
exact pattern: a `coordinator` hub with `user` in its `can_talk_to`, a
`researcher` and a `reviewer` spoke that only talk to the coordinator.

Step-by-step against the live model:

```bash
# 0) Bring the swarm up (real CLIs in the example; swap for mocks to go key-free).
cp examples/research.yaml my-research.yaml
./agentainer up -c my-research.yaml

# 1) Open the human mailbox so the coordinator can reply to you.
./agentainer user available -c my-research.yaml

# 2) Send the task. Only the coordinator may receive it (it has `user`).
./agentainer send --to coordinator \
  "Summarize the state of Rust async runtimes and flag the top open question."

# 3) Watch the delegation fan out.
#    - coordinator decomposes, writes outbox/researcher/ + outbox/reviewer/
#    - researcher answers the coordinator; reviewer critiques it back to coordinator
./agentainer status -c my-research.yaml
./agentainer logs   -c my-research.yaml -f

# 4) Read the synthesized answer (it waits in the user queue).
./agentainer user inbox -c my-research.yaml

# 5) Close up.
./agentainer user away -c my-research.yaml
./agentainer down -c my-research.yaml
```

What you should observe in the logs: a `user-send` event, then `route` events
from `coordinator` to `researcher`/`reviewer`, `route` events back to
`coordinator`, and finally a `delivered`/`user-held` event as the coordinator
writes to `user`. If you forgot step 1, the final event is `user-held` (held +
acked), not lost — flip available later and read it.

(The larger `examples/software-company.yaml` shows the same hub pattern at team
scale: a `cto` hub, an `architect`, `backend`/`frontend`, `qa`, and `docs`,
where workers talk to the hub and to specific peers but never to `user`.)

---

## 7. Tips

- **Keep `user` on exactly one agent — the orchestrator.** If more than one
  agent lists `user`, several agents can independently decide to "answer the
  human," and you get duplicate, possibly contradictory, replies in your inbox.
  The ACL makes this a one-line config decision; resist the urge to widen it.
- **Use `pings` sparingly.** A ping is a `system` message
  injected into an idle agent's queue (guards: idle-only, no-pile-up,
  once-per-minute). It is a liveness nudge, not a task driver — set it only
  for agents that genuinely go quiet mid-task, and keep the interval large.
- **The `system` mailbox carries errors and nudges.** Bounces, delivery acks,
  read receipts and periodic pings all arrive `From: system`. That is by design:
  agents self-correct in-band, so never strip or suppress `system` mail — it is
  how a model learns its message was bounced or that it has unread mail.
- **You are never auto-nudged.** Unlike agents, `user` has no pane to paste into
  (ProjectPlan §11). The UI surfaces your incoming mail with an unread badge /
  highlight; from the CLI, `agentainer user inbox` is your read check.
- **`user_available: false` by default is a feature.** Bring the mailbox up only
  when you are actually watching; workers' replies pile up (held + acked) and
  nothing is lost while you're away.
- **Resume is on by default.** `up` reattaches each agent to its recorded
  conversation, so re-running the pipeline continues context instead of starting
  cold. Use `agentainer remove-session` to wipe state and start fresh.

---

### Reference commands

| Goal | Command |
| --- | --- |
| Open the human mailbox | `./agentainer user available -c <cfg>` |
| Send a task to the hub | `./agentainer send --to orchestrator "..." -c <cfg>` |
| Watch agents / log / inbox | `./agentainer status` · `logs -f` · `inbox <agent>` `-c <cfg>` |
| Read the answer | `./agentainer user inbox -c <cfg>` |
| Close the mailbox | `./agentainer user away -c <cfg>` |
| Validate the graph | `./agentainer validate -c <cfg>` |

*All behaviour described here is implemented in `lib/mail.py` (`route_outbound`,
`on_stop`, `deliver_to_user`, `set_user_available`) and `lib/cli.py`
(`cmd_user`, `cmd_send`). The end-to-end loop was verified against the live
`chy3` model.*

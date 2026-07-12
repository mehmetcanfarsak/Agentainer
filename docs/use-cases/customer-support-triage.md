# Use case: customer-support ticket triage & routing

A concrete, end-to-end walkthrough of the shipped
`examples/customer-support-triage.yaml` swarm — a four-agent support desk where a
single **intake** hub classifies every incoming ticket and routes it to the right
specialist (**billing**, **technical**) or a human-facing **escalation** handler
for angry / churn-risk cases. It's the canonical "triage → route → resolve →
report back" loop, wired entirely through Agentainer's file-based mail model.

Everything below is based on the actual contents of
`examples/customer-support-triage.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The problem it solves

Support inboxes drown teams. Tickets arrive in one stream — billing questions,
login bugs, and an enraged "I'm cancelling!" all mixed together — and the wrong
person picks them up: a billing agent answers a technical issue badly, two agents
reply to the same angry customer with conflicting tones, or a churn-risk message
sits unnoticed for a day.

This swarm fixes routing *structurally*, not by trusting humans to remember the
rules:

- **One front door.** Every ticket enters through `intake` and is classified
  before anyone acts. No ticket reaches a specialist that shouldn't.
- **Specialists never face the customer.** `billing` and `technical` talk only to
  `intake`, so they can't accidentally email the customer. The customer only ever
  sees `intake`'s voice (or `escalation`'s, for hard cases) — the handoff is
  invisible.
- **Angry / churn-risk is owned, not lost.** Anything emotional or risky goes to a
  senior `escalation` handler that may write to the human directly, with empathy
  and a concrete remedy.
- **The human stays in the loop.** You drop tickets in with `send --to intake`
  and read resolutions that come back to the `user` mailbox.

**Who uses it:** a small team that wants consistent triage without a human
dispatcher; an on-call rotation that wants technical vs. billing paged to the
right place; anyone prototyping an AI support front-desk on top of existing agents.

---

## 2. The topology

```
                 ticket
   user ──────────────────────▶ intake (hub / classifier)
                                 │   │   │
                 ┌───────────────┘   │   └───────────────┐
                 ▼                   ▼                    ▼
            billing             technical            escalation
                 │                   │                 │  (angry / churn)
                 └───── report ──────┴─────────────────┘
                              back to intake
                                 │
                                 ▼ reply (relayed)
                               user
```

Four agents, one directed flow:

1. **`user` → `intake`** — you send a ticket; intake reads it first.
2. **`intake` classifies** into `billing` / `technical` / `escalation` / `trivial`.
3. **`intake` → `billing` | `technical` | `escalation`** — the ticket is routed to
   the one specialist who can own it. A `trivial` ticket is answered in place.
4. **`billing` / `technical` → `intake`** — the specialist reports the resolution
   *back to intake only* (never to the customer). `technical` may ask intake for
   clarification first.
5. **`intake` → `user`** — intake relays the resolution to the customer in plain,
   warm language. For hard cases, **`escalation` → `user`** replies directly.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. A `billing` agent that tries to mail `user` is bounced back as
a `system` message and filed in `failed/` (see §7).

### The ACL, stated plainly

| agent       | may talk to                      | may reach `user`? |
|-------------|----------------------------------|-------------------|
| `intake`    | `billing`, `technical`, `escalation`, `user` | **yes** (relays all answers) |
| `billing`   | `intake`                         | no                |
| `technical` | `intake`                         | no                |
| `escalation`| `intake`, `user`                  | **yes** (hard cases) |

Only `intake` and `escalation` may write to the human — that keeps the
customer-facing surface to two deliberate voices and stops a specialist from
emailing a customer by accident.

---

## 3. The config, explained

Here is `examples/customer-support-triage.yaml` in full:

```yaml
# 🎫 Customer-support triage -- an INTAKE hub classifies incoming tickets and
# routes them to the right specialist: billing, technical, or a human-facing
# escalation handler for angry / churn-risk cases.
#
#   cp examples/customer-support-triage.yaml my-support.yaml
#   agentainer up   -c my-support.yaml
#   agentainer send -c my-support.yaml --to intake "I was double-charged $49 this month and I'm furious."
#   agentainer down -c my-support.yaml
#
#   user
#    │ ticket
#    ▼
# intake ───────┬───────────┬────────────┐
#  (hub)        │           │            │
#            billing    technical    escalation ──▶ user
#               │           │            │  (angry / churn)
#               └──── back to intake ────┘
#
# Key-free: every `command` launches a real coding CLI. For a demo with NO API
# keys, swap each `command` for a mock bash loop and set `capture: none`.

swarm:
  name: support
  root: ./support-workspace

defaults:
  can_talk_to: []           # tightened per agent below

agents:
  - name: intake
    type: claude
    can_talk_to: [billing, technical, escalation, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are INTAKE -- the front desk of a customer-support team and the only
      hub. Every ticket the customer (user) sends lands in your inbox. For each
      one you do exactly two things: (1) CLASSIFY it, and (2) either answer it
      yourself or ROUTE it to the right specialist.
      ... (classify billing / technical / escalation / trivial; relay answers;
           MAILBOX reminder) ...

  - name: billing
    type: codex
    can_talk_to: [intake]
    command: "codex --yolo"
    role: |
      You are the BILLING specialist. ... resolve charges/refunds/invoices/
      subscriptions; report back to outbox/intake/ only.

  - name: technical
    type: codex
    can_talk_to: [intake]
    command: "codex --yolo"
    role: |
      You are the TECHNICAL specialist. ... diagnose bugs/errors/outages/logins;
      ask intake for clarification; report to outbox/intake/ only.

  - name: escalation
    type: claude
    can_talk_to: [intake, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the ESCALATION handler -- senior owner of angry / churn-risk /
      legal / security tickets; may reply to outbox/user/ directly.
```

(Read the file for the full role text — each is concrete and self-contained so a
dummy model can run it.)

Field by field:

### `swarm`
- **`name: support`** — the swarm's name (shows up in `status`, logs, sessions).
- **`root: ./support-workspace`** — parent directory for each agent's working
  directory and mailboxes. Every agent gets `support-workspace/<name>/` as its
  workdir (created on `up`), with its mailbox folders alongside. Orchestrator
  state goes under `support-workspace/.agentainer/` (never commit it).

### `defaults`
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent states
  its own list explicitly, so the empty default is just a safe floor — a typo'd
  agent can never silently reach the customer.

### `intake` (type: `claude`)
- **`can_talk_to: [billing, technical, escalation, user]`** — the hub. It is the
  *only* agent that can both fan out to all three specialists **and** talk to
  `user`, so the customer-facing funnel has one entry and one relay voice.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the classifier + relay logic: classify each ticket, route or
  answer, and relay specialist resolutions to `user`. It ends with the standard
  **MAILBOX reminder** (read `inbox/`; write `outbox/<name>/`; move to `read/`).
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `billing` (type: `codex`)
- **`can_talk_to: [intake]`** — a leaf. It can only report back to intake, never
  to the customer. This is the structural guard that stops billing from emailing a
  customer directly.
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `technical` (type: `codex`)
- **`can_talk_to: [intake]`** — also a leaf, same shape as billing. It may *ask*
  intake for clarification (intake is on its list), but never the customer.

### `escalation` (type: `claude`)
- **`can_talk_to: [intake, user]`** — the second human-facing agent. It owns hard
  tickets and may reply to `user` directly; coordination with specialists still
  goes through `intake`.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.

### What's *not* in this config
- **No `periodically_ping_seconds`.** The desk is purely event-driven off real
  tickets — nothing self-starts a turn. (If you wanted intake to nudge a quiet
  specialist, add `periodically_ping_seconds: 300` to that agent; see §7.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — a reply addressed to you is *held* (never bounced) until you flip it
  on.
- **No `telegram:` bridge.** Off by default. Add one to mirror tickets/answers to
  a chat (see `docs/telegram-bridge.md`).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/customer-support-triage.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (the `type`/`command`
   match passes, so no mismatch warning here).
2. Creates the runtime dirs (`support-workspace/.agentainer/…`: log, queue, run,
   sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The contact card in each
   `about.md` *is* the ACL made visible: `intake` gets `outbox/billing/`,
   `outbox/technical/`, `outbox/escalation/`, `outbox/user/`; `billing`/`technical`
   get only `outbox/intake/`; `escalation` gets `outbox/intake/` and `outbox/user/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `intake` and
   `escalation`, the Codex `notify` hook for `billing` and `technical`.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified"), so `intake` sits ready for your first ticket.
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the desk.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'support' is up with 4 agent(s)
:: attach with:  tmux attach -t <intake-session>
:: you can use the UI with:  agentainer serve -c examples/customer-support-triage.yaml --port 8000
```

The `serve` line launches the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). **By default it binds `127.0.0.1`** — never
expose it on `0.0.0.0` without a `--token` (see `docs/ui-guide.md` and
CLAUDE.md §18). Drop any `--host`/`--token` to keep the safe loopback bind.

> **Key-free demo:** swap each `command:` for a mock bash loop and set `capture:
> none` per agent, and you can watch a live ticket route through the whole desk
> with **no API keys** — the classification and routing mechanics are identical.

---

## 5. Drive it as the human

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* replies as mail (rather than have them held),
turn yourself available first:

```bash
./agentainer user available -c examples/customer-support-triage.yaml
```

This rewrites the `user` contact card in `intake`'s and `escalation`'s
`outbox/user/about.md` to `Status: available`, so they see you're reachable.
(While away, mail to you is *held* and the sender gets a `system` ack — nothing
bounces.)

Now drop a ticket into the desk, addressed to `intake`:

```bash
./agentainer send --to intake "I was double-charged $49 this month and I'm furious."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped
`From: user` with a fresh id, enqueued for `intake`, then — because its inbox was
empty — **released into `inbox/`** and `intake` is **nudged** (the protocol is
re-pasted into its pane, including the allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the desk advance one turn at a time. Each arrow
is a `stop → sweep → route → release → nudge` cycle:

1. **intake receives the ticket.** It reads `inbox/`, classifies it (here:
   *billing* + anger → it may route to `billing` and copy `escalation`, or judge
   it an escalation outright), and writes a delegation into `outbox/billing/`. On
   stop, the orchestrator sweeps that outbox, routes to `billing`, and nudges it.
2. **billing resolves.** It reads its inbox, works the charge, writes the
   resolution into `outbox/intake/`. On stop, that routes back to `intake`.
3. **intake relays.** It reads `billing`'s report and relays a warm resolution to
   `outbox/user/`. On stop, that's delivered to your `user` mailbox (see it with
   `agentainer user inbox`, or in the UI).

If the ticket was angry / churn-risk instead, `intake` routes to `escalation`,
which replies to `user` directly. You never relay anything by hand — the
orchestrator releases exactly one inbox message at a time, off each agent's turn
completion.

> **Try each flavour:** send a technical bug ("the API returns 500 on /login"),
> a billing question ("can I get a refund for the duplicate charge?"), and an
> angry one ("I'm cancelling, this is the third time!") — watch `intake` pick the
> right lane every time.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/customer-support-triage.yaml
```

```
swarm: support   root: ./support-workspace
  intake (claude) up idle queue=0 unread=1 talks=billing, technical, escalation, user
  billing (codex)  up idle queue=0 unread=0 talks=intake
  technical (codex) up idle queue=0 unread=0 talks=intake
  escalation (claude) up idle queue=0 unread=0 talks=intake, user
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/customer-support-triage.yaml          # whole swarm, last 20
./agentainer logs -c examples/customer-support-triage.yaml -f        # follow live
./agentainer logs billing -c examples/customer-support-triage.yaml  # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event. A `bounce` here is usually a specialist that
tried to reach `user` and was corrected by the ACL.

**A specific inbox** — what an agent is currently looking at:

```bash
./agentainer inbox intake -c examples/customer-support-triage.yaml
```

Prints the one released message (headers + body), or `intake: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue intake -c examples/customer-support-triage.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach intake -c examples/customer-support-triage.yaml
```

Detach with the usual tmux `Ctrl-b d`. Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.

---

## 7. Tips & footguns

- **Only `intake` and `escalation` may reach `user`.** This is the deliberate
  customer-facing funnel. If `billing` or `technical` tries to reply to the
  customer, the orchestrator **bounces** it (ACL) and drops a `system` note in
  that agent's inbox explaining who it *can* message — the model self-corrects
  in-band. The structural guard (leaf agents list only `intake`) makes the mistake
  nearly impossible in the first place.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch (e.g. a `claude` agent whose `command`
  doesn't launch Claude) means completion never triggers and the agent pins
  "busy" forever. `status` showing an agent `busy` for a long time with `unread`
  mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  desk: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **Force-idle if a hook-captured agent's turn never registers.** If `billing` or
  `technical` (Codex) or `intake`/`escalation` (Claude) seems pinned, nudge the
  state along:
  ```bash
  ./agentainer idle billing -c examples/customer-support-triage.yaml
  ```

- **Keep `user` available when you want answers.** Flip yourself available before
  sending if you want resolutions delivered as mail rather than held.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down -c examples/customer-support-triage.yaml
  ./agentainer remove-session -c examples/customer-support-triage.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

---

## 8. Customize

- **Swap models.** Change an agent's `type` and matching `command`, e.g. make
  `technical` a `gemini` agent: `type: gemini` + `command: "gemini --yolo"` and
  add `capture: pane` (Gemini has no completion hook — the orchestrator polls its
  pane). Keep `type` and `command` consistent or `up` refuses with a mismatch
  error.

- **Add a `fraud` agent.** Drop in a specialist for suspicious charges / account
  takeover:
  ```yaml
  - name: fraud
    type: codex
    can_talk_to: [intake]
    command: "codex --yolo"
    role: |
      You are the FRAUD specialist. Intake routes suspected fraud / unauthorized
      charges / account-takeover. Triage severity, recommend lock/refund, and
      report every finding back to outbox/intake/ only.
  ```
  Then add `fraud` to `intake`'s `can_talk_to` so it can route there.

- **Tune the ACL.** Want `technical` to ask `billing` about payment-gated
  features? Add `billing` to `technical`'s `can_talk_to` (and `technical` to
  `billing`'s). Remember: any name added to a `can_talk_to` that includes `user`
  widens the customer-facing surface — keep that narrow on purpose.

- **Keep a quiet specialist warm.** Add a periodic nudge so a stalled escalation
  can't go silent:
  ```yaml
  - name: escalation
    # ...
    periodically_ping_seconds: 600
    periodically_ping_message: "Any open high-risk tickets still need a reply?"
  ```

- **Bridge to chat.** Add a `telegram:` block (off by default) to mirror `user`
  mail and agent answers into a Telegram chat so a human on-call stays reachable
  even while `user` is "away". See `docs/telegram-bridge.md`.

- **Bind the UI safely.** `agentainer serve` binds `127.0.0.1` by default. To
  reach it remotely you must pass `--token` (and, if you really must, `--host
  0.0.0.0`) — never expose the control plane without a token. See
  `docs/ui-guide.md`.

---

### See also

- [`../getting-started.md`](../getting-started.md) — install and first swarm.
- [`../mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`../sessions-and-resume.md`](../sessions-and-resume.md) — conversations resume
  by default; how to inspect/reset.
- [`../delegation-pipeline.md`](../delegation-pipeline.md) — the hub-and-spoke
  delegation pattern this swarm is built on.
- [`../multi-llm-swarm.md`](../multi-llm-swarm.md) — mixing Claude / Codex / Gemini
  / Hermes in one swarm (and the turn-detection differences).
- `examples/customer-support-triage.yaml` — the config this walkthrough uses.

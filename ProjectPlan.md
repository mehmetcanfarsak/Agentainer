# Agentainer v2 — Project Plan

> The complete design record for **Agentainer v2**: a zero-dependency,
> mail-based multi-agent orchestrator built to work with *nearly every*
> tool-calling LLM — including weak ones. This document captures every decision
> made during design, the reasoning behind each, the invariants we must not
> regress, what we reuse from v1 vs. rewrite, and the phased build order.
>
> Companion document: `/root/AgentSwarm/docs/PROJECT-DOCUMENTATION.md` (the full v1 record). Read
> its §13 ("Building v2") alongside this plan — the invariants there are binding.

---

## Table of contents

1. [Why v2 exists (the v1 failure)](#1-why-v2-exists-the-v1-failure)
2. [The core idea: mail-inbox methodology](#2-the-core-idea-mail-inbox-methodology)
3. [Governing design principles](#3-governing-design-principles)
4. [The agent's world: four folders, two verbs](#4-the-agents-world-four-folders-two-verbs)
5. [Folder layout & ownership](#5-folder-layout--ownership)
6. [Message lifecycle (send → route → deliver → read)](#6-message-lifecycle-send--route--deliver--read)
7. [One-at-a-time delivery & read-state](#7-one-at-a-time-delivery--read-state)
8. [Turn-completion detection — the system clock](#8-turn-completion-detection--the-system-clock)
9. [The nudge & re-injecting the protocol](#9-the-nudge--re-injecting-the-protocol)
10. [Periodic pings (agent cron)](#10-periodic-pings-agent-cron)
11. [Virtual participants: `system` and `user`](#11-virtual-participants-system-and-user)
12. [Access control (`can_talk_to`) — cooperative, not isolation](#12-access-control-can_talk_to--cooperative-not-isolation)
13. [Agent definition & bootstrapping (first prompt, trust modals)](#13-agent-definition--bootstrapping)
14. [Runaway-loop guard](#14-runaway-loop-guard)
15. [`agentainer.yaml` — the config file](#15-agentaineryaml--the-config-file)
16. [Mail path customization & shared workspaces](#16-mail-path-customization--shared-workspaces)
17. [The orchestrator as a daemon](#17-the-orchestrator-as-a-daemon)
18. [The UI / control plane](#18-the-ui--control-plane)
19. [Concurrency & locking](#19-concurrency--locking)
20. [Durable event log](#20-durable-event-log)
21. [Resume & persistence](#21-resume--persistence)
22. [Branding: retire "swarm"](#22-branding-retire-swarm)
23. [Reuse vs. rewrite](#23-reuse-vs-rewrite)
24. [Invariants we must not regress](#24-invariants-we-must-not-regress)
25. [Testing & coverage](#25-testing--coverage)
26. [Build phases](#26-build-phases)
27. [Risks & open questions](#27-risks--open-questions)
28. [v2 acceptance checklist](#28-v2-acceptance-checklist)
29. [Decisions log](#29-decisions-log)
30. [Glossary](#30-glossary)

---

## 1. Why v2 exists (the v1 failure)

Agentainer v1 (formerly AgentSwarm) is a working, 100%-covered, zero-dependency
multi-agent orchestrator. It launches AI coding-agent CLIs (Claude Code, Codex,
Gemini, Hermes) each in its own tmux session + working directory, defined by one
YAML file, and lets them message one another where a `can_talk_to` ACL permits.

**It failed on one thing: how agents exchange messages.** v1 asked each agent to
emit a precise tagged XML envelope **inside its own prose output** (e.g.
`<message to="bob">...</message>`), and the orchestrator scraped that back out of
a fullscreen-TUI pane. This fights the model on two fronts simultaneously:

- **Structured generation in prose** — many LLMs (especially weaker ones) fail to
  emit the exact envelope: they paraphrase it, wrap it in code fences, explain it
  instead of producing it, or drift from the schema.
- **Brittle pane capture** — reading structured data back out of an
  alternate-screen TUI with no scrollback is inherently fragile.

The net result: message delivery was unreliable across the range of LLMs we want
to support. **v2 replaces the messaging layer entirely** while keeping the parts
of v1 that work.

---

## 2. The core idea: mail-inbox methodology

Agents communicate like people using email.

- Each agent has an **inbox folder** in its working area.
- Its initial prompt tells it to check its inbox.
- A background **orchestrator** (its own tmux session) watches every agent.
- When an agent **stops** (finishes its turn) **and has unread mail**, the
  orchestrator pastes a short reminder: *"You have unread mail — read it, do
  what it asks, then move it to `read/`."* The agent receives that prompt and
  gets to work.

**Why this is the right fix:** receiving a message becomes a **file read**, and
reading files is the single most reliable thing every coding-agent LLM does.
There is no structured output to parse on the receive side, and no pane-scraping.
The mental model — an inbox you check, mail you reply to — is deeply
in-distribution for any pretrained model.

The one thing the inbox model does **not** solve on its own is *sending* — that is
addressed in §6 (send by writing a file into a per-recipient outbox folder, so
the send side is also just a file write, never structured prose).

---

## 3. Governing design principles

1. **Designed for dummy models.** The system must work on *nearly all*
   tool-calling LLMs, including weak ones. The only capability we require of an
   agent is: **it can read files and write files.** Every additional thing we ask
   a model to remember, format, or track is a failure point — so we push all
   bookkeeping into the orchestrator (deterministic code), and leave the model
   only "read a file / write a file."
2. **Zero runtime dependencies, forever.** Python 3 + bash + tmux only. PyYAML
   optional with a bundled fallback parser. This includes the UI: it is served by
   stdlib `http.server` with a single static vanilla-JS page — no framework, no
   build step. Keep the dedicated CI job proving the no-PyYAML path.
3. **The orchestrator does the hard, deterministic work; the model does the
   easy, fuzzy work.** ACL, IDs, threading, read-state, queueing, retries,
   routing, the durable log, availability — all orchestrator. Natural-language
   message bodies — the model.
4. **The model is always *told* its paths; it never assumes them.** Every nudge
   and first-prompt states the agent's exact inbox/outbox/read paths, so custom
   mail directories and shared-workspace prefixing are invisible to the model.
5. **Re-inject the protocol every time.** Weak models don't retain a protocol
   across turns, so each nudge is self-contained: what to read, how to reply, and
   who it is allowed to message.
6. **Correctness must never depend on the model doing housekeeping.** Moving mail
   to `read/`, deleting files — all best-effort. The orchestrator owns the
   authoritative state and has fallbacks so a model that forgets can't wedge or
   loop the system.
7. **Feedback loops in-band.** Errors (e.g. a disallowed recipient) come back as
   *mail*, so the model needs no new concept to understand and self-correct.
8. **100% line coverage remains a release gate**, driven entirely by mock agents
   (no API keys, nothing to pay for). The file-based model makes this *easier*
   than v1's pane-scraping.
9. **Keep the discovery layer** (`llms.txt`, README FAQ, quotable intro, keyword
   set, absolute image URLs) — it is a feature, not decoration.

---

## 4. The agent's world: four folders, two verbs

From the agent's point of view, its entire universe is four folders and two
actions. Nothing else.

**Two verbs:**
- **Read** a file (its inbox).
- **Write** a file (an outbox).

**Four folders the agent touches:**
- `inbox/` — read the message here (there is only ever **one** at a time).
- `outbox/<name>/` — write a file here to send a message to `<name>`; read
  `<name>/about.md` to see who they are and whether they're available.
- `read/` — move a message here once you've handled it (best-effort "I processed
  it" signal; see §7).
- `sent/` — your own record of messages that were successfully delivered
  (orchestrator moves them here; read-only to the agent).

Folders the agent must **never** touch (orchestrator-owned): staging/queue,
`done`/archive, `failed`, and the event log.

---

## 5. Folder layout & ownership

Per agent (paths configurable — see §16; shown here with defaults inside the
workspace):

```
<workspace>/
  inbox/                 # the ONE current unread message (orchestrator releases one at a time)
  read/                  # messages the agent moved here after processing (best-effort receipt)
  outbox/
    <recipientA>/
      about.md           # orchestrator-maintained: name, description, availability
      <message files>    # agent writes here to send; orchestrator sweeps on stop
    <recipientB>/
      about.md
  sent/                  # successfully delivered outgoing mail (orchestrator moves here)
  failed/                # outgoing mail that failed ACL/validation (orchestrator moves here)
```

Orchestrator-private runtime state (never in the workspace the agent edits; lives
under the run root, see §20):

```
.agentainer/
  logs/<agent>.jsonl     # per-agent durable event log
  logs/agentainer.jsonl  # global durable event log (source of truth for history)
  queue/<agent>/         # pending messages not yet released into inbox/ (one-at-a-time buffer)
  run/<agent>.turn.json  # turn state (delivered/completed/idle/busy/since)
  sessions.yaml          # recorded conversation ids for `up --resume`
```

**`outbox/<name>/about.md`** is the recipient's live contact card, written and
refreshed by the orchestrator:

```
Name: bob
Role: Backend engineer — owns the API service
Status: available            # or: working — your message will be delivered when he's free
```

The **presence of `outbox/<name>/` is itself the ACL**: the orchestrator only
creates the folder if `<name>` is in this agent's `can_talk_to`. So "who can I
message?" is answered by `ls outbox/`, and a model literally cannot address
someone it has no folder for.

---

## 6. Message lifecycle (send → route → deliver → read)

**Send (by the agent):** the agent writes a file (natural-language body) into
`outbox/<recipient>/`. The recipient is encoded in the **folder path**, never
parsed from the file contents — so there is no structured output and no shell
quoting to get wrong.

**Pickup (orchestrator, on stop):** the orchestrator sweeps every agent's
`outbox/*/` **at the moment it detects that agent has stopped** (see §8). Because
the agent is stopped, all its file writes are flushed and none are in flight —
this eliminates the partial-write race for free (no polling, no stability
heuristic needed).

**Route + ACL check:**
- **Allowed** (recipient in `can_talk_to`): the orchestrator stamps a header
  (`from`, `to`, `id`, `timestamp`, optional `re:` for replies), enqueues the
  message for the recipient (§7), logs the event, and **moves the sender's file
  to `sent/`** — the move *is* the delivery confirmation (no receipt mail needed
  on success).
- **Disallowed / unknown recipient / user unavailable**: see below.

**Body format:** the body is plain natural language written by the model. The
orchestrator prepends a small header it wrote itself when delivering, e.g.:

```
From: alice
To: bob
Id: m-0007
Time: 2026-07-11T14:03:00Z
---
<the sender's natural-language message>
```

The model never has to produce that header — it only writes prose.

**Failure / bounce:** if the recipient is not permitted, the orchestrator drops a
`system` mail into the *sender's* inbox — *"Your message to carol couldn't be
sent — you can message: alice, bob."* — and moves the outgoing file to `failed/`.
The sender self-corrects on its next turn. (See §11 for the `user`-unavailable
case, which **holds** rather than bounces.)

**Reply:** replies are just new messages — the sender states "please reply when
done" in the body, and the recipient writes a file into `outbox/<sender>/`.
Message IDs let the log thread request→reply; the recipient may reference the
`Id` it was shown (copying, never recalling). No dedicated reply subsystem.

---

## 7. One-at-a-time delivery & read-state

**Deliver one message at a time.** A weak model handed a pile of unread mail gets
confused. So we control what is *physically in the folder*:

- The orchestrator keeps its own queue per agent (`.agentainer/queue/<agent>/`).
- **`inbox/` only ever contains a single message file.**
- Flow: release one → nudge → agent handles it → agent stops → orchestrator
  processes stop (sweep outbox, mark this message handled) → release the next →
  nudge again.

Even a model that runs `read inbox/*` sees exactly one message.

**Read-state is orchestrator-owned; the `read/` move is a best-effort receipt.**

- The authoritative "has this been seen/handled" state lives in the orchestrator,
  keyed by message id — **not** in filesystem presence and **not** in the model's
  actions.
- **Primary signal:** the agent moves the handled message from `inbox/` to
  `read/`. This is the "I processed it" signal and drives **read receipts**: when
  a message lands in `read/`, the orchestrator marks the *sender's* `sent/` copy
  as read (or drops a small `system` note), so the sender can distinguish
  "processed" from "ignored."
- **Fallback (guarantees liveness):** if the model forgets to move the file, the
  orchestrator auto-archives after N presentations to its private `done`/archive
  and advances the queue. Worst case: the *sender doesn't get a read receipt*.
  The swarm never loops or wedges. **Read receipts are best-effort; liveness is
  guaranteed.**

This split is the key rule: moving to `read/` (like moving to `done/`) is
**optional** for correctness — it only improves receipt fidelity.

---

## 8. Turn-completion detection — the system clock

Reliably knowing the exact moment an agent **finishes its turn and goes idle** is
the single signal the entire reactive design runs on:

- the **nudge** only pastes when the agent is idle,
- **one-at-a-time release** advances the queue on stop,
- **outbox pickup** happens on stop,
- the **periodic ping** only fires when idle.

If this detector is wrong the failures are silent: miss a stop and the agent sits
on unread mail forever (looks hung); fire a false stop and we paste into a live
TUI and corrupt the turn.

**Detection is per agent `type` (ported from v1):**

| Type | Mechanism |
|------|-----------|
| `claude` | **Stop hook** installed into `<workdir>/.claude/settings.json` |
| `codex` | **`notify`** program configured in `<workdir>/.codex/config.toml` |
| `gemini` / `hermes` | **Pane polling** (`capture-pane` + readiness heuristic) |

**The load-bearing deadlock (carried from v1 §13.3):** if an agent's `command`
launches a different CLI than its `type` implies, the completion event never
fires, the agent pins "busy"/never-idle forever, and no mail flows to or from it.

**v2 additions:**
- **Detect/prevent `type`↔`command` mismatch** at `up` (validate the command
  against the declared type; fail loudly rather than deadlock silently).
- **Per-agent health probe** so a *silent-but-alive* agent is surfaced, not only a
  *dead* session (the v1 supervisor can only catch the latter).

**Reused v1 code (do not rewrite):** `install_claude_hook`, `install_codex_hook`,
the completion handler (`on_turn_finished` → becomes the trigger for
outbox-sweep + inbox-release), the paste stack (`paste_into`, `paste_score`,
`wait_until_ready`), and `capture_pane` (also feeds the UI terminal view).

---

## 9. The nudge & re-injecting the protocol

The nudge is the fixed reminder pasted into an idle agent that has mail waiting.
Because it is a **constant, simple string**, the flaky paste-into-TUI problem is
minimized (and retryable next tick if it fails).

Every nudge is **self-contained** and re-teaches the protocol — never assume the
model remembers it:

```
You have a new message in ./inbox. Read it and do what it asks.
When you're done, move that file to ./read.
To send a message, write a file into ./outbox/<name>/ (read ./outbox/<name>/about.md
to see who they are and whether they're available).
You can message: alice, bob.
```

Listing the **allowed recipients** in the nudge doubles as ACL documentation and
stops a weak model from inventing a name it isn't permitted to reach.

The nudge is delivered via the reused paste stack; if a paste fails, the
orchestrator simply retries on the next supervisor tick — idempotent by
construction.

---

## 10. Periodic pings (agent cron)

Some agents have standing, time-based duties (e.g. a news reporter should do its
rounds regularly) even with an empty inbox. Two **optional** per-agent fields:

> **Superseded (D26).** The original fixed-cadence fields
> `periodically_ping_seconds` / `periodically_ping_message` have been **removed**
> in favour of the richer cron `pings:` list (see D26); a single repeating ping is
> now just one `pings` rule. The guards below are unchanged.

```yaml
agents:
  reporter:
    pings:
      - cron: "*/30 * * * *"    # every 30 minutes
        message: "Check the news wires and post any updates."
```

**Implementation:** a periodic ping is delivered as a message from `system` into
the agent's own inbox — reusing the exact one-at-a-time pipeline. The model's
experience is identical to receiving normal mail; nothing new to learn.

**Guards (supervisor):**
1. **Only when idle** — never interrupt a turn in progress.
2. **No pile-up** — do not inject a new periodic ping if an unhandled one is
   already queued (a slow agent must not accumulate ten identical pings).
3. **Cadence = minimum, not a hard cron tick** — "N seconds since it last acted,
   and idle now." If busy at the interval, defer until free.

Real mail always takes priority; the periodic ping matters only when the inbox
would otherwise be empty. Net effect: Agentainer is a lightweight **mailbox +
cron** for agents, both on one code path.

---

## 11. Virtual participants: `system` and `user`

Two reserved, virtual senders — no tmux session, no workdir, no hooks. Both are
just addresses on the mail bus. `system` and `user` are **reserved names**; the
config validator must reject any real agent claiming them.

**`system`** — the orchestrator's own voice: nudges, periodic pings, bounces,
delivery acknowledgements, read receipts.

**`user`** — the human operator, as a first-class mailbox reachable from the UI.
This unifies human-in-the-loop with the same mail machinery — approvals, "ask me
before you deploy," status questions all ride the existing bus with no special
subsystem.

- **No process.** `user`'s "terminal" is the UI; its inbox is a UI panel backed
  by the same queue + JSONL, so full history is free.
- **"Send a prompt from the UI" and "mail from the user" are the same
  operation**, differing only in sender identity (`user` vs `system`) — one code
  path.
- **Two gates on the `user` mailbox:**
  - **ACL (static):** an agent may mail `user` only if `user` is in its
    `can_talk_to`. Opt-in per agent so agents can't spam the human.
  - **Availability (dynamic, UI toggle, default *unavailable*):** whether the
    human is currently accepting mail.
- **Availability behavior = hold, not bounce.** When `user` is unavailable, an
  agent's send still succeeds into the user's queue, and the sender receives an
  immediate `system` acknowledgement: *"Delivered — the user is away and may
  respond later."* No mail lost, sender not blocked waiting for an instant reply.
  When the human flips to *available* in the UI, held mail flows into the inbox
  panel. `outbox/user/about.md` reflects the live status.
- **user → agent is operator privilege:** the UI may message any agent (the human
  runs the swarm), but the UI should surface which agents listed `user` ("expect
  to hear from you") vs. which are being reached cold.
- **The human is the one participant not auto-nudged.** So the UI must surface
  incoming `user` mail (unread badge / highlight / optional sound) — that is the
  human's version of the nudge.

---

## 12. Access control (`can_talk_to`) — cooperative, not isolation

`can_talk_to` is a strict whitelist (`"*"` = everyone). In v2 it is enforced by
the orchestrator on every routed message (outbox sweep → route), and represented
physically by which `outbox/<name>/` folders exist in each agent's area.

**Honest limitation (accepted decision):** in v1 the ACL was a hard boundary
because agents could only deliver *through* the orchestrator. In v2, agents are
coding agents with filesystem access — nothing at the OS level stops a confused
or rogue agent from writing directly into another agent's `inbox/` and bypassing
`outbox/` and the ACL. Therefore:

> **The `can_talk_to` ACL is enforced for well-behaved agents that route through
> `outbox/`. It is NOT an OS-level isolation boundary.**

We **accept this and move on.** It is documented plainly, with no false guarantee.
If hard isolation is ever required, it must come from OS-level means (separate
users/permissions per workspace), not from the mail model.

---

## 13. Agent definition & bootstrapping

Mail is only transport. Each agent also needs a **standing role** and a correct
**first prompt**, and must survive the trust-modal footgun.

- **Role field.** `agentainer.yaml` gives each agent standing instructions /
  persona (v1's `first_prompt`), e.g. `role:` / `prompt:` — who it is and what
  its job is. Periodic pings and mail sit on top of this base role.
- **First-prompt template.** The very first message at `up` bootstraps the
  protocol: your inbox is *here*, send by writing to *here*, move handled mail to
  `read/`, you can message *these* agents. This is the one-time full protocol
  teach (the nudge re-injects the essentials thereafter).
- **Standby wrapper (D25).** The first prompt is the agent's `role` wrapped with
  an explicit STANDBY notice: *no task has been assigned yet, do NOT send any
  mail, you will be notified (a message lands in `inbox/`) when your first real
  task arrives.* This stops a proactive model from mailing its peers the instant
  the swarm comes up, before any human-assigned task exists. The human delivers
  the first task via `agentainer send`, and the normal nudge is the notification.
  Implemented in `mail.standby_prompt` and used by `launch_agent_full`.
- **Trust-modal handling (carried from v1 §13.3).** If "trust this directory?"
  swallows the first prompt, the agent never learns the protocol. Port
  `pretrust_claude_dir` and make trust-handling a **pluggable per-type step**, not
  special-cased code, so every new agent type gets the equivalent.

---

## 14. Runaway-loop guard

Two agents can auto-reply forever ("thanks!" → "you're welcome!"). The
one-at-a-time + stop-triggered design rate-limits this naturally, but that is not
a guarantee. Add an explicit **rate limit / max-auto-exchanges cap** (per pair,
per time window) so a bad prompt cannot spin the swarm indefinitely. Cheap
insurance; enforced by the orchestrator. (Conceptually the successor to v1's
hop-guard on auto-forwarding.)

---

## 15. `agentainer.yaml` — the config file

Renamed from v1's `agents.yaml`/`agents.example.yaml` to **`agentainer.yaml`**.
Illustrative schema (final field names to be settled during P1):

```yaml
defaults:
  mail_dir: ./                 # optional; base for inbox/outbox/read/sent (default: workspace)
  supervise_interval_ms: 15000
  resume_args: [...]           # falls through to agents (as in v1)

agents:
  alice:
    type: claude               # claude | codex | gemini | hermes
    command: "claude ..."      # validated against `type` (mismatch = hard error, see §8)
    workspace: ./work/alice
    role: "Coordinator. Break work down and delegate."   # standing instructions / first prompt
    can_talk_to: [bob, user]   # strict whitelist; presence => outbox/<name>/ folder created
    mail_dir: ./mail/alice     # optional per-agent override
    # cron-scheduled pings (optional):
    pings:
      - cron: "*/30 * * * *"
        message: "Any progress to report? Reply, or stay quiet."

  bob:
    type: codex
    command: "codex ..."
    workspace: ./work/bob
    role: "Backend engineer. Owns the API service."
    can_talk_to: [alice]

# UI / control plane (optional; off unless configured):
ui:
  enabled: true
  bind: 127.0.0.1              # localhost-only by default
  port: 8787
  token: ""                    # required for any non-localhost bind
```

Notes:
- `broadcast` from v1 is **removed** (see decision D14).
- `user` and `system` are reserved and cannot be agent names.
- `package.json` remains the single source of truth for version, tag-verified at
  publish.

---

## 16. Mail path customization & shared workspaces

- **Customizable base path.** `mail_dir` (global default + per-agent override)
  sets where `inbox/`, `outbox/`, `read/`, `sent/` live. Default: inside the
  workspace. Reasons: keep mail out of a git-tracked workspace, or centralize all
  mailboxes for the UI to watch.
- **Shared-workspace collision handling.** If two agents share one workspace,
  plain `inbox/` would collide. Rule:
  - **Distinct workspace:** plain `inbox/`, `outbox/`, `read/`, `sent/`.
  - **Shared workspace (auto-detected):** namespace everything by agent —
    `alice-inbox/`, `alice-outbox/`, `bob-inbox/`, … so they cannot collide.
- **This is invisible to the model.** Because every nudge and first-prompt states
  the agent's *exact* paths (Principle 4), custom `mail_dir` and prefixing just
  work — the orchestrator computes the real path, the agent uses whatever it's
  handed. The model never hardcodes or assumes a path.

---

## 17. The orchestrator as a daemon

The orchestrator is a single long-running process in its own tmux session,
composed of three cooperating parts:

1. **Supervisor loop** (heartbeat) — reconciles stale-busy / dead agents, runs
   health probes, fires periodic pings, retries nudges. The event-driven core
   *needs* this; v1 shipped without it in 0.1.0 and had to add it in 0.1.5. Do
   not drop it.
2. **Mailroom** — reacts to stop events: sweep outbox → route + ACL → enqueue →
   move to `sent`/`failed` → release next inbox message → nudge; manages
   read-state, receipts, bounces, and the durable log.
3. **HTTP control plane** — the optional UI server (§18).

All three share one state store and one lock discipline (§19).

---

## 18. The UI / control plane

Served by the orchestrator. **Constraints first, because they govern everything:**

- **Zero-dependency:** stdlib `http.server`; **one static HTML page, vanilla JS,
  no framework, no build step.** The page polls a few JSON endpoints with
  `fetch`. Accepting a framework/build would break the "clone and it just works"
  promise.
- **Localhost-only by default; opt-in.** The UI is a *control plane* — it can
  start processes, edit config, and type into agent sessions (which may run
  `--dangerously-skip-permissions`/`--yolo`). An exposed port is remote code
  execution. So: **bind `127.0.0.1` by default, never `0.0.0.0`**; UI is opt-in
  (`ui.enabled` / `--ui`); headless CLI stays fully functional (CI + mock tests
  depend on it); any non-localhost bind requires an explicit token.

**Features, in build order (easiest → hardest):**

1. **Mail observability (P2).** Render what the orchestrator already owns:
   per-agent mailbox views (unread / read / sent / failed), a global message
   timeline, and a live who-talks-to-whom graph (from `from`/`to`/`id`). Highest
   value, lowest risk, no new state.
2. **Terminal view + send (P3).** Read: poll `capture-pane` and render a text
   snapshot (refresh every 1–2s over plain HTTP). Write: a text box routing
   through the existing paste path (a manual nudge), and "send this agent a
   message" = injecting a `system`/`user` mail into its inbox pipeline. A fully
   interactive terminal (xterm.js + websockets) is a later upgrade — it pulls in
   a JS dependency, so not v2.
3. **Dynamic reconcile (P4).** Edit `agentainer.yaml` and add/delete agents from
   the UI. This is declarative-config → running-state reconciliation:
   - **YAML stays the single source of truth**; the UI is an editor + "Apply"
     button and holds no separate state.
   - Apply = `validate` → **reconcile** (diff desired vs. running, apply only
     deltas), logging every action to the JSONL.
   - **Add agent** = create workdir, install per-type hook, start tmux session,
     create `outbox/<new>/` + `about.md` in every agent allowed to talk to it (and
     vice versa).
   - **Delete agent** = stop session, remove `outbox/<name>/` from everyone who
     could reach it, and give its in-flight mail a disposition (archive/drop).
   - **Sharp edges:** changing `type` while running, or revoking a `can_talk_to`
     with mail queued — validate hard, apply as explicit deltas.

4. **The `user` mailbox in the UI** — compose/read `user` mail, the
   availability toggle (default unavailable, §11), and unread notifications.

Because UI handlers call the **same core functions the CLI uses**, the tested core
stays covered; handlers remain a thin, mostly-untested shell (§25).

---

## 19. Concurrency & locking

v2 has more concurrent writers than v1: the supervisor, hook/notify callbacks,
and now the HTTP UI (injecting `user` mail, editing YAML, reconciling agents). All
state mutation must go through the same locks (`file_lock`/`pane_lock`, ported and
extended to the UI handlers) or hook-firing will race human clicks. This is a
first-class requirement, not an afterthought.

---

## 20. Durable event log

The JSONL event log remains the **source of truth for history** — fullscreen TUIs
keep no scrollback, so history cannot be recovered from panes. Every mail event is
logged: send, route, ACL-allow/deny, deliver, read, receipt, bounce, ping,
reconcile action. Per-agent (`.agentainer/logs/<agent>.jsonl`) + global
(`.agentainer/logs/agentainer.jsonl`). Never shipped to npm.

---

## 21. Resume & persistence

- **`up --resume` is an invariant.** Claude `--resume <id>`, Codex `resume <id>`,
  Gemini/Hermes always fresh with a warning. Recorded in `sessions.yaml`.
- **Mail durability is a free bonus of the file model.** Mailboxes and read-state
  are plain folders + JSONL on disk, so mail survives a crash/restart with no
  reconstruction — a strict improvement over v1. Conversation resume still needs
  `sessions.yaml`; keep both.

---

## 22. Branding: retire "swarm"

Use the brand — **Agentainer** — everywhere. "swarm" is retired.

| v1 | v2 |
|----|----|
| `agents.yaml` / `agents.example.yaml` | **`agentainer.yaml`** |
| `.swarm/` (runtime state) | **`.agentainer/`** |
| `SWARM_HOME` | **`AGENTAINER_HOME`** |
| `lib/swarm.py` | **`lib/agentainer.py`** (or `core.py`) |
| `swarm.jsonl` (event log) | **`agentainer.jsonl`** |
| "the swarm" (prose/docs) | **"the agents"** (reserve "Agentainer" for the product) |

Collective-noun decision: use **"the agents"** in user-facing text; do not coin a
new collective noun.

---

## 23. Reuse vs. rewrite

Concentrate risk where it already is; do not reintroduce solved problems.

**Reuse (careful port of proven, already-100%-covered v1 code):**
- Turn-completion detection: `install_claude_hook`, `install_codex_hook`, pane
  polling, `on_turn_finished`.
- Paste-into-TUI stack: `paste_into`, `paste_score`, `wait_until_ready`.
- `capture_pane` (detection + UI terminal view).
- Trust-modal pre-trust (`pretrust_claude_dir`), generalized per-type.
- Liveness supervisor skeleton, locking primitives, config loading + `minyaml`
  fallback, sessions/resume machinery, the JSONL logging layer.

**Rewrite (this is the v2 work):**
- The **messaging layer**: XML-envelope-in-prose + pane-scrape → file-based
  inbox/outbox/read/sent mailroom.
- Backpressure: v1's `{delivered, completed}` counter + queue dance is largely
  **replaced** — the inbox *is* the queue; mail waits in the folder until the
  agent is free (no pasting into a busy pane).
- Reply-reminder subsystem: **removed** — reply-in-body + message IDs + an
  advisory read-receipt replace it.
- `broadcast`: **removed**.
- New: the HTTP control-plane/UI, `user`/`system` virtual mailboxes, periodic
  pings, `type`↔`command` mismatch detection, per-agent health probe.

---

## 24. Invariants we must not regress

From `PROJECT-DOCUMENTATION.md` §13.1, still binding in v2:

- Zero runtime dependencies (incl. the no-PyYAML CI job) — **and the UI honors
  this** (stdlib server, no build).
- `can_talk_to` enforced at the routing layer (now *cooperative* per §12 —
  documented honestly).
- Per-type turn-completion detection with mismatch handling.
- A liveness heartbeat (supervisor) — do not drop it.
- Durable JSONL event log as history's source of truth.
- `package.json` = single source of truth for version, tag-verified at publish.
- `up --resume` semantics.
- Never ship runtime state (`.agentainer/`, workspaces, `__pycache__`) — keep the
  three-layer guard (`.gitignore` + `.npmignore` + `files` allowlist).
- 100% line coverage as a release gate, driven by mock agents.
- Keep the discovery layer (`llms.txt`, FAQ, quotable intro, keywords, absolute
  image URLs).

---

## 25. Testing & coverage

- **100% line coverage stays the gate**, all via mock agents (bash loops), real
  tmux, real hooks, real locks/queues — no API keys, nothing to pay for.
- **The file-based model is *more* testable than v1.** Mail is files on disk, not
  scraped panes — assertions become "did the right file land in the right folder
  with the right header," which is deterministic and trivial to inspect.
- **Keep the stdlib-only CI job** proving the no-PyYAML path across the Python/OS
  matrix, plus shell-lint (ShellCheck) from day one.
- **UI/HTTP layer:** keep handlers thin — they call the same tested core
  functions the CLI does. Business logic stays in the covered core; the browser
  page (vanilla JS) is out of scope for the mock-agent pytest suite by design.

---

## 26. Build phases

Each phase is independently shippable and testable.

- **P1 — Mail runtime (CLI-driven).** inbox/outbox/read/sent, per-recipient outbox
  folders + `about.md`, one-at-a-time release, orchestrator-owned read-state +
  best-effort `read/` receipts + auto-archive fallback, ACL enforcement, outbox
  sweep on stop, bounces, `system`/`user` virtual mailboxes (user via CLI for
  now), periodic pings, ported turn-detection + paste + trust-modal, mismatch
  detection, health probe, runaway-loop guard, `agentainer.yaml` schema,
  `mail_dir` + shared-workspace prefixing, branding rename, durable log, resume.
- **P2 — UI observability.** stdlib HTTP server, localhost-only, static page;
  read-only mailbox views + timeline + talk graph.
- **P3 — Terminal snapshot + send.** `capture-pane` view; send-from-UI via the
  paste path / `system`+`user` mail injection; the `user` mailbox UI +
  availability toggle + unread notifications.
- **P4 — Dynamic reconcile.** Edit `agentainer.yaml`, add/delete agents from the
  UI via validate → reconcile (delta apply), fully logged.

Post-P4, three additive control-plane layers shipped (each 100%-covered): the
**multi-swarm registry + shared settings** (one `serve` for every swarm on the
machine, global `~/.agentainer/` home, guided create flow), the **Telegram
bridge**, and the **MCP server** (D27) — the fourth surface, letting a coding
agent monitor and manage the system. All four surfaces (CLI / UI / Telegram /
MCP) stay at capability parity over the shared `lib/` core (CLAUDE.md #7, D27).

---

## 27. Risks & open questions

- **Turn-completion detection remains the #1 risk** — the whole clock. Mitigated
  by reusing proven v1 code + adding mismatch detection and a health probe, but
  gemini/hermes pane-polling stays inherently heuristic.
- **Paste-into-TUI reliability** — reduced (nudge is a fixed, retryable string)
  but not eliminated; still the flakiest layer.
- **Cooperative ACL** — accepted (§12); revisit only if hard isolation becomes a
  requirement (would need OS-level separation).
- **Latency** — everything is stop-triggered/poll-driven, so a multi-hop
  A→B→A conversation spans several turns + ticks. Acceptable for this model; make
  intervals configurable.
- **Ignored-vs-processed at the edges** — read receipts are best-effort; a model
  that never moves to `read/` yields no receipt (liveness still guaranteed by
  auto-archive). Acceptable.
- **Open naming** — final field names in `agentainer.yaml` (`role` vs `prompt`,
  exact `mail_dir` semantics) to be locked in P1.
- **`read/` growth** — long runs accumulate files in `read/`/archive; may want a
  retention/rotation policy (log stays source of truth).

---

## 28. v2 acceptance checklist

A v2 is "at parity + the new model" when, with **zero pip installs**, it can:

- [ ] `validate` an `agentainer.yaml` and print the resolved config, launching
      nothing.
- [ ] `up` a multi-agent set: create dirs + mailboxes, install correct per-type
      turn-detection, open one tmux session per agent, deliver the first prompt
      reliably (past trust modals), and create `outbox/<name>/` + `about.md` per
      the ACL.
- [ ] Detect a `type`↔`command` mismatch at `up` and fail loudly.
- [ ] Send by writing to `outbox/<name>/`; sweep on stop; enforce `can_talk_to`;
      deliver with an orchestrator-written header; move to `sent/`; bounce
      disallowed mail to the sender and move it to `failed/`.
- [ ] Deliver mail **one at a time**; keep `inbox/` to a single message; advance
      on stop.
- [ ] Own read-state; honor best-effort `read/` receipts; auto-archive fallback so
      nothing loops or wedges.
- [ ] Nudge idle agents with mail, re-injecting the protocol + allowed recipients.
- [ ] Fire periodic pings as `system` mail with the three guards.
- [ ] Support `user` as a virtual mailbox: ACL gate + availability (default off,
      hold-not-bounce + `system` ack).
- [ ] Run the liveness heartbeat + per-agent health probe; reconcile stale-busy /
      dead / silent-but-alive agents.
- [ ] Enforce the runaway-loop rate cap.
- [ ] `up --resume` reattaches Claude/Codex; warns for Gemini/Hermes; mail state
      persists across restart for free.
- [ ] Serve the optional UI from stdlib, localhost-only: mail observability →
      terminal snapshot + send → dynamic reconcile.
- [ ] Route all state mutation (supervisor, hooks, UI) through shared locks.
- [ ] Keep the durable JSONL log as the source of truth.
- [ ] Pass a mock-agent test suite at **100% coverage**; prove the no-PyYAML path
      in CI across the Python/OS matrix; publish to npm with provenance +
      tag/version verification.
- [ ] Ship the discovery layer (README SEO + FAQ + `llms.txt`) intact.

---

## 29. Decisions log

Every explicit choice made during design:

- **D1.** Replace v1's XML-envelope-in-prose messaging with a **file-based
  mail-inbox model**. Receiving = reading a file.
- **D2.** Sending = **writing a file into `outbox/<recipient>/`**; recipient is in
  the *path*, never parsed from content. (Chosen over a `send` CLI command and
  over agents writing directly to each other's inboxes.)
- **D3.** Message **body is natural language**; the orchestrator writes the header
  (`from`/`to`/`id`/`time`/`re`). No structured generation by the model.
- **D4.** **Deliver one message at a time**; `inbox/` holds exactly one; the rest
  wait in the orchestrator's queue.
- **D5.** **Read-state is orchestrator-owned.** Moving to `read/` (and to
  `done/`) is **optional/best-effort**, used for read receipts; auto-archive
  fallback guarantees liveness.
- **D6.** **Outbox pickup happens on stop**, not by continuous polling — which
  also eliminates the partial-write race.
- **D7.** **`read/` folder = best-effort "processed" signal → read receipts** back
  to the sender ("read" vs "ignored").
- **D8.** On send **success, move file to `sent/`** (the move is the receipt); on
  **failure, bounce a `system` mail** to the sender and move the file to
  `failed/`.
- **D9.** **`outbox/<name>/about.md`** = orchestrator-maintained contact card
  (name, description, availability). Folder existence = the ACL.
- **D10.** **Re-inject the full protocol on every nudge**, including the list of
  allowed recipients. Never assume model memory.
- **D11.** **The model is always told its exact paths**; it never assumes them
  (makes `mail_dir` + shared-workspace prefixing invisible to the model).
- **D12.** **Per-agent periodic ping**, delivered as `system` mail, with idle-only
  + no-pile-up guards. *(Superseded by D26: the original fixed-cadence
  `periodically_ping_seconds`/`periodically_ping_message` fields were removed in
  favour of the cron `pings:` list.)*
- **D13.** **`user` and `system` are virtual, reserved participants.** `user` is a
  UI-backed mailbox with a static ACL gate + a dynamic availability toggle
  (default **unavailable**, **hold-not-bounce** + `system` ack). `user` is
  auto-surfaced in the UI (no auto-nudge for humans).
- **D14.** **Remove `broadcast`.**
- **D15.** **`can_talk_to` ACL is cooperative, not OS isolation** — accepted and
  documented honestly.
- **D16.** **Reuse v1's turn-detection / paste / hook-install / trust-modal /
  supervisor / locking / config code; rewrite only the messaging layer.**
- **D17.** **Keep hooks/notify/pane-polling** for stop detection; **add**
  `type`↔`command` mismatch detection + a per-agent health probe.
- **D18.** **Add a role/prompt field** per agent + a first-prompt bootstrap
  template + generalized per-type trust-modal handling.
- **D19.** **Add a runaway-loop rate cap** (per pair, per window).
- **D20.** **UI = stdlib `http.server` + one static vanilla-JS page**,
  **localhost-only by default**, opt-in, token for remote; YAML stays the single
  source of truth; handlers reuse the tested core.
- **D21.** **Rename everything "swarm" → "agentainer"**; collective noun in prose
  is "the agents".
- **D22.** **Customizable `mail_dir`** (global + per-agent); **auto-prefix
  mailboxes by agent name when a workspace is shared**.
- **D23.** **Build in four phases** (P1 mail runtime → P2 observability → P3
  terminal+send → P4 dynamic reconcile).
- **D24.** **Keep all v1 invariants** (§24): zero-deps + no-PyYAML CI job, durable
  JSONL log, `package.json` version source, resume, never-ship-state, 100%
  coverage gate, discovery layer.
- **D25.** **First prompt is a STANDBY wrapper, not the raw `role`.** At `up` each
  agent gets its `role` (identity + mailbox protocol) wrapped with "no task yet,
  do NOT send anything, you'll be notified when a real task arrives." Prevents a
  proactive model from mailing peers at startup before any task exists. The human
  delivers the first task via `agentainer send`; the nudge is the notification.

- **D26.** **Cron-scheduled pings (`pings:`).** An agent (or `defaults`) may declare
  a `pings:` list of `{message, cron, when_busy}` rules, so it is nudged with
  different messages at different times (working hours vs nights vs weekends). This
  **replaces** the removed fixed-cadence `periodically_ping_seconds` /
  `periodically_ping_message` fields (D12). A
  zero-dep 5-field cron parser (`lib/cron.py`) evaluates rules in the host's LOCAL
  time. Guards are preserved: at most one unhandled ping outstanding (global
  no-pile-up) and per-rule per-minute dedup; overlap resolves to the first
  *deliverable* rule in list order. `when_busy` is per-rule (`skip` default =
  don't fill a busy mailbox; `queue` = enqueue to wait). A non-empty `pings` list
  takes precedence over the legacy fields, which still work when it is absent.
  Cron is validated at config load (a bad expression is a `ConfigError` naming the
  agent), and editable from the UI agent form. Chose structured cron rules over a
  new bespoke schedule DSL for familiarity; accepted the local-time (no tz db)
  trade-off to keep zero deps.

- **D27.** **MCP server — the fourth control plane, permanently maintained.**
  Agentainer manages coding agents, so a coding agent managing *Agentainer* over
  the Model Context Protocol is a first-class, forever-supported use case.
  CLAUDE.md principle #7 is upgraded from three surfaces to **four** (CLI / UI / Telegram /
  **MCP**) at capability parity. `lib/mcp.py` is a thin JSON-RPC 2.0 adapter over
  the same tested `lib/` core — never its own copy of the routing/ACL/lifecycle
  logic — kept at 100% coverage. **Two transports, one tool set:** `agentainer
  mcp` (stdio; the `.mcp.json` path, no running `serve` required, operates over
  the global registry) and `POST /mcp` on the `serve` HTTP control plane (reuses
  the UI Bearer token; POST-only, `GET /mcp` → 405 since we push no server→client
  notifications). Methods: `initialize`/`tools/list`/`tools/call`/`ping`;
  notifications get no reply. Tools cover monitor (`list_swarms`, `swarm_status`,
  `read_inbox`, `read_queue`, `read_user_inbox`, `agent_logs`, `capture_pane`,
  `read_config`) and manage (`send_message`, `set_availability`,
  `start_agent`/`stop_agent`, `up_swarm`/`down_swarm`, `create_swarm`,
  `add_agent`/`remove_agent`); each takes an optional `swarm` (required only when
  more than one is managed). **Tool failures are returned as `isError` results,
  not JSON-RPC errors** (only malformed JSON-RPC uses the numeric codes), so the
  model self-corrects in-band exactly like the mailroom's `system` mail. Zero new
  dependencies. New management capabilities must add a matching MCP tool
  alongside their CLI/UI/Telegram surfaces. Docs: `docs/mcp.md`.

---

## 30. Glossary

- **Agent** — one AI coding-agent CLI (`claude`/`codex`/`gemini`/`hermes`) in its
  own tmux session + workspace.
- **Orchestrator** — the long-running daemon (supervisor + mailroom + optional UI)
  in its own tmux session.
- **Mailroom** — the orchestrator subsystem that routes files between mailboxes.
- **Nudge** — the fixed reminder pasted into an idle agent that has mail waiting.
- **Turn / stop** — one agent working period; "stop" = it finished and went idle
  (the system clock).
- **`system`** — reserved virtual sender for orchestrator messages (nudges, pings,
  bounces, receipts, acks).
- **`user`** — reserved virtual mailbox for the human operator, reachable via the
  UI.
- **Contact card** — `outbox/<name>/about.md`, the orchestrator-maintained
  name/description/availability of a reachable recipient.
- **Read receipt** — sender-visible signal (best-effort) that a recipient moved a
  message into its `read/` folder.
```

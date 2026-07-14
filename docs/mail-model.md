# The file-based mail model

> A deep conceptual guide to the heart of Agentainer v2.

This document explains **how agents in Agentainer v2 talk to each other** — the
file-based mailroom implemented in `lib/mail.py`. It is a conceptual guide, not
an API reference: read it to understand *why* the design is shaped this way, then
read `lib/mail.py` and `ProjectPlan.md` (§4–§14) for the exact mechanics.

The single idea to hold in your head: **an agent's entire world is two verbs and
a handful of folders.** It *reads a file* to receive, and *writes a file* to
send. Everything else — routing, access control, message IDs, queueing,
read-state, retries, availability, the durable log — is deterministic
orchestrator code the model never sees.

---

## 1. Why files, not prose envelopes

### What v1 did, and why it broke

Agentainer v1 had agents exchange messages as **tagged XML envelopes emitted
inside their prose output**. An agent would write something like
`<msg to="bob">…</msg>` in the middle of its normal reply, and the orchestrator
would **scrape that envelope back out of a fullscreen-TUI pane**. This is the
thing that failed:

- It depended on the model reliably producing **well-formed structured output**
  buried in free-form prose — something weak models get wrong constantly (missing
  close tags, wrong attribute quoting, hallucinated recipients).
- It depended on **scraping a TUI pane**, which mangles wrapping, truncates long
  messages, and loses anything that scrolled off.
- Recovery from a malformed envelope was hard, because the "protocol" lived in a
  place the model didn't reliably control.

### What v2 does instead

v2 replaces the messaging layer with a **file-based mail model**. The recipient
is encoded in the **folder path** (`outbox/bob/`), never parsed from the file's
contents. The body is **plain natural language** — no tags, no JSON, no shell
quoting to get wrong.

This is Principle 1 from `CLAUDE.md`, **designed for dummy models**: the *only*
capability an agent must have is the ability to read and write files. Every
tool-calling LLM can do that, including weak ones. Contrast the two failure
surfaces:

| | v1 (XML-in-prose) | v2 (files) |
|---|---|---|
| How you send | emit a structured tag inside prose | write a file into `outbox/<name>/` |
| How you receive | orchestrator scrapes it from a TUI pane | read the one file in `inbox/` |
| Who names the recipient | the model, in an attribute | the **folder path** |
| Who writes the header | the model | the **orchestrator** |
| Failure mode | malformed tag, silent scrape loss | (almost none — it's a file) |

The model writes prose; the orchestrator does everything hard. That division is
the whole design.

---

## 2. The folders: real and virtual

### The five real mailbox folders

Every agent has five folders (created by `init_mailboxes`). From the agent's
point of view, only the first three matter:

```
<mail_dir>/
  inbox/                 # the ONE current unread message
  outbox/
    <recipientA>/
      about.md           # orchestrator-maintained contact card (= the ACL)
      <message file>     # you write here to send
    <recipientB>/
      about.md
  read/                  # move a handled message here (best-effort receipt)
  sent/                  # your delivered outgoing mail (orchestrator moves here)
  failed/                # ACL-bounced / rejected outgoing mail (orchestrator moves here)
```

- **`inbox/`** — holds **exactly one** unread message at a time. The orchestrator
  releases the next only after the current one is handled (see §4). Even a model
  that runs `cat inbox/*` sees a single message.
- **`outbox/<name>/`** — write a file here to send to `<name>`. Read
  `outbox/<name>/about.md` first to see who they are and whether they're
  available. **The mere presence of the folder + card is the ACL** — the
  orchestrator only creates `outbox/<name>/` if `<name>` is in this agent's
  `can_talk_to`. So "who can I message?" is answered by `ls outbox/`, and a model
  literally cannot address someone it has no folder for.
- **`read/`** — move a message here once you've handled it. This is the
  best-effort "I processed it" signal that drives read receipts (§7).
- **`sent/`** — your own record of successfully delivered mail. The orchestrator
  *moves* your outbox file here on successful routing — the move **is** the
  delivery confirmation. Read-only to the agent.
- **`failed/`** — outgoing mail that failed the ACL check or was rejected
  (bounce, rate-limit). The orchestrator moves it here; you also get a `system`
  mail explaining why.

The agent should **never touch** the orchestrator-private folders: the queue
(`.agentainer/queue/<agent>/`), the archive, or the event log.

### A message on the wire

When the orchestrator delivers a message, it **stamps a header** the model never
had to write (`stamp_message` / `format_header` in `lib/mail.py`):

```
From: alice
To: bob
Id: m-1a2b3c4d
Time: 2026-07-11T14:03:00Z

please review the API change in service/auth.py and reply when done
```

The model only ever wrote the body (`please review…`). The `From`/`To`/`Id`/
`Time` block is orchestrator-owned; message IDs (`m-` + 8 hex chars, from
`new_message_id`) let the durable log thread request→reply.

### The `about.md` contact card

`outbox/<name>/about.md` is the recipient's live contact card, written and
refreshed by the orchestrator (`write_contact_cards`):

```
Name: bob
Role: Backend engineer — owns the API service
Status: available
```

For the `user` mailbox, `Status:` reflects the live availability toggle
(`available` / `away`, see §8).

### Two virtual mailboxes

Beyond the real agents, there are two **reserved, virtual** participants — no
tmux session, no workdir, no hooks, just addresses on the mail bus. The config
validator rejects any real agent trying to claim these names.

- **`system`** — the orchestrator's own voice: nudges, bounces, delivery acks,
  read receipts, periodic pings. `system` is a *sender only* — it can never be a
  *recipient* (mailing `system` bounces; see §5 and §8).
- **`user`** — the human operator, as a first-class mailbox reachable from the
  UI. Gated by an ACL (`user` must be in the sender's `can_talk_to`) and a dynamic
  availability toggle (§8).

---

## 3. The two verbs, end to end

Here is a complete round trip, so the folder roles click into place. Say
`alice` wants `bob` to review some code.

1. **alice writes a file** into her outbox:
   `…/alice/outbox/bob/review-request.txt` containing plain prose.
2. **alice's turn stops.** The orchestrator detects the stop (§4) and sweeps her
   outbox (`on_stop`).
3. **The orchestrator routes** the message (`route_outbound`): checks the ACL,
   stamps a header, and **enqueues** it for `bob` in
   `.agentainer/queue/bob/m-1a2b3c4d.txt`. It **moves alice's file to
   `…/alice/sent/`** — the move is her delivery confirmation.
4. **The orchestrator releases** the next queued message into
   `…/bob/inbox/m-1a2b3c4d.txt` (one-at-a-time, §4) and **nudges** bob (§6).
5. **bob reads** `inbox/`, does the work, writes his reply into
   `…/bob/outbox/alice/`, and **moves the handled message to `…/bob/read/`**.
6. bob's turn stops → the cycle repeats in the other direction.

Notice what the models did: alice wrote one file; bob read one file, wrote one
file, moved one file. Everything else was the orchestrator.

---

## 4. One-at-a-time release

**A weak model handed a pile of unread mail gets confused.** So Agentainer
controls what is *physically in the inbox folder*: **`inbox/` only ever contains
one message.** The rest wait in the orchestrator-private queue
(`.agentainer/queue/<agent>/`).

`release_next(cfg, agent_name)` implements this:

- If the inbox already holds a message, it does **nothing** but bump the
  *presentation counter* (see below) and return `False`. One-at-a-time is
  preserved.
- Otherwise it moves the **oldest** queued file into the inbox, logs a
  `delivered` event, sets the presentation count to 1, and returns `True`.

The whole read-decide-move runs under **one per-recipient lock**. This is not
incidental: two concurrent `release_next(bob)` calls — say two agents both stop
and route into bob at once, or a Stop hook firing during a supervisor tick —
could otherwise each observe an empty inbox, each pick the oldest file, and land
**two** messages in one inbox (breaking one-at-a-time) or crash with
`FileNotFoundError` when the other already moved the file. Serialising the whole
decision under the lock removes that TOCTOU race. (See the long comment in
`release_next`.)

### Liveness: auto-archive after N presentations

What if a forgetful model **never moves the message to `read/`**? Without a
fallback, the inbox would stay occupied forever and the queue would never
advance — a wedged agent.

The guard is the **presentation counter**. Every time a message is presented (or
re-presented) to an agent, `_bump_presentations` increments a count keyed by
message id. When `process_read_folder` runs and finds the inbox still holding a
single message whose presentation count has reached
`AUTO_ARCHIVE_PRESENTATIONS` (5) and it was never processed, it
**auto-archives** that message (`log.archive_message`) and calls `release_next`
to advance the queue.

This is Principle 5 from `CLAUDE.md`: **correctness never depends on the model
doing housekeeping.** Moving mail to `read/` is best-effort; the auto-archive
fallback guarantees the swarm can never wedge or loop, even with a model that
forgets to file its mail. Worst case: the sender doesn't get a read receipt.

---

## 5. Routing and access control

`route_outbound(cfg, sender, recipient, body)` is the single routing decision
point. It returns one of `delivered`, `bounce`, `rate-limited`, or `user-held`,
and handles four cases in order:

1. **`recipient == "system"`** → **bounce.** `system` is a sender, never a
   recipient. The orchestrator drops a `system` mail into the sender's inbox
   (*"system is not a valid recipient…"*), moves the outbox file to `failed/`, and
   logs a `bounce`.
2. **`recipient == "user"`** → delegated to `deliver_to_user` (§8).
3. **recipient not in `sender.can_talk_to`** → **ACL bounce.** The sender gets a
   `system` mail listing exactly who they *can* message
   (*"Your message to carol couldn't be sent — you can message: alice, bob."*),
   the file moves to `failed/`, and a `bounce` is logged with `reason="acl"`.
4. **rate-limited** (§10) → file moves to `failed/`, `rate-limited` logged.

Otherwise it's **delivered**: mint a message id, stamp the header, `enqueue` for
the recipient, **move the sender's file to `sent/`**, and log a `route` event.

**The ACL is `can_talk_to`, enforced at the routing layer.** It is also
represented *physically* by which `outbox/<name>/` folders exist — a well-behaved
model can't even name a recipient it has no folder for, and if it somehow does,
the router bounces it.

> **Cooperative, not OS isolation (Decision D15).** In v2 the agents are coding
> agents with filesystem access. Nothing at the OS level stops a confused or
> rogue agent from writing *directly* into another agent's `inbox/`, bypassing
> `outbox/` and the ACL entirely. The `can_talk_to` ACL is **enforced for
> well-behaved agents that route through `outbox/`**; it is **not** a security
> boundary. This is documented plainly with no false guarantee. If hard
> isolation is ever required, it must come from OS-level means (separate
> users/permissions per workspace), not the mail model.

Errors always come back **as mail** from `system` (Principle 6), so the model
self-corrects in-band on its next turn with no new concept to learn.

---

## 6. The stop-triggered sweep — the system clock

**The orchestrator sweeps an agent's outbox only when that agent's turn STOPS.**
This is the beating heart of the whole design, and it is *the system clock*: the
nudge, one-at-a-time release, the outbox sweep, and periodic pings all fire off
"the agent stopped."

Why gate on stop? Because **when the agent is stopped, all its file writes are
flushed and none are in flight.** That eliminates the partial-write race for
free — no polling a half-written file, no stability heuristic needed.

### `on_stop` — the core routine

`on_stop(cfg, agent_name)` does exactly this:

1. **Snapshot the outbox under a lock.** Walk every `outbox/<recipient>/` subdir,
   read each message file (skipping `about.md` — the contact card is never routed
   or deleted). Routing happens *outside* the lock, so `enqueue` (which takes its
   own per-recipient lock) can't deadlock.
2. **Route every message** through `route_outbound`, tallying
   delivered/bounced/rate-limited. Any leftover original file is unlinked so it's
   never double-routed.
3. **Mark the turn finished** (`turn.on_turn_finished`) to clamp busy counters
   *before* releasing new mail.
4. **Deliver to everyone who received mail:** for each recipient (including the
   *sender itself* if it was bounced an error), `release_next` then `nudge` —
   nudge only fires when a message actually landed.

### Turn detection is per `type` — get it right or fail silently

How the orchestrator learns an agent stopped depends on its `type` (ported from
v1, **do not rewrite**):

| Type | Mechanism |
|------|-----------|
| `claude` | **Stop hook** installed into `<workdir>/.claude/settings.json` |
| `codex` | **`notify`** program in `<workdir>/.codex/config.toml` |
| `gemini` / `hermes` | **Pane polling** (`capture-pane` + readiness heuristic) |

If this detector is wrong, **failures are silent**: miss a stop and the agent
sits on unread mail forever (looks hung); fire a false stop and you paste into a
live TUI and corrupt the turn.

The load-bearing footgun: **a `type` ↔ `command` mismatch is a hard deadlock.**
If `command` launches a different CLI than `type` implies, the completion signal
never fires and the agent pins "busy" forever. `lib/config.py` detects this at
load time (`CLI_TOKENS` word-boundary check) and refuses to start — better a
loud error than a silent hang. A per-agent health probe catches the
"silent-but-alive" case the dead-session supervisor can't.

---

## 7. The nudge and read-state

### The nudge

When an agent stops with unread mail, the orchestrator pastes a **nudge** into
its pane (`nudge`). The nudge is a fixed, self-contained reminder that
**re-injects the protocol on every turn** — Principle 4, *assume no memory across
turns*:

```
You have a new message in <inbox>. Read it and do what it asks.
When you're done, move that file to <read>.
To send a message, write a file into <outbox>/<name>/ (read <outbox>/<name>/about.md
to see who they are and whether they're available). …
You can message: alice, bob.
```

Two things the nudge always does:

- **States the agent's EXACT paths.** The model never assumes its inbox/outbox/
  read locations — Principle 3. This is what makes custom `mail_dir` and
  shared-workspace prefixing (§9) invisible to the model.
- **Lists the allowed recipients.** This doubles as ACL documentation and stops a
  weak model from inventing a name it can't reach.

A paste failure is non-fatal: the mail still sits in the queue and gets released
on the next tick or when the session comes back (`nudge` swallows
`tmux.SwarmError` and returns `False`). Idempotent by construction.

The `standby_prompt` is the *first* message an agent gets at `up` — its `role`
plus an explicit **STANDBY** notice ("no task yet — do NOT initiate mail; wait to
be nudged"). This stops a proactive model from mailing its peers at startup
before any real task exists.

### Read-state is orchestrator-owned

Moving a handled message to `read/` is the **primary "I processed it" signal**,
but it is **best-effort**. The authoritative state lives in the orchestrator,
keyed by message id — not in filesystem presence, not in the model's diligence.

`process_read_folder(cfg, agent_name)` walks the `read/` folder, and for each
message it hasn't already processed:

- parses the `Id` and `From` header fields,
- records a **read receipt** for the sender (`mark_read` → the sender's
  `sent/` copy is marked read, plus a durable `read-receipt` log event),
- adds the id to the agent's processed set (`<agent>.read.json`).

Then it runs the **auto-archive fallback** (§4) so a forgetful model can never
wedge the queue. Read receipts are best-effort; **liveness is guaranteed.**

---

## 8. The `user` and `system` virtual mailboxes

### `system` — the orchestrator's voice

`system_mail(cfg, to_agent, body)` enqueues a message with `From: system` into an
agent's queue. This is how **every** orchestrator-originated message reaches an
agent: bounces, delivery acks, and periodic pings. Because errors come back as
ordinary mail (Principle 6), the model self-corrects **in-band** with no new
concept — an ACL bounce reads just like any other message, and the fix ("message
one of: alice, bob") is right there in the body.

`system` is never a valid *recipient* — mailing it bounces (§5).

### `user` — the human, gated and held

`deliver_to_user(cfg, sender, body)` routes an agent → human message. There are
**two gates**:

1. **ACL (static):** the sender must have `user` in its `can_talk_to`, or the send
   ACL-bounces to `failed/` like any other disallowed recipient.
2. **Availability (dynamic, default OFF):** `cfg.user_available`, toggled from the
   UI (`set_user_available`), reflected live in every `outbox/user/about.md`.

The key behavior: **when the user is away, mail is held, not bounced.** The
send *always succeeds* into the user's queue and the sender's file moves to
`sent/`. Only the human's *reply* is deferred:

- **User available** → returns `delivered`, logs `delivered`.
- **User away** → returns `user-held`, and the sender gets an immediate `system`
  ack: *"Delivered — the user is away and may respond later."* No mail is lost,
  and the sender isn't blocked waiting for an instant reply.

The reverse direction — **human → agent** — is `send_as_user(cfg, to_agent,
body)`: it enqueues a `From: user` message and immediately `release_next` +
`nudge`. "Send a prompt from the UI" and "mail from the user" are the **same
operation**, differing only in sender identity. The human is the one participant
*not* auto-nudged — the UI's unread badge is the human's version of the nudge.

---

## 9. Mail paths and shared workspaces

Where the five folders live is resolved by `cfg.mail_paths(agent)` in
`lib/config.py`. The base is `agent.mail_dir` (defaults to the agent's workdir).

The subtle case is **two agents sharing one workdir**. `SwarmConfig.__post_init__`
computes the set of shared workdirs up front (`self._shared`); when an agent's
workdir is shared, `mail_paths` **prefixes every folder with `<name>-`**:

```
# alice and bob share /work
/work/alice-inbox/   /work/alice-outbox/   /work/alice-read/  …
/work/bob-inbox/     /work/bob-outbox/     /work/bob-read/    …
```

The model **never sees this namespacing** — every nudge and first-prompt is
handed the exact computed paths (Principle 3), so custom `mail_dir` and
shared-workspace prefixing stay completely invisible to the agent.

---

## 10. Periodic pings

Some agents have standing, time-based duties (a news reporter doing its rounds)
even with an empty inbox. A per-agent `pings:` list of cron-scheduled nudges
drives this:

```yaml
agents:
  reporter:
    pings:
      - cron: "*/30 * * * *"    # every 30 minutes
        message: "Check the news wires and post any updates."
```

A periodic ping is delivered as a `system` message into the agent's own queue —
**reusing the exact one-at-a-time pipeline**, so the model's experience is
identical to normal mail; nothing new to learn. `maybe_ping(cfg, agent_name)`
injects one only if **all the §10 guards** pass:

1. **Idle-only (by default)** — a rule that comes due while the agent is busy
   (`turn.busy_info` is not `None`) is skipped, unless it opts in with
   `when_busy: queue`. Never interrupt a turn in progress.
2. **No pile-up** — skip if an unhandled ping already sits in the queue or inbox.
   Ping files are named with the `PING_MARKER` (`ping-…`) prefix so a still-
   pending ping is detectable; a slow agent must not accumulate ten identical
   pings.
3. **Due-this-minute** — a rule fires at most once per matching wall-clock minute
   (deduped in `<agent>.ping.json`); on overlap the first deliverable rule in
   list order wins. See [`configuration.md` §5 `pings`](configuration.md#pings)
   for the full cron syntax and `when_busy` policy.

Real mail always takes priority; a ping matters only when the inbox would
otherwise be empty. Net effect: Agentainer is a lightweight **mailbox + cron**
for agents, both on one code path.

---

## 11. The runaway-loop cap

Two chatty agents can fall into a "thanks!" / "you're welcome!" loop and mail
each other forever, each stop triggering the next. The **runaway-loop cap** is
cheap insurance against that.

`rate_limited(cfg, a, b)` keeps a per-**pair** sliding window (keyed by the two
names sorted, in `<a>-<b>.loop.json`): if the pair has already exchanged
`RUNAWAY_CAP` (20) messages within `RUNAWAY_WINDOW_S` (60 seconds), further
messages are **rate-limited** — the outbox file moves to `failed/`, a
`rate-limited` event is logged, and delivery is dropped. The window is a rolling
60-second cutoff, so once the burst subsides the pair can talk again.

This bounds the blast radius of a misbehaving pair without any model cooperation.

---

## 12. The durable JSONL event log

Every meaningful event — `delivered`, `route`, `bounce`, `rate-limited`, `read`,
`read-receipt`, `ping`, `user-send`, `user-held`, `user-available`/`user-away` —
is written to the **durable JSONL event log** under `.agentainer/logs/*.jsonl`
(per-agent `<agent>.jsonl` and global `agentainer.jsonl`).

This log is **the source of truth for history**, and it is load-bearing for one
specific reason: **the coding-agent CLIs run as fullscreen TUIs that keep no
scrollback.** You *cannot* recover what happened from a pane after the fact. The
JSONL log is the only durable record — it feeds the UI's history view, threads
request→reply via message IDs, and is what you read when debugging why a message
did or didn't flow.

The log lives under `.agentainer/` (orchestrator-private runtime state) and is
**never committed or shipped** — see the three-layer guard in `CLAUDE.md`.

---

## Recap: what the model does vs. what the orchestrator does

| The **model** does | The **orchestrator** does |
|---|---|
| Read the one file in `inbox/` | Release exactly one message at a time |
| Write a prose file into `outbox/<name>/` | Sweep the outbox on stop; route it |
| Read `outbox/<name>/about.md` | Maintain contact cards; enforce the ACL |
| (Best-effort) move handled mail to `read/` | Own read-state; auto-archive fallback |
| — | Stamp headers, mint message IDs |
| — | Nudge, re-inject the protocol every turn |
| — | Hold `user` mail; run periodic pings |
| — | Rate-cap runaway loops |
| — | Write the durable JSONL log |

The model's half of that table is *read a file, write a file*. Everything else —
every hard, stateful, race-prone, protocol-bearing thing — is deterministic code
in `lib/mail.py`. That is the entire point of the file-based mail model: push
**nothing** onto the model that a dummy model would get wrong.

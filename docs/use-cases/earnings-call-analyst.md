# Use case: Earnings-call analyst

A concrete, end-to-end walkthrough of the shipped
`examples/earnings-call-analyst.yaml` swarm — a LIVE earnings-call digest +
consensus-surprise score + price-target revision pipeline. A single
**earnings-lead** hub takes the transcript and the consensus from you and
coordinates three specialists so a non-analyst can read the call in plain
language: a **transcript-digest** parses the call and pulls reported metrics, a
**surprise-scorer** scores beats/misses versus consensus, and a **target-reviser**
revises a (simulated) target and flags guidance cuts or raises. The lead
synthesizes the three into the one summary you receive.

> ⚠️ **Educational, not financial advice.** This swarm is a **paper / simulated**
> exercise. It never places an order, never connects to a broker, and never emits
> a buy/sell signal you should act on. Every target/rating is a simulated model
> output for learning. Nothing here is investment advice.

Everything below is based on the actual contents of
`examples/earnings-call-analyst.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Investors, founders, and operators who want to *read a live earnings call without
doing the parsing and the consensus math themselves* — and who want the surprise,
the revised (simulated) target, and any guidance change explained in plain
language rather than buried in a 90-minute transcript. The swarm encodes the
discipline that makes an earnings read trustworthy: one owner of the
human-facing surface, a parser that never scores, a scorer that never pulls
metrics from raw prose, and a reviser that keeps the "is this a guidance cut?"
question explicit.

It is deliberately a **hub-and-spoke**, not a free-for-all: every transcript and
every deliverable passes through the earnings-lead, so the point where the
digest meets the score meets the revised target lives in exactly one place.
Swapping in a sibling (e.g. a `macro-strategy-desk` or a dedicated guidance
tracker) is a few lines of config.

This is **distinct from equity-research**: it is about the *live call* — the
transcript, the consensus surprise, and the guidance change — **not** 10-K/10-Q
filings or a DCF valuation.

---

## 2. The topology

```
          user
            |
       earnings-lead               (the hub: talks to all three specialists + user)
         /      |      \
transcript-  surprise-   target-reviser
digest      scorer       (revises target + flags guidance cut/raise)
(codex)     (gemini)     (claude)
```

Four agents, one directed flow:

1. **`user` → `earnings-lead`** — you send the live call transcript (a file, a
   paste, or a location to read) plus the consensus estimates (reported EPS/rev
   expectations and any starting target/rating), or a plain-English question.
2. **`earnings-lead` → `transcript-digest`** — the lead sends the raw transcript
   and asks for it parsed into prepared remarks vs Q&A, with the reported metrics
   pulled out (EPS, revenue, segments, margins, guidance ranges), each sourced to
   its place in the call.
3. **`transcript-digest` → `earnings-lead`** — the structured digest comes back.
4. **`earnings-lead` → `surprise-scorer`** — the lead hands over the reported
   metrics + the consensus and asks for a beat/miss score on EPS and revenue, the
   magnitude, and a better/in-line/worse read.
5. **`surprise-scorer` → `earnings-lead`** — the surprise score comes back.
6. **`earnings-lead` → `target-reviser`** — the lead sends the digest + the
   consensus target/rating (or the user's starting figures) and asks for a revised
   (simulated) target + rating and an explicit guidance-cut/raise flag.
7. **`target-reviser` → `earnings-lead`** — the revision + guidance flag comes back.
8. **`earnings-lead` → `user`** — the lead assembles all three into one
   plain-language synthesis (headline surprise → metric detail → target/rating
   change → guidance flag, closing with the "not financial advice" disclaimer)
   and delivers it to you.

The routing above is *enforced* by each agent's `can_talk_to` list. The three
specialists **never** talk to `user` (or to each other) — only the lead does. If
a specialist tried to mail `user` directly, the orchestrator bounces it as a
`system` message and files it in `failed/`.

---

## 3. The config, explained

Here is `examples/earnings-call-analyst.yaml` in full (role bodies abbreviated
with `...` for readability; the structure, names, ACLs, and commands are exact):

```yaml
swarm:
  name: earnings-call-analyst
  root: ./earnings-call-analyst-workspace

defaults:
  capture: none              # claude/codex are auto-upgraded to their hook at up
  can_talk_to: []            # tightened per agent below

agents:
  - name: earnings-lead
    type: claude
    can_talk_to: [transcript-digest, surprise-scorer, target-reviser, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the EARNINGS-LEAD and the only agent who talks to the human (user). ...
      (1) read the transcript + consensus, ask ONE clarifying question if scope is
       ambiguous; (2) delegate the parse to TRANSCRIPT-DIGEST; (3) delegate the
       surprise score to SURPRISE-SCORER; (4) delegate the target revision +
       guidance flag to TARGET-REVISER; (5) assemble all three into one synthesis
       and post it to user with the "not financial advice" disclaimer. ...

  - name: transcript-digest
    type: codex
    can_talk_to: [earnings-lead]
    command: "codex --yolo"
    role: |
      You are the TRANSCRIPT-DIGEST. Parse the call into prepared remarks vs Q&A,
      pull the reported metrics (EPS, revenue, segments, margins, guidance ranges)
      sourced to their place in the call ... Do NOT score vs consensus. Report ONLY
      to the earnings-lead. ...

  - name: surprise-scorer
    type: gemini
    can_talk_to: [earnings-lead]
    command: "gemini --yolo"
    role: |
      You are the SURPRISE-SCORER. Score reported vs consensus on EPS/revenue, the
      magnitude, and a better/in-line/worse read ... work only from the digest.
      Report ONLY to the earnings-lead. ...

  - name: target-reviser
    type: claude
    can_talk_to: [earnings-lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the TARGET-REVISER. Produce a SIMULATED, educational target/rating
      revision with rationale, and flag guidance CUT/RAISE/REITERATE vs the prior
      call ... Do NOT score the surprise. Report ONLY to the earnings-lead. ...
```

Field by field:

### `swarm`
- **`name: earnings-call-analyst`** — the swarm's name (shows up in `status`,
  logs, sessions).
- **`root: ./earnings-call-analyst-workspace`** — the parent directory for the
  agents' working directories and mailboxes. Each agent's workdir defaults to
  `earnings-call-analyst-workspace/<name>` (earnings-lead, transcript-digest,
  surprise-scorer, target-reviser), and orchestrator state goes under
  `earnings-call-analyst-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless it overrides them.
- **`capture: none`** — the default turn-detection mode. At `up`, the loader
  **auto-upgrades** this for `claude` and `codex` to their natural hook (the
  `validate` run prints warnings confirming it). It is a safe floor; every agent
  states its own `can_talk_to`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Each agent below
  states its own list explicitly.

### `earnings-lead` (type: `claude`)
- **`can_talk_to: [transcript-digest, surprise-scorer, target-reviser, user]`** —
  the lead is the hub and the **only agent that can talk to `user`**. That last
  part is the whole point: keep the human-facing synthesis to one agent so the
  digest, the score, and the revision all converge there before you see anything.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`;
  the `capture: none` default is auto-upgraded to hook here).

### `transcript-digest` (type: `codex`)
- **`can_talk_to: [earnings-lead]`** — returns the parsed digest to the lead and
  nowhere else. It cannot reach the user, the scorer, or the reviser directly.
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`
  (the `capture: none` default auto-upgrades to the notify hook).

### `surprise-scorer` (type: `gemini`)
- **`can_talk_to: [earnings-lead]`** — receives the reported metrics + consensus
  from the lead and returns the surprise score to the lead only. It never touches
  the user.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **Turn detection:** `gemini` has no completion hook, so it relies on **pane
  polling** — the supervisor watches its pane for turn completion.

### `target-reviser` (type: `claude`)
- **`can_talk_to: [earnings-lead]`** — returns the (simulated) target revision and
  the guidance-cut/raise flag to the lead only. It cannot reach the user, so its
  output always passes through the hub's synthesis.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook (auto-upgraded from `capture: none`).

### ACL enforcement

The ACL is **cooperative, not OS isolation** (Decision D15): agents have
filesystem access and *could* write straight into another inbox, but the
orchestrator only ever *releases* and *routes* mail between names on the
sender's `can_talk_to` list. Anything addressed outside that list is bounced back
as a `system` message filed in `failed/`, so a model that forgets the rule
self-corrects in-band. Here that means the three specialists can *only* reach the
lead, and only the lead can reach `user` — the human only ever sees the lead's
synthesis.

### Per-type turn detection

Turn-completion detection is the system clock (the stop → sweep → route → release
→ nudge loop). It is **per `type`**:
- `claude` (`earnings-lead`, `target-reviser`) → **Stop hook** — fires when Claude
  finishes a turn.
- `codex` (`transcript-digest`) → **`notify` hook** — fires when Codex finishes.
- `gemini` (`surprise-scorer`) → **pane polling** — the supervisor reads the pane
  to decide the turn ended.

A `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
Claude) means completion never fires and the agent pins "busy" forever — which is
why `command` must launch the same CLI family `type` implies.

### What's *not* in this config
- **No `pings:` block.** Unlike `fp-and-a-analyst.yaml` (which self-starts a
  monthly close), this swarm is event-driven off your transcript send — there is
  no scheduled trigger, because earnings calls land on an event basis, not a fixed
  cron. Add a `pings:` to `earnings-lead` if you want a periodic "any new call?"
  nudge.
- **No per-agent `capture` overrides.** The `defaults: capture: none` is
  auto-upgraded to the type's hook for claude/codex; gemini uses pane polling.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/earnings-call-analyst.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the auto-upgrade warnings for the
   claude/codex agents.
2. Creates the runtime dirs (`earnings-call-analyst-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/`
   `about.md` contact card *is* the ACL made visible: the lead gets
   `outbox/transcript-digest/`, `outbox/surprise-scorer/`, `outbox/target-reviser/`,
   `outbox/user/`; each specialist gets only `outbox/earnings-lead/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `earnings-lead`
   and `target-reviser`, the Codex `notify` hook for `transcript-digest`; the
   gemini agent is covered by pane polling.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents (and drives gemini's pane polling) so one stuck agent can't
   wedge the swarm.

At the end, `up` prints attach and **`serve`** hints. The `serve` line gives you
the mail-app control-plane UI (threads, live panes, send-as-user, availability
toggle). Drop `--host`/`--token` for the safe loopback-only `127.0.0.1` bind —
the UI can start processes, edit config, and type into agents that may run with
elevated permissions, so it must **never** be exposed on `0.0.0.0` without a
token. See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole digest→score→revise loop route mail with no API keys — the mechanics
> are identical.

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the lead's synthesis as mail (rather than have
it held), turn yourself available first:

```bash
./agentainer user available -c examples/earnings-call-analyst.yaml
```

This rewrites the `user` contact card in the lead's `outbox/user/about.md` to
`Status: available`, so the lead sees you're reachable. (While away, mail to you is
*held* and the sender gets a `system` ack — nothing bounces.)

Now send the transcript + consensus into the swarm, addressed to the lead:

```bash
./agentainer send --to earnings-lead -c examples/earnings-call-analyst.yaml \
  "Attached: the live FYQ3 earnings call transcript (call/transcript.md) and \
   consensus (call/consensus.csv: EPS est, revenue est). Digest it, score the \
   surprise, and revise my model target. Starting target = $180, rating = Hold."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the lead, then — because the
inbox was empty — **released into `inbox/`** and the lead is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the earnings loop advance one turn at a time.
Each arrow is a `stop → sweep → route → release → nudge` cycle:

1. **earnings-lead receives the transcript + consensus.** It reads `inbox/`, asks
   its one clarifying question if scope is ambiguous, then writes a delegation
   into `outbox/transcript-digest/`. On stop, that routes to the transcript-digest.
2. **transcript-digest parses the call.** It reads its inbox, splits prepared vs
   Q&A and pulls the reported metrics, and reports back into
   `outbox/earnings-lead/`. On stop, that routes to the lead.
3. **earnings-lead briefs the scorer.** It writes the reported metrics + consensus
   into `outbox/surprise-scorer/`. On stop, that routes to the surprise-scorer.
4. **surprise-scorer scores the beat/miss.** It reads its inbox, scores reported vs
   consensus, and reports back into `outbox/earnings-lead/`. On stop, that routes
   to the lead.
5. **earnings-lead briefs the reviser.** It writes the digest + starting
   target/rating into `outbox/target-reviser/`. On stop, that routes to the
   target-reviser.
6. **target-reviser revises + flags guidance.** It reads its inbox, returns a
   (simulated) revised target/rating and the guidance CUT/RAISE/REITERATE flag into
   `outbox/earnings-lead/`. On stop, that routes to the lead.
7. **earnings-lead synthesizes and writes to user.** It assembles the digest +
   score + revision into one plain-language synthesis (closing with the "not
   financial advice" disclaimer) into `outbox/user/`. On stop, that's delivered to
   your `user` mailbox.
8. **you get the synthesis** — visible with `agentainer user inbox`, or in the UI.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion. If you
never send a transcript, the agents just sit in standby.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/earnings-call-analyst.yaml
```

```
swarm: earnings-call-analyst   root: ./earnings-call-analyst-workspace
  earnings-lead     (claude) up idle queue=0 unread=0 talks=transcript-digest, surprise-scorer, target-reviser, user
  transcript-digest (codex)  up idle queue=0 unread=1 talks=earnings-lead
  surprise-scorer   (gemini) up idle queue=0 unread=0 talks=earnings-lead
  target-reviser    (claude) up idle queue=0 unread=0 talks=earnings-lead
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/earnings-call-analyst.yaml          # whole swarm, last 20
./agentainer logs -c examples/earnings-call-analyst.yaml -f        # follow live
./agentainer logs target-reviser -c examples/earnings-call-analyst.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox earnings-lead -c examples/earnings-call-analyst.yaml
```

Prints the one released message (headers + body), or `earnings-lead: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue earnings-lead -c examples/earnings-call-analyst.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach transcript-digest -c examples/earnings-call-analyst.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or by sending notes
into an agent's inbox.

- **Send a clarification to the lead.** Realized the numbers are non-GAAP?
  `./agentainer send --to earnings-lead -c examples/earnings-call-analyst.yaml
  "Re-brief transcript-digest: pull NON-GAAP EPS, the company changed the
  definition this quarter."` The lead relays the change down the chain.
- **Ask the reviser about the guidance flag.** `./agentainer inbox earnings-lead`
  (or the UI) shows the guidance CUT/RAISE/REITERATE flag the lead received, so you
  can see whether the change was caught.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/earnings-call-analyst.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/earnings-call-analyst.yaml     # resume is the default
```

On `up`, Agentainer reads
`earnings-call-analyst-workspace/.agentainer/sessions.yaml` (written as each agent
finished its first turn) and reattaches the recorded conversations via each type's
native resume: `claude --resume <id>` for the lead and reviser, `codex resume
<id>` for the transcript-digest, and the gemini session via its recorded id. A
resumed agent is *not* re-sent the standby prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/earnings-call-analyst.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a scheduled "new call?" nudge
This config is event-driven (you send the transcript). To self-start a periodic
check, add a `pings:` block to `earnings-lead`:

```yaml
  - name: earnings-lead
    type: claude
    can_talk_to: [transcript-digest, surprise-scorer, target-reviser, user]
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: |
          Check call/ for any new transcript dropped since last call. If one is
          present, run the full TRANSCRIPT-DIGEST -> SURPRISE-SCORER ->
          TARGET-REVISER loop and post the synthesis to user.
        cron: "0 9 * * 1-5"          # 09:00 on weekdays
        when_busy: queue
    role: |
      ...
```

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `transcript-digest: type: claude` (or `hermes`/`gemini`) to put the parse on a
  different model than the lead.
- `surprise-scorer: type: claude` if you want the scoring on Claude while keeping
  gemini out.
- Remember: `gemini`/`hermes` rely on **pane polling** (no completion hook), so
  they don't need (and shouldn't be given) a hook-based `capture`.

### Tune the ACL
- To let a specialist escalate straight to `user` (not only via the lead), add
  `user` to its `can_talk_to`. Mind that this widens the human-facing surface and
  bypasses the lead's single-funnel synthesis — the doc's convention keeps the lead
  the sole `user` contact.
- To make a specialist unreachable from anyone but the lead (already the case
  here), leave its `can_talk_to: [earnings-lead]` — that's the one-place-owns-the-
  synthesis guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md)
  for mixing model families safely.

### Keep it paper-only
The reviser is instructed to emit a *simulated, educational* target and to close
every lead synthesis with the "not financial advice" disclaimer. If you wire this
to a broker or any execution path, you break the swarm's safety model — keep the
output as analysis only.

---

## 10. Tips & footguns

- **Keep the lead the only `user`-facing agent.** Only the lead lists `user` in
  `can_talk_to`. That gives you a single funnel: raw digests, scores, and revisions
  all converge in the lead's synthesis before you see anything. If a specialist
  tried to mail `user` directly, the orchestrator bounces it (ACL) and drops a
  `system` note in their inbox explaining who they *can* message — the model
  self-corrects in-band.

- **The "not financial advice" disclaimer is load-bearing.** It is part of the
  lead's `role:` and should be carried into every user-facing synthesis. Treat the
  whole swarm as a paper/simulated exercise; never connect it to an execution
  venue.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch means completion never triggers and the
  agent pins "busy" forever. `status` showing an agent `busy` for a long time with
  `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/earnings-call-analyst.yaml
  ./agentainer remove-session -c examples/earnings-call-analyst.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files (the transcript you dropped in)
  or your config.

- **Availability shapes the ending.** If `user` is **away** when the lead finishes,
  your synthesis is *held* (with a `system` "the user is away" ack to the lead)
  rather than lost — read it later with `agentainer user inbox` or flip yourself
  available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions (`--dangerously-skip-permissions`,
  `--yolo`).

- **Treat `command` as sensitive.** It may embed keys via shell aliases; don't
  print or commit it.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- `examples/earnings-call-analyst.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

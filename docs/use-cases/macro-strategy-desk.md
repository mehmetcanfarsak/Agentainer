# Use case: Macro strategy desk

A concrete, end-to-end walkthrough of the shipped
`examples/macro-strategy-desk.yaml` swarm — a cross-asset macro research desk
that takes a macro/geopolitical event and turns it into a cross-asset impact read
(equities / rates / FX / commodities) plus a proposed **PAPER / SIMULATED**
positioning view. A **macro-chief** hub is the only agent that talks to you. It
fans out to four single-asset analysts — **rates-analyst**, **fx-analyst**,
**commodity-analyst**, **geopolitics-analyst** — and hands their bundled reads to
a **strategist** that synthesizes the cross-asset view and proposes positioning.
The strategist's proposal is the last word on positioning; the macro-chief relays
it to you, always labeled PAPER / SIMULATED / NOT FINANCIAL ADVICE.

> **Financial safety:** this swarm is **educational, not financial advice**, and
> produces **hypothetical, simulated** output only. It never places, routes, or
> recommends live trades, and nothing here is a solicitation or an executable
> order. Treat the output as a study exercise and do your own due diligence.

Everything below is based on the actual contents of
`examples/macro-strategy-desk.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Investors, researchers, and curious operators who want a disciplined cross-asset
read of a macro/geopolitical event without running the four single-asset analyses
themselves. The swarm encodes the discipline that makes a macro desk useful — one
owner of the human-facing surface, four analysts who each own exactly one asset
class / driver and never invent the others, and a strategist who only *synthesizes*
(a single cross-asset thesis + a clearly-labeled paper tilt) and never presents it
as live.

It is deliberately a **hub-and-spoke**, not a free-for-all: the four analysts never
talk to each other, so no single asset-class read can be silently over-weighted,
and the one place where the four reads meet (and where the paper-only gate sits)
lives in exactly one agent. Swapping in a dedicated equities analyst or a fifth
asset class is a few lines of config.

---

## 2. The topology

```
          user
            |
        macro-chief                  (the HUB: talks to all four analysts + strategist + user)
         /   |    \    \    \
  rates-  fx-  commodity-  geopolitics-  strategist
  analyst analyst analyst     analyst        (proposes PAPER positioning -> relayed by macro-chief)
  (codex) (gemini)(claude)   (gemini)
```

Six agents, one directed flow:

1. **`user` → `macro-chief`** — you send a macro/geopolitical event (a central-bank
   decision, an election, a conflict, a policy surprise, a data print) as prose, a
   paste, or a file, plus the horizon if you care (tactical vs. strategic).
2. **`macro-chief` → {rates, fx, commodity, geopolitics}`-analyst`** — the chief
   sends the same event (and horizon) to all four analysts in parallel.
3. **each analyst → `macro-chief`** — four single-asset reads come back (rates
   curve, FX, commodities, and the geopolitical *why* / tail risk).
4. **`macro-chief` → `strategist`** — the chief bundles the four reads (unchanged)
   and asks for one cross-asset synthesis + a **proposed PAPER** positioning view.
5. **`strategist` → `macro-chief`** — the synthesis + the hypothetical tilt come
   back, explicitly labeled PAPER / SIMULATED / NOT FINANCIAL ADVICE.
6. **`macro-chief` → `user`** — the chief assembles the four reads + the strategist's
   proposal into one desk note (positioning labeled PAPER) and delivers it to you.

The routing above is *enforced* by each agent's `can_talk_to` list. The four
analysts and the strategist **never** talk to `user` (or to each other) — only the
macro-chief does. If an analyst tried to mail `user` directly, the orchestrator
bounces it as a `system` message and files it in `failed/`.

---

## 3. The config, explained

Here is `examples/macro-strategy-desk.yaml` in full (role bodies abbreviated with
`...` for readability; the structure, names, ACLs, and commands are exact):

```yaml
swarm:
  name: macro-strategy-desk
  root: ./macro-strategy-desk-workspace

defaults:
  capture: none              # claude/codex are auto-upgraded to their hook at up
  can_talk_to: []            # tightened per agent below

agents:
  - name: macro-chief
    type: claude
    can_talk_to: [rates-analyst, fx-analyst, commodity-analyst, geopolitics-analyst, strategist, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the MACRO-CHIEF and the ONLY agent who talks to the human (user). ...
      (1) read the event, ask ONE clarifying question if scope is ambiguous;
       (2) fan out to the four analysts in parallel; (3) bundle the four reads and
       send to the STRATEGIST for one cross-asset synthesis + PAPER positioning;
       (4) assemble the desk note and label positioning PAPER / SIMULATED;
       (5) only then post to user. ...
      MAILBOX: ... You may message: rates-analyst, fx-analyst, commodity-analyst,
      geopolitics-analyst, strategist, user.

  - name: rates-analyst
    type: codex
    can_talk_to: [macro-chief]
    command: "codex --yolo"
    role: |
      You are the RATES-ANALYST. Given the event + horizon, write a tight rates /
      curve impact read ... front-end vs. long-end, curve shape, channels, the
      read (not an order), the risk to the read. Cover rates ONLY. Report ONLY to
      the macro-chief. ...

  - name: fx-analyst
    type: gemini
    can_talk_to: [macro-chief]
    command: "gemini --yolo"
    role: |
      You are the FX-ANALYST. Given the event + horizon, write a tight currency
      impact read ... USD direction, funding pair, DM-vs-EM, carry, channels, the
      read (not an order), the risk. Cover FX ONLY. Report ONLY to the macro-chief. ...

  - name: commodity-analyst
    type: claude
    can_talk_to: [macro-chief]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the COMMODITY-ANALYST. Given the event + horizon, write a tight
      commodity impact read ... energy, metals, ags, the USD link, the read (not an
      order), the risk. Cover commodities ONLY. Report ONLY to the macro-chief. ...

  - name: geopolitics-analyst
    type: gemini
    can_talk_to: [macro-chief]
    command: "gemini --yolo"
    role: |
      You are the GEOPOLITICS-ANALYST. Given the event + horizon, write the WHY:
      the regime / driver read, second-order spillovers, tail risks, the one-line
      "so what", the de-escalation path. Cover the driver / tail-risk read ONLY.
      Report ONLY to the macro-chief. ...

  - name: strategist
    type: claude
    can_talk_to: [macro-chief]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the STRATEGIST -- the positioning mind. Given the four bundled reads,
      synthesize ONE cross-asset thesis + a PROPOSED PAPER positioning view ...
      open with "PAPER / SIMULATED -- NOT FINANCIAL ADVICE, NOT AN EXECUTABLE
      ORDER." Work only from the four reads; report ONLY to the macro-chief. ...
```

Field by field:

### `swarm`
- **`name: macro-strategy-desk`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./macro-strategy-desk-workspace`** — the parent directory for the
  agents' working directories and mailboxes. Each agent's workdir defaults to
  `macro-strategy-desk-workspace/<name>` (macro-chief, rates-analyst, fx-analyst,
  commodity-analyst, geopolitics-analyst, strategist), and orchestrator state goes
  under `macro-strategy-desk-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless it overrides them.
- **`capture: none`** — the default turn-detection mode. At `up`, the loader
  **auto-upgrades** this for `claude` and `codex` to their natural hook (the
  `validate` run prints warnings confirming it). It is a safe floor; every agent
  states its own `can_talk_to`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Each agent below
  states its own list explicitly.

### `macro-chief` (type: `claude`)
- **`can_talk_to: [rates-analyst, fx-analyst, commodity-analyst, geopolitics-analyst, strategist, user]`**
  — the chief is the hub and the **only agent that can talk to `user`**. That last
  part is the whole point: keep the human-facing surface to one agent and put the
  paper-only gate in front of it.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command, e.g. a
  shell alias. Treat command strings as sensitive; they may embed keys.)
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`;
  the `capture: none` default is auto-upgraded to hook here).

### `rates-analyst` (type: `codex`)
- **`can_talk_to: [macro-chief]`** — reports its rates read back to the chief and
  nowhere else. It cannot reach the user, the other analysts, or the strategist.
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`
  (the `capture: none` default auto-upgrades to the notify hook).

### `fx-analyst` (type: `gemini`)
- **`can_talk_to: [macro-chief]`** — receives the event from the chief and returns
  the FX read to the chief only. It never touches the user.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **Turn detection:** `gemini` has no completion hook, so it relies on **pane
  polling** — the supervisor watches its pane for turn completion.

### `commodity-analyst` (type: `claude`)
- **`can_talk_to: [macro-chief]`** — returns the commodity read to the chief only.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook (auto-upgraded from `capture: none`).

### `geopolitics-analyst` (type: `gemini`)
- **`can_talk_to: [macro-chief]`** — returns the driver / tail-risk read to the
  chief only.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **Turn detection:** `gemini` → pane polling.

### `strategist` (type: `claude`)
- **`can_talk_to: [macro-chief]`** — the positioning proposal lives behind the
  chief: the strategist only ever talks to the chief, never to the user, so its
  PAPER view is always relayed and labeled through the hub.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook (auto-upgraded from `capture: none`).

### ACL enforcement

The ACL is **cooperative, not OS isolation** (Decision D15): agents have
filesystem access and *could* write straight into another inbox, but the
orchestrator only ever *releases* and *routes* mail between names on the sender's
`can_talk_to` list. Anything addressed outside that list is bounced back as a
`system` message filed in `failed/`, so a model that forgets the rule self-corrects
in-band. Here that means the four analysts and the strategist can *only* reach the
macro-chief, and only the chief can reach `user` — the paper-only gate is
structurally guaranteed to sit between the positioning proposal and the human.

### Per-type turn detection

Turn-completion detection is the system clock (the stop → sweep → route → release
→ nudge loop). It is **per `type`**:
- `claude` (`macro-chief`, `commodity-analyst`, `strategist`) → **Stop hook** —
  fires when Claude finishes a turn.
- `codex` (`rates-analyst`) → **`notify` hook** — fires when Codex finishes.
- `gemini` (`fx-analyst`, `geopolitics-analyst`) → **pane polling** — the
  supervisor reads the pane to decide the turn ended.

A `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
Claude) means completion never fires and the agent pins "busy" forever — which is
why `command` must launch the same CLI family `type` implies.

### What's *not* in this config
- **No `pings:` blocks.** Unlike some sibling swarms, this desk is entirely
  event-driven off your mail — there is no scheduled "market open" ping. (Add one
  per agent if you want a recurring macro brief; see below.)
- **No per-agent `capture` overrides.** The `defaults: capture: none` is
  auto-upgraded to the type's hook for claude/codex; gemini uses pane polling.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/macro-strategy-desk.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the `capture: none` auto-upgrade warnings
   for the claude/codex agents.
2. Creates the runtime dirs (`macro-strategy-desk-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/`
   `about.md` contact card *is* the ACL made visible: the macro-chief gets
   `outbox/rates-analyst/`, `outbox/fx-analyst/`, `outbox/commodity-analyst/`,
   `outbox/geopolitics-analyst/`, `outbox/strategist/`, `outbox/user/`; each spoke
   gets only `outbox/macro-chief/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `macro-chief`,
   `commodity-analyst`, `strategist`; the Codex `notify` hook for `rates-analyst`;
   the gemini agents are covered by pane polling.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents (and drives gemini's pane polling) so one stuck agent can't
   wedge the swarm.

At the end, `up` prints attach and **`serve`** hints. The `serve` line gives you
the mail-app control-plane UI (threads, live panes, send-as-user, availability
toggle). Drop `--host`/`--token` for the safe loopback-only `127.0.0.1` bind — the
UI can start processes, edit config, and type into agents that may run with
elevated permissions, so it must **never** be exposed on `0.0.0.0` without a
token. See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole event→four-analysts→strategist→user loop route mail with no API keys —
> the mechanics are identical.

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the finished desk note as mail (rather than have
it held), turn yourself available first:

```bash
./agentainer user available -c examples/macro-strategy-desk.yaml
```

This rewrites the `user` contact card in the macro-chief's `outbox/user/about.md`
to `Status: available`, so the chief sees you're reachable. (While away, mail to
you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the event into the swarm, addressed to the macro-chief:

```bash
./agentainer send --to macro-chief -c examples/macro-strategy-desk.yaml \
  "Event: the central bank surprised with a 50bp hike and signaled more to come. \
   Horizon: tactical (1-4 weeks). Give me the cross-asset read and proposed \
   positioning."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the macro-chief, then — because
the inbox was empty — **released into `inbox/`** and the chief is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the macro loop advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **macro-chief receives the event.** It reads `inbox/`, asks its one clarifying
   question if scope is ambiguous, then fans out the same event to all four
   analysts (writes into `outbox/rates-analyst/`, `outbox/fx-analyst/`,
   `outbox/commodity-analyst/`, `outbox/geopolitics-analyst/`). On stop, those
   route to the four analysts.
2. **the four analysts each write their read.** Each reads its inbox, writes its
   single-asset read, and reports back into `outbox/macro-chief/`. On stop, those
   route to the chief.
3. **macro-chief bundles and briefs the strategist.** It writes the four reads
   (unchanged) into `outbox/strategist/`. On stop, that routes to the strategist.
4. **strategist synthesizes.** It reads the bundle, writes ONE cross-asset thesis +
   a PAPER positioning view (opening with "PAPER / SIMULATED — NOT FINANCIAL
   ADVICE, NOT AN EXECUTABLE ORDER"), and reports back into `outbox/macro-chief/`.
   On stop, that routes to the chief.
5. **macro-chief assembles the desk note.** It writes the four reads + the
   strategist's proposal (positioning labeled PAPER) into `outbox/user/`. On stop,
   that's delivered to your `user` mailbox.
6. **you get the desk note** — visible with `agentainer user inbox`, or in the UI.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion. If you
never send anything, the agents just sit in standby.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/macro-strategy-desk.yaml
```

```
swarm: macro-strategy-desk   root: ./macro-strategy-desk-workspace
  macro-chief        (claude) up idle queue=0 unread=0 talks=rates-analyst, fx-analyst, commodity-analyst, geopolitics-analyst, strategist, user
  rates-analyst      (codex)  up idle queue=0 unread=1 talks=macro-chief
  fx-analyst         (gemini) up idle queue=0 unread=0 talks=macro-chief
  commodity-analyst  (claude) up idle queue=0 unread=0 talks=macro-chief
  geopolitics-analyst(gemini) up idle queue=0 unread=0 talks=macro-chief
  strategist         (claude) up idle queue=0 unread=0 talks=macro-chief
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/macro-strategy-desk.yaml          # whole swarm, last 20
./agentainer logs -c examples/macro-strategy-desk.yaml -f        # follow live
./agentainer logs strategist -c examples/macro-strategy-desk.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox macro-chief -c examples/macro-strategy-desk.yaml
```

Prints the one released message (headers + body), or `macro-chief: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue macro-chief -c examples/macro-strategy-desk.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach strategist -c examples/macro-strategy-desk.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails the framing. Because every message is natural-language
mail, you can steer the desk mid-flight through the `user` mailbox or by sending
notes into an agent's inbox.

- **Send a clarification to the macro-chief.** Realized you wanted strategic, not
  tactical? `./agentainer send --to macro-chief -c examples/macro-strategy-desk.yaml
  "Re-brief the analysts: horizon is strategic (6-18 months), not tactical."` The
  chief re-fans out and re-bundles to the strategist.
- **Ask an analyst to go deeper.** `./agentainer send --to macro-chief -c
  examples/macro-strategy-desk.yaml "Have the geopolitics-analyst spell out the
  escalation tail risk in one more paragraph."` The chief relays it down the chain.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want to
  nudge a specific agent without guessing its name.

When you're done (or want to try a different event), tear it down:

```bash
./agentainer down -c examples/macro-strategy-desk.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/macro-strategy-desk.yaml     # resume is the default
```

On `up`, Agentainer reads
`macro-strategy-desk-workspace/.agentainer/sessions.yaml` (written as each agent
finished its first turn) and reattaches the recorded conversations via each type's
native resume: `claude --resume <id>` for the claude agents (`macro-chief`,
`commodity-analyst`, `strategist`), `codex resume <id>` for the rates-analyst, and
the gemini sessions via their recorded ids. A resumed agent is *not* re-sent the
standby prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/macro-strategy-desk.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a fifth asset class (equities)
The desk covers rates / FX / commodities / geopolitics but not equities directly.
Add a dedicated equities analyst:

```yaml
  - name: equity-analyst
    type: codex
    can_talk_to: [macro-chief]
    command: "codex --yolo"
    role: |
      You are the EQUITY-ANALYST. Given the event + horizon, write a tight equity
      impact read ... index/sector direction, the rates/FX pass-through, the
      growth-vs-inflation split, the read (not an order), the risk. Cover equities
      ONLY. Report ONLY to the macro-chief.
```

Then add `equity-analyst` to the macro-chief's `can_talk_to` so it can be briefed,
and have the chief bundle five reads (not four) to the strategist.

### Add a scheduled macro brief
This config ships event-driven with no `pings:`. To have the chief produce a
recurring read (e.g. a Monday-open cross-asset brief), add a `pings:` block to the
macro-chief:

```yaml
  - name: macro-chief
    type: claude
    can_talk_to: [rates-analyst, fx-analyst, commodity-analyst, geopolitics-analyst, strategist, user]
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: |
          Weekly open. Summarize the dominant macro regime this week, fan out to
          the four analysts for the cross-asset read, synthesize via the strategist
          (PAPER positioning only), and post the desk note to user.
        cron: "0 8 * * 1"             # 08:00 every Monday
        when_busy: skip
```

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `rates-analyst: type: claude` (or `hermes`/`gemini`) to put the rates math on a
  different model than the chief.
- `strategist: type: codex` if you want the synthesis on Codex while keeping claude
  on the human-facing chief.
- Remember: `gemini`/`hermes` rely on **pane polling** (no completion hook), so
  they don't need (and shouldn't be given) a hook-based `capture`.

### Tune the ACL
- To let the `strategist` escalate straight to `user` (not only via the chief), add
  `user` to its `can_talk_to`. Mind that this widens the human-facing surface and
  bypasses the chief's single-funnel guarantee — the doc's convention keeps the
  chief the sole `user` contact so the paper-only gate always sits in front.
- To make an analyst unreachable from anyone but the chief (already the case here),
  leave its `can_talk_to: [macro-chief]` — that's the one-place-owns-the-gate
  guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader discussion
  of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md) for
  mixing model families safely.

---

## 10. Tips & footguns

- **Keep the macro-chief the only `user`-facing agent.** Only the chief lists `user`
  in `can_talk_to`. That gives you a single funnel: raw single-asset reads and the
  strategist's proposal always pass through the chief (and its PAPER labeling)
  before they reach you. If an analyst tried to mail `user` directly, the
  orchestrator bounces it (ACL) and drops a `system` note in their inbox explaining
  who they *can* message — the model self-corrects in-band.

- **The PAPER / SIMULATED label is the feature, not boilerplate.** The strategist
  opens its note with "PAPER / SIMULATED — NOT FINANCIAL ADVICE, NOT AN EXECUTABLE
  ORDER," and the chief must carry that label into the desk note. Do not "fix" this
  by widening ACLs or editing the strategist's risk framing — the label is how the
  human stays protected. This swarm produces a hypothetical study view only; it
  never places or recommends live trades.

- **Analysts own one asset class each.** rates/fx/commodity/geopolitics each cover
  ONLY their slice and report ONLY to the chief. The hub-and-spoke shape is what
  stops one asset-class read from being silently over-weighted — the four never
  coordinate directly, so their disagreement surfaces in the strategist's synthesis
  rather than being smoothed over in a back-channel.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch (e.g. a `claude` agent whose `command`
  doesn't launch Claude, or a `gemini` agent whose pane never settles) means
  completion never triggers and the agent pins "busy" forever. `status` showing an
  agent `busy` for a long time with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops — relevant if an analyst and the chief
  chatter past the gate.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime + mailboxes)
  and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/macro-strategy-desk.yaml
  ./agentainer remove-session -c examples/macro-strategy-desk.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files (the event notes you dropped in)
  or your config.

- **Availability shapes the ending.** If `user` is **away** when the chief finishes,
  your desk note is *held* (with a `system` "the user is away" ack to the chief)
  rather than lost — read it later with `agentainer user inbox` or flip yourself
  available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions (`--dangerously-skip-permissions`,
  `--yolo`).

- **`command` strings are sensitive.** They may embed API keys via shell aliases.
  Don't print or commit them. A swarm's disposable `root` matters for
  `--yolo`/`--dangerously-skip-permissions` runs.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- [`configuration.md`](../configuration.md) — the full `pings:` / `cron:` / `when_busy` grammar.
- `examples/macro-strategy-desk.yaml` — the config this walkthrough is built on.
- `examples/fp-and-a-analyst.yaml` — a sibling hub-and-spoke finance example (variance + memo).
- `examples/red-team-blue-team.yaml` — another hub-and-spoke with a human-facing hub + spokes that never coordinate.
- ProjectPlan.md — the design source of truth (mail model §4–§14).

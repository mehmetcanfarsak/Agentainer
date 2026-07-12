# Use case: the startup idea validator swarm

A concrete, end-to-end walkthrough of the shipped `examples/startup-validator.yaml`
swarm — a **`lead` hub** that stress-tests one startup idea across four lenses
(market, technical feasibility, financials, pitch) and returns a single verdict
to the founder. It's the "validate before you build" loop, wired entirely through
Agentainer's file-based mail model.

Everything below is based on the actual contents of
`examples/startup-validator.yaml` and the shipped CLI (`lib/cli.py`) and mailroom
(`lib/mail.py`). No API keys are needed to understand the mechanics; to run it
*for real* you supply the coding-CLI commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who it's for

- **Founders / solo builders** who have a one-line idea and need a fast, structured
  read on whether to quit their job for it — without hiring an analyst team.
- **Intrapreneurs** inside a company pitching a new product line, who need a
  defensible go / no-go memo for their boss.
- **Accelerator mentors and angel investors** who want a repeatable first-pass
  diligence on inbound decks.

The point is *structure, not certainty*: four different lenses, each from an agent
that can only talk back to one hub, so the analysis is sequenced and reconciled
instead of negotiated in a group chat.

---

## 2. The topology

```
                  idea
   user ───────────────▶ lead ◀──┬──▶ market       (TAM/SAM, competition, pain)
          ▲           hub/sequencer ├──▶ feasibility  (build risk, MVP scope)
          │ pitch + risks          ├──▶ financials   (unit economics, 3-yr model)
          └──────────── pitch ◀────┘──▶ pitch        (deck narrative + risks)
```

Five agents, one directed flow:

1. **`user` → `lead`** — the founder sends the idea ("Validate: an AI that
   summarizes compliance docs for banks.").
2. **`lead` → `market` / `feasibility` / `financials`** — the lead restates the
   idea and briefs the three analysts **separately**. They each report only back
   to the lead (their `can_talk_to` is just `[lead]`).
3. **`lead` → `pitch`** — when all three have reported, the lead reconciles them
   into a **GO / GO-IF / NO-GO** verdict and hands it to `pitch`.
4. **`pitch` → `user`** — `pitch` turns the verdict into a founder-facing pitch
   narrative + an honest risks section, and delivers it to the human.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. An agent can only deliver to names on its own list; anything
else is bounced back as a `system` message and filed in `failed/` (see §8).

> Note the ACL shape: `lead` can reach `user`, but so can `pitch`. Only `pitch`
> delivers the final story to the human, while `lead` is the one who *receives*
> the idea. The two human-facing agents keep the funnel clean — the founder sends
> to `lead`, hears back from `pitch`.

---

## 3. The config, explained

Here is `examples/startup-validator.yaml` in full (commands shown are real CLIs;
see the key-free note at the end of the header for the no-key demo):

```yaml
# 🚀 Startup validator -- a `lead` hub validates an idea across four lenses.
# Key-free: swap each `command` for a mock bash loop and the swarm routes mail
# with NO API keys.
swarm:
  name: startup-validator
  root: ./startup-validator-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: lead
    type: claude
    can_talk_to: [market, feasibility, financials, pitch, user]
    command: "claude --dangerously-skip-permissions"
    role: "The LEAD validator: sequence market/feasibility/financials, merge into a GO/GO-IF/NO-GO verdict, hand to pitch."
  - name: market
    type: claude
    can_talk_to: [lead]
    command: "claude --dangerously-skip-permissions"
    role: "MARKET analyst: size TAM/SAM with assumptions, name incumbents + substitutes, say buyer/pain/price. Report to lead."
  - name: feasibility
    type: codex
    can_talk_to: [lead]
    command: "codex --yolo"
    role: "FEASIBILITY analyst: rate build risk solved/risky/research, scope the MVP, estimate engineer-weeks. Report to lead."
  - name: financials
    type: claude
    can_talk_to: [lead]
    command: "claude --dangerously-skip-permissions"
    role: "FINANCIAL analyst: unit economics, cost to build/run, rough 3-yr model; state assumptions. Report to lead."
  - name: pitch
    type: claude
    can_talk_to: [lead, user]
    command: "claude --dangerously-skip-permissions"
    role: "PITCH writer: turn the merged verdict into a founder-facing narrative + honest risks; deliver to user."
```

(The shipped file has the full multi-line `role:` prompts; the above is the
compact form for readability. The behavior is identical.)

Field by field:

### `swarm`
- **`name: startup-validator`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./startup-validator-workspace`** — parent directory for each agent's
  working directory and mailboxes. Each agent gets
  `startup-validator-workspace/<name>/` as its workdir (created on `up`), and its
  mailbox folders live alongside. Orchestrator state goes under
  `startup-validator-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, and it's ultimately keyed off each
  agent's `type`. For `claude` and `codex`, whose CLIs support a completion
  **hook**, setting `capture: none` is a footgun — so the config loader *upgrades*
  it back to `hook` and prints a warning at `up`
  (`capture: none on a claude agent gives the orchestrator no way to detect turn
  completion; using the type's default: capture: hook.`). Net effect here: with
  real commands, every agent uses its hook; with **mock** commands you'd drop
  `capture: none` on purpose because a bash loop has no hook to fire.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `lead` (type: `claude`)
The hub. **`can_talk_to: [market, feasibility, financials, pitch, user]`** — it can
brief the three analysts, hand the verdict to `pitch`, and it is the agent that
**receives the idea from `user`**. Its `role` carries a short MAILBOX reminder
(read `inbox/`, move handled mail to `read/`, write to `outbox/<name>/`, read
`about.md` first). **Turn detection:** `claude` → a **Stop hook** (installed
automatically at `up`).

### `market` (type: `claude`)
- **`can_talk_to: [lead]`** — can only answer the lead. It cannot reach `user` or
  the other analysts directly, so the market read always flows back through the
  hub for reconciliation.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command (treat command strings as sensitive; they may embed keys).
- **Turn detection:** `claude` → Stop hook.

### `feasibility` (type: `codex`)
- **`can_talk_to: [lead]`** — reports only upward to the lead.
- **`command: "codex --yolo"`** — placeholder launch command for Codex.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `financials` (type: `claude`)
- **`can_talk_to: [lead]`** — same spoke shape as `market`.
- **`command: "claude --dangerously-skip-permissions"`**.
- **Turn detection:** `claude` → Stop hook.

### `pitch` (type: `claude`)
- **`can_talk_to: [lead, user]`** — the only agent besides `lead` that can reach
  `user`, and the one that *delivers* the final story. It reads the lead's merged
  verdict and writes the founder-facing narrative + risks to `outbox/user/`.
- **`command: "claude --dangerously-skip-permissions"`**.
- **Turn detection:** `claude` → Stop hook.

### What's *not* in this config
- **No `periodically_ping_seconds`.** None of the five agents has a periodic ping,
  so the swarm is purely event-driven off real mail. If you wanted the lead to
  poke a slow `market` agent, you'd add `periodically_ping_seconds: 300` to it.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/startup-validator.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade).
2. Creates the runtime dirs (`startup-validator-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: `lead` gets
   `outbox/market/`, `outbox/feasibility/`, `outbox/financials/`,
   `outbox/pitch/`, `outbox/user/`; `market` gets `outbox/lead/`; and so on.
4. **Installs per-type turn detection** — the Claude Stop hooks for `lead`,
   `market`, `financials`, `pitch`; the Codex `notify` hook for `feasibility`.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'startup-validator' is up with 5 agent(s)
:: attach with:  tmux attach -t <lead-session>
:: you can use the UI with:  agentainer serve -c examples/startup-validator.yaml
```

The `serve` line launches the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). It binds **`127.0.0.1` by default** — never
`0.0.0.0` — and is opt-in; add `--host`/`--token`/`--port` only for a remote
bind. See the [`ui-guide.md`](../ui-guide.md) and CLAUDE.md §18.

> **Key-free demo:** swap each `command:` for a mock bash loop (e.g.
> `bash -c 'while true; do read x; done'`) and you can watch the whole pipeline
> route mail with no API keys — the mechanics are identical. (With mocks, the
> loader keeps the agents `capture: none` so the swarm doesn't wait on a hook
> that will never fire.)

---

## 5. Drive it: send the idea

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. You *can* send the idea while away — it lands in `lead`'s inbox and the
review runs — but if you want to *receive* the final pitch as mail (rather than
have it held), turn yourself available first:

```bash
./agentainer user available -c examples/startup-validator.yaml
```

This rewrites the `user` contact card in `pitch`'s `outbox/user/about.md` to
`Status: available`, so `pitch` sees you're reachable. (While away, the final
pitch is *held* with a `system` ack — nothing is lost; read it later with
`agentainer user inbox`, or flip yourself available and it's delivered.)

Now send the idea into the swarm, addressed to the lead:

```bash
./agentainer send --to lead "Validate: an AI that summarizes compliance docs for banks."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the lead, then — because the
inbox was empty — **released into `inbox/`** and the lead is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

---

## 6. The mail flowing

Watching the log (§7), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **lead receives the idea.** It reads `inbox/`, restates the idea, and briefs
   `market`, `feasibility` and `financials` — three separate files into the
   respective `outbox/<name>/` folders. Its turn ends; the orchestrator sweeps the
   outbox and nudges the three analysts.
2. **the three analysts work in parallel.** Each reads its inbox, does its lens,
   and writes a report into `outbox/lead/`. As each stops, its report routes back
   to the lead.
3. **lead reconciles.** Once all three are in, the lead reads them, merges them
   into a **GO / GO-IF / NO-GO** verdict with the deciding facts, and writes it
   into `outbox/pitch/`. On stop, that routes to `pitch`.
4. **pitch delivers.** It reads the verdict, writes the founder-facing narrative +
   honest risks into `outbox/user/`. On stop, that's delivered to your `user`
   mailbox (you'll see it with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send an idea, the agents just sit in standby (that's the point of
> the standby prompt). The pipeline only moves when real mail arrives — this swarm
> has no periodic pings to self-start it.

---

## 7. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/startup-validator.yaml
```

```
swarm: startup-validator   root: ./startup-validator-workspace
  lead (claude) up idle queue=0 unread=0 talks=market, feasibility, financials, pitch, user
  market (claude) up busy queue=0 unread=1 talks=lead
  feasibility (codex) up busy queue=0 unread=1 talks=lead
  financials (claude) up busy queue=0 unread=1 talks=lead
  pitch (claude) up idle queue=0 unread=0 talks=lead, user
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/startup-validator.yaml          # whole swarm, last 20
./agentainer logs -c examples/startup-validator.yaml -f        # follow live
./agentainer logs lead -c examples/startup-validator.yaml      # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox market -c examples/startup-validator.yaml
```

Prints the one released message (headers + body), or `market: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue lead -c examples/startup-validator.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach lead -c examples/startup-validator.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 8. Iterate on the verdict

The lead's merged verdict is the artifact worth arguing with. If you disagree with
the call, or want a deeper cut on one lens, just reply by mail:

```bash
./agentainer send --to lead "Your GO-IF hinged on CAC. Have financials redo the model assuming a 2x higher CAC and tell me if it flips to NO-GO."
```

The lead re-briefs `financials`, reconciles again, and routes an updated verdict to
`pitch`. Because resume is on by default (see §9), the lead still has the earlier
exchange in context — this is a *conversation*, not a one-shot.

You can also steer the *style* of the output without editing the config: ask
`pitch` for a different format via the lead, e.g. "have pitch write it as a
one-page memo, not a deck narrative."

---

## 9. Resume after a stop

Tear the swarm down when you're done:

```bash
./agentainer down -c examples/startup-validator.yaml
```

Bring it back later and **conversations resume by default**:

```bash
./agentainer up -c examples/startup-validator.yaml     # resume is the default
```

On `up`, Agentainer reads `startup-validator-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for `lead`,
`market`, `financials`, `pitch`; `codex resume <id>` for `feasibility`. Resumed
agents are *not* re-sent the standby prompt (their prior context is restored), so
your earlier verdict round still lives in the hub's memory.

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/startup-validator.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 10. Customize

### Add a `legal` / `gdpr` agent
A regulated idea (the example is *banks* + *compliance*) often needs a compliance
lens. Add an agent and wire it into the hub:

```yaml
  - name: legal
    type: claude
    can_talk_to: [lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the LEGAL/GCPR analyst. Given the idea, flag the regulatory and
      data-protection landmines (banking confidentiality, GDPR/CCPA, audit
      retention, required approvals). Rate each as blocker / manageable / minor,
      and name the one thing most likely to delay launch. Report to lead.
```

Then add `legal` to `lead`'s `can_talk_to` and have the lead brief it alongside
the other three. Because `legal` lists only `[lead]`, it slots in with no other
ACL changes.

### Swap models
Mix providers to match each lens (a multi-LLM swarm). The only rule: `type` must
match `command` (a mismatch wedges the agent — see footguns). E.g. make `market`
a `gemini` pane-captured agent:

```yaml
  - name: market
    type: gemini
    can_talk_to: [lead]
    capture: pane
    command: "gemini --yolo"
    role: "MARKET analyst (gemini): size TAM/SAM, name competitors, say buyer/pain/price. Report to lead."
```

`feasibility` already demonstrates a `codex` worker. See
[`multi-llm-swarm.md`](../use-cases/multi-llm-swarm.md) for the full pattern.

### Tune the ACL
- To keep **all** human contact inside `lead` (no `pitch → user`), drop `user`
  from `pitch`'s `can_talk_to`. The lead then forwards the final pitch to `user`
  itself. Anything `pitch` tries to mail `user` is bounced as a `system` message
  and filed in `failed/` — the model self-corrects in-band.
- To let the analysts cross-check each other, add peers to their `can_talk_to` —
  but you lose the clean "everything reconciles in the hub" property, and risk
  two agents negotiating scope instead of the lead deciding it.

---

## 11. Tips & footguns

- **Keep `lead` the only idea-receiver and `pitch` the only story-sender.** In
  this config `lead` is the one with `user` *inbound*, `pitch` the one with
  `user` *outbound*. That gives you a single point of contact in and out, and a
  clean funnel: raw analyses always pass through the lead's reconciliation before
  they reach you. If `market` tries to mail `user` directly, the orchestrator
  bounces it (ACL) and drops a `system` note in `market`'s inbox explaining who
  it *can* message — the model self-corrects in-band.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch (e.g. a `claude` agent whose `command`
  doesn't launch Claude) means completion never triggers and the agent pins
  "busy" forever. `status` showing an agent `busy` for a long time with `unread`
  mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived
  so the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s)
  to kill "thanks!/you're welcome!" loops.

- **A slow lens is the usual bottleneck.** If `feasibility` is still grinding and
  `lead` is blocked, you can nudge it along or, if its pane capture never
  registered, force-idle it:
  ```bash
  ./agentainer idle feasibility -c examples/startup-validator.yaml
  ```

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/startup-validator.yaml
  ./agentainer remove-session -c examples/startup-validator.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

- **Availability shapes the ending.** If `user` is **away** when `pitch` finishes,
  your final pitch is *held* (with a `system` "the user is away" ack to `pitch`)
  rather than lost — read it later with `agentainer user inbox` or flip yourself
  available and it's delivered.

- **Don't print secrets.** `command:` strings may embed API keys via shell
  aliases; never `echo` or commit them. The UI binds `127.0.0.1` by default (see
  §4) and is opt-in.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the file-based inbox/outbox/read/sent model.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resume conversations across `up`/`down`.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — the hub-and-spoke pattern this swarm extends.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing claude/codex/gemini/hermes across lenses.
- `examples/startup-validator.yaml` — the config this guide is built from.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

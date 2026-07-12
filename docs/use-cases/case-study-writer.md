# Use case: the case-study writer swarm

A concrete, end-to-end walkthrough of the shipped `examples/case-study-writer.yaml`
swarm — a four-agent pipeline where an **interview-prep lead** turns a customer win
into one publish-ready case study: it preps the interview questions, an analyst
crunchs the metrics, a writer drafts the narrative, and a quote-puller mines the
best customer quotes, all reconciled through a single owner. It's the canonical
"direct the work → do the work → ship the work" loop, wired entirely through
Agentainer's file-based mail model.

If you came here searching for a **customer case study template** or "how do I
write a case study," this page shows you the machine that produces one — a
repeatable, Agentainer-orchestrated pipeline you can run with zero API keys for
the mechanics and your real coding-CLI commands for the output.

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The topology

```
            customer win
   user ───────────────▶ interview_prep ◀──┬──▶ metrics_analyst
          (final)     hub lead             ├──▶ narrative_writer
                                              └──▶ quote_puller
   ...analyst/writer/puller never talk to each other; only the hub talks to user.
```

Four agents, one directed flow:

1. **`user` → `interview_prep`** — you send the customer win.
2. **`interview_prep` → `metrics_analyst`** — the lead drafts the interview
   questions and asks for the validated before/after numbers.
3. **`interview_prep` → `narrative_writer`** — the brief plus the metrics become a
   draft narrative (challenge → solution → results).
4. **`interview_prep` → `quote_puller`** — the interview material and draft yield
   the strongest customer quotes.
5. **`interview_prep` → `user`** — the lead reviews draft + quotes and delivers the
   final case study.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. An agent can only deliver to names on its own list; anything
else is bounced back as a `system` message and filed in `failed/` (see §7).

---

## 2. The config, explained

Here is `examples/case-study-writer.yaml` in full:

```yaml
# 📄 Case-study writer -- an interview-prep hub turns a customer win into one
# publish-ready case study: prep the questions, crunch the metrics, write the
# narrative, and pull the quotes -- all funnelled through a single owner.
# Key-free: swap each `command` for a mock bash loop and the swarm routes mail
# with NO API keys.
swarm:
  name: case-study-writer
  root: ./case-study-writer-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: interview_prep
    type: claude
    can_talk_to: [metrics_analyst, narrative_writer, quote_puller, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the INTERVIEW-PREP LEAD and owner of the finished case study...
  - name: metrics_analyst
    type: claude
    can_talk_to: [interview_prep]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the METRICS ANALYST. Turn the results into a defensible before/after...
  - name: narrative_writer
    type: claude
    can_talk_to: [interview_prep]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the NARRATIVE WRITER. Draft the challenge → solution → results arc...
  - name: quote_puller
    type: codex
    can_talk_to: [interview_prep]
    command: "codex --yolo"
    role: |
      You are the QUOTE PULLER. Mine the strongest customer quotes...
```

Field by field:

### `swarm`
- **`name: case-study-writer`** — the swarm's name (shows up in `status`, logs, sessions).
- **`root: ./case-study-writer-workspace`** — parent directory for the agents'
  working directories and mailboxes. Each agent gets
  `case-study-writer-workspace/<name>/` as its workdir (created on `up`), and its
  mailbox folders live alongside. Orchestrator state goes under
  `case-study-writer-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. But note: `capture` is
  how Agentainer knows a turn finished, keyed off each agent's `type`. For
  `claude` and `codex`, whose CLIs support a completion **hook**, setting
  `capture: none` is a footgun — so the config loader *upgrades* it back to
  `hook` and prints a warning at `up`. Net effect here: all four agents use
  `capture: hook`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `interview_prep` (type: `claude`)
- **`can_talk_to: [metrics_analyst, narrative_writer, quote_puller, user]`** — the
  hub: it can delegate to all three workers *and* it is the **only agent that can
  talk to `user`**. Keep the human-facing surface to this one agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command. Treat command
  strings as sensitive; they may embed keys.)
- **`role`** — the standing identity: owner of the case study, the person who
  sequences interview prep → metrics → narrative → quotes → final.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `metrics_analyst`, `narrative_writer` (type: `claude`)
- **`can_talk_to: [interview_prep]`** — each can only report back to the hub. They
  never talk to each other, so the numbers, the prose, and the quotes are
  reconciled by the lead instead of three agents negotiating.
- **`role`** — analyst produces a defensible before/after metric set; writer
  drafts the narrative arc into `DRAFT.md`.
- **Turn detection:** `claude` → Stop hook.

### `quote_puller` (type: `codex`)
- **`can_talk_to: [interview_prep]`** — reports the mined quotes upward only.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "mine the strongest customer quotes with attribution; never fabricate."
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### What's *not* in this config
- **No `periodically_ping_seconds`.** None of the four agents has a periodic ping,
  so the pipeline is purely event-driven off real mail.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **No shared workdir.** Each agent has its own directory, so the writer's
  `DRAFT.md` and the analyst's metrics note live separately; the lead coordinates
  them by mail. Quoting the workdir inside `role` is unnecessary — every nudge
  gives the model its exact mailbox paths.

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/case-study-writer.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for all four agents).
2. Creates the runtime dirs (`case-study-writer-workspace/.agentainer/…`).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, and an `outbox/<peer>/` folder **for each
   allowed recipient** with an `about.md` contact card (the ACL made visible).
4. **Installs per-type turn detection** — the Claude Stop hooks for the three
   `claude` agents, the Codex `notify` hook for `quote_puller`.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles
   stale/dead/silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints. The `serve` line gives you
the mail-app control-plane UI (threads, live panes, send-as-user, availability
toggle). Drop `--host`/`--token` for the safe loopback-only bind.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole pipeline route mail with no API keys — the mechanics are identical.
> (The YAML's `capture: none` is exactly what makes mock loops valid: they don't
> fire a hook, and the loader upgrades them to `hook` for real CLIs.)

---

## 4. Drive a case study

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the final case study as mail, turn yourself
available first:

```bash
./agentainer user available -c examples/case-study-writer.yaml
```

Now send the customer win into the swarm, addressed to the hub:

```bash
./agentainer send --to interview_prep "Write a case study on Acme Corp: they cut onboarding time 60% with our API."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped
`From: user` with a fresh id, enqueued for `interview_prep`, then — because the
inbox was empty — **released into `inbox/`** and the lead is **nudged**.

### The mail flowing

1. **interview_prep receives the win.** It drafts the interview questions and
   writes a metrics ask into `outbox/metrics_analyst/`. On stop, that routes to
   the analyst and nudges it.
2. **metrics_analyst crunches numbers.** It reads its inbox, returns the
   before/after metric set into `outbox/interview_prep/`. On stop, routes back to
   the lead.
3. **narrative_writer drafts.** The lead passes brief + metrics to
   `outbox/narrative_writer/`; the writer returns `DRAFT.md` to the lead.
4. **quote_puller mines quotes.** The lead passes material to
   `outbox/quote_puller/`; the puller returns attributed quotes to the lead.
5. **interview_prep finalizes.** It reviews draft + quotes and writes the final
   case study into `outbox/user/`. On stop, that's delivered to your `user` mailbox.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

---

## 5. Observe

```bash
./agentainer status -c examples/case-study-writer.yaml
./agentainer logs -c examples/case-study-writer.yaml -f
./agentainer inbox narrative_writer -c examples/case-study-writer.yaml
./agentainer attach metrics_analyst -c examples/case-study-writer.yaml
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event in the durable log.

---

## 6. Resume after a stop

```bash
./agentainer down -c examples/case-study-writer.yaml
./agentainer up   -c examples/case-study-writer.yaml     # resume is the default
```

On `up`, Agentainer reads `case-study-writer-workspace/.agentainer/sessions.yaml`
and reattaches recorded conversations (`claude --resume <id>`,
`codex resume <id>`). A resumed agent is *not* re-sent the standby prompt. Pass
`--no-resume` to force everyone fresh; inspect with `agentainer sessions`.

---

## 7. Tips & footguns

- **Keep `interview_prep` the only `user`-facing agent.** Only the hub lists
  `user` in `can_talk_to`. That gives you a single point of contact and a clean
  funnel: raw metrics, draft, and quotes always pass through the lead before they
  reach you. If a worker tries to mail `user` directly, the orchestrator bounces
  it (ACL) and drops a `system` note in its inbox explaining who it *can* message.
- **Watch the stop → nudge loop.** The whole clock runs on turn completion. A
  `type`/`command` mismatch means completion never triggers and the agent pins
  "busy" forever — `status` showing `busy` with `unread` mail is the tell.
- **Nudges re-inject the protocol every time**, including the allowed-recipient
  list, so a forgetful model can't wedge the swarm. Mail moved to `read/` is
  best-effort; auto-archive after repeated presentations and a per-pair runaway
  cap (≤20 msgs / 60s) prevent loops.
- **Force-idle if a turn never registers:**
  ```bash
  ./agentainer idle metrics_analyst -c examples/case-study-writer.yaml
  ```
- **`remove-session` to reset:**
  ```bash
  ./agentainer down           -c examples/case-study-writer.yaml
  ./agentainer remove-session -c examples/case-study-writer.yaml
  ```

---

### Search-intent quick answers

- **What is a customer case study template?** A structured before/after story
  (challenge → solution → results) backed by real metrics and a customer quote.
  This swarm produces one: `metrics_analyst` supplies the numbers,
  `narrative_writer` writes the arc, `quote_puller` supplies the voice.
- **How do I write a case study with AI agents?** Run this config, send one
  customer win to the hub, and let the four agents decompose it — no manual
  relay, the orchestrator sequences every hand-off.
- **Why use a hub-and-spoke case-study pipeline?** Because metrics, prose, and
  quotes are different skills that fight when drafted in one context; separating
  them (and reconciling through one owner) yields a tighter, better-sourced piece.

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`cli-reference.md`](../cli-reference.md) — every subcommand and flag.
- `examples/case-study-writer.yaml` — the config described here.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

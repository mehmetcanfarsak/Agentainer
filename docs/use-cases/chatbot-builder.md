# Use case: the chatbot builder

A concrete, end-to-end walkthrough of the shipped `examples/chatbot-builder.yaml`
swarm — a five-agent team that turns a one-line request ("I want a bot that does
X") into a ready-to-ship conversational agent: an **intent map and flows**,
**written dialog**, a consistent **persona**, and an adversarial **roleplay test
pass** before anything reaches you. It's the "brief → build in parallel →
stress-test → deliver one package" loop, wired entirely through Agentainer's
file-based mail model.

**Who this is for:** product managers scoping a support or onboarding bot;
support and success leads who know the questions users actually ask; and builders
who want the conversation design (intents, scripts, voice, edge cases) done
before they wire it into Rasa, Dialogflow, a custom LLM prompt, or a widget SDK.
You bring the domain and the "done" bar; the swarm produces the design artifacts.

Everything below is based on the actual contents of `examples/chatbot-builder.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you supply the coding-CLI
commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the deeper [`mail-model.md`](../mail-model.md). The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The topology

```
        build a bot for X
  user ────────────────────▶ architect ──┬────────────▶ intent_designer
        (finished bot)  ◀────────┘        │                  │ flows
                                          ├────────────▶ persona
                                          │                  │ voice
                                          │              (both feed)
                                          ├────────────▶ dialog_writer
                                          │                  │ scripts
                                          └────────────▶ tester
                                                          gaps ▲
```

Five agents, one hub, a deliberate fan-out/fan-in:

1. **`user` → `architect`** — you send the bot's purpose.
2. **`architect` → `intent_designer` + `persona`** — the architect turns the
   request into a brief and kicks off the "what can it do" and "how does it sound"
   work in parallel.
3. **`intent_designer` → `dialog_writer`** and **`persona` → `dialog_writer`** —
   the writer needs both the flows *and* the voice before it can write words.
4. **`dialog_writer` → `architect`** — the assembled scripts come back to the hub.
5. **`architect` → `tester`** — the architect hands the assembled bot for a
   roleplay pass; **`tester` → `architect`** returns a prioritized gap list.
6. **`architect` → `user`** — the architect delivers a single package: intents,
   flows, scripts, persona guide, and the known gaps.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. An agent can only deliver to names on its own list; anything
else is bounced back as a `system` message and filed in `failed/` (see §7). The
key discipline: **only the architect talks to `user`**, so specialists can't ship
half-answers straight to the human.

---

## 2. The config, explained

Here is the shape of `examples/chatbot-builder.yaml` (roles trimmed for space —
the file has the full standing instructions):

```yaml
swarm:
  name: chatbot-builder
  root: ./chatbot-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: architect
    type: claude
    can_talk_to: [intent_designer, dialog_writer, persona, tester, user]
    command: "claude --dangerously-skip-permissions"
    role: "You are the CHATBOT ARCHITECT and the single point of contact ..."
  - name: intent_designer
    type: claude
    can_talk_to: [architect, dialog_writer]
    command: "claude --dangerously-skip-permissions"
    role: "You are the INTENT DESIGNER. Define intents, slots, and flows ..."
  - name: dialog_writer
    type: codex
    can_talk_to: [intent_designer, persona, architect]
    command: "codex --yolo"
    role: "You are the DIALOG WRITER. Turn flows into the exact words ..."
  - name: persona
    type: claude
    can_talk_to: [architect, dialog_writer]
    command: "claude --dangerously-skip-permissions"
    role: "You are the PERSONA designer. Give the bot a coherent voice ..."
  - name: tester
    type: claude
    can_talk_to: [architect]
    command: "claude --dangerously-skip-permissions"
    role: "You are the CONVERSATION TESTER. Roleplay users and report gaps ..."
```

Field by field:

### `swarm`
- **`name: chatbot-builder`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./chatbot-workspace`** — the parent directory for the agents' working
  directories and mailboxes. Each agent gets `chatbot-workspace/<name>/` as its
  workdir (created on `up`), and its mailbox folders live alongside. Orchestrator
  state goes under `chatbot-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, and it's keyed off each agent's `type`.
  For `claude` and `codex`, whose CLIs support a completion **hook**, setting
  `capture: none` is a footgun — so the config loader *upgrades* it back to `hook`
  and prints a warning at `up`. Net effect here: every agent gets its natural
  hook-based detection, and you'll see one upgrade warning per agent.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent states
  its own list explicitly, so this default is just a safe floor.

### `architect` (type: `claude`)
- **`can_talk_to: [intent_designer, dialog_writer, persona, tester, user]`** — the
  hub. It briefs all four specialists and is the **only agent that can talk to
  `user`**. Keep the human-facing surface to a single agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the architect waits for your request instead of
  proactively mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `intent_designer` (type: `claude`)
- **`can_talk_to: [architect, dialog_writer]`** — it reports flows up to the
  architect and hands them across to the dialog_writer. It deliberately cannot
  reach `user`.
- **`role`** — defines the intent list (utterances + slots), the flows (happy path
  plus missing-slot/ambiguity/change-of-mind branches), and the fallback +
  human-handoff path, in `INTENTS.md`.

### `dialog_writer` (type: `codex`)
- **`can_talk_to: [intent_designer, persona, architect]`** — it needs *both*
  inputs: the flows from intent_designer and the voice rules from persona. It
  writes the actual bot copy into `SCRIPTS.md`, keyed to flow node ids.
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.
- Using a *different* model here (Codex) than the Claude specialists is
  deliberate — see [`multi-llm-swarm.md`](./multi-llm-swarm.md) for why mixing
  model families across roles is a feature, not an accident.

### `persona` (type: `claude`)
- **`can_talk_to: [architect, dialog_writer]`** — it publishes the voice guide up
  to the architect and coordinates directly with the writer when a line fights the
  tone. Produces `PERSONA.md` (name, tone, do/don't examples, boundaries).

### `tester` (type: `claude`)
- **`can_talk_to: [architect]`** — the narrowest ACL: the tester only reports the
  gap list upward to the architect. It cannot reach the writer directly, so fixes
  are always routed and prioritized through one place (the architect re-briefs
  dialog_writer). Produces failing transcripts classified by defect type.

### What's *not* in this config
- **No `pings`.** No agent is auto-nudged on a timer while
  idle — the pipeline is purely event-driven off real mail. (If you wanted the
  architect to poke a slow tester, you'd add a `pings` cron rule
  to it.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/chatbot-builder.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for each agent).
2. Creates the runtime dirs (`chatbot-workspace/.agentainer/…`: log, queue, run,
   sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: the architect gets an outbox
   for all four specialists plus `user`; the tester gets only `outbox/architect/`.
4. **Installs per-type turn detection** — the Claude Stop hook for the four Claude
   agents, the Codex `notify` hook for dialog_writer.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'chatbot-builder' is up with 5 agent(s)
:: attach with:  tmux attach -t <architect-session>
:: you can use the UI with:  agentainer serve -c examples/chatbot-builder.yaml
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). It **binds `127.0.0.1` by default** — keep it
loopback-only unless you deliberately opt into a remote bind with a token (see
[`../ui-guide.md`](../ui-guide.md) and `remote-access.md`). See the `README.md`
"control-plane UI" section.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole pipeline route mail with no API keys — the mechanics are identical.

---

## 4. Drive a request

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the architect's finished bot as mail (rather
than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/chatbot-builder.yaml
```

This rewrites the `user` contact card in the architect's `outbox/user/about.md`
to `Status: available`, so the architect sees you're reachable. (While away, mail
to you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the request into the swarm, addressed to the architect:

```bash
./agentainer send --to architect \
  "Build a support bot for a food-delivery app. It should track orders, start
   refunds, and answer 'are you open now?'. Channel: web widget. Tone: friendly
   but fast. Done = every intent has a script and a fallback, tested."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the architect, then — because the
inbox was empty — **released into `inbox/`** and the architect is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§5), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **architect receives the request.** It reads `inbox/`, writes a brief, and
   drops one file into `outbox/intent_designer/` and one into `outbox/persona/`.
   On stop, both route and both specialists are nudged.
2. **intent_designer and persona work in parallel.** Each reads its inbox, writes
   its artifact (`INTENTS.md` / `PERSONA.md`), and mails `dialog_writer` (and a
   summary to `architect`).
3. **dialog_writer writes the scripts.** Once it has both the flows and the voice,
   it writes `SCRIPTS.md` and reports to `architect`.
4. **architect runs the test gate.** It assembles the bot and mails `tester`;
   tester roleplays users, writes failing transcripts, and returns a prioritized
   gap list to `architect`.
5. **architect closes gaps and delivers.** It re-briefs `dialog_writer` on the
   fixable gaps, then writes the finished package into `outbox/user/`. On stop,
   that's delivered to your `user` mailbox (you'll see it with
   `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a request, the agents just sit in standby (that's the point
> of the standby prompt). The pipeline only moves when real mail arrives — this
> swarm has no periodic pings to self-start it.

---

## 5. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/chatbot-builder.yaml
```

```
swarm: chatbot-builder   root: ./chatbot-workspace
  architect (claude) up idle queue=0 unread=0 talks=intent_designer, dialog_writer, persona, tester, user
  intent_designer (claude) up idle queue=0 unread=1 talks=architect, dialog_writer
  dialog_writer (codex) up idle queue=0 unread=0 talks=intent_designer, persona, architect
  persona (claude) up idle queue=0 unread=1 talks=architect, dialog_writer
  tester (claude) up idle queue=0 unread=0 talks=architect
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/chatbot-builder.yaml            # whole swarm, last 20
./agentainer logs -c examples/chatbot-builder.yaml -f          # follow live
./agentainer logs dialog_writer -c examples/chatbot-builder.yaml  # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox dialog_writer -c examples/chatbot-builder.yaml
```

Prints the one released message (headers + body), or
`dialog_writer: inbox is empty`.

**Queue depth** — mail waiting behind the one released message (the writer often
has one queued behind the other, since both intent_designer and persona mail it):

```bash
./agentainer queue dialog_writer -c examples/chatbot-builder.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach tester -c examples/chatbot-builder.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 6. Iterate

A first pass rarely nails the bot. Because the architect is your single door, you
iterate by just sending it more mail — the swarm re-runs the relevant legs:

```bash
./agentainer send --to architect \
  "Users keep asking about delivery ETA — add that intent, and make the refund
   flow ask for the reason before confirming."
```

The architect re-briefs `intent_designer` (new intent + changed flow), which
cascades to `dialog_writer`, back through `tester`, and out to you — the same
pipeline, driven by a new message. Nothing resets; each agent keeps its
conversation context (see §7 on resume), so "add an intent" is understood against
everything already built.

Two other iteration levers:

- **Tighten the persona mid-build.** Send the architect a note ("too chatty on
  errors — one apology, then the fix") and it re-briefs `persona`, whose updated
  do/don't list dialog_writer applies on the next pass.
- **Re-run only the test gate.** Ask the architect to have `tester` re-roleplay a
  specific flow after a fix ("re-test the refund path with a user who refuses to
  give an order id"). The tester's job is deliberately repeatable.

---

## 7. Customize

- **Add an `integrations` agent.** A design is only half the bot — the other half
  is the backend it calls (order lookup, refund API, store-hours service). Add a
  specialist that maps each intent to the API calls and payloads it needs:

  ```yaml
    - name: integrations
      type: codex
      can_talk_to: [intent_designer, architect]
      command: "codex --yolo"
      role: |
        You are the INTEGRATIONS engineer. For each intent in INTENTS.md, define
        the backend call it needs (endpoint, request payload from the captured
        slots, the response fields the bot reads back) in INTEGRATIONS.md. Flag
        any intent whose data the backend cannot supply so the flow can degrade
        gracefully. Coordinate with intent_designer on slot names; report to
        architect.
  ```

  Then add `integrations` to the architect's `can_talk_to` so it can brief and
  receive from it. Keep its ACL narrow (it doesn't need `dialog_writer` or `user`).

- **Swap models per role.** `type` and `command` are independent of the role, so
  you can put the reasoning-heavy design work on one model and the bulk copywriting
  on another. This config already mixes `claude` and `codex`; change any agent to
  `gemini`/`hermes` (with a matching `command`) if that model suits the role
  better. Just keep `type` and `command` consistent — a mismatch means the
  turn-completion signal never fires and the agent pins "busy" forever (the loader
  catches the obvious cases at `up`). See
  [`multi-llm-swarm.md`](./multi-llm-swarm.md).

- **Tune the ACL to your process.** The default funnels everything through the
  architect. If you'd rather let `tester` file fixes straight to `dialog_writer`
  (faster, but the architect loses oversight of scope), add `dialog_writer` to
  tester's `can_talk_to`. Conversely, to make the human even more insulated, you
  could route final delivery through a separate reviewer. The ACL *is* your
  workflow — see [`delegation-pipeline.md`](./delegation-pipeline.md) for the
  general "who reports to whom" patterns.

---

## 8. Tips & footguns

- **Keep the architect the only `user`-facing agent.** In this config only the
  architect lists `user` in `can_talk_to`. That gives you a single point of contact
  and a clean funnel: raw flows, scripts, and persona notes always pass through the
  architect (and the test gate) before they reach you. If a specialist tries to
  mail `user` directly, the orchestrator bounces it (ACL) and drops a `system` note
  in the sender's inbox explaining who it *can* message — the model self-corrects
  in-band.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually fires**
  — a `type`/`command` mismatch (e.g. a `codex` agent whose `command` doesn't
  launch Codex) means completion never triggers and the agent pins "busy" forever.
  `status` showing an agent `busy` for a long time with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops — useful here, since dialog_writer and
  persona can otherwise volley over one line's wording.

- **Availability shapes the ending.** If `user` is **away** when the architect
  finishes, your finished bot is *held* (with a `system` "the user is away" ack to
  the architect) rather than lost — read it later with `agentainer user inbox` or
  flip yourself available and it's delivered.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/chatbot-builder.yaml
  ./agentainer remove-session -c examples/chatbot-builder.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' artifacts or your config.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and the send/receive
  contract in depth.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — how conversations
  survive a `down`/`up` so iteration (§6) keeps context.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — the general hub-and-spoke
  "who reports to whom" pattern.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing Claude/Codex/Gemini/Hermes
  across roles, as this swarm does.
- `examples/chatbot-builder.yaml` — the config walked through above.

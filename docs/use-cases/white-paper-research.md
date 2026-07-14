# Use case: the white-paper research swarm

A concrete, end-to-end walkthrough of the shipped `examples/white-paper-research.yaml`
swarm — a four-agent pipeline that turns a fuzzy **B2B topic** into a publishable
**white-paper package**. A **topic researcher** scopes the angle and gathers
sources, an **analyst** synthesizes them into a thesis and **white-paper outline**,
a **draft writer** writes the paper, and a **design brief writer** specs the layout.
It's the "research → synthesize → draft → design" content-production loop, wired
entirely through Agentainer's file-based mail model.

Everything below is based on the actual contents of `examples/white-paper-research.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you supply the coding-CLI
commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Why this makes a great swarm

Producing a white paper is a classic multi-role job: the person who digs up the
market data is rarely the one who frames the argument, and neither is the one who
writes the prose or lays out the pages. Doing all four in one head produces a
paper that is either under-researched or badly structured.

This swarm gives each role its own agent, its own working directory, and one job:

- **Sources are gathered once.** The researcher owns the topic and the citations,
  so the analyst, writer, and designer all build on the *same* vetted material
  instead of re-Googling the topic three times.
- **The argument is settled before a word is written.** The analyst produces the
  thesis and outline first; the draft writer executes an approved structure rather
  than discovering it mid-draft.
- **Design is planned from the content, not bolted on.** The design brief writer
  works from the finished outline and draft, so charts and callouts map to the
  actual data.

The result is a package — sources, thesis, outline, draft, and design brief —
that a human editor can take straight to publication.

---

## 2. The topology

```
        topic + audience
  user ─────────────────▶ topic_researcher ──┬──▶ analyst
        (final package) ◀──────────┐         │      │ thesis + outline
                                   │         │◀─────┘
                                   │         ├──▶ draft_writer ──▶ (draft back)
                                   │         └──▶ design_brief_writer ──▶ (brief back)
                                   └─────────────── everything returns to the hub
```

Four agents in a hub-and-spoke, with the researcher as the hub:

1. **`user` → `topic_researcher`** — you send the B2B topic.
2. **`topic_researcher` → `analyst`** — the researcher gathers a source pack and
   hands it off for synthesis.
3. **`analyst` → `topic_researcher`** — the analyst returns a thesis and outline.
4. **`topic_researcher` → `draft_writer`** — the researcher briefs the writer with
   the approved outline + sources.
5. **`topic_researcher` → `design_brief_writer`** — the researcher requests a
   layout brief once the structure is set.
6. **`topic_researcher` → `user`** — the researcher assembles and returns the
   finished package to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. The three specialists talk **only** to the researcher (never
to each other), and the researcher is the **only** agent that can talk to `user`.
Anything off-graph is bounced back as a `system` message and filed in `failed/`.

---

## 3. The config

Here is the shape of `examples/white-paper-research.yaml` (see the file for the
full `role` blocks):

```yaml
swarm:
  name: white-paper-research
  root: ./white-paper-research-workspace
defaults:
  capture: none              # tightened per agent below
  can_talk_to: []            # default ACL is "talk to no one"; set per agent
agents:
  - name: topic_researcher
    type: claude
    can_talk_to: [analyst, draft_writer, design_brief_writer, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the TOPIC RESEARCHER and hub for a white-paper project. ...
  - name: analyst
    type: claude
    can_talk_to: [topic_researcher]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the ANALYST. Given the researcher's source pack, turn raw findings
      into a defensible argument ...
  - name: draft_writer
    type: codex
    can_talk_to: [topic_researcher]
    command: "codex --yolo"
    role: |
      You are the DRAFT WRITER. ...
  - name: design_brief_writer
    type: claude
    can_talk_to: [topic_researcher]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the DESIGN BRIEF WRITER. ...
```

📄 **Full config:** [`examples/white-paper-research.yaml`](../../examples/white-paper-research.yaml)

Field notes:

- **`root: ./white-paper-research-workspace`** — each agent gets
  `white-paper-research-workspace/<name>/` as its workdir (created on `up`), with
  its five mailbox folders alongside. Orchestrator state lives under
  `white-paper-research-workspace/.agentainer/` (never commit it).
- **`defaults.capture: none`** — for `claude` and `codex` agents this is a footgun
  the loader *fixes*: their CLIs support a completion **hook**, so `capture: none`
  is auto-upgraded back to `hook` with a warning at `up`. Net effect: all four
  agents get real turn-completion detection (three via the Claude Stop hook, the
  draft writer via the Codex `notify` hook).
- **`topic_researcher` is the hub** — the only agent listing `user`, and the only
  one that reaches all three specialists. Keep the human-facing surface to a single
  agent so sources and structure funnel through one place.
- **The three specialists list only `[topic_researcher]`** — they cannot reach each
  other or the `user`; their output always returns to the hub for assembly.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/white-paper-research.yaml
```

`up` loads and validates the config (printing the `capture: none → hook`
upgrades), creates the runtime dirs, initializes every agent's five mailbox
folders plus an `outbox/<peer>/` for each allowed recipient, installs per-type
turn detection, opens one tmux session per agent, delivers the standby first
prompt, and starts the liveness supervisor.

At the end it prints attach and `serve` hints. The `serve` line gives you the
mail-app control-plane UI (threads, live panes, send-as-user, availability
toggle). It **binds `127.0.0.1` by default** — pass `--host`/`--token` only when
you deliberately want a remote bind.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole pipeline route mail with no API keys — the mechanics are identical.

---

## 5. Drive a topic

The `user` mailbox defaults to **away**, so the final package is *held* rather
than lost if you step away. To receive it as mail, turn yourself available first:

```bash
./agentainer user available -c examples/white-paper-research.yaml
```

Then send the topic into the swarm, addressed to the hub:

```bash
./agentainer send --to topic_researcher \
  "White paper on zero-trust security for mid-market SaaS buyers."
```

### The mail flowing

Watching the log, you'll see the pipeline advance one turn at a time — each hop is
a `stop → sweep → route → release → nudge` cycle:

1. **topic_researcher scopes and gathers.** Reads the topic, writes a scope +
   source pack, sends it to `outbox/analyst/`.
2. **analyst synthesizes.** Reads the pack, writes a thesis + white-paper outline,
   returns it to `outbox/topic_researcher/`.
3. **topic_researcher briefs the writers.** Relays the approved outline + sources
   to `outbox/draft_writer/`, and requests a layout plan from
   `outbox/design_brief_writer/`.
4. **draft_writer and design_brief_writer produce.** Each returns its artifact
   (draft, design brief) to `outbox/topic_researcher/`.
5. **topic_researcher assembles.** Reads both, writes the finished package to
   `outbox/user/` — delivered to your `user` mailbox.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

---

## 6. Observe

```bash
./agentainer status -c examples/white-paper-research.yaml   # who's up, queue, unread, ACL
./agentainer logs   -c examples/white-paper-research.yaml -f # durable event log, follow live
./agentainer inbox  analyst -c examples/white-paper-research.yaml   # one agent's current message
./agentainer attach draft_writer -c examples/white-paper-research.yaml # watch a live pane
```

The durable JSONL log is the source of truth for history — tmux keeps no
scrollback, so this is how you reconstruct what happened (`user-send`,
`delivered`, `route`, `read`, `bounce`, …).

---

## 7. What people search for (and how this answers it)

- **"how to structure a white paper" / "white paper outline template"** — the
  analyst's job is exactly this: it produces the thesis and section-by-section
  outline before any prose is written.
- **"B2B content research workflow"** — the researcher-as-hub pattern shows how to
  gather sources once and reuse them across synthesis, drafting, and design.
- **"automate white paper writing with AI agents"** — this is a working,
  zero-dependency multi-agent pipeline you can run today, not a prompt template.
- **"separate research from writing"** — the ACL *enforces* the separation: the
  writer never talks to the analyst directly; the argument is settled upstream.
- **"white paper design brief"** — the design_brief_writer turns the finished
  content into an executable layout plan (charts, callouts, cover concept).

---

## 8. Tips & notes

- **Keep the researcher the only `user`-facing agent.** It's your single point of
  contact and the funnel that guarantees a topic is scoped and sources are vetted
  before specialists touch it. If a specialist tries to mail `user` directly, the
  ACL bounces it and drops a `system` note explaining who it *can* reach.

- **The specialists deliberately can't talk to each other.** If you *wanted* the
  writer to consult the analyst directly, you'd add each to the other's
  `can_talk_to` — but routing through the hub keeps one owner of the topic and
  avoids two half-decisions.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion. If an
  agent seems stuck, check its turn detection actually fires — a `type`/`command`
  mismatch (a `claude` agent whose `command` doesn't launch Claude) means
  completion never triggers and the agent pins "busy" forever.

- **Add a periodic ping for a slow researcher.** There are no periodic pings in
  this config — it's purely event-driven off real mail. To poke the researcher
  while it waits on a specialist, add a `pings` cron rule to it.

- **Availability shapes the ending.** If `user` is **away** when the researcher
  finishes, your final package is *held* (with a `system` ack) rather than lost —
  read it later with `agentainer user inbox` or flip yourself available.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`use-cases/research-swarm.md`](./research-swarm.md) — the simpler three-agent
  research pipeline this builds on.
- [`cli-reference.md`](../cli-reference.md) — every subcommand and flag.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

# Use case: the FAQ knowledge-sync swarm

A concrete, end-to-end walkthrough of the shipped `examples/faq-knowledge-sync.yaml`
swarm — a four-agent pipeline that mines the **questions real users actually ask**
(from support tickets, chat logs, and site-search queries), writes authoritative
answers, and emits **FAQPage JSON-LD schema markup** so the published FAQ is
eligible for Google **rich results** and is easy for LLM-powered search to quote.

It's the "listen → answer → publish for machines" loop, wired entirely through
Agentainer's file-based mail model. A **faq_lead** owns the cycle and the human, a
**question_miner** surfaces demand, an **answer_writer** drafts the copy, and a
**schema_writer** turns that copy into valid structured data.

Everything below is based on the actual contents of `examples/faq-knowledge-sync.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you supply the coding-CLI
commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Why this is a great fit for a swarm

FAQ maintenance is three genuinely different jobs that most teams collapse into
one and do badly:

- **Finding the right questions** is a data-mining task — clustering thousands of
  real utterances into canonical phrasings ranked by volume and intent. It is *not*
  a copywriting task.
- **Answering** is an editorial task — accurate, self-contained, snippet-shaped
  prose that reads well in isolation.
- **Publishing for machines** is an engineering task — valid schema.org `FAQPage`
  JSON-LD whose markup exactly mirrors the visible answer (Google penalizes markup
  that doesn't match on-page content).

Splitting them across agents with a tight ACL keeps each one focused and keeps the
markup honest: the schema is generated from the *actual* answer text, in lock-step,
rather than drifting from it. And because the answers are written to be
self-contained, the same corpus feeds both **Google rich results** and the
**LLM/AI-search** answers that increasingly quote FAQ content directly.

---

## 2. The topology

```
                         user
                          │  "refresh the FAQ"
                          ▼
                       faq_lead   (hub: owns the queue + the human)
              ┌───────────┼───────────┐
              ▼           ▼           ▼
      question_miner  answer_writer ── schema_writer
        (real Qs)      (answers)  peer  (FAQPage JSON-LD)
```

Four agents, one directed flow:

1. **`user` → `faq_lead`** — you ask for an FAQ refresh (and name the sources).
2. **`faq_lead` → `question_miner`** — the lead asks for the real, ranked questions.
3. **`question_miner` → `faq_lead`** — mined, deduplicated, intent-tagged questions.
4. **`faq_lead` → `answer_writer`** — draft authoritative answers for the set.
5. **`answer_writer` → `schema_writer`** — finished Q&A pairs, peer-to-peer, so the
   markup tracks the copy without a detour through the lead.
6. **`schema_writer` / `answer_writer` → `faq_lead`** — the JSON-LD block and the
   drafted answers come back to the lead.
7. **`faq_lead` → `user`** — the lead returns the finished FAQ (human-readable Q&A
   **plus** the JSON-LD) to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. The **miner talks only to the lead**; the **answer_writer and
schema_writer are peers** so copy and schema stay synchronized; and **only the
faq_lead talks to `user`**. Anything off-list is bounced back as a `system` message
and filed in `failed/` (see §7).

---

## 3. The config

Here is the shape of `examples/faq-knowledge-sync.yaml` (see the file for the full
`role` text):

```yaml
swarm:
  name: faq-knowledge-sync
  root: ./faq-knowledge-sync-workspace
defaults:
  capture: none              # tightened per agent below
  can_talk_to: []            # default ACL is "talk to no one"
agents:
  - name: faq_lead
    type: claude
    can_talk_to: [question_miner, answer_writer, schema_writer, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the FAQ LEAD. ... you are the only agent who talks to the user ...
  - name: question_miner
    type: gemini
    can_talk_to: [faq_lead]
    capture: pane
    command: "gemini --yolo"
    role: |
      You are the QUESTION MINER. ... extract the REAL questions ... ranked by
      volume ... tagged with search intent ...
  - name: answer_writer
    type: claude
    can_talk_to: [faq_lead, schema_writer]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the ANSWER WRITER. ... each answer must stand alone ... send the
      finished Q&A pairs to schema_writer ...
  - name: schema_writer
    type: codex
    can_talk_to: [faq_lead, answer_writer]
    command: "codex --yolo"
    role: |
      You are the SCHEMA WRITER. ... a single valid schema.org FAQPage JSON-LD
      block ... markup must exactly match the published visible answers ...
```

👉 Full file: [`examples/faq-knowledge-sync.yaml`](../../examples/faq-knowledge-sync.yaml)

Field by field:

### `swarm`
- **`name: faq-knowledge-sync`** — the swarm's name (shows in `status`, logs, sessions).
- **`root: ./faq-knowledge-sync-workspace`** — parent directory for each agent's
  working directory and mailbox folders. Orchestrator state goes under
  `faq-knowledge-sync-workspace/.agentainer/` (never commit it).

### `defaults`
- **`capture: none`** — the default turn-detection mode. For `claude` and `codex`,
  whose CLIs support a completion **hook**, the loader *upgrades* `none` back to
  `hook` and prints a warning at `validate`/`up` (you'll see three such lines). Net
  effect: faq_lead, answer_writer and schema_writer use their hook; question_miner
  overrides to `pane`.
- **`can_talk_to: []`** — the ACL floor is "talk to no one"; every agent states its
  own list explicitly.

### `faq_lead` (type: `claude`)
- **`can_talk_to: [question_miner, answer_writer, schema_writer, user]`** — the hub.
  It's the **only agent that can reach `user`**, giving you a single funnel: raw
  mined questions and draft markup always pass through the lead before they reach you.
- **Turn detection:** `claude` → a **Stop hook**, installed automatically at `up`.

### `question_miner` (type: `gemini`)
- **`can_talk_to: [faq_lead]`** — reports only upward. Its raw output never goes
  straight to the user.
- **`capture: pane`** — Gemini's CLI can't call a completion program, so Agentainer
  detects "turn done" by **polling the tmux pane** until it stops changing (hence
  the explicit override of the `none` default).

### `answer_writer` (type: `claude`)
- **`can_talk_to: [faq_lead, schema_writer]`** — reports to the lead **and** hands
  finished Q&A pairs directly to the schema_writer (the peer edge).

### `schema_writer` (type: `codex`)
- **`can_talk_to: [faq_lead, answer_writer]`** — delivers the JSON-LD to the lead,
  and can ping the answer_writer to reconcile markup with the current copy.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/faq-knowledge-sync.yaml
```

`up` loads and validates the config (printing the three `capture: none → hook`
upgrade warnings), creates the runtime dirs, **initializes the mailboxes** (the five
folders `inbox/ outbox/ read/ sent/ failed/` plus an `outbox/<peer>/` with an
`about.md` contact card for each allowed recipient), installs per-type turn
detection, opens one tmux session per agent, delivers the standby first prompt, and
starts the liveness supervisor. It ends with attach and **`serve`** hints.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch the
> whole pipeline route mail with no API keys — the mechanics are identical.

---

## 5. Drive a refresh

The `user` is a **virtual mailbox** that defaults to **away**. To *receive* the
finished FAQ as mail rather than have it held, mark yourself available first:

```bash
./agentainer user available -c examples/faq-knowledge-sync.yaml
```

Then send the request into the swarm, addressed to the lead:

```bash
./agentainer send --to faq_lead \
  "Refresh the FAQ from last month's support tickets and site-search logs. Return the Q&A plus the FAQPage JSON-LD."
```

Under the hood (`cmd_send` → `mail.send_as_user`) the message is stamped `From: user`,
enqueued for the faq_lead, released into its `inbox/`, and the lead is **nudged** (the
protocol — including its allowed-recipient list — is re-pasted into its pane).

### The mail flowing

Watching the log (§6), you'll see the pipeline advance one turn at a time — each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **faq_lead receives the request**, writes a mining brief into
   `outbox/question_miner/`. On stop that routes to the miner.
2. **question_miner mines** the sources, clusters duplicates, ranks by volume, tags
   intent, and writes the ranked list into `outbox/faq_lead/`.
3. **faq_lead** forwards the question set into `outbox/answer_writer/`.
4. **answer_writer drafts** self-contained answers, sends the Q&A pairs into
   `outbox/schema_writer/` (peer edge) and a copy to `outbox/faq_lead/`.
5. **schema_writer emits** the `FAQPage` JSON-LD into `outbox/faq_lead/`.
6. **faq_lead finalizes** and writes the combined FAQ + JSON-LD into `outbox/user/`
   — delivered to your `user` mailbox (`agentainer user inbox`, or the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

---

## 6. Observe

```bash
./agentainer status -c examples/faq-knowledge-sync.yaml   # who's up, idle/busy, queue, unread, ACL
./agentainer logs   -c examples/faq-knowledge-sync.yaml -f # durable JSONL event log, live
./agentainer inbox  answer_writer -c examples/faq-knowledge-sync.yaml
./agentainer queue  schema_writer -c examples/faq-knowledge-sync.yaml
./agentainer attach question_miner -c examples/faq-knowledge-sync.yaml   # Ctrl-b d to detach
```

The event log is the source of truth for history (tmux keeps no scrollback) — you'll
see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`, one JSONL
line per event.

---

## 7. What this unlocks for search (intent)

This swarm is built around how people (and machines) actually search. It targets:

- **"How do I add FAQ schema markup for Google rich results?"** — the schema_writer
  emits ready-to-paste `FAQPage` JSON-LD for a `<script type="application/ld+json">`
  tag.
- **"Which FAQ questions are worth answering?"** — the question_miner ranks by real
  volume and intent instead of guesswork.
- **"Make our help content quotable by AI search / LLM answers."** — self-contained,
  first-sentence-answer copy is exactly what generative search engines extract.
- **"Keep FAQ structured data in sync with the visible answers."** — the peer edge
  between answer_writer and schema_writer keeps markup matching on-page text, avoiding
  Google's markup-mismatch penalties.
- **"Turn support tickets / search logs into an SEO FAQ page."** — the whole pipeline,
  end to end.

---

## 8. Tips & footguns

- **Keep the faq_lead the only `user`-facing agent.** Only the lead lists `user` in
  `can_talk_to`, so mined questions and raw markup always pass through it. If another
  agent tries to mail `user`, the orchestrator bounces it (ACL) and drops a `system`
  note explaining who it *can* reach — the model self-corrects in-band.

- **The peer edge is deliberate.** answer_writer ↔ schema_writer talk directly so
  JSON-LD stays synchronized with the copy. **Markup must match the visible answer** —
  if the copy changes, the schema_writer's role tells it to re-fetch the current text
  before regenerating, rather than shipping stale structured data.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an agent
  stops, its outbox is swept, mail is routed, recipients are nudged. If an agent seems
  stuck, check that turn detection fires — a `type`/`command` mismatch (a `claude`
  agent whose `command` doesn't launch Claude) means completion never triggers and the
  agent pins "busy" forever. `status` showing `busy` for a long time with `unread`
  mail is the tell.

- **Force-idle a pane-captured agent if its turn never registers.** The miner uses
  pane polling; if its capture never fires:
  ```bash
  ./agentainer idle question_miner -c examples/faq-knowledge-sync.yaml
  ```

- **The UI binds loopback by default.** `agentainer serve` is `127.0.0.1`-only unless
  you pass `--host` (and a `--token` for any remote bind). The UI is a control plane —
  it can start processes and type into agents running `--yolo` — so keep it local.

- **Validate your own edits.** After changing the config:
  ```bash
  ./agentainer validate -c examples/faq-knowledge-sync.yaml
  ```

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`use-cases/research-swarm.md`](./research-swarm.md) — the sibling delegate →
  do → review pipeline.
- [`cli-reference.md`](../cli-reference.md) — every subcommand and flag.
- `examples/quickstart.yaml` — the key-free mock-agent starter.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

# Use case: the affiliate product-review desk

A concrete, end-to-end walkthrough of the shipped `examples/affiliate-product-reviews.yaml`
swarm — a four-agent review desk where a **review_editor** briefs a
**product_researcher** to gather verifiable facts, hands them to a **review_writer**
who drafts an honest pros/cons review, and to a **comparison_builder** who builds
the affiliate comparison table. It's the canonical "brief → research → write →
compare → publish" loop for producing **"<product> review"** and **"best X for Y"**
content that ranks in search and gets cited by LLM answers — wired entirely through
Agentainer's file-based mail model.

Everything below is based on the actual contents of
`examples/affiliate-product-reviews.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Why this swarm is great for affiliate content

Affiliate reviews live and die on two things: **trust** and **search intent**.
This desk is built for both.

- **Honest by construction.** The `review_writer` is instructed to write real cons
  and a clear affiliate-disclosure line, and the `review_editor` "holds the line on
  honesty" — cut hype, never cut the cons. Reviews that hedge sell nothing and
  rank for nothing.
- **Sourced, not hallucinated.** The `product_researcher` records every spec with a
  source and marks anything it can't confirm as "unconfirmed"; the writer and
  builder may use **only** confirmed facts. That fact discipline is exactly what
  Google's helpful-content signals and LLM citation prefer.
- **Comparison tables win featured snippets and LLM answers.** "<product> vs
  <rival>" and "best X for Y" queries reward a scannable, factual table — the
  `comparison_builder` produces exactly that.
- **One voice to the reader.** Only the editor talks to `user`, so the published
  piece is assembled and disclosure-checked in one place.

---

## 2. The topology

```
                 review Acme X200
   user ───────────────────────────▶ review_editor
        (finished review + table) ◀────────┘  │
                                             │ brief / facts
              ┌──────────────────────────────┼──────────────────────────────┐
              ▼                               ▼                               ▼
      product_researcher              review_writer                 comparison_builder
       (fact sheet)                  (honest pros/cons)              (comparison table)
              └──────────────▶ all report back to review_editor ◀──────────────┘
```

Four agents, a hub-and-spoke flow:

1. **`user` → `review_editor`** — you name the product and its rivals.
2. **`review_editor` → `product_researcher`** — the editor writes a brief and asks
   for a sourced fact sheet.
3. **`product_researcher` → `review_editor`** — verifiable specs/prices come back.
4. **`review_editor` → `review_writer`** — the editor hands over confirmed facts;
   the writer drafts the honest pros/cons review.
5. **`review_editor` → `comparison_builder`** — the editor requests the comparison
   table from the same confirmed facts.
6. **`review_editor` → `user`** — the editor assembles review + table, checks
   disclosure, and returns the finished piece to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. The three specialists list **only** `review_editor`; anything
else is bounced back as a `system` message and filed in `failed/` (see §6).

---

## 3. The config, explained

Here is the shape of `examples/affiliate-product-reviews.yaml` (see the file for the
full `role` blocks):

```yaml
swarm:
  name: affiliate-product-reviews
  root: ./affiliate-product-reviews-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: review_editor
    type: claude
    can_talk_to: [product_researcher, review_writer, comparison_builder, user]
    command: "claude --dangerously-skip-permissions"
    capture: pane
    role: |
      You are the REVIEW EDITOR, the hub ... the ONLY agent who talks to the user.
      ... (1) brief product_researcher; (2) brief review_writer; (3) hand facts to
      comparison_builder; (4) assemble + disclose, then send to the user.
  - name: product_researcher
    type: gemini
    can_talk_to: [review_editor]
    command: "gemini --yolo"
    capture: pane
    role: "You are the PRODUCT RESEARCHER. Gather verifiable facts with sources ..."
  - name: review_writer
    type: codex
    can_talk_to: [review_editor]
    command: "codex --yolo"
    role: "You are the REVIEW WRITER. Honest pros/cons from confirmed facts only ..."
  - name: comparison_builder
    type: codex
    can_talk_to: [review_editor]
    command: "codex --yolo"
    role: "You are the COMPARISON BUILDER. Build the Markdown comparison table ..."
```

Field by field:

### `swarm`
- **`name: affiliate-product-reviews`** — the swarm's name (shows in `status`,
  logs, sessions).
- **`root: ./affiliate-product-reviews-workspace`** — the parent directory for the
  agents' working directories and mailboxes. Each agent gets
  `affiliate-product-reviews-workspace/<name>/` as its workdir (created on `up`),
  with its mailbox folders alongside. Orchestrator state goes under
  `…/.agentainer/` (never commit it).

### `defaults`
- **`capture: none`** — the default turn-detection mode. For `claude`/`codex`,
  whose CLIs support a completion **hook**, `capture: none` is a footgun, so the
  loader *upgrades* it back to `hook` with a warning at `up`. Net effect: the two
  `codex` specialists use their hook; the editor and researcher override to `pane`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent states
  its own list, so this is a safe floor.

### `review_editor` (type: `claude`, `capture: pane`)
- **`can_talk_to: [product_researcher, review_writer, comparison_builder, user]`** —
  the hub. It briefs all three specialists and is the **only agent that can talk to
  `user`**, keeping a single human-facing surface and one disclosure checkpoint.
- **`command`** — launches Claude Code in its tmux pane (placeholder — substitute
  your own launch command/alias; treat command strings as sensitive, they may embed
  keys).
- **`role`** — the standing identity. On `up` it becomes the first prompt wrapped in
  a **standby notice**, so the editor waits for your request instead of proactively
  mailing peers.

### `product_researcher` (type: `gemini`, `capture: pane`)
- **`can_talk_to: [review_editor]`** — reports only to the editor; cannot reach the
  writer, the builder, or `user` directly.
- **`capture: pane`** — Gemini's CLI can't call a completion program, so Agentainer
  detects "turn done" by **polling the tmux pane** until it stops changing.

### `review_writer` and `comparison_builder` (type: `codex`)
- **`can_talk_to: [review_editor]`** — both report only to the editor.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.
- The writer produces `REVIEW.md` (honest pros/cons + disclosure); the builder
  produces `COMPARISON.md` (the affiliate table). Both may use **only** the
  editor's confirmed facts.

### What's *not* in this config
- **No `periodically_ping_seconds`.** Nothing is auto-nudged on a timer; the desk is
  purely event-driven off real mail.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/affiliate-product-reviews.yaml
```

`up` loads and validates the config (printing the `capture: none → hook` upgrade
for the two `codex` agents), creates the runtime dirs, initializes every agent's
five mailbox folders plus an `outbox/<peer>/` (with an `about.md` contact card) for
each allowed recipient, installs per-type turn detection, opens one tmux session
per agent, delivers the standby first prompt, and starts the liveness supervisor.

At the end it prints attach and **`serve`** hints. The `serve` line gives you the
mail-app control-plane UI (threads, live panes, send-as-user, availability toggle).
The UI **binds `127.0.0.1` by default**; only add `--host`/`--token` for a
deliberate, token-protected remote bind — never expose it on `0.0.0.0` unprotected.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch the
> whole desk route mail with no API keys — the mechanics are identical.

---

## 5. Drive a review

Turn yourself available so the editor's finished piece is delivered (not held):

```bash
./agentainer user available -c examples/affiliate-product-reviews.yaml
```

Now send the request, addressed to the editor:

```bash
./agentainer send --to review_editor \
  "Review the Acme X200 robot vacuum against its top 3 rivals for pet-owner households."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped `From:
user` with a fresh id, enqueued for the editor, released into its `inbox/` (empty),
and the editor is **nudged** (its protocol + allowed-recipient list re-pasted).

### The mail flowing

Each arrow is a `stop → sweep → route → release → nudge` cycle:

1. **review_editor receives the request.** It writes a brief into
   `outbox/product_researcher/` and ends its turn; the orchestrator routes it and
   nudges the researcher.
2. **product_researcher gathers facts.** It records sourced specs in `RESEARCH.md`
   and writes the fact sheet into `outbox/review_editor/`.
3. **review_editor briefs the writer and the builder.** Reading the fact sheet, it
   sends confirmed facts to `outbox/review_writer/` and `outbox/comparison_builder/`.
4. **review_writer drafts the review; comparison_builder builds the table.** Each
   writes its output (`REVIEW.md`, `COMPARISON.md`) and returns it to
   `outbox/review_editor/`.
5. **review_editor assembles and publishes.** It checks every claim is sourced and
   the affiliate disclosure is present, then writes the finished review + table into
   `outbox/user/` — delivered to your `user` mailbox (`agentainer user inbox`, or
   the UI).

You don't relay anything by hand — the orchestrator releases one inbox message at a
time and fires the next hop off each agent's turn completion.

---

## 6. Search intent this desk targets

Each output maps to a high-value affiliate search query:

- **"<product> review"** / **"is <product> worth it"** — the `review_writer`'s
  honest verdict + pros/cons, the highest-intent affiliate query there is.
- **"best <category> for <use case>"** (e.g. "best robot vacuum for pet hair") —
  the `comparison_builder`'s table plus the editor's "who it's for" framing.
- **"<product> vs <rival>"** — the comparison table's per-row, per-spec layout is
  purpose-built for these head-to-head queries and for featured snippets.
- **"<product> specs / price / dimensions"** — the researcher's sourced fact sheet
  answers the long-tail spec questions LLM assistants love to cite.
- **"<product> pros and cons"** — surfaced directly as labeled PROS/CONS sections,
  the structure snippet extractors and LLM answers pull from.

Because every claim is sourced and disclosure is explicit, the output is the kind of
**helpful, trustworthy content** both search ranking systems and LLM answer engines
prefer to surface and cite.

---

## 7. Tips & footguns

- **Keep the editor the only `user`-facing agent.** Only `review_editor` lists
  `user`. That funnels every piece through one honesty/disclosure checkpoint. If a
  specialist tries to mail `user` directly, the orchestrator bounces it (ACL) and
  drops a `system` note explaining who it *can* message — the model self-corrects
  in-band.

- **Confirmed facts only.** The writer and builder are told to use only the editor's
  confirmed facts and to leave a cell empty rather than guess. Keep that constraint
  when you adapt the roles — it's what makes the content trustworthy.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion. If an
  agent seems stuck, check its **turn detection actually fires** — a `type`/`command`
  mismatch (e.g. a `claude` agent whose `command` doesn't launch Claude) means
  completion never triggers and the agent pins "busy" forever. `status` showing an
  agent `busy` for a long time with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances; a per-pair runaway cap (≤20 messages / 60s) kills
  "thanks!/you're welcome!" loops.

- **Force-idle a pane-captured agent.** The editor and researcher use pane polling;
  if a capture never fires you can nudge the state along:
  ```bash
  ./agentainer idle product_researcher -c examples/affiliate-product-reviews.yaml
  ```

- **Availability shapes the ending.** If `user` is **away** when the editor finishes,
  your final review is *held* (with a `system` ack to the editor) rather than lost —
  read it later with `agentainer user inbox` or flip yourself available.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`use-cases/research-swarm.md`](./research-swarm.md) — the sibling delegate →
  research → review pipeline.
- [`cli-reference.md`](../cli-reference.md) — every subcommand and flag.
- `examples/quickstart.yaml` — the key-free mock-agent starter.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

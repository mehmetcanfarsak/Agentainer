# Use case: the RAG builder swarm

A concrete, end-to-end walkthrough of the shipped `examples/rag-builder.yaml`
swarm — a four-agent pipeline that turns a **corpus + a use-case** from a human
into a working **retrieval-augmented assistant** (RAG). An **architect** hub
plans the build, a **chunker** designs the splitting strategy, an **embedder**
writes the ingest/index code, and an **evaluator** measures retrieval quality and
loops fixes back — all wired through Agentainer's file-based mail model.

Everything below is based on the actual contents of `examples/rag-builder.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you supply the
coding-CLI commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state. For the protocol details
> see [`mail-model.md`](../mail-model.md) and the hub-and-spoke pattern in
> [`use-cases/delegation-pipeline.md`](./delegation-pipeline.md).

---

## 1. Who this is for

- **ML / applied-AI engineers** who want a RAG pipeline scaffolded and evaluated
  without hand-writing the plumbing (loaders, chunkers, index build, eval
  harness) — and without one agent trying to do all four jobs at once.
- **Builders shipping a docs / knowledge-base Q&A bot** (internal wikis, product
  manuals, support articles, legal/regulatory corpora) who need retrieval that is
  *measured*, not assumed.
- **Teams mixing LLM vendors** — here the chunker and evaluator are `claude` and
  the code-writing embedder is `codex` — to play to each model's strength
  (reasoning vs. code execution). See
  [`use-cases/multi-llm-swarm.md`](./multi-llm-swarm.md).

The point isn't that four LLMs outperform one. It's that the work **decomposes
into distinct roles** — planning, strategy, implementation, and independent
verification — and a fixed ACL keeps each role in its lane so the build and the
evaluation don't get negotiated inside one context window.

---

## 2. The topology

```
                corpus + use-case
      user ───────────────────────▶ architect ◀────────────────┐
           (build plan) ◀───────────────┘ │                    │ results
                                           │ chunking task      │
                                           ▼                    │
                                       chunker ──▶ embedder ──▶ evaluator
                                                      ▲  shared      │
                                                      └── repo ◀─────┘ fixes
```

```
  architect (claude)  can_talk_to: chunker, embedder, evaluator, user   [hub]
  chunker  (claude)   can_talk_to: architect, embedder
  embedder (codex)    can_talk_to: architect, chunker, evaluator        [shared repo]
  evaluator(claude)   can_talk_to: architect, embedder                  [shared repo]
```

Four agents, one directed build-and-evaluate loop:

1. **`user` → `architect`** — you send the corpus (a path/description) and the
   use-case (who asks what, what a good answer looks like).
2. **`architect` → `chunker`** — the architect restates the goal and delegates the
   splitting-strategy design.
3. **`chunker` → `embedder`** — the chunker returns a concrete split spec +
   metadata schema, which the embedder implements.
4. **`embedder` ⇄ `evaluator`** — the embedder builds the ingest/index in a
   **shared repo**, the evaluator runs real retrieval-quality checks and sends
   fixes, the embedder re-indexes; this tightens until recall is acceptable.
5. **`architect` → `user`** — the architect returns a **build plan**: chunking,
   embedding model + store, ingest steps, measured quality, and known limits.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. An agent can only deliver to names on its own list; anything
else is bounced back as a `system` message and filed in `failed/` (see §8).

---

## 3. The config, explained

Here is `examples/rag-builder.yaml` in full:

```yaml
# 📚 RAG builder — architect hub turns a corpus + use-case into a working RAG
# assistant. Real agents: `command:` lines launch the actual CLIs (PLACEHOLDERS).
# Key-free: NO API keys needed. UI binds 127.0.0.1 by default.
swarm:
  name: rag-builder
  root: ./rag-builder-workspace
defaults:
  capture: none              # claude/codex auto-upgrade to their hook
  can_talk_to: []
agents:
  - name: architect
    type: claude
    can_talk_to: [chunker, embedder, evaluator, user]
    command: "claude --dangerously-skip-permissions"
    role: "You are the RAG ARCHITECT and planning hub. Restate the goal, brief the chunker, ensure the embedder has corpus+spec, loop the evaluator's fixes, then send the user a BUILD PLAN."
  - name: chunker
    type: claude
    can_talk_to: [architect, embedder]
    command: "claude --dangerously-skip-permissions"
    role: "You are the CHUNKING STRATEGIST. From the architect's brief, design split unit, size/overlap, and metadata schema; send the spec to the embedder and a rationale to the architect."
  - name: embedder
    type: codex
    can_talk_to: [architect, chunker, evaluator]
    command: "codex --yolo"
    workdir: "{root}/rag-repo"
    role: "You are the INGEST/INDEX ENGINEER. From the chunker's spec, implement loader+chunker+embedder+index+query in the shared repo; ask the evaluator to check; apply fixes; report the final setup to the architect."
  - name: evaluator
    type: claude
    can_talk_to: [architect, embedder]
    command: "claude --dangerously-skip-permissions"
    workdir: "{root}/rag-repo"
    role: "You are the RETRIEVAL EVALUATOR. Write realistic eval questions, run the embedder's query entry point, measure recall@k and failure modes, send concrete fixes to the embedder, and a verdict to the architect."
```

### `swarm`
- **`name: rag-builder`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./rag-builder-workspace`** — parent dir for working dirs and
  mailboxes. By default each agent gets `rag-builder-workspace/<name>/`, except
  the embedder and evaluator, which **share** `rag-builder-workspace/rag-repo/`
  (see §3b). Orchestrator state goes under `rag-builder-workspace/.agentainer/`
  (never commit it).

### `defaults`
- **`capture: none`** — the default turn-detection mode. For `claude`/`codex`,
  whose CLIs support a completion **hook**, `none` is a footgun, so the loader
  *upgrades* it to `hook` and prints a warning at `up`
  (`capture: none on a claude agent … auto-upgraded to capture: hook.`). All four
  agents end up using their native hook.
- **`can_talk_to: []`** — default ACL is "talk to no one"; each agent states its
  own list, so this is just a safe floor.

### The four agents
- **`architect` (claude, hub)** — the only agent that may reach `user`; keeps the
  human-facing surface to one agent. Turn detection: Stop hook.
- **`chunker` (claude)** — designs split unit/size/overlap/metadata and ships the
  spec straight to the embedder. Cannot reach `user` or the evaluator directly.
  Turn detection: Stop hook.
- **`embedder` (codex, shared repo)** — implements the pipeline and loops fixes
  with the evaluator; reports the final setup to the architect. Cannot reach
  `user`. Turn detection: `notify` hook.
- **`evaluator` (claude, shared repo)** — runs retrieval-quality checks and sends
  fixes to the embedder and a verdict to the architect. Cannot reach the chunker
  or `user`. Turn detection: Stop hook.

### 3b. The shared `rag-repo` workdir

The embedder and evaluator both set `workdir: "{root}/rag-repo"`. This is
deliberate: the embedder **writes** the ingest/index code there, and the
evaluator must **run** it to measure retrieval — they need the same checkout. At
`up` you'll see a config warning that the two agents share a directory. That
warning is about *source* collisions (two agents editing the same file at once);
their **mailboxes never collide** — because the workdir is shared, the
orchestrator automatically namespaces every mailbox folder (`embedder-inbox/`,
`evaluator-inbox/`, …), so mail for one is invisible to the other. See the
shared-workdir rules in
[`use-cases/custom-workspace.md`](./custom-workspace.md). Point both `workdir`
lines at your real corpus checkout to build in place.

### What's *not* in this config
- **No `pings`** — purely event-driven; add
  a `pings` cron rule to an agent if you want timer nudges.
- **No `user` availability set** — the `user` mailbox defaults to **away**, so
  mail to you is *held* until you flip it on (see §4).
- **No `telegram:`** — bridge off by default; enable in `swarm:` to mirror the
  plan to a chat (see [`telegram-bridge.md`](../telegram-bridge.md)).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/rag-builder.yaml
```

What `up` does (`cmd_up` in `lib/cli.py`): 1) loads/validates, printing the
`capture: none → hook` upgrades and the shared-`rag-repo` note; 2) creates the
runtime dirs; 3) **initializes the mailboxes** (`inbox/ outbox/ read/ sent/
failed/`, per-agent queue, and an `outbox/<peer>/` folder per allowed recipient,
namespaced `embedder-*`/`evaluator-*` on the shared workdir); 4) **installs
per-type turn detection** (Claude Stop hooks, Codex `notify` hook); 5) **opens one
tmux session per agent** `cd`'d into its workdir; 6) **delivers the standby first
prompt**; 7) **starts the liveness supervisor** so one stuck agent can't wedge the
swarm.

`up` ends with attach + `serve` hints:

```
:: swarm 'rag-builder' is up with 4 agent(s)
:: attach with:  tmux attach -t <architect-session>
:: you can use the UI with:  agentainer serve -c examples/rag-builder.yaml --port 8000
```

The `serve` line is the mail-app control-plane UI; it binds **127.0.0.1 by default**
— keep it loopback-only unless you add `--token`. See
[`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and watch the whole
> build-and-evaluate loop route mail with no API keys — the mechanics are
> identical.

---

## 5. Drive a build

The `user` is a **virtual mailbox** that defaults to **away**. Flip yourself
available first so the architect's final plan is delivered as mail:

```bash
./agentainer user available -c examples/rag-builder.yaml
```

This sets `Status: available` in the architect's `outbox/user/about.md`. Now send
the corpus + use-case, addressed to the architect:

```bash
./agentainer send -c examples/rag-builder.yaml --to architect \
  "Build a support-doc Q&A bot over ./docs (Markdown, ~1200 files). Users ask how-to questions; answers must cite the source page."
```

Under the hood (`cmd_send` → `mail.send_as_user`): stamped `From: user` + fresh
id, enqueued, then — because the inbox was empty — **released into `inbox/`** and
the architect is **nudged** (protocol re-pasted, allowed-recipient list included).

### The mail flowing

Each arrow is a `stop → sweep → route → release → nudge` cycle:

1. **architect** reads the task, restates the goal, writes a chunking brief into
   `outbox/chunker/`. On stop → routes to chunker + nudge.
2. **chunker** designs the split, writes the spec into `outbox/embedder/` and a
   rationale into `outbox/architect/`. On stop → both route + nudge.
3. **embedder** reads the spec + corpus, builds ingest/index/query in `rag-repo/`,
   writes a summary into `outbox/evaluator/` and asks for a check. On stop →
   routes to evaluator.
4. **evaluator** writes eval questions, runs the query entry point, measures
   recall@k, sends concrete fixes into `outbox/embedder/`; the embedder re-indexes
   and re-asks. Repeat until recall is acceptable, then the evaluator sends a
   verdict to `outbox/architect/`.
5. **architect** reads the verdict and writes the **build plan** into
   `outbox/user/`. On stop → delivered to your `user` mailbox.

You don't relay anything by hand — the orchestrator releases one inbox message at
a time and fires the next hop off each turn's completion.

---

## 6. Observe

```bash
./agentainer status -c examples/rag-builder.yaml          # who's up, busy, queue, ACL
./agentainer logs -c examples/rag-builder.yaml -f         # follow the event log live
./agentainer logs embedder -c examples/rag-builder.yaml   # one agent's history
./agentainer inbox embedder -c examples/rag-builder.yaml  # (namespaced) current message
./agentainer queue evaluator -c examples/rag-builder.yaml # mail waiting behind the one released
./agentainer attach embedder -c examples/rag-builder.yaml # watch/type into the live pane
```

`status` looks like:

```
swarm: rag-builder   root: ./rag-builder-workspace
  architect (claude)    up idle queue=0 unread=0 talks=chunker, embedder, evaluator, user
  chunker  (claude)     up idle queue=0 unread=1 talks=architect, embedder
  embedder (codex)      up busy queue=1 unread=0 talks=architect, chunker, evaluator
  evaluator(claude)     up idle queue=0 unread=0 talks=architect, embedder
supervisor: alive
```

Detach a pane with tmux `Ctrl-b d`. Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.

---

## 7. Resume after a stop

```bash
./agentainer down -c examples/rag-builder.yaml          # tear down
./agentainer up -c examples/rag-builder.yaml            # resume is the default
```

On `up`, Agentainer reattaches each conversation via its type's native resume
(`claude --resume <id>`, `codex resume <id>`). A resumed agent is *not* re-sent
the standby prompt. The embedder/evaluator **shared `rag-repo/` persists** across
`down`/`up` (Agentainer never deletes source) — only the agent conversations
reattach. Inspect recorded sessions:

```bash
./agentainer sessions -c examples/rag-builder.yaml
```

See [`sessions-and-resume.md`](../sessions-and-resume.md) for the full story.

---

## 8. Iterate on retrieval quality

The embedder ⇄ evaluator loop is the heart of this swarm. Levers, all via mail:

- **Tighten the eval set** — ask the architect for queries targeting hard cases
  (cross-section answers, tables, code blocks, near-dup chunks).
- **Change the chunking** — ask the architect to have the chunker reconsider
  (e.g. recursive-by-heading instead of fixed-token); the spec flows back to the
  embedder.
- **Re-measure after a fix** — each evaluator→embedder cycle re-runs recall@k, so
  you watch the metric move in the logs rather than trusting prose.

You can also `attach` into the embedder's pane and run `ingest.py`/`query.py`
yourself to sanity-check.

---

## 9. Customize

**Add a `reranker`** — drop a `claude` agent after the evaluator that re-ranks the
embedder's top-k with a cross-encoder, and widen the architect's/evaluator's
`can_talk_to`:

```yaml
  - name: reranker
    type: claude
    can_talk_to: [architect, evaluator]
    command: "claude --dangerously-skip-permissions"
    role: "You are the RERANKER. Take the embedder's top-20 chunks per eval query and re-rank with a cross-encoder; report whether the correct source rises into the top-5 to outbox/evaluator/."
```

**Swap the models** — every agent's `type` is independent. Make the chunker and
evaluator `gemini` (pane-captured) while the embedder stays `codex`, or run all
four on `hermes`; only `command` and capture change (see
[`use-cases/multi-llm-swarm.md`](./multi-llm-swarm.md)). Beware a `type`/`command`
mismatch — it wedges the agent (Tips).

**Tune the ACL** — remove `architect` from the evaluator's `can_talk_to` to route
verdicts through the embedder, or add `evaluator` to the chunker's list so it
hears quality notes. The orchestrator re-validates the graph at `up` and bounces
mail to any non-listed peer.

**Build in a real repo** — point `embedder.workdir` and `evaluator.workdir` at
your actual corpus checkout. See
[`use-cases/custom-workspace.md`](./custom-workspace.md) for `{root}`/`{name}`
placeholders and mailbox-namespacing.

---

## 10. Tips & footguns

- **Keep the architect the only `user`-facing agent** — only it lists `user`. Raw
  retrieval results pass through planning before reaching you; a direct evaluator→
  `user` mail is bounced (ACL) with an in-band `system` note to self-correct.
- **Watch the stop → nudge loop** — a `type`/`command` mismatch means completion
  never fires and the agent pins "busy" forever. `status` showing `busy` with
  unread mail is the tell.
- **The shared repo is a feature, not a bug — but serialize edits.** The embedder
  and evaluator share `rag-repo` on purpose; they still take turns, so collisions
  are rare. Treat it like any shared git checkout.
- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: `read/` is best-effort, `AUTO_ARCHIVE_PRESENTATIONS` (5) auto-archives an
  unhandled message, and a per-pair cap (≤20 msgs / 60s) kills "fix!/ok!" loops.
- **Force-idle a stuck turn:** `./agentainer idle embedder -c examples/rag-builder.yaml`.
- **`remove-session` to reset** (after `down`): wipes runtime + mailboxes but never
  your source `rag-repo/` or config.
- **Availability shapes the ending** — if `user` is away when the architect
  finishes, the build plan is *held* (with a `system` ack), not lost.
- **UI stays local** — the control-plane UI can start processes and type into
  `--yolo`/`--dangerously-skip-permissions` agents; it binds 127.0.0.1 by default.
  See [`remote-access.md`](./remote-access.md).

---

### See also

- [`../getting-started.md`](../getting-started.md) — install and first swarm.
- [`../mail-model.md`](../mail-model.md) — the file-based protocol, end to end.
- [`../sessions-and-resume.md`](../sessions-and-resume.md) — resuming agents and
  the shared-repo caveat.
- [`use-cases/delegation-pipeline.md`](./delegation-pipeline.md) — the hub-and-spoke
  pattern this swarm uses.
- [`use-cases/multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing `claude`/`codex`/
  `gemini`/`hermes` in one swarm.
- [`use-cases/custom-workspace.md`](./custom-workspace.md) — shared workdirs and the
  `{root}`/`{name}` placeholders.
- [`../cli-reference.md`](../cli-reference.md) — every subcommand and flag.

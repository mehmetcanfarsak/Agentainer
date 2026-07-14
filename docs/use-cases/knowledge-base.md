# Use case: the knowledge-base builder swarm

A concrete, end-to-end walkthrough of the shipped `examples/knowledge-base.yaml`
swarm — a four-agent pipeline that turns a pile of raw source docs into a
**navigable knowledge base**: a **lead** hub takes a corpus/path from a human, an
**ingester** reads and summarizes the sources, a **structuring** agent organizes
the summaries into a topic tree, and a **qa_maker** writes FAQ/QA pairs; the lead
delivers the finished KB back to the human. It's the canonical
"receive a brief → do the work in stages → assemble → hand back" loop, wired
entirely through Agentainer's file-based mail model.

Everything below is based on the actual contents of
`examples/knowledge-base.yaml` and the shipped CLI (`lib/cli.py`) and mailroom
(`lib/mail.py`). No API keys are needed to understand the mechanics; to run it
*for real* you supply the coding-CLI commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state. The shared-corpus layout is
> covered in [`custom-workspace.md`](./custom-workspace.md).

---

## 1. The topology

```
           corpus/  (shared working directory)
        ┌──────────────────────────────────────────────┐
        │ SUMMARY.md → TOPICS.md → QA.md                │
        └──────────────────────────────────────────────┘
   user ─────────▶ lead ◀──┬──▶ ingester    (read + summarize sources)
          (final KB)  hub   ├──▶ structuring  (topic tree)
                             └──▶ qa_maker     (FAQ / QA pairs)
   ...ingester/structuring/qa_maker never talk to each other; only lead talks
      to user. Each worker's output file is read by the next stage because they
      share the `corpus/` workdir.
```

Four agents, one directed flow:

1. **`user` → `lead`** — you hand over a source corpus or a path to one.
2. **`lead` → `ingester`** — the lead passes your intent + the corpus path to the
   ingester.
3. **`ingester` → `lead`** — the ingester writes `SUMMARY.md` and reports back.
4. **`lead` → `structuring`** — the summary is handed to structuring to produce
   `TOPICS.md` (the topic tree).
5. **`structuring` → `lead`** — structuring reports the tree shape to the lead.
6. **`lead` → `qa_maker`** — the topic tree goes to qa_maker to write `QA.md`.
7. **`qa_maker` → `lead`** — qa_maker reports coverage to the lead.
8. **`lead` → `user`** — the lead reviews the assembled KB and delivers the
   finished knowledge base to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. The three workers can only deliver to `lead`; only `lead` can
reach `user`. Anything else is bounced back as a `system` message and filed in
`failed/` (see §7).

---

## 2. The config, explained

Here is `examples/knowledge-base.yaml` in full:

```yaml
# 📚 Knowledge-base builder swarm -- a lead hub takes a source corpus/path from a
# human, an ingester reads + summarizes the source docs, a structuring agent
# organizes them into a topic tree, and a qa_maker writes FAQ/QA pairs; the lead
# delivers the finished KB back to the human.
swarm:
  name: kb
  root: ./kb-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: lead
    type: claude
    can_talk_to: [ingester, structuring, qa_maker, user]
    command: "claude --dangerously-skip-permissions"
    role: "You are the LEAD of a knowledge-base build team. ..."
  - name: ingester
    type: claude
    can_talk_to: [lead]
    command: "claude --dangerously-skip-permissions"
    workdir: ./kb-workspace/corpus
    role: "You are the INGESTER. ... write SUMMARY.md in the shared corpus ..."
  - name: structuring
    type: claude
    can_talk_to: [lead]
    command: "claude --dangerously-skip-permissions"
    workdir: ./kb-workspace/corpus
    role: "You are the STRUCTURING agent. ... write TOPICS.md ..."
  - name: qa_maker
    type: claude
    can_talk_to: [lead]
    command: "claude --dangerously-skip-permissions"
    workdir: ./kb-workspace/corpus
    role: "You are the QA_MAKER. ... write QA.md ..."
```

Field by field:

### `swarm`
- **`name: kb`** — the swarm's name (shows up in `status`, logs, sessions).
- **`root: ./kb-workspace`** — the parent directory for the agents' working
  directories and mailboxes. `lead` gets `kb-workspace/lead/` as its own workdir;
  the three workers each point their `workdir` at `kb-workspace/corpus/`, the
  shared directory where the source docs live and the build artifacts
  (`SUMMARY.md`, `TOPICS.md`, `QA.md`) accumulate. Orchestrator state goes under
  `kb-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, and it's ultimately keyed off each
  agent's `type`. For `claude`, whose CLI supports a completion **hook**, setting
  `capture: none` is a footgun — so the config loader *upgrades* it back to `hook`
  and prints a warning at `up`
  (`capture: none on a claude agent gives the orchestrator no way to detect turn
  completion; using the type's default: capture: hook.`). In this example every
  agent is `type: claude`, so all four end up using the Stop hook.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `lead` (type: `claude`)
- **`can_talk_to: [ingester, structuring, qa_maker, user]`** — the lead is the
  hub: it can delegate to the three workers and it is the **only agent that can
  talk to `user`**. That last part matters — keep the human-facing surface to a
  single agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the lead waits for your corpus brief instead of
  proactively mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).
- **`workdir`** — not set, so it defaults to `kb-workspace/lead/`; the lead only
  coordinates and assembles, it doesn't need to sit in the corpus.

### `ingester` / `structuring` / `qa_maker` (type: `claude`, shared `corpus/`)
- **`can_talk_to: [lead]`** — each worker can report *only* to the lead. They
  cannot reach `user`, each other, or anyone else. All staging goes through the
  hub, exactly like the research/editor swarms.
- **`workdir: ./kb-workspace/corpus`** — the shared working directory (see §3).
  This is what lets the pipeline chain: ingester's `SUMMARY.md` is on disk where
  structuring picks it up, and structuring's `TOPICS.md` is where qa_maker finds
  it. Because three agents share one `workdir`, the orchestrator **namespaces
  their mailboxes** (`ingester-inbox/`, `structuring-inbox/`, `qa_maker-inbox/`,
  …) so the folders don't collide — the model never sees this, it's handed the
  exact prefixed paths in every nudge.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  commands (all key-free stubs here).
- **`role`** — each describes the one artifact it owns: `SUMMARY.md`,
  `TOPICS.md`, `QA.md`.

### What's *not* in this config
- **No `pings`.** None of the four agents has a periodic ping
  configured, so no agent is auto-nudged on a timer while idle — the build is
  purely event-driven off real mail. (If a stage stalled, you'd add a ping to the
  relevant worker, or nudge it by hand — see §5.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **No `mail_dir` overrides.** The mailboxes live inside each agent's workdir by
  default; for the three shared-workdir agents they simply get the namespaced
  prefix described above.

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/knowledge-base.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for the four `claude` agents).
2. Creates the runtime dirs (`kb-workspace/.agentainer/…`: log, queue, run,
   sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. Because `ingester`,
   `structuring` and `qa_maker` share `kb-workspace/corpus/`, their folders are
   created namespaced (`ingester-inbox/`, `structuring-inbox/`,
   `qa_maker-inbox/`, …) so nothing clobbers anything. The lead, with its own
   workdir, gets plain names. The `about.md` contact card in each `outbox/<peer>/`
   folder *is* the ACL made visible.
4. **Installs per-type turn detection** — the Claude Stop hook for all four agents.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`. The three workers share the corpus pane-wise as separate sessions,
   each in its own session but pointed at the same directory.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'kb' is up with 4 agent(s)
:: attach with:  tmux attach -t <lead-session>
:: you can use the UI with:  agentainer serve --host 127.0.0.1 -c examples/knowledge-base.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). By default it binds **`127.0.0.1`** only —
never `0.0.0.0` — so it stays on your machine unless you opt in with a token and
an explicit remote host. See the `README.md` "control-plane UI" section and
[`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** the `command:` lines are real Claude invocations but, with no
> keys or an alias stub, the swarm still comes up and routes mail end-to-end — the
> mechanics are identical. Swap in real, keyed commands to produce a real KB.

> **Shared-corpus gotcha:** because the workers write into the same `corpus/`
> directory, they can overwrite each other's *source* files. This config avoids
> that by giving each stage its own artifact name (`SUMMARY.md`, `TOPICS.md`,
> `QA.md`) and by routing strictly through `lead`, so two workers are never editing
> the same file at once. If you add a worker that also writes to `corpus/`, give
> its output a unique filename. See [`custom-workspace.md`](./custom-workspace.md).

---

## 4. Drive a build

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the lead's finished KB as mail (rather than
have it held), turn yourself available first:

```bash
./agentainer user available -c examples/knowledge-base.yaml
```

This rewrites the `user` contact card in the lead's `outbox/user/about.md` to
`Status: available`, so the lead sees you're reachable. (While away, mail to you
is *held* and the sender gets a `system` ack — nothing bounces.)

Now start the build, addressed to the lead, telling it where the source corpus is:

```bash
./agentainer send --to lead "Build a KB from the docs in /srv/our-docs. Audience is new support engineers."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the lead, then — because the
inbox was empty — **released into `inbox/`** and the lead is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§5), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **lead receives the brief.** It reads `inbox/`, sends the corpus path + intent
   to the ingester (`outbox/ingester/`). On stop, that routes to the ingester.
2. **ingester summarizes.** It reads the corpus, writes `SUMMARY.md`, reports to
   the lead (`outbox/lead/`). On stop, that routes back to the lead.
3. **lead hands off to structuring.** It forwards the summary to
   `outbox/structuring/`. On stop, that routes to structuring.
4. **structuring builds the tree.** It reads `SUMMARY.md`, writes `TOPICS.md`,
   reports to the lead. On stop, back to the lead.
5. **lead hands off to qa_maker.** It forwards `TOPICS.md` to `outbox/qa_maker/`.
   On stop, that routes to qa_maker.
6. **qa_maker writes QA.** It reads `TOPICS.md`, writes `QA.md`, reports to the
   lead. On stop, back to the lead.
7. **lead finalizes.** It reviews `SUMMARY.md`/`TOPICS.md`/`QA.md`, writes a short
   KB tour into `outbox/user/`, and on stop that's delivered to your `user`
   mailbox (you'll see it with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a brief, the agents just sit in standby (that's the point of
> the standby prompt). The build only moves when real mail arrives — this swarm has
> no periodic pings to self-start it.

---

## 5. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/knowledge-base.yaml
```

```
swarm: kb   root: ./kb-workspace
  lead (claude) up idle queue=0 unread=0 talks=ingester, structuring, qa_maker, user
  ingester (claude) up idle queue=0 unread=1 talks=lead
  structuring (claude) up idle queue=0 unread=0 talks=lead
  qa_maker (claude) up idle queue=0 unread=0 talks=lead
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/knowledge-base.yaml          # whole swarm, last 20
./agentainer logs -c examples/knowledge-base.yaml -f        # follow live
./agentainer logs ingester -c examples/knowledge-base.yaml  # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox ingester -c examples/knowledge-base.yaml
```

Prints the one released message (headers + body), or `ingester: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue ingester -c examples/knowledge-base.yaml
```

**The corpus on disk** — because the workers share `kb-workspace/corpus/`, you can
watch the build artifacts appear as the pipeline runs:

```bash
ls -1 kb-workspace/corpus/        # source docs + SUMMARY.md, TOPICS.md, QA.md
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach qa_maker -c examples/knowledge-base.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 6. Resume after a stop

Tear the swarm down when you're done:

```bash
./agentainer down -c examples/knowledge-base.yaml
```

Bring it back later and **conversations resume by default**:

```bash
./agentainer up -c examples/knowledge-base.yaml     # resume is the default
```

On `up`, Agentainer reads `kb-workspace/.agentainer/sessions.yaml` (written as
each agent finished its first turn) and reattaches the recorded conversations via
`claude --resume <id>` for each `claude` agent. A resumed agent is *not* re-sent
the standby prompt (its prior context is restored), so if you restart mid-build
the lead still remembers what stage it was on. The shared `corpus/` artifacts on
disk persist independently of the sessions, so the next run can pick up the
half-built KB.

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/knowledge-base.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md) and
the reboot walkthrough in
[`use-cases/resume-after-reboot.md`](./resume-after-reboot.md).

---

## 7. Iterate and refine

The build is a loop, not a one-shot. Once the lead delivers the KB, you can tighten
it by sending follow-ups to the lead:

- **"qa_maker missed our refund policy — add 3 QA pairs about billing."** The lead
  re-engages qa_maker with the narrower brief.
- **"Restructure TOPICS.md: 'Getting started' should come before 'Advanced'."**
  The lead sends a revision note to structuring.
- **"The summary dropped the API reference docs."** The lead asks the ingester to
  re-ingest a sub-path you name.

Each iteration is just another `send --to lead` and another pass through the same
staged pipeline. Because the workers only talk to the lead, every revision is
sequenced and reviewed in one place — you never have two agents negotiating a draft
behind your back.

If a stage stalls (an agent shows `busy` with `unread` mail for a long time), the
likely cause is a **turn-detection miss** — see Tips. You can nudge the state along
with:

```bash
./agentainer idle structuring -c examples/knowledge-base.yaml
```

---

## 8. Customize

### Add a `translator` stage
A common extension: localize the KB into another language before delivery. Add a
worker that reads `QA.md` and emits `QA.<lang>.md`, talking only to the lead:

```yaml
  - name: translator
    type: claude
    can_talk_to: [lead]
    command: "claude --dangerously-skip-permissions"
    workdir: ./kb-workspace/corpus
    role: |
      You are the TRANSLATOR. Read QA.md (and TOPICS.md for context) in the shared
      corpus working directory and produce QA.fr.md: the same QA pairs in French,
      preserving the topic references. Report coverage to outbox/lead/.
```

and add `translator` to the lead's `can_talk_to`. Now the lead can fan the
localization out after qa_maker, before delivering to `user`.

### Swap models (mixed swarm)
The config is all `type: claude` by default to keep turn-detection uniform. To mix
LLMs, change a worker's `type` and `command` (they must match — a `gemini` agent
whose `command` launches Claude never fires completion and pins "busy"):

```yaml
  - name: ingester
    type: gemini
    can_talk_to: [lead]
    capture: pane            # gemini has no Stop hook; poll the pane instead
    command: "gemini --yolo"
    workdir: ./kb-workspace/corpus
```

The lead stays `claude` (it owns the human-facing hook) while cheaper models do the
parallelizable read/summarize work. See
[`use-cases/multi-llm-swarm.md`](./multi-llm-swarm.md) for the full mechanics and
footguns of mixing agent types.

### Tune the ACL
The strict `worker → lead only` graph is intentional — it guarantees every
hand-off is reviewed. If you *want* a worker to talk to another worker (e.g. let
`structuring` pull directly from `ingester` without the lead in the middle), add it
to `can_talk_to`:

```yaml
  - name: structuring
    can_talk_to: [lead, ingester]
```

The orchestrator validates the graph at `up` and bounces any message to a name not
on the sender's list (filed in `failed/`, with a `system` note explaining who the
sender *can* reach). For the general ACL rules see
[`use-cases/delegation-pipeline.md`](./delegation-pipeline.md).

### Point at a real corpus
The `workdir: ./kb-workspace/corpus` in the config is a relative scratch dir. To
build from an existing docs tree, set each worker's `workdir` to the real path (or a
copy) — see [`custom-workspace.md`](./custom-workspace.md) for `workdir`, `mail_dir`,
`env`, and the namespacing rules that keep the shared directory's mailboxes from
colliding.

---

## 9. Tips & footguns

- **Keep the lead the only `user`-facing agent.** In this config only the lead
  lists `user` in `can_talk_to`. That gives you a single point of contact and a
  clean funnel: raw artifacts always pass through the lead's review before they
  reach you. If a worker tries to mail `user` directly, the orchestrator bounces it
  (ACL) and drops a `system` note in the worker's inbox explaining who it *can*
  message — the model self-corrects in-band.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually fires**
  — a `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't
  launch Claude) means completion never triggers and the agent pins "busy" forever.
  `status` showing an agent `busy` for a long time with `unread` mail is the tell.

- **Shared workdir = shared files, not shared mailboxes.** The three workers share
  `corpus/` for the *docs and build artifacts*, but the orchestrator namespaces
  their `inbox/`/`outbox/`/… so mail never collides. The collision you *can* get is
  at the file level: two workers writing the same filename in `corpus/`. This config
  avoids it with distinct artifact names; keep that discipline when you add a stage.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime + mailboxes)
  and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/knowledge-base.yaml
  ./agentainer remove-session -c examples/knowledge-base.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config, and it leaves
  `corpus/` on disk unless you delete it yourself.

- **Availability shapes the ending.** If `user` is **away** when the lead finishes,
  your finished KB is *held* (with a `system` "the user is away" ack to the lead)
  rather than lost — read it later with `agentainer user inbox` or flip yourself
  available and it's delivered.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the file-based mail model in depth.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resume after a stop/reboot.
- [`use-cases/delegation-pipeline.md`](./delegation-pipeline.md) — ACL and hub patterns.
- [`use-cases/multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing Claude/Codex/Gemini/Hermes.
- [`use-cases/custom-workspace.md`](./custom-workspace.md) — shared workdirs and mailbox namespacing.

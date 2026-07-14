# Use case: the glossary term writer

A concrete, end-to-end walkthrough of the shipped `examples/glossary-term-writer.yaml`
swarm — a four-agent pipeline that **mines terms from a domain**, **writes a
definition for each**, **adds a concrete usage example**, and **links the terms
internally** to build SEO topic clusters. It's the canonical "mine → define →
exemplify → link" loop, wired entirely through Agentainer's file-based mail model.

Everything below is based on the actual contents of
`examples/glossary-term-writer.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 0. Why a glossary is a great SEO asset

A glossary is one of the highest-leverage pieces of organic content you can own:

- **It captures "what is X definition" search intent directly.** People Google
  definitional queries constantly (`what is a sidecar container`, `kubernetes
  definition`, `observability vs monitoring`). A clean term page targets that
  intent head-on.
- **Each term is its own indexable page.** Ten terms = ten doorway pages into your
  site, each ranking for a different long-tail query.
- **Internal links build topic clusters.** When the `linker` wires "sidecar" →
  "service mesh" → "pod", you signal to search engines that these pages form a
  coherent subject — which lifts the whole cluster, not just one page.
- **Definitions are evergreen and quotable.** Other sites link to good glossaries,
  earning you backlinks and a knowledge-panel-worthy footprint.

This swarm produces exactly that asset, term by term, with one agent specializing
in each step so quality doesn't get diluted by asking one model to mine, define,
exemplify, and link all at once.

---

## 1. The topology

```
  user  <-->  term_miner              (hub: the ONLY agent that talks to user)
               /      |      \
     definition_writer  example_writer  linker
          (each talks only back to term_miner)
```

A hub-and-spoke graph:

1. **`user` → `term_miner`** — you send the domain/topic ("build a glossary of
   cloud-native observability terms").
2. **`term_miner` → `definition_writer`** — the miner mines the term list and
   feeds the first term to the definition writer.
3. **`term_miner` → `example_writer`** — once a definition lands, the miner hands
   term + definition to the example writer.
4. **`term_miner` → `linker`** — once the example lands, the miner hands
   term + definition + example to the linker to connect it to its neighbors.
5. **`term_miner` → `user`** — when every term is fully built, the miner delivers
   the finished glossary back to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. `definition_writer`, `example_writer`, and `linker` can each
only deliver to `term_miner`; anything else (including a direct mail to `user`)
is bounced back as a `system` message and filed in `failed/` (see §7). The miner
is the single point of contact with the human and the only agent that sequences
the work.

---

## 2. The config, explained

Here is `examples/glossary-term-writer.yaml` in full:

```yaml
# =============================================================================
# 📖 Glossary term writer -- a term hub mines domain terms, then fans them out
# to a definition writer, an example writer, and a linker that builds the internal
# topic-cluster links, all funneled through one hub that talks to the human.
#
#   cp examples/glossary-term-writer.yaml my-glossary.yaml
#   agentainer up    -c my-glossary.yaml
#   agentainer send  -c my-glossary.yaml --to term_miner "Build a glossary of cloud-native observability terms."
#   agentainer down  -c my-glossary.yaml
#
# The graph is a hub-and-spoke: term_miner owns the term list and the human;
# definition_writer / example_writer / linker never freelance to the user and
# never talk to each other -- they only report back to the miner, so every
# hand-off is sequenced and reviewed in one place.
#
#   user  <-->  term_miner            (hub: the ONLY agent that talks to user)
#               /     |     \
#     definition_writer  example_writer  linker
#       (each talks only back to term_miner)
#
# Key-free: no API keys live in this file. The `command:` lines are placeholder
# launchers for the real CLIs -- swap each for a mock bash loop for a keyless demo.
# =============================================================================

swarm:
  name: glossary-term-writer
  root: ./glossary-term-writer-workspace

defaults:
  capture: none              # tightened per agent (claude/codex auto-upgrade to hook)
  can_talk_to: []            # deny-by-default ACL; each agent opts in below

agents:
  - name: term_miner
    type: claude
    can_talk_to: [definition_writer, example_writer, linker, user]
    command: "claude --dangerously-skip-permissions"
    capture: none            # claude has a Stop hook -> auto-upgraded to capture: hook
    role: |
      You are the GLOSSARY TERMS MINER and the hub of this glossary factory. You
      own the term list and you are the ONLY agent who talks to the user. You do
      not write definitions, examples, or links yourself; you mine the domain,
      sequence the work, and guard quality.
      Your team:
        - definition_writer (writes a precise, sourced definition for one term)
        - example_writer (writes a concrete, realistic usage example for one term)
        - linker (writes internal links that form topic clusters across the terms)
      Run it like this: (1) from the user's topic, mine a flat list of the key
      domain terms -- a short paragraph of scope, the target audience, and the
      terms to cover -- and send the first term to the definition_writer; (2) when
      a definition lands, hand the term + definition to the example_writer; (3) when
      the example lands, hand the term + definition + example to the linker so
      it can connect this term to its neighbors; (4) once a term is fully built
      (definition + example + links), assemble it and either start the next term
      or, when the list is exhausted, deliver the finished glossary to the user.
      Proceed one term at a time so each is fully built before the next begins.
      Cut scope before you ship a term that is thin or unsourced.
      MAILBOX: when a message lands in your inbox/, read it and act. To send, write
      a file into outbox/<name>/ (read outbox/<name>/about.md first to see who they
      are and whether they're available), then finish your turn. When you have
      handled an inbox message, move it to read/. You may only message the agents
      in your can_talk_to list.

  - name: definition_writer
    type: claude
    can_talk_to: [term_miner]
    command: "claude --dangerously-skip-permissions"
    capture: none            # claude Stop hook -> auto-upgraded to capture: hook
    role: |
      You are the DEFINITION WRITER. Given a single term from the term_miner,
      write one precise, accurate, sourced definition (2-4 sentences) that a
      curious non-expert in the domain could understand. State what the term is,
      what problem it solves, and how it differs from the nearest related concept.
      If the term's scope is ambiguous, ask the term_miner rather than inventing a
      definition. Write the definition to DEFINITION.md in your working directory
      and return it to the term_miner.

  - name: example_writer
    type: codex
    can_talk_to: [term_miner]
    command: "codex --yolo"
    capture: none            # codex has a notify hook -> auto-upgraded to capture: hook
    role: |
      You are the EXAMPLE WRITER. Given a term and its definition from the
      term_miner, write one concrete, realistic usage example (code snippet, config
      block, or short scenario) that shows the term in action. Keep it minimal and
      correct; annotate the lines that matter. Do not redefine the term -- the
      definition_writer already did. Write the example to EXAMPLE.md in your
      working directory and return it to the term_miner.

  - name: linker
    type: gemini
    can_talk_to: [term_miner]
    command: "gemini --yolo"
    capture: pane            # gemini has no completion hook -> poll the tmux pane
    role: |
      You are the LINKER. Given a term, its definition, and its example from the
      term_miner, write the internal links that turn the glossary into SEO topic
      clusters: 3-5 links to other terms in this glossary (by name) with one-line
      notes on why each link helps a reader go deeper, plus a suggested "see also"
      cluster heading. Prefer linking terms that share a concept or a workflow.
      Write the links to LINKS.md in your working directory and return them to the
      term_miner.
```

Field by field:

### `swarm`
- **`name: glossary-term-writer`** — the swarm's name (shows up in `status`,
  logs, sessions).
- **`root: ./glossary-term-writer-workspace`** — the parent directory for the
  agents' working directories and mailboxes. Each agent gets
  `glossary-term-writer-workspace/<name>/` as its workdir (created on `up`), and
  its mailbox folders live alongside. Orchestrator state goes under
  `glossary-term-writer-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, and it's ultimately keyed off each
  agent's `type`. For `claude` and `codex`, whose CLIs support a completion
  **hook**, setting `capture: none` is a footgun — so the config loader
  *upgrades* it back to `hook` and prints a warning at `up`
  (`capture: none on a claude agent gives the orchestrator no way to detect turn
  completion; using the type's default: capture: hook.`). Net effect here:
  `term_miner` and `definition_writer` (claude) and `example_writer` (codex) use
  their hook; the `linker` is `gemini` and explicitly overrides to `pane`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `term_miner` (type: `claude`)
- **`can_talk_to: [definition_writer, example_writer, linker, user]`** — the
  miner is the hub: it can delegate to all three writers and is the **only agent
  that can talk to `user`**. That last part matters — keep the human-facing
  surface to a single agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command, e.g. a
  shell alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity: mine the terms, sequence one term through
  define → exemplify → link, then deliver the glossary. On `up` this becomes the
  agent's first prompt, wrapped in a **standby notice**, so the miner waits for
  your topic instead of proactively mailing peers. Note the `MAILBOX:` reminder
  at the end — the orchestrator-injected hub mailbox instructions.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `definition_writer` (type: `claude`)
- **`can_talk_to: [term_miner]`** — can only report back to the hub. It
  deliberately cannot reach the other writers or `user`.
- **`capture: none`** — claude → auto-upgraded to `hook`.
- **`role`** — "write one precise, sourced definition per term."

### `example_writer` (type: `codex`)
- **`can_talk_to: [term_miner]`** — reports back to the hub only.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`capture: none`** — codex has a `notify` hook → auto-upgraded to `hook`.
- **`role`** — "write one concrete usage example per term."

### `linker` (type: `gemini`)
- **`can_talk_to: [term_miner]`** — reports back to the hub only.
- **`capture: pane`** — Gemini's CLI can't call a completion program, so
  Agentainer detects "turn done" by **polling the tmux pane** until it stops
  changing. (This is why the linker explicitly overrides the `none` default.)
- **`command: "gemini --yolo"`** — placeholder launch command.
- **`role`** — "write the internal links that turn the glossary into topic
  clusters."

### What's *not* in this config
- **No `pings`.** None of the four agents has a periodic ping
  configured, so no agent is auto-nudged on a timer while idle — the pipeline is
  purely event-driven off real mail. (If you wanted the miner to poke a slow
  writer, you'd add a `pings` cron rule to it.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **No shared workdir.** Each agent has its own directory, so their
  `DEFINITION.md` / `EXAMPLE.md` / `LINKS.md` files never collide — no quoting of
  the `mail_dir` is needed here.

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/glossary-term-writer.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for `term_miner` / `definition_writer` /
   `example_writer`).
2. Creates the runtime dirs
   (`glossary-term-writer-workspace/.agentainer/…`: log, queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: `term_miner` gets
   `outbox/definition_writer/`, `outbox/example_writer/`, `outbox/linker/`,
   `outbox/user/`; each writer gets `outbox/term_miner/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `term_miner`
   and `definition_writer`, the Codex `notify` hook for `example_writer`, and
   (for the pane-captured `linker`) arranges pane polling.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'glossary-term-writer' is up with 4 agent(s)
:: attach with:  tmux attach -t <term_miner-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/glossary-term-writer.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only bind (`127.0.0.1` by default). See the `README.md` "control-plane
UI" section.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole pipeline route mail with no API keys — the mechanics are identical.

---

## 4. Drive a glossary

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the miner's finished glossary as mail (rather
than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/glossary-term-writer.yaml
```

This rewrites the `user` contact card in the miner's `outbox/user/about.md` to
`Status: available`, so the miner sees you're reachable. (While away, mail to you
is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the topic into the swarm, addressed to the miner:

```bash
./agentainer send --to term_miner "Build a glossary of cloud-native observability terms: start with span, trace, metric, log, and exemplar."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the miner, then — because the
inbox was empty — **released into `inbox/`** and the miner is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§5), you'll see the pipeline advance one turn at a time, one
term at a time. Each arrow is a `stop → sweep → route → release → nudge` cycle:

1. **term_miner receives the topic.** It reads `inbox/`, mines the term list, and
   writes the first term into `outbox/definition_writer/`. On stop, that routes to
   the definition writer and nudges it.
2. **definition_writer writes the definition.** It reads its inbox, writes
   `DEFINITION.md`, and writes the term + definition into `outbox/term_miner/`. On
   stop, that routes back to the miner.
3. **term_miner delegates to the example_writer.** It forwards term + definition
   into `outbox/example_writer/`. On stop, that routes to the example writer.
4. **example_writer writes the example.** It writes `EXAMPLE.md` and returns term
   + definition + example to `outbox/term_miner/`. On stop, that routes back.
5. **term_miner delegates to the linker.** It forwards the full package into
   `outbox/linker/`. On stop, that routes to the linker.
6. **linker writes the internal links.** It writes `LINKS.md` and returns the
   linked term back to `outbox/term_miner/`. On stop, that routes back.
7. **term_miner proceeds to the next term** (back to step 1) or, when the list is
   exhausted, **assembles the glossary and writes it into `outbox/user/`** — which
   (if you're available) delivers to your `user` mailbox.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a topic, the agents just sit in standby (that's the point of
> the standby prompt). The pipeline only moves when real mail arrives — this swarm
> has no periodic pings to self-start it.

---

## 5. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/glossary-term-writer.yaml
```

```
swarm: glossary-term-writer   root: ./glossary-term-writer-workspace
  term_miner (claude) up idle queue=0 unread=0 talks=definition_writer, example_writer, linker, user
  definition_writer (claude) up idle queue=0 unread=1 talks=term_miner
  example_writer (codex) up idle queue=0 unread=0 talks=term_miner
  linker (gemini) up idle queue=0 unread=0 talks=term_miner
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/glossary-term-writer.yaml          # whole swarm, last 20
./agentainer logs -c examples/glossary-term-writer.yaml -f       # follow live
./agentainer logs linker -c examples/glossary-term-writer.yaml   # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox definition_writer -c examples/glossary-term-writer.yaml
```

Prints the one released message (headers + body), or
`definition_writer: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue definition_writer -c examples/glossary-term-writer.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach linker -c examples/glossary-term-writer.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom
— handy for un-sticking an agent, but the mail model is the normal path.)

---

## 6. Resume after a stop

Tear the swarm down when you're done:

```bash
./agentainer down -c examples/glossary-term-writer.yaml
```

Bring it back later and **conversations resume by default**:

```bash
./agentainer up -c examples/glossary-term-writer.yaml     # resume is the default
```

On `up`, Agentainer reads
`glossary-term-writer-workspace/.agentainer/sessions.yaml` (written as each agent
finished its first turn) and reattaches the recorded conversations via each
type's native resume: `claude --resume <id>` for `term_miner` and
`definition_writer`, `codex resume <id>` for `example_writer`. The `linker`
(`gemini`) has no resume bridge, so it starts a **fresh** conversation with a
warning — its linking role is stateless-per-term anyway. A resumed agent is *not*
re-sent the standby prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/glossary-term-writer.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md) and
the reboot walkthrough in
[`use-cases/resume-after-reboot.md`](./resume-after-reboot.md).

---

## 7. Tips & footguns

- **Keep the miner the only `user`-facing agent.** In this config only
  `term_miner` lists `user` in `can_talk_to`. That gives you a single point of
  contact and a clean funnel: raw definitions/examples/links always pass through
  the miner's sequence before they reach you. If a writer tries to mail `user`
  directly, the orchestrator bounces it (ACL) and drops a `system` note in the
  writer's inbox explaining who it *can* message — the model self-corrects in-band.

- **One term at a time.** The miner's role tells it to fully build each term
  (definition + example + links) before starting the next. This keeps the
  one-at-a-time inbox release clean and means a slow writer can't interleave with
  a later term's work. Resist telling it to fan all terms out in parallel — the
  hub-and-spoke ACL is built for sequential hand-offs, not a broadcast.

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

- **Force-idle if a pane-captured agent's turn never registers.** The `linker`
  uses pane polling; if its capture never fires you can nudge the state along:
  ```bash
  ./agentainer idle linker -c examples/glossary-term-writer.yaml
  ```

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down               -c examples/glossary-term-writer.yaml
  ./agentainer remove-session     -c examples/glossary-term-writer.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

- **Availability shapes the ending.** If `user` is **away** when the miner
  finishes, your glossary is *held* (with a `system` "the user is away" ack to the
  miner) rather than lost — read it later with `agentainer user inbox` or flip
  yourself available and it's delivered.

---

## 8. Search intent this swarm targets

The published glossary earns traffic from queries like:

- **"what is X definition"** — the primary definitional-intent phrase for every
  term the miner produces.
- **"X meaning / X explained"** — the everyday synonym of the definitional query.
- **"X vs Y"** — comparisons the `definition_writer` pre-empts by stating how
  each term differs from its nearest neighbor.
- **"X example"** — satisfied directly by the `example_writer`'s output.
- **"X see also / related terms"** — the topic-cluster surfaced by the `linker`'s
  internal links, which also helps search engines map the cluster.

Because each term is built by a dedicated agent, the result is consistent in depth
and structure across the whole glossary — exactly what a search engine rewards
with cluster-level ranking.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`cli-reference.md`](../cli-reference.md) — every subcommand and flag.
- `examples/glossary-term-writer.yaml` — the config this page describes.
- `examples/content-studio.yaml` / `examples/seo-content-factory.yaml` — sibling
  content pipelines with the same hub-and-spoke shape.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

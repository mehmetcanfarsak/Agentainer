# Use case: the technical-documentation swarm

Point this swarm at a codebase and it will **document your code**: a hub
**doc_lead** plans the outline, a **codebase_analyzer** maps the public surface,
and three writers turn that map into an **API reference**, **tutorials**, and a
**changelog** — all working in one shared checkout of the repo. It's the
canonical "read the code → agree on an outline → write the sections → review for
accuracy" loop, wired entirely through Agentainer's file-based mail model.

If you've ever wanted to **generate API docs from source**, **auto-generate a
changelog from git history**, or **write tutorials that actually run against your
code** without babysitting a single chat window, this is the shape of it: one
lead, four specialists, a star topology that keeps every decision funnelling
through one place.

Everything below is based on the actual contents of
`examples/technical-documentation.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md)
> first, then the four-folders recap in the repo `README.md`. The one-line
> version: an agent **reads a file** to receive mail and **writes a file** to
> send it; the orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Why this is a great fit for the mail model

Documentation is a natural fan-out/fan-in job, which is exactly what the file
mail model does well:

- **One source of truth, many outputs.** The analyzer reads the code once and
  hands everyone the same map, so the API reference, tutorials, and changelog all
  describe the *same* software instead of three drifting interpretations.
- **A single human-facing surface.** Only `doc_lead` talks to `user`, so you
  brief one agent and get one finished doc set back — never four half-answers.
- **Accuracy is reviewable.** Every draft flows back through the lead, who checks
  it against the code before calling it done. Docs that lie are the failure mode;
  the star topology puts a reviewer between every writer and "shipped".
- **It runs unattended.** The whole pipeline advances off turn-completion — you
  send one task and watch the log, rather than relaying messages by hand.

---

## 2. The topology

```
  document ./repo
  user ───────────────▶ doc_lead   (the hub: the only agent that talks to you)
        (doc set)  ◀────────�
                            │
        +----------+--------+--------+-----------------+
        │          │                 │                 │
   codebase_   api_doc_        tutorial_          changelog_
    analyzer    writer          writer             writer
        \__________\________________\_________________/
                 all four share {root}/repo
```

Five agents, a star with `doc_lead` at the centre:

1. **`user` → `doc_lead`** — you point the lead at the repo and ask for docs.
2. **`doc_lead` → `codebase_analyzer`** — the lead asks for a map of the public
   surface first.
3. **`codebase_analyzer` → `doc_lead`** — the analyzer reports the ground truth.
4. **`doc_lead` → `api_doc_writer` / `tutorial_writer` / `changelog_writer`** —
   the lead briefs each writer on the sections it owns.
5. **writers → `doc_lead`** — each writer reports drafts back for accuracy review.
6. **`doc_lead` → `user`** — the lead returns the finished doc set to you.

The routing isn't a suggestion — it's *enforced* by each agent's `can_talk_to`
list. The four writers can only deliver to `doc_lead`; if one tries to mail
another writer (or `user`) directly, the orchestrator bounces it back as a
`system` message and files it in `failed/` (see the research walkthrough §7). The
lead is the only agent with `user` on its list.

**The shared workdir.** All four leaves set `workdir: "{root}/repo"` — one
checkout of the code being documented. They read the same source and write docs
alongside it. Because the directory is shared, Agentainer automatically
**namespaces each agent's mailbox** (`repo/codebase_analyzer-inbox`,
`repo/api_doc_writer-inbox`, …) so the four don't collide — the model never sees
this; every nudge hands it the exact paths. `validate` prints a
"share the working directory" warning to remind you they can overwrite each
other's files and interleave commits in a shared git checkout; here that sharing
is the point (see Notes).

---

## 3. The config

Here is the core of `examples/technical-documentation.yaml` (see the file for the
full header comment and every `role`):

```yaml
swarm:
  name: technical-documentation
  root: ./technical-documentation-workspace

defaults:
  capture: none              # tightened per agent below
  can_talk_to: []            # star topology set explicitly per agent

agents:
  - name: doc_lead
    type: claude
    can_talk_to: [codebase_analyzer, api_doc_writer, tutorial_writer, changelog_writer, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the DOC LEAD -- the hub of a documentation team ...
      MAILBOX: when a message lands in your inbox/, read it and act; when done,
      move it to read/. To send, write a file into outbox/<name>/ ...

  - name: codebase_analyzer
    type: claude
    can_talk_to: [doc_lead]
    command: "claude --dangerously-skip-permissions"
    capture: pane
    workdir: "{root}/repo"
    role: |
      You are the CODEBASE ANALYZER. Read the source ... report to the doc_lead.

  - name: api_doc_writer      # + tutorial_writer, changelog_writer
    type: claude
    can_talk_to: [doc_lead]
    command: "claude --dangerously-skip-permissions"
    capture: pane
    workdir: "{root}/repo"
    role: |
      You are the API REFERENCE WRITER. ... report progress to the doc_lead.
```

👉 Full config: [`examples/technical-documentation.yaml`](../../examples/technical-documentation.yaml)

Field by field:

### `swarm`
- **`name: technical-documentation`** — shows up in `status`, logs, sessions.
- **`root: ./technical-documentation-workspace`** — parent for the agents'
  workdirs and mailboxes. `doc_lead` gets its own `…/doc_lead/` workdir; the four
  writers all share `…/repo/`. Orchestrator state lives under
  `…/.agentainer/` (never commit it).

### `defaults`
- **`capture: none`** — the default turn-detection mode, tightened per agent. For
  `claude`, whose CLI supports a completion **hook**, `capture: none` is a footgun,
  so the loader **upgrades** `doc_lead` back to `hook` and prints a warning at
  `up`. The four writers override to `capture: pane` explicitly.
- **`can_talk_to: []`** — a safe floor; every agent states its own ACL.

### `doc_lead` (type: `claude`)
- **`can_talk_to: [codebase_analyzer, api_doc_writer, tutorial_writer, changelog_writer, user]`**
  — the hub, and the **only agent that can talk to `user`**.
- **`role`** — a HUB `MAILBOX` block: the standing identity plus the reminder of
  how to read (`inbox/` → `read/`) and send (`outbox/<name>/`, read `about.md`
  first). On `up` this becomes the first prompt, wrapped in a standby notice so
  the lead waits for your task instead of proactively mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### the four writers (all type: `claude`, `capture: pane`)
- **`can_talk_to: [doc_lead]`** — each reports only upward to the lead.
- **`workdir: "{root}/repo"`** — the shared checkout of the code being documented.
  `{root}` expands to the resolved `swarm.root`.
- **`capture: pane`** — turn completion is detected by polling the tmux pane until
  it stops changing (an explicit choice here so all leaves detect uniformly).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/technical-documentation.yaml
```

Then drop the code you want documented into
`technical-documentation-workspace/repo/` (or make that a checkout/symlink of your
project), turn yourself available, and brief the lead:

```bash
./agentainer user available -c examples/technical-documentation.yaml
./agentainer send --to doc_lead -c examples/technical-documentation.yaml \
  "Document the code in ./repo: an API reference, a getting-started tutorial, and a CHANGELOG."
```

What happens next, one turn at a time (each hop is a
`stop → sweep → route → release → nudge` cycle):

1. **doc_lead plans.** It reads its inbox and writes a request into
   `outbox/codebase_analyzer/` for a map of the public surface. On stop, that
   routes to the analyzer.
2. **codebase_analyzer maps the code.** It reads the shared `repo/`, writes a
   factual map back into `outbox/doc_lead/`. On stop, that routes to the lead.
3. **doc_lead briefs the writers.** It writes three delegations —
   `outbox/api_doc_writer/`, `outbox/tutorial_writer/`, `outbox/changelog_writer/`
   — each scoping the sections that writer owns.
4. **writers draft in the shared repo.** Each writes its docs alongside the code
   and reports a draft into `outbox/doc_lead/`.
5. **doc_lead reviews and finalizes.** It checks each draft against the code, and
   when the set holds together writes the finished docs into `outbox/user/`. On
   stop, that's delivered to your `user` mailbox.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

**Observe** it exactly as in the research walkthrough:

```bash
./agentainer status -c examples/technical-documentation.yaml   # who's idle/busy, queue, unread
./agentainer logs   -c examples/technical-documentation.yaml -f # the durable event log, live
./agentainer inbox doc_lead -c examples/technical-documentation.yaml
```

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole star route mail with no API keys — the mechanics are identical.

---

## 5. What people search for (and how this answers it)

- **"generate API documentation from source code"** → `api_doc_writer` turns the
  analyzer's map + the code into a signature-accurate reference.
- **"auto-generate a changelog from git history"** → `changelog_writer` builds an
  honest `CHANGELOG.md` grouped Added/Changed/Fixed/Removed from the repo's
  history and diffs.
- **"write developer tutorials / getting-started guides"** → `tutorial_writer`
  produces runnable, task-oriented walkthroughs verified against the code.
- **"AI agent to document my codebase" / "multi-agent documentation pipeline"** →
  the whole swarm: a lead plus specialists coordinating over files, no framework.
- **"keep docs in sync with code"** → re-send the lead a task after changes; the
  analyzer re-maps and the writers update their sections.

---

## 6. Notes & footguns

- **The shared `repo/` workdir is intentional — mind the git interleaving.** All
  four writers share one checkout so they document the same source. `validate`
  warns that they "share the working directory" — expected here. If they'll each
  `git commit`, be aware their commits interleave in one history; prefer having
  the writers put docs under distinct paths (`docs/api/`, `docs/tutorials/`,
  `CHANGELOG.md`) so they don't overwrite each other. Mailboxes are auto-namespaced
  (`repo/<name>-inbox`, …), so mail never collides even though the dir is shared.

- **Keep `doc_lead` the only `user`-facing agent.** Only the lead lists `user` in
  `can_talk_to`, giving you one point of contact and a review gate: no draft
  reaches you until the lead has checked it against the code. A writer that tries
  to mail `user` directly gets bounced (ACL) with a `system` note explaining who
  it *can* message — the model self-corrects in-band.

- **Accuracy over fluency.** Every `role` tells its writer to trust the code over
  the map and flag contradictions to the lead. Docs that read well but lie are the
  real failure; the analyzer-first sequence and the lead's review are there to
  catch it.

- **Watch the stop → nudge loop.** The clock runs on turn completion: an agent
  stops, its outbox is swept, mail is routed, recipients are nudged. If an agent
  seems stuck, check its turn detection actually fires — `status` showing an agent
  `busy` for a long time with `unread` mail is the tell. The writers use
  `capture: pane`; if a pane-captured turn never registers you can nudge state
  along with `./agentainer idle <name> -c examples/technical-documentation.yaml`.

- **The UI binds loopback by default.** `agentainer serve` is a control plane that
  can type into agents running `--dangerously-skip-permissions`; it binds
  `127.0.0.1` unless you opt into a remote bind with a token. Keep it that way
  unless you know what you're exposing.

---

### See also

- [`use-cases/research-swarm.md`](./research-swarm.md) — the sibling
  delegate → do → review walkthrough, with fuller CLI/observe/resume detail.
- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`cli-reference.md`](../cli-reference.md) — every subcommand and flag.
- `examples/software-company.yaml` — a larger hub-and-spoke team.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

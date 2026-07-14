# Use case: the academic paper co-writing swarm

A concrete, end-to-end walkthrough of the shipped `examples/academic-coauthor.yaml`
swarm — a four-agent pipeline where a **literature** lead surveys prior work and
drives a structured writing loop: a **methodologist** pressure-tests the approach,
a **writer** drafts the paper section by section, and a **citation** checker
verifies every claim and reference before a person signs off. It's the "survey →
design → draft → fact-check" loop of a real paper, wired entirely through
Agentainer's file-based mail model.

> **This is decision support, not authorship.** The humans on the paper own the
> authorship, the scientific integrity, and every claim that ships. Treat all
> agent output as a draft to verify — especially citations, which LLMs fabricate.
> The `citation` agent narrows that risk; it does not remove it.

Everything below is based on the actual contents of `examples/academic-coauthor.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you supply the coding-CLI
commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in [`mail-model.md`](../mail-model.md). The one-line
> version: an agent **reads a file** to receive mail and **writes a file** to send
> it; the orchestrator owns all routing, ACL, IDs, and state.

**Who this is for:** researchers assembling a survey or related-work section,
graduate students structuring a first paper, and anyone who wants a rigor check
and a citation pass built into the drafting loop instead of bolted on at the end.

---

## 1. The topology

```
              research question
   user ───────────────────────▶ literature ◀──┬──▶ methodologist  (rigor/validity)
                    (verdict) ◀── hub           └──▶ writer         (drafts sections)
          ◀──────────────────── citation ◀──────────  (checks claims + references)
```

Four agents, one hub-and-spoke flow:

1. **`user` → `literature`** — you send the research question.
2. **`literature` → `methodologist`** — the lead proposes an approach; the
   methodologist flags threats to validity before any drafting starts.
3. **`literature` → `writer`** — with the method settled, the lead hands over the
   outline and survey; the writer drafts section by section.
4. **`literature` → `citation`** — each drafted section goes to the citation
   checker to verify claims and references.
5. **`citation` → `literature`** — the checker returns a fix list to the lead.
6. **`citation` → `user`** and **`literature` → `user`** — the checker sends you a
   plain-English confirmation summary, and the lead delivers the finished paper.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. `methodologist` and `writer` can talk **only** to
`literature`; they can't reach each other or the human. `literature` and
`citation` are the **only** two agents that can talk to `user`. Anything else is
bounced back as a `system` message and filed in `failed/` (see §7).

Why two agents talk to `user`? Because they serve different purposes: the lead
delivers the *paper*, and the citation checker delivers the *integrity verdict*
directly, so a person can make the final call without it being paraphrased by the
same agent that ran the project.

---

## 2. The config, explained

Here is `examples/academic-coauthor.yaml` (roles abbreviated — read the file for
the full standing instructions):

```yaml
swarm:
  name: academic-coauthor
  root: ./academic-coauthor-workspace
defaults:
  capture: none              # mock agents don't fire a turn-completion hook
  can_talk_to: []            # tightened per agent below
agents:
  - name: literature
    type: claude
    can_talk_to: [methodologist, writer, citation, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the LITERATURE LEAD and the hub of this paper. ...
  - name: methodologist
    type: claude
    can_talk_to: [literature]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the METHODOLOGIST. ...
  - name: writer
    type: claude
    can_talk_to: [literature]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the WRITER. ...
  - name: citation
    type: claude
    can_talk_to: [literature, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the CITATION AND CLAIMS CHECKER, the paper's last line of defense. ...
```

Field by field:

### `swarm`
- **`name: academic-coauthor`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./academic-coauthor-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent gets
  `academic-coauthor-workspace/<name>/` as its workdir (created on `up`), and its
  mailbox folders live alongside. Orchestrator state goes under
  `academic-coauthor-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — this is the *mock-agent* default. **But note:** `capture`
  is how Agentainer knows a turn finished, and it's keyed off each agent's `type`.
  Every agent here is `type: claude`, whose CLI supports a completion **hook**, so
  `capture: none` is a footgun — the config loader *upgrades* it back to `hook`
  and prints a warning at `up`
  (`capture: none on a claude agent gives the orchestrator no turn-completion
  signal -- auto-upgraded to capture: hook.`). Net effect: all four agents use
  their Stop hook. Leave `capture: none` only if you swap the commands for mock
  bash loops.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent states
  its own list explicitly, so this default is just a safe floor.

### `literature` (type: `claude`, the hub)
- **`can_talk_to: [methodologist, writer, citation, user]`** — the lead is the
  hub: it briefs the methodologist and writer, sends drafts to the citation
  checker, and delivers the finished paper to `user`.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the lead waits for your question instead of proactively
  mailing peers. It carries the hub **MAILBOX reminder** (read inbox/, act, move
  to read/; write to outbox/<name>/ to send).
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `methodologist` (type: `claude`)
- **`can_talk_to: [literature]`** — reports rigor and validity concerns only back
  to the lead. It deliberately cannot reach the writer directly or the `user` —
  method decisions funnel through one place.
- **`role`** — assess construct/internal/external validity, name threats to
  validity, and return a concise rigor memo; it does not draft the paper.

### `writer` (type: `claude`)
- **`can_talk_to: [literature]`** — drafts sections and returns them to the lead;
  cannot talk to the methodologist or `user`.
- **`role`** — draft section by section into `DRAFT.md`, keep claims proportional
  to evidence, never fabricate a reference, and mark spots that need a source with
  a `[CITE: ...]` placeholder for the citation checker.

### `citation` (type: `claude`)
- **`can_talk_to: [literature, user]`** — returns a fix list to the lead **and**
  sends the human a plain-English confirmation summary. It is the second agent
  allowed to talk to `user`, by design (see §1).
- **`role`** — verify every claim is supported and every reference is real and
  well-formed; treat fabricated/hallucinated citations as the top-priority find;
  produce a checklist verdict. It carries its own **MAILBOX reminder** because it,
  too, sends to `user`.

### What's *not* in this config
- **No `pings`.** No agent is auto-nudged on a timer while
  idle — the pipeline is purely event-driven off real mail. (If you wanted the
  lead to poke a slow writer, you'd add a `pings` cron rule to it.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/academic-coauthor.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for all four claude agents when you leave the
   real commands in).
2. Creates the runtime dirs (`academic-coauthor-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: the lead gets
   `outbox/methodologist/`, `outbox/writer/`, `outbox/citation/`, `outbox/user/`;
   the methodologist and writer each get only `outbox/literature/`; the citation
   checker gets `outbox/literature/` and `outbox/user/`.
4. **Installs per-type turn detection** — the Claude Stop hook for each of the
   four agents.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'academic-coauthor' is up with 4 agent(s)
:: attach with:  tmux attach -t <literature-session>
:: you can use the UI with:  agentainer serve -c examples/academic-coauthor.yaml
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). It **binds `127.0.0.1` by default** — keep it
loopback-only unless you deliberately add `--host` and a `--token` for remote
access (see [`remote-access.md`](./remote-access.md)). The control plane can type
into agents running `--dangerously-skip-permissions`, so treat the bind seriously.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole pipeline route mail with no API keys — the mechanics are identical.

---

## 4. Drive a question

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the lead's paper and the citation checker's
summary as mail (rather than have them held), turn yourself available first:

```bash
./agentainer user available -c examples/academic-coauthor.yaml
```

This rewrites the `user` contact card in each sender's `outbox/user/about.md` to
`Status: available`, so they see you're reachable. (While away, mail to you is
*held* and the sender gets a `system` ack — nothing bounces.)

Now send the question into the swarm, addressed to the literature lead:

```bash
./agentainer send --to literature "Draft a survey on retrieval-augmented generation for code."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for `literature`, then — because the
inbox was empty — **released into `inbox/`** and the lead is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§5), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **literature receives the question.** It surveys prior work into
   `RELATED-WORK.md`, drafts a scope note and outline, and writes the proposed
   approach into `outbox/methodologist/`. On stop, that routes to the
   methodologist.
2. **methodologist reviews rigor.** It reads the approach, writes a rigor memo
   (threats to validity + fixes) into `outbox/literature/`. On stop, that routes
   back to the lead.
3. **literature briefs the writer.** With the method settled, it writes the
   outline + survey + method into `outbox/writer/`. On stop, that routes to the
   writer.
4. **writer drafts a section.** It writes prose into `DRAFT.md`, marks gaps with
   `[CITE: ...]`, and returns the section via `outbox/literature/`.
5. **literature sends the draft to citation.** It forwards the section into
   `outbox/citation/`. On stop, that routes to the checker.
6. **citation verifies.** It checks each claim and reference, writes a fix list to
   `outbox/literature/` **and** a plain-English confirmation summary to
   `outbox/user/`. On stop, the lead gets the fixes and *you* get the verdict.
7. **literature finalizes.** After reconciling fixes, it writes the finished paper
   into `outbox/user/`. On stop, it's delivered to your `user` mailbox (you'll see
   it with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a question, the agents just sit in standby (that's the point
> of the standby prompt). The pipeline only moves when real mail arrives — this
> swarm has no periodic pings to self-start it.

---

## 5. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/academic-coauthor.yaml
```

```
swarm: academic-coauthor   root: ./academic-coauthor-workspace
  literature (claude) up idle queue=0 unread=0 talks=methodologist, writer, citation, user
  methodologist (claude) up idle queue=0 unread=1 talks=literature
  writer (claude) up idle queue=0 unread=0 talks=literature
  citation (claude) up idle queue=0 unread=0 talks=literature, user
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/academic-coauthor.yaml            # whole swarm, last 20
./agentainer logs -c examples/academic-coauthor.yaml -f          # follow live
./agentainer logs literature -c examples/academic-coauthor.yaml  # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox methodologist -c examples/academic-coauthor.yaml
```

Prints the one released message (headers + body), or
`methodologist: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue writer -c examples/academic-coauthor.yaml
```

**Your own mailbox** — the paper and the citation verdict land here:

```bash
./agentainer user inbox -c examples/academic-coauthor.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach literature -c examples/academic-coauthor.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 6. Resume after a stop

Tear the swarm down when you're done:

```bash
./agentainer down -c examples/academic-coauthor.yaml
```

Bring it back later and **conversations resume by default**:

```bash
./agentainer up -c examples/academic-coauthor.yaml     # resume is the default
```

On `up`, Agentainer reads
`academic-coauthor-workspace/.agentainer/sessions.yaml` (written as each agent
finished its first turn) and reattaches the recorded conversations via each type's
native resume: `claude --resume <id>` for every agent here, since all four are
`type: claude`. A long paper is exactly the case where resume matters — the lead's
survey, the settled methodology, and the draft-so-far all survive a `down`/`up`. A
resumed agent is *not* re-sent the standby prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/academic-coauthor.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md) and
the reboot walkthrough in
[`use-cases/resume-after-reboot.md`](./resume-after-reboot.md).

---

## 7. Tips & footguns

- **Keep authorship and integrity human-owned.** This swarm drafts and checks; it
  does not vouch. The citation checker's summary tells you what it *could and could
  not confirm* — that's a prompt to verify, not a green light. A person reads every
  cited source and signs off. LLMs invent plausible references; that's the single
  biggest risk here, which is why `citation` treats a reference it can't confirm as
  the top-priority find.

- **Two agents talk to `user` — on purpose.** Only `literature` and `citation`
  list `user` in `can_talk_to`, and they carry different payloads (the paper vs.
  the integrity verdict). If the methodologist or writer tries to mail `user`
  directly, the orchestrator bounces it (ACL) and drops a `system` note in the
  sender's inbox explaining who it *can* message — the model self-corrects in-band.

- **Gate drafting on the rigor memo.** The value of the pipeline is that the writer
  starts *after* the methodologist has flagged validity threats. The lead's role
  says to hold drafting until the method is settled; if you watch the log and see a
  draft go out before the methodologist has replied, nudge the lead (attach, or
  send it a `user` note) to sequence it correctly.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually fires**
  — a `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't
  launch Claude) means completion never triggers and the agent pins "busy" forever.
  `status` showing an agent `busy` for a long time with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "looks good!/thanks!" loops between the lead and a reviewer.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/academic-coauthor.yaml
  ./agentainer remove-session -c examples/academic-coauthor.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

- **Availability shapes the ending.** If `user` is **away** when the paper is
  ready, your final copy and the citation verdict are *held* (with a `system` ack
  to the sender) rather than lost — read them later with `agentainer user inbox` or
  flip yourself available and they're delivered.

---

## 8. Customize it

- **Add a `latex`/`figures` agent.** Give the writer a downstream typesetter:
  ```yaml
  - name: latex
    type: codex
    can_talk_to: [literature]
    command: "codex --yolo"
    role: |
      You are the TYPESETTER. Turn the approved DRAFT.md into a compilable LaTeX
      paper (main.tex + refs.bib) and build the figures/tables. Fix only markup
      and layout -- never alter a claim or a citation. Report build errors and the
      final PDF path to the literature lead.
  ```
  Then add `latex` to the lead's `can_talk_to` so it can route the approved draft.
  A `codex` agent uses a `notify` hook for turn detection (installed at `up`) — no
  extra config needed. Mixing agent CLIs like this is the
  [multi-LLM swarm](../use-cases/multi-llm-swarm.md) pattern.

- **Swap models per role.** Nothing forces all-claude. Point `type`/`command` at
  whichever CLI you prefer per seat — e.g. a stronger model for the methodologist
  and citation checker (the correctness-critical seats), a faster one for the
  writer. Keep `type` and `command` in agreement or the turn signal never fires
  (see the footgun above). See
  [`multi-llm-swarm.md`](../use-cases/multi-llm-swarm.md).

- **Tune the ACL.** The default routes everything through the lead — the
  [delegation-pipeline](../use-cases/delegation-pipeline.md) pattern. If you want
  the methodologist and citation checker to compare notes directly (e.g. "does the
  claimed effect size survive this stat?"), add each other to their `can_talk_to`.
  Loosening the graph trades sequencing for speed; keep the `user`-facing surface
  small so a person still owns the final call.

- **Add a periodic ping.** For a long survey, let the lead poke a slow writer:
  ```yaml
  - name: literature
    pings:
      - cron: "*/10 * * * *"    # every 10 minutes
        message: "Any section ready for review? If blocked, say what you need."
  ```

- **Share a workspace (advanced).** If the writer and typesetter must edit the
  same files, give them the same `workdir` — Agentainer namespaces each agent's
  mailbox folders (`<name>-inbox/`, …) so they don't collide, and warns you about
  the shared checkout. See [`custom-workspace.md`](./custom-workspace.md).

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and the read/write verbs.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — how `down`/`up` restores
  a long paper's context.
- [`use-cases/delegation-pipeline.md`](./delegation-pipeline.md) — the hub-and-spoke
  routing this swarm is built on.
- [`use-cases/multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing agent CLIs per
  role (e.g. adding a `codex` typesetter).
- `examples/quickstart.yaml` — the key-free mock-agent starter.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

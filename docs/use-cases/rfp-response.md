# Use case: the RFP / grant proposal writing swarm

A concrete, end-to-end walkthrough of the shipped `examples/rfp-response.yaml`
swarm — a five-agent bid team that turns an incoming RFP, grant call, or
tender into a single compliant, submission-ready proposal. A **proposal manager**
(`pm`) owns the process and is the only agent that talks to you; a **parser**
extracts the hard requirements and how the bid will be scored; two **section
writers** draft the technical and cost portions; an **editor** merges them into
one consistent, compliant document. The whole loop runs through Agentainer's
file-based mail model.

Everything below is based on the actual contents of `examples/rfp-response.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you supply the
coding-CLI commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

- **Business development / sales teams** responding to customer RFPs — software
  builds, professional services, agency scopes. The swarm drafts the approach,
  staffing and pricing so a human can review instead of starting from a blank
  page.
- **Grants and proposal writers** answering federal, foundation, or
  institutional calls — where compliance with formatting, eligibility and
  evaluation criteria is as important as the content itself.
- **Agencies and consultancies** that field many similar bids and want a
  repeatable, auditable drafting pipeline they can point at a new solicitation
  each time.

The win is *separation of concerns under one compliance roof*: the writers
produce prose, the parser tells everyone exactly what "winning" means, and the
editor is the single place where voice and compliance are enforced. You, the
human, touch the bid exactly twice — once to drop in the solicitation, once to
review and submit.

---

## 2. The topology

```
            the RFP / grant call
  user ------------------------->  pm  ------------->  parser
       (final proposal) <-------    ^  \        (requirements + criteria)
                                     |  |   \-- briefs both writers --\
                                     |  |                             |
                                     |  |                             v
                                     |  +--- editor <-- writer_tech, writer_cost
                                     |
                                     +-- pm delivers finished proposal to user
```

Five agents, one funnel:

1. **`user` → `pm`** — you paste the full solicitation.
2. **`pm` → `parser`** — the pm hands the whole RFP to the parser and waits for
   its requirements brief.
3. **`pm` → `writer_tech` / `writer_cost`** — using the brief, the pm assigns the
   technical sections to one writer and the budget/cost sections to the other,
   with the exact section list, limits and evaluation weights that apply.
4. **`writer_tech` / `writer_cost` → `editor`** — the two drafts converge on the
   editor (writers do **not** talk to each other).
5. **`editor` → `pm`** — the editor merges the drafts into one document, runs the
   compliance matrix, and returns the merged proposal + compliance report.
6. **`pm` → `user`** — the pm reviews against the parser's requirements and
   delivers the finished bid to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. A writer that tries to mail `user` directly is bounced back
as a `system` message and filed in `failed/` (see §7).

---

## 3. The config, explained

Here is `examples/rfp-response.yaml` in full:

```yaml
# 📄 RFP / grant proposal writing swarm -- a bid team that turns an incoming
# solicitation into a compliant, submission-ready proposal.
swarm:
  name: rfp
  root: ./rfp-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: pm
    type: claude
    can_talk_to: [parser, writer_tech, writer_cost, editor, user]
    command: "claude --dangerously-skip-permissions"
    role: "You are the PROPOSAL MANAGER, the single owner of this bid..."
  - name: parser
    type: claude
    can_talk_to: [pm]
    command: "claude --dangerously-skip-permissions"
    role: "You are the REQUIREMENTS ANALYST. Extract requirements + scoring..."
  - name: writer_tech
    type: claude
    can_talk_to: [pm, editor]
    command: "claude --dangerously-skip-permissions"
    role: "You are the TECHNICAL WRITER. Draft the technical/approach sections..."
  - name: writer_cost
    type: claude
    can_talk_to: [pm, editor]
    command: "claude --dangerously-skip-permissions"
    role: "You are the COST & BUDGET WRITER. Draft budget/pricing/staffing..."
  - name: editor
    type: claude
    can_talk_to: [pm]
    command: "claude --dangerously-skip-permissions"
    role: "You are the EDITOR and COMPLIANCE LEAD. Merge drafts, enforce compliance..."
```

Field by field:

### `swarm`
- **`name: rfp`** — the swarm's name (shows up in `status`, logs, sessions).
- **`root: ./rfp-workspace`** — the parent directory for the agents' working
  directories and mailboxes. Each agent gets `rfp-workspace/<name>/` as its
  workdir (created on `up`), and its mailbox folders live alongside. Orchestrator
  state goes under `rfp-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the key-free default. **But note:** for `claude` agents,
  whose CLI supports a completion **hook**, `capture: none` is a footgun — so the
  config loader *upgrades* it back to `hook` and prints a warning at `up`. Net
  effect here: every agent uses its Stop hook for turn detection. (When you swap
  in `codex`/`gemini`/`hermes` agents, see the capture guidance in §8.)
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `pm` (type: `claude`)
- **`can_talk_to: [parser, writer_tech, writer_cost, editor, user]`** — the hub
  and the **only agent that can talk to `user`**. It runs the process: solicit →
  parse → brief writers → collect → merge → deliver. Keeping the human-facing
  surface to one agent is deliberate (see §7).
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command (substitute your own, e.g. a shell alias). Treat command strings as
  sensitive; they may embed keys.
- **`role`** — the standing identity: process owner, not prose author. On `up`
  this becomes the first prompt, wrapped in a **standby notice** so the pm waits
  for the solicitation instead of mailing peers proactively.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `parser` (type: `claude`)
- **`can_talk_to: [pm]`** — it only reports the requirements brief back up to the
  pm. It never sees the writers' drafts and never reaches the user.
- **`role`** — extract mandatory requirements, the evaluation criteria and their
  weights, and a compliance matrix mapping each requirement to the section that
  must answer it. This brief is the contract the writers build against.

### `writer_tech` / `writer_cost` (type: `claude`)
- **`can_talk_to: [pm, editor]`** — each writer takes its assignment from the pm
  and delivers its draft to the **editor** (not to the other writer, not to the
  user). It escalates scope/missing-info questions to the pm.
- **`role`** — `writer_tech` drafts the technical/approach/management sections to
  the scoring weights; `writer_cost` drafts the budget/pricing/staffing so the
  numbers are internally consistent and compliant. Both mark missing facts as
  `[TODO: ...]` rather than inventing them.

### `editor` (type: `claude`)
- **`can_talk_to: [pm]`** — it only reports the merged proposal + compliance
  report back up to the pm. It is the convergence point for both writers.
- **`role`** — merge the two drafts into one single-voice document, then run the
  parser's compliance matrix and confirm every mandatory item is addressed.

### What's *not* in this config
- **No `pings`.** The bid moves only when real mail arrives —
  purely event-driven. (If a writer stalled, you could add
  a `pings` cron rule to nudge it.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — the finished proposal is *held* until you flip yourself available
  (see §5).
- **One `type` (`claude`) throughout.** Every agent is `claude` so the swarm
  comes up and routes mail with a single CLI. Swap any agent to `codex`/`gemini`/
  `hermes` (and match its `command`) to mix capabilities — see §8.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/rfp-response.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for each claude agent).
2. Creates the runtime dirs (`rfp-workspace/.agentainer/…`: log, queue, run,
   sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: the pm gets
   `outbox/parser/`, `outbox/writer_tech/`, `outbox/writer_cost/`,
   `outbox/editor/`, `outbox/user/`; each writer gets `outbox/pm/` and
   `outbox/editor/`; the parser and editor get `outbox/pm/`.
4. **Installs per-type turn detection** — the Claude Stop hook for all five
   agents.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'rfp' is up with 5 agent(s)
:: attach with:  tmux attach -t <pm-session>
:: you can use the UI with:  agentainer serve -c examples/rfp-response.yaml --port 8080
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). By default the UI binds **`127.0.0.1`** only —
never `0.0.0.0` — so it stays local unless you opt into a remote bind with a
token. See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole bid route mail with no API keys — the mechanics are identical.

---

## 5. Drive a proposal

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the finished proposal as mail (rather than
have it held), turn yourself available first:

```bash
./agentainer user available -c examples/rfp-response.yaml
```

This rewrites the `user` contact card in the pm's `outbox/user/about.md` to
`Status: available`, so the pm sees you're reachable. (While away, mail to you is
*held* and the sender gets a `system` ack — nothing bounces.)

Now send the solicitation into the swarm, addressed to the pm:

```bash
./agentainer send --to pm -c examples/rfp-response.yaml --file the-rfp.txt
# or, for shorter calls, inline:
./agentainer send -c examples/rfp-response.yaml --to pm "$(cat the-rfp.txt)"
```

Under the hood (`cmd_send` → `mail.send_as_user`): the text is stamped with a
`From: user` header and a fresh id, enqueued for the pm, then — because the
inbox was empty — **released into `inbox/`** and the pm is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the bid advance one turn at a time. Each arrow
is a `stop → sweep → route → release → nudge` cycle:

1. **pm receives the RFP.** It reads `inbox/`, sends the whole solicitation to the
   parser, and finishes its turn. On stop, that routes to the parser and the
   parser is nudged.
2. **parser extracts requirements.** It produces `REQUIREMENTS.md` and reports the
   brief back to the pm. On stop, that routes to the pm.
3. **pm briefs the writers.** Using the brief, it sends an assignment to
   `writer_tech` and a separate one to `writer_cost`. On stop, both route out and
   the writers are nudged.
4. **writers draft.** Each writes its section set and sends the draft to the
   editor. On stop, both route to the editor.
5. **editor merges + checks compliance.** It writes the merged proposal and a
   compliance report into `outbox/pm/`. On stop, that routes to the pm.
6. **pm finalizes.** It reviews against the parser's requirements and writes the
   finished proposal into `outbox/user/`. On stop, that's delivered to your
   `user` mailbox (visible with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send an RFP, the agents just sit in standby (that's the point of
> the standby prompt). The pipeline only moves when the solicitation arrives.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/rfp-response.yaml
```

```
swarm: rfp   root: ./rfp-workspace
  pm          (claude) up idle queue=0 unread=0 talks=parser, writer_tech, writer_cost, editor, user
  parser      (claude) up idle queue=0 unread=1 talks=pm
  writer_tech (claude) up idle queue=0 unread=0 talks=pm, editor
  writer_cost (claude) up idle queue=0 unread=0 talks=pm, editor
  editor      (claude) up idle queue=0 unread=0 talks=pm
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct the bid):

```bash
./agentainer logs -c examples/rfp-response.yaml           # whole swarm, last 20
./agentainer logs -c examples/rfp-response.yaml -f        # follow live
./agentainer logs editor -c examples/rfp-response.yaml    # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox pm -c examples/rfp-response.yaml
```

Prints the one released message (headers + body), or `pm: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue pm -c examples/rfp-response.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach editor -c examples/rfp-response.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom
— handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the draft

The first proposal the pm delivers is the *starting point*, not the final word.
Because the whole conversation is preserved across turns, iterating is just more
mail:

```bash
# Tell the pm what to change; it re-runs the pipeline and redelivers.
./agentainer send --to pm -c examples/rfp-response.yaml \
  "Cut the technical section by 2 pages, emphasize prior results, and tighten the budget to $250k."
```

The pm will re-brief the writers and the editor, and return a revised draft. If
you only want to fix wording, you can bypass the writers and ask the editor
directly (the pm still relays it):

```bash
./agentainer send --to pm -c examples/rfp-response.yaml \
  "Ask the editor to unify the section headings under the solicitation's part numbers."
```

If a writer needs a fact only you have (a real metric, a certification, a rate),
answer its `[TODO]` by mailing the pm and naming the writer to brief:

```bash
./agentainer send --to pm -c examples/rfp-response.yaml \
  "For writer_cost: the loaded labor rate is $180/hr; tell the editor the totals are now locked."
```

Because `up` resumes conversations by default (§8), even a multi-round revision
keeps each agent's accumulating context — the editor remembers the last merge,
the writers remember the brief.

---

## 8. Customize

### Add a subject-matter expert
A domain specialist that only the writer_tech consults keeps the funnel intact
while adding depth:

```yaml
  - name: subject_matter_expert
    type: claude
    can_talk_to: [writer_tech]
    command: "claude --dangerously-skip-permissions"
    role: "You are the SME. Answer writer_tech's technical questions from real domain knowledge; do not write proposal prose."
```
and add `subject_matter_expert` to `writer_tech`'s `can_talk_to`. The SME can only
reach the technical writer, preserving the single-voice edit.

### Swap in different models
The swarm ships `claude` throughout, but you can mix capabilities. Remember the
**`type` ↔ `command` must match** rule: a `codex` agent must launch `codex`,
a `gemini`/`hermes` agent must launch that CLI, or turn-completion never fires
and the agent pins "busy" forever. Also set the right `capture`:

```yaml
  - name: writer_cost
    type: codex
    capture: hook                 # codex uses its notify hook
    can_talk_to: [pm, editor]
    command: "codex --yolo"
    role: "You are the COST & BUDGET WRITER..."
  - name: writer_tech
    type: gemini
    capture: pane                 # gemini has no hook; poll the pane
    can_talk_to: [pm, editor]
    command: "gemini --yolo"
    role: "You are the TECHNICAL WRITER..."
```

`claude`/`codex` → hook; `gemini`/`hermes` → pane; `none` only on agents you
intentionally leave uncaptured (the loader auto-upgrades `none` on hook types).

### Tune the ACL
The funnel is the safe default; widen it only if you accept the risk. For example,
to let the editor ask a writer a direct clarifying question, add the peer to the
editor's `can_talk_to` and the writer's `can_talk_to`. But never put `user` on a
writer's list — keep the human-facing surface on the pm (a writer that mails
`user` directly is bounced by ACL and told who it may message).

### Keep the control plane local
The UI (`agentainer serve`) binds **`127.0.0.1`** by default. To expose it
remotely you must opt in with `--host 0.0.0.0` **and** `--token <generated>`;
never leave it open without a token, because the UI can type into agents that may
run `--dangerously-skip-permissions`. Prefer the loopback bind.

---

## 9. Resume after a stop

Tear the swarm down when you're done:

```bash
./agentainer down -c examples/rfp-response.yaml
```

Bring it back later and **conversations resume by default**:

```bash
./agentainer up -c examples/rfp-response.yaml     # resume is the default
```

On `up`, Agentainer reads `rfp-workspace/.agentainer/sessions.yaml` (written as
each agent finished its first turn) and reattaches the recorded conversations via
`claude --resume <id>` for each claude agent. A resumed agent is *not* re-sent the
standby prompt (its prior context — the brief, the drafts, the prior merge — is
restored). This is what makes multi-round revision feel continuous.

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/rfp-response.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md) and
the reboot walkthrough in
[`use-cases/resume-after-reboot.md`](./resume-after-reboot.md).

---

## 10. Tips & footguns

- **Keep the pm the only `user`-facing agent.** Only the pm lists `user` in
  `can_talk_to`. That gives you a single point of contact and a clean funnel: raw
  drafts always pass through the editor before they reach you. If a writer tries
  to mail `user` directly, the orchestrator bounces it (ACL) and drops a `system`
  note in the writer's inbox explaining who it *can* message — the model
  self-corrects in-band. This is the same pattern as
  [`use-cases/delegation-pipeline.md`](./delegation-pipeline.md).

- **The parser's brief is the contract.** If the writers or editor drift from
  `REQUIREMENTS.md`, the pm is the one who catches it on final review. Spend the
  effort to make the parser's output precise — weights, section lists, limits —
  because the whole downstream quality rides on it.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch means completion never triggers and the
  agent pins "busy" forever. `status` showing an agent `busy` for a long time
  with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **`[TODO]` markers are the honesty signal.** Writers are instructed to mark
  unknown facts rather than invent them. Grep the delivered proposal for
  `[TODO:` before you submit — an unclosed one is a gap you must fill yourself.

- **Force-idle if a pane-captured agent's turn never registers.** If you swap in
  a `gemini`/`hermes` writer and its capture never fires, nudge the state along:
  ```bash
  ./agentainer idle writer_tech -c examples/rfp-response.yaml
  ```

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/rfp-response.yaml
  ./agentainer remove-session -c examples/rfp-response.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

- **Availability shapes the ending.** If `user` is **away** when the pm finishes,
  your proposal is *held* (with a `system` "the user is away" ack to the pm)
  rather than lost — read it later with `agentainer user inbox` or flip yourself
  available and it's delivered.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resume conversations by default.
- [`use-cases/delegation-pipeline.md`](./delegation-pipeline.md) — the hub-and-spoke pattern this bid reuses.
- [`use-cases/multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing claude/codex/gemini/hermes agents.
- `examples/rfp-response.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

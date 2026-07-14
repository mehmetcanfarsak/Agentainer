# Use case: Threat modeler

A concrete, end-to-end walkthrough of the shipped
`examples/threat-modeler.yaml` swarm — a four-agent hub-and-spoke that turns an
architecture spec or data-flow description into a ranked threat-model backlog.
A **modeler** hub takes your spec, fans it out to a **STRIDE analyst** and an
**abuse-case writer**, then a **prioritizer** ranks every collected threat by
likelihood × impact into a remediation backlog. The modeler delivers the
consolidated model back to you.

Everything below is based on the actual contents of
`examples/threat-modeler.yaml` and the shipped CLI (`lib/cli.py`) and mailroom
(`lib/mail.py`). No API keys are needed to understand the mechanics; to run it
*for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in [`mail-model.md`](../mail-model.md). The one-line
> version: an agent **reads a file** to receive mail and **writes a file** to send
> it; the orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Security engineers, architects, and technical leads who want a structured way to
go from a loose architecture description to a concrete, ranked list of threats
and abuse cases — without doing every angle themselves. The swarm encodes the
discipline that makes a threat model useful: a single owner of the consolidated
model, two independent analysis lenses (STRIDE + abuse cases) that never
overlap, and one place that ranks everything before it reaches you.

It is deliberately a **hub-and-spoke**, not a free-for-all: the STRIDE analyst,
the abuse-case writer, and the prioritizer each analyze the spec from a different
angle and report *only* to the modeler, so the consolidated threat model has
exactly one authority and no two spokes analyze the same angle twice. Swapping in
a real `validator` agent (see §7) is a one-line config change.

---

## 2. The topology

```
          user
            |
         modeler                 (the hub: talks to stride-analyst, abuse-case-writer, prioritizer, user)
          /      |      \
stride-analyst  abuse-case-writer  prioritizer
   (each spoke talks ONLY to modeler — never to each other)
```

Four agents, one directed flow:

1. **`user` → `modeler`** — you send the architecture spec (components, trust
   boundaries, auth model, external integrations).
2. **`modeler` → `stride-analyst`** (and, in parallel, **`modeler` →
   `abuse-case-writer`**) — the modeler restates the full spec to both analysts.
3. **`stride-analyst` → `modeler`** — returns a per-component STRIDE threat list
   with mitigations. **`abuse-case-writer` → `modeler`** — returns concrete
   attacker stories.
4. **`modeler` → `prioritizer`** (with the combined material) — rank everything by
   likelihood × impact into a tiered backlog.
5. **`prioritizer` → `modeler`** — returns the ranked remediation backlog.
6. **`modeler` → `user`** — once both analyses are in and ranked, the modeler
   assembles the final threat model (STRIDE table + abuse cases + ranked backlog)
   and hands it to you.

The routing above is *enforced* by each agent's `can_talk_to` list. A spoke can
only deliver to `modeler`; the modeler is the **only** agent that can reach
`user`. Anything addressed outside an agent's list is bounced back as a `system`
message and filed in `failed/` (see §7). Notably, `stride-analyst`,
`abuse-case-writer`, and `prioritizer` **never** talk to each other or to `user`
directly — only the modeler does.

---

## 3. The config, explained

Here is `examples/threat-modeler.yaml` (roles trimmed to their key instructions;
the full natural-language role text lives in the file):

```yaml
swarm:
  name: threat-modeler
  root: ./threat-modeler-workspace

defaults:
  capture: none              # mock agents don't fire a turn-completion hook
  can_talk_to: []           # tightened per agent below

agents:
  - name: modeler
    type: claude
    can_talk_to: [stride-analyst, abuse-case-writer, prioritizer, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the THREAT MODELING COORDINATOR (the modeler). ... You do NOT
      analyze threats yourself -- you orchestrate. ... Run it: (1) acknowledge the
      architecture to the human ... (2) send the full architecture spec to
      stride-analyst and, in parallel, to abuse-case-writer; (3) collect both
      analyses and forward the combined material to prioritizer ... (4) assemble
      the final threat model ... and send it to the user (outbox/user/). ...
      You may message: stride-analyst, abuse-case-writer, prioritizer, user.

  - name: stride-analyst
    type: codex
    can_talk_to: [modeler]
    command: "codex --yolo"
    role: |
      You are the STRIDE ANALYST. ... walk EVERY component and trust boundary
      through the six STRIDE categories (Spoofing, Tampering, Repudiation,
      Information disclosure, Denial of service, Elevation of privilege). ...
      component | STRIDE category | threat description | likelihood | impact |
      mitigation. ... Write your full STRIDE threat list back to outbox/modeler/.
      ... You may message: modeler.

  - name: abuse-case-writer
    type: gemini
    can_talk_to: [modeler]
    command: "gemini --yolo"
    role: |
      You are the ABUSE-CASE WRITER. ... write CONCRETE attacker stories --
      abuse cases an adversarial user (or malicious insider, or compromised
      dependency) could exploit. ... 8-15 distinct, realistic abuse cases ...
      Write your abuse-case set back to outbox/modeler/. ... You may message: modeler.

  - name: prioritizer
    type: claude
    can_talk_to: [modeler]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the PRIORITIZER. ... rank EVERY threat into a remediation backlog.
      Score each item by likelihood x impact on a simple 1-5 scale for each axis
      (max 25), sort descending, and group into tiers: Must-fix-now, Near-term,
      and Backlog. ... Write the tiered, ranked backlog back to outbox/modeler/.
      ... You may message: modeler.
```

Field by field:

### `swarm`
- **`name: threat-modeler`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./threat-modeler-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent gets its own private workdir
  (`threat-modeler-workspace/modeler`, `.../stride-analyst`, `.../abuse-case-writer`,
  `.../prioritizer`). Orchestrator state goes under
  `threat-modeler-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.
- **`capture: none`** — the shipped example disables turn-completion detection at
  the default level. The in-file comment explains the intent: *"mock agents don't
  fire a turn-completion hook."* This makes the key-free mock-bash-loop demo path
  self-contained (see the footnote in §10 for what changes when you run with real
  agents).

### `modeler` (type: `claude`)
- **`can_talk_to: [stride-analyst, abuse-case-writer, prioritizer, user]`** — the
  modeler is the hub: it delegates to the three specialists and is the **only
  agent that can talk to `user`**. That last part matters — keep the human-facing
  surface to a single agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the modeler waits for your spec instead of
  proactively mailing peers.
- **Turn detection (natural mode):** `claude` → a **Stop hook** (installed
  automatically at `up`).

### `stride-analyst` (type: `codex`)
- **`can_talk_to: [modeler]`** — the STRIDE analyst only reports back to the
  modeler. It deliberately cannot reach the abuse-case writer, the prioritizer,
  or the `user`; each spoke owns a distinct analysis angle.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "walk every component and trust boundary through the six STRIDE
  categories; produce `component | category | threat | likelihood | impact |
  mitigation`; write the list back to `outbox/modeler/`."
- **Turn detection (natural mode):** `codex` → a `notify` program (its hook),
  installed at `up`.

### `abuse-case-writer` (type: `gemini`)
- **`can_talk_to: [modeler]`** — the abuse-case writer only reports back to the
  modeler; it cannot reach the other spokes or `user`.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **`role`** — "write 8–15 concrete attacker stories (actor, entry point, steps,
  gain, why controls fail), covering technical and social/process angles; write
  them back to `outbox/modeler/`."
- **Turn detection (natural mode):** `gemini` → **pane polling** (`capture: pane`),
  since Gemini has no completion hook.

### `prioritizer` (type: `claude`)
- **`can_talk_to: [modeler]`** — the prioritizer only reports back to the modeler;
  it receives the combined STRIDE + abuse-case material and returns a ranked
  backlog. It cannot reach the spokes or `user` directly.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **`role`** — "score every incoming threat by likelihood × impact (1–5 each, max
  25), sort descending, group into Must-fix-now / Near-term / Backlog tiers; merge
  overlaps but keep coverage; write the ranked backlog back to `outbox/modeler/`."
- **Turn detection (natural mode):** `claude` → Stop hook.

### ACL enforcement (how the spokes are kept apart)

`can_talk_to` is the cooperative access control the orchestrator enforces on
every send. Each agent's `outbox/` is *seeded* with a folder **only** for the
names on its list — the model literally cannot `write` a file into a peer it
isn't allowed to address, because no `outbox/<peer>/` folder exists for it. If an
agent did try to deliver outside its ACL (e.g. a spoke writing straight into
another spoke's `inbox/`), the orchestrator bounces it and drops a `system`
message in the sender's inbox explaining who it *can* message — the model
self-corrects in-band. This is cooperative, not OS isolation (see
[`mail-model.md`](../mail-model.md)); it is not a security boundary. Keeping
`user` reachable from only the modeler is what gives you a single reviewed funnel
for the finished threat model.

### What's *not* in this config
- **No shared workdirs.** Unlike the data-pipeline swarm, every agent here gets
  its own private directory under `root`, so there is **no mailbox namespacing** —
  each agent's five folders (`inbox/ outbox/ read/ sent/ failed/`) are created
  unprefixed. (See [`custom-workspace.md`](./custom-workspace.md) for how workdirs
  and computed mailbox paths are resolved when you *do* share one.)
- **No `pings`.** The swarm is purely event-driven off real mail — it only moves
  when you send a spec. (Add a ping to the modeler if you want a "stale threat
  model" nag.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/threat-modeler.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings.
2. Creates the runtime dirs (`threat-modeler-workspace/.agentainer/…`: log, queue,
   run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/about.md`
   contact card *is* the ACL made visible: the modeler gets
   `outbox/stride-analyst/`, `outbox/abuse-case-writer/`, `outbox/prioritizer/`,
   `outbox/user/`; each spoke gets just `outbox/modeler/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `modeler` and
   `prioritizer`, the Codex `notify` hook for `stride-analyst`, and pane polling
   for `abuse-case-writer` (when `capture` is left at its per-type default — see
   the §3 note on the shipped `capture: none`).
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'threat-modeler' is up with 4 agent(s)
:: attach with:  tmux attach -t <modeler-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/threat-modeler.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents, so it must **never** be exposed on `0.0.0.0` without a token.
See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole threat-model assembly route mail with no API keys — the mechanics are
> identical.

---

## 5. Drive a spec

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the modeler's finished threat model as mail
(rather than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/threat-modeler.yaml
```

This rewrites the `user` contact card in the modeler's `outbox/user/about.md`
to `Status: available`, so the modeler sees you're reachable. (While away, mail to
you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the architecture spec into the swarm, addressed to the modeler:

```bash
./agentainer send --to modeler -c examples/threat-modeler.yaml \
  "Architecture: a 3-tier web app. Frontend SPA talks to a REST API (Node/Express) \
   backed by Postgres; auth via JWT in localStorage; payments via Stripe webhooks."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the modeler, then — because the
inbox was empty — **released into `inbox/`** and the modeler is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the model advance one turn at a time. Each arrow
is a `stop → sweep → route → release → nudge` cycle:

1. **modeler receives the spec.** It reads `inbox/`, acknowledges to you briefly,
   and writes the spec into both `outbox/stride-analyst/` and
   `outbox/abuse-case-writer/` (in parallel). On stop, both route to their spokes.
2. **stride-analyst walks STRIDE; abuse-case-writer writes stories.** Each reads
   its inbox, produces its analysis, and writes it back into `outbox/modeler/`. On
   stop, both route back to the modeler.
3. **modeler forwards the combined material to prioritizer.** It writes the
   merged STRIDE + abuse-case set into `outbox/prioritizer/`. On stop, that routes
   to the prioritizer.
4. **prioritizer ranks.** It scores every threat, tiers the backlog, and writes it
   back into `outbox/modeler/`. On stop, that routes to the modeler.
5. **modeler finalizes.** It de-duplicates overlapping findings, flags
   contradictions, assembles the final STRIDE table + abuse cases + ranked backlog,
   and writes it into `outbox/user/`. On stop, that's delivered to your `user`
   mailbox (visible with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a spec, the agents just sit in standby (that's the point of
> the standby prompt). The model only moves when real mail arrives — this swarm has
> no periodic pings to self-start it.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/threat-modeler.yaml
```

```
swarm: threat-modeler   root: ./threat-modeler-workspace
  modeler           (claude) up idle queue=0 unread=1 talks=stride-analyst, abuse-case-writer, prioritizer, user
  stride-analyst    (codex)  up idle queue=0 unread=0 talks=modeler
  abuse-case-writer (gemini) up idle queue=0 unread=0 talks=modeler
  prioritizer       (claude) up idle queue=0 unread=0 talks=modeler
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/threat-modeler.yaml            # whole swarm, last 20
./agentainer logs -c examples/threat-modeler.yaml -f          # follow live
./agentainer logs prioritizer -c examples/threat-modeler.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox modeler -c examples/threat-modeler.yaml
```

Prints the one released message (headers + body), or `modeler: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue modeler -c examples/threat-modeler.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach stride-analyst -c examples/threat-modeler.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or (more directly)
by sending notes into an agent's inbox.

- **Send a clarification to the modeler.** Realized you forgot a trust boundary?
  `./agentainer send --to modeler -c examples/threat-modeler.yaml "There's also a
  Redis cache between API and Postgres holding session tokens — re-fan to both
  analysts."` The modeler relays the change down the chain.
- **Ask the prioritizer for the tiering rationale.** `./agentainer send --to modeler
  ... "Have the prioritizer attach the likelihood/impact scores behind the
  Must-fix-now tier."` — the modeler forwards it.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/threat-modeler.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/threat-modeler.yaml     # resume is the default
```

On `up`, Agentainer reads `threat-modeler-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for `modeler`
and `prioritizer`, `codex resume <id>` for `stride-analyst`, and Gemini's resume
for `abuse-case-writer`. A resumed agent is *not* re-sent the standby prompt (its
prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/threat-modeler.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a `validator` agent
Once the backlog is produced, you may want someone who sanity-checks coverage
(every component got a STRIDE pass, every abuse case has a matching ranked item).
Add a fifth agent that can read the modeler's consolidation and owns a coverage
report:

```yaml
  - name: validator
    type: claude
    can_talk_to: [modeler, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the THREAT-MODEL VALIDATOR. Once the modeler delivers a consolidated
      threat model, check that every named component has STRIDE coverage and every
      abuse case maps to a ranked backlog item. Report gaps to outbox/modeler/ and
      a coverage summary to outbox/user/. You never write threats yourself.
```

Then add `validator` to the modeler's `can_talk_to` so it can be briefed.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `stride-analyst: type: claude` (or `hermes`) to put STRIDE on a different model
  than the abuse cases.
- `prioritizer: type: codex` if you want ranking on Codex while the hub stays
  Claude.
- Remember: `gemini`/`hermes` need `capture: pane` (pane polling) since they have
  no completion hook. This swarm already exercises three families — see
  [`multi-llm-swarm.md`](./multi-llm-swarm.md) for mixing model families safely.

### Tune the ACL
- To let the `prioritizer` escalate straight to `user` (not only via the modeler),
  add `user` to its `can_talk_to`. Mind that this widens the human-facing surface;
  the doc's convention keeps the modeler the sole `user` contact.
- To make a spoke unreachable from anyone but the modeler (already the case here),
  leave its `can_talk_to: [modeler]` — that's the one-place-owns-the-model
  guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing.

---

## 10. Tips & footguns

- **Keep the modeler the only `user`-facing agent.** Only the modeler lists `user`
  in `can_talk_to`. That gives you a single funnel: raw STRIDE drafts and abuse
  cases always pass through consolidation and ranking before they reach you. If a
  spoke tries to mail `user` directly, the orchestrator bounces it (ACL) and drops
  a `system` note in its inbox explaining who it *can* message — the model
  self-corrects in-band.

- **The shipped `capture: none` default is for the mock path — override it for
  real agents.** `examples/threat-modeler.yaml` sets `defaults.capture: none` so
  the placeholders/mock-bash-loop demo doesn't depend on a hook firing. If you run
  the example *with real agents* (leaving `capture: none` in place), the
  orchestrator never learns a turn finished and won't auto-route the next hop —
  agents will look "stuck." For real runs, **remove `capture: none`** (so it
  defaults to `auto`, selecting per-type detection) or set per-agent `capture`
  explicitly: `claude` → Stop hook, `codex` → `notify` hook, `gemini` → `pane`.
  The §3 turn-detection notes describe each type's natural mode.

- **Mind the multi-provider spread.** This swarm mixes `claude`, `codex`, and
  `gemini` in one topology. Each needs its matching `command`; a `type`/`command`
  mismatch on any one agent pins it "busy" forever (the completion signal never
  arrives). `status` showing an agent `busy` for a long time with `unread` mail is
  the tell. See [`multi-llm-swarm.md`](./multi-llm-swarm.md).

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/threat-modeler.yaml
  ./agentainer remove-session -c examples/threat-modeler.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files in their workdirs or your
  config.

- **Availability shapes the ending.** If `user` is **away** when the modeler
  finishes, your threat model is *held* (with a `system` "the user is away" ack to
  the modeler) rather than lost — read it later with `agentainer user inbox` or
  flip yourself available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- [`custom-workspace.md`](./custom-workspace.md) — workdirs + mailbox namespacing.
- [`configuration.md`](../configuration.md) — every config field documented.
- [`cli-reference.md`](../cli-reference.md) — all subcommands (`up`, `send`, `status`, …).
- [`ui-guide.md`](../ui-guide.md) — the `serve` mail-app control plane.
- `examples/threat-modeler.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

# Use case: CI/CD builder

A concrete, end-to-end walkthrough of the shipped
`examples/ci-cd-builder.yaml` swarm — a four-agent factory that turns a human's
"here's a repo, here's the target platform" ask into a **hardened** CI/CD
pipeline, and then does the failure forensics no one enjoys. A **builder** hub
takes the request, a **pipeline-writer** authors the CI config for the named
platform, a **hardener** security-reviews it before merge (pinned versions,
secret handling, least-privilege tokens, supply-chain), and a **log-correlator**
that — when a build goes red — traces the failure across stages to find the
*true* root cause instead of the last line of the log. The builder delivers the
finished pipeline back to you.

Everything below is based on the actual contents of
`examples/ci-cd-builder.yaml` and the shipped CLI (`lib/cli.py`) and mailroom
(`lib/mail.py`). No API keys are needed to understand the mechanics; to run it
*for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Platform engineers, release engineers, and repo owners who want a disciplined
way to go from "we have a repo and a target CI platform" to a pipeline that is
**hardened before merge** — secrets never inlined, tokens scoped, dependencies
pinned and sourced from trusted registries — and that, when it breaks, comes
with a root-cause analysis instead of a wall of red text.

The swarm encodes two hard-won habits: (1) the security sign-off happens *before*
the pipeline is declared done, and (2) a failed build is investigated
forensically across stages rather than by reading the tail of the log. The agents
do the typing; the builder keeps a single reconciling authority.

It is deliberately a **hub-and-spoke**, not a free-for-all: the three specialists
never talk to each other. The pipeline text, the security verdict, and the
failure analysis all converge on the builder and are reconciled there. Only the
builder reaches the human. Swapping in a real `deployer` or `monitor` agent (see
§9) is a one-line config change.

---

## 2. The topology

```
          user
            |
         builder                 (the hub: talks to pipeline_writer, hardener, log_correlator, user)
          /    |    \
   pipeline_   hard-   log_
     writer    ener    correlator
               (each spoke reports ONLY to builder)
```

Four agents, one directed flow:

1. **`user` → `builder`** — you send the repo path + target platform (GitHub
   Actions, GitLab CI, Tekton, Jenkins, CircleCI, or "decide") and the required
   stages.
2. **`builder` → `pipeline_writer`** — the builder hands off the repo + platform +
   stage list and waits for the draft CI config.
3. **`builder` → `hardener`** (with the draft) — the builder forwards the draft
   for a security review and **requires its fixes**; it will not ship a pipeline
   the hardener has not cleared.
4. **`builder` ↔ `log_correlator`** — when the human (or a CI system) reports a
   failing build, the builder hands the raw stage logs to the correlator; the
   correlator reports the root cause, and the builder relays that as a fix brief
   back to `pipeline_writer`.
5. **`builder` → `user`** — once the config is written *and* hardened, the builder
   reconciles writer + hardener + correlator outputs into one pipeline file plus a
   short human-readable summary and sends that to you.

The routing above is *enforced* by each agent's `can_talk_to` list. The three
specialists **only** list `builder` — they can never mail each other or the
`user` directly. Anything off-list is bounced back as a `system` message and
filed in `failed/` (see §7). Only `builder` lists `user`.

---

## 3. The config, explained

Here is `examples/ci-cd-builder.yaml` in full (agent `role:` blocks abridged with
`…` — the shipped file has the complete prompts):

```yaml
swarm:
  name: ci-cd-builder
  root: ./ci-cd-builder-workspace

defaults:
  capture: none              # mock agents don't fire a turn-completion hook
  can_talk_to: []           # default ACL is "talk to no one"; opened per agent

agents:
  - name: builder
    type: claude
    can_talk_to: [pipeline_writer, hardener, log_correlator, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the BUILDER -- the human-facing hub of this CI/CD pipeline factory.
      … (orchestrate the team, require hardener sign-off before done, reconcile
          writer + hardener + correlator into ONE pipeline file + summary) …

  - name: pipeline_writer
    type: codex
    can_talk_to: [builder]
    command: "codex --yolo"
    role: |
      You are the PIPELINE-WRITER. Given the builder's brief (repo path, target
      platform, required stages -- test / lint / build / package / deploy),
      author the CI configuration file for that platform …
      Write it back to outbox/builder/ and nothing else.

  - name: hardener
    type: gemini
    can_talk_to: [builder]
    command: "gemini --yolo"
    role: |
      You are the HARDENER, the security reviewer of the CI pipeline. Given the
      pipeline config, RETURN a concrete prioritized list of mandatory fixes …
      (secrets handling, least-privilege tokens, pinned dependencies, supply
       chain, attack surface). Mark fork-exfiltration risks CRITICAL …
      Report the checklist back to outbox/builder/.

  - name: log_correlator
    type: codex
    can_talk_to: [builder]
    command: "codex --yolo"
    role: |
      You are the LOG-CORRELATOR. When a build FAILS, the builder hands you the
      raw logs from every stage plus which stage went red. Find the TRUE root
      cause, not the last error line …
      Send the root-cause report back to outbox/builder/.
    pings:
      - message: "Any builds currently red? If you have failure logs staged from
          the builder, run a root-cause correlation and report it. If all green,
          reply 'all clear'."
        cron: "*/15 * * * *"        # every 15 minutes
        when_busy: skip             # don't pile on if you're mid-analysis
```

Field by field:

### `swarm`
- **`name: ci-cd-builder`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./ci-cd-builder-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent gets its own private workdir
  (`ci-cd-builder-workspace/builder`, `…/pipeline_writer`, `…/hardener`,
  `…/log_correlator`) — there is **no shared workdir** here, so no mailbox
  namespacing is needed (unlike the data-pipeline example). Orchestrator state
  goes under `ci-cd-builder-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — this is the one quirk to know about. The shipped example
  sets `capture: none` so that **no turn-completion signal fires** — that's what
  lets the key-free mock demo (mock bash loops) come up and route mail with zero
  API keys. **For real runs with the real CLIs you should delete this line** (or
  set it to `auto`) so each type uses its natural turn detection (see the
  per-agent notes below). Leaving `capture: none` in place with live agents means
  the stop → sweep → route → nudge clock never advances on its own.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `builder` (type: `claude`)
- **`can_talk_to: [pipeline_writer, hardener, log_correlator, user]`** — the
  builder is the hub: it delegates to the three specialists and is the **only
  agent that can talk to `user`**. Keep the human-facing surface to a single
  agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the builder waits for your spec instead of proactively
  mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`,
  once the `capture: none` default is removed — see above).

### `pipeline_writer` (type: `codex`)
- **`can_talk_to: [builder]`** — it only reports back to the builder. It cannot
  reach the `hardener`, the `log_correlator`, or the `user`; the CI config has
  exactly one author before hardening.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "author the CI config for the named platform (`.github/workflows/
  ci.yml`, `.gitlab-ci.yml`, a Tekton TaskRun/Pipeline, …), covering checkout,
  pinned toolchain, test/lint/build/package, and a gated deploy-to-staging.
  Leave placeholders for secrets; never inline them."
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `hardener` (type: `gemini`)
- **`can_talk_to: [builder]`** — it only reports its security checklist back to
  the builder. It deliberately cannot reach the writer, the correlator, or the
  `user`.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **`role`** — "review the config for secrets, least-privilege, pinned
  dependencies, supply-chain, and attack surface; return a prioritized list of
  mandatory fixes, not a rewrite. Mark fork-exfiltration risks CRITICAL."
- **Turn detection:** `gemini` has **no completion hook**, so it needs **pane
  polling** (`capture: pane`). Because the example's `defaults` sets `capture:
  none`, remember to give `hardener` its own `capture: pane` when you run it for
  real, or its turns won't be detected.

### `log_correlator` (type: `codex`)
- **`can_talk_to: [builder]`** — the forensic bot only talks to the builder, who
  feeds it logs and relays its findings.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "given the raw stage logs and the red stage, find the *first*
  stage that actually failed, correlate timestamps/error chains, separate the
  proximate error from the root cause, and report root cause + evidence + fix +
  a guard. If logs are incomplete, ask the builder rather than guess."
- **Turn detection:** `codex` → `notify` hook.
- **`pings`** — a single periodic ping: `cron: "*/15 * * * *"` (every 15
  minutes) delivers the listed message into the correlator's `inbox/` and nudges
  it. `when_busy: skip` means if the correlator is **mid-turn** (busy analyzing a
  previous failure) the ping is *not* delivered, so you never pile a stale
  "any builds red?" on top of an in-flight investigation. This is the swarm's one
  piece of self-starting behavior — the other agents are purely event-driven off
  real mail from you or the builder.

### What's *not* in this config
- **No shared `workdir`.** Unlike the data-pipeline swarm, each agent has its own
  directory, so there is no mailbox namespacing and no on-disk coordination risk
  between agents. The pipeline *file* the writer produces gets relayed to the
  others by mail (the builder forwards it to the hardener), not by a shared repo.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/ci-cd-builder.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings.
2. Creates the runtime dirs (`ci-cd-builder-workspace/.agentainer/…`: log, queue,
   run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/
   about.md` contact card *is* the ACL made visible: the builder gets
   `outbox/pipeline_writer/`, `outbox/hardener/`, `outbox/log_correlator/`,
   `outbox/user/`; each specialist gets just `outbox/builder/`.
4. **Installs per-type turn detection** — but recall the example's `capture: none`
   default suppresses this; for live agents, remove it so the Claude Stop hook,
   Codex `notify` hook, and Gemini pane polling fire.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'ci-cd-builder' is up with 4 agent(s)
:: attach with:  tmux attach -t <builder-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/ci-cd-builder.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents, so it must **never** be exposed on `0.0.0.0` without a token.
See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** the shipped config sets `capture: none` precisely so you can
> swap each `command:` for a mock bash loop and watch the whole pipeline route
> mail with no API keys — the mechanics are identical. Swap them back for the real
> CLIs (and drop `capture: none`) to run real agents.

---

## 5. Drive a build

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the builder's finished-pipeline summary as
mail (rather than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/ci-cd-builder.yaml
```

This rewrites the `user` contact card in the builder's `outbox/user/about.md` to
`Status: available`, so the builder sees you're reachable. (While away, mail to
you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the repo + platform brief into the swarm, addressed to the builder:

```bash
./agentainer send --to builder -c examples/ci-cd-builder.yaml \
  "Repo at ./acme-api. Build a GitHub Actions pipeline that tests, lint, builds, \
   and deploys to staging."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the builder, then — because the
inbox was empty — **released into `inbox/`** and the builder is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the build advance one turn at a time. Each arrow
is a `stop → sweep → route → release → nudge` cycle:

1. **builder receives the brief.** It reads `inbox/`, acknowledges repo + platform
   to you (or holds it if you're away) and writes a delegation into
   `outbox/pipeline_writer/`. On stop, that routes to the writer.
2. **pipeline_writer drafts the config.** It reads its inbox, writes the CI config
   file into its workspace, and reports back into `outbox/builder/`. On stop, that
   routes to the builder.
3. **builder forwards to hardener.** It writes the draft into `outbox/hardener/`
   and waits for the security checklist. On stop, that routes to the hardener.
4. **hardener returns fixes.** It reads the draft, returns a prioritized fix list
   into `outbox/builder/`. The builder relays the mandatory fixes back to
   `pipeline_writer` and **will not finish until the hardener has cleared the
   config**.
5. **(on failure) builder → log_correlator.** When you report a red build, the
   builder hands the raw stage logs to `outbox/log_correlator/`. The correlator
   traces the root cause and reports it; the builder relays that as a fix brief to
   the writer. On each stop, mail routes onward.
6. **builder finalizes.** It reconciles the writer's config + the hardener's
   checklist + (optionally) the correlator's fix into ONE pipeline file plus a
   short summary, and writes that into `outbox/user/`. On stop, it's delivered to
   your `user` mailbox (visible with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

### The correlator ping in action

Every 15 minutes, if the swarm is up, the correlator's `pings` cron fires: the
ping message lands in its `inbox/` and it's nudged to check for staged failure
logs. If the correlator is busy (mid-analysis), `when_busy: skip` means the ping
is dropped silently — no pile-on. This is the one self-starting behavior; the
other three agents only move on real mail.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/ci-cd-builder.yaml
```

```
swarm: ci-cd-builder   root: ./ci-cd-builder-workspace
  builder        (claude) up idle queue=0 unread=1 talks=pipeline_writer, hardener, log_correlator, user
  pipeline_writer (codex) up idle queue=0 unread=0 talks=builder
  hardener        (gemini) up idle queue=0 unread=0 talks=builder
  log_correlator  (codex) up idle queue=0 unread=0 talks=builder
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/ci-cd-builder.yaml          # whole swarm, last 20
./agentainer logs -c examples/ci-cd-builder.yaml -f        # follow live
./agentainer logs pipeline_writer -c examples/ci-cd-builder.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
`ping`, etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox builder -c examples/ci-cd-builder.yaml
```

Prints the one released message (headers + body), or `builder: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue builder -c examples/ci-cd-builder.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach hardener -c examples/ci-cd-builder.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or by sending notes
into an agent's inbox.

- **Send a clarification to the builder.** Realized you want a manual gate on
  deploy, not auto-deploy? `./agentainer send --to builder -c examples/ci-cd-builder.yaml
  "Require a manual approval step before the staging deploy; do not auto-trigger
  on every push."` The builder relays the change down to the writer.
- **Ask for the security evidence.** `./agentainer send --to builder ... "Have the
  hardener call out which lines it flagged CRITICAL."` — the builder forwards it.
- **Report a red build.** `./agentainer send --to builder -c examples/ci-cd-builder.yaml
  "CI failed on the build stage. Logs: <paste stage logs>. Find the root cause."`
  The builder hands the logs to the correlator and relays the analysis.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different platform), tear it down:

```bash
./agentainer down -c examples/ci-cd-builder.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/ci-cd-builder.yaml     # resume is the default
```

On `up`, Agentainer reads `ci-cd-builder-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the
builder, `codex resume <id>` for the writers/correlator, `gemini …` for the
hardener. A resumed agent is *not* re-sent the standby prompt (its prior context
is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/ci-cd-builder.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a `deployer` or `monitor` agent
Once the pipeline ships, you may want someone owning the actual rollout or
watching runs. Add an agent that can read the builder's deliverable and owns its
part of the chain:

```yaml
  - name: deployer
    type: claude
    can_talk_to: [builder, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the DEPLOYER. Once the builder delivers a hardened pipeline, own the
      staging rollout runbook and the rollback plan; report status to outbox/user/.
      You never write pipeline code.
```
Then add `deployer` to the builder's `can_talk_to` so it can be briefed.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `pipeline_writer: type: claude` (or `hermes`/`gemini`) to put authoring on a
  different model than the builder.
- `hardener: type: claude` if you'd rather review security on Claude — but
  remember `gemini`/`hermes` need `capture: pane` (pane polling) since they have
  no completion hook.
- **Always delete the `defaults.capture: none` line before running real agents**,
  or set per-agent `capture` (`auto` for claude/codex, `pane` for gemini/hermes).

### Tune the ACL
- To let the `hardener` escalate straight to `user` (not only via the builder),
  add `user` to its `can_talk_to`. Mind that this widens the human-facing surface;
  the doc's convention keeps the builder the sole `user` contact.
- To keep the writer and correlator strictly firewalled from each other (already
  the case here — each lists only `builder`), leave them as-is; that's the
  one-place-reconciles-everything guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md)
  for mixing model families safely.

---

## 10. Tips & footguns

- **Keep the builder the only `user`-facing agent.** Only the builder lists `user`
  in `can_talk_to`. That gives you a single funnel: raw pipeline drafts and
  security verdicts always pass through review before they reach you. If the
  writer or hardener tries to mail `user` directly, the orchestrator bounces it
  (ACL) and drops a `system` note in their inbox explaining who they *can* message
  — the model self-corrects in-band.

- **`capture: none` is for the mock demo — remove it for real runs.** The shipped
  example disables turn detection so key-free mock agents come up and route mail.
  With live CLIs left at `capture: none`, the stop → sweep → route → nudge clock
  never advances on its own and agents pin "busy". Delete the `defaults.capture:
  none` line (or set per-agent `capture`) and the natural per-type detection takes
  over: claude→Stop hook, codex→`notify` program, gemini/hermes→pane polling.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch (e.g. a `claude` agent whose `command`
  doesn't launch Claude) means completion never triggers and the agent pins
  "busy" forever. `status` showing an agent `busy` for a long time with `unread`
  mail is the tell.

- **The correlator's `when_busy: skip` ping is deliberate.** The every-15-minute
  ping keeps the forensic bot from forgetting to check, but `when_busy: skip`
  prevents a stale "any builds red?" from landing on top of an in-flight analysis.
  If you want the ping to always fire, drop `when_busy: skip` — but expect possible
  pile-on if a long correlation is running.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/ci-cd-builder.yaml
  ./agentainer remove-session -c examples/ci-cd-builder.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

- **Availability shapes the ending.** If `user` is **away** when the builder
  finishes, your pipeline summary is *held* (with a `system` "the user is away" ack
  to the builder) rather than lost — read it later with
  `agentainer user inbox` or flip yourself available and it's delivered.

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
- [`cli-reference.md`](../cli-reference.md) — every subcommand, including `user`,
  `send`, `pings`, and `serve`.
- `examples/ci-cd-builder.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

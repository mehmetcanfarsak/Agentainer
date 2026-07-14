# Use case: Terraform reviewer

A concrete, end-to-end walkthrough of the shipped
`examples/terraform-reviewer.yaml` swarm — an IaC (infrastructure-as-code)
review line that turns a human's infrastructure request into a reviewed,
drift-checked Terraform plan. A hub **architect** takes your request, delegates
plan authoring to a **planner**, fans the plan out to **two independent,
non-redundant reviewers** (security/cost/least-privilege vs
correctness/idempotency/state), and runs a **drift-checker** that compares the
plan against live cloud state. The architect reconciles everything into one
plan-of-record and delivers it back to you.

Everything below is based on the actual contents of
`examples/terraform-reviewer.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Platform engineers, SREs, and infra/DevOps teams who want a structured,
multi-lens review of Terraform *before* it touches a cloud account — without
being every one of those lenses themselves. The swarm encodes the discipline
that makes IaC safe: a single owner of the plan-of-record (the architect), a
builder who only builds, two reviewers who use deliberately *different* lenses
so a flaw slips past neither, and a drift-checker that closes the loop by
proving the plan still matches reality after apply.

It is a **hub-and-spoke**, not a free-for-all: the planner and all three
reviewers report *only* to the architect, and only the architect talks to you.
That keeps the human-facing surface to a single reconciled deliverable. Swapping
the `gemini` reviewer for `claude`/`codex`, or adding a `cost-optimizer` spoke,
is a one-line config change (see §9).

---

## 2. The topology

```
           user
             │  "build <infra request>"
             ▼
          architect                                  (the hub: owns the human,
             │                                        the plan-of-record, the merge)
             ├──▶ planner ───────── writes the Terraform + modules ─┐
             ├──▶ reviewer-sec ─── security · cost · least-priv ────┤  (independent lenses)
             ├──▶ reviewer-correct ─ correctness · idempotency · state ┤
             └──▶ drift-checker ─ compares live state vs plan ──────┘
                      ▲ all four report back only to architect
                      │
               architect ──▶ user   (reconciled plan + drift verdict)
```

Five agents, one directed flow:

1. **`user` → `architect`** — you send the infrastructure request (topology,
   regions, guardrails, or a repo to operate on).
2. **`architect` → `planner`** — the architect forwards your intent verbatim and
   asks for the Terraform (root module + reusable child modules + tfvars).
3. **`planner` → `architect`** — the planner returns the full plan.
4. **`architect` → `reviewer-sec`** and **`architect` → `reviewer-correct`** — the
   plan is fanned out as two *separate* messages, each told its lane is unique
   (sec owns risk/cost/IAM; correct owns apply/idempotency/state). They run in
   parallel but both report back to the architect.
5. **`architect` → `drift-checker`** (with plan + both reviews) — the loop-closer
   compares the proposed plan against live cloud state and reports what would
   drift.
6. **`architect` → `user`** — once all four replies are in, the architect merges
   the plan with both critiques and the drift verdict into one delivered
   plan-of-record.

The routing above is *enforced* by each agent's `can_talk_to` list. Every
non-architect agent lists **only** `architect`; the planner and reviewers can
never reach the `user` or each other directly. Anything addressed outside an
agent's list is bounced back as a `system` message and filed in `failed/` (see
§7). Notably, the two reviewers never talk to each other — architect is the only
cross-lens junction, which is what keeps their lenses from overlapping.

---

## 3. The config, explained

Here is `examples/terraform-reviewer.yaml` with the long role bodies trimmed to
their essence (the real file keeps the full standing prompts):

```yaml
swarm:
  name: terraform-reviewer
  root: ./terraform-reviewer-workspace

defaults:
  capture: none              # loader auto-upgrades claude/codex to their hook
  can_talk_to: []            # tightened per agent below

agents:
  - name: architect
    type: claude
    can_talk_to: [planner, reviewer-sec, reviewer-correct, drift-checker, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      The hub. The only agent that talks to user. Owns the plan-of-record.
      Delegates to planner, fans the plan to the two reviewers, forwards
      plan+reviews to drift-checker, then RECONCILES into one delivered plan
      and writes it to outbox/user/. Never writes Terraform itself.

  - name: planner
    type: codex
    can_talk_to: [architect]
    command: "codex --yolo"
    role: |
      The ONLY agent that writes Terraform. Builds root module + child modules +
      tfvars + remote-state/locking, following the guardrails. Does NOT review
      (security/cost/correctness are the reviewers' job). Reports back to
      outbox/architect/.

  - name: reviewer-sec
    type: gemini
    can_talk_to: [architect]
    command: "gemini --yolo"
    role: |
      Reviewer #1. Lens = SECURITY, COST, LEAST-PRIVILEGE IAM only. Reviews IAM,
      data exposure, network, cost; ranks findings; does NOT edit the plan.
      Reports to outbox/architect/.

  - name: reviewer-correct
    type: codex
    can_talk_to: [architect]
    command: "codex --yolo"
    role: |
      Reviewer #2. Lens = CORRECTNESS, IDEMPOTENCY, REMOTE-STATE/DRIFT only.
      Reviews references/version pinning, apply idempotency, backend/locking,
      drift risk; ranks findings; does NOT edit the plan. Reports to
      outbox/architect/.

  - name: drift-checker
    type: claude
    can_talk_to: [architect]
    command: "claude --dangerously-skip-permissions"
    role: |
      The loop-closer. Compares the reconciled plan (plus reviews) against live
      cloud state; produces a drift report (what apply would CREATE/CHANGE/
      DESTROY, out-of-band resources, a one-line verdict). Does NOT modify the
      plan or the cloud. Reports to outbox/architect/.
```

Field by field:

### `swarm`
- **`name: terraform-reviewer`** — the swarm's name (shows up in `status`,
  logs, sessions).
- **`root: ./terraform-reviewer-workspace`** — the parent directory for the
  agents' working directories and mailboxes. Each agent gets its own private
  workdir (`terraform-reviewer-workspace/architect`, `.../planner`, etc. —
  note this is **not** a shared workdir like the data-pipeline example, so no
  mailbox namespacing is needed). Orchestrator state goes under
  `terraform-reviewer-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. *Crucially, the loader
  only auto-upgrades `claude` and `codex` to their natural completion hook; it
  does **not** touch `gemini`.* So `architect`, `planner`, `reviewer-correct`,
  and `drift-checker` are auto-upgraded to `capture: hook`, but `reviewer-sec`
  (`gemini`) stays `capture: none` — see the footgun note below.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent
  below states its own list explicitly, so this default is just a safe floor.

### `architect` (type: `claude`)
- **`can_talk_to: [planner, reviewer-sec, reviewer-correct, drift-checker, user]`**
  — the architect is the hub: it delegates to the four specialists and is the
  **only agent that can talk to `user`**. That last part matters — keep the
  human-facing surface to a single agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command, e.g. a
  shell alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the architect waits for your request instead of
  proactively mailing peers. Its job is explicitly *routing, gating, and
  reconciling*, never authoring Terraform.
- **Turn detection:** `claude` → a **Stop hook** (auto-installed at `up`, since
  `capture: none` is upgraded to `hook` for claude).

### `planner` (type: `codex`)
- **`can_talk_to: [architect]`** — the planner only reports back to the
  architect. It is the *only* agent that writes Terraform; the reviewers are
  forbidden from editing it, which keeps authorship and review cleanly separated.
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `reviewer-sec` (type: `gemini`)
- **`can_talk_to: [architect]`** — reports its security/cost findings only to
  the architect and never to the other reviewer or the `user`.
- **`command: "gemini --yolo"`** — placeholder launch command for Gemini CLI.
- **Turn detection: `capture: none`, NOT auto-upgraded.** `gemini` has no
  completion hook, and the loader only auto-upgrades `claude`/`codex`. As
  written, this agent has **no turn-completion signal** and will pin "busy"
  forever once it stops — see the footgun in §10. Add `capture: pane` to this
  agent to use pane polling.

### `reviewer-correct` (type: `codex`)
- **`can_talk_to: [architect]`** — reports its correctness/state findings only
  to the architect. Deliberately *not* allowed to talk to `reviewer-sec`, so the
  two lenses can't blur into one review.
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → `notify` hook.

### `drift-checker` (type: `claude`)
- **`can_talk_to: [architect]`** — the loop-closer; reports the drift verdict
  only to the architect.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook.

### What's *not* in this config
- **No `ping`/cron schedules.** The swarm is purely event-driven off real mail —
  it only moves when you send an infra request. (Add a `pings:` entry to the
  architect if you want a stale-plan nag, e.g. re-validate drift on a schedule.)
- **No shared workdir.** Every agent has its own private directory, so there is
  no mailbox namespacing (unlike a shared-repo swarm). Each agent's on-disk
  layout is the plain `inbox/ outbox/ read/ sent/ failed/` set. For the
  shared-workdir treatment see [`custom-workspace.md`](./custom-workspace.md).
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **A mixed-model cluster.** This swarm runs `claude`, `codex`, and `gemini`
  agents side by side. That mix is exactly what [`multi-llm-swarm.md`](./multi-llm-swarm.md)
  covers — the only constraint is that each `command` launches the family its
  `type` implies, so completion actually fires.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/terraform-reviewer.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings. Here it will warn that
   `capture: none` on the `claude`/`codex` agents is auto-upgraded to `hook`
   (and silently leaves the `gemini` agent on `none` — see §10).
2. Creates the runtime dirs (`terraform-reviewer-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. Because every agent
   has a distinct workdir, folders are created unprefixed. The
   `outbox/<peer>/about.md` contact card *is* the ACL made visible: the
   architect gets `outbox/planner/`, `outbox/reviewer-sec/`,
   `outbox/reviewer-correct/`, `outbox/drift-checker/`, `outbox/user/`; the
   planner gets only `outbox/architect/`; and so on.
4. **Installs per-type turn detection** — the Claude Stop hook for `architect`
   and `drift-checker`, the Codex `notify` hook for `planner` and
   `reviewer-correct`. (`reviewer-sec`/`gemini` gets nothing unless you set
   `capture: pane` — see §10.)
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'terraform-reviewer' is up with 5 agent(s)
:: attach with:  tmux attach -t <architect-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/terraform-reviewer.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents, so it must **never** be exposed on `0.0.0.0` without a token.
See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole review fan-out route mail with no API keys — the mechanics are
> identical.

---

## 5. Drive a spec

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the architect's reconciled plan as mail
(rather than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/terraform-reviewer.yaml
```

This rewrites the `user` contact card in the architect's `outbox/user/about.md`
to `Status: available`, so the architect sees you're reachable. (While away, mail
to you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the infrastructure request into the swarm, addressed to the architect:

```bash
./agentainer send --to architect -c examples/terraform-reviewer.yaml \
  "Stand up a 3-tier app: ALB + 2 ECS services in private subnets, RDS Postgres \
   Multi-AZ, KMS key, all in eu-west-1. Least privilege, no public buckets."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the architect, then — because
the inbox was empty — **released into `inbox/`** and the architect is **nudged**
(the protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the review advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **architect delegates to planner.** It reads `inbox/`, forwards your intent
   verbatim into `outbox/planner/`. On stop, that routes to the planner.
2. **planner authors the Terraform.** It writes the root module + child modules
   + tfvars + remote-state config into `outbox/architect/`. On stop, that routes
   back to the architect.
3. **architect fans out to the two reviewers.** It writes the *same* plan into
   `outbox/reviewer-sec/` (security/cost lens) and `outbox/reviewer-correct/`
   (correctness/state lens) as two separate messages, each told its lane is
   unique. On stop, both route in parallel.
4. **the two reviewers report back.** Each writes its ranked findings into
   `outbox/architect/`. On stop, both route back. (They never see each other's
   output — only the architect does.)
5. **architect briefs the drift-checker.** It forwards the plan + both reviews
   into `outbox/drift-checker/`. On stop, that routes to the drift-checker.
6. **drift-checker closes the loop.** It writes the drift report
   (CREATE/CHANGE/DESTROY vs live state, out-of-band resources, a one-line
   verdict) into `outbox/architect/`. On stop, that routes back.
7. **architect reconciles and delivers.** Once all four replies are in, it merges
   plan + both critiques + drift verdict into one plan-of-record and writes it
   into `outbox/user/`. On stop, that's delivered to your `user` mailbox (visible
   with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a request, the agents just sit in standby (that's the point
> of the standby prompt). The review only moves when real mail arrives — this
> swarm has no periodic pings to self-start it.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/terraform-reviewer.yaml
```

```
swarm: terraform-reviewer   root: ./terraform-reviewer-workspace
  architect        (claude) up idle queue=0 unread=0 talks=planner, reviewer-sec, reviewer-correct, drift-checker, user
  planner          (codex)  up idle queue=0 unread=1 talks=architect
  reviewer-sec     (gemini) up idle queue=0 unread=0 talks=architect
  reviewer-correct (codex)  up idle queue=0 unread=0 talks=architect
  drift-checker    (claude) up idle queue=0 unread=0 talks=architect
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/terraform-reviewer.yaml          # whole swarm, last 20
./agentainer logs -c examples/terraform-reviewer.yaml -f        # follow live
./agentainer logs planner -c examples/terraform-reviewer.yaml   # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox architect -c examples/terraform-reviewer.yaml
```

Prints the one released message (headers + body), or `architect: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue architect -c examples/terraform-reviewer.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach reviewer-sec -c examples/terraform-reviewer.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom
— handy for un-sticking an agent, but the mail model is the normal path.)

**The agent workdirs** — each agent's Terraform work (the planner's modules, the
reviewers' notes, the drift-checker's report) lives in its own
`terraform-reviewer-workspace/<name>/` directory. Inspect the produced
modules, the two review reports, and the drift report there.

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or (more directly)
by sending notes into an agent's inbox.

- **Send a clarification to the architect.** Realized RDS should be serverless,
  not Multi-AZ? `./agentainer send --to architect -c examples/terraform-reviewer.yaml
  "Make RDS serverless; re-brief the planner and have reviewer-correct re-check
  idempotency."` The architect relays the change down the chain.
- **Ask a reviewer for the evidence.** `./agentainer send --to architect ... "Have
  reviewer-sec cite the exact security-group lines for the 0.0.0.0/0 finding."`
  — the architect forwards it.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/terraform-reviewer.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/terraform-reviewer.yaml     # resume is the default
```

On `up`, Agentainer reads `terraform-reviewer-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the
architect and drift-checker, `codex resume <id>` for the planner and
reviewer-correct, and `gemini`'s native resume for reviewer-sec. A resumed agent
is *not* re-sent the standby prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/terraform-reviewer.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Fix the `gemini` turn-detection gap
As shipped, `reviewer-sec` runs `gemini` with `capture: none`, which gives the
orchestrator no turn-completion signal (the loader only auto-upgrades
`claude`/`codex`). Add the pane-polling capture so it reports completion:

```yaml
  - name: reviewer-sec
    type: gemini
    capture: pane            # <-- add this; gemini has no completion hook
    can_talk_to: [architect]
    command: "gemini --yolo"
    role: |
      ...
```

### Add a `cost-optimizer` spoke
If you want cost analysis pulled out of `reviewer-sec` into its own agent, add a
fifth reviewer that reports only to the architect and add it to the architect's
`can_talk_to`:

```yaml
  - name: cost-optimizer
    type: codex
    can_talk_to: [architect]
    command: "codex --yolo"
    role: |
      You are COST-OPTIMIZER. Given the Terraform plan, find the cheapest safe
      instance/storage classes, flag always-on vs scheduled, NAT/egress, and
      unused elastic IPs. Report ranked savings to outbox/architect/. You never
      edit the plan.
```

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `reviewer-sec: type: claude` (or `codex`) to put security review on the same
  family as the rest — and then `capture: none` *will* auto-upgrade, removing the
  footgun above.
- `planner: type: hermes` if you want plan authoring on a different model than
  the reviewers. Remember: `gemini`/`hermes` need `capture: pane` (pane
  polling) since they have no completion hook. For the safe way to mix families,
  see [`multi-llm-swarm.md`](./multi-llm-swarm.md).

### Tune the ACL
- To let a reviewer escalate straight to `user` (not only via the architect), add
  `user` to its `can_talk_to`. Mind that this widens the human-facing surface;
  the doc's convention keeps the architect the sole `user` contact.
- To keep the two reviewers from ever overlapping, leave each on
  `can_talk_to: [architect]` — that's the two-independent-lenses guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing.

---

## 10. Tips & footguns

- **The `gemini` reviewer has no turn-completion signal as shipped.** This is the
  biggest footgun in the config. `defaults.capture: none` is *only* auto-upgraded
  for `claude` and `codex` agents; `reviewer-sec` is `gemini`, so it stays
  `capture: none` and the orchestrator never learns it finished a turn. The agent
  looks "busy" forever and the architect's fan-out stalls on that spoke. **Fix:**
  add `capture: pane` to `reviewer-sec` (§9), or change its `type` to `claude`/
  `codex` so the auto-upgrade applies. Watch `status` — a `gemini` agent pinned
  `busy` with `unread` mail is the tell.

- **Keep the architect the only `user`-facing agent.** Only the architect lists
  `user` in `can_talk_to`. That gives you a single funnel: raw plans and review
  verdicts always pass through reconciliation before they reach you. If a planner
  or reviewer tries to mail `user` directly, the orchestrator bounces it (ACL)
  and drops a `system` note in their inbox explaining who they *can* message —
  the model self-corrects in-band.

- **The two reviewers are independent by design.** Neither lists the other, and
  neither can write Terraform (only `planner` can). That separation is what makes
  the security/cost and correctness/state lenses non-redundant. Don't "help" by
  letting them talk to each other — the architect is the deliberate single
  cross-lens junction.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch (e.g. a `claude` agent whose `command`
  doesn't launch Claude) means completion never triggers and the agent pins
  "busy" forever. `status` showing an agent `busy` for a long time with `unread`
  mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/terraform-reviewer.yaml
  ./agentainer remove-session -c examples/terraform-reviewer.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files in
  `terraform-reviewer-workspace/` or your config.

- **Availability shapes the ending.** If `user` is **away** when the architect
  finishes, your reconciled plan is *held* (with a `system` "the user is away"
  ack to the architect) rather than lost — read it later with
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
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- `examples/terraform-reviewer.yaml` — the config this walkthrough is built on.

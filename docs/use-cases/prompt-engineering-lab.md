# Use case: the prompt-engineering lab

A concrete, end-to-end walkthrough of the shipped `examples/prompt-engineering-lab.yaml`
swarm — a four-agent loop that turns a task description plus a handful of test
cases into a battle-tested prompt. A **lab_lead** takes the brief from the human,
a **generator** drafts candidate prompts, an **evaluator** scores each candidate
against the test cases, and a **critic** diagnoses the failures and proposes
fixes. The lab_lead runs the generate → evaluate → critique loop for a few rounds
and ships the winner back to you.

If you've ever tuned a prompt by hand — draft, run it against your examples, stare
at what broke, tweak, repeat — this is that inner loop, split across four
specialists and wired through Agentainer's file-based mail model.

Everything below is based on the actual contents of `examples/prompt-engineering-lab.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you supply the coding-CLI
commands (or swap them for mock bash loops).

**Who this is for:** prompt engineers who want a repeatable optimization loop
instead of ad-hoc tweaking; PMs and support/ops leads who own a prompt in
production and want it hardened against a fixed test set; anyone who has a "make
this prompt better" task and a few input→expected examples to grade against.

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The topology

```
        task + test cases
  user ──────────────────▶ lab_lead ──────────────▶ generator
        (best prompt)  ◀──────┼─┐  ▲                    │
                              │ │  │ candidate          │ candidate
                    scorecard │ │  └────────────────────┘
                     evaluator◀─┘  │
                              │    │ diagnosis
                     failing  │    │
                     cases    ▼    │
                            critic ┘
```

Four agents, one closed loop that always passes through the hub:

1. **`user` → `lab_lead`** — you send the task and the test cases.
2. **`lab_lead` → `generator`** — the lab_lead briefs the generator and asks for
   one candidate prompt.
3. **`generator` → `lab_lead`** — the generator returns a candidate.
4. **`lab_lead` → `evaluator`** — the lab_lead forwards the candidate + test cases
   for scoring.
5. **`evaluator` → `lab_lead`** — the evaluator returns a pass/fail scorecard.
6. **`lab_lead` → `critic`** — if cases failed, the lab_lead sends the candidate +
   failures for diagnosis.
7. **`critic` → `lab_lead`** — the critic returns root causes and concrete fixes,
   which the lab_lead feeds into the next generator round.
8. **`lab_lead` → `user`** — when the candidate passes (or the score plateaus),
   the lab_lead delivers the winning prompt to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. The generator, evaluator, and critic can each only talk to the
lab_lead; they never message each other. Everything funnels through the hub, so
one place owns the round counter and the stopping decision. Anything off-list is
bounced back as a `system` message and filed in `failed/` (see §7).

---

## 2. The config, explained

Here is `examples/prompt-engineering-lab.yaml` in full (comments trimmed for
brevity — the file has a header block too):

```yaml
swarm:
  name: promptlab
  root: ./promptlab-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: lab_lead
    type: claude
    can_talk_to: [generator, evaluator, critic, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the LAB LEAD ... run an iterative loop that produces the best
      possible prompt ... you orchestrate the specialists and own the stopping
      decision ... stop when the candidate passes all test cases OR the score
      plateaus ... deliver the final prompt to the user.
  - name: generator
    type: claude
    can_talk_to: [lab_lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the PROMPT GENERATOR ... write ONE new candidate prompt ... return
      the full prompt text verbatim, plus what you changed and why.
  - name: evaluator
    type: claude
    can_talk_to: [lab_lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the EVALUATOR ... run the candidate against EVERY test case ...
      PASS/FAIL with a one-line reason ... end with "Score: N/M passed".
  - name: critic
    type: claude
    can_talk_to: [lab_lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the CRITIC ... explain the ROOT CAUSE of each failure and propose a
      concrete, specific fix ... hand the generator an actionable diagnosis.
```

Field by field:

### `swarm`
- **`name: promptlab`** — the swarm's name (shows up in `status`, logs, sessions).
- **`root: ./promptlab-workspace`** — the parent directory for the agents' working
  directories and mailboxes. Each agent gets `promptlab-workspace/<name>/` as its
  workdir (created on `up`), and its mailbox folders live alongside. Orchestrator
  state goes under `promptlab-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, and it's ultimately keyed off each
  agent's `type`. For `claude`, whose CLI supports a completion **hook**, setting
  `capture: none` is a footgun — so the config loader *upgrades* it back to `hook`
  and prints a warning at `up` (`capture: none on a claude agent gives the
  orchestrator no turn-completion signal -- auto-upgraded to capture: hook.`). Net
  effect here: **all four agents use their Stop hook**, because all four are
  `claude`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `lab_lead` (type: `claude`)
- **`can_talk_to: [generator, evaluator, critic, user]`** — the lab_lead is the
  hub: it can reach all three specialists and it is the **only agent that can talk
  to `user`**. That last part matters — keep the human-facing surface to a single
  agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the lab_lead waits for your brief instead of
  proactively mailing the specialists. The role also tells it to keep an explicit
  **round counter** so the loop terminates.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `generator` (type: `claude`)
- **`can_talk_to: [lab_lead]`** — replies only to the hub. It cannot reach the
  evaluator or critic directly, and it cannot talk to `user`.
- **`role`** — "write ONE new candidate prompt per round; return the full text
  verbatim plus what changed and why." Returning the prompt *verbatim* matters:
  it's exactly what the evaluator will run next.

### `evaluator` (type: `claude`)
- **`can_talk_to: [lab_lead]`** — reports scores only to the hub.
- **`role`** — "run the candidate against EVERY test case; PASS/FAIL with a
  one-line reason; end with `Score: N/M passed`." It's told to be a harsh, literal
  grader and to never rewrite the prompt — measurement and authorship stay
  separate on purpose (see Tips).

### `critic` (type: `claude`)
- **`can_talk_to: [lab_lead]`** — reports diagnoses only to the hub.
- **`role`** — "explain the ROOT CAUSE of each failure and propose a concrete
  fix; hand the generator an actionable diagnosis, not a rewrite." Keeping the
  critic out of full rewrites stops it and the generator from thrashing on the
  same text.

### What's *not* in this config
- **No `pings`.** No agent is auto-nudged on a timer while
  idle — the loop is purely event-driven off real mail. (If you wanted the
  lab_lead to poke a slow evaluator, you'd add a `pings` cron rule
  to it.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **Mixed models?** Not here — all four are `claude` so every agent has a native
  Stop hook. See §8 for swapping in `codex`/`gemini` specialists and what changes.

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/prompt-engineering-lab.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for all four claude agents).
2. Creates the runtime dirs (`promptlab-workspace/.agentainer/…`: log, queue, run,
   sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: the lab_lead gets
   `outbox/generator/`, `outbox/evaluator/`, `outbox/critic/`, `outbox/user/`;
   each specialist gets only `outbox/lab_lead/`.
4. **Installs per-type turn detection** — the Claude Stop hook for every agent.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'promptlab' is up with 4 agent(s)
:: attach with:  tmux attach -t <lab_lead-session>
:: you can use the UI with:  agentainer serve -c examples/prompt-engineering-lab.yaml
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). It **binds `127.0.0.1` by default** — pass
`--host 0.0.0.0 --token <secret>` only if you deliberately want remote access. See
the `README.md` "control-plane UI" section and [`../ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole loop route mail with no API keys — the mechanics are identical.

---

## 4. Drive a task

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the lab_lead's final prompt as mail (rather
than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/prompt-engineering-lab.yaml
```

This rewrites the `user` contact card in the lab_lead's `outbox/user/about.md` to
`Status: available`, so the lab_lead sees you're reachable. (While away, mail to
you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the brief into the swarm, addressed to the lab_lead. Put the task and the
test cases in one message — the lab_lead re-packages them for the specialists:

```bash
./agentainer send --to lab_lead -c examples/prompt-engineering-lab.yaml "$(cat <<'EOF'
Task: classify each support ticket as exactly one of: bug, billing, how-to.
Deliver the best SYSTEM PROMPT for this.

Test cases (input -> expected label):
1. "The app crashes when I tap export" -> bug
2. "I was charged twice this month" -> billing
3. "How do I change my avatar?" -> how-to
4. "Export button does nothing AND I want a refund" -> bug   (bug wins on ambiguity)
5. "Is there a keyboard shortcut for search?" -> how-to
EOF
)"
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the lab_lead, then — because the
inbox was empty — **released into `inbox/`** and the lab_lead is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The loop turning

Watching the log (§5), you'll see the loop advance one turn at a time. Each arrow
is a `stop → sweep → route → release → nudge` cycle:

1. **lab_lead receives the brief.** It reads `inbox/`, restates the task + test
   cases, and writes a generation request into `outbox/generator/`. On stop, that
   routes to the generator.
2. **generator drafts round 1.** It reads its inbox, writes a candidate prompt
   into `outbox/lab_lead/`. On stop, that routes back to the lab_lead.
3. **lab_lead forwards to scoring.** It writes the candidate + test cases into
   `outbox/evaluator/`.
4. **evaluator scores.** It runs the candidate against every case and writes a
   `Score: N/M passed` scorecard into `outbox/lab_lead/`.
5. **lab_lead decides.** If not all cases passed, it sends the candidate + failing
   cases to `outbox/critic/`; the critic returns a diagnosis; the lab_lead feeds
   that into a fresh `outbox/generator/` request for round 2 — and the loop
   repeats from step 2.
6. **lab_lead ships.** When a candidate passes all cases (or the score plateaus
   after a few rounds), the lab_lead writes the winning prompt + a short summary
   into `outbox/user/`. On stop, that's delivered to your `user` mailbox (read it
   with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a task, the agents just sit in standby (that's the point of
> the standby prompt). The loop only moves when real mail arrives — this swarm has
> no periodic pings to self-start it.

---

## 5. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/prompt-engineering-lab.yaml
```

```
swarm: promptlab   root: ./promptlab-workspace
  lab_lead (claude) up idle queue=0 unread=0 talks=generator, evaluator, critic, user
  generator (claude) up idle queue=0 unread=1 talks=lab_lead
  evaluator (claude) up idle queue=0 unread=0 talks=lab_lead
  critic (claude) up idle queue=0 unread=0 talks=lab_lead
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/prompt-engineering-lab.yaml           # whole swarm, last 20
./agentainer logs -c examples/prompt-engineering-lab.yaml -f         # follow live
./agentainer logs generator -c examples/prompt-engineering-lab.yaml  # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event. Following the log is the best way to watch the
round-by-round loop and confirm the score is actually improving.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox evaluator -c examples/prompt-engineering-lab.yaml
```

Prints the one released message (headers + body), or `evaluator: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue lab_lead -c examples/prompt-engineering-lab.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach lab_lead -c examples/prompt-engineering-lab.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 6. Iterate — running more rounds and more tasks

The loop's stopping rule lives in the lab_lead's `role` ("stop when the candidate
passes all cases OR the score plateaus after a few rounds"). A few ways to steer
it:

- **Ask for more rounds explicitly.** If you want the lab to push harder, say so
  in your brief: *"run at least 4 rounds even if an early candidate passes, and
  report the best of all of them."* The lab_lead honors instructions in the task
  message.
- **Tighten the test set between tasks.** The most effective iteration lever is
  the test cases, not the prompt. When the shipped prompt fails on a real ticket
  later, add that ticket as a new expected case and re-send the whole brief — the
  lab hardens the prompt against the case that actually broke.
- **Run a second, harder task in the same swarm.** Just `send --to lab_lead`
  again with a new task + cases. Conversations persist, so the specialists carry
  context; if you'd rather start clean, see `remove-session` in §7.
- **Inspect a round mid-flight.** `agentainer inbox evaluator` shows the exact
  candidate being scored right now; `logs -f` shows each `Score: N/M` as it lands.

Because the evaluator grades a fixed, explicit test set, "better" is measurable
across rounds — that's the whole point of splitting generation from evaluation.

---

## 7. Tips & footguns

- **Keep the lab_lead the only `user`-facing agent.** In this config only the
  lab_lead lists `user` in `can_talk_to`. That gives you a single point of contact
  and a clean funnel: candidates always pass through scoring and critique before
  anything reaches you. If a specialist tries to mail `user` directly, the
  orchestrator bounces it (ACL) and drops a `system` note in the specialist's
  inbox explaining who it *can* message — the model self-corrects in-band.

- **Separate the grader from the author.** The evaluator never rewrites the prompt
  and the generator never scores its own work. This is deliberate: a model grading
  its own output tends to be generous. Routing the candidate to a fresh evaluator
  keeps the `Score: N/M` honest. Resist the urge to let the generator "just fix it
  and re-check" — that collapses the loop.

- **Give the loop a stopping rule, or it runs forever.** The lab_lead's role
  includes an explicit round counter and a plateau clause. If you rewrite the role,
  keep a termination condition, or a stubborn task ("no candidate ever passes case
  4") will loop until the runaway cap trips.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually fires**
  — a `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't
  launch Claude) means completion never triggers and the agent pins "busy" forever.
  `status` showing an agent `busy` for a long time with `unread` mail is the tell.
  (Agentainer refuses to `up` an obvious mismatch — see `lib/config.py`.)

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill a generator↔lab_lead ping-pong that stops making progress.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/prompt-engineering-lab.yaml
  ./agentainer remove-session -c examples/prompt-engineering-lab.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

- **Availability shapes the ending.** If `user` is **away** when the lab_lead
  finishes, your final prompt is *held* (with a `system` "the user is away" ack to
  the lab_lead) rather than lost — read it later with `agentainer user inbox` or
  flip yourself available and it's delivered.

---

## 8. Customize

- **Add a `dataset_builder`.** If you don't have test cases yet, add a fifth agent
  that manufactures them from the task description, and let the lab_lead consult it
  first:
  ```yaml
  - name: dataset_builder
    type: claude
    can_talk_to: [lab_lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the DATASET BUILDER. Given a task description, produce a diverse set
      of test cases (input -> expected output), including tricky edge cases and
      ambiguous inputs. Return them as a numbered list to the lab_lead.
  ```
  Then add `dataset_builder` to the lab_lead's `can_talk_to`, and tell the
  lab_lead in its role to request cases from `dataset_builder` when the user's
  brief doesn't include any.

- **Swap models per role.** All four agents are `claude` here for the native Stop
  hook, but nothing requires that. You could make the generator a `codex` agent
  (also hook-captured) or the evaluator a `gemini` agent — just remember `gemini`
  and `hermes` are **pane-captured**, so add `capture: pane` and expect no resume
  bridge for those (see [`../sessions-and-resume.md`](../sessions-and-resume.md)).
  A pattern that pays off: use *different* model families for generator and
  evaluator so the grader doesn't share the author's blind spots. For the
  mechanics of a mixed-model swarm, see
  [`./multi-llm-swarm.md`](./multi-llm-swarm.md).

- **Tune the ACL.** The strict hub-and-spoke here keeps iteration honest. If you
  want the critic and generator to collaborate directly (faster, but less
  controlled), add each to the other's `can_talk_to` — but then the lab_lead loses
  visibility into the round count, so keep the plateau/round-cap language in the
  lab_lead's role. Loosening the ACL trades control for speed; the default is the
  conservative choice.

- **Force-idle a stuck turn.** If you swap in a pane-captured model and its turn
  never registers, nudge the state along:
  ```bash
  ./agentainer idle evaluator -c examples/prompt-engineering-lab.yaml
  ```

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders, routing, ACL, and state.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — how conversations
  persist across `down`/`up` and which types can resume.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — the general
  hub-delegates-work pattern this loop is built on.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing claude/codex/gemini/hermes
  in one swarm, and the capture/resume trade-offs.
- `examples/prompt-engineering-lab.yaml` — the config walked through above.

# Use case: the new-hire onboarding buddy

A concrete, end-to-end walkthrough of the shipped `examples/onboarding-buddy.yaml`
swarm — a four-agent hub where a friendly **buddy** welcomes a brand-new employee
and quietly pulls in three specialists (**faq**, **checklist**, **it_help**) so the
new hire only ever talks to *one* agent. It's the "single front door, expert team
behind it" pattern, wired entirely through Agentainer's file-based mail model.

**Who this is for:**
- **HR / People Ops** who want new hires to get fast, consistent answers in week
  one without a human answering the same twenty questions each Monday.
- **Team leads / onboarding buddies** who want a checklist and IT-setup track that
  doesn't fall on the floor.
- **New hires** themselves — one place to ask *anything*, no "who do I email about
  the VPN?" guesswork.

Everything below is based on the actual contents of `examples/onboarding-buddy.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you supply the coding-CLI
commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The topology

```
        "Hi, I'm Priya..."
  user ──────────────────▶ buddy ─────┬─────▶ faq        (policy / culture)
        (one warm answer) ◀───────────┤
                                       ├─────▶ checklist  (tasks + deadlines)
                                       │
                                       └─────▶ it_help    (tools / access)
```

Four agents, one hub-and-spoke flow:

1. **`user` → `buddy`** — the new hire introduces themselves and asks a question.
2. **`buddy` → specialist** — buddy decides *which* expert owns the question and
   delegates to `faq`, `checklist`, or `it_help` (one at a time).
3. **specialist → `buddy`** — each specialist answers *only* back to buddy. They
   never talk to each other, and never to the new hire directly.
4. **`buddy` → `user`** — buddy folds the specialist replies into a single,
   friendly message and delivers it to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. An agent can only deliver to names on its own list; anything
else is bounced back as a `system` message and filed in `failed/` (see §7). Here
that means **only `buddy` can reach `user`**, and each specialist can reach **only
`buddy`** — so the new hire always gets one coherent voice, never four.

---

## 2. The config, explained

Here is `examples/onboarding-buddy.yaml`, agent by agent (the header comment and
full role text are in the file itself):

```yaml
swarm:
  name: onboarding
  root: ./onboarding-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: buddy
    type: claude
    can_talk_to: [faq, checklist, it_help, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are BUDDY, the onboarding buddy ... (greets the human, routes to the
      right specialist, synthesizes ONE answer back to user)
  - name: faq
    type: claude
    can_talk_to: [buddy]
    command: "claude --dangerously-skip-permissions"
    role: "You are the PEOPLE & CULTURE FAQ desk ..."
  - name: checklist
    type: claude
    can_talk_to: [buddy]
    command: "claude --dangerously-skip-permissions"
    role: "You are the ONBOARDING CHECKLIST keeper ... (owns CHECKLIST.md + dates)"
  - name: it_help
    type: claude
    can_talk_to: [buddy]
    command: "claude --dangerously-skip-permissions"
    role: "You are IT HELP for onboarding ... (laptop, accounts, VPN, access)"
```

Field by field:

### `swarm`
- **`name: onboarding`** — the swarm's name (shows up in `status`, logs, sessions).
- **`root: ./onboarding-workspace`** — the parent directory for the agents' working
  directories and mailboxes. Each agent gets `onboarding-workspace/<name>/` as its
  workdir (created on `up`), and its mailbox folders live alongside. Orchestrator
  state goes under `onboarding-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** all four
  agents are `type: claude`, whose CLI supports a completion **hook**, so the
  config loader *upgrades* `none` back to `hook` and prints a warning at `up`
  (`capture: none on a claude agent ... auto-upgraded to capture: hook.`). Net
  effect: every agent uses its Stop hook — the reliable, event-driven signal.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `buddy` (type: `claude`) — the hub
- **`can_talk_to: [faq, checklist, it_help, user]`** — buddy is the hub: it can
  delegate to all three specialists, and it is the **only agent that can talk to
  `user`**. Keeping the human-facing surface to a single agent is the whole point
  (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity: greet the new hire, ask a couple of scoping
  questions, route to the right specialist, and synthesize **one** answer. On `up`
  this becomes the agent's first prompt, wrapped in a **standby notice** ("no task
  yet — don't send anything, you'll be notified"), so buddy waits for the new
  hire's first message instead of proactively mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).
- **The MAILBOX reminder in the role** spells out the two verbs (read a file to
  receive, write a file into `outbox/<name>/` to send) and re-lists buddy's allowed
  recipients — so even a weak model can follow the protocol. The orchestrator also
  re-injects this on every nudge; the role text just makes intent explicit.

### `faq` (type: `claude`)
- **`can_talk_to: [buddy]`** — the FAQ desk answers **only** back to buddy. It
  cannot reach the new hire or the other specialists.
- **`role`** — policy, benefits, PTO, expenses, hours, remote norms, culture. Its
  standing instruction includes "if this is really an IT or deadline question, say
  so" — so buddy can re-route instead of getting a wrong-desk guess.

### `checklist` (type: `claude`)
- **`can_talk_to: [buddy]`** — reports only to buddy.
- **`role`** — owns a running `CHECKLIST.md` in its workdir: task, owner, due date
  (relative to the start date), and status. It recomputes deadlines when buddy
  supplies the start date, answers "what's due" in due-date order, and marks items
  done when buddy reports completion.

### `it_help` (type: `claude`)
- **`can_talk_to: [buddy]`** — reports only to buddy.
- **`role`** — laptop provisioning, SSO/email accounts, VPN, MFA, tool/repo access,
  "I can't log in to X". It gives numbered steps and **never asks for or repeats
  passwords or secrets** — it points to the self-service reset flow instead.

### What's *not* in this config
- **No `periodically_ping_seconds`.** No agent is auto-nudged on a timer while
  idle — the swarm is purely event-driven off real mail. (If you wanted buddy to
  proactively check in on a slow-onboarding hire, you'd add
  `periodically_ping_seconds: 86400` to it.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/onboarding-buddy.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade for all four claude agents).
2. Creates the runtime dirs (`onboarding-workspace/.agentainer/…`: log, queue, run,
   sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: buddy gets
   `outbox/faq/`, `outbox/checklist/`, `outbox/it_help/`, `outbox/user/`; each
   specialist gets only `outbox/buddy/`.
4. **Installs per-type turn detection** — the Claude Stop hook for all four agents.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified"), so nobody sends unprompted mail.
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'onboarding' is up with 4 agent(s)
:: attach with:  tmux attach -t <buddy-session>
:: you can use the UI with:  agentainer serve -c examples/onboarding-buddy.yaml
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). It **binds `127.0.0.1` by default** — safe,
loopback-only; a remote bind is opt-in and requires a token. See the `README.md`
"control-plane UI" section.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole hub route mail with no API keys — the mechanics are identical.

---

## 4. Drive it: onboard a new hire

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. To *receive* buddy's answers as mail (rather than have them held), turn
yourself available first:

```bash
./agentainer user available -c examples/onboarding-buddy.yaml
```

This rewrites the `user` contact card in buddy's `outbox/user/about.md` to
`Status: available`, so buddy sees you're reachable. (While away, mail to you is
*held* and the sender gets a `system` ack — nothing bounces.)

Now introduce the new hire, addressed to buddy:

```bash
./agentainer send --to buddy \
  "Hi, I'm Priya, starting Monday on the data team, fully remote. What do I need to do in my first week, and when does my laptop arrive?"
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for buddy, then — because the inbox
was empty — **released into `inbox/`** and buddy is **nudged** (the protocol is
re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§5), you'll see the hub advance one turn at a time. Each arrow is
a `stop → sweep → route → release → nudge` cycle:

1. **buddy receives the intro.** It reads `inbox/`, welcomes Priya, and realizes the
   message spans two areas. It writes the start date to `outbox/checklist/`
   ("start date Monday, remote — what's due week one?"). Its turn ends; the
   orchestrator sweeps the outbox, routes to checklist, and nudges checklist.
2. **checklist replies.** It recomputes the deadlines against Monday, writes the
   week-one list into `outbox/buddy/`. On stop, that routes back to buddy.
3. **buddy asks it_help.** Now it addresses the laptop half: it writes
   "when does a remote hire's laptop arrive / how do they set it up?" into
   `outbox/it_help/`. On stop, routes to it_help.
4. **it_help replies** with numbered provisioning steps into `outbox/buddy/`.
5. **buddy synthesizes.** It reads both replies and writes ONE warm message —
   week-one checklist *and* laptop steps — into `outbox/user/`. On stop, that's
   delivered to your `user` mailbox (see it with `agentainer user inbox`, or in the
   UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> A pure-policy question ("what's our PTO policy?") takes the shorter path
> `user → buddy → faq → buddy → user`; buddy only fans out when the question
> actually spans desks.

---

## 5. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/onboarding-buddy.yaml
```

```
swarm: onboarding   root: ./onboarding-workspace
  buddy (claude) up idle queue=0 unread=0 talks=faq, checklist, it_help, user
  faq (claude) up idle queue=0 unread=0 talks=buddy
  checklist (claude) up idle queue=0 unread=1 talks=buddy
  it_help (claude) up idle queue=0 unread=0 talks=buddy
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/onboarding-buddy.yaml          # whole swarm, last 20
./agentainer logs -c examples/onboarding-buddy.yaml -f        # follow live
./agentainer logs checklist -c examples/onboarding-buddy.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox buddy -c examples/onboarding-buddy.yaml
```

Prints the one released message (headers + body), or `buddy: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue buddy -c examples/onboarding-buddy.yaml
```

**Your own mailbox** — the answers buddy has delivered to you:

```bash
./agentainer user inbox -c examples/onboarding-buddy.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach checklist -c examples/onboarding-buddy.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 6. Iterate across the first week

Onboarding is not one message — it's a week. This swarm is built to be poked at
repeatedly, and **conversations resume by default** so the checklist keeper
remembers what's already done.

Tear down at the end of the day:

```bash
./agentainer down -c examples/onboarding-buddy.yaml
```

Bring it back the next morning and pick up where you left off:

```bash
./agentainer up -c examples/onboarding-buddy.yaml     # resume is the default
```

On `up`, Agentainer reads `onboarding-workspace/.agentainer/sessions.yaml` (written
as each agent finished its first turn) and reattaches the recorded conversations
via Claude's native `claude --resume <id>` for each agent. A resumed agent is *not*
re-sent the standby prompt (its prior context is restored), so `checklist` still
knows Priya's start date and which items are done.

Then just keep sending as the week unfolds:

```bash
./agentainer send --to buddy "Priya finished the security-awareness training — mark it done and tell me what's left."
./agentainer send --to buddy "What's our expense policy for a home-office monitor?"
```

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/onboarding-buddy.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 7. Tips & footguns

- **Keep buddy the only `user`-facing agent.** In this config only buddy lists
  `user` in `can_talk_to`. That gives the new hire a single point of contact and a
  clean funnel: raw expert answers always pass through buddy, who turns three
  terse replies into one welcoming message. If a specialist tries to mail `user`
  directly, the orchestrator bounces it (ACL) and drops a `system` note in the
  specialist's inbox explaining who it *can* message — the model self-corrects
  in-band.

- **Give buddy the start date early.** The `checklist` keeper computes deadlines
  *relative to the start date*, so buddy's first job is to learn it and pass it on.
  If you skip it, checklist will ask (via buddy) rather than invent dates.

- **Secrets stay out of the mail.** `it_help` is instructed never to ask for or
  echo passwords, tokens, or secrets — it points the new hire at the self-service
  reset flow. Keep it that way if you customize the role; the mailboxes are plain
  files on disk.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually fires**
  — a `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't
  launch Claude) means completion never triggers and the agent pins "busy" forever.
  `status` showing an agent `busy` for a long time with `unread` mail is the tell.
  Agentainer validates command-vs-type at `up` to catch this early.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **Availability shapes the ending.** If `user` is **away** when buddy finishes,
  its answer is *held* (with a `system` "the user is away" ack to buddy) rather
  than lost — read it later with `agentainer user inbox` or flip yourself available
  and it's delivered.

---

## 8. Customize

- **Add a `security-training` specialist.** Onboarding often has a compliance
  track. Add a fourth spoke and let buddy reach it:

  ```yaml
    - name: security_training
      type: claude
      can_talk_to: [buddy]
      command: "claude --dangerously-skip-permissions"
      role: |
        You are SECURITY TRAINING. buddy forwards you questions about the new
        hire's required security-awareness modules, phishing simulation, data
        handling policy, and completion deadlines. Track who has completed what
        and what remains. Reply only to buddy by writing into outbox/buddy/.
  ```

  Then add `security_training` to **buddy's** `can_talk_to` so the hub can reach
  it. (Names are lowercase, `[A-Za-z0-9_-]`; `security_training` is fine,
  `security-training` also works as an agent name.) You do **not** add buddy to the
  specialist's list beyond `[buddy]` — spokes stay single-edged.

- **Swap models per role.** Nothing forces all four agents onto `claude`. Point a
  cost-sensitive desk at a different CLI by changing its `type` **and** `command`
  together (they must match, or turn-completion never fires):

  ```yaml
    - name: faq
      type: gemini
      capture: pane          # gemini has no completion hook — poll the pane
      command: "gemini --yolo"
      can_talk_to: [buddy]
  ```

  For a `codex` desk use `type: codex` + `command: "codex --yolo"` (hook-based, no
  `capture` override needed). See [`multi-llm-swarm.md`](./multi-llm-swarm.md) for
  mixing model families in one swarm.

- **Tune the ACL.** The default here is deliberately strict (spokes talk only to
  buddy). If your process wants, say, `it_help` and `checklist` to coordinate
  directly on "laptop must arrive before setup task is due", add each to the
  other's `can_talk_to` — but weigh that against the clean single-voice funnel;
  every extra edge is another path the new hire's experience can fragment along.
  Keep `user` on **buddy alone**.

- **Make buddy proactive.** Add `periodically_ping_seconds` to buddy with a
  `periodically_ping_message` like "check whether any onboarding item is overdue;
  if so, ask the new hire" to turn the reactive desk into a gentle daily nudger.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and the two verbs in full.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — how resume keeps the
  checklist's memory across a `down`/`up`.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — the general hub-and-spoke
  `user → orchestrator → worker → user` pattern this swarm specializes.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing claude/codex/gemini/hermes
  in one swarm.
- `examples/onboarding-buddy.yaml` — the config walked through above.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

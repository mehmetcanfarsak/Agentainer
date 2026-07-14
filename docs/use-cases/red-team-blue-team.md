# Use case: Red team / blue team

A concrete, end-to-end walkthrough of the shipped
`examples/red-team-blue-team.yaml` swarm — an adversarial security exercise
where a leashed **red** attacker tries to compromise a scoped, sandboxed target,
a **blue** defender detects, contains, and hardens against those moves, and a
neutral **scorekeeper** tallies the after-action. A **range-control** hub runs
the whole exercise and is the *only* agent that talks to you. Red, blue, and the
scorekeeper never talk to each other — every attack, detection, and score routes
through range-control.

Everything below is based on the actual contents of
`examples/red-team-blue-team.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Security teams, detection-engineering trainers, and anyone running a contained
adversarial exercise — CTF-style labs, purple-team drills, detection-gap
hunts. The swarm encodes the discipline that makes an exercise safe and useful:
the **attacker is leashed** to an explicitly-scoped, disposable sandbox and only
moves on range-control's "GO"; the **defender** reports clean evidence, not
victories; and a **neutral scorekeeper** keeps the tally so the exercise isn't
just two agents arguing. You stay out of the live traffic and only ever hear
from range-control.

It is deliberately a **hub-and-spoke**, not a free-for-all: red and blue are
*opponents* and are never allowed to coordinate or even message each other. All
three spokes report only to range-control, who relays to you. (For the broader
hub-and-spoke discussion, see [`delegation-pipeline.md`](./delegation-pipeline.md).)

---

## 2. The topology

```
   red  ----\
            >-- range-control <--> user
   blue ---/      ^
   scorekeeper ---/
```

Four agents, one directed flow:

1. **`user` → `range-control`** — you send the kickoff ("Run exercise EX-1.
   Target scope is in `TARGET.md` — a sandboxed, disposable lab network. GO.").
2. **`range-control` → `red`** — range-control reads the target scope from
   `TARGET.md` (or your message), briefs red with **one** objective at a time,
   and sends the explicit "GO" only for moves that stay inside the declared
   scope.
3. **`range-control` → `blue`** — range-control tells blue *what objective red is
   working* so blue knows what to hunt for. Red never tells blue directly.
4. **`red` → `range-control`** — red executes the smallest step that proves the
   point and reports success/blocked, then **waits** for the next instruction.
5. **`blue` → `range-control`** — blue reports what it detected, how fast,
   how it contained, and what it hardened (a running `BLUE-LOG.md`).
6. **`range-control` → `scorekeeper`** — as moves resolve, range-control forwards
   each outcome (red's attempt + blue's detection/containment) to be scored.
7. **`scorekeeper` → `range-control`** — the scorekeeper keeps `SCOREBOARD.md` and,
   at exercise end, writes `AFTER-ACTION.md` and delivers the consolidated summary
   to range-control, who relays it to `user`.

The routing above is *enforced* by each agent's `can_talk_to` list. An agent can
only deliver to names on its own list; anything else is bounced back as a
`system` message. Notably, **red, blue, and scorekeeper each list only
`range-control`** — they can never reach each other or you directly.

---

## 3. The config, explained

Here is `examples/red-team-blue-team.yaml` in full:

```yaml
swarm:
  name: red-team-blue-team
  root: ./red-team-blue-team-workspace

defaults:
  capture: none              # mock agents fire no turn-completion hook; real
                             # claude/codex agents get auto-upgraded back to `hook`.
  can_talk_to: []            # tightened per agent below

agents:
  - name: range-control
    type: claude
    can_talk_to: [red, blue, scorekeeper, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are RANGE CONTROL -- the exercise director for a red-team / blue-team
      security simulation. You own the exercise: the target scope, the rules of
      engagement, the sequence of moves, and the abort. You do NOT attack and you
      do NOT defend yourself -- you orchestrate. You are the ONLY agent that talks
      to the human.
      ... (briefs red one objective at a time, tells blue what to hunt for, holds
      the abort, forwards resolved moves to scorekeeper, delivers the summary) ...
      MAILBOX: read new mail in inbox/, act, then move it to read/. To reply, write
      a file into outbox/<name>/ (read outbox/<name>/about.md first) and finish
      your turn. You may message: red, blue, scorekeeper, user.

  - name: red
    type: codex
    can_talk_to: [range-control]
    command: "codex --yolo"
    role: |
      You are the RED TEAM -- the attacker in this exercise. Your job is to devise
      and execute realistic attacks against the EXPLICITLY SCOPED, SANDBOXED,
      DISPOSABLE target that range-control briefs you on. You are a leashed
      attacker: you run exactly the objective range-control sends you, the moment
      range-control sends the "GO", and NOTHING outside the declared scope.
      ... (phishing, CVE exploitation, cred stuffing, lateral movement, priv-esc,
      exfil sim -- smallest step, report, WAIT) ...
      Hard rules: never target anything outside TARGET.md / the brief; never use
      real production systems, credentials, or personal data; if range-control says
      HOLD/ABORT, stop immediately. If a request breaks these rules, refuse.
      MAILBOX: ... You may message: range-control.

  - name: blue
    type: gemini
    can_talk_to: [range-control]
    command: "gemini --yolo"
    role: |
      You are the BLUE TEAM -- the defender in this exercise. Your job is to
      detect, contain, and harden against the RED team's moves on the in-scope
      sandboxed target. You do not attack and you do not score -- you defend and
      you report. ... keep a running BLUE-LOG.md ... report detected / missed /
      contained / hardened / residual-gap. Do not editorialize -- deliver clean
      defensive evidence to range-control.
      MAILBOX: ... You may message: range-control.

  - name: scorekeeper
    type: claude
    can_talk_to: [range-control]
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: "Publish the current running scoreboard and any new lessons to range-control."
        cron: "*/15 * * * *"
        when_busy: queue
    role: |
      You are the SCOREKEEPER -- the neutral judge of this red/blue exercise. You
      do not attack, you do not defend, and you do not talk to the human directly;
      you track what happened and you score it fairly. ... keep a running
      SCOREBOARD.md ... when the exercise ends, write AFTER-ACTION.md (final score,
      what worked, defensive gaps, lessons, open questions) and deliver it to
      range-control. Never favor one side without citing the move's evidence.
      MAILBOX: ... You may message: range-control.
```

Field by field:

### `swarm`
- **`name: red-team-blue-team`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./red-team-blue-team-workspace`** — the parent directory for the
  agents' working directories and mailboxes. Each agent's workdir defaults to
  `red-team-blue-team-workspace/<name>`, so they're all **private** (no shared
  workdir here, unlike the data-pipeline example — see
  [`custom-workspace.md`](./custom-workspace.md)). Orchestrator state goes under
  `red-team-blue-team-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-capture mode. It's there so the
  key-free mock run comes up cleanly (mock bash loops don't fire completion
  hooks). For the *real* `claude`/`codex` agents below, the config loader
  auto-upgrades `none` → `hook` (you'll see three `capture: none on a <type>
  agent ... auto-upgraded to capture: hook` warnings on `validate`), restoring
  their natural completion signal. `blue` is `gemini` and is the exception — see
  its note below.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `range-control` (type: `claude`)
- **`can_talk_to: [red, blue, scorekeeper, user]`** — the hub: it delegates to
  all three spokes and is the **only agent that can talk to `user`**. Keep the
  human-facing surface to this single agent.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity: exercise director who owns scope, the GO,
  and the abort, and relays the summary to you. On `up` this becomes the agent's
  first prompt, wrapped in a **standby notice** so range-control waits for your
  kickoff instead of proactively mailing peers.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `red` (type: `codex`)
- **`can_talk_to: [range-control]`** — red only reports back to range-control. It
  cannot reach `blue`, `scorekeeper`, or `user`; it is deliberately isolated from
  its opponent so no attack or hardening lands without range-control's go.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — the leashed attacker: runs exactly the one objective it's given,
  does the smallest proving step, reports success/blocked, then **waits**. The
  hard rules (scope, no real creds/systems, stop on HOLD/ABORT, refuse illegal
  asks) are baked into the role so the model self-polices in-band.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `blue` (type: `gemini`)
- **`can_talk_to: [range-control]`** — blue only reports back to range-control,
  never to red or the scorekeeper.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **`role`** — the defender: hunts for the objective range-control named, keeps a
  running `BLUE-LOG.md`, and reports clean evidence (detected/missed/contained/
  hardened/residual-gap).
- **Turn detection — important footgun.** `gemini`'s natural capture mode is
  **`pane`** (pane polling), *not* `hook`. Because `defaults.capture: none` only
  auto-upgrades `claude`/`codex` (hook types) back to `hook`, `blue` keeps
  `capture: none` — the orchestrator gets **no** turn-completion signal for it and
  flags it `silent-but-alive` in `status`. A `silent-but-alive` agent's outgoing
  mail isn't auto-swept, so for a **real gemini run** set `blue:
  capture: pane` (pane polling via the watcher) so blue's detections get routed.
  The key-free mock loop is the only scenario where `capture: none` is fine for
  blue. (The data-pipeline doc flags the same: a `gemini` agent needs
  `capture: pane`.)

### `scorekeeper` (type: `claude`)
- **`can_talk_to: [range-control]`** — the scorekeeper reports only to
  range-control; it's a neutral judge, never your direct contact.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **`pings:`** — a scheduled ping (see the pings note below).
- **Turn detection:** `claude` → Stop hook.

### The `pings` on `scorekeeper`
```yaml
pings:
  - message: "Publish the current running scoreboard and any new lessons to range-control."
    cron: "*/15 * * * *"
    when_busy: queue
```
A **cron-scheduled nudge**. Every 15 minutes (`*/15 * * * *`) the orchestrator
drops this message into the scorekeeper's `inbox/` and nudges it — so the running
tally stays fresh even when no move is in flight. `when_busy: queue` means that
if the scorekeeper is mid-turn when the cron fires, the ping is **queued** behind
the one released message and delivered later, rather than dropped. Real mail (a
resolved move from range-control) always takes priority over a ping. See
[`configuration.md`](../configuration.md) for the full `pings` schema
(`when_busy: skip | queue`).

### What's *not* in this config
- **No shared `workdir`.** All four agents get private directories, so there's no
  mailbox namespacing — every `inbox/ outbox/ read/ sent/ failed/` is plainly
  named under each agent's folder.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on.
- **`capture: none` is a mock/demo default, not a recommendation for real
  gemini.** As noted, add `capture: pane` to `blue` for a real run.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/red-team-blue-team.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the three `capture` auto-upgrade
   warnings (for `range-control`, `red`, `scorekeeper` — `blue` is intentionally
   left at `none`; set `capture: pane` for a real gemini run).
2. Creates the runtime dirs
   (`red-team-blue-team-workspace/.agentainer/…`: log, queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/about.md`
   contact card *is* the ACL made visible: range-control gets `outbox/red/`,
   `outbox/blue/`, `outbox/scorekeeper/`, `outbox/user/`; red/blue/scorekeeper each
   get just `outbox/range-control/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `range-control`
   and `scorekeeper`, the Codex `notify` hook for `red`. (`blue`/`gemini` gets no
   wiring at `capture: none`; see §3.)
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles
   stale/dead/silent agents (so `blue`'s `silent-but-alive` state is surfaced, not
   hidden) so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'red-team-blue-team' is up with 4 agent(s)
:: attach with:  tmux attach -t <range-control-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/red-team-blue-team.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents, so it must **never** be exposed on `0.0.0.0` without a token.
See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole exercise route mail with no API keys — the mechanics are identical.

---

## 5. Drive the exercise

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the final summary as mail (rather than have it
held), turn yourself available first:

```bash
./agentainer user available -c examples/red-team-blue-team.yaml
```

This rewrites the `user` contact card in range-control's `outbox/user/about.md`
to `Status: available`, so range-control sees you're reachable. (While away, mail
to you is *held* and range-control gets a `system` ack — nothing bounces.)

Now send the kickoff into the swarm, addressed to range-control:

```bash
./agentainer send -c examples/red-team-blue-team.yaml --to range-control \
  "Run exercise EX-1. Target scope is in TARGET.md -- a sandboxed, disposable lab \
   network. GO."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for range-control, then — because the
inbox was empty — **released into `inbox/`** and range-control is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list: red,
blue, scorekeeper, user).

### The exercise flowing

Watching the log (§6), you'll see the exercise advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **range-control receives the kickoff.** It reads `inbox/` (or `TARGET.md`),
   sets the scope, and writes a one-objective brief + "GO" into `outbox/red/` and
   a "here's what red is working" note into `outbox/blue/`. On stop, both route.
2. **red attacks; blue defends.** Red reads its inbox, does the smallest proving
   step, reports to `outbox/range-control/`, then waits. Blue reads its inbox,
   hunts, and reports evidence to `outbox/range-control/`. On each stop, mail
   routes to range-control.
3. **range-control forwards to the scorekeeper.** It writes each resolved outcome
   into `outbox/scorekeeper/`. On stop, that routes to the scorekeeper.
4. **scorekeeper tallies.** It updates `SCOREBOARD.md` and, every 15 minutes, the
   `pings` cron also nudges it to publish the running board. At exercise end,
   range-control tells it to write `AFTER-ACTION.md`, which it delivers to
   `outbox/range-control/`.
5. **range-control relays the summary.** It reads the after-action and writes the
   consolidated summary into `outbox/user/`. On stop, that's delivered to your
   `user` mailbox (visible with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion. (If
`blue` is left at `capture: none`, its replies won't auto-route; set
`capture: pane` as noted in §3.)

> If you *don't* send a kickoff, the agents just sit in standby (that's the point
> of the standby prompt). The exercise only moves when real mail — or the
> scorekeeper's ping — arrives.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, the ACL, and
any `silent-but-alive` flags:

```bash
./agentainer status -c examples/red-team-blue-team.yaml
```

```
swarm: red-team-blue-team   root: ./red-team-blue-team-workspace
  range-control (claude) up idle queue=0 unread=0 talks=red, blue, scorekeeper, user
  red          (codex)  up idle queue=0 unread=1 talks=range-control
  blue         (gemini) up idle queue=0 unread=0 talks=range-control  [silent-but-alive]
  scorekeeper  (claude) up idle queue=0 unread=0 talks=range-control
supervisor: alive
```

(`blue` shows `silent-but-alive` precisely because its `capture: none` leaves the
orchestrator without a turn signal — expected unless you set `capture: pane`.)

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/red-team-blue-team.yaml          # whole swarm, last 20
./agentainer logs -c examples/red-team-blue-team.yaml -f        # follow live
./agentainer logs red -c examples/red-team-blue-team.yaml       # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
`ping`, etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox range-control -c examples/red-team-blue-team.yaml
```

Prints the one released message (headers + body), or `range-control: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue red -c examples/red-team-blue-team.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach blue -c examples/red-team-blue-team.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

**The lab artifacts** — red, blue, and the scorekeeper can write their evidence
files (`BLUE-LOG.md`, `SCOREBOARD.md`, `AFTER-ACTION.md`) into their own workdirs
under `red-team-blue-team-workspace/`. Inspect them there as the exercise unfolds.

---

## 7. Iterate on the exercise

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the exercise mid-flight through the `user` mailbox or by sending
notes into an agent's inbox.

- **Hold or abort a move.** Realized red is drifting out of scope? Send
  range-control a HOLD: `./agentainer send --to range-control -c
  examples/red-team-blue-team.yaml "HOLD red's current move — it's probing outside
  TARGET.md. Confirm it stopped."` range-control relays the abort to red.
- **Re-brief the defender.** `./agentainer send --to range-control ... "Tell blue to
  also watch for credential stuffing on the lab's auth log."` — range-control
  forwards it to blue; red never sees it.
- **Ask the scorekeeper for the evidence.** `./agentainer send --to range-control ...
  "Have the scorekeeper cite the exact move behind blue's last point."` — routed
  through the hub.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're done (or want to try a different scope), tear it down:

```bash
./agentainer down -c examples/red-team-blue-team.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/red-team-blue-team.yaml     # resume is the default
```

On `up`, Agentainer reads `red-team-blue-team-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for
range-control and the scorekeeper, `codex resume <id>` for red. A resumed agent
is *not* re-sent the standby prompt (its prior context — the running scope,
scoreboard, blue-log — is restored).

**One caveat:** `blue` is `gemini`, and gemini has **no recoverable session id**
from a scraped pane, so it has no resume recipe — on resume it comes up fresh
(its `BLUE-LOG.md` file on disk persists, but its in-pane context doesn't). The
`claude`/`codex` agents resume fully. Pass `--no-resume` to force everyone fresh.
Inspect what's recorded with:

```bash
./agentainer sessions -c examples/red-team-blue-team.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a white-cell / observer
An exercise often wants a neutral facilitator who can watch both sides and talk
to you directly. Add a fifth agent that can read the scorekeeper's board and own
comms:

```yaml
  - name: white-cell
    type: claude
    can_talk_to: [range-control, scorekeeper, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the WHITE CELL -- the neutral facilitator. You observe via
      range-control and the scorekeeper, keep the human informed of exercise
      health, and never influence attacks or defenses. You may message:
      range-control, scorekeeper, user.
```
Then add `white-cell` to range-control's and scorekeeper's `can_talk_to`.

### Tighten or loosen red's leash
Red's scope discipline is already enforced in its `role` (it refuses out-of-scope
asks and stops on HOLD/ABORT). If you want an even stricter gate, have
range-control require red to *state its intended step and wait for explicit
confirmation* before executing — already implied by "do the smallest step that
proves the point, report, then WAIT." Remember: the `can_talk_to` ACL is
**cooperative, not OS isolation** (Decision D15) — it can't *enforce* safety; the
role text and your scope file (`TARGET.md`) do. Keep red's target a real isolated
lab.

### Swap models / fix blue's capture
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- For a **real gemini run**, set `blue: capture: pane` so its turn completion is
  detected by pane polling and its detections get auto-routed.
- Put red on a different model (`type: hermes`) — but then add `capture: pane`
  (hermes has no completion hook either).
- Mixed families are fine here: claude (range-control, scorekeeper) + codex (red)
  + gemini (blue) is exactly the shipped mix. See
  [`multi-llm-swarm.md`](./multi-llm-swarm.md) for mixing model families safely.

### Tune the ACL
- To let the **scorekeeper** escalate straight to `user` (not only via
  range-control), add `user` to its `can_talk_to`. Mind that this widens the
  human-facing surface; the doc's convention keeps range-control the sole `user`
  contact.
- Red and blue must **never** be allowed to talk to each other — leaving both at
  `can_talk_to: [range-control]` is the isolation guarantee that makes this an
  exercise rather than a chat.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for broader hub-and-spoke
  routing patterns.

### Adjust the scorekeeper's ping
The `*/15 * * * *` cadence is aggressive for a slow exercise. Widen it
(`0 * * * *` = hourly) or set `when_busy: skip` if you'd rather a ping never
queues behind live scoring. Full schema in
[`configuration.md`](../configuration.md).

---

## 10. Tips & footguns

- **Keep range-control the only `user`-facing agent.** Only range-control lists
  `user` in `can_talk_to`. That gives you a single funnel: raw attack steps and
  scores always pass through review before they reach you. If red, blue, or the
  scorekeeper tries to mail `user` directly, the orchestrator bounces it (ACL) and
  drops a `system` note in their inbox explaining who they *can* message — the
  model self-corrects in-band.

- **Red's leash is a role contract, not an OS boundary.** The `can_talk_to` ACL
  is cooperative (Decision D15): red *could* write straight into blue's `inbox/`
  on the filesystem, bypassing `outbox/`. It's enforced for well-behaved agents and
  documented honestly. The real safety comes from the role text (scope, no real
  creds/systems, stop on HOLD) plus your `TARGET.md` pointing at a genuine
  sandboxed lab. Never point red at anything you care about.

- **`blue` needs `capture: pane` for a real run.** With the shipped
  `defaults.capture: none`, `blue` (`gemini`) is left `silent-but-alive` and its
  outgoing mail is not auto-swept — its detections would sit in `outbox/range-control/`
  unrouted. Set `capture: pane` on `blue` (or run the key-free mock loop). The
  `claude`/`codex` agents are fine as-is because `none` auto-upgrades to `hook`.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion (unless
  `blue` is at `capture: none`). If an agent seems stuck, check that its **turn
  detection actually fires** — a `type`/`command` mismatch (e.g. a `claude` agent
  whose `command` doesn't launch Claude) means completion never triggers and the
  agent pins "busy" forever. `status` showing an agent `busy` for a long time with
  `unread` mail is the tell.

- **The scorekeeper's ping keeps the tally alive.** Even when no move is in flight,
  the `*/15` cron nudges the scorekeeper to publish the running board. With
  `when_busy: queue` it never interrupts live scoring.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops between range-control and a spoke.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime + mailboxes)
  and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/red-team-blue-team.yaml
  ./agentainer remove-session -c examples/red-team-blue-team.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' evidence files (`BLUE-LOG.md`,
  `SCOREBOARD.md`, `AFTER-ACTION.md`) in their workdirs or your config.

- **Availability shapes the ending.** If `user` is **away** when range-control
  finishes, your summary is *held* (with a `system` "the user is away" ack to
  range-control) rather than lost — read it later with `agentainer user inbox` or
  flip yourself available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing/ACL works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop (incl. gemini's no-resume caveat).
- [`configuration.md`](../configuration.md) — `can_talk_to`, `capture`, and the `pings` schema.
- [`cli-reference.md`](../cli-reference.md) — commands and the type/command mismatch wedge.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families (claude/codex/gemini) safely.
- [`ui-guide.md`](../ui-guide.md) — the mail-app control plane.
- `examples/red-team-blue-team.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14, capture §8 / D17).

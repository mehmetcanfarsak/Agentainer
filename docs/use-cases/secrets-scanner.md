# Use case: Secrets scanner

A concrete, end-to-end walkthrough of the shipped
`examples/secrets-scanner.yaml` swarm — a hub-and-spoke team that hunts for
hardcoded secrets in a codebase and tells humans how to kill them. A **scanner**
(claude) takes a repo or path from you and fans out to two specialists: a
**detector** (codex) that finds hardcoded keys/tokens/passwords/certs, and a
**remediator** (gemini) that turns each finding into rotation + secrets-manager
guidance. The spokes report only to the scanner, and the scanner is the only
agent that reaches you. The whole swarm is built around one rule: **never echo
the secret** — report the type, location, and what to do about it, never the
value.

Everything below is based on the actual contents of
`examples/secrets-scanner.yaml` and the shipped CLI (`lib/cli.py`) and mailroom
(`lib/mail.py`). No API keys are needed to understand the mechanics; to run it
*for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Security-minded engineers, platform/DevSecOps teams, and anyone who wants a
repeatable, hands-off pass over a repository (or a nightly cron sweep) that
surfaces leaked credentials and hands back an actionable remediation plan
instead of a raw grep dump. The swarm encodes the discipline that makes secret
findings safe: a single coordinator owns the report, a detector that *only finds*
(never fixes), and a remediator that always recommends rotation + a vault move
and **never prints the secret itself**.

It is deliberately a **hub-and-spoke**, not a free-for-all: the detector and
remediator never talk to each other or to the human directly — every finding and
every remediation note passes through the scanner, so the human-facing report
has exactly one author and one place to enforce the "no secret in the output"
rule.

---

## 2. The topology

```
          user
            |
         scanner                  (the hub: talks to detector, remediator, user)
          /    \
   detector   remediator
   (codex)     (gemini)
   (finds)     (remediates)
```

Three agents, one directed flow:

1. **`user` → `scanner`** — you send a repo or path to scan.
2. **`scanner` → `detector`** — the scanner acknowledges the target and asks for
   a precise scan (exact path, what counts as a secret, any scope limits).
3. **`detector` → `scanner`** — the detector returns findings as `file:line` +
   **secret TYPE** (value redacted to a 2-4 char prefix + `(...redacted...)`).
4. **`scanner` → `remediator`** — the scanner forwards *each* finding (type +
   location, value still redacted) and asks for rotation + secrets-manager
   guidance.
5. **`remediator` → `scanner`** — the remediator returns a tightly scoped note:
   rotate now, move it to an env var / vault / managed store, and the PR to make.
   The value is referenced only by type and location.
6. **`scanner` → `user`** — the scanner assembles one human-readable report:
   per finding the `file:line`, the type (never the value), the rotation steps,
   where to move it, and the PR, then delivers it to you. If the detector finds
   nothing, it tells you the target is clean.

The routing above is *enforced* by each agent's `can_talk_to` list. The detector
and remediator can **only** deliver to the scanner — there is no path from either
spoke to `user`, and they cannot coordinate with each other. Anything outside an
agent's ACL is bounced back as a `system` message and filed in `failed/` (see
§7). Notably, the detector and remediator **never** talk to `user` directly — only
the scanner does.

---

## 3. The config, explained

Here is `examples/secrets-scanner.yaml` in full:

```yaml
swarm:
  name: secrets-scanner
  root: ./secrets-scanner-workspace

defaults:
  capture: none
  can_talk_to: []

agents:
  - name: scanner
    type: claude
    can_talk_to: [detector, remediator, user]
    command: "claude --dangerously-skip-permissions"
    # A nightly sweep so a stale repo is re-checked even when nobody asks.
    # `when_busy: queue` means a scan that lands while the scanner is mid-turn
    # runs as soon as it frees up -- never silently drop a security sweep.
    pings:
      - message: |
          Nightly sweep: pick the last repo/path you were given (or the default
          scan target configured for this swarm) and run a fresh secrets scan.
          Fan out to the detector and remediator, then summarize new/changed
          findings back to the user. Do not print any secret value.
        cron: "0 2 * * *"              # 02:00 every night
        when_busy: queue
    role: |
      You are the SCANNER, the orchestrating hub of a secrets-detection swarm.
      You NEVER hunt for or remediate secrets yourself -- you coordinate.
      When the user gives you a repo or path to scan: (1) acknowledge the target
      and send the detector a precise scan request (the exact repo/path, what
      counts as a secret, and any scope limits). (2) When the detector returns
      findings (file:line + type), forward each one to the remediator and ask
      for a rotation + secrets-manager note. (3) When the remediator replies,
      assemble a single human-readable report: per finding, the file:line, the
      secret TYPE (never the value), the rotation steps, where to move it (env
      var / vault / secrets manager), and the PR to make. (4) Send that report to
      the user. Aggregate and de-duplicate; do not pass secret values onward.
      If the detector finds nothing, tell the user the target is clean.
      MAILBOX: read new mail in inbox/, act, then move it to read/. To reply,
      write a file into outbox/<name>/ (read outbox/<name>/about.md first) and
      finish your turn. You may message: detector, remediator, user.

  - name: detector
    type: codex
    can_talk_to: [scanner]
    command: "codex --yolo"
    role: |
      You are the DETECTOR, a secrets-finding specialist. You receive a repo or
      path from the scanner and hunt for hardcoded secrets: API keys, access
      tokens / bearer tokens, OAuth client secrets, private keys and certs,
      passwords and connection strings, .env files, credentials in config, and
      secrets leaked into git history or CI logs. Use pattern matching, secret
      scanners, and history inspection as needed. For every hit report exactly:
      the file:line, the SECRET TYPE, and a short reason it is a secret. REDACT
      the value -- show at most a 2-4 character prefix and "(...redacted...)",
      never the full token. Do not "fix" anything or edit files; your only job is
      to find and report. If the target is clean, say so plainly.
      MAILBOX: read new mail in inbox/, act, then move it to read/. To reply,
      write a file into outbox/<name>/ (read outbox/<name>/about.md first) and
      finish your turn. You may message: scanner.

  - name: remediator
    type: gemini
    can_talk_to: [scanner]
    command: "gemini"
    role: |
      You are the REMEDIATOR, a secrets-remediation specialist. For each finding
      the scanner forwards (file:line + type, value REDACTED), write a tightly
      scoped remediation note: (1) ROTATE the secret now -- assume it is
      compromised the moment it was committed; state the rotation steps. (2) Move
      it out of code: to an environment variable, a vault (e.g. HashiCorp Vault),
      or a managed secrets store; give the concrete steps and the new reference
      in code (e.g. read from env / vault path). (3) The PR to make: remove the
      hardcoded value, add a .gitignore / secret-scanning pre-commit, and
      force-rotate if history is exposed. NEVER print or reconstruct the secret
      value -- reference it only by type and location. If you lack the value,
      say what the human must supply. Keep each note actionable and short.
      MAILBOX: read new mail in inbox/, act, then move it to read/. To reply,
      write a file into outbox/<name>/ (read outbox/<name>/about.md first) and
      finish your turn. You may message: scanner.
```

Field by field:

### `swarm`
- **`name: secrets-scanner`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./secrets-scanner-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent's workdir defaults to
  `secrets-scanner-workspace/<name>` (scanner, detector, remediator). Orchestrator
  state goes under `secrets-scanner-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.
- **`capture: none`** — the default turn-capture mode. This is *overridden by the
  loader* for the two agents that have a completion hook (see per-agent notes
  below); the `gemini` remediator keeps `capture: none`. The loader never lets a
  `claude`/`codex` agent run truly uncaptured, because that would blind the
  orchestrator to its turns.

### `scanner` (type: `claude`)
- **`can_talk_to: [detector, remediator, user]`** — the scanner is the hub: it
  delegates to both specialists and is the **only agent that can talk to
  `user`**. Keeping the human-facing surface to a single agent is what enforces
  the "report never contains a secret value" rule — every word to you is framed
  by the scanner.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the scanner waits for your scan request instead of
  proactively mailing peers. The role is where the no-echo rule lives: report
  type, never value.
- **Turn detection:** `claude` → a **Stop hook**. The config literally says
  `capture: none`, but the loader auto-upgrades it to `capture: hook` with a
  warning ("capture: none on a claude agent gives the orchestrator no
  turn-completion signal -- auto-upgraded to capture: hook."), because a claude
  agent with no hook would wedge the swarm.
- **`pings`** — a nightly cron (see §4).

### `detector` (type: `codex`)
- **`can_talk_to: [scanner]`** — the detector only reports back to the scanner.
  It cannot reach the remediator or the `user`; the findings have exactly one
  recipient.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "hunt for hardcoded secrets; for every hit report `file:line`,
  the **TYPE**, and a reason; REDACT the value to a 2-4 char prefix +
  `(...redacted...)`; never fix or edit files." The redaction instruction is the
  detector's half of the no-echo contract.
- **Turn detection:** `codex` → a `notify` program (its hook). Same auto-upgrade
  from `capture: none` to `capture: hook` as the scanner (a `codex` agent with no
  hook would also wedge).

### `remediator` (type: `gemini`)
- **`can_talk_to: [scanner]`** — the remediator only reports back to the scanner.
  It cannot reach the detector or the `user`; the remediation notes have exactly
  one recipient.
- **`command: "gemini"`** — placeholder launch command. (Note: gemini has no
  completion hook, unlike claude/codex.)
- **`role`** — "for each forwarded finding (value redacted) write a tightly
  scoped note: rotate now, move it to an env var / vault / managed store, and the
  PR to make; NEVER print or reconstruct the secret value — reference it only by
  type and location." This is the remediator's half of the no-echo contract.
- **Turn detection:** `gemini` → **`capture: none` stays as written.** gemini has
  no completion hook; the natural mode would be `capture: pane` (pane polling),
  but this config leaves it at `none`. Practically that means the orchestrator
  has **no turn-completion signal** for the remediator and marks it
  "silent-but-alive" in the supervisor — see the footgun in §10.

### ACL enforcement

`can_talk_to` is the cooperative boundary the orchestrator enforces on every
outbound message. When an agent writes a file into `outbox/<name>/`, the
mailroom's `route_outbound` checks the sender's ACL: if `<name>` is on its list
the message is delivered (moved to `sent/`); otherwise it is **bounced** — a
`system` note explaining the allowed recipients is dropped in the sender's inbox
and the file is moved to `failed/`. So even though agents have filesystem access
and *could* technically write into another agent's `inbox/`, the intended path is
the `outbox/` + ACL check, and a mistaken direct send self-corrects in-band. The
detector and remediator literally cannot reach `user`; only the scanner's
`outbox/user/` folder exists.

### What's *not* in this config
- **No shared workdir.** Each agent has its own `secrets-scanner-workspace/<name>`
  directory, so mailboxes are created unprefixed (no namespacing). See
  [`custom-workspace.md`](./custom-workspace.md) for the shared-workdir case.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **No `capture` override on any agent.** The loader's per-type knowledge drives
  detection: claude/codex auto-upgrade `none`→`hook`; gemini keeps `none`. If you
  want the remediator's turns tracked (so its replies auto-route), you'd add
  `capture: pane` — see §10.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/secrets-scanner.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the capture-upgrade warnings for the
   `scanner` and `detector` ("capture: none on a claude/codex agent … auto-upgraded
   to capture: hook.").
2. Creates the runtime dirs (`secrets-scanner-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/about.md`
   contact card *is* the ACL made visible: the scanner gets `outbox/detector/`,
   `outbox/remediator/`, `outbox/user/`; the detector and remediator each get only
   `outbox/scanner/`.
4. **Installs per-type turn detection** — the Claude Stop hook for the `scanner`
   and the Codex `notify` hook for the `detector`; the `remediator` runs
   uncaptured (gemini, `capture: none`).
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents (it will note the `remediator` as "silent-but-alive") so one
   uncaptured agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'secrets-scanner' is up with 3 agent(s)
:: attach with:  tmux attach -t <scanner-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/secrets-scanner.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents (some of which run with elevated permissions), so it must
**never** be exposed on `0.0.0.0` without a token. See
[`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole scan→find→remediate→report flow route mail with no API keys — the
> mechanics are identical.

### The nightly ping (cron)

The `scanner` has a `pings:` block with a single rule:

```yaml
    pings:
      - message: |
          Nightly sweep: pick the last repo/path you were given (or the default
          scan target configured for this swarm) and run a fresh secrets scan.
          Fan out to the detector and remediator, then summarize new/changed
          findings back to the user. Do not print any secret value.
        cron: "0 2 * * *"              # 02:00 every night
        when_busy: queue
```

This is a **cron-scheduled ping**, not event-driven mail. The supervisor's cron
engine evaluates it each cycle: at `02:00` every day, if the scanner is **idle**,
the message is dropped into its inbox and it's nudged to run a fresh sweep
(reruns detection + remediation and re-reports to you). If the scanner is **busy**
mid-turn when the cron fires, `when_busy: queue` means the ping is *queued* and
delivered the moment it frees up — a security sweep is never silently dropped.
This is what keeps a stale repo re-checked even when nobody asks. (Pings take
lower priority than real queued mail, so a human-sent scan still wins.)

---

## 5. Drive a scan

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the scanner's finished report as mail (rather
than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/secrets-scanner.yaml
```

This rewrites the `user` contact card in the scanner's `outbox/user/about.md` to
`Status: available`, so the scanner sees you're reachable. (While away, mail to
you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the repo/path to scan into the swarm, addressed to the scanner:

```bash
./agentainer send --to scanner -c examples/secrets-scanner.yaml \
  "Scan /srv/app/repo for secrets and report findings."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the scanner, then — because its
inbox was empty — **released into `inbox/`** and the scanner is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list:
`detector, remediator, user`).

### The mail flowing

Watching the log (§6), you'll see the scan advance one turn at a time. Each arrow
is a `stop → sweep → route → release → nudge` cycle:

1. **scanner receives the request.** It reads `inbox/`, acknowledges the target,
   and writes a precise scan request into `outbox/detector/`. On stop, that routes
   to the detector.
2. **detector hunts.** It reads its inbox, scans, and reports `file:line` + type
   with the value redacted, into `outbox/scanner/`. On stop (codex `notify` hook),
   that routes back to the scanner.
3. **scanner fans out to remediation.** It forwards each finding (value still
   redacted) into `outbox/remediator/`. On stop, that routes to the remediator.
4. **remediator writes notes.** It reads its inbox and writes a rotation +
   secrets-manager note into `outbox/scanner/`. **See §10 — the remediator runs
   uncaptured, so its reply is not auto-routed by the orchestrator unless you
   enable `capture: pane`.**
5. **scanner finalizes.** Once it has the findings + notes, it assembles one
   report (type, never value) and writes it into `outbox/user/`. On stop, that's
   delivered to your `user` mailbox (visible with `agentainer user inbox`, or in
   the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion, except
where §10 notes the gap.

> The scanner also runs on its own via the nightly cron ping (§4) when you don't
> send anything — that's the "set and forget" security sweep.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/secrets-scanner.yaml
```

```
swarm: secrets-scanner   root: ./secrets-scanner-workspace
  scanner    (claude) up idle queue=0 unread=0 talks=detector, remediator, user
  detector   (codex)  up idle queue=0 unread=1 talks=scanner
  remediator (gemini) up idle queue=0 unread=0 talks=scanner
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/secrets-scanner.yaml          # whole swarm, last 20
./agentainer logs -c examples/secrets-scanner.yaml -f        # follow live
./agentainer logs detector -c examples/secrets-scanner.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
plus the supervisor's `silent-but-alive` event for the uncaptured remediator —
one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox scanner -c examples/secrets-scanner.yaml
```

Prints the one released message (headers + body), or `scanner: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue scanner -c examples/secrets-scanner.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach detector -c examples/secrets-scanner.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

**The scanned repo** — the detector's findings reference real `file:line` paths
in the target you supplied (`/srv/app/repo` in the example). Inspect the reported
files there; nothing in the swarm edits them (the detector "only finds", the
remediator only advises).

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or (more directly)
by sending notes into an agent's inbox.

- **Narrow the scope.** Realized you only care about AWS keys? `./agentainer send
  --to scanner -c examples/secrets-scanner.yaml "Re-scan but only flag AWS access
  keys and private keys; ignore test fixtures under ./tests."` The scanner relays
  the tighter spec to the detector.
- **Ask for more detail on a finding.** `./agentainer send --to scanner ... "For
  the finding in config/database.yml, have the remediator also specify the exact
  vault path and IAM policy."` — the scanner forwards it.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different target), tear it down:

```bash
./agentainer down -c examples/secrets-scanner.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/secrets-scanner.yaml     # resume is the default
```

On `up`, Agentainer reads `secrets-scanner-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the
scanner, `codex resume <id>` for the detector. (The gemini remediator has no
recoverable session id from a scraped pane, so it always starts fresh — see
`lib/config.py`.) A resumed agent is *not* re-sent the standby prompt (its prior
context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/secrets-scanner.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a "watchtower" that alerts on new secrets
Once the scanner reports, you may want someone watching the repo for *future*
commits that reintroduce secrets. Add a fourth agent that can read the scanner's
deliverable and owns alerting:

```yaml
  - name: watchtower
    type: claude
    can_talk_to: [scanner, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the SECRETS WATCHTOWER. Once the scanner delivers a report, define
      the pre-commit secret-scanning gate and CI check that should block future
      commits, and report the runbook to outbox/user/. You never scan or remediate
      yourself.
```
Then add `watchtower` to the scanner's `can_talk_to` so it can be briefed.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `detector: type: hermes` (or `gemini`/`claude`) to put detection on a different
  model than the scanner.
- `remediator: type: claude` if you want remediation on Claude while the detector
  stays Codex.
- Remember: `gemini`/`hermes` need a capture mode (this config leaves the
  remediator at `capture: none`; set `capture: pane` to track its turns — see
  §10).

### Tune the ACL
- To let the **detector** escalate a critical finding straight to `user` (not only
  via the scanner), add `user` to its `can_talk_to`. Mind that this widens the
  human-facing surface and bypasses the scanner's no-echo framing — the doc's
  convention keeps the scanner the sole `user` contact so every report is
  sanitized.
- To make the remediator unreachable from anyone but the scanner (already the case
  here), leave its `can_talk_to: [scanner]` — that's the one-place-owns-the-report
  guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md)
  for mixing model families safely (this swarm already mixes claude + codex +
  gemini).

---

## 10. Tips & footguns

- **Keep the scanner the only `user`-facing agent.** Only the scanner lists
  `user` in `can_talk_to`. That gives you a single funnel where the no-echo rule
  is enforced: every word to you is framed by the scanner, which only ever prints
  the *type* and *location*, never the value. If the detector or remediator tried
  to mail `user` directly, the orchestrator bounces it (ACL) and drops a `system`
  note in their inbox explaining who they *can* message — the model self-corrects
  in-band. The redaction instruction is duplicated in *both* spokes' roles as
  defense in depth, but the ACL is the structural guarantee.

- **The `capture: none` default matters, and the loader overrides it for claude/
  codex.** `defaults.capture: none` would leave every agent blind to its own turns.
  The loader auto-upgrades the `scanner` (claude) and `detector` (codex) to their
  natural `hook` capture — without that, completion never fires and the agent pins
  "busy" forever (the validate output literally warns you). The **`remediator`
  (gemini) keeps `capture: none`** because gemini has no completion hook and this
  config does not opt into pane polling. The consequence: the orchestrator has
  **no turn-completion signal** for the remediator, so its outbound sweep never
  runs automatically and its remediation replies would sit in `outbox/scanner/`
  until you do something about it. The supervisor marks it `silent-but-alive` to
  surface this. **To close the loop, set `capture: pane` on the remediator** (pane
  polling) so the orchestrator can detect its turns and route its notes back to
  the scanner. This is the one piece of the shipped config you'll most likely want
  to add for a fully autonomous scan→remediate→report.

- **Redaction is a model instruction, not a system guarantee.** The detector and
  remediator are *told* to redact (2-4 char prefix + `(...redacted...)`) and the
  scanner is told never to pass values onward, but a forgetful model could still
  leak a value in prose. The ACL prevents a spoke from reaching `user` directly,
  and the scanner is the last line of sanitization — review the scanner's report
  before trusting it, and consider a `system`-injected reminder if you see a leak.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch (e.g. a `claude` agent whose `command`
  doesn't launch Claude) means completion never triggers and the agent pins
  "busy" forever. `status` showing an agent `busy` for a long time with `unread`
  mail is the tell. (The remediator's `capture: none` is the *opposite* problem —
  it never reports busy, so it looks idle while its replies wait unswept.)

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **The nightly ping never drops a sweep.** `when_busy: queue` on the scanner's
  cron ping means if `02:00` lands while the scanner is mid-turn, the sweep is
  queued and run the moment it frees up — a security re-check is never silently
  lost. Real `user` mail still takes priority over the ping.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/secrets-scanner.yaml
  ./agentainer remove-session -c examples/secrets-scanner.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the scanned target repo or your config.

- **Availability shapes the ending.** If `user` is **away** when the scanner
  finishes, your report is *held* (with a `system` "the user is away" ack to the
  scanner) rather than lost — read it later with `agentainer user inbox` or flip
  yourself available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely (this swarm uses claude + codex + gemini).
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- [`configuration.md`](../configuration.md) — full config reference, including `pings:`/`cron` and `capture`.
- [`security-audit.md`](./security-audit.md) — a related single-agent security use case.
- `examples/secrets-scanner.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14, pings/cron §cron).

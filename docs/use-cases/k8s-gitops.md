# Use case: Kubernetes / GitOps

A concrete, end-to-end walkthrough of the shipped
`examples/k8s-gitops.yaml` swarm — a Helm/ArgoCD chart-review + CrashLoopBackOff
diagnosis + deploy-gating assembly. A **controller** hub takes a deploy request
or a live incident (e.g. "pods crashlooping in prod") from you and fans it out to
a **chart-reviewer**, a **diagnoser**, and a **gatekeeper** that is the only agent
allowed to flip the ship decision — nothing deploys until the gatekeeper says
APPROVE, and a HOLD blocks it with reasons.

Everything below is based on the actual contents of
`examples/k8s-gitops.yaml` and the shipped CLI (`lib/cli.py`) and mailroom
(`lib/mail.py`). No API keys are needed to understand the mechanics; to run it
*for real* you supply the coding-CLI commands (or swap them for mock bash loops).

> New to the mail model? Read [`../getting-started.md`](../getting-started.md)
> first, then [`../mail-model.md`](../mail-model.md) for the four-folders recap.
> The one-line version: an agent **reads a file** to receive mail and **writes a
> file** to send it; the orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Platform engineers, SREs, and release managers who want a disciplined,
auditable path from "ship chart X to prod" (or "prod is crashlooping") to a
go / no-go decision without doing every step themselves. The swarm encodes the
discipline that makes deploys safe: one owner of the fan-out, a specialist who
only checks the chart's Kubernetes correctness, a specialist who only diagnoses
the running service, and a strict gatekeeper whose only job is to **stop bad
deploys**.

It is deliberately a **hub-and-spoke**, not a free-for-all: every request and
every verdict passes through the controller, and **only the controller talks to
you**. The spokes never mail the human and never mail each other — so the human
facing surface and the deploy decision both have exactly one authority. This is
distinct from `examples/incident-response.yaml` (general incident handling) and
`examples/postmortem.yaml` (write-up); this swarm is specifically the Kubernetes
deploy path — chart review, live crashloop diagnosis, and a hard gate.

---

## 2. The topology

```
          user
            │  "deploy chart X"  /  "prod is crashlooping"
            ▼
        controller           (the hub: talks to chart-reviewer, diagnoser,
            │                 gatekeeper, user — and ONLY controller talks to user)
         /   |   \
  chart-   diagnoser  gatekeeper
  reviewer  (gemini:   (claude:
  (codex:   CrashLoop  APPROVE
   Helm/     root       or HOLD)
   ArgoCD    cause)
   review)
```

Four agents, one directed flow:

1. **`user` → `controller`** — you send a deploy request or an incident.
2. **`controller` → `chart-reviewer` + `diagnoser` + `gatekeeper`** — the
   controller fans the work out: the chart-reviewer checks the Helm chart and
   ArgoCD Application manifest, the diagnoser (for incidents, or any chart
   already running) pulls logs/events and finds the root cause, and the
   gatekeeper is told it will receive the collected findings.
3. **`chart-reviewer` → `controller`** and **`diagnoser` → `controller`** — each
   specialist reports back only to the controller (separate messages in its
   `inbox/`).
4. **`controller` → `gatekeeper`** — the controller bundles both reports
   *verbatim* and asks for a single verdict: APPROVE or HOLD, with reasons.
5. **`gatekeeper` → `controller`** — the verdict. The controller never overrides
   it: APPROVE → it tells you the deploy is green-lit; HOLD → it tells you it's
   BLOCKED with the gatekeeper's stated blockers, and announces no ship.

The routing above is *enforced* by each agent's `can_talk_to` list. A spoke that
tries to mail `user` or a sibling is bounced back as a `system` message and filed
in `failed/` (see §7). Notably, `chart-reviewer`, `diagnoser`, and `gatekeeper`
**never** talk to `user` — only the controller does.

---

## 3. The config, explained

Here is `examples/k8s-gitops.yaml` in full:

```yaml
swarm:
  name: k8s-gitops
  root: ./k8s-gitops-workspace

defaults:
  capture: none              # loader auto-upgrades claude/codex to their hook
  can_talk_to: []           # tightened per agent below

agents:
  - name: controller
    type: claude
    can_talk_to: [chart-reviewer, diagnoser, gatekeeper, user]
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: |
          Working-hours check: if a deploy request or a crashloop incident is
          mid-flight and one specialist (chart-reviewer, diagnoser, or gatekeeper)
          is lagging, note which lane is outstanding and chase it. If nothing is
          in flight, do nothing and wait for the next request.
        cron: "0 9-17 * * 1-5"        # top of the hour, 09:00-17:59, Mon-Fri
        when_busy: skip
    role: |
      You are CONTROLLER, the hub of a Kubernetes / GitOps deploy-gate swarm. The
      human (user) only ever talks to you -- you never let a spoke talk to the
      human directly.
      When the human drops a DEPLOY REQUEST (e.g. "deploy chart X v1.8.2 to prod")
      or an INCIDENT (e.g. "pods crashlooping in prod") into your inbox/, read it.
      Decompose it and FAN IT OUT in parallel by writing a file into:
        - outbox/chart-reviewer/  -> review the relevant Helm chart / ArgoCD
                                     Application manifests,
        - outbox/diagnoser/       -> (for incidents, or any chart that's already
                                     running) pull logs/events and diagnose,
        - outbox/gatekeeper/      -> you will send it the collected findings so it
                                     can APPROVE or HOLD the deploy.
      WAIT until the chart-reviewer and the diagnoser report back (separate
      messages in your inbox/). Bundle their findings VERBATIM and forward them to
      the gatekeeper by writing a file into outbox/gatekeeper/, asking for a single
      verdict: APPROVE or HOLD, with the reasons.
      When the gatekeeper replies with APPROVE, tell the human the deploy is
      green-lit and summarize what was reviewed. When it replies HOLD, tell the
      human it is BLOCKED, list the gatekeeper's stated blockers, and do NOT
      announce any ship. You never override the gatekeeper -- its decision is the
      decision. Spokes may only report to you; you are the sole human-facing agent.
      MAILBOX: read new mail in inbox/, act on it, then move it to read/. To reply,
      write a file into outbox/<name>/ (read outbox/<name>/about.md first) and
      finish your turn. You may message: chart-reviewer, diagnoser, gatekeeper, user.

  - name: chart-reviewer
    type: codex
    can_talk_to: [controller]
    command: "codex --yolo"
    role: |
      You are the CHART-REVIEWER. You receive a deploy request (a Helm chart and
      the relevant ArgoCD Application manifest, or a repo+path) from the
      controller and you review it for Kubernetes correctness ONLY:
        - resource requests/limits set and sane (no unbounded memory, no 0 CPU),
        - liveness/readiness/startup probes present and correctly pointed (wrong
          path or missing probe is a top CrashLoopBackOff cause),
        - rollout strategy (RollingUpdate maxUnavailable/maxSurge, or a healthy
          blue/green) that won't drop traffic,
        - image tag is pinned (no :latest) and matches the requested version,
        - ArgoCD syncPolicy (automated prune/diff) and health checks are correct,
        - secrets/ConfigMaps referenced actually exist (missing secret = crash).
      For each finding cite the exact manifest file + the field, the risk, and the
      smallest safe fix. Do NOT edit the chart and do NOT deploy anything. Report
      your review back to the controller by writing a file into outbox/controller/,
      then finish your turn.
      MAILBOX: read new mail in inbox/, act on it, then move it to read/. To reply,
      write a file into outbox/<name>/ (read outbox/<name>/about.md first) and
      finish your turn. You may message: controller.

  - name: diagnoser
    type: gemini
    can_talk_to: [controller]
    command: "gemini --yolo"
    role: |
      You are the DIAGNOSER. Given a crashlooping (or otherwise unhealthy) service
      -- a namespace, Deployment/StatefulSet name, or "pods CrashLoopBackOff in
      prod" -- you determine the ROOT CAUSE. Reason like an on-call SRE: pull
      `kubectl get pods -n <ns>`, `kubectl describe pod`, and `kubectl logs
      --previous` for the failing container, then classify the failure into the
      usual buckets and state which it is:
        - OOMKilled (limits too low / leak) -- show the OOMKilled event,
        - bad/missing config or missing Secret/ConfigMap (ImagePullBackOff,
          CreateContainerConfigError, CrashLoopBackOff on env/config),
        - probe misconfig (readiness/liveness pointing at the wrong port/path, or
          too aggressive, so the pod is killed before it's ready),
        - app-level error (stack trace in logs), bad image tag, or RBAC/quota.
      For the root cause give: the evidence (the exact event/log line), the
      concrete fix, and whether it's a chart bug (route to chart-reviewer) or a
      runtime issue. Do NOT fix or roll anything yourself. Report the diagnosis
      back to the controller by writing a file into outbox/controller/, then
      finish your turn.
      MAILBOX: read new mail in inbox/, act on it, then move it to read/. To reply,
      write a file into outbox/<name>/ (read outbox/<name>/about.md first) and
      finish your turn. You may message: controller.

  - name: gatekeeper
    type: claude
    can_talk_to: [controller]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the GATEKEEPER -- the ONLY agent who can flip the deploy decision.
      The controller forwards you the chart-reviewer's review and the diagnoser's
      diagnosis (verbatim) about a proposed deploy or a prod incident, and asks for
      one verdict. You return exactly one of:
        - APPROVE -- only if the chart review is clean (limits/probes/rollout
          correct, image pinned, secrets present) AND there is no active
          crashloop/diagnosis that's unresolved.
        - HOLD -- if ANY reviewer/diagnoser flagged a blocker (missing probe,
          missing secret, OOM risk, unresolved CrashLoopBackOff, :latest tag,
          unsafe rollout, etc). List every blocker explicitly.
      Be strict: when in doubt, HOLD. Your job is to stop bad deploys, not to be
      agreeable. Never approve something the controller already knows is crashing.
      You report ONLY to the controller -- you never message the human directly.
      Reply with APPROVE or HOLD plus the reasons by writing a file into
      outbox/controller/, then finish your turn.
      MAILBOX: read new mail in inbox/, act on it, then move it to read/. To reply,
      write a file into outbox/<name>/ (read outbox/<name>/about.md first) and
      finish your turn. You may message: controller.
```

Field by field:

### `swarm`
- **`name: k8s-gitops`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./k8s-gitops-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent gets its own default workdir
  (`k8s-gitops-workspace/controller`, `…/chart-reviewer`, `…/diagnoser`,
  `…/gatekeeper`). Orchestrator state goes under
  `k8s-gitops-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode is "capture nothing". The
  loader *auto-upgrades* this to the type's natural hook for `claude` and `codex`
  agents (see §3 "Per-type turn detection"), but it does **not** auto-upgrade
  `gemini`/`hermes` — that's the one thing to watch with the `diagnoser` (see
  Tips).
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `controller` (type: `claude`)
- **`can_talk_to: [chart-reviewer, diagnoser, gatekeeper, user]`** — the
  controller is the hub: it delegates to the three specialists and is the **only
  agent that can talk to `user`**. That last part matters — keep the human-facing
  surface to a single agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`pings:`** — one working-hours ping (see §3 "The working-hours ping").
- **`role`** — the standing identity plus the gate protocol: fan out, wait for
  both specialist reports, bundle them *verbatim* to the gatekeeper, and relay
  the verdict to you (green-lit or BLOCKED). On `up` this becomes the controller's
  first prompt, wrapped in a **standby notice** ("no task yet — don't send
  anything, you'll be notified"), so the controller waits for your request.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `chart-reviewer` (type: `codex`)
- **`can_talk_to: [controller]`** — the chart-reviewer only reports back to the
  controller. It cannot reach the diagnoser, the gatekeeper, or the `user`; the
  chart correctness is owned by one place.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "review the Helm chart + ArgoCD Application manifest for Kubernetes
  correctness only (limits, probes, rollout, pinned image, syncPolicy, existing
  secrets), cite the exact manifest + field per finding, and do **not** edit or
  deploy."
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `diagnoser` (type: `gemini`)
- **`can_talk_to: [controller]`** — the diagnoser only reports the root cause back
  to the controller. It cannot reach the `user` or the other spokes.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **`role`** — "SRE-style root-cause of a crashlooping/unhealthy service via
  `kubectl get/describe/logs --previous`, classify into OOM / bad config+missing
  secret / probe misconfig / app error, give evidence + concrete fix, route to
  chart-reviewer if it's a chart bug. Do **not** fix or roll anything."
- **Turn detection:** `gemini` has **no** completion hook, so it relies on **pane
  polling**. In this exact config the `defaults: capture: none` is *not*
  auto-upgraded for `gemini` (the auto-upgrade only covers `claude`/`codex`
  hooks), so the diagnoser resolves to `capture: none` → **the orchestrator gets
  no turn-completion signal** unless you add `capture: pane` (see Tips & the
  Customize note). This is the one real footgun in the shipped file.

### `gatekeeper` (type: `claude`)
- **`can_talk_to: [controller]`** — the gatekeeper only reports its verdict to the
  controller. It never messages the human directly.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **`role`** — "return exactly one of APPROVE or HOLD with reasons; be strict,
  HOLD on any blocker, never override a known crashloop." It is the **only** agent
  that can flip the deploy decision.
- **Turn detection:** `claude` → Stop hook.

### How the gate is enforced (the ACL)
"Nothing ships until the gatekeeper says so" is not a polite convention — it's the
`can_talk_to` graph. Only the controller lists `user`, so the human can only ever
hear from the controller. Only the gatekeeper can be the *source* of the final
verdict, because the controller is instructed to relay the gatekeeper's APPROVE /
HOLD verbatim and is the sole writer of mail to `user`. If a spoke (say the
diagnoser) ever tried to write into `outbox/user/` or `outbox/chart-reviewer/`,
the orchestrator bounces it as a `system` message ("you can only message:
controller") and files the attempt in `failed/` — the model self-corrects in-band
and the gate holds. The `can_talk_to` ACL is cooperative, not OS isolation (a
determined agent with filesystem access *could* write another inbox directly);
it's enforced for well-behaved agents and documented honestly.

### Per-type turn detection (the system clock)
Every agent's "stop" is what drives the `stop → sweep → route → release → nudge`
cycle. The type selects the detection mode:
- `controller` (`claude`) → **Stop hook**. The orchestrator installs a hook into
  the Claude session at `up`; when Claude finishes a turn the hook fires.
- `chart-reviewer` (`codex`) → **`notify` hook**. Codex calls a `notify` program
  on completion; the orchestrator installs it at `up`.
- `gatekeeper` (`claude`) → **Stop hook**.
- `diagnoser` (`gemini`) → **should be pane polling**, but in this config resolves
  to `capture: none` (see §3 `diagnoser` note). Add `capture: pane` to give it a
  signal. See [`../cli-reference.md`](../cli-reference.md) and
  [`../configuration.md`](../configuration.md) for `capture`/`type` semantics.

A `type`/`command` mismatch wedges the agent (completion never fires, the agent
pins "busy" forever) — here the commands match the types, so each hook lines up.

### The working-hours ping (cron)
The controller carries one `pings:` entry — the swarm is otherwise purely
event-driven off real mail. Its `cron: "0 9-17 * * 1-5"` fires at the top of
every hour from 09:00–17:59, Monday–Friday. `when_busy: skip` means that if the
controller is mid-turn (actively fanning out or gating), the ping is **dropped**
rather than interrupting an in-flight gate. The message simply tells the hub: if a
request/incident is mid-flight and one specialist is lagging, note which lane is
outstanding and chase it; if nothing is in flight, do nothing. It's a gentle
nag so a stalled lane doesn't sit unacknowledged during the workday — not a
self-start trigger.

### What's *not* in this config
- **No shared `workdir`.** Each of the four agents gets its own default directory
  under `k8s-gitops-workspace/`, so there's no mailbox namespacing and no shared
  source tree to coordinate — the spokes communicate purely through mail. (If you
  later want the chart-reviewer and diagnoser to share a checkout, see
  [`custom-workspace.md`](./custom-workspace.md).)
- **No `capture` overrides on individual agents.** `controller`, `chart-reviewer`,
  and `gatekeeper` are auto-upgraded from the `none` default to their natural hook;
  the `diagnoser` is the exception that needs an explicit `capture: pane` (Tips).
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 4. Run it

From the repo root (you can copy the example to own it first):

```bash
cp examples/k8s-gitops.yaml my-k8s-gate.yaml
./agentainer up -c my-k8s-gate.yaml
# or, to run the example in place:
./agentainer up -c examples/k8s-gitops.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the capture auto-upgrade warnings for
   `controller`/`chart-reviewer`/`gatekeeper` (and the absence of one for
   `diagnoser`).
2. Creates the runtime dirs (`k8s-gitops-workspace/.agentainer/…`: log, queue,
   run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/
   about.md` contact card *is* the ACL made visible: the controller gets
   `outbox/chart-reviewer/`, `outbox/diagnoser/`, `outbox/gatekeeper/`,
   `outbox/user/`; each spoke gets only `outbox/controller/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `controller` and
   `gatekeeper`, and the Codex `notify` hook for `chart-reviewer`. (The `diagnoser`
   has no hook here — see Tips.)
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the gate.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'k8s-gitops' is up with 4 agent(s)
:: attach with:  tmux attach -t <controller-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c my-k8s-gate.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents, so it must **never** be exposed on `0.0.0.0` without a token.
See [`../ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole gate route mail with no API keys — the mechanics are identical.

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the controller's green-lit / BLOCKED verdict as
mail (rather than have it held), turn yourself available first:

```bash
./agentainer user available -c my-k8s-gate.yaml
```

This rewrites the `user` contact card in the controller's `outbox/user/about.md`
to `Status: available`, so the controller sees you're reachable. (While away, mail
to you is *held* and the controller gets a `system` ack — nothing bounces.)

Now send a deploy request or an incident into the swarm, addressed to the
controller:

```bash
./agentainer send -c my-k8s-gate.yaml --to controller \
  "Deploy charts/checkout-api v1.8.2 to prod (ArgoCD app checkout-api)."

# or, an incident:
./agentainer send -c my-k8s-gate.yaml --to controller \
  "INCident: checkout-api pods are CrashLoopBackOff in prod, 6/6 down."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the controller, then — because
the inbox was empty — **released into `inbox/`** and the controller is **nudged**
(the protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the gate advance one turn at a time. Each arrow
is a `stop → sweep → route → release → nudge` cycle:

1. **controller receives the request/incident.** It reads `inbox/`, decomposes it,
   and writes fan-out delegations into `outbox/chart-reviewer/`, `outbox/diagnoser/`,
   and a heads-up into `outbox/gatekeeper/`. On stop, those route to the spokes.
2. **chart-reviewer and diagnoser work.** The chart-reviewer reviews the manifests
   and writes its findings into `outbox/controller/`; the diagnoser pulls
   `kubectl` evidence and writes its root cause into `outbox/controller/`. On each
   stop, those route back to the controller.
3. **controller bundles and forwards.** It waits for both reports, copies them
   *verbatim* into `outbox/gatekeeper/`, and asks for APPROVE or HOLD. On stop,
   that routes to the gatekeeper.
4. **gatekeeper decides.** It writes APPROVE or HOLD (+ reasons) into
   `outbox/controller/`. On stop, that routes back.
5. **controller relays the verdict to you.** APPROVE → "green-lit, here's what was
   reviewed"; HOLD → "BLOCKED, reasons: …", and it announces **no ship**. On stop,
   that's delivered to your `user` mailbox (visible with `agentainer user inbox`,
   or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a request, the agents just sit in standby (that's the point
> of the standby prompt). The swarm is event-driven; the only scheduled activity
> is the controller's working-hours ping, which self-suppresses when nothing is in
> flight.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c my-k8s-gate.yaml
```

```
swarm: k8s-gitops   root: ./k8s-gitops-workspace
  controller    (claude) up idle queue=0 unread=0 talks=chart-reviewer, diagnoser, gatekeeper, user
  chart-reviewer (codex) up idle queue=0 unread=1 talks=controller
  diagnoser     (gemini) up idle queue=0 unread=0 talks=controller
  gatekeeper    (claude) up idle queue=0 unread=0 talks=controller
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c my-k8s-gate.yaml            # whole swarm, last 20
./agentainer logs -c my-k8s-gate.yaml -f          # follow live
./agentainer logs diagnoser -c my-k8s-gate.yaml   # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox controller -c my-k8s-gate.yaml
```

Prints the one released message (headers + body), or `controller: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue controller -c my-k8s-gate.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach controller -c my-k8s-gate.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails it, and a HOLD is a feature, not a failure. Because
every message is natural-language mail, you can steer the swarm through the `user`
mailbox; everything routes back through the controller.

- **Send a clarification to the controller.** Realized the app is
  `checkout-api` but the namespace is `payments`? `./agentainer send --to
  controller -c my-k8s-gate.yaml "Re-brief the diagnoser: namespace is 'payments',
  not 'default'." The controller relays the change down the chain.
- **Chase a stalled lane.** If the controller's working-hours ping is off and a
  specialist has gone quiet, send the controller a nudge to re-fan the outstanding
  lane — or `attach` to that agent's pane directly.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c my-k8s-gate.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c my-k8s-gate.yaml     # resume is the default
```

On `up`, Agentainer reads `k8s-gitops-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the
controller and gatekeeper, `codex resume <id>` for the chart-reviewer (Gemini has
no recoverable session id, so the diagnoser starts fresh — see Tips). A resumed
`claude`/`codex` agent is *not* re-sent the standby prompt (its prior context is
restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c my-k8s-gate.yaml
```

For the full story, see [`../sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Give the diagnoser a turn signal (recommended)
As shipped, `defaults: capture: none` auto-upgrades `claude`/`codex` but **not**
`gemini`, so the `diagnoser` resolves to `capture: none` and the orchestrator gets
no "stop" signal from it. Add an explicit pane capture so the gate actually
advances off the diagnosis:

```yaml
  - name: diagnoser
    type: gemini
    can_talk_to: [controller]
    command: "gemini --yolo"
    capture: pane          # <- add this; gemini has no completion hook
    role: |
      ...
```

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command`
mismatch wedges the agent — see [`../cli-reference.md`](../cli-reference.md)):
- `diagnoser: type: codex` if you'd rather run root-cause on Codex (and then it
  inherits the `notify` hook automatically, no `capture: pane` needed).
- `chart-reviewer: type: claude` to put chart review on Claude while the
  diagnoser stays Gemini.
- Remember: `gemini`/`hermes` need `capture: pane` (pane polling) since they have
  no completion hook; this swarm is exactly the case that bites you.

### Tune the ACL
- To let the `gatekeeper` escalate a HOLD straight to `user` (not only via the
  controller), add `user` to its `can_talk_to`. Mind that this widens the
  human-facing surface; the doc's convention keeps the controller the sole `user`
  contact so the gate stays single-sourced.
- To let the `diagnoser` tell the `chart-reviewer` directly that a crashloop is a
  chart bug, add `chart-reviewer` to its `can_talk_to` — but then you also add
  `diagnoser` to the chart-reviewer's list, and the "spokes never talk to each
  other" guarantee is gone. Prefer routing through the controller.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md)
  for mixing model families (claude + codex + gemini) safely.

### Make the gate stricter / softer
Edit the gatekeeper's `role:` — e.g. require a second reviewer's sign-off, or
auto-HOLD on any `:latest` tag. The gate is just instructions the orchestrator
relays; the controller's role already forbids overriding it.

---

## 10. Tips & footguns

- **Keep the controller the only `user`-facing agent.** Only the controller lists
  `user` in `can_talk_to`. That gives you a single funnel: raw reviews, diagnoses,
  and the final verdict all pass through one agent before they reach you. If a
  spoke tries to mail `user` directly, the orchestrator bounces it (ACL) and drops
  a `system` note in its inbox explaining who it *can* message — the model
  self-corrects in-band.

- **The gate is real, not decorative.** The controller is explicitly forbidden
  from overriding the gatekeeper; APPROVE is the *only* green light and a HOLD
  blocks the ship with reasons. Trust the HOLD — when in doubt the gatekeeper is
  told to HOLD. If you want a softer gate, edit its `role:`, don't bypass it.

- **The diagnoser needs `capture: pane` (the one footgun in this file).** Because
  `defaults: capture: none` only auto-upgrades `claude`/`codex` to their hooks, the
  `gemini` diagnoser ends up with no turn-completion signal and the orchestrator
  will never learn it stopped — the gate stalls after the diagnosis. Add
  `capture: pane` to the diagnoser (see §9), or swap it to `type: codex`.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If the gate seems stuck, check that each agent's **turn detection
  actually fires** — a `type`/`command` mismatch means completion never triggers
  and the agent pins "busy" forever. `status` showing an agent `busy` for a long
  time with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **The working-hours ping won't interrupt a live gate.** `when_busy: skip` drops
  the controller's hourly ping if it's mid-turn, so a fan-out or gate in progress
  is never disturbed. It only chases a *stalled* lane when the controller is idle.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c my-k8s-gate.yaml
  ./agentainer remove-session -c my-k8s-gate.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches your `agentainer.yaml` or any repo the agents might
  share.

- **Availability shapes the ending.** If `user` is **away** when the controller
  finishes, your green-lit / BLOCKED verdict is *held* (with a `system` "the user
  is away" ack to the controller) rather than lost — read it later with
  `agentainer user inbox` or flip yourself available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions.

---

### See also

- [`../getting-started.md`](../getting-started.md) — install and first swarm.
- [`../mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`../sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`../configuration.md`](../configuration.md) — `capture`, `pings`, `defaults`.
- [`../cli-reference.md`](../cli-reference.md) — `up`/`send`/`status`/`logs`/`user`.
- [`../ui-guide.md`](../ui-guide.md) — the mail-app control plane.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- `examples/k8s-gitops.yaml` — the config this walkthrough is built on.
- `examples/incident-response.yaml` and `examples/postmortem.yaml` — related but
  distinct (general incident handling / write-up, not the deploy gate). See
  [`postmortem.md`](./postmortem.md) for the write-up use case.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

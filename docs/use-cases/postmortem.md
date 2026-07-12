# Use case: the postmortem swarm

A concrete, end-to-end walkthrough of the shipped `examples/postmortem.yaml`
swarm — a four-agent pipeline that turns a *resolved* incident into a blameless
post-incident review: rebuild the **timeline**, find the **root cause**, then
write **action items** and the final document. It's the canonical
"investigate → reason → write it up" loop, wired entirely through Agentainer's
file-based mail model.

Everything below is based on the actual contents of `examples/postmortem.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you supply the
coding-CLI commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The problem it solves (and who it's for)

An incident is over. The page fired, someone mitigated, customers are fine, and
now there's a blank doc titled `postmortem.md` and a Slack thread full of "what
actually happened?" The honest writeup is the part everyone defers — it needs
someone to (a) reconstruct the timeline from scattered logs, (b) think hard
about why it happened without turning it into a witch-hunt, and (c) turn that
into action items a human will actually do.

This swarm is for **SREs, on-call engineers, and ops/incident managers** who
want that writeup *produced* rather than *facilitated by hand*. You hand it the
incident summary and a path to the logs; it comes back with a structured,
blameless review.

**It is deliberately unhurried.** Unlike `examples/incident-response.yaml`, which
coordinates a *live* fire under time pressure (mitigate first, ask questions
later), this runs *after* the fire is out. There is no commander racing a clock
and approving one change at a time — there's an incident lead who sequences
three analytical passes and refuses to let anyone blame a person.

### How it differs from `incident-response.yaml`

| | `incident-response.yaml` | `postmortem.yaml` |
|---|---|---|
| Phase | **During** the incident (live) | **After** the incident (resolved) |
| Goal | Stop the bleeding fast | Understand it fully, leave a doc |
| Hub | `commander` (drives mitigation) | `incident_lead` (drives the writeup) |
| Pressure | High — one approved change at a time | None — accuracy over speed |
| Analysts | investigator / responder / scribe | timeline / rootcause / action |
| Deliverable | A mitigated incident + a timeline skeleton | A finished blameless postmortem for the human |
| Blameless? | Scribe *drafts* a review at the end | Blameless is the load-bearing rule throughout |

Same hub-and-spoke wiring, opposite job. Keep both — triage the fire with one,
write it up with the other.

---

## 2. The topology

```
        timeline
           |
   rootcause --- incident_lead --- user
           |
         action ------------------ user
```

Four agents, one directed flow:

1. **`user` → `incident_lead`** — you send the incident summary + a path to logs.
2. **`incident_lead` → `timeline`** — the lead restates the incident and points
   the timeline analyst at the evidence.
3. **`timeline` → `incident_lead`** — the timeline comes back as facts with sources.
4. **`incident_lead` → `rootcause`** — the settled timeline is handed to root cause.
5. **`rootcause` → `incident_lead`** — causes come back (contributing + root, blameless).
6. **`incident_lead` → `action`** — timeline + causes go to action items.
7. **`action` → `user`** — the finished postmortem lands in your mailbox.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. An agent can only deliver to names on its own list; anything
else is bounced back as a `system` message and filed in `failed/` (see §8).

### The ACL, exactly

- **`incident_lead`** → `[timeline, rootcause, action, user]` — the hub. It is
  the only agent that can reach all three analysts *and* you.
- **`timeline`** → `[incident_lead]` — talks only to the lead (no cross-analysis).
- **`rootcause`** → `[incident_lead]` — talks only to the lead.
- **`action`** → `[incident_lead, user]` — writes the final doc to you, and copies
  the lead.

Notice the analysts **never talk to each other**. That's intentional: each pass
builds on the previous one, and the lead is the single place where the timeline,
causes, and actions are stitched together. If `timeline` mailed `rootcause`
directly, the root-cause pass might reason about a draft that the lead never
approved.

---

## 3. The config, explained

Here is `examples/postmortem.yaml` in full:

```yaml
# 🔍 Postmortem -- turn a resolved incident into a blameless writeup.
swarm:
  name: postmortem
  root: ./postmortem-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: incident_lead
    type: claude
    can_talk_to: [timeline, rootcause, action, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the INCIDENT LEAD running a BLAMELESS postmortem for an incident
      that is already resolved. You do not analyze logs yourself; you sequence
      the analysis and assemble the final review.
      Team: timeline, rootcause, action.
      Run it in three ordered passes ... Hold the line on BLAMELESS ...

  - name: timeline
    type: claude
    can_talk_to: [incident_lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the TIMELINE analyst. From the logs and notes the incident lead
      gives you, reconstruct a minute-by-minute timeline ... facts only ...

  - name: rootcause
    type: claude
    can_talk_to: [incident_lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the ROOT CAUSE analyst, working BLAMELESSLY. Given the settled
      timeline, separate trigger / contributing factors / root cause ...

  - name: action
    type: claude
    can_talk_to: [incident_lead, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the ACTION ITEMS analyst and the author of the final writeup ...
      send it to outbox/user/ so the human has the finished document.
```

(Shown abbreviated; see the file for the full `role:` text that each agent
receives as its first prompt.)

### `swarm`
- **`name: postmortem`** — the swarm's name (shows up in `status`, logs, sessions).
- **`root: ./postmortem-workspace`** — parent directory for the agents' working
  directories and mailboxes. Each agent gets `postmortem-workspace/<name>/` as
  its workdir (created on `up`), and its mailbox folders live alongside.
  Orchestrator state goes under `postmortem-workspace/.agentainer/` (never
  commit it). The analysts write their artifacts (`TIMELINE.md`, `CAUSES.md`,
  `ACTIONS.md`) into their own workdirs; the lead reads them by path.

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, keyed off each agent's `type`. For
  `claude` agents, whose CLI supports a completion **hook**, setting `capture:
  none` is a footgun — so the config loader *upgrades* it back to `hook` and
  prints a warning at `up`. All four agents here are `claude`, so all four are
  auto-upgraded to `capture: hook` (the Stop hook). If you swap any agent to
  `gemini`/`hermes`, override with `capture: pane`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `incident_lead` (type: `claude`)
- **`can_talk_to: [timeline, rootcause, action, user]`** — the hub. It is the
  **only agent that can talk to `user`**, *and* the only one that can reach all
  three analysts. Keep the human-facing surface to this single agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias or the `chy3` alias used in testing. Treat command strings as sensitive;
  they may embed keys.)
- **`role`** — the standing identity and the **three-pass procedure**: restate →
  timeline → rootcause → action. On `up` this becomes the agent's first prompt,
  wrapped in a **standby notice** ("no task yet — don't send anything, you'll be
  notified"), so the lead waits for your summary instead of inventing an
  incident. The role also carries the **blameless guard**: if an analyst names a
  person, the lead sends it back.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `timeline` (type: `claude`)
- **`can_talk_to: [incident_lead]`** — can report only to the lead. It cannot
  reach `rootcause` or `action`; its output always flows back through the lead.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch command.
- **`role`** — "reconstruct a minute-by-minute, sourced timeline; facts only;
  never name a person."

### `rootcause` (type: `claude`)
- **`can_talk_to: [incident_lead]`** — reports only to the lead. Deliberately
  cannot reach `timeline` (it reasons about the lead-approved timeline, not a
  draft) or `user` (no pre-release findings to the human).
- **`role`** — "separate trigger / contributing / root cause; 5-whys or fault
  tree; show the chain; blame the system, never the person."

### `action` (type: `claude`)
- **`can_talk_to: [incident_lead, user]`** — the only analyst that can reach you.
  It writes `ACTIONS.md` and assembles the final postmortem, sending it to
  `outbox/user/` and copying the lead.
- **`role`** — "prioritized action items with an owner (a role/team), a done-when
  test, and a priority; then the finished blameless writeup for the human."

### What's *not* in this config
- **No `periodically_ping_seconds`.** The writeup is event-driven off your
  arrival; there's no timer poking agents. (If you wanted the lead to nudge a
  slow analyst, add `periodically_ping_seconds: 300` to `incident_lead`.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **No `telegram:` block.** The human-facing finish is the `user` mailbox; add a
  Telegram bridge (see [`telegram-bridge.md`](../telegram-bridge.md)) if you want
  the finished postmortem to also land in a chat.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/postmortem.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the `capture: none → hook` upgrades.
2. Creates the runtime dirs (`postmortem-workspace/.agentainer/…`: log, queue,
   run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: the lead gets
   `outbox/timeline/`, `outbox/rootcause/`, `outbox/action/`, `outbox/user/`;
   `action` gets `outbox/incident_lead/`, `outbox/user/`; the analysts get just
   `outbox/incident_lead/`.
4. **Installs the Claude Stop hook** for all four agents (turn detection).
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'postmortem' is up with 4 agent(s)
:: attach with:  tmux attach -t <incident_lead-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/postmortem.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). By default the UI binds **`127.0.0.1`** —
only pass `--host 0.0.0.0` with a `--token` when you truly need remote access
(CLAUDE.md §18). See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole pipeline route mail with no API keys — the mechanics are identical.

---

## 5. Drive a postmortem

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the finished postmortem as mail (rather than
have it held), turn yourself available first:

```bash
./agentainer user available -c examples/postmortem.yaml
```

This rewrites the `user` contact card in `action`'s `outbox/user/about.md` to
`Status: available`, so `action` sees you're reachable. (While away, mail to you
is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the incident into the swarm, addressed to the `incident_lead`:

```bash
./agentainer send --to incident_lead "Postmortem the outage on 2026-07-10; logs in /var/log/checkout/."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the incident lead, then —
because the inbox was empty — **released into `inbox/`** and the lead is
**nudged** (the protocol is re-pasted into its pane, including its allowed-
recipient list).

### The mail flowing

Watching the log (§6), you'll see the three passes advance one turn at a time.
Each arrow is a `stop → sweep → route → release → nudge` cycle:

1. **incident_lead receives the summary.** It reads `inbox/`, restates the
   incident, writes a delegation file into `outbox/timeline/`. On stop, that
   routes to `timeline` and nudges it.
2. **timeline reconstructs the timeline.** It reads its inbox, writes
   `TIMELINE.md`, and writes the timeline into `outbox/incident_lead/`. On stop,
   that routes back to the lead.
3. **incident_lead forwards to rootcause.** It sends the settled timeline to
   `outbox/rootcause/`. On stop, that routes to `rootcause` and nudges it.
4. **rootcause reasons about causes.** It writes `CAUSES.md` and sends the
   analysis to `outbox/incident_lead/`. On stop, that routes back to the lead.
5. **incident_lead forwards to action.** It sends timeline + causes to
   `outbox/action/`. On stop, that routes to `action` and nudges it.
6. **action writes the deliverable.** It writes `ACTIONS.md`, assembles the final
   postmortem, and writes it into `outbox/user/` (and copies
   `outbox/incident_lead/`). On stop, the postmortem is delivered to your `user`
   mailbox.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a summary, the agents just sit in standby (that's the point
> of the standby prompt). The pipeline only moves when your mail arrives.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/postmortem.yaml
```

```
swarm: postmortem   root: ./postmortem-workspace
  incident_lead (claude) up idle queue=0 unread=0 talks=timeline, rootcause, action, user
  timeline (claude)       up idle queue=0 unread=1 talks=incident_lead
  rootcause (claude)      up idle queue=0 unread=0 talks=incident_lead
  action (claude)         up idle queue=0 unread=0 talks=incident_lead, user
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/postmortem.yaml          # whole swarm, last 20
./agentainer logs -c examples/postmortem.yaml -f        # follow live
./agentainer logs action -c examples/postmortem.yaml   # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox incident_lead -c examples/postmortem.yaml
```

Prints the one released message (headers + body), or
`incident_lead: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue timeline -c examples/postmortem.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach incident_lead -c examples/postmortem.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

**Read the analyst artifacts** — the timeline, causes, and actions land in each
agent's workdir:

```bash
cat postmortem-workspace/timeline/TIMELINE.md
cat postmortem-workspace/rootcause/CAUSES.md
cat postmortem-workspace/action/ACTIONS.md
```

---

## 7. Iterate on the writeup

The finished postmortem is delivered as a file in your `user` mailbox — read it
with:

```bash
./agentainer user inbox -c examples/postmortem.yaml
```

Often the first pass is *good but not final*. Because the agents **resume by
default** (see §8), you can keep the conversation going without losing context:

```bash
# You're away? Turn available first, then send a revision note to the lead.
./agentainer user available -c examples/postmortem.yaml
./agentainer send --to incident_lead "The action items are too vague. Ask action for concrete done-when tests and re-issue the postmortem."
```

The lead picks the thread back up from its existing context, re-sequences the
analysts, and `action` re-delivers an improved postmortem to your mailbox. You
can also target an analyst directly via the lead, or, once you've made the
`action` agent's `can_talk_to` include `user` (it already does), ask it for a
specific revision — though routing revision requests through the lead keeps the
pipeline coherent.

If an analyst strays from blameless, the **lead is the enforcer**: its role tells
it to send person-blaming output back and ask for a system-gap restatement. If
you see a name in the mail, nudge the lead the same way.

---

## 8. Resume after a stop

Tear the swarm down when you're done:

```bash
./agentainer down -c examples/postmortem.yaml
```

Bring it back later and **conversations resume by default**:

```bash
./agentainer up -c examples/postmortem.yaml     # resume is the default
```

On `up`, Agentainer reads `postmortem-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for all four
agents here. A resumed agent is *not* re-sent the standby prompt (its prior
context — including the incident you're mid-postmortem on — is restored), so you
can pick the writeup back up exactly where it stalled.

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/postmortem.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md) and
the reboot walkthrough in
[`use-cases/resume-after-reboot.md`](./resume-after-reboot.md).

---

## 9. Customize

This config is a starting point. Common variations:

- **Add a `comms` agent for stakeholder updates.** Stakeholders want a shorter,
  less technical version than the engineering postmortem. Add:
  ```yaml
  - name: comms
    type: claude
    can_talk_to: [incident_lead, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are COMMS. Given the finished postmortem (ask incident_lead for it),
      write a blameless external/status-page summary for customers and
      non-technical stakeholders: what happened, what we did, what's changed.
      Send it to outbox/user/. Never name people; lead with impact and next steps.
  ```
  and add `comms` to `incident_lead`'s `can_talk_to`. Now the lead can fan the
  finished doc out to both the engineering `action` writeup and a customer-facing
  `comms` summary.

- **Swap models per role.** The analysts are independent of each other, so you
  can mix CLI types freely — e.g. make `timeline` a `gemini` pane-polling agent
  and `rootcause` a `codex` agent — and tune each one's strengths. Remember: for
  `gemini`/`hermes` you must set `capture: pane` (they have no completion hook),
  and the `command` must launch that CLI:
  ```yaml
  - name: timeline
    type: gemini
    can_talk_to: [incident_lead]
    capture: pane
    command: "gemini --yolo"
    role: "..."
  ```
  The `type` ↔ `command` pair is checked at `up`; a mismatch pins the agent
  "busy" forever (CLAUDE.md footgun).

- **Tune the ACL for a single-writer final doc.** If you want *only* `action` to
  ever touch `user` (lead can't short-circuit the human), drop `user` from
  `incident_lead`'s `can_talk_to`. Conversely, to let the lead send the finished
  doc too, `action` already copies it — leave as-is.

- **Point agents at the real evidence.** The analysts work in their own workdirs;
  if the logs/notes live outside `postmortem-workspace/`, give the relevant agent
  a `workdir:` that can see them, or (cleaner) pass an absolute path in your
  `send` message as the example `command` does. Never put secrets in the path.

- **Add a periodic nudge.** If a pass stalls, add `periodically_ping_seconds: 300`
  to the stalled agent's entry so the orchestrator re-pastes the protocol on a
  timer.

---

## 10. Tips & footguns

- **Keep `incident_lead` the only `user`-facing hub (besides `action`).** Both the
  lead and `action` list `user`; that's deliberate — the lead coordinates, and
  `action` delivers the finished doc. If `timeline` or `rootcause` tried to mail
  `user` directly, the orchestrator bounces it (ACL) and drops a `system` note
  explaining who it *can* message — the model self-corrects in-band.

- **Blameless is enforced in the roles, not the code.** The lead's role carries
  the guard ("if any analyst names or blames a person, send it back"). It works
  because the lead is the only hub — but it's a model instruction, so a stray
  name can still slip through. Spot-check the mail; a one-line nudge to the lead
  fixes it (§7).

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch means completion never triggers and the
  agent pins "busy" forever. `status` showing an agent `busy` for a long time
  with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived
  so the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s)
  to kill "thanks!/you're welcome!" loops.

- **Force-idle if a turn never registers.** If a `claude` agent's Stop hook
  didn't fire (e.g. you typed into its pane by hand), nudge the state along:
  ```bash
  ./agentainer idle incident_lead -c examples/postmortem.yaml
  ```

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/postmortem.yaml
  ./agentainer remove-session -c examples/postmortem.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

- **Availability shapes the ending.** If `user` is **away** when `action` finishes,
  your final postmortem is *held* (with a `system` "the user is away" ack to
  `action`) rather than lost — read it later with `agentainer user inbox` or flip
  yourself available and it's delivered.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing/ACL work.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resume conversations
  across restarts (the postmortem stays coherent mid-writeup).
- [`use-cases/delegation-pipeline.md`](./delegation-pipeline.md) — the
  "delegate → do → check" pattern this three-pass flow generalizes.
- [`use-cases/multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing `claude`/
  `codex`/`gemini`/`hermes` in one swarm (how to set `capture`).
- `examples/incident-response.yaml` — the *live* triage counterpart to run
  *during* the incident.

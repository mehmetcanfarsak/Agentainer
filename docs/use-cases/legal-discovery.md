# Use case: Legal discovery (eDiscovery)

A concrete, end-to-end walkthrough of the shipped
`examples/legal-discovery.yaml` swarm — a four-agent eDiscovery triage line that
takes a legal matter and a relevance query and returns a counsel-ready privilege
log plus a relevance summary. A **discovery-lead** fans a corpus out to a
**tagger** (does this doc respond to the request?), a **privilege-checker** (is
it protected and must be withheld?), and a **summarizer** that turns the merged
work product into a privilege log and a relevance summary. The lead is the only
agent that ever addresses the human.

Everything below is based on the actual contents of
`examples/legal-discovery.yaml` and the shipped CLI (`lib/cli.py`) and mailroom
(`lib/mail.py`). No API keys are needed to understand the mechanics; to run it
*for real* you supply the coding-CLI commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state. Also see the privilege
> sibling walkthrough [`legal-contract-review.md`](./legal-contract-review.md).

---

## 1. Who this is for

Litigation support, paralegals, and in-house legal ops who need a structured
first-pass document review over a corpus (custodian mailboxes, a deal-data room,
a dropped-in `./corpus`) without reading every page themselves. The swarm encodes
the discipline that matters in discovery: a single owner of the human-facing
answer, a separate *authority* on privilege, and a summarizer that never sees —
and never leaks — protected content.

It is deliberately a **fan-out hub**, not a free-for-all: the three specialists
never talk to each other (they'd each re-read the corpus and clobber each other's
work product); they report only to the lead, who fans the corpus out and merges
the results into one response for counsel. Swapping in a real archival/redaction
agent (see §7) is a one-line config change.

> **Decision-support, not legal advice.** This swarm produces a first-pass triage
> to help counsel decide what to produce and what to withhold. It does not replace
> a qualified attorney's privilege call, and nothing it emits is a legal opinion.

---

## 2. The topology

```
  tagger ──────────┐
  privilege-checker ┼──▶ discovery-lead ──▶ user
  summarizer ──────┘        (lead fans the corpus out, then returns the
                             merged relevance summary + privilege log)
```

Four agents, one directed flow:

1. **`user` → `discovery-lead`** — you send the matter, the custodians/corpus
   location, and the relevance query (what "responsive" means).
2. **`discovery-lead` → `tagger` + `discovery-lead` → `privilege-checker`** — the
   lead sends the *same* corpus path + query + custodian scope to both in
   parallel. The privilege-checker sees the same documents so it can flag before
   anything leaves.
3. **`tagger` / `privilege-checker` → `discovery-lead`** — each reports back only
   to the lead (tags, and privilege flags respectively).
4. **`discovery-lead` → `summarizer`** — the lead merges the tags + privilege
   flags and asks for the privilege log and the relevance summary.
5. **`summarizer` → `discovery-lead`** — two artifacts come back (log + summary).
6. **`discovery-lead` → `user`** — the lead returns the merged log + summary,
   ending with the standing "not legal advice" note.

The routing above is *enforced* by each agent's `can_talk_to` list. `tagger`,
`privilege-checker`, and `summarizer` **never** talk to each other and **never**
talk to `user` — only the lead does. Anything else is bounced back as a `system`
message and filed in `failed/` (see §7).

---

## 3. The config, explained

Here is `examples/legal-discovery.yaml` (role bodies trimmed to their opening
lines; the full prose is in the file):

```yaml
swarm:
  name: legal-discovery
  root: ./legal-discovery-workspace

defaults:
  capture: none              # example ships for the swap-to-mock demo; see note
  can_talk_to: []           # tightened per agent below

agents:
  - name: discovery-lead
    type: claude
    can_talk_to: [tagger, privilege-checker, summarizer, user]
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: |
          Progress check: if a corpus review is in flight, send the human a
          one-line status (docs seen / tagged / flagged privileged / summarized)
          and the ETA to a full response. If nothing is pending, say "no active
          review" plainly.
        cron: "0 */4 * * *"            # every 4 hours
        when_busy: queue
    role: |
      You are the DISCOVERY LEAD... the ONLY agent that talks to the human...
      PRIVILEGE IS SACRED: never quote, summarize the contents of, or forward
      any privileged document to the human.

  - name: tagger
    type: codex
    can_talk_to: [discovery-lead]
    command: "codex --yolo"
    role: |
      You are the DOCUMENT TAGGER... emit a stable id, a verdict
      (RESPONSIVE / NON-RESPONSIVE), a one-sentence reason, and a CONFIDENCE.

  - name: privilege-checker
    type: gemini
    can_talk_to: [discovery-lead]
    command: "gemini --yolo"
    role: |
      You are the PRIVILEGE CHECKER -- the authority on what must be WITHHELD...
      you FLAG and WITHHELD; you never reveal the protected content.

  - name: summarizer
    type: claude
    can_talk_to: [discovery-lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the SUMMARIZER... Produce TWO counsel-ready artifacts... the
      PRIVILEGE LOG and a RELEVANCE SUMMARY. The log may contain ONLY an id, a
      privilege type, and a withhold reason.
```

Field by field:

### `swarm`
- **`name: legal-discovery`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./legal-discovery-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent's workdir defaults to
  `legal-discovery-workspace/<name>` (no shared workdir here — each agent owns
  its own folder). Orchestrator state goes under
  `legal-discovery-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.
- **`capture: none`** — the shipped example disables automatic turn-completion
  capture by default. This is intentional: the example is built so you can swap
  each `command` for a mock bash loop (a "key-free demo") where no CLI fires a
  Stop/notify hook. **When you run real agents**, drop this default (or set a
  per-agent `capture`) so the stop → sweep → route → nudge clock actually fires —
  see the per-type turn detection note below.

### `discovery-lead` (type: `claude`)
- **`can_talk_to: [tagger, privilege-checker, summarizer, user]`** — the lead is
  the hub: it fans the corpus to the three specialists and is the **only agent
  that can talk to `user`**. Keep the human-facing surface to a single agent.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command. Treat command
  strings as sensitive; they may embed keys.)
- **`pings`** — see the cron note below.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`),
  once you re-enable capture for live runs.
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the lead waits for your matter instead of proactively
  mailing peers. Its role bakes in the privilege rule: never quote, summarize, or
  forward privileged content to the human.

### `tagger` (type: `codex`)
- **`can_talk_to: [discovery-lead]`** — reports tags only to the lead. Cannot
  reach `privilege-checker`, `summarizer`, or `user`.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — "read each document, emit a stable id, a verdict
  (RESPONSIVE / NON-RESPONSIVE), a one-sentence reason, and a CONFIDENCE; add a
  *provisional* privilege flag for anything that looks privileged, but never try
  to characterize its contents." It does not decide privilege and does not write
  the summary.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `privilege-checker` (type: `gemini`)
- **`can_talk_to: [discovery-lead]`** — reports privilege flags only to the lead.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **`role`** — "the authority on what must be WITHHELD." Flags only two kinds
  (ATTORNEY-CLIENT, ATTORNEY WORK PRODUCT), each with an id, type, and the
  one-sentence basis, and is **explicitly told never to reveal protected
  content** — its report is ids + privilege type + withhold reason only.
- **Turn detection:** `gemini` → **pane polling** (no completion hook exists for
  Gemini, so the supervisor watches the pane).

### `summarizer` (type: `claude`)
- **`can_talk_to: [discovery-lead]`** — reports the artifacts only to the lead.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **`role`** — "produce a PRIVILEGE LOG (one line per withheld doc: id |
  A-C / work product | withhold reason) and a RELEVANCE SUMMARY (counts, top
  themes, withheld-id list). The log may contain ONLY an id, a privilege type,
  and a withhold reason — never the protected text." Ends with the "not legal
  advice" note.
- **Turn detection:** `claude` → Stop hook.

### ACL enforcement (the important part)

The `can_talk_to` lists are *cooperative*, not OS isolation. The orchestrator
enforces them: when an agent writes a file into `outbox/<name>/`, the mailroom
only releases it if `<name>` is on the sender's own `can_talk_to` list. This
swarm's ACL is what keeps privileged content contained — the `tagger`,
`privilege-checker`, and `summarizer` literally cannot address `user`, so the
only path privileged-flag data can take toward the human is through the lead, who
is instructed to strip it and forward only log entries. If a specialist tries to
mail someone off-list (e.g. `summarizer` → `user`), the orchestrator **bounces**
it as a `system` message filed in `failed/`, and drops a `system` note in the
sender's inbox naming who it *can* message — the model self-corrects in-band.
See [`delegation-pipeline.md`](./delegation-pipeline.md) for the broader
hub-and-spoke routing discussion.

### The ping / cron note

Only `discovery-lead` carries a `pings` block — a gentle progress nudge during a
long review:

```yaml
    pings:
      - message: |
          Progress check: if a corpus review is in flight, send the human a
          one-line status (docs seen / tagged / flagged privileged / summarized)
          and the ETA to a full response...
        cron: "0 */4 * * *"            # every 4 hours
        when_busy: queue
```

- `cron: "0 */4 * * *"` — the orchestrator's scheduler fires this message at the
  lead every 4 hours (standard 5-field cron).
- `when_busy: queue` — if the lead is mid-turn (busy reviewing) when the cron
  fires, the nudge is **queued**, not dropped. The lead keeps working and the
  progress check lands as soon as it's free, so counsel (via you) stays informed
  without the lead having to remember to report. The lead can also report "no
  active review" plainly when nothing is pending.

### What's *not* in this config
- **No shared workdir.** Unlike `examples/data-pipeline-builder.yaml`, each agent
  owns `legal-discovery-workspace/<name>`; there's no `<name>-` mailbox
  namespacing to worry about. (If you later add a shared repository agent, see
  [`custom-workspace.md`](./custom-workspace.md).)
- **`capture: none` globally** (see above) — re-enable per agent for live runs.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 4. Run it

From the repo root. You can run the shipped file directly, or copy it first (the
YAML header suggests `cp examples/legal-discovery.yaml my-discovery.yaml`):

```bash
./agentainer up -c examples/legal-discovery.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings.
2. Creates the runtime dirs (`legal-discovery-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. Because the three
   specialists each list only `discovery-lead`, they get a single
   `outbox/discovery-lead/`; the lead gets `outbox/tagger/`,
   `outbox/privilege-checker/`, `outbox/summarizer/`, `outbox/user/`. The
   `outbox/<peer>/about.md` contact card *is* the ACL made visible.
4. **Installs per-type turn detection** — the Claude Stop hooks for
   `discovery-lead` and `summarizer`, the Codex `notify` hook for `tagger`, and
   (for `privilege-checker`, type `gemini`) pane polling. *(Only fires once you
   drop the `defaults: capture: none` or set per-agent capture; with the shipped
   default, you drive turns manually — see §6.)*
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'legal-discovery' is up with 4 agent(s)
:: attach with:  tmux attach -t <discovery-lead-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/legal-discovery.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only `127.0.0.1` bind — the UI can start processes, edit config, and
type into agents, so it must **never** be exposed on `0.0.0.0` without a token.
See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole triage route mail with no API keys — the mechanics are identical, and
> `defaults: capture: none` is exactly what makes the mock loop behave.

---

## 5. Drive a matter

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the lead's finished privilege log + relevance
summary as mail (rather than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/legal-discovery.yaml
```

This rewrites the `user` contact card in the lead's `outbox/user/about.md` to
`Status: available`, so the lead sees you're reachable. (While away, mail to you
is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the matter into the swarm, addressed to the lead:

```bash
./agentainer send --to discovery-lead -c examples/legal-discovery.yaml \
  "Matter: Acme v. Beta. Custodians: eng@acme, fin@acme. Pull everything in \
   ./corpus and find docs responsive to: 'all communications about the 2019 \
   pricing change and any internal analysis of its antitrust risk.'"
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the lead, then — because the
inbox was empty — **released into `inbox/`** and the lead is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the triage advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **discovery-lead receives the matter.** It reads `inbox/`, acknowledges to you,
   restates the relevance query, and writes the corpus path + query + custodian
   scope into `outbox/tagger/` **and** `outbox/privilege-checker/` (in parallel).
   On stop, both route.
2. **tagger + privilege-checker review.** The tagger reads its inbox, tags each
   doc RESPONSIVE/NON-RESPONSIVE with confidence, and reports to
   `outbox/discovery-lead/`. The privilege-checker reads the *same* corpus, flags
   A-C / work-product material with id + type + basis, and reports to
   `outbox/discovery-lead/` — never revealing protected content. On each stop,
   mail routes back to the lead.
3. **discovery-lead briefs the summarizer.** It merges the tags + privilege flags
   and writes them into `outbox/summarizer/`, asking for the privilege log and the
   relevance summary. On stop, that routes to the summarizer.
4. **summarizer emits the artifacts.** It writes the PRIVILEGE LOG (id | type |
   withhold reason — and nothing else about privileged docs) and the RELEVANCE
   SUMMARY back to `outbox/discovery-lead/`. On stop, that routes to the lead.
5. **discovery-lead finalizes.** It returns the merged log + summary to your
   `user` mailbox (visible with `agentainer user inbox`, or in the UI), ending
   with the "decision-support only — not legal advice" note.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a matter, the agents just sit in standby. The lead *does*
> carry a 4-hour progress ping (§3), but the triage itself only moves when real
> mail arrives.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/legal-discovery.yaml
```

```
swarm: legal-discovery   root: ./legal-discovery-workspace
  discovery-lead (claude) up idle queue=0 unread=0 talks=tagger, privilege-checker, summarizer, user
  tagger          (codex)  up idle queue=0 unread=1 talks=discovery-lead
  privilege-checker (gemini) up idle queue=0 unread=1 talks=discovery-lead
  summarizer      (claude) up idle queue=0 unread=0 talks=discovery-lead
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/legal-discovery.yaml          # whole swarm, last 20
./agentainer logs -c examples/legal-discovery.yaml -f        # follow live
./agentainer logs summarizer -c examples/legal-discovery.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox discovery-lead -c examples/legal-discovery.yaml
```

Prints the one released message (headers + body), or `discovery-lead: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue tagger -c examples/legal-discovery.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach privilege-checker -c examples/legal-discovery.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom
— handy for un-sticking an agent, but the mail model is the normal path.)

**The corpus** — the documents you point the lead at live wherever you said
(`./corpus` in the example). The agents read them in place; their own work
product lands in `legal-discovery-workspace/<name>/`.

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or (more directly)
by sending notes into an agent's inbox.

- **Send a clarification to the lead.** Realized the custodian scope is wrong?
  `./agentainer send --to discovery-lead -c examples/legal-discovery.yaml "Add
  the legal@acme custodian and re-fan the corpus to tagger and
  privilege-checker."` The lead re-briefs both.
- **Ask the tagger for the evidence.** `./agentainer send --to discovery-lead ...
  "Have the summarizer include the representative responsive document ids per
  theme."` — the lead forwards it.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/legal-discovery.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/legal-discovery.yaml     # resume is the default
```

On `up`, Agentainer reads `legal-discovery-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the lead
and summarizer, `codex resume <id>` for the tagger, and Gemini's resume for the
privilege-checker. A resumed agent is *not* re-sent the standby prompt (its prior
context — including the matter scope — is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/legal-discovery.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add an `archival` / `redaction` agent
Once the log is produced, you may want someone staging the to-be-produced set.
Add a fifth agent that can read the lead's deliverable and owns the production
list:

```yaml
  - name: production-clerk
    type: claude
    can_talk_to: [discovery-lead, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the PRODUCTION CLERK. Given the lead's privilege log + responsive
      list, assemble the production set (everything responsive minus withheld
      ids), and report the staging manifest to outbox/user/. You never see or copy
      privileged content.
```

Then add `production-clerk` to the lead's `can_talk_to` so it can be briefed.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `tagger: type: claude` if you want tagging on Claude while the checker stays
  Gemini.
- `summarizer: type: codex` to put summary authoring on Codex.
- Remember: `gemini`/`hermes` need `capture: pane` (pane polling) since they have
  no completion hook. For live runs, replace `defaults: capture: none` with
  per-agent `capture: auto` (or explicit `stop` / `notify` / `pane`).
- See [`multi-llm-swarm.md`](./multi-llm-swarm.md) for mixing model families
  safely.

### Tune the ACL
- To let the `summarizer` escalate straight to `user` (not only via the lead), add
  `user` to its `can_talk_to`. **Mind the privilege risk:** that widens the
  human-facing surface so a possibly-leaked privileged id could reach you without
  the lead's strip step — the doc's convention keeps the lead the sole `user`
  contact precisely to enforce the "forward only log entries" rule.
- To keep the specialists perfectly siloed (already the case here), leave their
  `can_talk_to: [discovery-lead]` — that's the guarantee the corpus isn't
  re-read and the human never hears a raw specialist voice.

---

## 10. Tips & footguns

- **Keep the lead the only `user`-facing agent.** Only the lead lists `user` in
  `can_talk_to`. That gives you a single funnel: raw tags, privilege flags, and
  summaries always pass through review (and the privilege strip) before they
  reach you. If a specialist tries to mail `user` directly, the orchestrator
  bounces it (ACL) and drops a `system` note in their inbox explaining who they
  *can* message — the model self-corrects in-band.

- **Privilege is enforced in two places: the ACL and the roles.** The ACL stops a
  specialist from *addressing* you; the role text stops the lead and summarizer
  from *including protected content* even when they're allowed to address you.
  The summarizer's privilege log may contain ONLY an id, a privilege type, and a
  withhold reason — never the protected text. Treat the role wording as the
  second fence; don't loosen it.

- **`defaults: capture: none` is a demo setting, not a production one.** With it
  on, no turn-completion hook fires, so the stop → sweep → route → nudge clock is
  idle and you drive turns by nudging/attaching manually (fine for the mock
  bash-loop demo). For live agents, set per-agent `capture` so `claude`→Stop hook,
  `codex`→notify, and `gemini`→pane actually fire — otherwise an agent pins
  "busy" forever after its first turn.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion. A
  `type`/`command` mismatch (e.g. a `gemini` agent whose `command` doesn't launch
  Gemini) means completion never triggers and the agent pins "busy" forever.
  `status` showing an agent `busy` for a long time with `unread` mail is the tell.

- **The 4-hour ping is `when_busy: queue`, not `drop`.** During a long review the
  lead gets a progress nudge that waits in the queue if it's busy, so counsel
  stays informed without interrupting the work or losing the status check. Adjust
  the `cron` expression if you want a more or less frequent heartbeat; set
  `when_busy: skip` if you'd rather it not fire mid-turn at all.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/legal-discovery.yaml
  ./agentainer remove-session -c examples/legal-discovery.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the documents in your `./corpus` or your config.

- **Availability shapes the ending.** If `user` is **away** when the lead
  finishes, your privilege log + summary is *held* (with a `system` "the user is
  away" ack to the lead) rather than lost — read it later with
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
- [`legal-contract-review.md`](./legal-contract-review.md) — the privilege sibling walkthrough.
- `examples/legal-discovery.yaml` — the config this walkthrough is built on.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

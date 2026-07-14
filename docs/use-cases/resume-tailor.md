# Use case: the resume & cover-letter tailor swarm

A concrete, end-to-end walkthrough of the shipped `examples/resume-tailor.yaml`
swarm — a four-agent hiring-prep pipeline where a **career coach** takes a job
description plus the candidate's resume, an **analyzer** maps the role's
requirements to the candidate's real experience, a **resume writer** tailors the
resume, and a **cover-letter writer** produces a matching letter — all flowing
back through the coach to the human. It's the canonical "hub briefs the
specialists → specialists report to the hub → hub delivers once" loop, wired
entirely through Agentainer's file-based mail model.

Everything below is based on the actual contents of
`examples/resume-tailor.yaml` and the shipped CLI (`lib/cli.py`) and mailroom
(`lib/mail.py`). No API keys are needed to understand the mechanics; to run it
*for real* you supply the coding-CLI commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

- **Job seekers** applying to a specific role who want their resume and cover
  letter rewritten *for that posting* — keyword-aligned for ATS, but grounded in
  what they actually did (no invented jobs, degrees, or metrics).
- **Career coaches** who run the same "analyze the fit, then tailor both
  documents" sequence for every client and want it done consistently and in
  parallel, with them as the single quality gate before anything goes to the
  client.
- **Bootcamp / university career services** prepping many candidates against many
  postings, where the coach reviews one finished package per applicant instead of
  doing the mechanical rewrite four times.

The swarm enforces one opinionated discipline: the **fit analysis is produced
once** and shared with both document writers, so the resume and the letter tell
the *same* story instead of drifting apart.

---

## 2. The topology

```
         JD + resume (1 message)
   user ───────────────────────────▶ career_coach
          (final resume + letter)  ◀──┐    │  brief
                                      │    ▼
                                      │  analyzer  ── fit map ──┐
                                      │                         │
                                      │  resume_writer ─ RESUME ┤──▶ all to coach
                                      │                         │
                                      │  cover_writer ── LETTER ┘
                                      │
                              coach reviews both, then delivers
```

Four agents, one directed flow:

1. **`user` → `career_coach`** — you send the job description and the resume in a
   single message.
2. **`career_coach` → `analyzer`** — the coach splits them and asks for a
   requirement-by-requirement fit map (and the exact JD keywords to mirror).
3. **`analyzer` → `career_coach`** — the fit map comes back; the coach forwards it
   (plus JD + resume) to both writers.
4. **`career_coach` → `resume_writer`** and **`career_coach` → `cover_writer`** —
   each writes its document against the *same* analysis.
5. **`resume_writer` / `cover_writer` → `career_coach`** — both documents land
   back for the coach's review.
6. **`career_coach` → `user`** — the coach delivers the final resume and cover
   letter together, having checked both are grounded in the candidate's real
   experience.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. The three specialists can only reach the coach, and only the
coach can reach `user`; anything else is bounced back as a `system` message and
filed in `failed/` (see §7).

---

## 3. The config, explained

Here is `examples/resume-tailor.yaml` in full:

```yaml
# 📄 Resume & cover-letter tailor -- a career coach runs a hiring-prep pipeline.
swarm:
  name: resume-tailor
  root: ./resume-tailor-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: career_coach
    type: claude
    can_talk_to: [analyzer, resume_writer, cover_writer, user]
    command: "claude --dangerously-skip-permissions"
    role: "You are the CAREER COACH. You take a job description + the candidate's
      resume, brief the analyzer, then the resume_writer and cover_writer, and
      deliver both finished documents to the user. See the file for the full flow."
  - name: analyzer
    type: claude
    can_talk_to: [career_coach]
    command: "claude --dangerously-skip-permissions"
    role: "You are the FIT ANALYZER. Map the JD's requirements to the candidate's
      experience; flag gaps. Hand a structured analysis back to the coach."
  - name: resume_writer
    type: claude
    can_talk_to: [career_coach]
    command: "claude --dangerously-skip-permissions"
    role: "You are the RESUME WRITER. Using the JD + resume + fit map, tailor the
      resume into RESUME.md, grounded in real experience only. Return it to the coach."
  - name: cover_writer
    type: claude
    can_talk_to: [career_coach]
    command: "claude --dangerously-skip-permissions"
    role: "You are the COVER-LETTER WRITER. Using the JD + fit map + tailored
      resume, write COVER_LETTER.md. Return it to the coach."
```

(The real file carries the full, multi-line `role:` prose shown in §2's flow; the
snippet above is abbreviated for the field-by-field read.)

Field by field:

### `swarm`
- **`name: resume-tailor`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./resume-tailor-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent gets
  `resume-tailor-workspace/<name>/` as its workdir (created on `up`), and its
  mailbox folders live alongside. Orchestrator state goes under
  `resume-tailor-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture`
  is how Agentainer knows a turn finished, and it's ultimately keyed off each
  agent's `type`. For `claude`, whose CLI supports a completion **Stop hook**,
  setting `capture: none` is a footgun — so the config loader *upgrades* it back
  to `hook` and prints a warning at `up` for every one of the four agents
  (`capture: none on a claude agent gives the orchestrator no turn-completion
  signal -- auto-upgraded to capture: hook.`). Net effect: all four agents use the
  hook.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `career_coach` (type: `claude`)
- **`can_talk_to: [analyzer, resume_writer, cover_writer, user]`** — the coach is
  the hub: it can brief all three specialists and is the **only agent that can
  talk to `user`**. That last part matters — keep the human-facing surface to a
  single agent (see §7) so one finished package arrives per application.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity. On `up` this becomes the agent's first
  prompt, wrapped in a **standby notice** ("no task yet — don't send anything,
  you'll be notified"), so the coach waits for your JD + resume instead of
  proactively mailing peers. The full role spells out the four-step flow and the
  honesty rule: never fabricate qualifications.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `analyzer`, `resume_writer`, `cover_writer` (type: `claude`)
- **`can_talk_to: [career_coach]`** — each specialist only reports upward to the
  coach. They deliberately **cannot** talk to `user` or to each other, which is
  what guarantees the fit map is produced once and both documents are written
  against it.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  commands (each a distinct tmux session; the coach's pane can't see their output
  directly).
- **`role`** — each is given a narrow job: the analyzer produces a structured fit
  map and the JD's keyword list; the resume_writer writes `RESUME.md`; the
  cover_writer writes `COVER_LETTER.md`. All three are told to ground every claim
  in the candidate's real experience and to ask (via the coach) rather than invent.
- **Turn detection:** `claude` → Stop hook for all three.

### What's *not* in this config
- **No `pings`.** None of the four agents has a periodic ping
  configured, so no agent is auto-nudged on a timer while idle — the pipeline is
  purely event-driven off your one incoming message. (If you wanted the coach to
  poke a slow specialist, you'd add a `pings` cron rule to it.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **No `mail_dir` overrides.** Each agent's four folders live inside its own
  workdir by default; because every workdir is distinct, no namespacing is
  needed (the shared-workdir warning won't fire).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/resume-tailor.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the four `capture: none → hook`
   upgrades.
2. Creates the runtime dirs
   (`resume-tailor-workspace/.agentainer/…`: log, queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: the coach gets
   `outbox/analyzer/`, `outbox/resume_writer/`, `outbox/cover_writer/`,
   `outbox/user/`; each specialist gets a single `outbox/career_coach/`.
4. **Installs the Claude Stop hook** for all four agents (turn detection).
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the pipeline.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'resume-tailor' is up with 4 agent(s)
:: attach with:  tmux attach -t <career_coach-session>
:: you can use the UI with:  agentainer serve --host 127.0.0.1 -c examples/resume-tailor.yaml --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). By default it binds **127.0.0.1** — the safe
loopback-only bind that satisfies the "control plane never on 0.0.0.0 unprotected"
rule. Add `--token <generated>` only if you deliberately expose it beyond
loopback. See the `README.md` "control-plane UI" section.

> **Key-free demo:** swap each `command:` for a mock bash loop
> (`bash -c 'while true; do read x; done'`) and you can watch the whole pipeline
> route mail with no API keys — the mechanics are identical.

---

## 5. Drive an application

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the coach's finished package as mail (rather
than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/resume-tailor.yaml
```

This rewrites the `user` contact card in the coach's `outbox/user/about.md` to
`Status: available`, so the coach sees you're reachable. (While away, mail to you
is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the job description and the resume into the swarm, addressed to the
coach. Keep them in **one** message so the coach has the full picture before it
briefs the analyzer:

```bash
./agentainer send --to career_coach \
  "JOB DESCRIPTION: (paste the full posting here)
   ---
   RESUME: (paste the candidate's current resume here)
   ---
   NOTES: emphasize the Kubernetes work; the candidate is pivoting from ops to platform."
```

> Tip: it's fine to paste long text in `send` — it's written verbatim into the
> coach's `inbox/` file. You can also drop the JD into a file and have the coach
> read it, or paste it once if you're iterating on the same role (see §6).

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the coach, then — because the
inbox was empty — **released into `inbox/`** and the coach is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **coach receives the JD + resume.** It splits the two, writes a brief into
   `outbox/analyzer/`, and stops. The orchestrator sweeps, routes to the analyzer,
   and nudges it.
2. **analyzer produces the fit map.** It reads the brief, writes the
   requirement-by-requirement analysis into `outbox/career_coach/`, stops; it
   routes back to the coach.
3. **coach briefs both writers.** It forwards the JD + resume + fit map to
   `outbox/resume_writer/` and `outbox/cover_writer/` (two messages, released
   one at a time), and stops.
4. **resume_writer and cover_writer write.** Each reads its inbox, writes its
   document (`RESUME.md` / `COVER_LETTER.md`) into its workdir, and reports back
   to `outbox/career_coach/`.
5. **coach reviews and delivers.** It reads both documents, checks they lead with
   the highest-ranked JD requirements and contain no fabricated claims, requests a
   fix if needed, then writes the final resume + letter into `outbox/user/`. On
   stop, that's delivered to your `user` mailbox (you'll see it with
   `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a JD, the agents just sit in standby (that's the point of
> the standby prompt). The pipeline only moves when real mail arrives — this swarm
> has no periodic pings to self-start it.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/resume-tailor.yaml
```

```
swarm: resume-tailor   root: ./resume-tailor-workspace
  career_coach (claude) up idle queue=0 unread=0 talks=analyzer, resume_writer, cover_writer, user
  analyzer (claude) up busy queue=0 unread=1 talks=career_coach
  resume_writer (claude) up idle queue=0 unread=0 talks=career_coach
  cover_writer (claude) up idle queue=0 unread=0 talks=career_coach
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/resume-tailor.yaml          # whole swarm, last 20
./agentainer logs -c examples/resume-tailor.yaml -f        # follow live
./agentainer logs cover_writer -c examples/resume-tailor.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox analyzer -c examples/resume-tailor.yaml
```

Prints the one released message (headers + body), or `analyzer: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue resume_writer -c examples/resume-tailor.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach career_coach -c examples/resume-tailor.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Resume after a stop

Tear the swarm down when you're done:

```bash
./agentainer down -c examples/resume-tailor.yaml
```

Bring it back later and **conversations resume by default**:

```bash
./agentainer up -c examples/resume-tailor.yaml     # resume is the default
```

On `up`, Agentainer reads
`resume-tailor-workspace/.agentainer/sessions.yaml` (written as each agent
finished its first turn) and reattaches the recorded conversations via each type's
native resume: `claude --resume <id>` for all four agents here. A resumed agent is
*not* re-sent the standby prompt (its prior context is restored) — convenient when
you're iterating on the same role (§8). Pass `--no-resume` to force everyone
fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/resume-tailor.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 8. Iterate on the same application

Because the coach is the only `user`-facing agent and resumes by default, round
two is cheap:

- **Tighten the resume.** Read the delivered package, then send the coach a
  follow-up: `./agentainer send --to career_coach "On the resume, cut the
  education section to two lines and lead with the platform migration. Keep the
  cover letter."` The coach re-briefs `resume_writer` and delivers the update to
  you (make sure you're still `user available`).
- **Re-target a different posting.** Paste a new JD as a fresh `send --to
  career_coach`; the analyzer re-maps, and both documents are rewritten against
  the new role.

---

## 9. Tips & footguns

- **Keep the coach the only `user`-facing agent.** In this config only the coach
  lists `user` in `can_talk_to`. That gives you a single point of contact and a
  clean funnel: the fit analysis is settled once and both documents are reviewed
  before they reach you. If a specialist tries to mail `user` directly, the
  orchestrator bounces it (ACL) and drops a `system` note in that specialist's
  inbox explaining who it *can* message — the model self-corrects in-band.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **Stop hook actually fires** —
  a `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't
  launch Claude) means completion never triggers and the agent pins "busy"
  forever. `status` showing an agent `busy` for a long time with `unread` mail is
  the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops.

- **Force-idle if a captured agent's turn never registers.** If some agent's Stop
  hook never fired and it pins `busy`, nudge the state along:
  ```bash
  ./agentainer idle analyzer -c examples/resume-tailor.yaml
  ```

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/resume-tailor.yaml
  ./agentainer remove-session -c examples/resume-tailor.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

- **Never fabricate qualifications — that's a content rule, not a config rule.**
  The roles tell each agent to ground claims in the candidate's real experience
  and to *ask* rather than invent. If a JD requirement is a hard gap, the coach
  is instructed to coach honestly around it. This is behavior you set in the
  `role:` prose; the orchestrator can't enforce it, so read the delivered package.

- **Availability shapes the ending.** If `user` is **away** when the coach
  finishes, your final package is *held* (with a `system` "the user is away" ack
  to the coach) rather than lost — read it later with `agentainer user inbox` or
  flip yourself available and it's delivered.

---

## 10. Customize

### Add a `linkedin` agent (summarize to a profile/about section)
Drop in a fourth specialist that turns the resume into a LinkedIn "About" blurb
and a headline, again speaking only to the coach:

```yaml
  - name: linkedin
    type: codex
    can_talk_to: [career_coach]
    command: "codex --yolo"
    role: |
      You are the LINKEDIN WRITER. Using the JD + fit map + tailored resume, write
      a 3-paragraph LinkedIn "About" section and a keyword-rich headline in
      LINKEDIN.md. Mirror the resume's facts exactly; no new claims. Return both to
      the career_coach.
```

Then add `linkedin` to the coach's `can_talk_to` so it can brief the new
specialist: `can_talk_to: [analyzer, resume_writer, cover_writer, linkedin, user]`.

### Swap models per agent
The pipeline isn't Claude-only by necessity. Mix in other supported CLIs (each
`type` must match its `command`):

```yaml
  - name: analyzer
    type: codex
    can_talk_to: [career_coach]
    command: "codex --yolo"          # type and command both say codex
```
`codex` uses its own `notify` hook for turn detection; `gemini`/`hermes` use pane
polling (set `capture: pane`). See
[`use-cases/multi-llm-swarm.md`](./multi-llm-swarm.md) for a full mixed-fleet
example.

### Tune the ACL / flow
- **Parallelize the writers fully:** this swarm already forwards to both writers
  after the single fit map — that's the right shape. Don't let a writer talk to
  the other writer; it invites two divergent stories.
- **Let the analyst also see the finished resume** (a second-pass QA): add
  `resume_writer` to the analyzer's `can_talk_to` *and* `analyzer` to the
  resume_writer's — but now the chain is a ring, so prefer the coach gating it to
  keep one reviewer of record.
- **Add periodic pings** to keep a slow specialist honest:
  a `pings` cron rule on the `cover_writer`, with a
  `message: "still working on the letter?"`.

### Wire it to a real delivery channel
The coach delivers to the virtual `user` mailbox. To get the finished package
pushed to you wherever you are, bridge `user` mail to Telegram (off by default)
via a top-level `telegram:` block in the config — set `mirror_user: true`. See
[`telegram-bridge.md`](../telegram-bridge.md). Keep any bot token out of the
committed file; the `user` mailbox is mirrored, not the agents' internals.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four-folders read/write file model.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resume-by-default and
  `sessions`/`remove-session`.
- [`use-cases/delegation-pipeline.md`](./delegation-pipeline.md) — the hub-and-spoke
  pattern this swarm is built on.
- [`use-cases/multi-llm-swarm.md`](./multi-llm-swarm.md) — running mixed CLI fleets.
- `examples/resume-tailor.yaml` — the config this doc walks through.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

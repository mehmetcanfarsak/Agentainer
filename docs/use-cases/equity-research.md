# Use case: Equity research

A concrete, end-to-end walkthrough of the shipped `examples/equity-research.yaml`
swarm — a SEC-filings + DCF research assembly line that turns a ticker (and a
local EDGAR corpus, or a fetch instruction) into an **investment thesis plus a
valuation opinion**: intrinsic value, margin of safety, and a rating. A
**research-lead** hub takes the request from you, delegates document retrieval to
a **filings-retriever**, the bull/bear case to a **fundamentals-analyst**, and
the valuation to a **valuation-analyst**, then assembles the thesis + the number
and delivers the opinion to you. The lead is the only agent that talks to
`user`, so the point where the thesis meets the valuation lives in exactly one
place.

> ⚠️ **Educational only. Not financial advice.** This swarm is a PAPER /
> SIMULATED research assistant. It does **not** trade, does **not** place orders,
> and ships with **no** real brokerage or market-data keys. Treat its output as a
> structured study aid, never as a buy/sell recommendation. You are responsible
> for any real-world decision.

Everything below is based on the actual contents of
`examples/equity-research.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Investors, students, and analysts who want a *disciplined* read on a company
built from primary documents — the SEC filings — rather than from headlines or
earnings-call chatter. The swarm encodes the discipline that makes a research
note trustworthy: one owner of the human-facing surface, a retriever who only
extracts facts, an analyst who builds the bull/bear case strictly from the
filings, and a valuation specialist who puts a number on it with stated
assumptions. The hub assembles both into one opinion.

This is deliberately a **hub-and-spoke**, not a free-for-all: every request and
every deliverable passes through the research-lead, so the place where the
thesis meets the valuation lives in exactly one agent. Swapping in a second
filings source or a reviewer gate is a few lines of config.

> **How this differs from `earnings-call-analyst`.** This swarm is built around
> **SEC filings (10-K / 10-Q / 8-K)** and a **DCF valuation**. It reads MD&A,
> risk factors, and financial statements — it does *not* process live
> earnings-call transcripts or estimate-surprise prints. If your question is
> "what did management say on the call / did they beat estimates," that is the
> other swarm's job; if it's "what do the filings say the business is worth,"
> this is the one.

---

## 2. The topology

```
          user
            |
       research-lead              (the hub: talks to all three specialists + user)
        /    |    \
 filings-   fundamentals-   valuation-
 retriever   analyst        analyst
 (codex)     (gemini)       (claude)
```

Four agents, one directed flow:

1. **`user` → `research-lead`** — you send a ticker (and optionally a local
   filings corpus path, or where to fetch from), plus the ask (thesis + DCF +
   rating).
2. **`research-lead` → `filings-retriever`** — the lead sends the ticker + source
   and asks for the key extracted sections from the latest 10-K and 10-Q (and any
   material 8-K): MD&A, risk factors, and the financial statements as clean
   structured numbers.
3. **`filings-retriever` → `research-lead`** — the extracted filings come back.
4. **`research-lead` → `fundamentals-analyst`** — the lead hands over the filings
   and asks for a bull and a bear thesis built strictly from the documents.
5. **`fundamentals-analyst` → `research-lead`** — the two-sided thesis comes back.
6. **`research-lead` → `valuation-analyst`** — the lead hands over the same filings
   and asks for a DCF (with explicit assumptions), a comps sanity check, intrinsic
   value per share, margin of safety, and a rating.
7. **`valuation-analyst` → `research-lead`** — the valuation comes back.
8. **`research-lead` → `user`** — the lead assembles the thesis (both sides) + the
   valuation into one investment opinion (labeled PAPER / SIMULATED, not
   financial advice) and delivers it to you.

The routing above is *enforced* by each agent's `can_talk_to` list. The three
specialists **never** talk to `user` (or to each other) — only the research-lead
does. If a specialist tried to mail `user` directly, the orchestrator bounces it
as a `system` message and files it in `failed/`.

---

## 3. The config, explained

Here is `examples/equity-research.yaml` in full (role bodies abbreviated with
`...` for readability; the structure, names, ACLs, and commands are exact):

```yaml
swarm:
  name: equity-research
  root: ./equity-research-workspace

defaults:
  capture: none              # claude/codex are auto-upgraded to their hook at up
  can_talk_to: []            # tightened per agent below

agents:
  - name: research-lead
    type: claude
    can_talk_to: [filings-retriever, fundamentals-analyst, valuation-analyst, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the RESEARCH-LEAD and the only agent who talks to the human (user).
      ... (1) ask ONE clarifying question if scope is ambiguous; (2) delegate
      retrieval to FILINGS-RETRIEVER; (3) delegate the bull/bear thesis to
      FUNDAMENTALS-ANALYST; (4) delegate the DCF to VALUATION-ANALYST; (5) assemble
      the thesis + the valuation into ONE investment opinion and post it to user,
      labeled PAPER/SIMULATED, not financial advice. ...

  - name: filings-retriever
    type: codex
    can_talk_to: [research-lead]
    command: "codex --yolo"
    role: |
      You are the FILINGS-RETRIEVER. Given a ticker + source, fetch/parse the SEC
      filings (10-K / 10-Q / 8-K) and extract MD&A, risk factors, and the
      financial statements as clean structured numbers ... Report ONLY to the
      research-lead. ...

  - name: fundamentals-analyst
    type: gemini
    can_talk_to: [research-lead]
    command: "gemini --yolo"
    role: |
      You are the FUNDAMENTALS-ANALYST. From the extracted filings, build a bull
      and a bear thesis -- strictly from what the filings say -- with the filing
      evidence for each side ... No valuation. Report ONLY to the research-lead. ...

  - name: valuation-analyst
    type: claude
    can_talk_to: [research-lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the VALUATION-ANALYST. From the extracted filings, build a DCF (with
      explicit WACC / terminal-growth / margin assumptions), a comps sanity check,
      intrinsic value per share, margin of safety, and a rating ... Report ONLY to
      the research-lead. ...
```

Field by field:

### `swarm`
- **`name: equity-research`** — the swarm's name (shows up in `status`, logs,
  sessions).
- **`root: ./equity-research-workspace`** — the parent directory for the agents'
  working directories and mailboxes. Each agent's workdir defaults to
  `equity-research-workspace/<name>` (research-lead, filings-retriever,
  fundamentals-analyst, valuation-analyst), and orchestrator state goes under
  `equity-research-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless it overrides them.
- **`capture: none`** — the default turn-detection mode. At `up`, the loader
  **auto-upgrades** this for `claude` and `codex` to their natural hook (the
  `validate` run prints three warnings confirming it — see §3 turn-detection
  below). It is a safe floor; every agent states its own `can_talk_to`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Each agent below
  states its own list explicitly.

### `research-lead` (type: `claude`)
- **`can_talk_to: [filings-retriever, fundamentals-analyst, valuation-analyst, user]`**
  — the lead is the hub and the **only agent that can talk to `user`**. That last
  part is the whole point: keep the human-facing surface to one agent so the
  thesis and the valuation only ever meet inside it.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code
  in its tmux pane. (Placeholder — substitute your own launch command, e.g. a
  shell alias. Treat command strings as sensitive; they may embed keys, such as
  an EDGAR access token.)
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at
  `up`; the `capture: none` default is auto-upgraded to hook here).

### `filings-retriever` (type: `codex`)
- **`can_talk_to: [research-lead]`** — fetches and parses the filings and returns
  the extracted sections to the lead, and nowhere else. It cannot reach the user,
  the analyst, or the valuation specialist directly.
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`
  (the `capture: none` default auto-upgrades to the notify hook).

### `fundamentals-analyst` (type: `gemini`)
- **`can_talk_to: [research-lead]`** — receives the extracted filings from the
  lead and returns the bull/bear thesis to the lead only. It never touches the
  user or the other spokes.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **Turn detection:** `gemini` has no completion hook, so it relies on **pane
  polling** — the supervisor watches its pane for turn completion. (This is why
  the `capture: none` default needs no upgrade for gemini; only claude/codex get
  the auto-hook warnings.)

### `valuation-analyst` (type: `claude`)
- **`can_talk_to: [research-lead]`** — receives the extracted filings from the
  lead and returns the DCF / comps / rating to the lead only. It cannot reach the
  user, so its number is always relayed through the hub.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook (auto-upgraded from `capture: none`).

### ACL enforcement

The ACL is **cooperative, not OS isolation** (Decision D15): agents have
filesystem access and *could* write straight into another inbox, but the
orchestrator only ever *releases* and *routes* mail between names on the
sender's `can_talk_to` list. Anything addressed outside that list is bounced back
as a `system` message filed in `failed/`, so a model that forgets the rule
self-corrects in-band. Here that means the three specialists can *only* reach the
research-lead, and only the research-lead can reach `user` — the assembly of the
thesis and the valuation is structurally guaranteed to happen in one place.

### Per-type turn detection

Turn-completion detection is the system clock (the stop → sweep → route → release
→ nudge loop). It is **per `type`**:
- `claude` (`research-lead`, `valuation-analyst`) → **Stop hook** — fires when
  Claude finishes a turn.
- `codex` (`filings-retriever`) → **`notify` hook** — fires when Codex finishes.
- `gemini` (`fundamentals-analyst`) → **pane polling** — the supervisor reads the
  pane to decide the turn ended.

A `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
Claude) means completion never fires and the agent pins "busy" forever — which is
why `command` must launch the same CLI family `type` implies.

### What's *not* in this config
- **No per-agent `capture` overrides.** The `defaults: capture: none` is
  auto-upgraded to the type's hook for claude/codex; gemini uses pane polling.
- **No `pings:` block.** Every run is event-driven off your mail (you send the
  ticker + ask; the loop advances one turn at a time). Add a `pings:` to the
  research-lead if you want a periodic "re-run the coverage on your watchlist"
  nudge — see [`configuration.md`](../configuration.md).
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **No real brokerage/EDGAR keys.** The swarm is paper/simulated by default. If
  you wire a data source whose `command` carries a token, treat that string as
  sensitive.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/equity-research.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the three `capture: none` auto-upgrade
   warnings for the claude/codex agents.
2. Creates the runtime dirs (`equity-research-workspace/.agentainer/…`: log,
   queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/`
   `about.md` contact card *is* the ACL made visible: the research-lead gets
   `outbox/filings-retriever/`, `outbox/fundamentals-analyst/`,
   `outbox/valuation-analyst/`, `outbox/user/`; each specialist gets only
   `outbox/research-lead/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `research-lead`
   and `valuation-analyst`, the Codex `notify` hook for `filings-retriever`; the
   gemini agent is covered by pane polling.
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents (and drives gemini's pane polling) so one stuck agent can't
   wedge the swarm.

At the end, `up` prints attach and **`serve`** hints. The `serve` line gives you
the mail-app control-plane UI (threads, live panes, send-as-user, availability
toggle). Drop `--host`/`--token` for the safe loopback-only `127.0.0.1` bind —
the UI can start processes, edit config, and type into agents that may run with
elevated permissions, so it must **never** be exposed on `0.0.0.0` without a
token. See [`ui-guide.md`](../ui-guide.md).

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole retrieve→thesis→valuation loop route mail with no API keys — the
> mechanics are identical.

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the finished investment opinion as mail
(rather than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/equity-research.yaml
```

This rewrites the `user` contact card in the research-lead's `outbox/user/about.md`
to `Status: available`, so the lead sees you're reachable. (While away, mail
to you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the research request into the swarm, addressed to the research-lead:

```bash
./agentainer send --to research-lead -c examples/equity-research.yaml \
  "Research ACME. Pull its latest 10-K and 10-Q from the local EDGAR corpus \
   (./corpus/acme/), build a bull/bear thesis from the filings, run a DCF, and \
   give me an investment opinion with a margin of safety and a rating."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the research-lead, then — because
the inbox was empty — **released into `inbox/`** and the lead is **nudged**
(the protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the research loop advance one turn at a time.
Each arrow is a `stop → sweep → route → release → nudge` cycle:

1. **research-lead receives the request.** It reads `inbox/`, asks its one
   clarifying question if scope is ambiguous, then writes a delegation into
   `outbox/filings-retriever/`. On stop, that routes to the filings-retriever.
2. **filings-retriever extracts the filings.** It reads its inbox, fetches/parses
   the 10-K/10-Q/8-K, and reports the structured sections (MD&A, risk factors,
   financials) back into `outbox/research-lead/`. On stop, that routes to the lead.
3. **research-lead briefs the fundamentals-analyst.** It writes the extracted
   filings into `outbox/fundamentals-analyst/`. On stop, that routes to the
   fundamentals-analyst.
4. **fundamentals-analyst drafts the bull/bear thesis.** It reads its inbox,
   writes the two-sided thesis, and reports back into `outbox/research-lead/`. On
   stop, that routes to the lead.
5. **research-lead briefs the valuation-analyst.** It writes the same extracted
   filings into `outbox/valuation-analyst/`. On stop, that routes to the
   valuation-analyst.
6. **valuation-analyst runs the DCF.** It reads its inbox, writes the DCF + comps
   + rating, and reports back into `outbox/research-lead/`. On stop, that routes
   to the lead.
7. **research-lead assembles the opinion and posts it.** It combines the thesis
   (both sides) + the valuation (intrinsic value, margin of safety, rating) into
   one investment opinion — explicitly labeled PAPER / SIMULATED, not financial
   advice — and writes it into `outbox/user/`. On stop, that's delivered to your
   `user` mailbox.
8. **you get the opinion** — visible with `agentainer user inbox`, or in the UI.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion. If you
never send anything, the agents just sit in standby.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/equity-research.yaml
```

```
swarm: equity-research   root: ./equity-research-workspace
  research-lead      (claude) up idle queue=0 unread=0 talks=filings-retriever, fundamentals-analyst, valuation-analyst, user
  filings-retriever  (codex)  up idle queue=0 unread=1 talks=research-lead
  fundamentals-analyst (gemini) up idle queue=0 unread=0 talks=research-lead
  valuation-analyst  (claude) up idle queue=0 unread=0 talks=research-lead
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/equity-research.yaml          # whole swarm, last 20
./agentainer logs -c examples/equity-research.yaml -f        # follow live
./agentainer logs valuation-analyst -c examples/equity-research.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox research-lead -c examples/equity-research.yaml
```

Prints the one released message (headers + body), or `research-lead: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue research-lead -c examples/equity-research.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach valuation-analyst -c examples/equity-research.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or by sending notes
into an agent's inbox.

- **Send a clarification to the lead.** Realized the corpus lives elsewhere?
  `./agentainer send --to research-lead -c examples/equity-research.yaml
  "Re-brief the filings-retriever: the corpus is ./filings/acme/, not ./corpus/."`
  The lead relays the change down the chain.
- **Ask the valuation-analyst to show its WACC.** `./agentainer inbox research-lead`
  (or the UI) shows the valuation note the lead received — the assumptions behind
  the intrinsic value — so you can sanity-check the number.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/equity-research.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agentainer up -c examples/equity-research.yaml     # resume is the default
```

On `up`, Agentainer reads `equity-research-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the
research-lead and valuation-analyst, `codex resume <id>` for the
filings-retriever, and the gemini session via its recorded id. A resumed agent is
*not* re-sent the standby prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/equity-research.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a reviewer gate (optional)
The spec allows a reviewer gate in front of the human-facing opinion. Add a fifth
agent and put it in the lead's `can_talk_to`:

```yaml
  - name: reviewer
    type: claude
    can_talk_to: [research-lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the REVIEWER. The research-lead routes you the assembled opinion
      (bull/bear thesis + DCF + rating). Check hard: the thesis cites only filing
      evidence; the DCF assumptions are stated and internally consistent; the
      intrinsic value, margin of safety, and rating agree; the "not financial
      advice" disclaimer is present. Reply CLEAR or BOUNCE with specific defects.
      The human should never see an opinion you have not signed off. Report ONLY to
      the research-lead.
```

Then add `reviewer` to `research-lead`'s `can_talk_to` so it can be briefed, and
have the lead route the assembled draft to the reviewer and wait for `CLEAR`
before posting to `user` — mirroring `examples/fp-and-a-analyst.yaml`'s gate.

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `filings-retriever: type: claude` (or `hermes`/`gemini`) to put retrieval on a
  different model than the lead.
- `valuation-analyst: type: codex` if you want the DCF on Codex while keeping
  claude for the lead.
- Remember: `gemini`/`hermes` rely on **pane polling** (no completion hook), so
  they don't need (and shouldn't be given) a hook-based `capture`.

### Tune the ACL
- To let the `valuation-analyst` escalate straight to `user` (not only via the
  lead), add `user` to its `can_talk_to`. Mind that this widens the human-facing
  surface and bypasses the lead's single-funnel guarantee — the doc's convention
  keeps the lead the sole `user` contact so the assembly always happens in one
  place.
- To make a specialist unreachable from anyone but the lead (already the case
  here), leave its `can_talk_to: [research-lead]` — that's the one-place-owns-the-
  assembly guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md)
  for mixing model families safely.

### Add a periodic re-run ping
Every run here is event-driven off your mail. If you want a standing "re-run my
watchlist" nudge, add a `pings:` block to `research-lead`:

```yaml
  pings:
    - message: |
        Re-run coverage on the watchlist (ACME, BEAR, CROW). For each, pull the
        latest filings, rebuild the bull/bear thesis, refresh the DCF, and post
        the updated opinions to user.
      cron: "0 7 * * 1"             # 07:00 every Monday
      when_busy: skip
```

See [`configuration.md`](../configuration.md) for the full `pings:` / `cron:` /
`when_busy` grammar.

---

## 10. Tips & footguns

- **Keep the lead the only `user`-facing agent.** Only the lead lists `user` in
  `can_talk_to`. That gives you a single funnel: the bull/bear thesis and the DCF
  only ever meet inside the lead, so the one opinion you receive is the assembled
  whole. If a specialist tried to mail `user` directly, the orchestrator bounces
  it (ACL) and drops a `system` note in their inbox explaining who they *can*
  message — the model self-corrects in-band.

- **The output is a study, not advice.** The research-lead's opinion must carry
  the PAPER / SIMULATED, not-financial-advice label. Nothing in this swarm places
  an order or touches a brokerage — if you wire a live data key into a `command`,
  that's for *retrieval/valuation inputs only*, never execution.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch (e.g. a `claude` agent whose `command`
  doesn't launch Claude, or a `gemini` agent whose pane never settles) means
  completion never triggers and the agent pins "busy" forever. `status` showing
  an agent `busy` for a long time with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops — relevant if a specialist and the lead
  chatter past the gate.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/equity-research.yaml
  ./agentainer remove-session -c examples/equity-research.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files (the filings you dropped in)
  or your config.

- **Availability shapes the ending.** If `user` is **away** when the lead
  finishes, your investment opinion is *held* (with a `system` "the user is away"
  ack to the lead) rather than lost — read it later with `agentainer user inbox`
  or flip yourself available and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions (`--dangerously-skip-permissions`,
  `--yolo`).

- **Command strings may carry keys.** A `command` that wires an EDGAR access
  token or a data-provider key embeds a secret via a shell alias. Don't print or
  commit the resolved command; treat the alias as sensitive.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- `examples/equity-research.yaml` — the config this walkthrough is built on.
- `examples/fp-and-a-analyst.yaml` — a sibling finance example (variance + forecast
  narrative) that shows the same hub-and-spoke + reviewer-gate pattern.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

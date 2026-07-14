# Use case: Crypto / on-chain research

A concrete, end-to-end walkthrough of the shipped
`examples/crypto-onchain-research.yaml` swarm — a DeFi protocol + on-chain data
research desk that turns a protocol question (TVL, flows, smart-contract risk,
tokenomics) into a gated, plain-English, **educational** brief. A **crypto-lead**
hub takes the request from you and coordinates four specialists: an
**onchain-analyst** (what the chain says), a **defi-reviewer** (protocol +
smart-contract risk), a **tokenomics-analyst** (supply/emissions/vesting), and a
**risk-gate** that sanity-gates every recommendation before anything reaches the
human — and may **withhold** a conclusion rather than ship an unsafe one.

> **High-risk domain.** Crypto and DeFi are speculative, volatile, and frequently
> exploited asset classes. This swarm is **educational, not financial advice**,
> and is **simulated / research-only**: it does not trade, custody funds, or emit
> buy/sell signals, and runs with **no real keys by default**. The `risk-gate` exists
> precisely because the domain is dangerous.

Everything below is based on the actual contents of
`examples/crypto-onchain-research.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Who this is for

Researchers, builders, and the merely curious who want a sober, sourced read on a
DeFi protocol or an on-chain dataset without doing the RPC/explorer legwork or
reading Solidity themselves. The swarm encodes the discipline that makes a crypto
writeup *trustworthy* in a high-risk domain: one owner of the human-facing
surface, a data analyst who never forms an opinion, a reviewer who never advises,
a tokenomics modeler who never predicts, and a **risk gate** that holds the line
between "what the data shows" and "what you should do."

It is deliberately a **hub-and-spoke**, not a free-for-all: every request and
every deliverable passes through the crypto-lead, so the point where the
specialists' reports meet the human (and where the risk gate sits) lives in
exactly one place. Swapping in a different data source or adding a second reviewer
is a few lines of config.

---

## 2. The topology

```
  onchain-analyst --\
  defi-reviewer   ---> crypto-lead <--> user
  tokenomics-     --/            |
    analyst                    risk-gate
                              (the GATE -- clears / withholds every rec)
```

Five agents, one directed flow:

1. **`user` → `crypto-lead`** — you send a protocol to review, an on-chain
   question (TVL/flows), or a token to assess (as files, a paste, or a location
   to read).
2. **`crypto-lead` → `onchain-analyst`** — the lead sends the scope and asks for
   the on-chain facts (TVL + how it's computed, 30d flows, large-holder moves,
   contract balances, chain-revealed red flags). Numbers only.
3. **`onchain-analyst` → `crypto-lead`** — the facts come back.
4. **`crypto-lead` → `defi-reviewer`** — the lead sends the protocol + contract
   scope and asks for a risk review (audit history, exploit history,
   upgradeability, privileged roles, bridge/oracle exposure). Risk only.
5. **`defi-reviewer` → `crypto-lead`** — the risk review comes back.
6. **`crypto-lead` → `tokenomics-analyst`** — the lead sends the token scope and
   asks for supply/emissions/vesting/incentive-sustainability mechanics. Mechanics
   only.
7. **`tokenomics-analyst` → `crypto-lead`** — the model comes back.
8. **`crypto-lead` → `risk-gate`** — the lead assembles the three reports into
   one brief and routes it to the risk gate. The gate is the **sanity gate for a
   high-risk asset class**: it checks the claims are backed, the risk isn't
   understated, the brief is *not* advice or a call to action, and the no-real-keys
   default is intact. It replies `CLEAR`, `BOUNCE` (specific defects), or
   `WITHHOLD` (it refuses to ship a conclusion at all).
9. **`risk-gate` → `crypto-lead`** — on `BOUNCE`, the lead re-delegates the fix
   (back to the relevant specialist) and re-routes until the gate clears or
   withholds. On `CLEAR`, the lead writes the final educational brief.
10. **`crypto-lead` → `user`** — the gated brief is delivered to you.

The routing above is *enforced* by each agent's `can_talk_to` list. The four
specialists **never** talk to `user` (or to each other) — only the crypto-lead
does. If a specialist tried to mail `user` directly, the orchestrator bounces it
as a `system` message and files it in `failed/`.

---

## 3. The config, explained

Here is `examples/crypto-onchain-research.yaml` in full (role bodies abbreviated
with `...` for readability; the structure, names, ACLs, and commands are exact):

```yaml
swarm:
  name: crypto-onchain-research
  root: ./crypto-onchain-research-workspace

defaults:
  capture: none              # claude/codex are auto-upgraded to their hook at up
  can_talk_to: []            # tightened per agent below

agents:
  - name: crypto-lead
    type: claude
    can_talk_to: [onchain-analyst, defi-reviewer, tokenomics-analyst, risk-gate, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the CRYPTO-LEAD and the only agent who talks to the human (user). ...
      EDUCATIONAL, NOT FINANCIAL ADVICE, SIMULATED / RESEARCH-ONLY. Never trade,
      never custody, never emit buy/sell signals, no real keys. ...
      (1) clarify scope; (2) delegate to ONCHAIN-ANALYST; (3) delegate to
       DEFI-REVIEWER; (4) delegate to TOKENOMICS-ANALYST; (5) assemble the brief
       and route to RISK-GATE -- the sanity gate -- and re-route until it CLEARs
       or WITHHOLDS; (6) only then post the final brief to user. ...

  - name: onchain-analyst
    type: codex
    can_talk_to: [crypto-lead]
    command: "codex --yolo"
    role: |
      You are the ONCHAIN-ANALYST. Read the on-chain facts -- TVL, flows,
      balances, large-holder moves, red flags ... numbers only, no recommendation.
      Report ONLY to the crypto-lead. ...

  - name: defi-reviewer
    type: claude
    can_talk_to: [crypto-lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the DEFI-REVIEWER. Review protocol + smart-contract risk -- audit
      history, exploit history, upgradeability, privileged roles, bridge/oracle
      exposure ... risk only, no recommendation. Report ONLY to the crypto-lead. ...

  - name: tokenomics-analyst
    type: gemini
    can_talk_to: [crypto-lead]
    command: "gemini --yolo"
    role: |
      You are the TOKENOMICS-ANALYST. Model supply/emissions/vesting/incentive
      sustainability ... mechanics only, no recommendation. Report ONLY to the
      crypto-lead. ...

  - name: risk-gate
    type: claude
    can_talk_to: [crypto-lead]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the RISK-GATE -- the sanity gate for a HIGH-RISK asset class. Check
      claims backed, risk not understated, NOT advice / NOT a call, no real keys,
      legible ... reply CLEAR, BOUNCE, or WITHHOLD. The human must NEVER see a
      brief you have not signed off. Report ONLY to the crypto-lead. ...
```

Field by field:

### `swarm`
- **`name: crypto-onchain-research`** — the swarm's name (shows up in `status`,
  logs, sessions).
- **`root: ./crypto-onchain-research-workspace`** — the parent directory for the
  agents' working directories and mailboxes. Each agent's workdir defaults to
  `crypto-onchain-research-workspace/<name>` (crypto-lead, onchain-analyst,
  defi-reviewer, tokenomics-analyst, risk-gate), and orchestrator state goes under
  `crypto-onchain-research-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless it overrides them.
- **`capture: none`** — the default turn-detection mode. At `up`, the loader
  **auto-upgrades** this for `claude` and `codex` to their natural hook (the
  `validate` run prints warnings confirming it). It is a safe floor; every agent
  states its own `can_talk_to`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Each agent below
  states its own list explicitly.

### `crypto-lead` (type: `claude`)
- **`can_talk_to: [onchain-analyst, defi-reviewer, tokenomics-analyst, risk-gate, user]`**
  — the lead is the hub and the **only agent that can talk to `user`**. That last
  part is the whole point: keep the human-facing surface to one agent and put the
  risk gate in front of it. It also carries the **no-trade / no-real-keys** rule
  at the top of its role, so the swarm can't drift into advice.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`;
  the `capture: none` default is auto-upgraded to hook here).

### `onchain-analyst` (type: `codex`)
- **`can_talk_to: [crypto-lead]`** — reports the on-chain facts back to the lead
  and nowhere else. It cannot reach the user or the other spokes.
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`
  (the `capture: none` default auto-upgrades to the notify hook).

### `defi-reviewer` (type: `claude`)
- **`can_talk_to: [crypto-lead]`** — returns the contract-risk review to the lead
  only. It never touches the user.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook (auto-upgraded from `capture: none`).

### `tokenomics-analyst` (type: `gemini`)
- **`can_talk_to: [crypto-lead]`** — returns the tokenomics model to the lead
  only. It never touches the user.
- **`command: "gemini --yolo"`** — placeholder launch command.
- **Turn detection:** `gemini` has no completion hook, so it relies on **pane
  polling** — the supervisor watches its pane for turn completion. (This is why
  the `capture: none` default needs no upgrade for gemini; only claude/codex get
  the auto-hook warnings.)

### `risk-gate` (type: `claude`)
- **`can_talk_to: [crypto-lead]`** — the gate lives behind the lead: it only ever
  talks to the lead, replying `CLEAR` / `BOUNCE` / `WITHHOLD`. It cannot reach the
  user, so its verdict is always relayed through the hub.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.
- **Turn detection:** `claude` → Stop hook (auto-upgraded from `capture: none`).

### ACL enforcement

The ACL is **cooperative, not OS isolation** (Decision D15): agents have
filesystem access and *could* write straight into another inbox, but the
orchestrator only ever *releases* and *routes* mail between names on the
sender's `can_talk_to` list. Anything addressed outside that list is bounced back
as a `system` message filed in `failed/`, so a model that forgets the rule
self-corrects in-band. Here that means the four specialists can *only* reach the
crypto-lead, and only the crypto-lead can reach `user` — the risk gate is
structurally guaranteed to sit between the draft and the human.

> Note: `risk-gate` does **not** list `user` in `can_talk_to`. The gate's verdict
> is always relayed through the crypto-lead, which keeps the human-facing surface
> to one agent — the same single-funnel guarantee as the FP&A reviewer, but with a
> `WITHHOLD` outcome allowed for an unsafe conclusion.

### Per-type turn detection

Turn-completion detection is the system clock (the stop → sweep → route → release
→ nudge loop). It is **per `type`**:
- `claude` (`crypto-lead`, `defi-reviewer`, `risk-gate`) → **Stop hook** — fires
  when Claude finishes a turn.
- `codex` (`onchain-analyst`) → **`notify` hook** — fires when Codex finishes.
- `gemini` (`tokenomics-analyst`) → **pane polling** — the supervisor reads the
  pane to decide the turn ended.

A `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
Claude) means completion never fires and the agent pins "busy" forever — which is
why `command` must launch the same CLI family `type` implies.

### What's *not* in this config
- **No `pings:` block.** Unlike the FP&A example (which self-starts a monthly
  close), this swarm is purely event-driven off your mail — a crypto research
  question is ad-hoc, not a recurring tick. Add a `pings:` to any agent if you
  want a scheduled scan.
- **No per-agent `capture` overrides.** The `defaults: capture: none` is
  auto-upgraded to the type's hook for claude/codex; gemini uses pane polling.
- **No `workdir` overrides.** All five agents get the default
  `crypto-onchain-research-workspace/<name>`, so no mailbox namespacing is needed.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).
- **No real keys, by design.** The roles forbid trading, custody, and buy/sell
  signals, and the `command` lines are placeholders — swap in your CLIs with care
  and never embed live keys you don't intend to expose.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/crypto-onchain-research.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints the auto-upgrade warnings for the
   claude/codex agents.
2. Creates the runtime dirs (`crypto-onchain-research-workspace/.agentainer/…`:
   log, queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. The `outbox/<peer>/`
   `about.md` contact card *is* the ACL made visible: the crypto-lead gets
   `outbox/onchain-analyst/`, `outbox/defi-reviewer/`, `outbox/tokenomics-analyst/`,
   `outbox/risk-gate/`, `outbox/user/`; each specialist gets only
   `outbox/crypto-lead/`.
4. **Installs per-type turn detection** — the Claude Stop hook for `crypto-lead`,
   `defi-reviewer`, `risk-gate`; the Codex `notify` hook for `onchain-analyst`;
   the gemini agent is covered by pane polling.
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
> the whole onchain→review→tokenomics→gate loop route mail with no API keys — the
> mechanics are identical.

---

## 5. Drive it

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the crypto-lead's finished brief as mail
(rather than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/crypto-onchain-research.yaml
```

This rewrites the `user` contact card in the crypto-lead's `outbox/user/about.md`
to `Status: available`, so the lead sees you're reachable. (While away, mail to
you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send a research question into the swarm, addressed to the crypto-lead:

```bash
./agentainer send --to crypto-lead -c examples/crypto-onchain-research.yaml \
  "Research Uniswap v3: pull TVL and 30d flows from on-chain, summarize the \
   protocol's smart-contract risk and audit history, model the UNI tokenomics, \
   and give me a gated, educational read on what the data shows."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the crypto-lead, then — because
the inbox was empty — **released into `inbox/`** and the crypto-lead is **nudged**
(the protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the research loop advance one turn at a time.
Each arrow is a `stop → sweep → route → release → nudge` cycle:

1. **crypto-lead receives the question.** It reads `inbox/`, asks its one
   clarifying question if scope is ambiguous, then writes delegations into
   `outbox/onchain-analyst/`, `outbox/defi-reviewer/`, `outbox/tokenomics-analyst/`.
   On stop, those route to the specialists.
2. **onchain-analyst reports the facts.** It reads its inbox, pulls TVL/flows/
   balances, and reports back into `outbox/crypto-lead/`. On stop, that routes to
   the lead.
3. **defi-reviewer reports contract risk.** It reads its inbox, writes the audit/
   exploit/upgradeability review, and reports back into `outbox/crypto-lead/`. On
   stop, that routes to the lead.
4. **tokenomics-analyst reports mechanics.** It reads its inbox, models supply/
   emissions/vesting, and reports back into `outbox/crypto-lead/`. On stop, that
   routes to the lead.
5. **crypto-lead assembles the brief and routes to risk-gate.** The lead writes
   the combined brief into `outbox/risk-gate/`. On stop, that routes to the gate.
6. **risk-gate gates it.** It reads the brief and replies `CLEAR`, `BOUNCE` (with
   specific defects), or `WITHHOLD` (refuses to ship an unsafe conclusion) into
   `outbox/crypto-lead/`. On `BOUNCE`, the lead re-delegates the fix and re-routes
   until the gate clears. On `CLEAR`, the lead writes the final educational brief
   into `outbox/user/`. On stop, that's delivered to your `user` mailbox.
7. **you get the gated brief** — visible with `agentainer user inbox`, or in the
   UI.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion. If you
never send anything, the agents just sit in standby.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/crypto-onchain-research.yaml
```

```
swarm: crypto-onchain-research   root: ./crypto-onchain-research-workspace
  crypto-lead       (claude) up idle queue=0 unread=0 talks=onchain-analyst, defi-reviewer, tokenomics-analyst, risk-gate, user
  onchain-analyst   (codex)  up idle queue=0 unread=1 talks=crypto-lead
  defi-reviewer     (claude) up idle queue=0 unread=0 talks=crypto-lead
  tokenomics-analyst(gemini) up idle queue=0 unread=0 talks=crypto-lead
  risk-gate         (claude) up idle queue=0 unread=0 talks=crypto-lead
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/crypto-onchain-research.yaml          # whole swarm, last 20
./agentainer logs -c examples/crypto-onchain-research.yaml -f        # follow live
./agentainer logs risk-gate -c examples/crypto-onchain-research.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox crypto-lead -c examples/crypto-onchain-research.yaml
```

Prints the one released message (headers + body), or `crypto-lead: inbox is empty`.

**Queue depth** — mail waiting behind the one released message:

```bash
./agentainer queue crypto-lead -c examples/crypto-onchain-research.yaml
```

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach risk-gate -c examples/crypto-onchain-research.yaml
```

Detach with the usual tmux `Ctrl-b d`. (Typing into a pane bypasses the mailroom —
handy for un-sticking an agent, but the mail model is the normal path.)

---

## 7. Iterate on the result

The first pass rarely nails it. Because every message is natural-language mail,
you can steer the swarm mid-flight through the `user` mailbox or by sending notes
into an agent's inbox.

- **Send a clarification to the crypto-lead.** Realized you meant a different
  chain or contract? `./agentainer send --to crypto-lead -c
  examples/crypto-onchain-research.yaml "Re-scope to Arbitrum, contract
  0x...., last 90d."` The lead relays the change down the chain and re-routes past
  the gate.
- **Ask the gate what it bounced.** `./agentainer inbox crypto-lead` (or the UI)
  shows the `BOUNCE`/`WITHHOLD` note the lead received — which claim, which number,
  what's wrong — so you can see the gate doing its job.
- **Tune via the UI.** The `serve` mail-app exposes every thread, lets you send as
  `user`, toggle `user` availability, and watch panes live — useful when you want
  to nudge a specific agent without guessing its name.

When you're happy (or want to try a different framing), tear it down:

```bash
./agentainer down -c examples/crypto-onchain-research.yaml
```

---

## 8. Resume after a stop

Bringing the swarm back later resumes conversations by default:

```bash
./agenttainer up -c examples/crypto-onchain-research.yaml     # resume is the default
```

On `up`, Agentainer reads `crypto-onchain-research-workspace/.agentainer/sessions.yaml`
(written as each agent finished its first turn) and reattaches the recorded
conversations via each type's native resume: `claude --resume <id>` for the
crypto-lead, defi-reviewer, and risk-gate, `codex resume <id>` for the
onchain-analyst, and the gemini session via its recorded id. A resumed agent is
*not* re-sent the standby prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/crypto-onchain-research.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 9. Customize

This swarm is a starting point. A few common adjustments:

### Add a scheduled scan
The shipped config has no `pings:` (research is ad-hoc). To get a recurring
protocol-health check, add a `pings:` block to the crypto-lead:

```yaml
  - name: crypto-lead
    type: claude
    can_talk_to: [onchain-analyst, defi-reviewer, tokenomics-analyst, risk-gate, user]
    command: "claude --dangerously-skip-permissions"
    pings:
      - message: |
          Scheduled scan: pick the protocol in WATCHLIST.md, run the full
          onchain -> defi-reviewer -> tokenomics -> risk-gate loop, and post the
          gated brief to user. If WATCHLIST.md is empty, ask the user which
          protocol to scan before delegating.
        cron: "0 9 * * 1"             # 09:00 every Monday
        when_busy: skip
    role: |
      ...
```

### Swap models
The `type` selects both the CLI family and the turn-detection mode. Any of these
work, as long as `command` launches that same family (a `type`/`command` mismatch
wedges the agent — see [`cli-reference.md`](../cli-reference.md)):
- `onchain-analyst: type: claude` (or `hermes`/`gemini`) to put the data pull on a
  different model than the lead.
- `tokenomics-analyst: type: claude` if you want the model on Claude while keeping
  gemini out.
- Remember: `gemini`/`hermes` rely on **pane polling** (no completion hook), so
  they don't need (and shouldn't be given) a hook-based `capture`.

### Tune the ACL
- To let the `risk-gate` escalate straight to `user` (not only via the lead), add
  `user` to its `can_talk_to`. Mind that this widens the human-facing surface and
  bypasses the lead's single-funnel guarantee — the doc's convention keeps the
  lead the sole `user` contact so the gate always sits in front.
- To make a specialist unreachable from anyone but the lead (already the case
  here), leave its `can_talk_to: [crypto-lead]` — that's the one-place-owns-the-gate
  guarantee.
- See [`delegation-pipeline.md`](./delegation-pipeline.md) for a broader
  discussion of hub-and-spoke routing, and [`multi-llm-swarm.md`](./multi-llm-swarm.md)
  for mixing model families safely.

### Keep it research-only
The no-trade / no-custody / no-real-keys discipline lives in the roles, not the
code. If you wire in a real RPC key or an exchange API, treat it as sensitive and
keep `command` strings out of commits. The swarm will still refuse to trade or
emit buy/sell signals because every agent's role forbids it.

---

## 10. Tips & footguns

- **Keep the crypto-lead the only `user`-facing agent.** Only the lead lists
  `user` in `can_talk_to`. That gives you a single funnel: raw facts, contract
  risk, and tokenomics always pass through the risk gate before they reach you. If
  a specialist tries to mail `user` directly, the orchestrator bounces it (ACL)
  and drops a `system` note in their inbox explaining who they *can* message — the
  model self-corrects in-band.

- **The gate's `WITHHOLD` is the feature, not a failure.** In a high-risk domain,
  the right answer is sometimes "I will not ship a conclusion." `WITHHOLD` means
  the data didn't support a safe read, and the lead relays that honestly. Don't
  "fix" this by weakening the gate or widening ACLs — the gate is how the human
  stays protected.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, check that its **turn detection actually
  fires** — a `type`/`command` mismatch (e.g. a `claude` agent whose `command`
  doesn't launch Claude, or a `gemini` agent whose pane never settles) means
  completion never triggers and the agent pins "busy" forever. `status` showing an
  agent `busy` for a long time with `unread` mail is the tell.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is just a best-effort receipt, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s) to
  kill "thanks!/you're welcome!" loops — relevant if a specialist and the lead
  chatter past the gate.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/crypto-onchain-research.yaml
  ./agentainer remove-session -c examples/crypto-onchain-research.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files (the data you dropped in) or
  your config.

- **Availability shapes the ending.** If `user` is **away** when the lead finishes,
  your brief is *held* (with a `system` "the user is away" ack to the lead) rather
  than lost — read it later with `agentainer user inbox` or flip yourself available
  and it's delivered.

- **UI binding is a control plane.** Never run `serve` on `0.0.0.0` without
  `--token`; prefer the loopback `127.0.0.1` default. The UI can type into agents
  that may run with elevated permissions (`--dangerously-skip-permissions`,
  `--yolo`).

- **This is educational, not financial advice.** The brief describes what on-chain
  data and contract reviews show — it never tells the reader to buy, sell, stake,
  or custody. Treat any conclusion as research, not a recommendation, and do your
  own diligence.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing works.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — resuming after a stop.
- [`delegation-pipeline.md`](./delegation-pipeline.md) — hub-and-spoke routing patterns.
- [`multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing model families safely.
- [`custom-workspace.md`](./custom-workspace.md) — shared workdirs + mailbox namespacing.
- `examples/crypto-onchain-research.yaml` — the config this walkthrough is built on.
- `examples/fp-and-a-analyst.yaml` — the sibling "gated hub" pattern (CFO memo).
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

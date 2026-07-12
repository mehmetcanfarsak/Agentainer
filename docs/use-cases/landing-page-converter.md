# Use case: the landing-page converter swarm

A concrete, end-to-end walkthrough of the shipped
`examples/landing-page-converter.yaml` swarm — a four-agent pipeline where a
**brief_analyst** hub turns a raw product brief into high-converting
landing-page copy: hero + body, A/B + CTA variants, and a final
conversion-focused edit. It's the canonical "extract strategy → write copy →
test variants → tighten" loop, wired entirely through Agentainer's file-based
mail model.

Everything below is based on the actual contents of
`examples/landing-page-converter.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock
bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The topology

```
              brief
  user ─────────────▶ brief_analyst ──┬──▶ copywriter        (hero + body)
             (final)  hub             ├──▶ variants_tester   (A/B + CTA variants)
                                       └──▶ conversion_editor (polish + tighten)

  ...copywriter / variants_tester / conversion_editor never talk to each other;
  only brief_analyst talks to user. The whole team shares one workdir (the page).
```

Four agents, one directed flow:

1. **`user` → `brief_analyst`** — you send the product brief.
2. **`brief_analyst` → `copywriter`** — the analyst extracts the positioning and
   delegates the hero + body copy.
3. **`brief_analyst` → `variants_tester`** — once copy exists, the analyst asks
   for A/B headline + CTA variants.
4. **`brief_analyst` → `conversion_editor`** — the analyst sends the chosen
   direction for a final conversion polish.
5. **`brief_analyst` → `user`** — the analyst returns the finished page copy to
   you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. The three specialists can only deliver to `brief_analyst`;
anything else is bounced back as a `system` message and filed in `failed/`
(see §7).

---

## 2. The config, explained

Here is `examples/landing-page-converter.yaml` in full:

```yaml
# 🚀 Landing-page converter — a brief_analyst hub turns a raw product brief into
# high-converting landing-page copy: hero + body, A/B + CTA variants, and a final
# conversion-focused edit.
swarm:
  name: landing-page-converter
  root: ./landing-page-converter-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: brief_analyst
    type: claude
    workdir: "{root}/page"
    can_talk_to: [copywriter, variants_tester, conversion_editor, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the BRIEF ANALYST and the hub of a landing-page team ...
  - name: copywriter
    type: claude
    workdir: "{root}/page"
    can_talk_to: [brief_analyst]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the COPYWRITER ... write the landing-page copy into COPY.md ...
  - name: variants_tester
    type: gemini
    workdir: "{root}/page"
    capture: pane
    can_talk_to: [brief_analyst]
    command: "gemini --yolo"
    role: |
      You are the VARIANTS TESTER ... produce 3-5 A/B headline + CTA variants ...
  - name: conversion_editor
    type: codex
    workdir: "{root}/page"
    can_talk_to: [brief_analyst]
    command: "codex --yolo"
    role: |
      You are the CONVERSION EDITOR ... tighten the page for conversion ...
```

(Config docs link: [`examples/landing-page-converter.yaml`](../../examples/landing-page-converter.yaml))

Field by field:

### `swarm`
- **`name: landing-page-converter`** — the swarm's name (shows up in `status`,
  logs, sessions).
- **`root: ./landing-page-converter-workspace`** — the parent directory for the
  agents' working directories and mailboxes. All four agents share
  `landing-page-converter-workspace/page/` as their workdir (created on `up`),
  and their mailbox folders live alongside, namespace-prefixed per agent so they
  don't collide. Orchestrator state goes under
  `landing-page-converter-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. For `claude` and `codex`,
  whose CLIs support a completion **hook**, setting `capture: none` is a footgun
  — so the config loader *upgrades* it back to `hook` and prints a warning at
  `up`. Net effect here: brief_analyst, copywriter and conversion_editor use
  their hook; variants_tester overrides to `pane`.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent below
  states its own list explicitly, so this default is just a safe floor.

### `brief_analyst` (type: `claude`)
- **`can_talk_to: [copywriter, variants_tester, conversion_editor, user]`** — the
  analyst is the hub: it directs the three specialists and is the **only agent
  that can talk to `user`**. That last part matters — keep the human-facing
  surface to a single agent (see Tips).
- **`workdir: "{root}/page"`** — a quoted shared path. All four agents work in
  the same directory, so `COPY.md`, the variant matrix, and the polished final
  all live in one place; mailboxes are auto-namespaced to avoid collisions.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. (Placeholder — substitute your own launch command, e.g. a shell
  alias. Treat command strings as sensitive; they may embed keys.)
- **`role`** — the standing identity: extract positioning, sequence the work,
  decide when the page is done. On `up` this becomes the agent's first prompt,
  wrapped in a **standby notice**, so the analyst waits for your brief.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `copywriter` (type: `claude`)
- **`can_talk_to: [brief_analyst]`** — can only report back to the analyst. It
  writes the hero + body copy into `COPY.md` in the shared workdir.
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command.

### `variants_tester` (type: `gemini`)
- **`can_talk_to: [brief_analyst]`** — can only report back to the analyst.
- **`capture: pane`** — Gemini's CLI can't call a completion program, so
  Agentainer detects "turn done" by **polling the tmux pane** until it stops
  changing. (This is why it explicitly overrides the `none` default.)
- **`command: "gemini --yolo"`** — placeholder launch command.
- **`role`** — "produce 3-5 A/B headline + CTA variants with hypotheses."

### `conversion_editor` (type: `codex`)
- **`can_talk_to: [brief_analyst]`** — only reports upward to the analyst. It
  tightens the chosen copy for conversion without changing its meaning.
- **`command: "codex --yolo"`** — placeholder launch command.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### What's *not* in this config
- **No `periodically_ping_seconds`.** None of the four agents has a periodic ping
  configured, so no agent is auto-nudged on a timer while idle — the pipeline is
  purely event-driven off real mail.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on
  (see §4).

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/landing-page-converter.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings (including the
   `capture: none → hook` upgrade and the shared-workdir notice).
2. Creates the runtime dirs (`landing-page-converter-workspace/.agentainer/…`:
   log, queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**.
4. **Installs per-type turn detection** — the Claude Stop hook for the
   brief_analyst and copywriter, the Codex `notify` hook for the
   conversion_editor, and (for the pane-captured variants_tester) arranges pane
   polling.
5. **Opens one tmux session per agent**, `cd`'d into the shared `page/` workdir,
   running its `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints. The `serve` line gives you
the mail-app control-plane UI (threads, live panes, send-as-user, availability
toggle) — bound to loopback `127.0.0.1` by default. Drop `--host`/`--token` for
the safe local-only bind. See the `README.md` "control-plane UI" section.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole pipeline route mail with no API keys — the mechanics are identical.

---

## 4. Drive a brief

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the analyst's final page copy as mail (rather
than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/landing-page-converter.yaml
```

This rewrites the `user` contact card in the analyst's `outbox/user/about.md` to
`Status: available`, so the analyst sees you're reachable. (While away, mail to
you is *held* and the sender gets a `system` ack — nothing bounces.)

Now send the brief into the swarm, addressed to the analyst:

```bash
./agentainer send --to brief_analyst "Product: TaskFlow, an AI to-do app for busy teams. Audience: SMB managers. Goal: free-trial signups."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the analyst, then — because the
inbox was empty — **released into `inbox/`** and the analyst is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§5), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **brief_analyst extracts the positioning** from your brief and writes a
   delegation into `outbox/copywriter/`. On stop, that routes to the copywriter.
2. **copywriter writes the hero + body** into `COPY.md` and writes its summary
   into `outbox/brief_analyst/`. On stop, that routes back to the analyst.
3. **brief_analyst sends the draft to variants_tester** for A/B headline + CTA
   variants; on stop, the variant matrix routes back to the analyst.
4. **brief_analyst sends the chosen direction to conversion_editor** for polish;
   on stop, the tightened copy routes back to the analyst.
5. **brief_analyst finalizes** and writes the finished page copy into
   `outbox/user/`. On stop, that's delivered to your `user` mailbox (you'll see
   it with `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

> If you *don't* send a brief, the agents just sit in standby (that's the point
> of the standby prompt). The pipeline only moves when real mail arrives.

---

## 5. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/landing-page-converter.yaml
```

```
swarm: landing-page-converter   root: ./landing-page-converter-workspace
  brief_analyst (claude) up idle queue=0 unread=0 talks=copywriter, variants_tester, conversion_editor, user
  copywriter (claude)    up idle queue=0 unread=0 talks=brief_analyst
  variants_tester (gemini) up idle queue=0 unread=1 talks=brief_analyst
  conversion_editor (codex) up idle queue=0 unread=0 talks=brief_analyst
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/landing-page-converter.yaml          # whole swarm, last 20
./agentainer logs -c examples/landing-page-converter.yaml -f        # follow live
./agentainer logs copywriter -c examples/landing-page-converter.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox** — what a given agent is currently looking at:

```bash
./agentainer inbox copywriter -c examples/landing-page-converter.yaml
```

Prints the one released message (headers + body), or `copywriter: inbox is empty`.

**Attach to a live pane** — watch (or type directly into) an agent's tmux session:

```bash
./agentainer attach copywriter -c examples/landing-page-converter.yaml
```

Detach with the usual tmux `Ctrl-b d`.

---

## 6. Resume after a stop

Tear the swarm down when you're done:

```bash
./agentainer down -c examples/landing-page-converter.yaml
```

Bring it back later and **conversations resume by default**:

```bash
./agentainer up -c examples/landing-page-converter.yaml     # resume is the default
```

On `up`, Agentainer reads
`landing-page-converter-workspace/.agentainer/sessions.yaml` (written as each
agent finished its first turn) and reattaches the recorded conversations via
each type's native resume: `claude --resume <id>` for the analyst and copywriter,
`codex resume <id>` for the conversion_editor. The variants_tester (`gemini`) has
no resume bridge, so it starts a **fresh** conversation with a warning — its
role is stateless-per-task anyway. A resumed agent is *not* re-sent the standby
prompt (its prior context is restored).

Pass `--no-resume` to force everyone fresh. Inspect what's recorded with:

```bash
./agentainer sessions -c examples/landing-page-converter.yaml
```

For the full story, see [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 7. Tips & footguns

- **Keep brief_analyst the only `user`-facing agent.** In this config only the
  analyst lists `user` in `can_talk_to`. That gives you a single point of contact
  and a clean funnel: raw copy always passes through the analyst before it reaches
  you. If a specialist tries to mail `user` directly, the orchestrator bounces it
  (ACL) and drops a `system` note explaining who it *can* message.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion. A
  `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
  Claude) means completion never triggers and the agent pins "busy" forever.
  `status` showing an agent `busy` for a long time with `unread` mail is the tell.

- **Shared workdir, namespaced mail.** All four agents share `page/`, so their
  copy files coexist; the orchestrator prefix-namespaces each mailbox
  (`brief_analyst-inbox/`, `copywriter-inbox/`, …) so agents never read each
  other's mail. The shared warning at `up` is expected — it's how the COPY files
  land in one place.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is best-effort, and a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times without being handled is auto-archived so
  the queue advances. There's also a per-pair runaway cap (≤20 messages / 60s).

- **Force-idle if the pane-captured agent's turn never registers.** The
  variants_tester uses pane polling; if its capture never fires you can nudge the
  state along:
  ```bash
  ./agentainer idle variants_tester -c examples/landing-page-converter.yaml
  ```

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every agent's conversation fresh next `up`:
  ```bash
  ./agentainer down                 -c examples/landing-page-converter.yaml
  ./agentainer remove-session       -c examples/landing-page-converter.yaml
  ```
  It refuses while any agent (or the supervisor) is still running — always `down`
  first. It never touches the agents' source files or your config.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`cli-reference.md`](../cli-reference.md) — every subcommand and flag.
- `examples/quickstart.yaml` — the key-free mock-agent starter.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

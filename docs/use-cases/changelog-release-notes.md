# Use case: changelog & release notes swarm

A concrete, end-to-end walkthrough of the shipped
`examples/changelog-release-notes.yaml` swarm — a three-agent pipeline that
**mines a repo's git history**, **groups the commits into user-facing release
notes**, and **writes the migration / upgrade guide** for any breaking changes.
It's the canonical "read the raw material → distill it → fan out to two writers"
loop, wired entirely through Agentainer's file-based mail model.

Everything below is based on the actual contents of
`examples/changelog-release-notes.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The topology

```
              changelog-release-notes X
   user ─────────────────▶ commit_miner ──────────────┐
          (notes + guide) ◀──────────────┐            ├──▶ grouping_editor
                                          │            │     (release notes)
                                   digests git log     ├──▶ migration_guide_writer
                                          │                  (upgrade guide)
                                   returns drafts       ┘
```

Three agents, one directed flow:

1. **`user` → `commit_miner`** — you send the release range (e.g. "since v1.3.0").
2. **`commit_miner` → `grouping_editor`** — the miner distills `git log` into a
   clean digest and delegates the release notes.
3. **`commit_miner` → `migration_guide_writer`** — the miner also hands just the
   **breaking** changes to the migration writer.
4. **`grouping_editor` → `commit_miner`** — the editor returns the notes draft.
5. **`migration_guide_writer` → `commit_miner`** — the writer returns the guide.
6. **`commit_miner` → `user`** — the miner assembles `CHANGELOG.md` +
   `MIGRATION.md` and returns the final result to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. The two leaves never talk to each other; everything
converges on the miner so the notes and the guide stay consistent with one
source of truth.

---

## 2. The config, explained

Here is `examples/changelog-release-notes.yaml` in full:

```yaml
# 📝 Changelog / release-notes swarm -- mine git commits → group into
# user-facing notes → write a migration/upgrade guide.
swarm:
  name: changelog-release-notes
  root: ./changelog-release-notes-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: commit_miner
    type: claude
    can_talk_to: [grouping_editor, migration_guide_writer, user]
    command: "claude --dangerously-skip-permissions"
    workdir: "{root}/repo"
    capture: none
    role: |
      You are the COMMIT_MINER -- the hub and the human-facing owner of the release...
  - name: grouping_editor
    type: claude
    can_talk_to: [commit_miner]
    command: "claude --dangerously-skip-permissions"
    workdir: "{root}/repo"
    capture: none
    role: |
      You are the GROUPING_EDITOR. Given the miner's DIGEST.md, write the
      user-facing release notes in CHANGELOG.md...
  - name: migration_guide_writer
    type: codex
    can_talk_to: [commit_miner]
    command: "codex --yolo"
    workdir: "{root}/repo"
    capture: none
    role: |
      You are the MIGRATION_GUIDE_WRITER. Given the BREAKING changes the miner
      sends you, write MIGRATION.md...
```

Field by field:

### `swarm`
- **`name: changelog-release-notes`** — the swarm's name (shows up in `status`,
  logs, sessions).
- **`root: ./changelog-release-notes-workspace`** — parent directory for the
  shared workdir and mailboxes. Orchestrator state goes under
  `changelog-release-notes-workspace/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless overridden.
- **`capture: none`** — the default turn-detection mode. For `claude`/`codex`
  agents, whose CLIs support a completion **hook**, `capture: none` is a footgun,
  so the loader *upgrades* it to `hook` and prints a warning at `up`. (The
  per-agent `capture: none` lines below keep the *intent* explicit; the result is
  the same auto-upgrade.)
- **`can_talk_to: []`** — the default ACL is "talk to no one"; every agent
  states its list explicitly.

### `commit_miner` (type: `claude`)
- **`can_talk_to: [grouping_editor, migration_guide_writer, user]`** — the
  miner is the hub: it delegates to both writers and is the **only agent that can
  talk to `user`**. The human-facing surface stays at a single agent.
- **`workdir: "{root}/repo"`** — a shared quoted placeholder. All three agents
  resolve this to `changelog-release-notes-workspace/repo` so they read the same
  checkout and `git log`. Because the workdir is shared, Agentainer
  auto-namespaces each agent's mailbox folders (`commit_miner-inbox/`,
  `grouping_editor-inbox/`, …) so mail never collides.
- **`command`** — launches Claude Code in its tmux pane. (Placeholder — substitute
  your own launch command. Treat command strings as sensitive; they may embed keys.)
- **`role`** — on `up` this becomes the standby first prompt. The miner reads the
  git history, writes `DIGEST.md`, fans it to the two writers, and assembles the
  final `CHANGELOG.md` + `MIGRATION.md`.

### `grouping_editor` (type: `claude`)
- **`can_talk_to: [commit_miner]`** — the editor only reports upward to the
  miner. It cannot reach the other leaf or `user` directly.
- **`role`** — turns `DIGEST.md` into categorized, user-facing release notes
  ("Keep a Changelog" style), then reports back.

### `migration_guide_writer` (type: `codex`)
- **`can_talk_to: [commit_miner]`** — the writer only reports to the miner.
- **`command: "codex --yolo"`** — placeholder launch command.
- **`role`** — turns only the **breaking** changes into a step-by-step
  `MIGRATION.md` (before/after, exact steps), then reports back.

### What's *not* in this config
- **No `periodically_ping_seconds`.** The pipeline is purely event-driven off the
  human's `send`; no agent self-starts.
- **No `user` availability set.** The `user` mailbox defaults to **away** — mail
  to you is *held* (never bounced) until you flip it on (see §4).
- **`system` is never an addressable recipient.** Agents may not list it in
  `can_talk_to`.

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/changelog-release-notes.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints warnings (the `capture: none → hook`
   upgrade for all three agents, and the shared-workdir notice).
2. Creates the runtime dirs (`changelog-release-notes-workspace/.agentainer/…`).
3. **Initializes the mailboxes** — five folders `inbox/ outbox/ read/ sent/
   failed/` per agent, *plus* the auto-namespaced prefix (`commit_miner-inbox/`,
   …) because the workdir is shared. The miner's `outbox/` gets
   `grouping_editor/`, `migration_guide_writer/`, and `user/` folders — each with
   an `about.md` contact card that *is* the visible ACL.
4. **Installs per-type turn detection** — Claude Stop hooks for the miner and the
   editor, and a Codex `notify` hook for the migration writer.
5. **Opens one tmux session per agent**, `cd`'d into the shared repo, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'changelog-release-notes' is up with 3 agent(s)
:: attach with:  tmux attach -t <commit_miner-session>
:: you can use the UI with:  agentainer serve --host 127.0.0.1 -c examples/changelog-release-notes.yaml --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). By default it binds **`127.0.0.1`** — never
`0.0.0.0` — and a `--token` is required for any remote bind. See the `README.md`
"control-plane UI" section.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole pipeline route mail with no API keys — the mechanics are identical.

---

## 4. Drive a release

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the final notes as mail (rather than have them
held), turn yourself available first:

```bash
./agentainer user available -c examples/changelog-release-notes.yaml
```

Now send the release range into the swarm, addressed to the miner:

```bash
./agentainer send --to commit_miner "Draft release notes for v1.4.0 (commits since v1.3.0)."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped
`From: user`, enqueued for the miner, and — because the inbox was empty —
**released into `inbox/`** and the miner is **nudged** (the protocol is
re-pasted, including its allowed-recipient list).

### The mail flowing

1. **commit_miner mines git.** It reads `inbox/`, runs `git log`/`git diff` in the
   shared repo, and writes `DIGEST.md`. It fans the digest to the
   `grouping_editor` and the breaking subset to the `migration_guide_writer`.
2. **grouping_editor writes notes.** It reads its inbox, writes `CHANGELOG.md`,
   sends the draft back to the miner.
3. **migration_guide_writer writes the guide.** It reads its inbox, writes
   `MIGRATION.md`, sends its draft back to the miner.
4. **commit_miner assembles.** It reads both drafts, reconciles them against the
   digest, and writes the final `CHANGELOG.md` + `MIGRATION.md` to `outbox/user/`,
   delivered to you.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

---

## 5. Observe

**Overall status:**

```bash
./agentainer status -c examples/changelog-release-notes.yaml
```

**The durable event log** (the source of truth — tmux keeps no scrollback):

```bash
./agentainer logs -c examples/changelog-release-notes.yaml -f   # follow live
```

**A specific inbox** — what an agent is currently looking at:

```bash
./agentainer inbox grouping_editor -c examples/changelog-release-notes.yaml
```

**Attach to a live pane** (or type directly in to un-stick an agent):

```bash
./agentainer attach commit_miner -c examples/changelog-release-notes.yaml
```

Detach with tmux `Ctrl-b d`.

---

## 6. Resume after a stop

```bash
./agentainer down -c examples/changelog-release-notes.yaml
./agentainer up   -c examples/changelog-release-notes.yaml     # resume is the default
```

On `up`, Agentainer reattaches the recorded conversations via each type's native
resume (`claude --resume <id>` for the miner and editor; `codex resume <id>` for
the writer). The shared repo and the produced `CHANGELOG.md`/`MIGRATION.md` are
untouched. Pass `--no-resume` to force everyone fresh. Inspect with:

```bash
./agentainer sessions -c examples/changelog-release-notes.yaml
```

---

## 7. Tips & footguns

- **Keep the miner the only `user`-facing agent.** Only the miner lists `user` in
  `can_talk_to`. Raw digests always pass through it before reaching you. If a leaf
  tries to mail `user` directly, the orchestrator bounces it (ACL) and drops a
  `system` note in that leaf's inbox explaining who it *can* message.
- **The shared workdir is intentional but noisy.** All three agents resolve
  `workdir: "{root}/repo"` to the same directory, so the loader warns that they
  can overwrite each other's files. That's fine here — each agent writes a
  *different* file (`DIGEST.md`, `CHANGELOG.md`, `MIGRATION.md`) and the
  auto-namespaced mailboxes keep their inboxes separate. Point `workdir` at your
  real checkout by flipping `create_workdir: false`.
- **Watch the stop → nudge loop.** The whole clock runs on turn completion. A
  `type`/`command` mismatch (a `claude` agent whose `command` doesn't launch
  Claude) means completion never triggers and the agent pins "busy" forever.
  `status` showing an agent `busy` with `unread` mail is the tell.
- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is best-effort, a message shown
  `AUTO_ARCHIVE_PRESENTATIONS` (5) times unheeded is auto-archived, and a per-pair
  runaway cap (≤20 messages / 60s) kills "thanks!/you're welcome!" loops.
- **`remove-session` to reset.** To wipe all Agentainer state (runtime + mailboxes)
  and start fresh next `up`:
  ```bash
  ./agentainer down           -c examples/changelog-release-notes.yaml
  ./agentainer remove-session -c examples/changelog-release-notes.yaml
  ```
  It refuses while any agent is running — always `down` first. It never touches
  your source files or config.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`cli-reference.md`](../cli-reference.md) — every subcommand and flag.
- [`use-cases/custom-workspace.md`](./custom-workspace.md) — shared workdirs and
  mailbox namespacing.
- [`use-cases/refactor-planner.md`](./refactor-planner.md) — another shared-workdir
  hub swarm.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

---

### Search intent

- **How do I generate release notes from git commits automatically?** — Point
  this swarm at your repo; the miner distills `git log` and the editor writes
  "Keep a Changelog" notes, no manual triage.
- **Changelog from commits across multiple LLMs** — the miner (Claude), the
  editor (Claude) and the migration writer (Codex) each do one job, coordinated by
  the file-based mail model.
- **What changed and what do I need to upgrade?** — the miner splits breaking
  changes out to a dedicated `MIGRATION.md` writer so users get exact before/after
  upgrade steps.
- **Zero-dependency, key-free release-notes pipeline** — runs as mock bash loops
  with no API keys; swap in real CLIs when you want live agents.

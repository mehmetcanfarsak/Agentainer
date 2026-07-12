# Use case: mixing multiple LLM agents in one swarm

Agentainer is built to run a **heterogeneous** team: one Claude, one Codex, one
Gemini, one Hermes — or any combination — as independent tmux sessions that talk
to each other through the file-based mail model, under a single
`can_talk_to` ACL. This document explains the mechanics that make a *mixed*
swarm work, and the footguns that are specific to mixing agent types.

> All behaviour below is read straight from the runtime: `lib/config.py`,
> `lib/hooks.py`, `lib/cli.py`, and `lib/sessions.py`. Nothing here is invented.
> For the general model see `docs/getting-started.md` and `docs/cli-reference.md`;
> for resume specifics see `docs/sessions-and-resume.md`.

---

## 1. Why mix models

Different coding agents have different strengths, and a real project usually
wants more than one of them on the same codebase:

- **Claude** tends to be strong at architecture, decomposition, and careful
  reasoning — a natural *orchestrator*.
- **Codex** is fast and good at direct implementation from a clear spec — a
  natural *implementer*.
- **Gemini** / **Hermes** are cheap and good at parallel, lower-stakes work
  such as review, translation, or research — natural *reviewers* / *researchers*.

A mixed swarm lets you point each task at the model that fits it, while the
orchestrator owns all the hard parts (routing, ACL, read-state, retries,
threading) so the models only ever read and write natural-language mail files.
The models never need to know *what* their peers are — only the names in
`can_talk_to` and the paths in their inbox/outbox.

Several shipped examples already mix types:

- `examples/quickstart.yaml` — claude orchestrator → gemini researcher + codex developer → claude reviewer
- `examples/bug-hunt.yaml` — claude orchestrator + codex hunters + gemini hunter
- `examples/debate.yaml` — claude moderator + codex pro + gemini con
- `examples/code-review.yaml` — claude reviewer + three codex devs
- `examples/software-company.yaml` — claude cto/architect/qa/docs + codex backend/frontend
- `examples/localization.yaml` — claude source + three gemini translators
- `examples/research.yaml`, `examples/writers-room.yaml`, `examples/incident-response.yaml` — all mixed

---

## 2. The `type` ↔ `command` contract

Every agent has exactly two fields that decide *how* it runs:

- **`type`** selects the turn-detection recipe and the resume recipe. It is
  one of `claude`, `codex`, `gemini`, `hermes` (the built-in
  `BUILTIN_AGENT_TYPES`).
- **`command`** is the shell command `up` actually executes to launch the CLI.

These **must agree**. The four recognized CLI tokens are exactly
`claude`, `codex`, `gemini`, `hermes`. If `type: codex` but `command`
launches something else, the agent's turn-completion signal will *never fire*,
so the orchestrator keeps it marked "busy" forever — a **hard deadlock**. This
is the v1 footgun carried forward, and v2 catches it at load time.

### Mismatch detection (in `config.load`)

When `type` is one of the four real CLIs, `config.py` lowercases `command` and
checks it for *any other* CLI token as a word boundary. If it finds one, it
raises a `ConfigError` and `up` refuses to start:

```
agent 'x': type: codex but command launches 'claude' ('claude ...').
The command must launch the same agent CLI as `type`, or the
turn-completion signal will never fire and the agent will hang.
Fix `command` or `type`.
```

A **mock** command (e.g. `bash -c 'while true; do read ...'`) contains none of
the four tokens, so it passes — mock agents are used by the test suite and the
key-free demo loops.

### Built-in defaults

If you omit `command`, `config.py` fills in the type's default:

| type    | default command                       | built-in capture |
|---------|---------------------------------------|------------------|
| claude  | `claude --dangerously-skip-permissions` | `hook`         |
| codex   | `codex --yolo`                        | `hook`           |
| gemini  | `gemini --yolo`                       | `pane`           |
| hermes  | `hermes`                              | `pane`           |

### A correct mixed example

```yaml
agents:
  - name: orchestrator
    type: claude                       # Stop-hook turn detection
    command: "claude --dangerously-skip-permissions"
    can_talk_to: "*"

  - name: implementer
    type: codex                        # notify-program turn detection
    command: "codex --yolo"
    can_talk_to: [orchestrator, reviewer]

  - name: reviewer
    type: gemini                       # pane-polling turn detection
    command: "gemini --yolo"
    capture: pane
    can_talk_to: [implementer]
```

Each `type` matches its `command`, so each agent will correctly report when its
turn ends.

---

## 3. Per-type turn detection — the system clock

"Turn completion" is the **system clock** of the whole swarm. The nudge,
one-at-a-time inbox release, outbox sweep, and periodic pings all fire off *the
agent stopped*. If completion is detected wrongly, failures are **silent**: an
agent looks hung, or a live turn gets corrupted by a premature paste. The
detection method is chosen by `type` in `lib/hooks.py`
(`install_turn_detection`):

- **`claude` → Stop hook.** `install_claude_hook` writes a `Stop` hook into the
  workdir's `.claude/settings.json` pointing at `hooks/claude_stop.sh`. Claude
  calls it after each turn; the hook sweeps the outbox and finishes the turn.
  (No `matcher` key is set, because Stop is not a tool event and a matcher can
  suppress the hook.)

- **`codex` → `notify` program.** `install_codex_hook` gives codex a private
  `CODEX_HOME` (in the workdir's `.codex/`) whose `config.toml` sets
  `notify = [ ".../hooks/codex_notify.sh" ]`. Codex calls that program on
  `agent-turn-complete`. The hook payload is parsed by `cli.py cmd_hook` and the
  session id is recorded for `--resume`.

- **`gemini` / `hermes` → pane polling.** These CLIs have no "call this on
  completion" facility, so the orchestrator instead polls the tmux pane
  (`cli.py run_watcher` / `cmd_watch`). When the pane content stops changing for
  `pane_idle_ms` (default 2500 ms), the turn is treated as done. This is why
  their built-in capture is `pane`.

Because the method is per-type, a mixed swarm installs **three different
completion wirings at `up`** and they all feed the same mail loop. You do not
configure this by hand — `type` drives it.

---

## 4. Capture resolution

`capture` tells the orchestrator *how* to learn that a turn finished. Valid
values are `hook`, `pane`, `none`, `auto` (the default). Resolution rules from
`config.py`:

- **`auto`** resolves to the type's built-in capture: `hook` for claude/codex,
  `pane` for gemini/hermes. This is the safe default — normally you never set
  `capture` by hand.
- **`none`** on a hook-capable type (claude/codex) is dangerous: it removes the
  agent's *only* turn-completion signal, leaving the orchestrator blind to a
  silent turn that can wedge the swarm. `config.py` therefore **auto-upgrades**
  `none` → `hook` for claude/codex and emits a warning:

  ```
  agent 'x': capture: none on a claude agent gives the orchestrator no
  turn-completion signal -- auto-upgraded to capture: hook.
  ```

- **`hook`** requested on a type that has no completion hook (gemini/hermes)
  is downgraded to `pane` with a warning — those CLIs do not support an external
  completion program.

Practical guidance:

- **claude / codex**: leave `capture` unset (→ `hook`), or set `hook`. Never set
  `none` (it gets force-upgraded anyway).
- **gemini / hermes**: leave `capture` unset (→ `pane`), or set `pane`. They
  *cannot* use `hook`.

---

## 5. Trust modals

The first thing a fresh CLI may do in a new directory is pop a **"do you trust
the files in this folder?"** modal. Under `--dangerously-skip-permissions` /
`--yolo` the CLI still shows it, and **the modal swallows the first prompt**
(Enter answers the dialog, not your role text). `up` pre-trusts each workdir as
a per-type step so the agent actually learns the protocol.

What `lib/hooks.py` actually installs today:

- **claude** → `pretrust_claude_dir` edits `~/.claude.json`, setting
  `projects["<workdir>"].hasTrustDialogAccepted = true` (and
  `projectOnboardingSeenCount = 1`). It writes the file atomically so a running
  claude isn't corrupted. If `~/.claude.json` does not exist, claude will create
  it itself on first run (no pre-trust needed yet).
- **codex** → `install_codex_hook` writes a `config.toml` in the workdir's
  `.codex/` that includes `[projects."<workdir>"]` with
  `trust_level = "trusted"`, and symlinks/copies the user's real `auth.json` so
  the agent is logged in. The `notify` line must appear *before* any `[table]`
  header (TOML is order-sensitive).

For **gemini / hermes**, `hooks.py` installs no trust pre-trust step — they fall
under pane polling only. If a Gemini/Hermes CLI shows a trust modal in a fresh
folder, it would eat the first prompt; pre-trust those manually (or ensure the
CLI is invoked with the trust already granted) before relying on the first
standby prompt. This is an honest limitation of the current per-type wiring.

---

## 6. A worked mixed swarm

A classic shape: a **claude orchestrator** decomposes the task, delegates
implementation to a **codex implementer**, and asks a **gemini reviewer** to
check the result. Wiring:

- `orchestrator` can talk to everyone (`*`) — it fans out and gathers.
- `implementer` (codex) talks to `orchestrator` and `reviewer`.
- `reviewer` (gemini) talks back only to `implementer`.

```yaml
# ============================================================================
# mixed-team.yaml — one orchestrator, one codex implementer, one gemini reviewer.
# Commands launch REAL CLIs. For a key-free demo, swap each `command` for a
# mock bash loop, e.g.  bash -c 'while true; do read -r l || sleep 1; done'
# (a mock command contains no claude/codex/gemini/hermes token, so it passes
#  the type<->command mismatch check).
# ============================================================================
swarm:
  name: mixed-team
  root: ./mixed-workspace

agents:
  - name: orchestrator
    type: claude
    command: "claude --dangerously-skip-permissions"
    can_talk_to: "*"
    role: |
      You are the orchestrator. Wait for the user's task. Decompose it,
      send a clear implementation spec to `implementer`, then ask
      `reviewer` to check the result. Synthesize the final answer.

  - name: implementer
    type: codex
    command: "codex --yolo"
    can_talk_to: [orchestrator, reviewer]
    role: |
      You implement code from a clear spec. When done, report the diff
      summary to `orchestrator` and ask `reviewer` for a review.

  - name: reviewer
    type: gemini
    command: "gemini --yolo"
    capture: pane                 # optional: auto resolves to pane anyway
    can_talk_to: [implementer]
    role: |
      You review code for correctness and security. Reply to `implementer`
      with concrete findings.
```

Notes:

- `orchestrator` and `implementer` use the default `capture` (`hook`), so no
  `capture:` line is needed. `reviewer` is `gemini`; `capture: pane` is shown
  for clarity but is the same value `auto` would pick.
- Run it:

  ```bash
  agentainer up        -c mixed-team.yaml
  agentainer status    -c mixed-team.yaml
  agentainer send      -c mixed-team.yaml --to orchestrator "Add CSV export to the CLI."
  agentainer logs      -c mixed-team.yaml -f
  agentainer down      -c mixed-team.yaml
  ```

### Shell-wrapped commands and `resume_command`

If your `command` launches the CLI through a **shell alias or wrapper** rather
than calling it directly, you cannot simply append `--resume <id>` to the end —
the flags have to go *inside* the wrapper. For example, some operators wrap
Claude behind an alias like `chy3`:

```yaml
  - name: orchestrator
    type: claude
    command: "bash -ic 'chy3'"
    # resume_args (appended to command) would NOT work here, so provide an
    # exact recipe. {session_id} and {command} are substituted by the runtime.
    resume_command: "bash -ic 'chy3 --resume {session_id}'"
    can_talk_to: "*"
```

`resume_command` wins over `resume_args` precisely because a wrapper can't take
appended flags. (`gemini`/`hermes` have no recoverable session id from a scraped
pane, so they get no resume recipe and always start a fresh conversation —
see `docs/sessions-and-resume.md`.) Commands that embed secrets (API keys via
shell aliases) are treated as sensitive and are **never printed or committed**.

---

## 7. Gotchas

### Mismatch deadlock
The big one. `type: codex` with `command: "claude ..."` passes *no* completion
signal — the agent pins "busy" forever and the swarm stalls. `up` now rejects
this at config load, but the check is token-based and word-boundary only; keep
`type` and `command` literally aligned. A mock command (no CLI token) is exempt
so the test/demo loops keep working.

### Silent-but-alive (health probe)
Pane polling and hooks both assume the agent tells us when a turn ends. If the
CLI hangs *without* exiting or emitting a completion event, the orchestrator may
think the agent is still mid-turn. Two safeguards exist:

- **`ready_probe`** (default `true`) — `up` waits for the input box to respond
  before pasting the first prompt; if it never responds within
  `ready_timeout_ms` it warns and proceeds anyway.
- **The liveness supervisor** (on by default) — the heartbeat the event-driven
  core otherwise lacks. It reconciles dead/stale agents on a timer so one silent
  agent can't wedge the swarm. If completion truly never fires, `agentainer idle
  <agent>` forces the agent back to idle and drains its `read/` folder.

For pane-polling agents, a turn that ends but whose final pane state matches a
prior snapshot can be missed; the `pane_idle_ms` / `pane_poll_ms` knobs tune
sensitivity.

### Keep the ACL tight
`can_talk_to` is **cooperative, not OS isolation** (decision D15). Agents have
filesystem access and *could* write straight into another agent's `inbox/`,
bypassing `outbox/`. It is enforced for well-behaved agents and documented
honestly — not a security boundary. So:

- Grant the minimum: an implementer rarely needs to mail the user directly; a
  reviewer rarely needs to mail the orchestrator. Wire peer-to-peer edges only
  where the workflow needs them (as in the worked example).
- `system` can never be a recipient (config load rejects it). `user` is allowed
  only as a virtual mailbox. `*` expands to every *other* agent at load time.
- Use distinct `workdir`s per agent. Two agents sharing a workdir get a config
  warning (they can overwrite each other's files and interleave git commits);
  the mail model namespaces the five folders with a `<name>-` prefix when a
  workdir *is* shared, but separate directories are cleaner.

### Don't disable capture casually
`capture: none` on claude/codex is auto-upgraded to `hook` — but if you ever
force a non-capturing mode on a hook-capable agent through other means, it loses
its only completion signal and can wedge the swarm. Let `auto` (the default)
resolve it.

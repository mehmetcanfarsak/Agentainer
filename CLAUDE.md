# CLAUDE.md

Guidance for Claude Code (and other agents) working in this repository.

## What this is

This is **Agentainer v2** — a ground-up rewrite of the zero-dependency
multi-agent orchestrator. It launches coding-agent CLIs (Claude Code, Codex,
Gemini, Hermes) each in its own tmux session and working directory, defined by a
single `agentainer.yaml`, and lets them message each other only where the
config's `can_talk_to` ACL allows.

**Why a rewrite:** v1 had agents exchange messages as tagged XML envelopes
emitted *inside their prose output* and scraped back out of a fullscreen-TUI
pane. That was unreliable across LLMs and is the thing that failed. **v2 replaces
the messaging layer with a file-based mail model** — agents receive by *reading a
file* and send by *writing a file* — designed to work on nearly every
tool-calling LLM, including weak ones.

> v1 (the old project) lives at `/root/AgentSwarm/`. Do **not** edit it. This repo
> (`/root/AgentainerRepo/`) is v2.

## Status & source of truth

This repo is **greenfield** — right now it contains only the design:

- **`ProjectPlan.md` is the single source of truth for the design.** Read it
  before writing any code. Especially:
  - **§29 Decisions log (D1–D24)** — every settled choice, one line each.
  - **§24 Invariants we must not regress** — binding constraints.
  - **§26 Build phases (P1–P4)** — the shippable order of work.
  - **§23 Reuse vs. rewrite** — what to port from v1 vs. build fresh.
- The full v1 record is `/root/AgentSwarm/docs/PROJECT-DOCUMENTATION.md` (read-only
  reference for the hard-won behaviour and footguns).

If code and `ProjectPlan.md` disagree, the plan wins until the plan is updated.
Don't silently drift from it — change the plan first, then the code.

## The design in one screen

The agent's entire world is **two verbs** (read a file, write a file) and **four
folders**:

- `inbox/` — the **one** current unread message (orchestrator releases one at a
  time).
- `outbox/<name>/` — write a file here to send to `<name>`; read
  `<name>/about.md` (an orchestrator-maintained contact card) to see who they are
  and whether they're available.
- `read/` — move a handled message here (best-effort "I processed it" signal →
  read receipts).
- `sent/` — the agent's own record of delivered mail (orchestrator moves it here).

Everything hard — routing, ACL, message IDs, threading, read-state, queueing,
retries, availability, the durable log — is **deterministic orchestrator code**.
The model only reads and writes natural-language files. When an agent **stops**
with unread mail, the orchestrator sweeps its outbox and pastes a **nudge**
("you have mail — read it, then move it to `read/`"). See `ProjectPlan.md` §4–§11.

## Principles that govern all code

1. **Designed for dummy models.** The only capability required of an agent is
   read/write files. Never push formatting, bookkeeping, or protocol-memory onto
   the model — that's the orchestrator's job.
2. **Zero runtime dependencies, forever.** Python 3 + bash + tmux only. PyYAML is
   used *if present*, but a bundled parser must keep working without it. **This
   includes the UI:** stdlib `http.server` + one static vanilla-JS page, no
   framework, no build step. Keep the CI job that proves the no-PyYAML path.
3. **The model is always told its exact paths; it never assumes them.** Every
   nudge/first-prompt states the real inbox/outbox/read paths, so custom
   `mail_dir` and shared-workspace prefixing stay invisible to the model.
4. **Re-inject the protocol on every nudge**, including the list of allowed
   recipients. Assume no memory across turns.
5. **Correctness never depends on the model doing housekeeping.** Moving mail to
   `read/`/deleting is best-effort; the orchestrator owns authoritative state and
   has fallbacks (auto-archive after N presentations) so a forgetful model can't
   wedge or loop the system.
6. **Errors come back as mail** (`system` sender), so the model self-corrects
   in-band with no new concept.

## Footguns (carry these forward from v1)

- **Turn-completion detection is the system clock.** The nudge, one-at-a-time
  release, outbox sweep, and periodic pings all fire off "the agent stopped."
  Detection is per `type`: `claude` → Stop hook; `codex` → `notify` program;
  `gemini`/`hermes` → pane polling. Get this wrong and failures are *silent*
  (agent looks hung, or a live turn gets corrupted). **Port v1's detection, paste,
  and hook-install code — don't rewrite it.**
- **`type` ↔ `command` mismatch = hard deadlock.** If `command` launches a
  different CLI than `type` implies, completion never fires and the agent pins
  "busy" forever. v2 must **detect/prevent this at `up`** (validate command vs.
  type), and add a **per-agent health probe** for the "silent but alive" case the
  supervisor can't otherwise catch.
- **Trust modals eat the first prompt.** Pre-trust each workdir (port
  `pretrust_claude_dir`) as a pluggable per-type step, or the agent never learns
  the protocol.
- **The `can_talk_to` ACL is cooperative, not OS isolation.** Agents have
  filesystem access and *could* write straight into another inbox, bypassing
  `outbox/`. Enforced for well-behaved agents; documented honestly; not a security
  boundary. (Decision D15.)
- **The UI is a control plane.** It can start processes, edit config, and type
  into agents that may run `--dangerously-skip-permissions`/`--yolo`. **Bind
  `127.0.0.1` by default, never `0.0.0.0`**; opt-in; token required for any remote
  bind. Keep the headless CLI fully functional.
- **Keep the liveness supervisor.** The event-driven core needs the heartbeat —
  v1 shipped without it once and had to add it. Don't drop it in a "cleaner
  event-only" redesign.

## Runtime state & logs (never commit or ship)

Orchestrator-private state lives under `<root>/.agentainer/` (logs, per-agent
queue, turn state, `sessions.yaml`). The **durable JSONL event log**
(`.agentainer/logs/*.jsonl`) is the source of truth for history — fullscreen TUIs
keep **no scrollback**, so you cannot recover history from a pane. Never commit or
ship `.agentainer/`, agent workspaces/mailboxes, or `__pycache__/`; keep the
three-layer guard (`.gitignore` + `.npmignore` + npm `files` allowlist,
source-only).

## Conventions

- **Match v1's house style:** stdlib only, small focused functions, terse status
  prefixes on messages.
- **Branding: "swarm" is retired — it's Agentainer everywhere.** Config is
  `agentainer.yaml`; runtime dir `.agentainer/`; env `AGENTAINER_HOME`; collective
  noun in prose is "the agents". (Decision D21; rename table in `ProjectPlan.md`
  §22.)
- **`package.json` is the single source of truth for version**, tag-verified at
  publish.
- **100% line coverage is a release gate**, driven entirely by mock agents (bash
  loops — no API keys, nothing to pay for). The file-based model makes this
  *easier* than v1; keep it green. Keep UI/HTTP handlers thin so the tested core
  stays the substance.
- Treat agent `command` strings as sensitive (they may embed API keys via shell
  aliases). Don't print or commit secrets. A swarm's disposable `root` matters for
  `--yolo`/`--dangerously-skip-permissions` runs.
- **Keep the discovery layer** (`README.md` SEO structure + FAQ + `llms.txt`) — a
  feature, not decoration (invariant).

## Reuse vs. rewrite (see ProjectPlan.md §23)

- **Port from v1 (proven, already 100% covered):** turn-detection
  (`install_claude_hook`/`install_codex_hook`, pane polling, `on_turn_finished`),
  the paste stack (`paste_into`/`paste_score`/`wait_until_ready`), `capture_pane`,
  trust-modal pre-trust, the supervisor skeleton, locking primitives, config
  loading + `minyaml`, sessions/resume, and the JSONL logging layer.
- **Rewrite (the v2 work):** the messaging layer (file-based mailroom), replacing
  v1's `{delivered, completed}` backpressure with "the inbox *is* the queue",
  dropping the reply-reminder subsystem and `broadcast`, and adding the HTTP
  control-plane/UI, `user`/`system` virtual mailboxes, periodic pings, mismatch
  detection, and the runaway-loop rate cap.

## Build order

Phased and independently shippable (`ProjectPlan.md` §26): **P1** mail runtime
(CLI-driven) → **P2** UI observability → **P3** terminal snapshot + send-from-UI →
**P4** dynamic reconcile (add/delete agents, edit `agentainer.yaml`). Start at P1.

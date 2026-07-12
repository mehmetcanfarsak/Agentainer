# 📝 Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 🐛 [2.0.1] — 2026-07-12

Patch release: correctness fixes in the mailroom, turn tracking, and reconcile,
plus the bundled use-case docs and example swarms.

### ✨ Added
- **50+ use-case guides** under `docs/use-cases/` (one per example swarm) and a
  matching **50+ example swarms** under `examples/` — copy-paste agent configs
  for content, support, research, coding, and ops workflows.
- **UI templates API** (`GET /api/templates`, `POST /api/templates/apply`):
  seed an empty swarm from a bundled example swarm (onboarding helper).
- **UI bulk controls** (`POST /api/up_all`, `POST /api/down_all`): start every
  down agent / stop every running session from the control plane.
- **UI agent state machine**: honest per-agent state — `stopped` / `working` /
  `stalled` (a turn whose completion signal was lost) / `attention` (mail
  awaiting the operator) / `waiting` — with working age and an `attention` count.
- **Per-agent rate endpoint** (`GET /api/rate`) for opt-in messages/min over a
  window.

### 🔧 Fixed
- **Mail queue starvation.** `enqueue` now stamps a strictly-increasing mtime so
  `queued_files` delivers in true FIFO order; without it, random message-id
  filenames could starve a late-sorting message indefinitely. The outbox sweep
  (`on_stop`) and `/api/queue` now release/route in the same FIFO order.
- **Task yanked mid-turn.** `nudge` now marks the turn started, so a long but
  legitimate turn is no longer mistaken for idle and auto-archived out from
  under the agent (which corrupted the turn and drained the queue).
- **Whole queue drained into archive.** `process_read_folder` now only archives;
  the paired `release_next`+`nudge` (run by the supervisor tick and `cmd idle`)
  delivers-and-announces the next message. The "already handled" guard now
  compares the message's content id, not its filename, so handled mail isn't
  archived anyway.
- **Agent wedged busy / lost completion.** `mark_turn_started` now takes the
  same `turn.lock` as `mark_turn_finished`, fixing a read-modify-write race that
  could lose an update between a fresh delivery and a concurrent completion hook.
- **Silent shared inbox.** Folder namespacing keys off `mail_dir` (not
  `workdir`), so two agents pointing one `mail_dir` at the same place no longer
  silently share a single `inbox/`.
- **Config corrupted on rejected edit.** All reconcile mutators now commit via a
  `_commit` helper that reloads the written file and restores the previous one if
  validation fails; `remove_agent` also strips the removed agent from peers'
  `can_talk_to`. An empty `agents:` list is now valid (an empty swarm the UI can
  seed from a template).
- **`*` ACL dropped explicit peers.** A `*` in `can_talk_to` now expands to every
  other agent *and* preserves explicitly listed extras (notably `user`), which
  `*` does not cover, instead of replacing the whole list.

[2.0.1]: https://github.com/mehmetcanfarsak/Agentainer/releases/tag/v2.0.1

## 🎉 [2.0.0] — 2026-07-11

### ✨ Added
- **Ground-up rewrite around a file-based mail model.** Agents receive mail by
  *reading a file* (`inbox/`) and send by *writing a file* (`outbox/<name>/`); the
  orchestrator owns all routing, ACL, message IDs, threading, read-state,
  queueing, retries, availability, and the durable JSONL log. Replaces v1's
  tagged-XML-envelope-inside-prose messaging, which was unreliable across LLMs.
- **P1 — Mail runtime (CLI-driven):** `validate`, `up`, `down`, `restart`,
  `status`, `attach`, `send`, `user`, `queue`, `idle`, `inbox`, `logs`, `hook`,
  `watch`, `supervise`. Zero runtime dependencies (Python 3 + bash + tmux;
  PyYAML optional via the bundled `minyaml` fallback).
- **P2 — Control-plane UI:** `agentainer serve` — a zero-dependency web UI
  (stdlib `http.server` + one vanilla-JS page, no framework, no build) for
  observability and send-as-user. Binds `127.0.0.1`; token required for any
  non-loopback bind.
- **P3 — Terminal snapshot + send-from-UI:** live tmux pane capture per agent
  with auto-refresh, plus inject-mail-from-the-UI.
- **P4 — Dynamic reconcile:** `add` / `remove` / `edit` / `reconcile` rewrite
  `agentainer.yaml` with a stdlib-only YAML emitter (works with or without
  PyYAML) and then reconcile the change into effect (start missing sessions,
  stop orphaned ones).
- **Liveness supervisor heartbeat**, per-agent health probe, `type`↔`command`
  mismatch detection at `up`, `capture: none` auto-upgrade on hook types,
  one-at-a-time inbox release, best-effort read receipts, auto-archive after N
  presentations, and a runaway-loop rate cap.
- **Discovery layer:** keyword-rich README with FAQ + badges, `llms.txt` with
  "Verified CLI behaviours" + "Gotchas", and absolute-URL SVG assets.
- **Release automation:** `.github/workflows/publish.yml` publishing to npm with
  provenance, verifying the git tag matches the `package.json` version.
- **100% line coverage** across all `lib/` modules via mock (bash-loop) agents,
  no API keys.

### 🔧 Changed
- Branding: "swarm" retired — it's **Agentainer** everywhere (config is
  `agentainer.yaml`, runtime dir `.agentainer/`, env `AGENTAINER_HOME`).
- `can_talk_to` ACL is now enforced at routing time (a disallowed send is bounced
  as `system` mail into `failed/`), and `user`/`system` are reserved virtual
  mailboxes.

### 🗑️ Removed
- v1's tagged-envelope messaging, reply-reminder subsystem, and `broadcast`.

[2.0.0]: https://github.com/mehmetcanfarsak/Agentainer/releases/tag/v2.0.0

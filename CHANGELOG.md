# 📝 Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 🎉 [2.1.1] — 2026-07-14

### 🐛 Fixed
- **Idle agent never re-nudged about a message already in its inbox.** A nudge
  whose paste failed to land (agent looked idle, but the "you have mail" prompt
  never reached the pane) was never retried: the idle-recovery paths only nudged
  on a *fresh* `release_next`, so a message already sitting unread in the inbox
  was silently counted toward auto-archive and the model never saw it. New
  `mail.present_current` releases the next queued message **and** re-nudges
  whenever the inbox holds a message; the supervisor tick, `agentainer idle`, and
  Telegram `/idle` now use it, so a lost paste is retried on the next idle tick
  (as `nudge`'s contract always promised). Reminder: this retry is the liveness
  supervisor's job — a swarm running without `serve`/`supervise` has no heartbeat
  to fire it.

## 🎉 [2.1.0] — 2026-07-14

### ✨ Added
- **MCP server — manage Agentainer from a coding agent.** A new **fourth control
  plane** (CLI / UI / Telegram / **MCP**): a coding agent can monitor and manage
  every swarm over the Model Context Protocol (new module
  [`lib/mcp.py`](lib/mcp.py), a thin JSON-RPC 2.0 adapter over the same tested
  `lib/` core). Two transports, one tool set — `agentainer mcp` (stdio; no
  running `serve` needed, operates over the global registry) and `POST /mcp` on
  the `serve` control plane (reuses the Bearer token; POST-only). Tools cover
  monitoring (`list_swarms`, `swarm_status`, `read_inbox`, `read_queue`,
  `read_user_inbox`, `agent_logs`, `capture_pane`, `read_config`) and management
  (`send_message`, `set_availability`, `start_agent`/`stop_agent`,
  `up_swarm`/`down_swarm`, `create_swarm`, `add_agent`/`remove_agent`). Tool
  problems return `isError` results so the model self-corrects, like `system`
  mail. Zero new dependencies. Docs: [`docs/mcp.md`](docs/mcp.md); this is a
  permanent, maintained surface (CLAUDE.md principle #7).
- **Multi-swarm control plane — one `serve` runs every swarm.** A single
  `agentainer serve` now manages **every swarm on the machine** instead of one
  config at a time. A new global registry + shared settings live under
  `~/.agentainer/` (override with `$AGENTAINER_STATE_DIR`), kept separate from
  each swarm's per-`root` `.agentainer/` runtime. Any swarm you `up` (from any
  directory) auto-registers and appears in the dashboard. New module
  [`lib/registry.py`](lib/registry.py); every UI route is swarm-scoped via
  `?swarm=<name>`; `serve -c one.yaml` still works (single-swarm back-compat).
- **`swarms` command group** (CLI ⇄ UI ⇄ Telegram parity): `list`, `create`
  `[--template <example>]`, `register <path>`, `remove`, `up`, `down`, `build`,
  `approve`, `use`.
- **Guided swarm creation.** Create a swarm from a **bundled example** (preview +
  edit the YAML inline), or have a **coding-agent build it for you**: pick a CLI
  and Agentainer opens an **interactive tmux session** you talk to in the browser
  (new [`lib/scaffold.py`](lib/scaffold.py)); it writes `agentainer.yaml`, then
  **Approve & Launch**. New endpoints: `GET /api/examples`, `POST
  /api/swarms/{create,up,down,register,remove,build,approve}`.
- **Shared Telegram across all swarms.** Configure **one** bot once (Settings →
  Telegram, stored globally in `~/.agentainer/`) and every swarm shares it; a
  per-swarm `telegram:` block still overrides it. A single control-plane inbound
  poller drives any swarm from your phone, with `/swarms` (list) and `/use
  <name>` (switch the active swarm). A first-login nudge appears when Telegram is
  off.
- **Redesigned, beginner-friendly UI** (full front-end rewrite, still
  zero-dependency): swarm switcher + dashboard, the create flow above, and
  sensible defaults with the rarely-touched knobs collapsed under "Configure
  Advanced Settings" — it runs out of the box.

- **Cron-scheduled pings (`pings:`).** An agent (or `defaults:`) can now carry a
  list of scheduled pings, each with its own `message` and a standard 5-field
  `cron` expression (`minute hour day-of-month month day-of-week`), so an agent
  can be nudged with **different messages at different times** — working hours,
  nights, weekends — rather than a single fixed message on a raw idle cadence. The zero-dependency
  parser ([`lib/cron.py`](lib/cron.py)) supports `*`, `*/step`, `a-b` ranges,
  `a-b/step`, comma lists, and case-insensitive 3-letter month/day names
  (`jan`–`dec`, `sun`–`sat`; day-of-week `0`/`7` both Sunday), and follows the
  standard Vixie-cron day-of-month/day-of-week rule. Schedules are evaluated in
  the **host's local time** (no timezone database — deliberately zero-dep).
- **Per-rule `when_busy` policy** (`skip` | `queue`, default `skip`): a rule due
  while the agent is mid-turn is either dropped (so a busy agent's mailbox never
  fills with stale pings) or enqueued to wait for the turn to end. Guards carried
  over: at most **one** unhandled ping outstanding across all of an agent's rules
  (no pile-up), each rule fires **at most once per matching minute**, and on
  overlap the **first deliverable rule in list order** wins. Malformed schedules
  are rejected **fail-fast at config load** with an error naming the agent.
- **Two new scheduled example swarms + use-case guides** built around `pings:`:
  **`ops-watchtower`** (an on-call monitoring swarm — a `*/15` business-hours
  sweep with `skip`, an hourly overnight cadence and morning rollup with
  `when_busy: queue`, and a nightly deep probe) and **`content-cadence`** (an
  editorial team on a *weekly* calendar — plan Mon, draft Tue/Thu, review Wed,
  ship Fri, plus a monthly recap on the 1st, showcasing day-of-week and
  day-of-month cron). See [`docs/use-cases/ops-watchtower.md`](docs/use-cases/ops-watchtower.md)
  and [`docs/use-cases/content-cadence.md`](docs/use-cases/content-cadence.md).

### 🔧 Changed
- **`can_talk_to: "*"` now includes the `user`.** The wildcard previously
  expanded to every *other agent* but **not** the reserved `user` mailbox, so a
  `"*"` orchestrator could delegate to peers yet got an ACL bounce trying to
  report back to the human — a footgun. `"*"` now also grants `user` (still never
  `system`). List recipients explicitly if you deliberately want an agent that
  cannot reach the human.

### 🗑️ Removed
- **Legacy per-agent ping fields `periodically_ping_seconds` /
  `periodically_ping_message`.** These are superseded by the richer cron `pings:`
  list above; keeping both was redundant and confusing. Migrate a
  `periodically_ping_seconds: N` / `periodically_ping_message: "…"` pair to a
  single `pings` rule, e.g. `pings: [{cron: "*/10 * * * *", message: "…"}]`.
  The old keys are now ignored if present in a config.

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

[2.1.1]: https://github.com/mehmetcanfarsak/Agentainer/releases/tag/v2.1.1
[2.1.0]: https://github.com/mehmetcanfarsak/Agentainer/releases/tag/v2.1.0
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

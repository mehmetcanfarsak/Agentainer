# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] — 2026-07-11

### Added
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

### Changed
- Branding: "swarm" retired — it's **Agentainer** everywhere (config is
  `agentainer.yaml`, runtime dir `.agentainer/`, env `AGENTAINER_HOME`).
- `can_talk_to` ACL is now enforced at routing time (a disallowed send is bounced
  as `system` mail into `failed/`), and `user`/`system` are reserved virtual
  mailboxes.

### Removed
- v1's tagged-envelope messaging, reply-reminder subsystem, and `broadcast`.

[2.0.0]: https://github.com/mehmetcanfarsak/Agentainer/releases/tag/v2.0.0

# Agentainer v2 — Documentation

Agentainer v2 is a zero-dependency multi-agent orchestrator. It launches coding-agent
CLIs (Claude Code, Codex, Gemini, Hermes) each in its own tmux session and working
directory, defined by a single `agentainer.yaml`, and lets them message each other
through a **file-based mail model** (read a file / write a file) gated by a
`can_talk_to` ACL.

This folder holds the user-facing documentation. The design record is
[`ProjectPlan.md`](../ProjectPlan.md); the operator/agent guidance is
[`CLAUDE.md`](../CLAUDE.md); the discovery layer is [`README.md`](../README.md) and
[`llms.txt`](../llms.txt).

## Start here

- **[Getting Started](getting-started.md)** — install, write your first
  `agentainer.yaml`, `up` a swarm, watch it work, stop it. The fastest path from
  zero to a running swarm.

## How it works (concepts & reference)

- **[The Mail Model](mail-model.md)** — the file-based messaging architecture:
  the four folders + `user`/`system` virtual mailboxes, one-at-a-time release,
  stop-triggered pickup, ACL routing and bounces, nudges, read-state, periodic
  pings, the runaway-loop cap, and the durable JSONL log.
- **[Configuration Reference](configuration.md)** — every field of `agentainer.yaml`
  (`swarm:`, `defaults:`, `agents:`, `telegram:`), with types, defaults, and
  common mistakes.
- **[CLI Reference](cli-reference.md)** — every subcommand and flag, grouped by
  purpose (everyday, UI/control-plane, dynamic reconcile, lifecycle, internal).
- **[Sessions & Resume](sessions-and-resume.md)** — resume-by-default, what
  `.agentainer/sessions.yaml` stores, `remove-session`, and the shell-wrapper
  `resume_command` pitfall.
- **[UI Guide](ui-guide.md)** — the `agentainer serve` HTTP control plane
  (observability, terminal snapshot, send-from-UI, availability toggle, dynamic
  reconcile) and its security invariants.
- **[Telegram Bridge](telegram-bridge.md)** — mirror agent mail to a Telegram chat
  so you stay reachable from your phone.

## Real-world use cases

- **[Remote Access via Tailscale](use-cases/remote-access.md)** — reach your
  swarm's UI from your phone or laptop anywhere, safely, over a private mesh VPN
  (plus an SSH-tunnel alternative).
- **[Multi-LLM Swarm](use-cases/multi-llm-swarm.md)** — mix Claude, Codex, Gemini,
  and Hermes in one swarm; the `type`↔`command` contract, per-type turn detection,
  and capture resolution.
- **[Resume After Reboot](use-cases/resume-after-reboot.md)** — `down` (or a reboot)
  then `up` restores each agent's conversation; what persists and what doesn't.
- **[Delegation Pipeline](use-cases/delegation-pipeline.md)** — the
  `user → orchestrator → developer → user` hub-and-spoke pattern, with the in-band
  ACL bounce.
- **[Custom Workspace & Namespacing](use-cases/custom-workspace.md)** — per-agent
  `workdir`, shared workdirs (auto-namespaced mail), and custom `mail_dir`.
- **[Research Swarm Walkthrough](use-cases/research-swarm.md)** — an end-to-end run
  of the shipped `examples/research.yaml` (coordinator / researcher / reviewer).

## Security note

The UI and any agent running `--dangerously-skip-permissions`/`--yolo` are a control
plane. Never bind `serve` to `0.0.0.0` without a token, and prefer a private tunnel
(Tailscale / SSH) over a raw public exposure. Treat agent `command` strings and
`telegram.bot_token` as secrets — keep them out of git.

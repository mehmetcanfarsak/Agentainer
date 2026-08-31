#!/usr/bin/env bash
# Claude Code `SessionStart` hook: fires when a session starts, resumes, or
# restarts after a compaction. Claude passes a JSON payload on stdin with a
# `source` field ("startup" | "resume" | "clear" | "compact"). A compaction (or
# resume/clear) wipes the last nudge -- the mailbox paths and protocol -- from
# the model's context, so we re-present its current inbox message immediately
# instead of waiting for a supervisor tick. `agentainer hook` ignores `startup`
# (the normal launch flow already delivers the first prompt).
#
# A hook must never break the agent it is attached to, so every failure here is
# swallowed and the script always exits 0.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log="/dev/null"
if [[ -n "${AGENTAINER_ROOT:-}" ]] && mkdir -p "$AGENTAINER_ROOT/.agentainer/logs" 2>/dev/null; then
  log="$AGENTAINER_ROOT/.agentainer/logs/hooks.log"
fi

"$HERE/agentainer" hook claude --event sessionstart >>"$log" 2>&1
exit 0

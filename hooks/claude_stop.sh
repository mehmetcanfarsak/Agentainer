#!/usr/bin/env bash
# Claude Code `Stop` hook: fires when Claude finishes responding.
# Claude passes a JSON payload on stdin containing `transcript_path`.
# Installed automatically into <agent-workdir>/.claude/settings.json by
# `agentainer up`.
#
# A hook must never break the agent it is attached to, so every failure here is
# swallowed and the script always exits 0.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log="/dev/null"
if [[ -n "${AGENTAINER_ROOT:-}" ]] && mkdir -p "$AGENTAINER_ROOT/.agentainer/logs" 2>/dev/null; then
  log="$AGENTAINER_ROOT/.agentainer/logs/hooks.log"
fi

"$HERE/agentainer" hook claude >>"$log" 2>&1
exit 0

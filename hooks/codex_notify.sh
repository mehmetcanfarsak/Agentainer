#!/usr/bin/env bash
# Codex `notify` program: codex invokes it with a JSON payload as $1 whenever a
# turn completes (payload type: "agent-turn-complete", with last-assistant-message).
# Wired up via <agent-workdir>/.codex/config.toml + CODEX_HOME by `agentainer up`.
#
# Always exits 0 so a hook failure can never disturb the agent.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log="/dev/null"
if [[ -n "${AGENTAINER_ROOT:-}" ]] && mkdir -p "$AGENTAINER_ROOT/.agentainer/logs" 2>/dev/null; then
  log="$AGENTAINER_ROOT/.agentainer/logs/hooks.log"
fi

"$HERE/agentainer" hook codex "${1:-}" >>"$log" 2>&1
exit 0

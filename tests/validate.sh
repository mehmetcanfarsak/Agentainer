#!/usr/bin/env bash
# tests/validate.sh — key-free, model-free end-to-end smoke test of the REAL
# `agentainer` CLI against a mock (bash-loop) swarm. Exercises the load-bearing
# v2 behaviours: validate, up, one-at-a-time inbox release, ACL allow + bounce,
# user hold-not-bounce, and down. Parser-parity checks (minyaml == PyYAML) SKIP
# gracefully when PyYAML is absent, so a pure-stdlib clone still passes green.
#
# No API keys, no network, no model calls. Run: `bash tests/validate.sh`.

set -uo pipefail

pass=0; fail=0; skip=0

ok()    { echo "  PASS: $1"; pass=$((pass + 1)); }
bad()   { echo "  FAIL: $1"; fail=$((fail + 1)); }
skipped() { echo "  SKIP: $1"; skip=$((skip + 1)); }

assert_contains() {            # desc, got, needle
  if printf '%s' "$2" | grep -qF -- "$3"; then ok "$1"; else bad "$1 (missing: $3)"; fi
}
assert_eq() {                  # desc, got, want
  if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (got '$2' want '$3')"; fi
}

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" || { echo "cannot cd to $REPO"; exit 1; }

if [ -x ./agentainer ]; then AG="./agentainer"; else AG="agentainer"; fi

TMP="$(mktemp -d)"
CFG="$TMP/swarm.yaml"
WS="$TMP/ws"
cat > "$CFG" <<YAML
swarm:
  name: validate
  root: $WS
defaults:
  capture: none            # mock agents never fire a turn-completion hook
  can_talk_to: []
agents:
  - name: orchestrator
    type: claude
    can_talk_to: [developer, reviewer, user]   # may also talk to the human
    command: "bash -c 'while true; do read -r l || sleep 1; done'"
  - name: developer
    type: codex
    can_talk_to: [orchestrator]                 # may NOT reach reviewer or user
    command: "bash -c 'while true; do read -r l || sleep 1; done'"
  - name: reviewer
    type: claude
    can_talk_to: [developer]
    command: "bash -c 'while true; do read -r l || sleep 1; done'"
YAML

cleanup() { "$AG" down -c "$CFG" >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT

# status_unread <agent> -> the unread= count from `status`
status_unread() {
  "$AG" status -c "$CFG" 2>/dev/null \
    | grep -E "^  ${1} \(" \
    | grep -oE 'unread=[0-9]+' | head -1 | cut -d= -f2
}

if ! command -v tmux >/dev/null 2>&1; then
  echo "== validate.sh =="
  skipped "tmux not installed — skipping live swarm checks (run on a host with tmux)"
else
  echo "== validate.sh =="

  # ---- validate ----
  OUT=$("$AG" validate -c "$CFG" 2>&1); RC=$?
  assert_eq "validate exits 0" "$RC" "0"
  assert_contains "validate prints orchestrator" "$OUT" "orchestrator"

  # ---- up ----
  OUT=$("$AG" up -c "$CFG" --no-supervise 2>&1); RC=$?
  assert_eq "up exits 0" "$RC" "0"
  OUT=$("$AG" status -c "$CFG" 2>&1)
  assert_contains "status shows orchestrator up" "$OUT" "orchestrator"
  assert_contains "status shows developer up" "$OUT" "developer"

  # ---- one-at-a-time inbox release ----
  "$AG" send -c "$CFG" --to developer "task one" >/dev/null 2>&1
  "$AG" send -c "$CFG" --to developer "task two" >/dev/null 2>&1
  INBOX=$("$AG" inbox -c "$CFG" developer 2>&1)
  CNT=$(printf '%s\n' "$INBOX" | grep -c '^--- ')
  assert_eq "one-at-a-time: exactly one inbox message for developer" "$CNT" "1"

  # ---- ACL: allowed send is delivered ----
  U0=$(status_unread orchestrator)
  "$AG" send -c "$CFG" --from developer --to orchestrator "ping" >/dev/null 2>&1
  U1=$(status_unread orchestrator)
  if [ "${U1:-0}" -gt "${U0:-0}" ]; then ok "ACL allow: developer->orchestrator delivered"; \
  else bad "ACL allow: orchestrator unread did not increase ($U0 -> $U1)"; fi

  # ---- ACL: disallowed send is bounced (never delivered, bounce returns to sender) ----
  R0=$(status_unread reviewer)
  "$AG" send -c "$CFG" --from developer --to reviewer "hi" >/dev/null 2>&1
  R1=$(status_unread reviewer)
  if [ "${R1:-0}" = "${R0:-0}" ]; then ok "ACL bounce: reviewer never received the disallowed mail"; \
  else bad "ACL bounce: reviewer unread changed ($R0 -> $R1)"; fi
  Q=$("$AG" queue -c "$CFG" developer 2>&1)
  assert_contains "bounce returned to sender as system mail" "$Q" "From: system"

  # ---- user hold-not-bounce (user is away by default) ----
  H0=$("$AG" user inbox -c "$CFG" 2>&1)
  "$AG" send -c "$CFG" --from orchestrator --to user "progress?" >/dev/null 2>&1
  H1=$("$AG" user inbox -c "$CFG" 2>&1)
  if [ -n "$H1" ] && [ "$H1" != "$H0" ]; then ok "user hold: mail queued for the away user (not bounced)"; \
  else bad "user hold: no held mail for the away user"; fi

  # ---- down ----
  OUT=$("$AG" down -c "$CFG" 2>&1); RC=$?
  assert_eq "down exits 0" "$RC" "0"
  OUT=$("$AG" status -c "$CFG" 2>&1)
  assert_contains "status shows orchestrator down" "$OUT" "orchestrator (claude) down"
fi

# ---- parser parity (minyaml == PyYAML), SKIP if PyYAML absent ----
echo "== parser parity (minyaml vs PyYAML) =="
if python3 -c "import yaml" >/dev/null 2>&1; then
  for f in examples/*.yaml; do
    if python3 - "$f" <<'PY'
import sys, importlib.util
spec = importlib.util.spec_from_file_location("minyaml", "lib/minyaml.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
import yaml
txt = open(sys.argv[1]).read()
try:
    a = m.load(txt)
except m.YAMLError as e:
    if "multi-document" in str(e):
        sys.exit(0)
    print("minyaml error:", e); sys.exit(1)
b = yaml.safe_load(txt)
sys.exit(0 if a == b else 1)
PY
    then ok "parity: $f"; else bad "parity: $f (minyaml != PyYAML)"; fi
  done
else
  skipped "PyYAML absent — parser-parity checks (minyaml==PyYAML) skipped"
fi

echo "==== $pass passed, $fail failed, $skip skipped ===="
[ "$fail" -eq 0 ]

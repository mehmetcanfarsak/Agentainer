# Contributing to Agentainer

Thanks for your interest in improving Agentainer! This project is a
zero-dependency multi-agent orchestrator; most of its value is in being correct,
boring, and unattended-friendly. A few conventions keep it that way.

## Ground rules

1. **Zero runtime dependencies, forever.** The runtime is Python 3 + bash + tmux.
   PyYAML is optional (a bundled `minyaml` fallback must keep working without it).
   Do not add a `pip install` to the swarm runtime or the UI. The UI is stdlib
   `http.server` + one vanilla-JS page — no framework, no build step.
2. **Tests are a release gate, not a vanity metric.** The whole suite must stay
   at **100% line coverage** on every `lib/` module, driven entirely by mock
   agents (bash loops — no API keys, nothing to pay for). A wedged or silently
   broken agent is a failure only a user notices hours later, so every error path
   an unattended swarm could hit is tested.
3. **The model only reads and writes files.** Keep hard logic in the
   orchestrator. Never push formatting, bookkeeping, or protocol-memory onto the
   agent — that's the orchestrator's job (see `CLAUDE.md` Principles).
4. **`package.json` is the single source of truth for version.** Tag releases as
   `v<version>` so the publish workflow's tag/version check passes.

## Getting started

```bash
git clone <repo> && cd Agentainer
python3 -m venv .venv && . .venv/bin/activate
python -m pip install pytest pytest-cov   # PyYAML optional; the suite runs without it
./agentainer validate examples/quickstart.yaml
pytest tests/ -q
```

Run the key-free end-to-end smoke test (spins up a mock swarm in tmux):

```bash
bash tests/validate.sh
```

## Before you open a PR

- `pytest tests/ -q` is green at 100% line coverage:
  `python3 -m coverage run -m pytest tests/ -q && python3 -m coverage report --include='lib/*'`
- `bash tests/validate.sh` passes.
- `python -m py_compile lib/*.py` is clean and ShellCheck passes on `hooks/*.sh`,
  `agentainer`, and `bin/agentainer.js`.
- New behaviour gets a mock-agent test; do not require API keys.

## Commit / branch conventions

- Feature work happens on a branch off `main` (or the active release branch); do
  not commit directly to `main` without review.
- Keep commits focused; reference the relevant `ProjectPlan.md` section or
  decision (D1–D24) when behaviour changes.
- If code and `ProjectPlan.md` disagree, update the plan first, then the code.

## Reporting bugs & requesting features

Please use the GitHub issue templates (`bug_report`, `feature_request`). For
security issues, follow `SECURITY.md` — do **not** open a public issue.

# 🛡️ Security Policy

## 🔖 Supported versions

Only the latest `2.x` release line receives security fixes. Older lines are not
patched.

## 🔐 Reporting a vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Instead, report privately via GitHub's
[private vulnerability reporting](https://github.com/mehmetcanfarsak/Agentainer/security/advisories/new)
or email the maintainer. Include:

- a description of the issue and its impact,
- steps to reproduce (or a proof of concept),
- the affected version(s).

You can expect an acknowledgement within a few days. Once the issue is confirmed
and a fix is ready, we will coordinate disclosure with you.

## 🔍 Scope & trust model

Agentainer is an **orchestrator that runs other agent CLIs in `tmux` sessions.**
Be aware of its threat model before deploying it:

- **The `can_talk_to` ACL is cooperative, not an OS sandbox.** It is enforced for
  well-behaved agents at the routing layer, but an agent with filesystem access
  *could* write straight into another agent's `inbox/` and bypass `outbox/`. It is
  documented honestly as an access-control aid, **not** a security boundary. Do
  not rely on it to isolate untrusted agents.
- **Agents can run with elevated flags.** The control-plane UI and `up` can launch
  agents that use `--dangerously-skip-permissions` / `--yolo`. The UI therefore
  binds `127.0.0.1` by default and requires a token for any non-loopback bind.
  Only expose it behind a trusted network or a reverse proxy with auth.
- **Command strings may embed secrets.** Agent `command:` fields can include API
  keys (e.g. via shell aliases). Treat configs and agent workspaces as sensitive;
  never commit them, and prefer environment-provided credentials.
- **Disposable `root`.** Run swarms under a throwaway `root` so an agent that goes
  rogue is contained to that workspace.

## 🔗 Supply chain

Releases are published to npm from GitHub Releases using
`.github/workflows/publish.yml`, which publishes with
`npm publish --provenance --access public` and verifies the git tag matches the
`package.json` version. Provenance attestations let you verify the published
package was built by this repository's CI.

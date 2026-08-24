---
name: secrets-scan
description: Scan changed files for hard-coded secrets (API keys, tokens, passwords, private keys) before approving a change.
---

# Secrets scan

When reviewing a diff or a set of files, look for committed secrets:

- API keys / tokens: long high-entropy strings, `sk-`, `ghp_`, `AKIA…`, bearer tokens.
- Passwords or connection strings embedded in source or config.
- Private keys (`-----BEGIN … PRIVATE KEY-----`), `.pem`, `.p12`.
- `.env` files or credentials checked into the repo.

For each finding report the file, line, and a safe remediation (move to an env var
or secret manager, rotate the exposed credential). If nothing is found, say so
explicitly. Never print the full secret value back — mask it.

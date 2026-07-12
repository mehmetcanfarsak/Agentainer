# Use case: custom workspaces, shared workdirs, and mailbox namespacing

**Audience:** operators who want agents to work inside their own repos, or who
want several agents to collaborate inside one shared directory without their
mailboxes clobbering each other.

This document covers the `workdir`, `mail_dir`, and `env` knobs on an agent,
and the automatic mailbox *namespacing* that kicks in when two or more agents
share a working directory. Every statement here is grounded in `lib/config.py`
(the loader and `SwarmConfig.mail_paths`) and `lib/mail.py` (how paths reach
the model). Where the code is the authority, the code wins.

> **Principle 3 (from CLAUDE.md):** the model is always told its exact paths; it
> never assumes them. Custom `workdir`s, custom `mail_dir`s, and shared-workspace
> prefixes are all computed by the orchestrator and handed to the model verbatim
> in every nudge and first prompt — the model never has to reason about any of
> this. That property is what makes the machinery below "invisible and safe."

---

## 1. Defaults

When you write an agent with no `workdir` and no `mail_dir`, Agentainer derives
everything from `swarm.root` (default `./workspace`, resolved relative to the
config file's parent):

| Setting | Default | Notes |
|---|---|---|
| `workdir` | `<root>/<name>` | One directory per agent, e.g. `./workspace/alice`. |
| `mail_dir` | same as `workdir` | The five mailbox folders live *inside* the workdir by default. |
| `create_workdir` | `true` | Agentainer creates the workdir for you at `up`. |

So the on-disk layout for an agent `alice` with root `./workspace` is:

```
workspace/
  alice/                       <- workdir
    inbox/                     <- the one current unread message
    outbox/
      bob/about.md             <- bob's contact card (ACL gate)
      user/about.md            <- (if alice may message the user)
    read/                      <- handled messages (best-effort receipt)
    sent/                      <- alice's own sent record
    failed/                    <- bounced / rate-limited mail
```

The five folders are `inbox`, `outbox`, `read`, `sent`, and `failed` — resolved
by `SwarmConfig.mail_paths()` (see §4). They are created by `mail.init_mailboxes()`
at `up`; the workdir itself is created by the CLI's `start_agent()` (guarded by
`create_workdir`).

Because `create_workdir` defaults to `true`, the simplest possible config "just
works" and spins up scratch directories for a dry run.

---

## 2. Per-agent `workdir` — point an agent at an existing repo

Set `workdir` to an existing directory and the agent runs *inside your code*
instead of a scratch dir. Two things to get right:

1. **Path resolution.** `workdir` is expanded with the placeholders `{name}`,
   `{root}`, `{swarm}` (the swarm name), and `{type}`, then resolved relative to
   the config file's parent directory (or used as-is if absolute / `~`-expanded).
2. **`create_workdir`.** If the directory already exists, leave `create_workdir`
   at its default `true` — it is harmless (creation is `mkdir(..., exist_ok=True)`).
   If the directory does **not** exist and you want Agentainer to make it, keep
   `create_workdir: true`. If it does not exist and you want Agentainer to refuse
   (fail-fast rather than silently creating something), set `create_workdir: false`.

```yaml
# Point a developer agent straight at your real checkout.
swarm:
  name: myapp
  root: ./myapp-workspace

agents:
  - name: developer
    type: claude
    command: "claude --dangerously-skip-permissions"
    workdir: ../my-app          # an existing repo, relative to this config file
    create_workdir: false       # it already exists; refuse if it doesn't
    can_talk_to: [orchestrator]
    role: |
      You are a developer working in the my-app repository.
      The repo is mounted as your working directory.

  - name: orchestrator
    type: claude
    command: "claude --dangerously-skip-permissions"
    can_talk_to: [developer]    # default workdir ./myapp-workspace/orchestrator
    role: "Delegate tasks to the developer."
```

You can also use placeholders to keep things DRY:

```yaml
workdir: {root}/{name}          # identical to the default, but explicit
workdir: ../repos/{name}        # each agent gets ../repos/<name>
workdir: /srv/checkouts/{swarm}/{type}
```

A `workdir` that resolves to something that is not a directory is rejected at
`up` (the loader checks `workdir.exists()` and `is_dir()`), so a typo fails fast
instead of silently creating a file.

---

## 3. Shared workspace + automatic namespacing

Sometimes you want two or more agents to collaborate *in the same directory* —
e.g. two engineers pair-programming on one checkout, or a "reviewer" and a
"writer" editing the same docs tree.

The problem: if their mailbox folders were all named `inbox/`, `outbox/`, …,
agent A's mail would land in the same folder as agent B's and they'd collide.
Agentainer solves this **automatically** — no config flag required.

### How it works

When the config loads, `SwarmConfig.__post_init__` counts how many agents
resolve to each `workdir`. Any workdir shared by **two or more** agents is
recorded in `self._shared` (a `set` of resolved paths).

Then `mail_paths(agent)` applies a `<name>-` prefix to every one of the five
mailbox folders *when* that agent's workdir is in the shared set:

```python
prefix = agent.name + "-" if agent.workdir.resolve() in self._shared else ""
return SimpleNamespace(
    inbox   = base / (prefix + "inbox"),
    outbox  = base / (prefix + "outbox"),
    read    = base / (prefix + "read"),
    sent    = base / (prefix + "sent"),
    failed  = base / (prefix + "failed"),
)
```

So for two agents `alice` and `bob` sharing `./shared`, the disk layout becomes:

```
shared/
  alice-inbox/
  alice-outbox/
  alice-read/
  alice-sent/
  alice-failed/
  bob-inbox/
  bob-outbox/
  bob-read/
  bob-sent/
  bob-failed/
  # ...your actual project files live here too, unprefixed
```

Each agent's `outbox/<peer>/about.md` contact cards are likewise namespaced, so
ACL folders never collide either.

### Why this is invisible and safe (Principle 3)

The prefix is **orchestrator-internal bookkeeping**. The model never sees,
computes, or reasons about it. Every time Agentainer talks to the model it calls
`cfg.mail_paths(agent)` first and hands over the *already-computed* absolute
paths:

- `mail.standby_prompt()` (the agent's first message at `up`) prints the exact
  `inbox` / `outbox` / `read` paths, e.g.
  `inbox:   /abs/shared/alice-inbox`.
- `mail.nudge()` (fired when mail arrives) pastes the exact `inbox` / `read` /
  `outbox` paths into the agent's pane.

Because the model is *told* the final paths rather than told a rule ("use your
name as a prefix"), a weak model can't get the prefix wrong, and a shared
workspace is indistinguishable from a private one from the model's point of view.
Correctness depends on the orchestrator, not the model — which is exactly the
design goal.

> **Note:** namespacing is keyed off the *workdir*, not the `mail_dir`. If two
> agents share a `workdir` but point `mail_dir` somewhere else, their mail folders
> in that other location are **still** prefixed with `<name>-` (the share is
> detected from the workdir). This keeps the rule simple and predictable: "share a
> workdir → get prefixed mail."

The loader also emits a friendly warning when agents share a workdir, reminding
you that they can overwrite each other's files and that a shared git checkout
will interleave their commits. It does not block the run — sharing is allowed,
just cooperative.

---

## 4. Custom `mail_dir` — separate mail from work

By default mail lives *inside* the workdir (`mail_dir` == `workdir`). You can put
mail elsewhere — a fast local tmpfs, a shared network volume, or a path you want
to wipe independently of the source — with `mail_dir`. The same placeholder
expansion (`{name}`, `{root}`, `{swarm}`, `{type}`) applies, and the default when
`mail_dir` is omitted is the agent's `workdir`.

```yaml
swarm:
  name: myapp
  root: ./myapp-workspace

defaults:
  mail_dir: /mnt/agent-mail/{swarm}   # all agents' mail under one shared volume

agents:
  - name: alice
    type: claude
    command: "claude --dangerously-skip-permissions"
    workdir: ../my-app               # source code lives in your repo
    mail_dir: /mnt/agent-mail/{swarm}/alice   # but her mail lives on the volume
    can_talk_to: [bob]
    role: "..."

  - name: bob
    type: claude
    command: "claude --dangerously-skip-permissions"
    workdir: ../my-app               # shares alice's workdir...
    mail_dir: /mnt/agent-mail/{swarm}/bob
    can_talk_to: [alice]
    role: "..."
```

In this example `alice` and `bob` share `../my-app` as their workdir, so even
though their `mail_dir`s are different, **both** get `<name>-` prefixed mail
folders (because the share is detected from the workdir). The result:

```
/mnt/agent-mail/myapp/alice/alice-inbox/   ...
/mnt/agent-mail/myapp/bob/bob-inbox/       ...
../my-app/                                 <- shared source, no mail folders
```

`mail_paths()` always resolves the five folders from `mail_dir` + the
share-prefix. So `mail_dir` controls *where* mail lives; the prefix (when the
workdir is shared) controls *that it won't collide*. You get to choose the
location; Agentainer guarantees the uniqueness.

---

## 5. `env` — pass extra environment to an agent

Sometimes an agent needs credentials or configuration that you don't want baked
into the `command` string (API keys, a base URL, a feature flag). Use `env` to
pass them as process environment variables instead. Precedence, lowest to
highest:

1. `defaults.env` (swarm-wide defaults)
2. `agent_types.<type>.env` (per agent-type)
3. per-agent `env` (wins)

```yaml
defaults:
  env:
    LOG_LEVEL: info

agents:
  - name: analyst
    type: claude
    command: "claude --dangerously-skip-permissions"
    can_talk_to: [orchestrator]
    env:
      OPENAI_API_KEY: ${OPENAI_API_KEY}   # from the operator's shell, not the yaml
      ANALYTICS_BASE_URL: https://analytics.internal
    role: "You have OPENAI_API_KEY and ANALYTICS_BASE_URL in your environment."
```

The agent's launcher exports these vars before `cd`-ing into the workdir and
running `command`, so the agent process sees them but the values are never
stamped into a prompt.

> **Security:** `command` and `env` are treated as *sensitive*. The loader
> accepts env values verbatim; don't commit secrets into your `agentainer.yaml`.
> Prefer pulling values from the operator's environment (as in the `${VAR}`
> example) rather than hardcoding them. Agentainer never prints `command`/`env`
> contents back to you in most output paths, but the config file itself is the
> thing you must keep out of version control.

---

## 6. Worked example

Three agents: `alice` and `bob` pair-program in one shared repo workdir (their
mail is auto-namespaced); `carol` works in her own private workdir; `dave` works
in his own workdir but keeps his mail on a separate volume via `mail_dir`.

```yaml
swarm:
  name: trio
  root: ./trio-workspace

defaults:
  type: claude
  command: "claude --dangerously-skip-permissions"

agents:
  # alice + bob SHARE a workdir -> their mail folders get alice-/ bob- prefixes.
  - name: alice
    workdir: ../our-repo
    create_workdir: false
    can_talk_to: [bob, carol]
    role: "Engineer A. Pair with bob in our-repo."

  - name: bob
    workdir: ../our-repo
    create_workdir: false
    can_talk_to: [alice]
    role: "Engineer B. Pair with alice in our-repo."

  # carol: her own private workdir (default ./trio-workspace/carol).
  - name: carol
    can_talk_to: [alice]
    role: "Reviewer. Works in your own workspace."

  # dave: own workdir, but mail on a separate volume.
  - name: dave
    workdir: ./trio-workspace/dave
    mail_dir: /mnt/agent-mail/trio/dave
    can_talk_to: [carol]
    env:
      SOME_TOKEN: ${SOME_TOKEN}
    role: "Specialist. Source in dave/, mail on the volume."
```

Resulting on-disk layout:

```
../our-repo/
  alice-inbox/  alice-outbox/  alice-read/  alice-sent/  alice-failed/
  bob-inbox/    bob-outbox/    bob-read/    bob-sent/    bob-failed/
  <your real project files>

./trio-workspace/
  carol/   (inbox outbox read sent failed)   <- private, unprefixed
  dave/    (inbox outbox read sent failed)   <- private workdir

/mnt/agent-mail/trio/dave/
  dave-inbox/ dave-outbox/ dave-read/ dave-sent/ dave-failed/   <- dave's mail
```

Each agent, at `up`, receives a standby prompt stating its *exact* computed
paths (`alice` sees `../our-repo/alice-inbox`, `dave` sees
`/mnt/agent-mail/trio/dave/dave-inbox`, etc.). None of them needs to know about
prefixes, volumes, or sharing — the orchestrator told them precisely where to
read and write.

---

## 7. Gotchas

- **Don't hand-collide folder names.** Never name an agent's `workdir` the same
  as another agent's *namespaced* mail folder, and don't create a directory whose
  name would clash with a generated `<name>-inbox` etc. Let Agentainer generate
  the prefixes; inventing your own matching names is the one way to defeat the
  collision avoidance.

- **`remove-session` wipes mailboxes, not your source.** `agentainer remove-session`
  deletes two classes of state: the orchestrator `.agentainer/` runtime (sessions,
  queue, turn state, logs) **and** each agent's five mailbox folders. It
  deliberately **never** touches the agents' own source files or the config. So
  you can safely reset a run (and drop in-flight mail) without losing your code.
  It refuses to run while any agent or the supervisor is still up — `down` first.

- **A shared workdir means agents share *files*, not just mail.** Namespacing
  only separates the *mailbox* folders. The project files themselves are shared
  on disk, so `alice` and `bob` can overwrite each other's edits and interleave
  commits in a shared git checkout. The `can_talk_to` ACL is still just
  cooperative (it governs who may *mail* whom, not filesystem access — see
  CLAUDE.md footgun "the `can_talk_to` ACL is cooperative, not OS isolation").
  Coordinate through mail; don't assume isolation.

- **A `workdir` that doesn't exist + `create_workdir: false` is a hard error.**
  The loader rejects it at `up` with a message telling you to create it or flip
  `create_workdir: true`. This is intentional fail-fast, not a bug.

- **Sharing is detected by resolved path.** `../our-repo` and `./../our-repo`
  resolve to the same directory and *will* be treated as shared. Conversely, two
  paths that resolve to different directories (even if they look similar) are
  treated as separate and **won't** be prefixed — so if you *intended* to share,
  make sure both agents point at the exact same resolved location.

- **Don't put secrets in `command` or `env` in committed files.** Keep credentials
  in the operator's shell environment and reference them (the `${VAR}` pattern),
  or in a `agentainer.yaml` that is gitignored. Agentainer treats these fields as
  sensitive and won't print them, but the file on disk is your responsibility.

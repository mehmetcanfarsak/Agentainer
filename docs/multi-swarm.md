# Multi-swarm control plane

One `agentainer serve` manages **every swarm on your machine** — you no longer
run a server per swarm or pass `-c` to point at one config. This page covers the
global registry, the swarms dashboard, creating swarms (three ways), and how the
CLI, UI, and Telegram stay at parity.

## The two state homes

Agentainer keeps two clearly separated homes:

| Home | Location | Holds |
| --- | --- | --- |
| **Global control plane** | `~/.agentainer/` (override: `$AGENTAINER_STATE_DIR`) | the swarm **registry** (`registry.yaml`), shared **settings** (`settings.yaml` — the one Telegram bot + the Telegram-active swarm), swarms you create in the UI (`swarms/<name>/`), and the shared Telegram offset |
| **Per-swarm runtime** | `<root>/.agentainer/` | that swarm's logs, per-agent queue, turn state, `sessions.yaml` |

Neither is ever committed or shipped. `$AGENTAINER_STATE_DIR` is **not**
`$AGENTAINER_HOME` (the code/install root).

Any swarm you `up` — from any directory — **auto-registers**, so it shows up in
`serve` and `swarms list` without extra steps.

## Serve everything

```bash
agentainer serve                       # manages every registered swarm
agentainer serve --port 8080 --token s3cret --host 0.0.0.0   # remote (token required)
agentainer serve -c ./agentainer.yaml  # single swarm (back-compat) — also folded into the registry
```

Open the printed URL. The **Swarms dashboard** is the home screen: a card per
swarm (running/total agents, attention, **Start-all / Stop-all**, **Open**) plus
**➕ New Swarm**. A header **swarm switcher** scopes every view (agents, mail,
terminal, activity, settings) to the selected swarm. It runs **out of the box**:
an empty machine lands on an onboarding empty-state that walks you into creating
your first swarm.

## Managing swarms from the CLI

```bash
agentainer swarms list                         # every registered swarm + live status
agentainer swarms create myteam                # scaffold an empty swarm (unique session_prefix)
agentainer swarms create rsr --template research   # seed from a bundled example
agentainer swarms create myteam --root ~/ws --up   # custom root, bring it up now
agentainer swarms register /path/to/agentainer.yaml   # register an existing config
agentainer swarms up myteam                    # bring a swarm up
agentainer swarms down myteam                  # stop a swarm (+ its supervisor)
agentainer swarms remove myteam                # forget it (config files are left on disk)
agentainer swarms use myteam                   # set the Telegram-active swarm
```

`create` scaffolds a fresh `agentainer.yaml` (under `~/.agentainer/swarms/<name>/`
by default) with a **unique `session_prefix`** so two swarms' tmux sessions never
collide, validates it, and registers it. `remove` only forgets a swarm — it never
deletes your files.

## Creating a swarm, three ways (UI)

Click **➕ New Swarm**, then choose:

1. **Start from an example** — pick from the bundled example swarms, **preview the
   raw YAML**, and either **edit it yourself** inline (it is validated on submit)
   or hand it to a coding-agent (below).
2. **Have a coding-agent build it for you** — pick a CLI
   (`claude`/`codex`/`gemini`/`hermes`). Agentainer opens an **interactive tmux
   session** that you talk to **right in the browser terminal**:
   - *adapt* mode: "here's an example config — change it to …"
   - *scratch* mode: "ask me what swarm I want and which coding-agents to use,
     then write the config file."
   The agent reads the Agentainer schema, asks you questions, and writes
   `agentainer.yaml`. When it's ready, click **Approve & Launch** — Agentainer
   validates the config and brings the swarm up.

The same builder flow is available from the CLI:

```bash
agentainer swarms create mine
agentainer swarms build mine --agent claude --mode scratch --notes "a blog-writing team"
# ... talk to it (agentainer attach -t mine_builder, or via the UI terminal) ...
agentainer swarms approve mine
```

## Shared Telegram (drive any swarm from your phone)

Configure **one** bot once (Settings → Telegram in the UI, or a global
`settings.yaml`) and **every** swarm shares it — a per-swarm `telegram:` block
still overrides it for heavy users. A single inbound poller drives the whole
machine:

- reply to a mirrored message to answer its sender (routes to the right swarm
  automatically);
- run slash-commands (`/status`, `/to <agent> …`, `/up`, `/down`, …) against the
  **active** swarm;
- `/swarms` lists every swarm; `/use <name>` switches which swarm bare commands
  target (same as `agentainer swarms use <name>`).

See [telegram-bridge.md](telegram-bridge.md) for the full command set.

## Parity

Every capability exists on **all three** surfaces — CLI (`swarms …`), UI (the
dashboard + create flow + settings), and Telegram (`/swarms`, `/use`, the
slash-commands). The shared, 100%-tested core lives in `lib/` (`registry.py`,
`scaffold.py`, `telegram.py`); each surface is a thin adapter over it.

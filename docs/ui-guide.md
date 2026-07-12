# The Agentainer UI — HTTP control plane (`agentainer serve`)

Agentainer ships an optional web UI: a **control plane** for a running swarm.
It lets you watch every agent, read the mail flowing between them, peek at each
agent's live terminal, send mail as the `user`, and reshape the swarm
(add/remove/edit agents) — all from a browser.

> **Read this first — the UI is a control plane, not a viewer.** It can start
> and stop agent processes, type raw keystrokes into panes (agents that may run
> `--dangerously-skip-permissions` / `--yolo`), and rewrite `agentainer.yaml`.
> Treat the URL + token like an SSH key. See [Security posture](#security-posture).

The headless CLI is fully functional without the UI — see
[Headless-first](#headless-first). The UI is a convenience layer, never a
requirement.

---

## What it is

- **Zero runtime dependencies.** The server is Python's stdlib
  `http.server` (`ThreadingHTTPServer`) — no framework, no ASGI, no build step.
  The frontend is **one static vanilla-JS page** (`ui/index.html` + `ui/app.js`),
  served as-is. This is the same zero-dependency rule the rest of Agentainer
  lives by (Python 3 + bash + tmux only).
- **A thin shell over the tested core.** Every endpoint in `lib/ui.py` reads
  orchestrator state and calls the already-tested modules (`config`, `mail`,
  `tmux`, `turn`, `reconcile`, `supervisor`). The server never re-implements
  routing, the `can_talk_to` ACL, message IDs, or queueing — it only *reports*
  that state and lets a human inject mail as the virtual `user` mailbox.
- **A mail app.** The page is laid out like a mobile messaging client: a list
  of agents, each agent's correspondence rendered as threads, a reply box that
  sends as `user`, plus a terminal tab and a settings/config editor.

The UI has three top-level views: **Agents**, **Activity**, and **Settings**.

---

## Launching

```bash
./agentainer serve -c agentainer.yaml
```

That binds `http://127.0.0.1:<auto-port>` and prints, to stderr:

```
:: UI serving at http://127.0.0.1:53017
:: UI token: 8f3c…（32 hex chars）
```

Open the printed URL, paste the token into the **Connect** box, and you're in.

### Flags (`cmd_serve` / `p_serve`)

| Flag       | Default                                              | Meaning |
|------------|-----------------------------------------------------|---------|
| `--host`   | `127.0.0.1`                                          | Bind interface. **Never `0.0.0.0` without a token.** |
| `--port`   | `0` → OS picks a free port                           | TCP port. Pass e.g. `--port 4141` to pin it. |
| `--token`  | `--token` → `$AGENTAINER_UI_TOKEN` → freshly generated | Auth secret required for every API call. |
| `-c/--config` | discovered `agentainer.yaml`                      | Which swarm to serve. |

The token resolution order is exactly: the `--token` value if given, else the
`AGENTAINER_UI_TOKEN` environment variable, else a random `secrets.token_hex(16)`
printed to stderr. A generated token changes every run; set `--token` or the env
var if you want a stable one.

Pin the host and port explicitly for a predictable local URL:

```bash
./agentainer serve -c agentainer.yaml --host 127.0.0.1 --port 4141
# -> http://127.0.0.1:4141
```

`serve` runs in the foreground and blocks until `Ctrl-C`, which shuts the server
down cleanly.

### The `up` hint

When you bring a swarm up, `agentainer up` prints the exact `serve` command —
already carrying a freshly generated token — so you never have to remember the
flags:

```
:: swarm 'demo' is up with 3 agent(s)
:: attach with:  tmux attach -t demo-orchestrator
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c /path/agentainer.yaml --token 1a2b… --port 8000
```

That hint shows a **`--host 0.0.0.0` + `--token`** example on purpose: it
demonstrates that any non-loopback bind *must* carry a token. If you only need
local access, drop `--host`/`--port` and run the safe loopback default:

```bash
agentainer serve -c /path/agentainer.yaml
```

Only bind `0.0.0.0` if you genuinely intend to expose the control plane on your
network — and read the security section before you do.

---

## Features

Everything below is served by `lib/ui.py` and driven by the single-page app.

### Live observability dashboard

`GET /api/status` powers the **Agents** view. For the swarm it reports the name,
root, whether the `user` mailbox is available, and whether the liveness
supervisor is alive. For each agent it reports:

- **`running`** — is its tmux session up?
- **`busy`** — is a turn in flight right now? (from `turn.busy_info`)
- **`queue_depth`** — messages waiting to be released into its inbox
- **`unread`** — messages currently sitting in its inbox
- **`type`** and **`can_talk_to`** — its CLI kind and ACL

`GET /api/agent?agent=<name>` adds the full detail (command, role, workdir,
capture mode, tmux session name, and the periodic-ping settings). `GET /api/logs`
tails the durable JSONL event log (per-agent or the whole swarm).

### Mail threads

The mail app reconstructs conversations from the file mailroom:

- `GET /api/contacts?agent=<name>` — the agent's contact list (each peer's
  unread count, message count, and a preview of the last message).
- `GET /api/thread?agent=<name>&peer=<name>` — the full **bidirectional** thread
  between two mailboxes, time-sorted and deduped by message id, with each
  message's delivery status (`queued` → `delivered` → `read` → `archived`).
- `GET /api/inbox?agent=` and `GET /api/queue?agent=` — the raw current inbox and
  the waiting queue.

This is read-only introspection of the exact files agents read and write; the
orchestrator remains the source of truth for state.

### Terminal snapshot

`GET /api/pane?agent=<name>` returns a **snapshot of the agent's live tmux
pane** (`tmux.capture_pane`). The Agents view has a *terminal* tab that renders
it, so you can see what the CLI is showing right now without `tmux attach`. When
the session is down the snapshot is simply empty (the request still succeeds).

This is a snapshot, not a stream — the page re-fetches on a timer. Remember that
fullscreen TUIs keep no scrollback, so the durable JSONL log (`/api/logs`) is
where history lives, not the pane.

### Send-from-UI

Two distinct write paths, deliberately separate:

- **Mail (recommended).** `POST /api/send` with `{"to","text"}` calls
  `mail.send_as_user` — it delivers as the virtual **`user`** mailbox and goes
  through the *normal* mailroom (routing, queueing, nudge). This is what the
  reply box in a thread does. Correctness is unchanged from a CLI `user send`.
- **Direct pane input (raw).** `POST /api/type` with `{"agent","text"}` pastes
  straight into the agent's tmux pane (`tmux.paste_into`), **bypassing the
  mailroom entirely**. `POST /api/key` with `{"agent","key"}` sends a single
  key (`Escape`, `C-c`, …). These are power-user escape hatches — use them to
  unstick a modal or interrupt a runaway turn, not for normal messaging.

### The user mailbox + availability toggle

The swarm's human is modeled as a virtual `user` mailbox. The UI header has an
availability switch ("Receive mail from agents"):

- `GET /api/availability` → the current toggle state.
- `POST /api/availability` with `{"available": <bool>}` flips it and persists to
  `agentainer.yaml` (via `reconcile.edit_swarm` + `mail.set_user_available`).

When **available**, agents may deliver mail to you; when **away**, the
orchestrator holds it. Mail addressed to `user` accumulates in the `user` queue,
which the mail app renders as a thread you can read and reply to.

### Dynamic reconcile (add / remove / edit agents)

The **Settings** view is a live config editor. Every mutation **rewrites
`agentainer.yaml`** through `lib/reconcile`'s stdlib YAML emitter (so the
no-PyYAML path keeps working) and swaps the server's in-memory config so the
next request sees the change:

- `POST /api/agent/add` — add an agent (`name`, `type`, `command`,
  `can_talk_to`, optional `role`/`workdir`/`capture`/ping settings) and
  initialize its mailboxes.
- `POST /api/agent/edit` — change an existing agent's fields.
- `POST /api/agent/remove` — stop its tmux session (so it isn't orphaned), then
  drop it from the config.
- `POST /api/up` / `POST /api/down` — start or stop a single agent's session
  (`reconcile.start_one` / `reconcile.stop_one`) without editing the config.
- `GET`/`POST /api/config` — read the raw settings/agents, or persist swarm-level
  settings (`reconcile.edit_swarm`).

Removing an agent that a peer still lists in `can_talk_to` would leave the config
invalid; the server surfaces that as a `400` rather than writing a broken file.

### Telegram bridge (optional)

If a Telegram bot is configured, `GET/POST /api/telegram`,
`/api/telegram/test`, and `/api/telegram/poll` expose the mirror + reply poller.
The status endpoint reports `has_token` as a **boolean only — the raw bot token
is never returned**.

---

## Security posture

**The UI is a control plane and must be treated as privileged access.** It can
start processes, type into agents that may run with permission checks disabled,
and rewrite your config. Read this before exposing it anywhere.

### Bind loopback by default

`agentainer serve` binds **`127.0.0.1`** by default. The hosts treated as
loopback are `127.0.0.1`, `localhost`, `::1`, and `0:0:0:0:0:0:0:1`.

**Never bind `0.0.0.0` (or any routable interface) without a token.** This is a
hard invariant enforced in code: `ui.run_server` raises `ValueError` if you ask
for a non-loopback host with an empty token. There is no way to expose the
control plane on the network unauthenticated.

### Token auth

- Static assets (`/`, `/index.html`, `/app.js`) are token-exempt so the login
  page can load. **Every** API call and **every** POST requires the token.
- Supply it as `?token=<secret>` on the query string or as an
  `Authorization: Bearer <secret>` header. The single-page app appends
  `?token=` to each request after you Connect.
- A missing or wrong token gets a `401`.
- The token is a random 32-hex-char secret by default. **Never print, paste,
  log, screenshot, or commit it.** Set `--token`/`$AGENTAINER_UI_TOKEN` for a
  stable value across restarts, but keep that secret out of shell history and
  version control.

### Prefer a tunnel over a public bind

If you need the UI from another machine, **do not** bind `0.0.0.0` to the open
internet even with a token. Instead, keep the safe loopback bind and reach it
over an authenticated tunnel:

- **SSH port-forward:** `ssh -L 4141:127.0.0.1:4141 you@host`, then open
  `http://127.0.0.1:4141` locally.
- **Tailscale / WireGuard:** bind loopback (or the tailnet interface with a
  token) and reach it over the private mesh.

See [remote access](use-cases/remote-access.md) for a full walkthrough of
tunneling the control plane safely.

---

## Headless-first

The UI is optional. Everything it does has a CLI equivalent, and swarms run
perfectly with no server at all:

| UI action                     | CLI equivalent |
|-------------------------------|----------------|
| Watch agent status            | `agentainer status` |
| Read an agent's inbox         | `agentainer inbox <agent>` |
| See queued mail               | `agentainer queue <agent>` |
| Tail the event log            | `agentainer logs [<agent>] -f` |
| Send mail as `user`           | `agentainer user send --to <agent> …` / `agentainer send --to …` |
| Toggle availability           | `agentainer user available` / `agentainer user away` |
| Watch a terminal              | `agentainer attach <agent>` |
| Add / remove / edit an agent  | `agentainer add …` / `remove …` / `edit …` |
| Start / stop one agent        | `agentainer reconcile` (or `up --only` / `down --only`) |

Run the swarm headless on a server; start `serve` only when you want a window
into it.

---

## A typical session (no screenshots)

1. **Bring the swarm up.**
   ```bash
   ./agentainer up -c agentainer.yaml
   ```
   Note the `serve` hint it prints at the end — it already contains a token.

2. **Start the UI on loopback.**
   ```bash
   ./agentainer serve -c agentainer.yaml --port 4141
   ```
   It prints `:: UI serving at http://127.0.0.1:4141` and `:: UI token: …`.

3. **Connect.** Open `http://127.0.0.1:4141`, paste the token into **Connect**.
   (If the machine is remote, first: `ssh -L 4141:127.0.0.1:4141 you@host`.)

4. **Watch the agents.** The **Agents** view shows each agent's running/busy
   state, queue depth, and unread count, refreshing on a timer. Click an agent
   to read its mail threads or switch to the **terminal** tab for a live pane
   snapshot.

5. **Send a message.** Open a thread with an agent and type a reply — it's
   delivered as the `user` mailbox through the normal mailroom (routing + nudge).

6. **Toggle your availability.** Flip the header switch to **away** when you
   don't want agents mailing you; flip it back to **available** to resume. The
   change persists to `agentainer.yaml`.

7. **(Optional) reshape the swarm.** In **Settings**, add, edit, or remove an
   agent; the change rewrites the config and reconciles the running sessions.

8. **Stop the server** with `Ctrl-C`. The swarm keeps running headless; bring it
   down with `agentainer down` when you're finished.

---

## See also

- [Getting started](getting-started.md) — bring up your first swarm.
- [CLI reference](cli-reference.md) — every headless command.
- [Remote access](use-cases/remote-access.md) — tunneling the control plane.

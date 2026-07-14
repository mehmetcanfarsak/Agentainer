# The Telegram Bridge (`telegram:` config block)

A guide to mirroring your Agentainer swarm's mail out to a Telegram chat, routing
replies from your phone back into the swarm as `user` mail, and **driving the whole
swarm from Telegram** with the same commands you'd use in the CLI or web UI.

---

## 1. What it does

The Telegram bridge is an **optional, zero-dependency** integration (lives in
`lib/telegram.py`). When you add a `telegram:` block to `agentainer.yaml` with a
bot token and a chat id, it does three things:

1. **Mirror out** — whenever the orchestrator *delivers* a message (enqueues it
   into an agent's `user` mailbox or another agent's queue), a copy is pushed to
   your Telegram chat. A human watching their phone sees the swarm's mail traffic
   live. Which agents are mirrored is configurable; mail addressed to the `user`
   mailbox is mirrored by default so you stay reachable even while "away".

2. **Route replies in** — a background long-poll loop reads Telegram updates. When
   you *reply* (a Telegram message reply) to a mirrored piece of `user` mail, that
   reply is routed back into the swarm as `user` mail to the original sender.

3. **Full control plane** — slash commands give Telegram **complete parity with the
   CLI and the web UI**: start/stop/restart agents, read status/inbox/queue/pane/logs,
   send mail, type into live sessions, and add/edit/remove agents or edit swarm
   settings — all from your phone. See [§6a](#6a-full-command-surface--telegram--cli--ui)
   for the full table, or send `/help` in the chat.

It is **off by default**. Nothing touches the Telegram API unless you explicitly
set `enabled: true` with valid credentials.

### Hard invariants (why it can't hurt your swarm)

- **Zero runtime dependencies.** The bridge uses only the stdlib `urllib` — no
  `requests`, no Telegram SDK. Every network call goes through one `_urlopen`
  seam so tests can mock it with no sockets.
- **Correctness never depends on the network.** Mirroring is *best-effort*.
  The mail is already durably queued *before* the bridge ever touches the network,
  and the mirror call swallows all errors — a down or slow Telegram can never
  wedge, delay, or fail the mailroom. A failure is logged (`telegram-error`
  event) and dropped.
- **No secrets are logged or leaked.** The bot token lives only in your config and
  the locally-built request URL. The UI never returns the raw token (it reports
  `has_token: true/false` instead).

---

## 2. Enabling it

Add a top-level `telegram:` block to `agentainer.yaml`:

```yaml
telegram:
  enabled: true
  bot_token: "<your-telegram-bot-token>"
  chat_id: "<your-chat-id>"
```

Only three bare-minimum fields are required to turn it on: `enabled`,
`bot_token`, and `chat_id`. The bridge is considered "enabled" by the code only
when **all three** are present and non-empty (`lib/telegram.py` `is_enabled()`).

> **Never commit a real `bot_token`.** Treat it like an API key / password. It is
> a secret. Keep it out of version control; the repo's `.gitignore`/`.npmignore`
> guards exclude runtime state but do **not** scrub secrets from `agentainer.yaml`
> for you. Prefer per-machine configs that are not committed.

### Getting a bot token (BotFather)

1. Open Telegram and message **@BotFather**.
2. Send `/newbot` and follow the prompts (pick a name and a `username` ending in
   `bot`, e.g. `MyAgentainerBot`).
3. BotFather replies with a line like:
   ```
   Use this token to access the HTTP API:
   123456789:ABCdefGHIjklMNOpqrsTUVwxyz0123456789
   ```
   That whole string is your `bot_token`. Copy it into the config as
   `<your-telegram-bot-token>` above.

### Getting your chat id

The bridge only accepts updates from one chat — the `chat_id` you configure — so
a shared bot can't be driven by strangers. To find your own chat id:

1. Start a conversation with your new bot (send it any message, e.g. `hi`).
2. From a shell, hit the Bot API `getUpdates` method with your token:

   ```bash
   curl -s "https://api.telegram.org/bot<your-telegram-bot-token>/getUpdates"
   ```

3. The JSON contains an `update` with `"chat":{"id": 123456789, ...}`. That
   numeric id is your `chat_id`. (If you use a group, the `id` will be negative,
   e.g. `-1001234567890` — that's fine, use it verbatim.)

> Tip: bots can't see `getUpdates` results for messages sent *before* you call
> `getUpdates` the first time, and a pending update is consumed once. Send `hi`
> to the bot, then immediately call `getUpdates` once to grab the id, and you're
> done.

---

## 3. The `mirror` setting

`mirror` controls **which agents' mail** is pushed to Telegram. It accepts two
shapes:

- **`"*"`** — mirror *every* agent's mail (the default if `mirror` is omitted).
- **a list of agent names** — e.g. `[orchestrator, developer]` — mirror only
  those agents. `all` is accepted as a synonym for `"*"`.

Under the hood (`_should_mirror` in `lib/telegram.py`), a delivered message is
mirrored when **any** of these hold:

- it is addressed **to `user`** → gated by `mirror_user` (see §4),
- it is **from `system`** → gated by `mirror_system` (see §4),
- `mirror` is `"*"` (or contains `"*"`), **or**
- the sender **or** the recipient is named in your `mirror` list.

So with `mirror: [orchestrator, developer]`, a message *from* `orchestrator`
(resp. *to* it) is mirrored regardless of the other party; a message between two
agents neither of which is in the list is not.

```yaml
telegram:
  enabled: true
  bot_token: "<your-telegram-bot-token>"
  chat_id: "<your-chat-id>"
  mirror: "*"            # or: [orchestrator, developer]
```

---

## 4. `mirror_user` and `mirror_system`

These two booleans gate the two special virtual mailboxes.

### `mirror_user` (default: **true**)

Mail addressed **to the `user` mailbox** is mirrored whenever this is true. This
is the headline feature: when you're away from your computer, the `user`
mailbox is where incoming delegations, questions, and requests from the agents
land — and with mirroring on, they show up on your phone. Reply to one from
Telegram and it routes back into the swarm as `user` mail (see §5).

Leave this at its default (`true`) — it's the whole point of the bridge for a
human who steps away. Set it to `false` only if you want the bridge live for
agent-to-agent traffic but don't want your own inbox duplicated to your phone.

### `mirror_system` (default: **false**)

Mail **from the `system` sender** — errors, pings, nudges, operational noise —
is mirrored only when this is true. It defaults to `false` deliberately: this
traffic is not "mail you need to act on," and pushing it to a phone quickly
becomes a spam flood of heartbeats and nudges.

> **Recommendation:** keep `mirror_system: false` unless you are actively
> debugging the orchestrator and want to watch its chatter from your phone.

```yaml
telegram:
  enabled: true
  bot_token: "<your-telegram-bot-token>"
  chat_id: "<your-chat-id>"
  mirror: "*"
  mirror_user: true       # default; see your own incoming mail on your phone
  mirror_system: false    # default; don't flood your phone with operational noise
```

---

## 5. Typical mobile workflow

The bridge is built for the "I'm away from the keyboard" case:

1. **Swarm runs** (`agentainer up`). Agents exchange mail; some of it is
   addressed to `user` because a decision or input is needed from you.
2. **You get a notification on your phone.** Each mirrored `user` message arrives
   as a Telegram card like:

   ```
   🧑 user → user
   (reply to this message to answer as the user)

   Hey, I've finished the refactor but I'm not sure if I should also update the
   README. What do you want me to do?
   ```

   > **Long messages.** The inline card shows the first ~1200 characters. If the
   > body is longer, the card is *not* the whole story — Agentainer additionally
   > uploads the **full** body as a `.txt` attachment (sent as a reply to the
   > card), so nothing is silently dropped off your phone. Tap the attachment to
   > read the rest.

3. **When you're back at a computer**, you answer through the normal `user`/`send`
   path — either the CLI (`agentainer user send --to <agent> "..."`) or the web UI
   control plane. The agents never know you were unreachable in the meantime;
   the mail just waited in the `user` queue.
4. **(Optional) reply straight from Telegram.** If the UI server's reply poller is
   running (see §6), you can reply *to the mirrored message* from your phone and
   it routes back into the swarm as `user` mail to the original sender — no
   computer required. Agentainer confirms with a `✓ delivered to <agent>` message.

This keeps you looped in during the dead time between sessions without forcing
you to babysit a terminal.

---

## 6. Replying from Telegram (the reply poller)

Mirroring *out* is automatic — it happens on every delivered message as part of
the core mail path (`lib/mail.py` calls `telegram.on_enqueued`), with **no UI or
server required**.

Routing replies *in* is different: it needs the **long-poll Poller thread**
(`lib/telegram.py` `start_poller` / `Poller`), which lives inside the **web UI
control plane** (`lib/ui.py`), not `agentainer up`.

**"Receive replies" is ON by default.** You do **not** have to turn it on manually:

- When you start the UI server (`agentainer serve` / `ui`) and Telegram is fully
  configured, the poller starts **automatically** as the server begins serving.
- If you enable the bridge (or paste a token/chat id) from the UI while the server
  is already running, the poller starts **immediately** — no separate toggle.
- Editing the `telegram:` block restarts the poller so it picks up new credentials;
  disabling the bridge stops it.
- The UI's "Stop replies" button (and `POST /api/telegram/poll {"run": false}`)
  still lets you turn it **off** any time; `/api/telegram/poll {"run": true}`
  turns it back on.

The `enabled`/`chat_id`/`bot_token` checks still apply — the poller never starts
unless the bridge is fully configured. So the only requirement to reply from your
phone is that **a UI server is running** (`agentainer serve`); everything else is
automatic.

Two inbound modes are supported (`_process_update`):

- **Reply to a mirrored `user` message** → routed back as `user` mail to the
  original sending agent (`mail.send_as_user`).
- **`/to <agent> <text>`** → sends `user` mail to `<agent>` directly. Unknown
  agent names get a usage hint back.

Anything else — a plain message that isn't a reply (or a reply whose original
message is no longer in the bridge's reply-map) — has no recipient to route to, so
Agentainer sends back an **acknowledgement** (`ℹ️ received, but not routed
anywhere…`) telling you it saw the message and how to actually route one. This
way a stray message never leaves you wondering whether the swarm is quietly
working on it.

All inbound modes only accept messages originating from your configured
`chat_id`; anything from another chat is silently ignored.

> You can verify the bridge end-to-end from the UI's test action (sends
> "✅ Agentainer test message" to your chat via `send_message`), and inspect
> `enabled` / `has_token` / `polling` state without ever exposing the token.

---

## 6a. Full command surface — Telegram = CLI = UI

The bridge is a **complete control plane**, not just a notifier. Per Agentainer's
parity rule (`CLAUDE.md` principle #7), **anything you can do from the web UI or by
editing `agentainer.yaml`, you can do from Telegram** — because every command is a
thin adapter over the same tested `lib/` core the CLI and UI call. Send `/help` in
the chat for the live list.

> ⚠️ **This is a control plane with the same power as the UI.** From your phone you
> can start/stop agents, type straight into sessions (including ones running
> `--yolo` / `--dangerously-skip-permissions`), and rewrite the config. Only the
> configured `chat_id` is accepted — keep that chat (and your bot token) private.

| Command | Does | Backed by |
|---|---|---|
| `/status` | swarm overview (up/down, busy, queue depth, your availability) | `tmux` + `turn` + `mail` |
| `/agents` | list agents + their `can_talk_to` | `cfg.agents` |
| `/up [agent]` | start all / one | `reconcile.start_all` / `start_one` |
| `/down [agent]` | stop all / one | `reconcile.stop_all` / `stop_one` |
| `/restart [agent]` | restart all / one | `reconcile.stop_* + start_*` |
| `/reconcile` | make the running set match the config | `reconcile.reconcile` |
| `/to <agent> <msg>` | send mail **as the user** | `mail.send_as_user` |
| *(reply to a mirrored msg)* | answer its sender as the user | `telegram._route_user_reply` |
| `/available` · `/away` | toggle your availability | `reconcile.edit_swarm` + `mail.set_user_available` |
| `/inbox <agent>` | current inbox messages | `cfg.mail_paths().inbox` |
| `/queue <agent>` | queued (not-yet-released) mail | `mail.queued_files` |
| `/pane <agent>` | terminal snapshot of the live pane | `tmux.capture_pane` |
| `/logs [agent] [n]` | recent event-log lines | `.agentainer/logs/*.jsonl` |
| `/config` | swarm + telegram settings summary | `cfg` |
| `/type <agent> <text>` | type text straight into the pane | `tmux.paste_into` |
| `/key <agent> <Key>` | send one control key (`Enter`, `Escape`, `C-c`, …) | `tmux.send_key` |
| `/compact [agent]` | `/compact` one or all running agents | `tmux.paste_into` |
| `/idle <agent>` | force an agent idle + drain queued mail | `turn` + `mail` |
| `/add <name> <type> <command…>` | add an agent (talks to `user`; refine with `/edit`) | `reconcile.add_agent` |
| `/edit <agent> <key>=<value> …` | edit an agent's fields | `reconcile.edit_agent` |
| `/remove <agent>` | remove an agent + stop its session | `reconcile.remove_agent` |
| `/set <key>=<value> …` | swarm-level settings | `reconcile.edit_swarm` |
| `/mirror <*\|a,b,…>` | change the mirror scope | `reconcile.edit_telegram` |
| `/templates` | list bundled starter templates | `examples/` |
| `/apply <template>` | seed an **empty** swarm from a template | `reconcile.apply_template` |

**Argument notes.** A command word may carry a `@botname` suffix (Telegram adds it
in groups) and is case-insensitive. For `/edit` and `/set`, a **single** `key=value`
keeps spaces in the value (e.g. `/edit dev role=lead backend dev`), while **multiple**
pairs are split on whitespace (`/set ready_timeout_ms=5000 resume=false`). Bad usage
or an unknown agent comes back as a `⚠️` message so you can self-correct in-chat;
long replies are automatically split under Telegram's message-size limit.

---

## 7. Privacy & security notes

- **Mail content leaves your machine.** Mirrored messages can contain code, logs,
  file contents, and anything the agents put in their mail — including secrets
  that live in a workspace. By mirroring, you are copying that content to
  Telegram's servers and to whatever device holds your chat. Only enable it for
  swarms you trust to expose that way.
- **`bot_token` is a secret.** Anyone with it can send messages *as your bot* and
  read your bot's inbound updates. Never print it, never commit it, rotate it via
  BotFather if you suspect it leaked. The code deliberately avoids logging it and
  the UI deliberately avoids returning it.
- **`chat_id` restricts who can drive the swarm.** Inbound replies are only
  honored from the configured chat, so a stranger can't steer your agents through
  the bot. Keep `chat_id` to *your* chat.
- **Leave `mirror_system` off** unless you want operational noise (pings, nudges,
  error events) landing on your phone. It's `false` by default for that reason.
- **The UI is a control plane** (it can start processes, edit config, type into
  agents). It binds `127.0.0.1` by default and requires a token for any remote
  bind — keep those guards in place, since the Telegram poller runs inside it.

---

## 8. Minimal valid `telegram:` snippet

```yaml
# agentainer.yaml  (top-level key, alongside `swarm:` and `agents:`)
telegram:
  enabled: true
  bot_token: "<your-telegram-bot-token>"   # from @BotFather — keep secret, don't commit
  chat_id: "<your-chat-id>"                # numeric id from getUpdates
  mirror: "*"                              # "*" = all agents; or a list: [orchestrator, developer]
  mirror_user: true                        # see your own incoming `user` mail on your phone
  mirror_system: false                     # leave off — avoid operational noise on your phone
```

With just that block, every delivered message is mirrored to your chat (including
your `user` inbox), and — when the UI server's reply poller is running — you can
reply from Telegram and have it routed back as `user` mail.

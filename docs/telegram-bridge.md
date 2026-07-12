# The Telegram Bridge (`telegram:` config block)

A guide to mirroring your Agentainer swarm's mail out to a Telegram chat — and
routing replies from your phone back into the swarm as `user` mail.

---

## 1. What it does

The Telegram bridge is an **optional, zero-dependency** integration (lives in
`lib/telegram.py`). When you add a `telegram:` block to `agentainer.yaml` with a
bot token and a chat id, it does two things:

1. **Mirror out** — whenever the orchestrator *delivers* a message (enqueues it
   into an agent's `user` mailbox or another agent's queue), a copy is pushed to
   your Telegram chat. A human watching their phone sees the swarm's mail traffic
   live. Which agents are mirrored is configurable; mail addressed to the `user`
   mailbox is mirrored by default so you stay reachable even while "away".

2. **Route replies in** — a background long-poll loop reads Telegram updates. When
   you *reply* (a Telegram message reply) to a mirrored piece of `user` mail, that
   reply is routed back into the swarm as `user` mail to the original sender. A
   `/to <agent> <text>` command works too, for sending to any agent directly.

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
(`lib/telegram.py` `start_poller` / `Poller`). That thread is started from the
**web UI control plane** (`lib/ui.py`), not from `agentainer up`:

- The UI's `/api/telegram/poll` endpoint starts/stops the poller (a per-serve
  daemon thread, one per UI process).
- Editing the `telegram:` block via the UI also restarts the poller so it picks
  up new credentials.

So to actually reply from your phone, **the UI server must be running with
polling enabled.** Start the UI (`agentainer ui` / the `serve` command), open its
Telegram panel, and turn polling on. The `enabled`/`chat_id`/`bot_token` checks
still apply — polling refuses to start if the bridge isn't fully configured.

Two inbound modes are supported (`_process_update`):

- **Reply to a mirrored `user` message** → routed back as `user` mail to the
  original sending agent (`mail.send_as_user`).
- **`/to <agent> <text>`** → sends `user` mail to `<agent>` directly. Unknown
  agent names get a usage hint back.

Both only accept messages originating from your configured `chat_id`; anything
from another chat is silently ignored.

> You can verify the bridge end-to-end from the UI's test action (sends
> "✅ Agentainer test message" to your chat via `send_message`), and inspect
> `enabled` / `has_token` / `polling` state without ever exposing the token.

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

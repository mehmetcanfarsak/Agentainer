# Use case: reach your Agentainer UI from anywhere with Tailscale

You have a swarm running on an always-on machine — a home server, a work
box, a spare laptop in the closet — and you want to check on it from your phone
on the train, or from a different laptop at a café. This walk-through gets you
there **safely**, using a private mesh VPN (Tailscale) so the UI is never exposed
to the open internet.

If you just want the short version:

1. Run your swarm on the host: `./agentainer up -c agentainer.yaml`
2. Install Tailscale on the host and on your phone/laptop; sign both into the
   **same** account.
3. Serve the UI over the tailnet with a token:
   `./agentainer serve -c agentainer.yaml --host 0.0.0.0 --token <token> --port 8000`
4. On your phone, open `http://<host-tailscale-ip>:8000/` and paste the token.

The rest of this page explains *why* it's done this way and covers the
troubleshooting.

---

## 1. The goal

- The swarm lives on a machine that stays on (call it **the host**).
- You want to **observe and control** it — read threads, watch panes, send mail
  as the `user`, start/stop agents — from your **phone or a second laptop**,
  wherever you are.
- You want this to be **private**: nobody but you should be able to reach that
  control plane.

The Agentainer UI (`agentainer serve`) is exactly the tool for observing and
controlling a running swarm. The only question is how to reach it from off the
host without opening a hole to the world. Tailscale answers that.

---

## 2. Why not just `--host 0.0.0.0`?

Because **the UI is a control plane, not a dashboard.** Through it you can:

- send mail into any agent as the virtual `user`,
- **type directly into an agent's tmux pane** (`POST /api/type`) and send keys
  like `Escape` / `C-c` (`POST /api/key`),
- start and stop agents, add/remove them, and rewrite `agentainer.yaml`.

If your agents run with `--dangerously-skip-permissions` (Claude) or `--yolo`
(others), then *anyone who can reach the UI can make those agents run arbitrary
commands on the host.* Exposing that on a raw public interface would be handing
out a remote shell.

Agentainer enforces a hard rule about this (see `CLAUDE.md` §18 and the guard in
`lib/ui.py`):

> **The UI binds `127.0.0.1` (loopback) by default. A token is *required* for any
> non-loopback bind.**

Concretely, `run_server` refuses to start when you ask for a non-loopback host
without a token:

```python
if not _is_loopback(host) and not token:
    raise ValueError("a token is required to bind to a non-loopback host")
```

So there are three postures, from safest to most dangerous:

| Posture | Bind | Reachable from | Verdict |
|---|---|---|---|
| Loopback only (default) | `127.0.0.1` | the host itself | ✅ safe, but local-only |
| **Behind Tailscale + token** | `0.0.0.0`, token required | your private tailnet | ✅ **recommended** |
| Raw public | `0.0.0.0` on a public IP | the entire internet | ❌ never do this |

The middle row is the sweet spot. Binding `0.0.0.0` *sounds* scary, but here it's
safe **because Tailscale is doing the perimeter**: the host has no public
listener that strangers can reach — only devices in your own encrypted tailnet
can connect, and even they still need the token. You get remote access without
the raw public exposure.

> **Rule of thumb:** never put the Agentainer UI on a public IP. Put it on a
> private network (Tailscale, or an SSH tunnel — see §5) and always require a
> token off loopback.

---

## 3. Step-by-step with Tailscale

[Tailscale](https://tailscale.com) is a mesh VPN built on WireGuard. Every device
you enroll gets a stable private address in the `100.64.0.0/10` range (a
`100.x.y.z` IP) plus a friendly `*.ts.net` hostname, and they can all reach each
other over an encrypted link — no port-forwarding, no public exposure.

### 3.1 Install Tailscale on the host

On Linux (most home servers):

```bash
curl -fsSL https://tailscale.com/install.sh | sh
```

On macOS use the App Store app or `brew install --cask tailscale`; on Windows use
the installer from tailscale.com/download. The install script auto-detects your
distro and installs the right package.

Then bring the host onto your tailnet:

```bash
sudo tailscale up
```

This prints a login URL. Open it in any browser, sign in (Google, GitHub,
Microsoft, email — your choice), and the host joins your private network.

Find the host's tailnet address:

```bash
tailscale ip -4        # -> e.g. 100.101.102.103
tailscale status       # shows this device's *.ts.net name and all peers
```

Note the `100.x.y.z` IP (and/or the `*.ts.net` hostname like
`my-host.tailnet-1234.ts.net`). That's what you'll point your phone at. This
address is **stable** — it survives reboots and network changes, which is exactly
why it beats a home IP that your ISP rotates.

> **Optional — enable MagicDNS** in the Tailscale admin console so you can use the
> short `*.ts.net` name instead of memorizing the numeric IP.

### 3.2 Install Tailscale on your phone (or second laptop)

1. Install the **Tailscale** app from the App Store / Google Play (or the desktop
   app on a laptop).
2. Sign in with the **same account** you used on the host.
3. That's it — the host now shows up in the app's device list with its
   `100.x.y.z` address. Your phone and the host can now talk directly over the
   encrypted tailnet, from anywhere with internet.

### 3.3 Bring the swarm up on the host

From the repo on the host:

```bash
./agentainer up -c /path/to/agentainer.yaml
```

When `up` finishes it prints an attach hint and a ready-to-paste **serve hint**,
including a freshly generated token, e.g.:

```
:: swarm 'my-swarm' is up with 3 agent(s)
:: attach with:  tmux attach -t my-swarm-orchestrator
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c /path/to/agentainer.yaml --token 9f2c1e7a4b8d6f0e3a5c9b1d7e2f4a60 --port 8000
```

That token is a random 32-hex-char secret (`secrets.token_hex(16)`). Copy it —
you'll paste it into the browser on your phone. (Treat it like a password; don't
paste it into chats, screenshots, or commits.)

### 3.4 Serve the UI over the tailnet

Run the serve command on the host. You can paste the hint verbatim, or supply
your own token:

```bash
./agentainer serve \
  -c /path/to/agentainer.yaml \
  --host 0.0.0.0 \
  --token 9f2c1e7a4b8d6f0e3a5c9b1d7e2f4a60 \
  --port 8000
```

Flags (from `cmd_serve` in `lib/cli.py`):

- `--host 0.0.0.0` — listen on all interfaces so the tailnet address is
  reachable. Safe here *only because* Tailscale is the perimeter and a token is
  set. Without a token this exact command **refuses to start**.
- `--token <token>` — the auth secret. If omitted, Agentainer falls back to
  `$AGENTAINER_UI_TOKEN`, then generates a random one and prints it to stderr.
  For a non-loopback bind you must have one either way.
- `--port 8000` — a fixed port so the URL is predictable. (Omit it and the OS
  picks a free port, which is fine for loopback but inconvenient for remote
  access since the port would change each run.)

On start it prints where it's serving and the token in use:

```
:: UI serving at http://0.0.0.0:8000
:: UI token: 9f2c1e7a4b8d6f0e3a5c9b1d7e2f4a60
```

`serve` runs in the foreground and blocks until you press `Ctrl-C`. To keep it
alive after you log out of the host, run it under `tmux`, `nohup`, or a systemd
unit:

```bash
# quick-and-dirty: keep it running in its own tmux session
tmux new -d -s agentainer-ui \
  "./agentainer serve -c /path/to/agentainer.yaml --host 0.0.0.0 --token <token> --port 8000"
```

> **Tip — set the token via the environment** so it never appears in your shell
> history or process list arguments:
>
> ```bash
> export AGENTAINER_UI_TOKEN=9f2c1e7a4b8d6f0e3a5c9b1d7e2f4a60
> ./agentainer serve -c /path/to/agentainer.yaml --host 0.0.0.0 --port 8000
> ```

### 3.5 Open it from your phone

On your phone (with Tailscale connected), open a browser and go to:

```
http://100.101.102.103:8000/
```

…using **your** host's `100.x.y.z` from §3.1. If you enabled MagicDNS, the
hostname works too:

```
http://my-host.tailnet-1234.ts.net:8000/
```

The page loads (static assets are token-exempt so the login screen can appear),
then prompts for the token. Paste the token from §3.3/§3.4 and you're in — the
full mail-app UI: contacts, threads, live pane snapshots, send-as-user, and
agent controls, all from your phone, from anywhere.

> Note it's `http://`, not `https://`. That's fine here: Tailscale already
> encrypts every byte between your phone and the host with WireGuard, so the
> traffic is protected end-to-end even though the app itself speaks plain HTTP.

---

## 4. What you can do once you're in

- **Read every thread** between agents, and between agents and the `user`.
- **Watch an agent's terminal** via the live pane snapshot.
- **Send mail as the `user`** to any agent (`POST /api/send`).
- **Type into a pane** or send control keys (`Escape`, `C-c`) when an agent needs
  a nudge.
- **Start / stop** individual agents, and edit the config.
- Toggle your `user` availability, tail the logs, inspect queues.

All of this is the same control plane you'd have sitting at the host — which is
exactly why the token and the private network matter.

---

## 5. Alternative: SSH reverse tunnel (no VPN)

If you don't want to run a VPN, and you can SSH **from the host to the client**,
an SSH reverse tunnel is a solid alternative. It forwards the host's UI port to a
loopback port on your laptop.

On the host, keep the UI on **loopback** (the safe default — no token strictly
required, because nothing off-host can reach it):

```bash
# on the host — loopback bind, fixed port
./agentainer serve -c /path/to/agentainer.yaml --host 127.0.0.1 --port 8000
```

Then open a reverse tunnel from the host back to your laptop:

```bash
# on the host: expose host:8000 as laptop:4141 (loopback on the laptop)
ssh -N -R 4141:127.0.0.1:8000 user@laptop
```

Now on the laptop, browse to:

```
http://127.0.0.1:4141
```

Because the tunnel terminates on the laptop's **loopback**, the UI is only
reachable from that laptop, and the traffic rides SSH's encryption. A token isn't
strictly required in this configuration — but **setting one anyway is still
recommended** (defense in depth; it costs nothing):

```bash
./agentainer serve -c /path/to/agentainer.yaml --host 127.0.0.1 --port 8000 --token <token>
```

The more common direction is a **forward** tunnel, if you can SSH *from* your
laptop *to* the host:

```bash
# on the laptop: forward laptop:4141 -> host's 127.0.0.1:8000
ssh -N -L 4141:127.0.0.1:8000 user@host
# then open http://127.0.0.1:4141 on the laptop
```

Either way, the UI stays bound to loopback on the host and is only reachable
through the encrypted SSH session. Phones make SSH tunnels awkward, though — for
mobile access, Tailscale (§3) is the friendlier path.

---

## 6. Security checklist

- ✅ **Always set a token off loopback.** Any bind that isn't `127.0.0.1` /
  `localhost` / `::1` requires `--token`; Agentainer refuses to start otherwise.
- ✅ **Prefer a private network over the public internet.** Tailscale (or an SSH
  tunnel) instead of a raw `0.0.0.0` on a public IP. `0.0.0.0` is acceptable
  *only* when a private mesh is the perimeter.
- ✅ **Never expose the UI on a public IP.** It can type into
  `--dangerously-skip-permissions` / `--yolo` agents — that's remote code
  execution if it leaks.
- 🔒 **Keep agent credentials on the host.** Your `chy3` alias, API keys, and any
  secrets embedded in agent `command` strings stay on the host. Your phone only
  ever holds the UI token — never the model keys.
- 🔑 **Treat the UI token like a password.** Don't commit it, screenshot it, or
  paste it into a chat. Prefer `AGENTAINER_UI_TOKEN` over passing it on the
  command line. Rotate it by restarting `serve` with a new one.
- 🧹 **Shut it down when you're done.** `Ctrl-C` (or kill the tmux/systemd unit)
  stops the UI; `./agentainer down -c agentainer.yaml` stops the whole swarm.
- 🛡️ **Remember the ACL is cooperative, not isolation.** `can_talk_to` restrains
  well-behaved agents; it's not an OS security boundary. The real perimeter for
  remote access is the tunnel + token.

---

## 7. Troubleshooting

**Can't load the page at all.**

- Confirm Tailscale is connected on **both** ends: `tailscale status` on the
  host, and the app shows "Connected" on the phone. If either is down, the
  `100.x.y.z` address won't route.
- Double-check the host IP: `tailscale ip -4` on the host. Use that exact
  address (or the `*.ts.net` name if MagicDNS is on).
- Make sure you included the port: `http://100.x.y.z:8000/`, not just the IP.

**Page loads but every action says "unauthorized" (HTTP 401).**

- The token is wrong or missing. Copy the exact token printed by `serve`
  (`:: UI token: …`) and paste it into the login field. Tokens are
  case-sensitive 32-hex-char strings.
- If you restarted `serve` without `--token` / `AGENTAINER_UI_TOKEN`, it
  generated a **new** token — check the latest startup output.

**`serve` won't start:** `ValueError: a token is required to bind to a
non-loopback host`.

- You asked for `--host 0.0.0.0` (or any non-loopback host) with no token. Add
  `--token <token>` or set `AGENTAINER_UI_TOKEN`. This is the §18 invariant doing
  its job.

**Connection refused / times out.**

- Is `serve` still running on the host? It runs in the foreground; if your SSH
  session closed it, it died. Run it under `tmux`/`nohup`/systemd (see §3.4).
- Check the host's local firewall isn't blocking the port. On Linux with `ufw`:
  `sudo ufw allow 8000` (or, better, scope it to the tailscale interface only,
  e.g. `sudo ufw allow in on tailscale0 to any port 8000`).
- Confirm what's listening: `ss -tlnp | grep 8000` should show the serve process
  bound to `0.0.0.0:8000`.

**Is the swarm even up?**

- On the host: `./agentainer status -c /path/to/agentainer.yaml` lists each agent
  (`up`/`down`, busy/idle, queue depth, unread) and whether the supervisor is
  alive. If agents are down, `up` them first.
- `ps aux | grep 'cli.py serve'` (or `tmux ls`) confirms the UI process itself is
  still running.

**MagicDNS name doesn't resolve.**

- Fall back to the numeric `100.x.y.z` IP. If you want the name, enable MagicDNS
  in the Tailscale admin console and reconnect the client.

---

## See also

- `docs/getting-started.md` — bring a swarm up from scratch.
- `docs/cli-reference.md` — every subcommand and flag (`up`, `serve`, `down`,
  `status`, …).
- `CLAUDE.md` §18 — the UI-as-control-plane invariant in full.
</content>
</invoke>

# Use case: the design-system builder

A concrete, end-to-end walkthrough of the shipped `examples/design-system.yaml`
swarm — a **lead** hub breaks a brand/component brief into a plan, a **tokens**
agent defines the color/type/spacing foundation, a **components** agent implements
the accessible components on top of those tokens, and a **docs** agent writes the
usage docs and stories. The lead ships the finished system back to the human. It's
the canonical "human brief → foundation → build → document → deliver" loop, wired
entirely through Agentainer's file-based mail model. No API keys are needed to
follow the mechanics; to run it for real you supply the coding-CLI commands (or
swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first.
> The one-line version: an agent **reads a file** to receive mail and **writes a
> file** to send it; the orchestrator owns routing, ACL, IDs, and state.

---

## 1. Who this is for

- **Designers** with a brand direction and a component list, who don't want to
  hand-author every token value, component, and doc page — you write the *brief*, the
  swarm produces the *system*.
- **Frontend developers** integrating the output: components are built against real,
  named tokens and a documented accessibility bar, so wiring them in means consuming a
  token by name and copying a documented story.
- **Design-system maintainers** who want a repeatable, auditable build — every token,
  component, and doc page traces back to the brief through the mail log.

The `lead` is a coordinator, not a taste-maker; the swarm removes mechanical work so the
human spends attention on direction and judgment.

---

## 2. The topology

```
        brief
  user ─────────▶ lead ─────────▶ tokens
           (delivery)  hub   (foundation)   │
                    ▲                        │ tokens
                    │                        ▼
                    │                   components ─────▶ docs
                    │                  (accessible impl) (usage + stories)
                    └──────────────────────┬───────────────┘
                                  (each reports its result up to lead)
```

Four agents, one directed flow, built in a **shared repo**: `user` sends a brief to
`lead`; `lead` delegates the foundation to `tokens` first; `tokens` hands the token set
to `components` and reports to `lead`; `components` builds the accessible components,
hands a summary to `docs`, and reports to `lead`; `docs` writes the docs/stories and
reports to `lead`; `lead` returns the vetted delivery to `user`. The routing is *enforced*
by each agent's `can_talk_to` list — anything else is bounced as a `system` message filed
in `failed/`. **Access-control list:**

| agent       | type   | can_talk_to                    | reaches `user`? |
|-------------|--------|--------------------------------|----------------|
| `lead`      | claude | tokens, components, docs, user | yes (the hub)   |
| `tokens`    | claude | lead, components               | no             |
| `components`| codex  | lead, tokens, docs             | no             |
| `docs`      | claude | lead, components               | no             |

Only `lead` lists `user`, so the human-facing surface is a single agent and the finished
system always flows through the lead's ship decision before it reaches you.

---

## 3. The config, explained

Here is `examples/design-system.yaml` (roles abridged for readability; the file is the
source of truth):

```yaml
swarm:
  name: design-system
  root: ./design-system-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: lead
    type: claude
    can_talk_to: [tokens, components, docs, user]
    command: "claude --dangerously-skip-permissions"
    role: "You are the DESIGN-SYSTEM LEAD and the hub... (see §2 flow)..."
  - name: tokens
    type: claude
    can_talk_to: [lead, components]
    workdir: "{root}/design-system"
    command: "claude --dangerously-skip-permissions"
    role: "You define the color/type/spacing foundation as named tokens..."
  - name: components
    type: codex
    can_talk_to: [lead, tokens, docs]
    workdir: "{root}/design-system"
    command: "codex --yolo"
    role: "Build the components on the lead's list, consuming ONLY tokens..."
  - name: docs
    type: claude
    can_talk_to: [lead, components]
    workdir: "{root}/design-system"
    command: "claude --dangerously-skip-permissions"
    role: "Write usage docs + stories for each finished component..."
```

Field by field:

### `swarm` / `defaults`
`name` is the swarm label (shows in `status`/logs/sessions); `root` is the parent for
the agents' workdirs and mailboxes (orchestrator state lands under
`design-system-workspace/.agentainer/`, never commit it). `defaults` sets
`capture: none` (the loader upgrades `claude`/`codex` back to their `hook`) and a safe
`can_talk_to: []` floor; every agent states its real list explicitly.

### The shared `workdir` (important)
`tokens`, `components`, and `docs` all set `workdir: "{root}/design-system"` — they
**share one checkout**, the design-system repo, so the token file (`tokens.json` + a
CSS-variables theme) is consumed by the components and the docs describe that same
code, all in one place. The `{root}` placeholder expands to `design-system-workspace`,
so the shared path is `design-system-workspace/design-system`. Because three agents
resolve to the same `workdir`, Agentainer **namespaces their mailbox folders
automatically** (`<name>-inbox/`, etc.) so they never collide — the build artifacts are
*meant* to share; only the mail is isolated. See
[`custom-workspace.md`](./custom-workspace.md) for pointing the three `workdir` lines at
your real repo.

- **`lead`** (`claude`, `can_talk_to: [tokens, components, docs, user]`) — the hub and
  the **only agent that can talk to `user`**. Turn detection: Stop hook.
- **`tokens`** (`claude`, `can_talk_to: [lead, components]`) — defines the foundation in
  the shared repo, hands it to `components`, reports only to `lead`. Stop hook.
- **`components`** (`codex`, `can_talk_to: [lead, tokens, docs]`) — consumes the tokens,
  builds the accessible components, hands a summary to `docs`, reports to `lead`. `notify`
  hook.
- **`docs`** (`claude`, `can_talk_to: [lead, components]`) — writes usage docs + stories
  in the shared repo, asks `components` when unclear, reports to `lead`. Stop hook.

All four `command` lines are placeholders (substitute your own launch string, e.g. a
shell alias; treat command strings as sensitive). The per-agent `role:` text in the file
is the standing identity delivered as the first prompt.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/design-system.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`): load + validate the config (printing the
`capture: none → hook` warning), create the runtime dirs, **initialize the mailboxes**
(the five folders per agent, **namespaced** for the three sharing `workdir`, plus an
`outbox/<peer>/` folder with its `about.md` contact card — the ACL made visible — for
each allowed recipient), **install per-type turn detection** (Claude Stop hook for
`lead`/`tokens`/`docs`, Codex `notify` hook for `components`), open one tmux session per
agent (the shared repo for tokens/components/docs), deliver the standby first prompt, and
**start the liveness supervisor** — the heartbeat that reconciles stale/dead/silent agents
so one stuck agent can't wedge the swarm. At the end, `up` prints attach and **`serve`**
hints, e.g.:

```
:: swarm 'design-system' is up with 4 agent(s)
:: attach with:  tmux attach -t <lead-session>
:: you can use the UI with:  agentainer serve -c examples/design-system.yaml --port 8000
```

> **UI is bound to `127.0.0.1` by default** (never `0.0.0.0`), per the control-plane
> security rule. Add `--host` + `--token` only if you intentionally expose it, and
> keep `--host 127.0.0.1` for local use. The headless CLI is fully functional without
> it.

---

## 5. Drive a brief

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. If you want to *receive* the lead's final delivery as mail (rather than have
it held), turn yourself available first (this flips `Status: available` in `lead`'s
`outbox/user/about.md`; while away, mail to you is *held* with a `system` ack — nothing
bounces):

```bash
./agentainer user available -c examples/design-system.yaml
```

Now send the brief into the swarm, addressed to the `lead`:

```bash
./agentainer send -c examples/design-system.yaml --to lead \
  "Build a design system for a fintech dashboard: calm, high-contrast, WCAG AA. Start with Button, Input, and Card."
```

The message is stamped `From: user` + a fresh id, enqueued for `lead`, and — because
`lead`'s inbox was empty — **released into `inbox/`** and `lead` is **nudged** (the
protocol, including its allowed-recipient list, is re-pasted).

### The build flowing

Watching the log (§6), the pipeline advances one turn at a time — each hop a
`stop → sweep → route → release → nudge` cycle: **lead** writes the spec into
`outbox/tokens/`; **tokens** writes `tokens.json` + CSS variables in the shared repo,
hands the token set to `outbox/components/`, and reports to `lead`; **components**
consumes the tokens by name and implements the accessible components (semantic markup,
ARIA, keyboard, focus, states), hands a build summary to `outbox/docs/` and reports to
`lead`; **docs** writes the usage docs + stories and reports to `lead`; **lead** confirms
consistency and writes the delivery summary into `outbox/user/`.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/design-system.yaml   # see research-swarm.md for the layout
```

**The durable event log** — the source of truth for history (tmux keeps no scrollback):

```bash
./agentainer logs -c examples/design-system.yaml            # whole swarm, last 20
./agentainer logs -c examples/design-system.yaml -f          # follow live
./agentainer logs components -c examples/design-system.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`, etc. —
one JSONL line per event. This is also your audit trail: which brief produced which
token, component, and doc page.

**A specific inbox / live pane** — what an agent is looking at, or watch its tmux session:

```bash
./agentainer inbox tokens -c examples/design-system.yaml        # prints the released msg or "inbox is empty"
./agentainer attach components -c examples/design-system.yaml   # detach with Ctrl-b d
```

Typing into a pane bypasses the mailroom — handy for un-sticking an agent; the mail
model is the normal path.

---

## 7. Customize & iterate

The swarm is a starting point — common forks:

- **Add an `a11y` reviewer.** Insert a fifth agent (type `claude`,
  `can_talk_to: [lead, components]`) that the `lead` routes each finished component to
  before it ships; its role verifies the real WCAG bar (contrast, keyboard, focus order,
  ARIA, labeled controls) and returns PASS or a `file:line` fix note. Add `a11y` to
  `lead`'s and `components`' `can_talk_to` so the `lead` becomes the ship gate requiring
  the a11y verdict.

- **Iterate the brief.** Because the three builders share one repo, send a fresh note
  to `lead` ("drop Card, add a Modal, make danger meet 4.5:1") and the work rolls
  forward through `tokens` → `components` → `docs`; if a component looks wrong, reply
  to `lead` naming it (the ACL keeps `user` talking only to `lead`) and it routes the
  fix to `tokens`/`components`. Inspect the result directly in the shared repo
  (`design-system-workspace/design-system/`). The `lead` won't deliver to `user` until
  tokens, components, and docs are all done and consistent.

- **Swap the models.** `type` is independent of the build role. Any of the four CLIs
  (`claude`, `codex`, `gemini`, `hermes`) can play any role — change `type` and its
  `command`, keeping `command` launching the *same* CLI as `type` (a mismatch wedges
  the agent; see §8). For example, run `tokens` on `gemini` (`command: "gemini --yolo"`,
  `capture: pane`) if you prefer its design voice.

- **Tune the ACL.** Let `docs` talk to `tokens` directly
  (`can_talk_to: [lead, components, tokens]`) to cite token definitions without a hop
  through components — but keep `lead` as the sole `user` contact.

- **Point at your real repo.** Set all three shared `workdir` lines to your actual
  design-system checkout (and `create_workdir: false`). See
  [`custom-workspace.md`](./custom-workspace.md).

- **Multi-LLM flavor.** Mixing `claude`, `codex`, and `gemini` is the
  [`multi-llm-swarm.md`](./multi-llm-swarm.md) pattern — useful here for a different
  model's "taste" for tokens vs. coding discipline for components.

---

## 8. Tips & footguns

- **Keep `lead` the only `user`-facing agent.** Only `lead` lists `user` in
  `can_talk_to`. That gives you one funnel and a clean ship decision; raw component work
  always passes through the lead before it reaches you. If `tokens` mailed `user`
  directly, the orchestrator bounces it (ACL) and drops a `system` note explaining who it
  *can* message — the model self-corrects in-band.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an agent
  stops, its outbox is swept, mail is routed, recipients are released and nudged. A
  `type`/`command` mismatch means completion never fires and the agent pins "busy"
  forever (`status` showing `busy` with `unread` mail is the tell). Nudges re-inject the
  protocol every time, and a message shown `AUTO_ARCHIVE_PRESENTATIONS` (5) times without
  being handled is auto-archived, so a forgetful model can't wedge the swarm.

- **The shared repo is shared on purpose — the mail isn't.** Because `workdir` is the
  same for three agents, the orchestrator namespaces their mailbox folders so they never
  collide. Don't "fix" that by giving each its own `workdir` unless you want the token
  file, the component code, and the docs to live in three different places.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime + mailboxes) and
  start every conversation fresh: `agentainer down -c examples/design-system.yaml` then
  `agentainer remove-session -c examples/design-system.yaml`. It refuses while any agent
  is still running — always `down` first. It never touches the shared repo's source files
  or your config.

---

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`mail-model.md`](../mail-model.md) — the four folders and how routing/ACL work.
- [`sessions-and-resume.md`](../sessions-and-resume.md) — conversations resume by default.
- [`use-cases/delegation-pipeline.md`](./delegation-pipeline.md) — the hub-and-spoke pattern this swarm uses.
- [`use-cases/multi-llm-swarm.md`](./multi-llm-swarm.md) — mixing agent CLIs in one swarm.
- [`use-cases/custom-workspace.md`](./custom-workspace.md) — pointing agents at a real repo.

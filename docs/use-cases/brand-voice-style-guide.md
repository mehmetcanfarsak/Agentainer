# Use case: the brand voice & style guide swarm

A concrete, end-to-end walkthrough of the shipped `examples/brand-voice-style-guide.yaml`
swarm — a four-agent team that turns a pile of a brand's real writing into a
finished **brand voice guide**, a practical **style guide**, and an approved-terms
**glossary**. A **sample_collector** hub curates the corpus and sequences the work;
a **voice_analyst** extracts the voice and tone from the samples; a **guide_writer**
turns those traits into rules; and a **glossary_builder** compiles the approved
terminology. It's the canonical "gather → analyze → codify" content-ops loop, wired
entirely through Agentainer's file-based mail model.

If you've ever searched for a **tone of voice examples** template, a **brand voice
guidelines** checklist, or "how to document our writing style," this is that,
running as a live multi-agent pipeline instead of a static doc.

Everything below is based on the actual contents of `examples/brand-voice-style-guide.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you supply the coding-CLI
commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The topology

```
              user
               │  samples in / guide out
               ▼
        sample_collector  (hub)
          /      |       \
         ▼       ▼        ▼
   voice_     guide_    glossary_
   analyst    writer    builder
      (each spoke reports only to the hub; collector <--> all)
```

Four agents, hub-and-spoke:

1. **`user` → `sample_collector`** — you hand over the brand's writing samples.
2. **`sample_collector` → `voice_analyst`** — the hub sends the curated corpus for
   voice/tone analysis first.
3. **`voice_analyst` → `sample_collector`** — the analyst reports the voice traits
   (grounded in quotes) back to the hub.
4. **`sample_collector` → `guide_writer`** — the hub briefs the writer with the
   traits + samples; the writer returns the style guide.
5. **`sample_collector` → `glossary_builder`** — the hub hands off the guide +
   samples; the builder returns the approved-terms glossary.
6. **`sample_collector` → `user`** — the hub assembles the package and delivers it.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. The three specialists list only `sample_collector`, so they
never cross-talk; anything off-list is bounced back as a `system` message and filed
in `failed/` (see §7).

---

## 2. The config, explained

See `examples/brand-voice-style-guide.yaml` in full in the repo. The shape:

```yaml
swarm:
  name: brand-voice-style-guide
  root: ./brand-voice-style-guide-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: sample_collector
    type: claude
    can_talk_to: [voice_analyst, guide_writer, glossary_builder, user]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the SAMPLE COLLECTOR -- the hub ... (curate corpus, sequence work,
      only point of contact for the user) ...
  - name: voice_analyst
    type: claude
    can_talk_to: [sample_collector]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the VOICE ANALYST. Identify voice/tone traits, grounded in quotes ...
  - name: guide_writer
    type: claude
    can_talk_to: [sample_collector]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the GUIDE WRITER. Turn the analysis into a practical style guide ...
  - name: glossary_builder
    type: claude
    can_talk_to: [sample_collector]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the GLOSSARY BUILDER. Compile the approved-terms glossary ...
```

Field by field:

### `swarm`
- **`name: brand-voice-style-guide`** — the swarm's name (shows up in `status`,
  logs, sessions).
- **`root: ./brand-voice-style-guide-workspace`** — the parent directory for the
  agents' working directories and mailboxes. Each agent gets
  `.../brand-voice-style-guide-workspace/<name>/` as its workdir (created on `up`),
  and its mailbox folders live alongside. Orchestrator state goes under
  `.../.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless the agent overrides them.
- **`capture: none`** — the default turn-detection mode. **But note:** `capture` is
  how Agentainer learns a turn finished, and it's ultimately keyed off each agent's
  `type`. For `claude`, whose CLI supports a completion **hook**, `capture: none` is
  a footgun — so the config loader *upgrades* it back to `hook` and prints a warning
  at `up`. Net effect: all four agents use their Stop hook. (Swap to mock bash loops
  for a key-free demo and `none` stays `none`.)
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent states
  its own list explicitly, so this default is just a safe floor.

### `sample_collector` (type: `claude`) — the hub
- **`can_talk_to: [voice_analyst, guide_writer, glossary_builder, user]`** — the
  hub reaches all three specialists **and** is the **only agent that can talk to
  `user`**. Keep the human-facing surface to a single agent (see Tips).
- **`role`** — curate the corpus, sequence analyst → writer → builder, assemble and
  deliver. On `up` this becomes the agent's first prompt, wrapped in a **standby
  notice**, so the hub waits for your samples instead of proactively mailing peers.

### `voice_analyst` / `guide_writer` / `glossary_builder` (type: `claude`)
- Each lists **`can_talk_to: [sample_collector]`** only — they report upward and
  never to each other or the `user`. Work always flows back through the hub.
- **`role`** — the analyst extracts voice traits **grounded in quotes**; the writer
  turns them into do/don't rules and before/after rewrites; the builder compiles the
  **Term | Approved | Avoid | Notes** glossary.
- **Turn detection:** `claude` → a **Stop hook**, installed automatically at `up`.

### What's *not* in this config
- **No `periodically_ping_seconds`.** Nothing is auto-nudged on a timer; the
  pipeline is purely event-driven off real mail.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on.

---

## 3. Run it

From the repo root:

```bash
./agentainer up -c examples/brand-voice-style-guide.yaml
```

`up` loads/validates the config, creates the runtime dirs, initializes each agent's
mailbox (the five folders `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue,
and an `outbox/<peer>/` folder **for each allowed recipient** — that folder's
`about.md` contact card *is* the ACL made visible), installs the Claude Stop hook
per agent, opens one tmux session per agent `cd`'d into its workdir, delivers the
standby first prompt, and starts the liveness supervisor.

At the end `up` prints attach and **`serve`** hints. The `serve` line gives you the
mail-app control-plane UI (threads, live panes, send-as-user, availability toggle).
It binds **`127.0.0.1` by default** — keep it loopback-only unless you deliberately
add `--host`/`--token`. See the `README.md` "control-plane UI" section.

> **Key-free demo:** swap each `command:` for a mock bash loop and watch the whole
> pipeline route mail with no API keys — the mechanics are identical.

---

## 4. Drive it

The `user` is a **virtual mailbox** that defaults to **away**. To *receive* the
final package as mail rather than have it held, turn yourself available first:

```bash
./agentainer user available -c examples/brand-voice-style-guide.yaml
```

Now send your samples (and the brief) into the swarm, addressed to the hub:

```bash
./agentainer send --to sample_collector \
  "Here are 20 blog posts, 5 marketing emails, and our About page. Build our brand voice guide, style guide, and approved-terms glossary."
```

### The mail flowing

Watching the log (§5), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **sample_collector organizes the corpus** and writes a briefing into
   `outbox/voice_analyst/`. On stop, that routes to the analyst.
2. **voice_analyst extracts the voice**, writes `VOICE-ANALYSIS.md`, and returns the
   traits to `outbox/sample_collector/`.
3. **sample_collector briefs the writer** into `outbox/guide_writer/`; the writer
   produces `STYLE-GUIDE.md` and reports back.
4. **sample_collector hands off to the builder** into `outbox/glossary_builder/`;
   the builder produces `GLOSSARY.md` and reports back.
5. **sample_collector assembles the package** and writes the final answer into
   `outbox/user/` — delivered to your `user` mailbox (`agentainer user inbox`, or
   the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

---

## 5. Observe

```bash
./agentainer status -c examples/brand-voice-style-guide.yaml   # who's up, queue, unread, ACL
./agentainer logs   -c examples/brand-voice-style-guide.yaml -f # durable event log, follow live
./agentainer inbox  voice_analyst -c examples/brand-voice-style-guide.yaml
./agentainer queue  guide_writer  -c examples/brand-voice-style-guide.yaml
./agentainer attach glossary_builder -c examples/brand-voice-style-guide.yaml  # Ctrl-b d to detach
```

The durable JSONL log is the source of truth for history — tmux keeps no
scrollback, so this is how you reconstruct `user-send`, `route`, `delivered`,
`read`, `bounce`, etc.

---

## 6. Resume after a stop

```bash
./agentainer down -c examples/brand-voice-style-guide.yaml
./agentainer up   -c examples/brand-voice-style-guide.yaml   # resume is the default
```

On `up`, Agentainer reattaches each agent's recorded conversation via `claude
--resume <id>` (recorded in `.agentainer/sessions.yaml`). A resumed agent is *not*
re-sent the standby prompt. Pass `--no-resume` to force everyone fresh; inspect with
`agentainer sessions`. See [`sessions-and-resume.md`](../sessions-and-resume.md).

---

## 7. Search intent this serves

People arrive at this pattern looking for:

- **"brand voice guide" / "brand voice guidelines template"** — the guide_writer's
  `STYLE-GUIDE.md` is exactly that, generated from *your* real copy.
- **"tone of voice examples"** — the voice_analyst grounds every trait in quotes
  pulled from your samples, so the tone is shown, not asserted.
- **"style guide vs. glossary"** — this swarm produces both, and keeps them
  consistent by routing the guide into the glossary_builder.
- **"how to audit our writing for consistency"** — the glossary_builder flags the
  same thing spelled two ways for the hub to resolve.
- **"content ops multi-agent workflow"** — a working hub-and-spoke you can adapt.

---

## 8. Tips & footguns

- **Keep the hub the only `user`-facing agent.** Only `sample_collector` lists
  `user`. That gives you a single point of contact and guarantees raw analysis is
  assembled before it reaches you. If a specialist tries to mail `user` directly,
  the orchestrator bounces it (ACL) and drops a `system` note explaining who it
  *can* message — the model self-corrects in-band.

- **Feed a representative corpus.** The analyst is only as good as the samples.
  Include every channel you care about (long-form, email, social, error messages);
  a thin corpus makes the analyst ask for more rather than hallucinate a voice.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion. If an
  agent seems stuck, check that its **turn detection actually fires** — a
  `type`/`command` mismatch (a `claude` agent whose `command` doesn't launch Claude)
  means completion never triggers and the agent pins "busy" forever.

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: `read/` is a best-effort receipt, over-presented mail auto-archives, and a
  per-pair runaway cap kills "thanks!/you're welcome!" loops.

---

### See also

- [`research-swarm.md`](./research-swarm.md) — the delegate → do → review sibling.
- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`cli-reference.md`](../cli-reference.md) — every subcommand and flag.
- `examples/quickstart.yaml` — the key-free mock-agent starter.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

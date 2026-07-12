# Use case: the SEO content factory

A concrete, end-to-end walkthrough of the shipped `examples/seo-content-factory.yaml`
swarm — a four-agent pipeline that turns a **keyword brief** into a
publish-ready, search-optimized article. A **strategist** owns the brief and the
human, a **researcher** builds the keyword/SERP map, a **writer** drafts against
it, and an **seo_editor** runs the on-page pass (title tag, meta description,
heading outline, internal links, and a `FAQPage` JSON-LD block). It's the
"brief → research → draft → optimize → ship" loop, wired entirely through
Agentainer's file-based mail model.

Everything below is based on the actual contents of `examples/seo-content-factory.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you supply the coding-CLI
commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Why this is a great Agentainer use case

SEO content is a genuinely **multi-role pipeline with distinct search intent at
each stage**, which is exactly what the mail model is for:

- **Keyword research** is a divergent, exploratory job — read the SERP, map the
  intent, find the gaps. That's a different skill (and often a different model)
  than drafting.
- **Drafting** wants a writer that satisfies the search intent for a *human*
  reader, not a keyword-stuffer.
- **The on-page pass** is a checklist discipline — title tag length, meta
  description, one H1, valid schema — that benefits from a dedicated reviewer who
  didn't write the prose.

Splitting these across agents with an enforced `can_talk_to` graph keeps each role
honest: raw keyword data always flows through a writer, and nothing reaches the
user until the on-page pass has run. It also serves the **modern search surface** —
the same structured, intent-first, `FAQPage`-schema'd article that ranks in
Google is the one that gets **cited by AI answer engines and LLM search** (AEO/GEO),
because both reward clearly-structured answers to real questions.

---

## 2. The topology

```
        keyword brief
  user ─────────────▶ strategist ──────────────▶ researcher
        (article)  ◀──────┐   │  │                   │
                          │   │  └──────────────▶ writer
                     final package                 │  ▲
                          │   └──────────────▶ seo_editor
                          │                        (writer ⇄ seo_editor: peer loop)
                    strategist ◀── on-page report ─┘
```

Four agents, one hub-and-spoke flow:

1. **`user` → `strategist`** — you send the keyword brief.
2. **`strategist` → `researcher`** — the strategist restates the brief (primary
   keyword, intent, audience, length) and asks for a keyword/SERP map.
3. **`researcher` → `strategist` → `writer`** — the keyword map comes back to the
   hub, which briefs the writer to draft against it.
4. **`writer` ⇄ `seo_editor`** — the writer drafts, hands off to the editor for the
   on-page pass, and the two iterate directly as **peers**.
5. **`seo_editor` / `writer` → `strategist` → `user`** — the finished package is
   reported to the strategist, who returns the article to you.

The routing above isn't a suggestion — it's *enforced* by each agent's
`can_talk_to` list. Only the **strategist** lists `user`; the researcher, writer,
and seo_editor can't reach the human at all. Anything off-list is bounced back as
a `system` message and filed in `failed/` (see §7).

---

## 3. The config, explained

Here is the shape of `examples/seo-content-factory.yaml` (full file in the repo):

```yaml
swarm:
  name: seo-content-factory
  root: ./seo-content-factory-workspace

defaults:
  capture: none              # tightened per agent (claude/codex auto-upgrade to hook)
  can_talk_to: []            # deny-by-default ACL; each agent opts in below

agents:
  - name: strategist
    type: claude
    can_talk_to: [researcher, writer, seo_editor, user]
    command: "claude --dangerously-skip-permissions"
    capture: none            # claude Stop hook -> auto-upgraded to capture: hook
    role: |
      You are the SEO STRATEGIST and the hub ... the ONLY agent who talks to the user.
      ... MAILBOX: read inbox/, write outbox/<name>/, move handled mail to read/ ...

  - name: researcher
    type: gemini
    can_talk_to: [strategist]
    command: "gemini --yolo"
    capture: pane            # gemini has no hook -> poll the tmux pane
    role: |
      You are the KEYWORD & SERP RESEARCHER. Produce KEYWORDS.md: primary keyword,
      secondary/long-tail cluster, search intent, SERP gaps, and an H2/H3 outline ...

  - name: writer
    type: claude
    can_talk_to: [strategist, seo_editor]
    command: "claude --dangerously-skip-permissions"
    role: |
      You are the CONTENT WRITER. Draft ARTICLE.md against the keyword map. Human
      first, engine second; satisfy intent; no keyword stuffing ...

  - name: seo_editor
    type: codex
    can_talk_to: [strategist, writer]
    command: "codex --yolo"
    role: |
      You are the ON-PAGE SEO EDITOR. Produce SEO.md: <=60-char title tag,
      <=155-char meta description, H1/H2/H3 hierarchy, internal links, alt-text,
      and a valid FAQPage JSON-LD block ...
```

Field by field:

### `swarm`
- **`name: seo-content-factory`** — the swarm's name (shows in `status`, logs,
  sessions).
- **`root: ./seo-content-factory-workspace`** — parent directory for each agent's
  working directory and mailbox. Each agent gets
  `seo-content-factory-workspace/<name>/` (created on `up`); orchestrator state
  goes under `seo-content-factory-workspace/.agentainer/` (never commit it).

### `defaults`
- **`capture: none`** — the default turn-detection mode, tightened per agent.
  Because `capture` is keyed off each agent's `type`, `claude` and `codex` (whose
  CLIs support a completion **hook**) are **auto-upgraded** back to `hook` with a
  warning at `up` — leaving `capture: none` on them would blind the orchestrator to
  turn completion. Net effect: strategist, writer, seo_editor use their hooks; the
  researcher overrides to `pane`.
- **`can_talk_to: []`** — deny-by-default ACL; every agent opts in explicitly.

### `strategist` (type: `claude`)
- **`can_talk_to: [researcher, writer, seo_editor, user]`** — the hub, and the
  **only agent that can talk to `user`**. Keep the human-facing surface to one
  agent (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launcher
  (treat command strings as sensitive; they may embed keys via a shell alias).
- **`role`** — owns the brief, sequences research → draft → on-page pass, and
  returns the finished article. Its `MAILBOX` reminder re-states the two verbs and
  four folders.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `researcher` (type: `gemini`)
- **`can_talk_to: [strategist]`** — reports only to the hub; cannot reach `user`.
- **`capture: pane`** — Gemini's CLI can't call a completion program, so Agentainer
  detects "turn done" by **polling the tmux pane** until it stops changing.
- **`role`** — build `KEYWORDS.md`: primary keyword, secondary/long-tail cluster,
  intent, SERP gaps, People-Also-Ask questions, and a suggested outline.

### `writer` (type: `claude`)
- **`can_talk_to: [strategist, seo_editor]`** — drafts for the hub and iterates with
  the editor as a peer.
- **`role`** — draft `ARTICLE.md` against the keyword map; human reader first,
  search engine second; no keyword stuffing.
- **Turn detection:** `claude` → Stop hook.

### `seo_editor` (type: `codex`)
- **`can_talk_to: [strategist, writer]`** — runs the on-page pass and iterates with
  the writer; reports the shippable package to the hub.
- **`role`** — produce `SEO.md`: title tag, meta description, heading hierarchy,
  internal-link suggestions, alt-text, and a valid `FAQPage` JSON-LD block.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### What's *not* in this config
- **No `periodically_ping_seconds`.** The pipeline is purely event-driven off real
  mail; nothing self-nudges on a timer.
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — mail addressed to you is *held* (never bounced) until you flip it on.

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/seo-content-factory.yaml
```

`up` loads and validates the config (printing the `capture: none → hook` upgrade
warnings for strategist/writer/seo_editor), creates the runtime dirs, initializes
each agent's five mailbox folders (`inbox/ outbox/ read/ sent/ failed/`) plus an
`outbox/<peer>/` for **each allowed recipient**, installs per-type turn detection
(Claude Stop hook, Codex `notify` hook, pane polling for the researcher), opens one
tmux session per agent in its workdir, delivers the standby first prompt, and starts
the liveness supervisor.

At the end, `up` prints attach and **`serve`** hints. The `serve` line gives you the
mail-app control-plane UI (threads, live panes, send-as-user, availability toggle).
By default the UI binds **`127.0.0.1`** (loopback only) — safe. Only add
`--host 0.0.0.0` for a remote bind, and then a **`--token` is required**; never
expose this control plane on `0.0.0.0` without one. See the `README.md`
"control-plane UI" section.

> **Key-free demo:** swap each `command:` for a mock bash loop and watch the whole
> pipeline route mail with no API keys — the mechanics are identical.

---

## 5. Drive a brief

The `user` is a **virtual mailbox** that defaults to **away**. To *receive* the
finished article as mail (rather than have it held), turn yourself available first:

```bash
./agentainer user available -c examples/seo-content-factory.yaml
```

Now send the keyword brief into the swarm, addressed to the strategist:

```bash
./agentainer send --to strategist \
  "Write a 1500-word article for the keyword 'best standing desks for small apartments'. Informational intent, audience: remote workers in studio apartments."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped `From:
user`, enqueued for the strategist, released into its `inbox/` (empty inboxes
release immediately), and the strategist is **nudged** — the protocol, including its
allowed-recipient list, is re-pasted into its pane.

### The mail flowing

Each hop is a `stop → sweep → route → release → nudge` cycle:

1. **strategist receives the brief.** It restates the target (primary keyword,
   intent, audience, length) and writes it into `outbox/researcher/`. On stop, the
   orchestrator sweeps the outbox, routes to the researcher, and nudges it.
2. **researcher builds the keyword map.** It reads its inbox, produces `KEYWORDS.md`,
   and writes the map into `outbox/strategist/`. On stop, that routes back to the hub.
3. **strategist briefs the writer.** It hands the keyword map to `outbox/writer/`.
4. **writer drafts.** It reads the map, writes `ARTICLE.md`, and sends the draft to
   `outbox/seo_editor/` for the on-page pass.
5. **writer ⇄ seo_editor iterate.** The editor produces `SEO.md` (title tag, meta
   description, headings, internal links, `FAQPage` schema), flags any thin section
   or intent mismatch back to `outbox/writer/`, and they converge as peers.
6. **strategist finalizes.** The shippable package is reported to the strategist,
   which writes the finished article into `outbox/user/`. On stop, that's delivered
   to your `user` mailbox (`agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/seo-content-factory.yaml
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback):

```bash
./agentainer logs -c examples/seo-content-factory.yaml           # whole swarm
./agentainer logs -c examples/seo-content-factory.yaml -f        # follow live
./agentainer logs writer -c examples/seo-content-factory.yaml    # just one agent
```

**A specific inbox / queue / live pane:**

```bash
./agentainer inbox  writer     -c examples/seo-content-factory.yaml
./agentainer queue  seo_editor -c examples/seo-content-factory.yaml
./agentainer attach researcher -c examples/seo-content-factory.yaml   # Ctrl-b d to detach
```

---

## 7. Search intent this serves

This pipeline is built to earn placement across the surfaces where content is
discovered today:

- **Informational / "how-to" queries** — the researcher maps People-Also-Ask
  questions and the writer answers them directly, which is what wins featured
  snippets and the "People Also Ask" carousel.
- **Commercial-investigation / "best X" queries** — clear comparison structure and
  intent-matched subheads (e.g. "best standing desks for small apartments").
- **Long-tail, low-competition clusters** — the keyword map targets the specific
  questions where a focused article can rank fast.
- **AI answer engines & LLM search (AEO/GEO)** — the same `FAQPage` JSON-LD, clean
  heading hierarchy, and direct question→answer structure that Google rewards is
  what makes an article **quotable** by AI overviews and chat-based search.

---

## 8. Tips & footguns

- **Keep the strategist the only `user`-facing agent.** Only the strategist lists
  `user` in `can_talk_to`, giving you one point of contact and guaranteeing raw
  keyword data and unedited drafts pass through the on-page pass before they reach
  you. If the writer or researcher tries to mail `user`, the orchestrator bounces it
  (ACL) and drops a `system` note explaining who it *can* message.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion. A
  `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
  Claude) means completion never fires and the agent pins "busy" forever — Agentainer
  rejects that at `up`, but it's the first thing to check if an agent looks stuck.

- **Nudges re-inject the protocol every time.** Moving mail to `read/` is a
  best-effort receipt; a message shown too many times without being handled is
  auto-archived so the queue advances, and a per-pair runaway cap kills
  "thanks!/you're welcome!" loops.

- **The on-page checklist is the editor's, not the writer's.** Splitting drafting
  from the on-page pass is deliberate: a reviewer who didn't write the prose is
  better at catching a >60-char title tag, a missing H1, or invalid schema.

- **Force-idle a pane-captured agent if its turn never registers:**
  ```bash
  ./agentainer idle researcher -c examples/seo-content-factory.yaml
  ```

---

### See also

- [`research-swarm.md`](./research-swarm.md) — the canonical delegate → do → review pipeline.
- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`cli-reference.md`](../cli-reference.md) — every subcommand and flag.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

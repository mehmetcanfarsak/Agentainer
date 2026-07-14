# Use case: the e-commerce listing optimizer swarm

A concrete, end-to-end walkthrough of the shipped
`examples/ecommerce-listing-optimizer.yaml` swarm — a five-agent, hub-and-spoke
pipeline that turns a raw product into a **search-optimized marketplace listing**.
A **product_analyzer** hub writes a shared brief, three specialists draft the
**SEO title**, the **description**, and the **bullet points** in parallel, and an
**seo_checker** scores the assembled listing before it goes back to the human.

If you sell on Amazon, Shopify, Etsy, or Walmart, this is the everyday problem:
the same product wins or loses on how its listing is written. Titles, bullets, and
descriptions have to satisfy two audiences at once — the **marketplace search
algorithm** that indexes your keywords and the **buyer** who has to be convinced in
five seconds. This swarm splits that work across focused agents and wires it all
through Agentainer's file-based mail model.

Everything below is based on the actual contents of
`examples/ecommerce-listing-optimizer.yaml` and the shipped CLI (`lib/cli.py`) and
mailroom (`lib/mail.py`). No API keys are needed to understand the mechanics; to
run it *for real* you supply the coding-CLI commands (or swap them for mock bash
loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Why this is a great fit for Agentainer

Product listing optimization is naturally a **divide-and-recombine** task, which
is exactly what the hub-and-spoke mail model is good at:

- **Separation of concerns.** A great SEO title obeys different rules than a
  scannable bullet list or a conversion-focused description. Giving each its own
  agent with a tight `role` produces sharper copy than asking one model to juggle
  all three at once.
- **One source of truth.** Every writer optimizes from the *same* analyzer brief
  (target buyer, primary + secondary keywords, differentiators), so the title, the
  bullets, and the description reinforce one keyword strategy instead of drifting
  apart.
- **A built-in quality gate.** The `seo_checker` is a dedicated adversary that
  scores the finished listing against Amazon/Shopify SEO conventions and buyer
  search intent — the "check the work" half of the loop, kept honest by being a
  separate agent from the ones who wrote it.
- **LLM-search ready.** The same brief-driven, intent-mapped copy that ranks in
  marketplace search also reads well to AI shopping assistants and LLM answer
  engines that increasingly summarize product listings.

---

## 2. The topology

```
                            user
                             │  optimize this product
                             ▼
                      product_analyzer   (the hub: writes the brief, assembles)
          ┌──────────────┬──────┴──────┬──────────────┐
          ▼              ▼             ▼               ▼
     title_writer  description_   bullet_writer   seo_checker
                      writer                       (scores draft)
          └──────────────┴─────────────┴───────────────┘
                     all replies flow back to product_analyzer
```

Five agents, a strict hub-and-spoke flow:

1. **`user` → `product_analyzer`** — you send the raw product (name, specs,
   category, maybe a rough blurb).
2. **`product_analyzer` → title_writer / description_writer / bullet_writer** —
   the hub writes one shared brief and fans it out to the three writers.
3. **writers → `product_analyzer`** — each returns its piece (SEO title,
   description, bullets) to the hub. Writers **never** talk to each other.
4. **`product_analyzer` → `seo_checker`** — the hub assembles the draft listing
   and sends it for scoring.
5. **`seo_checker` → `product_analyzer`** — the checker returns a scorecard plus
   prioritized fixes; the hub applies them (looping back to a writer if needed).
6. **`product_analyzer` → `user`** — the hub returns the final, checked listing.

The routing isn't a suggestion — it's *enforced* by each agent's `can_talk_to`
list. Every writer can talk only to the analyzer; only the analyzer can talk to
`user`. Anything else is bounced back as a `system` message and filed in `failed/`
(see §7).

---

## 3. The config, explained

Here is the shape of `examples/ecommerce-listing-optimizer.yaml` (see the file for
the full `role:` blocks):

```yaml
swarm:
  name: ecommerce-listing-optimizer
  root: ./ecommerce-listing-optimizer-workspace

defaults:
  capture: none              # tightened per agent below
  can_talk_to: []            # default ACL: talk to no one

agents:
  - name: product_analyzer
    type: claude
    can_talk_to: [title_writer, description_writer, bullet_writer, seo_checker, user]
    command: "claude --dangerously-skip-permissions"
    capture: pane
    role: |
      You are the PRODUCT ANALYZER and the hub of a listing-optimization team.
      ... writes a shared BRIEF, fans it to the writers, assembles the draft,
      sends it to seo_checker, then returns the final listing to the user ...

  - name: title_writer
    type: claude
    can_talk_to: [product_analyzer]
    command: "claude --dangerously-skip-permissions"
    capture: pane
    role: |
      You are the SEO TITLE WRITER. Lead with the primary keyword, front-load
      brand + what-it-is + top attribute, stay under ~150 chars ...

  - name: description_writer
    type: claude
    can_talk_to: [product_analyzer]
    # ... conversion-focused description, keywords woven in naturally ...

  - name: bullet_writer
    type: claude
    can_talk_to: [product_analyzer]
    # ... 5 feature/benefit bullets mapped to buyer search intent ...

  - name: seo_checker
    type: claude
    can_talk_to: [product_analyzer]
    # ... audits keyword coverage, length, readability; returns a scorecard ...
```

> 📄 **Full config:**
> [`examples/ecommerce-listing-optimizer.yaml`](../../examples/ecommerce-listing-optimizer.yaml)

Field by field:

### `swarm`
- **`name: ecommerce-listing-optimizer`** — the swarm's name (shows up in
  `status`, logs, sessions).
- **`root: ./ecommerce-listing-optimizer-workspace`** — the parent directory for
  the agents' working directories and mailboxes. Each agent gets
  `…-workspace/<name>/` as its workdir (created on `up`), with its mailbox folders
  alongside. Orchestrator state goes under `…-workspace/.agentainer/` (never commit
  it).

### `defaults`
Applied to every agent unless overridden.
- **`capture: none`** — the default turn-detection floor. Every agent below
  overrides it to `capture: pane` so the orchestrator learns when a turn finished
  by polling the tmux pane until it stops changing. (For `claude` agents the loader
  would otherwise auto-upgrade `none` → `hook`; here we pick `pane` explicitly so
  the swarm behaves identically whether you run real Claude CLIs or key-free mock
  loops.)
- **`can_talk_to: []`** — the default ACL is "talk to no one". Every agent states
  its own list explicitly, so this default is just a safe floor.

### `product_analyzer` (type: `claude`) — the hub
- **`can_talk_to: [title_writer, description_writer, bullet_writer, seo_checker,
  user]`** — the analyzer is the hub: it briefs all three writers, sends drafts to
  the checker, and is the **only agent that can talk to `user`**. Keeping the
  human-facing surface to one agent gives you a single, clean funnel (see Tips).
- **`command: "claude --dangerously-skip-permissions"`** — placeholder launch
  command. Substitute your own (e.g. a shell alias). Treat command strings as
  sensitive; they may embed keys.
- **`role`** — the standing identity + the assembly workflow. On `up` this becomes
  the first prompt, wrapped in a **standby notice** ("no task yet — wait until
  notified"), so the hub waits for your product instead of proactively mailing
  peers.

### The four specialists (all type: `claude`)
- **`title_writer`, `description_writer`, `bullet_writer`, `seo_checker`** each
  have **`can_talk_to: [product_analyzer]`** and nothing else. They report only to
  the hub — never to each other, never to `user`. That's what keeps one keyword
  strategy authoritative and every deliverable passing through assembly and review.

### What's *not* in this config
- **No `pings`.** No agent is auto-nudged on a timer; the
  pipeline is purely event-driven off real mail.
- **No shared workdir.** Each agent has its own workspace, so there's no mailbox
  namespacing to think about. (If you *did* point two agents at one directory,
  Agentainer would prefix their mailbox folders and warn you — quote the path if
  you set it.)
- **No `user` availability set in the file.** The `user` mailbox defaults to
  **away** — the final listing is *held* (never bounced) until you flip it on
  (see §5).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/ecommerce-listing-optimizer.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints any warnings.
2. Creates the runtime dirs (`…-workspace/.agentainer/…`: log, queue, run,
   sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, the per-agent queue, and an
   `outbox/<peer>/` folder **for each allowed recipient**. That folder's
   `about.md` contact card *is* the ACL made visible: the analyzer gets
   `outbox/title_writer/`, `outbox/description_writer/`, `outbox/bullet_writer/`,
   `outbox/seo_checker/`, `outbox/user/`; each writer gets only
   `outbox/product_analyzer/`.
4. **Arranges pane polling** for every agent (all use `capture: pane`).
5. **Opens one tmux session per agent**, `cd`'d into its workdir, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until
   notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints. The `serve` line gives you
the mail-app control-plane UI (threads, live panes, send-as-user, availability
toggle). By default the UI **binds `127.0.0.1` only** (loopback) — drop nothing to
stay safe; add `--host`/`--token` deliberately to expose it. See the `README.md`
"control-plane UI" section.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole pipeline route mail with no API keys — the mechanics are identical.

---

## 5. Drive a product

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. To *receive* the analyzer's final listing as mail (rather than have it
held), turn yourself available first:

```bash
./agentainer user available -c examples/ecommerce-listing-optimizer.yaml
```

Now send the product into the swarm, addressed to the hub:

```bash
./agentainer send -c examples/ecommerce-listing-optimizer.yaml --to product_analyzer \
  "Optimize this listing: stainless steel insulated water bottle, 32oz, double-wall vacuum, keeps drinks cold 24h / hot 12h, BPA-free, powder-coat finish. Category: sports water bottles."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the analyzer, then — because the
inbox was empty — **released into `inbox/`** and the analyzer is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **analyzer writes the brief.** It reads `inbox/`, decides the target buyer,
   primary keyword, and differentiators, and writes that brief into
   `outbox/title_writer/`, `outbox/description_writer/`, and
   `outbox/bullet_writer/`. On stop, all three route and get nudged.
2. **the three writers work in parallel.** Each reads its inbox, drafts its piece
   (title / description / bullets), and writes the result into
   `outbox/product_analyzer/`. Each `stop` routes back to the hub.
3. **analyzer assembles + sends for review.** Once it has all three pieces, it
   writes the combined draft listing into `outbox/seo_checker/`.
4. **seo_checker scores.** It reads the draft, writes a scorecard + prioritized
   fixes into `outbox/product_analyzer/`.
5. **analyzer finalizes.** It applies the fixes (looping back to a writer if a fix
   needs a rewrite), then writes the final listing into `outbox/user/`. On stop,
   that's delivered to your `user` mailbox (see it with
   `agentainer user inbox`, or in the UI).

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/ecommerce-listing-optimizer.yaml
```

```
swarm: ecommerce-listing-optimizer   root: ./ecommerce-listing-optimizer-workspace
  product_analyzer (claude) up idle queue=0 unread=0 talks=title_writer, description_writer, bullet_writer, seo_checker, user
  title_writer (claude) up idle queue=0 unread=1 talks=product_analyzer
  description_writer (claude) up idle queue=0 unread=1 talks=product_analyzer
  bullet_writer (claude) up idle queue=0 unread=1 talks=product_analyzer
  seo_checker (claude) up idle queue=0 unread=0 talks=product_analyzer
supervisor: alive
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback, so this is how you reconstruct what happened):

```bash
./agentainer logs -c examples/ecommerce-listing-optimizer.yaml           # whole swarm
./agentainer logs -c examples/ecommerce-listing-optimizer.yaml -f         # follow live
./agentainer logs seo_checker -c examples/ecommerce-listing-optimizer.yaml # just one agent
```

You'll see `user-send`, `delivered`, `route`, `read`, `read-receipt`, `bounce`,
etc. — one JSONL line per event.

**A specific inbox / queue / pane:**

```bash
./agentainer inbox  title_writer -c examples/ecommerce-listing-optimizer.yaml
./agentainer queue  title_writer -c examples/ecommerce-listing-optimizer.yaml
./agentainer attach title_writer -c examples/ecommerce-listing-optimizer.yaml   # Ctrl-b d to detach
```

---

## 7. Search-intent tips (the SEO half)

The point of the swarm is copy that ranks *and* converts. A few things the writers
are steered toward, and that you should sanity-check in the output:

- **Front-load the primary keyword.** Marketplace search weights the first words of
  the title heavily, and buyers scan left-to-right. `title_writer` leads with the
  primary keyword + brand + what-it-is + top attribute.
- **Map bullets to real queries.** `bullet_writer` writes each bullet against a
  buyer search intent — "keeps drinks cold 24 hours", "fits a car cup holder" —
  not vague adjectives. If you know the exact phrases shoppers type, feed them in
  with the product; they become secondary keywords in the brief.
- **Weave keywords, don't stuff them.** `description_writer` writes for the buyer
  first; keyword stuffing gets penalized by marketplace algorithms and reads badly
  to the LLM answer engines that now summarize listings.
- **Let the checker be adversarial.** `seo_checker` scores keyword coverage, title
  length, bullet parallelism, readability, and truthfulness vs. the specs. Treat a
  failing check as a reason to loop back to the writer, not a formality.
- **Stay truthful to the specs.** None of the writers may invent features; the
  checker flags claims not supported by the product you sent. Inflated listings
  earn returns and bad reviews, which sink ranking harder than any keyword helps.

---

## 8. Tips & footguns

- **Keep the analyzer the only `user`-facing agent.** Only the analyzer lists
  `user` in `can_talk_to`, so raw drafts always pass through assembly and review
  before they reach you. If a writer tries to mail `user` directly, the
  orchestrator bounces it (ACL) and drops a `system` note in the writer's inbox
  explaining who it *can* message — the model self-corrects in-band.

- **Watch the stop → nudge loop.** The whole clock runs on turn completion: an
  agent stops, its outbox is swept, mail is routed, recipients are released and
  nudged. If an agent seems stuck, confirm its **turn detection actually fires** —
  a `type`/`command` mismatch (e.g. a `claude` agent whose `command` doesn't launch
  Claude) means completion never triggers and the agent pins "busy" forever.
  Agentainer refuses such a config at `up`, but a real CLI that hangs on a trust
  modal can look the same; pre-trust is handled for you, but `status` showing an
  agent `busy` for a long time with `unread` mail is the tell.

- **Force-idle a pane-captured agent whose turn never registers.** Every agent here
  uses pane polling; if a capture never fires you can nudge the state along:
  ```bash
  ./agentainer idle title_writer -c examples/ecommerce-listing-optimizer.yaml
  ```

- **Nudges re-inject the protocol every time.** A forgetful model can't wedge the
  swarm: mail moved to `read/` is a best-effort receipt, a message shown too many
  times is auto-archived so the queue advances, and a per-pair runaway cap kills
  "thanks!/you're welcome!" loops.

- **`remove-session` to reset.** To wipe all Agentainer state (runtime +
  mailboxes) and start every conversation fresh next `up`:
  ```bash
  ./agentainer down           -c examples/ecommerce-listing-optimizer.yaml
  ./agentainer remove-session -c examples/ecommerce-listing-optimizer.yaml
  ```
  It refuses while anything is still running — always `down` first. It never
  touches your config or the agents' output files.

---

### See also

- [`research-swarm.md`](./research-swarm.md) — the canonical delegate → do → review
  pipeline.
- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`cli-reference.md`](../cli-reference.md) — every subcommand and flag.
- `examples/quickstart.yaml` — the key-free mock-agent starter.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

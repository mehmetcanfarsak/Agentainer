# Use case: the SEO audit & fix swarm

A concrete, end-to-end walkthrough of the shipped `examples/seo-audit-and-fix.yaml`
swarm — a four-agent pipeline where a **crawler** hub inspects a site for
organic-search problems, an **issue analyzer** prioritizes them, a **content
fixer** *applies* the approved content changes in place, and a **report writer**
summarizes what was found and what changed. It is the canonical
"crawl → categorize → fix → report" loop for **pure search-engine optimization**,
wired entirely through Agentainer's file-based mail model.

Everything below is based on the actual contents of `examples/seo-audit-and-fix.yaml`
and the shipped CLI (`lib/cli.py`) and mailroom (`lib/mail.py`). No API keys are
needed to understand the mechanics; to run it *for real* you supply the coding-CLI
commands (or swap them for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. Why this swarm (and how it differs from the other audits)

Agentainer ships three other "audit" swarms. This one is **distinct in scope and
in behavior**:

| Swarm | Owns | Delivers |
|-------|------|----------|
| `performance-audit.yaml`  | speed / Core Web Vitals (LCP, INP, CLS, TTFB, bundles) | a *measured* fix list — **does not edit** |
| `accessibility-audit.yaml`| WCAG 2.2 AA conformance (alt text, contrast, keyboard) | a conformance report — **does not edit** |
| `security-audit.yaml`     | vulnerabilities (OWASP / STRIDE) | a findings report — **does not edit** |
| **`seo-audit-and-fix.yaml`** | **organic SEO**: titles, meta, headings, internal links, canonicals, crawlability (robots/sitemap/indexability) | **applies the content fixes in place**, then reports |

The crucial difference: the other audits only *report* — a human (or another
tool) applies the changes. This swarm **closes the loop**: `content_fixer`
writes the corrected `<title>`, meta description, headings, canonicals, internal
links, alt text, robots.txt and sitemap back into the site. That is why the hub
is a *crawler of a concrete working tree* and why the leaves **share one
`{root}/site` working directory** — the analyzer reads it, the fixer edits it,
the writer reports on it. (Agentainer auto-namespaces their mailbox folders so
their mail never collides, and flags the shared dir as a heads-up, not an error.)

"SEO" here means **ranking and discoverability in search engines and LLM
answers**, not page weight: we care about whether a page has a unique, keyword-
and brand-bearing title, whether the meta description earns the click, whether
headings form a real outline, whether important pages are internally linked and
crawlable, and whether canonical/sitemap/noindex signals are coherent.

---

## 2. The topology

```
                  issue_analyzer ──┐
   user ◀──▶  crawler  ◀──▶  content_fixer     (crawler <-> every leaf;
            report_writer ──┘                    leaves never talk to each other)
```

Four agents, one ordered flow:

1. **`user` → `crawler`** — you send the site target (a checkout at `{root}/site`).
2. **`crawler` → `issue_analyzer`** — the crawler crawls the site into a page
   inventory and hands it to the analyzer to prioritize.
3. **`issue_analyzer` → `crawler`** — returns a deduplicated, prioritized issue
   list (title/meta/headings/links/canonicals/crawlability).
4. **`crawler` → `content_fixer`** — the crawler forwards the *approved* fixes to
   the fixer to apply in place.
5. **`content_fixer` → `crawler`** — returns a precise change log (old → new per file).
6. **`crawler` → `report_writer`** — forwards issues + applied changes.
7. **`report_writer` → `crawler` → `user`** — the finished before/after report.

The routing above is *enforced* by each agent's `can_talk_to` list. The leaves
only ever address the crawler; only the crawler (as hub) may reach `user`. Mail
to anyone else is bounced back as a `system` message and filed in `failed/` (see §7).

---

## 3. The config, explained

Here is `examples/seo-audit-and-fix.yaml` in full:

```yaml
# 🔎 SEO audit & fix -- a crawler hub inspects a site for organic-search
# problems, an analyzer prioritizes them, a fixer APPLIES the content changes in
# place, and a writer reports what was found and what was changed.
swarm:
  name: seo-audit-and-fix
  root: ./seo-audit-and-fix-workspace
defaults:
  capture: none
  can_talk_to: []
agents:
  - name: crawler
    type: claude
    can_talk_to: [issue_analyzer, content_fixer, report_writer, user]
    command: "claude --dangerously-skip-permissions"
    workdir: "{root}/site"
    role: |
      You are the CRAWLER and audit lead. A human sends you a target ...
  - name: issue_analyzer
    type: claude
    can_talk_to: [crawler]
    command: "claude --dangerously-skip-permissions"
    workdir: "{root}/site"
    role: |
      You are the SEO ISSUE ANALYZER. Given the crawler's page inventory ...
  - name: content_fixer
    type: codex
    can_talk_to: [crawler]
    command: "codex --yolo"
    workdir: "{root}/site"
    role: |
      You are the CONTENT FIXER. Given the crawler's approved, prioritized fix
      list, APPLY the content changes to the files in {root}/site in place ...
  - name: report_writer
    type: claude
    can_talk_to: [crawler]
    command: "claude --dangerously-skip-permissions"
    workdir: "{root}/site"
    role: |
      You are the REPORT WRITER. Given the analyzer's prioritized issue list and
      the content_fixer's applied change log ...
```

(The full `role:` text is in the file; the excerpts above show the shape.)

### `swarm`
- **`name: seo-audit-and-fix`** — the swarm's name (shows up in `status`, logs, sessions).
- **`root: ./seo-audit-and-fix-workspace`** — parent for the agents' working
  directories and mailboxes. Each agent gets `.../site/<name>-*` mailbox folders
  (namespaced because they share `site/`), and orchestrator state goes under
  `.../site/.agentainer/` (never commit it).

### `defaults`
Applied to every agent unless overridden.
- **`capture: none`** — the key-free demo default. **But note:** for `claude` and
  `codex`, whose CLIs support a completion **hook**, `capture: none` removes the
  orchestrator's only turn-completion signal — so the config loader *upgrades*
  it back to `capture: hook` and prints a warning at `up`
  (`capture: none on a claude agent gives the orchestrator no way to detect turn
  completion; using the type's default: capture: hook.`). All four agents end up
  on their natural hook.
- **`can_talk_to: []`** — the default ACL is "talk to no one". Each agent states
  its own list explicitly, so this default is just a safe floor.

### `crawler` (type: `claude`) — the hub
- **`can_talk_to: [issue_analyzer, content_fixer, report_writer, user]`** — the
  crawler is the hub: it sequences analyzer → fixer → writer and is the **only
  agent that can talk to `user`**. Keep the human-facing surface to one agent.
- **`workdir: "{root}/site"`** — a single shared quoted workdir for the hub and
  all leaves. `{root}` expands to the resolved `root` path.
- **`command: "claude --dangerously-skip-permissions"`** — launches Claude Code in
  its tmux pane. Treat command strings as sensitive; they may embed keys.
- **Turn detection:** `claude` → a **Stop hook** (installed automatically at `up`).

### `issue_analyzer` (type: `claude`)
- **`can_talk_to: [crawler]`** — analyzes the inventory and reports only to the
  crawler. It **does not edit files** (that's the fixer's job).
- **Turn detection:** `claude` → Stop hook.

### `content_fixer` (type: `codex`)
- **`can_talk_to: [crawler]`** — the **only** agent that edits `{root}/site`.
  Applies the crawler-approved fixes, then reports a change log back to the hub.
- **Turn detection:** `codex` → a `notify` program (its hook), installed at `up`.

### `report_writer` (type: `claude`)
- **`can_talk_to: [crawler]`** — writes the final before/after report and hands it
  to the crawler for delivery to `user`.
- **Turn detection:** `claude` → Stop hook.

### What's *not* in this config
- **No `periodically_ping_seconds`.** The pipeline is purely event-driven off
  real mail — the crawler only moves when you send a target.
- **No `security`/`performance`/`accessibility` scope.** Explicitly out of scope
  per the design; the crawler's `role:` tells the model to leave page speed,
  WCAG, and vulnerabilities to the other swarms.
- **`user` availability defaults to away** — mail to you is *held* (never bounced)
  until you flip it on (see §4).

---

## 4. Run it

From the repo root:

```bash
./agentainer up -c examples/seo-audit-and-fix.yaml
```

What `up` does (see `cmd_up` in `lib/cli.py`):

1. Loads and validates the config; prints warnings (the `capture: none → hook`
   upgrades, and a heads-up that the four agents share `{root}/site`).
2. Creates the runtime dirs (`.../site/.agentainer/…`: log, queue, run, sessions).
3. **Initializes the mailboxes** — for every agent, the five folders
   `inbox/ outbox/ read/ sent/ failed/`, namespace-prefixed (`crawler-`,
   `issue_analyzer-`, …) because they share a workdir, plus an `outbox/<peer>/`
   for each allowed recipient.
4. **Installs per-type turn detection** — the Claude Stop hook for crawler /
   analyzer / writer, and the Codex `notify` hook for the fixer.
5. **Opens one tmux session per agent**, `cd`'d into `{root}/site`, running its
   `command`.
6. **Delivers the standby first prompt** to each pane (role + "wait until notified").
7. **Starts the liveness supervisor** — the heartbeat that reconciles stale/dead/
   silent agents so one stuck agent can't wedge the swarm.

At the end, `up` prints attach and **`serve`** hints, e.g.:

```
:: swarm 'seo-audit-and-fix' is up with 4 agent(s)
:: attach with:  tmux attach -t <crawler-session>
:: you can use the UI with:  agentainer serve --host 0.0.0.0 -c examples/seo-audit-and-fix.yaml --token <generated> --port 8000
```

The `serve` line gives you the mail-app control-plane UI (threads, live panes,
send-as-user, availability toggle). Drop `--host`/`--token` for the safe
loopback-only bind (`127.0.0.1` default). See the `README.md` "control-plane UI"
section.

> **Key-free demo:** swap each `command:` for a mock bash loop and you can watch
> the whole crawl → analyze → fix → report route mail with no API keys.

---

## 5. Drive an audit & fix

The `user` is a **virtual mailbox** with an availability toggle that defaults to
**away**. Turn yourself available first if you want the final report delivered as
mail (rather than held):

```bash
./agentainer user available -c examples/seo-audit-and-fix.yaml
```

Now send the target into the swarm, addressed to the crawler. Point it at a real
checkout — copy your site into the shared workdir first:

```bash
cp -r /path/to/my-site ./seo-audit-and-fix-workspace/site
./agentainer send --to crawler \
  "Audit & fix the static site checked out at ./seo-audit-and-fix-workspace/site (marketing pages)."
```

Under the hood (`cmd_send` → `mail.send_as_user`): the message is stamped with a
`From: user` header and a fresh id, enqueued for the crawler, then — because the
inbox was empty — **released into `inbox/`** and the crawler is **nudged** (the
protocol is re-pasted into its pane, including its allowed-recipient list).

### The mail flowing

Watching the log (§6), you'll see the pipeline advance one turn at a time. Each
arrow is a `stop → sweep → route → release → nudge` cycle:

1. **crawler crawls.** It reads `{root}/site`, inventories every page's title,
   meta, headings and links, writes that inventory into `outbox/issue_analyzer/`.
   On stop, it routes to the analyzer.
2. **issue_analyzer prioritizes.** It confirms against the files and writes a
   ranked issue list into `outbox/crawler/`. On stop, routes back to the crawler.
3. **crawler dispatches fixes.** It forwards the approved fixes to
   `outbox/content_fixer/`. On stop, routes to the fixer.
4. **content_fixer edits.** It rewrites titles/meta/headings/canonicals/links/alt
   in place and writes a change log into `outbox/crawler/`. On stop, routes back.
5. **crawler dispatches the report.** It forwards issues + change log to
   `outbox/report_writer/`. On stop, routes to the writer.
6. **report_writer reports.** It writes the before/after report into
   `outbox/crawler/`. On stop, the crawler forwards it to `outbox/user/`.

You don't relay anything by hand — the orchestrator releases exactly one inbox
message at a time and fires the next hop off each agent's turn completion.

---

## 6. Observe

**Overall status** — who's up, idle/busy, queue depth, unread count, and the ACL:

```bash
./agentainer status -c examples/seo-audit-and-fix.yaml
```

**The durable event log** — the source of truth for history (tmux keeps no
scrollback):

```bash
./agentainer logs -c examples/seo-audit-and-fix.yaml
./agentainer logs -c examples/seo-audit-and-fix.yaml -f        # follow live
./agentainer logs content_fixer -c examples/seo-audit-and-fix.yaml
```

**A specific inbox** — what an agent is currently looking at:

```bash
./agentainer inbox content_fixer -c examples/seo-audit-and-fix.yaml
```

**Attach to a live pane** — watch (or type into) an agent's tmux session:

```bash
./agentainer attach crawler -c examples/seo-audit-and-fix.yaml
```

Detach with tmux `Ctrl-b d`.

---

## 7. Resume after a stop & tips

Tear the swarm down when you're done:

```bash
./agentainer down -c examples/seo-audit-and-fix.yaml
```

Bring it back later and **conversations resume by default** (`agentainer up`).
Each agent is reattached via its type's native resume (`claude --resume <id>`,
`codex resume <id>`); sessions are recorded in
`.../site/.agentainer/sessions.yaml`. Inspect with:

```bash
./agentainer sessions -c examples/seo-audit-and-fix.yaml
```

- **Keep the crawler the only `user`-facing agent.** Only the crawler lists `user`
  in `can_talk_to`. If a leaf tries to mail `user`, the orchestrator bounces it
  (ACL) and drops a `system` note — the model self-corrects in-band.
- **The fixer is the only editor.** Its `role:` forbids touching speed, styling,
  scripts, or app logic. If an approved fix is unsafe, it asks the crawler and
  skips the item rather than guessing — so the site never gets a half-broken edit.
- **Watch the stop → nudge loop.** The clock runs on turn completion. A
  `type`/`command` mismatch means completion never fires and the agent pins
  "busy" forever — `status` showing `busy` with `unread` mail is the tell.
- **Shared workdir is intentional here.** The leaf warning at `up` is expected:
  the analyzer, fixer and writer deliberately share `{root}/site` so the fix is
  applied to the same files the analyzer read and the writer reports on. Their
  mailboxes are auto-namespaced so they never collide.
- **Force-idle if a pane-captured agent's turn never registers** (none here by
  default — all use hooks — but handy if you swap in a `gemini`/`hermes` leaf):
  `./agentainer idle <name> -c examples/seo-audit-and-fix.yaml`.
- **`remove-session` to reset** all runtime + mailbox state and start fresh next
  `up` (always `down` first). It never touches your source files or config.

### See also

- [`getting-started.md`](../getting-started.md) — install and first swarm.
- [`cli-reference.md`](../cli-reference.md) — every subcommand and flag.
- `examples/performance-audit.yaml`, `examples/accessibility-audit.yaml`,
  `examples/security-audit.yaml` — the sibling audits this one is distinct from.
- `ProjectPlan.md` — the design source of truth (mail model §4–§14).

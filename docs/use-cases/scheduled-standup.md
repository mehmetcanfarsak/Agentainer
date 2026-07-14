# Use case: the self-running scheduled standup

A concrete walkthrough of the shipped `examples/scheduled-standup.yaml` swarm —
the showcase for **cron-scheduled pings** (`pings:`). Every other example waits
for you to `send` something; this one **runs itself on the clock**. A
**facilitator** hub wakes up each weekday morning to run an async standup, again
in the evening to post a wrap, and on Friday afternoon for a weekly retro. Two
teammate agents (**eng**, **design**) each get their *own* earlier reminder to
prep. Bring it up once and walk away — the schedules do the rest.

Everything below is based on the actual contents of
`examples/scheduled-standup.yaml` and the shipped config loader (`lib/config.py`)
and cron parser (`lib/cron.py`). No API keys are needed to understand the
mechanics; to run it *for real* you supply the coding-CLI commands (or swap them
for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The idea: a swarm with its own clock

Most swarms are *reactive* — they move when a message lands. A standup is the
opposite: nobody sends it, it just needs to **happen at a time**. That's exactly
what `pings:` is for. Each agent can carry a list of scheduled nudges, and each
nudge is a `message` delivered as a `system` mail at a **cron** time. The
orchestrator owns the clock; the model just reads the message it's handed and
acts, the same as any other mail.

The showcase here is that **different agents run on different schedules**, and a
single agent can run several:

- `facilitator` has **three** rules — a morning kickoff, an evening wrap, and a
  Friday retro — each with its own message.
- `eng` and `design` each have **one** rule: a personal "prep your update"
  reminder that fires 15 minutes *before* the facilitator's kickoff.

Nobody types anything. The swarm self-drives.

---

## 2. The topology

```
   eng ─┐
        ├── facilitator ── user   (posts the standup digest, wrap, and retro)
 design ┘
```

Hub and spoke, enforced by `can_talk_to`:

- `facilitator` may talk to `eng`, `design`, and `user`.
- `eng` and `design` may talk **only** to `facilitator`.

So the two teammates report their status up to the facilitator, and the
facilitator is the single voice that posts the assembled digest to you. A
teammate that tried to mail `user` (or each other) would be bounced by the ACL,
with a `system` note explaining who it *can* reach.

---

## 3. The schedules, explained

Each `pings` entry is a **`message`** (the text, delivered as `system` mail), a
**`cron`** (standard 5-field `minute hour day-of-month month day-of-week`,
evaluated in the host's **local** time), and an optional **`when_busy`**
(`skip`, the default, or `queue`).

### `facilitator` — three rules

```yaml
    pings:
      - message: |
          Standup time. Ask eng and design for today's update ...
        cron: "15 9 * * 1-5"          # 09:15, Mon-Fri
      - message: |
          End-of-day wrap: ask eng and design what shipped vs. slipped ...
        cron: "30 17 * * 1-5"         # 17:30, Mon-Fri
      - message: |
          Weekly retro: ask eng and design for one win, one miss ...
        cron: "0 16 * * fri"          # 16:00 every Friday (day name)
```

- **`15 9 * * 1-5`** — 09:15 on weekdays (`1-5` = Mon-Fri). Kicks off the daily
  standup.
- **`30 17 * * 1-5`** — 17:30 on weekdays. Posts the end-of-day wrap.
- **`0 16 * * fri`** — 16:00 every Friday, using the 3-letter **day name** `fri`
  (equivalent to `5`). Runs the weekly retro.

Because each rule carries a *different* message, the facilitator knows which
ceremony to run from the ping text alone — no shared state to track.

### `eng` and `design` — one rule each

```yaml
    pings:
      - message: |
          Prep your standup: jot down what you finished yesterday ...
        cron: "0 9 * * 1-5"           # 09:00, Mon-Fri (before the 09:15 kickoff)
```

Both teammates get a 09:00 weekday nudge — 15 minutes *ahead* of the
facilitator's 09:15 kickoff — so they've written their update by the time they're
asked. Same schedule, different agents: that's the per-agent granularity the
feature buys you.

### `when_busy`: skip vs. queue

Every rule here uses the default **`when_busy: skip`** — if a ping comes due
while the agent is mid-turn, it's **dropped** rather than stacked, so a slow
standup can't pile a second kickoff on top of the first. Set **`when_busy:
queue`** on a rule you can't afford to miss (a status update, an alarm): it
**waits** for the agent to free up and then fires. See
[`incident-response.yaml`](../../examples/incident-response.yaml) for a
`queue` example (a round-the-clock status cadence) and
[`customer-support-triage.md`](./customer-support-triage.md) for an off-hours
`queue` sweep.

### Cron support, and the one rule that bites

The parser (`lib/cron.py`) handles `*`, `*/step`, `a-b`, `a-b/step`, comma lists,
and 3-letter month/day names; day-of-week `0` and `7` are both Sunday; the
Vixie dom/dow OR rule applies. **Cron is validated at config load** — an invalid
expression makes `up` (and `validate`) fail outright. So every field must be in
range and every range must **ascend**: to express an overnight window, use a
comma list in the hour field like `20-23,0-7`, never a descending `20-7`.

---

## 4. Run it

From the repo root:

```bash
./agentainer up            -c examples/scheduled-standup.yaml
./agentainer user available -c examples/scheduled-standup.yaml   # so digests reach you
```

Then leave it running. On `up`, Agentainer loads the config (validating every
cron string), initializes the mailboxes and ACL-derived `outbox/<peer>/` folders,
installs per-type turn detection (Claude Stop hook, Codex `notify`), opens a tmux
session per agent, delivers the standby first prompt, and starts the liveness
supervisor. From then on the **`pings:` schedules fire on their own**: at 09:00
the teammates are nudged to prep, at 09:15 the facilitator runs standup, and so
on.

You can watch it happen in the durable log (a `ping` event is logged each time a
rule fires):

```bash
./agentainer logs -c examples/scheduled-standup.yaml -f
```

> **Key-free demo:** swap each `command:` for a mock bash loop and the swarm
> still comes up and fires its scheduled pings with **no API keys** — the cron
> mechanics are identical. To see a ping *now* without waiting for the clock,
> temporarily set a rule's `cron` to the next minute (e.g. `"* * * * *"`).

---

## 5. Customize

- **Change the cadence.** The whole behavior lives in the `cron` strings — edit
  them freely. Twice-daily standup? Add `45 13 * * 1-5`. Weekends off is already
  the default (`1-5`). Monthly review? `0 10 1 * *` (10:00 on the 1st).
- **Add a teammate.** Drop in another `can_talk_to: [facilitator]` agent with its
  own prep `pings:` rule, and add its name to the facilitator's `can_talk_to`.
- **Split the ceremonies.** Give standup, wrap, and retro to *different* hubs if
  you want them owned separately — each just needs the matching `cron` rule.
- **Mind the time zone.** Cron is evaluated in the **host's local time** (zero
  deps, no tz database). If the machine runs UTC, `9` means 09:00 UTC — set the
  hours to match the wall clock you actually want.

---

### See also

- [`daily-briefing.md`](./daily-briefing.md) — the flagship `pings:` showcase (a
  weekday-morning + weekend digest schedule).
- [`customer-support-triage.md`](./customer-support-triage.md) — business-hours,
  off-hours (`when_busy: queue`), and weekend cron sweeps on one hub.
- [`../mail-model.md`](../mail-model.md) — the four folders and how `system` mail
  is routed.
- `examples/scheduled-standup.yaml` — the config this walkthrough is built from.
- `examples/incident-response.yaml` — a round-the-clock `when_busy: queue` status
  cadence.

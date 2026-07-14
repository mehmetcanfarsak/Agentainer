# Use case: the weekly content cadence

A concrete walkthrough of the shipped `examples/content-cadence.yaml` swarm — the
`pings:` example that treats cron as a **weekly editorial calendar** rather than
an intraday watch. Nothing here runs every hour. Instead each weekday owns a
phase of the publishing cycle: **plan on Monday, draft midweek, review Wednesday,
ship Friday**, plus a **monthly** look-back on the 1st. It's how you'd actually
run a content shop, encoded in `cron`. It's the example to read when you want to
schedule by **day of the week** (`mon`, `tue,thu`, `fri`) and **day of the
month** (the 1st), not by the hour.

Everything below is based on the actual contents of
`examples/content-cadence.yaml`, the shipped config loader (`lib/config.py`), and
the cron parser (`lib/cron.py`). No API keys are needed to understand the
mechanics; to run it *for real* you supply the coding-CLI commands (or swap them
for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The idea: a calendar, not a clock

The [ops watchtower](./ops-watchtower.md) fires many times a day; a content team
is the opposite. Its rhythm is *weekly* — planning, drafting, reviewing, and
shipping happen on **specific days**, not on a fast loop. `pings:` handles this
just as naturally: a cron field can pick weekdays by name and calendar days by
number, so "every Monday at 09:00" and "the 1st of the month at 10:00" are single
lines of config.

The lesson here is **one hub, one rule per phase**, each carrying a *different*
message so the editor knows which phase it is from the ping text alone — no
shared week-state to track. The two spokes then run on their own beats of the
same week.

---

## 2. The topology

```
   writer ────┐
              ├── editor ── user   (weekly plan, publish-ready pack, monthly recap)
 promoter ──┘
```

Hub and spoke, enforced by `can_talk_to`:

- `editor` may talk to `writer`, `promoter`, and `user`.
- `writer` and `promoter` may talk **only** to `editor`.

So the editor is the single voice that reaches you and the one place the week is
assembled, instead of three agents each publishing their own thing. A spoke that
tried to mail `user` would be bounced by the ACL with a `system` note.

---

## 3. The schedules, explained

Each `pings` entry is a **`message`** (delivered as `system` mail), a **`cron`**
(standard 5-field `minute hour day-of-month month day-of-week`, evaluated in the
host's **local** time), and an optional **`when_busy`** (`skip`, the default, or
`queue`).

### `editor` — the editorial calendar

```yaml
    pings:
      - message: |
          Weekly planning. Decide this week's topics/angles ... send them to writer.
        cron: "0 9 * * mon"             # 09:00 every Monday
      - message: |
          Midweek review. Ask writer for the drafts in progress ... revision notes.
        cron: "0 14 * * wed"            # 14:00 every Wednesday
      - message: |
          Publish day. Assemble the finished pieces ... hand it to promoter ...
        cron: "0 15 * * fri"            # 15:00 every Friday
        when_busy: queue
      - message: |
          Monthly recap. Ask promoter which pieces performed ... summarize ...
        cron: "0 10 1 * *"              # 10:00 on the 1st of every month
```

- **`0 9 * * mon`** — 09:00 every Monday. The **day-of-week name** `mon` (same as
  `1`) is the readable way to target a weekday. Kicks off planning.
- **`0 14 * * wed`** — 14:00 Wednesday. Midweek review.
- **`0 15 * * fri`** — 15:00 Friday, the publish step. It's **`when_busy: queue`**
  so a release is never dropped just because the editor is mid-review — a
  scheduled publish is the one nudge you can't afford to lose.
- **`0 10 1 * *`** — 10:00 on the **1st of the month**. This is the
  **day-of-month** field doing the work (`1` in the third position), on a cadence
  that has nothing to do with the weekday cycle. A monthly recap, not a publish.

Because each rule carries a different message, the editor runs the right phase
from the ping text alone.

> **A note on day-of-month + day-of-week together.** This example keeps the two
> fields separate (each rule sets *either* a `day-of-week` *or* a `day-of-month`,
> never both restricted at once). That's deliberate: when **both** fields are
> restricted, cron uses the **Vixie OR rule** — it matches if *either* the
> day-of-month *or* the day-of-week matches. So `0 10 13 * fri` means "the 13th
> **or** any Friday," not "Friday the 13th." Keep one of the two as `*` unless
> you actually want that OR behaviour.

### `writer` and `promoter` — their own days

```yaml
  # writer
    pings:
      - message: |
          Drafting block. Advance the pieces the editor briefed you on ...
        cron: "0 9 * * tue,thu"         # 09:00 Tuesday & Thursday
  # promoter
    pings:
      - message: |
          Distribution prep. If the editor has handed you this week's pack ...
        cron: "30 15 * * fri"           # 15:30 every Friday (after the 15:00 pack)
```

- `writer` gets a **`tue,thu`** drafting block — a **comma list** of day names —
  so there's fresh material for the Wednesday review and Friday publish.
- `promoter` runs at **15:30 Friday**, 30 minutes after the editor assembles the
  pack at 15:00, so distribution is prepped while the pieces are fresh.

Same week, three different beats — the per-agent granularity `pings:` buys you.

### The cron rule that bites

The parser (`lib/cron.py`) handles `*`, `*/step`, `a-b`, `a-b/step`, comma lists,
and 3-letter month/day names; day-of-week `0` and `7` are both Sunday; the Vixie
dom/dow OR rule (above) applies. **Cron is validated at config load** — an
invalid expression makes `up` (and `validate`) fail outright, so every field must
be in range and every range must **ascend**.

---

## 4. Run it

From the repo root:

```bash
./agentainer up             -c examples/content-cadence.yaml
./agentainer user available -c examples/content-cadence.yaml   # so the editor can reach you
```

Then let the calendar run. On `up`, Agentainer loads the config (validating every
cron string), initializes the mailboxes and ACL-derived `outbox/<peer>/` folders,
installs per-type turn detection, opens a tmux session per agent, and starts the
liveness supervisor. From then on the phases fire on their days: Monday planning,
the writer's Tue/Thu drafting, Wednesday review, Friday publish + distribution,
and the monthly recap on the 1st. You can also nudge the editor at any time
(`agentainer send --to editor "..."`) to change the week's direction — the cron
rhythm and manual steering coexist.

Watch it in the durable log (a `ping` event is logged when a rule fires):

```bash
./agentainer logs -c examples/content-cadence.yaml -f
```

> **Key-free demo:** swap each `command:` for a mock bash loop and the swarm
> still comes up and fires its scheduled pings with **no API keys**. To see a
> phase *now* without waiting for its day, temporarily set a rule's `cron` to
> `"* * * * *"` (every minute).

---

## 5. Customize

- **Shift the calendar.** Every phase is one `cron` line — move planning to
  Sunday evening (`0 18 * * sun`), add a second drafting day (`0 9 * * tue,wed,thu`),
  or push publish to Thursday (`0 15 * * thu`).
- **Add a cadence.** A biweekly newsletter, a quarterly review — day-of-month and
  month fields let you express calendar events the weekday cycle can't (`0 9 1
  1,4,7,10 *` = 09:00 on the 1st of each quarter).
- **Add a channel.** Give `promoter` more surfaces in its `role:`, or add a
  second distribution agent with its own Friday `pings:` rule.
- **Mind the time zone.** Cron is evaluated in the **host's local time** (zero
  deps, no tz database). If the machine runs UTC, `9` means 09:00 UTC — set the
  hours to match the wall clock you actually want.

---

### See also

- [`ops-watchtower.md`](./ops-watchtower.md) — the opposite end of the spectrum:
  high-frequency `*/N` monitoring with `when_busy: queue`.
- [`scheduled-standup.md`](./scheduled-standup.md) — a self-running standup; the
  gentle introduction to multi-agent `pings:`.
- [`daily-briefing.md`](./daily-briefing.md) — the flagship `pings:` showcase (a
  weekday-morning + weekend digest schedule).
- [`../configuration.md`](../configuration.md#pings) — the full `pings:` field
  reference and cron syntax table.
- `examples/content-cadence.yaml` — the config this walkthrough is built from.

# Use case: the self-driving ops watchtower

A concrete walkthrough of the shipped `examples/ops-watchtower.yaml` swarm — the
**heaviest `pings:` showcase** in the set. Where the other scheduled examples
post a digest once or twice a day, this one is built to *watch*: it sweeps every
15 minutes during business hours, checks in hourly overnight, rolls up what it
saw each morning, and reviews the week on Monday — all on its own clock, with no
human `send`. It's the example to read when you want the two cron features the
others only touch lightly: **high-frequency `*/N` scheduling** and the
**`when_busy: queue`** policy for ticks you can't afford to drop.

Everything below is based on the actual contents of
`examples/ops-watchtower.yaml`, the shipped config loader (`lib/config.py`), and
the cron parser (`lib/cron.py`). No API keys are needed to understand the
mechanics; to run it *for real* you supply the coding-CLI commands (or swap them
for mock bash loops).

> New to the mail model? Read [`getting-started.md`](../getting-started.md) first,
> then the four-folders recap in the repo `README.md`. The one-line version: an
> agent **reads a file** to receive mail and **writes a file** to send it; the
> orchestrator owns all routing, ACL, IDs, and state.

---

## 1. The idea: monitoring is a schedule, not a request

A monitoring system doesn't wait to be asked — it runs on a cadence and speaks up
only when something's wrong. That maps exactly onto `pings:`: each agent carries
a list of cron-scheduled nudges, and each nudge is a `message` delivered as a
`system` mail at a wall-clock time. The orchestrator owns the clock; the model
just reads the message it's handed and acts.

The watchtower uses **three different cadences on one hub**, which is the whole
lesson:

- a **fast** weekday-daytime sweep that must *not* stack (`skip`),
- a **slower** overnight sweep where every tick matters (`queue`),
- a **once-a-day** rollup and a **once-a-week** review.

Plus a spoke (`prober`) with a schedule of its own. Nobody types anything; the
swarm self-drives and stays quiet until there's a real reason to page you.

---

## 2. The topology

```
   prober ────┐
              ├── sentinel ── user   (posts alerts, rollups, the weekly review)
 diagnoser ──┘
```

Hub and spoke, enforced by `can_talk_to`:

- `sentinel` may talk to `prober`, `diagnoser`, and `user`.
- `prober` and `diagnoser` may talk **only** to `sentinel`.

So the sentinel is the single voice that reaches you — it's the *filter*, not a
firehose. A spoke that tried to page `user` directly would be bounced by the
ACL, with a `system` note explaining who it can reach. Routine "all green"
sweeps never leave the swarm.

---

## 3. The schedules, explained

Each `pings` entry is a **`message`** (delivered as `system` mail), a **`cron`**
(standard 5-field `minute hour day-of-month month day-of-week`, evaluated in the
host's **local** time), and an optional **`when_busy`** (`skip`, the default, or
`queue`).

### `sentinel` — four rules, three cadences

```yaml
    pings:
      - message: |
          Health sweep. If you are idle, ask prober for the current status ...
        cron: "*/15 9-17 * * 1-5"        # every 15 min, 09:00-17:45, Mon-Fri
      - message: |
          Overnight health check. Ask prober for status. Page user ONLY if ...
        cron: "0 18-23,0-7 * * *"        # hourly, 18:00-07:59, every day
        when_busy: queue
      - message: |
          Overnight rollup. Summarize what prober and diagnoser saw ...
        cron: "0 8 * * 1-5"              # 08:00, Mon-Fri
        when_busy: queue
      - message: |
          Weekly reliability review. Ask diagnoser for the recurring issues ...
        cron: "30 8 * * mon"             # 08:30 every Monday
```

- **`*/15 9-17 * * 1-5`** — the `*/15` **step** in the minute field means *every
  15 minutes* (:00, :15, :30, :45), across hours 09–17, Monday–Friday. This is
  the high-frequency sweep the other examples don't demonstrate. It uses the
  default **`skip`**: if the sentinel is still working a prior alert when the
  next quarter-hour comes due, that tick is **dropped** rather than stacking a
  second sweep behind the first. For monitoring, the *freshest* sweep is the one
  that matters — you never want a backlog of stale "please check" nudges.
- **`0 18-23,0-7 * * *`** — hourly overnight. The **comma list** `18-23,0-7` in
  the hour field is how you express a window that *crosses midnight*: 18:00–23:00
  **or** 00:00–07:00. (Cron ranges must ascend, so `18-7` is invalid — always
  split an overnight span into two ascending pieces.) This rule is
  **`when_busy: queue`**: off-hours ticks are sparse and each one matters, so a
  check that comes due while the sentinel is still writing up the last incident
  **waits** and fires when it's free, instead of being lost.
- **`0 8 * * 1-5`** — the 08:00 weekday rollup, also **`queue`** so a long-running
  overnight check can't cause the morning summary to be skipped.
- **`30 8 * * mon`** — 08:30 Monday, the weekly reliability review. A look-back,
  not an alert.

### `prober` — one rule of its own

```yaml
    pings:
      - message: |
          Nightly deep check: run the fuller synthetic probe ...
        cron: "0 3 * * *"               # 03:00 every day
        when_busy: queue
```

The spoke carries its own **03:00 nightly deep check** — a heavier synthetic
probe that only makes sense in the quiet of the night. Different agent, different
cadence, and `queue` so the deep run is never skipped. `diagnoser` has **no**
schedule at all: it's purely reactive, working only when the sentinel hands it a
real symptom.

### `skip` vs. `queue`, side by side

This example is the clearest place to see the trade-off:

| Rule | Policy | Why |
| --- | --- | --- |
| `*/15` daytime sweep | `skip` | High frequency; a dropped tick is replaced by the next one 15 min later. Never build a backlog. |
| Hourly overnight | `queue` | Sparse; each check is the only eyes on the system for that hour — don't lose it. |
| Morning rollup | `queue` | Must run once; a slow check shouldn't cancel the day's summary. |
| Weekly review | `skip` | Low stakes if a single one slips; not worth stacking. |

The rule of thumb: **`skip` when the next tick makes a missed one moot; `queue`
when the tick is the point.**

### The cron rule that bites

The parser (`lib/cron.py`) handles `*`, `*/step`, `a-b`, `a-b/step`, comma lists,
and 3-letter month/day names; day-of-week `0` and `7` are both Sunday; the Vixie
dom/dow OR rule applies. **Cron is validated at config load** — an invalid
expression makes `up` (and `validate`) fail outright. Every field must be in
range and every range must **ascend**: to express an overnight window, use a
comma list in the hour field like `18-23,0-7`, never a descending `18-7`.

---

## 4. Run it

From the repo root:

```bash
./agentainer up             -c examples/ops-watchtower.yaml
./agentainer user available -c examples/ops-watchtower.yaml   # so alerts reach you
```

Then leave it running. On `up`, Agentainer loads the config (validating every
cron string), initializes the mailboxes and ACL-derived `outbox/<peer>/` folders,
installs per-type turn detection (Claude Stop hook, Codex `notify`), opens a tmux
session per agent, and starts the liveness supervisor. From then on the
**`pings:` schedules fire on their own**: `:00/:15/:30/:45` sweeps through the
workday, an hourly check overnight, an 08:00 rollup, the 03:00 deep probe, and
the Monday review.

Watch it happen in the durable log (a `ping` event is logged each time a rule
fires):

```bash
./agentainer logs -c examples/ops-watchtower.yaml -f
```

> **Key-free demo:** swap each `command:` for a mock bash loop and the swarm
> still comes up and fires its scheduled pings with **no API keys** — the cron
> mechanics are identical. To see a sweep *now* without waiting for the clock,
> temporarily set a rule's `cron` to `"* * * * *"` (every minute).

---

## 5. Customize

- **Tune the sweep frequency.** `*/15` is every 15 minutes; use `*/5` for a
  tighter watch or `0,30` for every half hour. Widen the workday window by
  editing the hour range (`9-17` → `8-20`).
- **Reshape the overnight window.** It's just the comma list in the hour field.
  A 22:00–06:00 window is `22-23,0-6`; 24/7 hourly is simply `0 * * * *`.
- **Change what pages you.** The escalation policy lives in the `role:` text
  ("page user ONLY if…"), not the cron — tighten or loosen it there so routine
  checks stay silent and only real regressions reach you.
- **Add a watched surface.** Give `prober` more to collect in its `role:`, or add
  a second prober with its own `pings:` cadence for a different subsystem.
- **Mind the time zone.** Cron is evaluated in the **host's local time** (zero
  deps, no tz database). If the machine runs UTC, `9` means 09:00 UTC — set the
  hours to match the wall clock you actually want.

---

### See also

- [`scheduled-standup.md`](./scheduled-standup.md) — a self-running standup; the
  gentle introduction to multi-agent `pings:`.
- [`daily-briefing.md`](./daily-briefing.md) — the flagship `pings:` showcase (a
  weekday-morning + weekend digest schedule).
- [`content-cadence.md`](./content-cadence.md) — `pings:` as a *weekly calendar*
  (day-of-week and day-of-month scheduling) rather than an intraday watch.
- [`../configuration.md`](../configuration.md#pings) — the full `pings:` field
  reference and cron syntax table.
- `examples/ops-watchtower.yaml` — the config this walkthrough is built from.

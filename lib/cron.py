#!/usr/bin/env python3
"""Agentainer -- a tiny, zero-dependency cron parser for per-agent ping schedules.

Just enough of standard 5-field cron (``minute hour day-of-month month
day-of-week``) to schedule pings against local wall-clock time. Supports ``*``,
``*/step``, ``a``, ``a-b``, ``a-b/step``, comma lists, and 3-letter month/day
names (``jan``..``dec``, ``sun``..``sat``). Day-of-week ``0`` and ``7`` are both
Sunday.

Day-of-month vs day-of-week follows the standard Vixie-cron rule: when BOTH are
restricted (neither is ``*``) a time matches if EITHER matches; when only one is
restricted, only that one must match. Evaluation uses the host's LOCAL time --
this is deliberate (zero deps, no tz database); schedules are in server-local
time and that is documented for the operator.

No external deps: this is the scheduling core, kept small so it stays at 100%
line coverage with the rest of ``lib/``.
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass


class CronError(ValueError):
    """Raised for a malformed cron expression (bad field count, value, range)."""


# 1-based month names and 0-based (Sunday=0) day-of-week names.
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_DOWS = {d: i for i, d in enumerate(
    ["sun", "mon", "tue", "wed", "thu", "fri", "sat"], start=0)}


@dataclass(frozen=True)
class Cron:
    """A parsed cron expression: each field pre-expanded to the set it matches."""

    minutes: frozenset
    hours: frozenset
    doms: frozenset
    months: frozenset
    dows: frozenset
    dom_restricted: bool
    dow_restricted: bool
    source: str


def _name_or_int(tok: str, names) -> int:
    """Resolve a single token to an int, accepting month/day names when given."""
    t = tok.strip().lower()
    if names and t in names:
        return names[t]
    if not t.isdigit():
        raise CronError(f"bad value {tok!r}")
    return int(t)


def _parse_field(expr: str, lo: int, hi: int, names=None) -> set:
    """Expand one cron field into the concrete set of ints it matches."""
    expr = expr.strip()
    if expr == "":
        raise CronError("empty field")
    out: set = set()
    for part in expr.split(","):
        part = part.strip()
        if not part:
            raise CronError(f"empty item in {expr!r}")
        base, sep, stepstr = part.partition("/")
        if sep:
            if not stepstr.isdigit() or int(stepstr) == 0:
                raise CronError(f"bad step in {part!r}")
            step = int(stepstr)
        else:
            step = 1
        base = base.strip()
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, _, b = base.partition("-")
            start = _name_or_int(a, names)
            end = _name_or_int(b, names)
        else:
            start = end = _name_or_int(base, names)
        if start < lo or end > hi or start > end:
            raise CronError(f"range {start}-{end} out of bounds {lo}-{hi}")
        out.update(range(start, end + 1, step))
    return out


def parse(expr: str) -> Cron:
    """Parse a 5-field cron string into a :class:`Cron`, or raise ``CronError``."""
    if not isinstance(expr, str):
        raise CronError("cron must be a string")
    fields = expr.split()
    if len(fields) != 5:
        raise CronError(f"cron needs 5 fields, got {len(fields)}: {expr!r}")
    mn, hr, dom, mon, dow = fields
    dows_raw = _parse_field(dow, 0, 7, _DOWS)
    # Fold Sunday-as-7 into Sunday-as-0 so downstream comparison is uniform.
    dows = frozenset(0 if v == 7 else v for v in dows_raw)
    return Cron(
        minutes=frozenset(_parse_field(mn, 0, 59)),
        hours=frozenset(_parse_field(hr, 0, 23)),
        doms=frozenset(_parse_field(dom, 1, 31)),
        months=frozenset(_parse_field(mon, 1, 12, _MONTHS)),
        dows=dows,
        dom_restricted=dom.strip() != "*",
        dow_restricted=dow.strip() != "*",
        source=expr,
    )


def matches(cron: Cron, t=None) -> bool:
    """True iff *t* (a ``time.struct_time``; local now if omitted) fires *cron*."""
    if t is None:
        t = _time.localtime()
    if t.tm_min not in cron.minutes:
        return False
    if t.tm_hour not in cron.hours:
        return False
    if t.tm_mon not in cron.months:
        return False
    dom_ok = t.tm_mday in cron.doms
    # Python's tm_wday is Mon=0..Sun=6; cron's is Sun=0..Sat=6.
    cron_wday = (t.tm_wday + 1) % 7
    dow_ok = cron_wday in cron.dows
    if cron.dom_restricted and cron.dow_restricted:
        return dom_ok or dow_ok
    if cron.dom_restricted:
        return dom_ok
    if cron.dow_restricted:
        return dow_ok
    return True

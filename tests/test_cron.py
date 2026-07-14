#!/usr/bin/env python3
"""100% line coverage of lib/cron.py -- the zero-dep cron parser for ping schedules.

Every field form (`*`, step, range, list, names), the day-of-month/day-of-week
OR rule, and every error branch are exercised directly. `matches` is driven with
explicit `time.struct_time` values so the tests are timezone- and clock-stable.
"""

import time

import pytest

import cron


def st(year, mon, mday, hour, minute, wday):
    """A struct_time with the fields cron.matches reads (wday: Mon=0..Sun=6)."""
    return time.struct_time((year, mon, mday, hour, minute, 0, wday, 1, -1))


# -- parsing forms ----------------------------------------------------------

def test_parse_star_matches_everything():
    c = cron.parse("* * * * *")
    assert cron.matches(c, st(2024, 6, 10, 13, 37, 0)) is True


def test_parse_single_values():
    c = cron.parse("30 9 * * *")
    assert cron.matches(c, st(2024, 6, 10, 9, 30, 0)) is True
    assert cron.matches(c, st(2024, 6, 10, 9, 31, 0)) is False   # minute miss
    assert cron.matches(c, st(2024, 6, 10, 8, 30, 0)) is False   # hour miss


def test_parse_step_and_range():
    c = cron.parse("*/30 9-18 * * *")
    assert cron.matches(c, st(2024, 6, 10, 9, 0, 0)) is True
    assert cron.matches(c, st(2024, 6, 10, 9, 30, 0)) is True
    assert cron.matches(c, st(2024, 6, 10, 9, 15, 0)) is False
    assert cron.matches(c, st(2024, 6, 10, 19, 0, 0)) is False   # hour out of range


def test_parse_range_with_step():
    c = cron.parse("0-10/5 * * * *")
    assert cron.matches(c, st(2024, 1, 1, 0, 5, 0)) is True
    assert cron.matches(c, st(2024, 1, 1, 0, 6, 0)) is False


def test_parse_comma_list():
    c = cron.parse("0 12 * * 0,6")   # noon on Sun/Sat
    assert cron.matches(c, st(2024, 1, 6, 12, 0, 5)) is True    # Saturday
    assert cron.matches(c, st(2024, 1, 1, 12, 0, 0)) is False   # Monday


def test_parse_month_and_dow_names():
    c = cron.parse("0 9 * jan mon-fri")
    assert cron.matches(c, st(2024, 1, 1, 9, 0, 0)) is True     # Mon, January
    assert cron.matches(c, st(2024, 2, 1, 9, 0, 3)) is False    # February -> month miss


def test_dow_seven_is_sunday():
    c = cron.parse("0 0 * * 7")
    assert cron.matches(c, st(2024, 1, 7, 0, 0, 6)) is True     # Sunday (wday 6)
    assert cron.matches(c, st(2024, 1, 8, 0, 0, 0)) is False    # Monday


# -- day-of-month vs day-of-week OR semantics -------------------------------

def test_dom_and_dow_both_restricted_is_or():
    c = cron.parse("0 0 13 * fri")   # the 13th OR any Friday
    assert cron.matches(c, st(2024, 1, 13, 0, 0, 5)) is True    # 13th (a Saturday)
    assert cron.matches(c, st(2024, 1, 5, 0, 0, 4)) is True     # a Friday (not 13th)
    assert cron.matches(c, st(2024, 1, 6, 0, 0, 5)) is False    # neither


def test_only_dom_restricted():
    c = cron.parse("0 0 15 * *")
    assert cron.matches(c, st(2024, 1, 15, 0, 0, 0)) is True
    assert cron.matches(c, st(2024, 1, 16, 0, 0, 1)) is False


def test_only_dow_restricted():
    c = cron.parse("0 0 * * mon")
    assert cron.matches(c, st(2024, 1, 1, 0, 0, 0)) is True     # Monday
    assert cron.matches(c, st(2024, 1, 2, 0, 0, 1)) is False


def test_month_miss():
    c = cron.parse("0 0 * 6 *")
    assert cron.matches(c, st(2024, 5, 1, 0, 0, 2)) is False


def test_matches_defaults_to_local_now(monkeypatch):
    fixed = st(2024, 1, 1, 0, 0, 0)
    monkeypatch.setattr(cron._time, "localtime", lambda *a: fixed)
    assert cron.matches(cron.parse("* * * * *")) is True


# -- error branches ---------------------------------------------------------

def test_parse_non_string():
    with pytest.raises(cron.CronError):
        cron.parse(12345)


def test_parse_wrong_field_count():
    with pytest.raises(cron.CronError):
        cron.parse("* * * *")


def test_parse_empty_field():
    # split() collapses whitespace, so force an empty field via a comma item.
    with pytest.raises(cron.CronError):
        cron.parse("0, * * * *")


def test_parse_bad_step():
    with pytest.raises(cron.CronError):
        cron.parse("*/0 * * * *")
    with pytest.raises(cron.CronError):
        cron.parse("*/x * * * *")


def test_parse_bad_value():
    with pytest.raises(cron.CronError):
        cron.parse("x * * * *")


def test_parse_out_of_range():
    with pytest.raises(cron.CronError):
        cron.parse("60 * * * *")     # minute > 59
    with pytest.raises(cron.CronError):
        cron.parse("* 25 * * *")     # hour > 23


def test_parse_inverted_range():
    with pytest.raises(cron.CronError):
        cron.parse("10-5 * * * *")


def test_parse_field_empty_string_directly():
    with pytest.raises(cron.CronError):
        cron._parse_field("   ", 0, 59)

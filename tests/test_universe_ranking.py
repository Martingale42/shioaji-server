"""Unit tests for ``scripts.universe_ranking`` pure logic.

Synthetic data only, no network. Tests pin actual numeric behavior (exact share
counts, exact market-cap ordering, exact interval boundaries), not tautologies.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from scripts.universe_ranking import (
    build_union_and_membership,
    compute_market_cap,
    daily_top_n,
    reconstruct_daily_shares,
)


# --- reconstruct_daily_shares -------------------------------------------------
def test_reconstruct_applies_event_backward() -> None:
    days = [date(2025, 6, 2), date(2025, 6, 3), date(2025, 6, 4)]
    current = pl.DataFrame({"code": ["A"], "name": ["a"], "shares": [1100]})
    events = pl.DataFrame(
        {
            "code": ["A"],
            "date": [date(2025, 6, 4)],
            "shares_delta": [100],
            "kind": ["stock_dividend"],
        }
    )
    out = reconstruct_daily_shares(current, events, days)
    # before the 06-04 event: 1100 - 100 = 1000; on/after: 1100
    got = {r["date"]: r["shares"] for r in out.filter(pl.col("code") == "A").to_dicts()}
    assert got[date(2025, 6, 2)] == 1000
    assert got[date(2025, 6, 3)] == 1000
    assert got[date(2025, 6, 4)] == 1100


def test_reconstruct_no_event_is_constant() -> None:
    days = [date(2025, 6, 2), date(2025, 6, 3)]
    current = pl.DataFrame({"code": ["Z"], "name": ["z"], "shares": [5000]})
    events = pl.DataFrame(
        schema={
            "code": pl.Utf8,
            "date": pl.Date,
            "shares_delta": pl.Int64,
            "kind": pl.Utf8,
        }
    )
    out = reconstruct_daily_shares(current, events, days)
    assert out.select("shares").to_series().to_list() == [5000, 5000]


def test_reconstruct_two_events_accumulate_backward() -> None:
    # Two future events relative to the earliest day must BOTH be subtracted.
    days = [date(2025, 6, 1), date(2025, 6, 10), date(2025, 6, 20)]
    current = pl.DataFrame({"code": ["A"], "name": ["a"], "shares": [1000]})
    events = pl.DataFrame(
        {
            "code": ["A", "A"],
            "date": [date(2025, 6, 10), date(2025, 6, 20)],
            "shares_delta": [100, 50],
            "kind": ["dividend", "dividend"],
        }
    )
    out = reconstruct_daily_shares(current, events, days)
    got = {r["date"]: r["shares"] for r in out.to_dicts()}
    # 06-01: both events future => 1000 - 100 - 50 = 850
    assert got[date(2025, 6, 1)] == 850
    # 06-10: only the 06-20 event is future => 1000 - 50 = 950 (event-day is on/after)
    assert got[date(2025, 6, 10)] == 950
    # 06-20: no future events => full anchor 1000
    assert got[date(2025, 6, 20)] == 1000


def test_reconstruct_capital_decrease_negative_delta() -> None:
    # A capital REDUCTION has shares_delta < 0; pre-event shares are LARGER.
    days = [date(2025, 6, 1), date(2025, 6, 5)]
    current = pl.DataFrame({"code": ["A"], "name": ["a"], "shares": [800]})
    events = pl.DataFrame(
        {
            "code": ["A"],
            "date": [date(2025, 6, 5)],
            "shares_delta": [-200],
            "kind": ["capital_reduction"],
        }
    )
    out = reconstruct_daily_shares(current, events, days)
    got = {r["date"]: r["shares"] for r in out.to_dicts()}
    # before the reduction: 800 - (-200) = 1000; on/after: 800
    assert got[date(2025, 6, 1)] == 1000
    assert got[date(2025, 6, 5)] == 800


# --- compute_market_cap -------------------------------------------------------
def test_market_cap_product_and_inner_join() -> None:
    close = pl.DataFrame(
        {
            "code": ["A", "A", "B"],
            "date": [date(2025, 6, 2), date(2025, 6, 3), date(2025, 6, 2)],
            "close": [10.0, 11.0, 5.0],
        }
    )
    shares = pl.DataFrame(
        {
            "code": ["A", "A", "B"],
            "date": [date(2025, 6, 2), date(2025, 6, 3), date(2025, 6, 3)],
            "shares": [100, 100, 200],
        }
    )
    out = compute_market_cap(close, shares)
    got = {(r["code"], r["date"]): r["mktcap"] for r in out.to_dicts()}
    assert got[("A", date(2025, 6, 2))] == 1000.0
    assert got[("A", date(2025, 6, 3))] == 1100.0
    # B has close only on 06-02 and shares only on 06-03 => no inner-join row.
    assert ("B", date(2025, 6, 2)) not in got
    assert ("B", date(2025, 6, 3)) not in got
    assert out.height == 2


# --- daily_top_n --------------------------------------------------------------
def test_daily_top_n_picks_largest() -> None:
    mc = pl.DataFrame(
        {
            "date": [date(2025, 6, 2)] * 3,
            "code": ["A", "B", "C"],
            "mktcap": [9.0, 1.0, 8.0],
        }
    )
    top = daily_top_n(mc, n=2)
    assert set(top.select("code").to_series().to_list()) == {"A", "C"}


def test_daily_top_n_fewer_than_n_returns_all() -> None:
    # A day with only 2 codes but n=5 returns both, no padding, no error.
    mc = pl.DataFrame(
        {
            "date": [date(2025, 6, 2), date(2025, 6, 2)],
            "code": ["A", "B"],
            "mktcap": [9.0, 8.0],
        }
    )
    top = daily_top_n(mc, n=5)
    assert sorted(top.select("code").to_series().to_list()) == ["A", "B"]
    assert top.height == 2


def test_daily_top_n_tie_broken_by_code() -> None:
    # Equal market caps: deterministic tie-break by code ascending.
    mc = pl.DataFrame(
        {
            "date": [date(2025, 6, 2)] * 3,
            "code": ["C", "A", "B"],
            "mktcap": [5.0, 5.0, 5.0],
        }
    )
    top = daily_top_n(mc, n=2)
    assert sorted(top.select("code").to_series().to_list()) == ["A", "B"]


# --- build_union_and_membership ----------------------------------------------
def test_top_n_and_union_intervals() -> None:
    # B is top-2 only on day 1+2, drops day 3 -> interval [d1, d3); C enters day 3.
    mc = pl.DataFrame(
        {
            "date": [date(2025, 6, 2)] * 3
            + [date(2025, 6, 3)] * 3
            + [date(2025, 6, 4)] * 3,
            "code": ["A", "B", "C"] * 3,
            "mktcap": [9, 8, 1, 9, 8, 1, 9, 1, 8],
        }
    )
    top = daily_top_n(mc, n=2)
    union, mem = build_union_and_membership(top, {"A": "a", "B": "b", "C": "c"})
    assert set(union) == {"A", "B", "C"}
    b = mem.filter(pl.col("code") == "B").to_dicts()[0]
    assert b["effective_from"] == date(2025, 6, 2) and b["effective_to"] == date(
        2025, 6, 4
    )


def test_member_through_last_day_has_blank_effective_to() -> None:
    mc = pl.DataFrame(
        {
            "date": [date(2025, 6, 2), date(2025, 6, 3)],
            "code": ["A", "A"],
            "mktcap": [9.0, 9.0],
        }
    )
    top = daily_top_n(mc, n=1)
    _union, mem = build_union_and_membership(top, {"A": "a"})
    a = mem.filter(pl.col("code") == "A").to_dicts()[0]
    assert a["effective_from"] == date(2025, 6, 2)
    assert a["effective_to"] is None  # still a member as of the latest trading day


def test_enter_leave_reenter_splits_into_two_intervals() -> None:
    # 4 trading days. B is top-1 on d1, d2; out on d3; back in on d4.
    # Expect TWO intervals: [d1, d3) and [d4, blank).
    days = [date(2025, 6, 2), date(2025, 6, 3), date(2025, 6, 4), date(2025, 6, 5)]
    mc = pl.DataFrame(
        {
            "date": [days[0]] * 2 + [days[1]] * 2 + [days[2]] * 2 + [days[3]] * 2,
            "code": ["B", "A", "B", "A", "A", "X", "B", "A"],
            "mktcap": [9, 1, 9, 1, 9, 1, 9, 1],
        }
    )
    top = daily_top_n(mc, n=1)  # n=1 -> only the single largest each day
    _union, mem = build_union_and_membership(top, {"A": "a", "B": "b", "X": "x"})
    b_rows = sorted(
        mem.filter(pl.col("code") == "B").to_dicts(), key=lambda r: r["effective_from"]
    )
    assert len(b_rows) == 2
    assert b_rows[0]["effective_from"] == days[0]
    assert b_rows[0]["effective_to"] == days[2]  # exclusive: first day B absent
    assert b_rows[1]["effective_from"] == days[3]
    assert b_rows[1]["effective_to"] is None  # re-entered and still in on last day


def test_gap_in_global_calendar_does_not_falsely_split() -> None:
    # If a code is present on every day it COULD be (the only days that exist),
    # it must be one interval even when those days are not calendar-adjacent.
    days = [date(2025, 6, 2), date(2025, 6, 10), date(2025, 6, 20)]
    mc = pl.DataFrame(
        {
            "date": days,
            "code": ["A", "A", "A"],
            "mktcap": [9.0, 9.0, 9.0],
        }
    )
    top = daily_top_n(mc, n=1)
    _union, mem = build_union_and_membership(top, {"A": "a"})
    a_rows = mem.filter(pl.col("code") == "A").to_dicts()
    # All three are consecutive *global trading days* -> single interval.
    assert len(a_rows) == 1
    assert a_rows[0]["effective_from"] == days[0]
    assert a_rows[0]["effective_to"] is None


def test_end_to_end_capital_event_changes_ranking() -> None:
    # A capital event flips ranking pre/post: stock B's pre-event share count is
    # lower, so it ranks below C early, then above C after its shares jump.
    days = [date(2025, 6, 2), date(2025, 6, 3)]
    current = pl.DataFrame(
        {"code": ["B", "C"], "name": ["b", "c"], "shares": [200, 150]}
    )
    # B gets +120 shares effective 06-03 (so pre-event B = 80 shares).
    events = pl.DataFrame(
        {
            "code": ["B"],
            "date": [date(2025, 6, 3)],
            "shares_delta": [120],
            "kind": ["stock_dividend"],
        }
    )
    shares = reconstruct_daily_shares(current, events, days)
    # Sanity: B pre-event = 200 - 120 = 80; on event day = 200.
    b_shares = {
        r["date"]: r["shares"] for r in shares.filter(pl.col("code") == "B").to_dicts()
    }
    assert b_shares[date(2025, 6, 2)] == 80
    assert b_shares[date(2025, 6, 3)] == 200

    close = pl.DataFrame(
        {
            "code": ["B", "C", "B", "C"],
            "date": [days[0], days[0], days[1], days[1]],
            "close": [1.0, 1.0, 1.0, 1.0],
        }
    )
    mc = compute_market_cap(close, shares)
    top = daily_top_n(mc, n=1)
    by_day = {r["date"]: r["code"] for r in top.to_dicts()}
    # Day 1: C (150) > B (80) -> C is top-1.
    assert by_day[days[0]] == "C"
    # Day 2: B (200) > C (150) -> B is top-1.
    assert by_day[days[1]] == "B"

"""Unit tests for ``scripts.twse_tpex_market`` parsing + cache-robustness logic.

Synthetic data only, NO network. These tests pin numeric parsing behavior
(missing-close sentinels, capital-event delta matching) and cache-validity
helpers in isolation so the network layer's robustness invariants are checked
without hitting any official endpoint.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from scripts.twse_tpex_market import (
    _is_cached_parquet_valid,
    _match_deltas_to_ex_dates,
    _to_float,
)


# --- F5/F10: _to_float missing-close sentinels --------------------------------
def test_to_float_blank_is_none() -> None:
    # A blank cell must NOT become a real 0.0 close (would fabricate zero mktcap).
    assert _to_float("") is None


def test_to_float_zero_string_is_none() -> None:
    # "0.00" is the no-trade/suspended sentinel, not a real price.
    assert _to_float("0.00") is None


def test_to_float_dashes_are_none() -> None:
    assert _to_float("--") is None
    assert _to_float("---") is None


def test_to_float_parses_comma_number() -> None:
    assert _to_float("1,234.50") == 1234.5


def test_to_float_passes_through_numeric() -> None:
    assert _to_float(967.0) == 967.0
    assert _to_float(None) is None


# --- F2: cache validity helper (in isolation, no network) ---------------------
def test_zero_byte_parquet_is_invalid(tmp_path) -> None:
    # The PROVEN 04:49 crash mode: a 0-byte parquet left by a killed mid-write.
    p = tmp_path / "zero.parquet"
    p.write_bytes(b"")
    assert _is_cached_parquet_valid(p) is False


def test_truncated_parquet_is_invalid(tmp_path) -> None:
    # Non-empty but unparseable garbage must also be rejected.
    p = tmp_path / "garbage.parquet"
    p.write_bytes(b"not a parquet file")
    assert _is_cached_parquet_valid(p) is False


def test_missing_parquet_is_invalid(tmp_path) -> None:
    assert _is_cached_parquet_valid(tmp_path / "absent.parquet") is False


def test_well_formed_parquet_is_valid(tmp_path) -> None:
    p = tmp_path / "ok.parquet"
    pl.DataFrame({"a": [1, 2, 3]}).write_parquet(p)
    assert _is_cached_parquet_valid(p) is True


def test_empty_schema_only_parquet_is_valid(tmp_path) -> None:
    # A legitimately-empty (0-row) frame is still a valid, parseable parquet.
    p = tmp_path / "empty.parquet"
    pl.DataFrame(schema={"a": pl.Int64}).write_parquet(p)
    assert _is_cached_parquet_valid(p) is True


# --- F3: per-fiscal-year delta attribution ------------------------------------
def test_two_in_window_ex_dates_get_their_own_fiscal_year_delta() -> None:
    # A code with two in-window ex-dates in DIFFERENT fiscal years (a 2025 and a
    # 2026 stock dividend) must each receive ITS fiscal year's delta, not the
    # year-summed total at both.
    ex_events = [
        {"code": "1234", "date": date(2025, 7, 22), "kind": "權息", "fiscal_year": 113},
        {"code": "1234", "date": date(2026, 7, 21), "kind": "權息", "fiscal_year": 114},
    ]
    deltas_by_code_year = {("1234", 113): 100, ("1234", 114): 50}
    out = _match_deltas_to_ex_dates(ex_events, deltas_by_code_year)
    by_date = {r["date"]: r["shares_delta"] for r in out}
    # Each ex-date gets only its own fiscal-year delta (NOT 150 at both).
    assert by_date[date(2025, 7, 22)] == 100
    assert by_date[date(2026, 7, 21)] == 50


def test_two_same_fiscal_year_ex_dates_apply_annual_delta_once() -> None:
    # The MOPS delta is the ANNUAL total. A code with two ex-dates in the SAME
    # fiscal year (a real case: 2812 台中銀 ex 2025-08-13 and 2025-10-28) must
    # apply that total ONCE -- at the earliest ex-date -- not at both (which would
    # double-count the annual share increase under reconstruct_daily_shares).
    ex_events = [
        {"code": "2812", "date": date(2025, 10, 28), "kind": "權", "fiscal_year": 113},
        {"code": "2812", "date": date(2025, 8, 13), "kind": "權息", "fiscal_year": 113},
    ]
    deltas_by_code_year = {("2812", 113): 402_869_238}
    out = _match_deltas_to_ex_dates(ex_events, deltas_by_code_year)
    assert len(out) == 1  # exactly one event for the annual distribution
    assert out[0]["date"] == date(2025, 8, 13)  # the earliest ex-date
    assert out[0]["shares_delta"] == 402_869_238
    # The full annual delta is applied exactly once (no 2x over-subtraction).
    assert sum(r["shares_delta"] for r in out) == 402_869_238


def test_ex_date_with_no_matching_delta_is_dropped() -> None:
    # An ex-date whose fiscal year has no MOPS delta (or a zero delta) is skipped.
    ex_events = [
        {"code": "1234", "date": date(2025, 7, 22), "kind": "權息", "fiscal_year": 113},
        {"code": "5678", "date": date(2025, 8, 1), "kind": "權", "fiscal_year": 113},
    ]
    deltas_by_code_year = {("1234", 113): 100, ("5678", 113): 0}
    out = _match_deltas_to_ex_dates(ex_events, deltas_by_code_year)
    codes = {r["code"] for r in out}
    assert codes == {"1234"}  # 5678 has a zero delta -> dropped

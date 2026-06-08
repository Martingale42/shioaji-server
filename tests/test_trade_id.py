"""Uniqueness tests for the download-script TradeId construction.

The old microsecond ``strftime`` TradeId collided for trades that shared the
same microsecond (proven 36/1922 dupes on a single day). The nanosecond-integer
form (matching the sinopac HTTP adapter ``{code}-{ts_ns}``) is unique per
distinct nanosecond and aligns with the live HTTP path.
"""

from __future__ import annotations

from scripts.fetch_single_ticks import _ns_to_trade_id


def test_same_microsecond_different_ns_distinct_ids() -> None:
    """Two ticks in the same microsecond but different ns must NOT collide."""
    base_ns = 1_583_139_601_187_000_000  # 2020-03-02T01:00:01.187 (true UTC)
    a = _ns_to_trade_id("0050", base_ns)
    b = _ns_to_trade_id("0050", base_ns + 123)  # +123 ns, same microsecond

    assert a != b
    assert a.value == "0050-1583139601187000000"
    assert b.value == "0050-1583139601187000123"


def test_id_is_nanosecond_integer_suffix() -> None:
    tid = _ns_to_trade_id("2330", 1_718_348_400_000_000_000)
    code, _, suffix = tid.value.partition("-")
    assert code == "2330"
    assert suffix.isdigit()  # raw ns int, not a datetime string
    assert int(suffix) == 1_718_348_400_000_000_000


def test_distinct_codes_distinct_ids() -> None:
    ns = 1_718_348_400_000_000_000
    assert _ns_to_trade_id("0050", ns) != _ns_to_trade_id("2330", ns)

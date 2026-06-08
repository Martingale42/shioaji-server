"""Direction-locked tests for the gateway HTTP timezone correction.

The Shioaji SDK encodes Taiwan wall-clock time as a UTC nanosecond epoch, so a
trade that happened at TW 09:00 is stored as ``09:00 UTC`` (8h ahead of true
UTC). ``_to_utc_ns`` must shift it back to the true UTC moment (01:00 UTC).

These tests decode the result with ``datetime`` rather than only asserting an
arithmetic offset, so the *direction* of the shift is pinned: a regression that
ADDED 8h (wrong sign) would still satisfy ``== ts - const`` if written naively,
but it would NOT decode to 01:00 UTC.
"""

from __future__ import annotations

from datetime import datetime, timezone

from shioaji_server.routes.market_data import TW_UTC_OFFSET_NS, _to_utc_ns

# TW 09:00:00 on 2024-06-14, encoded as a UTC epoch the way the SDK does it.
_TW_0900_AS_UTC_NS = int(
    datetime(2024, 6, 14, 9, 0, 0, tzinfo=timezone.utc).timestamp() * 1e9
)


def _decode_utc(ns: int) -> datetime:
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)


def test_offset_constant_is_8_hours() -> None:
    assert TW_UTC_OFFSET_NS == 8 * 60 * 60 * 1_000_000_000


def test_tw_0900_as_utc_decodes_to_true_utc_0100() -> None:
    """TW 09:00 (stored as 09:00 UTC) must become 01:00 true UTC."""
    (result_ns,) = _to_utc_ns([_TW_0900_AS_UTC_NS])
    decoded = _decode_utc(result_ns)

    assert decoded == datetime(2024, 6, 14, 1, 0, 0, tzinfo=timezone.utc)
    # Direction guard: a wrong-sign regression would land at 17:00, not 01:00.
    assert decoded.hour == 1


def test_shift_magnitude_and_order_preserved() -> None:
    raw = [_TW_0900_AS_UTC_NS, _TW_0900_AS_UTC_NS + 1_000, _TW_0900_AS_UTC_NS + 5_000]
    shifted = _to_utc_ns(raw)

    assert len(shifted) == len(raw)
    assert all(s == r - TW_UTC_OFFSET_NS for s, r in zip(shifted, raw))
    # Relative ordering / spacing unchanged — only the absolute epoch moves.
    assert [s - shifted[0] for s in shifted] == [r - raw[0] for r in raw]


def test_empty_list_is_noop() -> None:
    assert _to_utc_ns([]) == []

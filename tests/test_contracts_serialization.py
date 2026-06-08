"""Unit tests for the gateway contract serializers.

Guards two coupled bugs that broke the downstream NautilusTrader instrument
builder:

1. Enum fields were emitted via ``str(enum)`` -> "OptionRight.Call" /
   "Currency.TWD" / "Exchange.TSE", which the adapter could not parse (options
   instruments failed wholesale). They must now carry the wire ``.value``
   ("C"/"P", "TWD", "TSE").
2. The authoritative instrument-construction fields (``unit``, ``multiplier``,
   ``currency``, ``underlying_code``) were absent from the serialized dict.

Tests build real Shioaji ``Stock``/``Future``/``Option`` contracts so the enum
types and field names match production exactly.
"""

from __future__ import annotations

from types import SimpleNamespace

from shioaji.constant import Currency, DayTrade, Exchange, OptionRight
from shioaji.contracts import Future, Option, Stock

from shioaji_server.routes.contracts import (
    _futures_to_dict,
    _options_to_dict,
    _stock_to_dict,
)


def _stock() -> Stock:
    return Stock(
        exchange=Exchange.TSE,
        code="2330",
        symbol="TSE2330",
        name="台積電",
        category="24",
        currency=Currency.TWD,
        unit=1000,
        multiplier=0,
        limit_up=1000.0,
        limit_down=800.0,
        reference=900.0,
        update_date="2026/06/08",
        day_trade=DayTrade.Yes,
    )


def _future() -> Future:
    return Future(
        exchange=Exchange.TAIFEX,
        code="TXFR1",
        symbol="TXF202606",
        name="台指期近月",
        category="TXF",
        currency=Currency.TWD,
        delivery_month="202606",
        delivery_date="2026/06/17",
        underlying_kind="I",
        underlying_code="TSE001",
        unit=1,
        multiplier=200,
        limit_up=22000.0,
        limit_down=18000.0,
        reference=20000.0,
        update_date="2026/06/08",
    )


def _option(right: OptionRight = OptionRight.Call) -> Option:
    return Option(
        exchange=Exchange.TAIFEX,
        code="TXO20000C6",
        symbol="TXO202606C20000",
        name="台指選擇權",
        category="TXO",
        currency=Currency.TWD,
        delivery_month="202606",
        delivery_date="2026/06/17",
        underlying_kind="I",
        underlying_code="TSE001",
        strike_price=20000,
        option_right=right,
        unit=1,
        multiplier=50,
        limit_up=2000.0,
        limit_down=0.0,
        reference=500.0,
        update_date="2026/06/08",
    )


# --- Stock ---


def test_stock_has_authoritative_fields():
    d = _stock_to_dict(_stock())
    assert d["unit"] == 1000
    assert d["multiplier"] == 0
    assert d["currency"] == "TWD"


def test_stock_currency_is_value_not_repr():
    d = _stock_to_dict(_stock())
    assert d["currency"] == "TWD"
    assert d["currency"] != "Currency.TWD"


def test_stock_day_trade_is_value():
    d = _stock_to_dict(_stock())
    assert d["day_trade"] == "Yes"
    assert d["day_trade"] != "DayTrade.Yes"


def test_stock_exchange_is_usable_code():
    d = _stock_to_dict(_stock())
    assert d["exchange"] == "TSE"
    assert d["exchange"] != "Exchange.TSE"


# --- Futures ---


def test_futures_has_authoritative_fields():
    d = _futures_to_dict(_future())
    assert d["unit"] == 1
    assert d["multiplier"] == 200
    assert d["currency"] == "TWD"
    assert d["underlying_code"] == "TSE001"


# --- Options ---


def test_options_right_call_is_C():
    d = _options_to_dict(_option(OptionRight.Call))
    assert d["option_right"] == "C"
    assert d["option_right"] != "OptionRight.Call"


def test_options_right_put_is_P():
    d = _options_to_dict(_option(OptionRight.Put))
    assert d["option_right"] == "P"
    assert d["option_right"] != "OptionRight.Put"


def test_options_has_authoritative_fields():
    d = _options_to_dict(_option())
    assert d["unit"] == 1
    assert d["multiplier"] == 50
    assert d["currency"] == "TWD"
    assert d["underlying_code"] == "TSE001"
    assert d["strike_price"] == 20000.0


# --- Defensive getattr (fields absent on some contract subtypes) ---


def test_serializers_tolerate_missing_optional_fields():
    """A minimal duck-typed contract lacking the new fields must not raise.

    Mirrors the StreamMultiContract path where odd subtypes may omit
    currency/unit/multiplier/underlying_code; defaults keep the dict shape.
    """
    bare = SimpleNamespace(
        code="9999",
        symbol="X9999",
        name="bare",
        exchange=Exchange.OTC,
        category="00",
        limit_up=1.0,
        limit_down=0.0,
        reference=0.5,
        update_date="2026/06/08",
    )
    d = _stock_to_dict(bare)
    assert d["currency"] == ""
    assert d["unit"] == 0
    assert d["multiplier"] == 0
    assert d["day_trade"] == ""

"""Unit tests for deal-event quantity normalization to shares.

These pin the assumed unit contract for stock deal events (design D1): a
common-lot ``StockDeal`` payload reports ``quantity`` in lots and must be
scaled x1000 to shares before broadcast, while odd-lot stock and
futures/options deals are already in their natural unit and pass through.

The unit is ASSUMED (the simulation engine produced no out-of-hours fills
during Batch 0.1); Task 4.1's intraday acceptance gate is the final arbiter.
See docs/notes/2026-06-11-sinopac-unit-verification.md §1.3.
"""

from __future__ import annotations

import pytest

from shioaji_server.session import _normalize_deal_quantity_to_shares

_STOCK_DEAL = "OrderState.StockDeal"
_STOCK_ORDER = "OrderState.StockOrder"
_FUTURES_DEAL = "OrderState.FuturesDeal"


def _stock_deal(order_lot: str, quantity: int) -> dict:
    # Flat StockDealEvent payload (top-level order_lot + quantity).
    return {
        "trade_id": "abc",
        "code": "2330",
        "action": "Buy",
        "order_cond": "Cash",
        "order_lot": order_lot,
        "price": 580.0,
        "quantity": quantity,
    }


def test_common_lot_deal_scaled_to_shares():
    msg = _stock_deal("Common", 1)
    out = _normalize_deal_quantity_to_shares(_STOCK_DEAL, msg)
    assert out["quantity"] == 1000


def test_common_lot_deal_multi_lot_scaled():
    out = _normalize_deal_quantity_to_shares(_STOCK_DEAL, _stock_deal("Common", 3))
    assert out["quantity"] == 3000


def test_odd_lot_deal_unchanged():
    out = _normalize_deal_quantity_to_shares(_STOCK_DEAL, _stock_deal("IntradayOdd", 37))
    assert out["quantity"] == 37


def test_odd_lot_after_hours_unchanged():
    out = _normalize_deal_quantity_to_shares(_STOCK_DEAL, _stock_deal("Odd", 100))
    assert out["quantity"] == 100


def test_non_deal_event_unchanged():
    """A StockOrder event (nested payload, no top-level order_lot) is untouched."""
    order_msg = {"order": {"order_lot": "Common", "quantity": 1}, "quantity": 1}
    out = _normalize_deal_quantity_to_shares(_STOCK_ORDER, order_msg)
    assert out is order_msg
    assert out["quantity"] == 1


def test_futures_deal_unchanged():
    """Futures deals carry contracts and lack a Common order_lot -> unchanged."""
    fut_deal = {"code": "TXFR1", "quantity": 2}
    out = _normalize_deal_quantity_to_shares(_FUTURES_DEAL, fut_deal)
    assert out["quantity"] == 2


def test_input_not_mutated():
    """Normalization returns a new dict; the source payload is preserved."""
    msg = _stock_deal("Common", 1)
    out = _normalize_deal_quantity_to_shares(_STOCK_DEAL, msg)
    assert msg["quantity"] == 1  # original untouched
    assert out is not msg


def test_missing_quantity_is_safe():
    msg = {"order_lot": "Common"}
    out = _normalize_deal_quantity_to_shares(_STOCK_DEAL, msg)
    assert out == {"order_lot": "Common"}


def test_non_dict_payload_passthrough():
    out = _normalize_deal_quantity_to_shares(_STOCK_DEAL, "not-a-dict")
    assert out == "not-a-dict"


@pytest.mark.parametrize("event_state", [_STOCK_ORDER, _FUTURES_DEAL, "OrderState.FuturesOrder"])
def test_only_stock_deal_state_triggers_scaling(event_state):
    msg = _stock_deal("Common", 1)
    out = _normalize_deal_quantity_to_shares(event_state, msg)
    assert out["quantity"] == 1

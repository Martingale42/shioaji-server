"""Unit tests for share/lot quantity conversion and venue-rejection handling.

The gateway owns all venue-unit knowledge (design D1): the HTTP wire unit is
shares, while Shioaji expects lots for common-lot stock orders. These tests
pin the shares->lots conversion at the SDK boundary, the multiple-of-1000
guard, and the synchronous venue-rejection -> 422 path, plus the shares
reporting and deal-aggregated ``filled_qty`` exposed by ``list_trades``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app():
    """Get the FastAPI app with a mocked ShioajiGatewaySession injected."""
    from shioaji_server.app import app as _app

    sj_mock = MagicMock()
    sj_mock.connected = True
    sj_mock.require_connected = MagicMock()
    _app.state.sj = sj_mock
    return _app


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=True)


def _wire_place(app, *, market: str, status: str = "OrderStatus.PendingSubmit"):
    """Wire the mocked SDK for a place_order call and return the captured api.Order mock."""
    sj = app.state.sj
    api = sj.api

    api.Contracts.Stocks.__getitem__ = MagicMock(return_value=MagicMock())
    api.Contracts.Futures.__getitem__ = MagicMock(return_value=MagicMock())
    api.Contracts.Options.__getitem__ = MagicMock(return_value=MagicMock())
    api.stock_account = MagicMock()
    api.futopt_account = MagicMock()

    api.Order = MagicMock(return_value=MagicMock())

    trade_result = MagicMock()
    trade_result.status.id = "trade-qty"
    trade_result.status.status = status
    api.place_order = MagicMock(return_value=trade_result)

    async def fake_run_sync(fn, *args):
        return fn(*args)

    sj.run_sync = fake_run_sync
    return api


# ---------------------------------------------------------------------------
# Task 1.2: shares -> lots conversion at the SDK boundary
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("order_lot", "request_qty", "expected_sdk_qty"),
    [
        ("Common", 1000, 1),
        ("Common", 2000, 2),
        ("IntradayOdd", 37, 37),
        ("Odd", 100, 100),
    ],
)
def test_stock_quantity_converted_to_lots(app, client, order_lot, request_qty, expected_sdk_qty):
    """Common-lot shares are floor-divided to lots; odd-lot stays in shares."""
    api = _wire_place(app, market="stock")

    resp = client.post(
        "/api/orders/place",
        json={
            "code": "2330",
            "action": "Buy",
            "price": 580.0,
            "quantity": request_qty,
            "order_lot": order_lot,
        },
    )
    assert resp.status_code == 200

    assert api.Order.call_args.kwargs["quantity"] == expected_sdk_qty


def test_common_lot_non_multiple_of_1000_is_422(app, client):
    """A common-lot quantity that is not a whole number of lots is rejected."""
    api = _wire_place(app, market="stock")

    resp = client.post(
        "/api/orders/place",
        json={
            "code": "2330",
            "action": "Buy",
            "price": 580.0,
            "quantity": 999,
            "order_lot": "Common",
        },
    )
    assert resp.status_code == 422
    assert "multiple of 1000" in resp.json()["detail"]
    api.Order.assert_not_called()


def test_futures_quantity_unchanged(app, client):
    """Futures quantity is contracts and passes through unconverted."""
    api = _wire_place(app, market="futures")

    resp = client.post(
        "/api/orders/place",
        json={
            "code": "TXFR1",
            "action": "Buy",
            "price": 20000.0,
            "quantity": 2,
            "market": "futures",
        },
    )
    assert resp.status_code == 200
    assert api.Order.call_args.kwargs["quantity"] == 2


# ---------------------------------------------------------------------------
# Task 1.2: synchronous venue rejection -> 422
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["OrderStatus.Failed", "OrderStatus.Inactive"])
def test_venue_rejected_status_is_422(app, client, status):
    """A trade returning a rejected status string is surfaced as HTTP 422."""
    _wire_place(app, market="stock", status=status)

    resp = client.post(
        "/api/orders/place",
        json={
            "code": "2330",
            "action": "Buy",
            "price": 1.0,
            "quantity": 1000,
            "order_lot": "Common",
        },
    )
    assert resp.status_code == 422
    assert "rejected by venue" in resp.json()["detail"]


def test_accepted_status_is_200(app, client):
    """A PendingSubmit trade (the sim accept state) returns 200."""
    _wire_place(app, market="stock", status="OrderStatus.PendingSubmit")

    resp = client.post(
        "/api/orders/place",
        json={
            "code": "2330",
            "action": "Buy",
            "price": 580.0,
            "quantity": 1000,
            "order_lot": "Common",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "OrderStatus.PendingSubmit"


# ---------------------------------------------------------------------------
# Task 1.3: list_trades reports shares and deal-aggregated filled_qty
# ---------------------------------------------------------------------------

def _make_deal(price: float, quantity: int):
    deal = MagicMock(spec=["price", "quantity"])
    deal.price = price
    deal.quantity = quantity
    return deal


def _make_trade(*, order_lot: str, order_qty: int, deals: list, deal_quantity: int = 0):
    trade = MagicMock()
    trade.status.id = "trade-rep"
    trade.contract.code = "2330"
    trade.order.action = "Buy"
    trade.order.price = 580.0
    trade.order.quantity = order_qty
    trade.order.order_lot = order_lot
    trade.order.order_type = "ROD"
    trade.order.price_type = "LMT"
    trade.order.custom_field = "tok"
    trade.status.status = "Filled"
    trade.status.deals = deals
    trade.status.deal_quantity = deal_quantity
    return trade


def _wire_list_trades(app, trades: list):
    sj = app.state.sj
    api = sj.api
    api.stock_account = MagicMock()
    api.futopt_account = None
    api.update_status = MagicMock()
    api.list_trades = MagicMock(return_value=trades)

    async def fake_run_sync(fn, *args):
        return fn(*args)

    sj.run_sync = fake_run_sync


def test_list_trades_common_lot_reports_shares_and_fills(app, client):
    """Common-lot trade reports quantity and filled_qty in shares plus VWAP."""
    trade = _make_trade(
        order_lot="Common",
        order_qty=2,
        deals=[_make_deal(580.0, 1)],
    )
    _wire_list_trades(app, [trade])

    resp = client.get("/api/orders/trades")
    assert resp.status_code == 200
    row = resp.json()[0]
    assert row["quantity"] == 2000
    assert row["filled_qty"] == 1000
    assert row["avg_fill_price"] == 580.0


def test_list_trades_common_lot_vwap_across_deals(app, client):
    """Multiple common-lot deals produce a volume-weighted average price."""
    trade = _make_trade(
        order_lot="Common",
        order_qty=3,
        deals=[_make_deal(580.0, 1), _make_deal(600.0, 2)],
    )
    _wire_list_trades(app, [trade])

    resp = client.get("/api/orders/trades")
    row = resp.json()[0]
    assert row["quantity"] == 3000
    assert row["filled_qty"] == 3000
    # (580*1 + 600*2) / 3 = 593.333...
    assert row["avg_fill_price"] == pytest.approx((580.0 + 1200.0) / 3)


def test_list_trades_odd_lot_factor_one(app, client):
    """Odd-lot trade keeps share-denominated quantities (factor 1)."""
    trade = _make_trade(
        order_lot="IntradayOdd",
        order_qty=100,
        deals=[_make_deal(580.0, 37)],
    )
    _wire_list_trades(app, [trade])

    resp = client.get("/api/orders/trades")
    row = resp.json()[0]
    assert row["quantity"] == 100
    assert row["filled_qty"] == 37


def test_list_trades_futures_factor_one(app, client):
    """Futures trade (no order_lot attr) reports contracts unchanged."""
    trade = MagicMock()
    trade.status.id = "trade-fut"
    trade.contract.code = "TXFR1"
    trade.order = MagicMock(spec=["action", "price", "quantity", "order_type", "price_type"])
    trade.order.action = "Buy"
    trade.order.price = 20000.0
    trade.order.quantity = 2
    trade.order.order_type = "ROD"
    trade.order.price_type = "LMT"
    trade.status.status = "Filled"
    trade.status.deals = [_make_deal(20000.0, 2)]
    trade.status.deal_quantity = 0
    _wire_list_trades(app, [trade])

    resp = client.get("/api/orders/trades")
    row = resp.json()[0]
    assert row["quantity"] == 2
    assert row["filled_qty"] == 2
    assert row["custom_field"] == ""


def test_list_trades_no_deals_filled_qty_zero(app, client):
    """A trade with no deals reports filled_qty 0 and avg_fill_price 0.0."""
    trade = _make_trade(order_lot="Common", order_qty=1, deals=[])
    _wire_list_trades(app, [trade])

    resp = client.get("/api/orders/trades")
    row = resp.json()[0]
    assert row["quantity"] == 1000
    assert row["filled_qty"] == 0
    assert row["avg_fill_price"] == 0.0


def test_list_trades_falls_back_to_deal_quantity(app, client):
    """When deals are absent, the SDK deal_quantity summary is used (in shares)."""
    trade = _make_trade(order_lot="Common", order_qty=2, deals=[], deal_quantity=1)
    _wire_list_trades(app, [trade])

    resp = client.get("/api/orders/trades")
    row = resp.json()[0]
    assert row["filled_qty"] == 1000  # 1 lot summary * 1000 share factor

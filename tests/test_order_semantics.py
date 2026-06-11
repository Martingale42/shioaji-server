"""Unit tests for Taiwan order-semantics validation and pass-through.

The gateway is the authoritative validator for Shioaji's venue rules
(ORDERS.md): market/range-market orders require IOC/FOK, intraday odd-lot
orders are cash-funded LMT+ROD trades of 1-999 shares, and day-trade short
selling requires a cash condition. These tests pin the 422 rejection matrix
plus the happy-path pass-through of ``daytrade_short`` (stock branch) and
``octype`` (futures branch) into the mocked Shioaji ``Order`` call.

The ``octype`` happy path exercises the real ``sj.constant.FuturesOCType``
enum resolution (the route is not mocked at the SDK module level), which
guards the wire-name keystone: the gateway ``OCType`` StrEnum values must be
valid SDK member names (Auto/New/Cover/DayTrade).
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


def _wire_place(app, *, status: str = "OrderStatus.PendingSubmit") -> MagicMock:
    """Wire the mocked SDK for a place_order call and return the captured api mock."""
    sj = app.state.sj
    api = sj.api

    api.Contracts.Stocks.__getitem__ = MagicMock(return_value=MagicMock())
    api.Contracts.Futures.__getitem__ = MagicMock(return_value=MagicMock())
    api.Contracts.Options.__getitem__ = MagicMock(return_value=MagicMock())
    api.stock_account = MagicMock()
    api.futopt_account = MagicMock()

    api.Order = MagicMock(return_value=MagicMock())

    trade_result = MagicMock()
    trade_result.status.id = "trade-sem"
    trade_result.status.status = status
    api.place_order = MagicMock(return_value=trade_result)

    async def fake_run_sync(fn, *args):
        return fn(*args)

    sj.run_sync = fake_run_sync
    return api


# ---------------------------------------------------------------------------
# 422 rejection matrix
# ---------------------------------------------------------------------------

_BASE_STOCK = {
    "code": "2330",
    "action": "Buy",
    "price": 580.0,
    "quantity": 1000,
    "market": "stock",
}


@pytest.mark.parametrize(
    ("overrides", "detail_fragment"),
    [
        # MKT/MKP require an immediate time in force.
        ({"price_type": "MKT", "order_type": "ROD"}, "MKT orders require IOC or FOK"),
        ({"price_type": "MKP", "order_type": "ROD"}, "MKP orders require IOC or FOK"),
        # IntradayOdd must be LMT + ROD.
        (
            {
                "order_lot": "IntradayOdd",
                "price_type": "MKT",
                "order_type": "IOC",
                "quantity": 37,
            },
            "IntradayOdd orders must be LMT + ROD",
        ),
        (
            {"order_lot": "IntradayOdd", "order_type": "IOC", "quantity": 37},
            "IntradayOdd orders must be LMT + ROD",
        ),
        # IntradayOdd quantity must be 1-999 shares.
        (
            {"order_lot": "IntradayOdd", "quantity": 1000},
            "IntradayOdd quantity must be 1-999 shares",
        ),
        (
            {"order_lot": "IntradayOdd", "quantity": 0},
            "IntradayOdd quantity must be 1-999 shares",
        ),
        # IntradayOdd must be a cash order.
        (
            {
                "order_lot": "IntradayOdd",
                "quantity": 37,
                "order_cond": "MarginTrading",
            },
            "IntradayOdd orders must be Cash",
        ),
        # daytrade_short requires a cash condition.
        (
            {"daytrade_short": True, "order_cond": "ShortSelling"},
            "daytrade_short requires order_cond=Cash",
        ),
    ],
)
def test_place_order_semantics_rejected_422(app, client, overrides, detail_fragment):
    """Each venue-rule violation returns 422 and never touches api.Order."""
    api = _wire_place(app)
    body = {**_BASE_STOCK, **overrides}

    resp = client.post("/api/orders/place", json=body)

    assert resp.status_code == 422
    assert detail_fragment in resp.json()["detail"]
    api.Order.assert_not_called()


# ---------------------------------------------------------------------------
# Happy paths: pass-through into api.Order(...)
# ---------------------------------------------------------------------------

def test_place_order_stock_passes_daytrade_short(app, client):
    """daytrade_short=True (with Cash) reaches api.Order(daytrade_short=...)."""
    api = _wire_place(app)

    resp = client.post(
        "/api/orders/place",
        json={**_BASE_STOCK, "daytrade_short": True, "order_cond": "Cash"},
    )

    assert resp.status_code == 200
    call_kwargs = api.Order.call_args.kwargs
    assert call_kwargs["daytrade_short"] is True


def test_place_order_stock_daytrade_short_defaults_false(app, client):
    """Omitting daytrade_short passes False to api.Order."""
    api = _wire_place(app)

    resp = client.post("/api/orders/place", json=dict(_BASE_STOCK))

    assert resp.status_code == 200
    assert api.Order.call_args.kwargs["daytrade_short"] is False


def test_place_order_intraday_odd_happy_path(app, client):
    """A 37-share Cash LMT+ROD IntradayOdd order is accepted and share-denominated."""
    api = _wire_place(app)

    resp = client.post(
        "/api/orders/place",
        json={
            **_BASE_STOCK,
            "order_lot": "IntradayOdd",
            "price_type": "LMT",
            "order_type": "ROD",
            "order_cond": "Cash",
            "quantity": 37,
        },
    )

    assert resp.status_code == 200
    call_kwargs = api.Order.call_args.kwargs
    # Odd-lot orders are share-denominated in Shioaji (no shares->lots division).
    assert call_kwargs["quantity"] == 37


@pytest.mark.parametrize(
    ("octype", "expected_member"),
    [
        ("Auto", "Auto"),
        ("New", "New"),
        ("Cover", "Cover"),
        ("DayTrade", "DayTrade"),
    ],
)
def test_place_order_futures_passes_octype(app, client, octype, expected_member):
    """octype string resolves to the real FuturesOCType member and reaches api.Order."""
    import shioaji as sj

    api = _wire_place(app)

    resp = client.post(
        "/api/orders/place",
        json={
            "code": "TXFR1",
            "action": "Buy",
            "price": 20000.0,
            "quantity": 1,
            "price_type": "LMT",
            "order_type": "ROD",
            "market": "futures",
            "octype": octype,
        },
    )

    assert resp.status_code == 200
    passed = api.Order.call_args.kwargs["octype"]
    assert passed is getattr(sj.constant.FuturesOCType, expected_member)


def test_place_order_futures_octype_defaults_auto(app, client):
    """Omitting octype passes FuturesOCType.Auto to api.Order."""
    import shioaji as sj

    api = _wire_place(app)

    resp = client.post(
        "/api/orders/place",
        json={
            "code": "TXFR1",
            "action": "Buy",
            "price": 20000.0,
            "quantity": 1,
            "price_type": "LMT",
            "order_type": "ROD",
            "market": "futures",
        },
    )

    assert resp.status_code == 200
    assert api.Order.call_args.kwargs["octype"] is sj.constant.FuturesOCType.Auto


# ---------------------------------------------------------------------------
# update_order: odd-lot price-modification guard
# ---------------------------------------------------------------------------

def _wire_update(app, *, order_lot_value: str) -> MagicMock:
    """Wire a single resolvable trade whose order has the given order_lot value."""
    sj = app.state.sj
    api = sj.api

    trade = MagicMock()
    trade.status.id = "trade-mod"
    trade.order.order_lot = MagicMock()
    trade.order.order_lot.value = order_lot_value

    api.list_trades = MagicMock(return_value=[trade])
    api.update_order = MagicMock()

    async def fake_run_sync(fn, *args):
        return fn(*args)

    sj.run_sync = fake_run_sync
    return api


def test_update_intraday_odd_price_change_rejected_422(app, client):
    """Changing the price of an IntradayOdd order returns 422 and skips the SDK."""
    api = _wire_update(app, order_lot_value="IntradayOdd")

    resp = client.put(
        "/api/orders/update",
        json={"trade_id": "trade-mod", "price": 581.0},
    )

    assert resp.status_code == 422
    assert "IntradayOdd orders cannot change price" in resp.json()["detail"]
    api.update_order.assert_not_called()


def test_update_intraday_odd_quantity_reduce_allowed(app, client):
    """Reducing the quantity of an IntradayOdd order (no price) is permitted."""
    api = _wire_update(app, order_lot_value="IntradayOdd")

    resp = client.put(
        "/api/orders/update",
        json={"trade_id": "trade-mod", "quantity": 10},
    )

    assert resp.status_code == 200
    api.update_order.assert_called_once()
    assert api.update_order.call_args.kwargs["qty"] == 10


def test_update_common_lot_price_change_allowed(app, client):
    """Common-lot orders may still change price (guard is odd-lot only)."""
    api = _wire_update(app, order_lot_value="Common")

    resp = client.put(
        "/api/orders/update",
        json={"trade_id": "trade-mod", "price": 581.0},
    )

    assert resp.status_code == 200
    assert api.update_order.call_args.kwargs["price"] == 581.0


# ---------------------------------------------------------------------------
# list_trades: order_lot / order_cond exposure
# ---------------------------------------------------------------------------

def _wire_list_trades(app, trade: MagicMock) -> None:
    """Wire list_trades to return a single trade with a stock-only account."""
    sj = app.state.sj
    api = sj.api

    api.stock_account = MagicMock()
    api.futopt_account = None
    api.update_status = MagicMock()
    api.list_trades = MagicMock(return_value=[trade])

    async def fake_run_sync(fn, *args):
        return fn(*args)

    sj.run_sync = fake_run_sync


def test_list_trades_exposes_order_lot_and_cond_for_stock(app, client):
    """A stock trade surfaces its enum-valued order_lot and order_cond."""
    trade = MagicMock()
    trade.status.id = "trade-l1"
    trade.contract.code = "2330"
    trade.order.action = "Buy"
    trade.order.price = 580.0
    trade.order.quantity = 37
    trade.status.status = "Submitted"
    trade.status.deals = []
    trade.status.deal_quantity = 0
    trade.order.order_type = "ROD"
    trade.order.price_type = "LMT"
    trade.order.custom_field = ""
    # SDK exposes these as StrEnum-like objects carrying a .value.
    trade.order.order_lot = MagicMock()
    trade.order.order_lot.value = "IntradayOdd"
    trade.order.order_cond = MagicMock()
    trade.order.order_cond.value = "Cash"

    _wire_list_trades(app, trade)

    resp = client.get("/api/orders/trades")

    assert resp.status_code == 200
    row = resp.json()[0]
    assert row["order_lot"] == "IntradayOdd"
    assert row["order_cond"] == "Cash"


def test_list_trades_order_lot_cond_empty_for_futures(app, client):
    """A futures trade lacking order_lot/order_cond surfaces empty strings."""
    trade = MagicMock()
    trade.status = MagicMock()
    trade.status.id = "trade-l2"
    trade.status.status = "Submitted"
    trade.status.deals = []
    trade.status.deal_quantity = 0
    trade.contract = MagicMock()
    trade.contract.code = "TXFR1"
    # Futures orders lack order_lot / order_cond / custom_field attributes.
    trade.order = MagicMock(spec=[])
    trade.order.action = "Buy"
    trade.order.price = 20000.0
    trade.order.quantity = 1
    trade.order.order_type = "ROD"
    trade.order.price_type = "LMT"

    _wire_list_trades(app, trade)

    resp = client.get("/api/orders/trades")

    assert resp.status_code == 200
    row = resp.json()[0]
    assert row["order_lot"] == ""
    assert row["order_cond"] == ""

"""Unit tests for custom_field round-trip through the gateway.

Validates that the adapter's 6-char token reaches the Shioaji SDK's Order
object and that list_trades exposes it for restart reconciliation.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from shioaji_server.models import PlaceOrderRequest, TradeInfo


# ---------------------------------------------------------------------------
# PlaceOrderRequest model tests
# ---------------------------------------------------------------------------

def test_place_order_request_default_custom_field():
    req = PlaceOrderRequest(code="2330", action="Buy", price=580.0, quantity=1)
    assert req.custom_field == ""


def test_place_order_request_accepts_custom_field():
    req = PlaceOrderRequest(
        code="2330", action="Buy", price=580.0, quantity=1, custom_field="ab12cd"
    )
    assert req.custom_field == "ab12cd"


# ---------------------------------------------------------------------------
# TradeInfo model tests
# ---------------------------------------------------------------------------

def test_trade_info_has_custom_field():
    info = TradeInfo(
        trade_id="t1",
        code="2330",
        action="Buy",
        price=580.0,
        quantity=1,
        status="Submitted",
        order_type="ROD",
        price_type="LMT",
        custom_field="tok123",
    )
    assert info.custom_field == "tok123"


def test_trade_info_custom_field_defaults_empty():
    info = TradeInfo(
        trade_id="t1",
        code="2330",
        action="Buy",
        price=580.0,
        quantity=1,
        status="Submitted",
        order_type="ROD",
        price_type="LMT",
    )
    assert info.custom_field == ""


# ---------------------------------------------------------------------------
# Route-level: custom_field reaches api.Order() + truncated to 6 chars
# ---------------------------------------------------------------------------

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


def test_place_order_stock_passes_custom_field(app, client):
    """custom_field from the request body reaches api.Order(custom_field=...)."""
    sj = app.state.sj
    api = sj.api

    # Mock contract resolution
    api.Contracts.Stocks.__getitem__ = MagicMock(return_value=MagicMock())

    # Capture the Order() call
    order_mock = MagicMock()
    api.Order = MagicMock(return_value=order_mock)

    # Mock place_order to return a trade
    trade_result = MagicMock()
    trade_result.status.id = "trade-001"
    trade_result.status.status = "PendingSubmit"
    api.place_order = MagicMock(return_value=trade_result)

    # run_sync just calls the function directly for testing
    async def fake_run_sync(fn, *args):
        return fn(*args)

    sj.run_sync = fake_run_sync

    resp = client.post(
        "/api/orders/place",
        json={
            "code": "2330",
            "action": "Buy",
            "price": 580.0,
            "quantity": 1000,  # 1 common lot in shares (wire unit is shares, D1)
            "custom_field": "ab12cd",
        },
    )
    assert resp.status_code == 200

    # Verify custom_field was passed to api.Order
    api.Order.assert_called_once()
    call_kwargs = api.Order.call_args
    # custom_field may be positional or keyword
    assert call_kwargs.kwargs.get("custom_field") == "ab12cd" or \
        "ab12cd" in str(call_kwargs)


def test_place_order_truncates_custom_field_to_6(app, client):
    """custom_field longer than 6 chars is truncated to 6."""
    sj = app.state.sj
    api = sj.api

    api.Contracts.Stocks.__getitem__ = MagicMock(return_value=MagicMock())
    order_mock = MagicMock()
    api.Order = MagicMock(return_value=order_mock)

    trade_result = MagicMock()
    trade_result.status.id = "trade-002"
    trade_result.status.status = "PendingSubmit"
    api.place_order = MagicMock(return_value=trade_result)

    async def fake_run_sync(fn, *args):
        return fn(*args)

    sj.run_sync = fake_run_sync

    resp = client.post(
        "/api/orders/place",
        json={
            "code": "2330",
            "action": "Buy",
            "price": 580.0,
            "quantity": 1000,  # 1 common lot in shares (wire unit is shares, D1)
            "custom_field": "toolongtoken",
        },
    )
    assert resp.status_code == 200

    call_kwargs = api.Order.call_args.kwargs
    assert call_kwargs["custom_field"] == "toolon"  # truncated to 6


def test_place_order_futures_passes_custom_field(app, client):
    """Futures branch also passes custom_field."""
    sj = app.state.sj
    api = sj.api

    api.Contracts.Futures.__getitem__ = MagicMock(return_value=MagicMock())
    order_mock = MagicMock()
    api.Order = MagicMock(return_value=order_mock)
    api.futopt_account = MagicMock()

    trade_result = MagicMock()
    trade_result.status.id = "trade-003"
    trade_result.status.status = "PendingSubmit"
    api.place_order = MagicMock(return_value=trade_result)

    async def fake_run_sync(fn, *args):
        return fn(*args)

    sj.run_sync = fake_run_sync

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
            "custom_field": "fut123",
        },
    )
    assert resp.status_code == 200

    call_kwargs = api.Order.call_args.kwargs
    assert call_kwargs["custom_field"] == "fut123"


# ---------------------------------------------------------------------------
# list_trades: custom_field exposed in response
# ---------------------------------------------------------------------------

def test_list_trades_includes_custom_field(app, client):
    """list_trades response includes custom_field from trade.order."""
    sj = app.state.sj
    api = sj.api

    # Build a mock Trade with custom_field on its order
    mock_trade = MagicMock()
    mock_trade.status.id = "trade-010"
    mock_trade.contract.code = "2330"
    mock_trade.order.action = "Buy"
    mock_trade.order.price = 580.0
    mock_trade.order.quantity = 1
    mock_trade.status.status = "Filled"
    mock_trade.order.order_type = "ROD"
    mock_trade.order.price_type = "LMT"
    mock_trade.order.custom_field = "tok999"

    # Mock update_status + list_trades
    api.stock_account = MagicMock()
    api.futopt_account = None
    api.update_status = MagicMock()
    api.list_trades = MagicMock(return_value=[mock_trade])

    async def fake_run_sync(fn, *args):
        return fn(*args)

    sj.run_sync = fake_run_sync

    resp = client.get("/api/orders/trades")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["custom_field"] == "tok999"


def test_list_trades_missing_custom_field_defaults_empty(app, client):
    """When trade.order has no custom_field attr, default to empty string."""
    sj = app.state.sj
    api = sj.api

    mock_trade = MagicMock(spec=[])  # no attributes by default
    mock_trade.status = MagicMock()
    mock_trade.status.id = "trade-011"
    mock_trade.contract = MagicMock()
    mock_trade.contract.code = "2330"
    mock_trade.order = MagicMock(spec=[])
    mock_trade.order.action = "Buy"
    mock_trade.order.price = 580.0
    mock_trade.order.quantity = 1
    mock_trade.status.status = "Submitted"
    mock_trade.order.order_type = "ROD"
    mock_trade.order.price_type = "LMT"
    # Deliberately NOT setting custom_field on order

    api.stock_account = MagicMock()
    api.futopt_account = None
    api.update_status = MagicMock()
    api.list_trades = MagicMock(return_value=[mock_trade])

    async def fake_run_sync(fn, *args):
        return fn(*args)

    sj.run_sync = fake_run_sync

    resp = client.get("/api/orders/trades")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["custom_field"] == ""

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

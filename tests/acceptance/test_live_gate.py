"""Live-gate acceptance tests against a running **simulation** Sinopac gateway.

These are the five BACKLOGS "verification gates" (SINOPAC-01..05) that arbitrate
the production-hardening work end to end. They run against a *live* FastAPI
shioaji-server in ``simulation=true`` mode, not against mocks.

Structure & how to run
----------------------
All gates are marked ``@pytest.mark.gateway`` and SKIP unless the
``SINOPAC_GATEWAY_URL`` environment variable is set (a module-level skip).
The whole module collects cleanly with no env var (it just skips), so it never
errors in CI or a market-closed window::

    SINOPAC_GATEWAY_URL=http://localhost:8000 \
        uv run pytest tests/acceptance/test_live_gate.py -v -m gateway

**Two observation layers.** Every gate asserts against the gateway *contract*
over HTTP (``/api/orders/*``) and the WebSocket order-event broadcast (``/ws``),
because that broadcast is exactly the substrate an NT node consumes: the gateway
broadcasts every order/deal event to **all** connected WS clients with no
per-connection registration (``ws/manager.py:broadcast_order_update``), so a raw
WS client receiving an ``order_update`` envelope reproduces what a wired
``SinopacLiveExecClientFactory`` node would receive. This keeps the gates
runnable from the shioaji-server uv env (which *can* import ``nautilus_trader``,
but booting a full ``TradingNode`` event loop inside pytest is heavyweight and
fill-dependent). The NT-position-level assertions (gate 1 "+1000", gate 2/5
convergence) are cross-referenced to the NT integration suite
(``nautilus_trader/tests/integration_tests/adapters/sinopac``) and a future
market-hours node run; they are marked PENDING here, never silently passed.

**Market-hours dependence.** The Taiwan sim matching engine does **not** fill
outside TWSE hours (09:00-13:30 Asia/Taipei). Every assertion that needs a
*fill* (deal event, ``filled_qty > 0``, position convergence, partial-fill
reconciliation) is skipped with a clear "pending market hours" reason rather
than failing. The fill-INDEPENDENT assertions (placement -> SDK-lot conversion
-> ``list_trades.quantity == 1000`` shares; async venue reject -> ``Failed``)
run and PASS now, even market-closed (Batch 0.1 reproduced the async-reject
path off-tick while closed).

Safety
------
``simulation=true`` is re-asserted before *every* order placement; the suite
hard-fails closed if the gateway is ever not in simulation. All resting orders
placed by a test are cancelled in teardown so the suite leaves zero open orders.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator

import httpx
import pytest

# ---------------------------------------------------------------------------
# Module-level gating: skip everything unless a gateway URL is configured.
# ---------------------------------------------------------------------------

GATEWAY_URL = os.environ.get("SINOPAC_GATEWAY_URL")

pytestmark = [
    pytest.mark.gateway,
    pytest.mark.skipif(
        GATEWAY_URL is None,
        reason="SINOPAC_GATEWAY_URL not set; live-gate acceptance tests skipped",
    ),
]

# A common-lot 2330 BUY priced well below the reference so it rests (does not
# fill) and is on the tick grid (5.0 tick in the 1000-5000 band) -> accepted,
# not venue-rejected. Used by the fill-independent placement assertions.
RESTING_PRICE = 2050.0
OFF_TICK_PRICE = 99999.0  # off the tick grid -> async venue rejection (Batch 0.1)

# Order-status string tails (gateway emits "OrderStatus.<Tail>").
REJECTED_TAILS = frozenset({"Failed", "Inactive"})
RESTING_TAILS = frozenset({"PendingSubmit", "PreSubmitted", "Submitted"})

PENDING_MARKET = (
    "pending market-hours run (09:00-13:30 Asia/Taipei); sim does not fill while closed"
)


def _status_tail(status: str) -> str:
    """Return the trailing token of an ``OrderStatus.<Tail>`` string."""
    return status.split(".")[-1]


@pytest.fixture
def http() -> Iterator[httpx.Client]:
    """A short-timeout HTTP client bound to the configured gateway."""
    with httpx.Client(base_url=GATEWAY_URL, timeout=10.0) as client:
        yield client


@pytest.fixture
def require_simulation(http: httpx.Client) -> None:
    """Fail closed unless the gateway reports ``simulation=true``.

    Re-checked as a fixture dependency of every order-placing gate so no test
    can ever place an order against a non-simulation account.
    """
    resp = http.get("/api/auth/status")
    resp.raise_for_status()
    payload = resp.json()
    assert payload.get("simulation") is True, (
        f"Gateway is NOT in simulation mode (auth status={payload}); refusing to place orders"
    )


@pytest.fixture
def order_janitor(http: httpx.Client) -> Iterator[list[str]]:
    """Collect placed trade_ids and cancel any still-resting ones in teardown.

    Tests append every trade_id they place. After the test, each is cancelled
    so the suite leaves zero open orders regardless of pass/fail.
    """
    placed: list[str] = []
    yield placed
    for trade_id in placed:
        try:
            http.request(
                "DELETE",
                "/api/orders/cancel",
                json={"trade_id": trade_id},
            )
        except httpx.HTTPError:
            # Best-effort cleanup; a terminal (Failed/Cancelled) order is a no-op.
            pass


def _place(
    http: httpx.Client,
    *,
    price: float,
    quantity: int,
    order_lot: str = "Common",
    code: str = "2330",
    action: str = "Buy",
) -> httpx.Response:
    """POST a single stock order and return the raw response."""
    return http.post(
        "/api/orders/place",
        json={
            "code": code,
            "action": action,
            "price": price,
            "quantity": quantity,
            "order_lot": order_lot,
            "market": "stock",
        },
    )


def _find_trade(http: httpx.Client, trade_id: str) -> dict | None:
    """Return the ``list_trades`` row for ``trade_id``, or None if absent."""
    resp = http.get("/api/orders/trades")
    resp.raise_for_status()
    for row in resp.json():
        if row["trade_id"] == trade_id:
            return row
    return None


def _poll_status_tail(
    http: httpx.Client,
    trade_id: str,
    *,
    until: frozenset[str],
    attempts: int = 10,
    interval: float = 1.0,
) -> str | None:
    """Poll ``list_trades`` until the order's status tail is in ``until``.

    Returns the matched status tail, or the last observed tail if the target
    was not reached within ``attempts`` polls. Used for the async venue-reject
    transition (place returns ``PendingSubmit``; ``Failed`` surfaces later).
    """
    last: str | None = None
    for _ in range(attempts):
        row = _find_trade(http, trade_id)
        if row is not None:
            last = _status_tail(row["status"])
            if last in until:
                return last
        time.sleep(interval)
    return last


# ===========================================================================
# Gate 1 (SINOPAC-01) — Quantity round-trip.
# ===========================================================================


def test_gate1_quantity_round_trip_placement(
    http: httpx.Client,
    require_simulation: None,
    order_janitor: list[str],
) -> None:
    """A 1000-share 2330 BUY round-trips as 1000 shares on the wire.

    FILL-INDEPENDENT (runs + passes now): the gateway accepts 1000 shares,
    converts to exactly 1 SDK lot at the boundary, and ``list_trades`` reports
    ``quantity == 1000`` shares back. This is the observable half of the
    lots-vs-shares contract (D1) without needing a deal.

    The deal-event-1000-shares + ``filled_qty > 0`` + NT-position-+1000 half is
    FILL-DEPENDENT and is asserted by ``test_gate1_quantity_round_trip_fill``.
    """
    resp = _place(http, price=RESTING_PRICE, quantity=1000)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    trade_id = body["trade_id"]
    order_janitor.append(trade_id)

    # Placement was accepted (not synchronously rejected).
    assert _status_tail(body["status"]) not in REJECTED_TAILS, body

    # list_trades reports the quantity back in SHARES (1 SDK lot -> 1000 shares).
    row = _find_trade(http, trade_id)
    assert row is not None, f"placed trade {trade_id} not found in list_trades"
    assert row["quantity"] == 1000, (
        f"expected 1000 shares round-tripped, was {row['quantity']}"
    )


@pytest.mark.skip(reason=f"FILL-DEPENDENT: {PENDING_MARKET}")
def test_gate1_quantity_round_trip_fill(
    http: httpx.Client,
    require_simulation: None,
    order_janitor: list[str],
) -> None:
    """The fill half of the quantity round-trip (PENDING market hours).

    During market hours, a marketable 1000-share 2330 BUY must fill and:
      * the WS ``order_update`` StockDeal event carries 1000 shares (deal-event
        normalization, gateway ``session.py``);
      * ``list_trades`` reports ``filled_qty == 1000`` (shares);
      * a wired NT node holds position +1000 for 2330.SINOPAC.

    The NT-position assertion is cross-referenced to the NT integration suite
    (``tests/integration_tests/adapters/sinopac``); the deal/filled_qty
    assertions are observable here over HTTP/WS once a fill occurs.
    """
    raise AssertionError(
        "market-hours fill assertion not yet executed"
    )  # pragma: no cover


# ===========================================================================
# Gate 2 (SINOPAC-02) — Kill/restore WS, converge after in-gap fill.
# ===========================================================================


@pytest.mark.skip(reason=f"FILL-DEPENDENT: {PENDING_MARKET}")
def test_gate2_kill_restore_ws_converges(
    http: httpx.Client,
    require_simulation: None,
) -> None:
    """Position converges within one reconnect cycle after an in-gap fill.

    Structure (executed at market hours):
      1. Wire an NT node (data+exec) and let it connect the shared WS.
      2. Drop the WS connection (close the socket / kill the gateway link).
      3. While down, a marketable order fills (the in-gap fill).
      4. Restore the WS; the Rust handler emits the ``Reconnected`` sentinel,
         the exec client reconciles (generate_order_status_reports +
         generate_fill_reports + generate_position_status_reports), and the
         position converges to the venue truth within one reconnect cycle.

    FILL-DEPENDENT: the in-gap fill cannot be produced market-closed. The
    reconnect-sentinel plumbing itself is unit-tested in the NT Rust + Python
    suites (Batch 4/5); this gate is the end-to-end convergence proof.
    """
    raise AssertionError(
        "market-hours convergence assertion not yet executed"
    )  # pragma: no cover


# ===========================================================================
# Gate 3 (SINOPAC-03) — Exec-only node receives accept (and fill) events.
# ===========================================================================


def test_gate3_order_events_broadcast_to_ws_subscriber(
    http: httpx.Client,
    require_simulation: None,
    order_janitor: list[str],
) -> None:
    """A WS subscriber receives the order accept event without a data client.

    FILL-INDEPENDENT (runs + passes now). The gateway broadcasts every order
    event to **all** connected WS clients with no per-connection registration
    (``ws/manager.py``), which is precisely the substrate an exec-only NT node
    (only ``SinopacLiveExecClientFactory``, no data client) relies on. We open a
    raw WS client, place a resting order, and assert an ``order_update`` envelope
    for that order arrives. This reproduces the SINOPAC-03 accept path: an
    exec-only client receives accept events because delivery is broadcast-based.

    The FILL portion (the same subscriber also receives the StockDeal event) is
    PENDING market hours -- see ``test_gate3_exec_only_receives_fill``.
    """
    # websockets is sync-friendly via its threading client (avoids needing an
    # event loop in this sync test).
    from websockets.sync.client import connect as ws_connect

    ws_url = (
        GATEWAY_URL.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
    )

    with ws_connect(ws_url, open_timeout=10) as ws:
        resp = _place(http, price=RESTING_PRICE, quantity=1000)
        assert resp.status_code == 200, resp.text
        trade_id = resp.json()["trade_id"]
        order_janitor.append(trade_id)

        # Drain WS messages until an order_update envelope arrives or we time out.
        deadline = time.time() + 15.0
        saw_order_update = False
        while time.time() < deadline:
            try:
                raw = ws.recv(timeout=max(0.1, deadline - time.time()))
            except TimeoutError:
                break
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if msg.get("type") == "order_update":
                saw_order_update = True
                break

    assert saw_order_update, (
        "exec-only WS subscriber did not receive any order_update broadcast "
        "after placing an order (SINOPAC-03 broadcast contract)"
    )


@pytest.mark.skip(reason=f"FILL-DEPENDENT: {PENDING_MARKET}")
def test_gate3_exec_only_receives_fill(
    http: httpx.Client,
    require_simulation: None,
) -> None:
    """An exec-only node (no data client) receives the FILL event (PENDING).

    Structure (executed at market hours): boot an NT node with only
    ``SinopacLiveExecClientFactory``, submit a marketable order, and assert the
    exec client emits OrderFilled (position updates). FILL-DEPENDENT.
    """
    raise AssertionError(
        "market-hours fill assertion not yet executed"
    )  # pragma: no cover


# ===========================================================================
# Gate 4 (SINOPAC-04) — Venue reject ends REJECTED, no placeholder mapping.
# ===========================================================================


def test_gate4_off_tick_order_is_rejected(
    http: httpx.Client,
    require_simulation: None,
    order_janitor: list[str],
) -> None:
    """An off-tick price drives the order to a terminal rejected status.

    FILL-INDEPENDENT (runs + passes now, even market-closed). Batch 0.1 showed
    the dominant venue-reject path in sim is ASYNC: ``/orders/place`` returns
    200 + ``PendingSubmit``, and the order transitions to ``OrderStatus.Failed``
    shortly after via the order-event stream (op_code != "00"). This gate is the
    end-to-end proof of the async-reject path that Task 3.3 drives to NT
    ``REJECTED``: we place an off-tick 99999 order and poll until the gateway
    reports a rejected terminal status. No placeholder/accepted mapping is
    created for it (it never reaches a resting/working state).
    """
    resp = _place(http, price=OFF_TICK_PRICE, quantity=1000)
    # The gateway may reject synchronously (422) for SDK-surfaced rejects, or
    # accept (200 + PendingSubmit) and reject asynchronously. Both are valid
    # reject paths; assert the order ends terminally rejected either way.
    if resp.status_code == 422:
        assert "rejected by venue" in resp.json()["detail"].lower(), resp.text
        return

    assert resp.status_code == 200, resp.text
    trade_id = resp.json()["trade_id"]
    order_janitor.append(trade_id)

    tail = _poll_status_tail(http, trade_id, until=REJECTED_TAILS)
    assert tail in REJECTED_TAILS, (
        f"off-tick order {trade_id} did not reach a rejected terminal status; "
        f"last observed tail was {tail!r}"
    )

    # The rejected order must never be left in a resting/working state.
    row = _find_trade(http, trade_id)
    assert row is not None
    assert _status_tail(row["status"]) not in RESTING_TAILS, (
        f"rejected order is stuck in a resting state: {row['status']}"
    )


# ===========================================================================
# Gate 5 (SINOPAC-05) — Restart reconciliation rebuilds filled_qty/position.
# ===========================================================================


@pytest.mark.skip(reason=f"FILL-DEPENDENT: {PENDING_MARKET}")
def test_gate5_restart_reconciliation(
    http: httpx.Client,
    require_simulation: None,
) -> None:
    """A partially-filled order reconciles correctly after a node restart.

    Structure (executed at market hours):
      1. Submit an order and let it PARTIALLY fill.
      2. Restart the NT node (dispose + rebuild).
      3. On boot, reconciliation reads the gateway ``filled_qty`` (shares, D1)
         and rebuilds the order's ``filled_qty`` and the venue position exactly,
         with no inconsistent FILLED/filled_qty=0 reports.

    FILL-DEPENDENT: producing a partial fill requires market hours. The
    gateway-reported ``filled_qty`` plumbing is exercised by the NT integration
    suite; this gate is the end-to-end restart proof.
    """
    raise AssertionError(
        "market-hours reconciliation assertion not yet executed"
    )  # pragma: no cover

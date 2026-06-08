import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect

from shioaji_server.client import ShioajiClient
from shioaji_server.errors import runtime_error_handler
from shioaji_server.models import HealthResponse
from shioaji_server.routes.account import router as account_router
from shioaji_server.routes.auth import router as auth_router
from shioaji_server.routes.contracts import router as contracts_router
from shioaji_server.routes.market_data import router as market_data_router
from shioaji_server.routes.orders import router as orders_router
from shioaji_server.ws.manager import ConnectionManager

log = logging.getLogger(__name__)

manager = ConnectionManager()

_ENV_SETUP_HINT = """
[shioaji-server] Auto-login skipped: missing environment variables.

To enable auto-login, create a .env file with:

    SHIOAJI_API_KEY=your_api_key
    SHIOAJI_SECRET_KEY=your_secret_key
    CA_PATH=/absolute/path/to/Sinopac.pfx    # optional, required for placing orders
    CA_PERSON=your_ca_password                # optional, required for placing orders

Then start the server from the directory containing .env,
or set SHIOAJI_ENV_FILE to point to the .env path.

See README.md for the full setup guide.
You can also login manually via POST /api/auth/login after the server starts.
""".strip()


async def _auto_login(sj_client: ShioajiClient) -> None:
    """Attempt auto-login from environment variables."""
    api_key = os.environ.get("SHIOAJI_API_KEY")
    secret_key = os.environ.get("SHIOAJI_SECRET_KEY")

    if not api_key or not secret_key:
        log.warning(_ENV_SETUP_HINT)
        return

    ca_path = os.environ.get("CA_PATH")
    ca_passwd = os.environ.get("CA_PERSON")
    simulation = os.environ.get("SHIOAJI_SIMULATION", "true").lower() in ("true", "1", "yes")

    mode = "simulation" if simulation else "LIVE"
    log.info("[shioaji-server] Auto-login starting (%s)...", mode)

    try:
        accounts = await sj_client.login(
            api_key=api_key,
            secret_key=secret_key,
            ca_path=ca_path,
            ca_passwd=ca_passwd,
            simulation=simulation,
        )
        log.info("[shioaji-server] Login successful! %d account(s):", len(accounts))
        for acc in accounts:
            log.info("  - %s: %s", acc["account_type"], acc["account_id"])
    except Exception:
        log.exception("[shioaji-server] Auto-login failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.sj = ShioajiClient()
    app.state.ws_manager = manager
    manager.set_loop(asyncio.get_running_loop())
    await _auto_login(app.state.sj)
    if app.state.sj.connected:
        app.state.sj.register_callbacks(manager)
    yield
    await app.state.sj.logout()


app = FastAPI(
    title="Shioaji Server",
    version="0.1.0",
    description=(
        "REST/WebSocket gateway for Sinopac Shioaji SDK.\n\n"
        "Wraps the Shioaji Python SDK into HTTP API + WebSocket real-time market data push, "
        "enabling NautilusTrader (Rust/PyO3) to connect to Taiwan stock/futures/options markets "
        "via standard network protocols.\n\n"
        "**WebSocket** endpoint at `/ws` for real-time tick and bid/ask streaming.\n\n"
        "Source: [GitHub](https://github.com/Martingale42/shioaji-server)"
    ),
    lifespan=lifespan,
)
app.include_router(auth_router)
app.include_router(contracts_router)
app.include_router(market_data_router)
app.include_router(orders_router)
app.include_router(account_router)
app.add_exception_handler(RuntimeError, runtime_error_handler)


@app.get(
    "/api/health",
    response_model=HealthResponse,
    summary="Health check",
    description=(
        "Returns server status and **live** Shioaji backend session state. "
        "`connected` is True only when logged in AND the backend session actually "
        "responds (probed via a lightweight usage call, cached briefly to respect "
        "the accounting rate limit). `logged_in` reflects the login flag alone and "
        "`session_alive` the probe result — together they distinguish 'never logged "
        "in' from 'session silently dropped'."
    ),
    tags=["system"],
)
async def health(request: Request) -> dict:
    sj_client = request.app.state.sj
    logged_in = sj_client.connected
    session_alive = await sj_client.check_session()
    return {
        "status": "ok",
        "connected": logged_in and session_alive,
        "logged_in": logged_in,
        "session_alive": session_alive,
    }


async def _unsubscribe_orphaned(
    sj_client: ShioajiClient, orphaned: list[tuple[str, str]],
) -> None:
    """Unsubscribe Shioaji quotes for keys with no remaining WS clients."""
    for code, quote_type in orphaned:
        try:
            contract = sj_client.resolve_contract(code)
            qt = sj_client.to_quote_type(quote_type)
            await sj_client.run_sync(
                partial(sj_client.api.quote.unsubscribe, contract, quote_type=qt),
            )
        except Exception:
            log.warning("Failed to unsubscribe %s/%s", code, quote_type, exc_info=True)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    sj_client = ws.app.state.sj
    await manager.connect(ws)
    try:
        while True:
            text = await ws.receive_text()

            # G6: a single malformed frame must not drop the connection.
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                await ws.send_text(json.dumps({
                    "type": "error", "detail": "Invalid JSON",
                }))
                continue

            action = msg.get("action")
            code = msg.get("contract_code")
            quote_type = msg.get("quote_type")

            if not code or quote_type not in ("tick", "bidask"):
                await ws.send_text(json.dumps({
                    "type": "error",
                    "detail": "Missing or invalid contract_code/quote_type",
                }))
                continue

            # G5: refuse to (un)subscribe when the backend session is down —
            # otherwise we'd record a WS-side subscription with no live Shioaji
            # feed behind it, then orphan a Shioaji stream that was never opened.
            if not sj_client.connected:
                await ws.send_text(json.dumps({
                    "type": "error", "detail": "gateway not connected",
                }))
                continue

            # G5: wrap the per-action body so a resolve_contract / SDK error
            # (ValueError, AttributeError, etc.) sends an error frame instead of
            # escaping the loop and killing the connection (leaving a zombie).
            try:
                if action == "subscribe":
                    is_new = manager.subscribe(ws, code, quote_type)
                    if is_new:
                        contract = sj_client.resolve_contract(code)
                        qt = sj_client.to_quote_type(quote_type)
                        await sj_client.run_sync(
                            partial(sj_client.api.quote.subscribe, contract, quote_type=qt),
                        )
                    await ws.send_text(json.dumps({
                        "type": "subscribed", "code": code, "quote_type": quote_type,
                    }))

                elif action == "unsubscribe":
                    is_empty = manager.unsubscribe(ws, code, quote_type)
                    if is_empty:
                        contract = sj_client.resolve_contract(code)
                        qt = sj_client.to_quote_type(quote_type)
                        await sj_client.run_sync(
                            partial(sj_client.api.quote.unsubscribe, contract, quote_type=qt),
                        )
                    await ws.send_text(json.dumps({
                        "type": "unsubscribed", "code": code, "quote_type": quote_type,
                    }))

                else:
                    await ws.send_text(json.dumps({
                        "type": "error", "detail": f"Unknown action: {action}",
                    }))

            except WebSocketDisconnect:
                # A disconnect mid-handling (e.g. send_text on a closing socket)
                # is a normal client exit, not an action failure — re-raise so the
                # outer handler cleans up instead of logging a spurious warning and
                # re-sending on a dead socket.
                raise
            except Exception as exc:  # noqa: BLE001 — keep the connection alive
                log.warning(
                    "WS %s failed for %s/%s", action, code, quote_type, exc_info=True,
                )
                await ws.send_text(json.dumps({
                    "type": "error",
                    "detail": f"{action} failed for {code}/{quote_type}: {exc}",
                }))

    except WebSocketDisconnect:
        pass
    finally:
        # G5: always release WS-side state + any orphaned Shioaji streams,
        # whatever broke the loop — no zombie connections, no orphan feeds.
        orphaned = manager.disconnect(ws)
        await _unsubscribe_orphaned(sj_client, orphaned)

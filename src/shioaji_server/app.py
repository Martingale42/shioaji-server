import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from functools import partial

import shioaji as sj
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect

from shioaji_server.client import ShioajiClient
from shioaji_server.errors import runtime_error_handler
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


app = FastAPI(title="Shioaji Server", version="0.1.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(contracts_router)
app.include_router(market_data_router)
app.include_router(orders_router)
app.include_router(account_router)
app.add_exception_handler(RuntimeError, runtime_error_handler)


@app.get("/api/health")
async def health(request: Request) -> dict:
    return {"status": "ok", "connected": request.app.state.sj.connected}


def _resolve_contract(sj_client: ShioajiClient, code: str):
    """Try stocks first, then futures, then options."""
    c = sj_client.api.Contracts.Stocks.get(code)
    if c:
        return c
    c = sj_client.api.Contracts.Futures.get(code)
    if c:
        return c
    c = sj_client.api.Contracts.Options.get(code)
    if c:
        return c
    raise ValueError(f"Contract {code} not found")


def _to_quote_type(quote_type: str) -> sj.constant.QuoteType:
    if quote_type == "tick":
        return sj.constant.QuoteType.Tick
    return sj.constant.QuoteType.BidAsk


async def _unsubscribe_orphaned(
    sj_client: ShioajiClient, orphaned: list[tuple[str, str]],
) -> None:
    """Unsubscribe Shioaji quotes for keys with no remaining WS clients."""
    for code, quote_type in orphaned:
        try:
            contract = _resolve_contract(sj_client, code)
            qt = _to_quote_type(quote_type)
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
            msg = json.loads(text)
            action = msg.get("action")
            code = msg.get("contract_code")
            quote_type = msg.get("quote_type")

            if not code or quote_type not in ("tick", "bidask"):
                await ws.send_text(json.dumps({
                    "type": "error",
                    "detail": "Missing or invalid contract_code/quote_type",
                }))
                continue

            if action == "subscribe":
                is_new = manager.subscribe(ws, code, quote_type)
                if is_new:
                    contract = _resolve_contract(sj_client, code)
                    qt = _to_quote_type(quote_type)
                    await sj_client.run_sync(
                        partial(sj_client.api.quote.subscribe, contract, quote_type=qt),
                    )
                await ws.send_text(json.dumps({
                    "type": "subscribed", "code": code, "quote_type": quote_type,
                }))

            elif action == "unsubscribe":
                is_empty = manager.unsubscribe(ws, code, quote_type)
                if is_empty:
                    contract = _resolve_contract(sj_client, code)
                    qt = _to_quote_type(quote_type)
                    await sj_client.run_sync(
                        partial(sj_client.api.quote.unsubscribe, contract, quote_type=qt),
                    )
                await ws.send_text(json.dumps({
                    "type": "unsubscribed", "code": code, "quote_type": quote_type,
                }))

    except WebSocketDisconnect:
        orphaned = manager.disconnect(ws)
        await _unsubscribe_orphaned(sj_client, orphaned)

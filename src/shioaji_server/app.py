import asyncio
import json
import logging
from contextlib import asynccontextmanager
from functools import partial

import shioaji as sj
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect

from shioaji_server.client import ShioajiClient
from shioaji_server.routes.auth import router as auth_router
from shioaji_server.routes.contracts import router as contracts_router
from shioaji_server.routes.market_data import router as market_data_router
from shioaji_server.ws.manager import ConnectionManager

log = logging.getLogger(__name__)

manager = ConnectionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.sj = ShioajiClient()
    app.state.ws_manager = manager
    manager.set_loop(asyncio.get_running_loop())
    yield
    await app.state.sj.logout()


app = FastAPI(title="Shioaji Server", version="0.1.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(contracts_router)
app.include_router(market_data_router)


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

import asyncio
import json
from contextlib import asynccontextmanager

import shioaji as sj
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect

from shioaji_server.client import ShioajiClient
from shioaji_server.routes.auth import router as auth_router
from shioaji_server.routes.contracts import router as contracts_router
from shioaji_server.routes.market_data import router as market_data_router
from shioaji_server.ws.manager import ConnectionManager

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
            quote_type = msg.get("quote_type")  # "tick" or "bidask"

            if action == "subscribe":
                is_new = manager.subscribe(ws, code, quote_type)
                if is_new:
                    contract = _resolve_contract(sj_client, code)
                    qt = (
                        sj.constant.QuoteType.Tick
                        if quote_type == "tick"
                        else sj.constant.QuoteType.BidAsk
                    )
                    sj_client.api.quote.subscribe(contract, quote_type=qt)
                await ws.send_text(json.dumps({
                    "type": "subscribed", "code": code, "quote_type": quote_type,
                }))

            elif action == "unsubscribe":
                is_empty = manager.unsubscribe(ws, code, quote_type)
                if is_empty:
                    contract = _resolve_contract(sj_client, code)
                    qt = (
                        sj.constant.QuoteType.Tick
                        if quote_type == "tick"
                        else sj.constant.QuoteType.BidAsk
                    )
                    sj_client.api.quote.unsubscribe(contract, quote_type=qt)
                await ws.send_text(json.dumps({
                    "type": "unsubscribed", "code": code, "quote_type": quote_type,
                }))

    except WebSocketDisconnect:
        manager.disconnect(ws)

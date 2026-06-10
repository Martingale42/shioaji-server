from functools import partial

import shioaji as sj
from fastapi import APIRouter, HTTPException, Request

from shioaji_server.models import (
    CancelOrderRequest,
    PlaceOrderRequest,
    TradeInfo,
    UpdateOrderRequest,
)

router = APIRouter(prefix="/api/orders", tags=["orders"])


def _resolve_contract(api, code: str, market: str):
    if market == "stock":
        return api.Contracts.Stocks[code]
    elif market == "futures":
        return api.Contracts.Futures[code]
    elif market == "options":
        return api.Contracts.Options[code]
    raise ValueError(f"Unknown market: {market}")


def _place_order_sync(api, contract, order):
    return api.place_order(contract, order)


def _update_order_sync(api, trade, **kwargs):
    api.update_order(trade=trade, **kwargs)


def _cancel_order_sync(api, trade):
    api.cancel_order(trade)


def _list_trades_sync(api):
    # A pure-futures (or pure-stock) login leaves the other account None.
    # update_status(None) raises inside the SDK -> 500, so guard symmetrically.
    if api.stock_account is not None:
        api.update_status(api.stock_account)
    if api.futopt_account is not None:
        api.update_status(api.futopt_account)
    return api.list_trades()


def _find_trade_sync(api, trade_id: str):
    for trade in api.list_trades():
        if str(trade.status.id) == trade_id:
            return trade
    return None


@router.post("/place", summary="Place order", description="Submit a new order. Stock orders use stock_account; futures/options use futopt_account. Market price orders (MKT/MKP) must use IOC or FOK.")
async def place_order(req: PlaceOrderRequest, request: Request) -> dict:
    sj_client = request.app.state.sj
    sj_client.require_connected()
    api = sj_client.api

    contract = _resolve_contract(api, req.code, req.market)

    action = sj.constant.Action.Buy if req.action == "Buy" else sj.constant.Action.Sell

    if req.market == "stock":
        price_type = getattr(sj.constant.StockPriceType, req.price_type)
        order_cond = getattr(sj.constant.StockOrderCond, req.order_cond)
        order_lot = getattr(sj.constant.StockOrderLot, req.order_lot)
        # The wire unit is shares (D1); Shioaji expects lots for common-lot stock
        # orders (1 lot = 1000 shares) and shares for odd-lot orders. Convert at
        # this SDK boundary so the gateway owns all venue-unit knowledge.
        if order_lot == sj.constant.StockOrderLot.Common:
            if req.quantity % 1000 != 0:
                raise HTTPException(
                    status_code=422,
                    detail=f"Common-lot quantity must be a multiple of 1000 shares, was {req.quantity}",
                )
            sj_quantity = req.quantity // 1000  # shares -> lots at the SDK boundary
        else:
            sj_quantity = req.quantity  # odd-lot orders are share-denominated in Shioaji
        order = api.Order(
            price=req.price,
            quantity=sj_quantity,
            action=action,
            price_type=price_type,
            order_type=getattr(sj.constant.OrderType, req.order_type),
            order_cond=order_cond,
            order_lot=order_lot,
            account=api.stock_account,
            custom_field=req.custom_field[:6],
        )
    else:
        price_type = getattr(sj.constant.FuturesPriceType, req.price_type)
        sj_quantity = req.quantity  # futures/options quantity is contracts, unchanged
        order = api.Order(
            price=req.price,
            quantity=sj_quantity,
            action=action,
            price_type=price_type,
            order_type=getattr(sj.constant.OrderType, req.order_type),
            octype=sj.constant.FuturesOCType.Auto,
            account=api.futopt_account,
            custom_field=req.custom_field[:6],
        )

    try:
        trade = await sj_client.run_sync(_place_order_sync, api, contract, order)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Second line of defense against venue rejection. This catches only rejects
    # the SDK surfaces synchronously (status already Failed/Inactive on return).
    # In simulation, off-tick / out-of-band orders return PendingSubmit here and
    # are rejected asynchronously later via the order-event stream (op_code != "00");
    # that async path is handled downstream in the NT exec client (plan Task 3.3).
    status = str(trade.status.status)
    if status.split(".")[-1] in ("Failed", "Inactive"):
        raise HTTPException(status_code=422, detail=f"Order rejected by venue: {status}")

    return {
        "trade_id": str(trade.status.id),
        "code": req.code,
        "action": req.action,
        "status": str(trade.status.status),
    }


@router.put("/update", summary="Modify order", description="Change the price or reduce the quantity of an existing order. Quantity can only be decreased, not increased. Returns 404 if trade_id not found.")
async def update_order(req: UpdateOrderRequest, request: Request) -> dict:
    sj_client = request.app.state.sj
    sj_client.require_connected()
    api = sj_client.api

    trade = await sj_client.run_sync(_find_trade_sync, api, req.trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail=f"Trade {req.trade_id} not found")
    kwargs = {}
    if req.price is not None:
        kwargs["price"] = req.price
    if req.quantity is not None:
        kwargs["qty"] = req.quantity
    try:
        await sj_client.run_sync(partial(_update_order_sync, api, trade, **kwargs))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "trade_id": req.trade_id}


@router.delete("/cancel", summary="Cancel order", description="Cancel an existing order by trade_id. Returns 404 if trade_id not found.")
async def cancel_order(req: CancelOrderRequest, request: Request) -> dict:
    sj_client = request.app.state.sj
    sj_client.require_connected()
    api = sj_client.api

    trade = await sj_client.run_sync(_find_trade_sync, api, req.trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail=f"Trade {req.trade_id} not found")
    try:
        await sj_client.run_sync(_cancel_order_sync, api, trade)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "trade_id": req.trade_id}


@router.get("/trades", response_model=list[TradeInfo], summary="List all trades", description="Returns all submitted orders/trades for the current session. Refreshes order status before returning.")
async def list_trades(request: Request) -> list[dict]:
    sj_client = request.app.state.sj
    sj_client.require_connected()
    api = sj_client.api

    trades = await sj_client.run_sync(_list_trades_sync, api)
    return [
        {
            "trade_id": str(t.status.id),
            "code": t.contract.code,
            "action": str(t.order.action),
            "price": float(t.order.price),
            "quantity": int(t.order.quantity),
            "status": str(t.status.status),
            "order_type": str(t.order.order_type),
            "price_type": str(t.order.price_type),
            "custom_field": getattr(t.order, "custom_field", ""),
        }
        for t in trades
    ]

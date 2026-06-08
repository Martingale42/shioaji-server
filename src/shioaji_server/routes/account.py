from fastapi import APIRouter, HTTPException, Query, Request

from shioaji_server.models import AccountBalance, MarginInfo, Position, ProfitLoss, UsageResponse

router = APIRouter(prefix="/api/account", tags=["account"])


def _list_positions_sync(api, account) -> list[dict]:
    positions = api.list_positions(account)
    return [
        {
            "code": p.code,
            "direction": str(p.direction),
            "quantity": int(p.quantity),
            "price": float(p.price),
            "last_price": float(p.last_price),
            "pnl": float(p.pnl),
            "yd_quantity": int(p.yd_quantity),
        }
        for p in positions
    ]


def _account_balance_sync(api) -> dict:
    balance = api.account_balance()
    return {
        "date": str(balance.date),
        "balance": float(balance.acc_balance),
    }


def _margin_sync(api, account) -> dict:
    m = api.margin(account)
    return {
        "yesterday_balance": float(m.yesterday_balance),
        "today_balance": float(m.today_balance),
        "available_margin": float(m.available_margin),
        "risk_indicator": float(m.risk_indicator),
    }


def _list_profit_loss_sync(api, account) -> list[dict]:
    pnl_list = api.list_profit_loss(account)
    return [
        {
            "code": p.code,
            "quantity": int(p.quantity),
            "buy_price": float(p.buy_price),
            "sell_price": float(p.sell_price),
            "pnl": float(p.pnl),
            "pr_ratio": float(p.pr_ratio),
        }
        for p in pnl_list
    ]


@router.get("/positions", response_model=list[Position], summary="List positions", description="Returns current holding positions for stock or futures/options account.")
async def list_positions(
    request: Request,
    market: str = Query("stock", description="'stock' or 'futures'"),
) -> list[dict]:
    sj = request.app.state.sj
    sj.require_connected()
    if market == "stock":
        if sj.api.stock_account is None:
            raise HTTPException(status_code=400, detail="No stock account available")
        account = sj.api.stock_account
    else:
        if sj.api.futopt_account is None:
            raise HTTPException(status_code=400, detail="No futures/options account available")
        account = sj.api.futopt_account
    return await sj.run_sync(_list_positions_sync, sj.api, account)


@router.get("/balance", response_model=AccountBalance, summary="Account balance", description="Returns stock account balance (TWD).")
async def account_balance(request: Request) -> dict:
    sj = request.app.state.sj
    sj.require_connected()
    return await sj.run_sync(_account_balance_sync, sj.api)


@router.get("/margin", response_model=MarginInfo, summary="Margin info", description="Returns futures/options margin account details. Returns 400 if no futures account.")
async def margin(request: Request) -> dict:
    sj = request.app.state.sj
    sj.require_connected()
    if sj.api.futopt_account is None:
        raise HTTPException(status_code=400, detail="No futures/options account available")
    return await sj.run_sync(_margin_sync, sj.api, sj.api.futopt_account)


@router.get("/pnl", response_model=list[ProfitLoss], summary="Profit & loss", description="Returns realized profit/loss records for the stock account.")
async def profit_loss(request: Request) -> list[dict]:
    sj = request.app.state.sj
    sj.require_connected()
    if sj.api.stock_account is None:
        raise HTTPException(status_code=400, detail="No stock account available")
    return await sj.run_sync(_list_profit_loss_sync, sj.api, sj.api.stock_account)


def _usage_sync(api) -> dict:
    u = api.usage()
    limit_bytes = u.limit_bytes
    used_bytes = u.bytes
    remaining = u.remaining_bytes
    return {
        "connections": u.connections,
        "bytes": used_bytes,
        "limit_bytes": limit_bytes,
        "remaining_bytes": remaining,
        "used_mb": round(used_bytes / 1_048_576, 2),
        "limit_mb": round(limit_bytes / 1_048_576, 2),
        "remaining_mb": round(remaining / 1_048_576, 2),
        "remaining_pct": round(remaining / limit_bytes * 100, 2) if limit_bytes > 0 else 0.0,
    }


@router.get("/usage", response_model=UsageResponse, summary="API usage status", description="Returns current daily traffic usage, quota limit, and remaining bytes. Use to monitor quota before heavy data fetching.")
async def usage(request: Request) -> dict:
    sj = request.app.state.sj
    sj.require_connected()
    return await sj.run_sync(_usage_sync, sj.api)

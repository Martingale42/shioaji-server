from fastapi import APIRouter, HTTPException, Query, Request

from shioaji_server.models import AccountBalance, MarginInfo, Position, ProfitLoss

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


@router.get("/positions", response_model=list[Position])
async def list_positions(
    request: Request,
    market: str = Query("stock", description="'stock' or 'futures'"),
) -> list[dict]:
    sj = request.app.state.sj
    sj.require_connected()
    if market != "stock" and sj.api.futopt_account is None:
        raise HTTPException(status_code=400, detail="No futures/options account available")
    account = sj.api.stock_account if market == "stock" else sj.api.futopt_account
    return await sj.run_sync(_list_positions_sync, sj.api, account)


@router.get("/balance", response_model=AccountBalance)
async def account_balance(request: Request) -> dict:
    sj = request.app.state.sj
    sj.require_connected()
    return await sj.run_sync(_account_balance_sync, sj.api)


@router.get("/margin", response_model=MarginInfo)
async def margin(request: Request) -> dict:
    sj = request.app.state.sj
    sj.require_connected()
    if sj.api.futopt_account is None:
        raise HTTPException(status_code=400, detail="No futures/options account available")
    return await sj.run_sync(_margin_sync, sj.api, sj.api.futopt_account)


@router.get("/pnl", response_model=list[ProfitLoss])
async def profit_loss(request: Request) -> list[dict]:
    sj = request.app.state.sj
    sj.require_connected()
    return await sj.run_sync(_list_profit_loss_sync, sj.api, sj.api.stock_account)

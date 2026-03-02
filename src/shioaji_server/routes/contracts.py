from functools import partial

from fastapi import APIRouter, HTTPException, Request

from shioaji_server.models import FuturesContract, OptionsContract, StockContract

router = APIRouter(prefix="/api/contracts", tags=["contracts"])


def _stock_to_dict(contract) -> dict:
    """Convert Shioaji stock contract to serializable dict."""
    return {
        "code": contract.code,
        "symbol": contract.symbol,
        "name": contract.name,
        "exchange": str(contract.exchange),
        "category": contract.category,
        "limit_up": float(contract.limit_up),
        "limit_down": float(contract.limit_down),
        "reference": float(contract.reference),
        "update_date": contract.update_date,
        "day_trade": str(contract.day_trade),
    }


def _futures_to_dict(contract) -> dict:
    return {
        "code": contract.code,
        "symbol": contract.symbol,
        "name": contract.name,
        "category": contract.category,
        "delivery_month": contract.delivery_month,
        "delivery_date": contract.delivery_date,
        "underlying_kind": contract.underlying_kind,
        "limit_up": float(contract.limit_up),
        "limit_down": float(contract.limit_down),
        "reference": float(contract.reference),
        "update_date": contract.update_date,
    }


def _options_to_dict(contract) -> dict:
    d = _futures_to_dict(contract)
    d["strike_price"] = float(contract.strike_price)
    d["option_right"] = str(contract.option_right)
    return d


def _list_stocks(api) -> list[dict]:
    return [_stock_to_dict(c) for c in api.Contracts.Stocks]


def _list_futures(api) -> list[dict]:
    return [_futures_to_dict(c) for c in api.Contracts.Futures]


def _list_options(api) -> list[dict]:
    return [_options_to_dict(c) for c in api.Contracts.Options]


@router.get("/stocks", response_model=list[StockContract])
async def list_stocks(request: Request) -> list[dict]:
    sj = request.app.state.sj
    sj.require_connected()
    return await sj.run_sync(_list_stocks, sj.api)


@router.get("/stocks/{code}", response_model=StockContract)
async def get_stock(code: str, request: Request) -> dict:
    sj = request.app.state.sj
    sj.require_connected()
    contract = sj.api.Contracts.Stocks.get(code)
    if contract is None:
        raise HTTPException(status_code=404, detail=f"Stock {code} not found")
    return _stock_to_dict(contract)


@router.get("/futures", response_model=list[FuturesContract])
async def list_futures(request: Request) -> list[dict]:
    sj = request.app.state.sj
    sj.require_connected()
    return await sj.run_sync(_list_futures, sj.api)


@router.get("/options", response_model=list[OptionsContract])
async def list_options(request: Request) -> list[dict]:
    sj = request.app.state.sj
    sj.require_connected()
    return await sj.run_sync(_list_options, sj.api)

from fastapi import APIRouter, HTTPException, Query, Request

from shioaji_server.models import KBarsResponse, SnapshotData, TicksResponse

router = APIRouter(prefix="/api/market", tags=["market_data"])


def _resolve_contract(api, code: str, market: str):
    if market == "stock":
        return api.Contracts.Stocks[code]
    elif market == "futures":
        return api.Contracts.Futures[code]
    elif market == "options":
        return api.Contracts.Options[code]
    raise HTTPException(status_code=400, detail=f"Unknown market: {market}")


def _fetch_snapshots(api, contracts) -> list[dict]:
    snaps = api.snapshots(contracts)
    return [
        {
            "code": s.code,
            "exchange": str(s.exchange),
            "open": float(s.open),
            "high": float(s.high),
            "low": float(s.low),
            "close": float(s.close),
            "volume": int(s.volume),
            "total_volume": int(s.total_volume),
            "buy_price": float(s.buy_price),
            "buy_volume": float(s.buy_volume),
            "sell_price": float(s.sell_price),
            "sell_volume": float(s.sell_volume),
            "change_price": float(s.change_price),
            "change_rate": float(s.change_rate),
            "ts": int(s.ts),
        }
        for s in snaps
    ]


def _fetch_ticks(api, contract, date: str) -> dict:
    t = api.ticks(contract, date=date)
    return {
        "ts": list(t.ts),
        "close": [float(x) for x in t.close],
        "volume": list(t.volume),
        "bid_price": [float(x) for x in t.bid_price],
        "ask_price": [float(x) for x in t.ask_price],
        "tick_type": list(t.tick_type),
    }


def _fetch_kbars(api, contract, start: str, end: str) -> dict:
    k = api.kbars(contract, start=start, end=end)
    return {
        "ts": list(k.ts),
        "open": [float(x) for x in k.Open],
        "high": [float(x) for x in k.High],
        "low": [float(x) for x in k.Low],
        "close": [float(x) for x in k.Close],
        "volume": list(k.Volume),
    }


@router.get("/snapshots", response_model=list[SnapshotData])
async def snapshots(
    request: Request,
    codes: str = Query(..., description="Comma-separated contract codes, e.g. '2330,2317'"),
    market: str = Query("stock", description="'stock', 'futures', or 'options'"),
) -> list[dict]:
    sj = request.app.state.sj
    sj.require_connected()
    contracts = [_resolve_contract(sj.api, c, market) for c in codes.split(",")]
    return await sj.run_sync(_fetch_snapshots, sj.api, contracts)


@router.get("/ticks", response_model=TicksResponse)
async def ticks(
    request: Request,
    code: str = Query(..., description="Contract code, e.g. '2330'"),
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
    market: str = Query("stock", description="'stock', 'futures', or 'options'"),
) -> dict:
    sj = request.app.state.sj
    sj.require_connected()
    contract = _resolve_contract(sj.api, code, market)
    data = await sj.run_sync(_fetch_ticks, sj.api, contract, date)
    data["code"] = code
    return data


@router.get("/kbars", response_model=KBarsResponse)
async def kbars(
    request: Request,
    code: str = Query(..., description="Contract code, e.g. '2330'"),
    start: str = Query(..., description="Start date YYYY-MM-DD"),
    end: str = Query(..., description="End date YYYY-MM-DD"),
    market: str = Query("stock", description="'stock', 'futures', or 'options'"),
) -> dict:
    sj = request.app.state.sj
    sj.require_connected()
    contract = _resolve_contract(sj.api, code, market)
    data = await sj.run_sync(_fetch_kbars, sj.api, contract, start, end)
    data["code"] = code
    return data

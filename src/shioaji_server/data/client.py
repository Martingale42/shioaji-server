"""Async httpx wrapper for the Shioaji gateway REST API."""

import asyncio

import httpx


class ShioajiGatewayClient:
    """Thin async client for the Shioaji gateway server.

    Rate-limits quote queries to ~10 concurrent requests with a small delay
    between batches to stay under the 50 req / 5 sec Sinopac limit.
    """

    def __init__(self, base_url: str = "http://localhost:8000"):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=60.0)
        self._sem = asyncio.Semaphore(10)

    async def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        async with self._sem:
            resp = await self._client.get(path, params=params)
            resp.raise_for_status()
            return resp

    async def get_stock_contracts(self) -> list[dict]:
        """GET /api/contracts/stocks → all stock contracts."""
        resp = await self._get("/api/contracts/stocks")
        return resp.json()

    async def get_snapshots(
        self, codes: list[str], batch_size: int = 500
    ) -> list[dict]:
        """GET /api/market/snapshots — auto-batches codes by *batch_size*.

        Skips batches that return server errors (e.g. outside market hours).
        """
        results: list[dict] = []
        for i in range(0, len(codes), batch_size):
            batch = codes[i : i + batch_size]
            try:
                resp = await self._get(
                    "/api/market/snapshots",
                    params={"codes": ",".join(batch), "market": "stock"},
                )
                results.extend(resp.json())
            except httpx.HTTPStatusError as e:
                print(f"  WARNING: snapshot batch {i//batch_size + 1} failed: {e.response.status_code}")
            if i + batch_size < len(codes):
                await asyncio.sleep(0.5)
        return results

    async def get_usage(self) -> dict:
        """GET /api/account/usage → current traffic quota status."""
        resp = await self._get("/api/account/usage")
        return resp.json()

    async def get_ticks(self, code: str, date: str) -> dict:
        """GET /api/market/ticks → tick-by-tick trades for *code* on *date*."""
        resp = await self._get(
            "/api/market/ticks",
            params={"code": code, "date": date, "market": "stock"},
        )
        return resp.json()

    async def get_kbars(self, code: str, start: str, end: str) -> dict:
        """GET /api/market/kbars → 1-min OHLCV bars for *code*."""
        resp = await self._get(
            "/api/market/kbars",
            params={"code": code, "start": start, "end": end, "market": "stock"},
        )
        return resp.json()

    async def close(self):
        await self._client.aclose()

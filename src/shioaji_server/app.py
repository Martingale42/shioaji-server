from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from shioaji_server.client import ShioajiClient
from shioaji_server.routes.auth import router as auth_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.sj = ShioajiClient()
    yield
    app.state.sj.logout()


app = FastAPI(title="Shioaji Server", version="0.1.0", lifespan=lifespan)
app.include_router(auth_router)


@app.get("/api/health")
async def health(request: Request) -> dict:
    return {"status": "ok", "connected": request.app.state.sj.connected}

from fastapi import APIRouter, HTTPException, Request

from shioaji_server.models import LoginRequest, LoginResponse, StatusResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, request: Request) -> LoginResponse:
    sj = request.app.state.sj
    try:
        accounts = await sj.login(
            api_key=req.api_key,
            secret_key=req.secret_key,
            ca_path=req.ca_path,
            ca_passwd=req.ca_passwd,
            simulation=req.simulation,
        )
        sj.register_callbacks(request.app.state.ws_manager)
        return LoginResponse(accounts=accounts)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/logout")
async def logout(request: Request) -> dict:
    await request.app.state.sj.logout()
    return {"status": "ok"}


@router.get("/status", response_model=StatusResponse)
async def status(request: Request) -> StatusResponse:
    sj = request.app.state.sj
    return StatusResponse(connected=sj.connected, simulation=sj.simulation)

from fastapi import Request
from fastapi.responses import JSONResponse


async def runtime_error_handler(request: Request, exc: RuntimeError) -> JSONResponse:
    msg = str(exc)
    if "Not connected" in msg:
        return JSONResponse(status_code=503, content={"error": msg})
    if "Already connected" in msg:
        return JSONResponse(status_code=409, content={"error": msg})
    return JSONResponse(status_code=500, content={"error": msg})

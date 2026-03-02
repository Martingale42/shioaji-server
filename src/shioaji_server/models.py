from pydantic import BaseModel


class LoginRequest(BaseModel):
    api_key: str
    secret_key: str
    ca_path: str | None = None
    ca_passwd: str | None = None
    simulation: bool = False


class LoginResponse(BaseModel):
    accounts: list[dict]


class StatusResponse(BaseModel):
    connected: bool
    simulation: bool

from pydantic import BaseModel


# --- Auth ---


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


# --- Contracts ---


class StockContract(BaseModel):
    code: str
    symbol: str
    name: str
    exchange: str
    category: str
    limit_up: float
    limit_down: float
    reference: float
    update_date: str
    day_trade: str


class FuturesContract(BaseModel):
    code: str
    symbol: str
    name: str
    category: str
    delivery_month: str
    delivery_date: str
    underlying_kind: str
    limit_up: float
    limit_down: float
    reference: float
    update_date: str


class OptionsContract(BaseModel):
    code: str
    symbol: str
    name: str
    category: str
    delivery_month: str
    delivery_date: str
    strike_price: float
    option_right: str
    underlying_kind: str
    limit_up: float
    limit_down: float
    reference: float
    update_date: str


# --- Market Data ---


class SnapshotData(BaseModel):
    code: str
    exchange: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    total_volume: int
    buy_price: float
    buy_volume: int
    sell_price: float
    sell_volume: int
    change_price: float
    change_rate: float
    ts: int


class TicksResponse(BaseModel):
    code: str
    ts: list[int]
    close: list[float]
    volume: list[int]
    bid_price: list[float]
    ask_price: list[float]
    tick_type: list[int]


class KBarsResponse(BaseModel):
    code: str
    ts: list[int]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    volume: list[int]

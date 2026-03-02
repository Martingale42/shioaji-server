from enum import StrEnum

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
    buy_volume: float
    sell_price: float
    sell_volume: float
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


# --- Orders ---


class Action(StrEnum):
    BUY = "Buy"
    SELL = "Sell"


class PriceType(StrEnum):
    LMT = "LMT"
    MKT = "MKT"
    MKP = "MKP"


class OrderType(StrEnum):
    ROD = "ROD"
    IOC = "IOC"
    FOK = "FOK"


class OrderCond(StrEnum):
    CASH = "Cash"
    MARGIN_TRADING = "MarginTrading"
    SHORT_SELLING = "ShortSelling"


class OrderLot(StrEnum):
    COMMON = "Common"
    ODD = "Odd"
    INTRADAY_ODD = "IntradayOdd"
    FIXING = "Fixing"


class PlaceOrderRequest(BaseModel):
    code: str
    action: Action
    price: float
    quantity: int
    price_type: PriceType = PriceType.LMT
    order_type: OrderType = OrderType.ROD
    order_cond: OrderCond = OrderCond.CASH
    order_lot: OrderLot = OrderLot.COMMON
    market: str = "stock"  # "stock", "futures", "options"


class UpdateOrderRequest(BaseModel):
    trade_id: str
    price: float | None = None
    quantity: int | None = None


class CancelOrderRequest(BaseModel):
    trade_id: str


class TradeInfo(BaseModel):
    trade_id: str
    code: str
    action: str
    price: float
    quantity: int
    status: str
    order_type: str
    price_type: str


# --- Account ---


class Position(BaseModel):
    code: str
    direction: str
    quantity: int
    price: float
    last_price: float
    pnl: float
    yd_quantity: int


class AccountBalance(BaseModel):
    date: str
    balance: float


class MarginInfo(BaseModel):
    yesterday_balance: float
    today_balance: float
    available_margin: float
    risk_indicator: float


class ProfitLoss(BaseModel):
    code: str
    quantity: int
    buy_price: float
    sell_price: float
    pnl: float
    pr_ratio: float

from enum import StrEnum

from pydantic import BaseModel, Field


# --- Auth ---


class LoginRequest(BaseModel):
    """Manual login credentials. Use when auto-login is not configured."""

    api_key: str = Field(description="Sinopac API key")
    secret_key: str = Field(description="Sinopac secret key (shown once at creation)")
    ca_path: str | None = Field(default=None, description="Absolute path to CA certificate (.pfx), required for placing orders")
    ca_passwd: str | None = Field(default=None, description="CA certificate password (defaults to ID number)")
    simulation: bool = Field(default=False, description="True for simulation mode, False for live trading")


class LoginResponse(BaseModel):
    """Accounts available after successful login."""

    accounts: list[dict] = Field(description="List of accounts with account_type and account_id")


class StatusResponse(BaseModel):
    """Current connection status."""

    connected: bool = Field(description="Whether Shioaji SDK is connected")
    simulation: bool = Field(description="Whether running in simulation mode")


# --- Contracts ---


class StockContract(BaseModel):
    """Taiwan stock contract information."""

    code: str = Field(description="Stock code, e.g. '2330'")
    symbol: str = Field(description="Full symbol identifier")
    name: str = Field(description="Stock name, e.g. '台積電'")
    exchange: str = Field(description="Exchange name, e.g. 'TSE', 'OTC'")
    category: str = Field(description="Industry category code")
    limit_up: float = Field(description="Daily price limit up")
    limit_down: float = Field(description="Daily price limit down")
    reference: float = Field(description="Reference price (yesterday's close)")
    update_date: str = Field(description="Contract info update date")
    day_trade: str = Field(description="Day trading eligibility")


class FuturesContract(BaseModel):
    """Taiwan futures contract information."""

    code: str = Field(description="Futures code, e.g. 'TXFR1' (front month TX)")
    symbol: str = Field(description="Full symbol identifier")
    name: str = Field(description="Futures name")
    category: str = Field(description="Product category")
    delivery_month: str = Field(description="Delivery month, e.g. '202603'")
    delivery_date: str = Field(description="Delivery (expiry) date")
    underlying_kind: str = Field(description="Underlying type, e.g. 'I' for index")
    limit_up: float = Field(description="Daily price limit up")
    limit_down: float = Field(description="Daily price limit down")
    reference: float = Field(description="Reference price")
    update_date: str = Field(description="Contract info update date")


class OptionsContract(BaseModel):
    """Taiwan options contract information."""

    code: str = Field(description="Options code")
    symbol: str = Field(description="Full symbol identifier")
    name: str = Field(description="Options name")
    category: str = Field(description="Product category")
    delivery_month: str = Field(description="Delivery month")
    delivery_date: str = Field(description="Delivery (expiry) date")
    strike_price: float = Field(description="Strike price")
    option_right: str = Field(description="'Call' or 'Put'")
    underlying_kind: str = Field(description="Underlying type")
    limit_up: float = Field(description="Daily price limit up")
    limit_down: float = Field(description="Daily price limit down")
    reference: float = Field(description="Reference price")
    update_date: str = Field(description="Contract info update date")


# --- Market Data ---


class SnapshotData(BaseModel):
    """Real-time market snapshot for a single contract."""

    code: str = Field(description="Contract code")
    exchange: str = Field(description="Exchange name")
    open: float = Field(description="Opening price")
    high: float = Field(description="Highest price")
    low: float = Field(description="Lowest price")
    close: float = Field(description="Latest price")
    volume: int = Field(description="Latest tick volume")
    total_volume: int = Field(description="Accumulated total volume")
    buy_price: float = Field(description="Best bid price")
    buy_volume: float = Field(description="Best bid volume")
    sell_price: float = Field(description="Best ask price")
    sell_volume: float = Field(description="Best ask volume")
    change_price: float = Field(description="Price change from reference")
    change_rate: float = Field(description="Change rate (%)")
    ts: int = Field(description="Timestamp (nanoseconds)")


class TicksResponse(BaseModel):
    """Historical tick-by-tick trade data for a single day."""

    code: str = Field(description="Contract code")
    ts: list[int] = Field(description="Timestamps (nanoseconds)")
    close: list[float] = Field(description="Trade prices")
    volume: list[int] = Field(description="Trade volumes")
    bid_price: list[float] = Field(description="Bid prices at time of trade")
    ask_price: list[float] = Field(description="Ask prices at time of trade")
    tick_type: list[int] = Field(description="Tick type (1=buy, 2=sell)")


class KBarsResponse(BaseModel):
    """Historical OHLCV candlestick (K-bar) data."""

    code: str = Field(description="Contract code")
    ts: list[int] = Field(description="Bar timestamps (nanoseconds)")
    open: list[float] = Field(description="Open prices")
    high: list[float] = Field(description="High prices")
    low: list[float] = Field(description="Low prices")
    close: list[float] = Field(description="Close prices")
    volume: list[int] = Field(description="Volumes")


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
    """Place a new order. Market price orders (MKT/MKP) must use IOC or FOK, not ROD."""

    code: str = Field(description="Contract code, e.g. '2330' for stocks, 'TXFR1' for futures")
    action: Action = Field(description="Buy or Sell")
    price: float = Field(description="Order price (ignored for MKT orders)")
    quantity: int = Field(description="Order quantity (lots)")
    price_type: PriceType = Field(default=PriceType.LMT, description="LMT=limit, MKT=market, MKP=market range")
    order_type: OrderType = Field(default=OrderType.ROD, description="ROD=day, IOC=immediate-or-cancel, FOK=fill-or-kill")
    order_cond: OrderCond = Field(default=OrderCond.CASH, description="Cash/MarginTrading/ShortSelling (stocks only)")
    order_lot: OrderLot = Field(default=OrderLot.COMMON, description="Common/Odd/IntradayOdd/Fixing (stocks only)")
    market: str = Field(default="stock", description="'stock', 'futures', or 'options'")


class UpdateOrderRequest(BaseModel):
    """Modify an existing order. Can change price or reduce quantity (cannot increase)."""

    trade_id: str = Field(description="Trade ID from place order response")
    price: float | None = Field(default=None, description="New price (omit to keep current)")
    quantity: int | None = Field(default=None, description="New quantity — can only decrease, not increase")


class CancelOrderRequest(BaseModel):
    """Cancel an existing order."""

    trade_id: str = Field(description="Trade ID to cancel")


class TradeInfo(BaseModel):
    """Information about a submitted order/trade."""

    trade_id: str = Field(description="Unique trade identifier")
    code: str = Field(description="Contract code")
    action: str = Field(description="Buy or Sell")
    price: float = Field(description="Order price")
    quantity: int = Field(description="Order quantity")
    status: str = Field(description="Order status (e.g. PendingSubmit, Submitted, Filled, Cancelled, Failed)")
    order_type: str = Field(description="ROD/IOC/FOK")
    price_type: str = Field(description="LMT/MKT/MKP")


# --- Account ---


class Position(BaseModel):
    """Current holding position."""

    code: str = Field(description="Contract code")
    direction: str = Field(description="Long or Short")
    quantity: int = Field(description="Current quantity")
    price: float = Field(description="Average entry price")
    last_price: float = Field(description="Latest market price")
    pnl: float = Field(description="Unrealized profit/loss")
    yd_quantity: int = Field(description="Yesterday's quantity (T+1 settlement)")


class AccountBalance(BaseModel):
    """Stock account balance."""

    date: str = Field(description="Balance date")
    balance: float = Field(description="Account balance (TWD)")


class MarginInfo(BaseModel):
    """Futures/options margin account information."""

    yesterday_balance: float = Field(description="Previous day ending balance")
    today_balance: float = Field(description="Current day balance")
    available_margin: float = Field(description="Available margin for new orders")
    risk_indicator: float = Field(description="Risk indicator ratio (%)")


class ProfitLoss(BaseModel):
    """Realized profit/loss record."""

    code: str = Field(description="Contract code")
    quantity: int = Field(description="Settled quantity")
    buy_price: float = Field(description="Average buy price")
    sell_price: float = Field(description="Average sell price")
    pnl: float = Field(description="Realized profit/loss (TWD)")
    pr_ratio: float = Field(description="Profit/loss ratio (%)")


# --- Usage ---


class UsageResponse(BaseModel):
    """Shioaji API usage status for current session."""

    connections: int = Field(description="Active connection count")
    bytes: int = Field(description="Bytes consumed today")
    limit_bytes: int = Field(description="Daily traffic quota (bytes)")
    remaining_bytes: int = Field(description="Remaining bytes today")
    used_mb: float = Field(description="Bytes consumed today (MB)")
    limit_mb: float = Field(description="Daily traffic quota (MB)")
    remaining_mb: float = Field(description="Remaining traffic (MB)")
    remaining_pct: float = Field(description="Remaining traffic (%)")

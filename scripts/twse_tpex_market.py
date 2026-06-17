"""Cached official-data client for the 00981A top-300 universe pipeline.

Network I/O layer. Implements exactly the three endpoint families verified by the
Task 1 spike and documented in
``docs/reference/00981a-market-data-endpoints.md`` (the authoritative source for
every URL / param / JSON key used here — do NOT re-derive them):

  Family 1 — current issued common shares (anchor)
    TWSE: ``openapi.twse.com.tw/v1/opendata/t187ap03_L``  key ``已發行普通股數或TDR原股發行股數``
    TPEx: ``www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O`` key ``IssueShares``

  Family 2 — historical daily close (per-code-per-month)
    TWSE: ``www.twse.com.tw/exchangeReport/STOCK_DAY`` close = ``data[i][6]`` (ROC date)
    TPEx: ``www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock`` close = ``tables[0].data[i][6]``

  Family 3 — capital-change events (share deltas for point-in-time shares)
    Ex-dates: TWSE ``www.twse.com.tw/rwd/zh/exRight/TWT49U`` (filter the 權/息 column to 權)
    Share deltas: MOPS ``mopsov.twse.com.tw/server-java/t05st09sub`` (big5 HTML POST, cell[16])
    NOTE: the snapshot-only OpenAPI ``t187ap45_L`` is deliberately NOT used — it reflects
    only the latest declared period, not point-in-time history.

All pulls are cached to ``cache/twse_tpex/`` and skipped on re-run, rate-limited
(~0.75 s between official-site GETs per the spike volume estimate), resumable, and
on a transient HTTP error retry-then-skip-the-month with a logged warning.

This module depends ONLY on polars + httpx; it never imports nautilus_trader or
shioaji_server, so it remains runnable independent of the trading-engine env.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import httpx
import polars as pl

logger = logging.getLogger(__name__)

# --- Reproducible config (no magic numbers buried in call sites) -------------
CACHE_DIR = Path("cache/twse_tpex")
RATE_LIMIT_SECONDS = 0.75  # polite delay between official-site GETs (spike estimate)
HTTP_TIMEOUT_SECONDS = 30.0
MAX_RETRIES = 3  # transient-error retries before skipping a code-month
RETRY_BACKOFF_SECONDS = 2.0
PAR_VALUE = 10.0  # NTD par value used to derive shares from paid-in capital
ROC_OFFSET = 1911  # ROC year + 1911 = Gregorian year

# Endpoint URLs (cited from docs/reference/00981a-market-data-endpoints.md).
TWSE_CURRENT_SHARES_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_CURRENT_SHARES_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
TWSE_STOCK_DAY_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
TPEX_TRADING_STOCK_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock"
TWSE_TWT49U_URL = "https://www.twse.com.tw/rwd/zh/exRight/TWT49U"
MOPS_DIVIDEND_URL = "https://mopsov.twse.com.tw/server-java/t05st09sub"

# JSON keys for Family 1 (EXACT, per spike doc).
TWSE_CODE_KEY = "公司代號"
TWSE_NAME_KEY = "公司簡稱"
TWSE_SHARES_KEY = "已發行普通股數或TDR原股發行股數"
TWSE_CAPITAL_KEY = "實收資本額"
TPEX_CODE_KEY = "SecuritiesCompanyCode"
TPEX_NAME_KEY = "CompanyAbbreviation"
TPEX_SHARES_KEY = "IssueShares"
TPEX_CAPITAL_KEY = "Paidin.Capital.NTDollars"

_COMMON_CODE_RE = re.compile(
    r"^\d{4}$"
)  # 4-digit numeric = common stock (excl. 91xxxx TDR)

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


# --- ROC date helpers --------------------------------------------------------
def _parse_roc_date(token: str) -> date | None:
    """Parse an ROC-formatted date token into a Gregorian ``date``.

    Definition: Convert a Republic-of-China calendar date string to Gregorian.
    Formula:    gregorian_year = roc_year + 1911 (ROC_OFFSET).
    Domain:     ``token`` in either slash form ``114/05/02`` (STOCK_DAY /
                tradingStock) or 年月日 form ``114年05月27日`` (TWT49U); whitespace
                tolerated; non-date or malformed tokens return None (caller skips).
    Returns:    A ``datetime.date`` or None on parse failure.
    """
    token = token.strip().replace(" ", "")
    # Normalise the 年月日 form to slash-delimited, then parse uniformly.
    token = token.replace("年", "/").replace("月", "/").replace("日", "")
    parts = [p for p in token.split("/") if p != ""]
    if len(parts) != 3:
        return None
    try:
        roc_y, mm, dd = (int(p) for p in parts)
    except ValueError:
        return None
    try:
        return date(roc_y + ROC_OFFSET, mm, dd)
    except ValueError:
        return None


def _months_between(start: date, end: date) -> list[tuple[int, int]]:
    """Enumerate the (year, month) pairs spanning ``[start, end]`` inclusive.

    Definition: Calendar months touched by the closed interval ``[start, end]``.
    Formula:    iterate (y, m) from (start.year, start.month) to (end.year, end.month).
    Domain:     ``start <= end``; both Gregorian dates.
    Returns:    Ordered list of ``(year, month)`` tuples, oldest first.
    """
    out: list[tuple[int, int]] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _to_int(raw: str | int | float | None) -> int | None:
    """Coerce a possibly comma/whitespace-laden numeric string to int.

    Definition: Parse an integer share count from raw official-data text.
    Formula:    strip commas/whitespace, truncate any fractional part to int.
    Domain:     ``raw`` may be str ``"160,200,000"``, numeric, or None/blank.
    Returns:    int, or None if not parseable.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    cleaned = raw.replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def _http_get_json(
    client: httpx.Client, url: str, params: dict[str, str] | None = None
) -> dict | list | None:
    """GET a URL and decode JSON, retry-then-give-up on transient errors.

    Returns the decoded JSON on success, or None after MAX_RETRIES failures
    (caller logs + skips). Rate-limits AFTER each network attempt.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.get(url, params=params, timeout=HTTP_TIMEOUT_SECONDS)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "GET %s params=%s failed (attempt %d/%d): %s",
                url,
                params,
                attempt,
                MAX_RETRIES,
                exc,
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
        finally:
            time.sleep(RATE_LIMIT_SECONDS)
    return None


# --- Family 1: current issued common shares ----------------------------------
def fetch_current_shares(cache_dir: Path = CACHE_DIR) -> pl.DataFrame:
    """
    Definition: Current issued common-share count per listed TWSE+TPEx code.
    Formula:    shares = issued_common_shares (or paid_in_capital / par_value=10).
    Domain:     One row per common stock; ETFs/warrants/preferred excluded downstream.
    Returns:    DataFrame[code: str, name: str, shares: int, market: str]. The
                ``market`` column ('twse'|'tpex') lets the orchestrator route
                daily-close pulls to the right endpoint. Cached to parquet.
    """
    schema = {"code": pl.Utf8, "name": pl.Utf8, "shares": pl.Int64, "market": pl.Utf8}
    cache_path = cache_dir / "current_shares.parquet"
    if cache_path.exists():
        logger.info("cache hit: current_shares (%s)", cache_path)
        return pl.read_parquet(cache_path)

    cache_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    headers = {"User-Agent": _USER_AGENT, "accept": "application/json"}
    with httpx.Client(headers=headers) as client:
        twse = _http_get_json(client, TWSE_CURRENT_SHARES_URL)
        if isinstance(twse, list):
            rows.extend(
                _parse_current_shares_rows(
                    twse,
                    TWSE_CODE_KEY,
                    TWSE_NAME_KEY,
                    TWSE_SHARES_KEY,
                    TWSE_CAPITAL_KEY,
                    "twse",
                )
            )
        else:
            logger.warning("TWSE current-shares pull returned no list payload")

        tpex = _http_get_json(client, TPEX_CURRENT_SHARES_URL)
        if isinstance(tpex, list):
            rows.extend(
                _parse_current_shares_rows(
                    tpex,
                    TPEX_CODE_KEY,
                    TPEX_NAME_KEY,
                    TPEX_SHARES_KEY,
                    TPEX_CAPITAL_KEY,
                    "tpex",
                )
            )
        else:
            logger.warning("TPEx current-shares pull returned no list payload")

    df = pl.DataFrame(rows, schema=schema) if rows else pl.DataFrame(schema=schema)
    df = df.unique(subset=["code"], keep="first").sort("code")
    df.write_parquet(cache_path)
    logger.info("fetched current shares: %d common-stock rows", df.height)
    return df


def _parse_current_shares_rows(
    payload: list,
    code_key: str,
    name_key: str,
    shares_key: str,
    capital_key: str,
    market: str,
) -> list[dict[str, object]]:
    """Filter a t187ap03 payload to 4-digit common stocks with a valid share count.

    Definition: Extract (code, name, shares, market) for common stocks from a
                t187ap03 array.
    Formula:    shares = issued_common_shares, else paid_in_capital / PAR_VALUE.
    Domain:     ``payload`` rows are dicts; codes not matching ^\\d{4}$ (e.g. 91xxxx
                TDR) are dropped; rows with no derivable positive share count skipped.
    Returns:    List of {code, name, shares, market} dicts.
    """
    out: list[dict[str, object]] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        code = str(row.get(code_key, "")).strip()
        if not _COMMON_CODE_RE.match(code):
            continue
        shares = _to_int(row.get(shares_key))
        if shares is None or shares <= 0:
            capital = _to_int(row.get(capital_key))
            if capital is None or capital <= 0:
                continue
            shares = int(capital / PAR_VALUE)
        if shares <= 0:
            continue
        out.append(
            {
                "code": code,
                "name": str(row.get(name_key, "")).strip(),
                "shares": shares,
                "market": market,
            }
        )
    return out


# --- Family 2: historical daily close ----------------------------------------
def fetch_daily_close(
    codes: list[str], start: date, end: date, cache_dir: Path = CACHE_DIR
) -> pl.DataFrame:
    """
    Definition: Daily official closing price per code over [start, end].
    Formula:    close(code, d) from www.twse.com.tw STOCK_DAY (TWSE) / TPEx daily report.
    Domain:     Trading days only; per-code-per-month requests, rate-limited + resumable.
    Returns:    DataFrame[code: str, date: date, close: float]. Cached per code-month.
    """
    close_root = cache_dir / "close"
    months = _months_between(start, end)
    frames: list[pl.DataFrame] = []
    schema = {"code": pl.Utf8, "date": pl.Date, "close": pl.Float64}

    with httpx.Client(
        headers={"User-Agent": _USER_AGENT, "accept": "application/json"}
    ) as client:
        for code in codes:
            is_tpex = _is_tpex_code(code, codes)
            for year, month in months:
                month_path = close_root / code / f"{year}{month:02d}.parquet"
                if month_path.exists():
                    frames.append(pl.read_parquet(month_path))
                    continue
                if is_tpex:
                    rows = _fetch_tpex_month_close(client, code, year, month)
                else:
                    rows = _fetch_twse_month_close(client, code, year, month)
                if rows is None:
                    logger.warning(
                        "skip code-month %s %d-%02d (transient error)",
                        code,
                        year,
                        month,
                    )
                    continue
                month_df = (
                    pl.DataFrame(rows, schema=schema)
                    if rows
                    else pl.DataFrame(schema=schema)
                )
                month_df = month_df.filter(
                    (pl.col("date") >= start) & (pl.col("date") <= end)
                )
                month_path.parent.mkdir(parents=True, exist_ok=True)
                month_df.write_parquet(month_path)
                frames.append(month_df)

    if not frames:
        return pl.DataFrame(schema=schema)
    out = (
        pl.concat(frames)
        .unique(subset=["code", "date"], keep="last")
        .sort(["code", "date"])
    )
    return out


def _is_tpex_code(code: str, all_codes: list[str]) -> bool:
    """Heuristic: route a code to the TPEx endpoint if it is a known OTC code.

    Definition: Decide whether a code's daily close comes from TPEx vs TWSE.
    Domain:     The orchestrator splits codes by market; standalone callers may not.
                We resolve membership from a per-call OTC set when present (set on the
                ``codes`` list via a sentinel) — otherwise default to TWSE. The
                orchestrator passes market-tagged codes via ``fetch_daily_close``'s
                caller, which separates markets explicitly; see build script.
    Returns:    True if TPEx, else False.
    """
    # Resolution is provided out-of-band by the orchestrator (it queries each market
    # separately). For a flat code list with no market info, default to TWSE; the
    # orchestrator never relies on this fallback because it tags markets up front.
    return code in _TPEX_CODES


# Module-level OTC code set; the orchestrator populates this before calling
# fetch_daily_close so routing is explicit and testable, not guessed per-code.
_TPEX_CODES: set[str] = set()


def set_tpex_codes(codes: set[str]) -> None:
    """Register which codes are TPEx (OTC) so daily-close routing is explicit.

    Definition: Set the module's OTC routing table used by ``fetch_daily_close``.
    Domain:     Called once by the orchestrator with the TPEx common-stock codes
                derived from the TPEx t187ap03 dataset.
    Returns:    None (mutates module state).
    """
    global _TPEX_CODES
    _TPEX_CODES = set(codes)


def _fetch_twse_month_close(
    client: httpx.Client, code: str, year: int, month: int
) -> list[dict[str, object]] | None:
    """Pull one TWSE code-month of daily closes from STOCK_DAY.

    Returns parsed rows, an empty list if the month has no data (stat != OK), or
    None on transient HTTP error (caller skips the month and logs).
    """
    params = {"response": "json", "date": f"{year}{month:02d}01", "stockNo": code}
    payload = _http_get_json(client, TWSE_STOCK_DAY_URL, params)
    if payload is None:
        return None
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return []  # 很抱歉... no-data month
    rows: list[dict[str, object]] = []
    for rec in payload.get("data", []):
        if not isinstance(rec, list) or len(rec) < 7:
            continue
        d = _parse_roc_date(str(rec[0]))
        close = _to_float(rec[6])
        if d is None or close is None:
            continue
        rows.append({"code": code, "date": d, "close": close})
    return rows


def _fetch_tpex_month_close(
    client: httpx.Client, code: str, year: int, month: int
) -> list[dict[str, object]] | None:
    """Pull one TPEx code-month of daily closes from tradingStock.

    Returns parsed rows, empty list if no data, or None on transient HTTP error.
    """
    params = {
        "code": code,
        "date": f"{year}/{month:02d}/01",
        "id": "",
        "response": "json",
    }
    payload = _http_get_json(client, TPEX_TRADING_STOCK_URL, params)
    if payload is None:
        return None
    if not isinstance(payload, dict):
        return []
    tables = payload.get("tables")
    if not isinstance(tables, list) or not tables:
        return []
    data = tables[0].get("data") if isinstance(tables[0], dict) else None
    if not isinstance(data, list) or not data:
        return []
    rows: list[dict[str, object]] = []
    for rec in data:
        if not isinstance(rec, list) or len(rec) < 7:
            continue
        d = _parse_roc_date(str(rec[0]))
        close = _to_float(rec[6])
        if d is None or close is None:
            continue
        rows.append({"code": code, "date": d, "close": close})
    return rows


_MISSING_CLOSE_TOKENS = frozenset({"", "--", "---", "0.00"})


def _to_float(raw: str | int | float | None) -> float | None:
    """Coerce a possibly comma-laden numeric string to float.

    Definition: Parse a closing price from raw official-data text, mapping
                missing/suspended-day sentinels to None (not a fabricated 0.0).
    Formula:    value = float(strip_commas_whitespace(raw)); a blank cell,
                ``"--"``/``"---"`` (suspended), or the ``"0.00"`` no-trade
                sentinel all map to None (price absent that day).
    Domain:     ``raw`` like ``"967.00"`` / ``"1,234.50"`` (real close) or one of
                ``""`` / ``"--"`` / ``"---"`` / ``"0.00"`` (no price that day). A
                fabricated 0.0 close would yield a spurious zero-mktcap row that can
                split a membership interval, so all of these are treated as missing.
    Returns:    float close price, or None if missing/unparseable. Unit: NTD/share.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    cleaned = raw.replace(",", "").strip()
    if cleaned in _MISSING_CLOSE_TOKENS:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


# --- Family 3: capital-change events -----------------------------------------
def fetch_capital_events(
    codes: list[str], start: date, end: date, cache_dir: Path = CACHE_DIR
) -> pl.DataFrame:
    """
    Definition: Share-count change events (capital increase/decrease, stock dividend).
    Formula:    each event = (code, effective_date, shares_delta).
    Domain:     Events with effective_date in [start, end]; empty for unchanged codes.
    Returns:    DataFrame[code: str, date: date, shares_delta: int, kind: str].
    """
    cache_path = cache_dir / "capital_events.parquet"
    schema = {
        "code": pl.Utf8,
        "date": pl.Date,
        "shares_delta": pl.Int64,
        "kind": pl.Utf8,
    }
    if cache_path.exists():
        logger.info("cache hit: capital_events (%s)", cache_path)
        return pl.read_parquet(cache_path)

    cache_dir.mkdir(parents=True, exist_ok=True)
    code_set = set(codes)

    with httpx.Client(headers={"User-Agent": _USER_AGENT}) as client:
        # 3a. Ex-dates that carry a stock-dividend (權) component, in-window.
        ex_events = _fetch_twt49u_ex_dates(client, start, end)
        ex_events = [e for e in ex_events if e["code"] in code_set]
        # 3b. Per-(market, ROC year) bulk POST -> share deltas (cell[16]).
        roc_years = sorted({d.year - ROC_OFFSET for d in (start, end)})
        # The dividend belongs to the PRIOR fiscal year (e.g. mid-2025 ex -> YEAR=113).
        roc_years = sorted({y - 1 for y in roc_years} | set(roc_years))
        deltas = _fetch_mops_share_deltas(client, roc_years)

    rows: list[dict[str, object]] = []
    for e in ex_events:
        delta = deltas.get(e["code"])
        if delta is None or delta == 0:
            continue
        rows.append(
            {
                "code": e["code"],
                "date": e["date"],
                "shares_delta": delta,
                "kind": e["kind"],
            }
        )

    df = pl.DataFrame(rows, schema=schema) if rows else pl.DataFrame(schema=schema)
    df = df.unique(subset=["code", "date"], keep="last").sort(["code", "date"])
    df.write_parquet(cache_path)
    logger.info("fetched capital events: %d in-window share-changing events", df.height)
    return df


def _fetch_twt49u_ex_dates(
    client: httpx.Client, start: date, end: date
) -> list[dict[str, object]]:
    """Pull TWT49U ex-right/ex-dividend records that carry a stock-dividend (權).

    Definition: In-window ex-dates whose 權/息 column contains 權 (shares change).
    Domain:     Returns 4-digit common-stock codes only; pure 息 (cash-only) skipped.
    Returns:    List of {code, date, kind} where kind is the raw 權/息 label.
    """
    params = {
        "startDate": start.strftime("%Y%m%d"),
        "endDate": end.strftime("%Y%m%d"),
        "response": "json",
    }
    payload = _http_get_json(client, TWSE_TWT49U_URL, params)
    out: list[dict[str, object]] = []
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        logger.warning("TWT49U returned no OK payload; no ex-date events")
        return out
    for rec in payload.get("data", []):
        if not isinstance(rec, list) or len(rec) < 7:
            continue
        code = str(rec[1]).strip()
        if not _COMMON_CODE_RE.match(code):
            continue
        kind = str(rec[6]).strip()  # 權 / 息 / 權息
        if "權" not in kind:
            continue  # cash-only dividend => no share change
        d = _parse_roc_date(str(rec[0]))
        if d is None or d < start or d > end:
            continue
        out.append({"code": code, "date": d, "kind": kind})
    return out


def _fetch_mops_share_deltas(
    client: httpx.Client, roc_years: list[int]
) -> dict[str, int]:
    """Pull MOPS t05st09sub dividend tables and extract per-code share deltas.

    Definition: Map code -> stock-dividend share delta (cell[16]) summed over the
                requested ROC fiscal years across sii/otc markets.
    Domain:     big5 HTML; cell[0] = "CODE - NAME", cell[16] = 股東配股總股數.
                A code appearing in multiple years accumulates (rare in a 13-mo window).
    Returns:    {code: total_shares_delta} for codes with a non-zero stock-dividend.
    """
    totals: dict[str, int] = {}
    for typek in ("sii", "otc"):
        for roc_year in roc_years:
            data = {
                "TYPEK": typek,
                "YEAR": str(roc_year),
                "step": "1",
                "firstin": "1",
            }
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    resp = client.post(
                        MOPS_DIVIDEND_URL, data=data, timeout=HTTP_TIMEOUT_SECONDS
                    )
                    resp.raise_for_status()
                    html = resp.content.decode("big5", errors="replace")
                    break
                except httpx.HTTPError as exc:
                    logger.warning(
                        "MOPS POST %s YEAR=%d failed (attempt %d/%d): %s",
                        typek,
                        roc_year,
                        attempt,
                        MAX_RETRIES,
                        exc,
                    )
                    if attempt < MAX_RETRIES:
                        time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                    html = ""
                finally:
                    time.sleep(RATE_LIMIT_SECONDS)
            else:
                continue
            if not html:
                continue
            for code, delta in _parse_mops_dividend_html(html).items():
                totals[code] = totals.get(code, 0) + delta
    return {c: d for c, d in totals.items() if d != 0}


class _MopsTableParser(HTMLParser):
    """Collect rows of <td> cell text from MOPS big5 dividend HTML tables."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:  # noqa: ARG002
        if tag == "tr":
            self._row = []
        elif tag == "td" and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._row is not None and self._cell is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _parse_mops_dividend_html(html: str) -> dict[str, int]:
    """Parse a MOPS t05st09sub HTML page into {code: share_delta} (cell[16]).

    Definition: Extract the 股東配股總股數 (shareholder stock-dividend total shares)
                per company from the big5 dividend-summary table.
    Formula:    delta(code) = int(cell[16]) where cell[0] = "CODE - NAME".
    Domain:     Only rows whose cell[0] starts with a 4-digit common code and whose
                cell[16] parses to a positive int are kept.
    Returns:    {code: share_delta}.
    """
    parser = _MopsTableParser()
    parser.feed(html)
    out: dict[str, int] = {}
    for row in parser.rows:
        if len(row) <= 16:
            continue
        head = row[0].strip()
        match = re.match(r"^(\d{4})\b", head)
        if not match:
            continue
        code = match.group(1)
        delta = _to_int(row[16])
        if delta is None or delta <= 0:
            continue
        out[code] = out.get(code, 0) + delta
    return out

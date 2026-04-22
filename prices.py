"""
prices.py — Live price fetching via yfinance.

The only module in this project that calls yfinance.
All other modules receive price data as plain dicts.
"""

import sys
import warnings
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Dict, Iterator, List

import pandas as pd
import yfinance as yf

HOLIDAY_WINDOW_DAYS = 7  # lookback window to find the prior close around a target date


class PriceFetchError(ValueError):
    pass


@contextmanager
def yf_warnings() -> Iterator[None]:
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', category=UserWarning, module='yfinance')
        yield


def _last_close(series: pd.Series, label: str) -> float:
    s = series.dropna()
    if s.empty:
        raise PriceFetchError(f"No usable price data for {label}.")
    return float(s.iloc[-1])


def fetch_price(ticker: str) -> float:
    with yf_warnings():
        data = yf.download(ticker, period='5d', progress=False, auto_adjust=True)

    if data.empty:
        raise PriceFetchError(
            f"No price data for '{ticker}'. Check the symbol and your connection."
        )

    close = data['Close']
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]

    return _last_close(close, f"'{ticker}'")


def fetch_prices_batch(tickers: List[str]) -> Dict[str, float]:
    """Batch close prices. Tickers that fail fetch are silently omitted."""
    if not tickers:
        return {}

    with yf_warnings():
        data = yf.download(tickers, period='5d', progress=False, auto_adjust=True)

    if data.empty:
        return {}

    close = data['Close'] if isinstance(data.columns, pd.MultiIndex) else data[['Close']]
    if isinstance(close, pd.Series):
        close = close.to_frame()

    last = close.ffill().iloc[-1]
    prices = {
        t: float(last[t])
        for t in tickers
        if t in last.index and pd.notna(last[t])
    }
    missing = [t for t in tickers if t not in prices]
    if missing:
        print(f"  Warning: price unavailable for {', '.join(missing)}", file=sys.stderr)
    return prices


def fetch_historical_price(ticker: str, date_str: str) -> float:
    """Closing price on or nearest to date_str; weekends/holidays resolve to the prior trading day."""
    target = date.fromisoformat(date_str)
    start  = (target - timedelta(days=HOLIDAY_WINDOW_DAYS)).isoformat()
    end    = (target + timedelta(days=1)).isoformat()

    with yf_warnings():
        data = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)

    if data.empty:
        raise PriceFetchError(
            f"No price data for '{ticker}' around {date_str}. "
            "Check the symbol and date, or pass --price manually."
        )

    close = data['Close']
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]

    return _last_close(close, f"'{ticker}' around {date_str}")


def fetch_label(ticker: str) -> str:
    """Human-readable name, falls back to ticker symbol."""
    try:
        info = yf.Ticker(ticker).info
        return info.get('shortName') or info.get('longName') or ticker
    except (AttributeError, KeyError, TypeError):
        return ticker


def fetch_prices_with_change(tickers: List[str]) -> Dict[str, Dict[str, float]]:
    """
    Returns {ticker: {"price": float, "prev_close": float}} in one network call.
    prev_close is the previous trading day's close, used for day-change calculations.
    """
    if not tickers:
        return {}

    with yf_warnings():
        data = yf.download(tickers, period='5d', progress=False, auto_adjust=True)

    if data.empty:
        return {}

    close = data['Close'] if isinstance(data.columns, pd.MultiIndex) else data[['Close']]
    if isinstance(close, pd.Series):
        close = close.to_frame(name=tickers[0])

    close = close.ffill()
    result: Dict[str, Dict[str, float]] = {}
    for t in tickers:
        if t not in close.columns:
            continue
        s = close[t].dropna()
        if s.empty:
            continue
        result[t] = {
            'price':      round(float(s.iloc[-1]), 4),
            'prev_close': round(float(s.iloc[-2]) if len(s) >= 2 else float(s.iloc[-1]), 4),
        }
    return result


def fetch_watchlist_history(tickers: List[str]) -> Dict[str, Dict[str, List[float]]]:
    """
    Returns {ticker: {"1W": [...], "1M": [...], "3M": [...], "6M": [...], "YTD": [...]}}
    Each list is daily closing prices, oldest to newest. Single network call.
    """
    if not tickers:
        return {}

    with yf_warnings():
        data = yf.download(tickers, period='1y', progress=False, auto_adjust=True)

    if data.empty:
        return {}

    close = data['Close'] if isinstance(data.columns, pd.MultiIndex) else data[['Close']]
    if isinstance(close, pd.Series):
        close = close.to_frame(name=tickers[0])

    today      = date.today()
    ytd_cutoff = pd.Timestamp(date(today.year, 1, 1))

    result: Dict[str, Dict[str, List[float]]] = {}
    for ticker in tickers:
        if ticker not in close.columns:
            continue
        series = close[ticker].dropna()
        if series.empty:
            continue
        n     = len(series)
        all_p = [round(float(v), 4) for v in series]
        ytd_p = [round(float(v), 4) for v in series[series.index >= ytd_cutoff]]
        result[ticker] = {
            '1W':  all_p[max(0, n - 5):],
            '1M':  all_p[max(0, n - 21):],
            '3M':  all_p[max(0, n - 63):],
            '6M':  all_p[max(0, n - 126):],
            'YTD': ytd_p if ytd_p else all_p[-1:],
        }

    return result

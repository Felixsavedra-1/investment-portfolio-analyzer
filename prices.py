"""prices.py — The only module that calls yfinance; all others receive plain dicts."""

from __future__ import annotations

import json
import os
import re
import sys
import warnings
from collections.abc import Iterator
from typing import Any
from contextlib import contextmanager
from datetime import date, timedelta, datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

from config import DATA_DIR

LOOKBACK_DAYS = 7  # look back up to 7 calendar days to find the prior trading close
_DESC_CACHE_FILE     = DATA_DIR / 'watchlist_descriptions_cache.json'
_ANALYSIS_CACHE_FILE = DATA_DIR / 'watchlist_analysis_cache.json'
_CACHE_TTL_DAYS = 30


def _load_cache(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            pass
    return {}


def _save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2))


def _is_cache_fresh(entry: dict) -> bool:
    ts = entry.get('cached_at')
    if not ts:
        return False
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - dt < timedelta(days=_CACHE_TTL_DAYS)


def _first_sentences(text: str, n: int = 3) -> str:
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    return ' '.join(sentences[:n])


def _rewrite_description(raw: str, ticker: str) -> str:
    """Rewrite via Claude if ANTHROPIC_API_KEY is set; else fall back to first sentences."""
    if not os.environ.get('ANTHROPIC_API_KEY'):
        return _first_sentences(raw)
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model='claude-haiku-4-5',
            max_tokens=150,
            messages=[{
                'role': 'user',
                'content': (
                    f"Describe {ticker} in 2-3 sentences. "
                    "Be blunt and factual. State what they make or sell, who buys it, "
                    "and one thing that sets them apart. "
                    "No adjectives like 'leading' or 'innovative'. No sentences starting "
                    "with 'The company'. No filler. Raw facts only.\n\n"
                    f"{raw}"
                ),
            }],
        )
        return msg.content[0].text.strip()
    except Exception as exc:
        print(
            f"  Warning: could not rewrite description for {ticker} "
            f"({type(exc).__name__}): {exc}",
            file=sys.stderr,
        )
        return _first_sentences(raw)


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


def _close_series(data: pd.DataFrame) -> pd.Series:
    close = data['Close']
    return close.iloc[:, 0] if isinstance(close, pd.DataFrame) else close


def _close_frame(data: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    if isinstance(data.columns, pd.MultiIndex):
        close = data['Close']
    else:
        close = data[['Close']].rename(columns={'Close': tickers[0]})
    return close  # type: ignore[return-value]


def fetch_price(ticker: str) -> float:
    with yf_warnings():
        data = yf.download(ticker, period='5d', progress=False, auto_adjust=True)

    if data.empty:
        raise PriceFetchError(
            f"No price data for '{ticker}'. Check the symbol and your connection."
        )

    return _last_close(_close_series(data), f"'{ticker}'")


def fetch_prices_batch(tickers: list[str]) -> dict[str, float]:
    """Batch close prices. Tickers that fail fetch are silently omitted."""
    if not tickers:
        return {}

    with yf_warnings():
        data = yf.download(tickers, period='5d', progress=False, auto_adjust=True)

    if data.empty:
        return {}

    close = _close_frame(data, tickers)
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
    try:
        target = date.fromisoformat(date_str)
    except ValueError as exc:
        raise PriceFetchError(f"Invalid date '{date_str}': {exc}") from exc
    start  = (target - timedelta(days=LOOKBACK_DAYS)).isoformat()
    end    = (target + timedelta(days=1)).isoformat()

    with yf_warnings():
        data = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)

    if data.empty:
        raise PriceFetchError(
            f"No price data for '{ticker}' around {date_str}. "
            "Check the symbol and date, or pass --price manually."
        )

    return _last_close(_close_series(data), f"'{ticker}' around {date_str}")


def fetch_label(ticker: str) -> str:
    """Human-readable name, falls back to ticker symbol on any failure (incl. network)."""
    try:
        info = yf.Ticker(ticker).info
        return info.get('shortName') or info.get('longName') or ticker
    except Exception:
        return ticker


def fetch_prices_with_change(tickers: list[str]) -> dict[str, dict[str, float | None]]:
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

    close = _close_frame(data, tickers).ffill()
    result: dict[str, dict[str, float | None]] = {}
    for t in tickers:
        if t not in close.columns:
            continue
        s = close[t].dropna()
        if s.empty:
            continue
        result[t] = {
            'price':      round(float(s.iloc[-1]), 4),
            'prev_close': round(float(s.iloc[-2]), 4) if len(s) >= 2 else None,
        }
    return result


def fetch_watchlist_history(tickers: list[str]) -> dict[str, dict[str, list[float]]]:
    """
    Returns {ticker: {"1W": [...], "1M": [...], "3M": [...], "6M": [...],
                      "YTD": [...], "2Y": [...], "5Y": [...]}}
    Each list is daily closing prices, oldest to newest. Single network call.
    """
    if not tickers:
        return {}

    with yf_warnings():
        data = yf.download(tickers, period='5y', progress=False, auto_adjust=True)

    if data.empty:
        return {}

    close = _close_frame(data, tickers)
    today      = date.today()
    ytd_cutoff = pd.Timestamp(date(today.year, 1, 1))

    result: dict[str, dict[str, list[float]]] = {}
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
            '2Y':  all_p[max(0, n - 504):],
            '5Y':  all_p[:],
        }

    return result


def fetch_watchlist_info(tickers: list[str]) -> dict[str, dict[str, str]]:
    """
    Returns {ticker: {"description": str, "sector": str}} for each ticker.
    Descriptions are rewritten by Claude for concision and cached for 30 days.
    """
    cache  = _load_cache(_DESC_CACHE_FILE)
    result: dict[str, dict[str, str]] = {}
    dirty  = False

    with yf_warnings():
        for ticker in tickers:
            cached = cache.get(ticker, {})
            if _is_cache_fresh(cached):
                result[ticker] = {'description': cached['description'], 'sector': cached['sector']}
                continue
            try:
                info   = yf.Ticker(ticker).info
                raw    = info.get('longBusinessSummary') or ''
                sector = info.get('sector') or ''
                desc   = _rewrite_description(raw, ticker) if raw else ''
                result[ticker] = {'description': desc, 'sector': sector}
                cache[ticker]  = {
                    'description': desc,
                    'sector':      sector,
                    'cached_at':   datetime.now(timezone.utc).isoformat(),
                }
                dirty = True
            except Exception as exc:
                print(
                    f"  Warning: could not fetch info for {ticker} "
                    f"({type(exc).__name__}): {exc}",
                    file=sys.stderr,
                )
                result[ticker] = {'description': '', 'sector': ''}

    if dirty:
        _save_cache(_DESC_CACHE_FILE, cache)

    return result


_ANALYSIS_KEYS = ('thesis', 'bull', 'bear', 'watch')


def _series_return(prices: list[float]) -> float | None:
    """Total return (%) across a price series, or None if it can't be computed."""
    if not prices or len(prices) < 2 or prices[0] == 0:
        return None
    return (prices[-1] / prices[0] - 1) * 100


def _generate_analysis(ticker: str, sector: str, returns: dict[str, float | None]) -> dict[str, str]:
    """Claude (Sonnet) structured equity brief. Raises on API/parse failure."""
    import anthropic
    client = anthropic.Anthropic()
    ret_line = ', '.join(f"{k} {v:+.1f}%" for k, v in returns.items() if v is not None) \
        or 'no usable return history'
    client_msg = (
        f"You are an equity analyst. Write a tight investment brief for {ticker}"
        f"{f' ({sector})' if sector else ''}.\n"
        f"Recent total returns: {ret_line}.\n\n"
        "Return ONLY a JSON object with exactly these keys, each 1-2 blunt, factual "
        "sentences. No filler, no adjectives like 'leading', 'strong', or 'innovative'.\n"
        '{"thesis": "core investment case", "bull": "upside scenario", '
        '"bear": "main risk", "watch": "the one metric or event to monitor next"}'
    )
    msg = client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=400,
        messages=[{'role': 'user', 'content': client_msg}],
    )
    text = msg.content[0].text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```[a-z]*\n?|\n?```$', '', text).strip()
    data = json.loads(text)
    return {k: str(data.get(k, '')).strip() for k in _ANALYSIS_KEYS}


def fetch_watchlist_analysis(
    tickers: list[str],
    history: dict[str, dict[str, list[float]]],
    info: dict[str, dict[str, str]] | None = None,
) -> dict[str, dict[str, str]]:
    """
    Claude-generated structured analysis {ticker: {thesis, bull, bear, watch}}.
    Runs at build time only when ANTHROPIC_API_KEY is set; cached 30 days. A ticker
    is omitted on missing key or failure, so the dashboard falls back to its
    quantitative-only panel.
    """
    if not tickers or not os.environ.get('ANTHROPIC_API_KEY'):
        return {}

    info   = info or {}
    cache  = _load_cache(_ANALYSIS_CACHE_FILE)
    result: dict[str, dict[str, str]] = {}
    dirty  = False

    for ticker in tickers:
        cached = cache.get(ticker, {})
        if _is_cache_fresh(cached):
            result[ticker] = {k: cached.get(k, '') for k in _ANALYSIS_KEYS}
            continue
        h       = history.get(ticker, {})
        sector  = info.get(ticker, {}).get('sector', '')
        returns = {w: _series_return(h.get(w, [])) for w in ('1M', '6M', 'YTD', '2Y', '5Y')}
        try:
            analysis = _generate_analysis(ticker, sector, returns)
        except Exception as exc:
            print(
                f"  Warning: could not generate analysis for {ticker} "
                f"({type(exc).__name__}): {exc}",
                file=sys.stderr,
            )
            continue
        result[ticker] = analysis
        cache[ticker]  = {**analysis, 'cached_at': datetime.now(timezone.utc).isoformat()}
        dirty = True

    if dirty:
        _save_cache(_ANALYSIS_CACHE_FILE, cache)

    return result

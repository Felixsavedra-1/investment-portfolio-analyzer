"""
metrics.py — Shared financial metric calculations.
"""

import collections
import numpy as np
import pandas as pd
from scipy import stats

from config import TRADING_DAYS_PER_YEAR


def annualized_sharpe(returns: pd.Series, risk_free_rate: float) -> float:
    if returns.empty:
        return float('nan')
    annual_ret = returns.mean() * TRADING_DAYS_PER_YEAR
    annual_vol = returns.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)
    if annual_vol == 0:
        return 0.0
    return (annual_ret - risk_free_rate) / annual_vol


def sharpe_ci(returns: pd.Series, sharpe: float, alpha: float = 0.05) -> tuple:
    """Lo (2002) asymptotic CI. SE = sqrt((1 + SR²/2) / T) where T = days/252."""
    n = len(returns)
    if n < 2 or not np.isfinite(sharpe):
        return float('nan'), float('nan')
    T  = n / TRADING_DAYS_PER_YEAR
    se = np.sqrt((1 + 0.5 * sharpe ** 2) / T)
    z  = stats.norm.ppf(1 - alpha / 2)
    return sharpe - z * se, sharpe + z * se


def max_drawdown(cumulative_returns: pd.Series) -> float:
    if cumulative_returns.empty:
        return float('nan')
    running_max = cumulative_returns.cummax()
    return float(((cumulative_returns - running_max) / running_max).min())


def time_weighted_return(transactions: list, prices_df: pd.DataFrame) -> float:
    """
    TWRR: geometrically links sub-period returns at each cash-flow event,
    eliminating timing effects to isolate selection skill. Returns NaN if
    data is insufficient.
    """
    known = set(prices_df.columns)
    relevant = [t for t in transactions if t.ticker in known]
    if not relevant:
        return float('nan')

    def price_at(ticker: str, date_str: str) -> float:
        col = prices_df.get(ticker)
        if col is None:
            return float('nan')
        s = col.dropna()
        mask = s.index.strftime('%Y-%m-%d') <= date_str
        if not mask.any():
            return float('nan')
        return float(s[mask].iloc[-1])

    def portfolio_value(holdings: dict, date_str: str) -> float:
        if not holdings:
            return 0.0
        total = 0.0
        for ticker, shares in holdings.items():
            p = price_at(ticker, date_str)
            if not np.isfinite(p):
                return float('nan')
            total += shares * p
        return total

    sorted_txns = sorted(relevant, key=lambda t: t.timestamp[:10])
    txns_by_date: dict = collections.defaultdict(list)
    for t in sorted_txns:
        txns_by_date[t.timestamp[:10]].append(t)

    holdings: dict = {}
    factors: list  = []
    prev_date: str | None = None

    for date_str in sorted(txns_by_date):
        if prev_date is not None and holdings:
            v_start = portfolio_value(holdings, prev_date)
            v_end   = portfolio_value(holdings, date_str)
            if v_start > 0 and np.isfinite(v_start) and np.isfinite(v_end):
                factors.append(v_end / v_start)

        for txn in txns_by_date[date_str]:
            if txn.action == 'buy':
                holdings[txn.ticker] = holdings.get(txn.ticker, 0.0) + txn.shares
            elif txn.action == 'sell':
                remaining = holdings.get(txn.ticker, 0.0) - txn.shares
                if remaining <= 1e-9:
                    holdings.pop(txn.ticker, None)
                else:
                    holdings[txn.ticker] = remaining
        prev_date = date_str

    # Final sub-period: last cash-flow date → most recent price date
    if holdings and prev_date is not None:
        latest = prices_df.index[-1].strftime('%Y-%m-%d')
        if latest > prev_date:
            v_start = portfolio_value(holdings, prev_date)
            v_end   = portfolio_value(holdings, latest)
            if v_start > 0 and np.isfinite(v_start) and np.isfinite(v_end):
                factors.append(v_end / v_start)

    if not factors:
        return float('nan')

    twrr = 1.0
    for f in factors:
        twrr *= f
    return twrr - 1.0


def risk_snapshot(
    returns: pd.Series,
    risk_free_rate: float,
    min_observations: int,
) -> dict:
    """Returns {} if history is insufficient."""
    trailing = returns.iloc[-TRADING_DAYS_PER_YEAR:].dropna()
    if len(trailing) < min_observations:
        return {}

    annual_vol = trailing.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)
    if annual_vol == 0:
        return {}

    sharpe     = annualized_sharpe(trailing, risk_free_rate)
    ci         = sharpe_ci(trailing, sharpe)
    cumulative = (1 + trailing).cumprod()

    return {
        'sharpe':       sharpe,
        'sharpe_ci':    ci,
        'volatility':   annual_vol,
        'max_drawdown': max_drawdown(cumulative),
    }

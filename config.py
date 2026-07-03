"""config.py — Watchlist, benchmark, and risk parameters."""

from __future__ import annotations

from pathlib import Path

DATA_DIR:          Path = Path.home() / '.portfolio'
HOLDINGS_FILE:     Path = DATA_DIR / 'holdings.json'
TRANSACTIONS_FILE: Path = DATA_DIR / 'transactions.json'
SAVINGS_FILE:      Path = DATA_DIR / 'savings.json'
GOALS_FILE:        Path = DATA_DIR / 'goals.json'

WATCHLIST: dict[str, str] = {}

# NAV is struck once daily after 4 PM ET — flagged with (*) in the brief.
MUTUAL_FUNDS: frozenset[str] = frozenset()

# The benchmark ticker must not appear in your holdings.
BENCHMARK: str = 'SPY'

RISK_FREE_RATE:        float = 0.045   # annual, ≈ current T-bill yield
TRANSACTION_COST:      float = 0.0     # one-way entry cost fraction (e.g. 0.001 = 10 bps)
TRADING_DAYS_PER_YEAR: int   = 252     # US equity convention

# Day of month savings accounts credit interest (1–28). Set to None to disable.
INTEREST_PAYMENT_DAY: int | None = None

# Lookback windows in trading days (1 week ≈ 5, 1 month ≈ 21, 1 year ≈ 252).
BRIEF_WINDOW_1D: int = 1
BRIEF_WINDOW_1W: int = 5
BRIEF_WINDOW_1M: int = 21
BRIEF_WINDOW_2Y: int = 504
BRIEF_WINDOW_5Y: int = 1260

# Watchlist momentum signal: 1M returns within ±MOMENTUM_FLAT_BAND are NEUTRAL.
MOMENTUM_FLAT_BAND: float = 0.01

# Minimum trading-day history required to show the Risk Snapshot (~3 months).
RISK_MIN_OBSERVATIONS: int = 60

BRIEF_TIMEZONE: str = 'America/New_York'

# Prices are in each index's local currency.
GLOBAL_INDICES: dict[str, str] = {
    'S&P 500    (US)':        '^GSPC',
    'TSX        (Canada)':    '^GSPTSE',
    'Bolsa IPC  (Mexico)':    '^MXX',
    'Bovespa    (Brazil)':    '^BVSP',
    'FTSE 100   (UK)':        '^FTSE',
    'CAC 40     (France)':    '^FCHI',
    'DAX        (Germany)':   '^GDAXI',
    'Nikkei 225 (Japan)':     '^N225',
    'KOSPI      (Korea)':     '^KS11',
    'Shanghai   (China)':     '000001.SS',
    'Hang Seng  (Hong Kong)': '^HSI',
    'ASX 200    (Australia)': '^AXJO',
}

try:
    from config_local import *  # noqa: F401, F403
except ModuleNotFoundError:
    pass

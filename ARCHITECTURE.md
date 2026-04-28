# Architecture

> Reference for contributors and curious readers. The user-facing intro is in [README.md](README.md); this document covers the module layout, data model, and design rationale.

## Commands

```bash
# Set up environment
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Portfolio management
python portfolio.py buy    TICKER DOLLARS [--date YYYY-MM-DD] [--price P] [--notes "..."]
python portfolio.py sell   TICKER DOLLARS [--date YYYY-MM-DD] [--price P] [--notes "..."]
python portfolio.py show
python portfolio.py gains  [--ticker TICKER]
python portfolio.py history [--ticker TICKER] [--limit N]
python portfolio.py remove TICKER

# --date fetches the closing price on that day (weekends/holidays resolve to prior trading day).

# Savings accounts (shown at the top of morning_brief.py)
python portfolio.py savings set   NAME BALANCE [--apy RATE] [--bank NAME]  # --apy required on first create
python portfolio.py savings remove NAME
python portfolio.py savings interest                                       # accrued + projected per cycle

# Goals (shown as progress bars in the dashboard)
python portfolio.py goal set portfolio AMOUNT   # set total portfolio value target
python portfolio.py goal set savings AMOUNT     # set total savings target
python portfolio.py goal remove portfolio|savings
python portfolio.py goal show

# Daily snapshot (also builds and opens the dashboard)
python morning_brief.py

# Dashboard only
python dashboard.py

# Deep analysis (generates ~/.portfolio/portfolio_analysis.png)
python portfolio_analyzer.py

# Demo dashboard with synthetic data (for screenshots)
python generate_preview.py

# Run all tests
pytest tests/

# Run a single test file
pytest tests/test_ledger.py -v
```

## Architecture

Four entry points (`portfolio.py`, `morning_brief.py`, `portfolio_analyzer.py`, `dashboard.py`) share four support modules (`ledger.py`, `prices.py`, `display.py`, `metrics.py`) and one config (`config.py`). Local overrides live in `config_local.py` (gitignored), wildcard-imported at the bottom of `config.py`.

```
portfolio-cli/
├── portfolio.py          # CLI router — buy, sell, show, gains, history, remove, savings, goal
├── dashboard.py          # Build and open the animated web dashboard
├── dashboard.html        # Dashboard template (injected with window.__DASH__ at runtime)
├── generate_preview.py   # Static demo payload → dashboard.html (for README screenshots)
├── ledger.py             # Data model + JSON I/O (Holding, Transaction, SavingsAccount, goals)
├── prices.py             # yfinance wrapper — only file that touches the network
├── display.py            # Terminal formatting — pure functions, no side effects
├── metrics.py            # Shared finance math (annualized_sharpe, sharpe_ci, max_drawdown, risk_snapshot)
├── morning_brief.py      # Daily snapshot entry point — also rebuilds and opens the dashboard
├── portfolio_analyzer.py # Deep analysis + 6-panel chart
├── config.py             # Paths, risk params, watchlist, benchmark, brief windows
├── config_local.py       # Personal overrides (gitignored) — WATCHLIST, MUTUAL_FUNDS, etc.
└── tests/
    ├── test_ledger.py
    ├── test_display.py
    ├── test_portfolio.py
    ├── test_morning_brief.py
    └── test_portfolio_analyzer.py
```

## Data storage

All portfolio data lives in `~/.portfolio/`, not in the project directory.

```
~/.portfolio/
├── holdings.json                      # current positions (written by portfolio.py)
├── transactions.json                  # append-only trade log (written by portfolio.py)
├── savings.json                       # savings accounts with balance + APY (written by portfolio.py)
├── goals.json                         # portfolio and savings targets (written by portfolio.py)
├── dashboard.html                     # generated dashboard (written by dashboard.py / morning_brief.py)
├── watchlist_descriptions_cache.json  # Claude-rewritten company descriptions, 30-day TTL
└── portfolio_analysis.png             # chart output (written by portfolio_analyzer.py)
```

Data files are created automatically on first use. The repo contains no personal data.

### config.py

Single source of truth for paths, risk parameters, watchlist, and benchmark.

Exports: `DATA_DIR`, `HOLDINGS_FILE`, `TRANSACTIONS_FILE`, `SAVINGS_FILE`, `GOALS_FILE`, `TRADING_DAYS_PER_YEAR`, `RISK_FREE_RATE`, `TRANSACTION_COST`, `BENCHMARK`, `WATCHLIST`, `MUTUAL_FUNDS`, `GLOBAL_INDICES`, `BRIEF_TIMEZONE`, `BRIEF_WINDOW_{1D,1W,1M}`, `MOMENTUM_FLAT_BAND`, `RISK_MIN_OBSERVATIONS`, `INTEREST_PAYMENT_DAY`.

### metrics.py

Pure financial-math functions, no I/O. Imported by `morning_brief.py` and `portfolio_analyzer.py`.

- `annualized_sharpe(returns, rf)` — daily returns × 252 conventions
- `sharpe_ci(returns, sharpe, alpha=0.05)` — Lo (2002) asymptotic CI
- `max_drawdown(cumulative)` — peak-to-trough on a cumulative-return series
- `risk_snapshot(returns, rf, min_obs)` — trailing-1Y `{sharpe, sharpe_ci, volatility, max_drawdown}`, returns `{}` if history insufficient

### dashboard.py

Builds and opens the portfolio dashboard. `build_payload()` assembles holdings, savings, watchlist, goals, and an embedded base64 PNG of the latest analysis chart into a single dict. `build_html()` injects it into `dashboard.html` as `window.__DASH__` and writes to `~/.portfolio/dashboard.html`.

`morning_brief.main()` reuses the prices already fetched for the brief and passes them into `build_payload(prices=..., prev_prices=...)`, avoiding a second network call.

**`dashboard.html`** — Three.js 3D ring charts, animated sparklines, watchlist with hover tooltips (description + sector) and click-to-analyze (returns, annualized vol, max drawdown, switchable price chart computed in-browser), portfolio + savings goal progress bars. Data is fully injected at build time; no server required.

### ledger.py

Data model and all JSON I/O. Nothing else reads or writes the JSON files.

**`Holding` dataclass:** `ticker`, `shares` (float — fractional), `cost` (cumulative cost basis), `first_purchase` (ISO 8601 datetime), `label`. Exposes `avg_cost_per_share` and `start_date` (backward-compat property, returns `first_purchase[:10]`).

**`Transaction` dataclass:** `id`, `timestamp` (ISO 8601 full datetime), `action` (`"buy"` | `"sell"`), `ticker`, `shares`, `dollars`, `price`, `realized_pnl` (None on buys, float on sells), `notes`.

**`SavingsAccount` dataclass:** `name`, `balance` (float), `apy` (decimal, e.g. `0.04`), `bank` (str). Exposes `monthly_interest` property (`balance * apy / 12`).

**I/O functions:** `load_holdings`, `save_holdings`, `load_transactions`, `append_transaction`, `load_savings`, `save_savings` — all use atomic `.tmp` → rename writes. `load_holdings` and `load_transactions` normalize legacy field names (`start_date` → `first_purchase`, `date` → `timestamp`, `type` → `action`) and coerce NaN shares/price to 0.0.

**Weight helpers:** `cost_basis_weights(holdings)` — cost-basis weights, no price data needed. `market_value_weights(holdings, prices)` — market-value weights.

### prices.py

Single yfinance wrapper. All other modules receive prices as plain `dict[str, float]` — none of them import yfinance directly.

- `fetch_price(ticker)` — single ticker, raises `PriceFetchError` on failure
- `fetch_prices_batch(tickers)` — batch close prices in one network call
- `fetch_prices_with_change(tickers)` — `{ticker: {price, prev_close}}` for day-change display
- `fetch_historical_price(ticker, date_str)` — closing price on/near a date (weekends fall back to prior trading day)
- `fetch_watchlist_history(tickers)` — daily closes per `1W/1M/3M/6M/YTD` window for sparklines
- `fetch_watchlist_info(tickers)` — `{description, sector}`; descriptions optionally rewritten by Claude (haiku-4-5) when `ANTHROPIC_API_KEY` is set, cached 30 days in `~/.portfolio/watchlist_descriptions_cache.json`
- `fetch_label(ticker)` — human-readable name, falls back to ticker symbol on any failure

### display.py

Pure formatting functions — no network calls, no file I/O. Each function takes data structures and returns a formatted string.

- `render_holdings(holdings, prices)` — current portfolio table with live P&L and weights. Weights are computed against the pre-calculated total portfolio value, not a running partial sum.
- `render_gains(transactions, holdings, prices, ticker=None)` — realized gains (from stored `realized_pnl` on SELL transactions) + unrealized gains (live prices). Optional `ticker` filter.
- `render_history(transactions, ticker=None, limit=None)` — transaction log, newest first.

### portfolio.py

Thin CLI router (~130 lines). All business logic lives in `ledger`, `prices`, and `display`. The router's only jobs are: parse args, call `_resolve_price`, update holdings, append transaction, print result.

**`buy`** — opens a new position or adds to an existing one (no separate `add` command). Fetches live price unless `--price` is given. Computes `shares = dollars / price`.

**`sell`** — reduces a position using the average cost method: `cost_sold = (shares_sold / total_shares) * total_cost`, `realized_pnl = proceeds - cost_sold`. Fully closes the position if shares drop below 1e-9.

**`_resolve_price(ticker, explicit)`** — returns `explicit` if given, otherwise calls `fetch_price` and prints the fetched price inline.

### morning_brief.py

`MorningBrief` class with a `main()` entry point. Prints a terminal-formatted daily snapshot: current portfolio value (shares × price), per-holding 1D/1W/1M/YTD returns with dollar P&L, alpha vs. SPY, global index moves, watchlist momentum signals, and a trailing 1-year risk snapshot.

**Pipeline:** `fetch()` → single yfinance call for all tickers; `render()` → formats and prints to stdout.

`main()` also builds the dashboard, passing the prices already fetched for the brief so no second network call is made.

Mutual fund tickers (set via `MUTUAL_FUNDS` in config) are flagged `*` — NAV lags by one business day.

### portfolio_analyzer.py

Four layers, top to bottom:

1. **Data model** — frozen dataclasses: `AssetMetrics`, `RollingMetrics`, `AnalysisResult`.
2. **Pure compute** — `compute_asset_metrics(returns, rf)`, `compute_rolling_metrics(...)`, and `compute_analysis(returns, weights, benchmark, rf, transaction_cost, rolling_window) -> AnalysisResult`. No I/O — take a returns DataFrame, return a frozen dataclass. Directly unit-testable with synthetic data.
3. **Pure render** — `print_results(result)` and `plot_dashboard(result, output_path)` depend only on the dataclass.
4. **Coordinator** — `PortfolioAnalyzer` class validates inputs (`_normalize_portfolio`, `_normalize_benchmark`, `_resolve_date_range`), fetches & aligns prices via `fetch_returns()`, then delegates to the pure layer in `run_analysis()`.

Metrics: CAGR, arithmetic annual return, volatility, Sharpe ratio with Lo (2002) 95% CI, total return, max drawdown — for the portfolio, benchmark, and each holding individually.

## Design notes

- `TRADING_DAYS_PER_YEAR = 252` is defined in `config.py` and re-declared locally in `portfolio_analyzer.py` for standalone import compatibility.
- Risk-free rate defaults to 4.5%; override via `RISK_FREE_RATE` in `config.py`.
- Fractional shares are stored as native Python floats — no special encoding.
- `realized_pnl` is written to the transaction log at sell time (average cost method). `gains` reads it directly — no log replay needed.
- All test suites are network-free: data is injected directly into instance attributes or passed as plain dicts, bypassing all fetch calls.

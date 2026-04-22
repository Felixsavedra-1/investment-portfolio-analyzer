"""
portfolio_analyzer.py — Deep portfolio analysis and 6-panel chart.

Produces a console tearsheet (CAGR, Sharpe with Lo 2002 CI, volatility,
max drawdown) and saves a chart to ~/.portfolio/portfolio_analysis.png.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Union

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import yfinance as yf

from config import BENCHMARK, DATA_DIR, HOLDINGS_FILE, RISK_FREE_RATE, TRADING_DAYS_PER_YEAR, TRANSACTION_COST
from ledger import cost_basis_weights, load_holdings
from metrics import annualized_sharpe, max_drawdown, sharpe_ci
from prices import yf_warnings

logger = logging.getLogger(__name__)

WEIGHT_TOLERANCE = 1e-6


class PortfolioAnalyzer:
    """
    Risk-adjusted portfolio analysis against a configurable benchmark.

    Metrics: Sharpe ratio with Lo (2002) 95% CI, CAGR, volatility, max drawdown.
    Supports rolling-window analysis and optional transaction cost modeling.
    """

    def __init__(self,
                 portfolio_dict: Dict[str, float],
                 start_date: Optional[Union[str, datetime]] = None,
                 end_date: Optional[Union[str, datetime]] = None,
                 benchmark: str = BENCHMARK,
                 risk_free_rate: float = RISK_FREE_RATE,
                 transaction_cost: float = TRANSACTION_COST):
        self.portfolio = portfolio_dict
        self._validate_weights()

        if not isinstance(benchmark, str):
            raise ValueError("Benchmark ticker must be a string.")
        self.benchmark = benchmark.strip().upper()
        if not self.benchmark:
            raise ValueError("Benchmark ticker cannot be empty.")
        if self.benchmark in self.portfolio:
            raise ValueError(
                f"Benchmark ticker '{self.benchmark}' cannot also be a portfolio holding."
            )

        self.risk_free_rate = float(risk_free_rate)
        self.transaction_cost = float(transaction_cost)

        self.end_date = self._coerce_date(end_date, default=datetime.now())
        self.start_date = self._coerce_date(start_date, default=(datetime.now() - timedelta(days=365*3)))
        if self.start_date >= self.end_date:
            raise ValueError(f"Start date {self.start_date.date()} must be before end date {self.end_date.date()}")
        self.start_date = self.start_date.strftime('%Y-%m-%d')
        self.end_date = self.end_date.strftime('%Y-%m-%d')
        
        self.price_data: Optional[pd.DataFrame] = None
        self.returns_data: Optional[pd.DataFrame] = None
        self.portfolio_returns: Optional[pd.Series] = None
        self.benchmark_returns: Optional[pd.Series] = None
        self.results = {}

    def _validate_weights(self) -> None:
        if not self.portfolio:
            raise ValueError("Portfolio is empty. Provide at least one ticker with a weight.")

        cleaned_portfolio: Dict[str, float] = {}
        for ticker, raw_weight in self.portfolio.items():
            cleaned_ticker = str(ticker).strip().upper()
            if not cleaned_ticker:
                raise ValueError("Ticker symbols must be non-empty strings.")
            if cleaned_ticker in cleaned_portfolio:
                raise ValueError(f"Duplicate ticker after normalization: '{cleaned_ticker}'")
            try:
                weight = float(raw_weight)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid weight for ticker '{cleaned_ticker}': {raw_weight}") from exc

            if not np.isfinite(weight):
                raise ValueError(f"Weight for ticker '{cleaned_ticker}' must be a finite number.")
            if weight < 0:
                raise ValueError("Portfolio weights must be non-negative.")
            cleaned_portfolio[cleaned_ticker] = weight

        self.portfolio = cleaned_portfolio
        total_weight = sum(self.portfolio.values())
        if not np.isclose(total_weight, 1.0, atol=WEIGHT_TOLERANCE):
            raise ValueError(f"Portfolio weights sum to {total_weight:.2%}, must equal 100%")

    @staticmethod
    def _coerce_date(value: Optional[Union[str, datetime]], default: datetime) -> datetime:
        if value is None:
            return default
        if isinstance(value, datetime):
            return value
        try:
            return datetime.strptime(value, '%Y-%m-%d')
        except ValueError as exc:
            raise ValueError(f"Invalid date '{value}'. Use YYYY-MM-DD format.") from exc

    def _fetch_data(self) -> None:
        """
        Download adjusted-close prices from Yahoo Finance.
        Forward-fills single-day gaps before aligning assets to their
        shortest common history via row-wise dropna.
        """
        logger.info("Fetching historical data...")

        tickers = list(dict.fromkeys(list(self.portfolio.keys()) + [self.benchmark]))

        with yf_warnings():
            data = yf.download(
                tickers,
                start=self.start_date,
                end=self.end_date,
                progress=False,
                auto_adjust=True,
            )

        self.price_data = data['Close'] if isinstance(data.columns, pd.MultiIndex) else data[['Close']]

        if isinstance(self.price_data, pd.Series):
            self.price_data = self.price_data.to_frame()

        missing = set(tickers) - set(self.price_data.columns)
        if missing:
            raise ValueError(f"Missing data for tickers: {', '.join(sorted(missing))}")

        self.price_data = self.price_data.ffill(limit=5)

        original_len = len(self.price_data)
        first_valid = {col: self.price_data[col].first_valid_index() for col in self.price_data.columns}
        limiting = {col: dt for col, dt in first_valid.items()
                    if dt is not None and dt > self.price_data.index[0]}
        self.price_data = self.price_data.dropna()

        if self.price_data.empty:
            raise ValueError("No overlapping data found for the selected tickers and date range.")

        dropped = original_len - len(self.price_data)
        if dropped > 0:
            detail = ', '.join(f"{t} (from {d.date()})" for t, d in limiting.items())
            logger.info("Aligned to shortest common history: %d rows dropped. Limiting: %s", dropped, detail)

        logger.info(
            "Data fetched: %d trading days (%s to %s)",
            len(self.price_data),
            self.price_data.index[0].date(),
            self.price_data.index[-1].date(),
        )

    def _calculate_returns(self) -> None:
        if self.price_data is None:
            raise ValueError("Price data not available. Call run_analysis().")
        self.returns_data = self.price_data.pct_change().dropna(how="any")
        if self.returns_data.empty:
            raise ValueError(
                "Insufficient overlapping data to compute returns. "
                "Increase date range or use assets with longer history."
            )

    def _calculate_portfolio_returns(self) -> None:
        if self.returns_data is None:
            raise ValueError("Returns data not available. Call run_analysis().")
            
        weights = pd.Series(self.portfolio)
        self.portfolio_returns = self.returns_data[list(self.portfolio)].mul(weights, axis=1).sum(axis=1)
        self.benchmark_returns = self.returns_data[self.benchmark]

    def _calculate_metrics(self) -> None:
        if self.portfolio_returns is None or self.benchmark_returns is None:
            raise ValueError("Portfolio returns not available. Call run_analysis().")

        def get_metrics(returns_series):
            returns_series = returns_series.dropna()
            if returns_series.empty:
                raise ValueError("Insufficient return observations to compute metrics.")
            cumulative   = (1 + returns_series).cumprod()
            total_return = cumulative.iloc[-1] - 1
            num_years    = len(returns_series) / TRADING_DAYS_PER_YEAR
            if num_years > 0 and total_return > -1:
                cagr = (1 + total_return) ** (1 / num_years) - 1
            else:
                cagr = float('nan')
            sharpe       = annualized_sharpe(returns_series, self.risk_free_rate)
            return {
                'annual_return':            cagr,
                'annual_return_arithmetic': returns_series.mean() * TRADING_DAYS_PER_YEAR,
                'annual_volatility':        returns_series.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR),
                'sharpe_ratio':             sharpe,
                'sharpe_ci':                sharpe_ci(returns_series, sharpe),
                'total_return':             total_return,
                'max_drawdown':             max_drawdown(cumulative),
            }, cumulative

        # Apply one-time entry transaction cost as a NAV haircut at inception.
        portfolio_input = self.portfolio_returns.copy()
        if self.transaction_cost > 0:
            portfolio_input.iloc[0] = (
                (1 + portfolio_input.iloc[0]) * (1 - self.transaction_cost) - 1
            )

        portfolio_metrics, portfolio_cum = get_metrics(portfolio_input)
        self.results['portfolio'] = {**portfolio_metrics, 'cumulative_returns': portfolio_cum}

        benchmark_metrics, benchmark_cum = get_metrics(self.benchmark_returns)
        self.results['benchmark'] = {**benchmark_metrics, 'cumulative_returns': benchmark_cum}

        self.results['individual_assets'] = {}
        for ticker in self.portfolio.keys():
            asset_metrics, _ = get_metrics(self.returns_data[ticker])
            asset_metrics['weight'] = self.portfolio[ticker]
            self.results['individual_assets'][ticker] = asset_metrics

    def _calculate_rolling_metrics(self, window: int = TRADING_DAYS_PER_YEAR) -> None:
        """Rolling Sharpe ratio and drawdown (default window: 252 trading days)."""
        if self.portfolio_returns is None or self.benchmark_returns is None:
            raise ValueError("Portfolio returns not available. Call run_analysis().")

        def rolling_sharpe(returns: pd.Series) -> pd.Series:
            roll_mean = returns.rolling(window).mean() * TRADING_DAYS_PER_YEAR
            roll_std = returns.rolling(window).std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)
            return (roll_mean - self.risk_free_rate) / roll_std

        def rolling_drawdown(returns: pd.Series) -> pd.Series:
            cumulative = (1 + returns).cumprod()
            running_max = cumulative.cummax()
            return (cumulative - running_max) / running_max

        self.results['rolling'] = {
            'window': window,
            'portfolio_sharpe': rolling_sharpe(self.portfolio_returns),
            'benchmark_sharpe': rolling_sharpe(self.benchmark_returns),
            'portfolio_drawdown': rolling_drawdown(self.portfolio_returns),
            'benchmark_drawdown': rolling_drawdown(self.benchmark_returns),
        }

    def print_results(self) -> None:
        def fmt(val, fmt_str):
            if pd.isna(val):
                return "n/a"
            return fmt_str.format(val)

        print("\nSummary")
        print('─' * 64)
        print(f"{'Metric':<26} {'Portfolio':>12} {self.benchmark:>12} {'Diff':>12}")
        print('─' * 64)
        
        metrics_map = [
            ('Annual Return (CAGR)', 'annual_return', '{:.2%}'),
            ('Annual Return (Arithmetic)', 'annual_return_arithmetic', '{:.2%}'),
            ('Annual Volatility', 'annual_volatility', '{:.2%}'),
            ('Sharpe Ratio', 'sharpe_ratio', '{:.3f}'),
            ('Total Return', 'total_return', '{:.2%}'),
            ('Max Drawdown', 'max_drawdown', '{:.2%}')
        ]
        
        for label, key, value_fmt in metrics_map:
            port_val = self.results['portfolio'][key]
            bench_val = self.results['benchmark'][key]
            diff = port_val - bench_val
            print(
                f"{label:<26} {fmt(port_val, value_fmt):>12} "
                f"{fmt(bench_val, value_fmt):>12} {fmt(diff, value_fmt):>12}"
            )
        
        sharpe_diff = self.results['portfolio']['sharpe_ratio'] - self.results['benchmark']['sharpe_ratio']
        print(f"\nSharpe vs {self.benchmark}: {sharpe_diff:+.3f}")

        port_ci = self.results['portfolio']['sharpe_ci']
        bench_ci = self.results['benchmark']['sharpe_ci']
        print("\nSharpe Ratio 95% CI (Lo 2002):")
        print(f"  {'Portfolio:':<16} [{port_ci[0]:.3f}, {port_ci[1]:.3f}]")
        print(f"  {self.benchmark + ':':<16} [{bench_ci[0]:.3f}, {bench_ci[1]:.3f}]")

        if self.transaction_cost > 0:
            print(f"\nTransaction cost: {self.transaction_cost:.2%} one-way entry — "
                  "portfolio metrics reflect net-of-cost returns.")

        print("\nAssets")
        print('─' * 64)
        print(f"{'Ticker':<10} {'Weight':>10} {'Return':>15} {'Volatility':>12} {'Sharpe':>10}")
        print('─' * 64)
        
        for ticker, metrics in self.results['individual_assets'].items():
            print(f"{ticker:<10} {metrics['weight']:>9.1%} {metrics['annual_return']:>14.2%} "
                  f"{metrics['annual_volatility']:>11.2%} {metrics['sharpe_ratio']:>10.3f}")

    def _plot_cumulative(self, ax) -> None:
        portfolio_cum = self.results['portfolio']['cumulative_returns']
        benchmark_cum = self.results['benchmark']['cumulative_returns']
        ax.plot(portfolio_cum.index, (portfolio_cum - 1) * 100,
                label='Portfolio', linewidth=2, color='#2E86AB')
        ax.plot(benchmark_cum.index, (benchmark_cum - 1) * 100,
                label=self.benchmark, linewidth=2, color='#A23B72', linestyle='--')
        ax.set_title('Cumulative Returns', fontweight='bold')
        ax.set_ylabel('Return (%)')
        ax.legend()

    def _plot_risk_return(self, ax) -> None:
        if not self.results['individual_assets']:
            return
        rf_pct = self.risk_free_rate * 100
        for ticker, metrics in self.results['individual_assets'].items():
            ax.scatter(metrics['annual_volatility'] * 100, metrics['annual_return'] * 100,
                       s=metrics['weight'] * 1000, alpha=0.6, label=ticker)
        ax.scatter(self.results['portfolio']['annual_volatility'] * 100,
                   self.results['portfolio']['annual_return'] * 100,
                   s=300, marker='*', color='gold', edgecolors='black',
                   linewidth=2, label='Portfolio', zorder=5)
        ax.scatter(self.results['benchmark']['annual_volatility'] * 100,
                   self.results['benchmark']['annual_return'] * 100,
                   s=300, marker='D', color='red', edgecolors='black',
                   linewidth=2, label=self.benchmark, zorder=5)
        ax.scatter(0, rf_pct, s=200, marker='^', color='#2CA02C', edgecolors='black',
                   linewidth=1.5, label=f'Risk-Free ({self.risk_free_rate:.1%})', zorder=5)
        bench_vol = self.results['benchmark']['annual_volatility'] * 100
        bench_ret = self.results['benchmark']['annual_return'] * 100
        if bench_vol > 0:
            max_vol = max(
                m['annual_volatility'] * 100 for m in self.results['individual_assets'].values()
            ) * 1.2
            slope = (bench_ret - rf_pct) / bench_vol
            cml_x = np.linspace(0, max(max_vol, bench_vol * 1.2), 100)
            ax.plot(cml_x, rf_pct + slope * cml_x, color='#2CA02C',
                    linewidth=1, linestyle=':', alpha=0.7, label='CML')
        ax.set_title('Risk-Return Profile', fontweight='bold')
        ax.set_xlabel('Volatility (Annual %)')
        ax.set_ylabel('CAGR (%)')
        ax.legend(loc='best')

    def _plot_sharpe(self, ax) -> None:
        sharpe_data = {
            'Portfolio':    self.results['portfolio']['sharpe_ratio'],
            self.benchmark: self.results['benchmark']['sharpe_ratio'],
        }
        colors = ['#2E86AB' if v == max(sharpe_data.values()) else '#A23B72'
                  for v in sharpe_data.values()]
        bars = ax.bar(sharpe_data.keys(), sharpe_data.values(), color=colors, alpha=0.7)
        for i, key in enumerate(sharpe_data):
            ci_key = 'portfolio' if key == 'Portfolio' else 'benchmark'
            ci = self.results[ci_key]['sharpe_ci']
            sr = sharpe_data[key]
            if np.isfinite(ci[0]) and np.isfinite(ci[1]):
                ax.errorbar(i, sr, yerr=[[sr - ci[0]], [ci[1] - sr]],
                            fmt='none', color='black', capsize=6, linewidth=1.5)
        ax.set_title('Sharpe Ratio with 95% CI (Lo 2002)', fontweight='bold')
        ax.set_ylabel('Sharpe Ratio')
        ax.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
        for bar in bars:
            height = bar.get_height()
            va, offset = ('bottom', 0.02) if height >= 0 else ('top', -0.02)
            ax.text(bar.get_x() + bar.get_width() / 2., height + offset,
                    f'{height:.3f}', ha='center', va=va, fontweight='bold')

    def _plot_allocation(self, ax) -> None:
        weights = [self.results['individual_assets'][t]['weight'] for t in self.portfolio]
        labels  = [f"{t}\n({w:.1%})" for t, w in zip(self.portfolio, weights)]
        ax.pie(weights, labels=labels, autopct='',
               colors=plt.cm.Set3(range(len(self.portfolio))), startangle=90)
        ax.set_title('Portfolio Allocation', fontweight='bold')

    def _plot_rolling_sharpe(self, ax, rolling: dict) -> None:
        window = rolling['window']
        ax.plot(rolling['portfolio_sharpe'].index, rolling['portfolio_sharpe'],
                label='Portfolio', linewidth=1.5, color='#2E86AB')
        ax.plot(rolling['benchmark_sharpe'].index, rolling['benchmark_sharpe'],
                label=self.benchmark, linewidth=1.5, color='#A23B72', linestyle='--')
        ax.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
        ax.axhline(y=1, color='gray', linestyle=':', linewidth=0.8, alpha=0.7)
        ax.set_title(f'Rolling Sharpe Ratio ({window // TRADING_DAYS_PER_YEAR}Y window)',
                     fontweight='bold')
        ax.set_ylabel('Sharpe Ratio')
        ax.legend()

    def _plot_drawdown(self, ax, rolling: dict) -> None:
        port_dd  = rolling['portfolio_drawdown'] * 100
        bench_dd = rolling['benchmark_drawdown'] * 100
        ax.fill_between(port_dd.index,  port_dd,  0, alpha=0.4, color='#2E86AB')
        ax.fill_between(bench_dd.index, bench_dd, 0, alpha=0.3, color='#A23B72')
        ax.plot(port_dd.index,  port_dd,  color='#2E86AB', linewidth=1, label='Portfolio')
        ax.plot(bench_dd.index, bench_dd, color='#A23B72', linewidth=1,
                linestyle='--', label=self.benchmark)
        ax.set_title('Underwater Chart (Drawdown from Peak)', fontweight='bold')
        ax.set_ylabel('Drawdown (%)')
        ax.legend()

    def plot_results(self) -> None:
        """Generate and save a 6-panel analysis dashboard."""
        sns.set_theme(style="whitegrid")
        fig, axes = plt.subplots(3, 2, figsize=(16, 18))
        fig.suptitle('Vero — Analysis Dashboard', fontsize=16, fontweight='bold')
        rolling = self.results.get('rolling', {})

        self._plot_cumulative(axes[0, 0])
        self._plot_risk_return(axes[0, 1])
        self._plot_sharpe(axes[1, 0])
        self._plot_allocation(axes[1, 1])
        if rolling:
            self._plot_rolling_sharpe(axes[2, 0], rolling)
            self._plot_drawdown(axes[2, 1], rolling)

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        output_path = DATA_DIR / 'portfolio_analysis.png'
        plt.tight_layout(rect=[0, 0, 1, 0.96])
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close(fig)
        logger.info("Chart saved: %s", output_path)

    def run_analysis(self) -> Dict:
        self._fetch_data()
        self._calculate_returns()
        self._calculate_portfolio_returns()
        self._calculate_metrics()
        self._calculate_rolling_metrics()
        self.print_results()
        self.plot_results()
        return self.results


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    holdings = load_holdings(HOLDINGS_FILE)
    if not holdings:
        print("No holdings found. Run: python portfolio.py buy TICKER DOLLARS")
        return

    start_date     = min(h.start_date for h in holdings.values())
    portfolio_dict = cost_basis_weights(holdings)

    analyzer = PortfolioAnalyzer(
        portfolio_dict=portfolio_dict,
        start_date=start_date,
        benchmark=BENCHMARK,
        risk_free_rate=RISK_FREE_RATE,
        transaction_cost=TRANSACTION_COST,
    )
    analyzer.run_analysis()

if __name__ == "__main__":
    main()

import warnings
from datetime import datetime, timedelta
from typing import Dict, Optional, Union

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import yfinance as yf

# Suppress noisy yfinance user warnings only.
warnings.filterwarnings('ignore', category=UserWarning, module='yfinance')

TRADING_DAYS_PER_YEAR = 252
WEIGHT_TOLERANCE = 1e-6

class PortfolioAnalyzer:
    """
    Analyzes investment portfolio performance and risk-adjusted returns.

    Benchmarks portfolio performance against the S&P 500 (SPY) using metrics
    such as Sharpe Ratio, Annual Return, and Maximum Drawdown.

    Attributes:
        portfolio (Dict[str, float]): Dictionary mapping ticker symbols to weights.
        benchmark (str): Ticker symbol for the benchmark (default: 'SPY').
        start_date (str): Analysis start date in 'YYYY-MM-DD' format.
        end_date (str): Analysis end date in 'YYYY-MM-DD' format.
        results (Dict): Dictionary storing analysis results.
    """

    def __init__(self, 
                 portfolio_dict: Dict[str, float], 
                 start_date: Optional[Union[str, datetime]] = None, 
                 end_date: Optional[Union[str, datetime]] = None,
                 benchmark: str = 'SPY'):
        """
        Initializes the PortfolioAnalyzer.

        Args:
            portfolio_dict: A dictionary where keys are ticker symbols (str)
                and values are portfolio weights (float). Weights must sum to 1.0.
            start_date: Start date for historical data (YYYY-MM-DD). 
                Defaults to 3 years prior to today.
            end_date: End date for historical data (YYYY-MM-DD). 
                Defaults to today.
        """
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
        """Validates that portfolio weights sum to 1.0."""
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

    def fetch_data(self) -> None:
        """
        Downloads historical price data from Yahoo Finance.
        
        Handles cases where assets have shorter history than the requested range
        by aligning data to the shortest common timeframe.
        """
        print("Fetching historical data...")
        
        tickers = list(dict.fromkeys(list(self.portfolio.keys()) + [self.benchmark]))
        
        # Download data
        data = yf.download(
            tickers,
            start=self.start_date,
            end=self.end_date,
            progress=False,
            auto_adjust=False,
        )
        
        # Handle multi-index columns if necessary
        if isinstance(data.columns, pd.MultiIndex):
            if 'Adj Close' in data.columns.get_level_values(0):
                self.price_data = data['Adj Close']
            elif 'Close' in data.columns.get_level_values(0):
                print("Warning: 'Adj Close' not found, using 'Close'")
                self.price_data = data['Close']
            else:
                raise ValueError(f"Neither 'Adj Close' nor 'Close' found in data columns: {data.columns}")
        else:
            # Single ticker case (unlikely given structure, but good for robustness)
            self.price_data = data['Adj Close'] if 'Adj Close' in data.columns else data['Close']

        # Ensure we have a DataFrame even if single ticker
        if isinstance(self.price_data, pd.Series):
            self.price_data = self.price_data.to_frame()

        # Verify all expected tickers are present
        expected = set(tickers)
        actual = set(self.price_data.columns)
        missing = expected - actual
        if missing:
            raise ValueError(f"Missing data for tickers: {', '.join(sorted(missing))}")

        # Drop missing data (aligns all assets to the shortest history)
        original_len = len(self.price_data)
        self.price_data = self.price_data.dropna()
        new_len = len(self.price_data)
        
        if new_len < original_len:
            print(f"Note: Data aligned to shortest history. Dropped {original_len - new_len} rows.")
            
        if self.price_data.empty:
            raise ValueError("No overlapping data found for the selected tickers and date range.")

        print(f"Data fetched: {len(self.price_data)} trading days")
        print(f"Date range: {self.price_data.index[0].date()} to {self.price_data.index[-1].date()}")

    def calculate_returns(self) -> None:
        """Calculates daily returns for all assets."""
        if self.price_data is None:
            raise ValueError("Price data not available. Run fetch_data() first.")
        self.returns_data = self.price_data.pct_change().dropna(how="any")
        if self.returns_data.empty:
            raise ValueError(
                "Insufficient overlapping data to compute returns. "
                "Increase date range or use assets with longer history."
            )

    def calculate_portfolio_returns(self) -> None:
        """Calculates weighted portfolio returns and extracts benchmark returns."""
        if self.returns_data is None:
            raise ValueError("Returns data not available. Run calculate_returns() first.")
            
        portfolio_tickers = list(self.portfolio.keys())
        weights = pd.Series(self.portfolio)
        
        # Calculate weighted returns
        self.portfolio_returns = self.returns_data[portfolio_tickers].mul(weights, axis=1).sum(axis=1)
        self.benchmark_returns = self.returns_data[self.benchmark]

    def calculate_sharpe_ratio(self, returns: pd.Series, risk_free_rate: float = 0.045) -> float:
        """
        Calculates the Sharpe Ratio for a given return series.

        Args:
            returns: Series of daily returns.
            risk_free_rate: Annual risk-free rate (default 4.5%).

        Returns:
            The annualized Sharpe Ratio.
        """
        if returns.empty:
            return float('nan')
        annual_return = returns.mean() * TRADING_DAYS_PER_YEAR
        annual_volatility = returns.std() * np.sqrt(TRADING_DAYS_PER_YEAR)
        
        if annual_volatility == 0:
            return 0.0
            
        return (annual_return - risk_free_rate) / annual_volatility

    def calculate_max_drawdown(self, cumulative_returns: pd.Series) -> float:
        """Calculates the maximum drawdown from peak."""
        if cumulative_returns.empty:
            return float('nan')
        running_max = cumulative_returns.cummax()
        drawdown = (cumulative_returns - running_max) / running_max
        return drawdown.min()

    def calculate_metrics(self) -> None:
        """Calculates comprehensive portfolio and benchmark metrics."""
        if self.portfolio_returns is None or self.benchmark_returns is None:
            raise ValueError("Portfolio returns not calculated.")

        # Helper to calculate metrics for a series
        def get_metrics(returns_series):
            returns_series = returns_series.dropna()
            if returns_series.empty:
                raise ValueError(
                    "Insufficient return observations to compute metrics."
                )
            cumulative = (1 + returns_series).cumprod()
            total_return = cumulative.iloc[-1] - 1
            num_periods = len(returns_series)
            num_years = num_periods / TRADING_DAYS_PER_YEAR
            cagr = float('nan')
            if num_years > 0 and (1 + total_return) > 0:
                cagr = (1 + total_return) ** (1 / num_years) - 1

            return {
                'annual_return': cagr,
                'annual_return_arithmetic': returns_series.mean() * TRADING_DAYS_PER_YEAR,
                'annual_volatility': returns_series.std() * np.sqrt(TRADING_DAYS_PER_YEAR),
                'sharpe_ratio': self.calculate_sharpe_ratio(returns_series),
                'total_return': total_return,
                'max_drawdown': self.calculate_max_drawdown(cumulative),
                'cumulative_returns': cumulative
            }

        self.results['portfolio'] = get_metrics(self.portfolio_returns)
        self.results['benchmark'] = get_metrics(self.benchmark_returns)
        
        # Individual asset metrics
        self.results['individual_assets'] = {}
        for ticker in self.portfolio.keys():
            asset_returns = self.returns_data[ticker]
            metrics = get_metrics(asset_returns)
            metrics['weight'] = self.portfolio[ticker]
            # Remove cumulative returns from individual assets to save space/complexity if not needed
            del metrics['cumulative_returns'] 
            self.results['individual_assets'][ticker] = metrics

    def print_results(self) -> None:
        """Prints formatted analysis results to the console."""
        def fmt(val, fmt_str):
            if pd.isna(val):
                return "n/a"
            return fmt_str.format(val)

        print("\nSummary")
        print("-" * 64)
        print(f"{'Metric':<26} {'Portfolio':>12} {'S&P 500':>12} {'Diff':>12}")
        print("-" * 64)
        
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
        print(f"\nSharpe vs S&P 500: {sharpe_diff:+.3f}")
        
        print("\nAssets")
        print("-" * 64)
        print(f"{'Ticker':<10} {'Weight':>10} {'Return':>15} {'Volatility':>12} {'Sharpe':>10}")
        print("-" * 64)
        
        for ticker, metrics in self.results['individual_assets'].items():
            print(f"{ticker:<10} {metrics['weight']:>9.1%} {metrics['annual_return']:>14.2%} "
                  f"{metrics['annual_volatility']:>11.2%} {metrics['sharpe_ratio']:>10.3f}")

    def plot_results(self) -> None:
        """Generates and saves a dashboard of visualization plots."""
        sns.set_theme(style="whitegrid")
        fig, axes = plt.subplots(2, 2, figsize=(16, 10))
        fig.suptitle('Portfolio Analysis Dashboard', fontsize=16, fontweight='bold')
        
        # 1. Cumulative Returns
        ax1 = axes[0, 0]
        portfolio_cum = self.results['portfolio']['cumulative_returns']
        benchmark_cum = self.results['benchmark']['cumulative_returns']
        
        ax1.plot(portfolio_cum.index, (portfolio_cum - 1) * 100, 
                label='Portfolio', linewidth=2, color='#2E86AB')
        ax1.plot(benchmark_cum.index, (benchmark_cum - 1) * 100, 
                label='S&P 500', linewidth=2, color='#A23B72', linestyle='--')
        ax1.set_title('Cumulative Returns', fontweight='bold')
        ax1.set_ylabel('Return (%)')
        ax1.legend()
        
        # 2. Risk-Return Scatter
        ax2 = axes[0, 1]
        
        # Plot individual assets
        for ticker, metrics in self.results['individual_assets'].items():
            ax2.scatter(metrics['annual_volatility'] * 100,
                       metrics['annual_return_arithmetic'] * 100,
                       s=metrics['weight'] * 1000, alpha=0.6, label=ticker)
        
        # Plot portfolio and benchmark
        ax2.scatter(self.results['portfolio']['annual_volatility'] * 100,
                   self.results['portfolio']['annual_return_arithmetic'] * 100,
                   s=300, marker='*', color='gold', edgecolors='black',
                   linewidth=2, label='Portfolio', zorder=5)
        ax2.scatter(self.results['benchmark']['annual_volatility'] * 100,
                   self.results['benchmark']['annual_return_arithmetic'] * 100,
                   s=300, marker='D', color='red', edgecolors='black',
                   linewidth=2, label='S&P 500', zorder=5)
        
        ax2.set_title('Risk-Return Profile', fontweight='bold')
        ax2.set_xlabel('Volatility (Annual %)')
        ax2.set_ylabel('Return (Arithmetic Annual %)')
        ax2.legend(loc='best')
        
        # 3. Sharpe Ratio Comparison
        ax3 = axes[1, 0]
        sharpe_data = {
            'Portfolio': self.results['portfolio']['sharpe_ratio'],
            'S&P 500': self.results['benchmark']['sharpe_ratio']
        }
        
        colors = ['#2E86AB' if v == max(sharpe_data.values()) else '#A23B72' 
                 for v in sharpe_data.values()]
        bars = ax3.bar(sharpe_data.keys(), sharpe_data.values(), color=colors, alpha=0.7)
        ax3.set_title('Sharpe Ratio Comparison', fontweight='bold')
        ax3.set_ylabel('Sharpe Ratio')
        ax3.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
        
        for bar in bars:
            height = bar.get_height()
            va = 'bottom' if height >= 0 else 'top'
            offset = 0.02 if height >= 0 else -0.02
            ax3.text(bar.get_x() + bar.get_width()/2., height + offset,
                    f'{height:.3f}', ha='center', va=va, fontweight='bold')
        
        # 4. Asset Allocation
        ax4 = axes[1, 1]
        asset_weights = [self.results['individual_assets'][ticker]['weight'] 
                        for ticker in self.portfolio.keys()]
        asset_labels = [f"{ticker}\n({weight:.1%})" 
                       for ticker, weight in zip(self.portfolio.keys(), asset_weights)]
        
        colors_pie = plt.cm.Set3(range(len(self.portfolio)))
        ax4.pie(asset_weights, labels=asset_labels, autopct='', 
               colors=colors_pie, startangle=90)
        ax4.set_title('Portfolio Allocation', fontweight='bold')
        
        plt.tight_layout(rect=[0, 0, 1, 0.96])
        plt.savefig('portfolio_analysis.png', dpi=300, bbox_inches='tight')
        plt.close(fig)
        print("\nChart saved: portfolio_analysis.png")

    def run_analysis(self) -> Dict:
        """Executes the complete portfolio analysis workflow."""
        print("Portfolio analysis")
        
        self.fetch_data()
        self.calculate_returns()
        self.calculate_portfolio_returns()
        self.calculate_metrics()
        self.print_results()
        self.plot_results()
        
        return self.results

def main():
    # Define portfolio: {ticker: weight}
    # The analyzer will align to the shortest available history.
    portfolio = {
        'IAU': 0.25,   # iShares Gold Trust
        'IBIT': 0.25,  # iShares Bitcoin Trust
        'AXP': 0.25,   # American Express
        'AAPL': 0.25   # Apple Inc.
    }
    
    analyzer = PortfolioAnalyzer(
        portfolio_dict=portfolio,
        start_date='2021-12-01',  # Will be adjusted by IBIT's start date
        end_date='2024-12-01'
    )
    
    results = analyzer.run_analysis()
    
    # Example of accessing results programmatically
    # print(f"Portfolio Sharpe: {results['portfolio']['sharpe_ratio']:.2f}")

if __name__ == "__main__":
    main()

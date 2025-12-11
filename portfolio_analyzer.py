import warnings
from datetime import datetime, timedelta
from typing import Dict, Optional, Union

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import yfinance as yf

# Suppress warnings for cleaner output
warnings.filterwarnings('ignore')

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
                 start_date: Optional[str] = None, 
                 end_date: Optional[str] = None):
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
        
        self.benchmark = 'SPY'
        self.end_date = end_date or datetime.now().strftime('%Y-%m-%d')
        self.start_date = start_date or (datetime.now() - timedelta(days=365*3)).strftime('%Y-%m-%d')
        
        self.price_data: Optional[pd.DataFrame] = None
        self.returns_data: Optional[pd.DataFrame] = None
        self.portfolio_returns: Optional[pd.Series] = None
        self.benchmark_returns: Optional[pd.Series] = None
        self.results = {}

    def _validate_weights(self) -> None:
        """Validates that portfolio weights sum to 1.0."""
        total_weight = sum(self.portfolio.values())
        if not np.isclose(total_weight, 1.0, atol=0.01):
            raise ValueError(f"Portfolio weights sum to {total_weight:.2%}, must equal 100%")

    def fetch_data(self) -> None:
        """
        Downloads historical price data from Yahoo Finance.
        
        Handles cases where assets have shorter history than the requested range
        by aligning data to the shortest common timeframe.
        """
        print("Fetching historical data...")
        
        tickers = list(self.portfolio.keys()) + [self.benchmark]
        
        # Download data
        data = yf.download(tickers, start=self.start_date, end=self.end_date, progress=False)
        
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
        self.returns_data = self.price_data.pct_change().dropna()

    def calculate_portfolio_returns(self) -> None:
        """Calculates weighted portfolio returns and extracts benchmark returns."""
        if self.returns_data is None:
            raise ValueError("Returns data not available. Run calculate_returns() first.")
            
        portfolio_tickers = list(self.portfolio.keys())
        weights = np.array([self.portfolio[ticker] for ticker in portfolio_tickers])
        
        # Calculate weighted returns
        self.portfolio_returns = (self.returns_data[portfolio_tickers] * weights).sum(axis=1)
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
        annual_return = returns.mean() * 252
        annual_volatility = returns.std() * np.sqrt(252)
        
        if annual_volatility == 0:
            return 0.0
            
        return (annual_return - risk_free_rate) / annual_volatility

    def calculate_max_drawdown(self, cumulative_returns: pd.Series) -> float:
        """Calculates the maximum drawdown from peak."""
        running_max = cumulative_returns.cummax()
        drawdown = (cumulative_returns - running_max) / running_max
        return drawdown.min()

    def calculate_metrics(self) -> None:
        """Calculates comprehensive portfolio and benchmark metrics."""
        if self.portfolio_returns is None or self.benchmark_returns is None:
            raise ValueError("Portfolio returns not calculated.")

        # Helper to calculate metrics for a series
        def get_metrics(returns_series):
            cumulative = (1 + returns_series).cumprod()
            return {
                'annual_return': returns_series.mean() * 252,
                'annual_volatility': returns_series.std() * np.sqrt(252),
                'sharpe_ratio': self.calculate_sharpe_ratio(returns_series),
                'total_return': cumulative.iloc[-1] - 1,
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
        print("\n" + "="*70)
        print("PORTFOLIO ANALYSIS RESULTS")
        print("="*70)
        
        print(f"\n{'Metric':<30} {'Portfolio':>15} {'S&P 500':>15} {'Difference':>15}")
        print("-"*70)
        
        metrics_map = [
            ('Annual Return', 'annual_return', '{:.2%}'),
            ('Annual Volatility', 'annual_volatility', '{:.2%}'),
            ('Sharpe Ratio', 'sharpe_ratio', '{:.3f}'),
            ('Total Return', 'total_return', '{:.2%}'),
            ('Max Drawdown', 'max_drawdown', '{:.2%}')
        ]
        
        for label, key, fmt in metrics_map:
            port_val = self.results['portfolio'][key]
            bench_val = self.results['benchmark'][key]
            diff = port_val - bench_val
            print(f"{label:<30} {fmt.format(port_val):>15} {fmt.format(bench_val):>15} {fmt.format(diff):>15}")
        
        sharpe_diff = self.results['portfolio']['sharpe_ratio'] - self.results['benchmark']['sharpe_ratio']
        print("\n" + "="*70)
        if sharpe_diff > 0:
            print(f"✓ Portfolio OUTPERFORMS S&P 500 by {sharpe_diff:.3f} Sharpe points")
        else:
            print(f"✗ Portfolio UNDERPERFORMS S&P 500 by {abs(sharpe_diff):.3f} Sharpe points")
        print("="*70)
        
        print("\n" + "="*70)
        print("INDIVIDUAL ASSET PERFORMANCE")
        print("="*70)
        print(f"\n{'Ticker':<10} {'Weight':>10} {'Annual Return':>15} {'Volatility':>12} {'Sharpe':>10}")
        print("-"*70)
        
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
                       metrics['annual_return'] * 100,
                       s=metrics['weight'] * 1000, alpha=0.6, label=ticker)
        
        # Plot portfolio and benchmark
        ax2.scatter(self.results['portfolio']['annual_volatility'] * 100,
                   self.results['portfolio']['annual_return'] * 100,
                   s=300, marker='*', color='gold', edgecolors='black',
                   linewidth=2, label='Portfolio', zorder=5)
        ax2.scatter(self.results['benchmark']['annual_volatility'] * 100,
                   self.results['benchmark']['annual_return'] * 100,
                   s=300, marker='D', color='red', edgecolors='black',
                   linewidth=2, label='S&P 500', zorder=5)
        
        ax2.set_title('Risk-Return Profile', fontweight='bold')
        ax2.set_xlabel('Volatility (Annual %)')
        ax2.set_ylabel('Return (Annual %)')
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
            ax3.text(bar.get_x() + bar.get_width()/2., height,
                    f'{height:.3f}', ha='center', va='bottom', fontweight='bold')
        
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
        
        plt.tight_layout()
        plt.savefig('portfolio_analysis.png', dpi=300, bbox_inches='tight')
        print("\n✓ Chart saved as 'portfolio_analysis.png'")

    def run_analysis(self) -> Dict:
        """Executes the complete portfolio analysis workflow."""
        print("\n" + "="*70)
        print("Starting Portfolio Analysis")
        print("="*70)
        
        self.fetch_data()
        self.calculate_returns()
        self.calculate_portfolio_returns()
        self.calculate_metrics()
        self.print_results()
        self.plot_results()
        
        print("\n✓ Analysis complete!")
        return self.results

def main():
    # Define portfolio: {ticker: weight}
    # Note: IBIT (Bitcoin ETF) has limited history (launched Jan 2024).
    # The analyzer will automatically adjust the date range to the shortest history.
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

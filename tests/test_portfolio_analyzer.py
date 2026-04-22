import numpy as np
import pandas as pd
import pytest
import os
import sys
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio_analyzer import PortfolioAnalyzer, TRADING_DAYS_PER_YEAR


def test_invalid_non_numeric_weight_raises_clear_error():
    with pytest.raises(ValueError, match="Invalid weight"):
        PortfolioAnalyzer({'SWPPX': 0.4, 'AXP': 'bad', 'IAU': 0.6})


def test_duplicate_ticker_after_normalization_raises():
    with pytest.raises(ValueError, match="Duplicate ticker"):
        PortfolioAnalyzer({'axp': 0.5, 'AXP': 0.5})


def test_benchmark_collision_raises():
    with pytest.raises(ValueError, match="cannot also be a portfolio holding"):
        PortfolioAnalyzer({'SPY': 1.0})


def test_calculate_returns_requires_more_than_one_row():
    analyzer = PortfolioAnalyzer({'AXP': 1.0}, start_date='2024-01-01', end_date='2024-01-10')
    analyzer.price_data = pd.DataFrame(
        {'AXP': [100.0], 'SPY': [400.0]},
        index=pd.to_datetime(['2024-01-02'])
    )

    with pytest.raises(ValueError, match="Insufficient overlapping data"):
        analyzer._calculate_returns()


def test_calculate_metrics_rejects_empty_return_observations():
    analyzer = PortfolioAnalyzer({'AXP': 1.0}, start_date='2024-01-01', end_date='2024-01-10')
    analyzer.portfolio_returns = pd.Series(dtype=float)
    analyzer.benchmark_returns = pd.Series(dtype=float)

    with pytest.raises(ValueError, match="Insufficient return observations"):
        analyzer._calculate_metrics()


def test_metrics_include_cagr_and_arithmetic_return():
    analyzer = PortfolioAnalyzer({'AXP': 1.0}, start_date='2024-01-01', end_date='2024-01-10')
    idx = pd.to_datetime(['2024-01-02', '2024-01-03', '2024-01-04'])
    analyzer.returns_data = pd.DataFrame(
        {
            'AXP': [0.01, 0.00, -0.005],
            'SPY': [0.002, 0.001, -0.001],
        },
        index=idx,
    )

    analyzer._calculate_portfolio_returns()
    analyzer._calculate_metrics()

    portfolio = analyzer.results['portfolio']

    expected_arithmetic = np.mean([0.01, 0.00, -0.005]) * TRADING_DAYS_PER_YEAR
    expected_total = (1.01 * 1.0 * 0.995) - 1
    expected_years = 3 / TRADING_DAYS_PER_YEAR
    expected_cagr = (1 + expected_total) ** (1 / expected_years) - 1

    assert portfolio['annual_return_arithmetic'] == pytest.approx(expected_arithmetic)
    assert portfolio['total_return'] == pytest.approx(expected_total)
    assert portfolio['annual_return'] == pytest.approx(expected_cagr)

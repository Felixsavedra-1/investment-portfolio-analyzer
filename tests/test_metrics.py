"""
tests/test_metrics.py — Unit tests for metrics.py.

All tests are network-free and use synthetic data.
"""

import numpy as np
import pandas as pd
import pytest

from ledger import Holding
from metrics import (
    cost_basis_weights,
    market_value_weights,
    max_drawdown,
    momentum_signal,
    sharpe_ci,
)


# ── Fixtures ────────────────────────────────────────────────────────────────────

def _holdings() -> dict[str, Holding]:
    return {
        'AXP': Holding(ticker='AXP', shares=3.0,   cost=300.0,
                       first_purchase='2023-01-03T09:30:00', label='American Express'),
        'IAU': Holding(ticker='IAU', shares=5.0,   cost=100.0,
                       first_purchase='2023-06-01T10:00:00', label='Gold (iShares)'),
        'BTC': Holding(ticker='BTC', shares=0.002, cost=100.0,
                       first_purchase='2024-01-01T00:00:00', label='Bitcoin'),
    }


# ── Weight helpers ──────────────────────────────────────────────────────────────

class TestCostBasisWeights:
    def test_sum_to_one(self):
        w = cost_basis_weights(_holdings())
        assert sum(w.values()) == pytest.approx(1.0)

    def test_correct_proportions(self):
        # AXP=$300, IAU=$100, BTC=$100 → 0.60, 0.20, 0.20
        w = cost_basis_weights(_holdings())
        assert w['AXP'] == pytest.approx(0.60)
        assert w['IAU'] == pytest.approx(0.20)
        assert w['BTC'] == pytest.approx(0.20)

    def test_single_holding_is_one(self):
        h = {'X': Holding('X', 1.0, 500.0, '2026-01-01', 'X')}
        assert cost_basis_weights(h)['X'] == pytest.approx(1.0)

    def test_zero_cost_returns_empty(self):
        h = {'X': Holding('X', 0.0, 0.0, '2026-01-01', 'X')}
        assert cost_basis_weights(h) == {}


class TestMarketValueWeights:
    def test_sum_to_one(self):
        prices = {'AXP': 100.0, 'IAU': 20.0, 'BTC': 50_000.0}
        w      = market_value_weights(_holdings(), prices)
        assert sum(w.values()) == pytest.approx(1.0)

    def test_correct_proportions(self):
        holdings = {
            'A': Holding('A', 2.0, 200.0, '2026-01-01', 'A'),
            'B': Holding('B', 1.0, 100.0, '2026-01-01', 'B'),
        }
        prices = {'A': 100.0, 'B': 100.0}  # A=$200, B=$100 → 2/3, 1/3
        w      = market_value_weights(holdings, prices)
        assert w['A'] == pytest.approx(2 / 3)
        assert w['B'] == pytest.approx(1 / 3)

    def test_missing_price_excludes_ticker(self):
        prices = {'AXP': 100.0}
        w      = market_value_weights(_holdings(), prices)
        assert 'IAU' not in w
        assert 'BTC' not in w
        assert w['AXP'] == pytest.approx(1.0)

    def test_zero_shares_excluded(self):
        holdings = {'A': Holding('A', 0.0, 0.0, '2026-01-01', 'A')}
        assert market_value_weights(holdings, {'A': 100.0}) == {}


# ── momentum_signal ─────────────────────────────────────────────────────────────

class TestMomentumSignal:
    FLAT_BAND = 0.01

    def test_nan_input_returns_neutral(self):
        signal, reason = momentum_signal(float('nan'), 0.01, 0.02, self.FLAT_BAND)
        assert signal == 'NEUTRAL'
        assert reason == 'insufficient data'

    def test_bearish_downtrend(self):
        signal, reason = momentum_signal(-0.01, -0.01, -0.05, self.FLAT_BAND)
        assert signal == 'BEARISH'
        assert reason == 'downtrend'

    def test_bearish_bounce(self):
        signal, reason = momentum_signal(0.01, -0.01, -0.05, self.FLAT_BAND)
        assert signal == 'BEARISH'
        assert reason == 'bounce in downtrend'

    def test_bullish_strong_momentum(self):
        signal, reason = momentum_signal(0.01, 0.01, 0.05, self.FLAT_BAND)
        assert signal == 'BULLISH'
        assert reason == 'strong momentum'

    def test_bullish_dip(self):
        signal, reason = momentum_signal(-0.01, 0.01, 0.05, self.FLAT_BAND)
        assert signal == 'BULLISH'
        assert reason == 'dip in uptrend'

    def test_flat_band_upper_edge_is_neutral(self):
        # r1m exactly at the flat band boundary does not count as bullish.
        signal, _ = momentum_signal(0.0, 0.0, self.FLAT_BAND, self.FLAT_BAND)
        assert signal == 'NEUTRAL'

    def test_flat_band_lower_edge_is_neutral(self):
        signal, _ = momentum_signal(0.0, 0.0, -self.FLAT_BAND, self.FLAT_BAND)
        assert signal == 'NEUTRAL'


# ── sharpe_ci ────────────────────────────────────────────────────────────────────

class TestSharpeCI:
    def test_too_few_observations_returns_nan(self):
        low, high = sharpe_ci(pd.Series([0.01]), sharpe=1.0)
        assert np.isnan(low)
        assert np.isnan(high)

    def test_non_finite_sharpe_returns_nan(self):
        low, high = sharpe_ci(pd.Series([0.01, 0.02]), sharpe=float('nan'))
        assert np.isnan(low)
        assert np.isnan(high)

    def test_known_bounds_lo_2002(self):
        # T = 252/252 = 1 year, sharpe = 1.0 → SE = sqrt(1.5) ≈ 1.224745,
        # z_0.975 ≈ 1.959964, so CI ≈ (-1.400, 3.400).
        returns    = pd.Series(np.zeros(252))
        low, high  = sharpe_ci(returns, sharpe=1.0)
        assert low  == pytest.approx(-1.4004558, abs=1e-5)
        assert high == pytest.approx(3.4004558, abs=1e-5)


# ── max_drawdown ─────────────────────────────────────────────────────────────────

class TestMaxDrawdown:
    def test_empty_series_returns_nan(self):
        assert np.isnan(max_drawdown(pd.Series(dtype=float)))

    def test_monotonic_increase_has_zero_drawdown(self):
        cumulative = pd.Series([1.0, 1.1, 1.2, 1.3])
        assert max_drawdown(cumulative) == pytest.approx(0.0)

    def test_known_peak_to_trough(self):
        # Peaks at 1.5, troughs at 0.75 → drawdown = (0.75 - 1.5) / 1.5 = -0.5.
        cumulative = pd.Series([1.0, 1.5, 1.0, 0.75, 1.2])
        assert max_drawdown(cumulative) == pytest.approx(-0.5)

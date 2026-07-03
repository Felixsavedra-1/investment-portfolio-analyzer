"""tests/test_dashboard.py — Unit tests for dashboard.py. Disk-isolated via tmp_path."""

from datetime import date
from unittest.mock import patch

import pytest

import dashboard
from ledger import Holding, SavingsAccount


def test_build_html_escapes_script_breakout(tmp_path):
    """A value containing '</script>' must not terminate the data <script> tag."""
    out = tmp_path / 'dashboard.html'
    payload = {'holdings': [{'label': 'Evil</script><img src=x onerror=alert(1)>'}]}

    with patch.object(dashboard, 'OUT_FILE', out):
        dashboard.build_html(payload)

    written = out.read_text()
    assert '</script><img' not in written
    assert '\\u003c/script>' in written


# ── _build_holdings_data ─────────────────────────────────────────────────────────

class TestBuildHoldingsData:
    def test_price_present(self):
        holdings = {'AAPL': Holding(ticker='AAPL', shares=2.0, cost=200.0,
                                     first_purchase='2023-01-01', label='Apple')}
        prices      = {'AAPL': 150.0}
        prev_prices = {'AAPL': 148.0}
        history     = {'AAPL': {'1M': [140.0, 145.0, 150.0]}}

        rows, portfolio_value, total_cost = dashboard._build_holdings_data(
            holdings, prices, prev_prices, history
        )

        assert portfolio_value == pytest.approx(300.0)
        assert total_cost == pytest.approx(200.0)
        row = rows[0]
        assert row['price'] == pytest.approx(150.0)
        assert row['value'] == pytest.approx(300.0)
        assert row['gain_dollar'] == pytest.approx(100.0)
        assert row['gain_pct'] == pytest.approx(50.0)
        assert row['day_change_dollar'] == pytest.approx(4.0)
        assert row['day_change_pct'] == pytest.approx(round((150.0 - 148.0) / 148.0 * 100, 2))
        assert row['history_1m'] == [140.0, 145.0, 150.0]

    def test_price_missing(self):
        holdings = {'XXX': Holding(ticker='XXX', shares=1.0, cost=50.0,
                                    first_purchase='2023-01-01', label='Unknown')}
        history = {'XXX': {'1M': [1.0, 2.0, 3.0]}}

        rows, portfolio_value, total_cost = dashboard._build_holdings_data(
            holdings, prices={}, prev_prices={}, holding_history=history
        )

        assert portfolio_value == pytest.approx(0.0)
        assert total_cost == pytest.approx(50.0)
        row = rows[0]
        assert row['price'] is None
        assert row['value'] is None
        assert row['gain_pct'] is None
        assert row['gain_dollar'] is None
        assert row['day_change_dollar'] is None
        assert row['day_change_pct'] is None
        assert row['history_1m'] == [1.0, 2.0, 3.0]


# ── _build_savings_data ──────────────────────────────────────────────────────────

class TestBuildSavingsData:
    def _account(self) -> SavingsAccount:
        return SavingsAccount(name='Test', balance=10_000.0, apy=0.04, bank='Bank')

    def test_with_interest_payment_day(self):
        with patch.object(dashboard, 'INTEREST_PAYMENT_DAY', 15):
            rows, savings_total, total_accrued = dashboard._build_savings_data(
                [self._account()], date(2025, 3, 20)
            )

        assert savings_total == pytest.approx(10_000.0)
        row = rows[0]
        assert row['accrued'] == pytest.approx(5.4795, abs=1e-3)
        assert row['projected_payment'] == pytest.approx(33.9726, abs=1e-3)
        assert row['daily_earn'] == pytest.approx(1.0959, abs=1e-3)
        assert row['days_until_payment'] == 26
        assert total_accrued == pytest.approx(5.4795, abs=1e-3)

    def test_without_interest_payment_day(self):
        with patch.object(dashboard, 'INTEREST_PAYMENT_DAY', None):
            rows, savings_total, total_accrued = dashboard._build_savings_data(
                [self._account()], date(2025, 3, 20)
            )

        assert savings_total == pytest.approx(10_000.0)
        row = rows[0]
        assert row['accrued'] is None
        assert row['projected_payment'] is None
        assert row['daily_earn'] is None
        assert row['days_until_payment'] is None
        assert total_accrued == pytest.approx(0.0)


# ── _compute_signal ──────────────────────────────────────────────────────────────

class TestComputeSignal:
    def test_derives_1d_from_last_two_1w_closes(self):
        history = {
            '1W': [100.0, 105.0, 110.0],
            '1M': [90.0, 95.0, 100.0, 105.0, 110.0],
        }
        signal = dashboard._compute_signal(history, flat_band=0.01)
        assert signal['type'] == 'BULLISH'
        assert signal['reason'] == 'strong momentum'

    def test_insufficient_history_is_neutral(self):
        signal = dashboard._compute_signal({}, flat_band=0.01)
        assert signal['type'] == 'NEUTRAL'
        assert signal['reason'] == 'insufficient data'

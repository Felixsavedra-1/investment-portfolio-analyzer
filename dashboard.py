"""dashboard.py — Build and open the animated web dashboard."""

from __future__ import annotations

import base64
import json
import webbrowser
from collections.abc import Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import cast

from config import DATA_DIR, GOALS_FILE, HOLDINGS_FILE, INTEREST_PAYMENT_DAY, MOMENTUM_FLAT_BAND, SAVINGS_FILE, WATCHLIST
from ledger import (
    Holding,
    GOAL_KEY_PORTFOLIO, GOAL_KEY_SAVINGS,
    payment_dates, accrued_interest, projected_next_payment,
    load_goals, load_holdings, load_savings,
)
from metrics import momentum_signal
from prices import fetch_prices_batch, fetch_prices_with_change, fetch_watchlist_analysis, fetch_watchlist_history, fetch_watchlist_info

OUT_FILE     = DATA_DIR / "dashboard.html"
TEMPLATE     = Path(__file__).parent / "dashboard.html"
ANALYSIS_PNG = DATA_DIR / "portfolio_analysis.png"


def _compute_signal(history: dict, flat_band: float) -> dict:
    """Dashboard has no daily-resolution feed; 1D is approximated by the last two 1W closes."""
    p1m = history.get('1M', [])
    p1w = history.get('1W', [])

    def pct_ret(p: list[float]) -> float:
        return (p[-1] - p[0]) / p[0] if len(p) >= 2 and p[0] else float('nan')

    sig, reason = momentum_signal(pct_ret(p1w[-2:]), pct_ret(p1w), pct_ret(p1m), flat_band)
    return {"type": sig, "reason": reason}


def _build_holdings_data(
    holdings: dict[str, Holding],
    prices: dict[str, float],
    prev_prices: dict[str, float],
    holding_history: dict[str, dict[str, list[float]]] | None = None,
) -> tuple[list[dict[str, object]], float, float]:
    rows = []
    portfolio_value = 0.0
    total_cost = 0.0
    history = holding_history or {}
    for ticker, h in holdings.items():
        total_cost += h.cost
        price = prices.get(ticker)
        if price is None:
            rows.append({
                "ticker": ticker, "label": h.label,
                "shares": round(h.shares, 4), "cost": round(h.cost, 2),
                "price": None, "value": None,
                "gain_pct": None, "gain_dollar": None,
                "day_change_dollar": None, "day_change_pct": None,
                "history_1m": history.get(ticker, {}).get('1M', []),
            })
            continue
        value          = h.shares * price
        gain_dollar    = value - h.cost
        gain_pct          = (gain_dollar / h.cost * 100) if h.cost > 0 else None
        prev              = prev_prices.get(ticker, price)
        day_change_dollar = (price - prev) * h.shares
        day_change_pct    = (price - prev) / prev * 100 if prev else None
        portfolio_value += value
        rows.append({
            "ticker":            ticker,
            "label":             h.label,
            "shares":            round(h.shares, 4),
            "cost":              round(h.cost, 2),
            "price":             round(price, 2),
            "value":             round(value, 2),
            "gain_pct":          round(gain_pct, 2) if gain_pct is not None else None,
            "gain_dollar":       round(gain_dollar, 2),
            "day_change_dollar": round(day_change_dollar, 2),
            "day_change_pct":    round(day_change_pct, 2) if day_change_pct is not None else None,
            "history_1m":        history.get(ticker, {}).get('1M', []),
        })
    return rows, portfolio_value, total_cost


def _build_savings_data(savings_acc: list, today_d: date) -> tuple[list[dict], float, float]:
    rows = []
    savings_total = 0.0
    total_accrued = 0.0
    days_until: int | None = None
    if INTEREST_PAYMENT_DAY:
        _, next_date = payment_dates(INTEREST_PAYMENT_DAY, today_d)
        days_until = (next_date - today_d).days
    for acc in savings_acc:
        savings_total += acc.balance
        acc_interest: float | None = None
        proj_payment: float | None = None
        daily_earn:   float | None = None
        if INTEREST_PAYMENT_DAY:
            acc_interest  = accrued_interest(acc, INTEREST_PAYMENT_DAY, today_d)
            proj_payment  = projected_next_payment(acc, INTEREST_PAYMENT_DAY, today_d)
            daily_earn    = acc.daily_interest
            total_accrued += acc_interest
        rows.append({
            "name":               acc.name,
            "balance":            round(acc.balance, 2),
            "apy":                acc.apy,
            "bank":               acc.bank,
            "accrued":            round(acc_interest, 4) if acc_interest is not None else None,
            "projected_payment":  round(proj_payment, 4) if proj_payment is not None else None,
            "days_until_payment": days_until,
            "daily_earn":         round(daily_earn, 4) if daily_earn is not None else None,
        })
    return rows, savings_total, total_accrued


def _build_watchlist_data() -> list[dict]:
    if not WATCHLIST:
        return []
    wl_tickers  = list(WATCHLIST.keys())
    wl_prices   = fetch_prices_batch(wl_tickers)
    wl_history  = fetch_watchlist_history(wl_tickers)
    wl_info     = fetch_watchlist_info(wl_tickers)
    wl_analysis = fetch_watchlist_analysis(wl_tickers, wl_history, wl_info)
    rows = []
    for ticker, label in WATCHLIST.items():
        history = wl_history.get(ticker, {})
        signal  = _compute_signal(history, MOMENTUM_FLAT_BAND)
        info    = wl_info.get(ticker, {})
        rows.append({
            "ticker":      ticker,
            "label":       label,
            "price":       round(p, 2) if (p := wl_prices.get(ticker)) is not None else None,
            "signal":      signal["type"],
            "reason":      signal["reason"],
            "history":     history,
            "description": info.get("description", ""),
            "sector":      info.get("sector", ""),
            "analysis":    wl_analysis.get(ticker, {}),
        })
    return rows


def build_payload(
    prices: dict[str, float] | None = None,
    prev_prices: dict[str, float] | None = None,
) -> dict[str, object]:
    holdings    = load_holdings(HOLDINGS_FILE)
    savings_acc = load_savings(SAVINGS_FILE)
    goals       = load_goals(GOALS_FILE)

    tickers = list(holdings.keys())
    if prices is None:
        raw         = fetch_prices_with_change(tickers) if tickers else {}
        prices      = {t: v['price']      for t, v in raw.items() if v['price'] is not None}
        prev_prices = {t: v['prev_close'] for t, v in raw.items() if v['prev_close'] is not None}
    prev_prices = prev_prices or {}

    holding_history = fetch_watchlist_history(tickers) if tickers else {}
    holding_rows, portfolio_value, total_cost = _build_holdings_data(holdings, prices, prev_prices, holding_history)
    holding_rows.sort(key=lambda r: cast(float, r["value"]) if r["value"] is not None else -1.0, reverse=True)

    savings_rows, savings_total, total_accrued = _build_savings_data(savings_acc, date.today())

    total_gain_pct = ((portfolio_value - total_cost) / total_cost * 100) if total_cost > 0 else 0.0
    portfolio_goal = goals.get(GOAL_KEY_PORTFOLIO)
    savings_goal   = goals.get(GOAL_KEY_SAVINGS)

    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "savings":   savings_rows,
        "holdings":  holding_rows,
        "watchlist": _build_watchlist_data(),
        "chart_src": _embed_chart(),
        "totals": {
            "portfolio_value":  round(portfolio_value, 2),
            "savings_total":    round(savings_total, 2),
            "total_cost":       round(total_cost, 2),
            "total_gain_pct":   round(total_gain_pct, 2),
            "portfolio_goal":   portfolio_goal,
            "savings_goal":     savings_goal,
            "total_accrued":    round(total_accrued, 4) if INTEREST_PAYMENT_DAY else None,
            "payment_day":      INTEREST_PAYMENT_DAY,
        },
    }


def _embed_chart() -> str:
    if not ANALYSIS_PNG.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(ANALYSIS_PNG.read_bytes()).decode()


def build_html(payload: Mapping[str, object]) -> Path:
    template    = TEMPLATE.read_text()
    placeholder = "// __DASH_DATA_PLACEHOLDER__"
    if template.count(placeholder) != 1:
        raise RuntimeError(
            f"Expected exactly 1 occurrence of '{placeholder}' in template, "
            f"found {template.count(placeholder)}."
        )
    # Escape '<' so a value containing '</script>' cannot break out of the tag.
    data = json.dumps(payload, indent=2).replace('<', '\\u003c')
    injected = template.replace(placeholder, f"window.__DASH__ = {data};")
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(injected)
    return OUT_FILE


def main() -> None:
    print("Fetching portfolio data…")
    out = build_html(build_payload())
    print(f"Dashboard written to {out}")
    try:
        webbrowser.open(out.as_uri())
    except (OSError, RuntimeError):
        print(f"  Open manually → {out.as_uri()}")


if __name__ == "__main__":
    main()

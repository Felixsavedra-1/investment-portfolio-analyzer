"""ledger.py — Portfolio data model and JSON I/O. All file access goes through this module."""

from __future__ import annotations

import calendar
import json
import math
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

GOAL_KEY_PORTFOLIO = '__portfolio__'
GOAL_KEY_SAVINGS   = '__savings__'


@dataclass
class Holding:
    ticker:         str
    shares:         float
    cost:           float
    first_purchase: str
    label:          str

    @property
    def avg_cost_per_share(self) -> float:
        return self.cost / self.shares if self.shares > 0 else 0.0

    @property
    def start_date(self) -> str:
        return self.first_purchase[:10]


@dataclass
class Transaction:
    id:           str
    timestamp:    str           # ISO 8601 full datetime, e.g. "2026-04-13T14:30:22.841504"
    action:       str
    ticker:       str
    shares:       float
    dollars:      float
    price:        float
    realized_pnl: float | None = None   # None on buys; dollar gain/loss on sells
    notes:        str = ""


def _atomic_write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + '.tmp')
    try:
        with open(tmp, 'w') as f:
            json.dump(data, f, indent=2)
        tmp.rename(path)
    except OSError as exc:
        sys.exit(f"Failed to write {path}: {exc}")


def _coerce_float(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return default if not math.isfinite(v) else v


def _load_json(path: Path) -> Any:
    with open(path) as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            sys.exit(f"Corrupt data file: {path}\nFix or remove the file to continue.")


def load_holdings(path: Path) -> dict[str, Holding]:
    if not path.exists():
        return {}
    data = _load_json(path)
    result = {}
    for ticker, v in data.items():
        result[ticker] = Holding(
            ticker=ticker,
            shares=_coerce_float(v.get('shares')),
            cost=_coerce_float(v.get('cost')),
            first_purchase=v.get('first_purchase') or v.get('start_date', ''),
            label=v.get('label', ticker),
        )
    return result


def save_holdings(holdings: dict[str, Holding], path: Path) -> None:
    _atomic_write(path, {
        ticker: {
            'shares':         h.shares,
            'cost':           h.cost,
            'first_purchase': h.first_purchase,
            'label':          h.label,
        }
        for ticker, h in holdings.items()
    })


def load_transactions(path: Path) -> list[Transaction]:
    if not path.exists():
        return []
    raw = _load_json(path)
    result = []
    for i, r in enumerate(raw):
        timestamp = r.get('timestamp') or r.get('date', '')
        action    = (r.get('action') or r.get('type', '')).lower()
        txn_id    = r.get('id') or f"txn_{timestamp[:10]}_{r.get('ticker', '')}_{i}"
        shares    = _coerce_float(r.get('shares'))
        price     = _coerce_float(r.get('price'))
        _rpnl     = r.get('realized_pnl')
        if isinstance(_rpnl, float) and not math.isfinite(_rpnl):
            _rpnl = None
        result.append(Transaction(
            id=txn_id,
            timestamp=timestamp,
            action=action,
            ticker=r.get('ticker', ''),
            shares=shares,
            dollars=_coerce_float(r.get('dollars')),
            price=price,
            realized_pnl=_rpnl,
            notes=r.get('notes', ''),
        ))
    return result


def append_transaction(txn: Transaction, path: Path) -> None:
    existing = _load_json(path) if path.exists() else []
    existing.append({
        'id':           txn.id,
        'timestamp':    txn.timestamp,
        'action':       txn.action,
        'ticker':       txn.ticker,
        'shares':       txn.shares,
        'dollars':      txn.dollars,
        'price':        txn.price,
        'realized_pnl': txn.realized_pnl,
        'notes':        txn.notes,
    })
    _atomic_write(path, existing)


@dataclass
class SavingsAccount:
    name:    str
    balance: float
    apy:     float   # decimal, e.g. 0.04 for 4%
    bank:    str = ''

    @property
    def monthly_interest(self) -> float:
        return self.balance * self.apy / 12

    @property
    def daily_interest(self) -> float:
        return self.balance * self.apy / 365


def load_savings(path: Path) -> list[SavingsAccount]:
    if not path.exists():
        return []
    data = _load_json(path)
    return [
        SavingsAccount(
            name=r['name'],
            balance=_coerce_float(r.get('balance')),
            apy=_coerce_float(r.get('apy')),
            bank=r.get('bank', ''),
        )
        for r in data
    ]


def save_savings(accounts: list[SavingsAccount], path: Path) -> None:
    _atomic_write(path, [
        {'name': a.name, 'balance': a.balance, 'apy': a.apy, 'bank': a.bank}
        for a in accounts
    ])


def load_goals(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    return _load_json(path)


def save_goals(goals: dict[str, float], path: Path) -> None:
    _atomic_write(path, goals)


def payment_dates(payment_day: int, today: date) -> tuple[date, date]:
    """Returns (last_payment_date, next_payment_date) for a given day-of-month."""
    def safe_date(year: int, month: int, day: int) -> date:
        return date(year, month, min(day, calendar.monthrange(year, month)[1]))

    if today.day >= payment_day:
        last        = safe_date(today.year, today.month, payment_day)
        next_month  = today.month % 12 + 1
        next_year   = today.year + (1 if today.month == 12 else 0)
        next_       = safe_date(next_year, next_month, payment_day)
    else:
        prev_month  = (today.month - 2) % 12 + 1
        prev_year   = today.year - (1 if today.month == 1 else 0)
        last        = safe_date(prev_year, prev_month, payment_day)
        next_       = safe_date(today.year, today.month, payment_day)
    return last, next_


def accrued_interest(
    account: SavingsAccount,
    payment_day: int,
    today: date | None = None,
) -> float:
    """Interest accrued since the last payment date (balance × APY / 365 × days)."""
    today = today or date.today()
    last, _ = payment_dates(payment_day, today)
    days = (today - last).days
    return account.balance * account.apy / 365 * days


def projected_next_payment(
    account: SavingsAccount,
    payment_day: int,
    today: date | None = None,
) -> float:
    """Projected interest for the full current cycle (last → next payment)."""
    today = today or date.today()
    last, next_ = payment_dates(payment_day, today)
    days = (next_ - last).days
    return account.balance * account.apy / 365 * days

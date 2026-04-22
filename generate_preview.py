"""
generate_preview.py — Write a static demo dashboard to ~/.portfolio/dashboard.html.

Run this, open the printed path in a browser, screenshot at ~1400×800px,
and save the result as docs/dashboard-preview.png.
"""

import webbrowser
from dashboard import build_html

# Realistic 1W price histories (Mon–Fri, 5 points) for sparklines
_JPM_1W   = [246.10, 248.90, 247.20, 249.80, 248.30]
_JPM_1M   = [241.00, 243.50, 246.20, 244.80, 247.30, 245.60, 248.90, 247.20, 249.80, 248.30]
_GOOGL_1W = [165.40, 163.20, 161.50, 158.90, 156.80]
_GOOGL_1M = [176.20, 174.50, 172.80, 170.10, 168.40, 166.90, 164.30, 162.50, 159.80, 156.80]
_META_1W  = [572.00, 578.40, 584.20, 589.80, 592.40]
_META_1M  = [545.00, 551.30, 558.60, 563.40, 567.80, 571.20, 575.90, 581.40, 587.60, 592.40]

PAYLOAD = {
    "generated": "2026-04-14T08:02:00",
    "holdings": [
        {"ticker": "NVDA",  "label": "NVIDIA Corp.",              "shares": 180.00, "cost":   9720.00, "price": 118.00, "value": 21240.00, "gain_pct": 118.52, "gain_dollar": 11520.00, "day_change_dollar":  392.94, "day_change_pct": 1.85},
        {"ticker": "AAPL",  "label": "Apple Inc.",                "shares":  95.00, "cost":  14440.00, "price": 199.00, "value": 18905.00, "gain_pct":  30.92, "gain_dollar":  4465.00, "day_change_dollar":   85.07, "day_change_pct": 0.45},
        {"ticker": "AXP",   "label": "American Express Company",  "shares":  75.00, "cost":  13500.00, "price": 242.00, "value": 18150.00, "gain_pct":  34.44, "gain_dollar":  4650.00, "day_change_dollar":  223.25, "day_change_pct": 1.23},
        {"ticker": "SWPPX", "label": "Schwab S&P 500 Index Fund", "shares": 240.00, "cost":  13440.00, "price":  73.40, "value": 17616.00, "gain_pct":  31.07, "gain_dollar":  4176.00, "day_change_dollar":   72.23, "day_change_pct": 0.41},
    ],
    "savings": [
        {"name": "Car Fund",     "bank": "Amex", "balance":  12450.00, "apy": 0.0400},
        {"name": "Housing Fund", "bank": "Amex", "balance":  38200.00, "apy": 0.0400},
    ],
    "watchlist": [
        {"ticker": "JPM",   "label": "JPMorgan",        "price": 248.30, "signal": "NEUTRAL", "reason": "mixed signals",    "history": {"1W": _JPM_1W,   "1M": _JPM_1M}},
        {"ticker": "GOOGL", "label": "Alphabet",        "price": 156.80, "signal": "BEARISH", "reason": "downtrend",        "history": {"1W": _GOOGL_1W, "1M": _GOOGL_1M}},
        {"ticker": "META",  "label": "Meta Platforms",  "price": 592.40, "signal": "BULLISH", "reason": "strong momentum",  "history": {"1W": _META_1W,  "1M": _META_1M}},
    ],
    "totals": {
        "portfolio_value":  75911.00,
        "savings_total":    50650.00,
        "total_cost":       51100.00,
        "total_gain_pct":   48.55,
        "portfolio_goal":   150000,
        "savings_goal":      75000,
    },
}

out = build_html(PAYLOAD)

# Disable all animations so the screenshot captures fully-rendered charts.
# Normal `brief` users still get the full animated experience.
html = out.read_text()
html = html.replace(
    '</head>',
    '<style>*, *::before, *::after {'
    ' animation-duration: 0.001ms !important;'
    ' transition-duration: 0.001ms !important; }'
    '</style>\n</head>',
)
out.write_text(html)

print(f"Preview written to: {out}")
webbrowser.open(out.as_uri())

# Strategy Reference

Quick-reference documents and strategy cards for the screening and trading methodologies used across all tools.

## Files

| File | Description |
|---|---|
| `momentum_strategy_reference.py` | Quick-reference card for the Indian momentum trading strategy — capital deployment, sizing, entry/exit rules, Darvas Box logic |

## Strategy overview

### Darvas Box
- Box top: highest high in a confirmed window where the next N bars all close below it
- Box bottom: lowest low in the same window where the next N bars all close above it
- **Current bar always excluded from box formation** — prevents lookahead bias
- Signal: price crossing box top → `BREAKOUT_BUY`; crossing box bottom → `BREAKDOWN_SELL`

### Piotroski F-Score (9 criteria)
Profitability (4): ROA > 0, OCF > 0, improving ROA, OCF > ROA (accrual quality)
Leverage (3): falling long-term debt ratio, improving current ratio, no share dilution
Efficiency (2): improving gross margin, improving asset turnover

Score ≥ 7 = financially strong.

### Coffee Can criteria
| Criterion | Threshold |
|---|---|
| Revenue CAGR | > 10% over available history |
| Avg ROCE | > 15% |
| Debt / Equity | < 1.0 |
| Market cap | ≥ threshold per market (₹500 Cr India / ¥100B Japan / ₩100B Korea / €1B Europe) |
| Net income | Positive every reported year |
| Free cash flow | Positive in most recent year |

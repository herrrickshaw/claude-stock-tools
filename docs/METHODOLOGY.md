# Methodology: Darvas Box, Piotroski F-Score, Coffee Can

(Moved verbatim from the former README.)

### Stage 1 — Darvas Box (price structure)

The Darvas Box algorithm identifies periods where price has consolidated inside a defined range and then breaks out.

**Box formation rules:**
- A **box top** is the highest high in a window where the *next N bars* all have lower highs — confirming the top is established
- A **box bottom** is the lowest low in the same window where the *next N bars* all have higher lows
- **The current bar is always excluded from box formation** — this is critical; including it makes breakdown detection on the current day impossible
- Confirmation period (`confirm=3` bars) is consistent across all markets

**Signals generated:**
- `BREAKOUT_BUY` — price closes above the confirmed box top
- `BREAKDOWN_SELL` — price closes below the confirmed box bottom
- `IN_BOX` — price is inside a confirmed box, no signal yet

All OHLC data is 1-year daily history. A 200-day moving average filter is applied where data permits — breakouts below the 200-day MA are still flagged but noted.

---

### Stage 2 — Piotroski F-Score (financial health)

The Piotroski F-Score scores a company out of 9 points using publicly reported financials. Only breakout candidates (not the full universe) are scored, which keeps runtime manageable.

| Group | Criterion | What it measures |
|---|---|---|
| **Profitability** | F1: ROA > 0 | Profitable on assets |
| | F2: OCF > 0 | Positive operating cash flow |
| | F3: ROA improving YoY | Getting more profitable |
| | F4: OCF > Net income (accrual) | Earnings backed by real cash |
| **Leverage / Liquidity** | F5: Long-term debt ratio falling | Deleveraging |
| | F6: Current ratio improving | Better short-term coverage |
| | F7: No new shares issued | No dilution |
| **Operating efficiency** | F8: Gross margin improving | Pricing power strengthening |
| | F9: Asset turnover improving | More revenue per dollar of assets |

**Score ≥ 7 = financially strong.** Scores of 8–9 are considered excellent.

---

### Stage 3 — Coffee Can (compounder filter)

The Coffee Can criteria, adapted from the long-hold investing framework, ensure the breakout stock is a genuine quality business — not just a temporarily extended price.

| Criterion | Threshold | Notes |
|---|---|---|
| Revenue CAGR | > 10% | Measured over all available annual data |
| Average ROCE | > 15% | Avg across last 3–5 reported years |
| Debt / Equity | < 1.0 | Most recent year |
| Market cap | Market-specific floor (see below) | Filters out microcaps with thin data |
| Net income | Positive every reported year | No loss-making years allowed |
| Free cash flow | Positive in most recent year | FCF = OCF − CapEx |

**Market cap floors:**

| Market | Floor |
|---|---|
| India | ₹500 Cr |
| USA | $1B |
| Europe | €1B |
| Japan | ¥100B |
| South Korea | ₩100B |
| Singapore | S$100M |

---

### Triple Hit filter

A stock is a **Triple Hit** when it passes all three gates simultaneously:

```
BREAKOUT_BUY  +  Piotroski ≥ 7  +  Coffee Can: all 6 criteria PASS
```

Triple hits are the primary output of the screener. They represent stocks that are breaking out of a price consolidation, are financially healthy on 9 objective criteria, and have demonstrated durable compounding quality over multiple years.

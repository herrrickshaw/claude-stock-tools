# Screener, report, Buffett and enrichment usage

(Moved verbatim from the former README.)

### 01 · Daily reports

Run against a single ticker. Prints a formatted terminal report covering:

- Live price, 52-week range, and day change
- Valuation multiples (PE, PB, EV/EBITDA)
- Piotroski F-Score with individual criterion breakdown
- Coffee Can checklist with pass/fail per criterion
- Current Darvas Box status and signal

```bash
python 01_daily_reports/india_daily_report.py RELIANCE
python 01_daily_reports/india_daily_report.py TCS

python 01_daily_reports/us_daily_report.py AAPL
python 01_daily_reports/us_daily_report.py MSFT

python 01_daily_reports/singapore_daily_report.py D05.SI
```

---

## 02 · Market screeners

Full-universe batch scanners. Each run:

1. Fetches the complete list of listed equities from the exchange
2. Batch-downloads 1-year daily OHLC for all stocks (in parallel, 200 per batch)
3. Runs Darvas Box detection on every stock
4. Runs Piotroski + Coffee Can only on BREAKOUT candidates (capped at 200–300 freshest)
5. Saves a styled 4-sheet Excel workbook: All Signals / Breakouts / Fundamentals / Triple Hits

```bash
# India
python 02_market_screeners/india_market_scan.py
python 02_market_screeners/india_market_scan.py --workers 12

# US (large universe — expect 60–90 min)
python 02_market_screeners/us_market_scan.py

# Europe
python 02_market_screeners/europe_market_scan.py
python 02_market_screeners/europe_market_scan.py --min-cap 5   # €5B+ only
python 02_market_screeners/europe_market_scan.py --exchange PA DE  # Paris + Xetra only

# Japan
python 02_market_screeners/japan_market_scan.py --workers 10

# Korea
python 02_market_screeners/korea_market_scan.py
python 02_market_screeners/korea_market_scan.py --kospi-only
```

---

## 03b · Buffett Value Screener

Warren Buffett-style stock picker using four gates: financial fortress, durable moat, rational management, and valuation discipline. Unlike Darvas (technical), this focuses on long-term compounding quality.

**Gates:**
- **Fortress** — ROE ≥15%, debt/equity ≤0.5, gross margin ≥30%, FCF yield ≥5%
- **Moat** — Margin stability ≥85%, profitable every year for 5 years
- **Valuation** — PE ≤20, discount to fair value ≥25% (margin of safety)
- **Output** — Ranked by quality×discount score (best moat at best price)

```bash
# Screen all markets (US, Europe, India, Japan, Korea)
python 02_market_screeners/buffett_value_screener.py --market all --workers 8

# Screen single market with custom limits
python 02_market_screeners/buffett_value_screener.py --market us --limit 500

# Output: buffett_value_scan_YYYY-MM-DD.xlsx with all screens + passes
```

See [Buffett Methodology](04_strategy_reference/buffett_methodology.md) for detailed principles, moat types, and comparison to Darvas/Piotroski/Coffee Can.

---

## 03 · Colab notebooks

Open directly in Google Colab — no local Python install needed.

| Notebook | Market | Open |
|---|---|---|
| `india_stock_analysis.ipynb` | NSE + BSE | [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/herrrickshaw/claude-stock-tools/blob/main/03_colab_notebooks/india_stock_analysis.ipynb) |
| `india_stock_reporting.ipynb` | NSE + BSE | [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/herrrickshaw/claude-stock-tools/blob/main/03_colab_notebooks/india_stock_reporting.ipynb) |
| `us_market_screener.ipynb` | NYSE + NASDAQ | [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/herrrickshaw/claude-stock-tools/blob/main/03_colab_notebooks/us_market_screener.ipynb) |

---

## 05 · Enrichment

Post-screening analysis tools to evaluate quality, moats, and valuations.

### Damodaran sector benchmarks

Enriches screener output with sector-level valuation benchmarks and country risk data from [Aswath Damodaran's NYU Stern dataset archive](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datacurrent.html) (January/April 2026 vintage).

```bash
# Enrich a scanner output
python 05_enrichment/damodaran_enrichment.py \
    --file europe_scan_2026-06-13.xlsx \
    --sheet "Triple Hits" \
    --market europe \
    --country-col Country \
    --pe-col PE
```

**Columns added:** Sector PE/EV/EBITDA/ROE/margins + Country ERP premiums. Data cached for 24h.

### Moat analyzer

Evaluates competitive advantage strength for identified picks using five indicators: margin stability (pricing power), ROE consistency, market dominance, asset efficiency, dividend policy. Rates moats 1-5 stars.

```bash
# Analyze moat for a single ticker
python 05_enrichment/moat_analyzer.py MSFT

# Output: Moat strength rating, type signals, and detailed explanations
```

Example output: "Asset-light model (5★). Margin rock-solid (5★). Consistent ROE >20% (5★). → STRONG MOAT"

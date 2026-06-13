# Claude Stock Tools

A collection of quantitative stock screening and analysis tools covering **six global markets** — India, USA, Europe, Japan, South Korea, and Singapore. Every tool runs the same three-stage pipeline:

```
1. Darvas Box  →  detect price breakouts across the full exchange universe
2. Piotroski F-Score  →  filter breakout candidates by financial health
3. Coffee Can  →  retain only compounders with durable growth and low debt
```

Stocks that pass all three stages simultaneously are called **Triple Hits** — the primary output of every screener.

---

## Table of contents

- [How the pipeline works](#how-the-pipeline-works)
- [Repository structure](#repository-structure)
- [01 · Daily reports](#01--daily-reports)
- [02 · Market screeners](#02--market-screeners)
- [03 · Colab notebooks](#03--colab-notebooks)
- [05 · Damodaran enrichment](#05--damodaran-enrichment)
- [Install](#install)
- [Data sources](#data-sources)
- [Limitations and assumptions](#limitations-and-assumptions)
- [Results snapshot — 13 Jun 2026](#results-snapshot--13-jun-2026)

---

## How the pipeline works

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

---

## Repository structure

```
claude-stock-tools/
├── 01_daily_reports/
│   ├── india_daily_report.py        Single-stock report for NSE/BSE
│   ├── us_daily_report.py           Single-stock report for NYSE/NASDAQ
│   └── singapore_daily_report.py   Single-stock report for SGX
│
├── 02_market_screeners/
│   ├── india_market_scan.py         Full NSE+BSE universe (~4,600 stocks)
│   ├── us_market_scan.py            Full NYSE+NASDAQ universe (~5,400 stocks)
│   ├── europe_market_scan.py        17 European exchanges (~1,700 stocks)
│   ├── japan_market_scan.py         TSE Prime+Standard (~3,600 stocks)
│   └── korea_market_scan.py         KOSPI+KOSDAQ (~2,600 stocks)
│
├── 03_colab_notebooks/
│   ├── india_stock_analysis.ipynb   NSE+BSE analysis notebook
│   ├── india_stock_reporting.ipynb  Indian daily report notebook
│   ├── us_market_screener.ipynb     US screener in Colab
│   └── us_stocks_colab_script.py    Combined Colab-compatible script
│
├── 04_strategy_reference/
│   └── momentum_strategy_reference.py  India momentum strategy card
│
├── 05_enrichment/
│   └── damodaran_enrichment.py     Post-scan enrichment with NYU Stern benchmarks
│
└── CHANGELOG.md
```

---

## 01 · Daily reports

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

## 03 · Colab notebooks

Open directly in Google Colab — no local Python install needed.

| Notebook | Market | Open |
|---|---|---|
| `india_stock_analysis.ipynb` | NSE + BSE | [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/herrrickshaw/claude-stock-tools/blob/main/03_colab_notebooks/india_stock_analysis.ipynb) |
| `india_stock_reporting.ipynb` | NSE + BSE | [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/herrrickshaw/claude-stock-tools/blob/main/03_colab_notebooks/india_stock_reporting.ipynb) |
| `us_market_screener.ipynb` | NYSE + NASDAQ | [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/herrrickshaw/claude-stock-tools/blob/main/03_colab_notebooks/us_market_screener.ipynb) |

---

## 05 · Damodaran enrichment

After a screener run, enrich the Triple Hits sheet with sector-level valuation benchmarks and country risk data from [Aswath Damodaran's NYU Stern dataset archive](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datacurrent.html) (January/April 2026 vintage).

**Columns added:**

| Column | Source |
|---|---|
| `Sector_PE_Trailing` | How does the stock's PE compare to its sector median? |
| `Sector_EV_EBITDA` | Sector median EV/EBITDA — is the stock cheap or expensive relative to peers? |
| `Sector_ROE` / `Sector_ROIC` | Sector average returns — context for the 15% ROCE threshold |
| `Sector_Net_Margin` | Sector median net margin |
| `Country_ERP_pct` | Total equity risk premium for the country (Moody's-based, %) |
| `Country_CRP_pct` | Country risk premium above the mature market baseline |

```bash
# Enrich a scanner output
python 05_enrichment/damodaran_enrichment.py \
    --file europe_scan_2026-06-13.xlsx \
    --sheet "Triple Hits" \
    --market europe \
    --country-col Country \
    --pe-col PE

# Preview the benchmark tables without processing a file
python 05_enrichment/damodaran_enrichment.py --market global --preview
```

Data is cached locally for 24 hours. Supports markets: `us`, `europe`, `japan`, `india`, `emerging`, `global`.

---

## Install

```bash
pip install yfinance pandas openpyxl requests nsepython bseindia kabupy pykrx
```

**Per-market dependencies:**

| Market | Extra requirement |
|---|---|
| India | `nsepython`, `bseindia` |
| Japan | `kabupy` |
| Korea | `pykrx` |
| US / Europe / Singapore | yfinance only |

Python 3.9+ required.

---

## Data sources

| Data | Source | Notes |
|---|---|---|
| **India universe** | NSE bhavcopy (nsepython) + BSE security master (bseindia) | ~2,364 NSE + 317 BSE-only = ~4,600 after dedup |
| **India OHLC** | yfinance (NSE suffix `.NS`, BSE suffix `.BO`) | nsepython equity_history skipped — requires browser cookies on Mac |
| **US universe** | yfinance `get_tickers()` / Nasdaq screener | ~5,400 NYSE + NASDAQ combined |
| **US OHLC + fundamentals** | yfinance | Auto-adjusted prices |
| **Europe universe** | `EU_All_Listed_Companies_Full_Jun2026.xlsx` (Database sheet) | 1,851 total; 1,704 after excluding US-listed EU-domiciled firms |
| **Europe OHLC + fundamentals** | yfinance (exchange suffixes: `.PA`, `.DE`, `.AS`, `.MI`, etc.) | 24 suffixes mapped across 17 exchanges |
| **Japan universe** | `kabupy.Jpx().issues` (JPX master list) | ~4,451 records; ~3,566 domestic equities after filtering `内国株式` |
| **Japan OHLC** | yfinance (`.T` suffix) | Bulk download in 200-stock batches |
| **Japan fundamentals** | yfinance | |
| **Korea universe** | KRX KIND open endpoint (`kind.krx.co.kr/corpgeneral/corpList.do`) | KOSPI + KOSDAQ; no auth required; parsed with `pd.read_html(encoding='euc-kr')` |
| **Korea OHLC** | pykrx `get_market_ohlcv_by_date()` | Backed by Naver Finance — accessible outside Korea |
| **Korea fundamentals** | yfinance (`.KS` KOSPI / `.KQ` KOSDAQ) | KRX ticker-list API is geo-blocked; KIND endpoint used instead |
| **Singapore OHLC + fundamentals** | yfinance (`.SI` suffix) | |
| **Sector benchmarks** | Damodaran / NYU Stern (Jan 2026) | PE, EV/EBITDA, ROE/ROIC, profit margins by sector; global and regional datasets |
| **Country risk premiums** | Damodaran / NYU Stern (Apr 2026) | Moody's sovereign rating → default spread → equity risk premium |
| **All financial statement data** | yfinance (`Ticker.income_stmt`, `.balance_sheet`, `.cashflow`) | Point-in-time annual figures; version-compat wrapper handles API changes |

---

## Limitations and assumptions

### Data quality

- **yfinance is unofficial.** It scrapes Yahoo Finance. Data can be missing, delayed, or wrong for specific tickers — especially for smaller-cap, less-liquid stocks. Always verify interesting results against a primary source (exchange filing, Bloomberg).
- **Financial statement data via yfinance is annual.** Piotroski and Coffee Can scores are based on annual reports, not quarterly. A company that deteriorated mid-year may still score well.
- **yfinance `debtToEquity` is sometimes reported on a 0–100 scale rather than 0–1.** The code normalises values > 10 by dividing by 100, but edge cases in unusual capital structures may slip through.
- **Korean fundamentals via yfinance `.KS`/`.KQ` have incomplete coverage.** Many KOSDAQ-listed companies are not in Yahoo Finance's database. Stocks with missing fundamentals are excluded from Piotroski/Coffee Can scoring rather than assumed to pass.

### Darvas Box

- **The current (most recent) bar is always excluded from box formation.** This is intentional — including it would make it impossible to detect a breakdown on the day it happens. It means the most recent bar's relationship to the box is always evaluated fresh.
- **Confirm = 3 bars.** Box tops and bottoms require 3 subsequent bars that respect the boundary. This is a design choice; shorter windows produce more noise, longer windows miss fast moves.
- **1-year lookback.** Boxes are formed from 252 trading days of history. A stock that broke out more than a year ago will not show a signal unless it has formed a new box.
- **No volume confirmation.** Classic Darvas methodology requires volume expansion on a breakout. This implementation uses price structure only. Volume filtering is left to the user's judgement after reviewing the output.

### Piotroski F-Score

- **Requires at least two years of financial data.** Year-over-year comparisons (ROA trend, gross margin trend, asset turnover trend) need two annual periods. Stocks with only one year of data in yfinance receive partial scores.
- **Score thresholds are uniform across markets.** A score of ≥ 7 means the same thing in India, Japan, and the US. In practice, accounting standards (IFRS vs. GAAP vs. Indian GAAP) mean that accrual quality (F4) and asset turnover (F9) are not perfectly comparable across markets.
- **Share issuance check (F7) uses total shares outstanding.** Stock splits can cause false negatives. The code uses adjusted share counts where available.

### Coffee Can

- **Revenue CAGR uses all available annual revenue figures from yfinance**, which is typically 3–5 years. For recently listed companies with fewer years, the threshold is applied to whatever data exists — this can produce misleading CAGRs for companies that listed after a restructuring or spin-off.
- **ROCE = EBIT / (Total Assets − Current Liabilities).** This is a proxy for invested capital. Companies with significant goodwill or intangibles (post-acquisition) may show artificially lower ROCE.
- **The FCF criterion uses the most recent reported year only.** A company with strong historical FCF that had a heavy capex year (capacity expansion) will fail this criterion even if the investment is value-creating.
- **Market cap floors are fixed in the local currency of the date the code was written.** Exchange rate movements will erode or inflate the effective USD-equivalent threshold over time.
- **Coffee Can was designed for Indian equities by Saurabh Mukherjea.** The 15% ROCE and 10% revenue growth thresholds reflect Indian market conditions and industry structures. Applying them uniformly to Japanese or Korean markets — where capital is cheaper and growth trajectories differ — will produce different hit rates. The Damodaran enrichment module adds sector-relative ROE context to help interpret results in each market.

### Universe coverage

- **India**: NSE bhavcopy excludes suspended, delisted, and SME-platform stocks. BSE security master similarly covers only active EQ-segment listings. The combined ~4,600 figure is not exhaustive.
- **Japan**: `kabupy.Jpx().issues` covers TSE Prime and Standard segments. TSE Growth (small/emerging companies) is excluded.
- **Korea**: pykrx `get_market_ohlcv_by_date()` is backed by Naver Finance and can time out or return incomplete data for illiquid KOSDAQ stocks. A 0.3-second delay between ticker calls is included to reduce load.
- **Europe**: The universe file (`EU_All_Listed_Companies_Full_Jun2026.xlsx`) was sourced in June 2026. Listings change — new IPOs and delistings will drift from this snapshot. The `--universe-file` flag allows you to supply a fresher file.
- **US**: yfinance universe fetch is best-effort. ETFs, closed-end funds, and ADRs are included in the raw list and may appear in breakout results if their price structure triggers the Darvas algorithm.

### Performance

- Full India scan (~4,600 stocks): ~25–40 minutes
- Full US scan (~5,400 stocks): ~60–90 minutes
- Full Europe scan (~1,700 stocks): ~20–35 minutes
- Full Japan scan (~3,600 stocks): ~30–50 minutes
- Full Korea scan (~2,600 stocks): ~30–45 minutes

Runtime depends heavily on yfinance API response time, which varies by time of day and total concurrent load. Running during US market hours (when Yahoo Finance is busiest) increases timeouts.

### This is a screening tool, not investment advice

The Triple Hit list is a starting point for further research — not a buy list. A stock can pass all three gates and still be:
- In a deteriorating business that has not yet shown up in annual financials
- Trading at a structurally elevated valuation that the sector PE benchmark does not fully explain
- Subject to regulatory, geopolitical, or currency risk not captured by any quantitative criterion

Always do your own due diligence.

---

## Results snapshot — 13 Jun 2026

| Market | Stocks scanned | Breakout buy | Breakdown sell | In-box | Triple hits |
|---|---|---|---|---|---|
| USA | 5,406 | 1,818 | 242 | 847 | 0 |
| Europe | 1,704 | — | — | — | **3** (Ferrari · ASML · Hermès) |
| India | 4,587 | 757 | 255 | 312 | **14** (Apollo · Kovai · Apcotex + 11 more) |
| Japan | 3,566 | 798 | 276 | — | **2** (東テク · Fast Retailing) |
| Korea | 2,606 | 640 | 20 | — | **2** (JW생명과학 · 아이비김영) |
| **Total** | **17,869** | **4,013+** | **793+** | — | **21** |

> Europe triple hits were recorded on the original 50-stock Euro Stoxx 50 run; the full 1,704-stock Europe scan results are pending.

See [CHANGELOG.md](CHANGELOG.md) for the full history of additions.

---

## Acknowledgements

- **Darvas Box** — Nicholas Darvas, *How I Made $2,000,000 in the Stock Market* (1960)
- **Piotroski F-Score** — Joseph Piotroski, *Value Investing: The Use of Historical Financial Statement Information to Separate Winners from Losers* (2000)
- **Coffee Can investing** — Robert Kirby (1984), popularised for India by Saurabh Mukherjea (*The Unusual Billionaires*, 2016)
- **Sector and country benchmarks** — Aswath Damodaran, NYU Stern School of Business

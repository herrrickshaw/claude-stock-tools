# Claude Stock Tools

Quantitative stock screening and analysis tools built with Claude. Covers five global markets with a consistent pipeline: Darvas Box breakout detection → Piotroski F-Score → Coffee Can portfolio screen.

---

## Repository structure

```
claude-stock-tools/
├── 01_daily_reports/        Per-stock daily reports (India, US, Singapore)
├── 02_market_screeners/     Full-universe screeners (India, US, Europe, Japan, Korea)
├── 03_colab_notebooks/      Google Colab notebooks — run without local Python
├── 04_strategy_reference/   Strategy docs, quick-reference cards
└── CHANGELOG.md             Chronological history of all additions
```

---

## 01 · Daily reports

Run on a single ticker. Outputs price snapshot, valuation multiples, Piotroski F-Score, Coffee Can screen, and Darvas Box signal.

| Script | Market |
|---|---|
| `india_daily_report.py` | NSE / BSE |
| `us_daily_report.py` | NYSE / NASDAQ |
| `singapore_daily_report.py` | SGX |

```bash
python 01_daily_reports/india_daily_report.py RELIANCE
python 01_daily_reports/us_daily_report.py AAPL
```

---

## 02 · Market screeners

Full-universe scanners. Batch-downloads OHLC for every listed stock, applies Darvas Box to all, then runs Piotroski + Coffee Can only on breakout candidates. Saves a styled 4-sheet Excel workbook.

| Script | Market | Universe | Data source |
|---|---|---|---|
| `india_market_scan.py` | NSE + BSE | ~4,600 | nsepython + bseindia + yfinance |
| `us_market_scan.py` | NYSE + NASDAQ | ~5,400 | yfinance |
| `europe_market_scan.py` | Euro Stoxx 50 | 50 | yfinance |
| `japan_market_scan.py` | TSE Prime + Standard | ~3,600 | kabupy + yfinance |
| `korea_market_scan.py` | KOSPI + KOSDAQ | ~2,600 | pykrx + yfinance |

```bash
python 02_market_screeners/india_market_scan.py
python 02_market_screeners/japan_market_scan.py --workers 10
python 02_market_screeners/korea_market_scan.py --kospi-only
```

---

## 03 · Colab notebooks

Open directly in Google Colab — no local install needed.

| Notebook | Market | Link |
|---|---|---|
| `india_stock_analysis.ipynb` | NSE + BSE | [![Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/herrrickshaw/claude-stock-tools/blob/main/03_colab_notebooks/india_stock_analysis.ipynb) |
| `india_stock_reporting.ipynb` | NSE + BSE | [![Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/herrrickshaw/claude-stock-tools/blob/main/03_colab_notebooks/india_stock_reporting.ipynb) |
| `us_market_screener.ipynb` | NYSE + NASDAQ | [![Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/herrrickshaw/claude-stock-tools/blob/main/03_colab_notebooks/us_market_screener.ipynb) |

---

## 04 · Strategy reference

- `momentum_strategy_reference.py` — Indian momentum strategy card: capital sizing, entry/exit rules, Darvas Box logic

---

## Triple Hit criteria

A stock passes the triple hit filter when all three align:

| Gate | Criteria |
|---|---|
| Darvas breakout | Price closes above the confirmed box top |
| Piotroski F-Score | ≥ 7 out of 9 |
| Coffee Can | Revenue CAGR >10% · avg ROCE >15% · D/E <1 · profitable every year · positive FCF |

---

## Install

```bash
pip install yfinance pandas openpyxl requests nsepython bseindia kabupy pykrx
```

---

## Results snapshot — 13 Jun 2026

| Market | Stocks scanned | Breakout buy | Breakdown sell | Triple hits |
|---|---|---|---|---|
| USA | 5,406 | 1,818 | 242 | 0 |
| Europe | 50 | 27 | 1 | **3** (Ferrari · ASML · Hermès) |
| India | 4,587 | 757 | 255 | **14** (Apollo · Kovai · Apcotex + 11 more) |
| Japan | 3,566 | 798 | 276 | **2** (東テク · Fast Retailing) |
| Korea | 2,606 | 640 | 20 | **2** (JW생명과학 · 아이비김영) |
| **Total** | **16,215** | **4,040** | **794** | **21** |

See [CHANGELOG.md](CHANGELOG.md) for the full history of additions.

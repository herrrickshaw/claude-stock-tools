# Changelog

All tools built with Claude, in reverse chronological order.

---

## 13 Jun 2026

### Added — Japan & Korea market scanners
- `02_market_screeners/japan_market_scan.py`
  - Full TSE Prime + Standard universe (~3,566 stocks) via **kabupy** `Jpx().issues`
  - yfinance bulk OHLC with `.T` suffix (batched, 200 per call)
  - Darvas Box + 200-day MA + Piotroski + Coffee Can (¥100B market cap floor)
  - **Triple hits found**: 東テク [9960] F=9/9, Fast Retailing [9983] F=7/9

- `02_market_screeners/korea_market_scan.py`
  - Full KOSPI + KOSDAQ universe (~2,606 stocks) via KRX KIND open endpoint
  - Per-ticker OHLC via **pykrx** `get_market_ohlcv_by_date` (Naver Finance backend)
  - yfinance `.KS` / `.KQ` for fundamentals only
  - **Triple hits found**: JW생명과학 [234080] F=7/9, 아이비김영 [339950] F=7/9

### Cross-market comparison (13 Jun 2026)
| Market | Scanned | Breakout | Breakdown | Triple hits |
|---|---|---|---|---|
| USA | 5,406 | 1,818 | 242 | 0 |
| Europe | 50 | 27 | 1 | 3 |
| India | 4,587 | 757 | 255 | 14 |
| Japan | 3,566 | 798 | 276 | 2 |
| Korea | 2,606 | 640 | 20 | 2 |

---

## 12 Jun 2026

### Added — Full universe scanners for India, US, Europe
- `02_market_screeners/india_market_scan.py`
  - Full NSE + BSE universe via nsepython bhavcopy + bseindia security master
  - **14 triple hits** including APCOTEXIND (F=9/9), KOVAI (F=9/9), Apollo Hospitals

- `02_market_screeners/us_market_scan.py`
  - Full NYSE + NASDAQ universe (~5,400 stocks), all breakouts scanned for fundamentals
  - 0 triple hits (high valuations prevent Coffee Can pass)

- `02_market_screeners/europe_market_scan.py`
  - Static Euro Stoxx 50 metadata (zero API calls for index list)
  - **3 triple hits**: Ferrari (F=9/9), ASML (F=8/9), Hermès (F=8/9)

### Added — Daily scan report notebook
- `03_colab_notebooks/` — daily_scan_report notebook for running scans in Colab

---

## 2 Jun 2026

### Added — Singapore daily report
- `01_daily_reports/singapore_daily_report.py`
  - SGX-listed equities via yfinance `.SI` suffix
  - Same report format as India/US: quote, Piotroski, Coffee Can, Darvas Box

---

## 30 May 2026

### Added — US Colab notebook and script
- `03_colab_notebooks/us_market_screener.ipynb` — full Colab notebook for US screening
- `03_colab_notebooks/us_stocks_colab_script.py` — combined per-stock report + batch screener

---

## 22 May 2026

### Added — US daily report and first US screener
- `01_daily_reports/us_daily_report.py` — per-stock daily report for NYSE/NASDAQ
- `02_market_screeners/us_market_screener_v1.py` — first version of the US universe screener

---

## 21 May 2026

### Added — India daily report (improved)
- `01_daily_reports/india_daily_report.py`
  - NSE/BSE per-stock report with Darvas Box, Piotroski F-Score, Coffee Can
  - yfinance-only (no nsepython OHLC dependency — avoids Mac cookie issue)

---

## 18 May 2026

### Added — India stock reporting notebook
- `03_colab_notebooks/india_stock_reporting.ipynb` — original Colab notebook for Indian stock daily reports

---

## 22 May 2026 (analysis)

### Added — India stock analysis Colab
- `03_colab_notebooks/india_stock_analysis.ipynb` — NSE + BSE analysis: Darvas, Piotroski, Coffee Can

---

## 1 May 2026

### Added — Momentum strategy reference
- `04_strategy_reference/momentum_strategy_reference.py`
  - Indian stock market momentum strategy quick-reference card
  - Capital deployment: ₹3,00,000/day across 500 stocks
  - Entry/exit rules, sizing, and Darvas Box logic summary

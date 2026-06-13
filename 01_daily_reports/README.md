# Daily Stock Reports

Per-stock daily report scripts. Run on any single ticker to get a full snapshot: price, valuation multiples, technicals, Piotroski F-Score, Coffee Can screen, Darvas Box, and optional scans.

## Scripts

| File | Market | Key data source |
|---|---|---|
| `india_daily_report.py` | NSE / BSE | yfinance (`.NS` / `.BO`) |
| `us_daily_report.py` | NYSE / NASDAQ / AMEX | yfinance |
| `singapore_daily_report.py` | SGX | yfinance (`.SI`) |

## Usage

```bash
# India
python india_daily_report.py RELIANCE
python india_daily_report.py TCS --scan darvas
python india_daily_report.py INFY --scan piotroski coffeecan

# US
python us_daily_report.py AAPL
python us_daily_report.py NVDA --scan all

# Singapore
python singapore_daily_report.py D05   # DBS Bank
```

## Install

```bash
pip install yfinance pandas openpyxl requests
```

## Output

Each script prints a formatted console report and optionally saves an Excel file with full data.

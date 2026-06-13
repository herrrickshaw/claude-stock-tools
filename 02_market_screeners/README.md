# Full Market Screeners

Full-universe screeners that scan every listed equity in a market through a 5-stage pipeline:

1. **Universe fetch** — pull all listed equity tickers for the market
2. **Bulk OHLC download** — 3-month price history for every ticker
3. **Darvas Box screen** — classify as `BREAKOUT_BUY`, `BREAKDOWN_SELL`, or `IN_BOX`
4. **Fundamental scan** — Piotroski F-Score + Coffee Can on breakout candidates only
5. **Excel export** — styled 4-sheet workbook

## Scripts

| File | Market | Universe | Primary data source | Added |
|---|---|---|---|---|
| `india_market_scan.py` | NSE + BSE | ~4,600 stocks | nsepython + bseindia + yfinance | 12 Jun 2026 |
| `us_market_scan.py` | NYSE + NASDAQ | ~5,400 stocks | yfinance | 12 Jun 2026 |
| `europe_market_scan.py` | Euro Stoxx 50 | 50 stocks | yfinance | 12 Jun 2026 |
| `japan_market_scan.py` | TSE Prime + Standard | ~3,600 stocks | kabupy (JPX) + yfinance | 13 Jun 2026 |
| `korea_market_scan.py` | KOSPI + KOSDAQ | ~2,600 stocks | pykrx (KRX/Naver) + yfinance | 13 Jun 2026 |
| `us_market_screener_v1.py` | NYSE + NASDAQ | ~6,500 stocks | yfinance | 22 May 2026 |

## Triple Hit criteria

A stock must clear all three gates simultaneously:
- **Darvas breakout** — price has closed above the confirmed box top
- **Piotroski F-Score ≥ 7/9** — strong financial health across 9 criteria
- **Coffee Can pass** — Revenue CAGR >10%, avg ROCE >15%, D/E <1, consistently profitable, positive FCF

## Install

```bash
pip install yfinance pandas openpyxl requests nsepython bseindia kabupy pykrx
```

## Usage

```bash
python india_market_scan.py                      # full NSE + BSE universe
python india_market_scan.py --nse-only           # NSE only
python india_market_scan.py --top 500            # first 500 tickers (test)
python india_market_scan.py --no-scans           # Darvas screen only

python us_market_scan.py
python us_market_scan.py --top 200 --no-scans

python europe_market_scan.py
python europe_market_scan.py --top 10

python japan_market_scan.py --workers 10
python japan_market_scan.py --top 200

python korea_market_scan.py                      # KOSPI + KOSDAQ
python korea_market_scan.py --kospi-only
python korea_market_scan.py --workers 8
```

## Sample results (Jun 2026)

| Market | Scanned | Breakout buy | Breakdown sell | Triple hits |
|---|---|---|---|---|
| USA | 5,406 | 1,818 | 242 | 0 |
| Europe | 50 | 27 | 1 | 3 |
| India | 4,587 | 757 | 255 | 14 |
| Japan | 3,566 | 798 | 276 | 2 |
| Korea | 2,606 | 640 | 20 | 2 |

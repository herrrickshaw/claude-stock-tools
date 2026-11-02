# Claude Stock Tools

Quantitative stock screeners, single-stock daily reports and Colab notebooks for six markets: India, USA, Europe,
Japan, South Korea and Singapore. Every tool runs the same three-stage idea (Darvas Box breakout, Piotroski F-Score,
Coffee Can compounder filter); stocks passing all three are called **Triple Hits**. Outputs are terminal reports and
styled Excel workbooks.

> Status: maintained, research-only. Educational use only; not investment advice. All data comes from unofficial
> free sources (mainly yfinance) and can be wrong or missing. New here? See
> [How to use this repo with AI](HOW_TO_USE_WITH_AI.md).

## What it does
- Single-ticker reports (price, valuation, Piotroski breakdown, Coffee Can checklist, Darvas status) for India, US and Singapore.
- Full-universe batch scanners for India (~4,600), US (~5,400), Europe (~1,700), Japan (~3,600) and Korea (~2,600) stocks.
- Writes a styled 4-sheet workbook per scan: All Signals, Breakouts, Fundamentals, Triple Hits.
- Buffett-style quality+value screener across markets (`buffett_value_screener.py`).
- Damodaran sector/country benchmark enrichment and a 1-5 star moat rating for picks.
- Colab notebooks for India and US analysis.

## Quick start
```bash
git clone https://github.com/herrrickshaw/claude-stock-tools.git
cd claude-stock-tools
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python 01_daily_reports/us_daily_report.py AAPL
```
Full-universe scans: `python 02_market_screeners/europe_market_scan.py --top 50` (a quick trial; a complete
scan takes 20-90 minutes depending on the market). More commands: [docs/SCREENER_USAGE.md](docs/SCREENER_USAGE.md).

## How it works
```
universe list -> bulk 1y OHLC (yfinance) -> Darvas Box -> Piotroski + Coffee Can (breakouts only) -> Excel
```

| Stage | Module | Output |
|---|---|---|
| Universe | `02_market_screeners/<market>_market_scan.py` | ticker list, cached 24 h in `<market>_scan/.symbols_cache.json` |
| Darvas Box | `compute_darvas_box()` in each scanner | `BREAKOUT_BUY` / `BREAKDOWN_SELL` / `IN_BOX` |
| Piotroski F-Score (0-9) | `scan_common.piotroski_f_score()` | score, strong if >= 7 |
| Coffee Can | `fundamental_scan()` in each scanner | pass/fail on revenue CAGR, ROCE, D/E, market cap, profits, FCF |
| Shared helpers | `02_market_screeners/scan_common.py` | statement readers, TTL cache, Excel styling |

Design decisions: the **current bar is always excluded from Darvas box formation** (otherwise a breakdown on the day
cannot be detected); box confirmation is 3 bars in every market; Coffee Can runs only on breakout candidates to keep
runtime manageable; market-cap floors are local-currency (India Rs 500 Cr, US $1B, Europe EUR 1B, Japan JPY 100B,
Korea KRW 100B, Singapore S$100M). Full rules: [docs/METHODOLOGY.md](docs/METHODOLOGY.md).

## Repository layout
| Path | Purpose |
|---|---|
| `01_daily_reports/` | single-ticker reports: India (NSE/BSE), US, Singapore (SGX) |
| `02_market_screeners/` | five full-universe scanners, `buffett_value_screener.py`, `scan_common.py` |
| `03_colab_notebooks/` | India analysis/reporting and US screener notebooks, Colab script |
| `04_strategy_reference/` | momentum strategy card, Buffett methodology |
| `05_enrichment/` | Damodaran benchmarks, moat analyzer |
| `docs/` | methodology, data sources, limitations, usage, results snapshot |
| `tests/` | offline pytest suite with golden files (no network) |

## Data sources
yfinance (prices and annual statements, all markets), NSE bhavcopy via `nsepython` and BSE master via `bseindia`
(India universe), `kabupy` / JPX master (Japan), KRX KIND + `pykrx` (Korea), an Excel European universe file
(Europe), Nasdaq Trader symbol files (US), Damodaran/NYU Stern datasets (benchmarks). Table with caveats:
[docs/DATA_SOURCES.md](docs/DATA_SOURCES.md).

## Configuration
No secrets are needed or committed. Useful flags: `--workers N`, `--top N`, `--no-scans` (Darvas only),
Europe `--min-cap`, `--exchange`, `--universe-file`, Korea `--kospi-only`. Optional US universe credentials are read
from `~/.nasdaq/ncds_auth.json` if present. Per-market extras: `nsepython`+`bseindia` (India), `kabupy` (Japan),
`pykrx` (Korea). Python 3.9+.

## Testing
```bash
pip install pytest ruff
pytest -q        # 42 offline tests, golden files in tests/golden
ruff check .
```

## Limitations and known issues
- yfinance is unofficial; financial statements are annual only, and Korean/KOSDAQ coverage is incomplete.
- No volume confirmation in the Darvas implementation; 1-year lookback.
- Piotroski and Coffee Can thresholds are uniform across markets (Coffee Can was designed for India).
- Universe lists drift (Europe file dated June 2026); US list may include ETFs and ADRs.
- The Europe full-universe results in the snapshot are pending; see [docs/LIMITATIONS.md](docs/LIMITATIONS.md) for all caveats.
- The scanners duplicate some logic (Darvas box, fundamentals) per market; consolidation is planned.

## Related repositories
None required. Change history: [CHANGELOG.md](CHANGELOG.md); 13 Jun 2026 results: [docs/RESULTS_SNAPSHOT_2026-06-13.md](docs/RESULTS_SNAPSHOT_2026-06-13.md).

## Licence
No licence file yet; all rights reserved.

## Acknowledgements
Darvas Box: Nicholas Darvas (1960). Piotroski F-Score: Joseph Piotroski (2000). Coffee Can: Robert Kirby (1984),
popularised for India by Saurabh Mukherjea. Sector and country benchmarks: Aswath Damodaran, NYU Stern.

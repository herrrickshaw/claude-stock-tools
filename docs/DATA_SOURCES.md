# Data sources

(Moved verbatim from the former README.)

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

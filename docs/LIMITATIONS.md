# Limitations and assumptions

(Moved verbatim from the former README.)

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

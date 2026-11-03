# full_korea_market_scan.py
# =========================
# KRX (KOSPI + KOSDAQ) universe scanner using pykrx + yfinance.
#
# Pipeline:
#   Stage 1 — Fetch full KOSPI + KOSDAQ universe via pykrx
#   Stage 2 — Bulk OHLC download via pykrx (native KRX data, no rate-limit issues)
#   Stage 3 — Darvas Box screen + 200-day MA trend on every stock
#   Stage 4 — Piotroski F-Score + Coffee Can on Darvas BREAKOUT candidates only
#             (yfinance .KS / .KQ used for fundamentals only — much smaller call volume)
#   Stage 5 — Save styled Excel workbook with ranked results
#
# Output sheets:
#   All_Stocks      — price summary for every stock scanned
#   Darvas_Signals  — breakout / breakdown alerts ranked by upside
#   Fundamentals    — Piotroski + Coffee Can results for breakout candidates
#   Triple_Hits     — BREAKOUT_BUY + Piotroski >= 7 + Coffee Can PASS
#
# Usage:
#   python full_korea_market_scan.py
#   python full_korea_market_scan.py --kospi-only
#   python full_korea_market_scan.py --top 300      # limit to first 300 tickers
#   python full_korea_market_scan.py --no-scans     # Darvas only
#   python full_korea_market_scan.py --workers 8
#
# Install:
#   pip install pykrx yfinance pandas openpyxl

import argparse
import io
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf
from scan_common import (  # sibling module; the script dir is on sys.path
    compact_darvas_box,
    OPENPYXL_OK, ASIA_PALETTE, first_df as _first_df, ma200_trend, piotroski_f_score, price_change,
    read_cache, run_fundamental_pool, series as _series, style_sheet as _shared_style_sheet, write_cache,
    write_scan_workbook,
)

try:
    from pykrx import stock as krx
except ImportError:
    sys.exit("❌  pip install pykrx")


# ── Configuration ─────────────────────────────────────────────────────────────
DOWNLOAD_DIR     = Path("./korea_scan")
DOWNLOAD_DIR.mkdir(exist_ok=True)

DARVAS_CONFIRM      = 3
SLEEP_BETWEEN_KRX   = 0.3   # pykrx per-ticker OHLC sleep (respectful rate)
MAX_WORKERS         = 8
PIOTROSKI_STRONG    = 7
MAX_FUND_CANDIDATES = 200
SYMBOL_CACHE_TTL    = 86400  # 24 h

_CACHE_FILE = DOWNLOAD_DIR / ".symbols_cache.json"

# ── Symbol universe ───────────────────────────────────────────────────────────

def _load_cache():
    data = read_cache(_CACHE_FILE, SYMBOL_CACHE_TTL)
    return data.get("universe", []) if data is not None else None


def _save_cache(universe):
    write_cache(_CACHE_FILE, universe=universe)


_KIND_URL     = "https://kind.krx.co.kr/corpgeneral/corpList.do"
_KIND_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://kind.krx.co.kr/"}

_NAVER_OHLC_URL = "https://fchart.stock.naver.com/sise.nhn"


def _fetch_kind_list(market_param: str, market_label: str, yf_suffix: str) -> list[dict]:
    """Fetch listed company codes from KRX KIND (open, no auth needed)."""
    params = {"method": "download", "searchType": "13", "marketType": market_param}
    try:
        r = requests.get(_KIND_URL, params=params, headers=_KIND_HEADERS, timeout=20)
        r.raise_for_status()
        dfs = pd.read_html(io.StringIO(r.text), encoding="euc-kr")
        df = dfs[0]
        # Columns: 회사명 (name), 종목코드 (code), 업종 (sector)
        result = []
        for _, row in df.iterrows():
            code = str(row.get("종목코드", "")).strip().zfill(6)
            if not code.isdigit() or len(code) != 6:
                continue
            result.append({
                "code":      code,
                "name":      str(row.get("회사명", "")).strip(),
                "sector":    str(row.get("업종", "—")).strip(),
                "market":    market_label,
                "yf_suffix": yf_suffix,
            })
        return result
    except Exception as e:
        print(f"  ⚠️  KIND fetch error ({market_label}): {e}")
        return []


def build_krx_universe(kospi_only: bool = False) -> list[dict]:
    """Return KOSPI (+ optionally KOSDAQ) ticker list, cached for 24 h."""
    cached = _load_cache()
    if cached:
        if kospi_only:
            cached = [s for s in cached if s["market"] == "KOSPI"]
        print(f"  Symbol cache hit: {len(cached)} KRX tickers")
        return cached

    print("  Fetching KOSPI tickers from KRX KIND …")
    universe = _fetch_kind_list("stockMkt", "KOSPI", ".KS")
    print(f"    → {len(universe)} KOSPI tickers")

    if not kospi_only:
        print("  Fetching KOSDAQ tickers from KRX KIND …")
        kosdaq = _fetch_kind_list("kosdaqMkt", "KOSDAQ", ".KQ")
        print(f"    → {len(kosdaq)} KOSDAQ tickers")
        universe.extend(kosdaq)

    if not universe:
        sys.exit("❌  Could not fetch any KRX tickers. Check network.")

    _save_cache(universe)
    return universe


# ── OHLC via pykrx ───────────────────────────────────────────────────────────

_KOR_COL_MAP = {
    "고가": "High", "저가": "Low", "종가": "Close",
    "시가": "Open", "거래량": "Volume",
}


def _normalise_krx_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Rename Korean column names returned by pykrx to English."""
    return df.rename(columns={k: v for k, v in _KOR_COL_MAP.items() if k in df.columns})


def fetch_krx_ohlc(universe: list[dict], months: int = 3) -> dict[str, pd.DataFrame]:
    """
    Fetch per-ticker OHLC via pykrx.get_market_ohlcv_by_date (uses Naver Finance
    under the hood — fast and reliable). Returns dict: code → DataFrame.
    """
    end_date   = datetime.today()
    start_date = end_date - timedelta(days=months * 31)
    from_str   = start_date.strftime("%Y%m%d")
    to_str     = end_date.strftime("%Y%m%d")

    result: dict[str, pd.DataFrame] = {}
    total = len(universe)
    print(f"  Fetching KRX OHLC ({from_str} → {to_str}) for {total} tickers …")

    for idx, stock_info in enumerate(universe, 1):
        code = stock_info["code"]
        try:
            df = krx.get_market_ohlcv_by_date(from_str, to_str, code)
            if df is None or df.empty:
                continue
            df = _normalise_krx_cols(df)
            if all(c in df.columns for c in ("High", "Low", "Close")):
                if len(df) >= DARVAS_CONFIRM + 5:
                    result[code] = df
        except Exception:
            pass

        if idx % 200 == 0:
            print(f"    {idx}/{total} fetched ({len(result)} usable) …")
        time.sleep(SLEEP_BETWEEN_KRX)

    print(f"  → {len(result)} tickers with usable OHLC data")
    return result


# ── Darvas Box ────────────────────────────────────────────────────────────────

def compute_darvas_box(df, confirm=DARVAS_CONFIRM):
    """Darvas Box on a High/Low/Close frame (whole-won prices); the current bar never forms the box."""
    return compact_darvas_box(df, confirm, ndigits=0)


# ── Fundamental helpers ───────────────────────────────────────────────────────


# ── Fundamental scan (yfinance, .KS / .KQ) ───────────────────────────────────

def fundamental_scan(code: str, yf_suffix: str) -> dict:
    """Piotroski F-Score + Korea-adapted Coffee Can via yfinance."""
    yf_ticker = f"{code}{yf_suffix}"
    result = {"code": code, "yf_ticker": yf_ticker, "f_score": None,
              "cc_qualifies": "FAIL", "error": None}
    try:
        ticker = yf.Ticker(yf_ticker)
        inc = _first_df(ticker, "income_stmt", "financials")
        bal = _first_df(ticker, "balance_sheet")
        cf  = _first_df(ticker, "cash_flow", "cashflow")
        if inc is None:
            result["error"] = "no_financial_data"
            return result
    except Exception as e:
        result["error"] = str(e)[:80]
        return result

    # ── Piotroski F-Score ─────────────────────────────────────────────────────
    try:
        f_score = piotroski_f_score(inc, bal, cf)
        result["f_score"]  = f_score
        result["f_strong"] = f_score >= PIOTROSKI_STRONG
    except Exception:
        return result

    if not result.get("f_strong"):
        result["cc_score"] = "—"
        return result

    # ── Coffee Can (Korea-adapted) ────────────────────────────────────────────
    # C1: Revenue CAGR > 10%
    # C2: Avg ROCE > 15%
    # C3: D/E < 1
    # C4: Market cap >= ₩100B (mid-cap floor, ~$75M USD)
    # C5: Consistently positive net income across all reported years
    # C6: Positive FCF in most recent year
    try:
        c = {}
        revs = _series(inc, "Total Revenue")
        if len(revs) >= 2 and revs[-1] > 0:
            cagr = ((revs[0] / revs[-1]) ** (1 / (len(revs) - 1)) - 1) * 100
            c["C1"] = 1 if cagr > 10 else 0
        else:
            cagr = None; c["C1"] = 0

        ebit_s = _series(inc, "EBIT", "Operating Income", "Ebit")
        ta_s   = _series(bal, "Total Assets")
        cl_s   = _series(bal, "Current Liabilities", "Total Current Liabilities")
        roce_l = [ebit_s[i] / (ta_s[i] - cl_s[i]) * 100
                  for i in range(min(len(ebit_s), len(ta_s), len(cl_s)))
                  if (ta_s[i] - cl_s[i]) > 0]
        avg_roce = sum(roce_l) / len(roce_l) if roce_l else None
        c["C2"] = 1 if (avg_roce and avg_roce > 15) else 0

        ltd_s = _series(bal, "Long Term Debt")
        eq_s  = _series(bal, "Stockholders Equity", "Total Stockholder Equity",
                        "Total Equity Gross Minority Interest")
        de = (ltd_s[0] / abs(eq_s[0])) if (ltd_s and eq_s and eq_s[0] != 0) else None
        c["C3"] = 1 if (de is not None and de < 1) else 0

        try:
            mcap = ticker.fast_info.market_cap or 0
        except Exception:
            mcap = 0
        c["C4"] = 1 if mcap >= 1e11 else 0   # ≥ ₩100B

        ni_s = _series(inc, "Net Income")
        c["C5"] = 1 if (ni_s and all(n > 0 for n in ni_s)) else 0

        fcf_s = _series(cf, "Free Cash Flow")
        if fcf_s:
            c["C6"] = 1 if fcf_s[0] > 0 else 0
        else:
            ocf_s   = _series(cf, "Operating Cash Flow")
            capex_s = _series(cf, "Capital Expenditure")
            c["C6"] = 1 if (ocf_s and capex_s and (ocf_s[0] - abs(capex_s[0])) > 0) else 0

        qualifies = sum(c.values()) == len(c)
        result.update({
            "cc_qualifies":   "PASS" if qualifies else "FAIL",
            "cc_score":       f"{sum(c.values())}/{len(c)}",
            "cc_rev_cagr":    round(cagr, 2) if cagr else None,
            "cc_roce_avg":    round(avg_roce, 2) if avg_roce else None,
            "cc_debt_equity": round(de, 2) if de is not None else None,
            "market_cap_b_krw": round(mcap / 1e8, 1) if mcap else None,  # show in ₩100M units
        })
    except Exception:
        pass

    return result


# ── Excel styling ─────────────────────────────────────────────────────────────

def style_sheet(ws):
    _shared_style_sheet(ws, ASIA_PALETTE)


# ── Main ──────────────────────────────────────────────────────────────────────

def _build_tech_row(stock_info: dict, df: pd.DataFrame) -> dict:
    """Stage-3 row for one stock: price change (whole won), 200-day MA trend and the Darvas Box fields."""
    darv = compute_darvas_box(df)
    closes = pd.to_numeric(df["Close"], errors="coerce").dropna()
    ltp, _prev, chg_pct = price_change(closes, 0)
    # 200-day MA from the 3-month window (may be None; acceptable for the Darvas screen)
    ma200, dist_ma, trend = ma200_trend(closes, ltp, 0, "Need 200+ days")
    code = stock_info["code"]
    return {
        "Code":               code,
        "Name":               stock_info.get("name", ""),
        "Market":             stock_info.get("market", ""),
        "YF_Ticker":          f"{code}{stock_info.get('yf_suffix', '.KS')}",
        "LTP_KRW":            ltp,
        "Change%":            chg_pct,
        "200_Day_MA":         ma200,
        "Distance_to_200MA%": dist_ma,
        "Trend_Signal":       trend,
        "Darvas_Signal":      darv.get("signal"),
        "Box_Top":            darv.get("box_top"),
        "Box_Bottom":         darv.get("box_bottom"),
        "Upside_to_Top%":     darv.get("upside_pct"),
        "Position_in_Box%":   darv.get("pos_in_box"),
        "Data_Points":        darv.get("data_points"),
    }


def _fund_row(code: str, sfx: str, tech: dict, res: dict) -> dict:
    """Stage-4 row: the technical context of a breakout joined with its fundamental scan result."""
    return {
        "Code":            code,
        "Name":            tech.get("Name"),
        "Market":          tech.get("Market"),
        "YF_Ticker":       f"{code}{sfx}",
        "LTP_KRW":         tech.get("LTP_KRW"),
        "Change%":         tech.get("Change%"),
        "Darvas_Signal":   tech.get("Darvas_Signal"),
        "Upside_to_Top%":  tech.get("Upside_to_Top%"),
        "200_Day_MA":      tech.get("200_Day_MA"),
        "Piotroski_Score": res.get("f_score"),
        "Piotroski_Strong": ("YES" if res.get("f_strong") else "NO"),
        "CoffeeCan":       res.get("cc_qualifies", "FAIL"),
        "CC_Score":        res.get("cc_score", "—"),
        "Rev_CAGR%":       res.get("cc_rev_cagr"),
        "ROCE_Avg%":       res.get("cc_roce_avg"),
        "Debt_Equity":     res.get("cc_debt_equity"),
        "MCap_100M_KRW":   res.get("market_cap_b_krw"),
        "Error":           res.get("error", ""),
    }


def _screen_darvas(universe: list, ohlc: dict) -> tuple:
    """Stage 3: (all_rows, darvas_rows, breakout_info) where breakout_info holds (code, yf_suffix) pairs."""
    all_rows, breakout_info = [], []
    for stock_info in universe:
        df = ohlc.get(stock_info["code"])
        if df is None:
            continue
        row = _build_tech_row(stock_info, df)
        all_rows.append(row)
        if row["Darvas_Signal"] == "BREAKOUT_BUY":
            breakout_info.append((stock_info["code"], stock_info.get("yf_suffix", ".KS")))
    darvas_rows = [r.copy() for r in all_rows if r["Darvas_Signal"] in ("BREAKOUT_BUY", "BREAKDOWN_SELL")]
    return all_rows, darvas_rows, breakout_info


def _scan_fundamentals(breakout_info: list, darvas_rows: list, workers: int) -> tuple:
    """Stage 4: (candidates, fund_rows, triple_rows) for the freshest breakouts (capped at MAX_FUND_CANDIDATES)."""
    def _upside(code_sfx):
        row = next((r for r in darvas_rows if r["Code"] == code_sfx[0]), None)
        return abs(row.get("Upside_to_Top%") or 999)

    if len(breakout_info) > MAX_FUND_CANDIDATES:
        breakout_info = sorted(breakout_info, key=_upside)[:MAX_FUND_CANDIDATES]
        print(f"  (capped to {MAX_FUND_CANDIDATES} freshest breakouts)")

    print(f"\nStage 4 — Fundamental scans ({len(breakout_info)} candidates, "
          f"{workers} workers) …")
    fund_rows, triple_rows = run_fundamental_pool(
        breakout_info, lambda cand: fundamental_scan(*cand), workers,
        build_row=lambda cand, res: _fund_row(
            cand[0], cand[1], next((r for r in darvas_rows if r["Code"] == cand[0]), {}), res),
        is_triple=lambda res: res.get("f_strong") and res.get("cc_qualifies") == "PASS",
        name_of=lambda cand: cand[0],
        progress_fmt="    {done}/{total} done  (triple hits: {triples})", every=20)
    return breakout_info, fund_rows, triple_rows


def _save_workbook(all_rows, darvas_rows, fund_rows, triple_rows) -> Path:
    """Stage 5: write the four result sheets to ``DOWNLOAD_DIR`` and return the file path."""
    date_str = datetime.today().strftime("%Y%m%d_%H%M")
    path = DOWNLOAD_DIR / f"korea_market_scan_{date_str}.xlsx"
    write_scan_workbook(path, [
        ("All_Stocks", all_rows, "Change%"),
        ("Darvas_Signals", darvas_rows, "Upside_to_Top%"),
        ("Fundamentals", fund_rows, "Piotroski_Score"),
        ("Triple_Hits", triple_rows, "Piotroski_Score"),
    ], style=style_sheet if OPENPYXL_OK else None)
    return path


def _print_summary(all_rows, breakout_info, fund_rows, triple_rows, path) -> None:
    """Print the closing banner, the triple-hit list and where the workbook went."""
    print(f"\n{'='*60}")
    print(f"  SCAN COMPLETE — {datetime.now().strftime('%d %b %Y  %H:%M:%S')}")
    print(f"  KRX stocks scanned:    {len(all_rows)}")
    print(f"  Darvas Breakouts:      {len(breakout_info)}")
    print(f"  Fundamentals scanned:  {len(fund_rows)}")
    print(f"  ★ TRIPLE HITS:         {len(triple_rows)}")
    if triple_rows:
        print("\n  Triple-hit stocks:")
        for r in sorted(triple_rows, key=lambda x: x.get("Piotroski_Score") or 0, reverse=True):
            print(f"    [{r['Code']}] {r['Name']:<30}  "
                  f"F={r['Piotroski_Score']}/9  CC={r['CC_Score']}  "
                  f"Market: {r['Market']}  ₩{r['LTP_KRW']:,.0f}")
    else:
        print("\n  No Triple Hit stocks found today.")
    print(f"\n  📊 Excel saved → {path}\n")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Full KRX scanner — Darvas + Piotroski + Coffee Can")
    parser.add_argument("--kospi-only", action="store_true", help="Scan KOSPI only (skip KOSDAQ)")
    parser.add_argument("--top",        type=int, default=0, help="Limit to first N tickers")
    parser.add_argument("--no-scans",   action="store_true", help="Skip fundamental scans")
    parser.add_argument("--workers",    type=int, default=MAX_WORKERS, help="Parallel threads")
    args = parser.parse_args()

    print(f"\n{'#'*60}")
    print(f"  FULL KOREA (KRX) MARKET SCAN")
    print(f"  Started: {datetime.now().strftime('%d %b %Y  %H:%M:%S')}")
    print(f"{'#'*60}\n")

    # Stage 1 — Universe
    print("Stage 1 — Building KRX equity universe …")
    universe = build_krx_universe(kospi_only=args.kospi_only)

    if args.top:
        universe = universe[:args.top]
        print(f"  (limited to first {args.top} tickers)")

    print(f"  Total: {len(universe)} tickers "
          f"({sum(1 for s in universe if s['market']=='KOSPI')} KOSPI + "
          f"{sum(1 for s in universe if s['market']=='KOSDAQ')} KOSDAQ)")

    # Stage 2 — OHLC via pykrx
    print(f"\nStage 2 — Fetching OHLC from KRX for {len(universe)} tickers …")
    ohlc = fetch_krx_ohlc(universe, months=3)

    # Stage 3 — Darvas Box + 200-day MA
    print("\nStage 3 — Darvas Box screen …")
    all_rows, darvas_rows, breakout_info = _screen_darvas(universe, ohlc)
    print(f"  Breakout BUY:   {len(breakout_info)}")
    print(f"  Breakdown SELL: {sum(1 for r in darvas_rows if r['Darvas_Signal'] == 'BREAKDOWN_SELL')}")
    print(f"  In Box:         {len(all_rows) - len(darvas_rows)}")

    # Stage 4 — Fundamentals on breakout candidates
    if not args.no_scans and breakout_info:
        breakout_info, fund_rows, triple_rows = _scan_fundamentals(breakout_info, darvas_rows, args.workers)
    else:
        fund_rows, triple_rows = [], []
        print("\nStage 4 — Skipped")

    # Stage 5 — Excel
    print("\nStage 5 — Saving Excel workbook …")
    path = _save_workbook(all_rows, darvas_rows, fund_rows, triple_rows)
    _print_summary(all_rows, breakout_info, fund_rows, triple_rows, path)


if __name__ == "__main__":
    main()

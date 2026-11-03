"""Helpers shared by the five ``*_market_scan.py`` scripts (pure functions plus small I/O edges).

The scanners are run as scripts (``python 02_market_screeners/<x>_market_scan.py``), so this module is
imported as a sibling (``from scan_common import ...``); Python puts the script directory on ``sys.path``.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional, Sequence

import pandas as pd

log = logging.getLogger(__name__)

try:
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    OPENPYXL_OK = True
except ImportError:  # pragma: no cover - openpyxl is optional for the scan itself
    OPENPYXL_OK = False


# ── yfinance statement helpers (pure; take a Ticker-like object or DataFrame) ─────────────────

def first_df(ticker: Any, *attrs: str) -> Optional[pd.DataFrame]:
    """Return the first non-empty DataFrame attribute of ``ticker`` named in ``attrs``, else None."""
    for attr in attrs:
        df = getattr(ticker, attr, None)
        if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
            return df
    return None


def row(df: Optional[pd.DataFrame], *names: str, col: int = 0) -> Optional[float]:
    """Value of the first statement line in ``names`` at column ``col`` (0 = latest year), or None."""
    if df is None or df.empty:
        return None
    for name in names:
        if name in df.index:
            try:
                val = df.loc[name].iloc[col]
                return float(val) if pd.notna(val) else None
            except (IndexError, KeyError, TypeError, ValueError):
                log.debug("statement line %r unreadable at column %d", name, col)
    return None


def series(df: Optional[pd.DataFrame], *names: str) -> list:
    """All non-NaN values (latest year first) of the first statement line in ``names``; [] if absent."""
    if df is None or df.empty:
        return []
    for name in names:
        if name in df.index:
            return [float(v) for v in df.loc[name].dropna() if pd.notna(v)]
    return []


def piotroski_f_score(inc: pd.DataFrame, bal: Optional[pd.DataFrame], cf: Optional[pd.DataFrame]) -> int:
    """Piotroski F-Score (0-9) from yfinance income / balance-sheet / cash-flow frames (latest year first)."""
    ni0 = row(inc, "Net Income", col=0);  a0 = row(bal, "Total Assets", col=0)
    ni1 = row(inc, "Net Income", col=1);  a1 = row(bal, "Total Assets", col=1)
    roa0 = (ni0 / a0) if (ni0 and a0) else None
    roa1 = (ni1 / a1) if (ni1 and a1) else None
    ocf0 = row(cf, "Operating Cash Flow", "Total Cash From Operating Activities")

    f1 = 1 if (roa0 and roa0 > 0) else 0
    f2 = 1 if (ocf0 and ocf0 > 0) else 0
    f3 = 1 if (roa0 and roa1 and roa0 > roa1) else 0
    f4 = 1 if (ocf0 and a0 and roa0 and (ocf0 / a0) > roa0) else 0

    ltd0 = row(bal, "Long Term Debt", col=0) or 0
    ltd1 = row(bal, "Long Term Debt", col=1) or 0
    f5 = 1 if (a0 and a1 and (ltd0 / a0) < (ltd1 / a1)) else 0

    ca0 = row(bal, "Current Assets", "Total Current Assets", col=0)
    cl0 = row(bal, "Current Liabilities", "Total Current Liabilities", col=0)
    ca1 = row(bal, "Current Assets", "Total Current Assets", col=1)
    cl1 = row(bal, "Current Liabilities", "Total Current Liabilities", col=1)
    f6 = 1 if (ca0 and cl0 and ca1 and cl1 and (ca0 / cl0) > (ca1 / cl1)) else 0

    sh0 = row(bal, "Share Issued", col=0)
    sh1 = row(bal, "Share Issued", col=1)
    f7 = (1 if sh0 <= sh1 else 0) if (sh0 and sh1) else 1

    rev0 = row(inc, "Total Revenue", col=0);  gp0 = row(inc, "Gross Profit", col=0)
    rev1 = row(inc, "Total Revenue", col=1);  gp1 = row(inc, "Gross Profit", col=1)
    f8 = 1 if (gp0 and rev0 and gp1 and rev1 and (gp0 / rev0) > (gp1 / rev1)) else 0
    f9 = 1 if (rev0 and a0 and rev1 and a1 and (rev0 / a0) > (rev1 / a1)) else 0

    return f1 + f2 + f3 + f4 + f5 + f6 + f7 + f8 + f9


# ── JSON universe / symbol cache with a time-to-live ──────────────────────────────────────────

def read_cache(path: Path, ttl: float) -> Optional[dict]:
    """Parsed cache payload if ``path`` exists and is younger than ``ttl`` seconds, else None."""
    try:
        if path.exists():
            data = json.loads(path.read_text())
            if time.time() - data.get("ts", 0) < ttl:
                return data
    except (OSError, ValueError, AttributeError):
        log.debug("cache %s unreadable; ignoring", path)
    return None


def write_cache(path: Path, **payload: Any) -> None:
    """Write ``payload`` plus a ``ts`` timestamp to ``path``; failures are logged, never raised."""
    try:
        path.write_text(json.dumps({"ts": time.time(), **payload}))
    except (OSError, TypeError, ValueError):
        log.debug("cache %s not written", path)


# ── Excel output ──────────────────────────────────────────────────────────────────────────────

class SheetPalette(NamedTuple):
    """Colours and number-format rules for :func:`style_sheet` (hex RGB without ``#``)."""

    header_fill: str
    alt_fill: str
    border: str
    price_keys: tuple
    price_format: str


EUROPE_PALETTE = SheetPalette("1A365D", "F7FAFC", "E2E8F0", ("CAP", "LTP", "BOX", "PRICE", "200", "MCAP"), "#,##0.00")
ASIA_PALETTE = SheetPalette("1A3A5C", "F0F4F8", "CBD5E0", ("CAP", "LTP", "BOX", "PRICE", "200"), "#,##0")


def style_sheet(ws: Any, palette: SheetPalette) -> None:
    """Apply header/banding/number formats to an openpyxl worksheet in place (no-op without openpyxl)."""
    if not OPENPYXL_OK:
        return
    fill_hdr = PatternFill(start_color=palette.header_fill, end_color=palette.header_fill, fill_type="solid")
    fill_alt = PatternFill(start_color=palette.alt_fill, end_color=palette.alt_fill, fill_type="solid")
    font_hdr = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    font_body = Font(name="Calibri", size=11, bold=False, color="000000")
    thin = Border(
        left=Side(style="thin", color=palette.border), right=Side(style="thin", color=palette.border),
        top=Side(style="thin", color=palette.border), bottom=Side(style="thin", color=palette.border),
    )
    for col_idx in range(1, ws.max_column + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = font_hdr; cell.fill = fill_hdr; cell.border = thin
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for row_idx in range(2, ws.max_row + 1):
        for col_idx in range(1, ws.max_column + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.font = font_body; cell.border = thin
            if row_idx % 2 == 1:
                cell.fill = fill_alt
            hdr = str(ws.cell(row=1, column=col_idx).value or "").upper()
            val = cell.value
            if isinstance(val, (int, float)):
                cell.alignment = Alignment(horizontal="right", vertical="center")
                if any(k in hdr for k in ["%", "CAGR", "ROCE", "YIELD"]):
                    cell.number_format = '0.00"%"'
                elif any(k in hdr for k in palette.price_keys):
                    cell.number_format = palette.price_format
                else:
                    cell.number_format = '0.00'
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")

    ws.row_dimensions[1].height = 28
    for r in range(2, ws.max_row + 1):
        ws.row_dimensions[r].height = 20
    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        ws.column_dimensions[get_column_letter(col[0].column)].width = max(max_len + 4, 12)


# ── Darvas Box: one implementation of the box maths for every scanner and report ──────────────

BOX_OK, BOX_NO_TOP, BOX_NO_BOTTOM = "ok", "no_top", "no_bottom"


class DarvasBox(NamedTuple):
    """Raw result of :func:`darvas_box`. Unrounded; each caller shapes it into its own output dict."""

    status: str  # BOX_OK | BOX_NO_TOP | BOX_NO_BOTTOM
    top: Optional[float]
    bottom: Optional[float]
    current: float
    signal: Optional[str] = None  # BREAKOUT_BUY | BREAKDOWN_SELL | IN_BOX (status ok only)
    box_range: Optional[float] = None
    pos_in_box_pct: Optional[float] = None  # unrounded, percent of the box range
    upside_to_top_pct: Optional[float] = None  # unrounded, percent of the current close


def find_column(df: pd.DataFrame, candidates: Sequence[str]) -> Optional[str]:
    """First column whose name contains any of ``candidates`` (case-insensitive, candidates in priority order)."""
    for cand in candidates:
        match = next((col for col in df.columns if cand.upper() in col.upper()), None)
        if match:
            return match
    return None


def ohlc_lists(df: pd.DataFrame, h_col: str, l_col: str, c_col: str) -> tuple:
    """(highs, lows, closes) as float lists with non-numeric values coerced to 0."""
    return tuple(pd.to_numeric(df[c], errors="coerce").fillna(0).tolist() for c in (h_col, l_col, c_col))


def darvas_box(highs: list, lows: list, closes: list, confirm: int) -> DarvasBox:
    """Find the Darvas box and classify the last close against it.

    The current (last) bar is excluded from box formation, so a spike or breakdown on it can never
    become the box boundary. Inputs are equal-length lists, oldest first.
    """
    current = closes[-1]
    hist_highs, hist_lows = highs[:-1], lows[:-1]

    top_idx = top = None
    for i in range(len(hist_highs) - confirm - 1, -1, -1):
        cand = hist_highs[i]
        if cand == 0:
            continue
        window = hist_highs[i + 1: i + 1 + confirm]
        if len(window) == confirm and all(h < cand for h in window):
            top_idx, top = i, cand
            break
    if top is None:
        return DarvasBox(BOX_NO_TOP, None, None, current)

    segment = hist_lows[top_idx:]
    bottom = None
    for i in range(len(segment) - confirm):
        cand = segment[i]
        if cand == 0:
            continue
        window = segment[i + 1: i + 1 + confirm]
        if len(window) == confirm and all(low > cand for low in window):
            bottom = cand
            break
    if bottom is None:
        valid = [low for low in segment if low > 0]
        bottom = min(valid) if valid else None
    if bottom is None:
        return DarvasBox(BOX_NO_BOTTOM, top, None, current)

    signal = "BREAKOUT_BUY" if current > top else "BREAKDOWN_SELL" if current < bottom else "IN_BOX"
    box_range = top - bottom
    pos = ((current - bottom) / box_range * 100) if box_range else 0
    upside = ((top - current) / current * 100) if current else 0
    return DarvasBox(BOX_OK, top, bottom, current, signal, box_range, pos, upside)


def compact_darvas_box(df: Optional[pd.DataFrame], confirm: int, ndigits: int) -> dict:
    """Darvas result for the Europe/Japan/Korea scanners (needs ``High``/``Low``/``Close`` columns).

    Prices are rounded to ``ndigits``; the keys are ``upside_pct``/``pos_in_box`` (the compact layout).
    """
    if df is None or df.empty or len(df) < confirm + 5:
        return {"signal": "INSUFFICIENT_DATA", "box_top": None, "box_bottom": None}
    highs, lows, closes = ohlc_lists(df, "High", "Low", "Close")
    box = darvas_box(highs, lows, closes, confirm)
    if box.status == BOX_NO_TOP:
        return {"signal": "NO_BOX", "box_top": None, "box_bottom": None, "current_price": box.current}
    if box.status == BOX_NO_BOTTOM:
        return {"signal": "NO_BOX", "box_top": round(box.top, ndigits), "box_bottom": None,
                "current_price": round(box.current, ndigits)}
    return {
        "signal": box.signal,
        "box_top": round(box.top, ndigits),
        "box_bottom": round(box.bottom, ndigits),
        "current_price": round(box.current, ndigits),
        "upside_pct": round(box.upside_to_top_pct, 2),
        "pos_in_box": round(box.pos_in_box_pct, 1),
        "data_points": len(closes),
    }


def full_box_result(box: DarvasBox, data_points: int, confirm: Optional[int] = None) -> dict:
    """Output dict (``upside_to_top_pct``/``position_in_box_pct`` layout, 2-dp prices) for a classified box.

    ``confirm`` adds the ``confirm_days`` key (used by the daily reports) just before ``data_points``.
    """
    result = {
        "signal": box.signal,
        "box_top": round(box.top, 2),
        "box_bottom": round(box.bottom, 2),
        "current_price": round(box.current, 2),
        "box_range": round(box.box_range, 2),
        "position_in_box_pct": round(box.pos_in_box_pct, 1),
        "upside_to_top_pct": round(box.upside_to_top_pct, 2),
    }
    if confirm is not None:
        result["confirm_days"] = confirm
    result["data_points"] = data_points
    return result


def report_darvas_box(highs: list, lows: list, closes: list, confirm: int,
                      no_top_note: str, no_bottom_note: str) -> dict:
    """Darvas result for the daily reports: ``note`` explains a NO_BOX, unrounded ``box_top`` when no bottom."""
    box = darvas_box(highs, lows, closes, confirm)
    if box.status == BOX_NO_TOP:
        return {"signal": "NO_BOX", "box_top": None, "box_bottom": None, "note": no_top_note}
    if box.status == BOX_NO_BOTTOM:
        return {"signal": "NO_BOX", "box_top": box.top, "box_bottom": None, "note": no_bottom_note}
    return full_box_result(box, len(closes), confirm)


# ── Per-row technicals shared by the scanners ─────────────────────────────────────────────────

def price_change(closes: pd.Series, ndigits: int = 2) -> tuple:
    """(last close, previous close, % change) rounded to ``ndigits``; each is None when unavailable."""
    ltp = round(float(closes.iloc[-1]), ndigits) if not closes.empty else None
    prev = round(float(closes.iloc[-2]), ndigits) if len(closes) >= 2 else None
    chg_pct = round((ltp - prev) / prev * 100, 2) if (ltp and prev) else None
    return ltp, prev, chg_pct


def ma200_trend(closes: pd.Series, ltp: Optional[float], ndigits: int, no_history_label: str) -> tuple:
    """(200-day MA, % distance of ``ltp`` from it, trend label); label is ``no_history_label`` when unknown."""
    ma200 = round(closes.rolling(200).mean().iloc[-1], ndigits) if len(closes) >= 200 else None
    dist = round((ltp - ma200) / ma200 * 100, 2) if (ma200 and ltp) else None
    trend = ("Above 200MA (Uptrend)" if dist and dist > 5 else
             "Below 200MA (Downtrend)" if dist and dist < -5 else
             "Near 200MA (Consolidation)" if dist else no_history_label)
    return ma200, dist, trend


# ── Stage 4: fundamental scans on breakout candidates, in a thread pool ───────────────────────

def run_fundamental_pool(candidates: list, scan: Callable, workers: int, build_row: Callable,
                         is_triple: Callable, name_of: Callable, progress_fmt: str,
                         every: int) -> tuple:
    """Scan every candidate concurrently; returns ``(fund_rows, triple_rows)`` in completion order.

    ``scan(candidate)`` does the (I/O-bound) fundamental scan, ``build_row(candidate, result)`` shapes the
    output row and ``is_triple(result)`` marks Darvas + Piotroski + Coffee Can hits. A failure on one
    candidate is printed and skipped. ``progress_fmt`` takes ``done``, ``total`` and ``triples``.
    """
    fund_rows, triple_rows = [], []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(scan, cand): cand for cand in candidates}
        for future in as_completed(futures):
            cand = futures[future]
            done += 1
            try:
                res = future.result()
                fund_row = build_row(cand, res)
                fund_rows.append(fund_row)
                if is_triple(res):
                    triple_rows.append(fund_row.copy())
                if done % every == 0 or done == len(candidates):
                    print(progress_fmt.format(done=done, total=len(candidates), triples=len(triple_rows)))
            except Exception as e:  # one bad ticker must not abort the scan
                print(f"    {name_of(cand)}: error — {e}")
    return fund_rows, triple_rows


def write_scan_workbook(path: Path, sheets: Sequence[tuple], style: Optional[Callable] = None) -> None:
    """Write ``(sheet_name, rows, sort_col)`` triples to an xlsx at ``path``; sort descending when possible."""
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, rows, sort_col in sheets:
            if not rows:
                pd.DataFrame().to_excel(writer, sheet_name=name, index=False)
                continue
            df = pd.DataFrame(rows)
            if sort_col and sort_col in df.columns:
                df = df.sort_values(sort_col, ascending=False)
            df.to_excel(writer, sheet_name=name, index=False)
            if style is not None:
                style(writer.sheets[name])


# ── Piotroski F-Score breakdown and Coffee Can criteria (daily reports) ───────────────────────

def _ratio(num: Optional[float], den: Optional[float]) -> Optional[float]:
    """``num / den`` when both are truthy numbers, else None."""
    return (num / den) if (num and den) else None


def _improved(now: Optional[float], before: Optional[float], lower_is_better: bool = False) -> int:
    """1 when both values exist and ``now`` beats ``before``, else 0."""
    if now is None or before is None:
        return 0
    return 1 if (now < before if lower_is_better else now > before) else 0


def _pct(value: Optional[float]) -> Any:
    """``value`` as a 2-dp percentage, or 'N/A' when missing or zero."""
    return round(value * 100, 2) if value else "N/A"


def piotroski_components(inc: pd.DataFrame, bal: Optional[pd.DataFrame], cf: Optional[pd.DataFrame],
                         ocf_detail: tuple, shares_fallback: Optional[float] = None,
                         shares_detail_key: Optional[str] = None) -> tuple:
    """The nine Piotroski criteria as ``(scores, details)`` dicts (ordered F1..F9).

    ``ocf_detail`` is ``(details key, divisor, decimals)`` for the operating-cash-flow figure (e.g.
    ``("OCF_current_$M", 1e6, 1)``). ``shares_fallback`` stands in for a missing latest share count;
    ``shares_detail_key`` adds the latest share count (millions) to the details when given.
    """
    scores, details = {}, {}
    ni = [row(inc, "Net Income", col=c) for c in (0, 1)]
    assets = [row(bal, "Total Assets", col=c) for c in (0, 1)]
    roa = [_ratio(ni[c], assets[c]) for c in (0, 1)]
    rev = [row(inc, "Total Revenue", col=c) for c in (0, 1)]

    # Profitability: F1-F4
    ocf_0 = row(cf, "Operating Cash Flow", "Total Cash From Operating Activities", col=0)
    scores["F1_ROA_positive"] = 1 if (roa[0] and roa[0] > 0) else 0
    details["ROA_current_%"] = _pct(roa[0])
    scores["F2_OCF_positive"] = 1 if (ocf_0 and ocf_0 > 0) else 0
    key, div, nd = ocf_detail
    details[key] = round(ocf_0 / div, nd) if ocf_0 else "N/A"
    scores["F3_ROA_improving"] = _improved(roa[0], roa[1])
    details["ROA_prev_%"] = _pct(roa[1])
    scores["F4_Accruals"] = 1 if (ocf_0 and assets[0] and roa[0] is not None and (ocf_0 / assets[0]) > roa[0]) else 0

    # Leverage and liquidity: F5-F7
    ltd = [row(bal, "Long Term Debt", col=c) or 0 for c in (0, 1)]
    lev = [(ltd[c] / assets[c]) if assets[c] else None for c in (0, 1)]
    scores["F5_Leverage_down"] = _improved(lev[0], lev[1], lower_is_better=True)
    details["LTD_ratio_curr_%"] = _pct(lev[0])
    details["LTD_ratio_prev_%"] = _pct(lev[1])
    cr = [_ratio(row(bal, "Current Assets", "Total Current Assets", col=c),
                 row(bal, "Current Liabilities", "Total Current Liabilities", col=c)) for c in (0, 1)]
    scores["F6_CurrentRatio_up"] = _improved(cr[0], cr[1])
    details["CurrentRatio_curr"] = round(cr[0], 2) if cr[0] else "N/A"
    details["CurrentRatio_prev"] = round(cr[1], 2) if cr[1] else "N/A"
    sh_0 = row(bal, "Share Issued", col=0) or shares_fallback
    sh_1 = row(bal, "Share Issued", col=1)
    scores["F7_No_dilution"] = (1 if sh_0 <= sh_1 else 0) if (sh_0 and sh_1) else 1  # no data: assume none
    if shares_detail_key:
        details[shares_detail_key] = round(sh_0 / 1e6, 1) if sh_0 else "N/A"

    # Operating efficiency: F8-F9
    gm = [_ratio(row(inc, "Gross Profit", col=c), rev[c]) for c in (0, 1)]
    scores["F8_GrossMargin_up"] = _improved(gm[0], gm[1])
    details["GrossMargin_curr_%"] = _pct(gm[0])
    details["GrossMargin_prev_%"] = _pct(gm[1])
    at = [_ratio(rev[c], assets[c]) for c in (0, 1)]
    scores["F9_AssetTurnover_up"] = _improved(at[0], at[1])
    details["AssetTurnover_curr"] = round(at[0], 3) if at[0] else "N/A"
    details["AssetTurnover_prev"] = round(at[1], 3) if at[1] else "N/A"
    return scores, details


def interpret_f_score(total: int) -> str:
    """Plain-language reading of a 0-9 Piotroski score."""
    return ("STRONG — likely outperformer" if total >= 7 else
            "MODERATE — neutral stance" if total >= 4 else
            "WEAK — avoid or short candidate")


def revenue_cagr_criterion(inc: pd.DataFrame, key: str) -> tuple:
    """Coffee Can C1: ``({key: 0/1}, details)`` for revenue CAGR > 10% over the available years."""
    revs = series(inc, "Total Revenue")
    if len(revs) < 2:
        return {key: 0}, {"Revenue_CAGR_%": "N/A"}
    years = len(revs) - 1  # revs[0] is the latest year, revs[-1] the oldest
    cagr = ((revs[0] / revs[-1]) ** (1 / years) - 1) * 100 if revs[-1] > 0 else None
    return ({key: 1 if (cagr and cagr > 10) else 0},
            {"Revenue_CAGR_%": round(cagr, 2) if cagr else "N/A", "Revenue_years": years})


def debt_to_equity_criterion(info: dict, bal: Optional[pd.DataFrame], eq_series: list,
                             abs_equity: bool) -> tuple:
    """Coffee Can C3: ``({'C3_LowDebt': 0/1}, details)`` for Debt/Equity < 1 (info first, balance sheet second)."""
    de_raw = info.get("debtToEquity")
    if de_raw is not None:
        de = de_raw / 100 if de_raw > 10 else de_raw  # yfinance sometimes reports percent (45.2 = 0.452x)
    else:
        ltd_s = series(bal, "Long Term Debt")
        if not (ltd_s and eq_series and eq_series[0] != 0):
            return {"C3_LowDebt": 0}, {"Debt_to_Equity": "N/A"}
        de = ltd_s[0] / (abs(eq_series[0]) if abs_equity else eq_series[0])
    return {"C3_LowDebt": 1 if de < 1 else 0}, {"Debt_to_Equity": round(de, 2)}


def no_loss_year_criterion(ni_series: list) -> tuple:
    """Coffee Can C5: ``({'C5_NoProfitLoss': 0/1}, details)`` - every analysed year had positive net income."""
    if not ni_series:
        return {"C5_NoProfitLoss": 0}, {}
    return ({"C5_NoProfitLoss": 1 if all(n > 0 for n in ni_series) else 0},
            {"Loss_years": sum(1 for n in ni_series if n <= 0), "Years_analysed": len(ni_series)})

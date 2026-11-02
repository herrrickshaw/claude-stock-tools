"""Helpers shared by the five ``*_market_scan.py`` scripts (pure functions plus small I/O edges).

The scanners are run as scripts (``python 02_market_screeners/<x>_market_scan.py``), so this module is
imported as a sibling (``from scan_common import ...``); Python puts the script directory on ``sys.path``.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, NamedTuple, Optional

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

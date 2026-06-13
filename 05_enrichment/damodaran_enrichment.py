"""
damodaran_enrichment.py
=======================
Downloads and caches Damodaran sector/country datasets from NYU Stern,
then enriches any scanner's triple-hits DataFrame with:

  - Sector median PE  (vs stock's trailing PE)
  - Sector median EV/EBITDA
  - Sector median ROE  (context for Coffee Can ROCE threshold)
  - Sector net profit margin
  - Country Equity Risk Premium (ERP) from Moodys-based ratings

Usage — standalone enrichment of a scanner Excel output
--------------------------------------------------------
    python damodaran_enrichment.py --file path/to/scan_results.xlsx \\
                                   --sheet "Triple Hits" \\
                                   --market global

Usage — import in a scanner
----------------------------
    from damodaran_enrichment import enrich_triple_hits
    df_enriched = enrich_triple_hits(df_triple_hits, market="global")

Data is cached locally as parquet files (24-hour TTL) to avoid hammering
the NYU server on every run.

Markets supported: us, europe, japan, india, emerging, global
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

_BASE = "https://pages.stern.nyu.edu/~adamodar/pc/datasets"
_CACHE_DIR = Path(__file__).resolve().parent / ".damodaran_cache"
_CACHE_TTL_H = 24

# Market suffix map used in Damodaran filenames
_MARKET_SUFFIX = {
    "us":       "",
    "europe":   "Europe",
    "japan":    "Japan",
    "india":    "India",
    "china":    "China",
    "emerging": "emerg",
    "global":   "Global",
}

# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_path(key: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{key}.csv"

def _meta_path(key: str) -> Path:
    return _CACHE_DIR / f"{key}.meta.json"

def _is_fresh(key: str) -> bool:
    mp = _meta_path(key)
    if not mp.exists() or not _cache_path(key).exists():
        return False
    meta = json.loads(mp.read_text())
    return (time.time() - meta.get("ts", 0)) < _CACHE_TTL_H * 3600

def _save_cache(key: str, df: pd.DataFrame) -> None:
    df.to_csv(_cache_path(key))
    _meta_path(key).write_text(json.dumps({"ts": time.time()}))

def _load_cache(key: str) -> pd.DataFrame:
    return pd.read_csv(_cache_path(key), index_col=0)


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

_HEADERS = {"User-Agent": "Mozilla/5.0 (research/enrichment script)"}


def _download_bytes(url: str) -> Optional[bytes]:
    try:
        r = requests.get(url, headers=_HEADERS, timeout=30)
        r.raise_for_status()
        return r.content
    except Exception as exc:
        log.warning("Could not download %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Sector dataset loaders
# ---------------------------------------------------------------------------
#
# All Damodaran sector XLS files share the same layout:
#   Sheet: "Industry Averages"
#   Row 7 (0-indexed): column headers
#   Row 8+: data
#   Column 0: "Industry Name"

_SECTOR_SHEET = "Industry Averages"
_SECTOR_HEADER_ROW = 7

_SECTOR_URLS = {
    "pe":      f"{_BASE}/pe{{sfx}}.xls",
    "vebitda": f"{_BASE}/vebitda{{sfx}}.xls",
    "pbv":     f"{_BASE}/pbv{{sfx}}.xls",
    "margin":  f"{_BASE}/margin{{sfx}}.xls",
}

# Which columns to extract from each dataset and what to rename them
_SECTOR_COLS = {
    "pe": {
        "Current PE":  "Sector_PE_Current",
        "Trailing PE": "Sector_PE_Trailing",
        "Forward PE":  "Sector_PE_Fwd",
    },
    "vebitda": {
        "EV/EBITDA":             "Sector_EV_EBITDA",
        "EV/EBIT":               "Sector_EV_EBIT",
        # alternate column name used in some files
        "EV/EBITDA (corrected)": "Sector_EV_EBITDA",
    },
    "pbv": {
        "ROE":                   "Sector_ROE",
        "ROIC":                  "Sector_ROIC",
        "Price/Book":            "Sector_PB",
        "EV/Invested Capital":   "Sector_EV_IC",
    },
    "margin": {
        "Net Margin":            "Sector_Net_Margin",
        "Pre-tax Operating Margin": "Sector_Op_Margin",
        "EBITDA/Sales":          "Sector_EBITDA_Margin",
        "Gross Margin":          "Sector_Gross_Margin",
    },
}


def _parse_sector_sheet(raw_bytes: bytes, dataset: str) -> Optional[pd.DataFrame]:
    """Parse a Damodaran sector XLS file and return a tidy DataFrame."""
    try:
        xl = pd.ExcelFile(io.BytesIO(raw_bytes))
    except Exception as exc:
        log.warning("Cannot open Excel bytes: %s", exc)
        return None

    sheet = _SECTOR_SHEET if _SECTOR_SHEET in xl.sheet_names else xl.sheet_names[-1]

    # Some files (vebitda, margin) have an extra category header row at row 7;
    # actual column names are at row 8. Detect this by checking if row 7 parsing
    # leaves "Unnamed" in column 0 (meaning real names are one row lower).
    df = xl.parse(sheet, header=_SECTOR_HEADER_ROW)
    if str(df.columns[0]).startswith("Unnamed"):
        # Row 8 has real column names (stored as data row 0)
        raw_cols = ["Sector"] + df.iloc[0, 1:].astype(str).str.strip().tolist()
        # Deduplicate: keep first occurrence of each name
        seen: dict[str, int] = {}
        deduped = []
        for c in raw_cols:
            if c in seen:
                seen[c] += 1
                deduped.append(f"{c}__{seen[c]}")
            else:
                seen[c] = 0
                deduped.append(c)
        df.columns = deduped
        df = df.iloc[1:].reset_index(drop=True)
    else:
        # Column 0 should be "Industry Name"
        df = df.rename(columns={df.columns[0]: "Sector"})
    df["Sector"] = df["Sector"].astype(str).str.strip()
    df = df[~df["Sector"].isin(["nan", "Industry Name", "Total Market"])].copy()

    # Apply column renames
    rename = _SECTOR_COLS.get(dataset, {})
    df = df.rename(columns=rename)

    keep = ["Sector"] + [v for v in rename.values() if v in df.columns]
    df = df[[c for c in keep if c in df.columns]].copy()

    # Remove any duplicate column names keeping first occurrence
    df = df.loc[:, ~df.columns.duplicated(keep="first")]

    for col in df.columns[1:]:
        col_data = df[col]
        if isinstance(col_data, pd.DataFrame):
            col_data = col_data.iloc[:, 0]
        df[col] = pd.to_numeric(col_data, errors="coerce")

    df = df.set_index("Sector")
    return df


def load_sector_table(dataset: str, market: str) -> Optional[pd.DataFrame]:
    """Load and cache a Damodaran sector table for the given market."""
    sfx = _MARKET_SUFFIX.get(market, "Global")
    key = f"{dataset}_{market}"

    if _is_fresh(key):
        return _load_cache(key)

    url = _SECTOR_URLS[dataset].format(sfx=sfx)
    log.info("Downloading %s [%s] …", dataset, market)
    raw = _download_bytes(url)

    # Fallback to Global if market-specific fails
    if raw is None and sfx:
        url_global = _SECTOR_URLS[dataset].format(sfx="Global")
        log.info("Falling back to Global dataset for %s", dataset)
        raw = _download_bytes(url_global)

    if raw is None:
        return None

    df = _parse_sector_sheet(raw, dataset)
    if df is None or df.empty:
        return None

    _save_cache(key, df)
    return df


# ---------------------------------------------------------------------------
# Country Risk Premium loader
# ---------------------------------------------------------------------------
#
# Sheet: "ERPs by country"
# Row 7: column headers  →  col0=Country, col4=Total Equity Risk Premium
# Row 8+: data

_ERP_URL   = f"{_BASE}/ctrypremApr26.xlsx"
_ERP_SHEET = "ERPs by country"
_ERP_HEADER_ROW = 7


def load_country_erp() -> Optional[pd.DataFrame]:
    """
    Returns a DataFrame indexed by country name:
      ERP  (Total Equity Risk Premium, e.g. 0.055 = 5.5%)
      CRP  (Country Risk Premium over mature market)
    """
    key = "country_erp"
    if _is_fresh(key):
        return _load_cache(key)

    log.info("Downloading Country Risk Premiums (Apr 2026) …")
    raw = _download_bytes(_ERP_URL)
    if raw is None:
        return None

    try:
        xl = pd.ExcelFile(io.BytesIO(raw))
        df = xl.parse(_ERP_SHEET, header=_ERP_HEADER_ROW)
    except Exception as exc:
        log.warning("Cannot parse ERP sheet: %s", exc)
        return None

    # Column names after header row 7:
    # col0=Country, col1=region, col2=Moody's rating, col3=Rating-based Default Spread,
    # col4=Total Equity Risk Premium, col5=Country Risk Premium
    df.columns = [str(c).strip() for c in df.columns]
    country_col = df.columns[0]
    erp_col     = df.columns[4]   # "Total Equity Risk Premium"
    crp_col     = df.columns[5]   # "Country Risk Premium"

    df = df[[country_col, erp_col, crp_col]].copy()
    df.columns = ["Country", "ERP", "CRP"]
    df["Country"] = df["Country"].astype(str).str.strip()
    df = df[~df["Country"].isin(["nan", "Country"])].copy()
    df["ERP"] = pd.to_numeric(df["ERP"], errors="coerce")
    df["CRP"] = pd.to_numeric(df["CRP"], errors="coerce")
    df = df.dropna(subset=["ERP"]).set_index("Country")

    _save_cache(key, df)
    return df


# ---------------------------------------------------------------------------
# Sector-name matching
# ---------------------------------------------------------------------------

def _match_sector(user_sector: str, index: pd.Index) -> Optional[str]:
    """Fuzzy-match a stock's sector string to the Damodaran index."""
    if pd.isna(user_sector) or not str(user_sector).strip():
        return None
    s = str(user_sector).strip().lower()
    # Exact match first
    for idx in index:
        if idx.lower() == s:
            return idx
    # Substring match
    for idx in index:
        if s in idx.lower() or idx.lower() in s:
            return idx
    return None


# ---------------------------------------------------------------------------
# Main enrichment function
# ---------------------------------------------------------------------------

def enrich_triple_hits(
    df: pd.DataFrame,
    market: str = "global",
    sector_col: str = "Sector",
    country_col: Optional[str] = "Country",
    pe_col: Optional[str] = None,
    ev_ebitda_col: Optional[str] = None,
) -> pd.DataFrame:
    """
    Enrich a DataFrame of triple-hit stocks with Damodaran sector benchmarks.

    Parameters
    ----------
    df            DataFrame with at least a sector column
    market        one of 'us', 'europe', 'japan', 'india', 'emerging', 'global'
    sector_col    column in df that holds GICS/industry sector name
    country_col   column in df that holds country name (for ERP), or None
    pe_col        column in df that holds trailing PE (for relative valuation), or None
    ev_ebitda_col column in df that holds EV/EBITDA, or None

    Returns df enriched with extra columns (prefixed Sector_ / Country_)
    """
    result = df.copy()

    pe_tbl      = load_sector_table("pe",      market)
    vebitda_tbl = load_sector_table("vebitda", market)
    pbv_tbl     = load_sector_table("pbv",     market)
    margin_tbl  = load_sector_table("margin",  market)
    erp_tbl     = load_country_erp()

    def _map(tbl: Optional[pd.DataFrame], out_col: str) -> None:
        if tbl is None or out_col not in tbl.columns or sector_col not in result.columns:
            return
        result[out_col] = result[sector_col].apply(
            lambda s: tbl.at[m, out_col] if (m := _match_sector(s, tbl.index)) else None
        )

    # PE benchmarks
    if pe_tbl is not None:
        _map(pe_tbl, "Sector_PE_Trailing")
        _map(pe_tbl, "Sector_PE_Fwd")
        if pe_col and pe_col in result.columns and "Sector_PE_Trailing" in result.columns:
            result["PE_vs_Sector"] = (result[pe_col] / result["Sector_PE_Trailing"]).round(2)

    # EV/EBITDA benchmarks
    if vebitda_tbl is not None:
        _map(vebitda_tbl, "Sector_EV_EBITDA")
        _map(vebitda_tbl, "Sector_EV_EBIT")
        if ev_ebitda_col and ev_ebitda_col in result.columns and "Sector_EV_EBITDA" in result.columns:
            result["EV_EBITDA_vs_Sector"] = (result[ev_ebitda_col] / result["Sector_EV_EBITDA"]).round(2)

    # Return measures
    if pbv_tbl is not None:
        _map(pbv_tbl, "Sector_ROE")
        _map(pbv_tbl, "Sector_ROIC")

    # Margin benchmarks
    if margin_tbl is not None:
        _map(margin_tbl, "Sector_Net_Margin")
        _map(margin_tbl, "Sector_Op_Margin")

    # Country ERP
    if erp_tbl is not None and country_col and country_col in result.columns:
        result["Country_ERP_pct"] = result[country_col].apply(
            lambda c: erp_tbl.at[m, "ERP"] * 100 if (m := _match_sector(str(c), erp_tbl.index)) else None
        ).round(2)
        result["Country_CRP_pct"] = result[country_col].apply(
            lambda c: erp_tbl.at[m, "CRP"] * 100 if (m := _match_sector(str(c), erp_tbl.index)) else None
        ).round(2)

    return result


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def _print_preview(market: str) -> None:
    tables = {
        "pe":      load_sector_table("pe",      market),
        "vebitda": load_sector_table("vebitda", market),
        "pbv":     load_sector_table("pbv",     market),
        "margin":  load_sector_table("margin",  market),
        "erp":     load_country_erp(),
    }
    for name, tbl in tables.items():
        if tbl is None:
            print(f"\n── {name.upper()} : FAILED TO LOAD")
            continue
        print(f"\n── {name.upper()} ({len(tbl)} rows) ──────────────────────")
        print(tbl.head(6).to_string())


def main() -> None:
    parser = argparse.ArgumentParser(description="Damodaran enrichment for scanner output")
    parser.add_argument("--file",         help="Path to scanner Excel output to enrich")
    parser.add_argument("--sheet",        default="Triple Hits")
    parser.add_argument("--market",       default="global", choices=list(_MARKET_SUFFIX.keys()))
    parser.add_argument("--sector-col",   default="Sector")
    parser.add_argument("--country-col",  default=None)
    parser.add_argument("--pe-col",       default=None)
    parser.add_argument("--ev-ebitda-col",default=None)
    parser.add_argument("--preview",      action="store_true",
                        help="Print first rows of each table and exit")
    parser.add_argument("--clear-cache",  action="store_true")
    args = parser.parse_args()

    if args.clear_cache and _CACHE_DIR.exists():
        import shutil
        shutil.rmtree(_CACHE_DIR)
        log.info("Cache cleared.")

    if args.preview or not args.file:
        _print_preview(args.market)
        return

    df = pd.read_excel(args.file, sheet_name=args.sheet)
    log.info("Loaded %d rows from '%s' → '%s'", len(df), args.file, args.sheet)

    enriched = enrich_triple_hits(
        df,
        market=args.market,
        sector_col=args.sector_col,
        country_col=args.country_col,
        pe_col=args.pe_col,
        ev_ebitda_col=args.ev_ebitda_col,
    )

    out_path = Path(args.file).with_name(Path(args.file).stem + "_enriched.xlsx")

    orig = pd.ExcelFile(args.file)
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        for sheet in orig.sheet_names:
            src = enriched if sheet == args.sheet else orig.parse(sheet)
            src.to_excel(writer, sheet_name=sheet, index=False)

    log.info("Saved → %s", out_path)
    new_cols = [c for c in enriched.columns if c not in df.columns]
    print("\nNew columns added:")
    for c in new_cols:
        print(f"  {c}")


if __name__ == "__main__":
    main()

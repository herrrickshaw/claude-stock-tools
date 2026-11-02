"""Characterisation tests for helpers shared by the five market scanners (no network)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from conftest import SCANNERS

GOLDEN = Path(__file__).parent / "golden"
FUND_SCANNERS = ("india", "us", "europe", "japan", "korea")


@pytest.mark.parametrize("name", SCANNERS)
def test_first_df_skips_empty_and_missing(load_scanner, name):
    m = load_scanner(name)
    full = pd.DataFrame({"a": [1]})
    t = SimpleNamespace(empty=pd.DataFrame(), junk="x", good=full)
    assert m._first_df(t, "missing", "empty", "junk", "good") is full
    assert m._first_df(t, "missing", "empty") is None


@pytest.mark.parametrize("name", SCANNERS)
def test_row_returns_float_or_none(load_scanner, name):
    m = load_scanner(name)
    df = pd.DataFrame({"y1": [1.5, None], "y0": [2.0, 3.0]}, index=["Rev", "NaNRow"])
    assert m._row(df, "Nope", "Rev") == 1.5
    assert m._row(df, "Rev", col=1) == 2.0
    assert m._row(df, "NaNRow") is None
    assert m._row(df, "Absent") is None
    assert m._row(None, "Rev") is None
    assert m._row(pd.DataFrame(), "Rev") is None


@pytest.mark.parametrize("name", ("europe", "japan", "korea"))
def test_series_drops_nan(load_scanner, name):
    m = load_scanner(name)
    df = pd.DataFrame([[1.0, None, 3.0]], index=["Rev"])
    assert m._series(df, "x", "Rev") == [1.0, 3.0]
    assert m._series(df, "x") == []
    assert m._series(None, "Rev") == []


@pytest.mark.parametrize("name,key", [("europe", "universe"), ("korea", "universe"), ("japan", "tickers")])
def test_universe_cache_roundtrip_and_ttl(load_scanner, name, key):
    m = load_scanner(name)
    assert m._load_cache() is None
    m._save_cache([{"a": 1}])
    raw = json.loads(m._CACHE_FILE.read_text())
    assert raw[key] == [{"a": 1}] and set(raw) == {"ts", key}
    assert m._load_cache() == [{"a": 1}]
    m._CACHE_FILE.write_text(json.dumps({"ts": time.time() - m.SYMBOL_CACHE_TTL - 5, key: [1]}))
    assert m._load_cache() is None
    m._CACHE_FILE.write_text("not json")
    assert m._load_cache() is None


@pytest.mark.parametrize("name,k1,k2", [("india", "nse", "bse_only"), ("us", "nasdaq", "nyse")])
def test_symbol_cache_roundtrip_and_ttl(load_scanner, name, k1, k2):
    m = load_scanner(name)
    assert m._load_symbol_cache() == (None, None)
    m._save_symbol_cache(["A"], ["B"])
    assert set(json.loads(m._SYMBOL_CACHE.read_text())) == {"ts", k1, k2}
    assert m._load_symbol_cache() == (["A"], ["B"])
    m._SYMBOL_CACHE.write_text(json.dumps({"ts": 0, k1: ["A"], k2: ["B"]}))
    assert m._load_symbol_cache() == (None, None)


def _sheet_fingerprint(ws):
    out = []
    for row in ws.iter_rows():
        for c in row:
            out.append([c.coordinate, c.font.name, c.font.sz, c.font.b, c.font.color.rgb if c.font.color else None,
                        c.fill.fgColor.rgb if c.fill.fill_type else None, c.number_format,
                        c.alignment.horizontal, c.alignment.vertical, bool(c.alignment.wrap_text),
                        c.border.left.color.rgb if c.border.left.color else None])
    out.append({k: v.height for k, v in ws.row_dimensions.items()})
    out.append({k: v.width for k, v in ws.column_dimensions.items()})
    return out


@pytest.mark.parametrize("name", ("europe", "japan", "korea"))
def test_style_sheet_matches_golden(load_scanner, name):
    from openpyxl import Workbook

    m = load_scanner(name)
    wb = Workbook()
    ws = wb.active
    ws.append(["Ticker", "Mcap", "CAGR %", "Price", "PE", "Note"])
    ws.append(["AAA", 1234567.891, 12.3456, 99.5, 7.123, "x"])
    ws.append(["BBB", 55.5, 1.0, 10, 2, None])
    ws.append(["CCC", 5, 5, 5, 5, "long text value here"])
    m.style_sheet(ws)
    got = json.loads(json.dumps(_sheet_fingerprint(ws)))
    path = GOLDEN / f"style_sheet_{name}.json"
    if not path.exists():  # first run on ORIGINAL code records the golden file
        path.write_text(json.dumps(got, indent=0))
    assert got == json.loads(path.read_text())


@pytest.mark.parametrize("name", ("india", "us"))
def test_save_excel_sheets_and_sort(load_scanner, name):
    m = load_scanner(name)
    rows = [{"Change%": 1, "Upside_to_Top%": 2, "Piotroski_Score": 3, "T": "a"},
            {"Change%": 5, "Upside_to_Top%": 1, "Piotroski_Score": 9, "T": "b"}]
    path = m.save_excel(rows, rows, rows, [], tag="t")
    assert path.parent == m.DOWNLOAD_DIR and path.name.startswith("t_full_scan_") and path.suffix == ".xlsx"
    xl = pd.ExcelFile(path)
    assert xl.sheet_names == ["All_Stocks", "Darvas_Signals", "Fundamentals", "Triple_Hits"]
    assert list(pd.read_excel(path, sheet_name="All_Stocks")["T"]) == ["b", "a"]
    assert pd.read_excel(path, sheet_name="Triple_Hits").empty


def _bars(highs, lows, closes):
    return pd.DataFrame({"High": highs, "Low": lows, "Close": closes})


@pytest.mark.parametrize("name", SCANNERS)
def test_darvas_insufficient_and_no_box(load_scanner, name):
    m = load_scanner(name)
    assert m.compute_darvas_box(pd.DataFrame())["signal"] == "INSUFFICIENT_DATA"
    assert m.compute_darvas_box(_bars([1] * 4, [1] * 4, [1] * 4))["signal"] == "INSUFFICIENT_DATA"
    rising = list(range(10, 30))
    assert m.compute_darvas_box(_bars(rising, rising, rising))["signal"] == "NO_BOX"


@pytest.mark.parametrize("name", SCANNERS)
def test_darvas_current_bar_excluded_from_box(load_scanner, name):
    """Owner rule: the current bar never forms the box, so a spike on the last bar is a BREAKOUT."""
    m = load_scanner(name)
    highs = [10, 12, 20, 15, 14, 13, 12, 13, 12, 13, 12, 13, 12, 13, 12, 13, 12, 13, 12, 99]
    lows = [h - 3 for h in highs]
    closes = [h - 1 for h in highs]
    closes[-1] = 99
    res = m.compute_darvas_box(_bars(highs, lows, closes))
    assert res["signal"] == "BREAKOUT_BUY"
    assert res["box_top"] < 99  # the 99 spike on the current bar did not become the box top
    closes[-1] = 1
    assert m.compute_darvas_box(_bars(highs, lows, closes))["signal"] == "BREAKDOWN_SELL"

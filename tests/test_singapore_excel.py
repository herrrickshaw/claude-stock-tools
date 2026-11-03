"""Golden characterisation of the Singapore daily report's Excel writer (cells, styling, widths)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from openpyxl import load_workbook

from test_darvas_variants import _load

GOLDEN = Path(__file__).parent / "golden"

RESULTS = [
    {"symbol": "D05.SI", "quote": {"name": "DBS", "sector": "Banks", "lastPrice": 40.0, "previousClose": 39.0,
                                   "trailingPE": 9.5, "priceToBook": 1.4, "dividendYield": 0.05,
                                   "52wHigh": 42.0, "52wLow": 30.0, "volume": 1200000, "200dAvg": 36.0,
                                   "marketCap": 1.1e11},
     "scans": {"darvas": {"signal": "BREAKOUT_BUY", "box_top": 39.5, "box_bottom": 35.0,
                          "position_in_box_pct": 110.0, "upside_to_top_pct": -1.25},
               "piotroski": {"f_score": 8, "interpretation": "STRONG"},
               "pegy": {"peg_ratio": 1.1, "pegy_adjusted": 0.9, "dividend_yield_%": 5.0,
                        "breakout": {"signal": "BREAKOUT", "distance_from_200ma_%": 11.1}}}},
    {"symbol": "Z74.SI", "quote": {"name": "Sparse"}, "scans": {}},
]


def _dump(path: Path) -> dict:
    out = {}
    for ws in load_workbook(path).worksheets:
        cells = {}
        for row in ws.iter_rows():
            for c in row:
                if c.value is not None or c.fill.fill_type:
                    cells[c.coordinate] = [c.value, c.font.b, c.font.sz, c.font.color.rgb if c.font.color else None,
                                           c.fill.start_color.rgb if c.fill.fill_type else None,
                                           c.alignment.horizontal]
        out[ws.title] = {"cells": cells, "merged": sorted(str(m) for m in ws.merged_cells.ranges),
                         "widths": {k: v.width for k, v in sorted(ws.column_dimensions.items())}}
    return out


@pytest.mark.parametrize("scans", [False, True], ids=["plain", "with_scans"])
def test_write_excel_report_golden(tmp_path, monkeypatch, scans):
    mod = _load("01_daily_reports/singapore_daily_report.py", tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "DOWNLOAD_DIR", tmp_path)
    mod._write_excel_report(RESULTS, include_scans=scans)
    (xlsx,) = tmp_path.glob("sg_stock_report_*.xlsx")
    got = json.loads(json.dumps(_dump(xlsx), default=str))
    path = GOLDEN / f"sg_excel_{'scans' if scans else 'plain'}.json"
    if not path.exists():  # recorded once from the ORIGINAL implementation
        path.write_text(json.dumps(got, indent=1, sort_keys=True))
    assert got == json.loads(path.read_text())

"""Golden characterisation of us_market_screener_v1.main(): SQLite checkpoint + CSV outputs, offline."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from test_darvas_variants import _load

GOLDEN = Path(__file__).parent / "golden"
STAMP = re.compile(r"\d{8}")
UNIVERSE = pd.DataFrame({"Symbol": ["AAA", "BBB", "CCC"], "Name": ["A Inc", "B Inc", "C Inc"],
                         "Exchange": ["NASDAQ", "NYSE", "NYSE"]})
DARVAS = {"AAA": {"signal": "BREAKOUT_BUY", "box_top": 10.0, "box_bottom": 8.0, "current_price": 11.0,
                  "upside_pct": -9.09, "pos_in_box": 150.0},
          "BBB": {"signal": "IN_BOX", "box_top": 20.0, "box_bottom": 15.0, "current_price": 17.0,
                  "upside_pct": 17.65, "pos_in_box": 40.0},
          "CCC": {"signal": "BREAKDOWN_SELL", "box_top": 5.0, "box_bottom": 4.0, "current_price": 3.0,
                  "upside_pct": 66.67, "pos_in_box": -100.0}}


def _fund(sym, name, exch):
    strong = sym == "AAA"
    return {"symbol": sym, "name": name, "exchange": exch, "scanned_at": "t", "sector": "Tech", "industry": "X",
            "market_cap": 5e9 if strong else 1e8, "last_price": 11.0, "f_score": 8 if strong else 3,
            **{f"f{i}": 1 for i in range(1, 10)}, "cc_qualifies": 1 if strong else 0,
            "cc_score": "6/6" if strong else "2/6", **{f"cc_c{i}": 1 for i in range(1, 7)}, "cc_roe_avg": 18.0}


def _run(tmp_path, monkeypatch, capsys, **kwargs):
    mod = _load("02_market_screeners/us_market_screener_v1.py", tmp_path, monkeypatch)
    out = tmp_path / "out"
    monkeypatch.setattr(mod, "OUTPUT_DIR", out)
    monkeypatch.setattr(mod, "DB_FILE", out / "screener.db")
    monkeypatch.setattr(mod, "TQDM_OK", False)
    monkeypatch.setattr(mod, "fetch_stock_universe", lambda exchange="all": UNIVERSE.copy())
    monkeypatch.setattr(mod, "run_darvas_batch", lambda syms, batch_size=500, pbar=None: {s: DARVAS[s] for s in syms})
    monkeypatch.setattr(mod, "scan_fundamentals_one", _fund)
    mod.main(delay=0, workers=1, **kwargs)
    text = capsys.readouterr().out.replace(str(tmp_path), "<TMP>")
    con = sqlite3.connect(out / "screener.db")
    results = pd.read_sql("SELECT * FROM results ORDER BY symbol", con).drop(columns=["scanned_at"])
    progress = pd.read_sql("SELECT symbol, phase FROM progress ORDER BY phase, symbol", con)
    con.close()
    files = {p.name: json.loads(pd.read_csv(p).drop(columns=["scanned_at"], errors="ignore")
                                                          .sort_values("symbol").to_json(orient="records")) if p.name.startswith(
        ("scan_summary", "darvas", "strong", "coffee", "triple")) and p.stat().st_size > 5 else None
        for p in sorted(out.glob("*.csv"))}
    got = {"stdout": STAMP.sub("<DATE>", text), "results": json.loads(results.to_json(orient="records")),
           "progress": progress.to_dict("records"),
           "files": {STAMP.sub("<DATE>", k): v for k, v in files.items()}}
    return json.loads(json.dumps(got, default=str))


@pytest.mark.parametrize("label,kwargs", [("full", {}), ("darvas_only", {"darvas_only": True}),
                                          ("limit", {"limit": 2})])
def test_main_golden(tmp_path, monkeypatch, capsys, label, kwargs):
    got = _run(tmp_path, monkeypatch, capsys, **kwargs)
    path = GOLDEN / f"screener_v1_main_{label}.json"
    if not path.exists():  # recorded once from the ORIGINAL implementation
        path.write_text(json.dumps(got, indent=1, sort_keys=True))
    assert got == json.loads(path.read_text())

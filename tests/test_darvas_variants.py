"""Golden characterisation of every Darvas Box implementation in the repo (offline, synthetic bars).

The eight historic copies differ only in column lookup, validation order and output keys, so each keeps
its own golden record; the shared box-finding core must reproduce all of them byte for byte.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).parent / "golden"

# (id, relative path, function name)
VARIANTS = [
    ("india_daily", "01_daily_reports/india_daily_report.py", "compute_darvas_box"),
    ("us_daily", "01_daily_reports/us_daily_report.py", "compute_darvas_box"),
    ("sg_daily", "01_daily_reports/singapore_daily_report.py", "compute_darvas_box"),
    ("us_v1", "02_market_screeners/us_market_screener_v1.py", "_darvas_from_df"),
    ("colab", "03_colab_notebooks/us_stocks_colab_script.py", "compute_darvas_box"),
]


def _load(rel: str, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(REPO / "02_market_screeners"))
    for top, attr in (("nse", "NSE"), ("bse", "BSE")):
        if importlib.util.find_spec(top) is None:
            mod = types.ModuleType(top)
            setattr(mod, attr, object)
            monkeypatch.setitem(sys.modules, top, mod)
    spec = importlib.util.spec_from_file_location("_darvas_" + Path(rel).stem, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _bars(highs, lows=None, closes=None):
    lows = lows if lows is not None else [h - 3 for h in highs]
    closes = closes if closes is not None else [h - 1 for h in highs]
    return pd.DataFrame({"High": highs, "Low": lows, "Close": closes})


BASE = [10, 12, 20, 15, 14, 13, 12, 13, 12, 13, 12, 13, 12, 13, 12, 13, 12, 13, 12, 12]


def _cases() -> dict:
    up = list(range(10, 30))
    breakout = _bars(BASE[:-1] + [99], closes=[h - 1 for h in BASE[:-1]] + [99])
    breakdown = _bars(BASE, closes=[h - 1 for h in BASE[:-1]] + [1])
    in_box = _bars(BASE)
    zeros = _bars([0] * 8 + BASE[8:], lows=[0] * 8 + [h - 3 for h in BASE[8:]])
    nan_bars = _bars(BASE)
    nan_bars.loc[4, "High"] = float("nan")
    no_bottom = _bars(BASE, lows=[0] * len(BASE))
    return {
        "empty": pd.DataFrame(),
        "short": _bars([1] * 4),
        "no_box": _bars(up, up, up),
        "breakout": breakout,
        "breakdown": breakdown,
        "in_box": in_box,
        "zeros": zeros,
        "nan": nan_bars,
        "no_bottom": no_bottom,
        "missing_cols": pd.DataFrame({"High": up, "Low": up}),
        "none": None,
    }


@pytest.mark.parametrize("vid,rel,fn", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_darvas_variant_golden(tmp_path, monkeypatch, vid, rel, fn):
    mod = _load(rel, tmp_path, monkeypatch)
    func = getattr(mod, fn)
    got = {}
    for name, df in _cases().items():
        for confirm in (3, 2):
            try:
                res = func(df, confirm)
            except Exception as exc:  # crashes on odd input are behaviour too
                res = {"raises": type(exc).__name__}
            got[f"{name}/{confirm}"] = json.loads(json.dumps(res, default=str))
    path = GOLDEN / f"darvas_{vid}.json"
    if not path.exists():  # recorded once from the ORIGINAL implementation
        path.write_text(json.dumps(got, indent=1, sort_keys=True))
    assert got == json.loads(path.read_text())


@pytest.mark.parametrize("name", ["india", "us", "europe", "japan", "korea"])
def test_scanner_darvas_golden(load_scanner, name):
    func = load_scanner(name).compute_darvas_box
    got = {}
    for case, df in _cases().items():
        for confirm in (3, 2):
            try:
                res = func(df, confirm)
            except Exception as exc:  # crashes on odd input are behaviour too
                res = {"raises": type(exc).__name__}
            got[f"{case}/{confirm}"] = json.loads(json.dumps(res, default=str))
    path = GOLDEN / f"darvas_scan_{name}.json"
    if not path.exists():  # recorded once from the ORIGINAL implementation
        path.write_text(json.dumps(got, indent=1, sort_keys=True))
    assert got == json.loads(path.read_text())

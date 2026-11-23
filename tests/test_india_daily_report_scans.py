"""india_daily_report: Piotroski F-Score (statements injected past the DataFrame-`or` bug in _yf_financials),
its display, the Darvas display and Darvas edge cases (NaN lows/highs, unconfirmed bottom)."""
import importlib.util
import os
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "01_daily_reports/india_daily_report.py"
COLS = [pd.Timestamp("2024-03-31"), pd.Timestamp("2023-03-31")]


@pytest.fixture(scope="module")
def india_mod(tmp_path_factory):
    stubs = {n: types.ModuleType(n) for n in ("nse", "bse", "nsepython")}
    stubs["nse"].NSE = type("NSE", (), {})
    stubs["bse"].BSE = type("BSE", (), {})
    saved = {n: sys.modules.get(n) for n in stubs}
    sys.modules.update(stubs)
    work = tmp_path_factory.mktemp("india_scans_cwd")
    old = os.getcwd()
    os.chdir(work)
    try:
        spec = importlib.util.spec_from_file_location("cst_india_scans", PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        os.chdir(old)
        for n, m in saved.items():
            if m is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = m
    return mod


def frame(rows):
    return pd.DataFrame({c: [v[i] if i < len(v) else None for v in rows.values()] for i, c in enumerate(COLS)},
                        index=list(rows))


def statements(**over):
    inc = {"Net Income": [100.0, 80.0], "Total Revenue": [1000.0, 900.0], "Gross Profit": [400.0, 330.0]}
    bal = {"Total Assets": [1000.0, 1000.0], "Long Term Debt": [100.0, 200.0],
           "Current Assets": [500.0, 400.0], "Current Liabilities": [250.0, 250.0], "Share Issued": [10.0, 10.0]}
    cf = {"Operating Cash Flow": [150.0, 120.0]}
    for d in (inc, bal, cf):
        for k in list(d):
            if k in over:
                if over[k] is None:
                    del d[k]
                else:
                    d[k] = over[k]
    return frame(inc), frame(bal), frame(cf)


@pytest.fixture
def piotroski(india_mod, monkeypatch):
    def run(info=None, **over):
        inc, bal, cf = statements(**over)
        monkeypatch.setattr(india_mod, "YFINANCE_OK", True)
        monkeypatch.setattr(india_mod, "yf", types.SimpleNamespace(
            Ticker=lambda s: types.SimpleNamespace(info=info if info is not None else {})), raising=False)
        monkeypatch.setattr(india_mod, "_yf_financials", lambda t: (inc, bal, cf))
        return india_mod.compute_piotroski_score("RELI")
    return run


def test_piotroski_perfect_nine_and_details(piotroski):
    r = piotroski()
    assert r["f_score"] == 9 and r["symbol"] == "RELI" and r["interpretation"] == "STRONG — likely outperformer"
    assert set(r["component_scores"].values()) == {1}
    d = r["details"]
    assert d["ROA_current_%"] == 10.0 and d["ROA_prev_%"] == 8.0
    assert d["OCF_current_Cr"] == 0.0                     # 150 / 1e7 rounds to 0.00 crore
    assert d["LTD_ratio_curr_%"] == 10.0 and d["LTD_ratio_prev_%"] == 20.0
    assert d["CurrentRatio_curr"] == 2.0 and d["CurrentRatio_prev"] == 1.6
    assert d["GrossMargin_curr_%"] == 40.0 and d["GrossMargin_prev_%"] == pytest.approx(36.67)
    assert d["AssetTurnover_curr"] == 1.0 and d["AssetTurnover_prev"] == 0.9


FAILS = [
    ({"Net Income": [-5.0, -10.0]}, "F1_ROA_positive"),
    ({"Operating Cash Flow": [-1.0, 120.0]}, "F2_OCF_positive"),
    ({"Net Income": [70.0, 80.0]}, "F3_ROA_improving"),
    ({"Operating Cash Flow": [90.0, 120.0]}, "F4_Accruals"),
    ({"Long Term Debt": [300.0, 200.0]}, "F5_Leverage_down"),
    ({"Current Assets": [300.0, 400.0]}, "F6_CurrentRatio_up"),
    ({"Share Issued": [12.0, 10.0]}, "F7_No_dilution"),
    ({"Gross Profit": [300.0, 330.0]}, "F8_GrossMargin_up"),
    ({"Total Revenue": [800.0, 900.0]}, "F9_AssetTurnover_up"),
]


@pytest.mark.parametrize("over,component", FAILS, ids=[f[1] for f in FAILS])
def test_piotroski_each_component_can_fail(piotroski, over, component):
    r = piotroski(**over)
    assert r["component_scores"][component] == 0
    assert r["f_score"] == sum(r["component_scores"].values())


@pytest.mark.parametrize("over,component", [
    ({"Net Income": [80.0, 80.0]}, "F3_ROA_improving"),
    ({"Long Term Debt": [200.0, 200.0]}, "F5_Leverage_down"),
    ({"Current Assets": [400.0, 400.0]}, "F6_CurrentRatio_up"),
    ({"Gross Profit": [330.0, 330.0], "Total Revenue": [900.0, 900.0]}, "F8_GrossMargin_up"),
    ({"Total Revenue": [900.0, 900.0]}, "F9_AssetTurnover_up"),
])
def test_piotroski_ties_are_not_improvements(piotroski, over, component):
    assert piotroski(**over)["component_scores"][component] == 0


def test_piotroski_share_count_equal_is_no_dilution(piotroski):
    assert piotroski(**{"Share Issued": [10.0, 10.0]})["component_scores"]["F7_No_dilution"] == 1


def test_piotroski_missing_prior_year_zeroes_delta_criteria(piotroski, india_mod, monkeypatch):
    inc, bal, cf = statements()
    one = lambda d: d.iloc[:, :1]
    monkeypatch.setattr(india_mod, "YFINANCE_OK", True)
    monkeypatch.setattr(india_mod, "yf", types.SimpleNamespace(Ticker=lambda s: types.SimpleNamespace(info={})),
                        raising=False)
    monkeypatch.setattr(india_mod, "_yf_financials", lambda t: (one(inc), one(bal), one(cf)))
    r = india_mod.compute_piotroski_score("RELI")
    c = r["component_scores"]
    assert (c["F3_ROA_improving"], c["F5_Leverage_down"], c["F6_CurrentRatio_up"], c["F8_GrossMargin_up"],
            c["F9_AssetTurnover_up"]) == (0, 0, 0, 0, 0)
    assert c["F7_No_dilution"] == 1                      # absent history is assumed non-dilutive
    assert r["details"]["ROA_prev_%"] == "N/A" and r["details"]["CurrentRatio_prev"] == "N/A"


def test_piotroski_shares_fall_back_to_info_and_missing_data(piotroski):
    r = piotroski(info={"sharesOutstanding": 11.0}, **{"Share Issued": None})
    assert r["component_scores"]["F7_No_dilution"] == 1            # sh_1 missing -> assumed fine
    r = piotroski(**{"Total Assets": None})
    assert r["component_scores"]["F1_ROA_positive"] == 0 and r["details"]["ROA_current_%"] == "N/A"
    assert r["component_scores"]["F4_Accruals"] == 0 and r["details"]["LTD_ratio_curr_%"] == "N/A"


INDEPENDENT_BREAKERS = [        # applied cumulatively; each one removes exactly one more point
    {"Long Term Debt": [300.0, 200.0]},            # F5
    {"Current Assets": [300.0, 400.0]},            # F6
    {"Share Issued": [12.0, 10.0]},                # F7
    {"Gross Profit": [300.0, 330.0]},              # F8
    {"Net Income": [70.0, 80.0]},                  # F3
    {"Operating Cash Flow": [60.0, 120.0]},        # F4 (OCF/assets .06 < ROA .07)
    {"Operating Cash Flow": [-1.0, 120.0]},        # F2 (also keeps F4 at 0)
]


@pytest.mark.parametrize("failures,score,band", [(0, 9, "STRONG"), (2, 7, "STRONG"), (3, 6, "MODERATE"),
                                                 (5, 4, "MODERATE"), (6, 3, "WEAK"), (7, 2, "WEAK")])
def test_piotroski_interpretation_band_boundaries(piotroski, failures, score, band):
    over = {}
    for b in INDEPENDENT_BREAKERS[:failures]:
        over.update(b)
    r = piotroski(**over)
    assert r["f_score"] == score
    assert r["interpretation"].startswith(band)


def test_piotroski_error_paths(india_mod, monkeypatch):
    monkeypatch.setattr(india_mod, "YFINANCE_OK", True)
    monkeypatch.setattr(india_mod, "yf", types.SimpleNamespace(Ticker=lambda s: types.SimpleNamespace(info={})),
                        raising=False)
    monkeypatch.setattr(india_mod, "_yf_financials", lambda t: (None, None, None))
    assert "No financial data found for RELI.NS" in india_mod.compute_piotroski_score("RELI")["error"]
    monkeypatch.setattr(india_mod, "_yf_financials", lambda t: (pd.DataFrame(), None, None))
    assert "No financial data" in india_mod.compute_piotroski_score("RELI")["error"]


def test_display_piotroski_india(india_mod, capsys, piotroski):
    r = piotroski()
    india_mod.display_piotroski_score(r)
    out = capsys.readouterr().out
    assert "9/9" in out and "STRONG" in out and "F1  ROA > 0" in out and out.count("✔") == 9
    india_mod.display_piotroski_score(dict(r, f_score=5))
    assert "5/9" in capsys.readouterr().out
    india_mod.display_piotroski_score({"error": "nope"})
    assert "nope" in capsys.readouterr().out


# ---- Darvas display & edge cases ------------------------------------------------------------

@pytest.mark.parametrize("sig,text", [("BREAKOUT_BUY", "BREAKOUT BUY"), ("BREAKDOWN_SELL", "BREAKDOWN SELL"),
                                      ("IN_BOX", "IN BOX"), ("NO_BOX", "No confirmed Darvas box found"),
                                      ("INSUFFICIENT_DATA", "Insufficient OHLC data"), ("WEIRD", "WEIRD")])
def test_display_darvas_signals(india_mod, capsys, sig, text):
    india_mod.display_darvas_box({"signal": sig})
    assert text in capsys.readouterr().out


def test_display_darvas_with_box_and_note(india_mod, capsys):
    india_mod.display_darvas_box({"signal": "IN_BOX", "box_top": 110.0, "box_bottom": 90.0, "current_price": 100.0,
                                  "box_range": 20.0, "position_in_box_pct": 50.0, "upside_to_top_pct": 10.0,
                                  "data_points": 60})
    out = capsys.readouterr().out
    assert "₹110.00" in out and "₹90.00" in out and "50.0%" in out and "10.00%" in out and "60" in out
    assert "Confirmation (days)" in out and " 3\n" in out                      # default DARVAS_CONFIRM
    india_mod.display_darvas_box({"signal": "NO_BOX", "note": "too short"})
    assert "Note: too short" in capsys.readouterr().out
    india_mod.display_darvas_box({})
    assert "N/A" in capsys.readouterr().out


def bars(highs, lows, closes):
    return pd.DataFrame({"High": highs, "Low": lows, "Close": closes})


def test_darvas_nan_low_is_never_the_box_bottom(india_mod):
    n = 12
    highs = [9.0] * n
    highs[2] = 10.0
    lows = [8.0] * n
    lows[4] = float("nan")                         # a missing low must not become a 0.0 box bottom
    closes = [8.5] * (n - 1) + [9.0]
    r = india_mod.compute_darvas_box(bars(highs, lows, closes))
    assert r["box_bottom"] == 8.0 and r["signal"] == "IN_BOX"


def test_darvas_all_lows_missing_gives_no_box(india_mod):
    n = 12
    highs = [9.0] * n
    highs[2] = 10.0
    r = india_mod.compute_darvas_box(bars(highs, [float("nan")] * n, [8.5] * n))
    assert r["signal"] == "NO_BOX" and r["box_top"] == 10.0 and r["box_bottom"] is None


def test_darvas_confirmed_bottom_beats_overall_minimum(india_mod):
    # lows: a confirmed local floor (7.0 held by the 3 higher lows after it) sits above a later dip to 5.0
    highs = [9.0, 9.0, 12.0, 9.0, 9.0, 9.0, 9.0, 9.0, 9.0, 9.0, 9.0, 9.0]
    lows = [8.0, 8.0, 8.0, 7.0, 7.5, 7.6, 7.7, 5.0, 8.0, 8.0, 8.0, 8.0]
    r = india_mod.compute_darvas_box(bars(highs, lows, [8.5] * 11 + [9.0]))
    assert r["box_bottom"] == 7.0 and r["box_top"] == 12.0

"""MoatAnalyzer indicator thresholds with a fake yfinance Ticker (no network).

The analyzer reads statements as rows = periods (newest first) and columns = line items.
"""
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

PATH = Path(__file__).resolve().parents[1] / "05_enrichment" / "moat_analyzer.py"


@pytest.fixture(scope="module")
def moat_module():
    # The module defines a dataclass under `from __future__ import annotations`, so it must be
    # registered in sys.modules while it executes (the shared load_script fixture does not do that).
    name = "cst_moat_analyzer"
    spec = importlib.util.spec_from_file_location(name, PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
        yield mod
    finally:
        sys.modules.pop(name, None)


@pytest.fixture
def mk(moat_module, monkeypatch):
    mod = moat_module

    def make(info=None, fi=None, bs=None):
        class T:
            pass
        t = T()
        t.info, t.income_stmt, t.balance_sheet = info or {}, fi, bs
        monkeypatch.setattr(mod.yf, "Ticker", lambda ticker: t)
        return mod.MoatAnalyzer("TST")
    make.mod = mod
    return make


def fi(rows):
    return pd.DataFrame(rows)


def revs(*pairs):
    return fi([{"Total Revenue": r, "Cost of Revenue": c, "Net Income": 0} for r, c in pairs])


# ---- margin stability ---------------------------------------------------------

def test_margin_needs_three_periods(mk):
    r = mk(fi=revs((100, 50), (100, 50))).analyze_margin_stability()
    assert (r.signal, r.strength, r.explanation) == ("UNKNOWN", 1, "Insufficient data")
    assert mk(fi=None).analyze_margin_stability().signal == "UNKNOWN"


def test_margin_no_revenue(mk):
    r = mk(fi=revs((0, 0), (0, 0), (0, 0))).analyze_margin_stability()
    assert (r.signal, r.explanation) == ("UNKNOWN", "No revenue data")


@pytest.mark.parametrize("worst_cogs,signal,strength,value", [
    (50.0, "BUY", 5, 1.0),         # perfectly stable
    (52.0, "BUY", 4, 0.96),        # stability .96 > .95
    (54.0, "HOLD", 4, 0.92),       # .95 >= s > .85
    (60.0, "AVOID", 4, 0.8),       # eroding (strength still int(0.8 * 5))
])
def test_margin_signals(mk, worst_cogs, signal, strength, value):
    # best margin 50%, worst margin = 1 - worst_cogs/100
    r = mk(fi=revs((100, 50), (100, 50), (100, worst_cogs))).analyze_margin_stability()
    assert r.metric == "margin_stability"
    assert r.signal == signal and r.strength == strength
    assert r.value == pytest.approx(value, abs=0.05)


def test_margin_boundaries(mk):
    # stability exactly 0.95 is HOLD (strict >), exactly 0.85 is AVOID (strict >)
    assert mk(fi=revs((100, 50), (100, 50), (100, 52.5))).analyze_margin_stability().signal == "HOLD"
    assert mk(fi=revs((100, 20), (100, 20), (100, 32))).analyze_margin_stability().signal == "AVOID"
    assert mk(fi=revs((100, 20), (100, 20), (100, 31))).analyze_margin_stability().signal == "HOLD"


def test_margin_uses_at_most_five_periods_and_skips_zero_revenue(mk):
    rows = [(100, 50)] * 5 + [(100, 99)]                       # 6th period ignored
    assert mk(fi=revs(*rows)).analyze_margin_stability().signal == "BUY"
    rows = [(0, 0), (100, 50), (100, 50)]                      # zero revenue skipped
    assert mk(fi=revs(*rows)).analyze_margin_stability().signal == "BUY"


def test_margin_all_negative_margins_is_zero_stability(mk):
    r = mk(fi=revs((100, 150), (100, 150), (100, 150))).analyze_margin_stability()
    assert r.value == 0 and r.signal == "AVOID" and r.strength == 0


# ---- ROE ------------------------------------------------------------------------

def roe_frames(ni, eq):
    inc = fi([{"Net Income": n, "Total Revenue": 1} for n in ni])
    bal = fi([{"Total Equity": e} for e in eq])
    return inc, bal


def test_roe_insufficient_data(mk):
    inc, bal = roe_frames([10, 10], [100, 100])
    assert mk(fi=inc, bs=bal).analyze_roe_consistency().explanation == "Insufficient data"
    assert mk(fi=inc, bs=None).analyze_roe_consistency().signal == "UNKNOWN"


def test_roe_needs_two_positive_equity_periods(mk):
    inc, bal = roe_frames([10, 10, 10], [100, -5, -5])
    r = mk(fi=inc, bs=bal).analyze_roe_consistency()
    assert r.explanation == "Insufficient history" and r.signal == "UNKNOWN"


@pytest.mark.parametrize("ni,signal,strength", [
    ([25, 25, 25], "BUY", 5),          # 25% steady
    ([18, 18, 18], "HOLD", 5),         # 18% steady, int(.18*33)=5
    ([10, 10, 10], "AVOID", 3),        # 10%
    ([30, 21, 10], "AVOID", 5),        # high average but erratic
])
def test_roe_signals(mk, ni, signal, strength):
    inc, bal = roe_frames(ni, [100, 100, 100])
    r = mk(fi=inc, bs=bal).analyze_roe_consistency()
    assert r.signal == signal and r.strength == strength


def test_roe_thresholds_are_strict(mk):
    inc, bal = roe_frames([20] * 4, [100] * 4)                 # avg exactly 0.20 -> not BUY
    assert mk(fi=inc, bs=bal).analyze_roe_consistency().signal == "HOLD"
    inc, bal = roe_frames([15] * 4, [100] * 4)                 # avg exactly 0.15 -> not HOLD
    assert mk(fi=inc, bs=bal).analyze_roe_consistency().signal == "AVOID"


def test_roe_consistency_threshold(mk):
    # avg 0.2 with min/avg exactly 0.9 -> not BUY (strict), HOLD
    inc, bal = roe_frames([18, 20, 22], [100, 100, 100])
    assert mk(fi=inc, bs=bal).analyze_roe_consistency().signal == "HOLD"


def test_roe_missing_balance_rows_default_equity_one(mk):
    inc = fi([{"Net Income": 0.2}, {"Net Income": 0.2}, {"Net Income": 0.2}])
    bal = fi([{"Total Equity": 1}])                            # shorter than income history
    r = mk(fi=inc, bs=bal).analyze_roe_consistency()
    assert r.value == pytest.approx(0.2) and r.signal == "BUY"


# ---- market dominance ----------------------------------------------------------

@pytest.mark.parametrize("mcap,sig,strength", [
    (299, "BUY", 5), (300, "HOLD", 4), (499, "HOLD", 4), (500, "HOLD", 2),
    (999, "HOLD", 2), (1000, "AVOID", 1),
])
def test_market_dominance_price_to_sales_bands(mk, mcap, sig, strength):
    r = mk(info={"marketCap": mcap}, fi=fi([{"Total Revenue": 100}])).analyze_market_dominance()
    assert (r.signal, r.strength) == (sig, strength)
    assert r.value == mcap / 100


def test_market_dominance_unknown_without_inputs(mk):
    assert mk(info={}, fi=fi([{"Total Revenue": 100}])).analyze_market_dominance().signal == "UNKNOWN"
    assert mk(info={"marketCap": 5}, fi=None).analyze_market_dominance().signal == "UNKNOWN"
    assert mk(info={"marketCap": 5}, fi=fi([{"Total Revenue": 0}])).analyze_market_dominance().signal == "UNKNOWN"


# ---- asset efficiency ----------------------------------------------------------

@pytest.mark.parametrize("rev,sig,strength", [
    (250, "BUY", 5), (200, "HOLD", 4), (151, "HOLD", 4), (150, "HOLD", 2),
    (101, "HOLD", 2), (100, "AVOID", 1), (50, "AVOID", 1),
])
def test_asset_efficiency_bands(mk, rev, sig, strength):
    r = mk(fi=fi([{"Total Revenue": rev}]), bs=fi([{"Total Assets": 100}])).analyze_asset_efficiency()
    assert (r.signal, r.strength) == (sig, strength)
    assert r.value == rev / 100


def test_asset_efficiency_unknown_and_zero_assets(mk):
    assert mk(fi=None, bs=fi([{"Total Assets": 1}])).analyze_asset_efficiency().signal == "UNKNOWN"
    assert mk(fi=fi([{"Total Revenue": 1}]), bs=None).analyze_asset_efficiency().signal == "UNKNOWN"
    assert mk(fi=fi([]), bs=fi([{"Total Assets": 1}])).analyze_asset_efficiency().signal == "UNKNOWN"
    r = mk(fi=fi([{"Total Revenue": 1}]), bs=fi([{"Total Assets": 0}])).analyze_asset_efficiency()
    assert r.value == 0 and r.signal == "AVOID"


# ---- dividends -----------------------------------------------------------------

@pytest.mark.parametrize("info,sig,strength", [
    ({}, "NEUTRAL", 2),
    ({"dividendYield": 0}, "NEUTRAL", 2),
    ({"dividendYield": 0.05, "payoutRatio": 0.8}, "CAUTION", 1),
    ({"dividendYield": 0.04, "payoutRatio": 0.5}, "BUY", 4),
    ({"dividendYield": 0.03, "payoutRatio": 0.5}, "HOLD", 3),   # yield must be > 3%
    ({"dividendYield": 0.04, "payoutRatio": 0.6}, "HOLD", 3),   # payout must be < 60%
    ({"dividendYield": 0.04, "payoutRatio": 0.7}, "HOLD", 3),   # payout > 70% is the caution cutoff
    ({"dividendYield": 0.04, "payoutRatio": 0.71}, "CAUTION", 1),
])
def test_dividend_policy(mk, info, sig, strength):
    r = mk(info=info).analyze_dividend_policy()
    assert (r.signal, r.strength) == (sig, strength)


# ---- overall -------------------------------------------------------------------

def strong_company(mk, **kw):
    inc = fi([{"Total Revenue": 100, "Cost of Revenue": 50, "Net Income": 30}] * 3)
    bal = fi([{"Total Equity": 100, "Total Assets": 40}] * 3)       # turnover 2.5
    info = {"marketCap": 200, "dividendYield": 0.05, "payoutRatio": 0.4}
    info.update(kw)
    return mk(info=info, fi=inc, bs=bal)


def test_run_analysis_strong_moat(mk):
    res = strong_company(mk).run_analysis()
    assert res["ticker"] == "TST"
    assert res["overall_signal"] == "STRONG MOAT"
    assert res["moat_rating"] == pytest.approx(round((5 + 5 + 5 + 5 + 4) / 5, 2))
    assert [i.metric for i in res["indicators"]] == [
        "margin_stability", "roe_consistency", "market_dominance", "asset_efficiency", "dividend_policy"]


def test_run_analysis_moderate_and_weak(mk):
    # margin + asset-light BUY (2), ROE AVOID (1), market HOLD -> MODERATE
    inc = fi([{"Total Revenue": 100, "Cost of Revenue": 50, "Net Income": 1}] * 3)
    bal = fi([{"Total Equity": 100, "Total Assets": 40}] * 3)
    res = mk(info={"marketCap": 700}, fi=inc, bs=bal).run_analysis()
    assert [i.signal for i in res["indicators"]] == ["BUY", "AVOID", "HOLD", "BUY", "NEUTRAL"]
    assert res["overall_signal"] == "MODERATE MOAT"
    inc = fi([{"Total Revenue": 100, "Cost of Revenue": 80, "Net Income": 1}] * 3)
    bal = fi([{"Total Equity": 100, "Total Assets": 400}] * 3)
    weak = mk(info={"marketCap": 5000}, fi=inc, bs=bal).run_analysis()
    assert weak["overall_signal"] == "WEAK MOAT"


def test_run_analysis_all_unknown_rates_zero_for_unknown_only(mk):
    res = mk(info={}, fi=None, bs=None).run_analysis()
    # margin/roe/market/asset are UNKNOWN with strength 1; dividend NEUTRAL with 2
    assert res["moat_rating"] == pytest.approx(round((1 + 1 + 1 + 1 + 2) / 5, 2))
    assert res["overall_signal"] == "MODERATE MOAT"


def test_print_analysis_output(mk, capsys):
    strong_company(mk).print_analysis()
    out = capsys.readouterr().out
    assert "MOAT ANALYSIS: TST" in out
    assert "Moat Strength: 4.8/5.0 (STRONG MOAT)" in out
    assert "MARGIN STABILITY" in out and "★" in out
    assert "→ Margins rock-solid" in out

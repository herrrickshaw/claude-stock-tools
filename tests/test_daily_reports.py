"""Formatting helpers, Piotroski, Coffee Can and displays of the three daily-report scripts
(India / US / Singapore), using hand-built statements and a fake yfinance Ticker."""
import importlib.util
import os
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "india": "01_daily_reports/india_daily_report.py",
    "us": "01_daily_reports/us_daily_report.py",
    "sg": "01_daily_reports/singapore_daily_report.py",
}
COLS = [pd.Timestamp("2024-03-31"), pd.Timestamp("2023-03-31")]


@pytest.fixture(scope="module")
def mods(tmp_path_factory):
    """Load the three scripts by path from inside a temp CWD (they create output dirs on import),
    with the India-only network libraries stubbed. sys.modules is restored afterwards."""
    stubs = {}
    for name in ("nse", "bse", "nsepython"):
        m = types.ModuleType(name)
        stubs[name] = m
    stubs["nse"].NSE = type("NSE", (), {})
    stubs["bse"].BSE = type("BSE", (), {})
    saved = {n: sys.modules.get(n) for n in stubs}
    sys.modules.update(stubs)
    work = tmp_path_factory.mktemp("daily_cwd")
    old = os.getcwd()
    os.chdir(work)
    loaded = {}
    try:
        for key, rel in FILES.items():
            spec = importlib.util.spec_from_file_location("cst_daily_" + key, ROOT / rel)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            loaded[key] = mod
    finally:
        os.chdir(old)
        for n, m in saved.items():
            if m is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = m
    return loaded


def stmt(rows):
    return pd.DataFrame({c: [v[i] if i < len(v) else None for v in rows.values()]
                         for i, c in enumerate(COLS)}, index=list(rows))


def statements(**over):
    inc = {"Net Income": [100.0, 80.0], "Total Revenue": [1000.0, 900.0],
           "Gross Profit": [400.0, 330.0], "EBIT": [200.0, 180.0]}
    bal = {"Total Assets": [1000.0, 1000.0], "Long Term Debt": [100.0, 200.0],
           "Current Assets": [500.0, 400.0], "Current Liabilities": [250.0, 250.0],
           "Share Issued": [10.0, 10.0], "Stockholders Equity": [500.0, 450.0]}
    cf = {"Operating Cash Flow": [150.0, 120.0], "Free Cash Flow": [90.0, 60.0]}
    for d in (inc, bal, cf):
        for k in list(d):
            if k in over:
                v = over[k]
                if v is None:
                    del d[k]
                else:
                    d[k] = v
    return stmt(inc), stmt(bal), stmt(cf)


class FakeTicker:
    def __init__(self, inc, bal, cf, info=None, fast_mcap=None):
        self.income_stmt, self.balance_sheet, self.cash_flow = inc, bal, cf
        self.info = info if info is not None else {"marketCap": 5e9, "debtToEquity": 45}
        if fast_mcap is not None:
            self.fast_info = types.SimpleNamespace(market_cap=fast_mcap)


def piotroski(mods, monkeypatch, kind, ticker):
    m = mods[kind]
    if kind == "india":
        monkeypatch.setattr(m.yf, "Ticker", lambda s: ticker, raising=False)
        monkeypatch.setattr(m, "YFINANCE_OK", True)
        return m.compute_piotroski_score("RELI")
    return m.compute_piotroski_score(ticker, "RELI")


def coffee(mods, monkeypatch, kind, ticker):
    m = mods[kind]
    if kind == "india":
        monkeypatch.setattr(m.yf, "Ticker", lambda s: ticker, raising=False)
        monkeypatch.setattr(m, "YFINANCE_OK", True)
        return m.compute_coffee_can("RELI")
    return m.compute_coffee_can(ticker, "RELI")


def ticker(**over):
    # India's _yf_financials uses `df or df`, which raises on a non-empty DataFrame; keep the fake
    # statements reachable through attributes only so the shared helpers below can still be tested.
    inc, bal, cf = statements(**over)
    return FakeTicker(inc, bal, cf)


PIO_KINDS = ["us", "sg"]


# ---- formatting helpers ----------------------------------------------------------------

@pytest.mark.parametrize("kind,prefix", [("india", "₹"), ("us", "$"), ("sg", "S$")])
def test_fmt(mods, kind, prefix):
    f = mods[kind].fmt
    assert f(1234567.891) == f"{prefix}1,234,567.89"
    assert f("12.5", decimals=1) == f"{prefix}12.5"
    assert f(None) == "N/A" and f(0) == f"{prefix}0.00"
    assert f("abc") == "abc"            # unparseable but truthy values pass through
    assert f("") == "N/A"
    assert f(5, prefix="") == "5.00"


@pytest.mark.parametrize("kind", ["india", "us", "sg"])
def test_pct(mods, kind):
    p = mods[kind].pct
    assert p(1.234) == "\033[92m▲ 1.23%\033[0m"
    assert p(-1.234) == "\033[91m▼ 1.23%\033[0m"
    assert p(0) == "\033[92m▲ 0.00%\033[0m"            # zero counts as up
    assert p("x") == "N/A" and p(None) == "N/A"


@pytest.mark.parametrize("kind,prefix", [("us", "$"), ("sg", "S$")])
@pytest.mark.parametrize("value,expected", [
    (4.48e12, "4.48T"), (1e12, "1.00T"), (999e9, "999.00B"), (1e9, "1.00B"), (999_999_999, "1000.00M"),
    (182.5e6, "182.50M"), (1e6, "1.00M"), (12_345, "12.35K"), (1e3, "1.00K"),
    (999, "999.00"), (-2.5e9, "-2.50B"),
])
def test_fmt_large(mods, kind, prefix, value, expected):
    assert mods[kind].fmt_large(value) == f"{prefix}{expected}"


@pytest.mark.parametrize("kind", ["us", "sg"])
def test_fmt_large_bad_input(mods, kind):
    assert mods[kind].fmt_large(None) == "N/A"
    assert mods[kind].fmt_large("zzz") == "N/A"
    assert mods[kind].fmt_large(5, prefix="") == "5.00"


@pytest.mark.parametrize("kind,width", [("india", 28), ("us", 30), ("sg", 30)])
def test_section_and_row(mods, capsys, kind, width):
    m = mods[kind]
    m.section("price data")
    assert capsys.readouterr().out == f"\n{'-' * 60}\n  PRICE DATA\n{'-' * 60}\n"
    m.row("Label", "value")
    assert capsys.readouterr().out == f"  {'Label':<{width}} value\n"
    m.row("Label", "value", width=10)
    assert capsys.readouterr().out == "  Label      value\n"


# ---- _row / _yf_financials --------------------------------------------------------------

@pytest.mark.parametrize("kind", ["india", "us", "sg"])
def test_row_helper(mods, kind):
    inc, _, _ = statements()
    r = mods[kind]._row
    assert r(inc, "Net Income") == 100.0
    assert r(inc, "Net Income", col=1) == 80.0
    assert r(inc, "Missing", "Net Income", col=1) == 80.0     # first matching name wins
    assert r(inc, "Missing") is None
    assert r(inc, "Net Income", col=5) is None                # out of range
    assert r(None, "Net Income") is None and r(pd.DataFrame(), "Net Income") is None
    nan = stmt({"Net Income": [float("nan"), 1.0]})
    assert r(nan, "Net Income") is None


@pytest.mark.parametrize("kind", ["us", "sg"])
def test_yf_financials_picks_first_non_empty_frame(mods, kind):
    inc, bal, cf = statements()
    t = types.SimpleNamespace(income_stmt=pd.DataFrame(), financials=inc, balance_sheet=bal,
                              cash_flow=None, cashflow=cf)
    got = mods[kind]._yf_financials(t)
    assert got[0] is inc and got[1] is bal and got[2] is cf
    none = mods[kind]._yf_financials(types.SimpleNamespace())
    assert none == (None, None, None)


@pytest.mark.xfail(strict=True, raises=ValueError,
                   reason="india_daily_report._yf_financials uses `getattr(...) or getattr(...)`; `or` on a "
                          "non-empty DataFrame raises 'truth value of a DataFrame is ambiguous' (US/SG copies "
                          "were fixed with explicit None/empty checks)")
def test_india_yf_financials_with_real_dataframes(mods):
    inc, bal, cf = statements()
    t = types.SimpleNamespace(income_stmt=inc, balance_sheet=bal, cash_flow=cf)
    assert mods["india"]._yf_financials(t)[0] is inc


def test_india_yf_financials_falls_back_to_old_attribute_names(mods):
    t = types.SimpleNamespace(income_stmt=None, financials="F", balance_sheet="B", cash_flow=None, cashflow="C")
    assert mods["india"]._yf_financials(t) == ("F", "B", "C")


# ---- Piotroski -------------------------------------------------------------------------

@pytest.mark.parametrize("kind", PIO_KINDS)
def test_piotroski_all_nine(mods, monkeypatch, kind):
    r = piotroski(mods, monkeypatch, kind, ticker())
    assert r["f_score"] == 9 and r["symbol"] == "RELI"
    assert r["interpretation"].startswith("STRONG")
    assert set(r["component_scores"]) == {
        "F1_ROA_positive", "F2_OCF_positive", "F3_ROA_improving", "F4_Accruals", "F5_Leverage_down",
        "F6_CurrentRatio_up", "F7_No_dilution", "F8_GrossMargin_up", "F9_AssetTurnover_up"}
    d = r["details"]
    assert d["ROA_current_%"] == 10.0
    assert d["CurrentRatio_curr"] == 2.0
    assert d["GrossMargin_curr_%"] == 40.0
    if kind == "us":
        assert d["ROA_prev_%"] == 8.0
        assert d["LTD_ratio_curr_%"] == 10.0 and d["LTD_ratio_prev_%"] == 20.0
        assert d["CurrentRatio_prev"] == 1.6
        assert d["GrossMargin_prev_%"] == pytest.approx(36.67)
        assert d["AssetTurnover_curr"] == 1.0 and d["AssetTurnover_prev"] == 0.9


# (statement overrides) -> the single component that must flip to 0 and the resulting score
PIO_FAILS = [
    ({"Net Income": [-5.0, -10.0]}, "F1_ROA_positive", None),
    ({"Operating Cash Flow": [-1.0, 120.0]}, "F2_OCF_positive", None),
    ({"Net Income": [70.0, 80.0]}, "F3_ROA_improving", None),
    ({"Operating Cash Flow": [90.0, 120.0]}, "F4_Accruals", None),
    ({"Long Term Debt": [300.0, 200.0]}, "F5_Leverage_down", None),
    ({"Current Assets": [300.0, 400.0]}, "F6_CurrentRatio_up", None),
    ({"Share Issued": [12.0, 10.0]}, "F7_No_dilution", None),
    ({"Gross Profit": [300.0, 330.0]}, "F8_GrossMargin_up", None),
    ({"Total Revenue": [800.0, 900.0]}, "F9_AssetTurnover_up", None),
]


@pytest.mark.parametrize("kind", PIO_KINDS)
@pytest.mark.parametrize("over,component,_", PIO_FAILS, ids=[f[1] for f in PIO_FAILS])
def test_piotroski_each_criterion_can_fail(mods, monkeypatch, kind, over, component, _):
    r = piotroski(mods, monkeypatch, kind, ticker(**over))
    assert r["component_scores"][component] == 0
    others = {k: v for k, v in r["component_scores"].items() if k != component}
    # a failing criterion never changes unrelated ones except through the derived ROA/ratios noted here
    assert r["f_score"] == sum(r["component_scores"].values())
    assert r["f_score"] < 9
    if component in ("F7_No_dilution", "F5_Leverage_down", "F6_CurrentRatio_up"):
        assert all(v == 1 for v in others.values())


@pytest.mark.parametrize("kind", PIO_KINDS)
def test_piotroski_equal_values_do_not_count_as_improvement(mods, monkeypatch, kind):
    r = piotroski(mods, monkeypatch, kind, ticker(**{"Net Income": [80.0, 80.0]}))
    assert r["component_scores"]["F3_ROA_improving"] == 0
    r = piotroski(mods, monkeypatch, kind, ticker(**{"Long Term Debt": [200.0, 200.0]}))
    assert r["component_scores"]["F5_Leverage_down"] == 0
    r = piotroski(mods, monkeypatch, kind, ticker(**{"Current Assets": [400.0, 400.0]}))
    assert r["component_scores"]["F6_CurrentRatio_up"] == 0
    r = piotroski(mods, monkeypatch, kind, ticker(**{"Share Issued": [10.0, 10.0]}))
    assert r["component_scores"]["F7_No_dilution"] == 1          # unchanged share count is not dilution


@pytest.mark.parametrize("kind", PIO_KINDS)
def test_piotroski_interpretation_bands(mods, monkeypatch, kind):
    # score 4 -> MODERATE, score 3 -> WEAK
    four = {"Net Income": [70.0, 80.0], "Operating Cash Flow": [60.0, 120.0], "Long Term Debt": [300.0, 200.0],
            "Current Assets": [300.0, 400.0], "Gross Profit": [300.0, 330.0]}
    r = piotroski(mods, monkeypatch, kind, ticker(**four))
    assert r["f_score"] == 4 and r["interpretation"].startswith("MODERATE")
    three = dict(four, **{"Share Issued": [12.0, 10.0]})
    r = piotroski(mods, monkeypatch, kind, ticker(**three))
    assert r["f_score"] == 3 and r["interpretation"].startswith("WEAK")
    seven = {"Net Income": [70.0, 80.0], "Operating Cash Flow": [60.0, 120.0]}
    r = piotroski(mods, monkeypatch, kind, ticker(**seven))
    assert r["f_score"] == 7 and r["interpretation"].startswith("STRONG")


@pytest.mark.parametrize("kind", PIO_KINDS)
def test_piotroski_no_income_statement(mods, monkeypatch, kind):
    t = FakeTicker(pd.DataFrame(), *statements()[1:])
    r = piotroski(mods, monkeypatch, kind, t)
    assert r["error"] == "No income statement data available" and r["symbol"] == "RELI"


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="Missing share counts earn the F7 'no dilution' point (scores[...] = 1 'assume no "
                          "dilution when data absent'); the scanners and README treat missing fundamentals as "
                          "not passing, and test_fundamental_scan pins F7 = 0 for them")
@pytest.mark.parametrize("kind", PIO_KINDS)
def test_piotroski_missing_share_count_does_not_earn_f7(mods, monkeypatch, kind):
    r = piotroski(mods, monkeypatch, kind, ticker(**{"Share Issued": None}))
    assert r["component_scores"]["F7_No_dilution"] == 0


@pytest.mark.parametrize("kind", PIO_KINDS)
def test_piotroski_missing_prior_year_zeroes_delta_criteria(mods, monkeypatch, kind):
    inc, bal, cf = statements()
    one_year = lambda df: df[[COLS[0]]]  # noqa: E731
    r = piotroski(mods, monkeypatch, kind, FakeTicker(one_year(inc), one_year(bal), one_year(cf)))
    s = r["component_scores"]
    assert (s["F1_ROA_positive"], s["F2_OCF_positive"], s["F4_Accruals"]) == (1, 1, 1)
    assert (s["F3_ROA_improving"], s["F5_Leverage_down"], s["F6_CurrentRatio_up"],
            s["F8_GrossMargin_up"], s["F9_AssetTurnover_up"]) == (0, 0, 0, 0, 0)


def test_india_piotroski_error_paths(mods, monkeypatch):
    m = mods["india"]
    monkeypatch.setattr(m, "YFINANCE_OK", False)
    assert "yfinance not installed" in m.compute_piotroski_score("X")["error"]
    monkeypatch.setattr(m, "YFINANCE_OK", True)
    monkeypatch.setattr(m.yf, "Ticker", lambda s: (_ for _ in ()).throw(RuntimeError("boom")), raising=False)
    assert m.compute_piotroski_score("X")["error"] == "yfinance error: boom"
    # the `or` between DataFrames is also what makes every real India lookup land in this branch
    monkeypatch.setattr(m.yf, "Ticker", lambda s: ticker(), raising=False)
    assert m.compute_piotroski_score("X")["error"].startswith("yfinance error:")


# ---- Coffee Can ------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["india", "us"])
def test_coffee_can_ok_when_sources_are_reachable(mods, monkeypatch, kind):
    t = ticker()
    if kind == "india":
        # India's _yf_financials cannot take DataFrames (see xfail above); inject the frames directly
        monkeypatch.setattr(mods["india"], "_yf_financials", lambda tk: (t.income_stmt, t.balance_sheet, t.cash_flow))
        t.info = {"marketCap": 1e10, "debtToEquity": 45}
    r = coffee(mods, monkeypatch, kind, t)
    assert r["qualifies"] is True
    assert r["score"] == ("5/5" if kind == "india" else "6/6")
    assert r["details"]["Revenue_CAGR_%"] == pytest.approx(11.11, abs=0.01)
    assert r["details"]["Debt_to_Equity"] == 0.45


def reach(mods, monkeypatch, kind, t):
    if kind == "india":
        monkeypatch.setattr(mods["india"], "_yf_financials", lambda tk: (t.income_stmt, t.balance_sheet, t.cash_flow))
    return coffee(mods, monkeypatch, kind, t)


CC_FAILS = [
    # (kind-agnostic overrides, info, criterion substring)
    ({"Total Revenue": [1000.0, 950.0]}, None, "C1"),           # CAGR 5.3%
    ({"Net Income": [-1.0, 80.0]}, None, "C5"),                   # a loss year
    ({}, {"marketCap": 5e8, "debtToEquity": 45}, "mcap"),         # below size cut-off (US 1B / India 500 Cr)
    ({}, {"marketCap": 5e9, "debtToEquity": 120}, "C3"),          # D/E 1.2
]


@pytest.mark.parametrize("kind", ["india", "us"])
@pytest.mark.parametrize("over,info,which", CC_FAILS, ids=[c[2] for c in CC_FAILS])
def test_coffee_can_each_criterion_can_fail(mods, monkeypatch, kind, over, info, which):
    t = ticker(**over)
    if info is not None:
        t.info = dict(info)
    if kind == "india":
        t.info = dict(t.info, marketCap=t.info["marketCap"] * (2 if which != "mcap" else 1) * 1e-0)
        if which == "mcap":
            t.info["marketCap"] = 4e9                               # 400 Cr < 500 Cr
        else:
            t.info["marketCap"] = 1e10
    r = reach(mods, monkeypatch, kind, t)
    assert r["qualifies"] is False
    failed = [k for k, v in r["criteria"].items() if v == 0]
    if which != "C5":
        assert len(failed) == 1                                     # a loss year also drags average ROE/ROCE down
    key = {"C1": "C1", "C5": "C5", "C3": "C3", "mcap": "C4"}[which]
    assert any(f.startswith(key) for f in failed)


@pytest.mark.parametrize("kind", ["india", "us"])
def test_coffee_can_cagr_threshold(mods, monkeypatch, kind):
    for revs, expected in (([1099.0, 1000.0], 0), ([1101.0, 1000.0], 1)):
        t = ticker(**{"Total Revenue": revs})
        t.info = {"marketCap": 1e10, "debtToEquity": 45}
        r = reach(mods, monkeypatch, kind, t)
        assert r["criteria"][[k for k in r["criteria"] if k.startswith("C1")][0]] == expected


@pytest.mark.parametrize("kind", ["india", "us"])
def test_coffee_can_debt_to_equity_fallbacks(mods, monkeypatch, kind):
    t = ticker()
    t.info = {"marketCap": 1e10}                                    # no debtToEquity -> from balance sheet
    r = reach(mods, monkeypatch, kind, t)
    assert r["details"]["Debt_to_Equity"] == 0.2                    # 100 / 500
    assert r["criteria"]["C3_LowDebt"] == 1
    inc, bal, cf = statements(**{"Long Term Debt": None})
    t2 = FakeTicker(inc, bal, cf, info={"marketCap": 1e10})
    r2 = reach(mods, monkeypatch, kind, t2)
    assert r2["criteria"]["C3_LowDebt"] == 0 and r2["details"]["Debt_to_Equity"] == "N/A"
    t3 = ticker()
    t3.info = {"marketCap": 1e10, "debtToEquity": 9.5}              # <= 10 is already a plain ratio
    assert reach(mods, monkeypatch, kind, t3)["details"]["Debt_to_Equity"] == 9.5
    t4 = ticker()
    t4.info = {"marketCap": 1e10, "debtToEquity": 10.5}             # > 10 is a percentage
    assert reach(mods, monkeypatch, kind, t4)["details"]["Debt_to_Equity"] == 0.1


@pytest.mark.parametrize("kind", ["india", "us"])
def test_coffee_can_debt_to_equity_boundaries(mods, monkeypatch, kind):
    # 10 is still a plain ratio (fails D/E < 1); only values above 10 are read as percentages.
    for raw, de, expected in ((10, 10, 0), (1.0, 1.0, 0), (0.99, 0.99, 1), (100.0, 1.0, 0), (99.0, 0.99, 1)):
        t = ticker()
        t.info = {"marketCap": 1e10, "debtToEquity": raw}
        r = reach(mods, monkeypatch, kind, t)
        assert r["details"]["Debt_to_Equity"] == de
        assert r["criteria"]["C3_LowDebt"] == expected


def test_us_coffee_can_fcf_rules(mods, monkeypatch):
    r = reach(mods, monkeypatch, "us", ticker(**{"Free Cash Flow": [-5.0, 60.0]}))
    assert r["criteria"]["C6_FreeCashFlow_pos"] == 0 and r["qualifies"] is False
    # no FCF row: computed as OCF - |CapEx|
    inc, bal, cf = statements(**{"Free Cash Flow": None})
    cf.loc["Capital Expenditure"] = [-100.0, -50.0]
    r = reach(mods, monkeypatch, "us", FakeTicker(inc, bal, cf))
    assert r["criteria"]["C6_FreeCashFlow_pos"] == 1 and r["details"]["FCF_latest_$M"] == 0.0
    cf.loc["Capital Expenditure"] = [-200.0, -50.0]
    r = reach(mods, monkeypatch, "us", FakeTicker(inc, bal, cf))
    assert r["criteria"]["C6_FreeCashFlow_pos"] == 0
    inc, bal, cf = statements(**{"Free Cash Flow": None})
    r = reach(mods, monkeypatch, "us", FakeTicker(inc, bal, cf))
    assert r["criteria"]["C6_FreeCashFlow_pos"] == 0 and r["details"]["FCF_latest_$M"] == "N/A"


def test_us_coffee_can_market_cap_from_fast_info_and_threshold(mods, monkeypatch):
    t = ticker()
    t.info = {"debtToEquity": 45}
    t.fast_info = types.SimpleNamespace(market_cap=2e9)
    assert reach(mods, monkeypatch, "us", t)["criteria"]["C4_MCap_ge1B"] == 1
    t.fast_info = types.SimpleNamespace(market_cap=1e9)
    assert reach(mods, monkeypatch, "us", t)["criteria"]["C4_MCap_ge1B"] == 1      # exactly $1B passes
    t.fast_info = types.SimpleNamespace(market_cap=1e9 - 1)
    assert reach(mods, monkeypatch, "us", t)["criteria"]["C4_MCap_ge1B"] == 0


def test_us_coffee_can_roe_requires_positive_equity(mods, monkeypatch):
    r = reach(mods, monkeypatch, "us", ticker(**{"Stockholders Equity": [-500.0, -450.0]}))
    assert r["criteria"]["C2_ROE_gt15"] == 0 and r["details"]["ROE_avg_%"] == "N/A"
    r = reach(mods, monkeypatch, "us", ticker(**{"Net Income": [60.0, 50.0]}))       # ROE 12% / 11%
    assert r["criteria"]["C2_ROE_gt15"] == 0


def test_india_coffee_can_roce(mods, monkeypatch):
    t = ticker()
    t.info = {"marketCap": 1e10, "debtToEquity": 45}
    r = reach(mods, monkeypatch, "india", t)
    assert r["details"]["ROCE_avg_%"] == pytest.approx(25.33, abs=0.01)             # (26.67 + 24.0) / 2
    assert r["details"]["ROCE_min_%"] == 24.0 and r["details"]["ROCE_years"] == 2
    t2 = ticker(**{"EBIT": [50.0, 60.0]})
    t2.info = dict(t.info)
    r2 = reach(mods, monkeypatch, "india", t2)
    assert r2["criteria"]["C2_ROCE_gt15"] == 0
    t3 = ticker(**{"Total Assets": [200.0, 200.0]})                                    # cap employed negative
    t3.info = dict(t.info)
    assert reach(mods, monkeypatch, "india", t3)["details"]["ROCE_avg_%"] == "N/A"


def test_india_coffee_can_market_cap_in_crores(mods, monkeypatch):
    t = ticker()
    t.info = {"marketCap": 5e9, "debtToEquity": 45}                                     # exactly 500 Cr
    r = reach(mods, monkeypatch, "india", t)
    assert r["criteria"]["C4_MCap_ge500Cr"] == 1 and r["details"]["Market_Cap_Cr"] == 500.0
    t.info = {"marketCap": 5e9 - 1e7, "debtToEquity": 45}
    assert reach(mods, monkeypatch, "india", t)["criteria"]["C4_MCap_ge500Cr"] == 0
    t.info = {"debtToEquity": 45}
    r = reach(mods, monkeypatch, "india", t)
    assert r["criteria"]["C4_MCap_ge500Cr"] == 0 and r["details"]["Market_Cap_Cr"] == "N/A"


@pytest.mark.parametrize("kind", ["india", "us"])
def test_coffee_can_no_income_data(mods, monkeypatch, kind):
    t = FakeTicker(pd.DataFrame(), *statements()[1:])
    if kind == "india":
        monkeypatch.setattr(mods["india"], "_yf_financials", lambda tk: (tk.income_stmt, tk.balance_sheet, tk.cash_flow))
    r = coffee(mods, monkeypatch, kind, t)
    assert "error" in r and r.get("qualifies") is None


def test_india_coffee_can_yfinance_missing(mods, monkeypatch):
    monkeypatch.setattr(mods["india"], "YFINANCE_OK", False)
    assert "yfinance not installed" in mods["india"].compute_coffee_can("X")["error"]


# ---- displays ----------------------------------------------------------------------------

@pytest.mark.parametrize("kind", PIO_KINDS + ["india"])
def test_display_piotroski(mods, capsys, kind):
    m = mods[kind]
    result = {"f_score": 7, "interpretation": "STRONG", "component_scores": {"F1_ROA_positive": 1, "F2_OCF_positive": 0},
              "details": {"ROA_current_%": 10.0}}
    m.display_piotroski_score(result)
    out = capsys.readouterr().out
    assert "PIOTROSKI F-SCORE" in out
    assert "\033[92m7/9\033[0m  — STRONG" in out
    if kind != "sg":                      # the Singapore display prints the score line only
        assert "\033[92m✔\033[0m  F1  ROA > 0" in out
        assert "\033[91m✘\033[0m  F2  Operating Cash Flow > 0" in out
        assert "ROA current %" in out
    for score, color in ((4, "\033[93m"), (3, "\033[91m")):
        m.display_piotroski_score(dict(result, f_score=score))
        assert f"{color}{score}/9" in capsys.readouterr().out
    m.display_piotroski_score({"error": "nope"})
    assert "nope" in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["us", "india"])
def test_display_coffee_can(mods, capsys, kind):
    m = mods[kind]
    result = {"qualifies": True, "score": "5/5", "criteria": {"C3_LowDebt": 1, "C5_NoProfitLoss": 0},
              "details": {"Debt_to_Equity": 0.4}}
    m.display_coffee_can(result)
    out = capsys.readouterr().out
    assert "✔ QUALIFIES" in out and "(5/5 criteria met)" in out
    assert "✔\033[0m  C3  Debt/Equity < 1" in out
    assert "✘\033[0m  C5  No loss" in out
    m.display_coffee_can(dict(result, qualifies=False))
    assert "DOES NOT QUALIFY" in capsys.readouterr().out
    m.display_coffee_can({"error": "bad"})
    assert "bad" in capsys.readouterr().out


# ---- Singapore PEGY / breakout -------------------------------------------------------------

def sg_quote(mods, monkeypatch, **quote):
    m = mods["sg"]
    monkeypatch.setattr(m, "fetch_quote", lambda t: quote)
    t = types.SimpleNamespace(info={}, history=lambda period: pd.DataFrame())
    return m.compute_pegy_and_breakout(t, "D05")


def test_sg_pegy_values(mods, monkeypatch):
    r = sg_quote(mods, monkeypatch, lastPrice=110.0, trailingPE=12.345, dividendYield=0.04, pegRatio=1.5, **{"200dAvg": 100.0})
    assert r["cmp"] == 110.0 and r["pe_ratio"] == 12.35 and r["peg_ratio"] == 1.5
    assert r["dividend_yield_%"] == 4.0
    assert r["pegy_adjusted"] == 5.5                        # PEG + yield expressed in percent points
    assert r["breakout"] == {"distance_from_200ma_%": 10.0, "signal": "Above 200-day MA - Uptrend"}


@pytest.mark.parametrize("price,signal", [
    (105.01, "Above 200-day MA - Uptrend"), (105.0, "Near 200-day MA - Consolidation"),
    (95.0, "Near 200-day MA - Consolidation"), (94.99, "Below 200-day MA - Downtrend"),
])
def test_sg_breakout_signal_boundaries(mods, monkeypatch, price, signal):
    r = sg_quote(mods, monkeypatch, lastPrice=price, **{"200dAvg": 100.0})
    assert r["breakout"]["signal"] == signal


def test_sg_pegy_missing_data(mods, monkeypatch):
    r = sg_quote(mods, monkeypatch)
    assert r["cmp"] is None and r["pegy_adjusted"] is None and r["breakout"] == {}
    assert r["dividend_yield_%"] is None
    r = sg_quote(mods, monkeypatch, lastPrice=100.0, pegRatio=2.0)
    assert r["pegy_adjusted"] == 2.0                        # no dividend -> PEG alone
    m = mods["sg"]
    bad = types.SimpleNamespace(info=None)
    bad.history = lambda period: (_ for _ in ()).throw(RuntimeError("x"))
    assert m.compute_pegy_and_breakout(bad, "D05") == {"symbol": "D05", "error": "Data fetch failed"}


def test_sg_display_pegy(mods, monkeypatch, capsys):
    m = mods["sg"]
    m.display_pegy_and_breakout({"error": "bad"})
    assert "bad" in capsys.readouterr().out
    m.display_pegy_and_breakout({"cmp": 110.0, "pe_ratio": 12.0, "peg_ratio": 1.5, "dividend_yield_%": 4.0,
                                 "pegy_adjusted": 5.5, "ma_200": 100.0,
                                 "breakout": {"distance_from_200ma_%": 10.0, "signal": "Above"}})
    out = capsys.readouterr().out
    assert "S$110.00" in out and "10.00%" in out and "Above" in out and "5.5" in out

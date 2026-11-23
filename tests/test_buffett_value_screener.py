"""BuffettScreener gates, ranking, universe loaders and the Excel writer (fake yfinance, no network)."""
import sys
import types

import pandas as pd
import pytest

PATH = "02_market_screeners/buffett_value_screener.py"


@pytest.fixture
def bv(load_script):
    return load_script(PATH)


def frame(**cols):
    """One row per period, newest first (the layout the screener reads with .iloc[i])."""
    n = max(len(v) for v in cols.values())
    return pd.DataFrame({k: v + [None] * (n - len(v)) if len(v) < n else v for k, v in cols.items()})


class FakeTicker:
    def __init__(self, info=None, inc=None, bal=None, cf=None):
        self.info = info if info is not None else {}
        self.income_stmt, self.balance_sheet, self.cashflow = inc, bal, cf


def install(bv, monkeypatch, tickers):
    monkeypatch.setattr(bv.yf, "Ticker", lambda t: tickers[t])


GOOD = dict(
    info={"marketCap": 1000.0, "trailingPE": 12.0, "earningsGrowth": 0.10},
    inc=frame(**{"Net Income": [200.0, 190.0, 180.0, 170.0, 160.0],
                 "Total Revenue": [1000.0] * 5, "Cost of Revenue": [500.0] * 5}),
    bal=frame(**{"Total Equity": [1000.0] * 5, "Total Debt": [300.0] * 5,
                 "Current Assets": [900.0] * 5, "Current Liabilities": [400.0] * 5}),
    cf=frame(**{"Operating Cash Flow": [120.0], "Capital Expenditure": [-30.0]}),
)


def good(**over):
    d = {k: (v.copy() if hasattr(v, "copy") else v) for k, v in GOOD.items()}
    d.update(over)
    return FakeTicker(**d)


# ---- fortress -----------------------------------------------------------------------

def test_fortress_pass(bv, monkeypatch):
    install(bv, monkeypatch, {"G": good()})
    r = bv.BuffettScreener("us").screen_fortress("G")
    assert r["roe"] == pytest.approx(0.2)
    assert r["debt_to_eq"] == pytest.approx(0.3)
    assert r["gross_margin"] == pytest.approx(0.5)
    assert r["current_ratio"] == pytest.approx(2.25)
    assert r["fcf_yield"] == pytest.approx(0.09)          # (120 - |-30|) / 1000
    assert r["passes_fortress"] is True


@pytest.mark.parametrize("field,frame_key,col,value", [
    ("roe", "inc", "Net Income", [149.9] + [100.0] * 4),                # < 15% ROE
    ("debt", "bal", "Total Debt", [501.0] * 5),                          # D/E > 0.5
    ("margin", "inc", "Cost of Revenue", [701.0] * 5),                   # GM < 30%
    ("liquidity", "bal", "Current Assets", [599.0] * 5),                 # CR < 1.5
])
def test_fortress_each_gate_can_fail(bv, monkeypatch, field, frame_key, col, value):
    base = good()
    df = getattr(base, {"inc": "income_stmt", "bal": "balance_sheet"}[frame_key]).copy()
    df[col] = value
    setattr(base, {"inc": "income_stmt", "bal": "balance_sheet"}[frame_key], df)
    install(bv, monkeypatch, {"X": base})
    assert bv.BuffettScreener("us").screen_fortress("X")["passes_fortress"] is False


def test_fortress_fcf_gate_and_exact_boundaries(bv, monkeypatch):
    low_fcf = good(cf=frame(**{"Operating Cash Flow": [79.0], "Capital Expenditure": [-30.0]}))   # 4.9%
    install(bv, monkeypatch, {"X": low_fcf})
    assert bv.BuffettScreener("us").screen_fortress("X")["passes_fortress"] is False
    # every threshold exactly at its limit passes (>= / <=)
    edge = FakeTicker(
        info={"marketCap": 1000.0},
        inc=frame(**{"Net Income": [150.0], "Total Revenue": [1000.0], "Cost of Revenue": [700.0]}),
        bal=frame(**{"Total Equity": [1000.0], "Total Debt": [500.0],
                     "Current Assets": [1500.0], "Current Liabilities": [1000.0]}),
        cf=frame(**{"Operating Cash Flow": [100.0], "Capital Expenditure": [-50.0]}),
    )
    install(bv, monkeypatch, {"E": edge})
    r = bv.BuffettScreener("us").screen_fortress("E")
    assert (r["roe"], r["debt_to_eq"], r["gross_margin"], r["current_ratio"], r["fcf_yield"]) == (
        0.15, 0.5, pytest.approx(0.3), 1.5, 0.05)
    assert r["passes_fortress"] is True


def test_fortress_capex_sign_does_not_matter(bv, monkeypatch):
    pos = good(cf=frame(**{"Operating Cash Flow": [120.0], "Capital Expenditure": [30.0]}))
    install(bv, monkeypatch, {"P": pos})
    assert bv.BuffettScreener("us").screen_fortress("P")["fcf_yield"] == pytest.approx(0.09)


def test_fortress_degenerate_denominators(bv, monkeypatch):
    t = good(bal=frame(**{"Total Equity": [0.0], "Total Debt": [10.0],
                          "Current Assets": [5.0], "Current Liabilities": [0.0]}),
             inc=frame(**{"Net Income": [1.0], "Total Revenue": [0.0], "Cost of Revenue": [0.0]}),
             info={"marketCap": 0})
    install(bv, monkeypatch, {"Z": t})
    r = bv.BuffettScreener("us").screen_fortress("Z")
    assert r["roe"] == 0 and r["debt_to_eq"] == 999 and r["gross_margin"] == 0
    assert r["current_ratio"] == 0 and r["fcf_yield"] == 0
    assert r["passes_fortress"] is False


def test_fortress_none_when_data_missing_or_error(bv, monkeypatch):
    install(bv, monkeypatch, {"A": good(inc=None), "B": good(bal=None), "C": good(cf=None)})
    s = bv.BuffettScreener("us")
    assert s.screen_fortress("A") is None
    assert s.screen_fortress("B") is None
    assert s.screen_fortress("C") is None                  # cf None -> len() raises -> swallowed
    assert s.screen_fortress("unknown") is None            # KeyError from the fake -> swallowed


# ---- moat ---------------------------------------------------------------------------

def test_moat_pass_and_metrics(bv, monkeypatch):
    install(bv, monkeypatch, {"G": good()})
    r = bv.BuffettScreener("us").screen_moat("G")
    assert r == {"margin_stability": 1.0, "profitable_years": 5, "passes_moat": True}


def test_moat_needs_three_periods(bv, monkeypatch):
    short = good(inc=frame(**{"Net Income": [1, 1], "Total Revenue": [10, 10], "Cost of Revenue": [5, 5]}))
    install(bv, monkeypatch, {"S": short, "N": good(inc=None)})
    s = bv.BuffettScreener("us")
    assert s.screen_moat("S") is None and s.screen_moat("N") is None


def test_moat_thresholds(bv, monkeypatch):
    # margin stability exactly 0.85 passes; four profitable years fail the five-year streak
    inc = frame(**{"Net Income": [1.0, 1.0, 1.0, 1.0, 1.0], "Total Revenue": [100.0] * 5,
                   "Cost of Revenue": [50.0, 50.0, 57.5, 50.0, 50.0]})
    install(bv, monkeypatch, {"E": good(inc=inc)})
    r = bv.BuffettScreener("us").screen_moat("E")
    assert r["margin_stability"] == pytest.approx(0.85)
    assert r["passes_moat"] is True
    inc2 = inc.copy()
    inc2["Cost of Revenue"] = [50.0, 50.0, 57.6, 50.0, 50.0]
    install(bv, monkeypatch, {"E": good(inc=inc2)})
    assert bv.BuffettScreener("us").screen_moat("E")["passes_moat"] is False
    inc3 = inc.copy()
    inc3["Net Income"] = [1.0, 1.0, 1.0, 1.0, 0.0]
    install(bv, monkeypatch, {"E": good(inc=inc3)})
    r = bv.BuffettScreener("us").screen_moat("E")
    assert r["profitable_years"] == 4 and r["passes_moat"] is False


def test_moat_only_first_three_periods_drive_margin_stability(bv, monkeypatch):
    inc = frame(**{"Net Income": [1.0] * 5, "Total Revenue": [100.0] * 5,
                   "Cost of Revenue": [50.0, 50.0, 50.0, 99.0, 99.0]})
    install(bv, monkeypatch, {"E": good(inc=inc)})
    assert bv.BuffettScreener("us").screen_moat("E")["margin_stability"] == 1.0


def test_moat_zero_revenue_gives_zero_stability(bv, monkeypatch):
    inc = frame(**{"Net Income": [1.0] * 3, "Total Revenue": [0.0] * 3, "Cost of Revenue": [0.0] * 3})
    install(bv, monkeypatch, {"E": good(inc=inc)})
    r = bv.BuffettScreener("us").screen_moat("E")
    assert r["margin_stability"] == 0 and r["profitable_years"] == 3 and r["passes_moat"] is False


# ---- valuation ----------------------------------------------------------------------

def test_valuation_pass(bv, monkeypatch):
    install(bv, monkeypatch, {"G": good()})
    r = bv.BuffettScreener("us").screen_valuation("G")
    assert r["pe"] == 12.0
    assert r["peg"] == pytest.approx(1.2)
    assert r["earnings_yield"] == pytest.approx(1 / 12)
    assert r["discount_to_fair"] == pytest.approx(1 - 12 / 18)
    assert r["passes_valuation"] is True


@pytest.mark.parametrize("info,passes", [
    ({"trailingPE": 14.0, "earningsGrowth": 0.1}, False),       # discount 22% < 25% -> fails
    ({"trailingPE": 13.5, "earningsGrowth": 0.1}, True),        # discount exactly 25% passes
    ({"trailingPE": 20.0, "earningsGrowth": 0.1}, False),       # PE at the cap but no discount
    ({"trailingPE": 10.0, "earningsGrowth": 0.04}, False),      # PEG 2.5 > 2
    ({"trailingPE": 10.0, "earningsGrowth": 0.05}, True),       # PEG exactly 2 passes
    ({"trailingPE": 10.0, "earningsGrowth": 0}, False),         # no growth -> PEG 999
])
def test_valuation_gates(bv, monkeypatch, info, passes):
    install(bv, monkeypatch, {"V": FakeTicker(info=info)})
    assert bv.BuffettScreener("us").screen_valuation("V")["passes_valuation"] is passes


def test_valuation_pe_fallbacks_and_defaults(bv, monkeypatch):
    install(bv, monkeypatch, {"F": FakeTicker(info={"forwardPE": 9.0}),
                              "N": FakeTicker(info={}),
                              "T": FakeTicker(info={"trailingPE": 8.0, "forwardPE": 30.0})})
    s = bv.BuffettScreener("us")
    assert s.screen_valuation("F")["pe"] == 9.0
    assert s.screen_valuation("T")["pe"] == 8.0
    n = s.screen_valuation("N")
    assert n["pe"] == 999 and n["earnings_yield"] == 0 and n["passes_valuation"] is False
    assert n["peg"] == pytest.approx(999 / 5)                # default growth 5%


def test_valuation_error_returns_none(bv, monkeypatch):
    install(bv, monkeypatch, {})
    assert bv.BuffettScreener("us").screen_valuation("missing") is None


# ---- screen_ticker / run_screen -----------------------------------------------------

def test_screen_ticker_combines_gates(bv, monkeypatch):
    install(bv, monkeypatch, {"AAA": good()})
    r = bv.BuffettScreener("us").screen_ticker(("AAA", "US", ""))
    assert r["ticker"] == "AAA" and r["exchange"] == "US" and r["full_ticker"] == "AAA"
    assert r["buffett_pick"] is True
    assert r["quality_score"] == pytest.approx(0.2 * 1.0)
    assert r["discount_score"] == pytest.approx(1 - 12 / 18)
    assert {"roe", "margin_stability", "pe", "peg"} <= set(r)


def test_screen_ticker_suffix_handling(bv, monkeypatch):
    install(bv, monkeypatch, {"TCS.NS": good()})
    r = bv.BuffettScreener("india").screen_ticker(("TCS", "NSE", ".NS"))
    assert r["full_ticker"] == "TCS.NS"
    install(bv, monkeypatch, {"SAP.DE": good()})
    assert bv.BuffettScreener("europe").screen_ticker(("SAP.DE", "Europe", ".DE"))["full_ticker"] == "SAP.DE"


def test_screen_ticker_not_a_pick_when_one_gate_fails(bv, monkeypatch):
    expensive = good(info={"marketCap": 1000.0, "trailingPE": 30.0, "earningsGrowth": 0.1})
    install(bv, monkeypatch, {"X": expensive})
    r = bv.BuffettScreener("us").screen_ticker(("X", "US", ""))
    assert r["buffett_pick"] is False and r["passes_fortress"] is True and r["passes_valuation"] is False


def test_screen_ticker_none_when_any_gate_has_no_data(bv, monkeypatch):
    install(bv, monkeypatch, {"X": good(inc=None)})
    assert bv.BuffettScreener("us").screen_ticker(("X", "US", "")) is None


def test_run_screen_ranks_and_limits(bv, monkeypatch):
    lower = good(inc=frame(**{"Net Income": [160.0] * 5, "Total Revenue": [1000.0] * 5,
                              "Cost of Revenue": [500.0] * 5}))
    install(bv, monkeypatch, {"HI": good(), "LO": lower, "BAD": good(inc=None)})
    s = bv.BuffettScreener("us")
    s.universe = [("LO", "US", ""), ("BAD", "US", ""), ("HI", "US", "")]
    out = s.run_screen(workers=2, limit=3)
    assert [r["ticker"] for r in out] == ["HI", "LO"]       # sorted by quality*discount, failures dropped
    assert s.results is out
    s.universe = [("LO", "US", ""), ("BAD", "US", ""), ("HI", "US", "")]
    assert [r["ticker"] for r in s.run_screen(limit=1)] == ["LO"]


def test_run_screen_logs_picks(bv, monkeypatch, caplog):
    install(bv, monkeypatch, {"HI": good()})
    s = bv.BuffettScreener("us")
    s.universe = [("HI", "US", "")]
    with caplog.at_level("INFO"):
        s.run_screen()
    assert any("Buffett pick: HI" in m for m in caplog.messages)


# ---- universe loaders ---------------------------------------------------------------

def test_load_universe_us_fallback_when_yf_has_no_sp500(bv, monkeypatch):
    s = bv.BuffettScreener("us")
    s.load_universe_us()
    assert s.universe[0] == ("AAPL", "US", "") and len(s.universe) == 5
    monkeypatch.setattr(bv.yf, "get_tickers_sp500", lambda: [f"T{i}" for i in range(600)], raising=False)
    s2 = bv.BuffettScreener("us")
    s2.load_universe_us()
    assert len(s2.universe) == 500 and s2.universe[0] == ("T0", "US", "")


def test_load_universe_europe_is_static_sample(bv):
    s = bv.BuffettScreener("europe")
    s.load_universe_europe()
    assert len(s.universe) == 10
    assert s.universe[0] == ("ASML.AS", "Europe", ".AS")
    assert all(t[1] == "Europe" for t in s.universe)


def test_load_universe_india_without_libs(bv, monkeypatch):
    monkeypatch.setitem(sys.modules, "nsepython", None)       # makes `import nsepython` raise ImportError
    s = bv.BuffettScreener("india")
    s.load_universe_india()
    assert s.universe == []


def test_load_universe_india_dedupes_and_uppercases(bv, monkeypatch):
    nse = types.ModuleType("nsepython")
    nse.nse_eq = lambda: ["tcs", " INFY ", "tcs", ""]
    bse = types.ModuleType("bseindia")
    bse.get_security_code = lambda: {"Symbol": ["infy", "zomato", ""]}
    monkeypatch.setitem(sys.modules, "nsepython", nse)
    monkeypatch.setitem(sys.modules, "bseindia", bse)
    s = bv.BuffettScreener("india")
    s.load_universe_india()
    assert s.universe == [("TCS", "NSE", ".NS"), ("INFY", "NSE", ".NS"), ("ZOMATO", "BSE", ".BO")]


def test_load_universe_japan(bv, monkeypatch):
    issues = [{"category": "内国株式", "security_code": "7203.0"},
              {"category": "ETF", "security_code": "1306"},
              {"category": "内国株式", "security_code": "abc"},
              {"category": "内国株式", "security_code": "99"},
              {"category": "内国株式", "security_code": "123456"}]
    kabu = types.ModuleType("kabupy")
    kabu.Jpx = lambda: types.SimpleNamespace(issues=issues)
    monkeypatch.setitem(sys.modules, "kabupy", kabu)
    s = bv.BuffettScreener("japan")
    s.load_universe_japan()
    assert s.universe == [("7203.T", "Japan", ".T"), ("0099.T", "Japan", ".T")]
    monkeypatch.setitem(sys.modules, "kabupy", None)
    s2 = bv.BuffettScreener("japan")
    s2.load_universe_japan()
    assert s2.universe == []


def test_load_universe_korea(bv, monkeypatch):
    pk = types.ModuleType("pykrx")
    pk.stock = types.SimpleNamespace(get_market_ticker_list=lambda d, market: (
        [f"K{i}" for i in range(120)] if market == "KOSPI" else [f"Q{i}" for i in range(70)]))
    monkeypatch.setitem(sys.modules, "pykrx", pk)
    s = bv.BuffettScreener("korea")
    s.load_universe_korea()
    assert len(s.universe) == 150
    assert s.universe[0] == ("K0.KS", "Korea", ".KS")
    assert s.universe[100] == ("Q0.KQ", "Korea", ".KQ")
    monkeypatch.setitem(sys.modules, "pykrx", None)
    s2 = bv.BuffettScreener("korea")
    s2.load_universe_korea()
    assert s2.universe == []


# ---- helpers / excel / main ----------------------------------------------------------

def test_first_df(bv):
    df = pd.DataFrame({"a": [1]})
    assert bv._first_df(df) is df
    assert bv._first_df({"x": 1, "y": df}) is df
    assert bv._first_df({"x": 1}) is None
    assert bv._first_df(None) is None


def test_criteria_constants(bv):
    c = bv.BuffettCriteria
    assert (c.MIN_ROE, c.MAX_DEBT_TO_EQUITY, c.MIN_CURRENT_RATIO, c.MIN_FCF_YIELD, c.MIN_GROSS_MARGIN) == (
        0.15, 0.5, 1.5, 0.05, 0.30)
    assert (c.MIN_MARGIN_STABILITY, c.MIN_YEARS_PROFITABLE, c.MAX_PE_RATIO, c.MIN_MARGIN_OF_SAFETY) == (
        0.85, 5, 20, 0.25)


def result_row(**kw):
    row = {"ticker": "AAA", "exchange": "US", "roe": 0.2, "debt_to_eq": 0.3, "gross_margin": 0.5,
           "current_ratio": 2.25, "margin_stability": 1.0, "profitable_years": 5, "pe": 12.0, "peg": 1.2,
           "discount_to_fair": 0.3333, "quality_score": 0.2, "buffett_pick": True}
    row.update(kw)
    return row


def test_save_excel_layout(bv, tmp_path):
    from openpyxl import load_workbook
    a = bv.BuffettScreener("us")
    a.results = [result_row(), result_row(ticker="BBB", buffett_pick=False)]
    long_name = bv.BuffettScreener("x" * 40)
    long_name.results = []
    out = tmp_path / "out.xlsx"
    bv.save_excel({"us": a, "y" * 40: long_name}, out)
    wb = load_workbook(out)
    assert wb.sheetnames == ["us", "y" * 31]
    ws = wb["us"]
    assert [c.value for c in ws[1]][:3] == ["Ticker", "Exchange", "ROE %"]
    assert ws.max_row == 3
    assert [c.value for c in ws[2]] == ["AAA", "US", 20.0, 0.3, 50.0, 2.25, 1.0, 5, 12.0, 1.2, 33.3, 0.2, "✓"]
    assert ws.cell(3, 13).value in (None, "")
    assert ws["A2"].fill.start_color.rgb.endswith("E2EFDA")
    assert not ws["A3"].fill.start_color.rgb.endswith("E2EFDA")
    assert ws["A1"].font.bold is True


def test_save_excel_caps_rows_at_100(bv, tmp_path):
    from openpyxl import load_workbook
    s = bv.BuffettScreener("us")
    s.results = [result_row(ticker=f"T{i}") for i in range(130)]
    out = tmp_path / "o.xlsx"
    bv.save_excel({"us": s}, out)
    assert load_workbook(out)["us"].max_row == 101


def test_main_runs_selected_market(bv, monkeypatch, tmp_path):
    calls = []

    class Fake:
        def __init__(self, market):
            self.market, self.universe, self.results = market, [], []
            calls.append(("init", market))

        def load_universe_europe(self):
            self.universe = [("X", "Europe", ".DE")]

        def run_screen(self, workers, limit):
            calls.append(("run", self.market, workers, limit))
            self.results = [{"buffett_pick": True}, {"buffett_pick": False}]

    saved = {}
    monkeypatch.setattr(bv, "BuffettScreener", Fake)
    monkeypatch.setattr(bv, "save_excel", lambda s, p: saved.update(screeners=s, path=p))
    monkeypatch.setattr(sys, "argv", ["b", "--market", "europe", "--workers", "7", "--limit", "3"])
    bv.main()
    assert calls == [("init", "europe"), ("run", "europe", 7, 3)]
    assert list(saved["screeners"]) == ["europe"]
    assert saved["path"].name.startswith("buffett_value_scan_") and saved["path"].suffix == ".xlsx"


def test_main_all_markets_skips_empty_universe(bv, monkeypatch):
    seen = []

    class Fake:
        def __init__(self, market):
            self.market, self.universe, self.results = market, [], []

        def load_universe_india(self): pass
        def load_universe_us(self): self.universe = [1]
        def load_universe_europe(self): pass
        def load_universe_japan(self): self.universe = [1]
        def load_universe_korea(self): pass

        def run_screen(self, workers, limit):
            seen.append((self.market, workers, limit))

    monkeypatch.setattr(bv, "BuffettScreener", Fake)
    monkeypatch.setattr(bv, "save_excel", lambda s, p: seen.append(list(s)))
    monkeypatch.setattr(sys, "argv", ["b"])
    bv.main()
    assert seen == [("us", 4, 100), ("japan", 4, 100), ["india", "us", "europe", "japan", "korea"]]


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="screen_fortress reads fi.iloc[0].get('Net Income'), i.e. it treats the first ROW as a "
                          "period; yfinance statements have line items as rows and dates as columns, so every "
                          "value falls back to its default (ROE 0)")
def test_fortress_reads_yfinance_orientation(bv, monkeypatch):
    dates = [pd.Timestamp("2024-03-31"), pd.Timestamp("2023-03-31")]
    inc = pd.DataFrame({d: [200.0, 1000.0, 500.0] for d in dates},
                       index=["Net Income", "Total Revenue", "Cost of Revenue"])
    bal = pd.DataFrame({d: [1000.0, 300.0, 900.0, 400.0] for d in dates},
                       index=["Total Equity", "Total Debt", "Current Assets", "Current Liabilities"])
    cf = pd.DataFrame({d: [120.0, -30.0] for d in dates}, index=["Operating Cash Flow", "Capital Expenditure"])
    install(bv, monkeypatch, {"Y": FakeTicker(info={"marketCap": 1000.0}, inc=inc, bal=bal, cf=cf)})
    assert bv.BuffettScreener("us").screen_fortress("Y")["roe"] == pytest.approx(0.2)

"""us_market_screener_v1: financial-statement helpers, the real Piotroski / Coffee Can scorers and the tqdm path of main."""
import types

import pandas as pd
import pytest

PATH = "02_market_screeners/us_market_screener_v1.py"
COLS = [pd.Timestamp("2024-12-31"), pd.Timestamp("2023-12-31"), pd.Timestamp("2022-12-31"),
        pd.Timestamp("2021-12-31")]


@pytest.fixture
def v1(load_script):
    return load_script(PATH)


def frame(rows):
    n = max(len(v) for v in rows.values())
    return pd.DataFrame({COLS[i]: [v[i] if i < len(v) else None for v in rows.values()] for i in range(n)},
                        index=list(rows))


def book(**over):
    inc = {"Net Income": [100.0, 80.0], "Total Revenue": [1000.0, 900.0], "Gross Profit": [400.0, 330.0]}
    bal = {"Total Assets": [1000.0, 1000.0], "Long Term Debt": [100.0, 200.0], "Current Assets": [500.0, 400.0],
           "Current Liabilities": [250.0, 250.0], "Share Issued": [10.0, 10.0], "Stockholders Equity": [500.0, 450.0]}
    cf = {"Operating Cash Flow": [150.0, 120.0], "Free Cash Flow": [90.0, 60.0]}
    for d in (inc, bal, cf):
        for k in list(d):
            if k in over:
                if over[k] is None:
                    del d[k]
                else:
                    d[k] = over[k]
    return frame(inc), frame(bal), frame(cf)


class T:
    def __init__(self, inc, bal, cf, info=None, fast=None):
        self.income_stmt, self.balance_sheet, self.cash_flow = inc, bal, cf
        self.info = {"marketCap": 5e9, "debtToEquity": 45.0} if info is None else info
        if fast is not None:
            self.fast_info = fast


def ticker(info=None, **over):
    return T(*book(**over), info=info)


# ---- helpers -------------------------------------------------------------------------------

def test_first_df_and_val(v1):
    df = pd.DataFrame({"a": [1]})
    t = types.SimpleNamespace(income_stmt=pd.DataFrame(), financials=df, balance_sheet=None)
    assert v1._first_df(t, "income_stmt", "financials") is df
    assert v1._first_df(t, "balance_sheet", "missing") is None
    assert v1._first_df(types.SimpleNamespace(x="not a frame"), "x") is None
    f = frame({"Net Income": [5.0, float("nan")], "Alt": [7.0, 8.0]})
    assert v1._val(f, "Net Income") == 5.0
    assert v1._val(f, "Net Income", col=1) is None                 # NaN -> None
    assert v1._val(f, "Missing", "Alt", col=1) == 8.0              # first matching name wins
    assert v1._val(f, "Net Income", col=9) is None                 # out-of-range column
    assert v1._val(None, "x") is None and v1._val(pd.DataFrame(), "x") is None


# ---- Piotroski -----------------------------------------------------------------------------

def test_piotroski_perfect(v1):
    r = v1.run_piotroski(ticker())
    assert r == {"f_score": 9, "components": {f"F{i}": 1 for i in range(1, 10)}, "error": None}


def test_piotroski_no_income(v1):
    assert v1.run_piotroski(T(pd.DataFrame(), None, None)) == {"f_score": None, "error": "no_income_data"}
    assert v1.run_piotroski(types.SimpleNamespace())["error"] == "no_income_data"


FAILS = [
    ({"Net Income": [-5.0, -10.0]}, "F1"),
    ({"Operating Cash Flow": [-1.0, 120.0]}, "F2"),
    ({"Net Income": [70.0, 80.0]}, "F3"),
    ({"Operating Cash Flow": [90.0, 120.0]}, "F4"),
    ({"Long Term Debt": [300.0, 200.0]}, "F5"),
    ({"Current Assets": [300.0, 400.0]}, "F6"),
    ({"Share Issued": [12.0, 10.0]}, "F7"),
    ({"Gross Profit": [300.0, 330.0]}, "F8"),
    ({"Total Revenue": [800.0, 900.0]}, "F9"),
]


@pytest.mark.parametrize("over,comp", FAILS, ids=[f[1] for f in FAILS])
def test_piotroski_component_fails(v1, over, comp):
    r = v1.run_piotroski(ticker(**over))
    assert r["components"][comp] == 0
    assert r["f_score"] == sum(r["components"].values()) < 9


@pytest.mark.parametrize("over,comp", [
    ({"Net Income": [80.0, 80.0]}, "F3"), ({"Long Term Debt": [200.0, 200.0]}, "F5"),
    ({"Current Assets": [400.0, 400.0]}, "F6"), ({"Total Revenue": [900.0, 900.0]}, "F9"),
    ({"Gross Profit": [330.0, 330.0], "Total Revenue": [900.0, 900.0]}, "F8"),
])
def test_piotroski_ties_do_not_count(v1, over, comp):
    assert v1.run_piotroski(ticker(**over))["components"][comp] == 0


def test_piotroski_f7_boundaries_and_missing_data(v1):
    assert v1.run_piotroski(ticker(**{"Share Issued": [10.0, 10.0]}))["components"]["F7"] == 1
    assert v1.run_piotroski(ticker(**{"Share Issued": None}))["components"]["F7"] == 1       # absent -> assumed fine
    r = v1.run_piotroski(ticker(**{"Total Assets": None}))
    assert r["components"]["F1"] == 0 and r["components"]["F4"] == 0 and r["components"]["F5"] == 0
    one_year = T(*[d.iloc[:, :1] for d in book()])
    c = v1.run_piotroski(one_year)["components"]
    assert (c["F3"], c["F5"], c["F6"], c["F8"], c["F9"]) == (0, 0, 0, 0, 0) and c["F7"] == 1
    assert c["F1"] == 1 and c["F2"] == 1


def test_piotroski_legacy_attribute_names(v1):
    inc, bal, cf = book()
    t = types.SimpleNamespace(income_stmt=None, financials=inc, balance_sheet=bal, cash_flow=None, cashflow=cf)
    assert v1.run_piotroski(t)["f_score"] == 9


# ---- Coffee Can ----------------------------------------------------------------------------

def rich(**over):
    base = {"Total Revenue": [2000.0, 1600.0, 1300.0, 1000.0],       # CAGR ~26%
            "Net Income": [300.0, 250.0, 200.0, 150.0],
            "Stockholders Equity": [1000.0, 900.0, 800.0, 700.0],      # ROE avg ~ 25%
            "Long Term Debt": [100.0, 100.0], "Operating Cash Flow": [150.0, 120.0],
            "Free Cash Flow": [90.0, 60.0]}
    base.update(over)
    return base


def cc_ticker(info=None, fast=None, **over):
    b = rich(**over)
    inc = frame({k: b[k] for k in ("Total Revenue", "Net Income")})
    bal = frame({k: b[k] for k in ("Stockholders Equity", "Long Term Debt") if b[k] is not None})
    cf_rows = {k: b[k] for k in ("Operating Cash Flow", "Free Cash Flow") if b[k] is not None}
    for extra in ("Capital Expenditure", "Total Cash From Operating Activities"):
        if extra in b:
            cf_rows[extra] = b[extra]
    cf = frame(cf_rows)
    return T(inc, bal, cf, info=info, fast=fast)


def test_coffee_can_all_six(v1):
    r = v1.run_coffee_can(cc_ticker(info={"marketCap": 5e9, "debtToEquity": 45.0}))
    assert r["qualifies"] is True and r["score"] == "6/6" and r["error"] is None
    assert r["criteria"] == {f"C{i}": 1 for i in range(1, 7)}
    assert 20 < r["roe_avg"] < 30


def test_coffee_can_no_income(v1):
    assert v1.run_coffee_can(T(pd.DataFrame(), None, None)) == {"qualifies": False, "score": "0/6",
                                                                 "error": "no_income_data"}


def test_coffee_can_info_failure_is_tolerated(v1):
    class Bad(T):
        @property
        def info(self):
            raise RuntimeError("429")

        @info.setter
        def info(self, v):
            pass

    t = cc_ticker()
    bad = Bad(t.income_stmt, t.balance_sheet, t.cash_flow, fast=types.SimpleNamespace(market_cap=2e9))
    r = v1.run_coffee_can(bad)
    assert r["criteria"]["C4"] == 1                                    # market cap from fast_info
    assert r["criteria"]["C3"] == 1                                    # D/E from the balance sheet: 100/1000


def test_coffee_can_c1_revenue_cagr_threshold(v1):
    # three growth years: 1330/1000 -> 9.97% a year (fails), 1332/1000 -> 10.03% (passes)
    flat = cc_ticker(**{"Total Revenue": [1330.0, 1210.0, 1100.0, 1000.0]})
    assert v1.run_coffee_can(flat)["criteria"]["C1"] == 0
    assert v1.run_coffee_can(cc_ticker(**{"Total Revenue": [1332.0, 1210.0, 1100.0, 1000.0]}))["criteria"]["C1"] == 1
    assert v1.run_coffee_can(cc_ticker(**{"Total Revenue": [2000.0]}))["criteria"]["C1"] == 0       # one year only
    zero_base = cc_ticker(**{"Total Revenue": [2000.0, 1500.0, 500.0, 0.0]})
    assert v1.run_coffee_can(zero_base)["criteria"]["C1"] == 0                                         # non-positive base


def test_coffee_can_c2_roe_threshold_and_zero_equity(v1):
    exactly = cc_ticker(**{"Net Income": [150.0] * 4, "Stockholders Equity": [1000.0] * 4})      # ROE exactly 15%
    assert v1.run_coffee_can(exactly)["criteria"]["C2"] == 0
    above = cc_ticker(**{"Net Income": [151.0] * 4, "Stockholders Equity": [1000.0] * 4})
    assert v1.run_coffee_can(above)["criteria"]["C2"] == 1
    zero_year = cc_ticker(**{"Stockholders Equity": [1000.0, 0.0, 800.0, 700.0]})               # must not divide by 0
    r = v1.run_coffee_can(zero_year)
    assert r["criteria"]["C2"] == 1 and r["error"] is None


def test_coffee_can_c2_roe(v1):
    low = cc_ticker(**{"Net Income": [100.0, 100.0, 100.0, 100.0]})                  # ROE ~ 12%
    assert v1.run_coffee_can(low)["criteria"]["C2"] == 0
    assert v1.run_coffee_can(low)["roe_avg"] is not None
    neg_eq = cc_ticker(**{"Stockholders Equity": [-1.0, -1.0, -1.0, -1.0]})
    r = v1.run_coffee_can(neg_eq)
    assert r["criteria"]["C2"] == 0 and r["roe_avg"] is None                          # negative equity ignored
    only_bad_year = cc_ticker(**{"Stockholders Equity": [1000.0, 900.0, -5.0, 700.0]})
    assert v1.run_coffee_can(only_bad_year)["criteria"]["C2"] == 1                    # skipped, others still > 15%


@pytest.mark.parametrize("de,ok", [(0.99, 1), (1.0, 0), (45.0, 1), (10.0, 0), (10.5, 1), (150.0, 0), (5.0, 0)])
def test_coffee_can_c3_debt_to_equity(v1, de, ok):
    r = v1.run_coffee_can(cc_ticker(info={"marketCap": 5e9, "debtToEquity": de}))
    assert r["criteria"]["C3"] == ok


def test_coffee_can_c3_balance_sheet_fallback(v1):
    t = cc_ticker(info={"marketCap": 5e9}, **{"Long Term Debt": [999.0, 1.0]})
    assert v1.run_coffee_can(t)["criteria"]["C3"] == 1                                # 999 / 1000 < 1
    t = cc_ticker(info={"marketCap": 5e9}, **{"Long Term Debt": [1000.0, 1.0]})
    assert v1.run_coffee_can(t)["criteria"]["C3"] == 0                                # exactly 1.0 fails
    t = cc_ticker(info={"marketCap": 5e9}, **{"Long Term Debt": None})
    assert v1.run_coffee_can(t)["criteria"]["C3"] == 0                                # no debt data -> not credited


def test_coffee_can_c4_market_cap(v1):
    assert v1.run_coffee_can(cc_ticker(info={"marketCap": 1e9, "debtToEquity": 1}))["criteria"]["C4"] == 1
    assert v1.run_coffee_can(cc_ticker(info={"marketCap": 1e9 - 1, "debtToEquity": 1}))["criteria"]["C4"] == 0
    t = cc_ticker(info={"debtToEquity": 1}, fast=types.SimpleNamespace(market_cap=3e9))
    assert v1.run_coffee_can(t)["criteria"]["C4"] == 1                                # fast_info fallback
    assert v1.run_coffee_can(cc_ticker(info={"debtToEquity": 1}))["criteria"]["C4"] == 0   # no fast_info at all


def test_coffee_can_c5_no_loss_year(v1):
    assert v1.run_coffee_can(cc_ticker(**{"Net Income": [300.0, 250.0, 200.0, -1.0]}))["criteria"]["C5"] == 0
    assert v1.run_coffee_can(cc_ticker(**{"Net Income": [300.0, 250.0, 200.0, 0.0]}))["criteria"]["C5"] == 0   # 0 is a loss


def test_coffee_can_c6_free_cash_flow_rules(v1):
    assert v1.run_coffee_can(cc_ticker(**{"Free Cash Flow": [1.0, -5.0]}))["criteria"]["C6"] == 1
    assert v1.run_coffee_can(cc_ticker(**{"Free Cash Flow": [0.0, 5.0]}))["criteria"]["C6"] == 0
    base = {"Free Cash Flow": None, "Operating Cash Flow": [100.0, 90.0], "Capital Expenditure": [-60.0, -50.0]}
    assert v1.run_coffee_can(cc_ticker(**base))["criteria"]["C6"] == 1                 # 100 - |-60| > 0
    assert v1.run_coffee_can(cc_ticker(**dict(base, **{"Capital Expenditure": [-100.0, -50.0]})))["criteria"]["C6"] == 0
    assert v1.run_coffee_can(cc_ticker(**dict(base, **{"Capital Expenditure": [100.0, 50.0]})))["criteria"]["C6"] == 0
    no_capex = {"Free Cash Flow": None, "Operating Cash Flow": [100.0, 90.0]}
    assert v1.run_coffee_can(cc_ticker(**no_capex))["criteria"]["C6"] == 0             # cannot derive FCF
    legacy = {"Free Cash Flow": None, "Operating Cash Flow": None, "Total Cash From Operating Activities": [100.0],
              "Capital Expenditure": [-10.0]}
    assert v1.run_coffee_can(cc_ticker(**legacy))["criteria"]["C6"] == 1


# ---- main(): tqdm progress bars ----------------------------------------------------------------

def test_main_with_tqdm_progress_bars(v1, tmp_path, monkeypatch):
    bars = []

    class Bar:
        def __init__(self, **kw):
            self.kw, self.n, self.closed = kw, 0, False
            bars.append(self)

        def update(self, k):
            self.n += k

        def close(self):
            self.closed = True

    monkeypatch.setattr(v1, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setattr(v1, "DB_FILE", tmp_path / "out" / "screener.db")
    monkeypatch.setattr(v1, "TQDM_OK", True)
    monkeypatch.setattr(v1, "tqdm", Bar, raising=False)
    monkeypatch.setattr(v1.time, "sleep", lambda s: None)
    monkeypatch.setattr(v1, "fetch_stock_universe", lambda ex: pd.DataFrame(
        {"Symbol": ["AAA", "BBB"], "Name": ["A", "B"], "Exchange": ["NYSE", "NASDAQ"]}))
    monkeypatch.setattr(v1, "run_darvas_batch", lambda syms, batch_size=500, pbar=None: {
        s: {"signal": "IN_BOX", "box_top": 1.0, "box_bottom": 0.5} for s in syms})
    monkeypatch.setattr(v1, "scan_fundamentals_one", lambda s, n, e: {
        "symbol": s, "name": n, "exchange": e, "f_score": 3, "cc_qualifies": 0, "cc_score": "1/6"})
    v1.main(workers=1, delay=0)
    assert [b.kw["desc"] for b in bars] == ["Darvas", "Fundamentals"]
    assert [b.kw["total"] for b in bars] == [2, 2]
    assert bars[1].n == 2 and all(b.closed for b in bars)

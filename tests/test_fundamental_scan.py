"""Piotroski F-Score + Coffee Can in the five market scanners, with a fake
yfinance Ticker (hand-built statements; no network)."""
import pandas as pd
import pytest

COLS = [pd.Timestamp("2024-03-31"), pd.Timestamp("2023-03-31")]

SCANNERS = {
    "02_market_screeners/india_market_scan.py": ("RELI", ".NS"),
    "02_market_screeners/us_market_scan.py": ("AAPL",),
    "02_market_screeners/japan_market_scan.py": ("7203.T",),
    "02_market_screeners/korea_market_scan.py": ("005930", ".KS"),
    "02_market_screeners/europe_market_scan.py": ("SAP.DE",),
}


def stmt(rows):
    return pd.DataFrame({c: [v[i] if i < len(v) else None for v in rows.values()]
                         for i, c in enumerate(COLS)}, index=list(rows))


def statements(**over):
    inc = {"Net Income": [100.0, 80.0], "Total Revenue": [1000.0, 900.0],
           "Gross Profit": [400.0, 330.0], "EBIT": [200.0, 180.0],
           "Operating Income": [200.0, 180.0]}
    bal = {"Total Assets": [1000.0, 1000.0], "Long Term Debt": [100.0, 200.0],
           "Current Assets": [500.0, 400.0], "Current Liabilities": [250.0, 250.0],
           "Share Issued": [10.0, 10.0], "Stockholders Equity": [500.0, 450.0],
           "Ordinary Shares Number": [10.0, 10.0]}
    cf = {"Operating Cash Flow": [150.0, 120.0], "Free Cash Flow": [90.0, 60.0],
          "Capital Expenditure": [-60.0, -60.0]}
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
    def __init__(self, inc, bal, cf, mcap=1e13):
        self.income_stmt, self.financials = inc, inc
        self.balance_sheet = bal
        self.cash_flow, self.cashflow = cf, cf
        self.fast_info = type("FI", (), {"market_cap": mcap})()
        self.info = {"marketCap": mcap}


def scan(load_script, path, **over):
    mod = load_script(path)
    inc, bal, cf = statements(**over)
    mod.yf.Ticker = lambda *a, **k: FakeTicker(inc, bal, cf)
    return mod.fundamental_scan(*SCANNERS[path])


@pytest.fixture(params=list(SCANNERS))
def path(request):
    return request.param


def test_row_helper(load_script):
    m = load_script("02_market_screeners/india_market_scan.py")
    inc, _, _ = statements()
    assert m._row(inc, "Missing", "Net Income", col=1) == 80.0
    assert m._row(None, "Net Income") is None


def test_hand_computed_score_is_nine(load_script, path, monkeypatch):
    # ROA .10>.08, OCF/TA .15>ROA, LTD/TA .1<.2, CR 2.0>1.6, shares flat,
    # GM .40>.367, asset turnover 1.0>0.9  ->  all nine points
    assert scan(load_script, path)["f_score"] == 9


def test_missing_share_count_does_not_earn_the_dilution_point(load_script, path):
    # The README promises missing fundamentals are excluded "rather than assumed
    # to pass"; F7 (no dilution) must not be awarded without share counts.
    r = scan(load_script, path, **{"Share Issued": None, "Ordinary Shares Number": None})
    assert r["f_score"] == 8


def test_dilution_loses_the_point(load_script, path):
    r = scan(load_script, path, **{"Share Issued": [12.0, 10.0],
                                   "Ordinary Shares Number": [12.0, 10.0]})
    assert r["f_score"] == 8


def test_no_income_statement_is_no_data(load_script, path):
    mod = load_script(path)
    _, bal, cf = statements()
    mod.yf.Ticker = lambda *a, **k: FakeTicker(pd.DataFrame(), bal, cf)
    assert mod.fundamental_scan(*SCANNERS[path])["f_score"] is None


def test_india_coffee_can_requires_positive_free_cash_flow(load_script):
    # README: a Triple Hit needs "Coffee Can: all 6 criteria PASS", incl. FCF > 0
    # (JP/KR/EU/US scanners implement C6). Negative FCF must not qualify.
    path = "02_market_screeners/india_market_scan.py"
    ok = scan(load_script, path)
    assert ok["qualifies"] is True and ok["cc_score"] == "6/6"
    bad = scan(load_script, path, **{"Free Cash Flow": [-50.0, 60.0]})
    assert bad["qualifies"] is False

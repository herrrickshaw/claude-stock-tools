"""Golden characterisation of the daily reports' Piotroski and Coffee Can scans (offline, fake tickers)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from test_darvas_variants import _load

GOLDEN = Path(__file__).parent / "golden"


def _frame(rows: dict, cols: int = 4) -> pd.DataFrame:
    return pd.DataFrame({k: v[:cols] for k, v in rows.items()}, index=[f"y{i}" for i in range(cols)]).T


def make_ticker(kind: str):
    k = -1.0 if kind == "weak" else 1.0
    inc = _frame({
        "Net Income": [k * 150, k * 120, k * 100, k * 80],
        "Total Revenue": [2000, 1700, 1450, 1250],
        "Gross Profit": [900, 700, 580, 480],
        "EBIT": [400, 330, 280, 230],
    })
    bal = _frame({
        "Total Assets": [2500, 2300, 2100, 1900],
        "Long Term Debt": [100, 200, 250, 300],
        "Current Assets": [1200, 1000, 900, 800],
        "Current Liabilities": [500, 480, 470, 460],
        "Stockholders Equity": [1500, 1300, 1100, 900],
        "Share Issued": [90, 90, 95, 95],
    })
    cf = _frame({
        "Operating Cash Flow": [220, 180, 150, 120],
        "Free Cash Flow": [160, 130, 110, 90],
        "Capital Expenditure": [-60, -50, -40, -30],
    })
    info = {"marketCap": 6e12, "sharesOutstanding": 85, "debtToEquity": 45.2}
    if kind == "sparse":  # one fiscal year, no optional rows, no D/E in info: exercises the fallbacks
        inc, bal, cf = inc.iloc[:, :1], bal.drop(index=["Share Issued", "Current Assets"]).iloc[:, :1], \
            cf.drop(index=["Free Cash Flow"]).iloc[:, :1]
        info = {"marketCap": 2e8}
    if kind == "empty":
        return SimpleNamespace(income_stmt=pd.DataFrame(), financials=None, balance_sheet=None,
                               cash_flow=None, info={})
    return SimpleNamespace(income_stmt=inc, balance_sheet=bal, cash_flow=cf, info=info,
                           fast_info=SimpleNamespace(market_cap=info.get("marketCap")))


KINDS = ["strong", "weak", "sparse", "empty"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("report", ["india", "india_scoring", "us"])
def test_daily_scans_golden(tmp_path, monkeypatch, report, kind):
    mod = _load(f"01_daily_reports/{report.split('_')[0]}_daily_report.py", tmp_path, monkeypatch)
    tkr = make_ticker(kind)
    if report == "india_scoring":
        # india's own _yf_financials raises ValueError on a non-empty frame (`df or df`, a known bug that
        # surfaces as an "error" result); bypass it so the scoring arithmetic itself is pinned too.
        monkeypatch.setattr(mod, "_yf_financials", lambda t: (t.income_stmt, t.balance_sheet, t.cash_flow))
    if report.startswith("india"):
        monkeypatch.setattr(mod.yf, "Ticker", lambda *_a, **_k: tkr)
        got = {"piotroski": mod.compute_piotroski_score("TCS"), "coffee_can": mod.compute_coffee_can("TCS")}
    else:
        got = {"piotroski": mod.compute_piotroski_score(tkr, "AAPL"),
               "coffee_can": mod.compute_coffee_can(tkr, "AAPL")}
    got = json.loads(json.dumps(got, default=str))
    path = GOLDEN / f"daily_scans_{report}_{kind}.json"
    if not path.exists():  # recorded once from the ORIGINAL implementation
        path.write_text(json.dumps(got, indent=1, sort_keys=True))
    assert got == json.loads(path.read_text())

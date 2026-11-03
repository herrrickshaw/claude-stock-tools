"""Golden characterisation of each scanner's fundamental_scan() against a fake offline Ticker."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from conftest import SCANNERS

GOLDEN = Path(__file__).parent / "golden"


def _frame(rows: dict) -> pd.DataFrame:
    return pd.DataFrame(rows, index=["y0", "y1", "y2", "y3"]).T


def fake_ticker(healthy: bool = True):
    k = 1.0 if healthy else -1.0
    inc = _frame({
        "Net Income": [k * 150, k * 120, k * 100, k * 80],
        "Total Revenue": [2000, 1700, 1450, 1250],
        "Gross Profit": [900, 700, 580, 480],
        "EBIT": [400, 330, 280, 230],
        "Operating Income": [400, 330, 280, 230],
    })
    bal = _frame({
        "Total Assets": [2500, 2300, 2100, 1900],
        "Long Term Debt": [100, 200, 250, 300],
        "Current Assets": [1200, 1000, 900, 800],
        "Current Liabilities": [500, 500, 480, 450],
        "Total Current Assets": [1200, 1000, 900, 800],
        "Total Current Liabilities": [500, 500, 480, 450],
        "Share Issued": [100, 100, 100, 100],
        "Total Debt": [300, 400, 450, 500],
        "Stockholders Equity": [1500, 1300, 1100, 900],
        "Total Equity Gross Minority Interest": [1500, 1300, 1100, 900],
    })
    cf = _frame({
        "Operating Cash Flow": [220, 180, 150, 120],
        "Capital Expenditure": [-60, -50, -40, -30],
        "Free Cash Flow": [160, 130, 110, 90],
    })
    return SimpleNamespace(income_stmt=inc, balance_sheet=bal, cash_flow=cf,
                           fast_info=SimpleNamespace(market_cap=5e12), info={"marketCap": 5e12, "sector": "Tech"})


CALL = {
    "india": lambda m: m.fundamental_scan("TCS"),
    "us": lambda m: m.fundamental_scan("AAPL"),
    "europe": lambda m: m.fundamental_scan("ASML.AS"),
    "japan": lambda m: m.fundamental_scan("7203.T"),
    "korea": lambda m: m.fundamental_scan("005930", ".KS"),
}


@pytest.mark.parametrize("name", SCANNERS)
@pytest.mark.parametrize("healthy", [True, False])
def test_fundamental_scan_golden(load_scanner, monkeypatch, name, healthy):
    m = load_scanner(name)
    monkeypatch.setattr(m.yf, "Ticker", lambda *_a, **_k: fake_ticker(healthy))
    got = json.loads(json.dumps(CALL[name](m), default=str))
    path = GOLDEN / f"fundamental_{name}_{'ok' if healthy else 'bad'}.json"
    if not path.exists():  # recorded once from the ORIGINAL implementation
        path.write_text(json.dumps(got, indent=1, sort_keys=True))
    assert got == json.loads(path.read_text())


@pytest.mark.parametrize("name", SCANNERS)
def test_fundamental_scan_no_data(load_scanner, monkeypatch, name):
    m = load_scanner(name)
    empty = SimpleNamespace(income_stmt=pd.DataFrame(), financials=None, balance_sheet=None, cash_flow=None)
    monkeypatch.setattr(m.yf, "Ticker", lambda *_a, **_k: empty)
    res = CALL[name](m)
    assert res["f_score"] is None and res["error"]

"""Golden characterisation of each scanner's main() on synthetic OHLC data (offline, temp cwd)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

from conftest import SCANNERS

GOLDEN = Path(__file__).parent / "golden"
STAMP = re.compile(r"\d{2} \w{3} \d{4}  \d{2}:\d{2}:\d{2}")
FILE_DATE = re.compile(r"\d{8}_\d{4}")


def _bars(highs, lows, closes):
    return pd.DataFrame({"High": highs, "Low": lows, "Close": closes,
                         "Open": closes, "Volume": [1000] * len(closes)})


_BASE = [10, 12, 20, 15, 14, 13, 12, 13, 12, 13, 12, 13, 12, 13, 12, 13, 12, 13, 12, 12]


def synthetic_ohlc() -> dict:
    """Four frames: breakout (spike), second breakout, breakdown, and a monotone no-box series."""
    def frame(spike):
        highs = _BASE[:-1] + [max(_BASE[-1], spike)]
        closes = [h - 1 for h in highs]
        closes[-1] = spike
        return _bars(highs, [h - 3 for h in highs], closes)

    rising = list(range(10, 30))
    return {"A": frame(99), "B": frame(40), "C": frame(1.5), "D": _bars(rising, rising, rising)}


def fake_fund(key: str) -> dict:
    scores = {"A": 8, "B": 5}
    s = scores.get(key, 3)
    ok = key == "A"
    return {"f_score": s, "f_strong": s >= 7, "qualifies": ok, "cc_qualifies": "PASS" if ok else "FAIL",
            "cc_score": 4 if ok else 1, "cc_rev_cagr": 12.5, "cc_roce_avg": 18.0, "cc_debt_equity": 0.3,
            "market_cap_b": 5.0, "market_cap_b_jpy": 5.0, "market_cap_b_krw": 5.0,
            "name": f"Name {key}", "sector": "Tech", "error": ""}


def _install(m, name, monkeypatch):
    """Stub every network/universe edge of scanner ``name``; return (argv, kwargs-style runner)."""
    ohlc = synthetic_ohlc()
    if name == "india":
        monkeypatch.setattr(m, "_load_symbol_cache", lambda: (["A", "B", "C"], ["D"]))
        monkeypatch.setattr(m, "bulk_download_ohlc", lambda t, period="3mo": {
            f"{k}.NS" if k != "D" else "D.BO": v for k, v in ohlc.items()})
        monkeypatch.setattr(m, "fundamental_scan", lambda sym, sfx=".NS": fake_fund(sym))
        return lambda: m.main(workers=1)
    if name == "us":
        monkeypatch.setattr(m, "_load_symbol_cache", lambda: (["A", "B"], ["C", "D"]))
        monkeypatch.setattr(m, "bulk_download_ohlc", lambda t, period="3mo": ohlc)
        monkeypatch.setattr(m, "fundamental_scan", lambda sym: fake_fund(sym))
        return lambda: m.main(workers=1)
    if name == "europe":
        uni = [{"ticker": f"{k}.PA", "name": f"N{k}", "country": "FR", "sector": "S", "cap_band": "Large",
                "mcap_b": 3.0} for k in ohlc]
        monkeypatch.setattr(m, "build_universe", lambda **kw: uni)
        monkeypatch.setattr(m, "bulk_download_ohlc", lambda t, period="3mo": {f"{k}.PA": v for k, v in ohlc.items()})
        monkeypatch.setattr(m, "fundamental_scan", lambda tkr: fake_fund(tkr[0]))
    elif name == "japan":
        uni = [{"yf_ticker": f"{k}.T", "code": k, "name": f"N{k}", "sector": "S"} for k in ohlc]
        monkeypatch.setattr(m, "build_ticker_universe", lambda: uni)
        monkeypatch.setattr(m, "bulk_download_ohlc", lambda t, period="3mo": {f"{k}.T": v for k, v in ohlc.items()})
        monkeypatch.setattr(m, "fundamental_scan", lambda tkr: fake_fund(tkr[0]))
    else:
        uni = [{"code": k, "name": f"N{k}", "market": "KOSPI" if k in "AB" else "KOSDAQ", "yf_suffix": ".KS"}
               for k in ohlc]
        monkeypatch.setattr(m, "build_krx_universe", lambda kospi_only=False: uni)
        monkeypatch.setattr(m, "fetch_krx_ohlc", lambda u, months=3: dict(ohlc))
        monkeypatch.setattr(m, "fundamental_scan", lambda code, sfx=".KS": fake_fund(code))
    monkeypatch.setattr(sys, "argv", ["prog", "--workers", "1"])
    return m.main


@pytest.mark.parametrize("cap", [None, 1], ids=["all", "capped"])
@pytest.mark.parametrize("name", SCANNERS)
def test_main_golden(load_scanner, monkeypatch, capsys, tmp_path, name, cap):
    m = load_scanner(name)
    if cap:  # exercise the "freshest N breakouts" pre-filter
        monkeypatch.setattr(m, "MAX_FUND_CANDIDATES", cap)
    run = _install(m, name, monkeypatch)
    ret = run()
    out = capsys.readouterr().out
    out = FILE_DATE.sub("<DATE>", STAMP.sub("<TS>", out))
    sheets = {}
    for xlsx in tmp_path.rglob("*.xlsx"):
        book = pd.read_excel(xlsx, sheet_name=None)
        sheets = {s: json.loads(df.to_json(orient="records")) for s, df in sorted(book.items())}
    ret_json = json.loads(json.dumps(ret, default=str)) if ret is not None else None
    if isinstance(ret_json, dict):
        ret_json["excel_path"] = FILE_DATE.sub("<DATE>", ret_json.get("excel_path", ""))
    got = {"stdout": out, "sheets": sheets, "return": ret_json}
    path = GOLDEN / (f"main_{name}.json" if cap is None else f"main_{name}_cap{cap}.json")
    if not path.exists():  # recorded once from the ORIGINAL implementation
        path.write_text(json.dumps(got, indent=1, sort_keys=True))
    assert got == json.loads(path.read_text())

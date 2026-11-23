"""singapore_daily_report: fetch helpers, PEGY/breakout scan, displays, run / run_batch / Excel workbook."""
import pandas as pd
import pytest

PATH = "01_daily_reports/singapore_daily_report.py"


@pytest.fixture
def sg(load_script, tmp_path, monkeypatch):
    mod = load_script(PATH)
    out = tmp_path / "sg_out"
    out.mkdir()
    monkeypatch.setattr(mod, "DOWNLOAD_DIR", out)
    mod.out_dir = out
    return mod


class FI:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Boom:
    def __getattr__(self, name):
        raise RuntimeError("fi")


class T:
    def __init__(self, **kw):
        self.fast_info = FI()
        self.info = {}
        self.__dict__.update(kw)


def test_get_ticker_adds_si_suffix_once(sg, monkeypatch):
    seen = []
    monkeypatch.setattr(sg.yf, "Ticker", lambda s: seen.append(s) or s)
    assert sg._get_ticker(" d05 ") == "D05.SI"
    assert sg._get_ticker("bn4.si") == "BN4.SI"
    assert seen == ["D05.SI", "BN4.SI"]


def test_fetch_quote_sg_defaults_and_priority(sg):
    q = sg.fetch_quote(T(info=None))
    assert q["currency"] == "SGD" and q["exchange"] == "SGX" and q["country"] == "SG" and q["name"] == "—"
    fi = FI(last_price=3.5, previous_close=0, currency="")
    q = sg.fetch_quote(T(fast_info=fi, info={"currentPrice": 9.9, "previousClose": 3.4, "regularMarketPrice": 1,
                                             "shortName": "DBS", "exchange": "SES", "currency": "SGD"}))
    assert q["lastPrice"] == 3.5 and q["previousClose"] == 3.4 and q["name"] == "DBS" and q["exchange"] == "SES"
    q = sg.fetch_quote(T(info={"regularMarketPrice": 7.0}))
    assert q["lastPrice"] == 7.0
    q = sg.fetch_quote(T(fast_info=Boom()))
    assert q["lastPrice"] is None


def test_fetch_quote_info_error(sg):
    class BadInfo(T):
        @property
        def info(self):
            raise RuntimeError("429")

        @info.setter
        def info(self, v):
            pass

    assert sg.fetch_quote(BadInfo())["trailingPE"] is None


def test_fetch_historical_sg(sg, capsys):
    df = pd.DataFrame({"Close": [1.0]})
    seen = []

    class H(T):
        def history(self, period, auto_adjust):
            seen.append((period, auto_adjust))
            return df

    assert sg.fetch_historical(H()) is df and seen == [("1mo", True)]

    class NotDf(T):
        def history(self, **kw):
            return []

    assert sg.fetch_historical(NotDf()).empty

    class Err(T):
        def history(self, **kw):
            raise RuntimeError("bad")

    assert sg.fetch_historical(Err()).empty
    assert "[Historical error] bad" in capsys.readouterr().out


def test_fetch_corporate_actions_sg(sg):
    idx = pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01", "2024-05-01", "2024-06-01",
                          "2024-07-01"])
    acts = pd.DataFrame({"Dividends": [0.1, 0.0, 0.3, 0.4, 0.5, 0.6, 0.7],
                         "Stock Splits": [0.0, 2.0, 0.0, 3.0, 0.0, 4.0, 5.0]}, index=idx)
    out = sg.fetch_corporate_actions(T(actions=acts, calendar={"Earnings Date": ["2025-03-01"],
                                                                "Ex-Dividend Date": "2025-04-01"}))
    assert [d["amount"] for d in out["dividends"]] == [0.3, 0.4, 0.5, 0.6, 0.7]       # last five non-zero
    assert [s["ratio"] for s in out["splits"]] == [3.0, 4.0, 5.0]
    assert out["calendar"] == {"next_earnings": "2025-03-01", "ex_dividend_date": "2025-04-01",
                               "dividend_date": "—"}
    out = sg.fetch_corporate_actions(T(actions=None, calendar=None))
    assert out["dividends"] == [] and out["calendar"]["next_earnings"] == "—"

    class Bad(T):
        @property
        def actions(self):
            raise RuntimeError

        @property
        def calendar(self):
            raise RuntimeError

    assert sg.fetch_corporate_actions(Bad()) == {"dividends": [], "splits": [], "calendar": {}}


# ---- PEGY & breakout -------------------------------------------------------------------

def pegy_ticker(last, ma200, peg=1.5, dy=0.04, pe=12.345, raise_info=False):
    class P(T):
        @property
        def info(self):
            if raise_info:
                raise RuntimeError("info")
            return {"pegRatio": peg, "dividendYield": dy, "trailingPE": pe}

        @info.setter
        def info(self, v):
            pass

        def history(self, period):
            return pd.DataFrame()

    return P(fast_info=FI(last_price=last, two_hundred_day_average=ma200))


def test_pegy_adds_yield_in_percent_points(sg):
    r = sg.compute_pegy_and_breakout(pegy_ticker(11.0, 10.0), "D05.SI")
    assert r["symbol"] == "D05.SI" and r["cmp"] == 11.0 and r["ma_200"] == 10.0
    assert r["pe_ratio"] == 12.35 and r["peg_ratio"] == 1.5
    assert r["dividend_yield_%"] == 4.0
    assert r["pegy_adjusted"] == 5.5                                    # 1.5 + 0.04 * 100
    assert r["breakout"] == {"distance_from_200ma_%": 10.0, "signal": "Above 200-day MA - Uptrend"}


@pytest.mark.parametrize("last,signal", [
    (10.5, "Near 200-day MA - Consolidation"),            # exactly +5% is NOT an uptrend
    (10.51, "Above 200-day MA - Uptrend"),
    (9.5, "Near 200-day MA - Consolidation"),             # exactly -5% is NOT a downtrend
    (9.49, "Below 200-day MA - Downtrend"),
    (10.0, "Near 200-day MA - Consolidation"),
])
def test_breakout_signal_boundaries(sg, last, signal):
    r = sg.compute_pegy_and_breakout(pegy_ticker(last, 10.0), "X")
    assert r["breakout"]["signal"] == signal


def test_pegy_without_peg_or_yield_or_prices(sg):
    r = sg.compute_pegy_and_breakout(pegy_ticker(None, None, peg=None, dy=None, pe=None), "X")
    assert r["pegy_adjusted"] is None and r["peg_ratio"] is None and r["dividend_yield_%"] is None
    assert r["cmp"] is None and r["pe_ratio"] is None and r["ma_200"] is None
    assert r["breakout"] == {}
    r = sg.compute_pegy_and_breakout(pegy_ticker(10.0, 10.0, peg=2.0, dy=None), "X")
    assert r["pegy_adjusted"] == 2.0                                    # no yield -> PEG unchanged


def test_pegy_info_failure_is_an_error_dict(sg):
    r = sg.compute_pegy_and_breakout(pegy_ticker(10.0, 10.0, raise_info=True), "X")
    assert r == {"symbol": "X", "error": "Data fetch failed"}


# ---- displays --------------------------------------------------------------------------

def test_display_pegy(sg, capsys):
    r = sg.compute_pegy_and_breakout(pegy_ticker(11.0, 10.0), "D05.SI")
    sg.display_pegy_and_breakout(r)
    out = capsys.readouterr().out
    assert "PEGY RATIO & BREAKOUT ANALYSIS" in out and "S$11.00" in out and "4.00%" in out and "5.5" in out
    assert "10.00%" in out and "Above 200-day MA - Uptrend" in out
    sg.display_pegy_and_breakout({"error": "boom"})
    assert "boom" in capsys.readouterr().out
    sg.display_pegy_and_breakout({"cmp": 1.0, "dividend_yield_%": 1.0, "breakout": {}})
    out = capsys.readouterr().out
    assert "200-Day MA" in out and "Breakout Signal" not in out


@pytest.mark.xfail(strict=True, raises=TypeError,
                   reason="singapore_daily_report.py display_pegy_and_breakout: compute_pegy_and_breakout stores "
                          "dividend_yield_% = None for non-payers, and `{None:.2f}` raises TypeError")
def test_display_pegy_for_stock_without_dividend(sg, capsys):
    r = sg.compute_pegy_and_breakout(pegy_ticker(11.0, 10.0, dy=None), "X")
    sg.display_pegy_and_breakout(r)
    out = capsys.readouterr().out
    assert "0.00%" in out or "N/A" in out


QUOTE = {"name": "DBS", "exchange": "SES", "sector": "Fin", "currency": "SGD", "lastPrice": 40.0,
         "previousClose": 38.0, "dayHigh": 41.0, "dayLow": 39.0, "52wHigh": 45.0, "52wLow": 30.0, "200dAvg": 36.0,
         "volume": 1234567, "marketCap": 1.1e11, "trailingPE": 10.0, "forwardPE": 9.5, "priceToBook": 1.6,
         "dividendYield": 0.055}


def test_display_price_summary_sg(sg, capsys):
    sg.display_price_summary(QUOTE)
    out = capsys.readouterr().out
    assert "S$40.00" in out and "S$2.00" in out and "▲ 5.26%" in out and "1,234,567" in out
    assert "S$110.00B" in out and "5.50%" in out
    sg.display_price_summary({})
    out = capsys.readouterr().out
    assert out.count("N/A") >= 8


def test_display_corporate_actions_sg(sg, capsys):
    sg.display_corporate_actions({"calendar": {"ex_dividend_date": "2025-02-01", "dividend_date": "2025-02-20"},
                                  "dividends": [{"date": "2024-01-01", "amount": 0.5},
                                                {"date": "2024-07-01", "amount": 0.6}]})
    out = capsys.readouterr().out
    assert "2025-02-01" in out and out.index("2024-07-01") < out.index("2024-01-01")
    sg.display_corporate_actions({})
    out = capsys.readouterr().out
    assert "Recent Dividends" not in out


# ---- run / batch / scans only ----------------------------------------------------------

@pytest.fixture
def wired(sg, monkeypatch):
    calls = []
    monkeypatch.setattr(sg, "_get_ticker", lambda s: calls.append(("ticker", s)) or "TK")
    monkeypatch.setattr(sg, "fetch_quote", lambda t: dict(QUOTE))
    monkeypatch.setattr(sg, "fetch_historical", lambda t, period="1mo": calls.append(("hist", period))
                        or pd.DataFrame())
    monkeypatch.setattr(sg, "fetch_corporate_actions", lambda t: {"calendar": {}, "dividends": []})
    monkeypatch.setattr(sg, "compute_darvas_box", lambda df: {"signal": "IN_BOX", "box_top": 3.0,
                                                              "box_bottom": 2.0, "position_in_box_pct": 50.0,
                                                              "upside_to_top_pct": 10.0})
    monkeypatch.setattr(sg, "compute_piotroski_score", lambda t, s="": {"f_score": 7, "interpretation": "Strong"})
    monkeypatch.setattr(sg, "compute_pegy_and_breakout", lambda t, s="": {
        "pegy_adjusted": 5.5, "peg_ratio": 1.5, "dividend_yield_%": 4.0,
        "breakout": {"signal": "Above", "distance_from_200ma_%": 10.0}})
    for n in ("display_darvas_box", "display_piotroski_score", "display_pegy_and_breakout"):
        monkeypatch.setattr(sg, n, lambda r, _n=n: calls.append((_n, r)))
    return calls


def test_run_adds_suffix_and_skips_scans_by_default(sg, wired, capsys):
    out = sg.run("d05")
    assert out["symbol"] == "D05.SI" and out["scans"] == {}
    assert ("ticker", "D05.SI") in wired
    assert [c for c in wired if c[0] == "hist"] == [("hist", "1mo")]
    text = capsys.readouterr().out
    assert "SINGAPORE STOCK REPORT — D05.SI" in text and "LIVE QUOTE & VALUATION" in text
    assert "Data sourced from Yahoo Finance" in text


def test_run_with_scans(sg, wired, capsys):
    out = sg.run("BN4.SI", run_scans=True)
    assert out["symbol"] == "BN4.SI"
    assert out["scans"]["darvas"]["signal"] == "IN_BOX" and out["scans"]["piotroski"]["f_score"] == 7
    assert out["scans"]["pegy"]["pegy_adjusted"] == 5.5
    assert [c[0] for c in wired if c[0].startswith("display_")] == [
        "display_darvas_box", "display_piotroski_score", "display_pegy_and_breakout"]
    assert [c for c in wired if c[0] == "hist"] == [("hist", "1mo"), ("hist", "6mo")]
    assert "Running quantitative scans" in capsys.readouterr().out


def test_run_non_text_format_prints_no_report(sg, wired, capsys):
    out = sg.run("D05", output_format="excel")
    assert "quote" in out and "LIVE QUOTE" not in capsys.readouterr().out


def test_run_batch_default_and_failures(sg, monkeypatch, capsys):
    seen = []

    def fake_run(sym, output_format="excel", run_scans=False):
        seen.append((sym, output_format, run_scans))
        if sym.startswith("BAD"):
            raise RuntimeError("no data")
        return {"symbol": sym, "quote": {"lastPrice": 2.0, "previousClose": 1.0, "name": "N"}, "scans": {}}

    monkeypatch.setattr(sg, "run", fake_run)
    res = sg.run_batch(["A.SI", "BAD.SI", "C.SI"])
    assert [r["symbol"] for r in res] == ["A.SI", "C.SI"]
    text = capsys.readouterr().out
    assert "SG BATCH REPORT — 3 stocks" in text and "[02/3] BAD" in text
    assert "BAD failed: no data" in text and "Done.  2 OK, 1 failed." in text and "Failed: BAD" in text
    assert len(list(sg.out_dir.glob("sg_stock_report_*.xlsx"))) == 1
    seen.clear()
    sg.run_batch(["A.SI"], output_format="text")
    assert seen == [("A.SI", "text", False)]
    assert len(list(sg.out_dir.glob("sg_stock_report_*.xlsx"))) == 1        # text mode writes no new workbook


def test_run_batch_defaults_to_sti30_and_skips_excel_when_all_fail(sg, monkeypatch):
    calls = []

    def fake_run(sym, output_format="excel", run_scans=False):
        calls.append(sym)
        raise RuntimeError("x")

    monkeypatch.setattr(sg, "run", fake_run)
    assert sg.run_batch() == []
    assert calls == sg.STI_30
    assert list(sg.out_dir.iterdir()) == []


def result(sym, last, prev, scans=None, dy=0.05):
    return {"symbol": sym, "quote": {"name": sym, "sector": "Fin", "lastPrice": last, "previousClose": prev,
                                      "trailingPE": 10.0, "priceToBook": 1.2, "dividendYield": dy,
                                      "52wHigh": 50.0, "52wLow": 30.0, "volume": 1000, "marketCap": 1e9,
                                      "200dAvg": 35.0},
            "scans": scans or {}}


def test_excel_report_summary_and_sheets(sg):
    import openpyxl
    sg._write_excel_report([result("D05.SI", 44.0, 40.0), result("BN4.SI", 10.0, None, dy=None)])
    wb = openpyxl.load_workbook(next(sg.out_dir.glob("sg_stock_report_*.xlsx")))
    assert wb.sheetnames == ["Summary", "D05", "BN4"]
    ws = wb["Summary"]
    assert [c.value for c in ws[1]][:4] == ["Symbol", "Company", "Sector", "CMP (S$)"]
    assert ws.max_column == 11
    r2 = [c.value for c in ws[2]]
    assert r2[0] == "D05" and r2[3] == 44.0 and r2[4] == pytest.approx(10.0) and r2[7] == pytest.approx(5.0)
    assert r2[8:] == [50.0, 30.0, 1000]
    r3 = [c.value for c in ws[3]]
    assert r3[4] is None and r3[7] is None                              # no prev close / no yield
    d = wb["D05"]
    assert d["A1"].value == "D05 — D05.SI"
    labels = {d.cell(row=r, column=1).value: d.cell(row=r, column=2).value for r in range(4, 14)}
    assert labels["CMP (S$)"] == 44.0 and labels["Change %"] == pytest.approx(10.0)
    assert labels["Div Yield %"] == pytest.approx(5.0) and labels["200-Day MA"] == 35.0
    assert "TECHNICAL & FUNDAMENTAL SCANS" not in {c.value for c in d["A"]}


def test_excel_report_with_scans(sg):
    import openpyxl
    scans = {"darvas": {"signal": "BREAKOUT_BUY", "box_top": 9.0, "box_bottom": 7.0,
                        "position_in_box_pct": 120.0, "upside_to_top_pct": -5.0},
             "piotroski": {"f_score": 8, "interpretation": "Strong"},
             "pegy": {"peg_ratio": 1.1, "pegy_adjusted": 5.1, "dividend_yield_%": 4.0,
                      "breakout": {"signal": "Above", "distance_from_200ma_%": 12.0}}}
    sg._write_excel_report([result("D05.SI", 44.0, 40.0, scans), result("Z.SI", 1.0, 1.0)], include_scans=True)
    wb = openpyxl.load_workbook(next(sg.out_dir.glob("sg_stock_report_*.xlsx")))
    ws = wb["Summary"]
    assert ws.max_column == 17
    assert [c.value for c in ws[1]][11:] == ["Darvas Signal", "Darvas Top", "Darvas Bottom", "Piotroski Score",
                                              "PEGY Ratio", "Breakout Signal"]
    assert [c.value for c in ws[2]][11:] == ["BREAKOUT_BUY", 9.0, 7.0, 8, 5.1, "Above"]
    assert [c.value for c in ws[3]][11:] == [None] * 6      # no scan data (empty strings read back as blank cells)
    d = wb["D05"]
    cells = {d.cell(row=r, column=1).value: d.cell(row=r, column=2).value for r in range(1, d.max_row + 1)}
    assert cells["Signal"] == "BREAKOUT_BUY" and cells["Box Top"] == 9.0
    assert cells["Score (0-9)"] == 8 and cells["Interpretation"] == "Strong"
    assert cells["PEGY Adjusted"] == 5.1 and cells["Distance from 200MA %"] == 12.0


def test_run_scans_only_sg(sg, wired, capsys):
    out = sg.run_scans_only("d05")
    assert out["symbol"] == "D05.SI" and out["piotroski"]["f_score"] == 7 and out["pegy"]["pegy_adjusted"] == 5.5
    assert ("ticker", "D05.SI") in wired and ("hist", "6mo") in wired
    assert [c[0] for c in wired if c[0].startswith("display_")] == [
        "display_darvas_box", "display_piotroski_score", "display_pegy_and_breakout"]
    assert "SCANS — D05.SI" in capsys.readouterr().out

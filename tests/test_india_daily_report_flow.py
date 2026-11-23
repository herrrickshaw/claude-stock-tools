"""india_daily_report: NSE/BSE fetch wrappers, bulk-deal sources, OHLC fetch, displays, run / batch / summary."""
import importlib.util
import json
import os
import sys
import types
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "01_daily_reports/india_daily_report.py"


@pytest.fixture(scope="module")
def india_mod(tmp_path_factory):
    stubs = {n: types.ModuleType(n) for n in ("nse", "bse", "nsepython")}
    stubs["nse"].NSE = type("NSE", (), {})
    stubs["bse"].BSE = type("BSE", (), {})
    saved = {n: sys.modules.get(n) for n in stubs}
    sys.modules.update(stubs)
    work = tmp_path_factory.mktemp("india_flow_cwd")
    old = os.getcwd()
    os.chdir(work)
    try:
        spec = importlib.util.spec_from_file_location("cst_india_flow", PATH)
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


@pytest.fixture
def ind(india_mod, tmp_path, monkeypatch):
    out = tmp_path / "in_out"
    out.mkdir()
    monkeypatch.setattr(india_mod, "DOWNLOAD_DIR", out)
    monkeypatch.setattr(india_mod, "NSEPYTHON_OK", True)
    india_mod.out_dir = out
    return india_mod


class Fake:
    """Library stand-in: methods return canned values or raise when the value is an Exception."""
    def __init__(self, **vals):
        self.vals, self.calls = vals, []

    def __getattr__(self, name):
        if name not in self.vals:
            raise AttributeError(name)

        def fn(*a, **k):
            self.calls.append((name, a, k))
            v = self.vals[name]
            if isinstance(v, Exception):
                raise v
            return v(*a, **k) if callable(v) else v
        return fn


E = RuntimeError("boom")


# ---------------------------------------------------------------------------------------
# NSE wrappers
# ---------------------------------------------------------------------------------------

def test_nse_simple_fetchers_return_value_or_empty_on_error(ind, capsys):
    nse = Fake(quote={"a": 1}, equityQuote={"b": 2}, equityMetaInfo={"c": 3},
               actions=[1, 2, 3, 4, 5, 6, 7], boardMeetings=[1, 2, 3, 4, 5])
    assert ind.fetch_nse_quote(nse, "X") == {"a": 1}
    assert nse.calls[0] == ("quote", ("X",), {"section": "trade_info"})
    assert ind.fetch_nse_equity_quote(nse, "X") == {"b": 2}
    assert ind.fetch_nse_meta(nse, "X") == {"c": 3}
    assert ind.fetch_nse_actions(nse, "X") == [1, 2, 3, 4, 5]               # top five
    assert ind.fetch_nse_board_meetings(nse, "X") == [1, 2, 3]              # top three
    bad = Fake(quote=E, equityQuote=E, equityMetaInfo=E, actions=E, boardMeetings=E)
    assert ind.fetch_nse_quote(bad, "X") == {} and ind.fetch_nse_equity_quote(bad, "X") == {}
    assert ind.fetch_nse_meta(bad, "X") == {}
    assert ind.fetch_nse_actions(bad, "X") == [] and ind.fetch_nse_board_meetings(bad, "X") == []
    out = capsys.readouterr().out
    for tag in ("NSE quote error", "NSE equityQuote error", "NSE meta error", "NSE actions error",
                "NSE board meetings error"):
        assert tag in out
    none = Fake(quote=None, equityQuote=None, equityMetaInfo=None, actions=None, boardMeetings=None)
    assert ind.fetch_nse_quote(none, "X") == {} and ind.fetch_nse_actions(none, "X") == []
    assert ind.fetch_nse_board_meetings(none, "X") == []


def test_nse_historical_method_fallback(ind, capsys):
    only_second = Fake(fetch_equity_historical_data=[{"c": 1}])
    assert ind.fetch_nse_historical(only_second, "X", days=10) == [{"c": 1}]
    name, args, kw = only_second.calls[0]
    assert kw["symbol"] == "X" and round((kw["end"] - kw["start"]).total_seconds() / 86400) == 10
    both = Fake(equityHistoricalData=E, fetch_equity_historical_data=[{"c": 2}])
    assert ind.fetch_nse_historical(both, "X") == [{"c": 2}]                # first raises -> try the next name
    assert "equityHistoricalData" in capsys.readouterr().out
    first = Fake(equityHistoricalData=[{"c": 3}], fetch_equity_historical_data=[{"c": 4}])
    assert ind.fetch_nse_historical(first, "X") == [{"c": 3}]
    assert ind.fetch_nse_historical(Fake(), "X") == []                       # neither method exists
    assert ind.fetch_nse_historical(Fake(equityHistoricalData=None), "X") == []
    assert ind.fetch_nse_historical(Fake(equityHistoricalData=E, fetch_equity_historical_data=E), "X") == []


def test_nse_historical_default_window_is_30_days(ind):
    nse = Fake(equityHistoricalData=[])
    ind.fetch_nse_historical(nse, "X")
    kw = nse.calls[0][2]
    assert round((kw["end"] - kw["start"]).total_seconds() / 86400) == 30


def test_nse_option_chain(ind, capsys):
    nse = Fake(optionChain={"data": 1}, maxpain=22500, compileOptionChain={"pcr": 1.3})
    assert ind.fetch_nse_option_chain(nse, "X") == {"maxpain": 22500, "pcr": 1.3, "raw": {"data": 1}}
    assert ind.fetch_nse_option_chain(Fake(optionChain=None), "X") == {}
    assert ind.fetch_nse_option_chain(Fake(optionChain=E), "X") == {}
    assert "option chain error" in capsys.readouterr().out


# ---------------------------------------------------------------------------------------
# bulk deals
# ---------------------------------------------------------------------------------------

def test_normalize_bulk_df_renames_and_strips(ind):
    df = pd.DataFrame({" Symbol ": ["TCS"], "Client Name": ["ABC"], "Quantity Traded": ["1,26,922"],
                       "Trade Price / Wght. Avg. Price": ["3,500"], "Buy / Sell": ["BUY"]})
    out = ind._normalize_bulk_df(df)
    assert out[0]["symbol"] == "TCS" and out[0]["clientname"] == "ABC" and out[0]["buysell"] == "BUY"
    assert out[0]["quantity"] == "126922"                                   # Indian comma grouping stripped
    assert pd.isna(out[0]["price"])                                         # "3,500" is not numeric -> coerced to NaN
    df2 = pd.DataFrame({"symbol": ["A"], "client": ["C"], "quantity": [" 5,000 "], "wght price": [10.5],
                        "sell": ["SELL"]})
    out = ind._normalize_bulk_df(df2)
    assert out[0]["quantity"] == "5000" and out[0]["price"] == 10.5 and out[0]["buysell"] == "SELL"
    assert df2.columns.tolist() == ["symbol", "client", "quantity", "wght price", "sell"]       # input not mutated


def test_normalize_bulk_df_missing_columns(ind, capsys):
    assert ind._normalize_bulk_df(pd.DataFrame({"symbol": ["A"], "quantity": [1]})) == []
    out = capsys.readouterr().out
    assert "Missing columns after rename" in out and "clientname" in out and "Available: ['symbol', 'quantity']" in out


GOOD_DEALS = pd.DataFrame({"Symbol": ["TCS"], "Client Name": ["ABC"], "Quantity": [100], "Price": [10.0],
                           "Buy/Sell": ["BUY"]})


def test_bulk_deals_primary_nsepython(ind, monkeypatch):
    monkeypatch.setattr(ind, "nspy", types.SimpleNamespace(get_bulkdeals=lambda: GOOD_DEALS))
    nse = Fake(bulkdeals=[{"never": 1}])
    deals = ind.fetch_bulk_deals(nse)
    assert deals[0]["symbol"] == "TCS" and deals[0]["quantity"] == "100"
    assert nse.calls == []                                                   # primary succeeded -> no API call


def test_bulk_deals_falls_to_nse_api(ind, monkeypatch, capsys):
    def boom():
        raise RuntimeError("csv 404")

    monkeypatch.setattr(ind, "nspy", types.SimpleNamespace(get_bulkdeals=boom))
    nse = Fake(bulkdeals=[{"symbol": "X"}])
    assert ind.fetch_bulk_deals(nse) == [{"symbol": "X"}]
    assert "[nsepython bulk deals error] csv 404" in capsys.readouterr().out
    kw = nse.calls[0][2]
    assert kw["option_type"] == "ALL" and kw["fromdate"] == kw["todate"]
    # empty / non-frame primary results also fall through
    monkeypatch.setattr(ind, "nspy", types.SimpleNamespace(get_bulkdeals=lambda: pd.DataFrame()))
    assert ind.fetch_bulk_deals(Fake(bulkdeals=[{"symbol": "Y"}])) == [{"symbol": "Y"}]
    monkeypatch.setattr(ind, "nspy", types.SimpleNamespace(get_bulkdeals=lambda: pd.DataFrame({"x": [1]})))
    assert ind.fetch_bulk_deals(Fake(bulkdeals=[{"symbol": "Z"}])) == [{"symbol": "Z"}]   # unusable columns -> API


def test_bulk_deals_nsepython_disabled_goes_straight_to_api(ind, monkeypatch):
    monkeypatch.setattr(ind, "NSEPYTHON_OK", False)
    monkeypatch.setattr(ind, "nspy", types.SimpleNamespace(get_bulkdeals=lambda: pytest.fail("must not be used")))
    assert ind.fetch_bulk_deals(Fake(bulkdeals=[{"symbol": "Q"}])) == [{"symbol": "Q"}]


def test_bulk_deals_local_csv_fallback(ind, monkeypatch, capsys):
    monkeypatch.setattr(ind, "NSEPYTHON_OK", False)
    nse = Fake(bulkdeals=E)
    assert ind.fetch_bulk_deals(nse) == []                                   # no file: reported, empty list
    out = capsys.readouterr().out
    assert "NSE bulk deals API error" in out and "No local fallback found" in out
    GOOD_DEALS.to_csv(ind.DOWNLOAD_DIR / "bulk_deals.csv", index=False)
    assert ind.fetch_bulk_deals(Fake(bulkdeals=[]))[0]["symbol"] == "TCS"    # empty API list -> csv
    (ind.DOWNLOAD_DIR / "bulk_deals.csv").write_bytes(b"")
    assert ind.fetch_bulk_deals(Fake(bulkdeals=None)) == []                  # unreadable csv -> []
    assert "[Bulk deals CSV error]" in capsys.readouterr().out


# ---------------------------------------------------------------------------------------
# BSE
# ---------------------------------------------------------------------------------------

def test_fetch_bse_data(ind, capsys):
    bse = Fake(getScripCode="500325", quote={"LastRate": 1}, actions=list(range(8)))
    assert ind.fetch_bse_data(bse, "RELIANCE") == {"scrip_code": "500325", "quote": {"LastRate": 1},
                                                   "actions": [0, 1, 2, 3, 4]}
    assert bse.calls[2] == ("actions", (), {"scripcode": "500325"})
    assert ind.fetch_bse_data(Fake(getScripCode=None), "ZZZ") == {}
    assert "Scrip code not found for ZZZ" in capsys.readouterr().out
    res = ind.fetch_bse_data(Fake(getScripCode="1", quote=None, actions=None), "X")
    assert res == {"scrip_code": "1", "quote": {}, "actions": []}
    assert ind.fetch_bse_data(Fake(getScripCode=E), "X") == {}
    assert "[BSE error] boom" in capsys.readouterr().out
    partial = ind.fetch_bse_data(Fake(getScripCode="1", quote={"a": 1}, actions=E), "X")
    assert partial == {"scrip_code": "1", "quote": {"a": 1}}                 # failure after partial progress keeps it


# ---------------------------------------------------------------------------------------
# OHLC history
# ---------------------------------------------------------------------------------------

def test_fetch_ohlc_history_sorts_by_date_and_formats_range(ind, monkeypatch):
    seen = {}
    df = pd.DataFrame({"CH_TIMESTAMP": ["2025-01-03", "2025-01-01", "2025-01-02"], "CH_CLOSING_PRICE": [3, 1, 2]})

    def eh(sym, series, start, end):
        seen.update(sym=sym, series=series, start=start, end=end)
        return df

    monkeypatch.setattr(ind, "nspy", types.SimpleNamespace(equity_history=eh))
    out = ind.fetch_ohlc_history("TCS", days=30)
    assert out["CH_CLOSING_PRICE"].tolist() == [1, 2, 3] and out.index.tolist() == [0, 1, 2]
    s = datetime.strptime(seen["start"], "%d-%m-%Y")
    e = datetime.strptime(seen["end"], "%d-%m-%Y")
    assert (e - s).days == 30 and seen["series"] == "EQ" and seen["sym"] == "TCS"


def test_fetch_ohlc_history_edge_cases(ind, monkeypatch, capsys):
    nodate = pd.DataFrame({"x": [3, 1]})
    monkeypatch.setattr(ind, "nspy", types.SimpleNamespace(equity_history=lambda *a: nodate))
    assert ind.fetch_ohlc_history("X")["x"].tolist() == [3, 1]               # no date column -> untouched order
    monkeypatch.setattr(ind, "nspy", types.SimpleNamespace(equity_history=lambda *a: None))
    assert ind.fetch_ohlc_history("X").empty
    monkeypatch.setattr(ind, "nspy", types.SimpleNamespace(equity_history=lambda *a: pd.DataFrame()))
    assert ind.fetch_ohlc_history("X").empty

    def bad(*a):
        raise RuntimeError("nse down")

    monkeypatch.setattr(ind, "nspy", types.SimpleNamespace(equity_history=bad))
    assert ind.fetch_ohlc_history("X").empty
    assert "[Darvas OHLC fetch error] nse down" in capsys.readouterr().out
    monkeypatch.setattr(ind, "NSEPYTHON_OK", False)
    assert ind.fetch_ohlc_history("X").empty


# ---------------------------------------------------------------------------------------
# displays
# ---------------------------------------------------------------------------------------

EQ = {"priceInfo": {"lastPrice": 110.0, "previousClose": 100.0, "change": 10.0, "pChange": 10.0, "open": 101.0,
                    "intraDayHighLow": {"max": 111.0, "min": 99.0}, "vwap": 105.0,
                    "weekHighLow": {"max": 150.0, "min": 80.0},
                    "priceBand": {"upper": 121.0, "lower": 99.0}},
      "metadata": {"companyName": "Reliance", "isin": "INE002A01018", "series": "EQ", "industry": "Oil"},
      "securityInfo": {"faceValue": 10}}
TI = {"trade_info": {"totalTradedVolume": 1234567, "totalTradedValue": 5e9}}


def test_display_nse_price_full(ind, capsys):
    ind.display_nse_price(EQ, TI)
    out = capsys.readouterr().out
    assert "NSE — LIVE PRICE & TRADE INFO" in out and "Reliance" in out and "INE002A01018" in out
    assert "₹110.00" in out and "▲ 10.00%" in out and "₹111.00" in out and "₹99.00" in out
    assert "1,234,567" in out and "₹500.00 Cr" in out and "₹121.00" in out and "₹150.00" in out


def test_display_nse_price_fallback_keys_and_empty(ind, capsys):
    eq = {"priceInfo": {"ltp": 5.0, "prevClose": 4.0, "dayHigh": 6.0, "dayLow": 3.0, "totalTradedVolume": 99,
                        "totalTradedValue": 2e7},
          "metadata": {"symbol": "ABC"}}
    ti = {"trade_info": {"priceBandHigh": 7.0, "priceBandLow": 2.0}}
    ind.display_nse_price(eq, ti)
    out = capsys.readouterr().out
    assert "ABC" in out and "₹5.00" in out and "₹6.00" in out and "99" in out and "₹2.00 Cr" in out
    assert "₹7.00" in out and "₹2.00" in out
    ind.display_nse_price({}, {})
    out = capsys.readouterr().out
    assert "N/A" in out and " Cr" not in out


def test_display_bse_price(ind, capsys):
    ind.display_bse_price({})
    assert "No BSE data available." in capsys.readouterr().out
    ind.display_bse_price({"scrip_code": "500325", "quote": {"CurrRate": 10.0, "Open": 9.0, "DayHigh": 11.0,
                                                              "DayLow": 8.0, "PrevClose": 9.5, "TotalVolume": 12345,
                                                              "MarketCap": 5e9}})
    out = capsys.readouterr().out
    assert "500325" in out and "₹10.00" in out and "₹11.00" in out and "12,345" in out and "₹500.00 Cr" in out
    ind.display_bse_price({"quote": {"LastRate": 0.0, "Volume": None}})
    out = capsys.readouterr().out
    assert "₹0.00" in out                                                    # 0.0 is a value, not "missing"
    assert out.count("N/A") >= 4 and "Volume" in out and " 0\n" in out


def test_display_corporate_actions_and_board_meetings(ind, capsys):
    ind.display_corporate_actions([{"exDate": "01-Jan", "purpose": "Dividend"}, {"recordDate": "02-Jan",
                                                                                   "subject": "Bonus"}, {}],
                                  [{"Ex_Date": "03-Jan", "Purpose": "Split"}, {"exDate": "04-Jan"}])
    out = capsys.readouterr().out
    assert "01-Jan" in out and "Dividend" in out and "02-Jan" in out and "Bonus" in out
    assert "03-Jan" in out and "Split" in out and "04-Jan" in out
    ind.display_corporate_actions([], [])
    assert capsys.readouterr().out.count("No upcoming actions found.") == 2
    ind.display_board_meetings([])
    assert "No upcoming board meetings." in capsys.readouterr().out
    ind.display_board_meetings([{"meetingDate": "10-Feb", "purpose": "Results"},
                                {"bm_date": "11-Feb", "bm_purpose": "Dividend"}, {}])
    out = capsys.readouterr().out
    assert "10-Feb" in out and "Results" in out and "11-Feb" in out and "Dividend" in out


def test_display_option_chain(ind, capsys):
    ind.display_option_chain({})
    assert "Not an F&O stock" in capsys.readouterr().out
    ind.display_option_chain({"maxpain": 22500, "pcr": 1.25})
    out = capsys.readouterr().out
    assert "₹22,500.00" in out and "1.25" in out and "Bullish (PCR > 1)" in out
    ind.display_option_chain({"maxpain": 22500, "pcr": 1.0})
    assert "Bearish (PCR ≤ 1)" in capsys.readouterr().out                    # PCR == 1 is bearish
    ind.display_option_chain({"maxpain": 0, "pcr": 0.0, "raw": 1})
    out = capsys.readouterr().out
    assert "N/A" in out and "0.00" in out and "Bearish" in out               # pcr 0.0 is shown, max pain 0 is N/A
    ind.display_option_chain({"maxpain": 1.0})
    out = capsys.readouterr().out
    assert "Sentiment" not in out and "N/A" in out


def test_display_historical_summary(ind, capsys):
    ind.display_historical_summary([])
    assert "No historical data returned." in capsys.readouterr().out
    hist = [{"CH_CLOSING_PRICE": "100"}, {"close": 110}, {"CLOSE": 120}, {"ClosePrice": 130}, {"LTP": 140},
            {"CH_CLOSING_PRICE": "bad", "close": 150}, {"nothing": 1}, {"close": 0}]
    ind.display_historical_summary(hist)
    out = capsys.readouterr().out
    assert " 6\n" in out                                                      # 0 and unparsable-only records dropped
    assert "₹150.00" in out and "₹100.00" in out and "₹125.00" in out and "▲ 50.00%" in out
    ind.display_historical_summary([{"foo": 1}])
    out = capsys.readouterr().out
    assert "Insufficient closing-price data." in out and "['foo']" in out
    ind.display_historical_summary([{"close": 5}])
    assert "Insufficient closing-price data." in capsys.readouterr().out     # exactly one close is not enough
    ind.display_historical_summary([{"close": 5}, {"close": 5}])
    assert "0.00%" in capsys.readouterr().out


def test_display_bulk_deals(ind, capsys):
    deals = [{"symbol": " tcs ", "clientname": "ABC", "quantity": "100", "price": 10.0, "buysell": " BUY "},
             {"symbol": "TCS", "buysell": None},
             {"symbol": "INFY", "clientname": "Z", "quantity": "1", "price": 1, "buysell": "SELL"}]
    ind.display_bulk_deals(deals, "tcs")
    out = capsys.readouterr().out
    assert "BUY" in out and "100 shares @ ₹10.00  [ABC]" in out and "[—]" in out and "INFY" not in out
    ind.display_bulk_deals(deals, "WIPRO")
    assert "No bulk deals for WIPRO today." in capsys.readouterr().out


# ---------------------------------------------------------------------------------------
# run / batch / summary / scans only
# ---------------------------------------------------------------------------------------

class Ctx:
    log = []

    def __init__(self, **kw):
        Ctx.log.append((type(self).__name__, kw))

    def __enter__(self):
        return Fake()

    def __exit__(self, *a):
        return False


class FNSE(Ctx):
    pass


class FBSE(Ctx):
    pass


@pytest.fixture
def wired(ind, monkeypatch):
    calls = []
    Ctx.log = []
    monkeypatch.setattr(ind, "NSE", FNSE)
    monkeypatch.setattr(ind, "BSE", FBSE)
    monkeypatch.setattr(ind, "fetch_nse_equity_quote", lambda n, s: EQ)
    monkeypatch.setattr(ind, "fetch_nse_quote", lambda n, s: TI)
    monkeypatch.setattr(ind, "fetch_nse_meta", lambda n, s: {"isin": "X"})
    monkeypatch.setattr(ind, "fetch_nse_actions", lambda n, s: [{"purpose": "Div"}])
    monkeypatch.setattr(ind, "fetch_nse_board_meetings", lambda n, s: [{"purpose": "BM"}])
    monkeypatch.setattr(ind, "fetch_nse_historical", lambda n, s, days=30: [{"close": 1}, {"close": 2}])
    monkeypatch.setattr(ind, "fetch_bulk_deals", lambda n: [{"symbol": "RELIANCE", "clientname": "C", "quantity": "1",
                                                              "price": 1.0, "buysell": "BUY"},
                                                             {"symbol": "OTHER"}])
    monkeypatch.setattr(ind, "fetch_bse_data", lambda b, s: {"scrip_code": "1", "quote": {"LastRate": 1.0},
                                                             "actions": [{"purpose": "BSEDIV"}]})
    monkeypatch.setattr(ind, "fetch_nse_option_chain", lambda n, s: calls.append("oc") or
                        {"maxpain": 100, "pcr": 1.4, "raw": {"big": 1}})
    monkeypatch.setattr(ind, "fetch_ohlc_history", lambda s, days=90: calls.append(("ohlc", days)) or pd.DataFrame())
    monkeypatch.setattr(ind, "compute_darvas_box", lambda df: {"signal": "IN_BOX", "box_top": 3.0,
                                                               "box_bottom": 2.0})
    monkeypatch.setattr(ind, "compute_piotroski_score", lambda s: {"f_score": 6})
    monkeypatch.setattr(ind, "compute_coffee_can", lambda s: {"qualifies": True, "score": "6/6"})
    for n in ("display_darvas_box", "display_piotroski_score", "display_coffee_can"):
        monkeypatch.setattr(ind, n, lambda r, _n=n: calls.append(_n))
    return calls


def test_run_text_default(ind, wired, capsys):
    out = ind.run(" reliance ")
    text = capsys.readouterr().out
    assert out["symbol"] == "RELIANCE" and set(out) == {"symbol", "timestamp"}
    assert "oc" not in wired and not [c for c in wired if c == "display_darvas_box"]
    assert "DAILY STOCK REPORT — RELIANCE" in text and "NSE — LIVE PRICE" in text and "BSE — LIVE QUOTE" in text
    assert "BSEDIV" in text and "BM" in text and "F&O" not in text
    assert "No bulk deals" not in text and "Data: NSE India" in text
    assert Ctx.log == [("FNSE", {"download_folder": str(ind.DOWNLOAD_DIR), "server": False}),
                       ("FBSE", {"download_folder": str(ind.DOWNLOAD_DIR)})]
    assert list(ind.out_dir.iterdir()) == []


def test_run_text_with_fno_and_scans(ind, wired, capsys):
    ind.run("RELIANCE", show_fno=True, run_scans=True)
    text = capsys.readouterr().out
    assert "oc" in wired and ("ohlc", 90) in wired
    assert "F&O — OPTION CHAIN SUMMARY" in text and "Bullish" in text and "Running quantitative scans" in text
    assert [c for c in wired if isinstance(c, str) and c.startswith("display_")] == [
        "display_darvas_box", "display_piotroski_score", "display_coffee_can"]


def test_run_json_output(ind, wired, capsys):
    out = ind.run("reliance", show_fno=True, output_format="json", run_scans=True)
    files = list(ind.out_dir.glob("RELIANCE_report_*.json"))
    assert len(files) == 1
    saved = json.loads(files[0].read_text())
    assert saved["nse"]["optionChain"] == {"maxpain": 100, "pcr": 1.4}        # bulky raw chain dropped
    assert [d["symbol"] for d in saved["nse"]["bulkDeals"]] == ["RELIANCE"]    # only the queried symbol's deals
    assert saved["bse"]["scrip_code"] == "1" and saved["nse"]["meta"] == {"isin": "X"}
    assert saved["scans"]["piotroski"] == {"f_score": 6} and out["scans"]["coffee_can"]["score"] == "6/6"
    text = capsys.readouterr().out
    assert "JSON saved" in text and "NSE — LIVE PRICE" not in text


def test_run_json_without_scans_or_fno(ind, wired):
    out = ind.run("RELIANCE", output_format="json")
    assert out["scans"] == {} and out["nse"]["optionChain"] == {}


def test_run_batch_isolates_failures(ind, monkeypatch, capsys):
    def fake_run(sym, show_fno=False, output_format="json", run_scans=False):
        if sym == "BAD":
            raise RuntimeError("no net")
        return {"symbol": sym, "timestamp": "t",
                "nse": {"equityQuote": {"priceInfo": {"lastPrice": 10.0, "pChange": 1.5}}}}

    monkeypatch.setattr(ind, "run", fake_run)
    res = ind.run_nifty50_batch(symbols=["AAA", "BAD", "CCC"])
    assert [r["symbol"] for r in res] == ["AAA", "CCC"]
    text = capsys.readouterr().out
    assert "NIFTY 50 BATCH — 3 stocks" in text and "[02/3] BAD" in text
    assert "BAD failed: no net" in text and "Done.  2 OK, 1 failed." in text and "Failed: BAD" in text
    df = pd.read_csv(next(ind.out_dir.glob("nifty50_summary_*.csv")))
    assert df["Symbol"].tolist() == ["AAA", "CCC"] and df["LTP"].tolist() == [10.0, 10.0]


def test_run_batch_defaults_to_nifty50_and_passes_flags(ind, monkeypatch):
    seen = []
    monkeypatch.setattr(ind, "run", lambda s, show_fno, output_format, run_scans:
                        seen.append((s, show_fno, output_format, run_scans)) or {"symbol": s})
    ind.run_nifty50_batch()
    assert [s for s, *_ in seen] == ind.NIFTY_50_SYMBOLS and len(seen) == 50
    assert seen[0][1:] == (False, "json", False)
    seen.clear()
    ind.run_nifty50_batch(show_fno=True, output_format="text", run_scans=True, symbols=["X"])
    assert seen == [("X", True, "text", True)]
    assert list(ind.out_dir.glob("nifty50_scan_summary_*.csv"))


def test_summary_csv(ind):
    ind._write_summary_csv([])
    assert list(ind.out_dir.iterdir()) == []
    full = {"symbol": "AAA", "timestamp": "t",
            "nse": {"equityQuote": {"priceInfo": {"ltp": 7.0, "pChange": -1.0, "dayHigh": 8.0, "dayLow": 6.0,
                                                   "weekHighLow": {"max": 9.0, "min": 5.0}}}},
            "scans": {"darvas": {"signal": "IN_BOX", "box_top": 3.0, "box_bottom": 2.0},
                      "piotroski": {"f_score": 5}, "coffee_can": {"qualifies": False, "score": "2/6"}}}
    ind._write_summary_csv([full, {"symbol": "BBB"}], include_scans=True)
    df = pd.read_csv(next(ind.out_dir.glob("nifty50_scan_summary_*.csv")))
    row = df.iloc[0]
    assert row["LTP"] == 7.0 and row["Change%"] == -1.0 and row["DayHigh"] == 8.0 and row["DayLow"] == 6.0
    assert row["52wHigh"] == 9.0 and row["52wLow"] == 5.0
    assert row["Darvas_Signal"] == "IN_BOX" and row["Piotroski_Score"] == 5
    assert row["CoffeeCan"] == "NO" and row["CoffeeCan_Score"] == "2/6"
    assert pd.isna(df.iloc[1]["LTP"]) and df.iloc[1]["CoffeeCan"] == "NO"
    ind._write_summary_csv([{"symbol": "CCC"}])                                # without scans: no scan columns
    assert "Darvas_Signal" not in pd.read_csv(next(ind.out_dir.glob("nifty50_summary_*.csv"))).columns


def test_run_scans_only(ind, wired, capsys):
    out = ind.run_scans_only(" tcs ")
    assert set(out) == {"darvas", "piotroski", "coffee_can"} and out["piotroski"] == {"f_score": 6}
    assert ("ohlc", 90) in wired
    assert "SCANS — TCS" in capsys.readouterr().out
    assert [c for c in wired if isinstance(c, str) and c.startswith("display_")] == [
        "display_darvas_box", "display_piotroski_score", "display_coffee_can"]

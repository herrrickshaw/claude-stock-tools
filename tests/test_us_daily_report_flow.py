"""us_daily_report: yfinance fetch helpers, console displays, run / run_batch / summary CSV / scans-only."""
import json
import types

import pandas as pd
import pytest

PATH = "01_daily_reports/us_daily_report.py"


@pytest.fixture
def us(load_script, tmp_path, monkeypatch):
    mod = load_script(PATH)
    out = tmp_path / "us_out"
    out.mkdir()
    monkeypatch.setattr(mod, "DOWNLOAD_DIR", out)
    mod.out_dir = out
    return mod


class FI:
    """fast_info stand-in; any attribute not given is simply absent (getattr default)."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Boom:
    """Attribute access raises, like a flaky yfinance property."""
    def __getattr__(self, name):
        raise RuntimeError("fast_info blew up")


class T:
    def __init__(self, **kw):
        self.fast_info = FI()
        self.info = {}
        self.__dict__.update(kw)


# ---------------------------------------------------------------------------------------
# fetch_quote
# ---------------------------------------------------------------------------------------

def test_get_ticker_upper_strips(us, monkeypatch):
    seen = []
    monkeypatch.setattr(us.yf, "Ticker", lambda s: seen.append(s) or "T")
    assert us._get_ticker("  aapl ") == "T" and seen == ["AAPL"]


def test_fetch_quote_prefers_fast_info_then_info(us):
    fi = FI(last_price=101.0, previous_close=0, market_cap=5e9, year_change=0.0, currency=None)
    info = {"currentPrice": 99.0, "previousClose": 98.0, "marketCap": 1e9, "trailingPE": 20.0,
            "recommendationKey": "buy", "shortName": "Acme", "exchange": "NMS", "sector": "Tech",
            "numberOfAnalystOpinions": 12, "currency": "EUR"}
    q = us.fetch_quote(T(fast_info=fi, info=info))
    assert q["lastPrice"] == 101.0                       # fast_info wins
    assert q["previousClose"] == 98.0                    # falsy fast_info value (0) falls back to info
    assert q["marketCap"] == 5e9
    assert q["ytdChange"] == 0.0                         # no fallback for ytdChange
    assert q["currency"] == "EUR"                        # fast_info None -> info
    assert q["trailingPE"] == 20.0 and q["recommendation"] == "BUY" and q["analystCount"] == 12
    assert q["name"] == "Acme" and q["exchange"] == "NMS" and q["sector"] == "Tech"


def test_fetch_quote_defaults_when_everything_missing(us):
    q = us.fetch_quote(T(info=None))
    assert q["lastPrice"] is None and q["currency"] == "USD"
    assert q["recommendation"] == "—" and q["name"] == "—" and q["exchange"] == "—"
    assert q["country"] == "US" and q["sector"] == "—" and q["industry"] == "—"


def test_fetch_quote_survives_info_and_fast_info_errors(us):
    class BadInfo(T):
        @property
        def info(self):
            raise RuntimeError("429")

        @info.setter
        def info(self, v):
            pass

    q = us.fetch_quote(BadInfo(fast_info=Boom()))
    assert q["lastPrice"] is None and q["trailingPE"] is None and q["name"] == "—"


def test_fetch_quote_name_falls_back_to_long_name(us):
    q = us.fetch_quote(T(info={"longName": "Long Co"}))
    assert q["name"] == "Long Co"


# ---------------------------------------------------------------------------------------
# fetch_historical / actions / options / insiders / holders / news
# ---------------------------------------------------------------------------------------

def test_fetch_historical(us, capsys):
    df = pd.DataFrame({"Close": [1.0]})
    seen = []

    class H(T):
        def history(self, period, auto_adjust):
            seen.append((period, auto_adjust))
            return df

    assert us.fetch_historical(H()) is df
    assert us.fetch_historical(H(), period="6mo") is df
    assert seen == [("1mo", True), ("6mo", True)]

    class NotDf(T):
        def history(self, **kw):
            return None

    assert us.fetch_historical(NotDf()).empty

    class Err(T):
        def history(self, **kw):
            raise RuntimeError("down")

    assert us.fetch_historical(Err()).empty
    assert "[Historical error] down" in capsys.readouterr().out


def actions_frame():
    idx = pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01", "2024-05-01", "2024-06-01",
                          "2024-07-01"])
    return pd.DataFrame({"Dividends": [0.1, 0.2, 0.0, 0.3, 0.4, 0.5, 0.6],
                         "Stock Splits": [0.0, 0.0, 2.0, 0.0, 3.0, 0.0, 4.0]}, index=idx)


def test_fetch_corporate_actions_tails(us):
    cal = {"Earnings Date": ["2025-01-30", "2025-02-01"], "Ex-Dividend Date": "2025-02-10",
           "Dividend Date": "2025-02-20", "Earnings Average": 1.5, "Earnings Low": 1.0,
           "Earnings High": 2.0, "Revenue Average": 5e9}
    out = us.fetch_corporate_actions(T(actions=actions_frame(), calendar=cal))
    assert [d["date"] for d in out["dividends"]] == ["2024-02-01", "2024-04-01", "2024-05-01", "2024-06-01",
                                                     "2024-07-01"]          # zero dividends skipped; last 5
    assert out["dividends"][-1] == {"date": "2024-07-01", "amount": 0.6}
    assert [s["ratio"] for s in out["splits"]] == [2.0, 3.0, 4.0]            # last 3
    assert out["calendar"] == {"next_earnings": "2025-01-30", "ex_dividend_date": "2025-02-10",
                               "dividend_date": "2025-02-20", "eps_est_avg": 1.5, "eps_est_low": 1.0,
                               "eps_est_high": 2.0, "rev_est_avg": 5e9}


def test_fetch_corporate_actions_empty_and_failing(us):
    out = us.fetch_corporate_actions(T(actions=pd.DataFrame(), calendar={}))
    assert out["dividends"] == [] and out["splits"] == []
    assert out["calendar"]["next_earnings"] == "—" and out["calendar"]["ex_dividend_date"] == "—"
    out = us.fetch_corporate_actions(T(actions=None, calendar=None))
    assert out["calendar"]["next_earnings"] == "—"

    class Bad(T):
        @property
        def actions(self):
            raise RuntimeError("x")

        @property
        def calendar(self):
            raise RuntimeError("y")

    out = us.fetch_corporate_actions(Bad())
    assert out == {"dividends": [], "splits": [], "calendar": {}}


class Chain:
    def __init__(self, calls, puts):
        self.calls, self.puts = calls, puts


def opt_ticker(call_oi, put_oi, strikes=(90.0, 100.0, 110.0), expiries=("2025-01-17", "2025-02-21")):
    calls = pd.DataFrame({"strike": list(strikes), "openInterest": call_oi})
    puts = pd.DataFrame({"strike": list(strikes), "openInterest": put_oi})
    asked = []

    class O(T):
        options = expiries

        def option_chain(self, e):
            asked.append(e)
            return Chain(calls, puts)

    t = O()
    t.asked = asked
    return t


def test_fetch_options_pcr_one_is_bearish_and_max_pain(us):
    t = opt_ticker([10, 20, 30], [30, 20, 10])
    out = us.fetch_options_data(t)
    assert t.asked == ["2025-01-17"]                                  # nearest expiry only
    assert out == {"expiry_date": "2025-01-17", "call_oi": 60, "put_oi": 60, "pcr": 1.0, "max_pain": 100.0,
                   "sentiment": "Bearish (PCR≤1)"}


def test_fetch_options_bullish_pcr_and_nan_oi(us):
    t = opt_ticker([float("nan"), 10, 10], [20, 30, 10])
    out = us.fetch_options_data(t)
    assert out["call_oi"] == 20 and out["put_oi"] == 60
    assert out["pcr"] == 3.0 and out["sentiment"] == "Bullish (PCR>1)"


def test_fetch_options_zero_call_oi_has_no_pcr(us):
    out = us.fetch_options_data(opt_ticker([0, 0, 0], [5, 5, 5]))
    assert out["pcr"] is None and out["sentiment"] == "Bearish (PCR≤1)"


def test_fetch_options_no_expiries_or_error(us, capsys):
    class NoOpts(T):
        options = ()

    assert us.fetch_options_data(NoOpts()) == {}

    class Err(T):
        @property
        def options(self):
            raise RuntimeError("no chain")

    assert us.fetch_options_data(Err()) == {}
    assert "[Options error] no chain" in capsys.readouterr().out


def test_fetch_insiders(us, capsys):
    df = pd.DataFrame({"Insider": ["A", "B", "C", "D", "E", "F"],
                       "Position": ["CEO"] * 6, "Transaction": ["Sale"] * 6, "Shares": [1] * 6, "Value": [2] * 6,
                       "Start Date": pd.to_datetime([f"2024-0{i}-01" for i in range(1, 7)])})
    out = us.fetch_insider_trades(T(insider_transactions=df))
    assert [r["insider"] for r in out] == ["F", "E", "D", "C", "B"]       # newest 5, newest first
    assert out[0]["date"].startswith("2024-06-01") and out[0]["shares"] == 1
    assert us.fetch_insider_trades(T(insider_transactions=None)) == []
    assert us.fetch_insider_trades(T(insider_transactions=pd.DataFrame())) == []
    assert us.fetch_insider_trades(T(insider_transactions=pd.DataFrame({"x": [1]}))) == []   # KeyError swallowed
    assert "[Insider trades error]" in capsys.readouterr().out


def test_fetch_institutional(us, capsys):
    df = pd.DataFrame({"Holder": list("ABCDEFG"), "Shares": range(7)})
    out = us.fetch_institutional_holders(T(institutional_holders=df))
    assert [h["Holder"] for h in out] == list("ABCDE")
    assert us.fetch_institutional_holders(T(institutional_holders=None)) == []
    assert us.fetch_institutional_holders(T(institutional_holders=pd.DataFrame())) == []

    class Err(T):
        @property
        def institutional_holders(self):
            raise RuntimeError("zz")

    assert us.fetch_institutional_holders(Err()) == []
    assert "[Institutional holders error] zz" in capsys.readouterr().out


def test_fetch_news_both_formats(us, capsys):
    news = [{"content": {"title": "New", "pubDate": "2025-01-02T10:00:00Z"}},
            {"title": "Old", "providerPublishTime": 1700000000},
            {"title": "NoTime"}] + [{"title": f"n{i}"} for i in range(5)]
    out = us.fetch_news(T(news=news))
    assert len(out) == 5                                               # capped at 5
    assert out[0] == {"title": "New", "published": "2025-01-02T10:00"}   # truncated to 16 chars
    assert out[1]["title"] == "Old" and out[1]["published"] == "2023-11-14 22:13"
    assert out[2] == {"title": "NoTime", "published": ""}
    assert us.fetch_news(T(news=None)) == []

    class Err(T):
        @property
        def news(self):
            raise RuntimeError("n")

    assert us.fetch_news(Err()) == []
    assert "[News error] n" in capsys.readouterr().out


# ---------------------------------------------------------------------------------------
# displays
# ---------------------------------------------------------------------------------------

QUOTE = {"name": "Acme", "exchange": "NMS", "sector": "Tech", "industry": "Soft", "country": "US",
         "lastPrice": 110.0, "previousClose": 100.0, "open": 101.0, "dayHigh": 111.0, "dayLow": 99.0,
         "52wHigh": 120.0, "52wLow": 80.0, "ytdChange": 0.25, "50dAvg": 105.0, "200dAvg": 95.0,
         "volume": 1234567, "avgVolume": 2345678, "marketCap": 2.5e12, "trailingPE": 25.0, "forwardPE": 22.0,
         "pegRatio": 1.5, "priceToBook": 6.0, "trailingEps": 4.4, "forwardEps": 5.0, "dividendYield": 0.0123,
         "beta": 1.1, "shortRatio": 2.0, "targetMean": 130.0, "recommendation": "BUY", "analystCount": 30}


def test_display_price_summary_full(us, capsys):
    us.display_price_summary(QUOTE)
    out = capsys.readouterr().out
    assert "LIVE QUOTE & VALUATION" in out
    assert "$110.00" in out and "$10.00" in out and "▲ 10.00%" in out         # change and change %
    assert "▲ 25.00%" in out                                                  # ytd 0.25 -> 25%
    assert "1,234,567" in out and "2,345,678" in out and "$2.50T" in out
    assert "1.23%" in out and "BUY" in out and "30" in out


def test_display_price_summary_sparse(us, capsys):
    us.display_price_summary({})
    out = capsys.readouterr().out
    assert out.count("N/A") >= 10
    assert "YTD Change" in out and "—" in out


def test_display_price_summary_ytd_zero_is_shown(us, capsys):
    us.display_price_summary({"ytdChange": 0.0})
    line = [ln for ln in capsys.readouterr().out.splitlines() if "YTD Change" in ln][0]
    assert "N/A" not in line and "0.00%" in line


ACTIONS = {"calendar": {"next_earnings": "2025-01-30", "ex_dividend_date": "2025-02-10",
                        "dividend_date": "2025-02-20", "eps_est_avg": 1.5, "eps_est_low": 1.0, "eps_est_high": 2.0,
                        "rev_est_avg": 5e9},
           "dividends": [{"date": "2024-01-01", "amount": 0.5}, {"date": "2024-04-01", "amount": 0.6}],
           "splits": [{"date": "2020-08-31", "ratio": 4.0}]}


def test_display_corporate_actions(us, capsys):
    us.display_corporate_actions(ACTIONS)
    out = capsys.readouterr().out
    assert "$1.50 / $1.00 / $2.00" in out and "$5.00B" in out and "2025-01-30" in out
    assert out.index("2024-04-01") < out.index("2024-01-01")                   # newest first
    assert "4.0:1 split" in out


def test_display_corporate_actions_empty(us, capsys):
    us.display_corporate_actions({})
    out = capsys.readouterr().out
    assert "No dividend history." in out and "Recent Stock Splits" not in out
    assert out.count("N/A") == 2


def test_display_options_insiders_holders(us, capsys):
    us.display_options_summary({})
    assert "Options data not available." in capsys.readouterr().out
    us.display_options_summary({"expiry_date": "2025-01-17", "call_oi": 12345, "put_oi": 6789, "pcr": 0.55,
                                "sentiment": "Bearish", "max_pain": 100.0})
    out = capsys.readouterr().out
    assert "12,345" in out and "6,789" in out and "0.550" in out and "$100.00" in out
    us.display_options_summary({"expiry_date": "x"})
    assert capsys.readouterr().out.count("N/A") == 2                          # no pcr, no max pain
    us.display_insider_trades([], "AAPL")
    assert "No recent insider transactions found for AAPL." in capsys.readouterr().out
    us.display_insider_trades([{"transaction": "Sale", "insider": "Tim", "position": "CEO", "shares": 5000,
                                "value": 2.5e6, "date": "2024-05-01 00:00:00"}, {}], "AAPL")
    out = capsys.readouterr().out
    assert "2024-05-01" in out and "5,000" in out and "$2.50M" in out and "Tim [CEO]" in out
    us.display_institutional_holders([])
    assert "No institutional holder data available." in capsys.readouterr().out
    us.display_institutional_holders([{"Holder": "Vanguard", "pctHeld": 0.0834, "Shares": 1.2e9, "pctChange": 0.01},
                                      {"Holder": "Nobody"}])
    out = capsys.readouterr().out
    assert " 8.34%" in out and "1.20B" in out and "▲ 1.00%" in out
    assert "Nobody" in out and "Δ —" in out


def test_display_historical_and_news(us, capsys):
    us.display_historical_summary(None)
    assert "No historical data available." in capsys.readouterr().out
    us.display_historical_summary(pd.DataFrame({"Close": [100.0]}))
    assert "Insufficient closing-price data." in capsys.readouterr().out
    us.display_historical_summary(pd.DataFrame({"Close": [100.0, 110.0, 120.0]}))
    out = capsys.readouterr().out
    assert "▲ 20.00%" in out and "$120.00" in out and "$110.00" in out and " 3\n" in out
    us.display_historical_summary(pd.DataFrame({"Close": [0.0, 5.0]}))
    assert "0.00%" in capsys.readouterr().out                                 # zero first close -> 0 return
    us.display_news([], "AAPL")
    assert "No news available." in capsys.readouterr().out
    us.display_news([{"published": "2025-01-02 10:00", "title": "Hello"}, {}], "AAPL")
    out = capsys.readouterr().out
    assert "LATEST NEWS — AAPL" in out and "[2025-01-02 10:00] Hello" in out


# ---------------------------------------------------------------------------------------
# run / run_batch / summary / scans only
# ---------------------------------------------------------------------------------------

@pytest.fixture
def wired(us, monkeypatch):
    """Replace every network fetch + scan with canned data; record calls."""
    calls = []
    hist = pd.DataFrame({"Close": [100.0, 110.0]})
    monkeypatch.setattr(us, "_get_ticker", lambda s: calls.append(("ticker", s)) or "TICKER")
    monkeypatch.setattr(us, "fetch_quote", lambda t: dict(QUOTE))
    monkeypatch.setattr(us, "fetch_historical", lambda t, period="1mo": calls.append(("hist", period)) or hist)
    monkeypatch.setattr(us, "fetch_corporate_actions", lambda t: ACTIONS)
    monkeypatch.setattr(us, "fetch_options_data", lambda t: calls.append(("opts",)) or {"pcr": 1.2})
    monkeypatch.setattr(us, "fetch_insider_trades", lambda t: [{"insider": "I"}])
    monkeypatch.setattr(us, "fetch_institutional_holders", lambda t: [{"Holder": "H"}])
    monkeypatch.setattr(us, "fetch_news", lambda t: [{"title": "N", "published": "P"}])
    monkeypatch.setattr(us, "compute_darvas_box", lambda df: {"signal": "BREAKOUT_BUY", "box_top": 10.0,
                                                              "box_bottom": 8.0})
    monkeypatch.setattr(us, "compute_piotroski_score", lambda t, s="": {"f_score": 8})
    monkeypatch.setattr(us, "compute_coffee_can", lambda t, s="": {"qualifies": True, "score": "6/6"})
    for name in ("display_darvas_box", "display_piotroski_score", "display_coffee_can"):
        monkeypatch.setattr(us, name, lambda r, _n=name: calls.append((_n, r)))
    return calls


def test_run_text_default(us, wired, capsys):
    out = us.run("  aapl ")
    text = capsys.readouterr().out
    assert out["symbol"] == "AAPL" and set(out) == {"symbol", "timestamp"}
    assert ("opts",) not in wired                                           # options only on request
    assert [c for c in wired if c[0] == "hist"] == [("hist", "1mo")]
    assert "US STOCK REPORT — AAPL" in text and "LIVE QUOTE" in text and "Latest News — AAPL".upper() in text
    assert "OPTIONS SUMMARY" not in text
    assert not any(c[0].startswith("display_") for c in wired)              # no scans
    assert list(us.out_dir.iterdir()) == []                                  # text mode writes nothing


def test_run_text_with_options_and_scans(us, wired, capsys):
    us.run("AAPL", show_options=True, run_scans=True)
    text = capsys.readouterr().out
    assert ("opts",) in wired
    assert [c for c in wired if c[0] == "hist"] == [("hist", "1mo"), ("hist", "6mo")]
    assert "OPTIONS SUMMARY" in text and "Running quantitative scans" in text
    shown = [c[0] for c in wired if c[0].startswith("display_")]
    assert shown == ["display_darvas_box", "display_piotroski_score", "display_coffee_can"]


def test_run_json_writes_file(us, wired, capsys):
    out = us.run("aapl", output_format="json", run_scans=True, show_options=True)
    files = list(us.out_dir.glob("AAPL_report_*.json"))
    assert len(files) == 1
    saved = json.loads(files[0].read_text())
    assert saved["symbol"] == "AAPL" and saved["quote"]["name"] == "Acme"
    assert saved["options"] == {"pcr": 1.2}
    assert saved["scans"]["piotroski"] == {"f_score": 8} and saved["scans"]["darvas"]["signal"] == "BREAKOUT_BUY"
    assert len(saved["historical_30d"]) == 2
    assert out["scans"]["coffee_can"]["qualifies"] is True
    text = capsys.readouterr().out
    assert "JSON saved" in text and "LIVE QUOTE" not in text


def test_run_json_without_scans_and_empty_history(us, wired, monkeypatch):
    monkeypatch.setattr(us, "fetch_historical", lambda t, period="1mo": pd.DataFrame())
    out = us.run("AAPL", output_format="json")
    assert out["scans"] == {} and out["historical_30d"] == [] and out["options"] == {}


def test_run_unknown_format_prints_nothing_and_returns_basics(us, wired, capsys):
    out = us.run("AAPL", output_format="xml")
    assert set(out) == {"symbol", "timestamp"}
    assert "LIVE QUOTE" not in capsys.readouterr().out


def test_run_batch_isolates_failures_and_writes_summary(us, monkeypatch, capsys):
    def fake_run(sym, show_options=False, output_format="json", run_scans=False):
        if sym == "BAD":
            raise RuntimeError("nope")
        return {"symbol": sym, "timestamp": "t", "quote": {"lastPrice": 110.0, "previousClose": 100.0,
                                                           "name": sym, "dividendYield": 0.02}}

    monkeypatch.setattr(us, "run", fake_run)
    res = us.run_batch(["AAA", "BAD", "CCC"])
    assert [r["symbol"] for r in res] == ["AAA", "CCC"]
    text = capsys.readouterr().out
    assert "US BATCH REPORT — 3 stocks" in text and "[02/3] BAD" in text
    assert "BAD failed: nope" in text and "Done.  2 OK, 1 failed." in text and "Failed: BAD" in text
    csv = list(us.out_dir.glob("us_batch_summary_*.csv"))
    assert len(csv) == 1
    df = pd.read_csv(csv[0])
    assert df["Symbol"].tolist() == ["AAA", "CCC"] and df["Change%"].tolist() == [10.0, 10.0]
    assert df["DivYield%"].tolist() == [2.0, 2.0]


def test_run_batch_defaults_to_dow30_and_passes_flags(us, monkeypatch):
    seen = []
    monkeypatch.setattr(us, "run", lambda s, show_options, output_format, run_scans:
                        seen.append((s, show_options, output_format, run_scans)) or {"symbol": s})
    us.run_batch()
    assert [s for s, *_ in seen] == us.DOW_JONES_30
    assert seen[0][1:] == (False, "json", False)
    seen.clear()
    us.run_batch(symbols=["X"], show_options=True, output_format="text", run_scans=True)
    assert seen == [("X", True, "text", True)]
    assert list(us.out_dir.glob("us_scan_summary_*.csv"))


def test_summary_csv_scans_columns_and_empty(us):
    us._write_summary_csv([])
    assert list(us.out_dir.iterdir()) == []                                 # nothing written for no results
    data = {"symbol": "AAA", "quote": {"lastPrice": 50.0, "previousClose": 40.0},
            "scans": {"darvas": {"signal": "IN_BOX", "box_top": 55.0, "box_bottom": 45.0},
                      "piotroski": {"f_score": 7}, "coffee_can": {"qualifies": False, "score": "3/6"}}}
    us._write_summary_csv([data, {"symbol": "BBB"}], include_scans=True)
    df = pd.read_csv(next(us.out_dir.glob("us_scan_summary_*.csv")))
    assert df.loc[0, "Darvas_Signal"] == "IN_BOX" and df.loc[0, "Piotroski_Score"] == 7
    assert df.loc[0, "CoffeeCan"] == "NO" and df.loc[0, "CoffeeCan_Score"] == "3/6"
    assert df.loc[0, "Change%"] == 25.0
    assert pd.isna(df.loc[1, "Change%"]) and df.loc[1, "CoffeeCan"] == "NO"


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="us_daily_report.py _write_summary_csv: `round(chg_pct, 2) if chg_pct else None` "
                          "turns an unchanged price (0.0%) into a blank Change% cell")
def test_summary_csv_unchanged_price_reports_zero_change(us):
    us._write_summary_csv([{"symbol": "FLAT", "quote": {"lastPrice": 10.0, "previousClose": 10.0}}])
    df = pd.read_csv(next(us.out_dir.glob("us_batch_summary_*.csv")))
    assert df.loc[0, "Change%"] == 0.0


def test_run_scans_only(us, wired, capsys):
    out = us.run_scans_only(" msft ")
    assert out["symbol"] == "MSFT"
    assert out["piotroski"] == {"f_score": 8} and out["coffee_can"]["score"] == "6/6"
    assert ("hist", "6mo") in wired and ("ticker", "MSFT") in wired
    assert [c[0] for c in wired if c[0].startswith("display_")] == [
        "display_darvas_box", "display_piotroski_score", "display_coffee_can"]
    assert "SCANS — MSFT" in capsys.readouterr().out

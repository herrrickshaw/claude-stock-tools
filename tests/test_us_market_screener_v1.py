"""us_market_screener_v1: universe parsing, batch Darvas, SQLite checkpointing, output compilation, main."""
import sqlite3
import types

import pandas as pd
import pytest

PATH = "02_market_screeners/us_market_screener_v1.py"

NASDAQ_TXT = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc.|Q|N|N|100|N|N
TSTX|Test Co|Q|Y|N|100|N|N
QQQ|Invesco QQQ|G|N|N|100|Y|N
BADC|Bad Co|Q|N|D|100|N|N
BRK.A|Berkshire|Q|N|N|100|N|N
TOOLONG|Too long|Q|N|N|100|N|N
MSFT|Microsoft|Q|N|N|100|N|N
File Creation Time: 1105202612:00||||||
"""

OTHER_TXT = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
IBM|IBM Corp|N|IBM|N|100|N|IBM
SPY|SPDR|P|SPY|Y|100|N|SPY
AAPL|Apple dup|N|AAPL|N|100|N|AAPL
XYZ|Xyz Co|Z|XYZ|N|100|N|XYZ
ODD|Odd Co|Q|ODD|N|100|N|ODD
TEST|Tst|N|TEST|N|100|Y|TEST
"""


@pytest.fixture
def v1(load_script, tmp_path, monkeypatch):
    mod = load_script(PATH)
    monkeypatch.setattr(mod, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setattr(mod, "DB_FILE", tmp_path / "out" / "screener.db")
    monkeypatch.setattr(mod, "TQDM_OK", False)
    return mod


# ---- universe -------------------------------------------------------------------------

@pytest.fixture
def fake_nasdaq(v1, monkeypatch):
    urls = []

    class R:
        def __init__(self, text): self.text = text
        def raise_for_status(self): pass

    def get(url, timeout=None):
        urls.append((url, timeout))
        return R(NASDAQ_TXT if url == v1.NASDAQ_URL else OTHER_TXT)

    monkeypatch.setattr(v1.requests, "get", get)
    return urls


def test_universe_all_filters_and_dedupes(v1, fake_nasdaq):
    df = v1.fetch_stock_universe()
    assert list(df.columns) == ["Symbol", "Name", "Exchange"]
    assert df["Symbol"].tolist() == ["AAPL", "MSFT", "IBM", "XYZ", "ODD"]   # NASDAQ first, dup AAPL dropped
    assert df.set_index("Symbol")["Exchange"].to_dict() == {
        "AAPL": "NASDAQ", "MSFT": "NASDAQ", "IBM": "NYSE", "XYZ": "BATS", "ODD": "OTHER"}
    assert df.set_index("Symbol").loc["AAPL", "Name"] == "Apple Inc."
    assert [u for u, _ in fake_nasdaq] == [v1.NASDAQ_URL, v1.OTHER_URL]
    assert all(t == 15 for _, t in fake_nasdaq)


def test_universe_nasdaq_only_and_other_only(v1, fake_nasdaq):
    assert v1.fetch_stock_universe("nasdaq")["Symbol"].tolist() == ["AAPL", "MSFT"]
    assert v1.fetch_stock_universe("nyse")["Symbol"].tolist() == ["IBM", "AAPL", "XYZ", "ODD"]
    assert v1.fetch_stock_universe("amex")["Symbol"].tolist() == ["IBM", "AAPL", "XYZ", "ODD"]
    assert v1.fetch_stock_universe("other")["Symbol"].tolist() == ["IBM", "AAPL", "XYZ", "ODD"]


# ---- Darvas ---------------------------------------------------------------------------

BOX = [105, 104, 103, 104, 105, 104, 103, 104, 105, 104] * 3


def ohlc(closes):
    closes = [float(c) for c in closes]
    idx = pd.bdate_range("2026-01-01", periods=len(closes))
    return pd.DataFrame({"Open": closes, "High": [c * 1.01 for c in closes], "Low": [c * 0.99 for c in closes],
                         "Close": closes, "Volume": 1000}, index=idx)


def test_darvas_signals_and_fields(v1):
    up = v1._darvas_from_df(ohlc(BOX + [120]))
    assert up["signal"] == "BREAKOUT_BUY"
    assert up["box_top"] == pytest.approx(106.05, abs=0.01)
    assert up["current_price"] == 120.0
    assert up["upside_pct"] == pytest.approx((106.05 - 120) / 120 * 100, abs=0.01)
    assert v1._darvas_from_df(ohlc(BOX + [80]))["signal"] == "BREAKDOWN_SELL"
    inbox = v1._darvas_from_df(ohlc(BOX + [104]))
    assert inbox["signal"] == "IN_BOX"
    assert 0 <= inbox["pos_in_box"] <= 100


def test_darvas_insufficient_and_no_box(v1):
    assert v1._darvas_from_df(None)["signal"] == "INSUFFICIENT_DATA"
    assert v1._darvas_from_df(pd.DataFrame())["signal"] == "INSUFFICIENT_DATA"
    assert v1._darvas_from_df(ohlc(BOX[:7]))["signal"] == "INSUFFICIENT_DATA"      # < confirm + 5 rows
    assert v1._darvas_from_df(ohlc(BOX[:8]))["signal"] != "INSUFFICIENT_DATA"      # exactly confirm + 5
    assert v1._darvas_from_df(ohlc(range(100, 140)))["signal"] == "NO_BOX"          # strictly rising: no top
    missing = ohlc(BOX).drop(columns=["High"])
    assert v1._darvas_from_df(missing)["signal"] == "INSUFFICIENT_DATA"


def test_darvas_current_bar_excluded_from_box(v1):
    # A huge last-bar high must not become the box top, so it cannot hide the breakout.
    df = ohlc(BOX + [120])
    df.iloc[-1, df.columns.get_loc("High")] = 500.0
    assert v1._darvas_from_df(df)["box_top"] == pytest.approx(106.05, abs=0.01)


def batch_frame(symbols, closes_by_symbol):
    cols = {}
    for s in symbols:
        if s not in closes_by_symbol:
            continue
        df = ohlc(closes_by_symbol[s])
        for c in df.columns:
            cols[(s, c)] = df[c]
    return pd.DataFrame(cols)


def test_run_darvas_batch_multiindex_missing_and_batching(v1, monkeypatch):
    calls = []

    def download(tickers, **kw):
        calls.append((tickers, kw))
        batch = tickers.split()
        return batch_frame(batch, {"UP": BOX + [120], "DOWN": BOX + [80]})

    monkeypatch.setattr(v1.yf, "download", download)
    res = v1.run_darvas_batch(["UP", "GONE", "DOWN"], batch_size=2)
    assert [c[0] for c in calls] == ["UP GONE", "DOWN"]
    assert calls[0][1]["period"] == "6mo" and calls[0][1]["progress"] is False
    assert res["UP"]["signal"] == "BREAKOUT_BUY" and res["DOWN"]["signal"] == "BREAKDOWN_SELL"
    assert res["GONE"] == {"signal": "NO_DATA", "box_top": None, "box_bottom": None}


def test_run_darvas_batch_single_flat_frame_and_progress(v1, monkeypatch):
    monkeypatch.setattr(v1.yf, "download", lambda tickers, **kw: ohlc(BOX + [120]))
    ticks = []
    pbar = types.SimpleNamespace(update=lambda n: ticks.append(n))
    res = v1.run_darvas_batch(["ONE"], pbar=pbar)
    assert res["ONE"]["signal"] == "BREAKOUT_BUY" and ticks == [1]


def test_run_darvas_batch_download_failure_isolated(v1, monkeypatch):
    def download(tickers, **kw):
        if tickers.startswith("A"):
            raise RuntimeError("rate limited")
        return batch_frame(tickers.split(), {"B": BOX + [120]})

    monkeypatch.setattr(v1.yf, "download", download)
    ticks = []
    res = v1.run_darvas_batch(["A", "B"], batch_size=1, pbar=types.SimpleNamespace(update=ticks.append))
    assert "A" not in res and res["B"]["signal"] == "BREAKOUT_BUY"
    assert ticks == [1, 1]                      # failed batch still advances the bar by its size


def test_run_darvas_batch_per_symbol_error(v1, monkeypatch):
    monkeypatch.setattr(v1.yf, "download", lambda tickers, **kw: batch_frame(["X"], {"X": BOX}))
    monkeypatch.setattr(v1, "_darvas_from_df", lambda df: 1 / 0)
    assert v1.run_darvas_batch(["X"])["X"] == {"signal": "ERROR", "box_top": None, "box_bottom": None}


# ---- SQLite helpers ---------------------------------------------------------------------

def test_db_helpers(v1, tmp_path):
    con = v1.init_db(tmp_path / "x.db")
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    assert {"results", "progress"} <= tables
    assert v1.already_done(con, "darvas") == set()
    v1.mark_done(con, "AAA", "darvas")
    v1.mark_done(con, "BBB", "fundamentals")
    v1.mark_done(con, "AAA", "darvas")                      # idempotent
    assert v1.already_done(con, "darvas") == {"AAA"}
    assert v1.already_done(con, "fundamentals") == {"BBB"}
    v1.upsert_result(con, {"symbol": "AAA", "name": "A", "darvas_signal": "IN_BOX"})
    v1.upsert_result(con, {"symbol": "AAA", "f_score": 8})   # partial update keeps earlier columns
    row = con.execute("select name, darvas_signal, f_score from results where symbol='AAA'").fetchone()
    assert row == ("A", "IN_BOX", 8)
    assert con.execute("select count(*) from results").fetchone()[0] == 1
    con.close()
    # re-opening an existing database is safe
    v1.init_db(tmp_path / "x.db").close()


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="progress.symbol is the PRIMARY KEY, so a symbol can only be 'done' for one phase at a time "
                          "(INSERT OR REPLACE drops the earlier phase)")
def test_progress_tracks_each_phase_independently(v1, tmp_path):
    con = v1.init_db(tmp_path / "p.db")
    v1.mark_done(con, "AAA", "darvas")
    v1.mark_done(con, "AAA", "fundamentals")
    assert v1.already_done(con, "darvas") == {"AAA"}
    assert v1.already_done(con, "fundamentals") == {"AAA"}


# ---- scan_fundamentals_one -------------------------------------------------------------

def fake_scans(v1, monkeypatch, piotr, cc):
    monkeypatch.setattr(v1, "run_piotroski", lambda t: piotr)
    monkeypatch.setattr(v1, "run_coffee_can", lambda t: cc)


class Ticker:
    def __init__(self, info=None, fast=None, info_raises=False):
        self._info, self._fast, self._raises = info or {}, fast, info_raises
        self.fast_info = types.SimpleNamespace(last_price=fast) if fast is not None else None

    @property
    def info(self):
        if self._raises:
            raise RuntimeError("no info")
        return self._info


def test_scan_fundamentals_flattens_results(v1, monkeypatch):
    monkeypatch.setattr(v1.yf, "Ticker", lambda s: Ticker({"sector": "Tech", "industry": "SW", "marketCap": 5}, fast=9.5))
    fake_scans(v1, monkeypatch,
               {"f_score": 7, "components": {f"F{i}": 1 for i in range(1, 10)}},
               {"qualifies": True, "score": "6/6", "criteria": {f"C{i}": 1 for i in range(1, 7)}, "roe_avg": 0.3})
    row = v1.scan_fundamentals_one("AAA", "Alpha", "NYSE")
    assert (row["symbol"], row["name"], row["exchange"]) == ("AAA", "Alpha", "NYSE")
    assert (row["sector"], row["industry"], row["market_cap"], row["last_price"]) == ("Tech", "SW", 5, 9.5)
    assert row["f_score"] == 7 and row["f1"] == 1 and row["f9"] == 1
    assert row["cc_qualifies"] == 1 and row["cc_score"] == "6/6" and row["cc_c6"] == 1 and row["cc_roe_avg"] == 0.3
    assert "error" not in row and "scanned_at" in row


def test_scan_fundamentals_fallbacks(v1, monkeypatch):
    # info missing -> blanks; fast_info missing -> info prices; non-qualifier -> 0
    monkeypatch.setattr(v1.yf, "Ticker", lambda s: Ticker(info_raises=True))
    fake_scans(v1, monkeypatch, {}, {"qualifies": False})
    row = v1.scan_fundamentals_one("B", "Bee", "AMEX")
    assert row["sector"] == "" and row["market_cap"] is None and row["last_price"] is None
    assert row["cc_qualifies"] == 0 and row["f_score"] is None and row["f3"] is None and row["cc_c4"] is None
    monkeypatch.setattr(v1.yf, "Ticker", lambda s: Ticker({"currentPrice": 3.0}))
    assert v1.scan_fundamentals_one("B", "Bee", "AMEX")["last_price"] == 3.0
    monkeypatch.setattr(v1.yf, "Ticker", lambda s: Ticker({"regularMarketPrice": 4.0}))
    assert v1.scan_fundamentals_one("B", "Bee", "AMEX")["last_price"] == 4.0


def test_scan_fundamentals_error_rules(v1, monkeypatch):
    monkeypatch.setattr(v1.yf, "Ticker", lambda s: Ticker())
    fake_scans(v1, monkeypatch, {"error": "p-err"}, {"error": "c-err"})
    assert v1.scan_fundamentals_one("C", "Cee", "NYSE")["error"] == "p-err"       # both failed -> reported
    fake_scans(v1, monkeypatch, {"error": "p-err"}, {"qualifies": False})
    assert "error" not in v1.scan_fundamentals_one("C", "Cee", "NYSE")             # one ok -> not an error
    monkeypatch.setattr(v1.yf, "Ticker", lambda s: (_ for _ in ()).throw(ValueError("x" * 500)))
    row = v1.scan_fundamentals_one("C", "Cee", "NYSE")
    assert row["error"] == "x" * 200                                                 # truncated


# ---- compile_outputs -------------------------------------------------------------------

def seed(v1, tmp_path):
    con = v1.init_db(tmp_path / "c.db")
    rows = [
        dict(symbol="TRI", name="Triple", exchange="NYSE", sector="T", industry="I", market_cap=100.0, last_price=10.0,
             darvas_signal="BREAKOUT_BUY", darvas_upside=-5.0, f_score=8, cc_qualifies=1, cc_score="6/6", cc_roe_avg=0.3),
        dict(symbol="BRK", name="BreakOnly", exchange="NYSE", market_cap=50.0, darvas_signal="BREAKOUT_BUY",
             darvas_upside=-9.0, f_score=6, cc_qualifies=0),
        dict(symbol="PIO", name="Piotroski7", exchange="NASDAQ", market_cap=70.0, darvas_signal="IN_BOX", f_score=7,
             cc_qualifies=1, cc_score="5/6"),
        dict(symbol="NAF", name="NoF", exchange="NASDAQ", market_cap=10.0, darvas_signal="BREAKOUT_BUY",
             darvas_upside=-1.0, cc_qualifies=1),
    ]
    for r in rows:
        v1.upsert_result(con, r)
    return con


def test_compile_outputs_files_and_filters(v1, tmp_path, capsys):
    v1.OUTPUT_DIR.mkdir(parents=True)
    con = seed(v1, tmp_path)
    v1.compile_outputs(con, "20260101")
    out = v1.OUTPUT_DIR
    summary = pd.read_csv(out / "scan_summary_20260101.csv")
    assert len(summary) == 4
    darvas = pd.read_csv(out / "darvas_breakouts_20260101.csv")
    assert darvas["symbol"].tolist() == ["BRK", "TRI", "NAF"]                # BREAKOUT_BUY, ascending upside
    pio = pd.read_csv(out / "strong_piotroski_20260101.csv")
    assert pio["symbol"].tolist() == ["TRI", "PIO"]                          # f_score >= 7, descending; NaN excluded
    cc = pd.read_csv(out / "coffee_can_20260101.csv")
    assert cc["symbol"].tolist() == ["TRI", "PIO", "NAF"]                    # market cap descending
    triple = pd.read_csv(out / "triple_hits_20260101.csv")
    assert triple["symbol"].tolist() == ["TRI"]
    printed = capsys.readouterr().out
    assert "Triple hits (1 stocks)" in printed and "Triple" in printed
    assert "Summary (4 rows)" in printed


def test_compile_outputs_piotroski_threshold_is_inclusive(v1, tmp_path):
    v1.OUTPUT_DIR.mkdir(parents=True)
    con = v1.init_db(tmp_path / "t.db")
    v1.upsert_result(con, dict(symbol="S6", f_score=6))
    v1.upsert_result(con, dict(symbol="S7", f_score=7))
    v1.compile_outputs(con, "d")
    assert pd.read_csv(v1.OUTPUT_DIR / "strong_piotroski_d.csv")["symbol"].tolist() == ["S7"]


def test_compile_outputs_empty_db(v1, tmp_path, capsys):
    v1.OUTPUT_DIR.mkdir(parents=True)
    v1.compile_outputs(v1.init_db(tmp_path / "e.db"), "d")
    assert "No results in database yet." in capsys.readouterr().out
    assert list(v1.OUTPUT_DIR.iterdir()) == []


# ---- main -----------------------------------------------------------------------------

def universe_df():
    return pd.DataFrame({"Symbol": ["AAA", "BBB", "CCC"], "Name": ["A", "B", "C"], "Exchange": ["NYSE", "NASDAQ", "AMEX"]})


@pytest.fixture
def wired(v1, monkeypatch):
    calls = {"universe": [], "darvas": [], "fund": []}

    def universe(exchange):
        calls["universe"].append(exchange)
        return universe_df()

    def darvas(symbols, batch_size=500, pbar=None):
        calls["darvas"].append((list(symbols), batch_size))
        return {s: {"signal": "BREAKOUT_BUY", "box_top": 1.0, "box_bottom": 0.5, "current_price": 2.0,
                    "upside_pct": -3.0, "pos_in_box": 99.0} for s in symbols}

    def fund(sym, name, exch):
        calls["fund"].append(sym)
        return {"symbol": sym, "name": name, "exchange": exch, "f_score": 8, "cc_qualifies": 1, "cc_score": "6/6"}

    monkeypatch.setattr(v1, "fetch_stock_universe", universe)
    monkeypatch.setattr(v1, "run_darvas_batch", darvas)
    monkeypatch.setattr(v1, "scan_fundamentals_one", fund)
    monkeypatch.setattr(v1.time, "sleep", lambda s: None)
    return calls


def test_main_full_pipeline(v1, wired, capsys):
    v1.main(exchange="nasdaq", workers=2, delay=0)
    assert wired["universe"] == ["nasdaq"]
    assert wired["darvas"] == [(["AAA", "BBB", "CCC"], 500)]
    assert sorted(wired["fund"]) == ["AAA", "BBB", "CCC"]
    assert (v1.OUTPUT_DIR / "stock_universe.csv").exists()
    triple_files = list(v1.OUTPUT_DIR.glob("triple_hits_*.csv"))
    assert len(triple_files) == 1 and len(pd.read_csv(triple_files[0])) == 3
    con = sqlite3.connect(v1.DB_FILE)
    assert con.execute("select count(*) from progress where phase='fundamentals'").fetchone()[0] == 3
    out = capsys.readouterr().out
    assert "Phase 1 done.  Breakouts: 3  In-box: 0  Breakdown: 0" in out


def test_main_limit_and_darvas_only(v1, wired, capsys):
    v1.main(limit=2, darvas_only=True, batch_size=7)
    assert wired["darvas"] == [(["AAA", "BBB"], 7)]
    assert wired["fund"] == []
    out = capsys.readouterr().out
    assert "Limited to 2 stocks (--limit 2)" in out and "--darvas-only set" in out
    assert list(v1.OUTPUT_DIR.glob("darvas_breakouts_*.csv"))


def test_main_resume_uses_cache_and_skips_done(v1, wired, capsys):
    v1.main(darvas_only=True)
    wired["darvas"].clear(); wired["universe"].clear()
    v1.main(resume=True, darvas_only=True)
    assert wired["universe"] == []                                   # cached universe reused
    assert wired["darvas"] == [([], 500)]                            # everything already done
    assert "Loaded cached universe: 3 stocks" in capsys.readouterr().out
    # resume without a cache file fetches again
    (v1.OUTPUT_DIR / "stock_universe.csv").unlink()
    v1.main(resume=True, darvas_only=True)
    assert wired["universe"] == ["all"]


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="progress.symbol is the PRIMARY KEY, so mark_done(sym, 'fundamentals') replaces the "
                          "'darvas' row; after a full run --resume re-scans everything")
def test_main_resume_skips_completed_fundamentals(v1, wired):
    v1.main(workers=1, delay=0)
    wired["fund"].clear()
    v1.main(resume=True, workers=1, delay=0)
    assert wired["fund"] == []


def test_darvas_close_equal_to_box_top_is_still_in_box(v1):
    highs = [105.0] * 10 + [110.0] + [105.0] * 9
    closes = [100.0] * 19 + [110.0]
    df = pd.DataFrame({"Open": closes, "High": highs, "Low": [95.0] * 20, "Close": closes, "Volume": 1},
                      index=pd.bdate_range("2026-01-01", periods=20))
    r = v1._darvas_from_df(df)
    assert r["box_top"] == 110.0 and r["box_bottom"] == 95.0
    assert r["signal"] == "IN_BOX"                      # strictly above the top is required to break out
    df.iloc[-1, df.columns.get_loc("Close")] = 110.01
    assert v1._darvas_from_df(df)["signal"] == "BREAKOUT_BUY"

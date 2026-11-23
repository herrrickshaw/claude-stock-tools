"""Bulk OHLC download, the five-stage ``main`` pipeline and Excel output of the five market scanners
(US / India / Europe / Japan / Korea). yfinance, pykrx and the universe fetchers are faked; every output
path is redirected into tmp_path."""
import sys
import types

import pandas as pd
import pytest

US = "02_market_screeners/us_market_scan.py"
IN = "02_market_screeners/india_market_scan.py"
EU = "02_market_screeners/europe_market_scan.py"
JP = "02_market_screeners/japan_market_scan.py"
KR = "02_market_screeners/korea_market_scan.py"
BULK = [US, IN, EU, JP]          # modules that have bulk_download_ohlc (yf.download based)


def ohlc(last_close, n=30, top=10.0, bottom=8.0):
    """30 flat bars (high 9, low 8) with a confirmed box top of ``top`` at bar 2; last bar closes at last_close."""
    highs = [9.0] * n
    highs[2] = top
    lows = [bottom] * n
    closes = [8.5] * n
    highs[-1] = max(last_close, 9.0)
    closes[-1] = last_close
    return pd.DataFrame({"High": highs, "Low": lows, "Close": closes})


def multi(frames):
    """yfinance-style frame: MultiIndex columns (Price, Ticker)."""
    return pd.concat(frames, axis=1, keys=list(frames)).swaplevel(axis=1).sort_index(axis=1)


class FakeYF:
    def __init__(self, result=None, exc=None):
        self.calls, self.result, self.exc = [], result, exc

    def download(self, batch, **kw):
        self.calls.append((list(batch), kw))
        if self.exc:
            raise self.exc
        res = self.result
        return res(batch) if callable(res) else res


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Redirect a module's output dir + caches into tmp_path and silence sleeping."""
    def _apply(mod):
        d = tmp_path / "scan_out"
        d.mkdir(exist_ok=True)
        monkeypatch.setattr(mod, "DOWNLOAD_DIR", d)
        for name in ("_CACHE_FILE", "_SYMBOL_CACHE"):
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, d / ".cache.json")
        sleeps = []
        monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(s))
        return d, sleeps
    return _apply


# ---------------------------------------------------------------------------------------
# bulk_download_ohlc
# ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", BULK)
def test_bulk_multiindex_keeps_usable_tickers_only(load_script, sandbox, monkeypatch, path):
    mod = load_script(path)
    _, sleeps = sandbox(mod)
    short = ohlc(10).iloc[:mod.DARVAS_CONFIRM + 4]      # 7 bars: one short of the minimum
    exact = ohlc(10).iloc[:mod.DARVAS_CONFIRM + 5]      # 8 bars: exactly enough
    raw = multi({"AAA": ohlc(10), "SHORT": short, "EXACT": exact})
    fake = FakeYF(result=raw)
    monkeypatch.setattr(mod, "yf", fake)
    out = mod.bulk_download_ohlc(["AAA", "SHORT", "EXACT", "MISSING"], period="1mo")
    assert sorted(out) == ["AAA", "EXACT"]
    assert len(out["EXACT"]) == mod.DARVAS_CONFIRM + 5
    assert fake.calls == [(["AAA", "SHORT", "EXACT", "MISSING"],
                           {"period": "1mo", "auto_adjust": True, "threads": True, "progress": False})]
    assert sleeps == []                                   # single batch -> no inter-batch sleep


@pytest.mark.parametrize("path", BULK)
def test_bulk_default_period_is_three_months(load_script, sandbox, monkeypatch, path):
    mod = load_script(path)
    sandbox(mod)
    fake = FakeYF(result=multi({"AAA": ohlc(10)}))
    monkeypatch.setattr(mod, "yf", fake)
    mod.bulk_download_ohlc(["AAA"])
    assert fake.calls[0][1]["period"] == "3mo"


@pytest.mark.parametrize("path", BULK)
def test_bulk_batches_and_sleep_between_batches(load_script, sandbox, monkeypatch, path, capsys):
    mod = load_script(path)
    _, sleeps = sandbox(mod)
    monkeypatch.setattr(mod, "BATCH_SIZE", 2)
    fake = FakeYF(result=lambda b: multi({t: ohlc(10) for t in b}))
    monkeypatch.setattr(mod, "yf", fake)
    out = mod.bulk_download_ohlc(["A", "B", "C", "D", "E"])
    assert [c[0] for c in fake.calls] == [["A", "B"], ["C", "D"], ["E"]]
    assert sorted(out) == ["A", "B", "C", "D", "E"]
    assert sleeps == [mod.SLEEP_BETWEEN] * 2             # between batches, not after the last
    text = capsys.readouterr().out
    assert "in 3 batches" in text
    assert "Batch 3/3" in text and "OK (1 usable)" in text


@pytest.mark.parametrize("path", BULK)
def test_bulk_empty_frame_is_skipped(load_script, sandbox, monkeypatch, path, capsys):
    mod = load_script(path)
    sandbox(mod)
    monkeypatch.setattr(mod, "yf", FakeYF(result=pd.DataFrame()))
    assert mod.bulk_download_ohlc(["A"]) == {}
    assert "empty" in capsys.readouterr().out


@pytest.mark.parametrize("path", BULK)
def test_bulk_single_ticker_flat_frame_goes_to_first_ticker(load_script, sandbox, monkeypatch, path):
    mod = load_script(path)
    sandbox(mod)
    flat = ohlc(10)
    monkeypatch.setattr(mod, "yf", FakeYF(result=flat))
    out = mod.bulk_download_ohlc(["ONLY"])
    assert list(out) == ["ONLY"]
    assert out["ONLY"] is flat


@pytest.mark.parametrize("path", BULK)
def test_bulk_exception_is_isolated_per_batch(load_script, sandbox, monkeypatch, path, capsys):
    mod = load_script(path)
    _, sleeps = sandbox(mod)
    monkeypatch.setattr(mod, "BATCH_SIZE", 1)
    state = {"n": 0}

    def result(batch):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("rate limited")
        return multi({batch[0]: ohlc(10)}) if len(batch) > 1 else ohlc(10)

    monkeypatch.setattr(mod, "yf", FakeYF(result=result))
    out = mod.bulk_download_ohlc(["A", "B"])
    assert list(out) == ["B"]                             # batch 1 failed, batch 2 survived
    assert "ERROR — rate limited" in capsys.readouterr().out
    assert len(sleeps) == 1


# ---------------------------------------------------------------------------------------
# save_excel (US / India)
# ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("path,tag", [(US, "us"), (IN, "indian")])
def test_save_excel_sorts_descending_and_names_file(load_script, sandbox, path, tag):
    mod = load_script(path)
    d, _ = sandbox(mod)
    all_rows = [{"Symbol": "A", "Change%": 1.0}, {"Symbol": "B", "Change%": 5.0}, {"Symbol": "C", "Change%": 3.0}]
    darvas = [{"Symbol": "A", "Upside_to_Top%": 2.0}, {"Symbol": "B", "Upside_to_Top%": 9.0}]
    fund = [{"Symbol": "A", "Piotroski_Score": 4}, {"Symbol": "B", "Piotroski_Score": 8}]
    p = mod.save_excel(all_rows, darvas, fund, [], tag=tag)
    assert p.parent == d and p.name.startswith(f"{tag}_full_scan_") and p.suffix == ".xlsx"
    book = sheets(p)
    assert list(book) == ["All_Stocks", "Darvas_Signals", "Fundamentals", "Triple_Hits"]
    assert book["All_Stocks"]["Symbol"].tolist() == ["B", "C", "A"]
    assert book["Darvas_Signals"]["Symbol"].tolist() == ["B", "A"]
    assert book["Fundamentals"]["Symbol"].tolist() == ["B", "A"]
    assert book["Triple_Hits"].empty               # empty rows -> empty sheet, not a crash


@pytest.mark.parametrize("path,tag", [(US, "us"), (IN, "indian")])
def test_save_excel_default_tag_and_missing_sort_column(load_script, sandbox, path, tag):
    mod = load_script(path)
    sandbox(mod)
    p = mod.save_excel([{"Symbol": "Z"}, {"Symbol": "A"}], [], [], [])    # no Change% column to sort on
    assert p.name.startswith(f"{tag}_full_scan_")
    assert pd.read_excel(p, sheet_name="All_Stocks")["Symbol"].tolist() == ["Z", "A"]


# ---------------------------------------------------------------------------------------
# main(): US
# ---------------------------------------------------------------------------------------

def fund_result(f_score=8, strong=True, qualifies=True, name="Acme", **extra):
    return {"f_score": f_score, "f_strong": strong, "qualifies": qualifies, "cc_score": "6/6",
            "name": name, "sector": "Tech", **extra}


def sheets(path):
    return pd.read_excel(path, sheet_name=None)


@pytest.fixture
def us(load_script, sandbox, monkeypatch):
    mod = load_script(US)
    d, sleeps = sandbox(mod)
    state = types.SimpleNamespace(mod=mod, dir=d, nasdaq=["AAA", "BBB", "CCC"], nyse=["CCC", "DDD"],
                                  ohlc={}, fund={}, calls=[], fund_calls=[])
    monkeypatch.setattr(mod, "fetch_nasdaq_symbols", lambda: state.nasdaq)
    monkeypatch.setattr(mod, "fetch_nyse_symbols", lambda: state.nyse)

    def bulk(tickers, period="3mo"):
        state.calls.append((list(tickers), period))
        return {t: state.ohlc[t] for t in tickers if t in state.ohlc}

    def fscan(sym):
        state.fund_calls.append(sym)
        r = state.fund.get(sym)
        if isinstance(r, Exception):
            raise r
        return r or fund_result()

    monkeypatch.setattr(mod, "bulk_download_ohlc", bulk)
    monkeypatch.setattr(mod, "fundamental_scan", fscan)
    return state


def test_us_main_full_pipeline(us, capsys):
    us.ohlc = {"AAA": ohlc(12.0),          # BREAKOUT_BUY  (box top 10)
               "BBB": ohlc(9.0),           # IN_BOX
               "CCC": ohlc(7.0),           # BREAKDOWN_SELL (box bottom 8)
               "DDD": ohlc(0.5)}           # below default min_price 1.0 -> dropped
    out = us.mod.main()
    assert us.calls == [(["AAA", "BBB", "CCC", "DDD"], "3mo")]   # NASDAQ + NYSE, CCC de-duplicated, order kept
    assert out["total_scanned"] == 3 and out["breakouts"] == 1
    assert [r["Symbol"] for r in out["triple_hits"]] == ["AAA"]
    assert us.fund_calls == ["AAA"]
    book = sheets(out["excel_path"])
    allst = book["All_Stocks"].set_index("Symbol")
    assert allst.loc["AAA", "Darvas_Signal"] == "BREAKOUT_BUY"
    assert allst.loc["BBB", "Darvas_Signal"] == "IN_BOX"
    assert allst.loc["CCC", "Darvas_Signal"] == "BREAKDOWN_SELL"
    assert allst.loc["AAA", "LTP"] == 12.0 and allst.loc["AAA", "Prev_Close"] == 8.5
    assert allst.loc["AAA", "Change%"] == round((12 - 8.5) / 8.5 * 100, 2)
    assert sorted(book["Darvas_Signals"]["Symbol"]) == ["AAA", "CCC"]    # IN_BOX excluded
    fund = book["Fundamentals"].iloc[0]
    assert fund["Symbol"] == "AAA" and fund["Name"] == "Acme" and fund["Piotroski_Score"] == 8
    assert fund["Piotroski_Strong"] == "YES" and fund["CoffeeCan"] == "PASS" and fund["CC_Score"] == "6/6"
    assert book["Triple_Hits"]["Symbol"].tolist() == ["AAA"]
    text = capsys.readouterr().out
    assert "Breakout BUY:  1" in text and "Breakdown SELL:1" in text and "In Box:        1" in text
    assert "Total unique symbols: 4" in text
    assert "★ TRIPLE HITS:       1" in text and "F=8/9" in text


def test_us_main_min_price_boundary_is_inclusive(us):
    us.ohlc = {"AAA": ohlc(2.0), "BBB": ohlc(1.99)}
    out = us.mod.main(min_price=2.0, run_scans=False)
    assert out["total_scanned"] == 1                          # price == min_price kept, below dropped
    assert pd.read_excel(out["excel_path"], sheet_name="All_Stocks")["Symbol"].tolist() == ["AAA"]


def test_us_main_default_min_price_filters_sub_dollar(us):
    us.ohlc = {"AAA": ohlc(1.0), "BBB": ohlc(0.99)}
    assert us.mod.main(run_scans=False)["total_scanned"] == 1


def test_us_main_nasdaq_only_top_and_no_scans(us, capsys):
    us.ohlc = {"AAA": ohlc(12.0), "BBB": ohlc(12.0), "CCC": ohlc(12.0)}
    out = us.mod.main(nasdaq_only=True, top=2, run_scans=False)
    assert us.calls == [(["AAA", "BBB"], "3mo")]              # NYSE never fetched; top=2 truncates
    assert us.fund_calls == []
    assert out["breakouts"] == 2 and out["triple_hits"] == []
    text = capsys.readouterr().out
    assert "(limited to first 2 symbols)" in text and "Stage 4 — Skipped" in text
    assert "NYSE/Other" not in text


def test_us_main_top_zero_means_no_limit(us, capsys):
    us.ohlc = {t: ohlc(9.0) for t in ("AAA", "BBB", "CCC", "DDD")}
    us.mod.main(top=0, run_scans=False)
    assert "(limited" not in capsys.readouterr().out


def test_us_main_symbol_cache_hit_skips_fetch(us, capsys, monkeypatch):
    us.mod._save_symbol_cache(["XX"], ["YY"])
    monkeypatch.setattr(us.mod, "fetch_nasdaq_symbols", lambda: pytest.fail("must use cache"))
    us.ohlc = {"XX": ohlc(9.0), "YY": ohlc(9.0)}
    us.mod.main(run_scans=False)
    assert us.calls == [(["XX", "YY"], "3mo")]
    assert "Symbol cache hit: 1 NASDAQ + 1 NYSE/other" in capsys.readouterr().out


def test_us_main_cache_hit_with_nasdaq_only_drops_nyse(us):
    us.mod._save_symbol_cache(["XX"], ["YY"])
    us.ohlc = {"XX": ohlc(9.0), "YY": ohlc(9.0)}
    us.mod.main(nasdaq_only=True, run_scans=False)
    assert us.calls == [(["XX"], "3mo")]


def test_us_main_fetch_writes_cache(us):
    us.ohlc = {}
    us.mod.main(run_scans=False)
    assert us.mod._load_symbol_cache() == (["AAA", "BBB", "CCC"], ["CCC", "DDD"])


def test_us_main_caps_to_freshest_breakouts(us, monkeypatch, capsys):
    monkeypatch.setattr(us.mod, "MAX_FUND_CANDIDATES", 2)
    us.nasdaq, us.nyse = ["AAA", "BBB", "CCC"], []
    us.ohlc = {"AAA": ohlc(13.0), "BBB": ohlc(10.5), "CCC": ohlc(11.0)}   # upside to top: -23%, -4.8%, -9%
    out = us.mod.main()
    assert sorted(us.fund_calls) == ["BBB", "CCC"]                          # the two closest to the box top
    assert out["breakouts"] == 2
    assert "(pre-filtered to 2 freshest breakouts)" in capsys.readouterr().out


def test_us_main_exactly_at_cap_is_not_filtered(us, monkeypatch, capsys):
    monkeypatch.setattr(us.mod, "MAX_FUND_CANDIDATES", 2)
    us.nasdaq, us.nyse = ["AAA", "BBB"], []
    us.ohlc = {"AAA": ohlc(13.0), "BBB": ohlc(10.5)}
    us.mod.main()
    assert sorted(us.fund_calls) == ["AAA", "BBB"]
    assert "pre-filtered" not in capsys.readouterr().out


def test_us_main_fundamental_failures_and_non_triple(us, capsys):
    us.nasdaq, us.nyse = ["AAA", "BBB", "CCC"], []
    us.ohlc = {t: ohlc(12.0) for t in ("AAA", "BBB", "CCC")}
    us.fund = {"AAA": RuntimeError("boom"),
               "BBB": fund_result(f_score=5, strong=False, qualifies=True),   # qualifies but not strong
               "CCC": {"f_score": None, "f_strong": False, "qualifies": False, "error": "no_data"}}
    out = us.mod.main()
    book = sheets(out["excel_path"])
    fund = book["Fundamentals"].set_index("Symbol")
    assert sorted(fund.index) == ["BBB", "CCC"]                # the raising ticker leaves no row
    assert fund.loc["BBB", "Piotroski_Strong"] == "NO" and fund.loc["BBB", "CoffeeCan"] == "PASS"
    assert fund.loc["CCC", "CoffeeCan"] == "FAIL" and fund.loc["CCC", "Error"] == "no_data"
    assert out["triple_hits"] == [] and book["Triple_Hits"].empty
    assert "AAA: error — boom" in capsys.readouterr().out


def test_us_main_progress_line_every_20(us, capsys):
    names = [f"S{i:02d}" for i in range(21)]
    us.nasdaq, us.nyse = names, []
    us.ohlc = {t: ohlc(12.0) for t in names}
    us.mod.main(workers=1)
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip().endswith(")") and "done" in ln]
    assert [ln.split()[0] for ln in lines] == ["20/21", "21/21"]


def test_us_main_symbol_with_no_close_is_skipped(us):
    bad = ohlc(12.0)
    bad["Close"] = float("nan")
    us.nasdaq, us.nyse = ["AAA", "BBB"], []
    us.ohlc = {"AAA": bad, "BBB": ohlc(9.0)}
    out = us.mod.main(run_scans=False)
    assert out["total_scanned"] == 1


def test_us_main_single_bar_has_no_prev_close(us):
    one = pd.DataFrame({"High": [10.0], "Low": [9.0], "Close": [9.5]})
    us.nasdaq, us.nyse = ["AAA"], []
    us.ohlc = {"AAA": one}
    out = us.mod.main(run_scans=False)
    row = pd.read_excel(out["excel_path"], sheet_name="All_Stocks").iloc[0]
    assert pd.isna(row["Prev_Close"]) and pd.isna(row["Change%"])


# ---------------------------------------------------------------------------------------
# main(): India
# ---------------------------------------------------------------------------------------

@pytest.fixture
def india(load_script, sandbox, monkeypatch):
    mod = load_script(IN)
    d, sleeps = sandbox(mod)
    st = types.SimpleNamespace(mod=mod, dir=d, nse=["RELI", "TCS"], bse=["SMALL"], ohlc={}, fund={},
                               calls=[], fund_calls=[], bse_args=[])
    monkeypatch.setattr(mod, "fetch_nse_symbols", lambda: st.nse)

    def bse(nse_set):
        st.bse_args.append(set(nse_set))
        return st.bse

    def bulk(tickers, period="3mo"):
        st.calls.append((list(tickers), period))
        return {t: st.ohlc[t] for t in tickers if t in st.ohlc}

    def fscan(sym, suffix=".NS"):
        st.fund_calls.append((sym, suffix))
        r = st.fund.get(sym)
        if isinstance(r, Exception):
            raise r
        return r or fund_result()

    monkeypatch.setattr(mod, "fetch_bse_only_symbols", bse)
    monkeypatch.setattr(mod, "bulk_download_ohlc", bulk)
    monkeypatch.setattr(mod, "fundamental_scan", fscan)
    return st


def test_india_main_full_pipeline(india, capsys):
    india.ohlc = {"RELI.NS": ohlc(12.0), "TCS.NS": ohlc(9.0), "SMALL.BO": ohlc(7.0)}
    out = india.mod.main()
    assert india.calls == [(["RELI.NS", "TCS.NS", "SMALL.BO"], "3mo")]
    assert india.bse_args == [{"RELI", "TCS"}]                 # BSE-only filtering gets the NSE set
    assert india.fund_calls == [("RELI", ".NS")]
    assert out["total_scanned"] == 3 and out["breakouts"] == 1
    assert [r["Symbol"] for r in out["triple_hits"]] == ["RELI"]
    book = sheets(out["excel_path"])
    allst = book["All_Stocks"].set_index("Symbol")
    assert allst.loc["SMALL", "Suffix"] == ".BO" and allst.loc["RELI", "Suffix"] == ".NS"
    assert allst.loc["SMALL", "Darvas_Signal"] == "BREAKDOWN_SELL"
    assert allst.loc["RELI", "Change%"] == round((12 - 8.5) / 8.5 * 100, 2)
    assert sorted(book["Darvas_Signals"]["Symbol"]) == ["RELI", "SMALL"]
    f = book["Fundamentals"].iloc[0]
    assert f["Suffix"] == ".NS" and f["Piotroski_Score"] == 8 and f["CoffeeCan"] == "PASS"
    text = capsys.readouterr().out
    assert "Breakout BUY:  1" in text and "Breakdown SELL:1" in text and "In Box:        1" in text
    assert "→ 2 NSE EQ symbols" in text and "→ 1 BSE-only symbols" in text


@pytest.mark.xfail(strict=True, raises=TypeError,
                   reason="india_market_scan.py:481: with --nse-only and no symbol cache, bse_only stays None "
                          "(from _load_symbol_cache) and `for code in bse_only` raises TypeError")
def test_india_main_nse_only_cold_cache_skips_bse(india):
    india.ohlc = {"RELI.NS": ohlc(9.0)}
    india.mod.main(nse_only=True, run_scans=False)
    assert india.bse_args == []
    assert india.calls[0][0] == ["RELI.NS", "TCS.NS"]


def test_india_main_top_limits_tickers(india, capsys):
    india.mod.main(top=2, run_scans=False)
    assert india.calls[0][0] == ["RELI.NS", "TCS.NS"]
    assert "(limited to first 2 tickers)" in capsys.readouterr().out
    india.calls.clear()
    india.mod.main(top=0, run_scans=False)       # 0 = no limit
    assert india.calls[0][0] == ["RELI.NS", "TCS.NS", "SMALL.BO"]


def test_india_main_cache_hit_and_cache_write(india, capsys):
    india.mod.main(run_scans=False)
    assert india.mod._load_symbol_cache() == (["RELI", "TCS"], ["SMALL"])
    india.calls.clear()
    india.bse_args.clear()
    india.mod.main(run_scans=False)                      # second run: cache hit -> no fetch
    assert india.bse_args == []
    assert "Symbol cache hit: 2 NSE + 1 BSE-only" in capsys.readouterr().out
    india.calls.clear()
    india.mod.main(nse_only=True, run_scans=False)
    assert india.calls[0][0] == ["RELI.NS", "TCS.NS"]    # cache hit + nse_only drops the BSE list


def test_india_main_caps_to_freshest_breakouts(india, monkeypatch, capsys):
    monkeypatch.setattr(india.mod, "MAX_FUND_CANDIDATES", 2)
    india.nse, india.bse = ["AAA", "BBB", "CCC"], []
    india.ohlc = {"AAA.NS": ohlc(13.0), "BBB.NS": ohlc(10.5), "CCC.NS": ohlc(11.0)}
    india.mod.main()
    assert sorted(s for s, _ in india.fund_calls) == ["BBB", "CCC"]
    assert "(pre-filtered to 2 freshest breakouts)" in capsys.readouterr().out


def test_india_main_failure_isolated_and_no_scans(india, capsys):
    india.nse, india.bse = ["AAA", "BBB"], []
    india.ohlc = {"AAA.NS": ohlc(12.0), "BBB.NS": ohlc(12.0)}
    india.fund = {"AAA": RuntimeError("kaput"),
                  "BBB": {"f_score": 3, "f_strong": False, "qualifies": False, "cc_score": "1/6", "error": "x"}}
    out = india.mod.main()
    assert pd.read_excel(out["excel_path"], sheet_name="Fundamentals")["Symbol"].tolist() == ["BBB"]
    assert "AAA: error — kaput" in capsys.readouterr().out
    india.fund_calls.clear()
    india.mod.main(run_scans=False)
    assert india.fund_calls == []
    assert "Stage 4 — Skipped (--no-scans)" in capsys.readouterr().out


def test_india_main_empty_close_series(india):
    nan = ohlc(12.0)
    nan["Close"] = float("nan")
    india.nse, india.bse = ["AAA"], []
    india.ohlc = {"AAA.NS": nan}
    out = india.mod.main(run_scans=False)
    row = pd.read_excel(out["excel_path"], sheet_name="All_Stocks").iloc[0]
    assert pd.isna(row["LTP"]) and pd.isna(row["Change%"])


def test_india_main_unmapped_ticker_falls_back_to_raw_symbol(india, monkeypatch):
    india.nse, india.bse = ["AAA"], []
    india.ohlc = {"ZZZ.NS": ohlc(9.0)}
    monkeypatch.setattr(india.mod, "bulk_download_ohlc",
                        lambda t, period="3mo": {"ZZZ.NS": india.ohlc["ZZZ.NS"]})
    out = india.mod.main(run_scans=False)
    row = pd.read_excel(out["excel_path"], sheet_name="All_Stocks").iloc[0]
    assert row["Symbol"] == "ZZZ.NS" and pd.isna(row["Suffix"])


# ---------------------------------------------------------------------------------------
# main(): Europe / Japan / Korea (argparse based, return None, write Excel + print summary)
# ---------------------------------------------------------------------------------------

def one_book(d):
    files = sorted(d.glob("*_market_scan_*.xlsx"))
    assert len(files) == 1
    return sheets(files[0]), files[0]


def tech_frames(n=40, last=12.0):
    return ohlc(last, n=n)


@pytest.fixture
def eu(load_script, sandbox, monkeypatch):
    mod = load_script(EU)
    d, _ = sandbox(mod)
    st = types.SimpleNamespace(mod=mod, dir=d, calls=[], fund_calls=[], fund={}, ohlc={}, build_kw=[],
                               universe=[
                                   {"ticker": "SAP.DE", "name": "SAP SE", "country": "Germany", "sector": "IT",
                                    "exchange": "Xetra", "mcap_b": 150.0, "cap_band": "Mega"},
                                   {"ticker": "MC.PA", "name": "LVMH", "country": "France", "sector": "Cons",
                                    "exchange": "Paris", "mcap_b": 300.0, "cap_band": "Mega"},
                                   {"ticker": "ABC.L", "name": "Abc", "country": "UK", "sector": "Fin",
                                    "exchange": "LSE", "mcap_b": 1.0, "cap_band": "Small"}])

    def build(**kw):
        st.build_kw.append(kw)
        return st.universe

    def bulk(tickers, period="3mo"):
        st.calls.append((list(tickers), period))
        return {t: st.ohlc[t] for t in tickers if t in st.ohlc}

    def fscan(t):
        st.fund_calls.append(t)
        r = st.fund.get(t)
        if isinstance(r, Exception):
            raise r
        return r or {"f_score": 8, "f_strong": True, "cc_qualifies": "PASS", "cc_score": "6/6",
                     "cc_rev_cagr": 12.5, "cc_roce_avg": 20.0, "cc_debt_equity": 0.3, "market_cap_b": 150.0,
                     "error": None}

    monkeypatch.setattr(mod, "build_universe", build)
    monkeypatch.setattr(mod, "bulk_download_ohlc", bulk)
    monkeypatch.setattr(mod, "fundamental_scan", fscan)
    return st


def argv(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["scan", *map(str, args)])


def test_europe_main_full_pipeline(eu, monkeypatch, capsys):
    argv(monkeypatch)
    eu.ohlc = {"SAP.DE": ohlc(12.0, n=40), "MC.PA": ohlc(9.0, n=40), "ABC.L": ohlc(7.0, n=40)}
    assert eu.mod.main() is None
    assert eu.build_kw == [{"universe_file": None, "min_cap_b": 0.0, "exchange_filter": []}]
    assert eu.calls == [(["SAP.DE", "MC.PA", "ABC.L"], "3mo")]
    assert eu.fund_calls == ["SAP.DE"]
    book, _ = one_book(eu.dir)
    allst = book["All_Stocks"].set_index("Ticker")
    assert allst.loc["SAP.DE", "Darvas_Signal"] == "BREAKOUT_BUY"
    assert allst.loc["MC.PA", "Darvas_Signal"] == "IN_BOX"
    assert allst.loc["ABC.L", "Darvas_Signal"] == "BREAKDOWN_SELL"
    assert allst.loc["SAP.DE", "Name"] == "SAP SE" and allst.loc["SAP.DE", "Country"] == "Germany"
    assert allst.loc["SAP.DE", "Exchange"] == "Xetra" and allst.loc["ABC.L", "Exchange"] == "LSE"
    assert allst.loc["SAP.DE", "Trend_Signal"] == "Insufficient History"      # < 200 bars
    assert allst.loc["SAP.DE", "Upside_to_Top%"] == round((10 - 12) / 12 * 100, 2)
    assert allst.loc["SAP.DE", "Position_in_Box%"] == round((12 - 8) / 2 * 100, 1)
    assert sorted(book["Darvas_Signals"]["Ticker"]) == ["ABC.L", "SAP.DE"]
    f = book["Fundamentals"].iloc[0]
    assert f["Ticker"] == "SAP.DE" and f["Piotroski_Strong"] == "YES" and f["CoffeeCan"] == "PASS"
    assert f["Rev_CAGR%"] == 12.5 and f["MCap_B_yf"] == 150.0 and f["Sector"] == "IT"
    assert book["Triple_Hits"]["Ticker"].tolist() == ["SAP.DE"]
    text = capsys.readouterr().out
    assert "Breakout BUY:   1" in text and "Breakdown SELL: 1" in text and "In Box:         1" in text
    assert "Xetra 1" in text and "LSE 1" in text
    assert "SAP.DE" in text and "F=8/9" in text


def test_europe_main_cli_flags_reach_build_universe(eu, monkeypatch):
    argv(monkeypatch, "--min-cap", "2.5", "--exchange", "PA", "DE", "--universe-file", "u.xlsx", "--no-scans",
         "--top", "1")
    eu.ohlc = {}
    eu.mod.main()
    assert eu.build_kw == [{"universe_file": "u.xlsx", "min_cap_b": 2.5, "exchange_filter": ["PA", "DE"]}]
    assert eu.calls == [(["SAP.DE"], "3mo")]


def test_europe_main_no_triple_hits_message(eu, monkeypatch, capsys):
    argv(monkeypatch, "--no-scans")
    eu.ohlc = {"SAP.DE": ohlc(9.0, n=40)}
    eu.mod.main()
    text = capsys.readouterr().out
    assert "No Triple Hit stocks found today." in text and "Stage 4 — Skipped" in text
    assert eu.fund_calls == []


def test_europe_main_200_day_ma_trend_buckets(eu, monkeypatch):
    argv(monkeypatch, "--no-scans")
    n = 210

    def series(level):
        df = ohlc(level, n=n)
        df["Close"] = [100.0] * (n - 1) + [level]
        df["High"] = [101.0] * n
        df["Low"] = [99.0] * n
        return df

    eu.ohlc = {"SAP.DE": series(106.0), "MC.PA": series(94.0), "ABC.L": series(103.0)}
    eu.mod.main()
    book, _ = one_book(eu.dir)
    t = book["All_Stocks"].set_index("Ticker")
    assert t.loc["SAP.DE", "Trend_Signal"] == "Above 200MA (Uptrend)"
    assert t.loc["MC.PA", "Trend_Signal"] == "Below 200MA (Downtrend)"
    assert t.loc["ABC.L", "Trend_Signal"] == "Near 200MA (Consolidation)"
    assert t.loc["SAP.DE", "200_Day_MA"] == round((100.0 * 199 + 106.0) / 200, 2)


def test_europe_main_caps_and_failures(eu, monkeypatch, capsys):
    argv(monkeypatch)
    monkeypatch.setattr(eu.mod, "MAX_FUND_CANDIDATES", 2)
    eu.ohlc = {"SAP.DE": ohlc(13.0, n=40), "MC.PA": ohlc(10.5, n=40), "ABC.L": ohlc(11.0, n=40)}
    eu.fund = {"MC.PA": RuntimeError("bad")}
    eu.mod.main()
    assert sorted(eu.fund_calls) == ["ABC.L", "MC.PA"]
    text = capsys.readouterr().out
    assert "(capped to 2 freshest breakouts for fundamentals)" in text
    assert "MC.PA: error — bad" in text
    book, _ = one_book(eu.dir)
    assert book["Fundamentals"]["Ticker"].tolist() == ["ABC.L"]


def test_europe_main_cap_boundary_not_filtered(eu, monkeypatch, capsys):
    argv(monkeypatch)
    monkeypatch.setattr(eu.mod, "MAX_FUND_CANDIDATES", 2)
    eu.ohlc = {"SAP.DE": ohlc(13.0, n=40), "MC.PA": ohlc(10.5, n=40)}
    eu.mod.main()
    assert "capped" not in capsys.readouterr().out
    assert sorted(eu.fund_calls) == ["MC.PA", "SAP.DE"]


def test_europe_main_non_strong_not_triple(eu, monkeypatch):
    argv(monkeypatch)
    eu.ohlc = {"SAP.DE": ohlc(12.0, n=40), "MC.PA": ohlc(12.0, n=40)}
    eu.fund = {"SAP.DE": {"f_score": 5, "f_strong": False, "cc_qualifies": "PASS"},
               "MC.PA": {"f_score": 8, "f_strong": True, "cc_qualifies": "FAIL"}}
    eu.mod.main()
    book, _ = one_book(eu.dir)
    assert book["Triple_Hits"].empty
    assert sorted(book["Fundamentals"]["Ticker"]) == ["MC.PA", "SAP.DE"]


def test_europe_main_top_zero_not_limited(eu, monkeypatch, capsys):
    argv(monkeypatch, "--no-scans")
    eu.mod.main()
    assert "(limited" not in capsys.readouterr().out
    assert eu.calls[0][0] == ["SAP.DE", "MC.PA", "ABC.L"]


# ---- Japan ----------------------------------------------------------------------------

@pytest.fixture
def jp(load_script, sandbox, monkeypatch):
    mod = load_script(JP)
    d, _ = sandbox(mod)
    st = types.SimpleNamespace(mod=mod, dir=d, calls=[], fund_calls=[], fund={}, ohlc={},
                               universe=[{"yf_ticker": "7203.T", "code": "7203", "name": "Toyota", "sector": "Auto"},
                                         {"yf_ticker": "6758.T", "code": "6758", "name": "Sony", "sector": "Elec"},
                                         {"yf_ticker": "9984.T", "code": "9984", "name": "SoftBank", "sector": "Comm"}])
    monkeypatch.setattr(mod, "build_ticker_universe", lambda: st.universe)

    def bulk(tickers, period="3mo"):
        st.calls.append((list(tickers), period))
        return {t: st.ohlc[t] for t in tickers if t in st.ohlc}

    def fscan(t):
        st.fund_calls.append(t)
        r = st.fund.get(t)
        if isinstance(r, Exception):
            raise r
        return r or {"f_score": 9, "f_strong": True, "cc_qualifies": "PASS", "cc_score": "5/5",
                     "cc_rev_cagr": 8.0, "cc_roce_avg": 11.0, "cc_debt_equity": 0.5,
                     "market_cap_b_jpy": 1234.5, "error": None}

    monkeypatch.setattr(mod, "bulk_download_ohlc", bulk)
    monkeypatch.setattr(mod, "fundamental_scan", fscan)
    return st


def test_japan_main_full_pipeline(jp, monkeypatch, capsys):
    argv(monkeypatch)
    jp.ohlc = {"7203.T": ohlc(1200.0, n=40, top=1000.0, bottom=800.0),
               "6758.T": ohlc(900.0, n=40, top=1000.0, bottom=800.0),
               "9984.T": ohlc(700.0, n=40, top=1000.0, bottom=800.0)}
    for df in jp.ohlc.values():            # scale the flat bars to yen levels
        df["High"] = df["High"].where(df["High"] != 9.0, 950.0)
        df["Low"] = 800.0
        df["Close"] = df["Close"].where(df["Close"] != 8.5, 850.0)
    assert jp.mod.main() is None
    assert jp.calls == [(["7203.T", "6758.T", "9984.T"], "3mo")]
    assert jp.fund_calls == ["7203.T"]
    book, _ = one_book(jp.dir)
    t = book["All_Stocks"].set_index("YF_Ticker")
    assert t.loc["7203.T", "Darvas_Signal"] == "BREAKOUT_BUY"
    assert t.loc["6758.T", "Darvas_Signal"] == "IN_BOX"
    assert t.loc["9984.T", "Darvas_Signal"] == "BREAKDOWN_SELL"
    assert t.loc["7203.T", "LTP_JPY"] == 1200.0 and t.loc["7203.T", "Name"] == "Toyota"
    assert t.loc["7203.T", "Code"] == 7203
    assert t.loc["7203.T", "Change%"] == round((1200 - 850) / 850 * 100, 2)
    assert t.loc["7203.T", "Trend_Signal"] == "Insufficient History"
    f = book["Fundamentals"].iloc[0]
    assert f["MCap_B_JPY"] == 1234.5 and f["Piotroski_Score"] == 9 and f["CoffeeCan"] == "PASS"
    assert book["Triple_Hits"]["Code"].tolist() == [7203]
    text = capsys.readouterr().out
    assert "Breakout BUY:   1" in text and "Breakdown SELL: 1" in text
    assert "[7203] Toyota" in text and "¥1,200" in text


def test_japan_main_flags_caps_and_failures(jp, monkeypatch, capsys):
    argv(monkeypatch, "--top", "2", "--workers", "1")
    monkeypatch.setattr(jp.mod, "MAX_FUND_CANDIDATES", 1)
    jp.ohlc = {"7203.T": ohlc(13.0, n=40), "6758.T": ohlc(10.5, n=40)}
    jp.fund = {"6758.T": RuntimeError("nope")}
    jp.mod.main()
    assert jp.calls[0][0] == ["7203.T", "6758.T"]
    assert jp.fund_calls == ["6758.T"]                       # only the freshest breakout survives the cap
    text = capsys.readouterr().out
    assert "(limited to first 2 tickers)" in text and "(capped to 1 freshest breakouts)" in text
    assert "6758.T: error — nope" in text
    book, _ = one_book(jp.dir)
    assert book["Fundamentals"].empty


def test_japan_main_no_scans_and_no_triple(jp, monkeypatch, capsys):
    argv(monkeypatch, "--no-scans")
    jp.ohlc = {"7203.T": ohlc(12.0, n=40)}
    jp.mod.main()
    assert jp.fund_calls == []
    assert "No Triple Hit stocks found today." in capsys.readouterr().out


def test_japan_main_ma200_and_cap_boundary(jp, monkeypatch, capsys):
    argv(monkeypatch)
    monkeypatch.setattr(jp.mod, "MAX_FUND_CANDIDATES", 2)
    n = 210
    df = ohlc(106.0, n=n)
    df["Close"] = [100.0] * (n - 1) + [106.0]
    df["High"] = [101.0] * (n - 1) + [107.0]
    df["Low"] = [99.0] * n
    jp.ohlc = {"7203.T": df, "6758.T": ohlc(10.5, n=40)}
    jp.mod.main()
    assert "capped" not in capsys.readouterr().out
    book, _ = one_book(jp.dir)
    row = book["All_Stocks"].set_index("YF_Ticker").loc["7203.T"]
    assert row["200_Day_MA"] == round((100.0 * 199 + 106.0) / 200, 0)
    assert row["Trend_Signal"] == "Above 200MA (Uptrend)"


def test_japan_unknown_ticker_uses_stripped_code(jp, monkeypatch):
    argv(monkeypatch, "--no-scans")
    jp.ohlc = {"1234.T": ohlc(9.0, n=40)}
    jp.mod.build_ticker_universe = lambda: [{"yf_ticker": "1234.T", "code": "1234", "name": "N", "sector": "S"}]
    jp.mod.main()
    book, _ = one_book(jp.dir)
    assert book["All_Stocks"].iloc[0]["Code"] == 1234


# ---- Korea ----------------------------------------------------------------------------

@pytest.fixture
def kr(load_script, sandbox, monkeypatch):
    mod = load_script(KR)
    d, _ = sandbox(mod)
    st = types.SimpleNamespace(
        mod=mod, dir=d, kospi_only=[], fund_calls=[], fund={}, ohlc={}, ohlc_args=[],
        universe=[{"code": "005930", "name": "Samsung", "sector": "Elec", "market": "KOSPI", "yf_suffix": ".KS"},
                  {"code": "035720", "name": "Kakao", "sector": "IT", "market": "KOSPI", "yf_suffix": ".KS"},
                  {"code": "247540", "name": "Ecopro", "sector": "Bat", "market": "KOSDAQ", "yf_suffix": ".KQ"}])

    def build(kospi_only=False):
        st.kospi_only.append(kospi_only)
        return st.universe

    def fetch(universe, months=3):
        st.ohlc_args.append(([s["code"] for s in universe], months))
        return {s["code"]: st.ohlc[s["code"]] for s in universe if s["code"] in st.ohlc}

    def fscan(code, sfx):
        st.fund_calls.append((code, sfx))
        r = st.fund.get(code)
        if isinstance(r, Exception):
            raise r
        return r or {"f_score": 8, "f_strong": True, "cc_qualifies": "PASS", "cc_score": "5/5",
                     "cc_rev_cagr": 9.0, "cc_roce_avg": 12.0, "cc_debt_equity": 0.4,
                     "market_cap_b_krw": 777.0, "error": None}

    monkeypatch.setattr(mod, "build_krx_universe", build)
    monkeypatch.setattr(mod, "fetch_krx_ohlc", fetch)
    monkeypatch.setattr(mod, "fundamental_scan", fscan)
    return st


def test_korea_main_full_pipeline(kr, monkeypatch, capsys):
    argv(monkeypatch)
    kr.ohlc = {"005930": ohlc(12.0, n=40), "035720": ohlc(9.0, n=40), "247540": ohlc(7.0, n=40)}
    assert kr.mod.main() is None
    assert kr.kospi_only == [False]
    assert kr.ohlc_args == [(["005930", "035720", "247540"], 3)]
    assert kr.fund_calls == [("005930", ".KS")]
    book, _ = one_book(kr.dir)
    t = book["All_Stocks"].set_index("YF_Ticker")
    assert t.loc["005930.KS", "Darvas_Signal"] == "BREAKOUT_BUY"
    assert t.loc["035720.KS", "Darvas_Signal"] == "IN_BOX"
    assert t.loc["247540.KQ", "Darvas_Signal"] == "BREAKDOWN_SELL"
    assert t.loc["247540.KQ", "Market"] == "KOSDAQ" and t.loc["005930.KS", "LTP_KRW"] == 12.0
    assert t.loc["005930.KS", "Trend_Signal"] == "Need 200+ days"
    f = book["Fundamentals"].iloc[0]
    assert f["YF_Ticker"] == "005930.KS" and f["MCap_100M_KRW"] == 777.0 and f["Market"] == "KOSPI"
    assert book["Triple_Hits"]["Name"].tolist() == ["Samsung"]
    text = capsys.readouterr().out
    assert "Total: 3 tickers (2 KOSPI + 1 KOSDAQ)" in text
    assert "Breakout BUY:   1" in text and "Breakdown SELL: 1" in text and "In Box:         1" in text
    assert "[005930] Samsung" in text and "Market: KOSPI" in text


def test_korea_main_flags_and_missing_ohlc(kr, monkeypatch, capsys):
    argv(monkeypatch, "--kospi-only", "--top", "2", "--no-scans")
    kr.ohlc = {"005930": ohlc(9.0, n=40)}                     # 035720 has no data -> skipped
    kr.mod.main()
    assert kr.kospi_only == [True]
    assert kr.ohlc_args == [(["005930", "035720"], 3)]
    assert kr.fund_calls == []
    book, _ = one_book(kr.dir)
    assert book["All_Stocks"]["Code"].tolist() == [5930]
    text = capsys.readouterr().out
    assert "(limited to first 2 tickers)" in text and "Total: 2 tickers (2 KOSPI + 0 KOSDAQ)" in text
    assert "No Triple Hit stocks found today." in text


def test_korea_main_cap_failures_and_ma200(kr, monkeypatch, capsys):
    argv(monkeypatch, "--workers", "1")
    monkeypatch.setattr(kr.mod, "MAX_FUND_CANDIDATES", 1)
    n = 210
    big = ohlc(94.0, n=n)
    big["Close"] = [100.0] * (n - 1) + [94.0]
    big["High"] = [101.0] * n
    big["Low"] = [99.0] * n
    kr.ohlc = {"005930": ohlc(13.0, n=40), "035720": ohlc(10.5, n=40), "247540": big}
    kr.fund = {"035720": RuntimeError("fail")}
    kr.mod.main()
    assert kr.fund_calls == [("035720", ".KS")]
    text = capsys.readouterr().out
    assert "(capped to 1 freshest breakouts)" in text and "035720: error — fail" in text
    book, _ = one_book(kr.dir)
    row = book["All_Stocks"].set_index("Code").loc[247540]
    assert row["Trend_Signal"] == "Below 200MA (Downtrend)"
    assert row["200_Day_MA"] == round((100.0 * 199 + 94.0) / 200, 0)


def test_korea_main_cap_boundary(kr, monkeypatch, capsys):
    argv(monkeypatch)
    monkeypatch.setattr(kr.mod, "MAX_FUND_CANDIDATES", 2)
    kr.ohlc = {"005930": ohlc(13.0, n=40), "035720": ohlc(10.5, n=40)}
    kr.mod.main()
    assert "capped" not in capsys.readouterr().out
    assert sorted(c for c, _ in kr.fund_calls) == ["005930", "035720"]

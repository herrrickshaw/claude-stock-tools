"""Symbol-universe fetchers, caches and Excel styling of the five market scanners. All HTTP, kabupy, pykrx and
read_html calls are faked; caches and creds live in tmp_path."""
import json
import sys
import time
import types
from datetime import datetime
from io import BytesIO

import pandas as pd
import pytest

US = "02_market_screeners/us_market_scan.py"
IN = "02_market_screeners/india_market_scan.py"
EU = "02_market_screeners/europe_market_scan.py"
JP = "02_market_screeners/japan_market_scan.py"
KR = "02_market_screeners/korea_market_scan.py"


class Resp:
    def __init__(self, text="", content=b"", payload=None, status_ok=True):
        self.text, self.content, self._payload, self._ok = text, content, payload, status_ok

    def raise_for_status(self):
        if not self._ok:
            raise RuntimeError("http 500")

    def json(self):
        return self._payload


def mod_for(load_script, monkeypatch, tmp_path, path, cache_attr):
    mod = load_script(path)
    d = tmp_path / "out"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(mod, "DOWNLOAD_DIR", d)
    monkeypatch.setattr(mod, cache_attr, d / ".cache.json")
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    return mod


# ---------------------------------------------------------------------------------------
# US
# ---------------------------------------------------------------------------------------

@pytest.fixture
def us(load_script, monkeypatch, tmp_path):
    mod = mod_for(load_script, monkeypatch, tmp_path, US, "_SYMBOL_CACHE")
    monkeypatch.setattr(mod, "_NCDS_CREDS_PATH", tmp_path / "ncds.json")
    monkeypatch.setattr(mod, "_ncds_symbol_cache", {})
    return mod


def test_us_cache_roundtrip_and_ttl(us, monkeypatch):
    assert us._load_symbol_cache() == (None, None)                       # no file
    us._save_symbol_cache(["A"], ["B"])
    assert us._load_symbol_cache() == (["A"], ["B"])
    now = time.time()
    us._SYMBOL_CACHE.write_text(json.dumps({"ts": now - us.SYMBOL_CACHE_TTL + 5, "nasdaq": ["X"]}))
    assert us._load_symbol_cache() == (["X"], [])                        # just inside the TTL; nyse defaults []
    us._SYMBOL_CACHE.write_text(json.dumps({"ts": now - us.SYMBOL_CACHE_TTL - 5, "nasdaq": ["X"]}))
    assert us._load_symbol_cache() == (None, None)                       # just outside
    us._SYMBOL_CACHE.write_text("{broken")
    assert us._load_symbol_cache() == (None, None)                       # corrupt -> miss, no raise


def test_us_save_cache_swallows_unwritable_path(us, monkeypatch, tmp_path):
    monkeypatch.setattr(us, "_SYMBOL_CACHE", tmp_path / "missing_dir" / "c.json")
    us._save_symbol_cache(["A"], ["B"])                                  # must not raise


def test_us_ncds_creds(us, monkeypatch):
    monkeypatch.setattr(us, "_NCDS_AVAILABLE", False)
    us._NCDS_CREDS_PATH.write_text('{"topic": "T"}')
    assert us._load_ncds_creds() is None                                 # SDK missing
    monkeypatch.setattr(us, "_NCDS_AVAILABLE", True)
    assert us._load_ncds_creds() == {"topic": "T"}
    us._NCDS_CREDS_PATH.write_text("not json")
    assert us._load_ncds_creds() is None
    us._NCDS_CREDS_PATH.unlink()
    assert us._load_ncds_creds() is None                                 # file missing


class Msg:
    """Message whose .value is a method (the confluent-kafka style)."""
    def __init__(self, val):
        self._v = val

    def value(self):
        return self._v


class AttrMsg:
    """Message whose .value is a plain attribute rather than a method."""
    def __init__(self, val):
        self.value = val


def dir_msg(sym, mkt, schema="SeqDirectoryMessage"):
    return {"schema_name": schema, "symbol": sym, "marketClass": mkt}


class FakeConsumer:
    def __init__(self, batches):
        self.batches, self.closed, self.calls = list(batches), False, 0

    def consume(self, num_messages, timeout):
        self.calls += 1
        assert (num_messages, timeout) == (500, 3)
        return self.batches.pop(0) if self.batches else []

    def close(self):
        self.closed = True


def install_ncds(us, monkeypatch, consumer, creds=None):
    seen = {}

    class Client:
        def __init__(self, security_cfg, kafka_cfg):
            seen["sec"], seen["kafka"] = security_cfg, kafka_cfg

        def ncds_kafka_consumer(self, topic):
            seen["topic"] = topic
            return consumer

    monkeypatch.setattr(us, "_NCDS_AVAILABLE", True)
    monkeypatch.setattr(us, "_NCDSClient", Client, raising=False)
    us._NCDS_CREDS_PATH.write_text(json.dumps(creds if creds is not None else {
        "oauth": {"endpoint": "https://auth", "client_id": "cid", "client_secret": "sec"},
        "kafka": {"bootstrap_servers": "host:9094"}, "topic": "MYTOPIC"}))
    return seen


def test_us_ncds_no_creds_returns_empty(us, monkeypatch):
    monkeypatch.setattr(us, "_NCDS_AVAILABLE", False)
    assert us.fetch_symbols_from_ncds() == ([], [])


def test_us_ncds_classifies_dedupes_and_closes(us, monkeypatch):
    consumer = FakeConsumer([[Msg(v) for v in (
        dir_msg("aapl", "Q"), dir_msg("AAPL", "G"),                     # dup after upper-casing
        dir_msg("msft", "S"), dir_msg("tsla", ""), dir_msg("ibm", "N"), dir_msg("ko", "A"),
        dir_msg("BRK.B", "N"), dir_msg("A^B", "Q"), dir_msg("X/Y", "Q"), dir_msg("C$D", "Q"), dir_msg("", "Q"),
        dir_msg("other", "Q", schema="Trade"),                           # wrong schema
        "not-a-dict")]])
    seen = install_ncds(us, monkeypatch, consumer)
    nasdaq, nyse = us.fetch_symbols_from_ncds()
    assert nasdaq == ["AAPL", "MSFT", "TSLA"]
    assert nyse == ["IBM", "KO"]
    assert consumer.closed
    assert consumer.calls == 6                                           # 1 full poll + 5 empty polls, then stop
    assert seen["topic"] == "MYTOPIC"
    assert seen["sec"] == {"oauth.token.endpoint.uri": "https://auth", "oauth.client.id": "cid",
                           "oauth.client.secret": "sec"}
    assert seen["kafka"] == {"bootstrap.servers": "host:9094", "auto.offset.reset": "earliest"}


def test_us_ncds_default_topic_and_attribute_values(us, monkeypatch):
    consumer = FakeConsumer([[AttrMsg(dir_msg("zz", "Q"))]])
    seen = install_ncds(us, monkeypatch, consumer, creds={"oauth": {}, "kafka": {}})
    assert us.fetch_symbols_from_ncds() == (["ZZ"], [])
    assert seen["topic"] == "NLSPLUS-CTA-V4"
    assert seen["sec"]["oauth.token.endpoint.uri"] == ""


def test_us_ncds_empty_poll_counter_resets_after_data(us, monkeypatch):
    consumer = FakeConsumer([[], [], [AttrMsg(dir_msg("aa", "Q"))], [], [], [], []])
    install_ncds(us, monkeypatch, consumer)
    us.fetch_symbols_from_ncds()
    assert consumer.calls == 8                                           # 2 empty, data, then 5 empty


def test_us_ncds_error_is_swallowed(us, monkeypatch, capsys):
    class Boom:
        def __init__(self, *a):
            raise RuntimeError("kafka down")

    install_ncds(us, monkeypatch, FakeConsumer([]))
    monkeypatch.setattr(us, "_NCDSClient", Boom, raising=False)
    assert us.fetch_symbols_from_ncds() == ([], [])
    assert "NCDS SDK error: kafka down" in capsys.readouterr().out


NASDAQ_TXT = ("Symbol|Security Name|Market Category|Test Issue|ETF\n"
              "AAPL|Apple|Q|N|N\n"
              "TEST|Test|Q|Y|N\n"
              "QQQ|Fund|Q|N|Y\n"
              "ABCDE|Five|Q|N|N\n"
              "ABCDEF|Six|Q|N|N\n"
              "BRK.A|Dot|Q|N|N\n"
              "A^B|Caret|Q|N|N\n"
              "|Blank|Q|N|N\n"
              "File Creation Time: 1105202612:00|||\n")


def test_us_parse_nasdaq_file_filters(us):
    assert us._parse_nasdaq_file(NASDAQ_TXT) == ["AAPL", "ABCDE"]       # 5 chars kept, 6 dropped
    assert us._parse_nasdaq_file("") == []
    assert us._parse_nasdaq_file("   \n") == []


def test_us_parse_other_file_exchange_filter_and_short_rows(us):
    text = ("ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue\n"
            "IBM|IBM|N|IBM|N|100|N\n"
            "SPY|Spdr|P|SPY|Y|100|N\n"
            "KO|Coke|N|KO|N|100|Y\n"
            "GE|GE|n|GE|N|100|N\n"
            "ZZ|Zed|Z|ZZ|N|100|N\n")
    assert us._parse_nasdaq_file(text) == ["IBM", "GE", "ZZ"]
    assert us._parse_nasdaq_file(text, exchange_filter="N") == ["IBM", "GE"]    # case-insensitive match


def test_us_parse_rows_shorter_than_symbol_column_are_skipped(us):
    assert us._parse_nasdaq_file("Name|Symbol\nAlpha|AAA\nlonely\nBeta|BBB\n") == ["AAA", "BBB"]


def test_us_parse_file_without_symbol_header_uses_first_column(us):
    assert us._parse_nasdaq_file("Ticker|Name\nAAA|x\nBBB|y\n") == ["AAA", "BBB"]


def test_us_fetch_nasdaq_file_https_then_http(us, monkeypatch):
    calls = []

    def get(url, timeout=None, headers=None):
        calls.append((url, timeout, headers["User-Agent"]))
        if url.startswith("https"):
            raise RuntimeError("tls")
        return Resp(text="data")

    monkeypatch.setattr(us.requests, "get", get)
    assert us._fetch_nasdaq_file("nasdaqlisted.txt") == "data"
    assert [c[0] for c in calls] == [f"{us.NASDAQ_FTP_BASE}/nasdaqlisted.txt",
                                     f"{us.NASDAQ_FTP_BASE_HTTP}/nasdaqlisted.txt"]
    assert calls[0][1] == 20


def test_us_fetch_nasdaq_file_empty_body_tries_next_then_gives_up(us, monkeypatch):
    seq = iter([Resp(text=""), Resp(status_ok=False)])
    monkeypatch.setattr(us.requests, "get", lambda *a, **k: next(seq))
    assert us._fetch_nasdaq_file("x") == ""


def test_us_sec_fallback(us, monkeypatch):
    rows = [[1, "A", "aaa", "Nasdaq"], [2, "B", "bbb", "NYSE"], [3, "C", "ccc", "NYSE Arca"],
            [4, "D", "ddd", "Cboe"], [5, "E", "e.e", "Nasdaq"], [6, "F", "f f", "NYSE"],
            [7, "G", "g^g", "Nasdaq"], [8, "H", "", "Nasdaq"], [9, "I", "iii"], [10, "J", "jjj", "AMEX"],
            [11]]
    seen = {}

    def get(url, timeout=None, headers=None):
        seen.update(url=url, timeout=timeout, ua=headers["User-Agent"])
        return Resp(payload={"data": rows})

    monkeypatch.setattr(us.requests, "get", get)
    nasdaq, nyse = us.fetch_all_us_symbols_from_sec()
    assert nasdaq == ["AAA"]
    assert nyse == ["BBB", "CCC", "JJJ"]
    assert seen["url"].endswith("company_tickers_exchange.json") and seen["timeout"] == 30


def test_us_sec_failure_returns_empty(us, monkeypatch, capsys):
    monkeypatch.setattr(us.requests, "get", lambda *a, **k: Resp(status_ok=False))
    assert us.fetch_all_us_symbols_from_sec() == ([], [])
    assert "SEC EDGAR fetch failed" in capsys.readouterr().out


def test_us_fetch_nasdaq_uses_ncds_first(us, monkeypatch):
    install_ncds(us, monkeypatch, FakeConsumer([[AttrMsg(dir_msg("aa", "Q")), AttrMsg(dir_msg("bb", "N"))]]))
    monkeypatch.setattr(us, "_fetch_nasdaq_file", lambda f: pytest.fail("FTP must not be used"))
    assert us.fetch_nasdaq_symbols() == ["AA"]
    assert us._ncds_symbol_cache == {"nasdaq": ["AA"], "nyse": ["BB"]}
    assert us.fetch_nyse_symbols() == ["BB"]                              # served from the shared NCDS cache


def test_us_fetch_nasdaq_ncds_empty_falls_back_to_ftp(us, monkeypatch, capsys):
    install_ncds(us, monkeypatch, FakeConsumer([]))
    monkeypatch.setattr(us, "_fetch_nasdaq_file", lambda f: NASDAQ_TXT)
    assert us.fetch_nasdaq_symbols() == ["AAPL", "ABCDE"]
    assert "NCDS returned no NASDAQ symbols" in capsys.readouterr().out


def test_us_fetch_nasdaq_ftp_then_sec(us, monkeypatch, capsys):
    monkeypatch.setattr(us, "_NCDS_AVAILABLE", False)
    files = []
    monkeypatch.setattr(us, "_fetch_nasdaq_file", lambda f: files.append(f) or "")
    monkeypatch.setattr(us, "fetch_all_us_symbols_from_sec", lambda: (["S1"], ["S2"]))
    assert us.fetch_nasdaq_symbols() == ["S1"]
    assert files == ["nasdaqlisted.txt"]
    assert "using SEC EDGAR fallback" in capsys.readouterr().out
    monkeypatch.setattr(us, "_fetch_nasdaq_file", lambda f: "Symbol|x\n")        # parses to nothing -> SEC too
    assert us.fetch_nasdaq_symbols() == ["S1"]


def test_us_fetch_nyse_ftp_then_sec(us, monkeypatch):
    files = []
    monkeypatch.setattr(us, "_fetch_nasdaq_file", lambda f: files.append(f) or "ACT Symbol|Security Name\nIBM|x\n")
    assert us.fetch_nyse_symbols() == ["IBM"]
    assert files == ["otherlisted.txt"]
    monkeypatch.setattr(us, "_fetch_nasdaq_file", lambda f: "")
    monkeypatch.setattr(us, "fetch_all_us_symbols_from_sec", lambda: (["S1"], ["S2"]))
    assert us.fetch_nyse_symbols() == ["S2"]


# ---------------------------------------------------------------------------------------
# India
# ---------------------------------------------------------------------------------------

@pytest.fixture
def india(load_script, monkeypatch, tmp_path):
    return mod_for(load_script, monkeypatch, tmp_path, IN, "_SYMBOL_CACHE")


def test_india_cache_roundtrip_ttl(india):
    assert india._load_symbol_cache() == (None, None)
    india._save_symbol_cache(["A"], ["B"])
    assert india._load_symbol_cache() == (["A"], ["B"])
    india._SYMBOL_CACHE.write_text(json.dumps({"ts": time.time() - india.SYMBOL_CACHE_TTL - 5, "nse": ["A"]}))
    assert india._load_symbol_cache() == (None, None)
    india._SYMBOL_CACHE.write_text(json.dumps({"ts": time.time(), "nse": ["A"]}))
    assert india._load_symbol_cache() == (["A"], [])                      # bse_only defaults to []


def test_india_save_cache_unwritable(india, monkeypatch, tmp_path):
    monkeypatch.setattr(india, "_SYMBOL_CACHE", tmp_path / "nope" / "c.json")
    india._save_symbol_cache(["A"], ["B"])


def test_india_fetch_nse_symbols_walks_back_through_dates(india, monkeypatch):
    dates = []
    frames = {0: None, 1: pd.DataFrame(), 2: pd.DataFrame({"X": [1]}),            # None / empty / no SERIES col
              3: "boom"}

    def bhav(d):
        i = len(dates)
        dates.append(d)
        f = frames.get(i)
        if f == "boom":
            raise RuntimeError("archive 404")
        if i == 4:
            return pd.DataFrame({"SYMBOL": [" TCS ", "RELI", "NOPE", None], " SERIES": ["EQ", " EQ", "BE", "EQ"]})
        return f

    monkeypatch.setattr(india, "_nse_get_bhavcopy", bhav)
    assert india.fetch_nse_symbols() == ["RELI", "TCS"]                   # EQ only, stripped, sorted, NaN dropped
    parsed = [datetime.strptime(d, "%d-%m-%Y") for d in dates]
    assert len(parsed) == 5
    assert all((a - b).days == 1 for a, b in zip(parsed, parsed[1:]))     # one day further back each time
    assert abs((datetime.today() - parsed[0]).days) <= 1


def test_india_fetch_nse_symbols_gives_up_after_seven_days(india, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(india, "_nse_get_bhavcopy", lambda d: calls.append(d))
    assert india.fetch_nse_symbols() == []
    assert len(calls) == 7
    assert "Could not fetch NSE symbol list" in capsys.readouterr().out


def test_india_fetch_nse_symbols_no_eq_rows_keeps_looking(india, monkeypatch):
    calls = []

    def bhav(d):
        calls.append(d)
        return pd.DataFrame({"SYMBOL": ["A"], "SERIES": ["BE"]})

    monkeypatch.setattr(india, "_nse_get_bhavcopy", bhav)
    assert india.fetch_nse_symbols() == []
    assert len(calls) == 7


class FakeSession:
    def __init__(self, text="", fail_home=False):
        self.headers, self.gets, self.text, self.fail_home = {}, [], text, fail_home

    def get(self, url, **kw):
        self.gets.append((url, kw))
        if self.fail_home and url.startswith("https://www.bseindia"):
            raise RuntimeError("blocked")
        return types.SimpleNamespace(text=self.text)


def test_india_bse_session_sets_headers_and_survives_warmup_failure(india, monkeypatch):
    made = []

    def Session():
        s = FakeSession(fail_home=True)
        s.headers = types.SimpleNamespace(update=lambda d: s.__dict__.setdefault("hdr", d))
        made.append(s)
        return s

    monkeypatch.setattr(india.requests, "Session", Session)
    s = india._bse_session()
    assert s is made[0]
    assert s.hdr["Referer"] == "https://www.bseindia.com/" and "Mozilla" in s.hdr["User-Agent"]
    assert s.gets[0][0] == "https://www.bseindia.com/" and s.gets[0][1] == {"timeout": 15}


BSE_CSV = ("Security Code,Issuer Name,Security Id,Security Name,Status,Group,Face Value,ISIN No,Instrument\n"
           "500001,A,AAA,A Ltd,Active,A,10,INE1,Equity\n"
           "500002,B,BBB,B Ltd,Active,A,10,INE2,Equity\n"
           "500003,C,CCC,C Ltd,Suspended,A,10,INE3,Equity\n"
           "500004,D,DDD,D Fund,Active,A,10,INE4,Mutual Fund\n"
           "500005,E, EEE ,E Ltd,Active,A,10,INE5,Equity\n")


def test_india_bse_only_symbols_filters_active_equity_not_in_nse(india, monkeypatch):
    sess = FakeSession(text=BSE_CSV)
    monkeypatch.setattr(india, "_bse_session", lambda: sess)
    assert india.fetch_bse_only_symbols({"AAA"}) == ["BBB", "EEE"]
    url, kw = sess.gets[0]
    assert url == "stub://" and kw["timeout"] == 30 and kw["params"]["segment"] == "Equity"


def test_india_bse_only_retries_three_times_then_gives_up(india, monkeypatch, capsys):
    sleeps, sessions = [], []
    monkeypatch.setattr(india.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(india, "_bse_session", lambda: sessions.append(1) or FakeSession(text="   "))
    assert india.fetch_bse_only_symbols(set()) == []
    assert len(sessions) == 3 and sleeps == [3, 3]                        # no sleep after the last attempt
    assert "Could not fetch BSE symbol list" in capsys.readouterr().out


def test_india_bse_only_recovers_on_second_attempt(india, monkeypatch):
    seq = iter([FakeSession(text=""), FakeSession(text=BSE_CSV)])
    monkeypatch.setattr(india, "_bse_session", lambda: next(seq))
    assert india.fetch_bse_only_symbols(set()) == ["AAA", "BBB", "EEE"]


# ---------------------------------------------------------------------------------------
# Europe
# ---------------------------------------------------------------------------------------

@pytest.fixture
def eu(load_script, monkeypatch, tmp_path):
    return mod_for(load_script, monkeypatch, tmp_path, EU, "_CACHE_FILE")


def test_europe_cache_roundtrip_ttl(eu):
    assert eu._load_cache() is None
    eu._save_cache([{"ticker": "A.PA"}])
    assert eu._load_cache() == [{"ticker": "A.PA"}]
    eu._CACHE_FILE.write_text(json.dumps({"ts": time.time() - eu.SYMBOL_CACHE_TTL - 5, "universe": [1]}))
    assert eu._load_cache() is None
    eu._CACHE_FILE.write_text(json.dumps({"ts": time.time()}))
    assert eu._load_cache() == []
    eu._CACHE_FILE.write_text("{")
    assert eu._load_cache() is None


def test_europe_save_cache_unwritable(eu, monkeypatch, tmp_path):
    monkeypatch.setattr(eu, "_CACHE_FILE", tmp_path / "nope" / "c.json")
    eu._save_cache([])


def test_europe_ticker_suffix(eu):
    assert eu._ticker_suffix("SAP.DE") == ".DE"
    assert eu._ticker_suffix("BRK.B.L") == ".L"                           # last dot wins
    assert eu._ticker_suffix("PLAIN") == ""
    assert eu._ticker_suffix("X.") == "."


def write_universe_xlsx(path, rows):
    cols = ["Ticker", "Company", "Country", "GICS Sector (top 200)", "Exchange", "Market Cap ($B)", "Cap Band",
            "Exchange Group"]
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        pd.DataFrame(rows, columns=cols).to_excel(w, sheet_name="Database", index=False)


def test_europe_load_universe_from_file(eu, tmp_path):
    p = tmp_path / "u.xlsx"
    write_universe_xlsx(p, [
        [" SAP.DE ", " SAP SE ", "Germany", "IT", "Xetra", 150.5, "Mega", "Germany"],
        ["MC.PA", "LVMH", "France", None, "Paris", None, "Mega", "France"],
        ["AAPL", "Apple", "US", "IT", "NASDAQ", 3000.0, "Mega", "US-listed (EU-domiciled)"],   # excluded group
        [None, "NoTicker", "X", "X", "X", 1.0, "X", "France"],                                  # NaN ticker dropped
        ["  ", "Blank", "X", "X", "X", 1.0, "X", "France"],                                     # blank ticker dropped
    ])
    out = eu.load_universe_from_file(p)
    assert [s["ticker"] for s in out] == ["SAP.DE", "MC.PA"]
    assert out[0] == {"ticker": "SAP.DE", "name": "SAP SE", "country": "Germany", "sector": "IT",
                      "exchange": "Xetra", "mcap_b": 150.5, "cap_band": "Mega"}
    assert out[1]["mcap_b"] is None and out[1]["sector"] in ("nan", "—", "None")


def test_europe_fetch_universe_live(eu, monkeypatch):
    csv = "Name,Symbol,Country\nSAP,SAP.DE,Germany\nNoDot,NODOT,X\nBlank,,Y\nLVMH,MC.PA,France\n"
    seen = {}

    def get(url, headers=None, timeout=None):
        seen.update(url=url, timeout=timeout)
        return Resp(text=csv)

    monkeypatch.setattr(eu.requests, "get", get)
    out = eu.fetch_universe_live()
    assert [s["ticker"] for s in out] == ["SAP.DE", "MC.PA"]
    assert out[0]["exchange"] == "Xetra" and out[1]["exchange"] == "Euronext Paris"
    assert out[0]["name"] == "SAP" and out[0]["country"] == "Germany" and out[0]["mcap_b"] is None
    assert seen["timeout"] == 20 and "companiesmarketcap" in seen["url"]


def test_europe_fetch_universe_live_failure(eu, monkeypatch, capsys):
    monkeypatch.setattr(eu.requests, "get", lambda *a, **k: Resp(status_ok=False))
    assert eu.fetch_universe_live() == []
    assert "Live fetch failed" in capsys.readouterr().out


U = [{"ticker": "SAP.DE", "mcap_b": 150.0}, {"ticker": "MC.PA", "mcap_b": None},
     {"ticker": "ABC.L", "mcap_b": 1.0}, {"ticker": "SAP.DE", "mcap_b": 150.0}]


def test_europe_build_universe_cache_hit(eu, monkeypatch):
    eu._save_cache([{"ticker": "C.PA"}])
    monkeypatch.setattr(eu, "load_universe_from_file", lambda p: pytest.fail("cache must win"))
    assert eu.build_universe() == [{"ticker": "C.PA"}]


def test_europe_build_universe_cache_ignored_when_filtering(eu, monkeypatch, tmp_path):
    eu._save_cache([{"ticker": "C.PA"}])
    p = tmp_path / "f.xlsx"
    p.write_text("x")
    monkeypatch.setattr(eu, "load_universe_from_file", lambda path: [dict(u) for u in U])
    assert [s["ticker"] for s in eu.build_universe(universe_file=p, min_cap_b=0.5)] == ["SAP.DE", "MC.PA", "ABC.L"]
    assert [s["ticker"] for s in eu.build_universe(universe_file=p, exchange_filter=["pa"])] == ["MC.PA"]


def test_europe_build_universe_file_dedupes_and_caches(eu, monkeypatch, tmp_path):
    p = tmp_path / "f.xlsx"
    p.write_text("x")
    monkeypatch.setattr(eu, "load_universe_from_file", lambda path: [dict(u) for u in U])
    out = eu.build_universe(universe_file=p)
    assert [s["ticker"] for s in out] == ["SAP.DE", "MC.PA", "ABC.L"]
    assert eu._load_cache() == out                                         # unfiltered result is cached


def test_europe_build_universe_filters_not_cached(eu, monkeypatch, tmp_path):
    p = tmp_path / "f.xlsx"
    p.write_text("x")
    monkeypatch.setattr(eu, "load_universe_from_file", lambda path: [dict(u) for u in U])
    eu.build_universe(universe_file=p, min_cap_b=1.0)
    assert eu._load_cache() is None
    eu.build_universe(universe_file=p, exchange_filter=["DE"])
    assert eu._load_cache() is None


def test_europe_build_universe_mcap_filter_boundary_and_none(eu, monkeypatch, tmp_path):
    p = tmp_path / "f.xlsx"
    p.write_text("x")
    monkeypatch.setattr(eu, "load_universe_from_file", lambda path: [dict(u) for u in U])
    out = eu.build_universe(universe_file=p, min_cap_b=1.0)
    assert [s["ticker"] for s in out] == ["SAP.DE", "MC.PA", "ABC.L"]     # 1.0 >= 1.0 kept; None kept
    out = eu.build_universe(universe_file=p, min_cap_b=1.01)
    assert [s["ticker"] for s in out] == ["SAP.DE", "MC.PA"]


def test_europe_build_universe_exchange_filter_normalises(eu, monkeypatch, tmp_path):
    p = tmp_path / "f.xlsx"
    p.write_text("x")
    monkeypatch.setattr(eu, "load_universe_from_file", lambda path: [dict(u) for u in U])
    out = eu.build_universe(universe_file=p, exchange_filter=[".de", "L"])
    assert [s["ticker"] for s in out] == ["SAP.DE", "ABC.L"]


def test_europe_build_universe_file_error_falls_back_to_live(eu, monkeypatch, tmp_path, capsys):
    p = tmp_path / "f.xlsx"
    p.write_text("x")

    def bad(path):
        raise ValueError("corrupt")

    monkeypatch.setattr(eu, "load_universe_from_file", bad)
    monkeypatch.setattr(eu, "fetch_universe_live", lambda: [{"ticker": "L.PA", "mcap_b": None}])
    assert eu.build_universe(universe_file=p) == [{"ticker": "L.PA", "mcap_b": None}]
    assert "File read error: corrupt" in capsys.readouterr().out


def test_europe_build_universe_missing_file_uses_live_then_exits(eu, monkeypatch, tmp_path):
    monkeypatch.setattr(eu, "fetch_universe_live", lambda: [{"ticker": "L.PA", "mcap_b": None}])
    assert eu.build_universe(universe_file=tmp_path / "absent.xlsx")[0]["ticker"] == "L.PA"
    eu._CACHE_FILE.unlink()
    monkeypatch.setattr(eu, "fetch_universe_live", lambda: [])
    with pytest.raises(SystemExit) as e:
        eu.build_universe(universe_file=tmp_path / "absent.xlsx")
    assert "Could not load EU universe" in str(e.value)


def test_europe_build_universe_default_file_used_when_none(eu, monkeypatch, tmp_path):
    p = tmp_path / "default.xlsx"
    p.write_text("x")
    monkeypatch.setattr(eu, "_DEFAULT_UNIVERSE_FILE", p)
    seen = []
    monkeypatch.setattr(eu, "load_universe_from_file", lambda path: seen.append(path) or [{"ticker": "A.PA"}])
    eu.build_universe()
    assert seen == [p]


# ---------------------------------------------------------------------------------------
# Japan
# ---------------------------------------------------------------------------------------

@pytest.fixture
def jp(load_script, monkeypatch, tmp_path):
    return mod_for(load_script, monkeypatch, tmp_path, JP, "_CACHE_FILE")


def test_japan_cache(jp):
    assert jp._load_cache() is None
    jp._save_cache([{"yf_ticker": "7203.T"}])
    assert jp._load_cache() == [{"yf_ticker": "7203.T"}]
    jp._CACHE_FILE.write_text(json.dumps({"ts": time.time() - jp.SYMBOL_CACHE_TTL - 5, "tickers": [1]}))
    assert jp._load_cache() is None
    jp._CACHE_FILE.write_text(json.dumps({"ts": time.time()}))
    assert jp._load_cache() == []
    jp._CACHE_FILE.write_text("[")
    assert jp._load_cache() is None


def test_japan_save_cache_unwritable(jp, monkeypatch, tmp_path):
    monkeypatch.setattr(jp, "_CACHE_FILE", tmp_path / "nope" / "c.json")
    jp._save_cache([])


def fake_kabupy(monkeypatch, issues=None, exc=None):
    m = types.ModuleType("kabupy")

    class Jpx:
        @property
        def issues(self):
            if exc:
                raise exc
            return issues

    m.Jpx = Jpx
    monkeypatch.setitem(sys.modules, "kabupy", m)


def test_japan_kabupy_filters_domestic_equities(jp, monkeypatch):
    fake_kabupy(monkeypatch, [
        {"category": "プライム（内国株式）", "security_code": "7203.0", "name": "Toyota",
         "33_industry_category": "Auto"},
        {"category": "スタンダード（内国株式）", "security_code": "67", "name": "Short",
         "17_industry_category": "Elec"},
        {"category": "ETF", "security_code": "1306", "name": "Etf"},
        {"category": "内国株式", "security_code": "ABCD", "name": "Alpha"},
        {"category": "内国株式", "security_code": "12345", "name": "TooLong"},
        {"category": "内国株式", "security_code": "9984", "name": "SoftBank"},
    ])
    out = jp.fetch_tse_universe_kabupy()
    assert [s["code"] for s in out] == ["7203", "0067", "9984"]
    assert out[0] == {"code": "7203", "name": "Toyota", "sector": "Auto", "market": "プライム"}
    assert out[1]["sector"] == "Elec"
    assert out[2]["sector"] == "—"


def test_japan_kabupy_missing_or_failing_returns_empty(jp, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "kabupy", None)                      # import raises ImportError
    assert jp.fetch_tse_universe_kabupy() == []
    assert "kabupy not installed" in capsys.readouterr().out
    fake_kabupy(monkeypatch, exc=RuntimeError("api"))
    assert jp.fetch_tse_universe_kabupy() == []
    assert "kabupy error: api" in capsys.readouterr().out
    fake_kabupy(monkeypatch, issues=[])
    assert jp.fetch_tse_universe_kabupy() == []


def jpx_xls(rows, cols):
    buf = BytesIO()
    pd.DataFrame(rows, columns=cols).to_excel(buf, index=False)
    return buf.getvalue()


def test_japan_jpx_parses_master(jp, monkeypatch):
    content = jpx_xls([
        ["7203.0", "Toyota", "Transport", "Prime Market"],
        ["67", "Sony", "Electric", "Standard Market"],
        ["9999", "Growthco", "Services", "Growth Market"],               # filtered out by market
        ["ABC", "Bad", "X", "Prime Market"],                              # non-digit code dropped
        ["1301", "Kyokuyo", "Fishery", "プライム"],
    ], ["コード", "銘柄名", "33業種区分", "市場・商品区分"])
    seen = {}

    def get(url, headers=None, timeout=None):
        seen.update(url=url, timeout=timeout)
        return Resp(content=content)

    monkeypatch.setattr(jp.requests, "get", get)
    out = jp.fetch_tse_universe_jpx()
    assert out == [{"code": "7203", "name": "Toyota", "sector": "Transport"},
                   {"code": "0067", "name": "Sony", "sector": "Electric"},
                   {"code": "1301", "name": "Kyokuyo", "sector": "Fishery"}]
    assert seen["url"] == jp.JPX_MASTER_URL and seen["timeout"] == 30


def test_japan_jpx_without_market_or_name_columns(jp, monkeypatch):
    content = jpx_xls([["7203", "x"]], ["Code", "Other"])
    monkeypatch.setattr(jp.requests, "get", lambda *a, **k: Resp(content=content))
    assert jp.fetch_tse_universe_jpx() == [{"code": "7203", "name": "", "sector": "—"}]


def test_japan_jpx_unknown_code_column_and_errors(jp, monkeypatch, capsys):
    content = jpx_xls([["7203"]], ["Foo"])
    monkeypatch.setattr(jp.requests, "get", lambda *a, **k: Resp(content=content))
    assert jp.fetch_tse_universe_jpx() == []
    assert "Cannot identify code column" in capsys.readouterr().out
    monkeypatch.setattr(jp.requests, "get", lambda *a, **k: Resp(status_ok=False))
    assert jp.fetch_tse_universe_jpx() == []
    assert "JPX fetch error" in capsys.readouterr().out


def test_japan_build_universe_cache_dedupe_and_exit(jp, monkeypatch):
    jp._save_cache([{"yf_ticker": "1.T"}])
    monkeypatch.setattr(jp, "fetch_tse_universe_kabupy", lambda: pytest.fail("cache must win"))
    assert jp.build_ticker_universe() == [{"yf_ticker": "1.T"}]
    jp._CACHE_FILE.unlink()
    monkeypatch.setattr(jp, "fetch_tse_universe_kabupy", lambda: [
        {"code": "7203", "name": "A", "sector": "S"}, {"code": "7203", "name": "dup", "sector": "S"},
        {"code": "6758", "name": "B", "sector": "T"}])
    out = jp.build_ticker_universe()
    assert out == [{"yf_ticker": "7203.T", "code": "7203", "name": "A", "sector": "S"},
                   {"yf_ticker": "6758.T", "code": "6758", "name": "B", "sector": "T"}]
    assert jp._load_cache() == out


def test_japan_build_universe_falls_back_to_jpx_then_exits(jp, monkeypatch):
    monkeypatch.setattr(jp, "fetch_tse_universe_kabupy", lambda: [])
    monkeypatch.setattr(jp, "fetch_tse_universe_jpx", lambda: [{"code": "1301", "name": "K", "sector": "F"}])
    assert jp.build_ticker_universe()[0]["yf_ticker"] == "1301.T"
    jp._CACHE_FILE.unlink()
    monkeypatch.setattr(jp, "fetch_tse_universe_jpx", lambda: [])
    with pytest.raises(SystemExit) as e:
        jp.build_ticker_universe()
    assert "Could not fetch TSE universe" in str(e.value)


# ---------------------------------------------------------------------------------------
# Korea
# ---------------------------------------------------------------------------------------

@pytest.fixture
def kr(load_script, monkeypatch, tmp_path):
    return mod_for(load_script, monkeypatch, tmp_path, KR, "_CACHE_FILE")


def test_korea_cache(kr):
    assert kr._load_cache() is None
    kr._save_cache([{"code": "005930"}])
    assert kr._load_cache() == [{"code": "005930"}]
    kr._CACHE_FILE.write_text(json.dumps({"ts": time.time() - kr.SYMBOL_CACHE_TTL - 5, "universe": [1]}))
    assert kr._load_cache() is None
    kr._CACHE_FILE.write_text(json.dumps({"ts": time.time()}))
    assert kr._load_cache() == []
    kr._CACHE_FILE.write_text("[")
    assert kr._load_cache() is None


def test_korea_save_cache_unwritable(kr, monkeypatch, tmp_path):
    monkeypatch.setattr(kr, "_CACHE_FILE", tmp_path / "nope" / "c.json")
    kr._save_cache([])


def test_korea_fetch_kind_list(kr, monkeypatch):
    df = pd.DataFrame({"회사명": [" Samsung ", "Bad", "Short", "Fund"],
                       "종목코드": [5930, "ABCDEF", 1234567, "000660"],
                       "업종": ["Elec", "x", "y", None]})
    seen = {}

    def get(url, params=None, headers=None, timeout=None):
        seen.update(url=url, params=params, timeout=timeout)
        return Resp(text="<html/>")

    def read_html(buf, encoding=None):
        seen["encoding"] = encoding
        return [df]

    monkeypatch.setattr(kr.requests, "get", get)
    monkeypatch.setattr(kr.pd, "read_html", read_html)
    out = kr._fetch_kind_list("stockMkt", "KOSPI", ".KS")
    MISSING = out[1]["sector"]
    assert MISSING in ("nan", "None")                                    # str(missing) leaks through (pandas-dependent)
    assert out == [{"code": "005930", "name": "Samsung", "sector": "Elec", "market": "KOSPI", "yf_suffix": ".KS"},
                   {"code": "000660", "name": "Fund", "sector": MISSING, "market": "KOSPI", "yf_suffix": ".KS"}]
    assert seen["params"] == {"method": "download", "searchType": "13", "marketType": "stockMkt"}
    assert seen["encoding"] == "euc-kr" and seen["timeout"] == 20


def test_korea_fetch_kind_list_failure(kr, monkeypatch, capsys):
    monkeypatch.setattr(kr.requests, "get", lambda *a, **k: Resp(status_ok=False))
    assert kr._fetch_kind_list("x", "KOSDAQ", ".KQ") == []
    assert "KIND fetch error (KOSDAQ)" in capsys.readouterr().out


def test_korea_build_universe(kr, monkeypatch):
    calls = []

    def kind(param, label, sfx):
        calls.append((param, label, sfx))
        return [{"code": "1", "market": label}]

    monkeypatch.setattr(kr, "_fetch_kind_list", kind)
    out = kr.build_krx_universe()
    assert calls == [("stockMkt", "KOSPI", ".KS"), ("kosdaqMkt", "KOSDAQ", ".KQ")]
    assert [s["market"] for s in out] == ["KOSPI", "KOSDAQ"]
    assert kr._load_cache() == out
    calls.clear()
    assert kr.build_krx_universe() == out                                 # cache hit
    assert calls == []
    assert kr.build_krx_universe(kospi_only=True) == [{"code": "1", "market": "KOSPI"}]


def test_korea_build_universe_kospi_only_fetches_only_kospi(kr, monkeypatch):
    calls = []
    monkeypatch.setattr(kr, "_fetch_kind_list", lambda p, l, s: calls.append(l) or [{"code": "1", "market": l}])
    kr.build_krx_universe(kospi_only=True)
    assert calls == ["KOSPI"]


def test_korea_build_universe_exits_when_empty(kr, monkeypatch):
    monkeypatch.setattr(kr, "_fetch_kind_list", lambda *a: [])
    with pytest.raises(SystemExit) as e:
        kr.build_krx_universe()
    assert "Could not fetch any KRX tickers" in str(e.value)
    assert kr._load_cache() is None


def test_korea_normalise_cols(kr):
    df = pd.DataFrame({"고가": [1], "저가": [2], "종가": [3], "시가": [4], "거래량": [5], "Other": [6]})
    assert list(kr._normalise_krx_cols(df).columns) == ["High", "Low", "Close", "Open", "Volume", "Other"]
    plain = pd.DataFrame({"High": [1]})
    assert list(kr._normalise_krx_cols(plain).columns) == ["High"]


def krx_frame(n):
    return pd.DataFrame({"고가": [10.0] * n, "저가": [8.0] * n, "종가": [9.0] * n})


def test_korea_fetch_krx_ohlc(kr, monkeypatch, capsys):
    need = kr.DARVAS_CONFIRM + 5
    frames = {"A": krx_frame(need), "B": krx_frame(need - 1), "C": None, "D": pd.DataFrame(),
              "E": pd.DataFrame({"고가": [1.0] * 20, "종가": [1.0] * 20}), "F": "boom"}
    seen = []

    def ohlcv(f, t, code):
        seen.append((f, t, code))
        v = frames[code]
        if isinstance(v, str):
            raise RuntimeError(v)
        return v

    sleeps = []
    monkeypatch.setattr(kr, "krx", types.SimpleNamespace(get_market_ohlcv_by_date=ohlcv))
    monkeypatch.setattr(kr.time, "sleep", lambda s: sleeps.append(s))
    out = kr.fetch_krx_ohlc([{"code": c} for c in frames], months=2)
    assert list(out) == ["A"]
    assert list(out["A"].columns)[:3] == ["High", "Low", "Close"]
    assert len(seen) == 6
    f, t, _ = seen[0]
    span = datetime.strptime(t, "%Y%m%d") - datetime.strptime(f, "%Y%m%d")
    assert span.days in (61, 62)                                           # months * 31 days
    # sleeps after every ticker that did not `continue` early (C and D skip the sleep)
    assert sleeps == [kr.SLEEP_BETWEEN_KRX] * 4


def test_korea_fetch_krx_ohlc_progress_every_200(kr, monkeypatch, capsys):
    monkeypatch.setattr(kr, "krx", types.SimpleNamespace(get_market_ohlcv_by_date=lambda f, t, c: krx_frame(10)))
    monkeypatch.setattr(kr.time, "sleep", lambda s: None)
    kr.fetch_krx_ohlc([{"code": f"{i:06d}"} for i in range(200)])
    assert "200/200 fetched (200 usable)" in capsys.readouterr().out


# ---------------------------------------------------------------------------------------
# style_sheet (Europe / Japan / Korea)
# ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("path,hdr,price_fmt", [(EU, "1A365D", "#,##0.00"), (JP, "1A3A5C", "#,##0"),
                                                 (KR, "1A3A5C", "#,##0")])
def test_style_sheet(load_script, path, hdr, price_fmt):
    openpyxl = pytest.importorskip("openpyxl")
    mod = load_script(path)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Name", "Change%", "LTP", "Score", "Rev_CAGR%"])
    ws.append(["A-long-company-name", 1.5, 100.0, 7, 12.0])
    ws.append(["B", 2.5, 200.0, 8, None])
    ws.append(["C", None, 300.0, 9, 3.0])
    mod.style_sheet(ws)
    assert ws["A1"].fill.start_color.rgb.endswith(hdr) and ws["A1"].font.bold
    assert ws["A1"].font.color.rgb.endswith("FFFFFF")
    assert ws["B2"].number_format == '0.00"%"'                          # % columns
    assert ws["E2"].number_format == '0.00"%"'                          # CAGR column
    assert ws["C2"].number_format == price_fmt                          # LTP/price columns
    assert ws["D2"].number_format == "0.00"                             # anything else numeric
    assert ws["B2"].alignment.horizontal == "right" and ws["A2"].alignment.horizontal == "left"
    assert ws["A1"].alignment.horizontal == "center"
    assert ws["A2"].fill.fill_type is None and ws["A3"].fill.fill_type == "solid"   # alternate rows shaded
    assert ws.row_dimensions[1].height == 28 and ws.row_dimensions[2].height == 20
    assert ws.column_dimensions["A"].width == len("A-long-company-name") + 4
    assert ws.column_dimensions["D"].width == 12                         # floor of 12


@pytest.mark.parametrize("path", [EU, JP, KR])
def test_style_sheet_noop_without_openpyxl(load_script, monkeypatch, path):
    mod = load_script(path)
    monkeypatch.setattr(mod, "OPENPYXL_OK", False)
    mod.style_sheet(object())                                           # would blow up if it touched ws

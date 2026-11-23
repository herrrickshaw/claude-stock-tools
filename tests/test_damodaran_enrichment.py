"""damodaran_enrichment: cache, sheet parsing, sector matching and enrichment (no network)."""
import io
import json
import time

import pandas as pd
import pytest

PATH = "05_enrichment/damodaran_enrichment.py"


@pytest.fixture
def dm(load_script, tmp_path, monkeypatch):
    mod = load_script(PATH)
    monkeypatch.setattr(mod, "_CACHE_DIR", tmp_path / "cache")
    return mod


def xlsx_bytes(sheets):
    """sheets: {name: list-of-rows}; rows are written verbatim (no header handling)."""
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        for name, rows in sheets.items():
            pd.DataFrame(rows).to_excel(w, sheet_name=name, index=False, header=False)
    return bio.getvalue()


def sector_sheet(header, data, extra_row=False, name="Industry Averages"):
    rows = [["x"] * len(header) for _ in range(7)]
    if extra_row:
        # row 7 (the pandas header row) has an empty first cell; real names sit one row lower
        rows.append([None, "grp"] + [None] * (len(header) - 2))
    rows.append(header)
    rows += data
    return xlsx_bytes({name: rows})


# ---- cache ------------------------------------------------------------------

def test_cache_roundtrip_and_freshness(dm):
    assert not dm._is_fresh("k")
    df = pd.DataFrame({"a": [1.5, 2.5]}, index=["x", "y"])
    dm._save_cache("k", df)
    assert dm._is_fresh("k")
    pd.testing.assert_frame_equal(dm._load_cache("k"), df, check_names=False)


def test_cache_paths_and_missing_pieces(dm):
    assert dm._cache_path("k").name == "k.csv"
    assert dm._cache_path("k").parent.is_dir()
    assert dm._meta_path("k").name == "k.meta.json"
    dm._save_cache("k", pd.DataFrame({"a": [1]}))
    dm._cache_path("k").unlink()
    assert not dm._is_fresh("k")           # meta without csv
    dm._save_cache("k", pd.DataFrame({"a": [1]}))
    dm._meta_path("k").unlink()
    assert not dm._is_fresh("k")           # csv without meta


def test_cache_expires_after_24_hours(dm):
    dm._save_cache("k", pd.DataFrame({"a": [1]}))
    mp = dm._meta_path("k")
    mp.write_text(json.dumps({"ts": time.time() - 23 * 3600}))
    assert dm._is_fresh("k")
    mp.write_text(json.dumps({"ts": time.time() - 25 * 3600}))
    assert not dm._is_fresh("k")
    mp.write_text(json.dumps({}))          # missing ts counts as epoch -> stale
    assert not dm._is_fresh("k")


# ---- download ---------------------------------------------------------------

def test_download_bytes_success_and_failure(dm, monkeypatch):
    calls = []

    class Resp:
        content = b"data"
        def raise_for_status(self): pass

    def ok(url, headers=None, timeout=None):
        calls.append((url, headers, timeout))
        return Resp()

    monkeypatch.setattr(dm.requests, "get", ok)
    assert dm._download_bytes("http://x") == b"data"
    assert calls[0][2] == 30 and "User-Agent" in calls[0][1]

    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(dm.requests, "get", boom)
    assert dm._download_bytes("http://x") is None


# ---- sheet parsing ----------------------------------------------------------

def test_parse_plain_header(dm):
    raw = sector_sheet(["Industry Name", "Number of firms", "Current PE", "Trailing PE", "Forward PE"],
                       [["Software", 10, 30.5, "28", 25.0],
                        ["Banks", 5, 10.0, 11.0, None],
                        ["Total Market", 99, 20, 20, 20],
                        [None, None, None, None, None]])
    df = dm._parse_sector_sheet(raw, "pe")
    assert df.index.name == "Sector"
    assert list(df.columns) == ["Sector_PE_Current", "Sector_PE_Trailing", "Sector_PE_Fwd"]
    assert list(df.index) == ["Software", "Banks"]          # Total Market and blank rows dropped
    assert df.loc["Software", "Sector_PE_Trailing"] == 28.0  # numeric coercion of strings
    assert pd.isna(df.loc["Banks", "Sector_PE_Fwd"])


def test_parse_two_row_header_with_duplicates(dm):
    header = ["Industry Name", "Number of firms", "EV/EBITDA", "EV/EBITDA", "EV/EBIT"]
    raw = sector_sheet(header, [["Software", 10, 15.0, 99.0, 20.0], ["Banks", 4, 8.0, 77.0, 9.0]],
                       extra_row=True)
    df = dm._parse_sector_sheet(raw, "vebitda")
    assert list(df.columns) == ["Sector_EV_EBITDA", "Sector_EV_EBIT"]
    assert df.loc["Software", "Sector_EV_EBITDA"] == 15.0   # first duplicate wins
    assert df.loc["Banks", "Sector_EV_EBIT"] == 9.0


def test_parse_falls_back_to_last_sheet(dm):
    raw = sector_sheet(["Industry Name", "Current PE"], [["Software", 30.0]], name="Other")
    df = dm._parse_sector_sheet(raw, "pe")
    assert df.loc["Software", "Sector_PE_Current"] == 30.0


def test_parse_unknown_dataset_keeps_only_sector(dm):
    raw = sector_sheet(["Industry Name", "Current PE"], [["Software", 30.0]])
    df = dm._parse_sector_sheet(raw, "nope")
    assert list(df.columns) == [] and list(df.index) == ["Software"]


def test_parse_bad_bytes_returns_none(dm):
    assert dm._parse_sector_sheet(b"not excel", "pe") is None


# ---- load_sector_table ------------------------------------------------------

def test_load_sector_table_downloads_then_serves_from_cache(dm, monkeypatch):
    raw = sector_sheet(["Industry Name", "Trailing PE"], [["Software", 30.0]])
    urls = []
    monkeypatch.setattr(dm, "_download_bytes", lambda u: urls.append(u) or raw)
    df = dm.load_sector_table("pe", "europe")
    assert urls == [f"{dm._BASE}/peEurope.xls"]
    assert df.loc["Software", "Sector_PE_Trailing"] == 30.0
    again = dm.load_sector_table("pe", "europe")
    assert len(urls) == 1                                     # cache hit
    assert again.loc["Software", "Sector_PE_Trailing"] == 30.0


def test_load_sector_table_us_uses_empty_suffix_and_unknown_market_global(dm, monkeypatch):
    urls = []
    monkeypatch.setattr(dm, "_download_bytes", lambda u: urls.append(u) and None)
    assert dm.load_sector_table("pbv", "us") is None
    assert urls == [f"{dm._BASE}/pbv.xls"]                    # no Global retry when sfx is empty
    urls.clear()
    monkeypatch.setattr(dm, "_download_bytes", lambda u: urls.append(u))
    dm.load_sector_table("margin", "mars")
    assert urls[0] == f"{dm._BASE}/marginGlobal.xls"


def test_load_sector_table_falls_back_to_global(dm, monkeypatch):
    raw = sector_sheet(["Industry Name", "Trailing PE"], [["Software", 30.0]])
    urls = []

    def dl(u):
        urls.append(u)
        return raw if u.endswith("peGlobal.xls") else None

    monkeypatch.setattr(dm, "_download_bytes", dl)
    df = dm.load_sector_table("pe", "japan")
    assert urls == [f"{dm._BASE}/peJapan.xls", f"{dm._BASE}/peGlobal.xls"]
    assert not df.empty


def test_load_sector_table_none_when_unparseable_or_empty(dm, monkeypatch):
    monkeypatch.setattr(dm, "_download_bytes", lambda u: b"junk")
    assert dm.load_sector_table("pe", "us") is None
    empty = sector_sheet(["Industry Name", "Trailing PE"], [])
    monkeypatch.setattr(dm, "_download_bytes", lambda u: empty)
    assert dm.load_sector_table("pe", "us") is None
    assert not dm._is_fresh("pe_us")                          # failures are not cached


# ---- country ERP ------------------------------------------------------------

def erp_bytes(data, header=("Country", "Region", "Rating", "Spread", "Total Equity Risk Premium",
                            "Country Risk Premium")):
    rows = [["x"] * 6 for _ in range(7)] + [list(header)] + data
    return xlsx_bytes({"ERPs by country": rows})


def test_load_country_erp(dm, monkeypatch):
    raw = erp_bytes([["India", "Asia", "Baa3", 0.02, 0.0725, 0.0225],
                     ["Country", "", "", "", "", ""],
                     ["Nowhere", "x", "y", 0, "n/a", 0.01],
                     [None, None, None, None, None, None]])
    monkeypatch.setattr(dm, "_download_bytes", lambda u: raw)
    df = dm.load_country_erp()
    assert list(df.index) == ["India"]                        # header repeat / non-numeric ERP dropped
    assert df.loc["India", "ERP"] == pytest.approx(0.0725)
    assert df.loc["India", "CRP"] == pytest.approx(0.0225)
    monkeypatch.setattr(dm, "_download_bytes", lambda u: pytest.fail("should hit cache"))
    assert dm.load_country_erp().loc["India", "ERP"] == pytest.approx(0.0725)


def test_load_country_erp_failures(dm, monkeypatch):
    monkeypatch.setattr(dm, "_download_bytes", lambda u: None)
    assert dm.load_country_erp() is None
    monkeypatch.setattr(dm, "_download_bytes", lambda u: b"junk")
    assert dm.load_country_erp() is None
    wrong_sheet = xlsx_bytes({"Other": [[1, 2]]})
    monkeypatch.setattr(dm, "_download_bytes", lambda u: wrong_sheet)
    assert dm.load_country_erp() is None


# ---- matching ---------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("software", "Software"),            # exact, case-insensitive
    ("  BANKS  ", "Banks"),              # trimmed
    ("Software (System & Application)", "Software"),   # index name inside user text
    ("Bank", "Banks"),                   # user text inside index name
    ("Mining", None),
    ("", None),
    ("   ", None),
    (None, None),
    (float("nan"), None),
])
def test_match_sector(dm, value, expected):
    assert dm._match_sector(value, pd.Index(["Software", "Banks"])) == expected


def test_match_sector_prefers_exact_over_substring(dm):
    idx = pd.Index(["Oil/Gas Distribution", "Oil/Gas"])
    assert dm._match_sector("oil/gas", idx) == "Oil/Gas"


# ---- enrich_triple_hits -----------------------------------------------------

@pytest.fixture
def tables(dm, monkeypatch):
    def mk(cols, vals):
        return pd.DataFrame(vals, index=["Software", "Banks"], columns=cols)
    data = {
        "pe": mk(["Sector_PE_Trailing", "Sector_PE_Fwd"], [[20.0, 18.0], [10.0, 9.0]]),
        "vebitda": mk(["Sector_EV_EBITDA", "Sector_EV_EBIT"], [[12.0, 15.0], [8.0, 9.0]]),
        "pbv": mk(["Sector_ROE", "Sector_ROIC"], [[0.3, 0.2], [0.1, 0.05]]),
        "margin": mk(["Sector_Net_Margin", "Sector_Op_Margin"], [[0.2, 0.25], [0.3, 0.35]]),
    }
    monkeypatch.setattr(dm, "load_sector_table", lambda ds, mk_: data[ds])
    monkeypatch.setattr(dm, "load_country_erp", lambda: pd.DataFrame(
        {"ERP": [0.07, 0.05], "CRP": [0.02, 0.0]}, index=pd.Index(["India", "United States"], name="Country")))
    return data


def test_enrich_adds_benchmarks_ratios_and_country(dm, tables):
    df = pd.DataFrame({"Sector": ["software", "Mining", "Banks"], "Country": ["India", "Atlantis", "united states"],
                       "PE": [40.0, 10.0, 5.0], "EVE": [24.0, 6.0, 4.0]})
    out = dm.enrich_triple_hits(df, pe_col="PE", ev_ebitda_col="EVE")
    assert out["Sector_PE_Trailing"].tolist()[0] == 20.0
    assert pd.isna(out["Sector_PE_Trailing"].tolist()[1])
    assert out["PE_vs_Sector"].tolist()[0] == 2.0
    assert out["PE_vs_Sector"].tolist()[2] == 0.5
    assert out["EV_EBITDA_vs_Sector"].tolist()[0] == 2.0
    assert out["Sector_ROE"].tolist()[2] == 0.1
    assert out["Sector_Op_Margin"].tolist()[0] == 0.25
    assert out["Country_ERP_pct"].tolist()[0] == 7.0
    assert out["Country_CRP_pct"].tolist()[0] == 2.0
    assert pd.isna(out["Country_ERP_pct"].tolist()[1])
    assert out["Country_ERP_pct"].tolist()[2] == 5.0
    assert "Sector_PE_Trailing" not in df.columns             # input untouched


def test_enrich_without_optional_columns(dm, tables):
    df = pd.DataFrame({"Sector": ["Banks"]})
    out = dm.enrich_triple_hits(df, country_col=None)
    assert "PE_vs_Sector" not in out and "EV_EBITDA_vs_Sector" not in out
    assert "Country_ERP_pct" not in out
    out2 = dm.enrich_triple_hits(df)                          # default country_col missing from df
    assert "Country_ERP_pct" not in out2


def test_enrich_missing_sector_column_or_tables(dm, monkeypatch, tables):
    out = dm.enrich_triple_hits(pd.DataFrame({"Other": [1]}))
    assert list(out.columns) == ["Other"]
    monkeypatch.setattr(dm, "load_sector_table", lambda ds, m: None)
    monkeypatch.setattr(dm, "load_country_erp", lambda: None)
    out = dm.enrich_triple_hits(pd.DataFrame({"Sector": ["Banks"], "Country": ["India"]}))
    assert list(out.columns) == ["Sector", "Country"]


def test_enrich_passes_market(dm, monkeypatch, tables):
    seen = []
    monkeypatch.setattr(dm, "load_sector_table", lambda ds, m: seen.append((ds, m)) or None)
    dm.enrich_triple_hits(pd.DataFrame({"Sector": ["Banks"]}), market="japan")
    assert seen == [("pe", "japan"), ("vebitda", "japan"), ("pbv", "japan"), ("margin", "japan")]


# ---- CLI --------------------------------------------------------------------

def run_main(dm, monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["damodaran_enrichment", *argv])
    dm.main()


def test_preview_prints_each_table(dm, monkeypatch, capsys, tables):
    run_main(dm, monkeypatch, "--preview", "--market", "us")
    out = capsys.readouterr().out
    for name in ("PE", "VEBITDA", "PBV", "MARGIN", "ERP"):
        assert f"── {name} (" in out
    assert "(2 rows)" in out


def test_preview_reports_failed_tables(dm, monkeypatch, capsys):
    monkeypatch.setattr(dm, "load_sector_table", lambda ds, m: None)
    monkeypatch.setattr(dm, "load_country_erp", lambda: None)
    run_main(dm, monkeypatch)                                   # no --file => preview
    out = capsys.readouterr().out
    assert out.count("FAILED TO LOAD") == 5


def test_clear_cache_removes_directory(dm, monkeypatch, tables):
    dm._cache_path("k")
    assert dm._CACHE_DIR.exists()
    run_main(dm, monkeypatch, "--clear-cache", "--preview")
    assert not dm._CACHE_DIR.exists()


def test_enrich_file_end_to_end(dm, monkeypatch, capsys, tables, tmp_path):
    src = tmp_path / "scan.xlsx"
    with pd.ExcelWriter(src, engine="openpyxl") as w:
        pd.DataFrame({"Sector": ["Software"], "PE": [40.0]}).to_excel(w, sheet_name="Triple Hits", index=False)
        pd.DataFrame({"a": [1]}).to_excel(w, sheet_name="Other", index=False)
    run_main(dm, monkeypatch, "--file", str(src), "--pe-col", "PE")
    out_file = tmp_path / "scan_enriched.xlsx"
    assert out_file.exists()
    book = pd.ExcelFile(out_file)
    assert book.sheet_names == ["Triple Hits", "Other"]
    enriched = book.parse("Triple Hits")
    assert enriched.loc[0, "PE_vs_Sector"] == 2.0
    assert book.parse("Other").loc[0, "a"] == 1                  # other sheets copied untouched
    printed = capsys.readouterr().out
    assert "New columns added:" in printed and "  PE_vs_Sector" in printed
    assert "  Sector" not in printed.replace("  Sector_", "")     # original columns not listed as new

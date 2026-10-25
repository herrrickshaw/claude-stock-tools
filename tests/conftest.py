"""Shared helpers. Nothing here touches the network.

The scripts live in digit-prefixed folders and create output folders in the
CWD at import time, so modules are loaded by path from inside a temp dir, with
the network-only India/Korea libraries (nsepython, bseindia, pykrx) stubbed.
"""
import ast
import importlib.util
import os
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]

DARVAS_FILES = [
    "01_daily_reports/india_daily_report.py",
    "01_daily_reports/singapore_daily_report.py",
    "01_daily_reports/us_daily_report.py",
    "02_market_screeners/india_market_scan.py",
    "02_market_screeners/us_market_scan.py",
    "02_market_screeners/japan_market_scan.py",
    "02_market_screeners/korea_market_scan.py",
    "02_market_screeners/europe_market_scan.py",
    "03_colab_notebooks/us_stocks_colab_script.py",
]


def load_functions(relpath, names=("compute_darvas_box", "_darvas_core"), extra=None):
    """Exec only the named top-level functions of a script (no module side effects)."""
    tree = ast.parse((ROOT / relpath).read_text())
    keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    ns = {"pd": pd, "DARVAS_CONFIRM": 3, **(extra or {})}
    exec(compile(ast.Module(body=keep, type_ignores=[]), relpath, "exec"), ns)
    return ns


def _stub_network_libs():
    for name in ("nsepython", "bseindia", "bseindia.libutil", "pykrx", "pykrx.stock"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["nsepython"].get_bhavcopy = lambda *a, **k: None
    sys.modules["bseindia.libutil"].SECURITY_MASTER_URL = "stub://"
    sys.modules["bseindia"].libutil = sys.modules["bseindia.libutil"]
    sys.modules["pykrx"].stock = sys.modules["pykrx.stock"]


@pytest.fixture(scope="session")
def load_script(tmp_path_factory):
    cache = {}
    workdir = tmp_path_factory.mktemp("cwd")

    def _load(relpath):
        if relpath not in cache:
            _stub_network_libs()
            old = os.getcwd()
            os.chdir(workdir)
            try:
                spec = importlib.util.spec_from_file_location(
                    "cst_" + Path(relpath).stem, ROOT / relpath)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
            finally:
                os.chdir(old)
            cache[relpath] = mod
        return cache[relpath]
    return _load

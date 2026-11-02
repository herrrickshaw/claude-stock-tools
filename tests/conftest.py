"""Shared fixtures: load the digit-prefixed scanner scripts by path, offline, in a temp cwd."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCREENERS = REPO / "02_market_screeners"
SCANNERS = ("india", "us", "europe", "japan", "korea")


@pytest.fixture
def load_scanner(tmp_path, monkeypatch):
    """Import ``<name>_market_scan.py`` with cwd=tmp_path (the scripts mkdir at import time)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(SCREENERS))  # mirrors running the script directly
    # Optional market-data libraries are import-time hard requirements of some scanners; stub them.
    for top, subs in (("nsepython", ()), ("bseindia", ("libutil",)), ("pykrx", ())):
        if importlib.util.find_spec(top) is None:
            monkeypatch.setitem(sys.modules, top, types.ModuleType(top))
            for sub in subs:
                monkeypatch.setitem(sys.modules, f"{top}.{sub}", types.ModuleType(f"{top}.{sub}"))
    stubs = {
        "nsepython": {"get_bhavcopy": lambda *a, **k: None},
        "bseindia.libutil": {"SECURITY_MASTER_URL": ""},
        "pykrx": {"stock": types.SimpleNamespace()},
    }
    for name, attrs in stubs.items():
        mod = sys.modules.get(name)
        if mod is not None and not getattr(mod, "__spec__", None):
            for k, v in attrs.items():
                setattr(mod, k, v)

    def _load(name: str):
        mod_name = f"_scan_{name}"
        sys.modules.pop(mod_name, None)
        spec = importlib.util.spec_from_file_location(mod_name, SCREENERS / f"{name}_market_scan.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod

    return _load

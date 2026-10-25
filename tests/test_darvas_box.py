"""compute_darvas_box is copy-pasted into 9 scripts; every copy is tested.

Rules: the current bar is excluded from box formation, and a missing close is
never zero-filled (a 0.0 'current price' sits below every box bottom and
reports BREAKDOWN_SELL — the failure that once flagged 2,472 of 2,480 Korean
stocks).
"""
import math

import numpy as np
import pandas as pd
import pytest

from conftest import DARVAS_FILES, load_functions

BOX = [105, 104, 103, 104, 105, 104, 103, 104, 105, 104] * 3


def frame(closes, highs=None, lows=None):
    closes = [float(c) for c in closes]
    highs = highs or [c * 1.01 for c in closes]
    lows = lows or [c * 0.99 for c in closes]
    idx = pd.bdate_range("2026-01-01", periods=len(closes))
    return pd.DataFrame({"Open": closes, "High": highs, "Low": lows, "Close": closes,
                         "Volume": 1000}, index=idx)


@pytest.fixture(params=DARVAS_FILES)
def darvas(request):
    return load_functions(request.param)["compute_darvas_box"]


def test_breakout_above_box_top(darvas):
    r = darvas(frame(BOX + [120]))
    assert r["signal"] == "BREAKOUT_BUY"
    # JP/KR copies round prices to whole yen/won, so allow 0.05
    assert r["box_top"] == pytest.approx(105 * 1.01, abs=0.06)


def test_breakdown_below_box_bottom_uses_history_only(darvas):
    # If the current bar's low (79.2) were in the box, this could never break down.
    r = darvas(frame(BOX + [80]))
    assert r["signal"] == "BREAKDOWN_SELL"
    assert r["box_bottom"] > 100


def test_in_box(darvas):
    assert darvas(frame(BOX + [104]))["signal"] == "IN_BOX"


def test_too_few_bars_is_insufficient(darvas):
    assert darvas(frame([100, 101, 102]))["signal"] == "INSUFFICIENT_DATA"


def test_rising_series_has_no_confirmed_top(darvas):
    assert darvas(frame([100 + i for i in range(40)]))["signal"] == "NO_BOX"


def test_trailing_missing_close_is_not_a_breakdown(darvas):
    # Today's bar has no settled close yet (NaN). It must be ignored, not read as 0.
    df = frame(BOX + [104, 104])
    df.iloc[-1, df.columns.get_loc("Close")] = np.nan
    r = darvas(df)
    assert r["signal"] != "BREAKDOWN_SELL"
    assert r.get("current_price") in (None, pytest.approx(104))


def test_zero_close_from_source_is_not_a_breakdown(darvas):
    df = frame(BOX + [104, 104])
    df.iloc[-1, df.columns.get_loc("Close")] = 0.0
    assert darvas(df)["signal"] != "BREAKDOWN_SELL"


def test_all_copies_agree():
    # The 9 copies must give the same signal, and the same box up to the
    # whole-unit rounding the JP/KR copies apply.
    cases = [BOX + [120], BOX + [80], BOX + [104], [100 + i for i in range(40)]]
    for closes in cases:
        results = [load_functions(f)["compute_darvas_box"](frame(closes)) for f in DARVAS_FILES]
        assert len({r["signal"] for r in results}) == 1, closes[-1]
        for key in ("box_top", "box_bottom"):
            vals = [r.get(key) for r in results]
            if vals[0] is None:
                assert all(v is None for v in vals)
            else:
                assert max(vals) - min(vals) <= 0.06, (key, vals)

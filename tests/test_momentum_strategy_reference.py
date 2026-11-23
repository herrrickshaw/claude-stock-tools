"""momentum_strategy_reference is a module of printable reference text; pin its structure."""
import pytest

PATH = "04_strategy_reference/momentum_strategy_reference.py"
NAMES = ["KEY_METRICS", "PROFIT_SCENARIOS", "COST_BREAKDOWN", "API_COMPARISON", "INDICATORS_REFERENCE",
         "EXECUTION_CHECKLIST", "MARKET_HOURS", "RISK_LIMITS", "QUICK_FORMULAS", "TROUBLESHOOTING"]


@pytest.fixture(scope="module")
def ref(load_script):
    return load_script(PATH)


@pytest.mark.parametrize("name", NAMES)
def test_every_section_is_non_trivial_text(ref, name):
    text = getattr(ref, name)
    assert isinstance(text, str) and len(text.strip()) > 100
    assert text.count("\n") > 3


def test_sections_are_unique_and_importing_has_no_side_effects(ref, tmp_path):
    assert len({getattr(ref, n) for n in NAMES}) == len(NAMES)
    # the file write to /mnt/user-data/outputs lives under the __main__ guard only
    assert not hasattr(ref, "reference_materials")

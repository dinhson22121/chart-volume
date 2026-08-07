"""Decision-tracing tests for SMC (app.smc.trace) -- the SMC counterpart to
test_wyckoff.py's trace_bar tests.

Uses the same zigzag fixtures as test_smc_events.py so a traced bar can be
cross-checked against the detector result the very same data produces."""

import pandas as pd

from app.smc.config import SMCConfig
from app.smc.events import BEARISH_FVG, BOS_BEAR, BOS_BULL, CHOCH_BEAR, CHOCH_BULL, detect_events
from app.smc.indicators import compute_features
from app.smc.trace import trace_bar

CFG = SMCConfig(swing_lookback=2, ob_lookback_bars=10, fvg_min_gap_mult=0.3)

# Same waypoints as test_smc_events.test_choch_bull_records_the_swing_high_it_broke:
# a CHoCH_Bull fires at index 20, breaking the swing high confirmed at index 11.
CHOCH_VALUES = [110, 108, 106, 104, 102, 100, 102, 104, 106, 108, 110, 112, 110, 108, 106, 104, 103, 105, 108, 111, 113]
CHOCH_INDEX = 20


def _df(values):
    opens = [v - 0.3 for v in values]
    highs = [v + 0.5 for v in values]
    lows = [v - 0.5 for v in values]
    n = len(values)
    return pd.DataFrame({
        "time": pd.date_range("2025-01-01", periods=n, freq="D"),
        "open": opens, "high": highs, "low": lows, "close": list(values),
        "volume": [1000.0] * n,
    })


def _feat(values):
    return compute_features(_df(values), CFG)


def _by_type(traces, event_type):
    return next((t for t in traces if t.type == event_type), None)


def test_matched_flags_agree_with_the_real_detectors():
    # The whole point of the design: `matched` is never re-derived, it comes
    # from detect_events. Any type that fired at this bar must be matched in
    # the trace, and nothing else may claim to have matched.
    feat = _feat(CHOCH_VALUES)
    fired = {e.type for e in detect_events(feat, CFG) if e.index == CHOCH_INDEX}

    traces = trace_bar(feat, CHOCH_INDEX, CFG)

    assert fired  # sanity: this bar really does fire something
    assert {t.type for t in traces if t.matched} == fired


def test_choch_bull_bar_explains_every_condition_it_met():
    traces = trace_bar(_feat(CHOCH_VALUES), CHOCH_INDEX, CFG)

    choch = _by_type(traces, CHOCH_BULL)
    assert choch is not None
    assert choch.matched is True
    assert all(c.passed for c in choch.checks)


def test_bos_bull_not_matched_on_the_same_bar_and_says_why():
    # The break itself happened, so the level/close checks pass for both --
    # what separates BOS from CHoCH here is only the prior structure trend.
    traces = trace_bar(_feat(CHOCH_VALUES), CHOCH_INDEX, CFG)

    bos = _by_type(traces, BOS_BULL)
    assert bos is not None
    assert bos.matched is False
    failed = [c for c in bos.checks if not c.passed]
    assert len(failed) == 1  # only the trend condition, not the break itself


def test_no_vacuous_all_false_rows():
    # A detector row is only worth showing when it matched or at least one of
    # its conditions holds -- otherwise it's noise (see the module docstring).
    traces = trace_bar(_feat(CHOCH_VALUES), CHOCH_INDEX, CFG)

    assert traces
    for t in traces:
        assert t.matched or any(c.passed for c in t.checks)


def test_early_bar_traces_its_fvg_without_claiming_any_structure_break():
    # Bar 3 sits mid-descent, where the zigzag leaves a bearish Fair Value
    # Gap -- but no swing level has been broken there, so no BOS/CHoCH type
    # may claim a match even though those rows are still shown (a level being
    # "in play" is itself worth reading).
    traces = trace_bar(_feat(CHOCH_VALUES), 3, CFG)

    assert {t.type for t in traces if t.matched} == {BEARISH_FVG}
    structure_types = {BOS_BULL, BOS_BEAR, CHOCH_BULL, CHOCH_BEAR}
    assert not any(t.matched for t in traces if t.type in structure_types)


def test_language_switch_changes_check_labels():
    vi = trace_bar(_feat(CHOCH_VALUES), CHOCH_INDEX, CFG, language="vi")
    en = trace_bar(_feat(CHOCH_VALUES), CHOCH_INDEX, CFG, language="en")

    vi_choch = _by_type(vi, CHOCH_BULL)
    en_choch = _by_type(en, CHOCH_BULL)
    assert vi_choch is not None and en_choch is not None
    assert vi_choch.matched == en_choch.matched
    vi_labels = " ".join(c.label for c in vi_choch.checks)
    en_labels = " ".join(c.label for c in en_choch.checks)
    assert vi_labels != en_labels
    assert "swing" in en_labels.lower()


def test_checks_are_plain_python_bools():
    # Same numpy.bool_ serialization trap app.wyckoff.events.Check guards
    # against -- FastAPI can't serialize numpy scalars.
    traces = trace_bar(_feat(CHOCH_VALUES), CHOCH_INDEX, CFG)

    for t in traces:
        assert isinstance(t.matched, bool)
        for c in t.checks:
            assert isinstance(c.passed, bool)

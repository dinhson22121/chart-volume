import pandas as pd
import pytest

from app.sonicr.config import SonicRConfig
from app.sonicr.indicators import cci, compute_features, ema, t3


def test_ema_matches_hand_computed_recursion():
    # alpha = 2/(3+1) = 0.5 -> ema[0]=10, ema[1]=0.5*11+0.5*10=10.5,
    # ema[2]=0.5*12+0.5*10.5=11.25, ema[3]=12.125, ema[4]=13.0625
    closes = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0])
    result = ema(closes, 3)
    assert result.tolist() == pytest.approx([10.0, 10.5, 11.25, 12.125, 13.0625])


def _manual_ema(values: list[float], period: int) -> list[float]:
    alpha = 2 / (period + 1)
    out: list[float] = []
    prev = None
    for v in values:
        prev = v if prev is None else alpha * v + (1 - alpha) * prev
        out.append(prev)
    return out


def test_t3_matches_independent_6_pass_ema_chain_oracle():
    # Independent oracle: manually chain 6 EMA passes + Tillson blend
    # coefficients, without calling into ema()/t3() internals.
    closes = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 18.0]
    period, vfactor = 2, 0.7

    e = [closes]
    for _ in range(6):
        e.append(_manual_ema(e[-1], period))
    e1, e2, e3, e4, e5, e6 = e[1:]

    a = vfactor
    c1 = -(a**3)
    c2 = 3 * a**2 + 3 * a**3
    c3 = -6 * a**2 - 3 * a - 3 * a**3
    c4 = 1 + 3 * a + a**3 + 3 * a**2
    expected = [c1 * e6[i] + c2 * e5[i] + c3 * e4[i] + c4 * e3[i] for i in range(len(closes))]

    result = t3(pd.Series(closes), period, vfactor)
    assert result.tolist() == pytest.approx(expected)


def test_cci_matches_hand_computed_typical_price_deviation():
    # TP = (H+L+C)/3 -> 9, 10, 11, 12, 13 (steady +1/bar).
    # window(3) at index 2 = [9,10,11]: sma=10, mad=mean(1,0,1)=2/3
    # cci = (11-10) / (0.015 * 2/3) = 100.0, and stays 100.0 for a linear ramp.
    df = pd.DataFrame({
        "high": [10, 11, 12, 13, 14],
        "low": [8, 9, 10, 11, 12],
        "close": [9, 10, 11, 12, 13],
    })
    result = cci(df, 3)
    assert result.iloc[:2].isna().all()
    assert result.iloc[2:].tolist() == pytest.approx([100.0, 100.0, 100.0])


def test_compute_features_adds_expected_columns():
    df = pd.DataFrame({
        "time": pd.date_range("2025-01-01", periods=40, freq="D"),
        "open": [100.0] * 40,
        "high": [101.0] * 40,
        "low": [99.0] * 40,
        "close": [100.0 + i * 0.1 for i in range(40)],
        "volume": [1000.0] * 40,
    })
    cfg = SonicRConfig(dragon_period=5, t3_fast_period=3, t3_slow_period=4, cci_fast_period=3, cci_slow_period=5)

    out = compute_features(df, cfg)

    for col in ("dragon", "t3_fast", "t3_slow", "cci_fast", "cci_slow"):
        assert col in out.columns
    # Enough bars given the short periods -> last row should be fully populated.
    last = out.iloc[-1]
    assert not pd.isna(last["dragon"])
    assert not pd.isna(last["t3_fast"])
    assert not pd.isna(last["cci_slow"])

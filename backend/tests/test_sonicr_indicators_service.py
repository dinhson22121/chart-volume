import pandas as pd

from app.models import Candle, Timeframe
from app.services import sonicr_indicators


def _seed_candles(session, n=60, ticker="BTC"):
    t0 = pd.Timestamp("2025-01-01")
    for i in range(n):
        price = 100.0 + i * 0.8
        session.add(
            Candle(
                ticker=ticker,
                timeframe=Timeframe.DAILY,
                bucket_start=(t0 + pd.Timedelta(days=i)).to_pydatetime(),
                open=price,
                high=price + 1,
                low=price - 1,
                close=price,
                volume=1000.0,
            )
        )
    session.commit()


def test_get_indicator_series_returns_empty_when_no_candles(session):
    result = sonicr_indicators.get_indicator_series(session, "BTC", Timeframe.DAILY)
    assert result == {"dragon": [], "t3_fast": [], "t3_slow": []}


def test_get_indicator_series_returns_dragon_and_t3_for_stored_candles(session):
    _seed_candles(session, n=60)

    result = sonicr_indicators.get_indicator_series(session, "BTC", Timeframe.DAILY)

    assert set(result.keys()) == {"dragon", "t3_fast", "t3_slow"}
    for series in result.values():
        assert len(series) > 0
        for point in series:
            assert "ts" in point and "value" in point
            assert isinstance(point["value"], float)
    # EMA/T3 are recursive and seeded from the first bar -- no NaN warm-up
    # period, so every series covers the full candle history.
    assert len(result["dragon"]) == 60
    assert len(result["t3_fast"]) == 60


def test_get_indicator_series_ticker_is_case_insensitive(session):
    _seed_candles(session, n=60, ticker="ETH")

    result = sonicr_indicators.get_indicator_series(session, "eth", Timeframe.DAILY)

    assert len(result["dragon"]) > 0

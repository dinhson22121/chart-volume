import pandas as pd
import pytest

from app.models import Candle, Timeframe
from app.services import benchmark


def _candle(ticker: str, day: int, close: float, timeframe: str = Timeframe.DAILY) -> Candle:
    t0 = pd.Timestamp("2025-01-01")
    ts = (t0 + pd.Timedelta(days=day)).to_pydatetime()
    return Candle(ticker=ticker, timeframe=timeframe, bucket_start=ts, open=close, high=close, low=close, close=close, volume=1.0)


def test_buy_hold_return_basic(session):
    session.add(_candle("FPT", 0, 100.0))
    session.add(_candle("FPT", 10, 120.0))
    session.commit()

    r = benchmark.buy_hold_return(
        session, "FPT", Timeframe.DAILY, pd.Timestamp("2025-01-01"), pd.Timestamp("2025-01-11")
    )

    assert r == pytest.approx(0.20)


def test_buy_hold_return_picks_nearest_candles_inside_window(session):
    session.add(_candle("FPT", 0, 100.0))
    session.add(_candle("FPT", 5, 110.0))
    session.add(_candle("FPT", 10, 120.0))
    session.commit()

    # Window narrower than the full series -- should use day 5 (nearest >=
    # start) and day 10 (nearest <= end), not day 0.
    r = benchmark.buy_hold_return(
        session, "FPT", Timeframe.DAILY, pd.Timestamp("2025-01-04"), pd.Timestamp("2025-01-11")
    )

    assert r == pytest.approx((120.0 - 110.0) / 110.0)


def test_buy_hold_return_none_when_no_candles(session):
    r = benchmark.buy_hold_return(
        session, "MISSING", Timeframe.DAILY, pd.Timestamp("2025-01-01"), pd.Timestamp("2025-01-11")
    )
    assert r is None


def test_buy_hold_return_none_when_window_collapses_to_one_bar(session):
    session.add(_candle("FPT", 0, 100.0))
    session.commit()

    r = benchmark.buy_hold_return(
        session, "FPT", Timeframe.DAILY, pd.Timestamp("2025-01-01"), pd.Timestamp("2025-01-01")
    )
    assert r is None


def test_buy_hold_return_ignores_other_ticker_and_timeframe(session):
    session.add(_candle("FPT", 0, 100.0))
    session.add(_candle("FPT", 10, 999.0, timeframe=Timeframe.WEEK))
    session.add(_candle("HPG", 10, 999.0))
    session.commit()

    r = benchmark.buy_hold_return(
        session, "FPT", Timeframe.DAILY, pd.Timestamp("2025-01-01"), pd.Timestamp("2025-01-11")
    )
    assert r is None  # only one DAILY/FPT candle in range -> window collapses

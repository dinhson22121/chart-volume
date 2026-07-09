import pandas as pd

from app.models import Symbol, Timeframe
from app.scheduler import build_scheduler, run_batch
from app.services import analysis as analysis_svc
from app.services import ingest

BASE = dict(open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0)
SPRING = dict(open=98.0, high=99.8, low=97.0, close=99.3, volume=1500.0)
CANNED = "NHẬN ĐỊNH:\nx\n\nLỜI KHUYÊN:\n- y"


def _daily_df():
    t0 = pd.Timestamp("2025-01-01")
    bars = [dict(BASE) for _ in range(25)] + [SPRING]
    return pd.DataFrame([{"time": t0 + pd.Timedelta(days=i), **b} for i, b in enumerate(bars)])


def test_build_scheduler_registers_expected_jobs():
    sched = build_scheduler()
    ids = {j.id for j in sched.get_jobs()}
    assert ids == {"half_session_morning", "half_session_afternoon", "daily_close"}


def test_run_batch_processes_all_tracked(session, mocker):
    session.add(Symbol(ticker="FPT", is_vn30=True))
    session.add(Symbol(ticker="HPG", is_watchlist=True))
    session.add(Symbol(ticker="XXX", is_watchlist=False, is_vn30=False))  # not tracked
    session.commit()

    mocker.patch.object(ingest.vnstock_client, "fetch_daily", return_value=_daily_df())
    mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)

    ok = run_batch(session, Timeframe.DAILY)

    assert ok == 2  # FPT + HPG, not XXX


def test_run_batch_isolates_ticker_failures(session, mocker):
    session.add(Symbol(ticker="GOOD", is_vn30=True))
    session.add(Symbol(ticker="BAD", is_vn30=True))
    session.commit()

    from app.models import Candle

    def fake_ingest(sess, ticker, *a, **k):
        if ticker == "BAD":
            raise RuntimeError("boom")
        t0 = pd.Timestamp("2025-01-01")
        bars = [dict(BASE) for _ in range(25)] + [SPRING]
        for i, b in enumerate(bars):
            sess.add(Candle(ticker=ticker, timeframe=Timeframe.DAILY,
                            bucket_start=(t0 + pd.Timedelta(days=i)).to_pydatetime(), **b))
        sess.commit()
        return len(bars)

    mocker.patch.object(ingest, "ingest_daily", side_effect=fake_ingest)
    mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)

    ok = run_batch(session, Timeframe.DAILY)

    assert ok == 1  # GOOD succeeded, BAD failed but didn't abort the batch

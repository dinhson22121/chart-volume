import pandas as pd
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import AssetClass, Symbol, Timeframe
from app.scheduler import build_scheduler, reschedule, run_batch, run_crypto_batch
from app.services import analysis as analysis_svc
from app.services import ingest, settings_service

BASE = dict(open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0)
SPRING = dict(open=98.0, high=99.8, low=97.0, close=99.3, volume=1500.0)
CANNED = "NHẬN ĐỊNH:\nx\n\nLỜI KHUYÊN:\n- y"


def _daily_df():
    t0 = pd.Timestamp("2025-01-01")
    bars = [dict(BASE) for _ in range(25)] + [SPRING]
    return pd.DataFrame([{"time": t0 + pd.Timedelta(days=i), **b} for i, b in enumerate(bars)])


def _fresh_engine():
    """A standalone SQLite engine, isolated from the shared `session` fixture,
    so scheduler tests that go through app.db.get_engine() don't touch the
    real file-backed DB or leak state between tests."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    return engine


def test_build_scheduler_registers_expected_jobs(mocker):
    engine = _fresh_engine()
    mocker.patch("app.scheduler.get_engine", return_value=engine)

    sched = build_scheduler()
    ids = {j.id for j in sched.get_jobs()}
    # crypto_analysis_refresh is on by default too (see test_run_batch/crypto tests below).
    assert ids == {"half_session_morning", "half_session_afternoon", "daily_close", "crypto_analysis_refresh"}


def test_scheduler_disabled_registers_no_jobs(mocker):
    engine = _fresh_engine()
    mocker.patch("app.scheduler.get_engine", return_value=engine)
    with Session(engine) as s:
        settings_service.update(s, {"scheduler_enabled": "false", "crypto_analysis_enabled": "false"})

    sched = build_scheduler()
    assert sched.get_jobs() == []


def test_reschedule_applies_custom_times(mocker):
    engine = _fresh_engine()
    mocker.patch("app.scheduler.get_engine", return_value=engine)
    with Session(engine) as s:
        settings_service.update(s, {"daily_time": "16:30"})

    sched = build_scheduler()
    job = sched.get_job("daily_close")
    fields = {f.name: f for f in job.trigger.fields}
    assert str(fields["hour"]) == "16"
    assert str(fields["minute"]) == "30"


def test_reschedule_can_toggle_back_on(mocker):
    engine = _fresh_engine()
    mocker.patch("app.scheduler.get_engine", return_value=engine)
    with Session(engine) as s:
        settings_service.update(s, {"scheduler_enabled": "false", "crypto_analysis_enabled": "false"})

    sched = build_scheduler()
    assert sched.get_jobs() == []

    with Session(engine) as s:
        settings_service.update(s, {"scheduler_enabled": "true"})
    reschedule(sched)

    assert {j.id for j in sched.get_jobs()} == {
        "half_session_morning",
        "half_session_afternoon",
        "daily_close",
    }


def test_screener_disabled_by_default(mocker):
    engine = _fresh_engine()
    mocker.patch("app.scheduler.get_engine", return_value=engine)

    sched = build_scheduler()

    assert "crypto_screener_scan" not in {j.id for j in sched.get_jobs()}


def test_screener_enabled_registers_interval_job(mocker):
    engine = _fresh_engine()
    mocker.patch("app.scheduler.get_engine", return_value=engine)
    with Session(engine) as s:
        settings_service.update(s, {"screener_enabled": "true", "screener_scan_interval": "4h"})

    sched = build_scheduler()
    job = sched.get_job("crypto_screener_scan")

    assert job is not None
    assert job.trigger.interval.total_seconds() == 4 * 3600


def test_screener_and_stock_scheduler_toggle_independently(mocker):
    engine = _fresh_engine()
    mocker.patch("app.scheduler.get_engine", return_value=engine)
    with Session(engine) as s:
        settings_service.update(
            s,
            {"scheduler_enabled": "false", "screener_enabled": "true", "crypto_analysis_enabled": "false"},
        )

    sched = build_scheduler()
    ids = {j.id for j in sched.get_jobs()}

    assert ids == {"crypto_screener_scan"}  # stock jobs off, screener on, crypto analysis off


def test_crypto_analysis_disabled_by_default_is_false():
    # Sanity check on the default itself (used implicitly by the "expected jobs" test above).
    assert settings_service.DEFAULTS["crypto_analysis_enabled"] == "true"


def test_crypto_analysis_toggle_independent_of_stock_and_screener(mocker):
    engine = _fresh_engine()
    mocker.patch("app.scheduler.get_engine", return_value=engine)
    with Session(engine) as s:
        settings_service.update(
            s,
            {
                "scheduler_enabled": "false",
                "screener_enabled": "false",
                "crypto_analysis_enabled": "true",
                "crypto_analysis_interval": "1h",
            },
        )

    sched = build_scheduler()
    ids = {j.id for j in sched.get_jobs()}

    assert ids == {"crypto_analysis_refresh"}
    job = sched.get_job("crypto_analysis_refresh")
    assert job.trigger.interval.total_seconds() == 3600


def test_run_batch_processes_all_tracked(session, mocker):
    session.add(Symbol(ticker="FPT", is_vn30=True))
    session.add(Symbol(ticker="HPG", is_watchlist=True))
    session.add(Symbol(ticker="XXX", is_watchlist=False, is_vn30=False))  # not tracked
    session.commit()

    mocker.patch.object(ingest.vnstock_client, "fetch_daily", return_value=_daily_df())
    mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)

    ok = run_batch(session, Timeframe.DAILY)

    assert ok == 2  # FPT + HPG, not XXX


def test_run_batch_excludes_crypto_tickers(session, mocker):
    # Previously crypto tickers silently fell through run_batch (it always
    # called the stock ingest functions on them, which is wrong) -- now they
    # must be filtered out entirely and handled by run_crypto_batch instead.
    session.add(Symbol(ticker="FPT", is_vn30=True, asset_class=AssetClass.STOCK))
    session.add(Symbol(ticker="PEPE", is_watchlist=True, asset_class=AssetClass.CRYPTO))
    session.commit()

    fetch_daily = mocker.patch.object(ingest.vnstock_client, "fetch_daily", return_value=_daily_df())
    mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)

    ok = run_batch(session, Timeframe.DAILY)

    assert ok == 1  # only FPT
    fetch_daily.assert_called_once_with("FPT", mocker.ANY, mocker.ANY)


def test_run_crypto_batch_ingests_all_three_timeframes(session, mocker):
    session.add(Symbol(ticker="PEPE", is_watchlist=True, asset_class=AssetClass.CRYPTO))
    session.add(Symbol(ticker="FPT", is_vn30=True, asset_class=AssetClass.STOCK))  # excluded
    session.commit()

    # No candles get written (ingest_crypto is stubbed out), so run_analysis
    # naturally no-ops (returns None, doesn't raise) -- ok just tracks that no
    # exception was raised, not that an Analysis row was actually produced.
    ingest_spy = mocker.patch.object(ingest, "ingest_crypto", return_value=1)

    ok = run_crypto_batch(session)

    assert ok == 3  # 1h + 4h + daily for PEPE only
    timeframes_called = {call.args[2] for call in ingest_spy.call_args_list}
    assert timeframes_called == {Timeframe.HOUR_1, Timeframe.HOUR_4, Timeframe.DAILY}
    tickers_called = {call.args[1] for call in ingest_spy.call_args_list}
    assert tickers_called == {"PEPE"}


def test_run_crypto_batch_isolates_timeframe_failures(session, mocker):
    session.add(Symbol(ticker="PEPE", is_watchlist=True, asset_class=AssetClass.CRYPTO))
    session.commit()

    def fake_ingest(sess, ticker, timeframe, **kwargs):
        if timeframe == Timeframe.HOUR_4:
            raise RuntimeError("boom")
        return 1

    mocker.patch.object(ingest, "ingest_crypto", side_effect=fake_ingest)

    ok = run_crypto_batch(session)

    assert ok == 2  # 1h + daily succeeded, 4h failed but didn't abort the batch


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

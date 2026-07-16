from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import AssetClass, Symbol, SystemActionLog, Timeframe
from app.scheduler import (
    _TZ,
    _crypto_batch_job,
    _daily_job,
    _half_session_job,
    _screener_job,
    build_scheduler,
    reschedule,
    run_batch,
    run_crypto_batch,
)
from app.services import analysis as analysis_svc
from app.services import activity_log, ingest, settings_service

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
    # crypto_analysis_refresh and top100_refresh are on by default too.
    assert ids == {
        "half_session_morning", "half_session_afternoon", "daily_close",
        "crypto_analysis_refresh", "top100_refresh",
    }


def test_scheduler_disabled_registers_no_jobs(mocker):
    engine = _fresh_engine()
    mocker.patch("app.scheduler.get_engine", return_value=engine)
    with Session(engine) as s:
        settings_service.update(
            s,
            {
                "scheduler_enabled": "false",
                "crypto_analysis_enabled": "false",
                "top100_auto_refresh_enabled": "false",
            },
        )

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
        settings_service.update(
            s,
            {
                "scheduler_enabled": "false",
                "crypto_analysis_enabled": "false",
                "top100_auto_refresh_enabled": "false",
            },
        )

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


def test_screener_enabled_runs_immediately_not_after_a_full_interval(mocker):
    # Interval jobs are now self-rescheduling (fixed-delay from completion,
    # not fixed-rate) -- the FIRST run fires right away regardless of the
    # configured interval; only the run *after* that waits the full interval.
    engine = _fresh_engine()
    mocker.patch("app.scheduler.get_engine", return_value=engine)
    with Session(engine) as s:
        settings_service.update(s, {"screener_enabled": "true", "screener_scan_interval": "4h"})

    sched = build_scheduler()
    job = sched.get_job("crypto_screener_scan")

    assert job is not None
    assert job.trigger.run_date - datetime.now(_TZ) < timedelta(seconds=5)


def test_screener_and_stock_scheduler_toggle_independently(mocker):
    engine = _fresh_engine()
    mocker.patch("app.scheduler.get_engine", return_value=engine)
    with Session(engine) as s:
        settings_service.update(
            s,
            {
                "scheduler_enabled": "false",
                "screener_enabled": "true",
                "crypto_analysis_enabled": "false",
                "top100_auto_refresh_enabled": "false",
            },
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
                "top100_auto_refresh_enabled": "false",
            },
        )

    sched = build_scheduler()
    ids = {j.id for j in sched.get_jobs()}

    assert ids == {"crypto_analysis_refresh"}
    job = sched.get_job("crypto_analysis_refresh")
    assert job.trigger.run_date - datetime.now(_TZ) < timedelta(seconds=5)


def test_top100_refresh_registers_daily_cron_at_configured_time(mocker):
    engine = _fresh_engine()
    mocker.patch("app.scheduler.get_engine", return_value=engine)
    with Session(engine) as s:
        settings_service.update(
            s,
            {
                "scheduler_enabled": "false",
                "crypto_analysis_enabled": "false",
                "top100_refresh_time": "08:45",
            },
        )

    sched = build_scheduler()
    job = sched.get_job("top100_refresh")

    assert job is not None
    fields = {f.name: f for f in job.trigger.fields}
    assert str(fields["hour"]) == "8"
    assert str(fields["minute"]) == "45"
    # Unlike the stock jobs, crypto runs 7 days a week (no mon-fri restriction).
    assert str(fields["day_of_week"]) == "*"


def test_top100_refresh_toggle_removes_and_restores_job(mocker):
    engine = _fresh_engine()
    mocker.patch("app.scheduler.get_engine", return_value=engine)

    sched = build_scheduler()
    assert sched.get_job("top100_refresh") is not None  # on by default

    with Session(engine) as s:
        settings_service.update(s, {"top100_auto_refresh_enabled": "false"})
    reschedule(sched)
    assert sched.get_job("top100_refresh") is None

    with Session(engine) as s:
        settings_service.update(s, {"top100_auto_refresh_enabled": "true"})
    reschedule(sched)
    assert sched.get_job("top100_refresh") is not None


def test_top100_job_swallows_crawl_errors(mocker):
    from app.crawler import coingecko_client
    from app.scheduler import _top100_job

    engine = _fresh_engine()
    mocker.patch("app.scheduler.get_engine", return_value=engine)
    mocker.patch.object(
        coingecko_client, "fetch_markets_page",
        side_effect=coingecko_client.CrawlError("down"),
    )

    _top100_job()  # must not raise -- seed_top100 already logged the error

    with Session(engine) as s:
        entry = s.exec(select(SystemActionLog)).one()
    assert entry.action == "top100_seed"
    assert entry.trigger == "scheduled"
    assert entry.status == "error"


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


def test_run_crypto_batch_includes_top100_symbols(session, mocker):
    # Top100 coins aren't vn30/watchlist -- must still get auto-analyzed so
    # the dashboard's bullish ranking has real data for them.
    session.add(
        Symbol(
            ticker="BITCOIN", display_symbol="BTC", asset_class=AssetClass.CRYPTO,
            is_top100=True, top100_rank=1, is_watchlist=False, is_vn30=False,
        )
    )
    session.commit()

    ingest_spy = mocker.patch.object(ingest, "ingest_crypto", return_value=1)

    ok = run_crypto_batch(session)

    assert ok == 3  # 1h + 4h + daily
    tickers_called = {call.args[1] for call in ingest_spy.call_args_list}
    assert tickers_called == {"BITCOIN"}


def test_run_crypto_batch_skips_ai_narrative_for_top100_only_symbols_by_default(session, mocker):
    # Default per-group AI toggles: vn30=on, watchlist=on, top100=OFF -- a
    # top100-only coin gets the quantitative analysis (feeds the dashboard
    # ranking) but must NOT trigger LLM narrative generation; watchlisted
    # coins keep AI narratives.
    session.add(
        Symbol(
            ticker="BITCOIN", display_symbol="BTC", asset_class=AssetClass.CRYPTO,
            is_top100=True, is_watchlist=False,
        )
    )
    session.add(
        Symbol(ticker="PEPE", display_symbol="PEPE", asset_class=AssetClass.CRYPTO, is_watchlist=True)
    )
    session.commit()

    mocker.patch.object(ingest, "ingest_crypto", return_value=1)
    analysis_spy = mocker.patch("app.scheduler.run_analysis", return_value=None)

    run_crypto_batch(session)

    use_ai_by_ticker = {call.args[1]: call.kwargs["use_ai"] for call in analysis_spy.call_args_list}
    assert use_ai_by_ticker == {"BITCOIN": False, "PEPE": True}


def test_run_crypto_batch_ai_group_toggles_are_configurable(session, mocker):
    # Flipping the per-group Settings toggles inverts the default behavior.
    session.add(
        Symbol(
            ticker="BITCOIN", display_symbol="BTC", asset_class=AssetClass.CRYPTO,
            is_top100=True, is_watchlist=False,
        )
    )
    session.add(
        Symbol(ticker="PEPE", display_symbol="PEPE", asset_class=AssetClass.CRYPTO, is_watchlist=True)
    )
    session.commit()
    settings_service.update(
        session, {"ai_narrative_top100": "true", "ai_narrative_watchlist": "false"}
    )

    mocker.patch.object(ingest, "ingest_crypto", return_value=1)
    analysis_spy = mocker.patch("app.scheduler.run_analysis", return_value=None)

    run_crypto_batch(session)

    use_ai_by_ticker = {call.args[1]: call.kwargs["use_ai"] for call in analysis_spy.call_args_list}
    assert use_ai_by_ticker == {"BITCOIN": True, "PEPE": False}


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


def test_daily_job_logs_a_scheduled_system_action(mocker):
    engine = _fresh_engine()
    mocker.patch("app.scheduler.get_engine", return_value=engine)
    mocker.patch("app.scheduler.run_batch", return_value=3)

    _daily_job()

    with Session(engine) as s:
        entries = s.exec(select(SystemActionLog)).all()
        assert len(entries) == 1
        assert entries[0].action == "daily_close"
        assert entries[0].trigger == "scheduled"
        assert entries[0].status == "success"
        assert entries[0].detail == "3 mã"
        assert entries[0].finished_at is not None


def test_half_session_job_logs_morning_and_afternoon_separately(mocker):
    # Both cron slots share the same function (see _add_jobs()'s
    # functools.partial binding) -- the `action` argument is what lets the
    # log tell a morning run from an afternoon run.
    engine = _fresh_engine()
    mocker.patch("app.scheduler.get_engine", return_value=engine)
    mocker.patch("app.scheduler.run_batch", return_value=0)

    _half_session_job("half_session_morning")
    _half_session_job("half_session_afternoon")

    with Session(engine) as s:
        actions = {e.action for e in s.exec(select(SystemActionLog)).all()}
        assert actions == {"half_session_morning", "half_session_afternoon"}


def test_crypto_batch_job_logs_error_status_on_failure(mocker):
    engine = _fresh_engine()
    mocker.patch("app.scheduler.get_engine", return_value=engine)
    mocker.patch("app.scheduler.run_crypto_batch", side_effect=RuntimeError("boom"))

    try:
        _crypto_batch_job()
    except RuntimeError:
        pass

    with Session(engine) as s:
        entries = s.exec(select(SystemActionLog)).all()
        assert len(entries) == 1
        assert entries[0].action == "crypto_analysis_refresh"
        assert entries[0].status == "error"
        assert entries[0].detail == "boom"


# --- Fixed-delay self-rescheduling: next run is queued from completion, not
# from registration time (see app.scheduler._reschedule_after_run) ---

def test_screener_job_reschedules_from_completion_time(mocker):
    engine = _fresh_engine()
    mocker.patch("app.scheduler.get_engine", return_value=engine)
    with Session(engine) as s:
        settings_service.update(s, {"screener_enabled": "true", "screener_scan_interval": "1h"})
    mocker.patch("app.scheduler.crypto_screener.run_scan_guarded")  # instant no-op
    fake_scheduler = mocker.Mock()
    mocker.patch("app.scheduler._active_scheduler", fake_scheduler)

    _screener_job()

    fake_scheduler.add_job.assert_called_once()
    kwargs = fake_scheduler.add_job.call_args.kwargs
    assert kwargs["id"] == "crypto_screener_scan"
    assert kwargs["replace_existing"] is True
    assert kwargs["run_date"] - datetime.now(_TZ) > timedelta(minutes=55)  # ~1h out, not immediate


def test_screener_job_does_not_reschedule_when_disabled_meanwhile(mocker):
    engine = _fresh_engine()
    mocker.patch("app.scheduler.get_engine", return_value=engine)
    with Session(engine) as s:
        settings_service.update(s, {"screener_enabled": "true", "screener_scan_interval": "1h"})

    def disable_mid_run(*a, **k):
        with Session(engine) as s:
            settings_service.update(s, {"screener_enabled": "false"})

    mocker.patch("app.scheduler.crypto_screener.run_scan_guarded", side_effect=disable_mid_run)
    fake_scheduler = mocker.Mock()
    mocker.patch("app.scheduler._active_scheduler", fake_scheduler)

    _screener_job()

    fake_scheduler.add_job.assert_not_called()


def test_crypto_batch_job_reschedules_from_completion_time(mocker):
    engine = _fresh_engine()
    mocker.patch("app.scheduler.get_engine", return_value=engine)
    with Session(engine) as s:
        settings_service.update(s, {"crypto_analysis_enabled": "true", "crypto_analysis_interval": "4h"})
    mocker.patch("app.scheduler.run_crypto_batch", return_value=0)
    fake_scheduler = mocker.Mock()
    mocker.patch("app.scheduler._active_scheduler", fake_scheduler)

    _crypto_batch_job()

    fake_scheduler.add_job.assert_called_once()
    kwargs = fake_scheduler.add_job.call_args.kwargs
    assert kwargs["id"] == "crypto_analysis_refresh"
    assert kwargs["run_date"] - datetime.now(_TZ) > timedelta(hours=3, minutes=55)


def test_crypto_batch_job_ignores_a_duplicate_trigger_while_already_running(mocker):
    engine = _fresh_engine()
    mocker.patch("app.scheduler.get_engine", return_value=engine)
    mocker.patch("app.scheduler.run_crypto_batch", return_value=0)

    from app.scheduler import _crypto_batch_lock

    _crypto_batch_lock.acquire()  # simulate a run already in progress
    try:
        _crypto_batch_job()  # the duplicate trigger
    finally:
        _crypto_batch_lock.release()

    with Session(engine) as s:
        assert s.exec(select(SystemActionLog)).all() == []  # no log row for the ignored duplicate


# --- reschedule() must not disrupt a self-rescheduling job's pending next-run ---

def test_reschedule_leaves_a_pending_screener_run_untouched(mocker):
    engine = _fresh_engine()
    mocker.patch("app.scheduler.get_engine", return_value=engine)
    with Session(engine) as s:
        settings_service.update(s, {"screener_enabled": "true", "screener_scan_interval": "4h"})

    sched = build_scheduler()
    original_run_date = sched.get_job("crypto_screener_scan").trigger.run_date

    # An unrelated settings save still triggers reschedule() (see
    # _SCHEDULER_KEYS in app/api/settings.py) -- it must not reset the
    # already-pending next-run back to "run immediately".
    reschedule(sched)

    assert sched.get_job("crypto_screener_scan").trigger.run_date == original_run_date

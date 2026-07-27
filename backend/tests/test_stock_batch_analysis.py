import pandas as pd
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import AssetClass, Exchange, Symbol, SystemActionLog, Timeframe
from app.services import analysis as analysis_svc
from app.services import hose_hnx, ingest, stock_batch_analysis

BASE = dict(open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0)
SPRING = dict(open=98.0, high=99.8, low=97.0, close=99.3, volume=1500.0)
CANNED = "NHẬN ĐỊNH:\nx\n\nLỜI KHUYÊN:\n- y"


@pytest.fixture(autouse=True)
def _reset_stock_batch_state():
    # _lock/_state/_cancel_requested are module-level globals, shared across
    # every test in this file -- reset so one test's run never leaks into the
    # next (same reasoning as potential_screener/crypto_screener's own reset
    # fixtures).
    yield
    stock_batch_analysis._cancel_requested.clear()
    stock_batch_analysis._state.update(
        running=False, total=None, completed=None, failed=None, current_ticker=None,
        last_error=None, last_cancelled=False, last_completed_at=None,
    )
    if stock_batch_analysis._lock.locked():
        stock_batch_analysis._lock.release()


def _daily_df():
    t0 = pd.Timestamp("2025-01-01")
    bars = [dict(BASE) for _ in range(25)] + [SPRING]
    return pd.DataFrame([{"time": t0 + pd.Timedelta(days=i), **b} for i, b in enumerate(bars)])


@pytest.fixture
def batch_session(tmp_path):
    """File-backed SQLite (mirrors test_scheduler.py's own fixture): the
    ThreadPoolExecutor inside run_full_universe_analysis needs each worker
    thread to open its own real connection, which a single shared in-memory
    connection can't provide."""
    db_path = tmp_path / "stock_batch_test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 30})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess


def _stub_hose_hnx_universe(mocker, tickers: list[str] = ()):
    mocker.patch.object(
        hose_hnx.vnstock_client, "fetch_hose_hnx_universe",
        return_value=[{"ticker": t, "name": t, "exchange": Exchange.HOSE} for t in tickers],
    )
    mocker.patch.object(
        hose_hnx.vnstock_client, "fetch_liquidity_snapshot", return_value={t: 2_000_000_000.0 for t in tickers}
    )


def test_seeds_hose_hnx_universe_before_analyzing(batch_session, mocker):
    _stub_hose_hnx_universe(mocker, ["SHS"])
    mocker.patch.object(ingest.vnstock_client, "fetch_daily", return_value=_daily_df())
    mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)

    stock_batch_analysis.run_full_universe_analysis(batch_session, trigger="manual")

    symbol = batch_session.get(Symbol, "SHS")
    assert symbol is not None
    assert symbol.is_hose_hnx is True  # seeded, not just manually tracked


def test_analyzes_every_tracked_stock_symbol(batch_session, mocker):
    _stub_hose_hnx_universe(mocker, [])  # nothing new to seed this run
    batch_session.add(Symbol(ticker="FPT", is_vn30=True, asset_class=AssetClass.STOCK))
    batch_session.add(Symbol(ticker="HPG", is_watchlist=True, asset_class=AssetClass.STOCK))
    batch_session.add(Symbol(ticker="XXX", asset_class=AssetClass.STOCK))  # not tracked
    batch_session.commit()
    fetch_daily = mocker.patch.object(ingest.vnstock_client, "fetch_daily", return_value=_daily_df())
    mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)

    status = stock_batch_analysis.run_full_universe_analysis(batch_session, trigger="manual")

    assert status["total"] == 2
    assert status["completed"] == 2
    tickers_called = {call.args[0] for call in fetch_daily.call_args_list}
    assert tickers_called == {"FPT", "HPG"}


def test_never_calls_ai_for_the_narrative(batch_session, mocker):
    # Hundreds of tickers per run -- calling a real AI provider per ticker
    # would be far too slow/costly, so the bulk run always uses the
    # deterministic template (use_ai=False), same reasoning as
    # scenario_backtest.run_backtest.
    _stub_hose_hnx_universe(mocker, [])
    batch_session.add(Symbol(ticker="FPT", is_vn30=True, asset_class=AssetClass.STOCK))
    batch_session.commit()
    mocker.patch.object(ingest.vnstock_client, "fetch_daily", return_value=_daily_df())
    call_claude = mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)

    stock_batch_analysis.run_full_universe_analysis(batch_session, trigger="manual")

    call_claude.assert_not_called()


def test_only_ingests_daily_timeframe(batch_session, mocker):
    _stub_hose_hnx_universe(mocker, [])
    batch_session.add(Symbol(ticker="FPT", is_vn30=True, asset_class=AssetClass.STOCK))
    batch_session.commit()
    mocker.patch.object(ingest.vnstock_client, "fetch_daily", return_value=_daily_df())
    mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)
    half_session_spy = mocker.patch.object(ingest, "ingest_half_session")

    stock_batch_analysis.run_full_universe_analysis(batch_session, trigger="manual")

    half_session_spy.assert_not_called()


def test_isolates_a_ticker_failure_from_the_rest(batch_session, mocker):
    _stub_hose_hnx_universe(mocker, [])
    batch_session.add(Symbol(ticker="FPT", is_vn30=True, asset_class=AssetClass.STOCK))
    batch_session.add(Symbol(ticker="BAD", is_watchlist=True, asset_class=AssetClass.STOCK))
    batch_session.commit()

    def _fetch_daily(ticker, *a, **k):
        if ticker == "BAD":
            raise RuntimeError("crawl failed")
        return _daily_df()

    mocker.patch.object(ingest.vnstock_client, "fetch_daily", side_effect=_fetch_daily)
    mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)

    status = stock_batch_analysis.run_full_universe_analysis(batch_session, trigger="manual")

    assert status["total"] == 2
    assert status["completed"] == 1
    assert status["failed"] == 1


def test_logs_action_on_completion(batch_session, mocker):
    _stub_hose_hnx_universe(mocker, [])
    batch_session.add(Symbol(ticker="FPT", is_vn30=True, asset_class=AssetClass.STOCK))
    batch_session.commit()
    mocker.patch.object(ingest.vnstock_client, "fetch_daily", return_value=_daily_df())
    mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)

    stock_batch_analysis.run_full_universe_analysis(batch_session, trigger="manual")

    log = batch_session.exec(
        select(SystemActionLog).where(SystemActionLog.action == "stock_batch_analysis")
    ).first()
    assert log is not None
    assert log.status == "success"


def test_lock_prevents_overlapping_runs(batch_session):
    stock_batch_analysis._lock.acquire()
    try:
        status = stock_batch_analysis.run_full_universe_analysis(batch_session, trigger="manual")
        assert status["running"] is False  # guarded no-op, never started
    finally:
        stock_batch_analysis._lock.release()


def test_cancel_stops_before_counting_remaining_symbols(batch_session, mocker):
    # Cancel arriving mid-run (not before the run starts -- run_full_universe_
    # analysis clears any stale flag from a PREVIOUS run at the top, same as
    # crypto_screener's own guarded-run pattern) must stop the loop before
    # every symbol is processed.
    _stub_hose_hnx_universe(mocker, [])
    for i in range(5):
        batch_session.add(Symbol(ticker=f"T{i}", is_watchlist=True, asset_class=AssetClass.STOCK))
    batch_session.commit()

    def _fetch_daily_and_cancel(ticker, *a, **k):
        stock_batch_analysis._cancel_requested.set()
        return _daily_df()

    mocker.patch.object(ingest.vnstock_client, "fetch_daily", side_effect=_fetch_daily_and_cancel)
    mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)

    status = stock_batch_analysis.run_full_universe_analysis(batch_session, trigger="manual")

    assert status["last_cancelled"] is True
    assert status["completed"] < status["total"]


def test_request_cancel_is_noop_when_nothing_running():
    status = stock_batch_analysis.request_cancel()
    assert status["running"] is False


def test_get_status_reflects_current_state(batch_session, mocker):
    _stub_hose_hnx_universe(mocker, [])
    batch_session.add(Symbol(ticker="FPT", is_vn30=True, asset_class=AssetClass.STOCK))
    batch_session.commit()
    mocker.patch.object(ingest.vnstock_client, "fetch_daily", return_value=_daily_df())
    mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)

    stock_batch_analysis.run_full_universe_analysis(batch_session, trigger="manual")
    status = stock_batch_analysis.get_status()

    assert status["running"] is False
    assert status["completed"] == 1
    assert status["last_completed_at"] is not None

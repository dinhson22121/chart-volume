import pandas as pd
import pytest
from sqlmodel import select

from app.ai.narrative import PROVIDER_ANTHROPIC, ProviderConfig
from app.models import AssetClass, Candle, PotentialScreenResult, Symbol, SystemActionLog, Timeframe
from app.services import potential_screener


@pytest.fixture(autouse=True)
def _reset_potential_screen_state():
    # _lock/_state are module-level globals, shared across every test in this
    # file (and any other module that imports potential_screener) -- reset so
    # one test's run never leaks into the next.
    yield
    potential_screener._state.update(
        {"running": False, "total": None, "scored": None, "last_completed_at": None, "last_error": None}
    )
    if potential_screener._lock.locked():
        potential_screener._lock.release()


def _seed_candles(session, ticker, n=30, timeframe=Timeframe.DAILY):
    t0 = pd.Timestamp("2025-01-01")
    for i in range(n):
        session.add(
            Candle(
                ticker=ticker, timeframe=timeframe,
                bucket_start=(t0 + pd.Timedelta(days=i)).to_pydatetime(),
                open=100.0 + i, high=101.0 + i, low=99.0 + i, close=100.5 + i, volume=1000.0,
            )
        )
    session.commit()


def test_tracked_symbols_matches_dashboard_universe(session):
    session.add(Symbol(ticker="FPT", is_vn30=True))
    session.add(Symbol(ticker="HPG", is_watchlist=True))
    session.add(Symbol(ticker="BITCOIN", is_top100=True, asset_class=AssetClass.CRYPTO))
    session.add(Symbol(ticker="XXX"))  # untracked
    session.commit()

    tickers = {s.ticker for s in potential_screener._tracked_symbols(session)}

    assert tickers == {"FPT", "HPG", "BITCOIN"}


def test_recent_candles_by_ticker_caps_and_orders_chronologically(session):
    _seed_candles(session, "FPT", n=50)

    by_ticker = potential_screener._recent_candles_by_ticker(session, ["FPT"])

    candles = by_ticker["FPT"]
    assert len(candles) == potential_screener.CANDLES_PER_SYMBOL
    assert candles[0].bucket_start < candles[-1].bucket_start  # chronological
    # the most recent CANDLES_PER_SYMBOL bars -- last one is the latest seeded bar
    assert candles[-1].bucket_start == pd.Timestamp("2025-02-19").to_pydatetime()


def test_recent_candles_by_ticker_avoids_n_plus_1_across_batch(session, mocker):
    _seed_candles(session, "FPT", n=5)
    _seed_candles(session, "HPG", n=5)
    spy = mocker.spy(potential_screener, "select")

    potential_screener._recent_candles_by_ticker(session, ["FPT", "HPG"])

    assert spy.call_count == 1  # one query for the whole batch, not one per ticker


def test_build_batch_prompt_instructs_against_named_strategies_but_carries_no_strategy_output(session):
    # The prompt legitimately NAMES the strategies once, to tell the AI not to
    # use them (e.g. "không Wyckoff, không SMC...") -- that's expected. What
    # must NEVER appear is actual quantitative-engine OUTPUT (phase/confidence/
    # signal-type strings), which would mean strategy results leaked in.
    _seed_candles(session, "FPT", n=5)
    symbol = Symbol(ticker="FPT", display_symbol="FPT", is_vn30=True)
    candles = potential_screener._recent_candles_by_ticker(session, ["FPT"])["FPT"]

    prompt_vi = potential_screener._build_batch_prompt([(symbol, candles)], "vi")
    prompt_en = potential_screener._build_batch_prompt([(symbol, candles)], "en")

    for prompt in (prompt_vi, prompt_en):
        assert "wyckoff" in prompt.lower()  # named once, in the "don't use this" instruction
        for leaked_output in ("confidence", "Accumulation", "Distribution", "BOS_Bull", "CHoCH", "phase"):
            assert leaked_output not in prompt
        # only raw OHLCV made it into the prompt
        assert "O=" in prompt and "H=" in prompt and "C=" in prompt and "V=" in prompt


def test_parse_batch_response_valid_json():
    raw = '[{"ticker": "FPT", "score": 82, "reason": "Xu hướng tăng rõ rệt."}]'

    parsed = potential_screener._parse_batch_response(raw)

    assert parsed == {"FPT": {"score": 82.0, "reason": "Xu hướng tăng rõ rệt."}}


def test_parse_batch_response_strips_markdown_code_fence():
    raw = '```json\n[{"ticker": "hpg", "score": 50, "reason": "Đi ngang."}]\n```'

    parsed = potential_screener._parse_batch_response(raw)

    assert parsed == {"HPG": {"score": 50.0, "reason": "Đi ngang."}}


def test_parse_batch_response_clamps_score_to_0_100():
    raw = '[{"ticker": "FPT", "score": 150, "reason": "x"}, {"ticker": "HPG", "score": -20, "reason": "y"}]'

    parsed = potential_screener._parse_batch_response(raw)

    assert parsed["FPT"]["score"] == 100.0
    assert parsed["HPG"]["score"] == 0.0


def test_parse_batch_response_malformed_returns_empty_dict():
    assert potential_screener._parse_batch_response("not json at all") == {}
    assert potential_screener._parse_batch_response('{"not": "a list"}') == {}


def test_parse_batch_response_skips_bad_entries_keeps_good_ones():
    raw = '[{"ticker": "FPT", "score": 70, "reason": "ok"}, {"ticker": 123, "score": "bad"}]'

    parsed = potential_screener._parse_batch_response(raw)

    assert list(parsed.keys()) == ["FPT"]


def test_upsert_result_overwrites_existing_row(session):
    potential_screener._upsert_result(session, "FPT", 60.0, "first reason")
    session.commit()

    potential_screener._upsert_result(session, "FPT", 90.0, "second reason")
    session.commit()

    rows = session.exec(select(PotentialScreenResult)).all()
    assert len(rows) == 1
    assert rows[0].score == 90.0
    assert rows[0].reason == "second reason"


def test_get_results_sorted_by_score_descending(session):
    session.add(Symbol(ticker="FPT", display_symbol="FPT", is_vn30=True))
    session.add(Symbol(ticker="HPG", display_symbol="HPG", is_vn30=True))
    session.commit()
    potential_screener._upsert_result(session, "FPT", 40.0, "low")
    potential_screener._upsert_result(session, "HPG", 90.0, "high")
    session.commit()

    results = potential_screener.get_results(session)

    assert [r["ticker"] for r in results] == ["HPG", "FPT"]


def test_run_potential_screen_batches_in_groups_of_10(session, mocker):
    for i in range(15):
        ticker = f"SYM{i}"
        session.add(Symbol(ticker=ticker, is_watchlist=True))
        _seed_candles(session, ticker, n=5)

    mocker.patch(
        "app.services.settings_service.get_narrative_config",
        return_value=ProviderConfig(provider=PROVIDER_ANTHROPIC, model="claude-sonnet-4-5", api_key="sk-x"),
    )
    call_spy = mocker.patch(
        "app.ai.narrative.call_provider_raw",
        return_value='[{"ticker": "SYM0", "score": 55, "reason": "ok"}]',
    )

    result = potential_screener.run_potential_screen(session, trigger="manual")

    assert call_spy.call_count == 2  # ceil(15 / BATCH_SIZE=10)
    assert result["running"] is False
    assert result["total"] == 15


def test_run_potential_screen_fails_fast_when_no_provider_configured(session, mocker):
    session.add(Symbol(ticker="FPT", is_vn30=True))
    session.commit()
    mocker.patch(
        "app.services.settings_service.get_narrative_config",
        return_value=ProviderConfig(provider=PROVIDER_ANTHROPIC, model="claude-sonnet-4-5", api_key=""),
    )

    result = potential_screener.run_potential_screen(session, trigger="manual")

    assert result["last_error"] == "no AI provider configured"
    entry = session.exec(select(SystemActionLog)).one()
    assert entry.action == "potential_screen"
    assert entry.status == "error"


def test_run_potential_screen_lock_prevents_overlap(session, mocker):
    session.add(Symbol(ticker="FPT", is_vn30=True))
    session.commit()
    potential_screener._lock.acquire()
    try:
        result = potential_screener.run_potential_screen(session, trigger="manual")
        assert result["running"] is False  # untouched -- guarded no-op, not started
    finally:
        potential_screener._lock.release()


def test_run_potential_screen_one_bad_batch_does_not_abort_others(session, mocker):
    # 12 symbols -> 2 batches (10 + 2): first batch's call fails, second
    # succeeds -- a single bad batch among others must not flag the whole
    # run as an error, since most of it still produced real results.
    for i in range(12):
        ticker = f"SYM{i}"
        session.add(Symbol(ticker=ticker, is_watchlist=True))
        _seed_candles(session, ticker, n=5)
    mocker.patch(
        "app.services.settings_service.get_narrative_config",
        return_value=ProviderConfig(provider=PROVIDER_ANTHROPIC, model="claude-sonnet-4-5", api_key="sk-x"),
    )
    mocker.patch(
        "app.ai.narrative.call_provider_raw",
        side_effect=[RuntimeError("provider down"), '[{"ticker": "SYM10", "score": 60, "reason": "ok"}]'],
    )

    result = potential_screener.run_potential_screen(session, trigger="manual")

    assert result["scored"] == 1
    assert result["last_error"] is None  # per-batch failure isolated, not surfaced as a run failure


def test_run_potential_screen_surfaces_error_when_every_batch_fails(session, mocker):
    # Only one batch (2 symbols), and it fails -- e.g. the configured
    # provider's SDK isn't even installed. Previously this looked identical
    # to "ran fine, 0 candidates scoreable" (last_error stayed None); now the
    # all-failed case must surface something the user can actually see.
    for i in range(2):
        ticker = f"SYM{i}"
        session.add(Symbol(ticker=ticker, is_watchlist=True))
        _seed_candles(session, ticker, n=5)
    mocker.patch(
        "app.services.settings_service.get_narrative_config",
        return_value=ProviderConfig(provider=PROVIDER_ANTHROPIC, model="claude-sonnet-4-5", api_key="sk-x"),
    )
    mocker.patch("app.ai.narrative.call_provider_raw", side_effect=RuntimeError("provider down"))

    result = potential_screener.run_potential_screen(session, trigger="manual")

    assert result["scored"] == 0
    assert result["last_error"] == "provider down"
    entry = session.exec(select(SystemActionLog)).one()
    assert entry.status == "error"

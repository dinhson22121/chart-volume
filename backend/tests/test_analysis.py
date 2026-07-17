import logging
import types

import pandas as pd
from sqlmodel import select

from app.ai.narrative import DISCLAIMER, ProviderConfig
from app.models import Analysis, Candle, SignalOutcome, Timeframe
from app.services import analysis as analysis_svc
from app.services import settings_service
from app.strategies import registry as strategy_registry
from app.wyckoff import AnalysisResult, Levels

BASE = dict(open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0)
SPRING_BAR = dict(open=98.0, high=99.8, low=97.0, close=99.3, volume=1500.0)
SOS_BAR = dict(open=101.2, high=103.0, low=101.0, close=102.8, volume=1800.0)

CANNED = "NHẬN ĐỊNH:\nCổ phiếu đang trong giai đoạn tích lũy.\n\nLỜI KHUYÊN:\n- Theo dõi vùng hỗ trợ."


def _seed_candles(session, bars, timeframe=Timeframe.DAILY, ticker="FPT"):
    t0 = pd.Timestamp("2025-01-01")
    for i, b in enumerate(bars):
        session.add(
            Candle(
                ticker=ticker,
                timeframe=timeframe,
                bucket_start=(t0 + pd.Timedelta(days=i)).to_pydatetime(),
                **b,
            )
        )
    session.commit()


def test_run_analysis_stores_result_with_narrative(session, mocker):
    _seed_candles(session, [dict(BASE) for _ in range(25)] + [SPRING_BAR])
    mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)

    result = analysis_svc.run_analysis(session, "FPT", Timeframe.DAILY)

    assert isinstance(result, Analysis)
    assert result.phase == "Accumulation"
    assert "tích lũy" in result.narrative
    assert DISCLAIMER in result.advice


def test_run_analysis_caches_and_skips_llm(session, mocker):
    _seed_candles(session, [dict(BASE) for _ in range(25)] + [SPRING_BAR])
    spy = mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)

    analysis_svc.run_analysis(session, "FPT", Timeframe.DAILY)
    analysis_svc.run_analysis(session, "FPT", Timeframe.DAILY)  # same as_of -> cached

    # 1 call from the first run: narrative only. The Spring event doesn't
    # spawn a scenario (and thus no explanation call) here -- Volume Profile
    # can't be computed from only 26 bars (needs vp_lookback_bars=50), so
    # volume_confirmed stays None, which the VP entry gate treats as
    # unconfirmed. The second (cached) run makes no call at all.
    assert spy.call_count == 1
    assert len(session.exec(select(Analysis)).all()) == 1


def test_force_reruns_llm(session, mocker):
    _seed_candles(session, [dict(BASE) for _ in range(25)] + [SPRING_BAR])
    spy = mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)

    analysis_svc.run_analysis(session, "FPT", Timeframe.DAILY)
    analysis_svc.run_analysis(session, "FPT", Timeframe.DAILY, force=True)

    # First run: narrative (1). No scenario explanation -- Volume Profile
    # can't be computed from only 26 bars (needs vp_lookback_bars=50), so the
    # Spring event's volume_confirmed stays None and the VP entry gate blocks
    # it. Forced second run: narrative regenerates (+1) -> 2 total.
    assert spy.call_count == 2


def test_use_ai_false_stores_without_narrative(session, mocker):
    _seed_candles(session, [dict(BASE) for _ in range(25)] + [SPRING_BAR])
    spy = mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)

    result = analysis_svc.run_analysis(session, "FPT", Timeframe.DAILY, use_ai=False)

    assert result.narrative is None
    assert spy.call_count == 0


def test_no_candles_returns_none(session):
    assert analysis_svc.run_analysis(session, "NOPE", Timeframe.DAILY) is None


def test_narrative_unavailable_provider_is_logged(session, mocker, caplog):
    # Previously is_available() returning False left zero trace -- narrative
    # just stayed None with no explanation anywhere, including the
    # downloadable backend log. Confirms the new log line fires instead.
    _seed_candles(session, [dict(BASE) for _ in range(25)] + [SPRING_BAR])
    mocker.patch.object(
        settings_service,
        "get_narrative_config",
        return_value=ProviderConfig(provider="anthropic", model="claude-sonnet-4-5", api_key=""),
    )

    with caplog.at_level(logging.INFO, logger="chart_volume.analysis"):
        result = analysis_svc.run_analysis(session, "FPT", Timeframe.DAILY)

    assert result.narrative is None
    assert "not available" in caplog.text
    assert "FPT" in caplog.text


# --- Multi-timeframe context: half_session analysis reads the latest daily phase ---

def test_half_session_uses_daily_trend_context(session, mocker):
    mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)
    _seed_candles(session, [dict(BASE) for _ in range(25)] + [SOS_BAR], timeframe=Timeframe.DAILY)
    analysis_svc.run_analysis(session, "FPT", Timeframe.DAILY)  # -> Markup (bullish)

    _seed_candles(session, [dict(BASE) for _ in range(25)] + [SOS_BAR], timeframe=Timeframe.HALF_SESSION)
    half = analysis_svc.run_analysis(session, "FPT", Timeframe.HALF_SESSION)

    assert half.daily_trend == "bullish"
    assert half.mtf_alignment == "aligned"


def test_half_session_without_daily_analysis_has_no_context(session, mocker):
    mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)
    _seed_candles(session, [dict(BASE) for _ in range(25)] + [SOS_BAR], timeframe=Timeframe.HALF_SESSION)

    half = analysis_svc.run_analysis(session, "FPT", Timeframe.HALF_SESSION)

    assert half.daily_trend is None
    assert half.mtf_alignment is None


def test_daily_analysis_never_gets_mtf_context(session, mocker):
    mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)
    _seed_candles(session, [dict(BASE) for _ in range(25)] + [SOS_BAR], timeframe=Timeframe.DAILY)

    daily = analysis_svc.run_analysis(session, "FPT", Timeframe.DAILY)

    assert daily.daily_trend is None
    assert daily.mtf_alignment is None


# --- Signal outcomes: forward-return bookkeeping runs as a side effect of analysis ---

def test_run_analysis_records_signal_outcomes(session, mocker):
    mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)
    _seed_candles(session, [dict(BASE) for _ in range(25)] + [SPRING_BAR] + [dict(BASE) for _ in range(20)])

    analysis_svc.run_analysis(session, "FPT", Timeframe.DAILY)

    outcomes = session.exec(select(SignalOutcome).where(SignalOutcome.event_type == "Spring")).all()
    assert len(outcomes) == 1
    assert outcomes[0].return_20 is not None  # 20 bars of flat data followed the Spring


def test_signal_outcomes_backfill_as_more_candles_arrive(session, mocker):
    mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)
    _seed_candles(session, [dict(BASE) for _ in range(25)] + [SPRING_BAR])

    analysis_svc.run_analysis(session, "FPT", Timeframe.DAILY)
    outcome = session.exec(select(SignalOutcome).where(SignalOutcome.event_type == "Spring")).first()
    assert outcome.return_5 is None  # Spring is the last bar -> no future bars yet
    assert outcome.return_20 is None

    t0 = pd.Timestamp("2025-01-01")
    for i in range(26, 46):
        session.add(
            Candle(
                ticker="FPT",
                timeframe=Timeframe.DAILY,
                bucket_start=(t0 + pd.Timedelta(days=i)).to_pydatetime(),
                **BASE,
            )
        )
    session.commit()

    analysis_svc.run_analysis(session, "FPT", Timeframe.DAILY)  # as_of moved -> fresh run

    outcome = session.exec(select(SignalOutcome).where(SignalOutcome.event_type == "Spring")).first()
    assert outcome.return_5 is not None
    assert outcome.return_20 is not None


# --- Strategy switching must not mix results across strategies ---

def _fake_analyze(candles, cfg, daily_trend=None, language="vi"):
    return AnalysisResult(
        phase="FakePhase",
        confidence=0.99,
        events=[],
        levels=Levels(support=0.0, resistance=0.0),
        as_of=candles[-1].bucket_start,
    )


def test_switching_strategy_creates_a_separate_analysis_row(session, mocker):
    mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)
    _seed_candles(session, [dict(BASE) for _ in range(25)] + [SPRING_BAR])

    wyckoff_result = analysis_svc.run_analysis(session, "FPT", Timeframe.DAILY)
    assert wyckoff_result.strategy == "wyckoff"
    assert wyckoff_result.phase == "Accumulation"

    fake_strategy = types.SimpleNamespace(
        analyze=_fake_analyze, BULLISH_EVENTS=set(), BEARISH_EVENTS=set(), RANGING_PHASES=set(),
        phase_trend=lambda _phase: "neutral",
    )
    mocker.patch.dict(strategy_registry.REGISTRY, {"fake-strategy": fake_strategy})
    settings_service.update(session, {"strategy": "fake-strategy"})

    fake_result = analysis_svc.run_analysis(session, "FPT", Timeframe.DAILY)
    assert fake_result.strategy == "fake-strategy"
    assert fake_result.phase == "FakePhase"

    # Same ticker/timeframe/as_of, different strategy -> two independent rows,
    # neither overwrote the other.
    rows = session.exec(
        select(Analysis).where(Analysis.ticker == "FPT", Analysis.timeframe == Timeframe.DAILY)
    ).all()
    assert len(rows) == 2
    assert {r.strategy for r in rows} == {"wyckoff", "fake-strategy"}
    wyckoff_row = next(r for r in rows if r.strategy == "wyckoff")
    assert wyckoff_row.phase == "Accumulation"  # untouched by the fake-strategy run


# --- Shadow strategies: background data collection for strategies not currently active ---

def test_run_shadow_strategies_analyses_every_other_strategy(session, mocker):
    mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)
    _seed_candles(session, [dict(BASE) for _ in range(25)] + [SPRING_BAR])

    active = settings_service.get_strategy(session)  # "wyckoff" by default
    analysis_svc.run_analysis(session, "FPT", Timeframe.DAILY, strategy=active)
    analysis_svc.run_shadow_strategies(session, "FPT", Timeframe.DAILY, active)

    rows = session.exec(
        select(Analysis).where(Analysis.ticker == "FPT", Analysis.timeframe == Timeframe.DAILY)
    ).all()
    assert {r.strategy for r in rows} == set(strategy_registry.REGISTRY)
    # Shadow runs never touch the LLM -- only the initial active-strategy run did.
    for row in rows:
        if row.strategy != active:
            assert row.narrative is None


def test_run_shadow_strategies_isolates_a_failing_strategy(session, mocker, caplog):
    mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)
    _seed_candles(session, [dict(BASE) for _ in range(25)] + [SPRING_BAR])

    def _broken_analyze(candles, cfg, daily_trend=None, language="vi"):
        raise RuntimeError("boom")

    broken_strategy = types.SimpleNamespace(
        analyze=_broken_analyze, BULLISH_EVENTS=set(), BEARISH_EVENTS=set(), RANGING_PHASES=set(),
        phase_trend=lambda _phase: "neutral",
    )
    mocker.patch.dict(strategy_registry.REGISTRY, {"broken-strategy": broken_strategy})

    with caplog.at_level(logging.WARNING, logger="chart_volume.analysis"):
        analysis_svc.run_shadow_strategies(session, "FPT", Timeframe.DAILY, "wyckoff")

    assert "broken-strategy" in caplog.text
    # The other (working) shadow strategies still ran despite the failure.
    rows = session.exec(
        select(Analysis).where(Analysis.ticker == "FPT", Analysis.timeframe == Timeframe.DAILY)
    ).all()
    assert {r.strategy for r in rows} == {"sonicr", "smc"}

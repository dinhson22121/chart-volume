import pandas as pd

from app.ai.narrative import DISCLAIMER
from app.models import Analysis, Candle, Timeframe
from app.services import analysis as analysis_svc

BASE = dict(open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0)
SPRING_BAR = dict(open=98.0, high=99.8, low=97.0, close=99.3, volume=1500.0)

CANNED = "NHẬN ĐỊNH:\nCổ phiếu đang trong giai đoạn tích lũy.\n\nLỜI KHUYÊN:\n- Theo dõi vùng hỗ trợ."


def _seed_candles(session, bars):
    t0 = pd.Timestamp("2025-01-01")
    for i, b in enumerate(bars):
        session.add(
            Candle(
                ticker="FPT",
                timeframe=Timeframe.DAILY,
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

    assert spy.call_count == 1
    assert len(session.exec(__import__("sqlmodel").select(Analysis)).all()) == 1


def test_force_reruns_llm(session, mocker):
    _seed_candles(session, [dict(BASE) for _ in range(25)] + [SPRING_BAR])
    spy = mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)

    analysis_svc.run_analysis(session, "FPT", Timeframe.DAILY)
    analysis_svc.run_analysis(session, "FPT", Timeframe.DAILY, force=True)

    assert spy.call_count == 2


def test_use_ai_false_stores_without_narrative(session, mocker):
    _seed_candles(session, [dict(BASE) for _ in range(25)] + [SPRING_BAR])
    spy = mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)

    result = analysis_svc.run_analysis(session, "FPT", Timeframe.DAILY, use_ai=False)

    assert result.narrative is None
    assert spy.call_count == 0


def test_no_candles_returns_none(session):
    assert analysis_svc.run_analysis(session, "NOPE", Timeframe.DAILY) is None

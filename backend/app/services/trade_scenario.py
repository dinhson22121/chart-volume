"""Entry/SL/TP scenario tracking, spawned from bullish/bearish events.

Mirrors app.services.signal_outcomes' pattern (same identity tuple, created
once at detection then updated on later analysis runs) but tracks a trade
plan's lifecycle (active -> hit_tp / hit_sl / expired) instead of a forward-
return stat.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.ai import narrative as narrative_mod
from app.ai.narrative import ProviderConfig
from app.models import Candle, TradeScenario
from app.wyckoff import Levels
from app.wyckoff.events import WyckoffEvent

logger = logging.getLogger("chart_volume.trade_scenario")

# Small cushion below/above the triggering bar's own low/high so ordinary
# intrabar noise doesn't close a scenario the instant it's created.
SL_BUFFER_PCT = 0.003

# Matches the rolling window every strategy already uses for its own
# support/resistance (app.wyckoff.indicators.RANGE_LOOKBACK,
# app.smc/_SWING_LOOKBACK_LEVELS, app.sonicr/_SWING_LOOKBACK -- all 20).
LEVELS_LOOKBACK = 20

# A single flash-crash/spike bar inside the lookback window (a real event on
# volatile, low-liquidity crypto -- e.g. an 80% one-day drop that mostly
# recovers) can dominate max(high)-min(low), producing a "measured move"
# many times the asset's actual current price. Left unbounded this has
# produced take-profits several multiples above entry, and even negative
# take-profits on the bearish side (impossible -- price can't go negative).
# Capping the height as a fraction of entry keeps the projection within a
# plausible range regardless of what a single outlier bar did.
MAX_RANGE_HEIGHT_PCT = 0.5

# Nothing in the codebase tracks "how many bars did the range that produced
# this event take to build", so a data-driven duration formula isn't
# available -- this stays a flat default rather than a fabricated formula.
DEFAULT_MAX_BARS = 10


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _close_reason(
    status: str,
    *,
    price: float | None = None,
    level: float | None = None,
    bar_ts: datetime | None = None,
    max_bars: int | None = None,
    language: str = "vi",
) -> str:
    if status == "hit_sl":
        if language == "en":
            return f"Close {price:.2f} broke past SL {level:.2f} at candle {bar_ts:%Y-%m-%d %H:%M}"
        return f"Giá đóng cửa {price:.2f} vượt qua SL {level:.2f} tại nến {bar_ts:%Y-%m-%d %H:%M}"
    if status == "hit_tp":
        if language == "en":
            return f"Reached TP {level:.2f} at candle {bar_ts:%Y-%m-%d %H:%M}"
        return f"Giá đạt TP {level:.2f} tại nến {bar_ts:%Y-%m-%d %H:%M}"
    if language == "en":
        return f"{max_bars} candles passed without hitting TP/SL, scenario expired"
    return f"Hết {max_bars} nến chưa đạt TP/SL, kịch bản hết hiệu lực"


def _template_explanation(
    event_type: str, is_bullish: bool, entry: float, stop_loss: float, take_profit: float, max_bars: int, language: str
) -> str:
    if language == "en":
        direction = "long" if is_bullish else "short"
        return (
            f"{event_type} signal ({direction}) at {entry:.2f}. SL at {stop_loss:.2f} sits just beyond the "
            f"event's own breakout point; TP at {take_profit:.2f} follows the current range's measured move. "
            f"Up to {max_bars} candles for the scenario to play out."
        )
    direction = "mua" if is_bullish else "bán"
    return (
        f"Tín hiệu {event_type} ({direction}) tại {entry:.2f}. SL tại {stop_loss:.2f} đặt ngay ngoài điểm phá vỡ "
        f"của chính sự kiện; TP tại {take_profit:.2f} theo chiều cao vùng tích luỹ/phân phối hiện tại. "
        f"Tối đa {max_bars} nến để kịch bản đi đúng hướng."
    )


def _generate_explanation(
    event_type: str,
    is_bullish: bool,
    entry: float,
    stop_loss: float,
    take_profit: float,
    max_bars: int,
    provider_cfg: ProviderConfig,
    use_ai: bool,
) -> str:
    template = _template_explanation(
        event_type, is_bullish, entry, stop_loss, take_profit, max_bars, provider_cfg.language
    )
    if not use_ai or not narrative_mod.is_available(provider_cfg):
        return template

    direction = "mua" if is_bullish else "bán"
    if provider_cfg.language == "en":
        prompt = (
            f"A trade scenario was just detected:\n"
            f"- Signal: {event_type} ({'long' if is_bullish else 'short'})\n"
            f"- Entry: {entry:.2f}\n- Stop-loss: {stop_loss:.2f}\n- Take-profit: {take_profit:.2f}\n"
            f"- Duration: up to {max_bars} candles\n\n"
            f"Write 2-3 short, plain-English sentences for a retail trader explaining why these levels make "
            f"sense. Interpret the numbers, don't just restate them. No disclaimers."
        )
    else:
        prompt = (
            f"Một kịch bản giao dịch vừa được phát hiện:\n"
            f"- Tín hiệu: {event_type} ({direction})\n"
            f"- Vào lệnh: {entry:.2f}\n- Cắt lỗ: {stop_loss:.2f}\n- Chốt lời: {take_profit:.2f}\n"
            f"- Thời hạn: tối đa {max_bars} nến\n\n"
            f"Viết 2-3 câu ngắn gọn bằng tiếng Việt cho nhà đầu tư cá nhân, giải thích vì sao các mức này hợp lý. "
            f"Diễn giải ý nghĩa, không lặp lại số liệu y nguyên. Không thêm disclaimer."
        )
    try:
        text = narrative_mod.call_provider_raw(prompt, provider_cfg)
        return text.strip() or template
    except Exception as exc:  # noqa: BLE001 - AI failure must never block scenario creation
        logger.warning("scenario explanation AI call failed, using template: %s", exc)
        return template


def _update_active_scenarios(
    session: Session, ticker: str, timeframe: str, strategy: str, candles: list[Candle], language: str
) -> None:
    active = session.exec(
        select(TradeScenario).where(
            TradeScenario.ticker == ticker,
            TradeScenario.timeframe == timeframe,
            TradeScenario.strategy == strategy,
            TradeScenario.status == "active",
        )
    ).all()
    if not active:
        return

    for scenario in active:
        subsequent = sorted(
            (c for c in candles if c.bucket_start > scenario.event_ts), key=lambda c: c.bucket_start
        )
        for bar in subsequent:
            hit_sl = bar.close <= scenario.stop_loss if scenario.is_bullish else bar.close >= scenario.stop_loss
            if hit_sl:
                scenario.status = "hit_sl"
                scenario.closed_bar_ts = bar.bucket_start
                scenario.close_reason = _close_reason(
                    "hit_sl", price=bar.close, level=scenario.stop_loss, bar_ts=bar.bucket_start, language=language
                )
                break
            hit_tp = bar.high >= scenario.take_profit if scenario.is_bullish else bar.low <= scenario.take_profit
            if hit_tp:
                scenario.status = "hit_tp"
                scenario.closed_bar_ts = bar.bucket_start
                scenario.close_reason = _close_reason(
                    "hit_tp", level=scenario.take_profit, bar_ts=bar.bucket_start, language=language
                )
                break
        else:
            if len(subsequent) >= scenario.max_bars:
                scenario.status = "expired"
                scenario.closed_bar_ts = subsequent[-1].bucket_start if subsequent else None
                scenario.close_reason = _close_reason("expired", max_bars=scenario.max_bars, language=language)

        if scenario.status != "active":
            scenario.closed_at = _utcnow()
            session.add(scenario)


def _pre_event_range_height(candles: list[Candle], event_index: int, levels: Levels) -> float:
    """Support/resistance measured over the LEVELS_LOOKBACK bars strictly
    before the event, not `levels` (computed from the full series, which for
    an event on the latest bar includes that very bar). A breakout event's
    own bar routinely sets a new high/low for the window it's in, so
    including it collapses "resistance" to ~the event's own price -- which
    is exactly the level the event claims to have broken through, making the
    measured-move height degenerate (near zero) instead of a real prior
    range. Falls back to the passed-in `levels` when there isn't enough
    prior history (event too close to the start of the series)."""
    window = candles[max(0, event_index - LEVELS_LOOKBACK) : event_index]
    if not window:
        return levels.resistance - levels.support
    return max(c.high for c in window) - min(c.low for c in window)


def _create_scenarios(
    session: Session,
    ticker: str,
    timeframe: str,
    strategy: str,
    candles: list[Candle],
    events: list[WyckoffEvent],
    bullish_events: set[str],
    bearish_events: set[str],
    levels: Levels,
    provider_cfg: ProviderConfig,
    use_ai: bool,
) -> None:
    qualifying = [e for e in events if e.type in bullish_events or e.type in bearish_events]
    if not qualifying:
        return

    # v1: at most one active scenario per (ticker, timeframe, strategy) -- a
    # new qualifying event is skipped while one is already in flight rather
    # than spawning an overlapping second plan.
    has_active = session.exec(
        select(TradeScenario).where(
            TradeScenario.ticker == ticker,
            TradeScenario.timeframe == timeframe,
            TradeScenario.strategy == strategy,
            TradeScenario.status == "active",
        )
    ).first()
    if has_active is not None:
        return

    # Only the single MOST RECENT qualifying event is ever a candidate for a
    # new scenario. `events` is recomputed from the full candle history on
    # every run, so a naive "first untracked event, in chronological order"
    # loop would -- on the very first run against a ticker with years of
    # history -- latch onto whatever old event happens to be earliest, then
    # crawl through the backlog one ancient event at a time (each one
    # expiring/closing before the next is even considered), never reaching a
    # currently-relevant signal. Jumping straight to the latest event avoids
    # that entirely; if it's already tracked (and closed), nothing new is
    # created until a genuinely new event appears on a later run.
    event = max(qualifying, key=lambda e: e.ts)
    n = len(candles)
    if event.index >= n:
        return  # defensive, mirrors signal_outcomes.record_outcomes

    existing = session.exec(
        select(TradeScenario).where(
            TradeScenario.ticker == ticker,
            TradeScenario.timeframe == timeframe,
            TradeScenario.strategy == strategy,
            TradeScenario.event_type == event.type,
            TradeScenario.event_ts == event.ts,
        )
    ).first()
    if existing is not None:
        return

    entry = event.price
    range_height = min(_pre_event_range_height(candles, event.index, levels), entry * MAX_RANGE_HEIGHT_PCT)
    bar = candles[event.index]
    is_bullish = event.type in bullish_events
    stop_loss = bar.low * (1 - SL_BUFFER_PCT) if is_bullish else bar.high * (1 + SL_BUFFER_PCT)
    take_profit = entry + range_height if is_bullish else entry - range_height
    explanation = _generate_explanation(
        event.type, is_bullish, entry, stop_loss, take_profit, DEFAULT_MAX_BARS, provider_cfg, use_ai
    )

    session.add(
        TradeScenario(
            ticker=ticker,
            timeframe=timeframe,
            strategy=strategy,
            event_type=event.type,
            event_ts=event.ts,
            is_bullish=is_bullish,
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            max_bars=DEFAULT_MAX_BARS,
            explanation=explanation,
        )
    )


def sync_scenarios(
    session: Session,
    ticker: str,
    timeframe: str,
    strategy: str,
    candles: list[Candle],
    events: list[WyckoffEvent],
    bullish_events: set[str],
    bearish_events: set[str],
    levels: Levels,
    provider_cfg: ProviderConfig,
    use_ai: bool = True,
) -> None:
    """Update any already-active scenario against the latest candles first
    (so a scenario closed in this same run doesn't block a new event from
    starting one), then create scenarios for qualifying events not yet
    tracked. ``bullish_events``/``bearish_events`` are the calling strategy's
    own event-type vocabulary (e.g. ``strategy_module.BULLISH_EVENTS``).
    ``provider_cfg`` supplies both the language for close_reason/explanation
    text and (when ``use_ai``) the AI provider for a written explanation."""
    _update_active_scenarios(session, ticker, timeframe, strategy, candles, provider_cfg.language)
    _create_scenarios(
        session, ticker, timeframe, strategy, candles, events, bullish_events, bearish_events, levels,
        provider_cfg, use_ai,
    )
    session.commit()


def get_scenario(session: Session, ticker: str, timeframe: str, strategy: str) -> TradeScenario | None:
    """Active scenario if one exists, else the most recently closed one --
    so the UI still shows why the last scenario ended instead of going blank."""
    active = session.exec(
        select(TradeScenario).where(
            TradeScenario.ticker == ticker,
            TradeScenario.timeframe == timeframe,
            TradeScenario.strategy == strategy,
            TradeScenario.status == "active",
        )
    ).first()
    if active is not None:
        return active
    return session.exec(
        select(TradeScenario)
        .where(
            TradeScenario.ticker == ticker,
            TradeScenario.timeframe == timeframe,
            TradeScenario.strategy == strategy,
        )
        .order_by(TradeScenario.event_ts.desc())
    ).first()

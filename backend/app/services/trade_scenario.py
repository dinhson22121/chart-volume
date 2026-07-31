"""Entry/SL/TP scenario tracking, spawned from bullish/bearish events.

Mirrors app.services.signal_outcomes' pattern (same identity tuple, created
once at detection then updated on later analysis runs) but tracks a trade
plan's lifecycle (active -> hit_tp / hit_sl / expired) instead of a forward-
return stat.
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlmodel import Session, func, select

from app.ai import narrative as narrative_mod
from app.ai.narrative import ProviderConfig
from app.models import AssetClass, Candle, Exchange, Symbol, Timeframe, TradeScenario
from app.services import benchmark as benchmark_svc
from app.services import settings_service
from app.services import stats_significance
from app.wyckoff import Levels
from app.wyckoff.events import SOS, SOW, SPRING, UPTHRUST, WyckoffEvent

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

# Fallback duration when ATR can't be computed (too little pre-event history)
# or comes out zero (dead-flat candles).
DEFAULT_MAX_BARS = 10

ATR_PERIOD = 14
MIN_MAX_BARS = 5
MAX_MAX_BARS = 30

# T+2.5 settlement: shares bought today only land in the account (tradeable
# again) ~2.5 trading days later -- a stock scenario's TP/SL can't realistically
# trigger before that many bars have elapsed, even if price crosses the level
# earlier, because there's nothing to sell yet. Not applicable to crypto (T+0,
# 24/7) or to a timeframe coarser than the lag itself (a weekly bar already
# spans far more than 2.5 days, so no gate is listed for Timeframe.WEEK).
SETTLEMENT_BARS_STOCK: dict[str, int] = {
    Timeframe.DAILY: 3,  # ~T+2.5 trading days, rounded up
    Timeframe.HALF_SESSION: 5,  # 2 sessions/day -> ~2.5 days = 5 half-sessions
}

# Below this many closed trades, expectancy/drawdown stats are too noisy to
# draw conclusions from -- get_scenario_stats flags this via low_sample_size
# rather than silently presenting an early, unstable read as settled.
MIN_RELIABLE_SAMPLE_SIZE = 30

# Exit mechanism: breakeven-at-1R + ATR trailing stop, no fixed take-profit
# ceiling. Backtested against optimize_wyckoff_exit.py's alternative (giving
# the fixed TP/SL model more max_bars instead) on the full HOSE/HNX universe,
# train/holdout split: the fixed-TP model was flat across every max_bars
# multiplier tried (1x-3x -- max_bars was never the actual constraint, the
# formula was already clamped at its floor), while this trail roughly halved
# holdout mean_r (-0.45 -> -0.25) and raised win_rate ~10pp (19.8% -> 31.3%
# at this multiplier, the best of {1.5, 2.0, 3.0} tried). Still net-negative
# on holdout -- an improvement to the exit mechanism, not a solved edge; see
# take_profit's own docstring note on what it means now.
TRAIL_ATR_MULT = 1.5

# NoDemand/NoSupply (app.wyckoff.events) are "supporting" signals that fire
# *inside* an already-established trend (an absorption bar showing a lack of
# selling/buying pressure) -- unlike Spring/SOS/SOW/BC/SC/Upthrust they don't
# mark a breakout out of a trading range, so there's no coherent prior range
# to project a measured move from. Using _pre_event_range_height for them
# anyway produced take-profits up to ~18x the stop distance in production data
# (avg 13.2% reward vs 1.02% risk on scenarios that hit SL) -- distances that
# are effectively unreachable within max_bars and, in a 155-scenario sample,
# accounted for 31 of 34 stop-outs. Rather than patch the TP formula, they're
# excluded from scenario creation entirely (see _create_scenarios) -- they
# also happen to be the two weakest signal types by win rate (signal_outcomes
# stats), consistent with treating them as trend confirmation rather than
# standalone entries. Harmless for other strategies' event vocabularies,
# whose event.type never matches these.
#
# DragonCrossUp/Down and SonicCrossUp/Down (app.sonicr): dedicated
# per-event-type backtests (scripts/backtest_sonicr.py, VN30 then a
# 100-ticker HOSE/HNX sample) showed neither has a coherent, statistically
# significant edge on its own -- bootstrap CI crosses zero both times, with
# no consistent chronological pattern. SonicEntryLong (the fully
# Dragon+CCI+MTF+pullback-confirmed entry -- see
# app.sonicr.phase.TREND_CONTINUATION_EVENTS) came right up to the edge of
# significance (100-ticker holdout CI=[-0.011, 0.162], walk_forward=0.80) and
# is left as SonicR's only real entry point; these two raw/informational
# signal pairs are still recorded via signal_outcomes for reference, same as
# NoDemand/NoSupply, just not turned into trade plans.
CONTINUATION_EVENT_TYPES = {"NoDemand", "NoSupply", "DragonCrossUp", "DragonCrossDown", "SonicCrossUp", "SonicCrossDown"}

# Same set as app.wyckoff.volume_profile._VP_CHECKABLE/phase._VP_CHECKABLE --
# the only 4 event types Volume Profile actually has a clean confirmation
# rule for (a genuine breakout/reclaim past the Value Area). Gating scenario
# creation on it for the other 6 types (NoDemand/NoSupply/climaxes/LPS/LPSY)
# would be pointless: their volume_confirmed is always None (never
# evaluated), so they'd never pass and never create a scenario at all.
_VP_GATED_EVENT_TYPES = {SOS, SOW, SPRING, UPTHRUST}


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
            return f"Price {price:.2f} breached SL {level:.2f} at candle {bar_ts:%Y-%m-%d %H:%M}"
        return f"Giá {price:.2f} chạm SL {level:.2f} tại nến {bar_ts:%Y-%m-%d %H:%M}"
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


@dataclass
class ResolvedOutcome:
    status: str  # "active" | "hit_sl" | "hit_tp" | "expired"
    closed_bar_ts: datetime | None
    exit_price: float | None
    touch_price: float | None = None  # hit_sl only -- the actual low/high that pierced, for the close_reason message


def _resolve_outcome(
    event_ts: datetime,
    entry: float,
    stop_loss: float,
    max_bars: int,
    is_bullish: bool,
    candles: list[Candle],
    min_settlement_bars: int = 0,
) -> ResolvedOutcome:
    """Breakeven-at-1R + ATR trailing stop -- no fixed take-profit ceiling
    (see TRAIL_ATR_MULT). Pure decision over plain values -- no DB, no
    session. Shared by live tracking (_update_active_scenarios, which polls
    this against whatever candles have arrived so far) and
    app.services.scenario_backtest (which has the complete future candle
    series up front and resolves a scenario the instant it's created,
    instead of waiting for later runs) -- so a backtest can never see a
    resolution live tracking couldn't have.

    Once the running favorable excursion reaches 1R (entry-to-stop
    distance), the stop moves to at least breakeven, then trails
    TRAIL_ATR_MULT ATRs behind the best price seen so far -- so a status of
    ``hit_tp`` now means "stopped out at breakeven or better" (a win or
    scratch, exit_price >= entry) and ``hit_sl`` means "stopped out below
    entry" (a real loss), regardless of which stop (original, breakeven, or
    trailed) actually triggered it. ATR is computed once from the pre-event
    window (same window _build_scenario_candidate already used for max_bars)
    -- not recomputed bar-by-bar as new candles arrive, to keep a live
    scenario's trail comparable to how it was backtested.

    ``min_settlement_bars`` (see SETTLEMENT_BARS_STOCK) skips stop checks on
    the first N subsequent bars: for a VN stock, shares bought at entry aren't
    tradeable again until T+2.5 settles, so a price touch on those bars isn't
    a fill a real order could have taken -- 0 for crypto (T+0) and any
    non-stock caller, via the default."""
    pre_event = [c for c in candles if c.bucket_start <= event_ts]
    atr = _atr(pre_event) or 0.0
    risk_distance = abs(entry - stop_loss)
    current_stop = stop_loss
    best = entry
    subsequent = sorted((c for c in candles if c.bucket_start > event_ts), key=lambda c: c.bucket_start)
    for idx, bar in enumerate(subsequent, start=1):
        if idx <= min_settlement_bars:
            continue  # T+2.5: shares not settled yet, can't exit regardless of price
        # Checked intrabar (low/high), matching how a real stop order
        # executes on touch rather than waiting for the candle to close past
        # the level.
        hit_stop = bar.low <= current_stop if is_bullish else bar.high >= current_stop
        if hit_stop:
            status = "hit_tp" if (current_stop >= entry if is_bullish else current_stop <= entry) else "hit_sl"
            touch_price = bar.low if is_bullish else bar.high
            return ResolvedOutcome(status, bar.bucket_start, current_stop, touch_price)

        best = max(best, bar.high) if is_bullish else min(best, bar.low)
        unrealized = (best - entry) if is_bullish else (entry - best)
        if unrealized >= risk_distance and atr > 0:
            trail_level = (best - TRAIL_ATR_MULT * atr) if is_bullish else (best + TRAIL_ATR_MULT * atr)
            current_stop = (
                max(current_stop, entry, trail_level) if is_bullish else min(current_stop, entry, trail_level)
            )

        if idx >= max_bars:
            return ResolvedOutcome("expired", bar.bucket_start, bar.close)
    return ResolvedOutcome("active", None, None)


def _settlement_bars_for(symbol: Symbol | None, timeframe: str) -> int:
    if symbol is None or symbol.asset_class != AssetClass.STOCK:
        return 0
    return SETTLEMENT_BARS_STOCK.get(timeframe, 0)


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

    symbol = session.get(Symbol, ticker)
    min_settlement_bars = _settlement_bars_for(symbol, timeframe)

    for scenario in active:
        outcome = _resolve_outcome(
            scenario.event_ts, scenario.entry, scenario.stop_loss, scenario.max_bars,
            scenario.is_bullish, candles, min_settlement_bars,
        )
        if outcome.status == "active":
            continue

        scenario.status = outcome.status
        scenario.closed_bar_ts = outcome.closed_bar_ts
        scenario.exit_price = outcome.exit_price
        if outcome.status == "hit_sl":
            scenario.close_reason = _close_reason(
                "hit_sl", price=outcome.touch_price, level=outcome.exit_price,
                bar_ts=outcome.closed_bar_ts, language=language,
            )
        elif outcome.status == "hit_tp":
            scenario.close_reason = _close_reason(
                "hit_tp", level=outcome.exit_price, bar_ts=outcome.closed_bar_ts, language=language,
            )
        else:  # expired
            scenario.close_reason = _close_reason("expired", max_bars=scenario.max_bars, language=language)
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


def _atr(candles: list[Candle], period: int = ATR_PERIOD) -> float | None:
    """Average True Range over the `period` bars ending at the last candle in
    `candles` (callers pass the pre-event window). None if there isn't enough
    history to compute one full period."""
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(len(candles) - period, len(candles)):
        prev_close = candles[i - 1].close
        tr = max(
            candles[i].high - candles[i].low,
            abs(candles[i].high - prev_close),
            abs(candles[i].low - prev_close),
        )
        trs.append(tr)
    return sum(trs) / len(trs)


def _compute_max_bars(candles_before_event: list[Candle], tp_distance: float) -> int:
    """How many bars the scenario gets before it's declared expired, scaled to
    how volatile the asset actually is: a TP that's a small multiple of ATR
    should resolve quickly, a distant TP against a calm ATR needs more bars.
    Falls back to DEFAULT_MAX_BARS when ATR isn't available."""
    atr = _atr(candles_before_event)
    if not atr or atr <= 0:
        return DEFAULT_MAX_BARS
    bars = round(tp_distance / atr)
    return max(MIN_MAX_BARS, min(MAX_MAX_BARS, bars))


def _min_bars_to_reach(move_pct: float, daily_limit_pct: float) -> float:
    """Fewest daily sessions needed to cover ``move_pct`` if price moved the
    maximum allowed ``daily_limit_pct`` every single day (HOSE's daily band
    compounds, e.g. 7%/day for a few days can cover a move a single day's
    band alone couldn't) -- ``math.log`` bases, not a flat
    ``move_pct / daily_limit_pct`` division, which would understate how many
    sessions a large move actually needs once compounding is accounted for."""
    if move_pct <= 0:
        return 0.0
    if daily_limit_pct <= 0:
        return float("inf")
    return math.log(1 + move_pct) / math.log(1 + daily_limit_pct)


def _count_active(session: Session, strategy: str | None = None, asset_class: str | None = None) -> int:
    query = select(TradeScenario.id).where(TradeScenario.status == "active")
    if strategy:
        query = query.where(TradeScenario.strategy == strategy)
    if asset_class:
        query = query.join(Symbol, Symbol.ticker == TradeScenario.ticker).where(Symbol.asset_class == asset_class)
    return len(session.exec(query).all())


def _build_scenario_candidate(
    ticker: str,
    timeframe: str,
    strategy: str,
    candles: list[Candle],
    event: WyckoffEvent,
    is_bullish: bool,
    levels: Levels,
    provider_cfg: ProviderConfig,
    strategy_module,
    strategy_cfg,
    daily_trend: str | None,
    ranging_phases: set[str],
    symbol: Symbol | None,
    risk_cfg: dict,
    use_ai: bool,
    config_version: str,
) -> TradeScenario | None:
    """Applies the entry gates (multi-timeframe alignment, phase-before-event,
    Volume Profile confirmation) and computes entry/SL/TP/max_bars/
    price_limit_caution for ONE candidate event -- returns an unsaved
    TradeScenario, or None if any gate blocks it. Shared by live
    _create_scenarios (which additionally restricts candidates to the single
    most recent event, plus DB/portfolio-level checks that only make sense
    for real-time tracking) and app.services.scenario_backtest (which walks
    every qualifying historical event in isolation, with no portfolio-level
    view across other tickers to check against)."""
    n = len(candles)
    if event.index >= n or event.index + 1 >= n:
        return None

    # Hard gate on multi-timeframe alignment: on an intraday timeframe with a
    # known daily trend (see app.services.analysis._get_daily_trend),
    # mtf_alignment used to be informational only -- a bullish signal against
    # a bearish daily trend still spawned a trade plan. Block it instead;
    # daily_trend is None on the daily timeframe itself or before any daily
    # analysis exists, so this never gates those cases.
    if daily_trend is not None and is_bullish != (daily_trend == "bullish"):
        return None

    # Gate on the phase as of just before the event, not the phase this same
    # analysis run just classified -- a breakout event (SOS/BOS/CHoCH-style)
    # inherently coincides with the phase flipping to Markup/Markdown/trending,
    # so checking the post-event phase would almost always pass trivially.
    # Re-running analyze() on the truncated pre-event window answers "was this
    # actually a breakout out of a real range, or did it fire once already
    # trending" -- the latter has no coherent range height to measure a move
    # against.
    #
    # Exempt for TREND_CONTINUATION_EVENTS (e.g. app.sonicr's
    # SonicEntryLong/Short): those are pullback-continuation entries that only
    # fire once a trend is ALREADY established, so the pre-event phase is
    # always Uptrend/Downtrend, never Ranging -- this gate would otherwise
    # reject 100% of them (getattr, not a hard attribute: every other
    # strategy simply has none, same defensive pattern as SMCEvent.mitigated).
    if event.type not in getattr(strategy_module, "TREND_CONTINUATION_EVENTS", frozenset()):
        truncated = candles[: event.index]
        phase_before_event = strategy_module.analyze(
            truncated, strategy_cfg, daily_trend, provider_cfg.language
        ).phase
        if phase_before_event not in ranging_phases:
            return None

    # Gate on Volume Profile confirmation for the 4 event types it can
    # actually evaluate (see _VP_GATED_EVENT_TYPES). volume_confirmed is
    # False (evaluated, didn't hold) or None (not enough history for a
    # profile yet) -- both are treated as "not confirmed" here: an
    # unevaluated event is a missing condition, not a free pass. Other event
    # types skip this gate entirely (see _VP_GATED_EVENT_TYPES for why).
    if event.type in _VP_GATED_EVENT_TYPES and not event.volume_confirmed:
        return None

    # Not event.price (the event bar's own close): that price is already
    # history by the time the signal can be confirmed. entry_bar.open is the
    # earliest realistic fill -- guaranteed to exist by the index+1 check
    # above.
    entry = candles[event.index + 1].open
    range_height = min(_pre_event_range_height(candles, event.index, levels), entry * MAX_RANGE_HEIGHT_PCT)
    bar = candles[event.index]
    stop_loss = bar.low * (1 - SL_BUFFER_PCT) if is_bullish else bar.high * (1 + SL_BUFFER_PCT)
    take_profit = entry + range_height if is_bullish else entry - range_height
    max_bars = _compute_max_bars(candles[: event.index], abs(take_profit - entry))
    explanation = _generate_explanation(
        event.type, is_bullish, entry, stop_loss, take_profit, max_bars, provider_cfg, use_ai
    )

    # Price-limit caution (stock only): flag, don't block, when the exchange's
    # compounding daily band (see settings_service.stock_daily_price_limit_pct
    # / _hnx) can't plausibly cover the SL or TP distance within max_bars
    # sessions -- a level that's arithmetically out of reach in the time the
    # scenario gives itself, not just "far away". See _min_bars_to_reach.
    # HNX's daily band (+/-10%) is wider than HOSE's (+/-7%); a stock with no
    # recorded exchange yet (predates this field) falls back to HOSE's
    # tighter band rather than silently assuming the wider one.
    price_limit_caution = False
    if symbol is not None and symbol.asset_class == AssetClass.STOCK:
        limit_key = (
            "stock_daily_price_limit_pct_hnx" if symbol.exchange == Exchange.HNX
            else "stock_daily_price_limit_pct"
        )
        limit_pct = risk_cfg[limit_key] / 100
        sl_move = abs(entry - stop_loss) / entry
        tp_move = abs(take_profit - entry) / entry
        price_limit_caution = (
            _min_bars_to_reach(sl_move, limit_pct) > max_bars or _min_bars_to_reach(tp_move, limit_pct) > max_bars
        )

    return TradeScenario(
        ticker=ticker,
        timeframe=timeframe,
        strategy=strategy,
        event_type=event.type,
        event_ts=event.ts,
        is_bullish=is_bullish,
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        max_bars=max_bars,
        explanation=explanation,
        config_version=config_version,
        price_limit_caution=price_limit_caution,
    )


def _create_scenarios(
    session: Session,
    ticker: str,
    timeframe: str,
    strategy: str,
    candles: list[Candle],
    events: list[WyckoffEvent],
    bullish_events: set[str],
    levels: Levels,
    provider_cfg: ProviderConfig,
    strategy_module,
    strategy_cfg,
    daily_trend: str | None,
    ranging_phases: set[str],
    use_ai: bool,
    config_version: str = "",
) -> None:
    # Spot-only: the app never models short-selling/margin, on either stock
    # (retail can't short VN equities) or crypto (spot buy/sell, no futures).
    # A bearish event is only ever a directional stat for signal_outcomes or a
    # cue to exit an existing long -- it never spawns its own trade plan.
    # NoDemand/NoSupply (see CONTINUATION_EVENT_TYPES) are excluded for a
    # separate reason: they're confirmation signals inside an already-
    # established trend, not entry points, even though they're bullish/bearish
    # in signal_outcomes' vocabulary. Both are recorded by signal_outcomes for
    # stats; this only affects trade-plan creation.
    #
    # getattr(e, "mitigated", False), not e.mitigated -- only app.smc's Order
    # Block events have this attribute (a since-mitigated OB's zone has
    # already been invalidated by price closing back through it, so entering
    # off it now would be trading a premise that no longer holds); every
    # other strategy's events default to "never mitigated" via the getattr
    # fallback, same defensive-attribute pattern _VP_GATED_EVENT_TYPES uses
    # for volume_confirmed.
    qualifying = [
        e for e in events
        if e.type in bullish_events
        and e.type not in CONTINUATION_EVENT_TYPES
        and not getattr(e, "mitigated", False)
    ]
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
    # A live trader can only confirm this signal once the event bar has
    # closed -- by then, that bar's own close is already history. The
    # earliest realistic fill is the NEXT bar's open (see `entry` below), so
    # if that bar doesn't exist yet (the event just fired on the newest
    # candle in `candles`), there's nothing to enter at. Return without
    # creating anything; nothing is persisted for this event, so a later run
    # -- once a new candle has arrived -- picks it up fresh via the same
    # "most recent qualifying event" selection above.
    if event.index + 1 >= n:
        return
    is_bullish = event.type in bullish_events

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

    # Portfolio-level risk caps: v1's has_active check above only prevents a
    # SECOND scenario on the same (ticker, timeframe, strategy) -- it says
    # nothing about how many are open across the whole tracked universe at
    # once, for THIS strategy. Scoped per-strategy, not pooled across every
    # strategy: shadow_strategy_keys runs several strategies in parallel
    # purely to accumulate signal_outcomes/trade_scenario data, and they never
    # compete for the same real capital -- pooling them meant whichever
    # strategy's active count reached the cap first (in practice, whichever
    # had run the longest) silently starved every OTHER strategy from ever
    # creating a scenario again, regardless of how few (even zero) of its own
    # were open. Small-cap crypto in particular tends to move as one
    # correlated cluster (risk-on/risk-off together), so a tighter sub-cap
    # applies to it specifically; asset_class is a simple proxy for
    # "correlated cluster" rather than a real correlation matrix, which is
    # overkill at this scale. Live-tracking only -- see
    # _build_scenario_candidate's docstring on why scenario_backtest can't
    # apply this (no cross-ticker portfolio view).
    risk_cfg = settings_service.get_risk_config(session)
    if _count_active(session, strategy=strategy) >= risk_cfg["max_concurrent_scenarios"]:
        return
    symbol = session.get(Symbol, ticker)
    if (
        symbol is not None
        and symbol.asset_class == AssetClass.CRYPTO
        and _count_active(session, strategy=strategy, asset_class=AssetClass.CRYPTO)
        >= risk_cfg["max_concurrent_scenarios_crypto"]
    ):
        return

    candidate = _build_scenario_candidate(
        ticker, timeframe, strategy, candles, event, is_bullish, levels, provider_cfg,
        strategy_module, strategy_cfg, daily_trend, ranging_phases, symbol, risk_cfg, use_ai, config_version,
    )
    if candidate is not None:
        session.add(candidate)


def sync_scenarios(
    session: Session,
    ticker: str,
    timeframe: str,
    strategy: str,
    candles: list[Candle],
    events: list[WyckoffEvent],
    bullish_events: set[str],
    levels: Levels,
    provider_cfg: ProviderConfig,
    strategy_module,
    strategy_cfg,
    daily_trend: str | None,
    ranging_phases: set[str],
    use_ai: bool = True,
    config_version: str = "",
) -> None:
    """Update any already-active scenario against the latest candles first
    (so a scenario closed in this same run doesn't block a new event from
    starting one), then create scenarios for qualifying events not yet
    tracked. ``bullish_events`` is the calling strategy's own event-type
    vocabulary (e.g. ``strategy_module.BULLISH_EVENTS``) -- there's no
    ``bearish_events`` counterpart because the app is spot-only (see
    ``_create_scenarios``): a bearish event never spawns its own trade plan.
    ``strategy_module``/``strategy_cfg``/``daily_trend``/``ranging_phases``
    let a new scenario be gated on the phase just before the triggering event
    (see the comment in ``_create_scenarios``). ``provider_cfg`` supplies both
    the language for close_reason/explanation text and (when ``use_ai``) the
    AI provider for a written explanation. ``config_version`` is stamped only
    on a newly-created scenario (see app.services.config_version)."""
    _update_active_scenarios(session, ticker, timeframe, strategy, candles, provider_cfg.language)
    _create_scenarios(
        session, ticker, timeframe, strategy, candles, events, bullish_events, levels,
        provider_cfg, strategy_module, strategy_cfg, daily_trend, ranging_phases, use_ai, config_version,
    )
    session.commit()


def _filtered_scenarios_query(
    ticker: str | None,
    status: str | None,
    strategy: str | None,
    asset_class: str | None = None,
    source: str | None = "live",
    is_bullish: bool | None = None,
):
    # source defaults to "live" so every pre-existing caller (Trade History
    # page, Trust Layer stats) keeps seeing only real-time-tracked scenarios
    # unless it explicitly asks otherwise -- backtest.run_backtest's rows
    # must never silently leak into numbers a user reads as "what actually
    # happened". Pass source=None for no filter (both live and backtest
    # pooled) -- callers should only do that for an explicit "all sources"
    # view, never as a stats default.
    #
    # is_bullish has no filter by default (None = both directions pooled).
    # source="live" rows are bullish-only by construction now (see
    # _create_scenarios -- the app is spot-only for both stock and crypto, no
    # short-selling), but source="backtest" rows still cover both directions
    # (scenario_backtest.walk_events resolves the bearish side too, purely for
    # one-off research scripts under scripts/), and a pre-existing live row
    # created before this restriction may still be bearish. Pass
    # is_bullish=True to scope to only what was ever tradeable.
    query = select(TradeScenario)
    if ticker:
        query = query.where(TradeScenario.ticker == ticker.upper())
    if status:
        query = query.where(TradeScenario.status == status)
    if strategy:
        query = query.where(TradeScenario.strategy == strategy)
    if asset_class:
        query = query.join(Symbol, Symbol.ticker == TradeScenario.ticker).where(Symbol.asset_class == asset_class)
    if source is not None:
        query = query.where(TradeScenario.source == source)
    if is_bullish is not None:
        query = query.where(TradeScenario.is_bullish == is_bullish)
    return query


def list_scenarios(
    session: Session,
    page: int,
    page_size: int,
    ticker: str | None = None,
    status: str | None = None,
    strategy: str | None = None,
    asset_class: str | None = None,
    source: str | None = "live",
    is_bullish: bool | None = None,
) -> tuple[list[TradeScenario], int]:
    """Every scenario ever created, across all tickers -- for the Trade
    History page (as opposed to ``get_scenario``, which only ever returns one
    row for a single ticker/timeframe/strategy)."""
    query = _filtered_scenarios_query(ticker, status, strategy, asset_class, source, is_bullish)
    total = session.exec(select(func.count()).select_from(query.subquery())).one()
    items = session.exec(
        query.order_by(TradeScenario.event_ts.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return items, total


def get_scenario_stats(
    session: Session,
    ticker: str | None = None,
    strategy: str | None = None,
    asset_class: str | None = None,
    current_config_version: str | None = None,
    source: str | None = "live",
    is_bullish: bool | None = None,
) -> dict:
    """``source`` defaults to "live" -- see _filtered_scenarios_query -- so the
    Trust Layer numbers (monte_carlo/bootstrap/walk_forward included) never
    silently pool in scenario_backtest's rows unless explicitly asked to.

    ``is_bullish`` has no filter by default -- pass True to scope every
    number (including monte_carlo/bootstrap/walk_forward) to only long
    scenarios, the relevant view for a spot-only trader who can never
    actually execute a bearish/short scenario.

    ``win_count``/``loss_count``/``win_rate``/``avg_pnl_pct`` keep their
    original, narrower meaning: only scenarios that clearly hit TP or SL.

    ``expectancy_r``/``total_pnl_amount`` cover a wider sample (TP/SL/expired
    -- every status that now carries an ``exit_price``, see
    _update_active_scenarios) expressed as an R-multiple: return relative to
    the scenario's own risk distance (entry-to-stop), the standard way to
    compare trades with different stop distances. A per-ticker cost haircut
    (slippage for both asset classes, plus broker fee + VN sell tax for
    stocks, CEX taker fee for crypto -- see settings_service.get_risk_config)
    worsens exit_price in the unfavorable direction first, so the number
    reflects a realistic fill rather than the exact trigger level -- without
    this, expectancy would silently assume free trading, which for VN stocks
    alone overstates every trade by ~0.2-0.3%. The $ amount applies
    fixed-fractional position sizing (risk_pct_per_trade of notional_capital
    per trade) -- both purely for display, computed at read time so tuning
    either assumption never needs a migration.

    ``median_expectancy_r`` is the median of the same per-trade R-multiples
    ``expectancy_r`` averages -- a single outlier trade (a too-tight stop
    blowing up an ordinary $ move into a huge R, see _create_scenarios'
    NoDemand/NoSupply comment) can drag the mean well above what most trades
    actually looked like; the median isn't moved by one extreme value the way
    a mean is. ``low_sample_size`` flags when there are fewer than
    MIN_RELIABLE_SAMPLE_SIZE R-multiples behind these stats -- both are
    ``None`` when there's no data at all.

    ``current_config_count`` (only computed when ``current_config_version``
    is given -- see app.services.config_version) is how many of the filtered
    scenarios were created under today's exact thresholds, as opposed to
    thresholds since retuned; left ``None`` when the caller can't name a
    single strategy's current version (e.g. no strategy filter, several
    strategies' scenarios pooled together).

    ``benchmark_buy_hold_pct``/``strategy_return_pct``/``edge_vs_buy_hold_pct``
    (see app.services.benchmark) compare the strategy's own realized return
    against simply buying and holding each ticker over that SAME trade's own
    window, sized in the same dollars actually put at risk -- the real
    opportunity cost, as opposed to the random-entry baseline
    signal_outcomes.get_stats compares win rate against.

    ``max_drawdown_r``/``max_drawdown_amount``/``max_consecutive_losses``
    describe the worst realized stretch, not just the average -- an
    expectancy that's positive on average can still ruin an account during a
    losing streak an average alone never shows.

    ``monte_carlo`` (see stats_significance.monte_carlo_permutation_test)
    reshuffles the SAME chronological R-multiples to check whether the
    realized equity path is unusually smooth/rough compared to a random
    reordering of the same wins/losses -- catches real loss-clustering risk
    that expectancy_r's average never shows, since shuffling can't change
    the mean itself. ``None`` below its 3-trade minimum.

    ``bootstrap`` (see stats_significance.bootstrap_sharpe_ci) resamples the
    same per-trade returns WITH replacement to put a confidence interval on
    r_sharpe -- unlike monte_carlo (which reorders this exact fixed trade
    set), this treats each return as a draw from an unknown distribution and
    asks how much the estimate would move on a fresh sample. ``None`` below
    5 trades.

    ``walk_forward`` (see stats_significance.walk_forward_analysis) splits
    the same R-multiples into sequential windows to check whether the edge
    held up across time or was carried by one lucky stretch. ``None`` below
    2 trades per window (5 windows by default -- needs 10+ trades)."""
    total_count = session.exec(
        select(func.count()).select_from(
            _filtered_scenarios_query(ticker, None, strategy, asset_class, source, is_bullish).subquery()
        )
    ).one()

    current_config_count = None
    if current_config_version is not None:
        current_config_count = session.exec(
            select(func.count()).select_from(
                _filtered_scenarios_query(ticker, None, strategy, asset_class, source, is_bullish)
                .where(TradeScenario.config_version == current_config_version)
                .subquery()
            )
        ).one()

    decided = session.exec(
        _filtered_scenarios_query(ticker, None, strategy, asset_class, source, is_bullish).where(
            TradeScenario.status.in_(["hit_tp", "hit_sl"])
        )
    ).all()
    wins = [s for s in decided if s.status == "hit_tp"]
    losses = [s for s in decided if s.status == "hit_sl"]

    def _pnl_pct(s: TradeScenario) -> float:
        exit_price = s.take_profit if s.status == "hit_tp" else s.stop_loss
        raw = (exit_price - s.entry) / s.entry
        return raw if s.is_bullish else -raw

    pnls = [_pnl_pct(s) for s in decided]

    closed = session.exec(
        _filtered_scenarios_query(ticker, None, strategy, asset_class, source, is_bullish).where(
            TradeScenario.status.in_(["hit_tp", "hit_sl", "expired"]),
            TradeScenario.exit_price.is_not(None),
        )
    ).all()
    risk_cfg = settings_service.get_risk_config(session)
    asset_classes = {
        row[0]: row[1] for row in session.exec(select(Symbol.ticker, Symbol.asset_class)).all()
    } if closed else {}

    def _cost_pct(tkr: str) -> float:
        if asset_classes.get(tkr) == AssetClass.CRYPTO:
            # Alongside slippage, a round-trip CEX taker fee is real drag a
            # pure slippage estimate ignores, same as broker_fee_pct_stock is
            # for stocks below.
            return (risk_cfg["slippage_pct_crypto"] + risk_cfg["trading_fee_pct_crypto"]) / 100
        # Stocks additionally carry a round-trip broker commission and VN's
        # flat sell-side tax -- both real drag a pure slippage estimate
        # ignores, and both apply whether the trade won or lost.
        return (
            risk_cfg["slippage_pct_stock"] + risk_cfg["broker_fee_pct_stock"] + risk_cfg["sell_tax_pct_stock"]
        ) / 100

    def _r_multiple(s: TradeScenario) -> float | None:
        risk_distance = abs(s.entry - s.stop_loss)
        if not risk_distance or s.exit_price is None:
            return None
        cost_amount = _cost_pct(s.ticker) * s.entry
        adjusted_exit = s.exit_price - cost_amount if s.is_bullish else s.exit_price + cost_amount
        raw = (adjusted_exit - s.entry) / risk_distance
        return raw if s.is_bullish else -raw

    scored = [(s, r) for s in closed if (r := _r_multiple(s)) is not None]
    r_multiples = [r for _, r in scored]
    risk_amount = risk_cfg["notional_capital"] * risk_cfg["risk_pct_per_trade"] / 100
    total_pnl_amount = risk_amount * sum(r_multiples) if r_multiples else None

    # Max drawdown + longest losing streak, walked in the order things
    # actually resolved on the market timeline (closed_bar_ts -- the historical
    # candle where TP/SL/expiry happened -- not closed_at, which is just
    # whenever this app's process happened to run and notice it).
    ordered = sorted(scored, key=lambda sr: sr[0].closed_bar_ts or sr[0].closed_at or sr[0].event_ts)
    cum_r = 0.0
    peak_r = 0.0
    max_drawdown_r = 0.0
    consecutive_losses = 0
    max_consecutive_losses = 0
    for s, r in ordered:
        cum_r += r
        peak_r = max(peak_r, cum_r)
        max_drawdown_r = max(max_drawdown_r, peak_r - cum_r)
        consecutive_losses = consecutive_losses + 1 if r < 0 else 0
        max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)

    # Monte Carlo permutation test (see stats_significance.monte_carlo_
    # permutation_test): reshuffles this SAME chronological R-multiple
    # sequence to check whether the realized equity path is unusually
    # smooth/rough compared to a random reordering of the same wins/losses --
    # catches real loss-clustering risk that expectancy_r's average can't.
    monte_carlo = stats_significance.monte_carlo_permutation_test(
        [r for _, r in ordered], risk_amount, risk_cfg["notional_capital"]
    )
    # Bootstrap Sharpe CI (see stats_significance.bootstrap_sharpe_ci):
    # resamples the SAME per-trade returns with replacement to estimate how
    # much r_sharpe would wobble on a fresh sample -- the closest thing to
    # "does the edge itself look real" this app computes without a full
    # backtest.
    bootstrap = stats_significance.bootstrap_sharpe_ci(
        [r for _, r in ordered], risk_amount, risk_cfg["notional_capital"]
    )
    # Walk-forward consistency (see stats_significance.walk_forward_analysis):
    # splits the same chronological R-multiples into sequential windows to
    # check whether the edge held up across time, or was carried by one
    # lucky stretch.
    walk_forward = stats_significance.walk_forward_analysis([r for _, r in ordered])

    # Buy-and-hold benchmark: for each closed scenario, what simply holding
    # the ticker (no signals, no risk management) over that SAME trade's own
    # entry-to-exit window would have returned, sized in the SAME dollars
    # actually put at risk on that trade (risk_amount) -- on the daily
    # timeframe regardless of which timeframe the scenario itself was
    # generated on, since "did nothing in particular" is a date-range
    # concept, not a signal-timeframe one. Pooling every scenario on a ticker
    # into one min/max window (the previous approach) and averaging bare %
    # returns compared a risk-managed, fractionally-sized return against a
    # full-notional one over a mismatched window -- not a real opportunity
    # cost. edge_vs_buy_hold_pct is derived from strategy/benchmark totals
    # over this SAME matched set (only scenarios with a computable buy-hold
    # return), not the full-population strategy_return_pct exposed below --
    # keeping both sides of the subtraction over an identical trade
    # population and dollar size is what makes "edge" mean anything.
    matched_strategy_amount = 0.0
    matched_buy_hold_amount = 0.0
    matched_count = 0
    for s, r in scored:
        end_ts = s.closed_bar_ts or s.closed_at or s.event_ts
        buy_hold_pct = benchmark_svc.buy_hold_return(session, s.ticker, Timeframe.DAILY, s.event_ts, end_ts)
        if buy_hold_pct is None:
            continue
        matched_strategy_amount += risk_amount * r
        matched_buy_hold_amount += risk_amount * buy_hold_pct
        matched_count += 1
    benchmark_buy_hold_pct = (
        round(matched_buy_hold_amount / risk_cfg["notional_capital"], 4) if matched_count else None
    )
    strategy_return_pct = (
        round(total_pnl_amount / risk_cfg["notional_capital"], 4) if total_pnl_amount is not None else None
    )
    edge_vs_buy_hold_pct = (
        round((matched_strategy_amount - matched_buy_hold_amount) / risk_cfg["notional_capital"], 4)
        if matched_count else None
    )

    return {
        "total_count": total_count,
        "current_config_count": current_config_count,
        "decided_count": len(decided),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(len(wins) / len(decided), 3) if decided else None,
        "avg_pnl_pct": round(sum(pnls) / len(pnls), 4) if pnls else None,
        "pnl_sample_count": len(r_multiples),
        "expectancy_r": round(sum(r_multiples) / len(r_multiples), 3) if r_multiples else None,
        # A single too-tight-stop outlier can blow one trade's R-multiple up
        # far beyond the rest (see MIN_RELIABLE_SAMPLE_SIZE's neighbor
        # _create_scenarios' NoDemand/NoSupply comment) and drag the mean
        # with it -- the median isn't moved by one extreme value the way a
        # mean is, so it's exposed alongside expectancy_r rather than in
        # place of it.
        "median_expectancy_r": round(statistics.median(r_multiples), 3) if r_multiples else None,
        "low_sample_size": (len(r_multiples) < MIN_RELIABLE_SAMPLE_SIZE) if r_multiples else None,
        "risk_amount_per_trade": round(risk_amount, 2),
        "total_pnl_amount": round(total_pnl_amount, 2) if total_pnl_amount is not None else None,
        "benchmark_buy_hold_pct": benchmark_buy_hold_pct,
        "strategy_return_pct": strategy_return_pct,
        "edge_vs_buy_hold_pct": edge_vs_buy_hold_pct,
        "max_drawdown_r": round(max_drawdown_r, 3) if ordered else None,
        "max_drawdown_amount": round(risk_amount * max_drawdown_r, 2) if ordered else None,
        "max_consecutive_losses": max_consecutive_losses if ordered else None,
        "monte_carlo": monte_carlo,
        "bootstrap": bootstrap,
        "walk_forward": walk_forward,
    }


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

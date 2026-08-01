"""Historical backtest engine: full-history replay for a fixed
(ticker, timeframe, strategy) using the exact same detection/gating/outcome
logic as live tracking (see trade_scenario._build_scenario_candidate and
._resolve_outcome), producing TradeScenario rows tagged source="backtest".

Live tracking only accumulates one data point per real-world candle close, so
building a sample large enough for the Trust Layer stats (Monte Carlo,
Bootstrap, Walk-Forward -- see trade_scenario.get_scenario_stats) to mean
anything can take weeks or months. This engine walks candle history already
sitting in the DB and resolves each qualifying event's outcome immediately
against its (already-known) future candles, instead of waiting for it to
happen in real time.

Backtested rows are NEVER pooled with source="live" in stats -- see
TradeScenario.source's docstring for why mixing them would contaminate the
Trust Layer's out-of-sample validity with in-sample/hindsight-tuned detector
thresholds.

Unlike live sync (incremental, driven by whatever's already in the DB), a
backtest run is a full deterministic replay over fixed historical data --
re-running it after a threshold/config change should recompute from scratch,
not layer on top of stale results. So run_backtest first deletes any existing
source="backtest" rows for this exact (ticker, timeframe, strategy) scope,
then rebuilds them.
"""

from __future__ import annotations

import logging

from sqlmodel import Session, select

from app.ai.narrative import PROVIDER_ANTHROPIC, ProviderConfig
from app.models import Candle, Symbol, TradeScenario
from app.services import settings_service
from app.services.trade_scenario import (
    CONTINUATION_EVENT_TYPES,
    _build_scenario_candidate,
    _close_reason_for,
    _resolve_outcome,
    _settlement_bars_for,
    _utcnow,
)
from app.wyckoff import Levels
from app.wyckoff.events import WyckoffEvent

logger = logging.getLogger("chart_volume.scenario_backtest")


def run_backtest(
    session: Session,
    ticker: str,
    timeframe: str,
    strategy: str,
    candles: list[Candle],
    events: list[WyckoffEvent],
    bullish_events: set[str],
    bearish_events: set[str],
    levels: Levels,
    strategy_module,
    strategy_cfg,
    daily_trend: str | None,
    ranging_phases: set[str],
    language: str = "vi",
    config_version: str = "",
) -> int:
    """Walks every qualifying historical event in chronological order (unlike
    live's _create_scenarios, which only ever considers the single most
    recent one) and resolves each one's outcome immediately. Only one
    candidate is ever "in flight" at a time per (ticker, timeframe, strategy),
    mirroring live's one-active-scenario policy -- the next candidate is only
    considered once the previous one's resolution timestamp has passed, and
    if the latest candidate never resolves (still "active" at the end of the
    known candle history), no further candidates are considered after it,
    exactly like a real unresolved scenario would block new ones live.

    Never spends AI cost on the explanation text (use_ai=False is passed to
    _build_scenario_candidate unconditionally) -- a single call can replay
    years of history, and nobody reads a narrative for a decade-old
    historical event.

    Portfolio-level risk caps (max_concurrent_scenarios, the crypto sub-cap)
    are intentionally NOT applied here -- see _build_scenario_candidate's
    docstring: those only make sense against a live, whole-universe view
    across every other ticker being tracked at once, which a single-ticker
    replay has no way to reconstruct.

    Returns the number of TradeScenario rows created."""
    stale = session.exec(
        select(TradeScenario).where(
            TradeScenario.ticker == ticker,
            TradeScenario.timeframe == timeframe,
            TradeScenario.strategy == strategy,
            TradeScenario.source == "backtest",
        )
    ).all()
    for row in stale:
        session.delete(row)

    symbol = session.get(Symbol, ticker)
    risk_cfg = settings_service.get_risk_config(session)
    scenarios = walk_events(
        ticker, timeframe, strategy, candles, events, bullish_events, bearish_events, levels,
        strategy_module, strategy_cfg, daily_trend, ranging_phases, symbol, risk_cfg, language, config_version,
    )
    for scenario in scenarios:
        scenario.source = "backtest"
        session.add(scenario)

    session.commit()
    logger.info("backtest %s/%s/%s: created %d scenario(s)", ticker, timeframe, strategy, len(scenarios))
    return len(scenarios)


def walk_events(
    ticker: str,
    timeframe: str,
    strategy: str,
    candles: list[Candle],
    events: list[WyckoffEvent],
    bullish_events: set[str],
    bearish_events: set[str],
    levels: Levels,
    strategy_module,
    strategy_cfg,
    daily_trend: str | None,
    ranging_phases: set[str],
    symbol: Symbol | None,
    risk_cfg: dict,
    language: str = "vi",
    config_version: str = "",
) -> list[TradeScenario]:
    """Pure history walk -- no DB reads or writes, no session. Returns unsaved
    TradeScenario objects (source left at the model default "live"; callers
    that persist them set it explicitly, e.g. run_backtest sets "backtest").

    Extracted so run_backtest (which persists these to the DB) and a
    parameter-sweep/optimizer script (which never persists -- just wants
    R-multiples in memory across many candidate WyckoffConfig/threshold
    combinations) replay history through IDENTICAL detection/gating/outcome
    logic. If the two ever drifted, the optimizer's conclusions wouldn't
    transfer to what an actual backtest or live run would do -- see this
    module's own docstring on why that consistency matters."""
    qualifying = sorted(
        (
            e for e in events
            if (e.type in bullish_events or e.type in bearish_events)
            and e.type not in CONTINUATION_EVENT_TYPES
            and not getattr(e, "mitigated", False)
        ),
        key=lambda e: e.ts,
    )

    provider_cfg = ProviderConfig(provider=PROVIDER_ANTHROPIC, model="", api_key="", language=language)

    created: list[TradeScenario] = []
    blocked_until = None  # None => nothing in flight yet
    still_open = False  # True once a candidate is created but never resolves
    for event in qualifying:
        if still_open:
            break
        if blocked_until is not None and event.ts <= blocked_until:
            continue

        is_bullish = event.type in bullish_events
        candidate = _build_scenario_candidate(
            ticker, timeframe, strategy, candles, event, is_bullish, levels, provider_cfg,
            strategy_module, strategy_cfg, daily_trend, ranging_phases, symbol, risk_cfg,
            use_ai=False, config_version=config_version,
        )
        if candidate is None:
            continue

        outcome = _resolve_outcome(
            candidate.event_ts, candidate.entry, candidate.stop_loss, candidate.max_bars,
            candidate.is_bullish, candles, _settlement_bars_for(symbol, timeframe),
            take_profit=candidate.take_profit,
        )
        candidate.partial_exit_price = outcome.partial_exit_price
        candidate.partial_exit_bar_ts = outcome.partial_exit_bar_ts
        if outcome.status == "active":
            still_open = True
        else:
            candidate.status = outcome.status
            candidate.closed_bar_ts = outcome.closed_bar_ts
            candidate.exit_price = outcome.exit_price
            candidate.close_reason = _close_reason_for(outcome, candidate.max_bars, language)
            candidate.closed_at = _utcnow()
            blocked_until = outcome.closed_bar_ts

        created.append(candidate)

    return created

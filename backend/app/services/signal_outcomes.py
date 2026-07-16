"""Signal-quality stats: forward return of price N bars after each detected event.

Answers "is this signal type actually reliable" without simulating real
entries/exits — for each event we just look N bars ahead in the already-stored
candle series and record the return. Horizons with no future bar yet stay
null and get filled in on a later run once more candles have been ingested.
"""

from __future__ import annotations

from collections import defaultdict

from sqlmodel import Session, select

from app.models import Candle, SignalOutcome
from app.wyckoff.events import WyckoffEvent

HORIZONS = (5, 10, 20)

# A "win" requires the forward move to clear this magnitude in the signal's
# expected direction, not merely close on the right side of zero. A >0%
# definition scores a +0.01% drift as a win, so noise/fees alone push the
# rate to ~50%; requiring a real move (1%) makes the win rate mean "the
# signal actually paid off". Applied at read time in get_stats, so it also
# re-scores every already-stored return without a migration.
WIN_THRESHOLD = 0.01


def _is_win(ret: float, is_bullish: bool) -> bool:
    return ret > WIN_THRESHOLD if is_bullish else ret < -WIN_THRESHOLD


def record_outcomes(
    session: Session,
    ticker: str,
    timeframe: str,
    strategy: str,
    candles: list[Candle],
    events: list[WyckoffEvent],
    bullish_events: set[str],
    phase_trend: str | None = None,
) -> None:
    """``bullish_events`` is the calling strategy's own set of bullish event
    type strings (e.g. ``strategy_module.BULLISH_EVENTS``) -- each strategy
    owns its own event-type vocabulary, so polarity can't be derived from a
    single shared set once more than one strategy exists.

    ``phase_trend`` is the trend the engine classified for this analysis
    (``strategy_module.phase_trend(result.phase)``): an event is ``aligned``
    when its own polarity matches it, letting stats separate signals the
    engine endorsed from counter-trend ones it discounted."""
    if not events:
        return
    closes = [c.close for c in candles]
    n = len(closes)

    for event in events:
        idx = event.index
        if idx >= n:
            continue  # defensive: index should always be within the analysed series

        existing = session.exec(
            select(SignalOutcome).where(
                SignalOutcome.ticker == ticker,
                SignalOutcome.timeframe == timeframe,
                SignalOutcome.strategy == strategy,
                SignalOutcome.event_type == event.type,
                SignalOutcome.event_ts == event.ts,
            )
        ).first()

        entry_price = closes[idx]
        is_bullish = event.type in bullish_events
        aligned = (
            None
            if phase_trend is None
            else phase_trend == ("bullish" if is_bullish else "bearish")
        )
        row = existing or SignalOutcome(
            ticker=ticker,
            timeframe=timeframe,
            strategy=strategy,
            event_type=event.type,
            event_ts=event.ts,
            event_price=entry_price,
            is_bullish=is_bullish,
            aligned=aligned,
        )
        changed = existing is None
        # Backfill alignment on a pre-existing row that predates this column.
        if existing is not None and existing.aligned is None and aligned is not None:
            existing.aligned = aligned
            changed = True

        for horizon in HORIZONS:
            if getattr(row, f"return_{horizon}") is not None:
                continue  # already computed; outcomes are immutable once set
            future_idx = idx + horizon
            if future_idx >= n:
                continue  # not enough future bars yet, try again on a later run

            ret = (closes[future_idx] - entry_price) / entry_price if entry_price else 0.0
            setattr(row, f"return_{horizon}", ret)
            setattr(row, f"is_win_{horizon}", _is_win(ret, is_bullish))
            changed = True

        if changed:
            session.add(row)

    session.commit()


def get_stats(
    session: Session,
    ticker: str | None = None,
    timeframe: str | None = None,
    strategy: str | None = None,
    aligned_only: bool = False,
) -> list[dict]:
    """Win rate is derived from the stored ``return_N`` at read time (via
    WIN_THRESHOLD), not from the stored ``is_win_N`` flags -- so tightening
    the threshold re-scores all history without a migration. ``aligned_only``
    restricts to signals whose polarity matched the engine's classified
    trend, excluding counter-trend signals the engine already discounted."""
    query = select(SignalOutcome)
    if ticker:
        query = query.where(SignalOutcome.ticker == ticker.upper())
    if timeframe:
        query = query.where(SignalOutcome.timeframe == timeframe)
    if strategy:
        query = query.where(SignalOutcome.strategy == strategy)
    if aligned_only:
        query = query.where(SignalOutcome.aligned == True)  # noqa: E712
    rows = session.exec(query).all()

    by_type: dict[str, list[SignalOutcome]] = defaultdict(list)
    for row in rows:
        by_type[row.event_type].append(row)

    stats: list[dict] = []
    for event_type, group in by_type.items():
        is_bullish = group[0].is_bullish
        entry: dict = {
            "type": event_type,
            "count": len(group),
            "is_bullish": is_bullish,
        }
        for horizon in HORIZONS:
            returns = [r for g in group if (r := getattr(g, f"return_{horizon}")) is not None]
            entry[f"n_{horizon}"] = len(returns)
            entry[f"avg_return_{horizon}"] = round(sum(returns) / len(returns), 4) if returns else None
            entry[f"win_rate_{horizon}"] = (
                round(sum(_is_win(r, is_bullish) for r in returns) / len(returns), 3) if returns else None
            )
        stats.append(entry)

    stats.sort(key=lambda s: s["count"], reverse=True)
    return stats

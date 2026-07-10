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


def record_outcomes(
    session: Session,
    ticker: str,
    timeframe: str,
    strategy: str,
    candles: list[Candle],
    events: list[WyckoffEvent],
    bullish_events: set[str],
) -> None:
    """``bullish_events`` is the calling strategy's own set of bullish event
    type strings (e.g. ``strategy_module.BULLISH_EVENTS``) -- each strategy
    owns its own event-type vocabulary, so polarity can't be derived from a
    single shared set once more than one strategy exists."""
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
        row = existing or SignalOutcome(
            ticker=ticker,
            timeframe=timeframe,
            strategy=strategy,
            event_type=event.type,
            event_ts=event.ts,
            event_price=entry_price,
            is_bullish=is_bullish,
        )
        changed = existing is None

        for horizon in HORIZONS:
            if getattr(row, f"return_{horizon}") is not None:
                continue  # already computed; outcomes are immutable once set
            future_idx = idx + horizon
            if future_idx >= n:
                continue  # not enough future bars yet, try again on a later run

            ret = (closes[future_idx] - entry_price) / entry_price if entry_price else 0.0
            win = ret > 0 if is_bullish else ret < 0
            setattr(row, f"return_{horizon}", ret)
            setattr(row, f"is_win_{horizon}", win)
            changed = True

        if changed:
            session.add(row)

    session.commit()


def get_stats(
    session: Session,
    ticker: str | None = None,
    timeframe: str | None = None,
    strategy: str | None = None,
) -> list[dict]:
    query = select(SignalOutcome)
    if ticker:
        query = query.where(SignalOutcome.ticker == ticker.upper())
    if timeframe:
        query = query.where(SignalOutcome.timeframe == timeframe)
    if strategy:
        query = query.where(SignalOutcome.strategy == strategy)
    rows = session.exec(query).all()

    by_type: dict[str, list[SignalOutcome]] = defaultdict(list)
    for row in rows:
        by_type[row.event_type].append(row)

    stats: list[dict] = []
    for event_type, group in by_type.items():
        entry: dict = {
            "type": event_type,
            "count": len(group),
            "is_bullish": group[0].is_bullish,
        }
        for horizon in HORIZONS:
            returns = [r for g in group if (r := getattr(g, f"return_{horizon}")) is not None]
            wins = [w for g in group if (w := getattr(g, f"is_win_{horizon}")) is not None]
            entry[f"n_{horizon}"] = len(returns)
            entry[f"avg_return_{horizon}"] = round(sum(returns) / len(returns), 4) if returns else None
            entry[f"win_rate_{horizon}"] = round(sum(wins) / len(wins), 3) if wins else None
        stats.append(entry)

    stats.sort(key=lambda s: s["count"], reverse=True)
    return stats

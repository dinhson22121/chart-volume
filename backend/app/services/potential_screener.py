"""AI-only "growth potential" screener -- deliberately bypasses every
quantitative strategy (Wyckoff/SMC/SonicR). Feeds raw crawled OHLCV candles
straight to whichever AI provider is configured (Anthropic/Codex/Ollama/
Antigravity) in batches of BATCH_SIZE tickers per call, and asks the AI to
score/explain growth potential purely from its own reading of the price and
volume data -- no phase, confidence, or signal from any strategy engine is
ever included in the prompt.

A full run across every tracked symbol means one real AI call per batch
(latency of several seconds to tens of seconds each), so this follows the
same background-task + lock + polled-status shape as crypto_screener.py
rather than running inline in the request.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.ai import narrative as narrative_mod
from app.models import Candle, PotentialScreenResult, Symbol, Timeframe
from app.services import activity_log, settings_service

logger = logging.getLogger("chart_volume.potential_screener")

BATCH_SIZE = 10
CANDLES_PER_SYMBOL = 30  # recent daily bars sent to the AI per ticker

_lock = threading.Lock()
_state: dict = {
    "running": False,
    "total": None,
    "scored": None,
    "last_completed_at": None,
    "last_error": None,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_status() -> dict:
    return dict(_state)


def _tracked_symbols(session: Session) -> list[Symbol]:
    """Same universe as the Dashboard -- VN30 + watchlist + Top100 crypto,
    both asset classes (not split like scheduler._tracked_symbols)."""
    return session.exec(
        select(Symbol).where(
            (Symbol.is_vn30 == True) | (Symbol.is_watchlist == True) | (Symbol.is_top100 == True)  # noqa: E712
        )
    ).all()


def _recent_candles_by_ticker(session: Session, tickers: list[str]) -> dict[str, list[Candle]]:
    """One batched query for the whole batch's candles (avoids N+1), grouped
    per ticker and capped to the most recent CANDLES_PER_SYMBOL bars, in
    chronological order."""
    rows = session.exec(
        select(Candle)
        .where(Candle.ticker.in_(tickers), Candle.timeframe == Timeframe.DAILY)
        .order_by(Candle.ticker, Candle.bucket_start.desc())
    ).all()
    by_ticker: dict[str, list[Candle]] = {}
    for row in rows:
        bucket = by_ticker.setdefault(row.ticker, [])
        if len(bucket) < CANDLES_PER_SYMBOL:
            bucket.append(row)
    for bucket in by_ticker.values():
        bucket.reverse()  # was newest-first; AI reads it chronologically
    return by_ticker


def _candle_lines(candles: list[Candle]) -> str:
    return "\n".join(
        f"{c.bucket_start:%Y-%m-%d} O={c.open:.4g} H={c.high:.4g} L={c.low:.4g} C={c.close:.4g} V={int(c.volume)}"
        for c in candles
    )


def _build_batch_prompt(entries: list[tuple[Symbol, list[Candle]]], language: str) -> str:
    symbol_blocks = "\n\n".join(
        f"### {symbol.ticker} ({symbol.display_symbol or symbol.ticker})\n{_candle_lines(candles)}"
        for symbol, candles in entries
    )
    if language == "en":
        return (
            "You are an experienced discretionary trader. For EACH of the tickers below, judge its "
            "growth potential using ONLY the raw price/volume data shown -- do NOT apply or name any "
            "specific technical method (no Wyckoff, no SMC, no Sonic R, no RSI/MACD/etc.), just read the "
            "raw data yourself and form your own independent judgment.\n\n"
            f"{symbol_blocks}\n\n"
            "Reply with ONLY a valid JSON array, no other text, no markdown code fence:\n"
            '[{"ticker": "<exact ticker as given above>", "score": <0-100 integer, growth potential>, '
            '"reason": "<2-3 sentence explanation in English>"}]'
        )
    return (
        "Bạn là một nhà giao dịch giàu kinh nghiệm. Với MỖI mã dưới đây, hãy tự đánh giá tiềm năng tăng giá "
        "CHỈ dựa vào dữ liệu giá/khối lượng thô bên dưới -- KHÔNG áp dụng hay nhắc tên bất kỳ phương pháp/chỉ báo "
        "kỹ thuật cụ thể nào (không Wyckoff, không SMC, không Sonic R, không RSI/MACD...), tự đọc dữ liệu thô và "
        "đưa ra nhận định độc lập của riêng bạn.\n\n"
        f"{symbol_blocks}\n\n"
        "Trả lời DUY NHẤT bằng một mảng JSON hợp lệ, không kèm text nào khác, không bọc markdown code fence:\n"
        '[{"ticker": "<đúng mã như trên>", "score": <số nguyên 0-100, tiềm năng tăng giá>, '
        '"reason": "<lý do 2-3 câu bằng tiếng Việt>"}]'
    )


def _strip_code_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]  # drop opening fence (with or without a "json" tag)
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _parse_batch_response(raw: str) -> dict[str, dict]:
    """Returns {ticker.upper(): {"score": float, "reason": str}}. Tolerates a
    malformed entry by skipping just that one; a totally unparsable response
    yields an empty dict (the batch is simply skipped, not fatal)."""
    try:
        data = json.loads(_strip_code_fence(raw))
    except (json.JSONDecodeError, ValueError):
        logger.warning("potential screener: could not parse AI response as JSON")
        return {}
    if not isinstance(data, list):
        return {}

    out: dict[str, dict] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        ticker = entry.get("ticker")
        score = entry.get("score")
        reason = entry.get("reason")
        if not isinstance(ticker, str) or not isinstance(reason, str):
            continue
        try:
            score = max(0.0, min(100.0, float(score)))
        except (TypeError, ValueError):
            continue
        out[ticker.strip().upper()] = {"score": score, "reason": reason.strip()}
    return out


def _upsert_result(session: Session, ticker: str, score: float, reason: str) -> None:
    row = session.get(PotentialScreenResult, ticker) or PotentialScreenResult(ticker=ticker)
    row.score = score
    row.reason = reason
    row.updated_at = _utcnow()
    session.add(row)


def get_results(session: Session) -> list[dict]:
    rows = session.exec(select(PotentialScreenResult)).all()
    symbols = {s.ticker: s for s in session.exec(select(Symbol)).all()}
    out = []
    for row in rows:
        symbol = symbols.get(row.ticker)
        if symbol is None:
            continue
        out.append({
            "ticker": row.ticker,
            "display_symbol": symbol.display_symbol or symbol.ticker,
            "name": symbol.name,
            "asset_class": symbol.asset_class,
            "score": row.score,
            "reason": row.reason,
            "updated_at": row.updated_at,
        })
    out.sort(key=lambda r: r["score"], reverse=True)
    return out


def run_potential_screen(session: Session, trigger: str = "manual") -> dict:
    if not _lock.acquire(blocking=False):
        logger.info("potential screen already running, ignoring duplicate trigger")
        return get_status()

    log_id = activity_log.log_action_start(session, "potential_screen", trigger)
    scored = 0
    total = 0
    try:
        cfg = settings_service.get_narrative_config(session)
        if not narrative_mod.is_available(cfg):
            _state["last_error"] = "no AI provider configured"
            activity_log.log_action_finish(session, log_id, "error", _state["last_error"])
            return get_status()

        symbols = _tracked_symbols(session)
        total = len(symbols)
        _state.update(running=True, total=total, scored=0, last_error=None)

        for i in range(0, len(symbols), BATCH_SIZE):
            batch = symbols[i : i + BATCH_SIZE]
            candles_by_ticker = _recent_candles_by_ticker(session, [s.ticker for s in batch])
            entries = [(s, candles_by_ticker[s.ticker]) for s in batch if candles_by_ticker.get(s.ticker)]
            if not entries:
                continue
            prompt = _build_batch_prompt(entries, cfg.language)
            try:
                raw = narrative_mod.call_provider_raw(prompt, cfg)
            except Exception as exc:  # noqa: BLE001 - one bad batch must not abort the run
                logger.warning("potential screener batch failed: %s", exc)
                continue
            parsed = _parse_batch_response(raw)
            for symbol, _candles in entries:
                result = parsed.get(symbol.ticker.upper())
                if result:
                    _upsert_result(session, symbol.ticker, result["score"], result["reason"])
                    scored += 1
            session.commit()
            _state["scored"] = scored

        activity_log.log_action_finish(session, log_id, "success", f"{scored}/{total} mã")
    except Exception as exc:  # noqa: BLE001 - never let this crash the caller
        logger.warning("potential screen failed: %s", exc)
        _state["last_error"] = str(exc)
        activity_log.log_action_finish(session, log_id, "error", str(exc))
    finally:
        _state["running"] = False
        _state["last_completed_at"] = _utcnow().isoformat()
        _lock.release()
    return get_status()

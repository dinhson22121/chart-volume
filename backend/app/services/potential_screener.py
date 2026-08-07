"""AI "growth potential" screener -- grounded in the SAME Wyckoff phase/
event/money-flow output the rest of the app trusts, not the AI's own
independent reading of raw candles. Feeds each ticker's real analyze()
result (phase, confidence, driving events) and money-flow read
(app.services.money_flow) to whichever AI provider is configured
(Anthropic/Codex/Ollama/Antigravity) in batches of BATCH_SIZE tickers per
call, and asks it to score/explain growth potential by CITING that evidence
-- not to invent a phase or signal of its own. A short recent-candle tail is
still included for color/reference, but it is no longer the AI's only input.

Previously this deliberately bypassed every quantitative strategy and told
the AI not to use any named method -- changed after the user found that
output unconvincing ("giống như 1 bài tập... không trust được"): real,
verified signals now do the grounding instead.

A full run across every tracked symbol means one real AI call per batch
(latency of several seconds to tens of seconds each), so this follows the
same background-task + lock + polled-status shape as crypto_screener.py
rather than running inline in the request.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlmodel import Session, select

import app.wyckoff as wyckoff_module
from app.ai import narrative as narrative_mod
from app.models import Candle, PotentialScreenResult, Symbol, Timeframe
from app.services import activity_log, money_flow, settings_service
from app.wyckoff import AnalysisResult

logger = logging.getLogger("chart_volume.potential_screener")

BATCH_SIZE = 10
# Recent daily bars sent per ticker -- large enough for a real analyze() read
# (Volume Profile needs 50 bars, the trend-efficiency gate needs ~21 bars of
# warm-up) plus a meaningful recent-events window, not just enough for a raw
# candle dump.
CANDLES_PER_SYMBOL = 90
# How many of the most recent raw candles are still shown to the AI for
# color/reference alongside the real evidence -- short on purpose, since the
# evidence block (not raw candles) is meant to do the grounding now.
RECENT_CANDLES_SHOWN = 10

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


@dataclass
class SymbolEvidence:
    """One ticker's real, verified technical evidence -- what the AI is
    grounded in, instead of guessing from raw candles alone."""

    symbol: Symbol
    candles: list[Candle]
    wyckoff: AnalysisResult
    flow: money_flow.MoneyFlowResult


def build_symbol_evidence(symbol: Symbol, candles: list[Candle]) -> SymbolEvidence:
    return SymbolEvidence(
        symbol=symbol, candles=candles,
        wyckoff=wyckoff_module.analyze(candles), flow=money_flow.analyze_money_flow(candles),
    )


def _wyckoff_evidence_text(result: AnalysisResult, language: str) -> str:
    en = language == "en"
    if result.phase == "Insufficient data":
        return "Wyckoff: not enough history yet" if en else "Wyckoff: chưa đủ dữ liệu lịch sử"

    lines = [
        (f"Wyckoff phase: {result.phase} (confidence {result.confidence:.2f})" if en
         else f"Giai đoạn Wyckoff: {result.phase} (độ tin cậy {result.confidence:.2f})")
    ]
    if result.drivers:
        lines.append(("Drivers: " if en else "Yếu tố dẫn dắt: ") + ", ".join(result.drivers))
    recent_events = result.events[-3:]
    if recent_events:
        event_lines = "; ".join(
            f"{e.type} @ {e.ts:%Y-%m-%d} (price {e.price:.4g})"
            + (" [volume-confirmed]" if getattr(e, "volume_confirmed", None) else "")
            for e in recent_events
        )
        lines.append(("Recent events: " if en else "Sự kiện gần đây: ") + event_lines)
    lines.append(
        f"Support/Resistance: {result.levels.support:.4g}/{result.levels.resistance:.4g}"
        if en else f"Hỗ trợ/Kháng cự: {result.levels.support:.4g}/{result.levels.resistance:.4g}"
    )
    return "\n".join(lines)


def _money_flow_evidence_text(result: money_flow.MoneyFlowResult, language: str) -> str:
    en = language == "en"
    label = {
        money_flow.NET_INFLOW: "net inflow" if en else "vào ròng",
        money_flow.NET_OUTFLOW: "net outflow" if en else "ra ròng",
        money_flow.NET_NEUTRAL: "neutral" if en else "trung tính",
    }[result.net_signal]
    if en:
        return (
            f"Money flow (last {result.recent_window} sessions): {result.recent_in_count} inflow day(s), "
            f"{result.recent_out_count} outflow day(s) -> {label}"
        )
    return (
        f"Dòng tiền ({result.recent_window} phiên gần nhất): {result.recent_in_count} phiên vào, "
        f"{result.recent_out_count} phiên ra -> {label}"
    )


def _evidence_block(evidence: SymbolEvidence, language: str) -> str:
    symbol = evidence.symbol
    recent = evidence.candles[-RECENT_CANDLES_SHOWN:]
    return "\n".join([
        f"### {symbol.ticker} ({symbol.display_symbol or symbol.ticker})",
        _wyckoff_evidence_text(evidence.wyckoff, language),
        _money_flow_evidence_text(evidence.flow, language),
        "Recent candles:" if language == "en" else "Nến gần đây:",
        _candle_lines(recent),
    ])


def _build_batch_prompt(entries: list[SymbolEvidence], language: str) -> str:
    symbol_blocks = "\n\n".join(_evidence_block(e, language) for e in entries)
    if language == "en":
        return (
            "You are an experienced discretionary trader. For EACH ticker below, you are given its REAL "
            "Wyckoff phase/confidence/events and technical money-flow reading, plus a short recent-candle "
            "tail for reference. Score its growth potential and write your reason by CITING this given "
            "evidence (phase, driving events, money flow) -- do NOT invent a phase, event, or signal that "
            "isn't in the evidence shown, and do NOT ignore it in favor of your own independent read of the "
            "raw candles.\n\n"
            f"{symbol_blocks}\n\n"
            "Reply with ONLY a valid JSON array, no other text, no markdown code fence:\n"
            '[{"ticker": "<exact ticker as given above>", "score": <0-100 integer, growth potential>, '
            '"reason": "<2-3 sentence explanation in English, referencing the evidence above>"}]'
        )
    return (
        "Bạn là một nhà giao dịch giàu kinh nghiệm. Với MỖI mã dưới đây, bạn được cung cấp giai đoạn/độ tin "
        "cậy/sự kiện Wyckoff THẬT và chỉ số dòng tiền kỹ thuật, kèm vài nến gần nhất để tham khảo. Hãy chấm "
        "điểm tiềm năng tăng giá và viết lý do DỰA VÀO bằng chứng đã cho (giai đoạn, sự kiện dẫn dắt, dòng "
        "tiền) -- KHÔNG tự bịa ra giai đoạn/sự kiện/tín hiệu không có trong bằng chứng bên dưới, và KHÔNG bỏ "
        "qua bằng chứng để tự đọc nến thô theo ý riêng.\n\n"
        f"{symbol_blocks}\n\n"
        "Trả lời DUY NHẤT bằng một mảng JSON hợp lệ, không kèm text nào khác, không bọc markdown code fence:\n"
        '[{"ticker": "<đúng mã như trên>", "score": <số nguyên 0-100, tiềm năng tăng giá>, '
        '"reason": "<lý do 2-3 câu bằng tiếng Việt, có nhắc tới bằng chứng ở trên>"}]'
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

        batch_count = 0
        failed_batch_count = 0
        last_batch_error: str | None = None
        for i in range(0, len(symbols), BATCH_SIZE):
            batch = symbols[i : i + BATCH_SIZE]
            candles_by_ticker = _recent_candles_by_ticker(session, [s.ticker for s in batch])
            entries = [
                build_symbol_evidence(s, candles_by_ticker[s.ticker])
                for s in batch if candles_by_ticker.get(s.ticker)
            ]
            if not entries:
                continue
            batch_count += 1
            prompt = _build_batch_prompt(entries, cfg.language)
            try:
                raw = narrative_mod.call_provider_raw(prompt, cfg)
            except Exception as exc:  # noqa: BLE001 - one bad batch must not abort the run
                logger.warning("potential screener batch failed: %s", exc)
                failed_batch_count += 1
                last_batch_error = str(exc)
                continue
            parsed = _parse_batch_response(raw)
            for evidence in entries:
                result = parsed.get(evidence.symbol.ticker.upper())
                if result:
                    _upsert_result(session, evidence.symbol.ticker, result["score"], result["reason"])
                    scored += 1
            session.commit()
            _state["scored"] = scored

        # Every batch failing (e.g. the configured provider's SDK isn't even
        # installed) previously still reported "success" with 0 scored --
        # indistinguishable from "ran fine, nothing scoreable". Only surface
        # last_error in that all-failed case; a single bad batch among many
        # good ones stays a warning-log-only blip, not a run-level error.
        if batch_count > 0 and failed_batch_count == batch_count:
            _state["last_error"] = last_batch_error
            activity_log.log_action_finish(session, log_id, "error", last_batch_error)
        else:
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

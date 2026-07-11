"""Wyckoff narrative generation from a structured analysis result.

The LLM receives the *already-computed* phase, events and levels plus a compact
recent-candle table, and writes an assessment + advice in the user's chosen
language (Vietnamese or English). It never decides the phase itself. A
disclaimer is always appended so it can't be lost.

Two interchangeable providers: Anthropic's hosted API (paid, needs an API key)
or a local Ollama model (free, runs on the user's machine). Both take the same
prompt and return the same two-section format (marker text differs by
language -- NHẬN ĐỊNH/LỜI KHUYÊN for Vietnamese, ASSESSMENT/ADVICE for English).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from anthropic import Anthropic

from app.wyckoff import AnalysisResult

logger = logging.getLogger("chart_volume.ai")

DISCLAIMER = "⚠️ Đây là phân tích kỹ thuật tự động dựa trên phương pháp Wyckoff, KHÔNG phải khuyến nghị đầu tư. Bạn tự chịu trách nhiệm với quyết định của mình."
DISCLAIMER_EN = "⚠️ This is an automated technical analysis based on the Wyckoff method, NOT investment advice. You are solely responsible for your own decisions."

_ADVICE_MARKER = "LỜI KHUYÊN:"
_NARRATIVE_MARKER = "NHẬN ĐỊNH:"
_ADVICE_MARKER_EN = "ADVICE:"
_NARRATIVE_MARKER_EN = "ASSESSMENT:"

PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OLLAMA = "ollama"

_OLLAMA_TIMEOUT = 120.0  # local inference on modest hardware can be slow


@dataclass(frozen=True)
class ProviderConfig:
    provider: str  # PROVIDER_ANTHROPIC | PROVIDER_OLLAMA
    model: str
    api_key: str = ""  # anthropic only
    base_url: str = "http://localhost:11434"  # ollama only
    language: str = "vi"  # "vi" | "en" -- controls the prompt/output language


def is_available(cfg: ProviderConfig) -> bool:
    if cfg.provider == PROVIDER_OLLAMA:
        return bool(cfg.model)
    return bool(cfg.api_key)


def _candle_table(recent, language: str) -> str:
    header = "Ngày | Open | High | Low | Close | Volume" if language != "en" else "Date | Open | High | Low | Close | Volume"
    lines = [header]
    for c in recent:
        ts = c.bucket_start
        day = ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)
        lines.append(
            f"{day} | {c.open:.2f} | {c.high:.2f} | {c.low:.2f} | {c.close:.2f} | {int(c.volume)}"
        )
    return "\n".join(lines)


def _build_prompt_vi(ticker: str, timeframe: str, result: AnalysisResult, recent) -> str:
    events_desc = (
        "\n".join(
            f"- {e.type} @ {e.ts:%Y-%m-%d} (giá {e.price:.2f}): {e.note}" for e in result.events[-8:]
        )
        or "- (không có sự kiện Wyckoff nổi bật gần đây)"
    )
    return f"""Bạn là chuyên gia phân tích kỹ thuật theo phương pháp Wyckoff cho thị trường chứng khoán Việt Nam.

Hệ thống định lượng đã tính sẵn kết quả dưới đây cho mã **{ticker}** (khung {timeframe}). Hãy DÙNG kết quả này, KHÔNG tự bịa ra phase khác:

- Giai đoạn Wyckoff (phase): {result.phase} (độ tin cậy {result.confidence})
- Yếu tố dẫn dắt: {', '.join(result.drivers) or 'không rõ ràng'}
- Hỗ trợ: {result.levels.support:.2f} | Kháng cự: {result.levels.resistance:.2f}
- Các sự kiện Wyckoff phát hiện:
{events_desc}

Dữ liệu {len(list(recent))} phiên gần nhất:
{_candle_table(recent, "vi")}

Hãy viết bằng tiếng Việt, ngắn gọn, dễ hiểu cho nhà đầu tư cá nhân, đúng 2 phần theo định dạng:

{_NARRATIVE_MARKER}
(3-5 câu diễn giải bối cảnh Wyckoff hiện tại: phase, quan hệ giá-khối lượng, ý nghĩa các sự kiện, vùng giá quan trọng)

{_ADVICE_MARKER}
(2-3 gạch đầu dòng hành động cụ thể theo kịch bản: điều kiện vào/thoát, vùng giá theo dõi, quản trị rủi ro)
"""


def _build_prompt_en(ticker: str, timeframe: str, result: AnalysisResult, recent) -> str:
    events_desc = (
        "\n".join(
            f"- {e.type} @ {e.ts:%Y-%m-%d} (price {e.price:.2f}): {e.note}" for e in result.events[-8:]
        )
        or "- (no notable Wyckoff events recently)"
    )
    return f"""You are a technical analysis expert specializing in the Wyckoff method for the Vietnamese stock market.

A quantitative system has already computed the result below for **{ticker}** (timeframe {timeframe}). USE this result, do NOT invent a different phase:

- Wyckoff phase: {result.phase} (confidence {result.confidence})
- Driving factors: {', '.join(result.drivers) or 'unclear'}
- Support: {result.levels.support:.2f} | Resistance: {result.levels.resistance:.2f}
- Detected Wyckoff events:
{events_desc}

Data for the last {len(list(recent))} sessions:
{_candle_table(recent, "en")}

Write in English, concise and easy to understand for a retail investor, in exactly 2 sections in this format:

{_NARRATIVE_MARKER_EN}
(3-5 sentences explaining the current Wyckoff context: phase, price-volume relationship, meaning of the events, key price zones)

{_ADVICE_MARKER_EN}
(2-3 bullet points of concrete action per scenario: entry/exit conditions, price zones to watch, risk management)
"""


def build_prompt(ticker: str, timeframe: str, result: AnalysisResult, recent, language: str = "vi") -> str:
    if language == "en":
        return _build_prompt_en(ticker, timeframe, result, recent)
    return _build_prompt_vi(ticker, timeframe, result, recent)


def _call_claude(prompt: str, api_key: str, model: str) -> str:
    client = Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(
        getattr(block, "text", "") for block in resp.content if getattr(block, "type", "text") == "text"
    )


def _call_ollama(prompt: str, model: str, base_url: str) -> str:
    resp = httpx.post(
        f"{base_url}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=_OLLAMA_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("response", "")


def _parse(raw: str, language: str = "vi") -> tuple[str, str]:
    advice_marker = _ADVICE_MARKER_EN if language == "en" else _ADVICE_MARKER
    narrative_marker = _NARRATIVE_MARKER_EN if language == "en" else _NARRATIVE_MARKER
    disclaimer = DISCLAIMER_EN if language == "en" else DISCLAIMER
    if advice_marker in raw:
        head, tail = raw.split(advice_marker, 1)
        narrative = head.replace(narrative_marker, "").strip()
        advice = tail.strip()
    else:
        narrative = raw.strip()
        advice = ""
    advice = (advice + "\n\n" + disclaimer).strip()
    return narrative, advice


def generate(
    ticker: str, timeframe: str, result: AnalysisResult, recent, cfg: ProviderConfig
) -> tuple[str, str]:
    prompt = build_prompt(ticker, timeframe, result, recent, cfg.language)
    if cfg.provider == PROVIDER_OLLAMA:
        raw = _call_ollama(prompt, cfg.model, cfg.base_url)
    else:
        raw = _call_claude(prompt, cfg.api_key, cfg.model)
    return _parse(raw, cfg.language)

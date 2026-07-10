"""Wyckoff narrative generation from a structured analysis result.

The LLM receives the *already-computed* phase, events and levels plus a compact
recent-candle table, and writes a Vietnamese assessment + advice. It never
decides the phase itself. A disclaimer is always appended so it can't be lost.

Two interchangeable providers: Anthropic's hosted API (paid, needs an API key)
or a local Ollama model (free, runs on the user's machine). Both take the same
prompt and return the same NHẬN ĐỊNH/LỜI KHUYÊN format.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from anthropic import Anthropic

from app.wyckoff import AnalysisResult

logger = logging.getLogger("chart_volume.ai")

DISCLAIMER = "⚠️ Đây là phân tích kỹ thuật tự động dựa trên phương pháp Wyckoff, KHÔNG phải khuyến nghị đầu tư. Bạn tự chịu trách nhiệm với quyết định của mình."

_ADVICE_MARKER = "LỜI KHUYÊN:"
_NARRATIVE_MARKER = "NHẬN ĐỊNH:"

PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OLLAMA = "ollama"

_OLLAMA_TIMEOUT = 120.0  # local inference on modest hardware can be slow


@dataclass(frozen=True)
class ProviderConfig:
    provider: str  # PROVIDER_ANTHROPIC | PROVIDER_OLLAMA
    model: str
    api_key: str = ""  # anthropic only
    base_url: str = "http://localhost:11434"  # ollama only


def is_available(cfg: ProviderConfig) -> bool:
    if cfg.provider == PROVIDER_OLLAMA:
        return bool(cfg.model)
    return bool(cfg.api_key)


def _candle_table(recent) -> str:
    lines = ["Ngày | Open | High | Low | Close | Volume"]
    for c in recent:
        ts = c.bucket_start
        day = ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)
        lines.append(
            f"{day} | {c.open:.2f} | {c.high:.2f} | {c.low:.2f} | {c.close:.2f} | {int(c.volume)}"
        )
    return "\n".join(lines)


def build_prompt(ticker: str, timeframe: str, result: AnalysisResult, recent) -> str:
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
{_candle_table(recent)}

Hãy viết bằng tiếng Việt, ngắn gọn, dễ hiểu cho nhà đầu tư cá nhân, đúng 2 phần theo định dạng:

{_NARRATIVE_MARKER}
(3-5 câu diễn giải bối cảnh Wyckoff hiện tại: phase, quan hệ giá-khối lượng, ý nghĩa các sự kiện, vùng giá quan trọng)

{_ADVICE_MARKER}
(2-3 gạch đầu dòng hành động cụ thể theo kịch bản: điều kiện vào/thoát, vùng giá theo dõi, quản trị rủi ro)
"""


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


def _parse(raw: str) -> tuple[str, str]:
    if _ADVICE_MARKER in raw:
        head, tail = raw.split(_ADVICE_MARKER, 1)
        narrative = head.replace(_NARRATIVE_MARKER, "").strip()
        advice = tail.strip()
    else:
        narrative = raw.strip()
        advice = ""
    advice = (advice + "\n\n" + DISCLAIMER).strip()
    return narrative, advice


def generate(
    ticker: str, timeframe: str, result: AnalysisResult, recent, cfg: ProviderConfig
) -> tuple[str, str]:
    prompt = build_prompt(ticker, timeframe, result, recent)
    if cfg.provider == PROVIDER_OLLAMA:
        raw = _call_ollama(prompt, cfg.model, cfg.base_url)
    else:
        raw = _call_claude(prompt, cfg.api_key, cfg.model)
    return _parse(raw)

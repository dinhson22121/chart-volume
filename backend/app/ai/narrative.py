"""Wyckoff narrative generation from a structured analysis result.

The LLM receives the *already-computed* phase, events and levels plus a compact
recent-candle table, and writes an assessment + advice in the user's chosen
language (Vietnamese or English). It never decides the phase itself. A
disclaimer is always appended so it can't be lost.

Four interchangeable providers: Anthropic's hosted API, OpenAI's hosted API
("Codex"), Google Antigravity's multi-agent SDK, or a local Ollama model
(free, runs on the user's machine). All take the same prompt and return the
same two-section format (marker text differs by language -- NHẬN ĐỊNH/LỜI
KHUYÊN for Vietnamese, ASSESSMENT/ADVICE for English).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from anthropic import Anthropic
from openai import OpenAI

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
PROVIDER_ANTIGRAVITY = "antigravity"
PROVIDER_CODEX = "codex"

_OLLAMA_TIMEOUT = 120.0  # local inference on modest hardware can be slow


@dataclass(frozen=True)
class ProviderConfig:
    provider: str  # PROVIDER_ANTHROPIC | PROVIDER_OLLAMA | PROVIDER_ANTIGRAVITY | PROVIDER_CODEX
    model: str
    api_key: str = ""  # anthropic + codex
    base_url: str = "http://localhost:11434"  # ollama only
    language: str = "vi"  # "vi" | "en" -- controls the prompt/output language


def is_available(cfg: ProviderConfig) -> bool:
    if cfg.provider == PROVIDER_OLLAMA:
        return bool(cfg.model)
    if cfg.provider == PROVIDER_ANTIGRAVITY:
        return True
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


def _build_prompt_vi(ticker: str, timeframe: str, result: AnalysisResult, recent, strategy_label: str) -> str:
    events_desc = (
        "\n".join(
            f"- {e.type} @ {e.ts:%Y-%m-%d} (giá {e.price:.2f}): {e.note}" for e in result.events[-8:]
        )
        or f"- (không có sự kiện {strategy_label} nổi bật gần đây)"
    )
    return f"""Bạn là chuyên gia phân tích kỹ thuật theo phương pháp {strategy_label} cho thị trường chứng khoán Việt Nam.

Hệ thống định lượng đã tính sẵn kết quả dưới đây cho mã **{ticker}** (khung {timeframe}). Hãy DÙNG kết quả này, KHÔNG tự bịa ra phase khác:

- Giai đoạn {strategy_label} (phase): {result.phase} (độ tin cậy {result.confidence})
- Yếu tố dẫn dắt: {', '.join(result.drivers) or 'không rõ ràng'}
- Hỗ trợ: {result.levels.support:.2f} | Kháng cự: {result.levels.resistance:.2f}
- Các sự kiện {strategy_label} phát hiện:
{events_desc}

Dữ liệu {len(list(recent))} phiên gần nhất:
{_candle_table(recent, "vi")}

Hãy viết bằng tiếng Việt, ngắn gọn, dễ hiểu cho nhà đầu tư cá nhân, đúng 2 phần theo định dạng:

{_NARRATIVE_MARKER}
(3-5 câu diễn giải bối cảnh {strategy_label} hiện tại: phase, quan hệ giá-khối lượng, ý nghĩa các sự kiện, vùng giá quan trọng)

{_ADVICE_MARKER}
(2-3 gạch đầu dòng hành động cụ thể theo kịch bản: điều kiện vào/thoát, vùng giá theo dõi, quản trị rủi ro)
"""


def _build_prompt_en(ticker: str, timeframe: str, result: AnalysisResult, recent, strategy_label: str) -> str:
    events_desc = (
        "\n".join(
            f"- {e.type} @ {e.ts:%Y-%m-%d} (price {e.price:.2f}): {e.note}" for e in result.events[-8:]
        )
        or f"- (no notable {strategy_label} events recently)"
    )
    return f"""You are a technical analysis expert specializing in the {strategy_label} method for the Vietnamese stock market.

A quantitative system has already computed the result below for **{ticker}** (timeframe {timeframe}). USE this result, do NOT invent a different phase:

- {strategy_label} phase: {result.phase} (confidence {result.confidence})
- Driving factors: {', '.join(result.drivers) or 'unclear'}
- Support: {result.levels.support:.2f} | Resistance: {result.levels.resistance:.2f}
- Detected {strategy_label} events:
{events_desc}

Data for the last {len(list(recent))} sessions:
{_candle_table(recent, "en")}

Write in English, concise and easy to understand for a retail investor, in exactly 2 sections in this format:

{_NARRATIVE_MARKER_EN}
(3-5 sentences explaining the current {strategy_label} context: phase, price-volume relationship, meaning of the events, key price zones)

{_ADVICE_MARKER_EN}
(2-3 bullet points of concrete action per scenario: entry/exit conditions, price zones to watch, risk management)
"""


def build_prompt(
    ticker: str, timeframe: str, result: AnalysisResult, recent, language: str = "vi", strategy_label: str = "Wyckoff"
) -> str:
    if language == "en":
        return _build_prompt_en(ticker, timeframe, result, recent, strategy_label)
    return _build_prompt_vi(ticker, timeframe, result, recent, strategy_label)


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


def _call_codex(prompt: str, api_key: str, model: str) -> str:
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content or ""


def _call_ollama(prompt: str, model: str, base_url: str) -> str:
    resp = httpx.post(
        f"{base_url}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=_OLLAMA_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("response", "")


def _call_antigravity(prompt: str, model: str, api_key: str) -> tuple[str, str]:
    import asyncio
    import json
    from google.antigravity import Agent, LocalAgentConfig

    async def _async_call():
        config_architect = LocalAgentConfig(
            model="gemini-2.5-flash",
            api_key=api_key,
            system_instruction=(
                "Bạn là AgyArchitect, chuyên gia phân tích kỹ thuật chứng khoán và crypto. "
                "Hãy phân tích các dữ liệu nến, chỉ báo và sự kiện Wyckoff/SMC/Sonic R được cung cấp. "
                "Đưa ra một phân tích kỹ thuật khách quan, chi tiết về xu hướng, các mức cản hỗ trợ/kháng cự quan trọng."
            )
        )

        config_analyst = LocalAgentConfig(
            model="gemini-2.5-flash",
            api_key=api_key,
            system_instruction=(
                "Bạn là AgyAnalyst, chuyên gia tư vấn đầu tư. Dựa trên bản phân tích kỹ thuật được cung cấp, "
                "hãy viết Lời khuyên đầu tư hành động ngắn gọn, dễ hiểu cho nhà đầu tư cá nhân "
                "(ví dụ: điểm mua, bán, cắt lỗ, quản lý rủi ro)."
            )
        )

        config_leader = LocalAgentConfig(
            model=model or "gemini-3.5-pro",
            api_key=api_key,
            system_instruction=(
                "Bạn là AgyLeader, trưởng nhóm phân tích đầu tư AI. Tổng hợp bản phân tích từ AgyArchitect và AgyAnalyst "
                "để trả về báo cáo cuối cùng. Bắt buộc báo cáo phải chứa tiêu đề '[NHẬN ĐỊNH]' trước phần nhận định kỹ thuật "
                "và '[LỜI KHUYÊN]' trước phần lời khuyên hành động đầu tư."
            )
        )

        sub_agents = []

        # 1. Spawning AgyArchitect
        sub_agents.append({
            "name": "AgyArchitect",
            "role": "Technical Chart Analyst",
            "model": "gemini-2.5-flash",
            "status": "RUNNING"
        })
        async with Agent(config_architect) as architect:
            resp_architect = await architect.chat(f"Hãy phân tích dữ liệu sau đây:\n{prompt}")
            analysis_text = await resp_architect.text()
            sub_agents[0]["status"] = "COMPLETED"
            sub_agents[0]["output_length"] = len(analysis_text)

        # 2. Spawning AgyAnalyst
        sub_agents.append({
            "name": "AgyAnalyst",
            "role": "Investment Advisor",
            "model": "gemini-2.5-flash",
            "status": "RUNNING"
        })
        async with Agent(config_analyst) as analyst:
            resp_analyst = await analyst.chat(f"Hãy đưa ra lời khuyên hành động dựa trên phân tích kỹ thuật sau:\n{analysis_text}")
            advice_text = await resp_analyst.text()
            sub_agents[1]["status"] = "COMPLETED"
            sub_agents[1]["output_length"] = len(advice_text)

        # 3. Spawning AgyLeader
        sub_agents.append({
            "name": "AgyLeader",
            "role": "Team Lead Orchestrator",
            "model": model or "gemini-3.5-pro",
            "status": "RUNNING"
        })
        async with Agent(config_leader) as leader:
            leader_prompt = (
                "Dưới đây là báo cáo từ nhóm của bạn:\n"
                f"### PHÂN TÍCH KỸ THUẬT (từ AgyArchitect):\n{analysis_text}\n\n"
                f"### LỜI KHUYÊN (từ AgyAnalyst):\n{advice_text}\n\n"
                "Hãy kết hợp và định dạng lại báo cáo đầy đủ của cả 2 phần, giữ nguyên cấu trúc tiêu đề '[NHẬN ĐỊNH]' và '[LỜI KHUYÊN]'."
            )
            resp_leader = await leader.chat(leader_prompt)
            final_text = await resp_leader.text()
            sub_agents[2]["status"] = "COMPLETED"
            sub_agents[2]["output_length"] = len(final_text)

        return final_text, json.dumps(sub_agents, ensure_ascii=False)

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(lambda: asyncio.run(_async_call()))
            final_text, sub_agents_json = future.result()
    else:
        final_text, sub_agents_json = asyncio.run(_async_call())

    return final_text, sub_agents_json


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
    ticker: str, timeframe: str, result: AnalysisResult, recent, cfg: ProviderConfig, strategy_label: str = "Wyckoff"
) -> tuple[str, str, str | None]:
    prompt = build_prompt(ticker, timeframe, result, recent, cfg.language, strategy_label)
    if cfg.provider == PROVIDER_OLLAMA:
        raw = _call_ollama(prompt, cfg.model, cfg.base_url)
        narrative, advice = _parse(raw, cfg.language)
        return narrative, advice, None
    elif cfg.provider == PROVIDER_CODEX:
        raw = _call_codex(prompt, cfg.api_key, cfg.model)
        narrative, advice = _parse(raw, cfg.language)
        return narrative, advice, None
    elif cfg.provider == PROVIDER_ANTIGRAVITY:
        raw, sub_agents = _call_antigravity(prompt, cfg.model, cfg.api_key)
        narrative, advice = _parse(raw, cfg.language)
        return narrative, advice, sub_agents
    else:
        raw = _call_claude(prompt, cfg.api_key, cfg.model)
        narrative, advice = _parse(raw, cfg.language)
        return narrative, advice, None

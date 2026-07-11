from datetime import datetime

from app.ai import narrative
from app.ai.narrative import PROVIDER_ANTHROPIC, PROVIDER_OLLAMA, ProviderConfig
from app.wyckoff import AnalysisResult, Levels

RESULT = AnalysisResult(
    phase="Accumulation",
    confidence=0.7,
    events=[],
    levels=Levels(support=99.0, resistance=101.0),
    as_of=datetime(2025, 1, 1),
    drivers=["Spring"],
)


def test_is_available_anthropic_requires_api_key():
    assert narrative.is_available(ProviderConfig(provider=PROVIDER_ANTHROPIC, model="claude-sonnet-4-5", api_key="sk-ant-x"))
    assert not narrative.is_available(ProviderConfig(provider=PROVIDER_ANTHROPIC, model="claude-sonnet-4-5", api_key=""))


def test_is_available_ollama_requires_model_not_api_key():
    assert narrative.is_available(ProviderConfig(provider=PROVIDER_OLLAMA, model="qwen2.5:7b"))
    assert not narrative.is_available(ProviderConfig(provider=PROVIDER_OLLAMA, model=""))


def test_generate_dispatches_to_claude_for_anthropic_provider(mocker):
    spy = mocker.patch.object(
        narrative, "_call_claude", return_value="NHẬN ĐỊNH:\nx\n\nLỜI KHUYÊN:\n- y"
    )
    cfg = ProviderConfig(provider=PROVIDER_ANTHROPIC, model="claude-sonnet-4-5", api_key="sk-ant-x")

    text, advice = narrative.generate("FPT", "daily", RESULT, [], cfg)

    spy.assert_called_once()
    assert spy.call_args[0][1] == "sk-ant-x"
    assert spy.call_args[0][2] == "claude-sonnet-4-5"
    assert text == "x"
    assert narrative.DISCLAIMER in advice


def test_generate_dispatches_to_ollama_for_ollama_provider(mocker):
    spy = mocker.patch.object(
        narrative, "_call_ollama", return_value="NHẬN ĐỊNH:\nx\n\nLỜI KHUYÊN:\n- y"
    )
    cfg = ProviderConfig(provider=PROVIDER_OLLAMA, model="qwen2.5:7b")

    text, advice = narrative.generate("FPT", "daily", RESULT, [], cfg)

    spy.assert_called_once()
    assert spy.call_args[0][1] == "qwen2.5:7b"
    assert text == "x"
    assert narrative.DISCLAIMER in advice


def test_generate_uses_english_markers_and_disclaimer_when_language_is_en(mocker):
    mocker.patch.object(
        narrative, "_call_claude", return_value="ASSESSMENT:\nx\n\nADVICE:\n- y"
    )
    cfg = ProviderConfig(provider=PROVIDER_ANTHROPIC, model="claude-sonnet-4-5", api_key="sk-ant-x", language="en")

    text, advice = narrative.generate("FPT", "daily", RESULT, [], cfg)

    assert text == "x"
    assert narrative.DISCLAIMER_EN in advice
    assert narrative.DISCLAIMER not in advice


def test_build_prompt_defaults_to_vietnamese():
    prompt = narrative.build_prompt("FPT", "daily", RESULT, [])
    assert narrative._NARRATIVE_MARKER in prompt
    assert narrative._ADVICE_MARKER in prompt
    assert "Bạn là chuyên gia" in prompt


def test_build_prompt_defaults_strategy_label_to_wyckoff():
    prompt = narrative.build_prompt("FPT", "daily", RESULT, [])
    assert "theo phương pháp Wyckoff" in prompt
    assert "Giai đoạn Wyckoff" in prompt


def test_build_prompt_uses_the_given_strategy_label():
    prompt_vi = narrative.build_prompt("FPT", "daily", RESULT, [], strategy_label="Smart Money Concept")
    assert "theo phương pháp Smart Money Concept" in prompt_vi
    assert "Giai đoạn Smart Money Concept" in prompt_vi
    assert "Wyckoff" not in prompt_vi

    prompt_en = narrative.build_prompt("FPT", "daily", RESULT, [], language="en", strategy_label="Sonic R")
    assert "specializing in the Sonic R method" in prompt_en
    assert "Sonic R phase" in prompt_en


def test_build_prompt_builds_english_variant_when_requested():
    prompt = narrative.build_prompt("FPT", "daily", RESULT, [], language="en")
    assert narrative._NARRATIVE_MARKER_EN in prompt
    assert narrative._ADVICE_MARKER_EN in prompt
    assert "You are a technical analysis expert" in prompt
    # Vietnamese markers must not leak into the English prompt.
    assert narrative._NARRATIVE_MARKER not in prompt
    assert narrative._ADVICE_MARKER not in prompt


def test_parse_falls_back_to_raw_text_when_marker_missing_regardless_of_language():
    narrative_text, advice = narrative._parse("just some text", language="en")
    assert narrative_text == "just some text"
    assert narrative.DISCLAIMER_EN in advice


def test_call_ollama_posts_to_generate_endpoint(mocker):
    mock_response = mocker.Mock()
    mock_response.json.return_value = {"response": "hello from ollama"}
    mock_response.raise_for_status.return_value = None
    post_spy = mocker.patch("httpx.post", return_value=mock_response)

    result = narrative._call_ollama("some prompt", "qwen2.5:7b", "http://localhost:11434")

    assert result == "hello from ollama"
    post_spy.assert_called_once()
    assert post_spy.call_args[0][0] == "http://localhost:11434/api/generate"
    assert post_spy.call_args[1]["json"]["model"] == "qwen2.5:7b"
    assert post_spy.call_args[1]["json"]["stream"] is False

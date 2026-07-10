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

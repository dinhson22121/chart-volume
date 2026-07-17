import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_get_settings_requires_token(client):
    assert client.get("/settings").status_code == 401


def test_get_settings_defaults(client, auth_header):
    resp = client.get("/settings", headers=auth_header)
    assert resp.status_code == 200
    body = resp.json()
    assert body["daily_lookback_days"] == 730
    assert body["has_anthropic_key"] is False
    assert "anthropic_api_key" not in body
    assert body["language"] == "vi"


def test_put_settings_accepts_english_language(client, auth_header):
    resp = client.put("/settings", json={"language": "en"}, headers=auth_header)
    assert resp.status_code == 200
    assert resp.json()["language"] == "en"


def test_put_settings_rejects_unknown_language(client, auth_header):
    resp = client.put("/settings", json={"language": "fr"}, headers=auth_header)
    assert resp.status_code == 422


def test_put_settings_updates_and_never_echoes_key(client, auth_header):
    resp = client.put(
        "/settings",
        json={"anthropic_api_key": "sk-ant-super-secret", "daily_lookback_days": 400},
        headers=auth_header,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_anthropic_key"] is True
    assert body["daily_lookback_days"] == 400
    assert "sk-ant-super-secret" not in resp.text


def test_put_settings_reschedules_when_scheduler_keys_change(client, auth_header):
    # No app.state.scheduler in the test client (lifespan doesn't run under
    # TestClient's default context manager usage here) -> must not crash.
    resp = client.put(
        "/settings",
        json={"scheduler_enabled": False},
        headers=auth_header,
    )
    assert resp.status_code == 200
    assert resp.json()["scheduler_enabled"] is False


def test_put_settings_rejects_invalid_lookback(client, auth_header):
    resp = client.put(
        "/settings", json={"daily_lookback_days": 1}, headers=auth_header
    )
    assert resp.status_code == 422


def test_get_settings_defaults_include_strategy(client, auth_header):
    body = client.get("/settings", headers=auth_header).json()
    assert body["strategy"] == "wyckoff"


def test_put_settings_accepts_known_strategy(client, auth_header):
    resp = client.put("/settings", json={"strategy": "wyckoff"}, headers=auth_header)
    assert resp.status_code == 200
    assert resp.json()["strategy"] == "wyckoff"


def test_put_settings_rejects_unknown_strategy(client, auth_header):
    resp = client.put("/settings", json={"strategy": "does-not-exist"}, headers=auth_header)
    assert resp.status_code == 422


def test_put_settings_accepts_sonicr_strategy_and_thresholds(client, auth_header):
    resp = client.put(
        "/settings",
        json={"strategy": "sonicr", "sonicr_dragon_period": 21, "sonicr_pullback_lookback_bars": 8},
        headers=auth_header,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["strategy"] == "sonicr"
    assert body["sonicr_dragon_period"] == 21
    assert body["sonicr_pullback_lookback_bars"] == 8


def test_put_settings_rejects_invalid_sonicr_vfactor(client, auth_header):
    resp = client.put("/settings", json={"sonicr_t3_vfactor": 1.5}, headers=auth_header)
    assert resp.status_code == 422


def test_put_settings_accepts_smc_strategy_and_thresholds(client, auth_header):
    resp = client.put(
        "/settings",
        json={"strategy": "smc", "smc_swing_lookback": 3, "smc_fvg_min_gap_mult": 0.5},
        headers=auth_header,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["strategy"] == "smc"
    assert body["smc_swing_lookback"] == 3
    assert body["smc_fvg_min_gap_mult"] == 0.5


def test_put_settings_rejects_invalid_smc_swing_lookback(client, auth_header):
    resp = client.put("/settings", json={"smc_swing_lookback": 0}, headers=auth_header)
    assert resp.status_code == 422


def test_put_settings_accepts_screener_require_volume_rising(client, auth_header):
    resp = client.put(
        "/settings", json={"screener_require_volume_rising": True}, headers=auth_header
    )
    assert resp.status_code == 200
    assert resp.json()["screener_require_volume_rising"] is True


def test_put_settings_accepts_restricted_crypto_exchanges(client, auth_header):
    resp = client.put("/settings", json={"crypto_exchanges": ["binance"]}, headers=auth_header)
    assert resp.status_code == 200
    assert resp.json()["crypto_exchanges"] == ["binance"]


def test_put_settings_rejects_empty_crypto_exchanges(client, auth_header):
    resp = client.put("/settings", json={"crypto_exchanges": []}, headers=auth_header)
    assert resp.status_code == 422


def test_put_settings_rejects_unknown_crypto_exchange(client, auth_header):
    resp = client.put("/settings", json={"crypto_exchanges": ["bybit"]}, headers=auth_header)
    assert resp.status_code == 422


def test_put_settings_accepts_crypto_analysis_settings(client, auth_header):
    resp = client.put(
        "/settings",
        json={"crypto_analysis_enabled": False, "crypto_analysis_interval": "1h"},
        headers=auth_header,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["crypto_analysis_enabled"] is False
    assert body["crypto_analysis_interval"] == "1h"


def test_put_settings_rejects_unknown_crypto_analysis_interval(client, auth_header):
    resp = client.put(
        "/settings", json={"crypto_analysis_interval": "bogus"}, headers=auth_header
    )
    assert resp.status_code == 422


def test_get_settings_exposes_top100_defaults(client, auth_header):
    body = client.get("/settings", headers=auth_header).json()
    assert body["top100_auto_refresh_enabled"] is True
    assert body["top100_refresh_time"] == "07:00"


def test_ai_narrative_group_defaults_and_roundtrip(client, auth_header):
    body = client.get("/settings", headers=auth_header).json()
    assert body["ai_narrative_vn30"] is True
    assert body["ai_narrative_watchlist"] is True
    assert body["ai_narrative_top100"] is False  # API-heavy, off by default

    resp = client.put(
        "/settings",
        json={"ai_narrative_top100": True, "ai_narrative_vn30": False},
        headers=auth_header,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ai_narrative_top100"] is True
    assert body["ai_narrative_vn30"] is False
    assert body["ai_narrative_watchlist"] is True  # untouched


def test_put_settings_accepts_top100_settings(client, auth_header):
    resp = client.put(
        "/settings",
        json={"top100_auto_refresh_enabled": False, "top100_refresh_time": "09:30"},
        headers=auth_header,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["top100_auto_refresh_enabled"] is False
    assert body["top100_refresh_time"] == "09:30"


def test_get_settings_exposes_potential_screen_defaults(client, auth_header):
    body = client.get("/settings", headers=auth_header).json()
    assert body["potential_screen_auto_enabled"] is False  # AI-heaviest feature, off by default
    assert body["potential_screen_time"] == "06:30"


def test_put_settings_accepts_potential_screen_settings(client, auth_header):
    resp = client.put(
        "/settings",
        json={"potential_screen_auto_enabled": True, "potential_screen_time": "05:00"},
        headers=auth_header,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["potential_screen_auto_enabled"] is True
    assert body["potential_screen_time"] == "05:00"


def test_put_settings_accepts_plain_ollama_model(client, auth_header):
    resp = client.put("/settings", json={"ollama_model": "qwen2.5:7b"}, headers=auth_header)
    assert resp.status_code == 200
    assert resp.json()["ollama_model"] == "qwen2.5:7b"


def test_put_settings_rejects_ollama_model_embedding_a_registry_host(client, auth_header):
    # Same guard as /ollama/pull -- a "/" would let this value redirect the
    # user's local Ollama daemon to an attacker-chosen host at generation time.
    resp = client.put(
        "/settings", json={"ollama_model": "evil-registry.example/library/llama3"}, headers=auth_header
    )
    assert resp.status_code == 422

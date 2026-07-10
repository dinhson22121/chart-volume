from app.ai.narrative import PROVIDER_ANTHROPIC, PROVIDER_OLLAMA
from app.services import settings_service


def test_defaults_when_nothing_stored(session):
    out = settings_service.get_public(session)
    assert out["strategy"] == "wyckoff"
    assert out["anthropic_model"] == "claude-sonnet-4-5"
    assert out["narrative_provider"] == PROVIDER_ANTHROPIC
    assert out["ollama_model"] == ""
    assert out["daily_lookback_days"] == 730
    assert out["half_session_lookback_days"] == 60
    assert out["scheduler_enabled"] is True
    assert out["climax_vol_mult"] == 2.0
    assert out["has_anthropic_key"] is False


def test_update_non_secret_values(session):
    settings_service.update(session, {"daily_lookback_days": "365", "climax_vol_mult": "3.5"})
    out = settings_service.get_public(session)
    assert out["daily_lookback_days"] == 365
    assert out["climax_vol_mult"] == 3.5


def test_update_api_key_is_encrypted_at_rest(session):
    settings_service.update(session, {"anthropic_api_key": "sk-ant-real-secret"})

    from app.models import Setting

    row = session.get(Setting, settings_service.KEY_API)
    assert row.value != "sk-ant-real-secret"
    assert row.value.startswith("enc:")

    out = settings_service.get_public(session)
    assert out["has_anthropic_key"] is True
    assert "anthropic_api_key" not in out  # never exposed to the client

    cfg = settings_service.get_narrative_config(session)
    assert cfg.provider == PROVIDER_ANTHROPIC
    assert cfg.api_key == "sk-ant-real-secret"


def test_clearing_api_key_with_empty_string(session):
    settings_service.update(session, {"anthropic_api_key": "sk-ant-real-secret"})
    settings_service.update(session, {"anthropic_api_key": ""})

    out = settings_service.get_public(session)
    assert out["has_anthropic_key"] is False


def test_narrative_config_switches_to_ollama(session):
    settings_service.update(
        session, {"narrative_provider": "ollama", "ollama_model": "qwen2.5:7b"}
    )
    cfg = settings_service.get_narrative_config(session)
    assert cfg.provider == PROVIDER_OLLAMA
    assert cfg.model == "qwen2.5:7b"
    assert cfg.api_key == ""  # not relevant for ollama, must stay empty


def test_get_wyckoff_config_reflects_overrides(session):
    settings_service.update(session, {"sos_vol_mult": "1.8", "low_vol_mult": "0.5"})
    cfg = settings_service.get_wyckoff_config(session)
    assert cfg.sos_vol_mult == 1.8
    assert cfg.low_vol_mult == 0.5
    assert cfg.climax_vol_mult == 2.0  # untouched default


def test_lps_lookback_bars_default_and_override(session):
    assert settings_service.get_public(session)["lps_lookback_bars"] == 10
    assert settings_service.get_wyckoff_config(session).lps_lookback_bars == 10

    settings_service.update(session, {"lps_lookback_bars": "5"})

    assert settings_service.get_public(session)["lps_lookback_bars"] == 5
    assert settings_service.get_wyckoff_config(session).lps_lookback_bars == 5


def test_get_scheduler_config_reflects_overrides(session):
    settings_service.update(
        session, {"scheduler_enabled": "false", "daily_time": "16:00"}
    )
    cfg = settings_service.get_scheduler_config(session)
    assert cfg["enabled"] is False
    assert cfg["daily_time"] == "16:00"
    assert cfg["half_morning_time"] == "11:35"  # untouched default


def test_get_strategy_defaults_to_wyckoff(session):
    assert settings_service.get_strategy(session) == "wyckoff"


def test_get_strategy_falls_back_to_default_for_unknown_stored_value(session, mocker):
    # A stored value could become stale (e.g. after a strategy is removed);
    # get_strategy() must degrade gracefully rather than error out.
    settings_service.update(session, {"strategy": "wyckoff"})
    mocker.patch(
        "app.services.settings_service.strategy_registry.is_known", return_value=False
    )
    assert settings_service.get_strategy(session) == "wyckoff"  # DEFAULTS["strategy"]


def test_get_strategy_config_returns_wyckoff_config(session):
    settings_service.update(session, {"sos_vol_mult": "1.9"})
    cfg = settings_service.get_strategy_config(session, "wyckoff")
    assert cfg.sos_vol_mult == 1.9


def test_get_sonicr_config_defaults(session):
    cfg = settings_service.get_sonicr_config(session)
    assert cfg.dragon_period == 34
    assert cfg.t3_fast_period == 5
    assert cfg.t3_slow_period == 8
    assert cfg.t3_vfactor == 0.7
    assert cfg.cci_fast_period == 6
    assert cfg.cci_slow_period == 14
    assert cfg.pullback_lookback_bars == 10


def test_get_sonicr_config_reflects_overrides(session):
    settings_service.update(
        session, {"sonicr_dragon_period": "50", "sonicr_pullback_lookback_bars": "5"}
    )
    cfg = settings_service.get_sonicr_config(session)
    assert cfg.dragon_period == 50
    assert cfg.pullback_lookback_bars == 5
    assert cfg.t3_fast_period == 5  # untouched default


def test_get_strategy_config_returns_sonicr_config_when_strategy_is_sonicr(session):
    settings_service.update(session, {"sonicr_dragon_period": "21"})
    cfg = settings_service.get_strategy_config(session, "sonicr")
    assert cfg.dragon_period == 21


def test_screener_require_volume_rising_defaults_to_false(session):
    assert settings_service.get_public(session)["screener_require_volume_rising"] is False
    assert settings_service.get_screener_config(session)["require_volume_rising"] is False


def test_screener_require_volume_rising_can_be_enabled(session):
    settings_service.update(session, {"screener_require_volume_rising": "true"})
    assert settings_service.get_public(session)["screener_require_volume_rising"] is True
    assert settings_service.get_screener_config(session)["require_volume_rising"] is True


def test_crypto_exchanges_defaults_to_centralized_exchanges(session):
    assert settings_service.get_public(session)["crypto_exchanges"] == ["binance", "kucoin", "mexc"]
    assert settings_service.get_crypto_exchanges(session) == ("binance", "kucoin", "mexc")


def test_crypto_exchanges_can_be_restricted_to_one(session):
    settings_service.update(session, {"crypto_exchanges": ["kucoin"]})
    assert settings_service.get_public(session)["crypto_exchanges"] == ["kucoin"]
    assert settings_service.get_crypto_exchanges(session) == ("kucoin",)


def test_crypto_analysis_config_defaults(session):
    cfg = settings_service.get_crypto_analysis_config(session)
    assert cfg["enabled"] is True
    assert cfg["interval"] == "4h"


def test_crypto_analysis_config_reflects_overrides(session):
    settings_service.update(
        session, {"crypto_analysis_enabled": "false", "crypto_analysis_interval": "1h"}
    )
    cfg = settings_service.get_crypto_analysis_config(session)
    assert cfg["enabled"] is False
    assert cfg["interval"] == "1h"

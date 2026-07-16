"""User settings store (key-value in SQLite).

Non-secret values are stored as plain strings; the Anthropic API key is stored
encrypted (see app.crypto). Backend consumers read typed values through the
``get_*`` helpers; env vars act as a fallback when a value is unset.
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.ai.narrative import (
    PROVIDER_ANTHROPIC,
    PROVIDER_ANTIGRAVITY,
    PROVIDER_CODEX,
    PROVIDER_OLLAMA,
    ProviderConfig,
)
from app.config import get_settings
from app.crypto import decrypt, encrypt
from app.models import CryptoExchange, Setting
from app.services import activity_log
from app.smc.config import SMCConfig
from app.sonicr.config import SonicRConfig
from app.strategies import registry as strategy_registry
from app.wyckoff.config import WyckoffConfig

KEY_API = "anthropic_api_key"
KEY_GEMINI_API = "gemini_api_key"
KEY_OPENAI_API = "openai_api_key"

DEFAULTS: dict[str, str] = {
    "language": "vi",  # "vi" | "en" -- controls both UI text and AI narrative language
    "strategy": strategy_registry.DEFAULT_STRATEGY,
    "narrative_provider": PROVIDER_ANTHROPIC,
    "anthropic_model": "claude-sonnet-4-5",
    "ollama_model": "",
    "antigravity_model": "gemini-3.5-flash",
    "gemini_api_key": "",
    "openai_model": "gpt-5",
    "openai_api_key": "",
    "daily_lookback_days": "730",
    "half_session_lookback_days": "60",
    "scheduler_enabled": "true",
    "half_morning_time": "11:35",
    "half_afternoon_time": "15:05",
    "daily_time": "15:15",
    "climax_vol_mult": "2.0",
    "wide_spread_mult": "1.5",
    "narrow_spread_mult": "0.7",
    "low_vol_mult": "0.7",
    "sos_vol_mult": "1.5",
    "lps_lookback_bars": "10",
    "sonicr_dragon_period": "34",
    "sonicr_t3_fast_period": "5",
    "sonicr_t3_slow_period": "8",
    "sonicr_t3_vfactor": "0.7",
    "sonicr_cci_fast_period": "6",
    "sonicr_cci_slow_period": "14",
    "sonicr_pullback_lookback_bars": "10",
    "smc_swing_lookback": "2",
    "smc_ob_lookback_bars": "10",
    "smc_fvg_min_gap_mult": "0.3",
    "screener_enabled": "false",
    "screener_mcap_max": "10000000",
    "screener_require_volume_rising": "false",
    "screener_min_volume_change_pct": "50.0",
    "screener_scan_interval": "1h",
    # GeckoTerminal defaults off: it's a much heavier, more rate-limited path
    # (DEX pool discovery + resolution) than the centralized exchanges -- opt-in only.
    "crypto_exchanges": ",".join(
        (CryptoExchange.BINANCE, CryptoExchange.KUCOIN, CryptoExchange.MEXC)
    ),
    "crypto_analysis_enabled": "true",
    "crypto_analysis_interval": "4h",
    "top100_auto_refresh_enabled": "true",
    "top100_refresh_time": "07:00",
    # Per-group toggles for LLM narrative generation during scheduled
    # batches (manual per-ticker refresh always generates AI regardless).
    # Top100 defaults off: ~100 coins x 3 timeframes per cycle of narratives
    # nobody opens is a token burn, not a feature.
    "ai_narrative_vn30": "true",
    "ai_narrative_watchlist": "true",
    "ai_narrative_top100": "false",
}

# Allowed values for settings that are a fixed choice rather than a free number.
SCREENER_MCAP_CHOICES = (10_000_000.0, 20_000_000.0, 30_000_000.0, 50_000_000.0)
SCREENER_INTERVAL_CHOICES = ("10m", "30m", "1h", "4h", "12h", "1d")
CRYPTO_EXCHANGE_CHOICES = CryptoExchange.ALL
CRYPTO_ANALYSIS_INTERVAL_CHOICES = SCREENER_INTERVAL_CHOICES

_FLOAT_KEYS = {
    "climax_vol_mult", "wide_spread_mult", "narrow_spread_mult", "low_vol_mult", "sos_vol_mult",
    "screener_mcap_max", "screener_min_volume_change_pct", "sonicr_t3_vfactor", "smc_fvg_min_gap_mult",
}
_INT_KEYS = {
    "daily_lookback_days", "half_session_lookback_days", "lps_lookback_bars",
    "sonicr_dragon_period", "sonicr_t3_fast_period", "sonicr_t3_slow_period",
    "sonicr_cci_fast_period", "sonicr_cci_slow_period", "sonicr_pullback_lookback_bars",
    "smc_swing_lookback", "smc_ob_lookback_bars",
}
_BOOL_KEYS = {
    "scheduler_enabled", "screener_enabled", "screener_require_volume_rising", "crypto_analysis_enabled",
    "top100_auto_refresh_enabled",
    "ai_narrative_vn30", "ai_narrative_watchlist", "ai_narrative_top100",
}
_LIST_KEYS = {"crypto_exchanges"}


def _stored(session: Session) -> dict[str, str]:
    return {row.key: row.value for row in session.exec(select(Setting)).all()}


def _as_bool(raw: str) -> bool:
    return str(raw).lower() in ("true", "1", "yes")


def _typed(key: str, raw: str):
    if key in _FLOAT_KEYS:
        return float(raw)
    if key in _INT_KEYS:
        return int(raw)
    if key in _BOOL_KEYS:
        return _as_bool(raw)
    if key in _LIST_KEYS:
        return [v for v in raw.split(",") if v]
    return raw


def get_public(session: Session) -> dict:
    """Settings for the UI. The API key itself is never returned, only a flag."""
    stored = _stored(session)
    out = {key: _typed(key, stored.get(key, default)) for key, default in DEFAULTS.items()}
    out["has_anthropic_key"] = bool(stored.get(KEY_API))
    out["has_gemini_key"] = bool(stored.get(KEY_GEMINI_API))
    out["has_openai_key"] = bool(stored.get(KEY_OPENAI_API))
    return out


def _set(session: Session, key: str, value: str) -> None:
    row = session.get(Setting, key)
    if row:
        row.value = value
        session.add(row)
    else:
        session.add(Setting(key=key, value=value))


def update(session: Session, partial: dict) -> None:
    before = _stored(session)
    for key, value in partial.items():
        if key in (KEY_API, KEY_GEMINI_API, KEY_OPENAI_API):
            # Empty string clears the key; otherwise store encrypted. Never
            # log the real (encrypted) value -- only whether it's set.
            new_raw = encrypt(str(value)) if value else ""
            old_present = "(đã đặt)" if before.get(key) else "(trống)"
            new_present = "(đã đặt)" if new_raw else "(trống)"
            activity_log.log_config_change(session, key, old_present, new_present)
            _set(session, key, new_raw)
        elif key in _LIST_KEYS:
            new_raw = ",".join(value) if isinstance(value, list) else str(value)
            activity_log.log_config_change(session, key, before.get(key, DEFAULTS.get(key, "")), new_raw)
            _set(session, key, new_raw)
        elif key in DEFAULTS:
            new_raw = str(value)
            activity_log.log_config_change(session, key, before.get(key, DEFAULTS.get(key, "")), new_raw)
            _set(session, key, new_raw)
    session.commit()


def get_strategy(session: Session) -> str:
    stored = _stored(session)
    key = stored.get("strategy", DEFAULTS["strategy"])
    return key if strategy_registry.is_known(key) else DEFAULTS["strategy"]


def get_strategy_config(session: Session, strategy: str):
    """Build the strategy-specific config object -- add an ``elif`` branch
    here alongside its own get_*_config() when adding a new strategy."""
    if strategy == "sonicr":
        return get_sonicr_config(session)
    if strategy == "smc":
        return get_smc_config(session)
    return get_wyckoff_config(session)


def get_language(session: Session) -> str:
    stored = _stored(session)
    return stored.get("language", DEFAULTS["language"])


def get_narrative_config(session: Session) -> ProviderConfig:
    stored = _stored(session)
    provider = stored.get("narrative_provider", DEFAULTS["narrative_provider"])
    language = get_language(session)
    if provider == PROVIDER_OLLAMA:
        return ProviderConfig(provider=PROVIDER_OLLAMA, model=stored.get("ollama_model", ""), language=language)
    if provider == PROVIDER_ANTIGRAVITY:
        model = stored.get("antigravity_model") or DEFAULTS["antigravity_model"]
        api_key = decrypt(stored.get(KEY_GEMINI_API, "")) or get_settings().gemini_api_key
        return ProviderConfig(provider=PROVIDER_ANTIGRAVITY, model=model, api_key=api_key, language=language)
    if provider == PROVIDER_CODEX:
        model = stored.get("openai_model") or DEFAULTS["openai_model"]
        api_key = decrypt(stored.get(KEY_OPENAI_API, "")) or get_settings().openai_api_key
        return ProviderConfig(provider=PROVIDER_CODEX, model=model, api_key=api_key, language=language)
    api_key = decrypt(stored.get(KEY_API, "")) or get_settings().anthropic_api_key
    model = stored.get("anthropic_model") or get_settings().anthropic_model
    return ProviderConfig(provider=PROVIDER_ANTHROPIC, model=model, api_key=api_key, language=language)


def get_lookbacks(session: Session) -> tuple[int, int]:
    stored = _stored(session)
    return (
        int(stored.get("daily_lookback_days", DEFAULTS["daily_lookback_days"])),
        int(stored.get("half_session_lookback_days", DEFAULTS["half_session_lookback_days"])),
    )


def get_wyckoff_config(session: Session) -> WyckoffConfig:
    stored = _stored(session)

    def val(key: str) -> float:
        return float(stored.get(key, DEFAULTS[key]))

    return WyckoffConfig(
        climax_vol_mult=val("climax_vol_mult"),
        wide_spread_mult=val("wide_spread_mult"),
        narrow_spread_mult=val("narrow_spread_mult"),
        low_vol_mult=val("low_vol_mult"),
        sos_vol_mult=val("sos_vol_mult"),
        lps_lookback_bars=int(val("lps_lookback_bars")),
    )


def get_sonicr_config(session: Session) -> SonicRConfig:
    stored = _stored(session)

    def val(key: str) -> float:
        return float(stored.get(key, DEFAULTS[key]))

    return SonicRConfig(
        dragon_period=int(val("sonicr_dragon_period")),
        t3_fast_period=int(val("sonicr_t3_fast_period")),
        t3_slow_period=int(val("sonicr_t3_slow_period")),
        t3_vfactor=val("sonicr_t3_vfactor"),
        cci_fast_period=int(val("sonicr_cci_fast_period")),
        cci_slow_period=int(val("sonicr_cci_slow_period")),
        pullback_lookback_bars=int(val("sonicr_pullback_lookback_bars")),
    )


def get_smc_config(session: Session) -> SMCConfig:
    stored = _stored(session)

    def val(key: str) -> float:
        return float(stored.get(key, DEFAULTS[key]))

    return SMCConfig(
        swing_lookback=int(val("smc_swing_lookback")),
        ob_lookback_bars=int(val("smc_ob_lookback_bars")),
        fvg_min_gap_mult=val("smc_fvg_min_gap_mult"),
    )


def get_scheduler_config(session: Session) -> dict:
    stored = _stored(session)
    return {
        "enabled": _as_bool(stored.get("scheduler_enabled", DEFAULTS["scheduler_enabled"])),
        "half_morning_time": stored.get("half_morning_time", DEFAULTS["half_morning_time"]),
        "half_afternoon_time": stored.get("half_afternoon_time", DEFAULTS["half_afternoon_time"]),
        "daily_time": stored.get("daily_time", DEFAULTS["daily_time"]),
    }


def get_ai_narrative_groups(session: Session) -> dict:
    """Which tracked-symbol groups get LLM narratives during scheduled
    batches. A symbol in several groups gets AI if ANY of its groups is on."""
    stored = _stored(session)

    def flag(key: str) -> bool:
        return _as_bool(stored.get(key, DEFAULTS[key]))

    return {
        "vn30": flag("ai_narrative_vn30"),
        "watchlist": flag("ai_narrative_watchlist"),
        "top100": flag("ai_narrative_top100"),
    }


def get_top100_config(session: Session) -> dict:
    stored = _stored(session)
    return {
        "enabled": _as_bool(stored.get("top100_auto_refresh_enabled", DEFAULTS["top100_auto_refresh_enabled"])),
        "time": stored.get("top100_refresh_time", DEFAULTS["top100_refresh_time"]),
    }


def get_screener_config(session: Session) -> dict:
    stored = _stored(session)
    return {
        "enabled": _as_bool(stored.get("screener_enabled", DEFAULTS["screener_enabled"])),
        "mcap_max": float(stored.get("screener_mcap_max", DEFAULTS["screener_mcap_max"])),
        "require_volume_rising": _as_bool(
            stored.get("screener_require_volume_rising", DEFAULTS["screener_require_volume_rising"])
        ),
        "min_volume_change_pct": float(
            stored.get("screener_min_volume_change_pct", DEFAULTS["screener_min_volume_change_pct"])
        ),
        "scan_interval": stored.get("screener_scan_interval", DEFAULTS["screener_scan_interval"]),
    }


def get_crypto_exchanges(session: Session) -> tuple[str, ...]:
    stored = _stored(session)
    raw = stored.get("crypto_exchanges", DEFAULTS["crypto_exchanges"])
    exchanges = tuple(v for v in raw.split(",") if v in CRYPTO_EXCHANGE_CHOICES)
    return exchanges or CryptoExchange.ALL


def get_crypto_analysis_config(session: Session) -> dict:
    stored = _stored(session)
    return {
        "enabled": _as_bool(stored.get("crypto_analysis_enabled", DEFAULTS["crypto_analysis_enabled"])),
        "interval": stored.get("crypto_analysis_interval", DEFAULTS["crypto_analysis_interval"]),
    }

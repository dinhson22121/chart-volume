"""User-configurable settings: Anthropic key/model, crawl lookback, scheduler
cadence, Wyckoff detector thresholds.

The Anthropic API key is write-only from the client's perspective: GET never
returns it, only a ``has_anthropic_key`` flag. Updating scheduler settings
reschedules the running APScheduler jobs immediately (no restart needed).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlmodel import Session

from app.auth import require_token
from app.db import get_session
from app.scheduler import reschedule
from app.services import settings_service
from app.strategies import registry as strategy_registry

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(require_token)])

_SCHEDULER_KEYS = {
    "scheduler_enabled", "half_morning_time", "half_afternoon_time", "daily_time",
    "screener_enabled", "screener_scan_interval",
    "crypto_analysis_enabled", "crypto_analysis_interval",
    "top100_auto_refresh_enabled", "top100_refresh_time",
}


class SettingsIn(BaseModel):
    language: str | None = Field(default=None, pattern="^(vi|en)$")
    strategy: str | None = None
    narrative_provider: str | None = Field(default=None, pattern="^(anthropic|ollama|antigravity|codex)$")
    anthropic_api_key: str | None = Field(default=None, description="Empty string clears the key")
    anthropic_model: str | None = None
    ollama_model: str | None = None
    antigravity_model: str | None = None
    gemini_api_key: str | None = Field(default=None, description="Empty string clears the key")
    openai_api_key: str | None = Field(default=None, description="Empty string clears the key")
    openai_model: str | None = None
    daily_lookback_days: int | None = Field(default=None, ge=30, le=3650)
    half_session_lookback_days: int | None = Field(default=None, ge=1, le=365)
    scheduler_enabled: bool | None = None
    half_morning_time: str | None = None
    half_afternoon_time: str | None = None
    daily_time: str | None = None
    climax_vol_mult: float | None = Field(default=None, gt=0)
    wide_spread_mult: float | None = Field(default=None, gt=0)
    narrow_spread_mult: float | None = Field(default=None, gt=0)
    low_vol_mult: float | None = Field(default=None, gt=0)
    sos_vol_mult: float | None = Field(default=None, gt=0)
    lps_lookback_bars: int | None = Field(default=None, ge=2, le=60)
    sonicr_dragon_period: int | None = Field(default=None, ge=2, le=200)
    sonicr_t3_fast_period: int | None = Field(default=None, ge=2, le=100)
    sonicr_t3_slow_period: int | None = Field(default=None, ge=2, le=100)
    sonicr_t3_vfactor: float | None = Field(default=None, gt=0, le=1)
    sonicr_cci_fast_period: int | None = Field(default=None, ge=2, le=100)
    sonicr_cci_slow_period: int | None = Field(default=None, ge=2, le=100)
    sonicr_pullback_lookback_bars: int | None = Field(default=None, ge=2, le=60)
    smc_swing_lookback: int | None = Field(default=None, ge=1, le=10)
    smc_ob_lookback_bars: int | None = Field(default=None, ge=2, le=30)
    smc_fvg_min_gap_mult: float | None = Field(default=None, gt=0)
    screener_enabled: bool | None = None
    screener_mcap_max: float | None = None
    screener_require_volume_rising: bool | None = None
    screener_min_volume_change_pct: float | None = Field(default=None, gt=0)
    screener_scan_interval: str | None = None
    crypto_exchanges: list[str] | None = None
    crypto_analysis_enabled: bool | None = None
    crypto_analysis_interval: str | None = None
    top100_auto_refresh_enabled: bool | None = None
    top100_refresh_time: str | None = None
    ai_narrative_vn30: bool | None = None
    ai_narrative_watchlist: bool | None = None
    ai_narrative_top100: bool | None = None

    @field_validator("strategy")
    @classmethod
    def _validate_strategy(cls, value: str | None) -> str | None:
        if value is not None and not strategy_registry.is_known(value):
            raise ValueError(f"unknown strategy: {value}")
        return value

    @field_validator("screener_mcap_max")
    @classmethod
    def _validate_mcap_max(cls, value: float | None) -> float | None:
        if value is not None and value not in settings_service.SCREENER_MCAP_CHOICES:
            raise ValueError(f"screener_mcap_max must be one of {settings_service.SCREENER_MCAP_CHOICES}")
        return value

    @field_validator("screener_scan_interval")
    @classmethod
    def _validate_scan_interval(cls, value: str | None) -> str | None:
        if value is not None and value not in settings_service.SCREENER_INTERVAL_CHOICES:
            raise ValueError(
                f"screener_scan_interval must be one of {settings_service.SCREENER_INTERVAL_CHOICES}"
            )
        return value

    @field_validator("crypto_exchanges")
    @classmethod
    def _validate_crypto_exchanges(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        if not value:
            raise ValueError("crypto_exchanges must not be empty")
        unknown = [v for v in value if v not in settings_service.CRYPTO_EXCHANGE_CHOICES]
        if unknown:
            raise ValueError(
                f"unknown crypto exchange(s) {unknown}; must be one of {settings_service.CRYPTO_EXCHANGE_CHOICES}"
            )
        return value

    @field_validator("crypto_analysis_interval")
    @classmethod
    def _validate_crypto_analysis_interval(cls, value: str | None) -> str | None:
        if value is not None and value not in settings_service.CRYPTO_ANALYSIS_INTERVAL_CHOICES:
            raise ValueError(
                f"crypto_analysis_interval must be one of {settings_service.CRYPTO_ANALYSIS_INTERVAL_CHOICES}"
            )
        return value


@router.get("")
def get_settings_view(session: Session = Depends(get_session)) -> dict:
    return settings_service.get_public(session)


@router.put("")
def update_settings(
    payload: SettingsIn, request: Request, session: Session = Depends(get_session)
) -> dict:
    partial = payload.model_dump(
        exclude_none=True, exclude={"anthropic_api_key", "gemini_api_key", "openai_api_key"}
    )
    if payload.anthropic_api_key is not None:
        partial["anthropic_api_key"] = payload.anthropic_api_key
    if payload.gemini_api_key is not None:
        partial["gemini_api_key"] = payload.gemini_api_key
    if payload.openai_api_key is not None:
        partial["openai_api_key"] = payload.openai_api_key

    settings_service.update(session, partial)

    if _SCHEDULER_KEYS & partial.keys():
        scheduler = getattr(request.app.state, "scheduler", None)
        if scheduler is not None:
            reschedule(scheduler)

    return settings_service.get_public(session)

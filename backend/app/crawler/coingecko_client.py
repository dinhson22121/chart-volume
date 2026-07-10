"""Thin wrapper over CoinGecko's free public API, with rate-limit backoff.

No API key needed for the public tier, but it's aggressively rate-limited
(observed empirically: a 429 after ~2 requests, resetting in roughly 20-45s).
Callers should expect a full market scan to take minutes, not seconds --
see app.services.crypto_screener, which paces requests deliberately.
"""

from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger("chart_volume.crawler")

BASE_URL = "https://api.coingecko.com/api/v3"
_MAX_RETRIES = 5
_RATE_LIMIT_BACKOFF = 30.0  # seconds to wait after a 429 before retrying
_TIMEOUT = 15.0
PER_PAGE = 250


class CrawlError(RuntimeError):
    """Raised when a CoinGecko request fails after all retries."""


def _get(path: str, params: dict) -> list | dict:
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = httpx.get(f"{BASE_URL}{path}", params=params, timeout=_TIMEOUT)
            if resp.status_code == 429:
                logger.warning(
                    "coingecko rate-limited (attempt %d/%d), backing off %.0fs",
                    attempt, _MAX_RETRIES, _RATE_LIMIT_BACKOFF,
                )
                time.sleep(_RATE_LIMIT_BACKOFF)
                continue
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            last_exc = exc
            logger.warning("coingecko %s attempt %d/%d failed: %s", path, attempt, _MAX_RETRIES, exc)
            time.sleep(2.0 * attempt)
    raise CrawlError(f"coingecko {path} failed after {_MAX_RETRIES} attempts: {last_exc}") from last_exc


def fetch_markets_page(page: int, order: str = "market_cap_desc") -> list[dict]:
    """One page of up to 250 coins with market_cap + total_volume (24h)."""
    data = _get(
        "/coins/markets",
        {"vs_currency": "usd", "order": order, "per_page": PER_PAGE, "page": page, "sparkline": "false"},
    )
    return data or []


def fetch_coin_platforms(coin_id: str) -> dict[str, str]:
    """Contract address per chain (CoinGecko's own platform ids, e.g.
    "ethereum", "binance-smart-chain") for a coin -- used to resolve a
    GeckoTerminal pool when the coin isn't listed on Binance/KuCoin."""
    data = _get(
        f"/coins/{coin_id}",
        {
            "localization": "false", "tickers": "false", "market_data": "false",
            "community_data": "false", "developer_data": "false",
        },
    )
    return data.get("platforms") or {}

"""Thin wrapper over GeckoTerminal's free public API (DEX pool data).

Two roles: (1) discovery -- new_pools/trending_pools list freshly-created or
hot DEX pools across every chain in one call, catching coins CoinGecko hasn't
indexed yet; (2) candle fallback -- OHLCV for a specific pool, for coins that
have a CoinGecko listing but aren't on Binance/KuCoin.

Verified against the live API before writing this (not assumed from docs):
- Rate limit is as aggressive as CoinGecko's: a 429 after ~2-3 requests in
  quick succession. Same backoff-and-retry shape as coingecko_client.
- Network ids differ from CoinGecko's platform ids (see NETWORK_ID_MAP).
- OHLCV needs the *pool* address, not the token address -- a token can have
  many pools; fetch_token_pools() picks the one with the most liquidity.
- new_pools/trending_pools need ``include=base_token`` to get symbol/name
  back at all; otherwise pools only reference opaque token ids.
- ohlcv_list rows come back newest-first, same as KuCoin -- must be re-sorted.
"""

from __future__ import annotations

import logging
import re
import time

import httpx
import pandas as pd

logger = logging.getLogger("chart_volume.crawler")

BASE_URL = "https://api.geckoterminal.com/api/v2"
_MAX_RETRIES = 5
_RATE_LIMIT_BACKOFF = 30.0
_TIMEOUT = 15.0

_COLUMNS = ["time", "open", "high", "low", "close", "volume"]

# CoinGecko `platforms` key -> GeckoTerminal network id. Best-effort: chains
# not listed here are simply skipped rather than exhaustively mapped (~200
# GeckoTerminal networks exist; only the common ones are worth the upkeep).
NETWORK_ID_MAP = {
    "ethereum": "eth",
    "binance-smart-chain": "bsc",
    "polygon-pos": "polygon_pos",
    "arbitrum-one": "arbitrum",
    "avalanche": "avax",
    "base": "base",
    "solana": "solana",
    "optimistic-ethereum": "optimism",
}

# Our internal timeframe names -> GeckoTerminal's {unit}?aggregate=N.
_TIMEFRAME_MAP = {
    "1h": ("hour", 1),
    "4h": ("hour", 4),
    "daily": ("day", 1),
    "1d": ("day", 1),
}

# token_address comes from CoinGecko's `platforms` response (see
# coingecko_client.fetch_coin_platforms), not user input -- but it's still
# spliced into a URL path unvalidated, so a malformed/unexpected value would
# otherwise reach GeckoTerminal as-is. Validate against the expected address
# shape per chain family before building any request.
_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def _is_valid_token_address(network: str, address: str) -> bool:
    if network == "solana":
        return bool(_SOLANA_ADDRESS_RE.match(address))
    return bool(_EVM_ADDRESS_RE.match(address))


class CrawlError(RuntimeError):
    """Raised when a GeckoTerminal request fails after all retries."""


def _get(path: str, params: dict | None = None) -> dict:
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = httpx.get(f"{BASE_URL}{path}", params=params or {}, timeout=_TIMEOUT)
            if resp.status_code == 429:
                logger.warning(
                    "geckoterminal rate-limited (attempt %d/%d), backing off %.0fs",
                    attempt, _MAX_RETRIES, _RATE_LIMIT_BACKOFF,
                )
                time.sleep(_RATE_LIMIT_BACKOFF)
                continue
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            last_exc = exc
            logger.warning("geckoterminal %s attempt %d/%d failed: %s", path, attempt, _MAX_RETRIES, exc)
            time.sleep(2.0 * attempt)
    raise CrawlError(f"geckoterminal {path} failed after {_MAX_RETRIES} attempts: {last_exc}") from last_exc


def _normalize_pools(body: dict) -> list[dict]:
    tokens_by_id = {
        item["id"]: item["attributes"]
        for item in body.get("included", [])
        if item.get("type") == "token"
    }
    out = []
    for pool in body.get("data", []):
        attrs = pool["attributes"]
        rel = pool.get("relationships", {})
        network = rel.get("network", {}).get("data", {}).get("id")
        base_token_id = rel.get("base_token", {}).get("data", {}).get("id")
        token = tokens_by_id.get(base_token_id, {})
        market_cap = attrs.get("market_cap_usd") or attrs.get("fdv_usd")
        volume_24h = (attrs.get("volume_usd") or {}).get("h24")
        if not network or not token.get("address") or market_cap is None:
            continue
        out.append(
            {
                "network": network,
                "pool_address": attrs["address"],
                "symbol": token.get("symbol", ""),
                "name": token.get("name", ""),
                "coingecko_coin_id": token.get("coingecko_coin_id"),
                "market_cap": float(market_cap),
                "volume_24h": float(volume_24h) if volume_24h is not None else 0.0,
            }
        )
    return out


def fetch_new_pools(page: int = 1) -> list[dict]:
    """Newest DEX pools across all networks -- catches coins before CoinGecko
    indexes them. Normalized shape matches fetch_trending_pools()."""
    body = _get("/networks/new_pools", {"page": page, "include": "base_token"})
    return _normalize_pools(body)


def fetch_trending_pools(page: int = 1) -> list[dict]:
    """Currently-hot DEX pools across all networks."""
    body = _get("/networks/trending_pools", {"page": page, "include": "base_token"})
    return _normalize_pools(body)


def fetch_token_pools(network: str, token_address: str) -> str | None:
    """Address of the most liquid pool for a token on ``network``, or None if
    the token has no pool there (i.e. not on any DEX we can query)."""
    if not _is_valid_token_address(network, token_address):
        logger.warning("rejecting malformed token address for %s: %r", network, token_address)
        return None
    body = _get(f"/networks/{network}/tokens/{token_address}/pools")
    pools = body.get("data", [])
    if not pools:
        return None
    best = max(pools, key=lambda p: float(p["attributes"].get("reserve_in_usd") or 0))
    return best["attributes"]["address"]


def fetch_ohlcv(network: str, pool_address: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
    """OHLCV candles for a specific pool. ``timeframe`` is one of our internal
    names ("1h"/"4h"/"daily"/"1d")."""
    unit, aggregate = _TIMEFRAME_MAP.get(timeframe, ("day", 1))
    body = _get(
        f"/networks/{network}/pools/{pool_address}/ohlcv/{unit}",
        {"aggregate": aggregate, "limit": limit},
    )
    rows = body.get("data", {}).get("attributes", {}).get("ohlcv_list", [])
    df = pd.DataFrame(
        [
            {
                "time": pd.to_datetime(int(row[0]), unit="s"),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            }
            for row in rows
        ],
        columns=_COLUMNS,
    )
    return df.sort_values("time").reset_index(drop=True)

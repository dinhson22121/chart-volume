"""Thin wrapper over Binance's public klines REST API, with retry + graceful failure.

Binance's spot market API is public and free (no API key needed) for
historical candle data. A coin not listed on Binance returns a clean
"Invalid symbol" error, surfaced as SymbolNotFoundError so callers can treat
it as "no candle source" rather than retrying a request that will never
succeed.
"""

from __future__ import annotations

import logging
import time

import httpx
import pandas as pd

logger = logging.getLogger("chart_volume.crawler")

BASE_URL = "https://api.binance.com"
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0
_TIMEOUT = 15.0
_INVALID_SYMBOL_CODE = -1121

_COLUMNS = ["time", "open", "high", "low", "close", "volume"]


class CrawlError(RuntimeError):
    """Raised when a crawl fails after all retries (transient/network issue)."""


class SymbolNotFoundError(RuntimeError):
    """Raised when Binance has no such trading pair -- not retryable."""


def to_pair(coin_symbol: str, quote: str = "USDT") -> str:
    """Best-effort guess at a Binance trading pair from a CoinGecko coin symbol.

    Not always correct -- ticker symbols collide across unrelated coins in
    crypto -- but a reasonable default absent a verified exchange mapping.
    """
    return f"{coin_symbol.upper()}{quote}"


def fetch_tradeable_symbols(quote: str = "USDT") -> set[str]:
    """Base-asset symbols (e.g. "BTC") with an actively trading pair against
    ``quote`` on Binance. One call for the whole exchange, used to filter the
    screener's candidates down to coins we can actually chart -- cheaper than
    probing each coin's klines endpoint individually."""
    resp = httpx.get(f"{BASE_URL}/api/v3/exchangeInfo", timeout=_TIMEOUT)
    resp.raise_for_status()
    return {
        s["baseAsset"].upper()
        for s in resp.json().get("symbols", [])
        if s.get("quoteAsset") == quote and s.get("status") == "TRADING"
    }


def fetch_klines(symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
    """OHLCV candles for a Binance spot pair (e.g. "BTCUSDT").

    ``interval`` is a Binance interval string: "1h", "4h", or "1d".
    """
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = httpx.get(
                f"{BASE_URL}/api/v3/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit},
                timeout=_TIMEOUT,
            )
            if resp.status_code == 400:
                body = resp.json()
                if body.get("code") == _INVALID_SYMBOL_CODE:
                    raise SymbolNotFoundError(f"{symbol} is not listed on Binance")
            resp.raise_for_status()
            rows = resp.json()
            return pd.DataFrame(
                [
                    {
                        "time": pd.to_datetime(row[0], unit="ms"),
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
        except SymbolNotFoundError:
            raise  # not retryable, propagate immediately
        except Exception as exc:  # noqa: BLE001 - any transient failure is retryable
            last_exc = exc
            logger.warning(
                "binance klines %s/%s attempt %d/%d failed: %s",
                symbol, interval, attempt, _MAX_RETRIES, exc,
            )
            time.sleep(_RETRY_BASE_DELAY * attempt)
    raise CrawlError(
        f"binance klines {symbol}/{interval} failed after {_MAX_RETRIES} attempts: {last_exc}"
    ) from last_exc

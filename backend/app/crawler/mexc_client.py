"""Thin wrapper over MEXC's public klines REST API, with retry + graceful failure.

MEXC's spot API is Binance-compatible in shape (same column order, same
-1121 "Invalid symbol"/"Invalid interval" error code), but verified live
against the real API before writing this (not assumed):

- Interval strings differ for the hourly candle: MEXC rejects "1h" outright
  (-1121 Invalid interval) and wants "60m" instead. "4h" and "1d" work as-is.
- exchangeInfo's per-symbol ``status`` is a numeric string ("1"/"2"), not
  Binance's "TRADING" -- use ``isSpotTradingAllowed`` instead, which is an
  unambiguous boolean and lines up with "1" in every sample checked.
- MEXC covers ~1668 USDT pairs (more than KuCoin's ~861), useful as another
  fallback before giving up and trying GeckoTerminal (DEX).
"""

from __future__ import annotations

import logging
import time

import httpx
import pandas as pd

logger = logging.getLogger("chart_volume.crawler")

BASE_URL = "https://api.mexc.com"
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0
_TIMEOUT = 15.0
_INVALID_SYMBOL_CODE = -1121

_COLUMNS = ["time", "open", "high", "low", "close", "volume"]

# Our internal interval names -> MEXC's own interval strings.
_INTERVAL_MAP = {"1h": "60m", "4h": "4h", "daily": "1d", "1d": "1d"}


class CrawlError(RuntimeError):
    """Raised when a crawl fails after all retries (transient/network issue)."""


class SymbolNotFoundError(RuntimeError):
    """Raised when MEXC has no such trading pair -- not retryable."""


def to_pair(coin_symbol: str, quote: str = "USDT") -> str:
    """Best-effort guess at a MEXC trading pair from a CoinGecko coin symbol."""
    return f"{coin_symbol.upper()}{quote}"


def fetch_tradeable_symbols(quote: str = "USDT") -> set[str]:
    """Base-asset symbols (e.g. "BTC") with an actively trading pair against
    ``quote`` on MEXC. One call for the whole exchange, used to filter the
    screener's candidates down to coins we can actually chart."""
    resp = httpx.get(f"{BASE_URL}/api/v3/exchangeInfo", timeout=_TIMEOUT)
    resp.raise_for_status()
    return {
        s["baseAsset"].upper()
        for s in resp.json().get("symbols", [])
        if s.get("quoteAsset") == quote and s.get("isSpotTradingAllowed")
    }


def fetch_klines(symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
    """OHLCV candles for a MEXC spot pair (e.g. "BTCUSDT").

    ``interval`` accepts either our internal names ("1h"/"4h"/"daily") or
    MEXC's own interval strings directly.
    """
    mexc_interval = _INTERVAL_MAP.get(interval, interval)
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = httpx.get(
                f"{BASE_URL}/api/v3/klines",
                params={"symbol": symbol, "interval": mexc_interval, "limit": limit},
                timeout=_TIMEOUT,
            )
            if resp.status_code == 400:
                body = resp.json()
                if body.get("code") == _INVALID_SYMBOL_CODE:
                    raise SymbolNotFoundError(f"{symbol} is not listed on MEXC")
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
                "mexc klines %s/%s attempt %d/%d failed: %s",
                symbol, interval, attempt, _MAX_RETRIES, exc,
            )
            time.sleep(_RETRY_BASE_DELAY * attempt)
    raise CrawlError(
        f"mexc klines {symbol}/{interval} failed after {_MAX_RETRIES} attempts: {last_exc}"
    ) from last_exc

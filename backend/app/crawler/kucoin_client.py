"""Thin wrapper over KuCoin's public klines REST API, with retry + graceful failure.

KuCoin lists many small/new coins earlier than Binance, so it's the fallback
source when a screener candidate isn't on Binance. Two gotchas verified
against the live API (not assumed from docs):

- Errors come back as HTTP 200 with a non-"200000" ``code`` field in the JSON
  body, not as an HTTP error status -- must be checked explicitly.
- Column order is [time, open, close, high, low, volume, turnover] (close
  before high/low), unlike Binance's [time, open, high, low, close, volume].
  Rows are also returned most-recent-first and need reversing.
"""

from __future__ import annotations

import logging
import time

import httpx
import pandas as pd

logger = logging.getLogger("chart_volume.crawler")

BASE_URL = "https://api.kucoin.com"
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0
_TIMEOUT = 15.0
_OK_CODE = "200000"

_COLUMNS = ["time", "open", "high", "low", "close", "volume"]

# Our internal interval names -> KuCoin's `type` query values.
_INTERVAL_MAP = {"1h": "1hour", "4h": "4hour", "daily": "1day", "1d": "1day"}


class CrawlError(RuntimeError):
    """Raised when a crawl fails after all retries (transient/network issue)."""


class SymbolNotFoundError(RuntimeError):
    """Raised when KuCoin has no such trading pair -- not retryable."""


def to_pair(coin_symbol: str, quote: str = "USDT") -> str:
    """Best-effort guess at a KuCoin trading pair from a CoinGecko coin symbol."""
    return f"{coin_symbol.upper()}-{quote}"


def fetch_tradeable_symbols(quote: str = "USDT") -> set[str]:
    """Base-currency symbols (e.g. "BTC") with an actively trading pair against
    ``quote`` on KuCoin. One call for the whole exchange, used to filter the
    screener's candidates down to coins we can actually chart -- cheaper than
    probing each coin's klines endpoint individually."""
    resp = httpx.get(f"{BASE_URL}/api/v1/symbols", timeout=_TIMEOUT)
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != _OK_CODE:
        raise CrawlError(f"kucoin symbols: {body.get('msg', body.get('code'))}")
    return {
        s["baseCurrency"].upper()
        for s in body.get("data", [])
        if s.get("quoteCurrency") == quote and s.get("enableTrading")
    }


def fetch_klines(symbol: str, interval: str) -> pd.DataFrame:
    """OHLCV candles for a KuCoin spot pair (e.g. "BTC-USDT").

    ``interval`` accepts either our internal names ("1h"/"4h"/"daily") or
    KuCoin's own type strings directly.
    """
    kucoin_type = _INTERVAL_MAP.get(interval, interval)
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = httpx.get(
                f"{BASE_URL}/api/v1/market/candles",
                params={"symbol": symbol, "type": kucoin_type},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("code") != _OK_CODE:
                raise SymbolNotFoundError(f"{symbol}: {body.get('msg', body.get('code'))}")
            rows = body.get("data") or []
            df = pd.DataFrame(
                [
                    {
                        "time": pd.to_datetime(int(row[0]), unit="s"),
                        "open": float(row[1]),
                        "high": float(row[3]),
                        "low": float(row[4]),
                        "close": float(row[2]),
                        "volume": float(row[5]),
                    }
                    for row in rows
                ],
                columns=_COLUMNS,
            )
            return df.sort_values("time").reset_index(drop=True)
        except SymbolNotFoundError:
            raise  # not retryable, propagate immediately
        except Exception as exc:  # noqa: BLE001 - any transient failure is retryable
            last_exc = exc
            logger.warning(
                "kucoin klines %s/%s attempt %d/%d failed: %s",
                symbol, interval, attempt, _MAX_RETRIES, exc,
            )
            time.sleep(_RETRY_BASE_DELAY * attempt)
    raise CrawlError(
        f"kucoin klines {symbol}/{interval} failed after {_MAX_RETRIES} attempts: {last_exc}"
    ) from last_exc

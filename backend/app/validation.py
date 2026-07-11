"""Shared input-validation helpers used across API routes."""

from __future__ import annotations

import re

# Ticker / coin-id / display-symbol identifiers: letters, digits, underscore,
# hyphen, colon and period only -- covers plain tickers (FPT, PEPE), CoinGecko
# slugs (based-pepe, pepecoin-on-solana), and synthesized GeckoTerminal ids
# (gt:solana:<address>, gt:eth:0x...). Deliberately excludes whitespace and
# other punctuation so a free-text prompt-injection payload (e.g. a malicious
# DEX token's self-reported symbol) can't pass through as a "ticker" -- see
# app.ai.narrative.build_prompt, which interpolates this value directly into
# the LLM prompt.
TICKER_PATTERN = re.compile(r"^[A-Za-z0-9_:.-]{1,64}$")


def is_valid_ticker(value: str) -> bool:
    return bool(TICKER_PATTERN.match(value))

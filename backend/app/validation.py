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


# Plain "name" or "name:tag" only -- no "/" anywhere. Ollama's API lets a model
# string embed an alternate registry host as a leading path segment (e.g.
# "some-host.example/library/model"), which would let a caller direct the
# user's own local Ollama daemon to fetch from (or query) a host of their
# choosing. None of the models this app suggests or supports need a
# namespace/host prefix, so it's simplest and safest to disallow "/" outright.
# Shared by app.api.ollama (pull) and app.api.settings (the ollama_model
# setting used for actual generation) -- both interpolate the value into an
# Ollama API request, so both need the same guard.
OLLAMA_MODEL_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}(:[a-zA-Z0-9._-]{1,20})?$")


def is_valid_ollama_model(value: str) -> bool:
    return bool(OLLAMA_MODEL_PATTERN.match(value))

"""Watchlist + VN30 symbol management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlmodel import Session, select

from app.auth import require_token
from app.crawler.vnstock_client import fetch_vn30
from app.db import get_session
from app.models import AssetClass, Symbol
from app.services import activity_log
from app.validation import is_valid_ticker

router = APIRouter(prefix="/symbols", tags=["symbols"], dependencies=[Depends(require_token)])

_VALID_ASSET_CLASSES = {AssetClass.STOCK, AssetClass.CRYPTO}


class SymbolIn(BaseModel):
    ticker: str
    name: str = ""
    asset_class: str = AssetClass.STOCK

    @field_validator("ticker")
    @classmethod
    def _validate_ticker(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not is_valid_ticker(cleaned):
            raise ValueError(
                "ticker must be 1-64 characters: letters, digits, '_', '-', ':', '.' only"
            )
        return cleaned

    @field_validator("asset_class")
    @classmethod
    def _validate_asset_class(cls, value: str) -> str:
        if value not in _VALID_ASSET_CLASSES:
            raise ValueError(f"unknown asset_class: {value}")
        return value


@router.get("")
def list_symbols(session: Session = Depends(get_session)) -> list[Symbol]:
    return session.exec(select(Symbol).order_by(Symbol.ticker)).all()


@router.post("")
def add_symbol(payload: SymbolIn, session: Session = Depends(get_session)) -> Symbol:
    ticker = payload.ticker  # already validated + normalized by SymbolIn._validate_ticker
    existing = session.get(Symbol, ticker)
    if existing:
        existing.is_watchlist = True
        if payload.name:
            existing.name = payload.name
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing
    symbol = Symbol(
        ticker=ticker, name=payload.name, display_symbol=ticker,
        asset_class=payload.asset_class, is_watchlist=True,
    )
    session.add(symbol)
    session.commit()
    session.refresh(symbol)
    return symbol


@router.delete("/{ticker}")
def remove_symbol(ticker: str, session: Session = Depends(get_session)) -> dict[str, str]:
    ticker = ticker.upper()
    symbol = session.get(Symbol, ticker)
    if not symbol:
        raise HTTPException(status_code=404, detail="symbol not found")
    # VN30 members stay (index membership), only drop the watchlist flag.
    if symbol.is_vn30:
        symbol.is_watchlist = False
        session.add(symbol)
    else:
        session.delete(symbol)
    session.commit()
    return {"status": "removed", "ticker": ticker}


@router.post("/seed-vn30")
def seed_vn30(session: Session = Depends(get_session)) -> dict:
    """Seeds/refreshes VN30 membership. ``source`` tells the UI whether this
    was live data or the static fallback list (see fetch_vn30), the same way
    the crypto screener surfaces its own last-run status instead of silently
    treating a degraded result as if it were fully fresh."""
    log_id = activity_log.log_action_start(session, "vn30_seed", "manual")
    tickers, source = fetch_vn30()
    for ticker in tickers:
        symbol = session.get(Symbol, ticker)
        if symbol:
            symbol.is_vn30 = True
            session.add(symbol)
        else:
            session.add(Symbol(ticker=ticker, display_symbol=ticker, is_vn30=True))
    session.commit()
    activity_log.log_action_finish(session, log_id, "success", f"{len(tickers)} mã ({source})")
    return {"count": len(tickers), "source": source}

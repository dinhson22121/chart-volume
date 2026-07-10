"""Watchlist + VN30 symbol management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlmodel import Session, select

from app.auth import require_token
from app.crawler.vnstock_client import fetch_vn30
from app.db import get_session
from app.models import AssetClass, Symbol

router = APIRouter(prefix="/symbols", tags=["symbols"], dependencies=[Depends(require_token)])

_VALID_ASSET_CLASSES = {AssetClass.STOCK, AssetClass.CRYPTO}


class SymbolIn(BaseModel):
    ticker: str
    name: str = ""
    asset_class: str = AssetClass.STOCK

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
    ticker = payload.ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker is required")
    existing = session.get(Symbol, ticker)
    if existing:
        existing.is_watchlist = True
        if payload.name:
            existing.name = payload.name
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing
    symbol = Symbol(ticker=ticker, name=payload.name, asset_class=payload.asset_class, is_watchlist=True)
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
def seed_vn30(session: Session = Depends(get_session)) -> dict[str, int]:
    tickers = fetch_vn30()
    for ticker in tickers:
        symbol = session.get(Symbol, ticker)
        if symbol:
            symbol.is_vn30 = True
            session.add(symbol)
        else:
            session.add(Symbol(ticker=ticker, is_vn30=True))
    session.commit()
    return {"count": len(tickers)}

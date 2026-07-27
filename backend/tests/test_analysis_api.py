import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.api.analysis import _actionable_signals
from app.db import get_session
from app.main import app
from app.models import Analysis, Timeframe


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


# --- _actionable_signals: mirrors trade_scenario._create_scenarios' own
# --- qualifying filter -- bullish only, continuation types excluded.

def test_actionable_signals_keeps_a_plain_bullish_entry_signal():
    signals = [{"type": "Spring", "ts": "2025-01-01T00:00:00", "price": 100.0}]
    assert _actionable_signals(signals, "wyckoff") == signals


def test_actionable_signals_drops_bearish_signals():
    # Spot trading has no short-selling -- a bearish signal never spawns a
    # TradeScenario, so it shouldn't headline the "what can I act on" panel.
    signals = [{"type": "SOW", "ts": "2025-01-01T00:00:00", "price": 100.0}]
    assert _actionable_signals(signals, "wyckoff") == []


def test_actionable_signals_drops_continuation_types_despite_being_bullish():
    # NoSupply IS in Wyckoff's own BULLISH_EVENTS (phase classification), but
    # trade_scenario.CONTINUATION_EVENT_TYPES excludes it from scenario
    # creation -- it's a trend-confirmation signal, not a new entry.
    signals = [{"type": "NoSupply", "ts": "2025-01-01T00:00:00", "price": 100.0}]
    assert _actionable_signals(signals, "wyckoff") == []


def test_actionable_signals_mixed_list_keeps_only_the_qualifying_ones():
    signals = [
        {"type": "Spring", "ts": "2025-01-01T00:00:00", "price": 100.0},
        {"type": "NoSupply", "ts": "2025-01-02T00:00:00", "price": 101.0},
        {"type": "SOW", "ts": "2025-01-03T00:00:00", "price": 99.0},
        {"type": "SOS", "ts": "2025-01-04T00:00:00", "price": 102.0},
    ]
    result = _actionable_signals(signals, "wyckoff")
    assert [s["type"] for s in result] == ["Spring", "SOS"]


def test_get_analysis_response_includes_filtered_actionable_signals(client, session, auth_header):
    signals = [
        {"type": "Spring", "ts": "2025-01-01T00:00:00", "price": 100.0, "note": "", "volume_confirmed": True},
        {"type": "NoDemand", "ts": "2025-01-02T00:00:00", "price": 101.0, "note": "", "volume_confirmed": None},
        {"type": "SOW", "ts": "2025-01-03T00:00:00", "price": 99.0, "note": "", "volume_confirmed": True},
    ]
    session.add(
        Analysis(
            ticker="FPT",
            timeframe=Timeframe.DAILY,
            strategy="wyckoff",
            as_of=datetime(2025, 1, 3),
            phase="Ranging",
            confidence=0.5,
            signals_json=json.dumps(signals),
            levels_json=json.dumps({"support": 90.0, "resistance": 110.0}),
        )
    )
    session.commit()

    resp = client.get("/analysis/FPT", headers=auth_header)

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["signals"]) == 3  # full list untouched (chart markers, signal_outcomes)
    assert [s["type"] for s in body["actionable_signals"]] == ["Spring"]

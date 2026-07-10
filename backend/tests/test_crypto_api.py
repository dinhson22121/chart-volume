import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app
from app.models import AssetClass, ScreenerCandidate, Symbol
from app.services import crypto_screener


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _reset_scan_state():
    # crypto_screener's scan lock/state is module-level global; make sure one
    # test's scan status never leaks into the next.
    yield
    crypto_screener._scan_state.update(
        {
            "running": False,
            "last_completed_at": None,
            "last_hits": None,
            "last_error": None,
            "last_cancelled": False,
        }
    )
    crypto_screener._cancel_requested.clear()
    if crypto_screener._scan_lock.locked():
        crypto_screener._scan_lock.release()


def test_trigger_scan_requires_token(client):
    assert client.post("/crypto/screener/scan").status_code == 401


def test_trigger_scan_starts_background_task(client, auth_header, mocker):
    mocker.patch.object(crypto_screener, "run_scan_guarded", return_value={"running": False})

    resp = client.post("/crypto/screener/scan", headers=auth_header)

    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


def test_trigger_scan_reports_already_running(client, auth_header):
    crypto_screener._scan_state["running"] = True

    resp = client.post("/crypto/screener/scan", headers=auth_header)

    assert resp.json()["status"] == "already_running"


def test_get_scan_status(client, auth_header):
    resp = client.get("/crypto/screener/status", headers=auth_header)
    assert resp.status_code == 200
    assert resp.json()["running"] is False


def test_cancel_scan_requires_token(client):
    assert client.post("/crypto/screener/cancel").status_code == 401


def test_cancel_scan_sets_flag_while_running(client, auth_header):
    crypto_screener._scan_state["running"] = True

    resp = client.post("/crypto/screener/cancel", headers=auth_header)

    assert resp.status_code == 200
    assert crypto_screener._cancel_requested.is_set()


def test_cancel_scan_is_noop_when_not_running(client, auth_header):
    resp = client.post("/crypto/screener/cancel", headers=auth_header)

    assert resp.status_code == 200
    assert not crypto_screener._cancel_requested.is_set()


def test_get_candidates_lists_screener_hits(session, client, auth_header):
    session.add(
        ScreenerCandidate(
            coin_id="altcoin-x", symbol="altx", name="Alt X", market_cap=20_000_000,
            volume_24h=1_000_000, volume_change_pct=75.0,
        )
    )
    session.commit()

    resp = client.get("/crypto/screener/candidates", headers=auth_header)

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["page"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["coin_id"] == "altcoin-x"
    assert body["items"][0]["volume_change_pct"] == 75.0


def test_get_candidates_paginates(session, client, auth_header):
    for i in range(5):
        session.add(
            ScreenerCandidate(
                coin_id=f"coin-{i}", symbol=f"c{i}", name=f"Coin {i}",
                market_cap=10_000_000 + i, volume_24h=1000, volume_change_pct=float(i),
            )
        )
    session.commit()

    resp = client.get("/crypto/screener/candidates?page=1&page_size=2", headers=auth_header)

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert len(body["items"]) == 2
    # sorted by volume_change desc by default -> coin-4 (4.0) first
    assert body["items"][0]["coin_id"] == "coin-4"


def test_get_candidates_sorts_by_market_cap(session, client, auth_header):
    session.add(ScreenerCandidate(coin_id="small", symbol="s", name="Small", market_cap=1_000_000, volume_24h=1))
    session.add(ScreenerCandidate(coin_id="big", symbol="b", name="Big", market_cap=20_000_000, volume_24h=1))
    session.commit()

    resp = client.get("/crypto/screener/candidates?sort=market_cap", headers=auth_header)

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [c["coin_id"] for c in items] == ["big", "small"]


def test_get_candidates_rejects_unknown_sort(client, auth_header):
    resp = client.get("/crypto/screener/candidates?sort=bogus", headers=auth_header)
    assert resp.status_code == 400


def test_get_candidates_filters_by_query(session, client, auth_header):
    session.add(ScreenerCandidate(coin_id="pepe-coin", symbol="PEPE", name="Pepe", market_cap=1, volume_24h=1))
    session.add(ScreenerCandidate(coin_id="doge-coin", symbol="DOGE", name="Dogecoin", market_cap=1, volume_24h=1))
    session.commit()

    resp = client.get("/crypto/screener/candidates?q=pepe", headers=auth_header)

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["coin_id"] == "pepe-coin"


def test_promote_candidate_creates_crypto_symbol(session, client, auth_header):
    session.add(
        ScreenerCandidate(
            coin_id="altcoin-x", symbol="altx", name="Alt X", market_cap=20_000_000,
            volume_24h=1_000_000, volume_change_pct=75.0,
        )
    )
    session.commit()

    resp = client.post("/crypto/screener/candidates/altcoin-x/promote", headers=auth_header)

    assert resp.status_code == 200
    assert resp.json() == {"ticker": "ALTX", "asset_class": "crypto"}
    symbol = session.get(Symbol, "ALTX")
    assert symbol.asset_class == AssetClass.CRYPTO
    assert symbol.is_watchlist is True


def test_promote_unknown_candidate_is_404(client, auth_header):
    resp = client.post("/crypto/screener/candidates/does-not-exist/promote", headers=auth_header)
    assert resp.status_code == 404


def test_promote_coingecko_candidate_sets_coingecko_id(session, client, auth_header):
    session.add(
        ScreenerCandidate(
            coin_id="altcoin-x", symbol="altx", name="Alt X", market_cap=20_000_000,
            volume_24h=1_000_000, source="coingecko",
        )
    )
    session.commit()

    client.post("/crypto/screener/candidates/altcoin-x/promote", headers=auth_header)

    symbol = session.get(Symbol, "ALTX")
    assert symbol.coingecko_id == "altcoin-x"
    assert symbol.dex_network is None
    assert symbol.dex_pool_address is None


def test_promote_geckoterminal_candidate_sets_dex_pool_ref(session, client, auth_header):
    session.add(
        ScreenerCandidate(
            coin_id="gt:eth:0xpool1", symbol="dexcoin", name="Dex Coin", market_cap=5_000_000,
            volume_24h=1000, source="geckoterminal", network="eth", pool_address="0xpool1",
        )
    )
    session.commit()

    client.post("/crypto/screener/candidates/gt:eth:0xpool1/promote", headers=auth_header)

    symbol = session.get(Symbol, "DEXCOIN")
    assert symbol.dex_network == "eth"
    assert symbol.dex_pool_address == "0xpool1"
    assert symbol.coingecko_id is None

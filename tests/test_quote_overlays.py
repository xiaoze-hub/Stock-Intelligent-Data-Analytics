"""K线叠加层 API(M5, 2026-09-10): 持仓成本线/买卖点/除权标记。

覆盖: 纯函数组装 + HTTP 端点(登录态) + 空数据空数组。用不可能的真实
代码 TSTOV0001, 测后清理, 不污染业务数据。
"""

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.web.api.quotes import _build_overlays
from src.web.database import SessionLocal
from src.web.models import CorporateAction, PaperTradingPosition, PaperTradingTrade

TEST_SYMBOL = "TSTOV0001"


def _pos(**kw):
    base = dict(stock_symbol=TEST_SYMBOL, stock_market="CN", quantity=100,
                entry_price=10.0, stop_loss=9.0, target_price=12.0,
                status="open", opened_at=datetime(2026, 8, 3, 10, 0, 0))
    base.update(kw)
    return SimpleNamespace(**base)


def test_build_overlays_pure():
    """纯函数: 成本/止损/目标三线 + 买点 + 除权标签口径。"""
    out = _build_overlays(
        TEST_SYMBOL, "CN",
        [_pos()],
        [SimpleNamespace(entry_price=9.5, exit_price=11.0, exit_reason="target_price",
                         opened_at=datetime(2026, 7, 1), closed_at=datetime(2026, 7, 20))],
        [SimpleNamespace(dividend_per_share=0.5, bonus_ratio=0, transfer_ratio=5,
                         ex_date="2026-06-18")],
    )
    kinds = {l["kind"] for l in out["cost_lines"]}
    assert kinds == {"cost", "stop", "target"}
    assert out["cost_lines"][0]["price"] == 10.0
    sides = [m["side"] for m in out["trade_markers"]]
    assert sides == ["buy", "buy", "sell"]
    assert "target_price" in out["trade_markers"][-1]["label"]
    assert out["ex_marks"] == [{"date": "2026-06-18", "label": "10派0.5转5"}]


def test_build_overlays_empty_never_throws():
    """空输入/坏日期一律空数组, 不抛。"""
    out = _build_overlays("X", "CN", None, [], [SimpleNamespace(ex_date="")])
    assert out == {"symbol": "X", "market": "CN", "cost_lines": [],
                   "trade_markers": [], "ex_marks": []}


@pytest.fixture()
def client():
    from src.web.app import app

    return TestClient(app)


@pytest.fixture()
def token(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "xz.170530"})
    assert r.status_code == 200, r.text
    return r.json()["data"]["token"]


@pytest.fixture()
def seed_rows():
    db = SessionLocal()
    try:
        db.add(PaperTradingPosition(stock_symbol=TEST_SYMBOL, stock_market="CN",
                                    quantity=200, entry_price=10.0, status="open"))
        db.add(PaperTradingTrade(stock_symbol=TEST_SYMBOL, stock_market="CN",
                                 quantity=100, entry_price=9.0, exit_price=11.0,
                                 exit_reason="manual",
                                 opened_at=datetime(2026, 7, 1),
                                 closed_at=datetime(2026, 7, 20)))
        db.add(CorporateAction(symbol=TEST_SYMBOL, market="CN", ex_date="2026-06-18",
                               dividend_per_share=0.5, source="eastmoney"))
        db.commit()
        yield
    finally:
        db.query(PaperTradingPosition).filter(
            PaperTradingPosition.stock_symbol == TEST_SYMBOL).delete()
        db.query(PaperTradingTrade).filter(
            PaperTradingTrade.stock_symbol == TEST_SYMBOL).delete()
        db.query(CorporateAction).filter(CorporateAction.symbol == TEST_SYMBOL).delete()
        db.commit()
        db.close()


def test_overlays_endpoint(client, token, seed_rows):
    """端点返回三组叠加数据, 口径与纯函数一致。"""
    r = client.get(f"/api/quotes/{TEST_SYMBOL}/overlays?market=CN",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["symbol"] == TEST_SYMBOL
    assert any(l["kind"] == "cost" and l["price"] == 10.0 for l in body["cost_lines"])
    assert {m["side"] for m in body["trade_markers"]} == {"buy", "sell"}
    assert body["ex_marks"][0]["date"] == "2026-06-18"


def test_overlays_endpoint_empty_symbol(client, token):
    """无持仓无除权的代码返回空数组, 不 404。"""
    r = client.get("/api/quotes/NOOVERLAYS999/overlays?market=CN",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["cost_lines"] == [] and body["trade_markers"] == [] and body["ex_marks"] == []

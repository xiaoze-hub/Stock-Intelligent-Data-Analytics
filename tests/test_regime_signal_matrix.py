"""市况×信号矩阵(L3): 归因键 + 最优策略 + 端点。"""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.core.regime_signal_matrix import compute_regime_matrix
from src.web import models as M  # noqa: F401  注册模型
from src.web.database import Base, SessionLocal
from src.web.models import MarketPhaseDaily, StrategyOutcome

D1 = "2099-12-01"  # rally
D2 = "2099-12-10"  # ebb


def _mem_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


_sid = [-5000]


def _out(code, snap, status="hit_target", ret=8.0, ht=True):
    _sid[0] -= 1
    return StrategyOutcome(
        signal_run_id=_sid[0], strategy_code=code, snapshot_date=snap,
        stock_symbol="L3T", stock_market="CN", horizon_days=5,
        target_date=snap, outcome_return_pct=ret,
        hit_target=ht, hit_stop=False, outcome_status=status,
    )


@pytest.fixture()
def memdb():
    db = _mem_db()
    db.add(MarketPhaseDaily(date=date(2099, 12, 1), phase="rally"))
    db.add(MarketPhaseDaily(date=date(2099, 12, 10), phase="ebb"))
    # rally 日: A 10中9, B 10中2；ebb 日: A 10中3, B 10中8
    for _ in range(9):
        db.add(_out("STRAT_A", D1))
    db.add(_out("STRAT_A", D1, status="evaluated", ret=-3.0, ht=False))
    for _ in range(2):
        db.add(_out("STRAT_B", D1))
    for _ in range(8):
        db.add(_out("STRAT_B", D1, status="evaluated", ret=-2.0, ht=False))
    for _ in range(3):
        db.add(_out("STRAT_A", D2))
    for _ in range(7):
        db.add(_out("STRAT_A", D2, status="evaluated", ret=-2.0, ht=False))
    for _ in range(8):
        db.add(_out("STRAT_B", D2))
    for _ in range(2):
        db.add(_out("STRAT_B", D2, status="evaluated", ret=-1.0, ht=False))
    # 无市况标签日: 丢弃不归属
    db.add(_out("STRAT_A", "2099-11-01"))
    db.commit()
    yield db
    db.close()


def test_matrix_attribution_and_best(memdb):
    m = compute_regime_matrix(memdb, window_days=365 * 200, today="2099-12-31")
    cells = {(c["phase"], c["strategy_code"]): c for c in m["cells"]}
    assert len(cells) == 4  # 无标签日已丢弃
    assert cells[("rally", "STRAT_A")]["hit_rate"] == 90.0
    assert cells[("rally", "STRAT_B")]["hit_rate"] == 20.0
    assert cells[("ebb", "STRAT_B")]["hit_rate"] == 80.0
    assert cells[("ebb", "STRAT_A")]["hit_rate"] == 30.0
    assert m["best"]["rally"]["strategy_code"] == "STRAT_A"
    assert m["best"]["ebb"]["strategy_code"] == "STRAT_B"
    assert m["current_phase"] == "ebb"  # 最近有标签日


@pytest.fixture()
def client():
    from src.web.app import app

    return TestClient(app)


@pytest.fixture()
def token(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "xz.170530"})
    assert r.status_code == 200, r.text
    return r.json()["data"]["token"]


def test_regime_matrix_endpoint_shape(client, token):
    """端点结构(真实库可能无数据, 只断言结构不断言行数)。"""
    from src.web.cache.biz_cache import biz_cache

    biz_cache.delete("regime:matrix:90")
    r = client.get("/api/strategies/regime-matrix",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert set(body) >= {"asof", "window_days", "current_phase", "cells", "best"}
    assert isinstance(body["cells"], list)
    biz_cache.delete("regime:matrix:90")

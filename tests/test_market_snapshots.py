"""日快照入库+单写者(P2): upsert 幂等 + 失败保旧 + 读端点。

同步测试用独立内存 sqlite(不碰业务库); 端点测试用不可能日期
2099-13-99? 不——端点读当日行, 用 TEST_MARK 标记行并清理。
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.core.market_snapshots as ms
from src.web import models as M  # noqa: F401  注册模型
from src.web.database import Base, SessionLocal
from src.web.models import MarketSnapshot

DAY = "2099-12-31"  # 不可能的真实日期, 避免撞业务快照


class _FakeCollector:
    def __init__(self, fail_kinds=()):
        self.fail_kinds = set(fail_kinds)
        self.calls: list = []

    def get_limit_up_pool(self, ymd):
        self.calls.append(("pool", ymd))
        if "limit_up_pool" in self.fail_kinds:
            raise RuntimeError("上游炸了")
        return [{"code": "000001", "name": "平安银行"}]

    def get_sector_rotation(self, top_n=20):
        self.calls.append(("rotation", top_n))
        if "sector_rotation" in self.fail_kinds:
            raise RuntimeError("上游炸了")
        return {"top": [{"name": "银行"}]}

    def get_index_snapshot(self):
        self.calls.append(("index",))
        if "index_snapshot" in self.fail_kinds:
            raise RuntimeError("上游炸了")
        return [{"name": "上证指数", "pct": 0.5}]


def _mem_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@pytest.fixture()
def memdb():
    db = _mem_db()
    yield db
    db.close()


def _patch_collector(monkeypatch, fake):
    monkeypatch.setattr(ms, "_collector", lambda: fake)


def test_sync_upsert_idempotent(memdb, monkeypatch):
    """两次 sync 行数不变(唯一键 kind+date+market), payload 更新。"""
    _patch_collector(monkeypatch, _FakeCollector())
    out1 = ms.sync_market_snapshots(memdb, snapshot_date=DAY)
    assert set(out1) == set(ms.KINDS) and all("rows" in v for v in out1.values())
    n1 = memdb.query(MarketSnapshot).filter(MarketSnapshot.snapshot_date == DAY).count()
    assert n1 == len(ms.KINDS)
    out2 = ms.sync_market_snapshots(memdb, snapshot_date=DAY)
    n2 = memdb.query(MarketSnapshot).filter(MarketSnapshot.snapshot_date == DAY).count()
    assert n2 == n1 == 3
    assert out2["limit_up_pool"] == {"rows": 1}


def test_sync_failure_keeps_old_rows(memdb, monkeypatch):
    """单 kind 失败记 error, 旧行保留, 其他 kind 照常。"""
    _patch_collector(monkeypatch, _FakeCollector())
    ms.sync_market_snapshots(memdb, snapshot_date=DAY)
    _patch_collector(monkeypatch, _FakeCollector(fail_kinds=("limit_up_pool",)))
    out = ms.sync_market_snapshots(memdb, snapshot_date=DAY)
    assert "error" in out["limit_up_pool"]
    assert out["index_snapshot"] == {"rows": 1}
    row = (
        memdb.query(MarketSnapshot)
        .filter(MarketSnapshot.kind == "limit_up_pool", MarketSnapshot.snapshot_date == DAY)
        .one()
    )
    assert "平安银行" in row.payload  # 旧快照还在


def test_read_snapshot_shapes(memdb, monkeypatch):
    """miss 返回 None; hit 带 asof/source/fetched_at/payload。"""
    assert ms.read_snapshot(memdb, "index_snapshot", DAY) is None
    _patch_collector(monkeypatch, _FakeCollector())
    ms.sync_market_snapshots(memdb, snapshot_date=DAY)
    got = ms.read_snapshot(memdb, "index_snapshot", DAY)
    assert got["asof"] == DAY and got["source"] == "tencent"
    assert got["payload"][0]["name"] == "上证指数"
    assert got["fetched_at"]


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
def seed_row():
    db = SessionLocal()
    try:
        db.add(MarketSnapshot(kind="index_snapshot", snapshot_date=DAY, market="CN",
                              payload='[{"name": "上证指数", "pct": 0.5}]',
                              source="tencent"))
        db.commit()
        yield
    finally:
        db.query(MarketSnapshot).filter(MarketSnapshot.snapshot_date == DAY).delete()
        db.commit()
        db.close()


def test_snapshots_endpoint_hit(client, token, seed_row):
    """命中行直返, 口径与 read_snapshot 一致(含 asof/source)。"""
    r = client.get(f"/api/market/snapshots?kind=index_snapshot&date_str={DAY}",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["asof"] == DAY and body["source"] == "tencent"
    assert body["payload"][0]["pct"] == 0.5


def test_snapshots_endpoint_bad_kind(client, token):
    """未知 kind 400, 不回源。"""
    r = client.get("/api/market/snapshots?kind=nope",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400

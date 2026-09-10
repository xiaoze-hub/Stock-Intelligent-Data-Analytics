"""信号周滚动 HitRate 榜(L1): 口径 + 排序 + 状态 + 端点。

内存 sqlite 测纯函数；端点用不可能的策略码 L1TEST*，测后清理。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.core.signal_hitrate import compute_hitrate_board
from src.web import models as M  # noqa: F401  注册模型
from src.web.database import Base, SessionLocal
from src.web.models import StrategyOutcome

DAY = "2099-12-20"


def _mem_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


_SID = [-1000]


def _row(code, hz, status, ht, hs, ret, target=DAY):
    _SID[0] -= 1
    return StrategyOutcome(
        signal_run_id=_SID[0], strategy_code=code, snapshot_date=DAY,
        stock_symbol="L1T", stock_market="CN", horizon_days=hz,
        target_date=target, outcome_return_pct=ret,
        hit_target=ht, hit_stop=hs, outcome_status=status,
    )


@pytest.fixture()
def memdb():
    db = _mem_db()
    # A策略h1: 12中里10中(83%)；h10: 12中里4中(33%)→衰减可见
    for i in range(10):
        db.add(_row("STRAT_A", 1, "hit_target" if i < 8 else "evaluated", i < 8, False, 12.0 if i < 8 else 3.0))
    for i in range(2):
        db.add(_row("STRAT_A", 1, "evaluated", False, False, -2.0))
    for i in range(12):
        db.add(_row("STRAT_A", 10, "hit_target" if i < 4 else "evaluated", i < 4, False, 11.0 if i < 4 else -1.0))
    # B策略: 25个全 miss → 待砍
    for _ in range(25):
        db.add(_row("STRAT_B", 5, "evaluated", False, True, -6.0))
    # C策略: 3个样本 → 样本不足
    for _ in range(3):
        db.add(_row("STRAT_C", 1, "hit_target", True, False, 9.0))
    # 窗口外 + pending: 不计入
    db.add(_row("STRAT_A", 1, "hit_target", True, False, 9.0, target="1899-01-01"))
    db.add(_row("STRAT_A", 1, "pending", None, None, None))
    db.commit()
    yield db
    db.close()


def test_board_shapes_and_status(memdb):
    board = compute_hitrate_board(memdb, window_days=365 * 200, today="2099-12-31")
    rows = {(r["strategy_code"], r["horizon_days"]): r for r in board["rows"]}
    assert board["asof"] == "2099-12-31"
    a1 = rows[("STRAT_A", 1)]
    assert a1["n"] == 12 and a1["hits"] == 10 and a1["hit_rate"] == 83.3
    assert a1["status"] == "正常"
    a10 = rows[("STRAT_A", 10)]
    assert a10["hit_rate"] < a1["hit_rate"]  # 半衰期衰减可见
    b = rows[("STRAT_B", 5)]
    assert b["hit_rate"] == 0.0 and b["stop_rate"] == 100.0 and b["status"] == "待砍"
    assert rows[("STRAT_C", 1)]["status"] == "样本不足"
    # 排序: hit_rate 倒序, 首行是 C(100%) 其次 A-h1
    assert board["rows"][0]["strategy_code"] == "STRAT_C"
    assert board["hit_def"]


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
    from datetime import date as _date
    from datetime import timedelta as _td

    from src.web.models import StrategySignalRun

    past = (_date.today() - _td(days=5)).isoformat()
    db = SessionLocal()
    try:
        for _ in range(12):
            sig = StrategySignalRun(snapshot_date=past, stock_symbol="L1TEST_X",
                                    stock_market="CN", strategy_code="L1TEST_X",
                                    status="active", action="buy")
            db.add(sig)
            db.flush()
            o = _row("L1TEST_X", 1, "hit_target", True, False, 8.0, target=past)
            o.snapshot_date = past
            o.signal_run_id = sig.id
            db.add(o)
        db.commit()
        yield
    finally:
        db.query(StrategyOutcome).filter(StrategyOutcome.strategy_code == "L1TEST_X").delete()
        db.query(StrategySignalRun).filter(StrategySignalRun.strategy_code == "L1TEST_X").delete()
        db.commit()
        db.close()


def test_hitrate_board_endpoint(client, token, seed_rows):
    from src.web.cache.biz_cache import biz_cache

    biz_cache.delete("hitrate:board:28")
    r = client.get("/api/strategies/hitrate-board",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    mine = [x for x in body["rows"] if x["strategy_code"] == "L1TEST_X"]
    assert mine and mine[0]["n"] == 12 and mine[0]["hit_rate"] == 100.0
    biz_cache.delete("hitrate:board:28")

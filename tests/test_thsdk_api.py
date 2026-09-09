"""thsdk_snapshot.py + thsdk_alert.py API 测试

mock 外部依赖,验证:
- _to_ths_symbol 转换
- 缓存命中
- 异常容错
- thsdk_alert.run() 输出结构

每个测试用唯一 symbol 避免缓存污染。
"""
from __future__ import annotations

import sys
import time as _time
import uuid
from unittest.mock import MagicMock

import pytest


# ---------- 启动时屏蔽 thsdk 模块导入 ----------
# src.core.thsdk_alert 顶层 `from thsdk import ...` 会失败,影响测试。
# 在所有 fixture 之前先 mock 掉,确保 lazy import 不触发真模块。
@pytest.fixture(autouse=True, scope="session")
def _mock_thsdk_module():
    """整个测试会话期间把 thsdk 模块屏蔽成 MagicMock。"""
    fake_thsdk = MagicMock()
    fake_thsdk.THS = MagicMock()
    fake_thsdk.THSResponse = MagicMock()
    sys.modules["thsdk"] = fake_thsdk
    yield
    sys.modules.pop("thsdk", None)


# ---------- _to_ths_symbol ----------

@pytest.mark.parametrize("raw,expected", [
    ("002361", "USZA002361"),  # 神剑
    ("600519", "USHA600519"),  # 茅台
    ("688041", "USHA688041"),  # 海康
    ("300750", "USZA300750"),  # 宁德
    ("830799", "USTM830799"),  # 北交所
    ("USZA002361", "USZA002361"),  # 已是thsdk格式
])
def test_to_ths_symbol(raw, expected):
    from src.web.api.thsdk_snapshot import _to_ths_symbol
    assert _to_ths_symbol(raw) == expected


def test_to_ths_symbol_invalid():
    from src.web.api.thsdk_snapshot import _to_ths_symbol
    with pytest.raises(ValueError):
        _to_ths_symbol("abc")
    with pytest.raises(ValueError):
        _to_ths_symbol("12345")  # 5 位


# ---------- snapshot 缓存 + 容错 ----------

def test_snapshot_cache_hit(monkeypatch):
    from src.web.api import thsdk_snapshot

    thsdk_snapshot._SNAP_CACHE.clear()  # unique symbol per test
    fake = {
        "quote": {"last": 12.5, "open": 12.0},
        "depth": {"bid": [12.4] * 5, "ask": [12.6] * 5},
        "main_flow": {"main_net": 1000},
        "sectors": [{"name": "芯片"}],
    }
    # 直接替换 lazy import 入口
    monkeypatch.setattr(thsdk_snapshot, "get_comprehensive_snapshot", lambda s: fake, raising=False)
    # 也设置模块级属性,防御 lazy import
    import sys
    monkeypatch.setitem(sys.modules, "data_source.thsdk_l2", MagicMock(get_comprehensive_snapshot=lambda s: fake))

    out1 = thsdk_snapshot._fetch_snapshot("002361")
    out2 = thsdk_snapshot._fetch_snapshot("002361")

    assert out1["symbol"] == "002361"
    assert out1["quote"]["last"] == 12.5
    assert out1["warnings"] == []


def test_snapshot_thsdk_failure_fallback(monkeypatch):
    from src.web.api import thsdk_snapshot

    thsdk_snapshot._SNAP_CACHE.clear()  # unique symbol per test
    monkeypatch.setattr(thsdk_snapshot, "get_comprehensive_snapshot", lambda s: (_ for _ in ()).throw(RuntimeError("熔断")), raising=False)
    import sys
    monkeypatch.setitem(sys.modules, "data_source.thsdk_l2", MagicMock(get_comprehensive_snapshot=lambda s: (_ for _ in ()).throw(RuntimeError("熔断"))))

    out = thsdk_snapshot._fetch_snapshot("002361")
    assert out["quote"] is None
    assert out["depth"] is None
    assert any("thsdk" in w for w in out["warnings"])


def test_snapshot_ttl_expired(monkeypatch):
    """用 600519 不与其他测试冲突。"""
    from src.web.api import thsdk_snapshot

    thsdk_snapshot._SNAP_CACHE.clear()  # unique symbol per test
    sym = "600519"
    fake_a = {"quote": {"last": 1.0}}
    fake_b = {"quote": {"last": 2.0}}
    calls = [0]

    def fetch(_):
        calls[0] += 1
        return fake_a if calls[0] == 1 else fake_b

    monkeypatch.setattr(thsdk_snapshot, "get_comprehensive_snapshot", fetch, raising=False)
    import sys
    monkeypatch.setitem(sys.modules, "data_source.thsdk_l2", MagicMock(get_comprehensive_snapshot=fetch))

    first = thsdk_snapshot._fetch_snapshot(sym)
    # 强制过期
    assert sym in thsdk_snapshot._SNAP_CACHE
    ts = thsdk_snapshot._SNAP_CACHE[sym][0]
    thsdk_snapshot._SNAP_CACHE[sym] = (ts - 100, thsdk_snapshot._SNAP_CACHE[sym][1])
    second = thsdk_snapshot._fetch_snapshot(sym)
    assert first["quote"]["last"] == 1.0
    assert second["quote"]["last"] == 2.0


# ---------- thsdk_alert API ----------

def test_alert_cache_hit(monkeypatch):
    from src.web.api import thsdk_alert

    thsdk_alert._ALERT_CACHE.clear()  # unique symbol per test
    fake_result = {
        "close_surge": {"direction": "拉尾", "surge_score": 75},
        "auction": {"direction": "高开", "gap_pct": 2.5},
        "wencai_pool": {"candidates": [{"code": "USZA002361", "name": "神剑"}], "note": "ok"},
    }
    # mock 模块级属性(thsdk_alert 内部 import 会触发 thsdk,我们直接替换 run 函数)
    monkeypatch.setattr("src.core.thsdk_alert.run", lambda *a, **kw: fake_result)

    out1 = thsdk_alert._run_alert("002361")
    out2 = thsdk_alert._run_alert("002361")

    assert out1["symbol"] == "002361"
    assert out1["close_surge"]["direction"] == "拉尾"
    assert out2["close_surge"]["direction"] == "拉尾"


def test_alert_failure_fallback(monkeypatch):
    from src.web.api import thsdk_alert

    thsdk_alert._ALERT_CACHE.clear()  # unique symbol per test
    monkeypatch.setattr(
        "src.core.thsdk_alert.run",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("失败")),
    )

    out = thsdk_alert._run_alert("002361")
    assert out["close_surge"] is None
    assert any("不可用" in w for w in out["warnings"])


# ---------- HTTP 端点 ----------

def test_http_snapshot_endpoint(monkeypatch, client, auth_token):
    from src.web.api import thsdk_snapshot
    thsdk_snapshot._SNAP_CACHE.clear()  # unique symbol per test
    fake = {"quote": {"last": 12.5}, "depth": None, "main_flow": None, "sectors": []}
    monkeypatch.setattr(thsdk_snapshot, "get_comprehensive_snapshot", lambda s: fake, raising=False)
    import sys
    monkeypatch.setitem(sys.modules, "data_source.thsdk_l2", MagicMock(get_comprehensive_snapshot=lambda s: fake))

    resp = client.get("/api/thsdk/snapshot/002361", headers={"Authorization": f"Bearer {auth_token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["quote"]["last"] == 12.5


def test_http_alert_endpoint(monkeypatch, client, auth_token):
    from src.web.api import thsdk_alert
    thsdk_alert._ALERT_CACHE.clear()  # unique symbol per test
    fake = {
        "close_surge": {"direction": "中性", "surge_score": 0},
        "auction": {"direction": "无数据"},
        "wencai_pool": {"candidates": [], "note": "非交易时段"},
    }
    monkeypatch.setattr("src.core.thsdk_alert.run", lambda *a, **kw: fake)

    resp = client.get("/api/thsdk/alert/002361", headers={"Authorization": f"Bearer {auth_token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["close_surge"]["direction"] == "中性"


# ---------- fixtures ----------

@pytest.fixture
def auth_token():
    """用 FastAPI app.dependency_overrides 绕过登录校验。

    比 monkeypatch 更稳:不依赖 app.py 里的 import 顺序。
    """
    from src.web.app import app
    from src.web.api.auth import get_current_user

    fake_user = {"id": "test", "username": "tester", "role": "owner"}
    app.dependency_overrides[get_current_user] = lambda: fake_user
    yield "test-token"
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture(autouse=True)
def reset_thsdk_caches():
    """每个测试前清缓存避免污染。"""
    try:
        from src.web.api import thsdk_snapshot, thsdk_alert
        thsdk_snapshot._SNAP_CACHE.clear()
        thsdk_alert._ALERT_CACHE.clear()
    except ImportError as e:
        print(f"[reset_thsdk_caches] import failed: {e}")
    yield
    # 测试结束后再清,避免下次 test 看到这次缓存
    try:
        from src.web.api import thsdk_snapshot, thsdk_alert
        thsdk_snapshot._SNAP_CACHE.clear()
        thsdk_alert._ALERT_CACHE.clear()
    except ImportError:
        pass


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from src.web.app import app
    return TestClient(app)
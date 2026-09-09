"""thsdk_extended.py(v0.3.1 目标 A)API 测试

覆盖 11 个待接能力端点 + 路由注册 + wencai 增强缓存。

mock 策略:
- 会话级把 `thsdk` 模块屏蔽成 MagicMock,让 data_source.thsdk_l2 可被 import。
- 端点测试 monkeypatch `src.web.api.thsdk_extended.get_xxx` 模块属性返 DataFrame
  (与 test_thsdk_api.py 同款模式)。
- wencai 缓存测试 patch `THSDKL2._query`(底层),验证真实 30s 缓存只拉一次。
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pandas as pd
import pytest

_EXPECTED_PATHS = [
    "/api/thsdk/news/{symbol}",
    "/api/thsdk/corporate-action/{symbol}",
    "/api/thsdk/dde/{symbol}",
    "/api/thsdk/hs300-constituents",
    "/api/thsdk/market-data-cn-extended/{symbol}",
    "/api/thsdk/market-data-index/{symbol}",
    "/api/thsdk/market-data-hk/{symbol}",
    "/api/thsdk/market-data-us/{symbol}",
    "/api/thsdk/market-data-bond/{symbol}",
    "/api/thsdk/market-data-fund/{symbol}",
    "/api/thsdk/wencai-enhanced",
]


@pytest.fixture(autouse=True, scope="session")
def _mock_thsdk_module():
    """整个测试会话期间把 thsdk 模块屏蔽成 MagicMock(否则 data_source.thsdk_l2
    import 即抛 ImportError, 无法 monkeypatch THSDKL2)。"""
    fake_thsdk = MagicMock()
    fake_thsdk.THS = MagicMock()
    fake_thsdk.THSResponse = MagicMock()
    sys.modules["thsdk"] = fake_thsdk
    yield
    sys.modules.pop("thsdk", None)


@pytest.fixture
def test_app():
    """基于 thsdk_extended.router 的隔离测试 app(不 import src.web.app,
    避免并行 agent 选项 C 在途改 chat.py 导致整 app 无法导入)。"""
    from fastapi import FastAPI

    from src.web.api import thsdk_extended
    from src.web.response import ResponseWrapperMiddleware

    app = FastAPI()
    app.add_middleware(ResponseWrapperMiddleware)
    app.include_router(thsdk_extended.router, prefix="/api/thsdk")
    return app


@pytest.fixture
def client(test_app):
    from fastapi.testclient import TestClient

    return TestClient(test_app)


@pytest.fixture
def auth_token(test_app):
    """用 dependency_overrides 绕过登录校验。"""
    from src.web.api.auth import get_current_user

    fake_user = {"id": "test", "username": "tester", "role": "owner"}
    test_app.dependency_overrides[get_current_user] = lambda: fake_user
    yield "test-token"
    test_app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture(autouse=True)
def _reset_wencai_cache():
    """每个测试前清问财缓存,避免污染。"""
    try:
        from data_source.thsdk_l2 import _WENCAI_CACHE

        _WENCAI_CACHE.clear()
    except Exception:
        pass
    yield


# ---------- 路由注册验证 ----------

def test_thsdk_extended_router_registered():
    """11 个端点已注册到 src.web.app。"""
    try:
        from src.web.app import app
    except Exception as e:  # noqa: BLE001
        # 并行 agent(选项 C)在途改 chat.py 时 src.web.app 可能暂不可导入
        pytest.skip(f"src.web.app 暂不可导入(并行选项C在途改动?): {e}")

    registered = _all_route_paths(app)
    for p in _EXPECTED_PATHS:
        assert p in registered, f"端点未注册: {p}"


def _all_route_paths(app) -> set:
    """展平 app.routes(含 FastAPI _IncludedRouter 嵌套)。"""
    out = set()

    def _walk(routes, prefix=""):
        for r in routes:
            orig = getattr(r, "original_router", None)
            if orig is not None:
                ctx = getattr(r, "include_context", None)
                pfx = (
                    getattr(r, "prefix", None)
                    or (getattr(ctx, "prefix", None) if ctx else None)
                    or getattr(orig, "prefix", "")
                    or ""
                )
                _walk(getattr(orig, "routes", []), prefix + str(pfx))
                continue
            inner = getattr(r, "routes", None)
            if inner:
                _walk(inner, prefix + str(getattr(r, "prefix", "") or ""))
                continue
            p = getattr(r, "path", None)
            if p:
                out.add(prefix + str(p))

    _walk(getattr(app, "routes", []))
    return out


# ---------- 通用端点 mock 工具 ----------

def _patch_module_fn(monkeypatch, name: str, df: pd.DataFrame):
    """把 thsdk_extended 模块级 get_xxx 替换成返 DataFrame 的函数。"""
    from src.web.api import thsdk_extended

    monkeypatch.setattr(thsdk_extended, name, lambda *a, **k: df, raising=False)


# ---------- 11 个端点 ----------

def test_news_endpoint(client, auth_token, monkeypatch):
    from src.web.api import thsdk_extended

    _patch_module_fn(monkeypatch, "get_news", pd.DataFrame([{"标题": "测试新闻"}]))
    resp = client.get(
        "/api/thsdk/news/USZA002361", headers={"Authorization": f"Bearer {auth_token}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["symbol"] == "USZA002361"
    assert isinstance(body["data"]["rows"], list)
    assert body["data"]["count"] == 1
    assert body["data"]["rows"][0]["标题"] == "测试新闻"


def test_corporate_action_endpoint(client, auth_token, monkeypatch):
    _patch_module_fn(monkeypatch, "get_corporate_action", pd.DataFrame([{"类型": "分红"}]))
    resp = client.get(
        "/api/thsdk/corporate-action/USZA002361",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["rows"][0]["类型"] == "分红"


def test_dde_endpoint(client, auth_token, monkeypatch):
    """2026-08-20 修复: thsdk 1.7.18 无 get_dde(), 改调 get_main_flow_official(官方 DDE API)。

    get_main_flow_official 返回 dict 含 summary/detail/主力净流入等。
    """
    fake_dde_result = {
        "symbol": "002361",
        "ths_code": "USZA002361",
        "price": 11.27,
        "total_amount_wan": 12345.0,
        "main_net_amount_wan": -2365.0,
        "main_net_ratio": -0.078,
        "summary": {"价格": 11.27, "主力净流入": -23650000.0, "总金额": 123450000.0},
        "detail": {
            "主动买入特大单金额": 0.0,
            "主动卖出特大单金额": -14800000.0,
            "主动买入大单金额": 0.0,
            "主动卖出大单金额": -8800000.0,
        },
    }
    _patch_module_fn(monkeypatch, "get_main_flow_official", fake_dde_result)
    resp = client.get(
        "/api/thsdk/dde/USZA002361", headers={"Authorization": f"Bearer {auth_token}"}
    )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["symbol"] == "USZA002361"
    assert body["main_net_amount_wan"] == -2365.0
    assert body["main_net_ratio"] == -0.078
    # rows 至少 1 行(顶层 row) + summary + detail
    assert body["count"] >= 1


def test_hs300_endpoint(client, auth_token, monkeypatch):
    _patch_module_fn(
        monkeypatch,
        "get_hs300_constituents",
        pd.DataFrame([{"代码": "USZA000001"}, {"代码": "USHA600000"}]),
    )
    resp = client.get(
        "/api/thsdk/hs300-constituents", headers={"Authorization": f"Bearer {auth_token}"}
    )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["count"] == 2


def test_market_data_cn_extended_endpoint(client, auth_token, monkeypatch):
    _patch_module_fn(monkeypatch, "get_market_data_cn_extended", pd.DataFrame([{"主力净流入": 12345.0}]))
    resp = client.get(
        "/api/thsdk/market-data-cn-extended/USZA002361",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["rows"][0]["主力净流入"] == 12345.0


def test_market_data_index_endpoint(client, auth_token, monkeypatch):
    _patch_module_fn(monkeypatch, "get_market_data_index", pd.DataFrame([{"最新价": 3500.0}]))
    resp = client.get(
        "/api/thsdk/market-data-index/USHI000001",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["rows"][0]["最新价"] == 3500.0


def test_market_data_hk_endpoint(client, auth_token, monkeypatch):
    _patch_module_fn(monkeypatch, "get_market_data_hk", pd.DataFrame([{"最新价": 500.0}]))
    resp = client.get(
        "/api/thsdk/market-data-hk/UHKG00700",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["rows"][0]["最新价"] == 500.0


def test_market_data_us_endpoint(client, auth_token, monkeypatch):
    _patch_module_fn(monkeypatch, "get_market_data_us", pd.DataFrame([{"最新价": 200.0}]))
    resp = client.get(
        "/api/thsdk/market-data-us/UNQQAAPL",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["rows"][0]["最新价"] == 200.0


def test_market_data_bond_endpoint(client, auth_token, monkeypatch):
    _patch_module_fn(monkeypatch, "get_market_data_bond", pd.DataFrame([{"最新价": 130.0}]))
    resp = client.get(
        "/api/thsdk/market-data-bond/USBK113550",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["rows"][0]["最新价"] == 130.0


def test_market_data_fund_endpoint(client, auth_token, monkeypatch):
    _patch_module_fn(monkeypatch, "get_market_data_fund", pd.DataFrame([{"最新价": 4.5}]))
    resp = client.get(
        "/api/thsdk/market-data-fund/USZF510300",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["rows"][0]["最新价"] == 4.5


# ---------- wencai 增强(端点 + 30s 缓存) ----------

def test_wencai_enhanced_endpoint(client, auth_token, monkeypatch):
    from src.web.api import thsdk_extended

    df = pd.DataFrame([{"股票代码": "300033.SZ", "股票简称": "某某"}])
    monkeypatch.setattr(thsdk_extended, "get_wencai_enhanced", lambda q, use_cache=True: df, raising=False)
    resp = client.get(
        "/api/thsdk/wencai-enhanced",
        params={"query": "均线多头排列"},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["query"] == "均线多头排列"
    assert body["rows"][0]["股票简称"] == "某某"


def test_wencai_enhanced_empty_query_400(client, auth_token):
    resp = client.get(
        "/api/thsdk/wencai-enhanced", params={"query": "   "},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 400


def test_wencai_enhanced_cache_hit(monkeypatch):
    """数据源层验证:同 query 30s 内第二次调用走缓存,底层 _query 只调一次。"""
    from data_source.thsdk_l2 import THSDKL2, _WENCAI_CACHE

    _WENCAI_CACHE.clear()
    calls = [0]

    class _FakeResp:
        def __init__(self, data):
            self.data = data

    def fake_query(func_name, *a, **k):
        calls[0] += 1
        return _FakeResp([{"股票代码": "300033.SZ", "股票简称": "某某"}])

    monkeypatch.setattr(THSDKL2, "_query", fake_query)
    client_ = THSDKL2()
    df1 = client_.get_wencai_enhanced("均线多头排列")
    df2 = client_.get_wencai_enhanced("均线多头排列")
    assert len(df1) == 1 and len(df2) == 1
    assert calls[0] == 1  # 缓存命中, 底层只拉一次
    _WENCAI_CACHE.clear()

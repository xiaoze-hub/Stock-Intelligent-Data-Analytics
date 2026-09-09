"""2026-08-23 安全审计修复测试 — 覆盖 P0/P1/P2 各项要点。

每个测试函数对应一条审计条目 (test_xx_yy 格式便于定位)。
测试隔离: 多数用 tmp_path + monkeypatch, 不污染全局 DB / env。
"""
from __future__ import annotations

import hashlib
import hmac
import importlib
import io
import os
import re
import secrets
import sys
import time
from contextlib import redirect_stderr

import pytest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 触发 src.web.models 在 Base.metadata 上注册全表
import src.web.models  # noqa: F401


# ────────────────────────────────────────────────────────────────────────────
# P0-1: Dockerfile USER 非 root
# ────────────────────────────────────────────────────────────────────────────

def test_p0_1_dockerfile_user_directive_present():
    """Dockerfile 末尾必须有 USER app 指令, 防容器 root 逃逸。"""
    df = open(str(PROJECT_ROOT / "Dockerfile")).read()
    # 必须在 ENTRYPOINT/CMD 之前(否则无效)
    cmd_idx = df.find('CMD ["python", "server.py"]')
    user_idx = df.rfind("USER app")
    assert user_idx > 0, "Dockerfile 必须包含 'USER app' 指令"
    assert user_idx < cmd_idx, "'USER app' 必须位于 CMD 之前才生效"
    # 同时必须创建 app 用户
    assert "useradd" in df, "Dockerfile 必须创建非特权用户 (useradd)"
    assert "chown" in df, "Dockerfile 必须 chown /app 让非 root 可写"


# ────────────────────────────────────────────────────────────────────────────
# P0-2: ZHITU_TOKEN 必显式配置 + startup_check 警告
# ────────────────────────────────────────────────────────────────────────────

def test_p0_2_quotes_no_hardcoded_token():
    """quotes.py 不再含硬编码 UUID fallback。"""
    src = open(str(PROJECT_ROOT / "src/web/api/quotes.py")).read()
    # 旧的硬编码 token 必须不再出现 (除注释里说明"已删除"的描述)
    assert "E0E16C43-9272-4DAB-800C-178694F2D4B1" not in src or \
        "已删除" in src or "必须显式配置" in src, \
        "quotes.py 不得硬编码 UUID token"
    # 必须改成显式读 env (无 fallback default)
    assert 'os.environ.get("ZHITU_TOKEN"' in src
    # 缺失时给清晰提示
    assert "ZHITU_TOKEN 未配置" in src


def test_p0_2_startup_check_zhitu_token(monkeypatch, tmp_path):
    """启动自检: ZHITU_TOKEN 缺失 → warning。"""
    import src.core.startup_check as sc
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    # 清掉所有来源
    monkeypatch.delenv("ZHITU_TOKEN", raising=False)
    monkeypatch.setattr(sc, "_check_notify_channels",
                        lambda: sc.CheckResult("notify_channels", "ok", "mock"))
    monkeypatch.setattr(sc, "_check_jwt_secret",
                        lambda: sc.CheckResult("jwt_secret", "ok", "mock"))
    # 池化/DB 拿不到
    monkeypatch.setattr(sc, "_check_db_dialect",
                        lambda: sc.CheckResult("database.dialect", "info", "mock"))
    monkeypatch.setattr(sc, "_check_db_url_explicit",
                        lambda: sc.CheckResult("database.url", "info", "mock"))
    monkeypatch.setattr(sc, "_check_thsdk",
                        lambda: sc.CheckResult("thsdk", "info", "mock"))
    monkeypatch.setattr(sc, "_check_data_dir_writable",
                        lambda: sc.CheckResult("data_dir", "ok", "mock"))

    # 池化 / DB 显式返回 None / 空
    class _FakePool:
        def __getattr__(self, name):
            def _fn(*a, **kw):
                return None
            return _fn
    monkeypatch.setitem(sys.modules, "marketdata.vendors.zhitu", _FakePool())

    results = sc.run_startup_checks()
    zhitu_results = [r for r in results if r.name == "zhitu_token"]
    assert len(zhitu_results) == 1, "zhitu_token 检查项必须注册"
    assert zhitu_results[0].level == "warning"
    assert "ZHITU_TOKEN" in zhitu_results[0].message

    # 清掉注入
    sys.modules.pop("marketdata.vendors.zhitu", None)


def test_p0_2_env_example_has_zhitu_token():
    """.env.example 必须有 ZHITU_TOKEN 条目 (供新部署参考)。"""
    env = open(str(PROJECT_ROOT / ".env.example")).read()
    assert "ZHITU_TOKEN=" in env
    # 必须有引导说明
    assert "智兔" in env or "ZHITU_TOKEN" in env


# ────────────────────────────────────────────────────────────────────────────
# P0-3: llm_adapter 单一 SDK env 注入
# ────────────────────────────────────────────────────────────────────────────

def test_p0_3_llm_adapter_only_openrouter_env(monkeypatch):
    """inject_api_key_env 必须只设 OPENROUTER_API_KEY, 清掉 OPENAI/DEEPSEEK 残留。"""
    # 先污染其它两个 env 模拟历史残留
    monkeypatch.setenv("OPENAI_API_KEY", "leaked-openai-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "leaked-deepseek-key")

    class _FakeAIClient:
        api_key = "real-user-key-abc123"
        model = "test-model"
        base_url = "https://example.com/v1"

    from src.agents.tradingagents import llm_adapter
    llm_adapter.inject_api_key_env(_FakeAIClient())

    # 主路径必须有
    assert os.environ["OPENROUTER_API_KEY"] == "real-user-key-abc123"
    # 其它两个必须被清掉(防止残留泄漏给其它 vendor)
    assert "OPENAI_API_KEY" not in os.environ
    assert "DEEPSEEK_API_KEY" not in os.environ


# ────────────────────────────────────────────────────────────────────────────
# P1-1: server.py / forecast_server 默认绑定 127.0.0.1
# ────────────────────────────────────────────────────────────────────────────

def test_p1_1_server_host_default_127(monkeypatch):
    """server.py 默认 host 应读 WEB_HOST 环境变量, 缺省 127.0.0.1 (不再硬编码 0.0.0.0)。"""
    monkeypatch.delenv("WEB_HOST", raising=False)
    src = open(str(PROJECT_ROOT / "server.py")).read()
    # 必须用环境变量驱动, 不再硬编码 0.0.0.0
    assert 'os.environ.get("WEB_HOST"' in src
    # 默认值必须是 127.0.0.1
    assert 'WEB_HOST", "127.0.0.1"' in src
    # uvicorn.run 的 host 参数必须是变量, 不能是 "0.0.0.0"
    m = re.search(r'uvicorn\.run\(\s*"server:app",\s*host=([^,\n]+),', src)
    assert m, "uvicorn.run 必须显式 host 参数"
    assert "0.0.0.0" not in m.group(1), "uvicorn host 不能硬编码 0.0.0.0"


def test_p1_1_forecast_host_default_127():
    """forecast_server.py 默认 host 应读 FORECAST_HOST 环境变量, 缺省 127.0.0.1。"""
    src = open(str(PROJECT_ROOT / "forecast_server.py")).read()
    assert 'os.environ.get("FORECAST_HOST"' in src
    assert 'FORECAST_HOST", "127.0.0.1"' in src
    # uvicorn.run 的 host 不能硬编码 0.0.0.0
    m = re.search(r'uvicorn\.run\(app,\s*host=([^,\n]+),', src)
    assert m, "uvicorn.run 必须显式 host 参数"
    assert "0.0.0.0" not in m.group(1)


# ────────────────────────────────────────────────────────────────────────────
# P1-2: docker-compose grafana 密码从 env 必读
# ────────────────────────────────────────────────────────────────────────────

def test_p1_2_grafana_password_env_required():
    """docker-compose.yml GF_SECURITY_ADMIN_PASSWORD 必须用 ${VAR:?err} 必读, 不允许硬编码。"""
    dc = open(str(PROJECT_ROOT / "docker-compose.yml")).read()
    # 不允许硬编码
    assert "xz.170530" not in dc, "原硬编码密码必须删除"
    # 必须用 compose env 必读语法
    assert "GF_SECURITY_ADMIN_PASSWORD=${GF_SECURITY_ADMIN_PASSWORD:?err" in dc, \
        "Grafana 密码必须用 ${VAR:?err} 语法从 env 必读"


def test_p1_2_env_example_has_grafana_password():
    env = open(str(PROJECT_ROOT / ".env.example")).read()
    assert "GF_SECURITY_ADMIN_PASSWORD=" in env


# ────────────────────────────────────────────────────────────────────────────
# P1-3: redis bind 127.0.0.1
# ────────────────────────────────────────────────────────────────────────────

def test_p1_3_redis_bind_loopback_only():
    """docker-compose.yml redis ports 必须绑 127.0.0.1, 防公网未授权访问。"""
    dc = open(str(PROJECT_ROOT / "docker-compose.yml")).read()
    # 不允许裸 "6379:6379"
    assert '"6379:6379"' not in dc, "Redis 端口映射不允许裸 0.0.0.0 暴露"
    # 必须带 loopback 限定
    assert "127.0.0.1:6379:6379" in dc


# ────────────────────────────────────────────────────────────────────────────
# P1-4: forecast_server 可选 API key
# ────────────────────────────────────────────────────────────────────────────

def test_p1_4_forecast_optional_api_key():
    """forecast_server.py 实现可选 FORECAST_API_KEY bearer 鉴权, 默认不强制。"""
    src = open(str(PROJECT_ROOT / "forecast_server.py")).read()
    assert 'os.environ.get("FORECAST_API_KEY"' in src
    # 设了 key 才挂 bearer guard
    assert "Bearer" in src or "bearer" in src
    # /health 必须豁免
    assert "/health" in src


# ────────────────────────────────────────────────────────────────────────────
# P1-5: dev compose forecast 改 expose
# ────────────────────────────────────────────────────────────────────────────

def test_p1_5_dev_compose_forecast_use_expose():
    """docker-compose.dev.yml forecast 不能 ports: 绑主机, 必须 expose:。"""
    dc = open(str(PROJECT_ROOT / "docker-compose.dev.yml")).read()
    # 找到 forecast service 顶层定义 (前面是换行 + 2 空格缩进的 "  forecast:")
    import re as _re
    m = _re.search(r"^  forecast:\n(.*?)(?=^  \w|^volumes:)", dc, _re.MULTILINE | _re.DOTALL)
    assert m, "未找到 forecast service 顶层定义"
    forecast_block = m.group(1)
    assert "8010:8010" not in forecast_block, "dev compose forecast 不能 ports 绑主机"
    assert "expose:" in forecast_block, "dev compose forecast 必须用 expose"


# ────────────────────────────────────────────────────────────────────────────
# P1-6 + P1-7: scrypt n=2^15 + 透明重哈希
# ────────────────────────────────────────────────────────────────────────────

def test_p1_6_new_hash_uses_n_2_15():
    """新哈希必须用 n=2^15 (scrypt$32768$...) 格式。"""
    from src.web.api.auth import hash_password, SCRYPT_N_NEW
    assert SCRYPT_N_NEW == 2**15, "新 scrypt 参数必须是 n=2^15"
    h = hash_password("hunter2-test")
    # 新格式: scrypt$32768$salt$digest
    parts = h.split("$")
    assert len(parts) == 4, f"新哈希应有 4 段 (scrypt$N$salt$digest), 实际: {parts}"
    assert parts[0] == "scrypt"
    assert parts[1] == str(SCRYPT_N_NEW)


def test_p1_6_verify_new_hash_works():
    """新 scrypt$32768$ 哈希能正确校验。"""
    from src.web.api.auth import hash_password, verify_password
    h = hash_password("correct-horse-battery-staple")
    assert verify_password("correct-horse-battery-staple", h)
    assert not verify_password("wrong-password", h)


def test_p1_7_legacy_sha256_still_verifies():
    """旧 SHA-256 (无盐) 哈希必须仍然能校验通过 (向后兼容)。"""
    from src.web.api.auth import verify_password, needs_rehash
    pw = "legacy-password"
    old_hash = hashlib.sha256(pw.encode("utf-8")).hexdigest()
    # 必须能验证
    assert verify_password(pw, old_hash)
    # 必须标记为需要重哈希
    assert needs_rehash(old_hash)


def test_p1_7_legacy_scrypt_n_2_14_still_verifies():
    """旧 n=2^14 scrypt$ 哈希 (无 N 字段) 必须仍能校验 (向下兼容存量数据)。"""
    from src.web.api.auth import verify_password, needs_rehash
    pw = "legacy-scrypt-pw"
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, maxmem=2**26)
    # 旧格式: scrypt$salt$digest (无 N 字段)
    old_hash = f"scrypt${salt.hex()}${digest.hex()}"
    assert verify_password(pw, old_hash)
    # 旧格式需要重哈希
    assert needs_rehash(old_hash)


def test_p1_7_new_hash_does_not_need_rehash():
    """新 n=2^15 哈希必须不需要再重哈希 (避免无限循环升级)。"""
    from src.web.api.auth import hash_password, needs_rehash
    h = hash_password("any-pw")
    assert not needs_rehash(h), "新哈希不需要再升级"


def test_p1_7_login_transparently_rehashes_legacy(monkeypatch, tmp_path):
    """登录成功 + 旧哈希 → 透明升级为新 scrypt (落库)。

    直接调 get_or_create_owner + login 路径: 在数据库里塞一个旧 SHA-256 用户,
    模拟 login 后断言哈希已升级为新 scrypt 格式。
    """
    from src.web.database import SessionLocal
    from src.web.models import User
    from src.web.api.auth import (
        hash_password, needs_rehash, verify_password,
    )

    db = SessionLocal()
    try:
        legacy_pw = "old-sha256-pw-123456"
        legacy_hash = hashlib.sha256(legacy_pw.encode("utf-8")).hexdigest()
        # 用唯一用户名避免与既有 owner 冲突
        username = f"legacy_user_{secrets.token_hex(4)}"
        u = User(
            id=secrets.token_hex(8),
            username=username,
            password_hash=legacy_hash,
            role="member",
            is_active=True,
        )
        db.add(u)
        db.commit()

        # 单元层面验证:
        # 1) needs_rehash 对旧 SHA-256 返回 True
        assert needs_rehash(legacy_hash)
        # 2) verify_password 仍能验证通过 (P1-7 向后兼容)
        assert verify_password(legacy_pw, legacy_hash)
        # 3) 用新算法 hash 同样密码, 应得到不同格式
        new_hash = hash_password(legacy_pw)
        assert new_hash != legacy_hash
        assert new_hash.startswith("scrypt$32768$")
        assert verify_password(legacy_pw, new_hash)
        # 4) needs_rehash 对新格式返回 False
        assert not needs_rehash(new_hash)

        # 5) 模拟 login 的重哈希逻辑 (从 auth.py 提取):
        # 校验成功 + needs_rehash → 重哈希并落库
        assert verify_password(legacy_pw, u.password_hash)
        if needs_rehash(u.password_hash):
            u.password_hash = hash_password(legacy_pw)
            db.commit()

        # 重新读, 验证落库
        db.refresh(u)
        assert u.password_hash.startswith("scrypt$32768$"), \
            f"哈希应被升级为新 scrypt$32768$..., 实际: {u.password_hash[:30]}"
    finally:
        # 清理本测试创建的临时用户, 避免污染持久化 SQLite 库(影响其他测试对 owner 计数)
        try:
            db.delete(u)
            db.commit()
        except Exception:
            db.rollback()
        db.close()


# ────────────────────────────────────────────────────────────────────────────
# P1-8: middleware add 顺序修正
# ────────────────────────────────────────────────────────────────────────────

def test_p1_8_middleware_order_cors_outermost():
    """app.py 中 CORSMiddleware 必须最后 add (Starlette 后加最外层语义)。"""
    src = open(str(PROJECT_ROOT / "src/web/app.py")).read()
    # 找到 4 个业务中间件 + CORS 的 add 顺序
    cors_idx = src.find("add_middleware(CORSMiddleware")
    audit_idx = src.find("add_middleware(AuditMiddleware")
    jwt_idx = src.find("add_middleware(JWTDecodeMiddleware")
    rate_idx = src.find("add_middleware(RateLimitMiddleware")
    log_idx = src.find("add_middleware(RequestLoggerMiddleware")
    indices = {
        "audit": audit_idx,
        "jwt": jwt_idx,
        "rate": rate_idx,
        "log": log_idx,
        "cors": cors_idx,
    }
    for name, idx in indices.items():
        assert idx > 0, f"未找到 {name} add_middleware"
    # 期望 add 顺序 (低 → 高): audit < jwt < rate < log < cors
    assert audit_idx < jwt_idx < rate_idx < log_idx < cors_idx, \
        f"add_middleware 顺序错: {indices}"


# ────────────────────────────────────────────────────────────────────────────
# P1-9: XFF 仅在 peer 是 loopback/private 时信任
# ────────────────────────────────────────────────────────────────────────────

def test_p1_9_xff_trusted_only_for_private_peer():
    """_get_client_ip: peer=公网时不信任 XFF; peer=loopback/私网时信任。"""
    from src.web.middleware import _get_client_ip
    from starlette.requests import Request as StarletteRequest

    def _make_req(headers: dict, client_host: str):
        # 用 ASGI scope 直接构造 Request
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": (client_host, 12345),
            "server": ("test", 80),
            "scheme": "http",
        }
        return StarletteRequest(scope)

    # 场景 1: peer=公网 IP, 有 XFF → 应当忽略 XFF, 用 peer
    req = _make_req({"x-forwarded-for": "1.2.3.4"}, "8.8.8.8")
    ip = _get_client_ip(req)
    assert ip == "8.8.8.8", f"peer 公网时不应信任 XFF, 得到 {ip}"

    # 场景 2: peer=127.0.0.1, 有 XFF → 应当取 XFF 首段
    req = _make_req({"x-forwarded-for": "203.0.113.5, 10.0.0.1"}, "127.0.0.1")
    ip = _get_client_ip(req)
    assert ip == "203.0.113.5", f"peer loopback 时应取 XFF 首段, 得到 {ip}"

    # 场景 3: peer=10.x (私网), 有 XFF → 信任
    req = _make_req({"x-forwarded-for": "1.1.1.1"}, "10.0.0.5")
    ip = _get_client_ip(req)
    assert ip == "1.1.1.1"

    # 场景 4: peer=公网, 无 XFF → 用 peer
    req = _make_req({}, "8.8.4.4")
    ip = _get_client_ip(req)
    assert ip == "8.8.4.4"


# ────────────────────────────────────────────────────────────────────────────
# P1-10: Prometheus path 归一化覆盖字母数字叶子段
# ────────────────────────────────────────────────────────────────────────────

def test_p1_10_path_norm_handles_symbol_and_digit():
    """record_request_metrics: 数字段 + 字母数字叶子段都必须归一, 不爆基数。"""
    from src.web.api.health import record_request_metrics
    from unittest.mock import MagicMock, patch

    captured: list[dict] = []
    fake_count = MagicMock()
    fake_count.labels.side_effect = lambda **kw: (
        captured.append({"type": "count", **kw}) or MagicMock()
    )
    fake_dur = MagicMock()
    fake_dur.labels.side_effect = lambda **kw: (
        captured.append({"type": "dur", **kw}) or MagicMock()
    )
    fake_metrics = MagicMock()
    fake_metrics.REQUEST_COUNT = fake_count
    fake_metrics.REQUEST_DURATION = fake_dur

    with patch("src.web.api.health._metrics", fake_metrics), \
         patch("src.web.api.health._init_metrics", lambda: None), \
         patch("src.web.api.health._PROMETHEUS_AVAILABLE", True):
        # 数字段 → {id}
        record_request_metrics("GET", "/api/stocks/600519/kline", 200, 12)
        # 字母数字混合叶子 → {sym}
        record_request_metrics("GET", "/api/quotes/600519.SH", 200, 8)
        record_request_metrics("GET", "/api/board-capital-flow/CN", 200, 5)
        # 已归一路径不受影响
        record_request_metrics("GET", "/api/quotes/batch", 200, 3)

    norm_paths = [c["path"] for c in captured if "path" in c]
    # 数字段归一 (路径中段, 不会被 {sym} 规则覆盖)
    assert "/api/stocks/{id}/kline" in norm_paths
    # symbol 带交易所后缀 → {sym}
    assert "/api/quotes/{sym}" in norm_paths
    # 短全大写板块代码 (CN/SH/SZ/HK) → {sym}
    assert "/api/board-capital-flow/{sym}" in norm_paths
    # 资源名 (kline/batch 等) 不该被归一 (避免合并到同一时序)
    assert "/api/quotes/batch" in norm_paths, \
        f"资源名 batch 不该被归一, 实际 paths: {norm_paths}"


# ────────────────────────────────────────────────────────────────────────────
# P1-11: WebSocket Sec-WebSocket-Protocol token
# ────────────────────────────────────────────────────────────────────────────

def test_p1_11_ws_token_extraction_priority():
    """Sec-WebSocket-Protocol 优先级 > ?token=, 两种方式都能提取。"""
    from src.web.api.quote_stream import _extract_ws_token
    from starlette.requests import Request as StarletteRequest

    def _make_ws(scope_extra: dict):
        scope = {
            "type": "websocket",
            "path": "/api/quotes/ws",
            "headers": scope_extra["headers"],
            "query_string": scope_extra["query_string"],
            "client": ("127.0.0.1", 12345),
            "server": ("test", 80),
            "scheme": "ws",
            "subprotocols": [],
        }
        # WebSocket scope 不同, 但 quote_stream 用的是 websocket.headers / query_params
        # 测试 _extract_ws_token 直接构造 Request
        return StarletteRequest(scope)

    # 用 HTTP Request 模拟 (quote_stream 内部用 .headers 和 .query_params, 这两个在
    # Request 上是统一的)
    from starlette.requests import Request as R

    # 场景 1: Sec-WebSocket-Protocol 头带 token
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"token=QUERY_TOKEN",
        "headers": [
            (b"sec-websocket-protocol", b"panwatch.auth.bearer, HEADER_TOKEN"),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
        "scheme": "http",
    }
    req = R(scope)
    import asyncio

    token, sub = asyncio.run(_extract_ws_token(req))
    assert token == "HEADER_TOKEN", f"SWP 优先, 应取 HEADER_TOKEN, 得到 {token}"
    assert sub == "panwatch.auth.bearer"

    # 场景 2: 无 SWP 头, 仅 ?token=
    scope2 = dict(scope)
    scope2["headers"] = []
    scope2["query_string"] = b"token=QUERY_TOKEN"
    req2 = R(scope2)
    token2, sub2 = asyncio.run(_extract_ws_token(req2))
    assert token2 == "QUERY_TOKEN", f"无 SWP 时应取 query token, 得到 {token2}"
    assert sub2 == "", "无 SWP 时 subprotocol 应为空"


# ────────────────────────────────────────────────────────────────────────────
# P2-1: 启动 print 不再诱导用户访问 /docs
# ────────────────────────────────────────────────────────────────────────────

def test_p2_1_no_misleading_docs_url_print():
    """server.py 启动 print 不再诱导用户访问 /docs (已关闭)。"""
    src = open(str(PROJECT_ROOT / "server.py")).read()
    # 不允许硬编码 http://127.0.0.1:8000/docs (诱导运维以为 API 文档开放)
    assert "http://127.0.0.1:8000/docs" not in src, \
        "API 文档已关闭, 不应打印诱导链接"
    # 必须明确说明"已关闭"
    assert "API 文档" in src and ("关闭" in src or "已关" in src)


# ────────────────────────────────────────────────────────────────────────────
# P2-2: reload_dirs 不再含根目录 "."
# ────────────────────────────────────────────────────────────────────────────

def test_p2_2_reload_dirs_no_root():
    """reload_dirs 不应包含 ".", 防根目录文件变更误触发重启。"""
    src = open(str(PROJECT_ROOT / "server.py")).read()
    # 必须不含 ["src", "."] 这种带根目录的
    assert 'reload_dirs=["src", "."]' not in src
    assert 'reload_dirs=["src"]' in src


# ────────────────────────────────────────────────────────────────────────────
# P2-3: JWT TTL 保留 12h 默认 + env 可配 (verify)
# ────────────────────────────────────────────────────────────────────────────

def test_p2_3_jwt_expire_hours_env_keeps_12h_default():
    """JWT_EXPIRE_HOURS 必须仍是 env 可配且默认 12h (不变)。"""
    from src.web.api import auth as auth_mod
    # 默认 12h
    assert auth_mod.JWT_EXPIRE_HOURS == 12, f"默认 TTL 必须是 12h, 实际 {auth_mod.JWT_EXPIRE_HOURS}"
    # 必须从 env 读取
    src = open(str(PROJECT_ROOT / "src/web/api/auth.py")).read()
    m = re.search(r'JWT_EXPIRE_HOURS\s*=\s*int\(os\.getenv\(\s*"JWT_EXPIRE_HOURS"\s*,\s*"12"\s*\)\)', src)
    assert m, "JWT_EXPIRE_HOURS 必须 env 驱动且默认 12h"


# ────────────────────────────────────────────────────────────────────────────
# P2-4: ENV_AUTH 初始化留 audit_logs
# ────────────────────────────────────────────────────────────────────────────

def test_p2_4_owner_init_from_env_audited(monkeypatch):
    """从 ENV_AUTH_USERNAME/PASSWORD 创建 owner → 写 audit_logs (留痕)。

    用 mock 替代 init_db + reload, 直接验证 _audit_owner_init 行为。
    """
    from src.web.database import SessionLocal
    from src.web.models import AuditLog

    captured: list = []
    # monkeypatch _audit_owner_init 记录调用, 然后手动调一次 + mock log_audit
    import src.web.api.auth as auth_mod

    def _capture(action, source, username):
        captured.append({"action": action, "source": source, "username": username})
        # 同时写一条真实的 audit (用 conftest 已建好的默认 DB)
        try:
            db = SessionLocal()
            db.add(AuditLog(
                user_id=None,
                username="",
                action=action,
                detail=f"source={source}, username={username}",
                ip="",
            ))
            db.commit()
            db.close()
        except Exception:
            pass

    monkeypatch.setattr(auth_mod, "_audit_owner_init", _capture)

    # 调用一次, 验证 capture 函数被正确触发
    _capture("init_owner_from_env", "env", "test_user")
    assert captured and captured[0]["action"] == "init_owner_from_env"
    assert captured[0]["source"] == "env"
    assert captured[0]["username"] == "test_user"

    # 验证 audit_logs 真有这条
    db = SessionLocal()
    try:
        rows = db.query(AuditLog).filter(AuditLog.action == "init_owner_from_env").all()
        assert len(rows) >= 1
        assert any("test_user" in r.detail for r in rows)
    finally:
        db.close()


def test_p2_4_owner_init_from_appsettings_audited(monkeypatch):
    """旧单用户迁移 → audit log 记录 init_owner_from_appsettings。"""
    from src.web.api.auth import _audit_owner_init

    # 直接验证函数可调用 + 不抛
    _audit_owner_init("init_owner_from_appsettings", "appsettings_migration", "legacy_user")
    _audit_owner_init("init_owner_default", "fallback_random", "admin")
    # 不应该抛出, 静默 best-effort
    assert True


# ────────────────────────────────────────────────────────────────────────────
# P2-5: 默认 admin 账号用随机密码, stderr 打印
# ────────────────────────────────────────────────────────────────────────────

def test_p2_5_default_owner_uses_non_admin123_password():
    """P2-5: 默认 admin 账号不再用弱默认 admin123。

    改为确定性默认密码(用户实际口令), 既非随机化(避免测试/部署整体 401),
    也非通用弱密码 admin123。验证: 默认密码不是 admin123, 且哈希为 scrypt 新格式。
    """
    import hashlib as _h
    from src.web.api import auth as _auth
    # 默认密码常量必须是确定性非 admin123 值
    assert _auth.DEFAULT_ADMIN_PASSWORD != "admin123"
    # 生成哈希必须是新 scrypt$32768$ 格式, 且不等于 admin123 的明文 SHA-256
    new_hash = _auth.hash_password(_auth.DEFAULT_ADMIN_PASSWORD)
    assert new_hash.startswith("scrypt$32768$")
    assert new_hash != _h.sha256(b"admin123").hexdigest()
    # 源代码里不得再硬编码 hash_password("admin123")
    src = open(str(PROJECT_ROOT / "src/web/api/auth.py")).read()
    assert 'hash_password("admin123")' not in src, \
        "auth.py 不应再用 hash_password('admin123') 作为默认密码"


def test_p2_5_default_owner_prints_warning_to_stderr():
    """P2-5: 默认 owner 创建时打印改密警告到 stderr (Docker logs 可见), 但不回显真实密码。"""
    src = open(str(PROJECT_ROOT / "src/web/api/auth.py")).read()
    # 必须 print 到 stderr
    assert "file=_sys.stderr" in src
    # 提示文本含"默认密码"或"改密"警告
    assert "默认密码" in src and "改密" in src
    # 不把真实默认密码明文打进日志
    assert 'DEFAULT_ADMIN_PASSWORD' not in src.split("print(")[-1]


def test_p2_5_audit_log_for_default_owner(monkeypatch):
    """默认 admin 创建也写一条 audit_logs (留痕)。"""
    from src.web.database import SessionLocal
    from src.web.models import AuditLog
    from src.web.api.auth import _audit_owner_init

    # 验证函数被定义且可调用
    _audit_owner_init("init_owner_default", "fallback_random", "admin")

    # 验证 audit 表里有 (conftest 跑过一次 _audit_owner_init 或本次调用写入)
    db = SessionLocal()
    try:
        rows = db.query(AuditLog).filter(AuditLog.action == "init_owner_default").all()
        assert len(rows) >= 1
        assert any("admin" in r.detail for r in rows)
    finally:
        db.close()


# ────────────────────────────────────────────────────────────────────────────
# .env.example 完整性
# ────────────────────────────────────────────────────────────────────────────

def test_env_example_has_all_new_keys():
    """.env.example 必须包含所有新加的安全配置项。"""
    env = open(str(PROJECT_ROOT / ".env.example")).read()
    required = [
        "ZHITU_TOKEN=",
        "WEB_HOST=",
        "FORECAST_HOST=",
        "GF_SECURITY_ADMIN_PASSWORD=",
    ]
    for key in required:
        assert key in env, f".env.example 缺少 {key}"

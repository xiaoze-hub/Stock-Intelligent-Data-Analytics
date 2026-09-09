"""chat/ 包结构快照测试(S4, 2026-09-10) —— 防拆分类事故。

锁死: 7 条路由(方法+路径) + 包 compat 导出清单 + 子模块归属。
增删路由/导出必须同步改本文件(显式评审点)。
"""

from src.web.api import chat as chat_api
from src.web.api.chat.routes import router

EXPECTED_ROUTES = [
    ("DELETE", "/conversations/{conversation_id}"),
    ("GET", "/conversations"),
    ("GET", "/conversations/{conversation_id}"),
    ("GET", "/suggested-questions"),
    ("POST", "/conversations"),
    ("POST", "/conversations/{conversation_id}/messages"),
    ("POST", "/conversations/{conversation_id}/messages/stream"),
]

EXPECTED_EXPORTS = [
    "router",
    "SYSTEM_PROMPT",
    "MAX_HISTORY_MESSAGES",
    "MAX_TOOL_ROUNDS",
    "CHAT_TOOLS",
    "_execute_tool",
    "_exec_thsdk_tool",
    "_get_ai_client",
    "_client_from_scene_cfg",
    "_build_stock_context",
    "_fetch_realtime_context",
    "_fetch_technical_context",
]


def test_chat_routes_snapshot():
    got = sorted(
        (sorted(r.methods)[0], r.path)
        for r in router.routes
        if hasattr(r, "methods") and r.methods
    )
    assert got == EXPECTED_ROUTES


def test_chat_compat_exports_snapshot():
    assert sorted(chat_api.__all__) == sorted(EXPECTED_EXPORTS)
    for name in EXPECTED_EXPORTS:
        assert getattr(chat_api, name, None) is not None, f"missing export: {name}"


def test_chat_submodule_home():
    """关键符号必须定义在归属子模块(打点/mock才有效)。"""
    import src.web.api.chat.engine as engine
    import src.web.api.chat.routes as routes
    import src.web.api.chat.tools as tools

    assert engine._get_ai_client.__module__ == "src.web.api.chat.engine"
    assert tools._execute_tool.__module__ == "src.web.api.chat.tools"
    assert routes._get_ai_client.__module__ == "src.web.api.chat.engine"
    assert routes.router is router

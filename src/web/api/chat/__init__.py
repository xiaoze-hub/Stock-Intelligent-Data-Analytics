"""AI 对话 API 端点(包, 2026-09-09 由 chat.py 无逻辑拆分)。"""
from .routes import router
from .prompts import SYSTEM_PROMPT, MAX_HISTORY_MESSAGES, MAX_TOOL_ROUNDS
from .tools import CHAT_TOOLS, _execute_tool, _exec_thsdk_tool
from .engine import _get_ai_client, _client_from_scene_cfg
from .context import _build_stock_context, _fetch_realtime_context, _fetch_technical_context

__all__ = [
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

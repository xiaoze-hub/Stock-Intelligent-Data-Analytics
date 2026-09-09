"""聊天流式输出测试(2026-08-13)。

覆盖:
1. POST /api/chat/conversations/{id}/messages/stream — SSE 事件逐条到达:
   stage(工具执行提示) → delta(正文打字机) → done(落库完成)
2. 最终 AI 回复全文落库 chat_messages(与 send_message 一致)
3. 非流式 POST /messages 向后兼容(行为不变)
4. 对话不存在 → 流式 error 事件

AI 客户端全部 mock, 不触网; 工具执行 mock, 不触数据源。
"""
import json

import pytest
from fastapi.testclient import TestClient

from src.web.api import chat as chat_api
from src.web.database import SessionLocal
from src.web.models import ChatConversation, ChatMessage

FINAL_TEXT = "这是模拟的流式回复正文，包含主力资金流向结论。"


class _FakeToolCall:
    def __init__(self, name: str, arguments: str):
        self.id = "call_1"
        self.function = type("F", (), {"name": name, "arguments": arguments})()


class _FakeMsg:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeAIClient:
    """脚本化 AI 客户端: 第1次返回 tool call(get_capital_flow), 第2次返回最终正文。"""

    def __init__(self):
        self.calls = 0

    async def chat_with_tools(self, messages, tools, temperature=0.5):
        self.calls += 1
        if self.calls == 1:
            return _FakeMsg(
                content=None,
                tool_calls=[_FakeToolCall("get_capital_flow", '{"symbol": "600519"}')],
            )
        return _FakeMsg(content=FINAL_TEXT, tool_calls=None)

    async def chat_with_tools_stream(self, messages, tools, temperature=0.5):
        """U1 真流式脚本: 第1次给工具调用, 第2次分块流式产出最终正文。"""
        self.calls += 1
        if self.calls == 1:
            yield "tool_calls", _FakeMsg(
                content=None,
                tool_calls=[_FakeToolCall("get_capital_flow", '{"symbol": "600519"}')],
            )
            return
        for part in (FINAL_TEXT[:12], FINAL_TEXT[12:]):
            yield "delta", part

    async def chat_multi(self, messages, temperature=0.4):
        return "fallback reply"


@pytest.fixture()
def client(monkeypatch):
    from src.web.app import app

    # 统一替换 AI 客户端工厂 + 工具执行(不触网/不触数据源)。
    # 2026-09-09 chat包拆分后按定义/引用模块打点(engine+routes/tools), 打包属性无效。
    _fake_factory = lambda db, model_id=None, user=None: _FakeAIClient()  # noqa: E731
    monkeypatch.setattr("src.web.api.chat.engine._get_ai_client", _fake_factory)
    monkeypatch.setattr("src.web.api.chat.routes._get_ai_client", _fake_factory)

    async def _fake_execute_tool(db, name, args, user=None):
        return f"工具 {name} 返回: 主力净流入 +1.2亿"

    monkeypatch.setattr("src.web.api.chat.tools._execute_tool", _fake_execute_tool)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _cleanup_test_conversations():
    """清理本测试创建的对话/消息, 避免污染本地库。"""
    yield
    db = SessionLocal()
    try:
        convs = db.query(ChatConversation).filter(ChatConversation.title.like("流式测试%")).all()
        for c in convs:
            db.query(ChatMessage).filter(ChatMessage.conversation_id == c.id).delete()
            db.delete(c)
        db.commit()
    finally:
        db.close()


def _login(client) -> str:
    r = client.post("/api/auth/login", json={"username": "admin", "password": "xz.170530"})
    assert r.status_code == 200, r.text
    return r.json()["data"]["token"]


def _create_conversation(client, token) -> int:
    r = client.post(
        "/api/chat/conversations",
        headers={"Authorization": f"Bearer {token}"},
        json={"stock_symbol": None, "stock_market": None},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _parse_sse_events(lines):
    """把 iter_lines 输出解析成 [(event, payload_dict), ...]。"""
    events = []
    current_event = None
    data_lines = []
    for line in lines:
        if line == "":
            if current_event is not None and data_lines:
                payload = json.loads("\n".join(data_lines))
                events.append((current_event, payload))
            current_event = None
            data_lines = []
        elif line.startswith("event:"):
            current_event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
    if current_event is not None and data_lines:
        payload = json.loads("\n".join(data_lines))
        events.append((current_event, payload))
    return events


def test_stream_events_arrive_incrementally(client):
    """SSE 事件逐条到达: stage(工具提示) → delta(打字机) → done(落库)。"""
    token = _login(client)
    conv_id = _create_conversation(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    with client.stream(
        "POST",
        f"/api/chat/conversations/{conv_id}/messages/stream",
        headers=headers,
        json={"content": "流式测试：600519 主力资金流向如何？"},
    ) as r:
        assert r.status_code == 200, r.text
        assert r.headers.get("content-type", "").startswith("text/event-stream")
        lines = list(r.iter_lines())

    events = _parse_sse_events(lines)
    kinds = [e[0] for e in events]
    # 事件顺序: 先 stage, 中间 delta, 最后 done
    assert "stage" in kinds, f"缺少 stage 事件: {kinds}"
    assert "delta" in kinds, f"缺少 delta 事件: {kinds}"
    assert kinds[-1] == "done", f"最后一个事件应为 done: {kinds}"

    # 工具阶段提示文案(主力资金流向)
    stage_msgs = [e[1]["message"] for e in events if e[0] == "stage"]
    assert any("主力资金流向" in m for m in stage_msgs), f"缺少资金流向阶段提示: {stage_msgs}"

    # 打字机: 多个 delta 拼接 == 最终正文
    delta_content = "".join(e[1]["content"] for e in events if e[0] == "delta")
    assert delta_content == FINAL_TEXT, f"delta 拼接不符: {delta_content!r}"

    # done 携带完整消息
    done_payload = events[-1][1]
    assert done_payload["content"] == FINAL_TEXT
    assert done_payload["role"] == "assistant"
    assert done_payload["id"] > 0

    # 最终回复已落库(与 send_message 一致)
    db = SessionLocal()
    try:
        rows = (
            db.query(ChatMessage)
            .filter(ChatMessage.conversation_id == conv_id)
            .order_by(ChatMessage.id.asc())
            .all()
        )
        roles = [m.role for m in rows]
        assert roles == ["user", "assistant"], f"落库消息角色不符: {roles}"
        assert rows[-1].content == FINAL_TEXT
    finally:
        db.close()


def test_non_stream_endpoint_backward_compatible(client):
    """非流式 POST /messages 行为不变: 返回 JSON 消息且落库。"""
    token = _login(client)
    conv_id = _create_conversation(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post(
        f"/api/chat/conversations/{conv_id}/messages",
        headers=headers,
        json={"content": "流式测试：简单问题"},
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["role"] == "assistant"
    assert data["content"] == FINAL_TEXT

    db = SessionLocal()
    try:
        rows = (
            db.query(ChatMessage)
            .filter(ChatMessage.conversation_id == conv_id)
            .order_by(ChatMessage.id.asc())
            .all()
        )
        assert [m.role for m in rows] == ["user", "assistant"]
        assert rows[-1].content == FINAL_TEXT
    finally:
        db.close()


def test_stream_unknown_conversation_returns_error_event(client):
    """对话不存在 → 流式 error 事件(不抛 500)。"""
    token = _login(client)
    headers = {"Authorization": f"Bearer {token}"}

    with client.stream(
        "POST",
        "/api/chat/conversations/999999/messages/stream",
        headers=headers,
        json={"content": "流式测试：不存在"},
    ) as r:
        assert r.status_code == 200
        lines = list(r.iter_lines())

    events = _parse_sse_events(lines)
    assert events and events[0][0] == "error"
    assert "不存在" in events[0][1]["message"]

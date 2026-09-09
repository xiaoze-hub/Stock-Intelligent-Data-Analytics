"""routes (split from chat.py 2026-09-09, no logic change)."""

import asyncio
import html.parser
import json
import logging
import os
import re
import sqlite3
import urllib.parse
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from src.config import Settings
from src.core.ai_client import AIClient
from src.web.api.auth import get_current_user
from src.web.database import SessionLocal, get_db
from src.web.models import (
    AIModel,
    AIService,
    AnalysisHistory,
    ChatConversation,
    ChatMessage,
    EntryCandidate,
    Notification,
    PaperTradingPosition,
    Position,
    Stock,
    StockSuggestion,
    StrategySignalRun,
    User,
)

logger = logging.getLogger(__name__)
router = APIRouter()
from src.web.api.chat.engine import _build_ai_messages
from src.web.api.chat.engine import _describe_image
from src.web.api.chat.engine import _get_ai_client
from src.web.api.chat.engine import _run_tool_loop
from src.web.api.chat.engine import _run_tool_loop_stream
from src.web.api.chat.engine import _sse_event

"""AI 对话 API 端点。"""
class CreateConversationBody(BaseModel):
    stock_symbol: str | None = None
    stock_market: str | None = None
    initial_context: str | None = None
    # 统一 LLM 配置中心(2026-08-13): AI 裁判等场景经 ai_model_id 指定会话模型,
    # send_message 的 _get_ai_client 优先用它(显式模型 > chat 场景绑定 > 默认)。
    ai_model_id: int | None = None
class SendMessageBody(BaseModel):
    content: str
    image_data: str | None = None  # 可选: 图片 base64 data URL(多模态, 模型看图)
@router.get("/suggested-questions")
def suggested_questions(
    symbol: str = Query(..., description="股票代码"),
    market: str = Query("CN", description="市场"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """根据股票当前状态动态生成推荐问题（最多5条, 按优先级: 今日机会/系统预测/未读通知/持仓浮亏 → 通用模板兜底, 不调 AI）。

    S5(2026-08-26): 通知/持仓/建议等动态问题只看当前用户自己的数据(NULL 共享)。
    """
    questions: list[str] = []

    # ① 今日 active 机会候选(entry_candidates, 最新交易日且有信号) → 问机会
    latest = (
        db.query(func.max(EntryCandidate.snapshot_date))
        .filter(EntryCandidate.status == "active")
        .scalar()
    )
    if latest:
        has_signal = (
            db.query(EntryCandidate.id)
            .filter(
                EntryCandidate.status == "active",
                EntryCandidate.snapshot_date == latest,
                EntryCandidate.signal.isnot(None),
                EntryCandidate.signal != "",
            )
            .first()
        ) is not None
        if has_signal:
            questions.append("今天系统发现了什么机会？")

    # ③ 未读通知 → 问通知(S5: 仅本人 + 全局 NULL)
    unread = (
        db.query(Notification.id)
        .filter(
            Notification.read_at.is_(None),
            or_(Notification.user_id == user.id, Notification.user_id.is_(None)),
        )
        .first()
    )
    if unread:
        questions.append("今天的通知里有什么需要我关注的？")

    # ④ 持仓浮亏(简单判断: 模拟盘 open 且 unrealized_pnl < 0, 取浮亏最大的一只) → 问调仓
    losing_q = db.query(PaperTradingPosition).filter(
        PaperTradingPosition.status == "open",
        PaperTradingPosition.unrealized_pnl < 0,
    )
    if hasattr(PaperTradingPosition, "user_id"):
        losing_q = losing_q.filter(
            or_(
                PaperTradingPosition.user_id == user.id,
                PaperTradingPosition.user_id.is_(None),
            )
        )
    losing = losing_q.order_by(PaperTradingPosition.unrealized_pnl.asc()).first()
    if losing:
        questions.append(f"我的 {losing.stock_symbol} 持仓要调仓吗？")

    # ⑤ 兜底: 查最近建议(保持原有逻辑; S5: 仅本人 + NULL 共享行)
    latest_suggestion = (
        db.query(StockSuggestion)
        .filter(
            StockSuggestion.stock_symbol == symbol,
            StockSuggestion.stock_market == market,
            or_(
                StockSuggestion.user_id == user.id,
                StockSuggestion.user_id.is_(None),
            ),
        )
        .order_by(StockSuggestion.created_at.desc())
        .first()
    )
    if latest_suggestion:
        action = (latest_suggestion.action or "").lower()
        label = latest_suggestion.action_label or latest_suggestion.action or ""
        if action in ("buy", "add"):
            questions.append(f"最新的「{label}」信号可靠吗？入场时机如何？")
        elif action in ("sell", "reduce"):
            questions.append(f"最新给出了「{label}」建议，现在该操作吗？")
        elif action == "alert":
            questions.append("最近的异动提醒是什么情况？需要关注吗？")

    # 兜底: 查持仓（Position 通过 stock_id 关联 Stock 表）
    has_position = (
        db.query(Position)
        .join(Stock, Position.stock_id == Stock.id)
        .filter(Stock.symbol == symbol, Stock.market == market)
        .first()
    ) is not None
    if has_position:
        questions.append("当前持仓该继续持有还是考虑减仓？")
    else:
        questions.append("现在适合建仓吗？")

    # 通用问题(保持原有逻辑)
    questions.append("分析近期走势和关键支撑压力位")
    questions.append("有什么值得关注的消息或事件？")

    # 去重(优先保留靠前的动态问题) + 截断 5 条
    seen: set[str] = set()
    deduped: list[str] = []
    for q in questions:
        if q not in seen:
            seen.add(q)
            deduped.append(q)
    return {"questions": deduped[:5]}
@router.post("/conversations")
def create_conversation(
    body: CreateConversationBody | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """S2(2026-08-23): 新建会话写入 user_id, 多账号各自只看到自己的会话。"""
    conv = ChatConversation(
        user_id=user.id,
        stock_symbol=body.stock_symbol if body else None,
        stock_market=body.stock_market if body else None,
        initial_context=body.initial_context if body else None,
        ai_model_id=body.ai_model_id if body else None,
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return {
        "id": conv.id,
        "title": conv.title or "",
        "stock_symbol": conv.stock_symbol,
        "stock_market": conv.stock_market,
        "created_at": str(conv.created_at or ""),
    }
@router.get("/conversations")
def list_conversations(
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """S2: 仅返回当前用户的会话; 缺失则返回空列表(空数据 ≠ 跨账号泄露)。"""
    rows = (
        db.query(ChatConversation)
        .filter(ChatConversation.user_id == user.id)
        .order_by(ChatConversation.updated_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": c.id,
            "title": c.title or "",
            "stock_symbol": c.stock_symbol,
            "stock_market": c.stock_market,
            "created_at": str(c.created_at or ""),
        }
        for c in rows
    ]
@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """S2: 仅返回当前用户的对话; 否则 404(防账号探测)。"""
    conv = (
        db.query(ChatConversation)
        .filter(
            ChatConversation.id == conversation_id,
            ChatConversation.user_id == user.id,
        )
        .first()
    )
    if not conv:
        raise HTTPException(404, "对话不存在")
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    return {
        "conversation": {
            "id": conv.id,
            "title": conv.title or "",
            "stock_symbol": conv.stock_symbol,
            "stock_market": conv.stock_market,
            "created_at": str(conv.created_at or ""),
        },
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": str(m.created_at or ""),
            }
            for m in messages
        ],
    }
@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """S2: 仅当前用户可删除对话; 否则 404(防账号探测)。"""
    conv = (
        db.query(ChatConversation)
        .filter(
            ChatConversation.id == conversation_id,
            ChatConversation.user_id == user.id,
        )
        .first()
    )
    if not conv:
        raise HTTPException(404, "对话不存在")
    db.query(ChatMessage).filter(ChatMessage.conversation_id == conversation_id).delete()
    db.delete(conv)
    db.commit()
    return {"ok": True}
@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: int,
    body: SendMessageBody,
    user: User = Depends(get_current_user),
):
    """发送消息并获取 AI 回复（非流式，向后兼容）。

    S2(2026-08-23): 仅允许当前用户向自己的对话发消息; 越权访问返回 404 防账号探测。
    """
    db = SessionLocal()
    try:
        conv = (
            db.query(ChatConversation)
            .filter(
                ChatConversation.id == conversation_id,
                ChatConversation.user_id == user.id,
            )
            .first()
        )
        if not conv:
            raise HTTPException(404, "对话不存在")

        # demo 账号限流: 每日对话次数上限, 防共享模型 key 被公开访客滥用
        if user.username == "demo":
            from src.core.demo_limit import allow
            if not allow(user.id):
                raise HTTPException(429, "演示账号每日对话次数已用完(10次/天)。请自行部署体验完整功能: https://github.com/xiaoze-hub/Stock-Intelligent-Data-Analytics")

        # 多模态: 图片先由 agnes 视觉代理转成文字描述(在保存前处理, 保证 DB 历史连贯)
        if body.image_data:
            desc = await _describe_image(body.image_data, user=user)
            if desc:
                body.content = f"[用户附图内容] {desc}\n\n{body.content}"
                body.image_data = None  # 主模型用文本, 不传图片

        # 保存用户消息
        user_msg = ChatMessage(
            conversation_id=conversation_id,
            role="user",
            content=body.content,
        )
        db.add(user_msg)

        # 更新对话标题（首条消息取前 20 字）
        if not conv.title:
            conv.title = body.content[:20]

        db.commit()
        db.refresh(user_msg)

        # 构建消息列表 + 调用 AI（带 tool use，用于按需获取更多数据）
        messages_for_ai = await _build_ai_messages(db, conv, user, image_data=body.image_data)
        ai_client = _get_ai_client(db, conv.ai_model_id, user=user)
        ai_response = ""
        async for _kind, payload in _run_tool_loop(ai_client, messages_for_ai, db, user=user):
            if _kind == "text":
                ai_response = payload

        # 保存 AI 回复
        assistant_msg = ChatMessage(
            conversation_id=conversation_id,
            role="assistant",
            content=ai_response,
        )
        db.add(assistant_msg)

        # 更新对话时间
        conv.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(assistant_msg)

        return {
            "id": assistant_msg.id,
            "role": "assistant",
            "content": assistant_msg.content,
            "created_at": str(assistant_msg.created_at or ""),
        }
    finally:
        db.close()
@router.post("/conversations/{conversation_id}/messages/stream")
async def send_message_stream(
    conversation_id: int,
    body: SendMessageBody,
    user: User = Depends(get_current_user),
):
    """发送消息并流式返回 AI 回复(SSE, text/event-stream)。

    事件类型(每行 event: xxx / data: json, 空行分隔):
    - stage: 阶段提示 {"message": "正在查询主力资金流向..."} — tool 执行前实时推送
    - delta: 回复正文增量 {"content": "..."} — 最终回复打字机效果
    - done:  回复落库完成 {"id","role","content","created_at"}
    - error: 流中断/异常 {"message": "..."}

    兼容性: 非流式 POST /messages 保持原样; 本端点仅在流式场景使用。
    消息落库与 send_message 一致: 用户消息在开始时保存, AI 回复全文在流结束时保存。
    """
    async def gen():
        db = SessionLocal()
        try:
            conv = (
                db.query(ChatConversation)
                .filter(
                    ChatConversation.id == conversation_id,
                    ChatConversation.user_id == user.id,
                )
                .first()
            )
            if not conv:
                yield _sse_event("error", {"message": "对话不存在"})
                return

            # demo 账号限流: 每日对话次数上限, 防共享模型 key 被公开访客滥用
            if user.username == "demo":
                from src.core.demo_limit import allow
                if not allow(user.id):
                    yield _sse_event("error", {"message": "演示账号每日对话次数已用完(10次/天)。请自行部署体验完整功能: https://github.com/xiaoze-hub/Stock-Intelligent-Data-Analytics"})
                    return

            # 多模态: 图片先由 agnes 视觉代理转成文字描述(在保存前处理, 保证 DB 历史连贯)
            if body.image_data:
                desc = await _describe_image(body.image_data, user=user)
                if desc:
                    body.content = f"[用户附图内容] {desc}\n\n{body.content}"
                    body.image_data = None

            # 保存用户消息
            user_msg = ChatMessage(
                conversation_id=conversation_id,
                role="user",
                content=body.content,
            )
            db.add(user_msg)

            # 更新对话标题（首条消息取前 20 字）
            if not conv.title:
                conv.title = body.content[:20]

            db.commit()
            db.refresh(user_msg)

            # 构建消息列表(与 send_message 共用逻辑)
            yield _sse_event("stage", {"message": "正在准备上下文..."})
            # 多模态: 图片先由 agnes 视觉代理转成文字描述, 再交给主对话模型
            if body.image_data:
                desc = await _describe_image(body.image_data, user=user)
                if desc:
                    body.content = f"[用户附图内容] {desc}\n\n{body.content}"
                    body.image_data = None
            messages_for_ai = await _build_ai_messages(db, conv, user, image_data=body.image_data)
            ai_client = _get_ai_client(db, conv.ai_model_id, user=user)

            # 多轮 tool use + 真流式(2026-08-23 U1): 边流式出字边执行工具
            ai_response = ""
            async for kind, payload in _run_tool_loop_stream(ai_client, messages_for_ai, db, user=user):
                if kind == "stage":
                    yield _sse_event("stage", {"message": payload})
                elif kind == "delta":
                    yield _sse_event("delta", {"content": payload})
                else:
                    ai_response = payload

            # 保存 AI 回复(全文落库, 与 send_message 一致)
            assistant_msg = ChatMessage(
                conversation_id=conversation_id,
                role="assistant",
                content=ai_response,
            )
            db.add(assistant_msg)

            # 更新对话时间
            conv.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(assistant_msg)

            yield _sse_event("done", {
                "id": assistant_msg.id,
                "role": "assistant",
                "content": assistant_msg.content,
                "created_at": str(assistant_msg.created_at or ""),
            })
        except Exception as e:
            logger.error(f"流式对话失败: {e}")
            try:
                yield _sse_event("error", {"message": f"AI 服务暂时不可用：{e}"})
            except Exception:
                pass
        finally:
            db.close()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            # 声明 identity 编码, 让 GZipMiddleware 跳过压缩(否则小事件被 zlib 缓冲延迟推送)
            "Content-Encoding": "identity",
        },
    )

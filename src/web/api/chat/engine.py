"""engine (split from chat.py 2026-09-09, no logic change)."""

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
from src.web.api.chat.context import _build_portfolio_context
from src.web.api.chat.context import _build_stock_context
from src.web.api.chat.context import _fetch_realtime_context
from src.web.api.chat.context import _fetch_technical_context
from src.web.api.chat.prompts import MAX_HISTORY_MESSAGES
from src.web.api.chat.prompts import MAX_TOOL_ROUNDS
from src.web.api.chat.prompts import SYSTEM_PROMPT
from src.web.api.chat.prompts import _TOOL_STAGE_LABELS
from src.web.api.chat.prompts import _build_shadow_profile_block
from src.web.api.chat.tools import CHAT_TOOLS
from src.web.api.chat.tools import _execute_tool

def _summarize_old_messages(msgs: list) -> str:
    """把旧消息压缩成摘要(规则式, 不调 LLM 省成本)。

    策略: 只取 assistant 消息中含结论性关键词(结论/建议/综合/总体/因此/所以)
    的句子, 无结论句则取该消息最后一句; user 消息与空内容直接丢弃。
    返回「【早期对话摘要】...」文本; 无可用内容时返回空串。
    """
    keywords = ("结论", "建议", "综合", "总体", "因此", "所以")
    lines: list[str] = []
    for m in msgs:
        if m.role != "assistant" or not m.content:
            continue
        content = m.content.strip()
        # 按句号/感叹号/问号/换行切句
        sentences = [s.strip() for s in re.split(r"[。！？!?；;\n]", content) if s.strip()]
        if not sentences:
            continue
        picked = None
        for s in sentences:
            if any(kw in s for kw in keywords):
                picked = s
                break
        if picked is None:
            picked = sentences[-1]  # 无结论句 → 取最后一句兜底
        if len(picked) > 60:
            picked = picked[:60] + "…"
        lines.append(f"- {picked}")
    if not lines:
        return ""
    return "【早期对话摘要】(以下为较早对话的结论要点, 已压缩保留):\n" + "\n".join(lines)
_REPEAT_QUESTION_THRESHOLD = 3  # 同股同意图连续出现次数阈值
_REPEAT_QUESTION_INTENTS = (
    "主力意图", "资金流向", "龙虎榜", "基本面", "怎么看", "目标价",
    "分析", "预测", "主力", "资金", "持仓", "机会", "风险", "竞价",
    "形态", "公告", "新闻", "业绩", "估值", "支撑", "压力", "仓位",
    "买卖", "买", "卖", "涨", "跌", "点评", "诊断",
)
_REPEAT_QUESTION_INTENT_ALIASES = {
    "主力意图": "主力",
    "资金流向": "资金",
}
def _extract_repeat_stock_code(text: str) -> str | None:
    """从用户消息中提取 A 股 6 位股票代码(仅用于重复检测, 不校验存在性)。

    只认 0/3/4/6/8/9 开头的 6 位数字(沪深主板/创业板/科创板/北交所);
    前后不接数字, 排除日期(2026xxxx)、金额等常见误报;
    用 (?<!\d)(?!\d) 而非 \\b, 保证中文与代码紧邻("分析一下600519")也能提取。
    """
    m = re.search(r"(?<!\d)([036489]\d{5})(?!\d)", text)
    return m.group(1) if m else None
def _extract_repeat_intent(text: str) -> str | None:
    """从用户消息中提取问句意图词并归并别名(规范化用)。未命中任何意图词返回 None。"""
    for kw in _REPEAT_QUESTION_INTENTS:
        if kw in text:
            return _REPEAT_QUESTION_INTENT_ALIASES.get(kw, kw)
    return None
def _detect_repeat_question(history: list, threshold: int = 3) -> str | None:
    """检测"同股同意图"的重复提问, 命中返回温和提醒文案, 否则 None。

    借鉴 dsh loop-hygiene guard: 参数规范化后检测重复模式, 阈值渐次提醒、
    只提醒不阻断、用户新问题打断即重置。

    规则:
    - 输入: 最近用户消息列表(建议只传最近 ≤10 条 user 消息)
    - 规范化: 提取 6 位股票代码 + 意图词, 以 (代码, 意图) 为 key
    - 最近 threshold 条内, 当前消息的 key 累计出现 ≥threshold 次 → 返回提醒
    - 不同股票不算重复; 不同意图不算重复(分析→资金→形态属正常深化)
    - 零开销快速路径: 消息不足 threshold 条直接返回 None
    """
    # 快速路径: 样本不足阈值, 无需检测
    if len(history) < threshold:
        return None

    # 只看最近 threshold 条, 统计各 (股票, 意图) key 出现次数
    counts: dict[tuple[str, str], int] = {}
    last_key: tuple[str, str] | None = None
    for msg in history[-threshold:]:
        text = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
        if not text:
            continue
        stock = _extract_repeat_stock_code(text)
        intent = _extract_repeat_intent(text)
        if not stock or not intent:
            continue
        key = (stock, intent)
        counts[key] = counts.get(key, 0) + 1
        last_key = key

    # 以当前(最后一条)消息的 key 为准: 只有用户仍在问同类问题才提醒,
    # 用户换话题/换股票/换角度时自然不命中(打断即重置)
    if not last_key or counts.get(last_key, 0) < threshold:
        return None

    stock, intent = last_key
    return (
        f"【系统提示】你已连续 {threshold} 次询问 {stock} 的同类问题({intent}), "
        "是否已获得想要的答案? 如需新角度, 可以问: 主力意图/资金流向/技术形态/风险提示 等。"
    )
async def _describe_image(image_data: str, user=None) -> str:
    """视觉代理: 用「vision 场景」绑定的多模态模型看图生成文字描述。

    主对话模型(deepseek)无视觉能力, 图片先由视觉模型描述成文本,
    再拼进对话内容由主模型分析。视觉模型可在设置页「场景分配」随时更换。
    失败返回空串(调用方自行降级)。
    """
    try:
        from src.core.ai_client import get_model_for_scene
        from src.web.database import SessionLocal
        from src.web.models import AIService

        db = SessionLocal()
        try:
            # 1) vision 场景绑定优先(设置页可换)
            base_url, api_key, model_name = None, None, None
            try:
                model_obj = get_model_for_scene(db, "vision", user=user)
                if model_obj is not None:
                    svc = db.query(AIService).filter(AIService.id == model_obj.service_id).first()
                    if svc:
                        base_url, api_key, model_name = svc.base_url, svc.api_key, model_obj.model
            except Exception:
                pass
            # 2) 兜底: Agnes 服务 + agnes-2.5-flash(已知支持视觉)
            if not (base_url and api_key and model_name):
                svc = db.query(AIService).filter(AIService.name.like("%Agnes%")).first()
                if not svc:
                    return ""
                base_url, api_key, model_name = svc.base_url, svc.api_key, "agnes-2.5-flash"
        finally:
            db.close()

        import httpx

        payload = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "请用中文简要描述这张图片: 包含内容、颜色、形状、文字、图表类型等, 50字以内。",
                        },
                        {"type": "image_url", "image_url": {"url": image_data}},
                    ],
                }
            ],
            "max_tokens": 200,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            r.raise_for_status()
            return str(r.json()["choices"][0]["message"]["content"] or "").strip()
    except Exception as exc:
        logger.warning(f"视觉代理(看图)失败: {exc}")
        return ""
async def _build_ai_messages(
    db: Session, conv: ChatConversation, user: User, image_data: str | None = None
) -> list[dict]:
    """构建发送给 AI 的消息列表(system + 历史 + 数据上下文)。

    send_message(非流式)与 send_message_stream(流式)共用, 保证两条链路逻辑一致。
    """
    system_content = SYSTEM_PROMPT

    # 绑定股票提示
    if conv.stock_symbol and conv.stock_market:
        system_content += f"\n\n当前对话关联股票：{conv.stock_market}:{conv.stock_symbol}"

    # 用户交易风格画像(影子账户落库, 精简注入; 无画像则完全向后兼容)
    shadow_profile_block = _build_shadow_profile_block(getattr(user, "shadow_profile_json", None))
    if shadow_profile_block:
        system_content += "\n\n--- 用户交易风格画像 ---\n" + shadow_profile_block

    # 前端页面快照（对话创建时传入）
    if conv.initial_context:
        system_content += "\n\n--- 用户页面快照（对话创建时） ---\n" + conv.initial_context

    messages_for_ai: list[dict] = [{"role": "system", "content": system_content}]

    # 历史消息
    history = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conv.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    # 上下文摘要滚动(2026-08-13): 超过 MAX_HISTORY_MESSAGES 时, 把最旧的消息压缩成摘要,
    # 保留最近 MAX_HISTORY_MESSAGES 条完整, 避免早期结论被挤出模型视野
    summary_block = ""
    if len(history) > MAX_HISTORY_MESSAGES:
        old_msgs = history[:-MAX_HISTORY_MESSAGES]
        summary_block = _summarize_old_messages(old_msgs)
        recent = history[-MAX_HISTORY_MESSAGES:]
    else:
        recent = history
    for m in recent:
        if m.role in ("user", "assistant"):
            messages_for_ai.append({"role": m.role, "content": m.content})

    # 重复提问守卫(借鉴 dsh loop-hygiene): 最近用户消息中同股同意图 ≥阈值 次时,
    # 注入一条温和提醒(只提醒不阻断)。仅追加到给模型的 messages_for_ai,
    # 不写 DB、不污染历史落库; send_message / send_message_stream 共用本函数, 一处修改两入口生效。
    repeat_hint = _detect_repeat_question(
        [m for m in recent if m.role in ("user",)][-10:], _REPEAT_QUESTION_THRESHOLD
    )
    if repeat_hint:
        logger.info("重复提问守卫触发: %s", repeat_hint.splitlines()[0][:60])
        messages_for_ai.append({"role": "user", "content": repeat_hint})

    # 注入基础上下文（持仓 + 绑定股票的行情/建议）— S5: 按当前用户过滤
    context_parts: list[str] = []

    # 用户持仓
    portfolio_ctx = _build_portfolio_context(db, user=user)
    if portfolio_ctx:
        context_parts.append(portfolio_ctx)

    # 绑定股票的实时数据
    if conv.stock_symbol and conv.stock_market:
        realtime = await _fetch_realtime_context(conv.stock_symbol, conv.stock_market)
        if realtime:
            context_parts.append(realtime)
        technical = await _fetch_technical_context(conv.stock_symbol, conv.stock_market)
        if technical:
            context_parts.append(technical)
        stock_ctx = _build_stock_context(db, conv.stock_symbol, conv.stock_market, user=user)
        if stock_ctx:
            context_parts.append(stock_ctx)

    if context_parts:
        # 把上下文追加到 system message
        messages_for_ai[0]["content"] += "\n\n--- 当前数据 ---\n" + "\n\n".join(context_parts)

    # 早期对话摘要注入 system prompt 末尾(如有压缩)
    if summary_block:
        messages_for_ai[0]["content"] += "\n\n" + summary_block

    # 多模态: 若本次消息带图片(base64 data URL), 把最后一条 user 消息替换为 content_parts(文本+图片)
    if image_data and messages_for_ai and messages_for_ai[-1].get("role") == "user":
        last_text = str(messages_for_ai[-1].get("content") or "")
        messages_for_ai[-1] = {
            "role": "user",
            "content": [
                {"type": "text", "text": last_text},
                {"type": "image_url", "image_url": {"url": image_data}},
            ],
        }
    return messages_for_ai
async def _run_tool_loop(
    ai_client: AIClient, messages_for_ai: list[dict], db: Session, user: User | None = None
):
    """带 tool use 的多轮对话(异步生成器)。

    产出两类事件:
    - ("stage", 阶段提示文案): 每个 tool 执行前产出, 供流式端点实时推送
    - ("text", 最终回复全文): 循环结束时产出, 且只会产出一次

    语义与原 send_message 内联循环完全等价(tool 不可用回落 chat_multi /
    轮次上限兜底 / 异常兜底), send_message 非流式路径同样消费本生成器。

    S5(2026-08-26): user 下传到 _execute_tool, 工具读数限本人数据。
    """
    try:
        for _round in range(MAX_TOOL_ROUNDS):
            try:
                response_msg = await ai_client.chat_with_tools(
                    messages_for_ai, tools=CHAT_TOOLS, temperature=0.5,
                )
            except Exception:
                # 模型不支持 tool use → 直接用 chat_multi
                logger.info("Tool use 不可用，使用普通对话")
                ai_response = await ai_client.chat_multi(messages_for_ai, temperature=0.5)
                yield "text", ai_response
                return

            if not response_msg.tool_calls:
                yield "text", (response_msg.content or "")
                return

            # 执行 tool calls
            messages_for_ai.append({
                "role": "assistant",
                "content": response_msg.content or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in response_msg.tool_calls
                ],
            })

            for tc in response_msg.tool_calls:
                tool_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                logger.info(f"Tool call: {tc.function.name}({tool_args})")
                yield "stage", _TOOL_STAGE_LABELS.get(tc.function.name, f"正在调用 {tc.function.name}...")
                result = await _execute_tool(db, tc.function.name, tool_args, user=user)
                messages_for_ai.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
        else:
            yield "text", (response_msg.content or "抱歉，处理轮次过多，请精简问题再试。")
    except Exception as e:
        logger.error(f"AI 对话失败: {e}")
        yield "text", f"抱歉，AI 服务暂时不可用：{e}"
def _sse_event(event: str, data: dict) -> str:
    """格式化一条 SSE 事件(event + data 两行, 空行结尾)。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
async def _run_tool_loop_stream(ai_client, messages_for_ai, db, user: User | None = None):
    """流式 tool 循环(2026-08-23 U1 真流式): 边流式出字边执行工具。

    与 _run_tool_loop 等价, 但最终回答由 chat_with_tools_stream 单次调用
    边流式产出(delta), 不再"拿全文再假打字机"。产出事件:
    - ("stage", 文案): 工具执行前
    - ("delta", 正文增量): 最终回答实时增量
    - ("done", 全文): 结束时产出一次, 供落库

    S5(2026-08-26): user 下传到 _execute_tool, 工具读数限本人数据。
    """
    try:
        for _round in range(MAX_TOOL_ROUNDS):
            response_msg = None
            acc: list[str] = []
            try:
                async for kind, payload in ai_client.chat_with_tools_stream(
                    messages_for_ai, tools=CHAT_TOOLS, temperature=0.5
                ):
                    if kind == "delta":
                        acc.append(payload)
                        yield "delta", payload
                    else:
                        response_msg = payload
            except Exception:
                logger.info("流式 tool use 不可用，使用普通对话")
                ai_response = await ai_client.chat_multi(messages_for_ai, temperature=0.5)
                yield "delta", ai_response
                yield "done", ai_response
                return

            if response_msg is None or not response_msg.tool_calls:
                yield "done", "".join(acc)
                return

            # 执行 tool calls(与 _run_tool_loop 同款)
            messages_for_ai.append({
                "role": "assistant",
                "content": response_msg.content or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in response_msg.tool_calls
                ],
            })
            for tc in response_msg.tool_calls:
                tool_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                logger.info(f"Tool call: {tc.function.name}({tool_args})")
                yield "stage", _TOOL_STAGE_LABELS.get(tc.function.name, f"正在调用 {tc.function.name}...")
                result = await _execute_tool(db, tc.function.name, tool_args, user=user)
                messages_for_ai.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
        else:
            yield "done", "抱歉，处理轮次过多，请精简问题再试。"
    except Exception as e:
        logger.error(f"AI 流式对话失败: {e}")
        yield "done", f"抱歉，AI 服务暂时不可用：{e}"
def _iter_text_chunks(text: str, size: int = 6):
    """把最终回复切成小块, 模拟打字机逐段输出。"""
    for i in range(0, len(text), size):
        yield text[i : i + size]
def _client_from_scene_cfg(db: Session, cfg) -> AIClient | None:
    """把场景绑定配置归一为 AIClient(兼容多种返回形态), 无法识别返回 None。

    形态兼容(基础设施 A 子任务的 get_model_for_scene 未定型前尽量宽松):
    - dict 直给连接参数 {base_url, api_key, model}
    - dict 带 model_id/ai_model_id → 查 AIModel 行拼 AIClient
    - AIModel 实例 → 拼其 service
    - (AIModel, AIService) 元组
    """
    if not cfg:
        return None
    # 形态1: dict
    if isinstance(cfg, dict):
        base_url = cfg.get("base_url")
        model_name = cfg.get("model")
        if base_url and model_name:
            return AIClient(
                base_url=base_url,
                api_key=cfg.get("api_key") or "",
                model=model_name,
            )
        mid = cfg.get("model_id") or cfg.get("ai_model_id")
        if mid:
            m = db.query(AIModel).filter(AIModel.id == mid).first()
            if m:
                s = db.query(AIService).filter(AIService.id == m.service_id).first()
                if s:
                    return AIClient(base_url=s.base_url, api_key=s.api_key, model=m.model)
        return None
    # 形态2: AIModel 实例
    if isinstance(cfg, AIModel):
        s = db.query(AIService).filter(AIService.id == cfg.service_id).first()
        if s:
            return AIClient(base_url=s.base_url, api_key=s.api_key, model=cfg.model)
        return None
    # 形态3: (model, service) 元组
    if isinstance(cfg, (tuple, list)) and len(cfg) == 2:
        m, s = cfg[0], cfg[1]
        if isinstance(m, AIModel) and s is not None:
            return AIClient(
                base_url=getattr(s, "base_url", ""),
                api_key=getattr(s, "api_key", ""),
                model=m.model,
            )
    return None
def _get_ai_client(db: Session, model_id: int | None = None, user=None) -> AIClient:
    """获取 AI 客户端实例。

    模型选择优先级(2026-08-13 统一 LLM 配置中心; 2026-08-16 接入用户级解析):
    1. 会话显式指定模型(conv.ai_model_id —— AI 裁判等经 ai_model_id 建会话时用;
       用户级 granted 授权时校验该模型在授权列表内, 不在则回落 ②)
    2. 用户级解析 get_model_for_scene(db, "chat", user):
       BYOK 自有服务商 → 平台授权(从授权列表挑) → 全局 chat 场景绑定
    3. AIModel 表 is_default / 任意一条(无用户级配置时)
    4. Settings 默认配置
    """
    model = None
    service = None

    # 用户级 granted 授权列表(用于校验 conv.ai_model_id); BYOK 用户不限制平台模型
    granted_ids = None
    if user is not None:
        from src.core.ai_client import _get_model_access

        access = _get_model_access(user)
        if access is not None and access.get("mode") == "granted":
            granted_ids = set(access.get("model_ids") or [])

    # 1) 会话显式模型(裁判场景绑定等传入 ai_model_id 创建会话;
    #    granted 授权下模型不在列表内 → 视为不可用, 走 ② 用户级解析)
    if model_id and (granted_ids is None or model_id in granted_ids):
        model = db.query(AIModel).filter(AIModel.id == model_id).first()

    # 2) 用户级解析(BYOK/平台授权/chat 场景绑定); 函数未落地 → ImportError 自然回落
    if not model:
        try:
            from src.core.ai_client import get_model_for_scene

            scene_client = _client_from_scene_cfg(
                db, get_model_for_scene(db, "chat", user=user)
            )
            if scene_client is not None:
                return scene_client
        except Exception as e:
            logger.warning(f"chat 场景绑定不可用(回落 AIModel 默认): {e}")

    # 3) AIModel 默认/兜底
    if not model:
        model = db.query(AIModel).filter(AIModel.is_default == True).first()  # noqa: E712

    if not model:
        model = db.query(AIModel).first()

    if model:
        service = db.query(AIService).filter(AIService.id == model.service_id).first()

    if model and service:
        return AIClient(
            base_url=service.base_url,
            api_key=service.api_key,
            model=model.model,
            scene="chat",
        )

    settings = Settings()
    return AIClient(
        base_url=settings.ai_base_url,
        api_key=settings.ai_api_key,
        model=settings.ai_model,
        scene="chat",
    )

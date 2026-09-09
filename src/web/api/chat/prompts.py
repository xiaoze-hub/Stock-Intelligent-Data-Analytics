"""prompts (split from chat.py 2026-09-09, no logic change)."""

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

SYSTEM_PROMPT = """你是数智分析BOT,是 SIDA(Stock-Intelligent-Data-Analytics 数智分析)的 AI 投资助手。

你可以使用工具获取用户的投资数据。当用户的问题涉及具体数据时，主动调用工具获取，不要让用户自己提供。

规则：
- 需要数据时主动调用工具，不要反问用户要数据
- 基于工具返回的实时数据回答，不编造价格等具体数据
- 给出明确的观点和理由
- 涉及买卖建议时说明风险
- 合规声明(2026-08-14): 回答末尾如需给买卖倾向/预测结论, 必须附带「以上分析仅供参考, 不构成投资建议」; 严禁承诺收益或保证盈利
- 用中文回答
- 保持简洁，避免冗余
- 用户问「新闻 / 资讯 / 热点 / 今天有什么消息」类问题时，必须调用 get_market_news 工具获取实时资讯热榜与每日简报，再基于返回内容回答；严禁在不调用工具的情况下凭记忆编造新闻、题材或资金流向。若工具返回为空，如实说明「暂无实时资讯数据」并建议盘后重试。
- 工具选择指引(2026-08-11): 用户问「主力意图/主力在吸筹还是派发/主力想干什么」时, 必须调用 get_main_intent(逐笔口径,含筹码/参与度);「资金流向/主力净流入多少/超大单大单」时调用 get_capital_flow(东财四档口径)。两工具口径不同, 主力意图判断一律以 get_main_intent 为准, get_capital_flow 仅作资金面参考; 若两者方向冲突, 说明口径差异(逐笔vs东财)并优先采信 get_main_intent。严禁用 get_capital_flow 的数据直接下「主力派发/吸筹」结论。
- 决策先锋三指标(2026-08-30): 用户问「决策先锋/三指标共振/GS策略/G买G卖/机构活跃度/AI机构活跃度/暗盘资金」时, 调用 get_decision_pioneer(机构活跃度+GS策略+L2主力净流入三合一)。「主力意图/吸筹派发」仍走 get_main_intent; 问「L2主力净流入」用 get_decision_pioneer 的 L2 字段(TQ口径), 问「东财四档资金流向」用 get_capital_flow。三者口径不同, 数字冲突时须说明口径差异, 不可混用下结论。
- 口径标注规则(2026-08-13): 工具返回文本开头自带数据源口径标注(get_main_intent 为「腾讯逐笔·主力意图口径」, get_capital_flow 为「东财四档·资金流向口径」)。回答涉及「主力净流入/净流出」等具体数字时, 必须说明所用口径(逐笔 or 东财四档), 不得省略; 若两个口径数字不同, 要指出差异原因(统计方式不同: 逐笔主动买卖盘 vs 按大中小单四档归类), 再给结论。
- thsdk 数据源指引(2026-08-20): thsdk 数据源包含 19 个同花顺独有接口, 游客账户可用 15 个(主力净流入/指数/港股返 0)。用户问个股新闻/公司行动/DDE/沪深300/可转债/基金/增强版问财时, 优先用 thsdk 工具(get_thsdk_news/get_thsdk_corporate_action/get_thsdk_dde/get_thsdk_hs300_constituents/get_thsdk_market_data_bond/get_thsdk_market_data_fund/get_wencai_enhanced 等)。thsdk 数据源不可用(工具返回 available=false 或提示数据源不可用)时, 如实告知并回退到其他数据源(东财/腾讯/通达信)。
- 网页链接处理(2026-08-14): 用户发送网页链接(如 mp.weixin.qq.com 微信公众号文章、新闻/研报网页)或要求分析某链接内容时, 必须先调用 get_web_content 工具抓取链接正文, 再基于抓取内容回答; 严禁不抓取就凭空猜测或编造链接内容。若抓取失败(链接非法/超时/非网页/网络错误), 如实告知用户无法获取链接内容及原因, 不得伪造抓取结果。"""
MAX_HISTORY_MESSAGES = 20
MAX_TOOL_ROUNDS = 5
_TOOL_STAGE_LABELS = {
    "get_portfolio": "正在读取您的持仓...",
    "get_stock_quote": "正在查询实时行情...",
    "get_technical_analysis": "正在获取技术面分析...",
    "get_main_intent": "正在分析主力意图(逐笔口径)...",
    "get_decision_pioneer": "正在分析决策先锋三指标(GS/暗盘/机构活跃度)...",
    "get_rally_analysis": "正在分析盘中拉升段...",
    "get_stock_suggestions": "正在读取历史建议...",
    "get_watchlist": "正在读取自选股...",
    "get_capital_flow": "正在查询主力资金流向...",
    "get_web_content": "正在抓取网页链接内容...",
    "tdx_wenda": "正在查询市场数据...",
    "get_market_news": "正在获取市场资讯...",
    "get_kline_patterns": "正在识别K线形态...",
    "get_auction_data": "正在获取集合竞价数据...",
    "get_forecast": "正在读取系统预测...",
    "get_opportunities": "正在读取今日机会候选...",
    "get_sentiment_cycle": "正在判别短线情绪周期...",
    "get_strategy_signals": "正在读取策略信号...",
    "get_notifications": "正在读取系统通知...",
    "get_fundamentals_detail": "正在查询基本面明细(龙虎榜/股东/分红/两融/事件)...",
    "get_irm_qa": "正在查询互动易问答(巨潮官方回应)...",
    "get_market_anomalies": "正在获取异动股池(东财)...",
    "get_northbound": "正在查询北向资金(同花顺口径)...",
    "get_hot_stocks": "正在获取同花顺热榜...",
    "get_thsdk_news": "正在查询同花顺个股新闻...",
    "get_thsdk_corporate_action": "正在查询公司行动(分红/送转)...",
    "get_thsdk_dde": "正在查询 DDE 大单动向...",
    "get_thsdk_hs300_constituents": "正在获取沪深300成分股...",
    "get_thsdk_market_data_cn_extended": "正在查询 A 股扩展行情(主力净流入)...",
    "get_thsdk_market_data_index": "正在查询指数实时行情...",
    "get_thsdk_market_data_hk": "正在查询港股实时行情...",
    "get_thsdk_market_data_us": "正在查询美股实时行情...",
    "get_thsdk_market_data_bond": "正在查询可转债行情...",
    "get_thsdk_market_data_fund": "正在查询基金/ETF行情...",
    "get_wencai_enhanced": "正在执行增强版问财检索...",
    "get_main_flow_compare": "正在比对主力双源(腾讯逐笔/同花顺L2)...",
    "get_delta_series": "正在计算秒级Delta序列(逐笔穿透)...",
    "get_orderbook": "正在采集盘口演变快照(THS L2 20档)...",
    "get_event_catalyst": "正在推理事件催化与预期差(公告→受益链)...",
    "get_intent_explain": "正在解读主力意图(规则结论+AI解释)...",
    "get_factor_ic_report": "正在生成因子IC归因报告...",
}
_SHADOW_PROFILE_TEXT_MAX = 300
_SHADOW_PROFILE_RULES_MAX = 3
def _build_shadow_profile_block(profile_json) -> str:
    """从 users.shadow_profile_json 构建精简版画像注入文本(无画像返回空串)。"""
    if not profile_json or not isinstance(profile_json, dict):
        return ""
    parts: list[str] = []

    profile_text = (profile_json.get("profile_text") or "").strip()
    if profile_text:
        if len(profile_text) > _SHADOW_PROFILE_TEXT_MAX:
            profile_text = profile_text[:_SHADOW_PROFILE_TEXT_MAX] + "…"
        parts.append(f"画像: {profile_text}")

    rules = profile_json.get("rules") or []
    if rules:
        rule_lines = []
        for rule in rules[:_SHADOW_PROFILE_RULES_MAX]:
            if isinstance(rule, dict) and rule.get("human_text"):
                rule_lines.append(f"- {rule['human_text']}")
        if rule_lines:
            parts.append("交易规则:\n" + "\n".join(rule_lines))

    preferred_markets = profile_json.get("preferred_markets") or []
    if preferred_markets:
        parts.append("偏好市场: " + ", ".join(str(m) for m in preferred_markets))

    holding_days = profile_json.get("typical_holding_days")
    if holding_days:
        if isinstance(holding_days, (list, tuple)) and len(holding_days) == 2:
            parts.append(f"典型持仓天数: 中位 {holding_days[0]} 天 / P75 {holding_days[1]} 天")
        else:
            parts.append(f"典型持仓天数: {holding_days} 天")

    if not parts:
        return ""
    return "以下是用户交易风格画像(AI 参考, 用于给出更贴合的建议):\n" + "\n".join(parts)

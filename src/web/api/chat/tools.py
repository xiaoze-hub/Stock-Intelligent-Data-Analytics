"""tools (split from chat.py 2026-09-09, no logic change)."""

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
from src.web.api.chat.context import _build_watchlist_context
from src.web.api.chat.context import _fetch_auction_context
from src.web.api.chat.context import _fetch_capital_flow_context
from src.web.api.chat.context import _fetch_kline_pattern_context
from src.web.api.chat.context import _fetch_realtime_context
from src.web.api.chat.context import _fetch_technical_context
from src.web.api.chat.context import _format_fundamentals_text
from src.web.api.chat.context import _read_forecast
from src.web.api.chat.context import _read_notifications
from src.web.api.chat.context import _read_opportunities
from src.web.api.chat.context import _read_sentiment_cycle
from src.web.api.chat.context import _read_strategy_signals
from src.web.api.chat.context import get_web_content

CHAT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_portfolio",
            "description": "获取用户的实盘持仓和模拟盘持仓。用于回答持仓相关问题（持仓健康吗、该调仓吗、盈亏情况等）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_quote",
            "description": "获取某只股票的实时行情（价格、涨跌幅、成交量等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码，如 600519"},
                    "market": {"type": "string", "description": "市场代码：CN/HK/US", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_technical_analysis",
            "description": "获取股票的技术面分析（趋势、MACD、RSI、支撑位、压力位等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码"},
                    "market": {"type": "string", "description": "市场代码：CN/HK/US", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_main_intent",
            "description": "获取股票当日主力意图（逐笔口径）：主力/超大单/大单净额、参与度、买占比、竞价、尾盘、筹码峰、成本带、获利盘。回答'主力在吸筹还是派发''主力意图如何'等问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码，如 002361"},
                    "market": {"type": "string", "description": "市场代码：CN/HK/US", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_decision_pioneer",
            "description": "获取股票决策先锋三指标（GS策略趋势 + 暗盘资金/L2主力净流入 + AI机构活跃度）：机构活跃度数值与档位(生命线1.56/强势线3/大牛线6)、连强天数、5日均值，GS策略G买/S卖信号与当前G区/S区状态，L2主力净流入(TQ口径,对齐同花顺暗盘)。回答'三指标共振''机构活跃度''GS策略信号''暗盘资金''决策先锋'等问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码，如 002361"},
                    "market": {"type": "string", "description": "市场代码：CN/HK/US", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_rally_analysis",
            "description": "分析股票当日盘中顺势拉升段（逐单明细）：识别所有放量拉升段，逐段拆解主力/散户买卖结构、主动买占比，判别'放量上涨(真拉升)'还是'拉高出货(假拉升)'。回答'拉升是拉高出货还是放量上涨''盘中拉升段分析'等问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码，如 002361"},
                    "market": {"type": "string", "description": "市场代码：CN/HK/US", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_suggestions",
            "description": "获取某只股票最近的 AI 建议和分析报告。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码"},
                    "market": {"type": "string", "description": "市场代码：CN/HK/US", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_watchlist",
            "description": "获取用户的自选股（关注列表）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_capital_flow",
            "description": "获取某只 A 股的主力资金流向（主力净流入、超大单/大单/中单/小单净流入、5日主力净流入趋势）。用于回答资金面问题（主力在吸筹还是出货、资金流向如何等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码，如 002361"},
                    "market": {"type": "string", "description": "市场代码：CN/HK/US", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tdx_wenda",
            "description": "通达信问小达：自然语言投研查询。用于回答市场级的选股/排行/资金流向问题，如“今日主力净流入前10的A股”“今日涨幅前10的概念板块”“近3日主力净流入前10的半导体”“今日涨停家数最多的概念板块”“今日龙虎榜机构净买入前10”。适合不指定个股、而是看板块/全市场维度的问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "自然语言查询，如 “今日主力净流入前10的A股”"},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_news",
            "description": "获取市场资讯与新闻：聚合全网/财经媒体热点话题(news_hotlist)与 AI 生成的每日市场简报(briefings, 早/午/收盘/晚盘)。用于回答「有什么新闻/资讯/热点」「今天消息面」「近期题材催化」等新闻资讯类问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "资讯热榜返回条数，默认 15", "default": 15},
                    "briefing_type": {"type": "string", "description": "每日简报类型：morning(早盘)/noon(午盘)/close(收盘)/evening(晚间)，不填则返回全部", "default": ""},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_kline_patterns",
            "description": "识别某只股票的K线组合形态（金针探底/双针探底/红三兵/涨停双响炮/揭竿而起/上升三法/小步上扬/放量突破/三只乌鸦/空方炮/黄昏之星等）。基于同花顺K线形态教学体系。用于回答「XX股票K线什么形态」「有没有金针探底/红三兵」「技术形态怎么样」等问题。返回形态名称+信号方向+特征描述。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码，如 600519"},
                    "market": {"type": "string", "description": "市场：CN(默认)/HK/US", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_auction_data",
            "description": "获取A股集合竞价数据（9:25后当日竞价已生成）：竞价全景（竞价涨停/跌停/涨停委买额/成交额/昨炸板反馈/昨涨停反馈）、竞价最强个股（按bidStrength/金额/涨幅）、竞价主线题材、弱转强/被核风险。用于回答「集合竞价怎么样」「今天竞价最强是谁」「竞价主线是什么」「竞价有超预期的吗」「昨高标被核了吗」「今天弱转强的是谁」等竞价相关问题。注意：9:25前当日竞价数据未生成，会明确提示。",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene": {"type": "string", "description": "竞价场景：overview(竞价全景，默认)/strongest(最强个股)/theme(主线题材)/weak_to_strong(弱转强)/risk(被核风险)/watchlist(盯盘名单)"},
                    "limit": {"type": "integer", "description": "返回条数，默认 10", "default": 10},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_forecast",
            "description": "读取系统 AI 预测引擎的最近预测结果（预测方向/预期涨跌幅/目标价/到期时间，数据来自预测引擎独立库）。⚠️ 历史回测准确率仅31.7%，预测方向不可靠，仅供参考，不可作为交易依据。用于回答「系统预测了什么」「预测引擎今天给了什么预测」「XX股票的预测结果怎么样」「预测目标价是多少」等问题。可选按股票代码过滤。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码，如 002361；不填则返回全部最近预测", "default": ""},
                    "limit": {"type": "integer", "description": "返回条数，默认 5", "default": 5},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sentiment_cycle",
            "description": "读取当前 A 股短线情绪周期(冰点/修复/发酵/高潮/退潮)及操作提示。用于回答「现在市场情绪怎么样」「短线情绪处于什么阶段」「现在适合打板还是防守」等问题。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_strategy_signals",
            "description": "读取系统最新策略信号（哪个策略对哪只股票给出了买/关注类信号、动作、得分与信号描述，取最新交易日）。用于回答「哪个策略给了信号」「今天策略信号有哪些」「XX策略有信号吗」「系统策略看好什么」等问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "返回条数，默认 10", "default": 10},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_notifications",
            "description": "读取系统最近通知/提醒（Agent 运行、报告生成、策略刷新等完成或失败消息）。用于回答「有什么通知」「系统有什么提醒」「有没有未读通知」等问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "返回条数，默认 10", "default": 10},
                    "unread_only": {"type": "boolean", "description": "是否只返回未读通知，默认 False", "default": False},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fundamentals_detail",
            "description": "获取个股基本面明细（合并五类）：龙虎榜（近10日上榜记录，含净买入/买卖额）、融资融券（两融余额/融资买入偿还）、股东户数（最新一期及环比变化）、分红历史（每股派息/送转）、事件日历（近7日公告/业绩预告）。用于回答「XX最近有什么龙虎榜/分红/股东户数变化/融资融券/事件公告」等个股基本面明细类问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码，如 002361"},
                    "market": {"type": "string", "description": "市场代码：CN/HK/US", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_irm_qa",
            "description": "获取个股互动易问答（巨潮 cninfo）：投资者提问与公司官方回应列表（问题+回复+时间）。互动易是公司对投资者提问的官方回复，是验证传闻/利好的权威信源。用于回答「XX公司最近有什么互动易回复」「公司对XX事的官方回应」「管理层怎么回应XX传闻」等问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码，如 002361"},
                    "market": {"type": "string", "description": "市场代码：CN/HK/US", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_anomalies",
            "description": "获取A股异动股池（东财，交易所「严重异常波动」口径）：触发严重异常波动规则的个股列表，含代码/名称/当日涨跌幅/累计偏离/统计窗口/规则说明/是否当日。用于回答「今天有什么异动股」「哪些股票严重异常波动」「被交易所点名波动的股票」等问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "返回条数，默认 20", "default": 20},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_northbound",
            "description": "获取北向资金(沪股通)当日净额：同花顺口径, 返回当日沪股通净买入(亿元)。用于回答「今天北向资金流入还是流出」「北向净买多少」「外资动向」等问题。注意: 2024-08 后交易所停止披露北向实时净买入, 此为同花顺估算口径仅供参考; 深股通数据暂缺。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_hot_stocks",
            "description": "获取同花顺热榜：按人气排名的热门A股（小时榜/日榜），含排名/代码/名称/涨跌幅/热度/概念标签/AI归因。用于回答「今天什么股票最热」「热榜前几」「XX为什么涨/为什么这么火」等问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {"type": "string", "description": "热榜周期：hour(小时榜，默认)/day(日榜)", "default": "hour"},
                    "limit": {"type": "integer", "description": "返回条数，默认 20", "default": 20},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_web_content",
            "description": "抓取网页链接正文(支持 http/https, 含微信公众号文章 mp.weixin.qq.com)。用于回答「帮我看看这个链接/这篇文章讲了什么/分析一下这个网页内容」等需要分析用户发来链接的问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要抓取的网页完整链接, 如 https://mp.weixin.qq.com/s/xxx 或 https://example.com/article"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_main_flow_compare",
            "description": "比对两路主力资金数据源(腾讯逐笔/同花顺L2)的一致性, 判断主力真实意图。仅限A股(CN)。入参 symbol=6位A股代码如002361。返回每路主力净额(元)及一致性评分(0-100)。用于回答「两路主力数据是否一致」「主力在吸筹还是派发」「各数据源口径对比」等问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "6位A股代码, 如 002361"},
                    "market": {"type": "string", "description": "市场代码, 仅支持 CN", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_delta_series",
            "description": "基于L2逐笔穿透计算秒级Delta序列(主动买-主动卖金额)及顶底背离信号。仅限A股(CN)。入参 symbol=6位A股代码如002361。先拉取THS L2全天逐笔, 再计算每秒净额、30秒平滑Delta、累计Delta、顶背离/底背离信号。用于回答「逐笔Delta分析」「有没有顶背离/底背离」「资金持续力度」等问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "6位A股代码, 如 002361"},
                    "market": {"type": "string", "description": "市场代码, 仅支持 CN", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_orderbook",
            "description": "采集THS L2盘口(20档)多快照演变分析: 托单/压单/撤单/幽灵单检测 + 订单簿失衡(OB) + 幽灵单比率。仅限A股(CN)。入参 symbol=6位A股代码如002361。采集约8个快照(间隔1.5s, 约12秒)。用于回答「盘口有没有托单压单」「有没有幽灵挂单」「订单簿是否失衡」「主力在护盘还是压制」等问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "6位A股代码, 如 002361"},
                    "market": {"type": "string", "description": "市场代码, 仅支持 CN", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_event_catalyst",
            "description": "事件驱动预期差分析: 把当日公告/新闻推理成催化信号 + 受益链 + 预期差。仅限A股(CN)。入参 symbol=6位A股代码如002361。基于当日公告做因果链推理(如停产→供给收缩→涨价→受益股), 输出催化题材/方向/置信度/受益股池/预期差(利好未反应=高预期差=潜伏价值, 利好已涨=兑现追高)。用于回答「这公告利好什么」「有哪些受益股」「预期差大不大」等问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "6位A股代码, 如 002361"},
                    "market": {"type": "string", "description": "市场代码, 仅支持 CN", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_intent_explain",
            "description": "主力意图 AI 解释: 对规则算法算出的主力意图结论, 用 AI 结合内外盘/拆单/筹码/位置给出「为什么」+ 置信度 + 方向(吸筹/派发/洗盘/中性)。仅限A股(CN)。入参 symbol=6位A股代码如002361。规则给结论, AI 只做解释不改变结论。用于回答「为什么说主力在吸筹」「这个主力意图怎么看」等需要解释的问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "6位A股代码, 如 002361"},
                    "market": {"type": "string", "description": "市场代码, 仅支持 CN", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_factor_ic_report",
            "description": "因子 IC 归因报告: 读取因子有效性评估(IC/IR)结果, 用 AI 解读哪些因子有真实 alpha、哪些失效、哪些市态依赖, 并给出调权建议。仅限A股(CN)。入参 market 默认 CN。用于回答「哪些因子最近有效」「因子权重该怎么调」等问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "market": {"type": "string", "description": "市场代码, 仅支持 CN", "default": "CN"},
                },
                "required": [],
            },
        },
    },
]
def _build_thsdk_chat_schemas() -> list:
    """构建 thsdk 工具的 CHAT_TOOLS function schema 列表(延迟 import 避免循环依赖)。"""
    from src.agents.chat.tools_thsdk import build_thsdk_tool_schemas
    return build_thsdk_tool_schemas()
_THSDK_TOOL_NAMES_SET: frozenset = frozenset(
    {
        "get_thsdk_news",
        "get_thsdk_corporate_action",
        "get_thsdk_dde",
        "get_thsdk_hs300_constituents",
        "get_thsdk_market_data_cn_extended",
        "get_thsdk_market_data_index",
        "get_thsdk_market_data_hk",
        "get_thsdk_market_data_us",
        "get_thsdk_market_data_bond",
        "get_thsdk_market_data_fund",
        "get_wencai_enhanced",
    }
)
async def _exec_thsdk_tool(name: str, args: dict) -> str:
    """执行 thsdk 工具(选项 C, 2026-08-20)。

    通过 tools_thsdk 间接调用 thsdk_l2, 工具函数内部已做降级(available=false),
    不会抛异常到用户层。同步网络调用包 to_thread 防阻塞事件循环。
    """
    import asyncio  # noqa: F811 — 同上, 入口绑定防局部import遮蔽

    from src.agents.chat.tools_thsdk import (
        THSDK_TOOL_HANDLERS,
        format_thsdk_tool_result,
    )

    handler = THSDK_TOOL_HANDLERS.get(name)
    if handler is None:
        # 防御: 若 handler 未注册(新工具未同步 registry), 降级返回
        return f"[thsdk] 工具 {name} 尚未注册实现。"
    try:
        result = await asyncio.to_thread(handler, **_filter_tool_args(handler, args))
        return format_thsdk_tool_result(name, result)
    except Exception as e:  # noqa: BLE001
        logger.warning("thsdk 工具执行失败 %s: %s", name, e)
        return f"[thsdk] {name} 执行出错: {str(e)[:80]}"
def _filter_tool_args(handler, args: dict) -> dict:
    """按 handler 的可选参数过滤 args, 只传显式提供的参数, 避免 schema 与实现不一致。"""
    import inspect

    try:
        sig = inspect.signature(handler)
    except (TypeError, ValueError):
        return dict(args or {})
    allowed = {
        p.name
        for p in sig.parameters.values()
        if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
    }
    return {k: v for k, v in (args or {}).items() if k in allowed}
CHAT_TOOLS.extend(_build_thsdk_chat_schemas())
async def _execute_tool(
    db: Session, name: str, args: dict, user: User | None = None
) -> str:
    """执行工具调用，返回结果文本。

    修复 2026-08-21(国内生产): get_market_news 等分支的局部 `import asyncio`
    使 asyncio 成为整个函数作用域的局部名 → get_main_intent / get_rally_analysis
    等分支引用 asyncio 时抛 UnboundLocalError(线上表现: "主力意图获取失败:
    cannot access local variable 'asyncio'")。在函数入口统一 import 一次,
    所有分支可用; 各分支内的重复局部 import 变为冗余但无害。

    S5(2026-08-26): user 沿工具链下传, 数据类工具(get_portfolio/get_stock_suggestions/
    get_watchlist)只读当前用户自己的数据。
    """
    import asyncio  # noqa: F811 — 函数入口统一绑定(2026-08-21热修, 防分支局部import致UnboundLocalError)
    try:
        if name == "get_portfolio":
            result = _build_portfolio_context(db, user=user)
            return result or "用户暂无持仓。"
        elif name == "get_stock_quote":
            symbol = args.get("symbol", "")
            market = args.get("market", "CN")
            result = await _fetch_realtime_context(symbol, market)
            return result or f"未能获取 {market}:{symbol} 的行情数据。"
        elif name == "get_technical_analysis":
            symbol = args.get("symbol", "")
            market = args.get("market", "CN")
            result = await _fetch_technical_context(symbol, market)
            return result or f"未能获取 {market}:{symbol} 的技术面数据。"
        elif name == "get_main_intent":
            symbol = args.get("symbol", "")
            market = args.get("market", "CN")
            if market != "CN":
                return "主力意图仅支持 A 股(CN)。"
            try:
                from src.agents.intraday_monitor import _main_intent_summary
                # 热修 2026-08-14: 同步网络调用包 to_thread, 防阻塞 asyncio 事件循环(登录超时根因)
                result = await asyncio.to_thread(_main_intent_summary, symbol)
                # 数据源口径标注(腾讯逐笔·主力意图), 用户可见, 避免与东财四档混淆
                return f"[数据源: 腾讯逐笔·主力意图口径]\n{result}" if result else f"未能获取 {symbol} 的主力意图数据。"
            except Exception as e:
                return f"主力意图获取失败: {str(e)[:100]}"
        elif name == "get_decision_pioneer":
            symbol = args.get("symbol", "")
            market = args.get("market", "CN")
            if market != "CN":
                return "决策先锋三指标仅支持 A 股(CN)。"
            try:
                from src.core.decision_pioneer import decision_pioneer_text
                # 热修: 同步网络调用包 to_thread, 防阻塞事件循环
                result = await asyncio.to_thread(decision_pioneer_text, symbol, market)
                return f"[数据源: 决策先锋三指标·机构活跃度+GS+L2主力净流入]\n{result}" if result else f"未能获取 {symbol} 的决策先锋数据。"
            except Exception as e:
                return f"决策先锋获取失败: {str(e)[:100]}"
        elif name == "get_rally_analysis":
            symbol = args.get("symbol", "")
            market = args.get("market", "CN")
            if market != "CN":
                return "拉升段分析仅支持 A 股(CN)。"
            try:
                from src.core.rally_analysis import analyze_rallies, format_rally_report
                # 热修 2026-08-14: 同步网络调用包 to_thread, 防阻塞事件循环
                result = await asyncio.to_thread(analyze_rallies, symbol)
                if not result:
                    return f"未能获取 {symbol} 的拉升段分析数据(可能盘前无数据)。"
                return format_rally_report(result)
            except Exception as e:
                return f"拉升段分析失败: {str(e)[:100]}"
        elif name == "get_stock_suggestions":
            symbol = args.get("symbol", "")
            market = args.get("market", "CN")
            result = _build_stock_context(db, symbol, market, user=user)
            return result or f"暂无 {market}:{symbol} 的 AI 建议。"
        elif name == "get_watchlist":
            return _build_watchlist_context(db, user=user)
        elif name == "get_capital_flow":
            symbol = args.get("symbol", "")
            market = args.get("market", "CN")
            result = await _fetch_capital_flow_context(symbol, market)
            # 数据源口径标注(东财四档·资金流向), 与 get_main_intent(逐笔) 区分
            return f"[数据源: 东财四档·资金流向口径]\n{result}" if result else f"未能获取 {market}:{symbol} 的资金流向数据。"
        elif name == "tdx_wenda":
            question = (args.get("question") or "").strip()
            if not question:
                return "请提供查询问题, 如 '今日主力净流入前10的A股'。"
            try:
                from marketdata.vendors.tdx import ask_wenda

                # 热修 2026-08-14: 同步网络调用包 to_thread, 防阻塞事件循环
                res = await asyncio.to_thread(ask_wenda, question)
                if not res or not isinstance(res, dict):
                    return f"通达信问小达未返回数据: {question}"
                rows = res.get("data") or []
                if not rows:
                    return f"通达信问小达无结果: {question}"
                lines = [f"通达信问小达查询结果「{question}」(共{len(rows)}条):"]
                for r in rows[:15]:
                    if not isinstance(r, dict):
                        continue
                    name = r.get("sec_name") or r.get("name") or ""
                    code = r.get("sec_code") or r.get("code") or ""
                    chg = r.get("chg") or r.get("change_pct") or ""
                    main_net = next(
                        (v for k, v in r.items() if "主力净额" in k or "主力净" in k),
                        "",
                    )
                    line = f"- {code} {name}"
                    if chg:
                        line += f" 涨{chg}%"
                    if main_net:
                        line += f" 主力净额{main_net}"
                    lines.append(line)
                return "\n".join(lines)
            except Exception as e:
                logger.warning(f"TDX 问小达工具失败: {e}")
                return f"通达信问小达查询失败: {e}"
        elif name == "get_market_news":
            limit = int((args.get("limit") or 15))
            briefing_type = (args.get("briefing_type") or "").strip()
            try:
                import asyncio

                # 1) 底层多源快讯体系(财联社/新浪/东财7x24, 市场级, 引擎主备+降级)
                parts = []
                try:
                    from src.core.marketdata_client import get_market_data

                    items = await asyncio.to_thread(
                        lambda: get_market_data().flash_news(market="CN", limit=limit)
                    )
                    if items:
                        lines = [f"【市场快讯】(最近{len(items)}条, 多源聚合)"]
                        for it in items[:limit]:
                            title = (getattr(it, "title", "") or "").strip()
                            src = (getattr(it, "source", "") or "").strip()
                            ts = getattr(it, "publish_time", None)
                            tstr = ts.strftime("%H:%M") if ts else ""
                            line = f"- [{tstr}] {title}"
                            if src:
                                line += f" ({src})"
                            lines.append(line)
                        parts.append("\n".join(lines))
                except Exception as e:
                    logger.warning(f"快讯聚合失败, 回退悟道热榜: {e}")

                # 2) 悟道热榜/简报作为补充(失败不影响主链路)
                try:
                    from src.collectors.wudao_mcp_client import WudaoMCPClient
                    cli = WudaoMCPClient()
                    # 热修 2026-08-14: wudao MCP 同步 requests(timeout 30-60s)包 to_thread, 防阻塞事件循环
                    await asyncio.to_thread(cli._initialize)
                    hot = await asyncio.to_thread(cli.call_tool, "news_hotlist", {"limit": limit})
                    hot_text = hot.get("text") if isinstance(hot, dict) else ""
                    if hot_text:
                        parts.append("【资讯热榜】\n" + str(hot_text))
                    else:
                        hot_rows = hot.get("rows") or hot.get("data") or hot.get("items") or []
                        if isinstance(hot_rows, dict):
                            hot_rows = hot_rows.get("rows") or hot_rows.get("data") or []
                        if hot_rows:
                            lines = ["【资讯热榜】"]
                            for r in hot_rows[:limit]:
                                if isinstance(r, dict):
                                    title = r.get("title") or r.get("name") or r.get("keyword") or ""
                                    heat = r.get("heat") or r.get("hot") or r.get("count") or ""
                                    tag = r.get("tag") or r.get("source") or ""
                                    line = "- " + str(title)
                                    if tag:
                                        line += " #" + str(tag)
                                    if heat:
                                        line += " (热度" + str(heat) + ")"
                                    lines.append(line)
                            parts.append("\n".join(lines))
                    brief_args = {"detailLevel": "digest"}
                    if briefing_type:
                        brief_args["type"] = briefing_type
                    brief = await asyncio.to_thread(cli.call_tool, "briefings", brief_args)
                    brief_text = ""
                    if isinstance(brief, dict):
                        brief_text = brief.get("text") or brief.get("digest") or ""
                    if brief_text:
                        parts.append("【每日简报】\n" + str(brief_text))
                except Exception as e:
                    logger.debug(f"悟道热榜/简报失败(不影响主链路): {e}")

                if not parts:
                    return "暂无实时资讯数据（可能非交易时段或数据源未就绪），建议盘后重试。"
                return "\n\n".join(parts)
            except Exception as e:
                logger.error(f"get_market_news 工具失败: {e}")
                return f"资讯获取失败: {e}"
        elif name == "get_kline_patterns":
            symbol = args.get("symbol", "")
            market = args.get("market", "CN")
            return await _fetch_kline_pattern_context(symbol, market)
        elif name == "get_auction_data":
            scene = args.get("scene", "overview")
            limit = int(args.get("limit", 10) or 10)
            return await _fetch_auction_context(scene, limit)
        elif name == "get_forecast":
            symbol = (args.get("symbol") or "").strip()
            limit = int(args.get("limit", 5) or 5)
            return _read_forecast(symbol, limit)
        elif name == "get_opportunities":
            limit = int(args.get("limit", 10) or 10)
            return _read_opportunities(db, limit)
        elif name == "get_sentiment_cycle":
            return await _read_sentiment_cycle()
        elif name == "get_strategy_signals":
            limit = int(args.get("limit", 10) or 10)
            return _read_strategy_signals(db, limit)
        elif name == "get_notifications":
            limit = int(args.get("limit", 10) or 10)
            unread_only = bool(args.get("unread_only") or False)
            return _read_notifications(db, limit, unread_only)
        elif name == "get_fundamentals_detail":
            symbol = (args.get("symbol") or "").strip()
            market = args.get("market", "CN")
            if not symbol:
                return "请提供股票代码(symbol)。"
            try:
                # 局部 import asyncio: _execute_tool 内 get_market_news 分支已有
                # `import asyncio`, 使 asyncio 成为整个函数作用域的局部名, 顶层 import 不可见
                import asyncio
                # 懒加载避免模块级循环依赖; 同步取数放线程池不阻塞事件循环
                from src.web.api.market_data import fetch_fundamentals_detail

                data = await asyncio.to_thread(fetch_fundamentals_detail, symbol, market)
                return _format_fundamentals_text(symbol, market, data)
            except Exception as e:
                logger.warning(f"get_fundamentals_detail 工具失败 [{symbol}]: {e}")
                return f"基本面明细查询失败: {e}"
        elif name == "get_irm_qa":
            # 互动易问答(巨潮 cninfo): 公司对投资者提问的官方回应, 验证传闻/利好的权威信源
            symbol = (args.get("symbol") or "").strip()
            market = args.get("market", "CN")
            if not symbol:
                return "请提供股票代码(symbol)。"
            if market != "CN":
                return "互动易问答仅支持 A 股(CN)。"
            try:
                # 局部 import asyncio: 本函数 get_market_news 分支已有局部 `import asyncio`,
                # 使 asyncio 成为整个函数作用域的局部名, 顶层 import 不可见, 必须在此分支内重新 import
                import asyncio
                from marketdata.symbol import Symbol
                from marketdata.vendors.cninfo_irm import CninfoIrmVendor

                vendor = CninfoIrmVendor()
                # 同步 vendor 放线程池执行, 避免阻塞事件循环
                items = await asyncio.to_thread(
                    vendor.fetch, [Symbol.parse(symbol)], {"page_size": 10}
                )
                if not items:
                    return f"{symbol} 暂无互动易问答记录(可能近期无提问或接口暂未收录)。"
                lines = [f"【互动易问答】{symbol}(数据源: 巨潮 cninfo, 最近{len(items)}条):"]
                for i, it in enumerate(items[:10], 1):
                    q = (getattr(it, "title", "") or "").strip()
                    answer = (getattr(it, "url", "") or "").strip()  # 公司回复临时存 url 字段
                    ts = getattr(it, "publish_time", None)
                    tstr = ts.strftime("%Y-%m-%d") if ts else ""
                    lines.append(f"{i}. [{tstr}] {q}")
                    if answer:
                        lines.append(f"   回复: {answer[:200]}")
                    else:
                        lines.append("   回复: (公司尚未回复)")
                return "\n".join(lines)
            except Exception as e:
                logger.warning(f"get_irm_qa 工具失败 [{symbol}]: {e}")
                return f"互动易问答查询失败: {e}"
        elif name == "get_market_anomalies":
            # 东财异动池: 交易所「严重异常波动」口径, 市场级(无视个股参数)
            limit = min(int(args.get("limit", 20) or 20), 50)
            try:
                import asyncio
                from marketdata.vendors.em_anomaly import EmAnomalyVendor

                vendor = EmAnomalyVendor()
                items = await asyncio.to_thread(
                    vendor.fetch, [], {"page_size": limit}
                )
                if not items:
                    return "东财异动池暂无数据(今日可能无触发「严重异常波动」规则的个股, 或非交易时段/数据源未就绪)。"
                has_today = any(getattr(it, "is_today", False) for it in items)
                lines = [
                    f"【东财异动池】交易所「严重异常波动」标的(共{len(items)}条, "
                    f"{'含当日' if has_today else '最近交易日'}):"
                ]
                for i, it in enumerate(items[:limit], 1):
                    chg = getattr(it, "change_pct", None)
                    dev = getattr(it, "deviation", None)
                    days = getattr(it, "days", None)
                    rule = getattr(it, "rule", "") or ""
                    chg_s = f" 涨跌幅{chg:+.2f}%" if isinstance(chg, (int, float)) else ""
                    dev_s = f" 累计偏离{dev:+.2f}%" if isinstance(dev, (int, float)) else ""
                    days_s = f"({days}日)" if days else ""
                    flag = "当日" if getattr(it, "is_today", False) else "非当日"
                    lines.append(
                        f"{i}. {it.symbol} {getattr(it, 'name', '') or ''} "
                        f"{chg_s}{dev_s}{days_s} [{flag}]"
                    )
                    if rule:
                        lines.append(f"   规则: {rule}")
                return "\n".join(lines)
            except Exception as e:
                logger.warning(f"get_market_anomalies 工具失败: {e}")
                return f"异动股池查询失败: {e}"
        elif name == "get_northbound":
            # 北向资金(同花顺口径): 当日沪股通净额(亿元)。
            # 2024-08 后交易所停止披露实时净买入, 同花顺估算口径仅供参考; 深股通暂缺。
            try:
                import asyncio
                from src.core.data_collector import DataCollectorManager

                mgr = DataCollectorManager()

                def _fetch_nb():
                    from src.web.database import SessionLocal
                    from src.web.models import DataSource

                    db = SessionLocal()
                    try:
                        src = db.query(DataSource).filter(DataSource.type == "northbound").first()
                        if not src:
                            return None, "未注册北向资金数据源"
                        return asyncio.run(mgr._test_northbound_source(src)), None
                    finally:
                        db.close()

                r, err = await asyncio.to_thread(_fetch_nb)
                if err or r is None:
                    return f"北向资金查询失败: {err or '无结果'}"
                if not r.success:
                    return f"北向资金查询失败: {(r.error or '数据源异常')[:100]}"
                data = r.data or []
                if not data:
                    return "北向资金暂无数据(非交易时段或数据源未就绪)。"
                lines = ["【北向资金】(同花顺估算口径, 仅供参考):"]
                for row in data:
                    date = row.get("date", "")
                    hgt = row.get("hgt_net")
                    total = row.get("total_net")
                    hgt_s = (
                        f"沪股通净{'流入' if (hgt or 0) >= 0 else '流出'} {abs(hgt):.2f}亿"
                        if isinstance(hgt, (int, float))
                        else "沪股通暂缺"
                    )
                    total_s = (
                        f" | 北向合计净额 {total:.2f}亿"
                        if isinstance(total, (int, float))
                        else " | 合计口径暂缺(深股通未披露)"
                    )
                    lines.append(f"- {date}: {hgt_s}{total_s}")
                lines.append(
                    "注: 2024-08 起交易所停止披露北向实时净买入, 以上为同花顺估算口径; "
                    "主力意图判断请以 get_main_intent 为准。"
                )
                return "\n".join(lines)
            except Exception as e:
                logger.warning(f"get_northbound 工具失败: {e}")
                return f"北向资金查询失败: {e}"
        elif name == "get_hot_stocks":
            # 同花顺热榜(小时榜/日榜): 排名/热度/概念标签/AI归因(analyse)
            period = (args.get("period") or "hour").strip().lower()
            if period not in ("hour", "day"):
                period = "hour"
            limit = min(int(args.get("limit", 20) or 20), 50)
            try:
                import asyncio
                from marketdata.vendors.ths_hot import ThsHotListVendor

                vendor = ThsHotListVendor()
                items = await asyncio.to_thread(
                    vendor.fetch, [], {"period": period, "limit": limit}
                )
                if not items:
                    return "同花顺热榜暂无数据(可能非交易时段或数据源未就绪)。"
                period_cn = "小时榜" if period == "hour" else "日榜"
                lines = [
                    f"【同花顺热榜·{period_cn}】人气前{len(items)}(数据源: 同花顺, 含AI归因):"
                ]
                for i, it in enumerate(items[:limit], 1):
                    rank = getattr(it, "rank", 0) or 0
                    chg = getattr(it, "change_pct", None)
                    heat = getattr(it, "heat", None)
                    concepts = getattr(it, "concepts", ()) or ()
                    reason = (getattr(it, "reason", "") or "").strip()
                    chg_s = f" 涨跌幅{chg:+.2f}%" if isinstance(chg, (int, float)) else ""
                    heat_s = f" 热度{heat}" if heat not in (None, "") else ""
                    tag_s = (" 概念: " + "/".join(str(c) for c in concepts)) if concepts else ""
                    lines.append(
                        f"{i}. 第{rank}名 {it.symbol} {getattr(it, 'name', '') or ''}"
                        f"{chg_s}{heat_s}{tag_s}"
                    )
                    if reason:
                        lines.append(f"   AI归因: {reason[:150]}")
                return "\n".join(lines)
            except Exception as e:
                logger.warning(f"get_hot_stocks 工具失败: {e}")
                return f"同花顺热榜查询失败: {e}"
        elif name == "get_web_content":
            url = (args.get("url") or "").strip()
            if not url:
                return "请提供要抓取的网页链接(url)。"
            # 局部 import asyncio: 函数内 get_market_news 分支有 `import asyncio`,
            # 使 asyncio 成为整个函数作用域的局部名, 必须在本分支内重新 import 才能使用
            import asyncio
            # 同步网络抓取放线程池, 不阻塞事件循环(与 2026-08-14 热修风格一致)
            return await asyncio.to_thread(get_web_content, url)
        elif name in _THSDK_TOOL_NAMES_SET:
            # thsdk 11 个高价值工具(2026-08-20, 选项 C): 通过 tools_thsdk 间接调用 thsdk_l2
            result = await _exec_thsdk_tool(name, args)
            return result
        elif name == "get_main_flow_compare":
            symbol = args.get("symbol", "")
            market = args.get("market", "CN")
            if market != "CN":
                return "主力双源对比仅支持 A 股(CN)。"
            try:
                from src.core.main_flow_compare import compare_main_flow
                result = await asyncio.to_thread(compare_main_flow, symbol)
                if not result or result.get("consistency") is None:
                    return f"[数据源: 腾讯逐笔/同花顺L2] {symbol} 主力双源数据均不可用, 无法比对。"
                lines = [f"[数据源: 腾讯逐笔/同花顺L2] {symbol} 主力双源对比:"]
                lines.append(f"  一致性: {result['consistency']}/100  |  发散幅度: {result['delta_pct']}%")
                for src_name, src_key in [("腾讯逐笔", "tencent"), ("同花顺L2", "thsdk")]:
                    src = result.get(src_key)
                    if src and src.get("available"):
                        mn = src.get("main_net")
                        mn_str = f"{mn:+,.0f}元" if isinstance(mn, (int, float)) else "N/A"
                        lines.append(f"  {src_name}: 主力净额 {mn_str}")
                    else:
                        lines.append(f"  {src_name}: 数据暂不可用")
                lines.append(f"  说明: {result.get('note', '')}")
                return "\n".join(lines)
            except Exception as e:
                logger.warning(f"get_main_flow_compare 工具失败 [{symbol}]: {e}")
                return f"主力双源对比失败: {str(e)[:100]}"
        elif name == "get_delta_series":
            symbol = args.get("symbol", "")
            market = args.get("market", "CN")
            if market != "CN":
                return "秒级Delta序列分析仅支持 A 股(CN)。"
            try:
                import asyncio
                from src.core.dark_l2 import fetch_l2_ticks
                from src.core.delta_engine import compute_delta_series
                ticks = await asyncio.to_thread(fetch_l2_ticks, symbol, "thsdk")
                if not ticks:
                    return f"[数据源: THS L2 逐笔] {symbol} 无逐笔数据(可能盘前或数据源异常)。"
                result = compute_delta_series(ticks, smooth_sec=30, divergence_min_sec=120)
                st = result["stats"]
                signals = result.get("signals", [])
                lines = [f"[数据源: THS L2 逐笔] {symbol} 秒级Delta序列:"]
                lines.append(f"  逐笔条数={result['ticks']}  |  时间区间={result['first_t']}~{result['last_t']}")
                lines.append(f"  主动买={st['total_buy_yuan']:,.0f}元  |  主动卖={st['total_sell_yuan']:,.0f}元  |  净额={st['net_yuan']:,.0f}元")
                lines.append(f"  Delta30峰值={st['peak_delta30']:,.0f}元  |  谷值={st['trough_delta30']:,.0f}元")
                lines.append(f"  累计Delta(末)={st['cum_net_last']:,.0f}元  |  价格区间={st['lo_price']}~{st['hi_price']}")
                if signals:
                    for s in signals[:5]:
                        lines.append(f"  ⚠ {s['type']} @ {s['t']}  price={s['price']}  delta30={s['delta30']:,.0f}  since={s['since']} 持续{s['streak']}s")
                else:
                    lines.append("  无顶底背离信号")
                return "\n".join(lines)
            except Exception as e:
                logger.warning(f"get_delta_series 工具失败 [{symbol}]: {e}")
                return f"秒级Delta序列分析失败: {str(e)[:100]}"
        elif name == "get_orderbook":
            symbol = args.get("symbol", "")
            market = args.get("market", "CN")
            if market != "CN":
                return "盘口演变分析仅支持 A 股(CN)。"
            try:
                import asyncio
                from src.core.main_flow_compare import _to_thsdk_symbol
                from src.core.orderbook_engine import run
                ths_code = _to_thsdk_symbol(symbol)
                if not ths_code:
                    return f"无法将 {symbol} 转换为 THS 代码(仅支持6位A股代码)。"
                result = await asyncio.to_thread(run, ths_code, 8, 1.5)
                lines = [f"[数据源: THS L2 盘口] {symbol}({ths_code}) 盘口演变分析:"]
                lines.append(f"  {result['summary']}")
                events = result.get("events", [])
                if events:
                    lines.append(f"  盘口事件({len(events)}条):")
                    for ev in events[:10]:
                        note = f"  - {ev['note']}" if ev.get("note") else ""
                        lines.append(f"    [{ev['type']}] {ev['side']} 档{ev.get('price_level', '?')} @ {ev['price']} 手数变化{ev.get('delta_hands', 0):+,} {note}")
                else:
                    lines.append("  无盘口事件(盘口静止或非交易时段)。")
                ob_series = result.get("ob_series", [])
                if ob_series:
                    labels = [s["label"] for s in ob_series]
                    lines.append(f"  订单簿失衡: {', '.join(labels)}")
                return "\n".join(lines)
            except Exception as e:
                logger.warning(f"get_orderbook 工具失败 [{symbol}]: {e}")
                return f"盘口演变分析失败: {str(e)[:100]}"
        elif name == "get_event_catalyst":
            symbol = args.get("symbol", "")
            market = args.get("market", "CN")
            if market != "CN":
                return "事件催化分析仅支持 A 股(CN)。"
            try:
                import asyncio
                from src.core.event_catalyst_engine import analyze_event_catalyst

                result = await asyncio.to_thread(analyze_event_catalyst, symbol, None)
                if not result:
                    return f"{symbol} 当日无公告事件, 或 AI 推理失败(静默降级), 无法生成催化信号。"
                gap = result.get("expectation_gap") or {}
                lines = [
                    f"[数据源: 当日公告→AI推理] {symbol} 事件催化与预期差:",
                    f"  催化题材: {result.get('catalyst')}",
                    f"  方向: {result.get('direction')} | 置信度: {result.get('confidence')}",
                ]
                pool = result.get("beneficiary_pool") or []
                if pool:
                    lines.append(f"  受益链: {' / '.join(pool)}")
                if gap:
                    lines.append(f"  预期差: {gap.get('level')} — {gap.get('note')}")
                if result.get("reason"):
                    lines.append(f"  理由: {result['reason']}")
                return "\n".join(lines)
            except Exception as e:
                logger.warning(f"get_event_catalyst 工具失败 [{symbol}]: {e}")
                return f"事件催化分析失败: {str(e)[:100]}"
        elif name == "get_intent_explain":
            symbol = args.get("symbol", "")
            market = args.get("market", "CN")
            if market != "CN":
                return "主力意图解释仅支持 A 股(CN)。"
            try:
                import asyncio
                from src.core.dark_flow import compute_dark_flow
                from src.core.intent_explain import explain_main_intent

                dark = await asyncio.to_thread(compute_dark_flow, symbol)
                if not dark:
                    return f"未能获取 {symbol} 的主力意图数据(可能盘前无数据)。"
                result = await asyncio.to_thread(explain_main_intent, dark, None)
                if not result:
                    return f"[数据源: 主力意图规则算法] {symbol} 数据不足或 AI 解释失败(静默降级)。规则结论: {dark.get('signal', '未知')}"
                return (
                    f"[数据源: 主力意图规则算法 + AI解释] {symbol}\n"
                    f"  方向: {result.get('direction')} | 置信度: {result.get('confidence')}\n"
                    f"  为什么: {result.get('why')}\n"
                    f"  (规则结论: {dark.get('signal', '未知')})"
                )
            except Exception as e:
                logger.warning(f"get_intent_explain 工具失败 [{symbol}]: {e}")
                return f"主力意图解释失败: {str(e)[:100]}"
        elif name == "get_factor_ic_report":
            market = args.get("market", "CN")
            if market != "CN":
                return "因子 IC 归因报告仅支持 A 股(CN)。"
            try:
                import asyncio
                from src.core.factor_ic_report import generate_factor_ic_report

                result = await asyncio.to_thread(generate_factor_ic_report, market, None)
                if not result:
                    return f"[数据源: 因子IC/IR评估] {market} 因子样本不足或 AI 归因失败(静默降级), 无法生成报告。"
                lines = [
                    f"[数据源: 因子IC/IR评估 + AI归因] {market} 因子有效性归因:",
                    f"  总评: {result.get('summary')}",
                ]
                for fa in result.get("factor_assessment") or []:
                    lines.append(f"  - {fa.get('factor_code')}: {fa.get('assessment')} — {fa.get('note')}")
                if result.get("adjustment_suggestion"):
                    lines.append(f"  调权建议: {result['adjustment_suggestion']}")
                lines.append(f"  置信度: {result.get('confidence')}")
                return "\n".join(lines)
            except Exception as e:
                logger.warning(f"get_factor_ic_report 工具失败 [{market}]: {e}")
                return f"因子 IC 归因报告失败: {str(e)[:100]}"
        else:
            return f"未知工具: {name}"
    except Exception as e:
        logger.error(f"工具执行失败 {name}: {e}")
        return f"工具执行出错: {e}"

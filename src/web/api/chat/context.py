"""context (split from chat.py 2026-09-09, no logic change)."""

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

def _build_watchlist_context(db: Session, user: User | None = None) -> str:
    """构建用户自选股列表。

    S5(2026-08-26): 传入 user 时只返回本人自选 + user_id=NULL 全局自选;
    不传保持旧行为(内部工具兼容)。
    """
    query = db.query(Stock).order_by(Stock.sort_order.asc())
    if user is not None:
        query = query.filter(or_(Stock.user_id == user.id, Stock.user_id.is_(None)))
    stocks = query.all()
    if not stocks:
        return "用户暂无自选股。"
    lines = [f"- {s.name}({s.market}:{s.symbol})" for s in stocks]
    return "自选股列表：\n" + "\n".join(lines)
_FORECAST_DB_PATH = os.path.join(os.path.expanduser("~"), ".panwatch_forecast.db")
_FORECAST_DIRECTION_CN = {"up": "看涨", "down": "看跌", "sideways": "横盘", "neutral": "中性"}
_FORECAST_COLUMN_MAP = {
    "forecasts": {
        "symbol": "symbol", "stock_name": "stock_name", "last_close": "last_close",
        "direction": "direction", "expected_pct": "expected_pct",
        "confidence": "confidence", "target_price": "target_price",
        "target_date": "target_date", "created_at": "created_at",
    },
    "prediction_runs": {
        "symbol": "symbol", "stock_name": "stock_name", "last_close": "last_close",
        "direction": "final_direction", "expected_pct": "final_expected_pct",
        "confidence": None, "target_price": "final_target_price",
        "target_date": "target_date", "created_at": "created_at",
    },
}
def _resolve_forecast_db_path() -> str:
    """解析预测引擎 SQLite 路径(与 forecast_lib.forecast_paths 同源: 环境变量优先, 默认 ~/.panwatch_forecast.db)。"""
    configured = os.getenv("FORECAST_DB_PATH", "")
    return os.path.abspath(os.path.expanduser(configured or _FORECAST_DB_PATH))
def _read_forecast(symbol: str = "", limit: int = 5) -> str:
    """读取系统最近预测(预测引擎独立库, 只读; 有 outcome 对照时优先展示, 无则返回预测本身)。"""
    db_path = _resolve_forecast_db_path()
    if not os.path.exists(db_path):
        return "暂无系统预测（未找到预测引擎数据库，预测引擎可能尚未运行）。"
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            cur = conn.cursor()
            tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            table = "forecasts" if "forecasts" in tables else ("prediction_runs" if "prediction_runs" in tables else None)
            if not table:
                return "暂无系统预测（预测引擎数据库中无预测表）。"
            cmap = _FORECAST_COLUMN_MAP[table]
            base_cols = ("symbol", "stock_name", "last_close", "direction", "expected_pct", "target_price", "target_date", "created_at")
            cols = [cmap[c] for c in base_cols if cmap.get(c)]
            if cmap.get("confidence"):
                cols.append(cmap["confidence"])
            sql = f"SELECT {', '.join(cols)} FROM {table}"
            where, params = "", []
            if symbol:
                where, params = " WHERE symbol = ?", [symbol]
            sql += where + " ORDER BY created_at DESC LIMIT ?"
            params.append(str(max(1, min(int(limit), 50))))
            rows = cur.execute(sql, params).fetchall()
            if not rows:
                return "暂无系统预测" + (f"（{symbol}）" if symbol else "") + "。"
            today = datetime.now().date().isoformat()
            lines = [f"【系统预测】最近{len(rows)}条" + (f"（{symbol}）" if symbol else "") + f"，来自预测引擎 {table} 表。",
                     "⚠️ 警告：历史回测准确率仅31.7%，预测方向不可靠，仅供参考，不可作为交易依据。"]
            for r in rows:
                # 列名统一回写为规范名(final_direction → direction 等), 便于下方格式化
                key_map = {actual: canon for canon, actual in cmap.items() if actual}
                d = {key_map.get(k, k): v for k, v in zip(cols, r)}
                direction = (d.get("direction") or "").strip()
                dir_cn = _FORECAST_DIRECTION_CN.get(direction.lower(), direction or "未知")
                pct = d.get("expected_pct")
                pct_str = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else (str(pct) if pct else "")
                target = d.get("target_price")
                target_str = f"{target:.2f}" if isinstance(target, (int, float)) else (str(target) if target else "—")
                close = d.get("last_close")
                close_str = f"{close:.2f}" if isinstance(close, (int, float)) else (str(close) if close else "—")
                tdate = (d.get("target_date") or "")[:10]
                expired = "已到期" if (tdate and tdate < today) else ("未到期" if tdate else "—")
                created = (d.get("created_at") or "")[:16]
                conf = d.get("confidence") if cmap.get("confidence") else None
                conf_str = f" 置信度:{conf}" if conf else ""
                line = (f"- {d.get('symbol')} {d.get('stock_name') or ''} {dir_cn} "
                        f"预期{pct_str} 目标价{target_str} 现价{close_str}"
                        f"{conf_str} 到期:{tdate or '—'}({expired}) 创建:{created}")
                lines.append(line)
            return "\n".join(lines)
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"get_forecast 读取预测库失败: {e}")
        return f"系统预测读取失败: {e}"
def _read_opportunities(db: Session, limit: int = 10) -> str:
    """读取今日机会候选(主库 entry_candidates, active 且有信号, 取最新日期, 按得分降序)。"""
    latest = (
        db.query(func.max(EntryCandidate.snapshot_date))
        .filter(EntryCandidate.status == "active")
        .scalar()
    )
    if not latest:
        return "暂无机会候选（今日没有 active 候选）。"
    total = (
        db.query(func.count(EntryCandidate.id))
        .filter(EntryCandidate.status == "active", EntryCandidate.snapshot_date == latest)
        .scalar()
    )
    rows = (
        db.query(EntryCandidate)
        .filter(
            EntryCandidate.status == "active",
            EntryCandidate.snapshot_date == latest,
            EntryCandidate.signal.isnot(None),
            EntryCandidate.signal != "",
        )
        .order_by(EntryCandidate.score.desc())
        .limit(max(1, min(int(limit), 50)))
        .all()
    )
    if not rows:
        return f"今日({latest})暂无带信号的机会候选（共{total}条 active，均无 signal）。"
    lines = [f"【今日机会候选】{latest} 共{total}条active，按得分Top{len(rows)}:"]
    for c in rows:
        target = c.target_price
        target_str = f"{target:.2f}" if isinstance(target, (int, float)) else "—"
        lines.append(
            f"- {c.stock_symbol} {c.stock_name} 得分{c.score:g} 操作:{c.action_label} "
            f"信号:{c.signal} 目标价:{target_str}"
        )
    return "\n".join(lines)
async def _read_sentiment_cycle() -> str:
    """情绪周期判别(2026-08-23 F1 接线): 接 MarketSentimentCollector 取涨停池
    指标 → classify_sentiment_cycle(此前为死代码, 生产零引用)。"""
    from src.core.sentiment_cycle import classify_sentiment_cycle, format_cycle
    from src.core.report_generator import _collect_limit_up_summary

    summary = await _collect_limit_up_summary()
    if not isinstance(summary, dict) or summary.get("error"):
        return "情绪周期: 涨停池数据获取失败, 暂无法判别短线情绪周期。"

    metrics = {
        "limit_up_count": summary.get("total"),
        "max_board_height": summary.get("max_days"),
        "break_rate": summary.get("break_rate"),
        "yesterday_board_perf": summary.get("yesterday_board_perf"),
        "losing_effect": summary.get("losing_effect"),
    }
    result = classify_sentiment_cycle(metrics)
    return "短线情绪周期: " + format_cycle(result)
def _read_strategy_signals(db: Session, limit: int = 10) -> str:
    """读取最新策略信号(主库 strategy_signal_runs, active 且动作属买/关注类, 取最新日期, 按得分降序)。"""
    action_whitelist = ("buy", "watch", "hold", "alert")  # 买/关注/持有/告警类信号
    latest = (
        db.query(func.max(StrategySignalRun.snapshot_date))
        .filter(
            StrategySignalRun.status == "active",
            StrategySignalRun.action.in_(action_whitelist),
        )
        .scalar()
    )
    if not latest:
        return "暂无策略信号（今日没有 active 的买/关注类信号）。"
    rows = (
        db.query(StrategySignalRun)
        .filter(
            StrategySignalRun.status == "active",
            StrategySignalRun.snapshot_date == latest,
            StrategySignalRun.action.in_(action_whitelist),
        )
        .order_by(StrategySignalRun.score.desc())
        .limit(max(1, min(int(limit), 50)))
        .all()
    )
    if not rows:
        return f"最新交易日({latest})暂无买/关注类策略信号。"
    lines = [f"【策略信号】{latest} 最新active买/关注类信号 Top{len(rows)}:"]
    for s in rows:
        score = f"{s.score:g}" if isinstance(s.score, (int, float)) else str(s.score or "—")
        lines.append(
            f"- {s.stock_symbol} {s.stock_name} 策略:{s.strategy_name or s.strategy_code} "
            f"动作:{s.action_label}({s.action}) 得分:{score} 信号:{s.signal or '—'}"
        )
    return "\n".join(lines)
def _read_notifications(db: Session, limit: int = 10, unread_only: bool = False) -> str:
    """读取最近通知(主库 notifications, 按时间倒序; unread_only 时只取未读)。"""
    q = db.query(Notification)
    if unread_only:
        q = q.filter(Notification.read_at.is_(None))
    total = q.count()
    if total == 0:
        return "暂无通知" + ("（无未读通知）" if unread_only else "") + "。"
    rows = q.order_by(Notification.created_at.desc()).limit(max(1, min(int(limit), 50))).all()
    lines = [f"【系统通知】最近{len(rows)}条" + ("（未读）" if unread_only else "") + f"（共{total}条）:"]
    for n in rows:
        ts = n.created_at.strftime("%Y-%m-%d %H:%M") if n.created_at is not None else ""
        unread = "未读" if n.read_at is None else "已读"
        body = (n.body or "").strip().replace("\n", " ")
        body = body[:50] + ("…" if len(body) > 50 else "")
        lines.append(f"- [{ts}] {n.title} 类型:{n.category}/{n.level} {unread} {body}")
    return "\n".join(lines)
def _fmt_yi(v) -> str:
    """元 → 亿(2位小数); None → —。"""
    if v is None:
        return "—"
    try:
        return f"{float(v) / 1e8:,.2f}"
    except (TypeError, ValueError):
        return str(v)
def _fmt_num(v) -> str:
    """千分位整数; None → —。"""
    if v is None:
        return "—"
    try:
        return f"{float(v):,.0f}"
    except (TypeError, ValueError):
        return str(v)
def _format_fundamentals_text(symbol: str, market: str, data: dict) -> str:
    """把 fetch_fundamentals_detail 的 dict 渲染成对话助手可读文本(无数据明确说「暂无」)。"""
    lines = [f"【{symbol} 基本面明细】(市场 {market})"]

    # 1) 龙虎榜(近10日)
    dt = data.get("dragon_tiger") or []
    lines.append(f"■ 龙虎榜(近10日): {len(dt)}条" if dt else "■ 龙虎榜(近10日): 暂无")
    for r in dt[:8]:
        chg = r.get("change_pct")
        chg_str = f"{chg:+.2f}%" if isinstance(chg, (int, float)) else "—"
        reason = r.get("reason") or "—"
        lines.append(
            f"- {r.get('trade_date') or '—'} 收盘{_fmt_num(r.get('close'))} "
            f"涨跌{chg_str} 净买{_fmt_yi(r.get('net_buy'))}亿 "
            f"买入{_fmt_yi(r.get('buy_amt'))}亿 卖出{_fmt_yi(r.get('sell_amt'))}亿 原因:{reason}"
        )

    # 2) 融资融券
    mg = data.get("margin") or []
    lines.append(f"■ 融资融券: {len(mg)}条" if mg else "■ 融资融券: 暂无")
    for r in mg[:3]:
        lines.append(
            f"- {r.get('date') or '—'} 融资余额{_fmt_yi(r.get('rz_balance'))}亿 "
            f"融券余额{_fmt_yi(r.get('rq_balance'))}亿 两融合计{_fmt_yi(r.get('total_balance'))}亿 "
            f"融资买入{_fmt_yi(r.get('rz_buy'))}亿 融资偿还{_fmt_yi(r.get('rz_repay'))}亿"
        )

    # 3) 股东户数
    sh = data.get("shareholders") or []
    lines.append(f"■ 股东户数: {len(sh)}期" if sh else "■ 股东户数: 暂无")
    for r in sh[:3]:
        cr = r.get("change_ratio")
        cr_str = f"{cr:+.2f}%" if isinstance(cr, (int, float)) else "—"
        cn = r.get("change_num")
        cn_str = f"{int(cn):+,}" if isinstance(cn, (int, float)) else "—"
        lines.append(
            f"- {r.get('report_date') or '—'} 户数{_fmt_num(r.get('holder_num'))} "
            f"较上期{cn_str}户(环比{cr_str}) 户均持股{_fmt_num(r.get('avg_shares'))}"
        )

    # 4) 分红
    dv = data.get("dividend") or []
    lines.append(f"■ 分红: {len(dv)}次" if dv else "■ 分红: 暂无")
    for r in dv[:8]:
        dps = r.get("dividend_per_share")
        dps_str = f"{dps:.2f}元" if isinstance(dps, (int, float)) else "—"
        tf = r.get("transfer_ratio")
        bf = r.get("bonus_ratio")
        tf_str = f"{tf:g}" if isinstance(tf, (int, float)) else "—"
        bf_str = f"{bf:g}" if isinstance(bf, (int, float)) else "—"
        lines.append(
            f"- {r.get('ex_date') or '—'} 每股派息{dps_str} 每10股转增{tf_str} "
            f"每10股送股{bf_str} [{r.get('progress') or '—'}]"
        )

    # 5) 事件日历(近7日)
    ev = data.get("events") or []
    lines.append(f"■ 事件日历(近7日): {len(ev)}条" if ev else "■ 事件日历(近7日): 暂无")
    for r in ev[:10]:
        ts = (r.get("publish_time") or "")[:10]
        src = r.get("source") or ""
        lines.append(f"- [{ts}] {r.get('title') or '—'} ({src})")

    return "\n".join(lines)
_INTERNAL_HOSTNAMES = {
    "localhost", "metadata.google.internal", "metadata.tencentyun.com",
    "metadata.aliyun.com", "metadata", "kubernetes.default.svc",
}
def _is_internal_target(parsed) -> bool:
    """判断目标 URL 是否指向内网/本地/云 metadata(SSRF 拦截)。"""
    import ipaddress
    import socket

    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        return True
    if host in _INTERNAL_HOSTNAMES:
        return True
    # IP 形式直接判断
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
    except ValueError:
        pass
    # 域名形式: 解析一次, 命中内网段也拒绝(防 DNS 指向内网)
    try:
        for info in socket.getaddrinfo(host, None):
            try:
                ip = ipaddress.ip_address(info[4][0])
                if ip.is_private or ip.is_loopback or ip.is_link_local:
                    return True
            except ValueError:
                continue
    except (socket.gaierror, OSError):
        pass  # 解析失败交给后续请求报错
    return False
_WEB_CONTENT_MAX_CHARS = 3000              # 返回给 LLM 的正文截断上限
_WEB_CONTENT_MAX_BYTES = 2 * 1024 * 1024   # 响应体读取上限, 防异常大页面拖垮
_WEB_FETCH_TIMEOUT = 15                    # 秒
_WEB_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_WEB_BLOCK_TAGS = frozenset({
    "p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "tr", "section", "article", "blockquote", "pre", "ul", "ol", "table", "hr",
})
_WEB_SKIP_TAGS = frozenset({
    "script", "style", "noscript", "head", "title", "meta", "link",
    "iframe", "svg", "nav", "footer", "header", "form", "button",
    "template", "video", "audio", "canvas", "aside",
})
_HTML_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})
class _WebTextExtractor(html.parser.HTMLParser):
    """轻量 HTML 正文提取器: 跳过 script/style 等噪音标签, 块级标签边界补换行。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _WEB_SKIP_TAGS and tag not in _HTML_VOID_TAGS:
            self._skip_depth += 1
        if self._skip_depth == 0 and tag in _WEB_BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _WEB_SKIP_TAGS and tag not in _HTML_VOID_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
        elif self._skip_depth == 0 and tag in _WEB_BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self.parts.append(data)
def _extract_web_text(html_text: str) -> str:
    """从 HTML 提取正文文本: 去噪音标签 → 折叠空白 → 去空行。解析异常不致命, 用已收集部分。"""
    parser = _WebTextExtractor()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:
        pass
    lines = []
    for ln in re.split(r"\n+", "".join(parser.parts)):
        ln = re.sub(r"[ \t\u00a0]+", " ", ln).strip()
        if ln:
            lines.append(ln)
    return "\n".join(lines)
def get_web_content(url: str) -> str:
    """抓取网页链接正文文本, 供 AI 分析用户发来的链接(含微信公众号文章 mp.weixin.qq.com)。

    安全/健壮性: 仅允许 http/https; 15s 超时; 常见浏览器 UA; 响应体上限 2MB;
    正文截断到 3000 字符返回; 任何失败均返回友好错误文本, 不抛异常。
    """
    url = (url or "").strip()
    if not url:
        return "抓取失败: 链接为空, 请提供有效的 http/https 网址。"
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return "抓取失败: 链接格式非法, 仅支持 http/https 网址。"
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "抓取失败: 仅支持 http/https 链接, 请检查链接格式。"

    # SSRF 防护(2026-08-15): 拒绝内网/本地/云 metadata 地址, 防服务器被当作代理扫描内网
    if _is_internal_target(parsed):
        return "抓取失败: 目标链接为内网/本地地址, 已拒绝访问。"

    try:
        import httpx
    except ImportError:
        return "抓取失败: 当前环境缺少 httpx 依赖, 无法发起网络请求。"

    try:
        headers = {
            "User-Agent": _WEB_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        raw = b""
        encoding = "utf-8"
        with httpx.Client(timeout=_WEB_FETCH_TIMEOUT, follow_redirects=True, headers=headers) as client:
            with client.stream("GET", url) as resp:
                resp.raise_for_status()
                ctype = (resp.headers.get("content-type") or "").lower()
                if ctype and not any(k in ctype for k in ("text/", "html", "xhtml", "xml", "json")):
                    return (
                        "抓取失败: 目标链接返回的不是网页内容"
                        f"(Content-Type: {ctype.split(';')[0].strip()}), 无法提取正文。"
                    )
                for chunk in resp.iter_bytes():
                    raw += chunk
                    if len(raw) > _WEB_CONTENT_MAX_BYTES:
                        return "抓取失败: 页面超过 2MB 读取上限, 已放弃抓取(可能为异常大页面)。"
                encoding = resp.encoding or "utf-8"
        try:
            html_text = raw.decode(encoding, errors="replace")
        except (LookupError, UnicodeDecodeError):
            html_text = raw.decode("utf-8", errors="replace")
        text = _extract_web_text(html_text)
        if not text:
            return f"抓取失败: 页面未提取到正文文本({url})。"
        if len(text) > _WEB_CONTENT_MAX_CHARS:
            text = text[:_WEB_CONTENT_MAX_CHARS] + "…[已截断]"
        return f"【网页内容】{url}\n{text}"
    except httpx.HTTPStatusError as e:
        return f"抓取失败: 目标链接返回 HTTP {e.response.status_code}。"
    except httpx.TimeoutException:
        return "抓取失败: 请求超时(15s), 链接可能不可达或响应过慢。"
    except httpx.RequestError as e:
        return f"抓取失败: 网络请求错误({e.__class__.__name__}: {str(e)[:120]})。"
    except Exception as e:
        logger.warning(f"get_web_content 抓取失败 [{url}]: {e}")
        return f"抓取失败: {str(e)[:120]}。"
def _build_stock_context(db: Session, symbol: str, market: str, user: User | None = None) -> str:
    """为绑定股票构建上下文摘要。

    S5(2026-08-26): 传入 user 时按归属过滤建议/报告(NULL 视为共享),
    防止跨账号读取他人的 AI 建议与分析历史。
    """
    parts = []

    # 最近建议
    sug_query = db.query(StockSuggestion).filter(
        StockSuggestion.stock_symbol == symbol,
        StockSuggestion.stock_market == market,
    )
    if user is not None:
        sug_query = sug_query.filter(
            or_(
                StockSuggestion.user_id == user.id,
                StockSuggestion.user_id.is_(None),
            )
        )
    suggestions = (
        sug_query.order_by(StockSuggestion.created_at.desc())
        .limit(3)
        .all()
    )
    if suggestions:
        lines = []
        for s in suggestions:
            lines.append(f"- [{s.agent_label or s.agent_name}] {s.action_label}: {s.signal or s.reason or ''}")
        parts.append("最近 AI 建议：\n" + "\n".join(lines))

    # 最近分析报告
    hist_query = db.query(AnalysisHistory).filter(
        AnalysisHistory.stock_symbol == symbol
    )
    if user is not None:
        hist_query = hist_query.filter(
            or_(
                AnalysisHistory.user_id == user.id,
                AnalysisHistory.user_id.is_(None),
            )
        )
    histories = (
        hist_query.order_by(AnalysisHistory.created_at.desc())
        .limit(1)
        .all()
    )
    if histories:
        h = histories[0]
        content_preview = (h.content or "")[:500]
        parts.append(f"最近分析（{h.agent_name}, {h.analysis_date}）：\n{content_preview}")

    if not parts:
        return ""
    return "\n\n".join(parts)
def _build_portfolio_context(db: Session, user: User | None = None) -> str:
    """构建用户全部持仓摘要。

    S5(2026-08-26): 传入 user 时只返回本人持仓 + user_id=NULL 全局持仓,
    实盘(Position)与模拟盘(PaperTradingPosition)同样处理。
    """
    lines: list[str] = []

    # 实盘持仓
    pos_query = db.query(Position)
    if user is not None:
        pos_query = pos_query.filter(
            or_(Position.user_id == user.id, Position.user_id.is_(None))
        )
    positions = pos_query.all()
    if positions:
        real_lines = []
        for p in positions:
            stock = db.query(Stock).filter(Stock.id == p.stock_id).first()
            if not stock:
                continue
            real_lines.append(
                f"- {stock.name}({stock.market}:{stock.symbol}) "
                f"{p.quantity}股 成本{p.cost_price} 风格{p.trading_style or '波段'}"
            )
        if real_lines:
            lines.append("实盘持仓：\n" + "\n".join(real_lines))

    # 模拟盘持仓
    paper_query = db.query(PaperTradingPosition).filter(
        PaperTradingPosition.status == "open"
    )
    if user is not None and hasattr(PaperTradingPosition, "user_id"):
        paper_query = paper_query.filter(
            or_(
                PaperTradingPosition.user_id == user.id,
                PaperTradingPosition.user_id.is_(None),
            )
        )
    paper_positions = paper_query.all()
    if paper_positions:
        paper_lines = []
        for pp in paper_positions:
            pnl_str = f"浮盈{pp.unrealized_pnl:.1f}" if pp.unrealized_pnl else ""
            paper_lines.append(
                f"- {pp.stock_name or pp.stock_symbol}({pp.stock_market}:{pp.stock_symbol}) "
                f"{pp.quantity}股 入场价{pp.entry_price}"
                f"{f' 止损{pp.stop_loss}' if pp.stop_loss else ''}"
                f"{f' 目标{pp.target_price}' if pp.target_price else ''}"
                f"{f' {pnl_str}' if pnl_str else ''}"
            )
        if paper_lines:
            lines.append("模拟盘持仓：\n" + "\n".join(paper_lines))

    if not lines:
        return ""
    return "\n\n".join(lines)
async def _fetch_realtime_context(symbol: str, market: str) -> str:
    """异步获取实时行情和技术面。"""
    try:
        from src.core.marketdata_client import md_quote_rows
        from src.models.market import MarketCode

        mc = MarketCode(market) if market in ("CN", "HK", "US") else MarketCode.CN
        rows = await asyncio.to_thread(md_quote_rows, [symbol], mc.value)
        if not rows:
            return ""
        q = rows[0]
        price = q.get("current_price", "--")
        change = q.get("change_pct", "--")
        volume = q.get("volume", "--")
        name = q.get("name", symbol)
        return f"实时行情：{name}（{market}:{symbol}）价格 {price}，涨跌幅 {change}%，成交量 {volume}"
    except Exception as e:
        logger.debug(f"获取实时行情失败: {e}")
        return ""
async def _fetch_technical_context(symbol: str, market: str) -> str:
    """获取技术面摘要。"""
    try:
        from src.collectors.kline_collector import KlineCollector
        from src.models.market import MarketCode

        mc = MarketCode(market) if market in ("CN", "HK", "US") else MarketCode.CN
        collector = KlineCollector(mc)
        summary = await asyncio.to_thread(
            collector.get_kline_summary, symbol
        )
        if not summary or summary.get("error"):
            return ""
        # get_kline_summary 直接返回 summary 内容(无嵌套);兼容 API 层包装
        s = summary.get("summary", {}) if "summary" in summary else summary
        trend = s.get("trend", "--")
        macd = s.get("macd_status", "--")
        rsi = s.get("rsi_status") or (f"{s.get('rsi6')}" if s.get('rsi6') is not None else "--")
        support = s.get("support", "--")
        resistance = s.get("resistance", "--")
        # 形态
        pattern = s.get("kline_pattern") or "--"
        return f"技术面：趋势 {trend}，MACD {macd}，RSI {rsi}，支撑位 {support}，压力位 {resistance}，K线形态 {pattern}"
    except Exception as e:
        logger.debug(f"获取技术面失败: {e}")
        return ""
async def _fetch_capital_flow_context(symbol: str, market: str) -> str:
    """获取主力资金流向摘要（A股, 今日实时, 含四档分项）。"""
    try:
        from src.collectors.capital_flow_collector import CapitalFlowCollector
        from src.models.market import MarketCode

        mc = MarketCode(market) if market in ("CN", "HK", "US") else MarketCode.CN
        collector = CapitalFlowCollector(mc)
        summary = await asyncio.to_thread(
            collector.get_capital_flow_summary, symbol
        )
        if not summary or summary.get("error"):
            return ""

        def _fmt(v: float | None) -> str:
            """净额(元) → 亿/万 友好格式。"""
            if v is None:
                return "--"
            if abs(v) >= 1e8:
                return f"{v / 1e8:+.2f}亿"
            return f"{v / 1e4:+.0f}万"

        main = float(summary.get("main_net_inflow") or 0)
        direction = "净流入" if main > 0 else ("净流出" if main < 0 else "平衡")
        pct = summary.get("main_net_inflow_pct")
        # collector 已归一化为 %(f184 ×100 → %); None 显示 --
        pct_str = f"{float(pct):+.1f}%" if pct is not None else "--"
        flow_date = summary.get("date") or "最近交易日"

        lines = [f"资金流向（今日实时, 基准日 {flow_date}）"]
        lines.append(f"- 主力{direction} {_fmt(main)}（占比{pct_str}）")
        if summary.get("super_net_inflow") is not None:
            lines.append(
                f"- 超大单{_fmt(summary.get('super_net_inflow'))} | "
                f"大单{_fmt(summary.get('big_net_inflow'))} | "
                f"中单{_fmt(summary.get('mid_net_inflow'))} | "
                f"小单{_fmt(summary.get('small_net_inflow'))}"
            )
        if summary.get("trend_5d") and summary.get("trend_5d") != "无数据":
            lines.append(f"- 5日资金：{summary.get('trend_5d')}")
        # 分歧提示: 主力流入但超大单流出
        super_net = summary.get("super_net_inflow")
        if main > 0 and super_net is not None and float(super_net) < 0:
            lines.append("- ⚠️ 主力净流入但超大单净流出(分歧): 大单拉抬、超大单出货, 谨慎追涨")
        return "\n".join(lines)
    except Exception as e:
        logger.debug(f"获取资金流失败: {e}")
        return ""
async def _fetch_kline_pattern_context(symbol: str, market: str) -> str:
    """识别 K 线组合形态(同花顺教学体系 + TA-Lib 标准形态)。"""
    try:
        from src.core.marketdata_client import get_market_data
        from src.core.kline_pattern import detect_patterns, format_patterns
        from src.collectors.kline_collector import _detect_talib_patterns

        md = get_market_data()
        bars = await asyncio.to_thread(md.klines, symbol, market="CN" if market == "CN" else market, days=60)
        if not bars:
            return f"未能获取 {market}:{symbol} 的K线数据。"
        hits = detect_patterns(bars)
        text = format_patterns(hits)
        # TA-Lib 标准形态
        talib_hits = _detect_talib_patterns(list(bars))
        if talib_hits:
            text += "\n\n【TA-Lib 标准形态】"
            for p in talib_hits[:8]:
                text += f"\n- {p['cn_name']}({p['name']}) {p['signal']} 强度{p['strength']}"
        # 附带最近价格信息
        last = bars[-1]
        head = f"{market}:{symbol} 最近K线({last.date}): 开{last.open} 高{last.high} 低{last.low} 收{last.close}\n"
        return head + text
    except Exception as e:
        logger.debug(f"获取K线形态失败: {e}")
        return f"K线形态识别失败: {e}"
async def _fetch_auction_context(scene: str, limit: int = 10) -> str:
    """集合竞价数据(auction_collector: 悟道优先, 腾讯批量降级, 30s 缓存)。"""
    from src.collectors.auction_collector import (
        fetch_auction_overview,
        fetch_auction_strongest,
        fetch_auction_theme,
        fetch_auction_weak_to_strong,
        fetch_auction_risk,
    )

    scene = (scene or "overview").strip() or "overview"
    try:
        if scene in ("strongest", "watchlist"):
            return fetch_auction_strongest(limit=limit)
        if scene == "theme":
            return fetch_auction_theme(limit=limit)
        if scene == "weak_to_strong":
            return fetch_auction_weak_to_strong(limit=limit)
        if scene == "risk":
            return fetch_auction_risk(limit=limit)
        return fetch_auction_overview(limit=limit)
    except Exception as e:
        logger.debug(f"获取集合竞价失败: {e}")
        return f"集合竞价数据获取失败: {e}"
def _latest_unexpired_forecast_symbol(symbol: str = "") -> str:
    """读取预测库中最新一条未到期预测(target_date >= 今天)的股票代码。

    优先匹配传入的 symbol; 无匹配则取全局最新一条未到期预测。读取失败/无数据返回空串。
    """
    db_path = _resolve_forecast_db_path()
    if not os.path.exists(db_path):
        return ""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            cur = conn.cursor()
            tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            table = "forecasts" if "forecasts" in tables else ("prediction_runs" if "prediction_runs" in tables else None)
            if not table:
                return ""
            today = datetime.now().date().isoformat()
            if symbol:
                row = cur.execute(
                    f"SELECT symbol FROM {table} WHERE symbol = ? AND target_date >= ? ORDER BY created_at DESC LIMIT 1",
                    [symbol, today],
                ).fetchone()
                if row:
                    return row[0]
            row = cur.execute(
                f"SELECT symbol FROM {table} WHERE target_date >= ? ORDER BY created_at DESC LIMIT 1",
                [today],
            ).fetchone()
            return row[0] if row else ""
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"suggested_questions 读预测库失败: {e}")
        return ""

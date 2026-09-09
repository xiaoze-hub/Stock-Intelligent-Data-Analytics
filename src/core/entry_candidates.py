from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

from sqlalchemy import and_, case, func, or_

from src.config import Settings
from src.collectors.discovery_collector import EastMoneyDiscoveryCollector
from src.collectors.kline_collector import KlineCollector
from src.core.json_safe import to_jsonable
from src.core.marketdata_client import md_stock_data
from src.core.notifier import get_global_proxy
from src.core.timezone import to_iso_with_tz, to_utc, utc_now
from src.models.market import MarketCode
from src.web.database import SessionLocal
from src.web.models import (
    EntryCandidate,
    EntryCandidateFeedback,
    EntryCandidateOutcome,
    MarketScanSnapshot,
    Position,
    Stock,
    StockSuggestion,
)

logger = logging.getLogger(__name__)


ACTION_BASE_SCORE: dict[str, float] = {
    "buy": 78.0,
    "add": 72.0,
    "hold": 58.0,
    "watch": 52.0,
    "alert": 45.0,
    "reduce": 30.0,
    "sell": 20.0,
    "avoid": 15.0,
}

AGENT_LABELS: dict[str, str] = {
    "premarket_outlook": "盘前分析",
    "intraday_monitor": "盘中监测",
    "daily_report": "收盘复盘",
    "news_digest": "新闻速递",
    "market_scan": "市场扫描",
}


CANDIDATE_SOURCE_LABELS: dict[str, str] = {
    "watchlist": "关注池",
    "market_scan": "市场池",
    "mixed": "市场+关注",
    # P1 多源入池(2026-08-21)
    "strategy": "策略信号",
    "auction": "竞价异动",
    "tdx": "问小达",
    "wencai": "问财选股",
}


STRATEGY_LABELS: dict[str, str] = {
    "trend_follow": "趋势延续",
    "macd_golden": "MACD金叉",
    "volume_breakout": "放量突破",
    "momentum": "动量强化",
    "pullback": "回踩确认",
    "rebound": "超跌反弹",
    "watchlist_agent": "Agent建议",
}

MARKET_SCAN_SEED_SYMBOLS: dict[str, list[str]] = {
    "CN": [
        "600519",
        "601318",
        "601127",
        "300750",
        "300308",
        "000333",
        "002594",
        "601899",
        "600036",
        "600900",
        "000858",
        "601288",
        "600276",
        "601888",
        "000651",
    ],
    "HK": [
        "00700",
        "09988",
        "03690",
        "01810",
        "02318",
        "01299",
        "00941",
        "00388",
        "00883",
        "09999",
        "01398",
        "02628",
    ],
    "US": [
        "AAPL",
        "MSFT",
        "NVDA",
        "AMZN",
        "GOOGL",
        "META",
        "TSLA",
        "AMD",
        "NFLX",
        "INTC",
        "BABA",
        "PDD",
        "NIO",
        "TSM",
        "QQQ",
    ],
}

# 自动市场扫描/候选池只跑目标市场: 生产为 A 股(CN)盯盘系统, 用户不做港美股,
# 港美股不再生成快照与候选(避免白烧 CPU / LLM token)。
# 注意: discovery 页面 CN/HK/US 三市场手动查询是产品功能, 走 discovery_collector
# 独立入口, 不受本常量影响。
MARKET_SCAN_TARGET_MARKETS: tuple[str, ...] = ("CN",)

# 无明确信号的占位文案: 入库时直接过滤, 不占候选池位。
NO_SIGNAL_MARKERS: frozenset[str] = frozenset({"", "暂无明确信号", "无明确信号"})


def _has_real_signal(signal: str | None) -> bool:
    """判断是否携带明确信号(排除空串与"暂无明确信号"占位)。"""
    s = (signal or "").strip()
    return bool(s) and s not in NO_SIGNAL_MARKERS


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _safe_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _to_market(value: str | None) -> MarketCode:
    try:
        return MarketCode((value or "CN").strip().upper())
    except Exception:
        return MarketCode.CN


def _resolve_market_scan_proxy() -> str | None:
    try:
        proxy = (get_global_proxy() or "").strip()
        if proxy:
            return proxy
    except Exception:
        pass
    try:
        proxy = (Settings().http_proxy or "").strip()
        if proxy:
            return proxy
    except Exception:
        pass
    return None


def _run_async(coro):
    try:
        return asyncio.run(coro)
    except RuntimeError:
        # In case we're in an already running loop, create an isolated one.
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def _candidate_sort_key(item: dict) -> tuple[int, float, float]:
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    source = str(meta.get("source") or "")
    source_priority = {
        "market_scan": 0,
        "market_scan_history": 1,
        "market_scan_seed_universe": 2,
        "market_scan_seed_watchlist": 3,
    }
    quote = item.get("quote_seed") if isinstance(item.get("quote_seed"), dict) else {}
    turnover = _safe_float(quote.get("turnover")) or 0.0
    change_abs = abs(_safe_float(quote.get("change_pct")) or 0.0)
    return (
        source_priority.get(source, 9),
        -float(turnover),
        -float(change_abs),
    )


def _extract_price_from_meta(meta: dict | None) -> dict:
    data = meta if isinstance(meta, dict) else {}
    quote = data.get("quote") if isinstance(data.get("quote"), dict) else {}
    context = data.get("context") if isinstance(data.get("context"), dict) else {}
    context_quote = (
        context.get("quote") if isinstance(context.get("quote"), dict) else {}
    )
    kline = data.get("kline") if isinstance(data.get("kline"), dict) else {}
    context_kline = (
        context.get("kline") if isinstance(context.get("kline"), dict) else {}
    )
    plan = data.get("plan") if isinstance(data.get("plan"), dict) else {}

    price = (
        _safe_float(quote.get("current_price"))
        or _safe_float(context_quote.get("current_price"))
        or _safe_float(data.get("trigger_price"))
        or _safe_float(data.get("current_price"))
        or _safe_float(kline.get("last_close"))
        or _safe_float(kline.get("close"))
        or _safe_float(context_kline.get("last_close"))
        or _safe_float(context_kline.get("close"))
        or _safe_float(plan.get("entry_price"))
    )
    change_pct = (
        _safe_float(quote.get("change_pct"))
        or _safe_float(context_quote.get("change_pct"))
        or _safe_float(data.get("change_pct"))
    )
    result = {}
    if price is not None:
        result["current_price"] = price
    if change_pct is not None:
        result["change_pct"] = change_pct
    return result


def _normalize_kline_summary(data: dict | None) -> dict:
    raw = data if isinstance(data, dict) else {}
    if not raw:
        return {}
    out: dict[str, object] = {}
    text_keys = ("trend", "macd_cross", "rsi_status", "kdj_status")
    num_keys = (
        "volume_ratio",
        "support",
        "resistance",
        "support_m",
        "resistance_m",
        "last_close",
        "close",
    )
    for key in text_keys:
        val = raw.get(key)
        if val is None:
            continue
        text = str(val).strip()
        if text:
            out[key] = text
    for key in num_keys:
        val = _safe_float(raw.get(key))
        if val is not None:
            out[key] = val
    return out


def _extract_kline_from_meta(meta: dict | None) -> dict:
    data = meta if isinstance(meta, dict) else {}
    if not data:
        return {}

    candidates: list[dict] = []
    for key in ("kline", "kline_summary"):
        item = data.get(key)
        if isinstance(item, dict):
            candidates.append(item)
    context = data.get("context")
    if isinstance(context, dict):
        for key in ("kline", "kline_summary"):
            item = context.get(key)
            if isinstance(item, dict):
                candidates.append(item)
    source_meta = data.get("source_meta")
    if isinstance(source_meta, dict):
        for key in ("kline", "kline_summary"):
            item = source_meta.get(key)
            if isinstance(item, dict):
                candidates.append(item)

    for item in candidates:
        normalized = _normalize_kline_summary(item)
        if normalized:
            return normalized
    return {}


def _load_quote_fallbacks(
    keys: set[str], *, lookback_days: int = 2, max_rows: int = 4000
) -> dict[str, dict]:
    if not keys:
        return {}

    symbols = sorted({k.split(":", 1)[1] for k in keys if ":" in k})
    if not symbols:
        return {}

    cutoff = utc_now() - timedelta(days=max(1, int(lookback_days)))
    db = SessionLocal()
    try:
        rows = (
            db.query(StockSuggestion)
            .filter(
                StockSuggestion.stock_symbol.in_(symbols),
                StockSuggestion.agent_name == "intraday_monitor",
                StockSuggestion.created_at >= cutoff,
            )
            .order_by(StockSuggestion.id.desc())
            .limit(max_rows)
            .all()
        )
    finally:
        db.close()

    fallback: dict[str, dict] = {}
    for row in rows:
        key = f"{_to_market(row.stock_market).value}:{row.stock_symbol}"
        if key not in keys or key in fallback:
            continue
        quote = _extract_price_from_meta(row.meta or {})
        if _safe_float(quote.get("current_price")) is not None:
            fallback[key] = quote
        if len(fallback) >= len(keys):
            break
    return fallback


def _load_kline_fallbacks(
    keys: set[str], *, lookback_days: int = 7, max_rows: int = 6000
) -> dict[str, dict]:
    if not keys:
        return {}
    symbols = sorted({k.split(":", 1)[1] for k in keys if ":" in k})
    if not symbols:
        return {}

    cutoff_day = (date.today() - timedelta(days=max(1, int(lookback_days)))).strftime("%Y-%m-%d")
    cutoff_time = utc_now() - timedelta(days=max(1, int(lookback_days)))
    fallback: dict[str, dict] = {}

    db = SessionLocal()
    try:
        rows = (
            db.query(EntryCandidate)
            .filter(
                EntryCandidate.stock_symbol.in_(symbols),
                EntryCandidate.snapshot_date >= cutoff_day,
            )
            .order_by(EntryCandidate.snapshot_date.desc(), EntryCandidate.updated_at.desc())
            .limit(max_rows)
            .all()
        )
        for row in rows:
            key = f"{_to_market(row.stock_market).value}:{row.stock_symbol}"
            if key not in keys or key in fallback:
                continue
            kline = _extract_kline_from_meta(row.meta if isinstance(row.meta, dict) else {})
            if not kline:
                continue
            fallback[key] = kline
            if len(fallback) >= len(keys):
                return fallback

        srows = (
            db.query(StockSuggestion)
            .filter(
                StockSuggestion.stock_symbol.in_(symbols),
                StockSuggestion.created_at >= cutoff_time,
                StockSuggestion.agent_name.in_(("intraday_monitor", "premarket_outlook", "daily_report")),
            )
            .order_by(StockSuggestion.id.desc())
            .limit(max_rows)
            .all()
        )
        for row in srows:
            key = f"{_to_market(row.stock_market).value}:{row.stock_symbol}"
            if key not in keys or key in fallback:
                continue
            kline = _extract_kline_from_meta(row.meta if isinstance(row.meta, dict) else {})
            if not kline:
                continue
            fallback[key] = kline
            if len(fallback) >= len(keys):
                break
    finally:
        db.close()

    return fallback


def _score_suggestion(
    *,
    action: str,
    suggestion: StockSuggestion,
    quote: dict | None,
    kline: dict | None,
    resonance_meta: dict | None = None,
) -> tuple[float, list[str]]:
    score = ACTION_BASE_SCORE.get((action or "").strip().lower(), 45.0)
    evidence: list[str] = []

    if suggestion.signal:
        evidence.append(f"建议信号: {suggestion.signal}")
    if suggestion.reason:
        evidence.append(f"建议依据: {suggestion.reason}")

    if quote:
        pct = _safe_float(quote.get("change_pct"))
        if pct is not None:
            if 0.5 <= abs(pct) <= 6:
                score += 2
                evidence.append(f"当日波动适中({pct:+.2f}%)")
            elif abs(pct) >= 10:
                score -= 3
                evidence.append(f"当日波动过大({pct:+.2f}%)")

    if kline:
        trend = (kline.get("trend") or "").strip()
        if trend == "多头排列":
            score += 8
            evidence.append("均线多头排列")
        elif trend == "空头排列":
            score -= 8
            evidence.append("均线空头排列")

        macd = (kline.get("macd_cross") or "").strip()
        if macd == "金叉":
            score += 5
            evidence.append("MACD 金叉")
        elif macd == "死叉":
            score -= 5
            evidence.append("MACD 死叉")

        rsi_status = (kline.get("rsi_status") or "").strip()
        if rsi_status in ("超卖", "偏弱"):
            score += 2
            evidence.append(f"RSI {rsi_status}")
        elif rsi_status in ("超买",):
            score -= 3
            evidence.append(f"RSI {rsi_status}")

        kdj_status = (kline.get("kdj_status") or "").strip()
        if "金叉" in kdj_status:
            score += 3
            evidence.append("KDJ 金叉")
        elif "死叉" in kdj_status:
            score -= 3
            evidence.append("KDJ 死叉")

        vol_ratio = _safe_float(kline.get("volume_ratio"))
        if vol_ratio is not None:
            if vol_ratio >= 2:
                score += 4
                evidence.append(f"量比放大({vol_ratio:.1f}x)")
            elif vol_ratio >= 1.3:
                score += 2
                evidence.append(f"量比温和放大({vol_ratio:.1f}x)")

    q = suggestion.meta or {}
    quality = _safe_float((q.get("context_quality_score") if isinstance(q, dict) else None))
    if quality is not None:
        quality_bonus = _clamp((quality - 60.0) / 10.0, -3.0, 5.0)
        score += quality_bonus
        evidence.append(f"上下文质量分 {quality:.0f}")

    created_at = suggestion.created_at
    if created_at:
        try:
            hours = (utc_now() - to_utc(created_at)).total_seconds() / 3600.0
            if hours <= 6:
                score += 3
                evidence.append("建议新鲜度高(6h内)")
            elif hours >= 48:
                score -= 3
                evidence.append("建议时效偏旧(48h+)")
        except Exception:
            pass

    # 多源共振加分(2026-08-21 P1; 2026-08-23 P2 修复): 共振值写在种子 meta 里,
    # 此前误读 ORM suggestion.meta 导致从未生效 — 优先读调用方传入的种子 meta
    seed_meta = resonance_meta if isinstance(resonance_meta, dict) else {}
    sug_meta = suggestion.meta if isinstance(suggestion.meta, dict) else {}
    res_meta = {**sug_meta, **seed_meta}
    resonance = _safe_float(res_meta.get("resonance_bonus"))
    if resonance and resonance > 0:
        n = int(_safe_float(res_meta.get("resonance_count")) or 2)
        score += resonance
        sources = res_meta.get("resonance_sources") or []
        evidence.append(f"多源共振×{n}({','.join(sources[:4])})")

    score = _clamp(score, 0.0, 100.0)
    return score, evidence[:8]


def _build_plan(
    *,
    action: str,
    quote: dict | None,
    kline: dict | None,
    suggestion_meta: dict | None = None,
) -> dict:
    meta = suggestion_meta if isinstance(suggestion_meta, dict) else {}
    meta_quote = meta.get("quote") if isinstance(meta.get("quote"), dict) else {}
    context = meta.get("context") if isinstance(meta.get("context"), dict) else {}
    context_quote = (
        context.get("quote") if isinstance(context.get("quote"), dict) else {}
    )
    meta_plan = meta.get("plan") if isinstance(meta.get("plan"), dict) else {}

    price = (
        _safe_float((quote or {}).get("current_price"))
        or _safe_float(meta_quote.get("current_price"))
        or _safe_float(context_quote.get("current_price"))
        or _safe_float(meta.get("trigger_price"))
        or _safe_float(meta.get("current_price"))
        or _safe_float(meta_plan.get("entry_price"))
        or _safe_float((kline or {}).get("last_close"))
    )
    support = _safe_float((kline or {}).get("support_m")) or _safe_float((kline or {}).get("support"))
    resistance = _safe_float((kline or {}).get("resistance_m")) or _safe_float((kline or {}).get("resistance"))
    trend = (kline or {}).get("trend") or ""

    entry_low = None
    entry_high = None
    stop_loss = None
    target_price = None
    invalidation = ""

    if price is not None:
        if support is None:
            support = price * 0.95
        if resistance is None:
            resistance = price * 1.06

        if action in ("buy", "add"):
            entry_low = price * 0.99
            entry_high = price * 1.01
            stop_loss = (support * 0.985) if support else (price * 0.95)
            if resistance and resistance > price:
                target_price = resistance * 0.99
            else:
                target_price = price * 1.06
            invalidation = f"若跌破 {stop_loss:.2f} 则失效"
        elif action in ("hold", "watch"):
            stop_loss = (support * 0.98) if support else (price * 0.94)
            target_price = (resistance * 0.99) if resistance else (price * 1.04)
            invalidation = f"若跌破 {stop_loss:.2f} 则转防守"
        else:
            invalidation = "当前以风险控制为主，不建议新开仓"

    return {
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop_loss": stop_loss,
        "target_price": target_price,
        "invalidation": invalidation,
        "trend": trend,
        "support": support,
        "resistance": resistance,
    }


def _candidate_source_label(source: str) -> str:
    return CANDIDATE_SOURCE_LABELS.get((source or "").strip(), source or "")


def _strategy_labels(tags: list[str] | None) -> list[str]:
    out: list[str] = []
    for t in tags or []:
        if not t:
            continue
        out.append(STRATEGY_LABELS.get(t, t))
    return out


def _plan_quality(plan: dict | None) -> int:
    p = plan if isinstance(plan, dict) else {}
    score = 0
    if _safe_float(p.get("entry_low")) is not None or _safe_float(p.get("entry_high")) is not None:
        score += 30
    if _safe_float(p.get("stop_loss")) is not None:
        score += 30
    if _safe_float(p.get("target_price")) is not None:
        score += 30
    if str(p.get("invalidation") or "").strip():
        score += 10
    return int(_clamp(float(score), 0, 100))


def _load_holding_keys() -> set[str]:
    db = SessionLocal()
    try:
        rows = (
            db.query(Stock.market, Stock.symbol)
            .join(Position, Position.stock_id == Stock.id)
            .all()
        )
        return {
            f"{(m or 'CN').strip().upper()}:{(s or '').strip()}"
            for m, s in rows
            if s
        }
    finally:
        db.close()


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except Exception:
            continue
    return None


def _pick_close_on_or_before(klines: list, target: date) -> float | None:
    if not klines:
        return None
    rows: list[tuple[date, float]] = []
    for k in klines:
        d = _parse_day(getattr(k, "date", None))
        c = getattr(k, "close", None)
        if d is None or c is None:
            continue
        try:
            rows.append((d, float(c)))
        except Exception:
            continue
    if not rows:
        return None
    rows.sort(key=lambda x: x[0])
    for d, c in reversed(rows):
        if d <= target:
            return c
    return None


def _derive_market_scan_decision(quote: dict | None, kline: dict | None) -> dict:
    q = quote or {}
    k = kline or {}
    points = 0
    tags: list[str] = []
    reasons: list[str] = []

    trend = (k.get("trend") or "").strip()
    if trend == "多头排列":
        points += 2
        tags.append("trend_follow")
        reasons.append("均线多头排列")
    elif trend == "空头排列":
        points -= 2

    macd = (k.get("macd_cross") or "").strip()
    if macd == "金叉":
        points += 2
        tags.append("macd_golden")
        reasons.append("MACD金叉")
    elif macd == "死叉":
        points -= 2

    vol_ratio = _safe_float(k.get("volume_ratio"))
    if vol_ratio is not None:
        if vol_ratio >= 1.8:
            points += 1
            tags.append("volume_breakout")
            reasons.append(f"放量({vol_ratio:.1f}x)")
        elif vol_ratio <= 0.7:
            points -= 1

    pct = _safe_float(q.get("change_pct"))
    if pct is not None:
        if 1.5 <= pct <= 8.5:
            points += 1
            tags.append("momentum")
            reasons.append(f"涨幅{pct:+.2f}%")
        elif pct >= 10.5:
            points -= 2
        elif pct <= -5.5:
            points += 1
            tags.append("rebound")
            reasons.append("短线超跌")

    support = _safe_float(k.get("support_m")) or _safe_float(k.get("support"))
    last_close = _safe_float(k.get("last_close")) or _safe_float(q.get("current_price"))
    if support and last_close and 0 < support < last_close <= support * 1.03:
        points += 1
        tags.append("pullback")
        reasons.append("回踩支撑附近")

    action = "watch"
    action_label = "观望"
    if points >= 4:
        action = "buy"
        action_label = "建仓"
    elif points >= 3:
        action = "add"
        action_label = "准备加仓"
    elif points <= -3:
        action = "avoid"
        action_label = "回避"

    signal = "，".join(_strategy_labels(tags[:3])) if tags else "暂无明确信号"
    reason = "，".join(reasons[:3]) if reasons else "等待更高确定性信号后介入"
    return {
        "action": action,
        "action_label": action_label,
        "signal": signal,
        "reason": reason,
        "strategy_tags": tags,
        "points": points,
    }


def _score_market_scan_candidate(
    *, action: str, quote: dict | None, kline: dict | None, strategy_tags: list[str] | None,
    resonance_meta: dict | None = None,
) -> tuple[float, list[str]]:
    score = ACTION_BASE_SCORE.get((action or "").strip().lower(), 45.0)
    evidence: list[str] = []

    q = quote or {}
    k = kline or {}
    tags = strategy_tags or []

    if tags:
        score += min(8, len(tags) * 2)
        evidence.append("策略信号: " + " / ".join(_strategy_labels(tags[:3])))

    pct = _safe_float(q.get("change_pct"))
    if pct is not None:
        if 1 <= pct <= 7:
            score += 2
            evidence.append(f"价格动量({pct:+.2f}%)")
        elif pct >= 10:
            score -= 3
            evidence.append(f"涨幅过热({pct:+.2f}%)")

    turnover = _safe_float(q.get("turnover"))
    if turnover is not None:
        if turnover >= 3e9:
            score += 3
            evidence.append("成交额高")
        elif turnover >= 1e9:
            score += 1

    trend = (k.get("trend") or "").strip()
    if trend == "多头排列":
        score += 5
        evidence.append("均线多头排列")
    elif trend == "空头排列":
        score -= 6
        evidence.append("均线空头排列")

    # 多源共振加分(2026-08-23 P2 修复): 市场池/多源种子路径此前完全没读共振字段
    res_meta = resonance_meta if isinstance(resonance_meta, dict) else {}
    resonance = _safe_float(res_meta.get("resonance_bonus"))
    if resonance and resonance > 0:
        n = int(_safe_float(res_meta.get("resonance_count")) or 2)
        score += resonance
        sources = res_meta.get("resonance_sources") or []
        evidence.append(f"多源共振×{n}({','.join(sources[:4])})")

    score = _clamp(score, 0.0, 100.0)
    return score, evidence[:8]


def _load_market_scan_history_inputs(
    *, market: str, limit: int, max_days: int = 7
) -> dict[str, dict]:
    if limit <= 0:
        return {}

    mkt = (market or "CN").strip().upper()
    cutoff = (date.today() - timedelta(days=max(1, int(max_days)))).strftime("%Y-%m-%d")
    db = SessionLocal()
    try:
        rows = (
            db.query(EntryCandidate)
            .filter(
                EntryCandidate.candidate_source.in_(("market_scan", "mixed")),
                EntryCandidate.stock_market == mkt,
                EntryCandidate.snapshot_date >= cutoff,
            )
            .order_by(
                case((EntryCandidate.status == "active", 0), else_=1),
                EntryCandidate.snapshot_date.desc(),
                EntryCandidate.score.desc(),
                EntryCandidate.updated_at.desc(),
            )
            .limit(max(1, int(limit)) * 8)
            .all()
        )
    finally:
        db.close()

    out: dict[str, dict] = {}
    for row in rows:
        symbol = (row.stock_symbol or "").strip()
        if not symbol:
            continue
        key = f"{mkt}:{symbol}"
        if key in out:
            continue

        meta = row.meta if isinstance(row.meta, dict) else {}
        quote = meta.get("quote") if isinstance(meta.get("quote"), dict) else {}
        quote_seed = {
            "current_price": _safe_float(quote.get("current_price")),
            "change_pct": _safe_float(quote.get("change_pct")),
            "turnover": _safe_float(quote.get("turnover")),
            "volume": _safe_float(quote.get("volume")),
        }
        if quote_seed["current_price"] is None:
            if row.entry_low is not None and row.entry_high is not None:
                quote_seed["current_price"] = (float(row.entry_low) + float(row.entry_high)) / 2
            elif row.entry_high is not None:
                quote_seed["current_price"] = float(row.entry_high)
            elif row.entry_low is not None:
                quote_seed["current_price"] = float(row.entry_low)

        out[key] = {
            "symbol": symbol,
            "market": mkt,
            "stock_name": (row.stock_name or symbol).strip(),
            "candidate_source": "market_scan",
            "source_agent": (row.source_agent or "market_scan"),
            "source_suggestion_id": row.source_suggestion_id,
            "source_trace_id": row.source_trace_id or "",
            "quote_seed": quote_seed,
            "action": (row.action or "").strip().lower(),
            "action_label": (row.action_label or "").strip(),
            "signal": (row.signal or "").strip(),
            "reason": (row.reason or "").strip(),
            "strategy_tags_seed": list(row.strategy_tags or []),
            "plan_seed": row.plan if isinstance(row.plan, dict) else {},
            "meta": {
                "source": "market_scan_history",
                "fallback_snapshot_date": row.snapshot_date,
                "fallback_candidate_id": row.id,
                "fallback_status": row.status or "",
            },
        }
        if len(out) >= int(limit):
            break

    return out


def _load_market_scan_snapshot_inputs(
    *, market: str, limit: int, max_days: int = 7
) -> dict[str, dict]:
    if limit <= 0:
        return {}

    mkt = (market or "CN").strip().upper()
    cutoff = (date.today() - timedelta(days=max(1, int(max_days)))).strftime("%Y-%m-%d")
    db = SessionLocal()
    try:
        rows = (
            db.query(MarketScanSnapshot)
            .filter(
                MarketScanSnapshot.stock_market == mkt,
                MarketScanSnapshot.snapshot_date >= cutoff,
            )
            .order_by(
                MarketScanSnapshot.snapshot_date.desc(),
                MarketScanSnapshot.score_seed.desc(),
                MarketScanSnapshot.updated_at.desc(),
            )
            .limit(max(1, int(limit)) * 8)
            .all()
        )
    finally:
        db.close()

    out: dict[str, dict] = {}
    for row in rows:
        symbol = (row.stock_symbol or "").strip()
        if not symbol:
            continue
        key = f"{mkt}:{symbol}"
        if key in out:
            continue
        quote_seed = row.quote if isinstance(row.quote, dict) else {}
        out[key] = {
            "symbol": symbol,
            "market": mkt,
            "stock_name": (row.stock_name or symbol).strip(),
            "candidate_source": "market_scan",
            "source_agent": "market_scan",
            "source_suggestion_id": None,
            "source_trace_id": "",
            "quote_seed": quote_seed,
            "meta": {
                "source": "market_scan_snapshot",
                "fallback_snapshot_date": row.snapshot_date,
                "fallback_score_seed": _safe_float(row.score_seed),
                "fallback_source": row.source or "market_scan",
            },
        }
        if len(out) >= int(limit):
            break

    return out


def _load_market_scan_seed_inputs(*, market: str, limit: int) -> dict[str, dict]:
    if limit <= 0:
        return {}
    mkt = (market or "CN").strip().upper()
    symbols = list(
        dict.fromkeys(
            [str(s).strip() for s in MARKET_SCAN_SEED_SYMBOLS.get(mkt, []) if str(s).strip()]
        )
    )
    if not symbols:
        return {}
    try:
        rows = md_stock_data(symbols, _to_market(mkt).value)
    except Exception as e:
        logger.warning(f"市场扫描种子池拉取失败({mkt}): {e}")
        rows = []
    out: dict[str, dict] = {}
    for row in rows or []:
        symbol = (getattr(row, "symbol", "") or "").strip()
        if not symbol:
            continue
        key = f"{mkt}:{symbol}"
        quote = {
            "current_price": _safe_float(getattr(row, "current_price", None)),
            "change_pct": _safe_float(getattr(row, "change_pct", None)),
            "turnover": _safe_float(getattr(row, "turnover", None)),
            "volume": _safe_float(getattr(row, "volume", None)),
        }
        if quote["current_price"] is None:
            continue
        out[key] = {
            "symbol": symbol,
            "market": mkt,
            "stock_name": (getattr(row, "name", "") or symbol).strip(),
            "candidate_source": "market_scan",
            "source_agent": "market_scan",
            "source_suggestion_id": None,
            "source_trace_id": "",
            "quote_seed": quote,
            "meta": {
                "source": "market_scan_seed_universe",
                "collected_at": to_iso_with_tz(utc_now()),
            },
        }
        if len(out) >= int(limit):
            break
    return out


def _merge_market_scan_seed(
    base: dict[str, dict],
    incoming: dict[str, dict],
    *,
    market: str,
    limit_per_market: int,
) -> int:
    if not incoming:
        return 0
    mkt = (market or "CN").strip().upper()
    added = 0
    for key, item in incoming.items():
        if not key.startswith(f"{mkt}:"):
            continue
        if key in base:
            exist_quote = (
                base[key].get("quote_seed")
                if isinstance(base[key].get("quote_seed"), dict)
                else {}
            )
            item_quote = (
                item.get("quote_seed")
                if isinstance(item.get("quote_seed"), dict)
                else {}
            )
            if (_safe_float(exist_quote.get("turnover")) or 0.0) < (
                _safe_float(item_quote.get("turnover")) or 0.0
            ):
                base[key] = item
            continue
        base[key] = item
        added += 1
    keys = [k for k in base.keys() if k.startswith(f"{mkt}:")]
    if len(keys) > int(limit_per_market):
        keys_sorted = sorted(keys, key=lambda k: _candidate_sort_key(base.get(k) or {}))
        keep = set(keys_sorted[: int(limit_per_market)])
        for k in keys:
            if k not in keep:
                base.pop(k, None)
    return added


def _load_market_scan_inputs(limit_per_market: int = 60) -> dict[str, dict]:
    collector = EastMoneyDiscoveryCollector(
        proxy=_resolve_market_scan_proxy(),
    )
    result: dict[str, dict] = {}
    safe_limit = max(20, int(limit_per_market))
    min_required = min(max(12, int(safe_limit * 0.55)), safe_limit)

    for market in MARKET_SCAN_TARGET_MARKETS:
        try:
            turnover = _run_async(
                collector.fetch_hot_stocks(
                    market=market,
                    mode="turnover",
                    limit=safe_limit,
                )
            )
        except Exception as e:
            logger.warning(f"市场扫描成交榜失败({market}): {e}")
            turnover = []
        try:
            gainers = _run_async(
                collector.fetch_hot_stocks(
                    market=market,
                    mode="gainers",
                    limit=safe_limit,
                )
            )
        except Exception as e:
            logger.warning(f"市场扫描涨幅榜失败({market}): {e}")
            gainers = []

        merged = list(turnover or []) + list(gainers or [])
        for row in merged:
            symbol = (getattr(row, "symbol", "") or "").strip()
            if not symbol:
                continue
            key = f"{market}:{symbol}"
            quote = {
                "current_price": _safe_float(getattr(row, "price", None)),
                "change_pct": _safe_float(getattr(row, "change_pct", None)),
                "turnover": _safe_float(getattr(row, "turnover", None)),
                "volume": _safe_float(getattr(row, "volume", None)),
            }
            if key in result:
                exist_quote = result[key].get("quote_seed") or {}
                if _safe_float(exist_quote.get("turnover")) is None and quote.get("turnover") is not None:
                    result[key]["quote_seed"] = quote
                continue
            result[key] = {
                "symbol": symbol,
                "market": market,
                "stock_name": (getattr(row, "name", "") or symbol).strip(),
                "candidate_source": "market_scan",
                "source_agent": "market_scan",
                "source_suggestion_id": None,
                "source_trace_id": "",
                "quote_seed": quote,
                "meta": {
                    "source": "market_scan",
                    "collected_at": to_iso_with_tz(utc_now()),
                },
            }
            if len([k for k in result if k.startswith(f"{market}:")]) >= safe_limit:
                break

        market_count = len([k for k in result if k.startswith(f"{market}:")])
        if market_count < min_required:
            fallback_map = _load_market_scan_history_inputs(
                market=market,
                limit=max(0, safe_limit - market_count),
                max_days=7,
            )
            added = _merge_market_scan_seed(
                result,
                fallback_map,
                market=market,
                limit_per_market=safe_limit,
            )
            if added > 0:
                logger.info(
                    "市场扫描回退补全: market=%s added=%s current=%s",
                    market,
                    added,
                    len([k for k in result if k.startswith(f"{market}:")]),
                )

        market_count = len([k for k in result if k.startswith(f"{market}:")])
        if market_count < min_required:
            snapshot_map = _load_market_scan_snapshot_inputs(
                market=market,
                limit=max(0, safe_limit - market_count),
                max_days=14,
            )
            snap_added = _merge_market_scan_seed(
                result,
                snapshot_map,
                market=market,
                limit_per_market=safe_limit,
            )
            if snap_added > 0:
                logger.info(
                    "市场扫描快照补全: market=%s added=%s current=%s",
                    market,
                    snap_added,
                    len([k for k in result if k.startswith(f'{market}:')]),
                )

        market_count = len([k for k in result if k.startswith(f"{market}:")])
        if market_count < min_required:
            seed_map = _load_market_scan_seed_inputs(
                market=market,
                limit=max(0, safe_limit - market_count),
            )
            seed_added = _merge_market_scan_seed(
                result,
                seed_map,
                market=market,
                limit_per_market=safe_limit,
            )
            if seed_added > 0:
                logger.info(
                    "市场扫描种子补全: market=%s added=%s current=%s",
                    market,
                    seed_added,
                    len([k for k in result if k.startswith(f"{market}:")]),
                )

    # Final per-market cap and stable ordering.
    for market in MARKET_SCAN_TARGET_MARKETS:
        keys = [k for k in result.keys() if k.startswith(f"{market}:")]
        if len(keys) <= safe_limit:
            continue
        keys_sorted = sorted(keys, key=lambda k: _candidate_sort_key(result.get(k) or {}))
        keep = set(keys_sorted[:safe_limit])
        for key in keys:
            if key not in keep:
                result.pop(key, None)

    return result


# ─── 多源入池适配器(2026-08-21 机会页整合 P1) ─────────────────────────────
# 把 策略信号/竞价异动/问小达/问财 4 个来源也作为候选池种子, 与
# watchlist建议/market_scan 同池竞争 + 共振计分。设计原则:
# - 每个适配器只产出"种子 dict"(与 _load_market_scan_inputs 相同结构),
#   失败静默返回空(单源故障不影响其他源)。
# - 种子 meta 里带 source_hits 列表, refresh 阶段按 key 合并去重后计共振分。

def _load_strategy_signal_seeds(snapshot_date: str, limit: int = 60) -> dict[str, dict]:
    """策略库批量扫描信号 → 候选种子(candidate_source=strategy)。"""
    from src.web.models import StrategySignalRun

    seeds: dict[str, dict] = {}
    db = SessionLocal()
    try:
        rows = (
            db.query(StrategySignalRun)
            .filter(
                StrategySignalRun.snapshot_date == snapshot_date,
                StrategySignalRun.status == "active",
                StrategySignalRun.stock_market == "CN",
            )
            .order_by(StrategySignalRun.rank_score.desc())
            .limit(max(10, int(limit)))
            .all()
        )
        for row in rows:
            symbol = (row.stock_symbol or "").strip()
            if not symbol:
                continue
            key = f"CN:{symbol}"
            score = _safe_float(row.rank_score) or _safe_float(row.score) or 0.0
            seed = {
                "symbol": symbol,
                "market": "CN",
                "stock_name": (row.stock_name or symbol).strip(),
                "candidate_source": "strategy",
                "source_agent": row.strategy_code or "strategy",
                "source_suggestion_id": None,
                "source_trace_id": "",
                "quote_seed": None,
                "action": (row.action or "watch").strip().lower(),
                "action_label": (row.action_label or "观望").strip(),
                "signal": (row.signal or "").strip(),
                "reason": (row.signal or "").strip() or f"{row.strategy_name or '策略'}信号(score={score:.0f})",
                "meta": {
                    "source": "strategy",
                    "strategy_name": row.strategy_name or "",
                    "strategy_score": score,
                    "collected_at": to_iso_with_tz(utc_now()),
                },
                "strategy_tags_seed": [f"strategy:{row.strategy_code or 'unknown'}"],
            }
            if key in seeds:
                # 同股多策略命中 → 记多标签, 共振阶段合并
                prev = seeds[key]
                tags = set(prev.get("strategy_tags_seed") or [])
                tags.update(seed["strategy_tags_seed"])
                prev["strategy_tags_seed"] = sorted(tags)
                prev["meta"]["strategy_multi"] = True
                if score > (_safe_float(prev["meta"].get("strategy_score")) or 0):
                    prev["meta"]["strategy_score"] = score
                continue
            seeds[key] = seed
        return seeds
    except Exception as e:  # noqa: BLE001 — 单源失败不拖垮入池
        logger.warning(f"策略信号入池失败(跳过): {e}")
        return {}
    finally:
        db.close()


def _load_auction_anomaly_seeds(limit: int = 40) -> dict[str, dict]:
    """当日竞价异动池 → 候选种子(candidate_source=auction)。"""
    from datetime import date as _date, timedelta as _timedelta

    from src.web.models import AuctionAnomalyRecord

    seeds: dict[str, dict] = {}
    db = SessionLocal()
    try:
        today = _date.today().strftime("%Y-%m-%d")
        since = utc_now() - _timedelta(hours=20)
        rows = (
            db.query(AuctionAnomalyRecord)
            .filter(AuctionAnomalyRecord.created_at >= since)
            .order_by(AuctionAnomalyRecord.created_at.desc())
            .limit(max(10, int(limit)))
            .all()
        )
        for row in rows:
            symbol = (row.symbol or "").strip()
            if not symbol:
                continue
            key = f"CN:{symbol}"
            if key in seeds:
                continue
            gap = _safe_float(row.gap_pct)
            note = (row.note or "").strip()
            reason_bits = []
            if gap is not None:
                reason_bits.append(f"竞价{'高开' if gap >= 0 else '低开'}{abs(gap):.2f}%")
            vr = _safe_float(row.volume_ratio)
            if vr is not None:
                reason_bits.append(f"量比{vr:.2f}")
            wr = _safe_float(row.withdraw_rate)
            if wr is not None:
                reason_bits.append(f"撤单率{wr:.1f}%")
            if note:
                reason_bits.append(note)
            seeds[key] = {
                "symbol": symbol,
                "market": "CN",
                "stock_name": (row.name or symbol).strip(),
                "candidate_source": "auction",
                "source_agent": "auction_anomaly",
                "source_suggestion_id": None,
                "source_trace_id": "",
                "quote_seed": {
                    "change_pct": gap,
                },
                "action": "watch",
                "action_label": "关注",
                "signal": "竞价异动",
                "reason": "、".join(reason_bits) or "当日竞价异动",
                "meta": {
                    "source": "auction",
                    "gap_pct": gap,
                    "volume_ratio": vr,
                    "withdraw_rate": wr,
                    "snapshot_date": today,
                    "collected_at": to_iso_with_tz(utc_now()),
                },
                "strategy_tags_seed": ["auction_anomaly"],
            }
        return seeds
    except Exception as e:  # noqa: BLE001
        logger.warning(f"竞价异动入池失败(跳过): {e}")
        return {}
    finally:
        db.close()


def _load_manual_query_seeds(kind: str, snapshot_date: str, limit_per_kind: int = 30) -> dict[str, dict]:
    """用户主动查询结果入池(问小达 tdx / 问财 wencai)。

    数据落在 EntryCandidateFeedback? 不 —— 查询结果由调用方写入
    manual_query_candidates 表(见 discovery.py/wencai.py 的 record hook);
    本函数只读表。若表不存在/为空则静默返回空。
    """
    from src.web.models import ManualQueryCandidate

    source_label = {"tdx": "通达信问小达", "wencai": "问财选股"}.get(kind, kind)
    seeds: dict[str, dict] = {}
    db = SessionLocal()
    try:
        rows = (
            db.query(ManualQueryCandidate)
            .filter(
                ManualQueryCandidate.kind == kind,
                ManualQueryCandidate.snapshot_date == snapshot_date,
            )
            .order_by(ManualQueryCandidate.created_at.desc())
            .limit(max(5, int(limit_per_kind)))
            .all()
        )
        for row in rows:
            symbol = (row.stock_symbol or "").strip()
            if not symbol:
                continue
            key = f"{(row.stock_market or 'CN').upper()}:{symbol}"
            if key in seeds:
                continue
            query_text = (row.query_text or "").strip()
            seeds[key] = {
                "symbol": symbol,
                "market": (row.stock_market or "CN").upper(),
                "stock_name": (row.stock_name or symbol).strip(),
                "candidate_source": kind,
                "source_agent": f"{kind}_query",
                "source_suggestion_id": None,
                "source_trace_id": "",
                "quote_seed": None,
                "action": "watch",
                "action_label": "关注",
                "signal": source_label,
                "reason": f"{source_label}: {query_text[:60]}" if query_text else source_label,
                "meta": {
                    "source": kind,
                    "query_text": query_text[:120],
                    "collected_at": to_iso_with_tz(utc_now()),
                },
                "strategy_tags_seed": [f"{kind}_query"],
            }
        return seeds
    except Exception as e:  # noqa: BLE001 — 表不存在等情况静默
        logger.debug(f"{kind} 查询入池跳过: {e}")
        return {}
    finally:
        db.close()


def _merge_extra_source_seeds(input_map: dict[str, dict], extra_map: dict[str, dict]) -> int:
    """把额外来源种子合并进 input_map。

    - 新票 → 直接放入(candidate_source=该源)
    - 已存在的票(source_hits 记录追加), 不覆盖主 seed —— 共振在评分阶段处理
    返回合并的种子数。
    """
    added = 0
    for key, seed in extra_map.items():
        existing = input_map.get(key)
        if existing is None:
            input_map[key] = seed
            added += 1
            continue
        # 已有票: 追加 source_hit 标记(共振证据)
        meta = existing.get("meta") if isinstance(existing.get("meta"), dict) else {}
        hits = list(meta.get("source_hits") or [])
        hit_src = seed.get("candidate_source") or ""
        main_src = existing.get("candidate_source") or ""
        if hit_src and hit_src != main_src and hit_src not in hits:
            hits.append(hit_src)
            meta["source_hits"] = hits
            # 采纳更丰富的 reason/signal(若主源缺)
            if not existing.get("reason") and seed.get("reason"):
                existing["reason"] = seed["reason"]
            if not existing.get("stock_name"):
                existing["stock_name"] = seed.get("stock_name") or existing.get("stock_name")
            existing["meta"] = meta
    return added


def _apply_resonance_bonus(input_map: dict[str, dict], bonus_per_source: float = 8.0) -> None:
    """多源共振加分(P1 整合核心): 一只票被 N 个独立来源命中时, 在评分前给
    meta.resonance_count=N / resonance_bonus=(N-1)*bonus 写入, 由 _score_* 读取。

    N 的口径 = 主 candidate_source + meta.source_hits 里不同来源数。
    """
    for key, seed in input_map.items():
        meta = seed.get("meta") if isinstance(seed.get("meta"), dict) else {}
        hits = set(meta.get("source_hits") or [])
        hits.discard(seed.get("candidate_source"))
        n = 1 + len(hits)
        if n > 1:
            meta["resonance_count"] = n
            meta["resonance_bonus"] = round((n - 1) * bonus_per_source, 2)
            meta["resonance_sources"] = sorted({seed.get("candidate_source", ""), *hits})
            seed["meta"] = meta


def _load_multi_source_seeds(snapshot_date: str, *, enabled: bool = True) -> dict[str, dict]:
    """聚合全部额外来源种子(策略/竞价/tdx/wencai), 全部失败返回空。"""
    if not enabled:
        return {}
    merged: dict[str, dict] = {}
    total = 0
    total += _merge_extra_source_seeds(merged, _load_strategy_signal_seeds(snapshot_date))
    total += _merge_extra_source_seeds(merged, _load_auction_anomaly_seeds())
    total += _merge_extra_source_seeds(merged, _load_manual_query_seeds("tdx", snapshot_date))
    total += _merge_extra_source_seeds(merged, _load_manual_query_seeds("wencai", snapshot_date))
    if total:
        logger.info(f"多源入池: 额外种子 {total} 条(策略/竞价/tdx/wencai)")
    return merged


def _persist_market_scan_snapshot(snapshot: str, market_scan_map: dict[str, dict]) -> None:
    if not snapshot:
        return
    db = SessionLocal()
    try:
        db.query(MarketScanSnapshot).filter(
            MarketScanSnapshot.snapshot_date == snapshot
        ).delete(synchronize_session=False)
        rows = sorted(
            market_scan_map.values(),
            key=lambda x: (
                str(x.get("market") or ""),
                _candidate_sort_key(x),
            ),
        )
        for item in rows:
            symbol = str(item.get("symbol") or "").strip()
            market = str(item.get("market") or "CN").strip().upper() or "CN"
            if not symbol:
                continue
            quote = item.get("quote_seed") if isinstance(item.get("quote_seed"), dict) else {}
            meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
            db.add(
                MarketScanSnapshot(
                    snapshot_date=snapshot,
                    stock_symbol=symbol,
                    stock_market=market,
                    stock_name=str(item.get("stock_name") or symbol).strip(),
                    source=str(meta.get("source") or "market_scan"),
                    score_seed=float(_safe_float(item.get("score_seed")) or 0.0),
                    quote=to_jsonable(quote),
                    meta=to_jsonable(meta),
                )
            )
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"写入市场池快照失败: {e}")
    finally:
        db.close()


def _format_candidate_row(row: EntryCandidate) -> dict:
    def _fmt(dt):
        if not dt:
            return ""
        return to_iso_with_tz(dt)

    plan_data = row.plan if isinstance(row.plan, dict) else {}
    entry_low = row.entry_low if row.entry_low is not None else _safe_float(plan_data.get("entry_low"))
    entry_high = row.entry_high if row.entry_high is not None else _safe_float(plan_data.get("entry_high"))
    stop_loss = row.stop_loss if row.stop_loss is not None else _safe_float(plan_data.get("stop_loss"))
    target_price = row.target_price if row.target_price is not None else _safe_float(plan_data.get("target_price"))

    return {
        "id": row.id,
        "stock_symbol": row.stock_symbol,
        "stock_market": row.stock_market,
        "stock_name": row.stock_name,
        "snapshot_date": row.snapshot_date,
        "status": row.status,
        "score": round(float(row.score or 0), 2),
        "confidence": round(float(row.confidence or 0), 3) if row.confidence is not None else None,
        "action": row.action,
        "action_label": row.action_label,
        "candidate_source": row.candidate_source or "watchlist",
        "candidate_source_label": _candidate_source_label(row.candidate_source or "watchlist"),
        "strategy_tags": row.strategy_tags or [],
        "strategy_labels": _strategy_labels(row.strategy_tags or []),
        "is_holding_snapshot": bool(row.is_holding_snapshot),
        "plan_quality": int(row.plan_quality or 0),
        "signal": row.signal or "",
        "reason": row.reason or "",
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop_loss": stop_loss,
        "target_price": target_price,
        "invalidation": row.invalidation or "",
        "source_agent": row.source_agent or "",
        "source_agent_label": AGENT_LABELS.get(
            (row.source_agent or "").strip(), row.source_agent or ""
        ),
        "source_suggestion_id": row.source_suggestion_id,
        "source_trace_id": row.source_trace_id or "",
        "evidence": row.evidence or [],
        "plan": row.plan or {},
        "meta": row.meta or {},
        "created_at": _fmt(row.created_at),
        "updated_at": _fmt(row.updated_at),
    }


def _load_latest_suggestions(limit: int = 300) -> list[StockSuggestion]:
    db = SessionLocal()
    try:
        now = utc_now()
        subquery = (
            db.query(
                StockSuggestion.stock_symbol,
                StockSuggestion.stock_market,
                func.max(StockSuggestion.id).label("max_id"),
            )
            .group_by(StockSuggestion.stock_symbol, StockSuggestion.stock_market)
            .subquery()
        )
        rows = (
            db.query(StockSuggestion)
            .join(
                subquery,
                and_(
                    StockSuggestion.id == subquery.c.max_id,
                    StockSuggestion.stock_symbol == subquery.c.stock_symbol,
                    StockSuggestion.stock_market == subquery.c.stock_market,
                ),
            )
            .filter(
                or_(
                    StockSuggestion.expires_at.is_(None),
                    StockSuggestion.expires_at > now,
                )
            )
            .order_by(StockSuggestion.created_at.desc(), StockSuggestion.id.desc())
            .limit(max(1, limit))
            .all()
        )
        return rows
    finally:
        db.close()


# K线摘要进程内按日缓存(M4): 同一交易日内重复扫描直接命中, 不再联网。
# key=(market, symbol, date_str)。日期滚动自动失效, 无需 TTL 线程。
_KLINE_SUMMARY_CACHE: dict[tuple[str, str, str], dict] = {}
_KLINE_FETCH_WORKERS = 8
_KLINE_FETCH_TIMEOUT_S = 20.0


def _fetch_kline_summary_one(market: str, symbol: str) -> dict:
    """单只 K 线摘要(worker 内执行, 实例独享防共享)。"""
    try:
        return KlineCollector(_to_market(market)).get_kline_summary(symbol) or {}
    except Exception:
        return {}


def fetch_kline_summaries(
    keys: list[str],
    *,
    snapshot_date: str,
    max_workers: int = _KLINE_FETCH_WORKERS,
    timeout_s: float = _KLINE_FETCH_TIMEOUT_S,
) -> dict[str, dict]:
    """批量 K 线摘要: 按日缓存命中则跳过, 未命中并行抓取(失败回 {})。

    与旧串行逻辑同语义: 返回 {key: summary}, 异常/超时一律 {} 不抛。
    """
    out: dict[str, dict] = {}
    missing: list[str] = []
    for key in keys:
        try:
            market, symbol = key.split(":", 1)
        except ValueError:
            out[key] = {}
            continue
        hit = _KLINE_SUMMARY_CACHE.get((market, symbol, snapshot_date))
        if isinstance(hit, dict):
            out[key] = hit
        else:
            missing.append(key)
    if not missing:
        return out
    workers = max(1, min(int(max_workers or 1), len(missing)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="kline-sum") as pool:
        futs = {}
        for key in missing:
            market, symbol = key.split(":", 1)
            futs[pool.submit(_fetch_kline_summary_one, market, symbol)] = key
        for fut in as_completed(futs, timeout=(timeout_s * len(futs) if futs else None)):
            key = futs[fut]
            try:
                summary = fut.result(timeout=timeout_s)
                summary = summary if isinstance(summary, dict) else {}
            except Exception:
                summary = {}
            market, symbol = key.split(":", 1)
            _KLINE_SUMMARY_CACHE[(market, symbol, snapshot_date)] = summary
            out[key] = summary
    for key in missing:
        out.setdefault(key, {})
    return out


def refresh_entry_candidates(
    *,
    max_inputs: int = 300,
    snapshot_date: str | None = None,
    market_scan_limit: int = 60,
    max_kline_symbols: int = 72,
    skip_market_scan: bool = False,
) -> dict:
    snapshot = (snapshot_date or date.today().strftime("%Y-%m-%d")).strip()
    suggestions = _load_latest_suggestions(limit=max_inputs)
    # skip_market_scan(2026-08-22 共振查询联动): 交互查询落库后秒级重算共振,
    # 跳过东财榜单抓取(全量重算的重头); 市场池沿用 7 日内已持久化的快照, 不清空
    if skip_market_scan:
        market_scan_map = {}
        for market in MARKET_SCAN_TARGET_MARKETS:
            market_scan_map.update(
                _load_market_scan_snapshot_inputs(
                    market=market, limit=max(20, int(market_scan_limit))
                )
            )
    else:
        market_scan_map = _load_market_scan_inputs(limit_per_market=max(20, int(market_scan_limit)))
        _persist_market_scan_snapshot(snapshot, market_scan_map)
    holding_keys = _load_holding_keys()

    # 多源入池(2026-08-21 P1): 策略/竞价/问小达/问财 种子与主源同池,
    # 已有票追加 source_hits → 共振计分在评分阶段生效
    extra_seeds = _load_multi_source_seeds(snapshot)
    input_map: dict[str, dict] = dict(market_scan_map)
    if extra_seeds:
        _merge_extra_source_seeds(input_map, extra_seeds)
    for s in suggestions:
        market = _to_market(s.stock_market).value
        symbol = (s.stock_symbol or "").strip()
        if not symbol:
            continue
        key = f"{market}:{symbol}"
        seed_quote = _extract_price_from_meta(s.meta or {})
        seed = {
            "symbol": symbol,
            "market": market,
            "stock_name": (s.stock_name or symbol).strip(),
            "candidate_source": "mixed" if key in market_scan_map else "watchlist",
            "source_agent": s.agent_name or "",
            "source_suggestion_id": s.id,
            "source_trace_id": str((s.meta or {}).get("trace_id") or ""),
            "quote_seed": seed_quote,
            "action": (s.action or "watch").strip().lower(),
            "action_label": (s.action_label or "观望").strip(),
            "signal": (s.signal or "").strip(),
            "reason": (s.reason or "").strip(),
            "meta": to_jsonable(s.meta or {}),
            "suggestion_obj": s,
            "strategy_tags_seed": ["watchlist_agent"],
        }
        if key in input_map:
            base = input_map[key]
            if _safe_float((seed_quote or {}).get("current_price")) is None:
                seed["quote_seed"] = base.get("quote_seed") or seed_quote
            seed["candidate_source"] = "mixed"
            if not seed.get("plan_seed") and isinstance(base.get("plan_seed"), dict):
                seed["plan_seed"] = base.get("plan_seed")
            seed["meta"] = to_jsonable(
                {
                    **(
                        base.get("meta")
                        if isinstance(base.get("meta"), dict)
                        else {}
                    ),
                    **(seed.get("meta") or {}),
                    "source_mix": ["market_scan", "watchlist"],
                }
            )
        input_map[key] = seed

    # suggestions 合并后统一计共振(watchlist seed 与 extra seeds 命中同票时也算共振)
    _apply_resonance_bonus(input_map)

    # 只保留目标市场(CN)的输入: 港美股不再生成候选(discovery 手动查询不受影响)。
    # 用户自选 100% CN, watchlist 建议里即使出现 HK/US 也不进候选池。
    input_map = {
        k: v
        for k, v in input_map.items()
        if k.split(":", 1)[0].strip().upper() in MARKET_SCAN_TARGET_MARKETS
    }

    if not input_map:
        return {"snapshot_date": snapshot, "count": 0, "items": [], "filtered": 0}

    key_set = set(input_map.keys())
    by_market: dict[MarketCode, list[str]] = {}
    for key in key_set:
        market, symbol = key.split(":", 1)
        by_market.setdefault(_to_market(market), []).append(symbol)

    quotes: dict[str, dict] = {}
    for market, symbols in by_market.items():
        uniq = sorted(set([x for x in symbols if x]))
        if not uniq:
            continue
        try:
            rows = md_stock_data(uniq, market.value)
        except Exception as e:
            logger.warning(f"入场候选行情采集失败({market.value}): {e}")
            rows = []
        for q in rows or []:
            quotes[f"{market.value}:{q.symbol}"] = {
                "current_price": getattr(q, "current_price", None),
                "change_pct": getattr(q, "change_pct", None),
                "turnover": getattr(q, "turnover", None),
            }

    quote_fallbacks = _load_quote_fallbacks(key_set)
    for key, value in quote_fallbacks.items():
        current = quotes.get(key) or {}
        if _safe_float(current.get("current_price")) is None:
            quotes[key] = value

    for key, seed in input_map.items():
        current = quotes.get(key) or {}
        if _safe_float(current.get("current_price")) is None:
            seed_quote = seed.get("quote_seed") if isinstance(seed.get("quote_seed"), dict) else {}
            if _safe_float(seed_quote.get("current_price")) is not None:
                quotes[key] = dict(seed_quote)

    kline_summary_map: dict[str, dict] = _load_kline_fallbacks(
        key_set,
        lookback_days=7,
        max_rows=8000,
    )
    fetch_candidates = [k for k in key_set if k not in kline_summary_map]
    if fetch_candidates:
        safe_cap = max(0, int(max_kline_symbols))
        fetch_candidates.sort(
            key=lambda k: (
                0 if input_map.get(k, {}).get("source_suggestion_id") else 1,
                _candidate_sort_key(input_map.get(k) or {}),
                k,
            )
        )
        if safe_cap > 0:
            fetch_candidates = fetch_candidates[:safe_cap]
        # M4: 并行抓取 + 按日缓存(同旧串行语义, 失败回 {})
        for key, summary in fetch_kline_summaries(
            fetch_candidates, snapshot_date=snapshot
        ).items():
            kline_summary_map[key] = summary
    for key in key_set:
        if key not in kline_summary_map:
            kline_summary_map[key] = {}

    db = SessionLocal()
    items: list[dict] = []
    filtered_count = 0
    try:
        # 幂等 upsert(2026-08-23 P1 修复): 按 (market, symbol) 原行更新保持 ID 稳定,
        # EntryCandidateOutcome 外键不再因每日 3 次全量重建而级联删除;
        # 当轮消失的候选标 retired 保留历史(供 1/3/5/10 日后验评估), 不物理删除
        existing_rows = (
            db.query(EntryCandidate)
            .filter(EntryCandidate.snapshot_date == snapshot)
            .all()
        )
        existing_map = {
            ((r.stock_market or "CN").upper(), (r.stock_symbol or "").strip()): r
            for r in existing_rows
        }
        touched_keys: set[tuple[str, str]] = set()

        for key, inp in input_map.items():
            market, symbol = key.split(":", 1)
            quote = dict(quotes.get(key, {}) or {})
            if _safe_float(quote.get("current_price")) is None:
                quote.update(
                    {
                        k: v
                        for k, v in _extract_price_from_meta(inp.get("meta") or {}).items()
                        if v is not None and quote.get(k) is None
                    }
                )
            kline = kline_summary_map.get(key, {}) or {}
            candidate_source = (inp.get("candidate_source") or "watchlist").strip()
            is_holding = key in holding_keys

            suggestion_obj = inp.get("suggestion_obj")
            strategy_tags: list[str] = list(inp.get("strategy_tags_seed") or [])
            if suggestion_obj is not None:
                action = (inp.get("action") or "watch").strip().lower()
                action_label = (inp.get("action_label") or "观望").strip()
                signal = (inp.get("signal") or "").strip()
                reason = (inp.get("reason") or "").strip()
                score, evidence = _score_suggestion(
                    action=action,
                    suggestion=suggestion_obj,
                    quote=quote,
                    kline=kline,
                    resonance_meta=inp.get("meta") or {},
                )
                if (kline.get("trend") or "").strip() == "多头排列":
                    strategy_tags.append("trend_follow")
                if (kline.get("macd_cross") or "").strip() == "金叉":
                    strategy_tags.append("macd_golden")
                if (_safe_float(kline.get("volume_ratio")) or 0) >= 1.8:
                    strategy_tags.append("volume_breakout")
            else:
                seeded_action = (inp.get("action") or "").strip().lower()
                decision = _derive_market_scan_decision(quote=quote, kline=kline)
                if seeded_action in ACTION_BASE_SCORE:
                    action = seeded_action
                    action_label = (inp.get("action_label") or decision.get("action_label") or "观望").strip()
                    signal = (inp.get("signal") or decision.get("signal") or "").strip()
                    reason = (inp.get("reason") or decision.get("reason") or "").strip()
                    strategy_tags = list(
                        dict.fromkeys(
                            [
                                x
                                for x in (
                                    inp.get("strategy_tags_seed")
                                    or decision.get("strategy_tags")
                                    or []
                                )
                                if x
                            ]
                        )
                    )
                else:
                    action = decision["action"]
                    action_label = decision["action_label"]
                    signal = decision["signal"]
                    reason = decision["reason"]
                    strategy_tags = list(decision.get("strategy_tags") or [])
                score, evidence = _score_market_scan_candidate(
                    action=action,
                    quote=quote,
                    kline=kline,
                    strategy_tags=strategy_tags,
                    resonance_meta=inp.get("meta") or {},
                )

            if is_holding and action == "buy":
                action = "add"
                action_label = "准备加仓"
            if (not is_holding) and action == "add":
                action = "buy"
                action_label = "建仓"

            strategy_tags = list(dict.fromkeys([x for x in strategy_tags if x]))
            plan = _build_plan(
                action=action,
                quote=quote,
                kline=kline,
                suggestion_meta=(inp.get("meta") or {}),
            )
            seed_plan = inp.get("plan_seed") if isinstance(inp.get("plan_seed"), dict) else {}
            if seed_plan and _plan_quality(plan) < 90:
                merged = dict(seed_plan)
                for k, v in (plan or {}).items():
                    if v is None:
                        continue
                    if isinstance(v, str) and not v.strip():
                        continue
                    merged[k] = v
                plan = merged
            quality = _plan_quality(plan)
            confidence = round(score / 100.0, 3)

            status = "inactive"
            threshold = 62 if candidate_source in ("market_scan", "mixed") else 55
            if action in ("buy", "add") and quality >= 90 and score >= threshold:
                status = "active"

            # 精选池: 只落库 active 且有明确信号的记录。无明确信号(空/"暂无明确信号")
            # 或未达 active 门槛的观望占位一律不落库, 避免稀释真信号。
            # (entry_candidate_outcomes 只评估 active 候选, 关联不受影响)
            if status != "active" or not _has_real_signal(signal):
                filtered_count += 1
                continue

            row_key = (market, symbol)
            row = existing_map.get(row_key)
            if row is None:
                row = EntryCandidate(
                    stock_symbol=symbol,
                    stock_market=market,
                    snapshot_date=snapshot,
                )
                db.add(row)
                existing_map[row_key] = row
            row.stock_name = (inp.get("stock_name") or symbol).strip()
            row.status = status
            row.score = score
            row.confidence = confidence
            row.action = action
            row.action_label = action_label
            row.signal = signal
            row.reason = reason
            row.candidate_source = candidate_source
            row.strategy_tags = to_jsonable(strategy_tags)
            row.is_holding_snapshot = bool(is_holding)
            row.plan_quality = quality
            row.entry_low = _safe_float(plan.get("entry_low"))
            row.entry_high = _safe_float(plan.get("entry_high"))
            row.stop_loss = _safe_float(plan.get("stop_loss"))
            row.target_price = _safe_float(plan.get("target_price"))
            row.invalidation = str(plan.get("invalidation") or "")
            row.source_agent = (inp.get("source_agent") or "")
            row.source_suggestion_id = inp.get("source_suggestion_id")
            row.source_trace_id = str(inp.get("source_trace_id") or "")
            row.evidence = to_jsonable(evidence)
            row.plan = to_jsonable(plan)
            row.meta = to_jsonable(
                {
                    "candidate_source": candidate_source,
                    "quote": quote,
                    "kline": {
                        "trend": kline.get("trend"),
                        "macd_cross": kline.get("macd_cross"),
                        "rsi_status": kline.get("rsi_status"),
                        "kdj_status": kline.get("kdj_status"),
                        "volume_ratio": kline.get("volume_ratio"),
                        "support": kline.get("support"),
                        "resistance": kline.get("resistance"),
                    },
                    "strategy_tags": strategy_tags,
                    "is_holding_snapshot": bool(is_holding),
                    "source_meta": inp.get("meta") or {},
                }
            )
            row.updated_at = utc_now()
            touched_keys.add(row_key)
            items.append(_format_candidate_row(row))

        retired_rows = [
            r
            for k, r in existing_map.items()
            if k not in touched_keys and (r.status or "") != "retired"
        ]
        for r in retired_rows:
            r.status = "retired"
            r.updated_at = utc_now()
        if retired_rows:
            logger.info(
                "[候选池] %s 本轮退役 %d 只(标 retired 保留历史, 供后验评估)",
                snapshot,
                len(retired_rows),
            )

        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"刷新入场候选失败: {e}")
        raise
    finally:
        db.close()

    items.sort(
        key=lambda x: (
            0
            if x.get("candidate_source") == "market_scan"
            else 1
            if x.get("candidate_source") == "mixed"
            else 2,
            -(x.get("score") or 0),
            x.get("stock_market") or "",
            x.get("stock_symbol") or "",
        )
    )
    return {
        "snapshot_date": snapshot,
        "count": len(items),
        "items": items,
        "filtered": filtered_count,
    }


def list_entry_candidates(
    *,
    market: str = "",
    status: str = "active",
    min_score: float = 0,
    limit: int = 20,
    snapshot_date: str = "",
    source: str = "",
    holding: str = "",
    strategy: str = "",
    trend: str = "",
    macd: str = "",
    rsi: str = "",
    kdj: str = "",
    volume_ratio_min: float = 0,
    change_pct_min: float | None = None,
    change_pct_max: float | None = None,
) -> dict:
    def _query_rows():
        db = SessionLocal()
        try:
            snapshot = (snapshot_date or "").strip()
            if not snapshot:
                latest = (
                    db.query(EntryCandidate.snapshot_date)
                    .order_by(EntryCandidate.snapshot_date.desc())
                    .first()
                )
                snapshot = latest[0] if latest else ""
            if not snapshot:
                return "", []

            q = db.query(EntryCandidate).filter(EntryCandidate.snapshot_date == snapshot)
            mkt = (market or "").strip().upper()
            if mkt:
                q = q.filter(EntryCandidate.stock_market == mkt)
            st = (status or "").strip().lower()
            if st and st != "all":
                q = q.filter(EntryCandidate.status == st)
            src = (source or "").strip().lower()
            if src and src != "all":
                if src == "market_scan":
                    q = q.filter(EntryCandidate.candidate_source.in_(("market_scan", "mixed")))
                elif src == "watchlist":
                    q = q.filter(EntryCandidate.candidate_source == "watchlist")
                else:
                    q = q.filter(EntryCandidate.candidate_source == src)
            h = (holding or "").strip().lower()
            if h == "held":
                q = q.filter(EntryCandidate.is_holding_snapshot.is_(True))
            elif h == "unheld":
                q = q.filter(EntryCandidate.is_holding_snapshot.is_(False))
            q = q.filter(EntryCandidate.score >= float(min_score or 0))

            # ---- 技术形态/行情过滤(meta JSON 内嵌字段) ----
            # meta 存于 EntryCandidate.meta(JSON 列): kline.trend/macd_cross/rsi_status/kdj_status/volume_ratio, quote.change_pct
            t = (trend or "").strip()
            if t and t != "all":
                q = q.filter(EntryCandidate.meta["kline"]["trend"].as_string() == t)
            m = (macd or "").strip()
            if m and m != "all":
                q = q.filter(EntryCandidate.meta["kline"]["macd_cross"].as_string() == m)
            r = (rsi or "").strip()
            if r and r != "all":
                q = q.filter(EntryCandidate.meta["kline"]["rsi_status"].as_string() == r)
            k = (kdj or "").strip()
            if k and k != "all":
                q = q.filter(EntryCandidate.meta["kline"]["kdj_status"].as_string() == k)
            if float(volume_ratio_min or 0) > 0:
                q = q.filter(func.json_extract(EntryCandidate.meta, "$.kline.volume_ratio") >= float(volume_ratio_min))
            if change_pct_min is not None:
                q = q.filter(func.json_extract(EntryCandidate.meta, "$.quote.change_pct") >= float(change_pct_min))
            if change_pct_max is not None:
                q = q.filter(func.json_extract(EntryCandidate.meta, "$.quote.change_pct") <= float(change_pct_max))

            rows = (
                q.order_by(
                    case(
                        (EntryCandidate.candidate_source == "market_scan", 0),
                        (EntryCandidate.candidate_source == "mixed", 1),
                        else_=2,
                    ),
                    EntryCandidate.score.desc(),
                    EntryCandidate.updated_at.desc(),
                )
                .limit(max(20, int(limit) * 4))
                .all()
            )
            return snapshot, rows
        finally:
            db.close()

    snapshot, rows = _query_rows()

    tag = (strategy or "").strip()
    if tag:
        rows = [r for r in rows if tag in (r.strategy_tags or [])]

    rows = rows[: max(1, int(limit))]
    items = [_format_candidate_row(r) for r in rows]
    return {"snapshot_date": snapshot, "count": len(items), "items": items}


def record_manual_query_candidates(
    *,
    kind: str,
    query_text: str,
    snapshot_date: str | None = None,
    items: list[dict] | None = None,
) -> int:
    """用户主动查询结果入池记录(P1 整合): 问小达/问财返回的标的落库。

    Args:
        kind: "tdx" | "wencai"
        query_text: 用户原始查询语句
        items: [{"symbol": "002361", "market": "CN", "name": "神剑股份"}, ...]

    供 discovery.py(tdx)/wencai.py 在返回结果时调用; 失败静默(不影响查询本身)。
    返回落库条数。
    """
    if kind not in ("tdx", "wencai") or not items:
        return 0
    from src.web.models import ManualQueryCandidate

    day = (snapshot_date or date.today().strftime("%Y-%m-%d")).strip()
    db = SessionLocal()
    saved = 0
    try:
        for i, it in enumerate(items[:50]):
            symbol = str(it.get("symbol") or "").strip()
            if not symbol:
                continue
            market = (str(it.get("market") or "CN").strip().upper() or "CN")[:8]
            db.add(
                ManualQueryCandidate(
                    snapshot_date=day,
                    kind=kind,
                    query_text=(query_text or "")[:200],
                    stock_symbol=symbol,
                    stock_market=market,
                    stock_name=(str(it.get("name") or "").strip())[:64],
                    rank_in_result=i + 1,
                )
            )
            saved += 1
        if saved:
            db.commit()
            logger.info(f"[{kind}] 查询结果入池 {saved} 条(query={query_text[:40]})")
        return saved
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.warning(f"{kind} 查询结果入池失败(静默): {e}")
        return 0
    finally:
        db.close()


def save_entry_candidate_feedback(
    *,
    snapshot_date: str,
    stock_symbol: str,
    stock_market: str,
    useful: bool,
    candidate_source: str = "watchlist",
    strategy_tags: list[str] | None = None,
    reason: str = "",
) -> bool:
    symbol = (stock_symbol or "").strip().upper()
    market = (stock_market or "CN").strip().upper() or "CN"
    if not symbol:
        return False
    snap = (snapshot_date or "").strip() or date.today().strftime("%Y-%m-%d")

    db = SessionLocal()
    try:
        row = EntryCandidateFeedback(
            snapshot_date=snap,
            stock_symbol=symbol,
            stock_market=market,
            candidate_source=(candidate_source or "watchlist").strip(),
            strategy_tags=to_jsonable(strategy_tags or []),
            useful=bool(useful),
            reason=(reason or "").strip()[:200],
        )
        db.add(row)
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        logger.warning(f"保存候选反馈失败: {e}")
        return False
    finally:
        db.close()


def list_entry_candidate_feedback(
    *,
    snapshot_date: str = "",
    market: str = "",
    limit: int = 200,
) -> dict:
    """查询候选反馈（每个快照+标的最多返回最新一条，供前端回显已反馈状态）。"""
    limit = max(1, min(int(limit or 200), 1000))
    db = SessionLocal()
    try:
        q = db.query(EntryCandidateFeedback)
        snap = (snapshot_date or "").strip()
        if snap:
            q = q.filter(EntryCandidateFeedback.snapshot_date == snap)
        mkt = (market or "").strip().upper()
        if mkt:
            q = q.filter(EntryCandidateFeedback.stock_market == mkt)
        rows = (
            q.order_by(
                EntryCandidateFeedback.created_at.desc(),
                EntryCandidateFeedback.id.desc(),
            )
            .limit(min(limit * 8, 2000))
            .all()
        )
        seen: set[tuple] = set()
        items = []
        for r in rows:
            key = (r.snapshot_date or "", r.stock_market or "", r.stock_symbol or "")
            if key in seen:
                continue
            seen.add(key)
            created_at = r.created_at
            items.append(
                {
                    "snapshot_date": r.snapshot_date or "",
                    "stock_symbol": r.stock_symbol,
                    "stock_market": r.stock_market or "CN",
                    "candidate_source": r.candidate_source or "",
                    "strategy_tags": r.strategy_tags or [],
                    "useful": bool(r.useful),
                    "reason": r.reason or "",
                    "created_at": created_at.isoformat() if created_at is not None else "",
                }
            )
            if len(items) >= limit:
                break
        return {"count": len(items), "items": items}
    finally:
        db.close()


def evaluate_entry_candidate_outcomes(
    *,
    horizons: tuple[int, ...] = (1, 3, 5, 10),
    snapshot_days: int = 45,
    limit: int = 400,
) -> dict:
    """评估 active 入场候选的到期后验。

    修复(2026-08): 原实现按 snapshot_date 降序(最新优先) + limit 截断扫描集,
    而候选只有"变老"才到期 —— 候选池按日增长且无清理, 池子超过 limit 后,
    最老的(最该验证的)候选被永久挤出扫描窗口, 其 5/10 日 horizon 永远不会被验证
    (实测 2026-08-13: 552 个 active 中 52 个最老候选已被挤出, 3 天后将累积
    699 个到期未验证缺口)。本实现改为:
      1. 扫描集不截断(候选池全部 active, 最老优先), limit 语义改为
         "单轮最多处理的候选数"——本轮没处理完的候选仍是到期未验证, 下轮继续,
         到期后必然被验证;
      2. 只处理"至少有一个到期且未验证 horizon"的候选(今日新增不可能到期, 不拉K线);
      3. 已有 no_base_price outcome 的 (候选,horizon) 原地更新重试, 不再永久跳过;
      4. K线拉取失败计数并告警, 不再静默吞掉(失败留待下轮重试)。
    """
    stats = {
        "total_candidates": 0,
        "eligible": 0,
        "evaluated": 0,
        "skipped_not_due": 0,
        "skipped_no_price": 0,
        "skipped_no_base_price": 0,
        "kline_failures": 0,
        "missing_due_pairs_after": 0,
    }
    safe_horizons = sorted({max(1, int(h)) for h in horizons if int(h) > 0})
    if not safe_horizons:
        safe_horizons = [1, 3, 5]

    db = SessionLocal()
    try:
        today = date.today()
        cutoff = today - timedelta(days=max(7, int(snapshot_days)))
        candidates = (
            db.query(EntryCandidate)
            .filter(
                # retired = 当日早间曾入池、后续刷新退出的候选 — 曾对用户可见,
                # 必须纳入后验(retired 不评估会重造幸存者偏差)
                EntryCandidate.status.in_(("active", "retired")),
                EntryCandidate.snapshot_date >= cutoff.strftime("%Y-%m-%d"),
            )
            .order_by(EntryCandidate.snapshot_date.asc(), EntryCandidate.score.desc())
            .all()
        )
        stats["total_candidates"] = len(candidates)
        if not candidates:
            return stats

        existing_rows = (
            db.query(
                EntryCandidateOutcome.candidate_id,
                EntryCandidateOutcome.horizon_days,
                EntryCandidateOutcome.outcome_status,
            )
            .filter(EntryCandidateOutcome.candidate_id.in_([c.id for c in candidates]))
            .all()
        )
        existing = set()
        has_failed = False
        for cid, h, st in existing_rows:
            if st != "no_base_price":
                existing.add((int(cid), int(h)))
            else:
                has_failed = True
        # no_base_price 是"尝试过但失败", 允许下轮原地重试 → 需完整 ORM 对象做更新
        failed_rows: dict[tuple[int, int], EntryCandidateOutcome] = {}
        if has_failed:
            failed_objs = (
                db.query(EntryCandidateOutcome)
                .filter(
                    EntryCandidateOutcome.candidate_id.in_([c.id for c in candidates]),
                    EntryCandidateOutcome.outcome_status == "no_base_price",
                )
                .all()
            )
            failed_rows = {(int(o.candidate_id), int(o.horizon_days)): o for o in failed_objs}

        kline_cache: dict[tuple[str, str], list] = {}
        pending = 0  # 分批提交计数,缩短写事务窗口
        processed = 0  # 本轮已处理候选数(limit = 处理上限, 非扫描上限)
        process_cap = max(1, int(limit))

        for c in candidates:
            snap_day = _parse_day(c.snapshot_date)
            if snap_day is None:
                continue

            # 先算"到期且未验证"的 horizon; 全部已验证/未到期的候选不拉 K 线
            missing_due: list[int] = []
            not_due: list[int] = []
            for h in safe_horizons:
                if (c.id, h) in existing:
                    continue
                if snap_day + timedelta(days=h) <= today:
                    missing_due.append(h)
                else:
                    not_due.append(h)
            stats["skipped_not_due"] += len(not_due)
            if not missing_due:
                continue
            if processed >= process_cap:
                break  # 超单轮处理上限: 剩余候选仍是到期未验证, 下轮继续
            processed += 1

            key = ((c.stock_symbol or "").strip(), (c.stock_market or "CN").strip().upper())
            if key not in kline_cache:
                try:
                    lookback = max(120, (today - snap_day).days + 30)
                    kline_cache[key] = KlineCollector(_to_market(key[1])).get_klines(
                        key[0], days=min(lookback, 600)
                    )
                except Exception as e:
                    kline_cache[key] = []
                    stats["kline_failures"] += 1
                    logger.warning(
                        "[候选后验] K线拉取失败: %s %s (error=%s), 到期验证留待下轮重试",
                        key[1],
                        key[0],
                        e,
                    )
            klines = kline_cache[key]

            for horizon in missing_due:
                stats["eligible"] += 1
                target_day = snap_day + timedelta(days=horizon)
                outcome_price = _pick_close_on_or_before(klines, target_day)
                if outcome_price is None:
                    stats["skipped_no_price"] += 1
                    continue

                base_price = None
                if c.entry_low is not None and c.entry_high is not None:
                    base_price = (float(c.entry_low) + float(c.entry_high)) / 2
                elif c.entry_high is not None:
                    base_price = float(c.entry_high)
                elif c.entry_low is not None:
                    base_price = float(c.entry_low)
                if base_price is None:
                    meta = c.meta if isinstance(c.meta, dict) else {}
                    quote = meta.get("quote") if isinstance(meta.get("quote"), dict) else {}
                    base_price = _safe_float(quote.get("current_price"))
                if base_price is None:
                    base_price = _pick_close_on_or_before(klines, snap_day)
                if base_price is None or base_price <= 0:
                    stats["skipped_no_base_price"] += 1
                    status = "no_base_price"
                    ret = None
                else:
                    ret = (outcome_price - base_price) / base_price * 100.0
                    if c.target_price is not None and outcome_price >= float(c.target_price):
                        status = "hit_target"
                    elif c.stop_loss is not None and outcome_price <= float(c.stop_loss):
                        status = "hit_stop"
                    else:
                        status = "evaluated"

                hit_target = (
                    bool(c.target_price is not None and outcome_price >= float(c.target_price))
                    if status != "no_base_price"
                    else None
                )
                hit_stop = (
                    bool(c.stop_loss is not None and outcome_price <= float(c.stop_loss))
                    if status != "no_base_price"
                    else None
                )

                fields = dict(
                    candidate_id=c.id,
                    snapshot_date=c.snapshot_date,
                    stock_symbol=c.stock_symbol,
                    stock_market=c.stock_market,
                    candidate_source=c.candidate_source or "watchlist",
                    strategy_tags=to_jsonable(c.strategy_tags or []),
                    horizon_days=horizon,
                    target_date=target_day.strftime("%Y-%m-%d"),
                    base_price=base_price,
                    outcome_price=outcome_price,
                    outcome_return_pct=ret,
                    hit_target=hit_target,
                    hit_stop=hit_stop,
                    outcome_status=status,
                    meta=to_jsonable(
                        {
                            "candidate_score": float(c.score or 0),
                            "action": c.action or "",
                            "action_label": c.action_label or "",
                        }
                    ),
                    evaluated_at=utc_now(),
                )
                failed = failed_rows.get((c.id, horizon))
                if failed is not None:
                    # 原地更新重试: (candidate_id, horizon) 有唯一约束, 不能重复插入
                    for k, v in fields.items():
                        setattr(failed, k, v)
                else:
                    db.add(EntryCandidateOutcome(**fields))
                stats["evaluated"] += 1
                existing.add((c.id, horizon))
                pending += 1

            # 分批提交:累计到阈值即落盘,缩短写事务,避免与 60s 调度器并发写长时间持锁
            if pending >= 50:
                db.commit()
                pending = 0

        db.commit()
        # 本轮结束后仍缺的到期验证 → 供调度器告警(缺口可观测, 0 = 全部到期候选已后验)
        stats["missing_due_pairs_after"] = sum(
            _due_unverified_pairs(
                db, safe_horizons=safe_horizons, cutoff_date=cutoff, today=today
            ).values()
        )
        return stats
    except Exception as e:
        db.rollback()
        logger.warning(f"候选后验评估失败: {e}")
        return stats
    finally:
        db.close()


def _due_unverified_pairs(
    db, *, safe_horizons: list[int], cutoff_date: date, today: date
) -> dict[int, int]:
    """只读统计: 窗口内 active 候选"已到期但尚无成功 outcome"的 (候选,horizon) 缺口。"""
    cutoff_str = cutoff_date.strftime("%Y-%m-%d")
    cand_rows = (
        db.query(EntryCandidate.id, EntryCandidate.snapshot_date)
        .filter(
            EntryCandidate.status.in_(("active", "retired")),
            EntryCandidate.snapshot_date >= cutoff_str,
        )
        .all()
    )
    missing = {h: 0 for h in safe_horizons}
    if not cand_rows:
        return missing
    ids = [r.id for r in cand_rows]
    out_rows = (
        db.query(
            EntryCandidateOutcome.candidate_id,
            EntryCandidateOutcome.horizon_days,
            EntryCandidateOutcome.outcome_status,
        )
        .filter(EntryCandidateOutcome.candidate_id.in_(ids))
        .all()
    )
    verified = {
        (int(cid), int(h)) for cid, h, st in out_rows if st != "no_base_price"
    }
    for r in cand_rows:
        snap = _parse_day(r.snapshot_date)
        if snap is None:
            continue
        for h in safe_horizons:
            if snap + timedelta(days=h) <= today and (int(r.id), h) not in verified:
                missing[h] += 1
    return missing


def count_missing_candidate_outcomes(
    *,
    horizons: tuple[int, ...] = (1, 3, 5, 10),
    snapshot_days: int = 45,
    sample_limit: int = 20,
) -> dict:
    """只读报告: active 候选里"已到期但尚未验证"的缺口, 供调度器告警 / API 查询。

    这是验证覆盖率的反向指标: missing_pairs == 0 意味着所有到期 active 候选
    都已有后验结果, 剩余覆盖率缺口只剩"未到期"(纯时间因素)。
    """
    safe_horizons = sorted({max(1, int(h)) for h in horizons if int(h) > 0})
    if not safe_horizons:
        safe_horizons = [1, 3, 5]
    today = date.today()
    cutoff = today - timedelta(days=max(7, int(snapshot_days)))
    db = SessionLocal()
    try:
        missing = _due_unverified_pairs(
            db, safe_horizons=safe_horizons, cutoff_date=cutoff, today=today
        )
        total_active = (
            db.query(func.count(EntryCandidate.id))
            .filter(
                EntryCandidate.status.in_(("active", "retired")),
                EntryCandidate.snapshot_date >= cutoff.strftime("%Y-%m-%d"),
            )
            .scalar()
        ) or 0
        samples: list[dict] = []
        if sum(missing.values()) > 0:
            rows = (
                db.query(
                    EntryCandidate.id,
                    EntryCandidate.stock_symbol,
                    EntryCandidate.stock_name,
                    EntryCandidate.snapshot_date,
                    EntryCandidate.action_label,
                    EntryCandidate.score,
                )
                .filter(
                    EntryCandidate.status == "active",
                    EntryCandidate.snapshot_date >= cutoff.strftime("%Y-%m-%d"),
                )
                .order_by(EntryCandidate.snapshot_date.asc(), EntryCandidate.score.desc())
                .limit(max(1, int(sample_limit)))
                .all()
            )
            out_rows = (
                db.query(
                    EntryCandidateOutcome.candidate_id,
                    EntryCandidateOutcome.horizon_days,
                    EntryCandidateOutcome.outcome_status,
                )
                .filter(EntryCandidateOutcome.candidate_id.in_([r.id for r in rows]))
                .all()
            )
            verified = {
                (int(cid), int(h)) for cid, h, st in out_rows if st != "no_base_price"
            }
            for r in rows:
                snap = _parse_day(r.snapshot_date)
                if snap is None:
                    continue
                miss = [
                    h
                    for h in safe_horizons
                    if snap + timedelta(days=h) <= today
                    and (int(r.id), h) not in verified
                ]
                if miss:
                    samples.append(
                        {
                            "candidate_id": int(r.id),
                            "stock_symbol": r.stock_symbol,
                            "stock_name": r.stock_name,
                            "snapshot_date": r.snapshot_date,
                            "action_label": r.action_label,
                            "score": round(float(r.score or 0), 2),
                            "missing_horizons": miss,
                        }
                    )
        return {
            "as_of": today.strftime("%Y-%m-%d"),
            "window_days": max(7, int(snapshot_days)),
            "total_active_in_window": total_active,
            "per_horizon": {str(h): n for h, n in missing.items()},
            "missing_pairs": sum(missing.values()),
            "samples": samples[: max(1, int(sample_limit))],
        }
    finally:
        db.close()


def get_entry_candidate_stats(*, days: int = 30) -> dict:
    days = max(1, min(int(days or 30), 365))
    since = utc_now() - timedelta(days=days)
    db = SessionLocal()
    try:
        total, useful = (
            db.query(
                func.count(EntryCandidateFeedback.id),
                func.sum(case((EntryCandidateFeedback.useful.is_(True), 1), else_=0)),
            )
            .filter(EntryCandidateFeedback.created_at >= since)
            .first()
        )
        total = int(total or 0)
        useful = int(useful or 0)
        useless = max(0, total - useful)
        useful_rate = round((useful / total * 100.0), 2) if total > 0 else 0.0

        by_source_rows = (
            db.query(
                EntryCandidateFeedback.candidate_source,
                func.count(EntryCandidateFeedback.id).label("total"),
                func.sum(case((EntryCandidateFeedback.useful.is_(True), 1), else_=0)).label("useful"),
            )
            .filter(EntryCandidateFeedback.created_at >= since)
            .group_by(EntryCandidateFeedback.candidate_source)
            .all()
        )
        by_market_rows = (
            db.query(
                EntryCandidateFeedback.stock_market,
                func.count(EntryCandidateFeedback.id).label("total"),
                func.sum(case((EntryCandidateFeedback.useful.is_(True), 1), else_=0)).label("useful"),
            )
            .filter(EntryCandidateFeedback.created_at >= since)
            .group_by(EntryCandidateFeedback.stock_market)
            .all()
        )

        by_strategy_map: dict[str, dict] = {}
        strat_rows = (
            db.query(EntryCandidateFeedback.strategy_tags, EntryCandidateFeedback.useful)
            .filter(EntryCandidateFeedback.created_at >= since)
            .all()
        )
        for tags, is_useful in strat_rows:
            for t in (tags or []):
                item = by_strategy_map.setdefault(t, {"strategy": t, "strategy_label": STRATEGY_LABELS.get(t, t), "total": 0, "useful": 0})
                item["total"] += 1
                if is_useful:
                    item["useful"] += 1

        latest_snapshot_row = (
            db.query(EntryCandidate.snapshot_date)
            .order_by(EntryCandidate.snapshot_date.desc())
            .first()
        )
        latest_snapshot = latest_snapshot_row[0] if latest_snapshot_row else ""
        coverage = {
            "snapshot_date": latest_snapshot,
            "total_snapshot_candidates": 0,
            "total_active": 0,
            "market_scan_active": 0,
            "watchlist_active": 0,
            "held_active": 0,
            "unheld_active": 0,
            "new_active_from_prev": 0,
            "dropped_from_prev": 0,
            "previous_snapshot_date": "",
            "observing_candidates": 0,
        }
        if latest_snapshot:
            snapshot_rows = (
                db.query(EntryCandidate.snapshot_date)
                .distinct()
                .order_by(EntryCandidate.snapshot_date.desc())
                .limit(2)
                .all()
            )
            previous_snapshot = (
                str(snapshot_rows[1][0])
                if len(snapshot_rows) > 1 and snapshot_rows[1] and snapshot_rows[1][0]
                else ""
            )
            total_active = (
                db.query(func.count(EntryCandidate.id))
                .filter(
                    EntryCandidate.snapshot_date == latest_snapshot,
                    EntryCandidate.status == "active",
                )
                .scalar()
            ) or 0
            total_snapshot_candidates = (
                db.query(func.count(EntryCandidate.id))
                .filter(EntryCandidate.snapshot_date == latest_snapshot)
                .scalar()
            ) or 0
            market_active = (
                db.query(func.count(EntryCandidate.id))
                .filter(
                    EntryCandidate.snapshot_date == latest_snapshot,
                    EntryCandidate.status == "active",
                    EntryCandidate.candidate_source.in_(("market_scan", "mixed")),
                )
                .scalar()
            ) or 0
            held_active = (
                db.query(func.count(EntryCandidate.id))
                .filter(
                    EntryCandidate.snapshot_date == latest_snapshot,
                    EntryCandidate.status == "active",
                    EntryCandidate.is_holding_snapshot.is_(True),
                )
                .scalar()
            ) or 0
            unheld_active = (
                db.query(func.count(EntryCandidate.id))
                .filter(
                    EntryCandidate.snapshot_date == latest_snapshot,
                    EntryCandidate.status == "active",
                    EntryCandidate.is_holding_snapshot.is_(False),
                )
                .scalar()
            ) or 0
            new_active_from_prev = 0
            dropped_from_prev = 0
            if previous_snapshot:
                latest_keys = set(
                    db.query(EntryCandidate.stock_market, EntryCandidate.stock_symbol)
                    .filter(
                        EntryCandidate.snapshot_date == latest_snapshot,
                        EntryCandidate.status == "active",
                    )
                    .all()
                )
                prev_keys = set(
                    db.query(EntryCandidate.stock_market, EntryCandidate.stock_symbol)
                    .filter(
                        EntryCandidate.snapshot_date == previous_snapshot,
                        EntryCandidate.status == "active",
                    )
                    .all()
                )
                new_active_from_prev = len(latest_keys - prev_keys)
                dropped_from_prev = len(prev_keys - latest_keys)
            watch_active = max(0, int(total_active) - int(market_active))
            coverage = {
                "snapshot_date": latest_snapshot,
                "total_snapshot_candidates": int(total_snapshot_candidates),
                "total_active": int(total_active),
                "market_scan_active": int(market_active),
                "watchlist_active": int(watch_active),
                "held_active": int(held_active),
                "unheld_active": int(unheld_active),
                "new_active_from_prev": int(new_active_from_prev),
                "dropped_from_prev": int(dropped_from_prev),
                "previous_snapshot_date": previous_snapshot,
                "observing_candidates": max(
                    0, int(total_snapshot_candidates) - int(total_active)
                ),
                "market_scan_share_pct": round((market_active / total_active * 100.0), 2)
                if total_active
                else 0.0,
            }

        by_source = []
        for source_name, cnt, u in by_source_rows:
            cnt = int(cnt or 0)
            u = int(u or 0)
            by_source.append(
                {
                    "source": source_name or "watchlist",
                    "source_label": _candidate_source_label(source_name or "watchlist"),
                    "total": cnt,
                    "useful": u,
                    "useful_rate": round((u / cnt * 100.0), 2) if cnt > 0 else 0.0,
                }
            )
        by_market = []
        for m, cnt, u in by_market_rows:
            cnt = int(cnt or 0)
            u = int(u or 0)
            by_market.append(
                {
                    "market": (m or "CN").strip().upper(),
                    "total": cnt,
                    "useful": u,
                    "useful_rate": round((u / cnt * 100.0), 2) if cnt > 0 else 0.0,
                }
            )

        by_strategy = sorted(
            [
                {
                    **v,
                    "useless": max(0, v["total"] - v["useful"]),
                    "useful_rate": round((v["useful"] / v["total"] * 100.0), 2)
                    if v["total"] > 0
                    else 0.0,
                }
                for v in by_strategy_map.values()
            ],
            key=lambda x: (x["total"], x["useful"]),
            reverse=True,
        )

        outcome_rows = (
            db.query(
                EntryCandidateOutcome.horizon_days,
                EntryCandidateOutcome.candidate_source,
                func.count(EntryCandidateOutcome.id).label("total"),
                func.sum(case((EntryCandidateOutcome.outcome_return_pct > 0, 1), else_=0)).label("wins"),
                func.avg(EntryCandidateOutcome.outcome_return_pct).label("avg_ret"),
            )
            .filter(
                EntryCandidateOutcome.created_at >= since,
                EntryCandidateOutcome.outcome_status.in_(("evaluated", "hit_target", "hit_stop")),
            )
            .group_by(EntryCandidateOutcome.horizon_days, EntryCandidateOutcome.candidate_source)
            .all()
        )
        outcome_summary = []
        for h, src, total_eval, wins, avg_ret in outcome_rows:
            total_eval = int(total_eval or 0)
            wins = int(wins or 0)
            outcome_summary.append(
                {
                    "horizon_days": int(h or 0),
                    "source": src or "watchlist",
                    "source_label": _candidate_source_label(src or "watchlist"),
                    "total": total_eval,
                    "wins": wins,
                    "win_rate": round((wins / total_eval * 100.0), 2) if total_eval > 0 else 0.0,
                    "avg_return_pct": round(float(avg_ret or 0.0), 3),
                }
            )
        outcome_summary.sort(key=lambda x: (x["horizon_days"], x["total"]), reverse=True)

        return {
            "window_days": days,
            "feedback": {
                "total": total,
                "useful": useful,
                "useless": useless,
                "useful_rate": useful_rate,
            },
            "by_source": by_source,
            "by_market": by_market,
            "by_strategy": by_strategy[:20],
            "coverage": coverage,
            "outcomes": outcome_summary,
        }
    finally:
        db.close()

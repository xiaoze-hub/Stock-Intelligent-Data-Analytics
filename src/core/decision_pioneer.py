"""决策先锋三指标复刻 + 主力意图 L2 增强 (2026-08-30, 周一开盘前上线)。

三指标 = GS策略(趋势) × 主力净流入(L2·明盘口径) × AI机构活跃度(强度)。

- AI机构活跃度: 零调参纯K线波动探测器。源码 7 因子 MAX × 1.2, 阈值 1.56/3/6。
- GS策略: BB0 慢线(MA3/7/13/27 均值) + A0 快线((H+L+2O+6C)/10) 交叉 → G买/S卖。
- L2 主力净流入(明盘口径, 非暗盘): TQ get_more_info.Zjl_HB 成品字段 = 同花顺"主力净额"——按单笔成交金额阈值(特大/大/中/小)汇总的**明盘资金流向**,不包含拆单识别。要做真正的暗盘请用 dark_flow(腾讯逐笔 + .tck 委托号拆单识别)。

盘中实时: 日线走 TQ(get_market_data period=1d 含当日), L2 走 TQ get_more_info。
数据源缺失时显式返回 None / 标注"无数据", 禁止推测编造。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ── AI 机构活跃度阈值(同花顺官方, 零调参) ──────────────────────────────
LIFE_LINE = 1.56      # 生命线 = 1.3% × 1.2
STRONG_LINE = 3.0     # 强势线 = 2.5% × 1.2
BULL_LINE = 6.0       # 大牛线 = 5% × 1.2
ACTIVITY_MULT = 1.2   # 波动放大系数(源码写死)


def _bar_close_open_high_low(b: dict) -> tuple[float, float, float, float]:
    """统一取 bar 的 OHLC(兼容 Bar dataclass / dict / 对象属性)。"""
    def g(k: str) -> float:
        v = b.get(k) if isinstance(b, dict) else getattr(b, k, None)
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0
    return g("close"), g("open"), g("high"), g("low")


def compute_institution_activity(bars: list[dict]) -> dict | None:
    """AI 机构活跃度(零调参, 纯K线波动探测器)。

    bars: 按日期升序的日K(list[dict] 或 list[Bar]), 至少 2 根。
    返回: {activity, level, life_line, strong_line, bull_line, streak_days, ma5,
           factors, close, is_yang}
    - activity: 当日活跃度 = max(7因子) × 1.2
    - level: 大牛(≥6) / 强势(3~6) / 生命(1.56~3) / 弱(<1.56)
    - streak_days: 活跃度>生命线连续日数(含当日)
    - ma5: 活跃度 5 日均值
    数据不足(bar<2)返回 None。
    """
    if not bars or len(bars) < 2:
        return None
    # 逐日算活跃度(第 0 根无 ref 前置, 活跃度记 None)
    acts: list[float | None] = [None]
    prev_close = _bar_close_open_high_low(bars[0])[0]
    for i in range(1, len(bars)):
        c, o, h, l = _bar_close_open_high_low(bars[i])
        if prev_close <= 0 or o <= 0 or l <= 0:
            acts.append(None)
            prev_close = c
            continue
        upper_shadow = (h - max(c, o)) / max(c, o) * 100          # X_10 上影
        lower_shadow = (min(c, o) - l) / l * 100                  # X_5  下影
        body = (c - o) / o * 100                                  # X_8  实体
        rise = (c - prev_close) / prev_close * 100                # X_6  涨幅
        gap_open = (o - prev_close) / prev_close * 100            # X_7  高开
        factors = [
            upper_shadow,
            lower_shadow,
            body + upper_shadow,       # 实体+上影
            body + lower_shadow,       # 实体+下影
            upper_shadow + lower_shadow,  # 上影+下影
            rise,
            gap_open,
        ]
        acts.append(max(factors) * ACTIVITY_MULT)
        prev_close = c

    cur = acts[-1]
    if cur is None:
        return None

    # level
    if cur >= BULL_LINE:
        level = "大牛"
    elif cur >= STRONG_LINE:
        level = "强势"
    elif cur >= LIFE_LINE:
        level = "生命"
    else:
        level = "弱"

    # 连强天数(活跃度 > 生命线 连续日数, 含当日)
    streak = 0
    for a in reversed(acts):
        if a is not None and a > LIFE_LINE:
            streak += 1
        else:
            break

    # 5 日均值(含当日, 只统计非 None)
    valid = [a for a in acts if a is not None]
    ma5 = round(sum(valid[-5:]) / max(1, len(valid[-5:])), 3) if valid else None

    last_bar = bars[-1]
    c, o, h, l = _bar_close_open_high_low(last_bar)
    return {
        "activity": round(cur, 3),
        "level": level,
        "life_line": LIFE_LINE,
        "strong_line": STRONG_LINE,
        "bull_line": BULL_LINE,
        "streak_days": streak,
        "ma5": ma5,
        "is_yang": c >= o,
        "close": c,
        "factors": {
            "upper_shadow": round(max([(h - max(c, o)) / max(c, o) * 100, 0]), 3),
            "lower_shadow": round(max([(min(c, o) - l) / l * 100, 0]), 3),
            "body": round((c - o) / o * 100, 3) if o else None,
        },
    }


def _sma(vals: list[float], n: int) -> float | None:
    """简单均线; 长度不足返回 None。"""
    if len(vals) < n or n <= 0:
        return None
    return sum(vals[-n:]) / n


def compute_gs_signal(bars: list[dict]) -> dict | None:
    """GS 策略(仿编版 + 自建结构, 未校准近似)。

    BB0 = (MA3 + MA7 + MA13 + MA27) / 4  慢线(趋势)
    A0  = (H + L + 2O + 6C) / 10          快线(收盘 60% 权重)
    A0 上穿 BB0 → G买; A0 下穿 BB0 → S卖。

    返回: {signal, state, bb0, a0, last_cross}
    - signal: 最近一次交叉方向 "G"(买) / "S"(卖) / None(无交叉)
    - state: 当前 "G区"(a0>bb0) / "S区"(a0<bb0)
    数据不足(bar<27)返回 None。
    """
    if not bars or len(bars) < 5:
        return None
    closes: list[float] = []
    a0_series: list[float] = []
    bb0_series: list[float] = []
    for b in bars:
        c, o, h, l = _bar_close_open_high_low(b)
        closes.append(c)
        a0_series.append((h + l + 2 * o + 6 * c) / 10)
        bb0s = []
        for n in (3, 7, 13, 27):
            m = _sma(closes, n)
            if m is None:
                bb0s = None
                break
            bb0s.append(m)
        bb0_series.append(sum(bb0s) / 4 if bb0s else None)

    # 找最近一次交叉
    last_cross = None
    last_cross_idx = -1
    for i in range(1, len(a0_series)):
        if bb0_series[i] is None or bb0_series[i - 1] is None:
            continue
        prev_diff = a0_series[i - 1] - bb0_series[i - 1]
        cur_diff = a0_series[i] - bb0_series[i]
        if prev_diff <= 0 < cur_diff:
            last_cross = "G"
            last_cross_idx = i
        elif prev_diff >= 0 > cur_diff:
            last_cross = "S"
            last_cross_idx = i

    a0 = a0_series[-1]
    bb0 = bb0_series[-1]
    if bb0 is None:
        return None
    state = "G区" if a0 > bb0 else "S区"
    ma5 = _sma(closes, 5)
    ma20 = _sma(closes, 20)
    return {
        "signal": last_cross,
        "state": state,
        "bb0": round(bb0, 4),
        "a0": round(a0, 4),
        "last_cross_idx": last_cross_idx,
        "close": closes[-1],
        "ma5": round(ma5, 4) if ma5 is not None else None,
        "ma20": round(ma20, 4) if ma20 is not None else None,
    }


def _bars_to_dicts(bars) -> list[dict]:
    """Bar 对象列表 → 统一 dict 列表(按 date 升序)。"""
    out: list[dict] = []
    for b in bars:
        if isinstance(b, dict):
            out.append(b)
        else:
            out.append({
                "date": getattr(b, "date", None),
                "open": getattr(b, "open", None),
                "high": getattr(b, "high", None),
                "low": getattr(b, "low", None),
                "close": getattr(b, "close", None),
                "volume": getattr(b, "volume", None),
            })
    return out


def fetch_bars(symbol: str, market: str = "CN", days: int = 60) -> list[dict]:
    """盘中实时日K(走 marketdata Engine, TQ 优先, 含当日)。失败返回 []。"""
    from src.core.marketdata_client import get_market_data

    try:
        bars = get_market_data().klines(symbol, market=market, days=days)
        return _bars_to_dicts(bars)
    except Exception as e:  # noqa: BLE001
        logger.warning("decision_pioneer fetch_bars %s failed: %s", symbol, e)
        return []


def fetch_tq_l2(symbol: str, market: str = "CN") -> dict | None:
    """TQ get_more_info 的 L2 成品字段(盘中实时)。失败返回 None。

    返回: {zjl_hb(主力净流入,元), zjl, cancel_buy, cancel_sell,
           l2_tick_num, l2_order_num, total_buy_vol, total_sell_vol}
    """
    from src.core.marketdata_client import get_market_data

    try:
        items = get_market_data().more_info([symbol], market=market)
        if not items:
            return None
        m = items[0]
        return {
            "zjl_hb": getattr(m, "zjl_hb", None),
            "zjl": getattr(m, "zjl", None),
            "cancel_buy": getattr(m, "cancel_buy", None),
            "cancel_sell": getattr(m, "cancel_sell", None),
            "l2_tick_num": getattr(m, "l2_tick_num", None),
            "l2_order_num": getattr(m, "l2_order_num", None),
            "total_buy_vol": getattr(m, "total_buy_vol", None),
            "total_sell_vol": getattr(m, "total_sell_vol", None),
            "volume_ratio": getattr(m, "volume_ratio", None),
            "turnover_rate": getattr(m, "turnover_rate", None),
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("decision_pioneer fetch_tq_l2 %s failed: %s", symbol, e)
        return None


def _l2_summary(l2: dict | None) -> dict:
    """L2 主力净流入结构化解读(对齐同花顺"主力资金"明盘口径, 非暗盘)。

    明盘口径: 按单笔成交金额汇总的特大/大/中/小单净额, 同花顺官方"主力净额"字段。
    暗盘口径(同花顺"暗盘资金")需要拆单识别, 走 dark_flow(腾讯逐笔 + .tck)。
    """
    if not l2:
        return {"available": False}
    zjl_hb = l2.get("zjl_hb")
    cancel_buy = l2.get("cancel_buy")
    cancel_sell = l2.get("cancel_sell")
    net = zjl_hb if isinstance(zjl_hb, (int, float)) else None
    direction = None
    if net is not None:
        direction = "净流入" if net > 0 else ("净流出" if net < 0 else "平衡")
    cancel_total = None
    if isinstance(cancel_buy, (int, float)) and isinstance(cancel_sell, (int, float)):
        cancel_total = cancel_buy + cancel_sell
    return {
        "available": True,
        # Zjl_HB 单位=万元(通达信 get_more_info 约定, 与成交额 Amount 同量纲)。
        # 统一转元返回, 上层(前端 fmtWan / 推送 /1e4 / 选股池)按"元→万"换算口径一致。
        "zjl_hb": net * 1e4 if net is not None else None,
        "direction": direction,
        "cancel_buy": cancel_buy,
        "cancel_sell": cancel_sell,
        "cancel_total": cancel_total,
        "l2_tick_num": l2.get("l2_tick_num"),
        "l2_order_num": l2.get("l2_order_num"),
    }


def fetch_decision_pioneer(symbol: str, market: str = "CN") -> dict:
    """组合三指标 + L2 主力净流入 + 主力意图 → 完整快照(盘中实时)。

    返回结构:
    {
      symbol, market,
      institution_activity: {...}|None,   # AI机构活跃度
      gs: {...}|None,                      # GS策略
      l2: {...},                           # TQ L2 主力净流入(含 available 标记)
      main_intent: {...}|None,             # 主力意图(腾讯逐笔, 复用 dark_flow)
      data_time: str,
    }
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    bars = fetch_bars(symbol, market=market, days=60)
    act = compute_institution_activity(bars)
    gs = compute_gs_signal(bars)
    l2 = fetch_tq_l2(symbol, market=market)

    # 主力意图(复用现有 dark_flow, 失败不阻塞三指标)
    main_intent = None
    try:
        from marketdata.symbol import Symbol as MDSymbol
        from src.core.dark_flow import compute_dark_flow
        mdsym = MDSymbol.parse(symbol, market)
        dark = compute_dark_flow(mdsym)
        if dark:
            main_intent = {
                "main_net": dark.get("main_net"),
                "big_net": dark.get("big_net"),
                "mid_net": dark.get("mid_net"),
                "small_net": dark.get("small_net", dark.get("retail_net")),
                "main_intensity": dark.get("main_intensity"),
                "main_buy_ratio": dark.get("main_buy_ratio"),
                "signal": dark.get("signal"),
                "data_status": dark.get("data_status"),
            }
    except Exception as e:  # noqa: BLE001
        logger.warning("decision_pioneer main_intent %s failed: %s", symbol, e)

    # L2 数据不确定性(双源分歧度): 只记录 + 展示, 不阻断(阈值等数据积累后定)
    l2s = _l2_summary(l2)
    uncertainty = None
    try:
        from src.core.data_uncertainty import compute_uncertainty

        uncertainty = compute_uncertainty(
            l2s.get("zjl_hb"), (main_intent or {}).get("main_net")
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("decision_pioneer uncertainty %s failed: %s", symbol, e)

    return {
        "symbol": symbol,
        "market": market,
        "institution_activity": act,
        "gs": gs,
        "l2": l2s,
        "main_intent": main_intent,
        "uncertainty": uncertainty,
        "data_time": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
    }


def decision_pioneer_text(symbol: str, market: str = "CN") -> str:
    """三指标文本摘要(AI 助手工具 / 企微推送用)。"""
    d = fetch_decision_pioneer(symbol, market=market)
    parts: list[str] = []

    act = d.get("institution_activity")
    if act:
        parts.append(
            f"机构活跃度{act['activity']:.2f}({act['level']}, 连强{act['streak_days']}日"
            f"{', 5日均'+str(act['ma5']) if act.get('ma5') is not None else ''})"
        )
    else:
        parts.append("机构活跃度: 无数据")

    gs = d.get("gs")
    if gs:
        sig = {"G": "G买", "S": "S卖"}.get(gs.get("signal"), "无")
        parts.append(f"GS策略: 当前{gs['state']}, 最近信号{sig}")
    else:
        parts.append("GS策略: 无数据")

    l2 = d.get("l2") or {}
    if l2.get("available"):
        zjl = l2.get("zjl_hb")
        zjl_txt = f"{zjl / 1e4:+.0f}万" if isinstance(zjl, (int, float)) else "无"
        parts.append(f"主力净流入(L2·TQ): {zjl_txt}({l2.get('direction') or '平衡'})")
    else:
        parts.append("主力净流入(L2·TQ): 无数据")

    mi = d.get("main_intent")
    if mi and mi.get("data_status") != "insufficient":
        mn = mi.get("main_net") or 0
        parts.append(f"主力意图(逐笔): 主力净额{mn / 1e4:+.0f}万")
    elif mi:
        parts.append("主力意图(逐笔): 数据不足")

    unc = d.get("uncertainty") or {}
    if unc.get("score") is not None:
        parts.append(f"数据不确定性: {unc.get('level')}(分歧{unc['score']:.2f})")
    elif unc:
        parts.append("数据不确定性: 未知(双源缺一)")

    return " | ".join(parts)

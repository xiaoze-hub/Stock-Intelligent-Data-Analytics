"""除权除息与前复权(M1, 2026-09-10)。

数据流: MarketData.dividend(东财) → corporate_actions 表 → 前复权调整。
口径说明: DividendItem.dividend_per_share 按**每股派息(元)**理解；
bonus_ratio/transfer_ratio 为每10股送/转股数。东财原始字段若为每10股口径，
sync 时换算(见 _per_share 注释)。单位不确定时宁可跳过该条(记日志)，
也不把错数写进库 —— 复权错比没复权害处更大。

前复权(forward adjust): 以最新价为基准, 除权日前所有 bar 的 OHLC 同乘
累计因子 f=π(理论除权价/前收), volume 除以股份扩张倍数 m。理论除权价 =
(prev_close - cash_per_share) / m, m = 1 + (bonus+transfer)/10。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CorpAction:
    """单条除权除息事件(内存形态, 与 CorporateAction ORM 同构)。"""

    symbol: str
    market: str
    ex_date: str  # YYYY-MM-DD
    dividend_per_share: float | None = None
    bonus_ratio: float | None = None
    transfer_ratio: float | None = None
    source: str = "eastmoney"


def _num(v) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f and f > 0 else None
    except Exception:
        return None


def ex_factor(prev_close: float, action: CorpAction) -> tuple[float, float] | None:
    """单次除权的 (价格因子 f, 股份倍数 m)。无实质内容返回 None。

    f = 理论除权价 / 前收; m = 1 + (送+转)/10。纯现金分红时 m=1。
    """
    if not prev_close or prev_close <= 0:
        return None
    cash = _num(action.dividend_per_share) or 0.0
    b = _num(action.bonus_ratio) or 0.0
    t = _num(action.transfer_ratio) or 0.0
    if cash <= 0 and b <= 0 and t <= 0:
        return None
    m = 1.0 + (b + t) / 10.0
    theo = (prev_close - cash) / m
    if theo <= 0 or theo >= prev_close * 2:
        logger.warning(
            "除权因子异常跳过 %s %s: prev=%s cash=%s b=%s t=%s",
            action.symbol, action.ex_date, prev_close, cash, b, t,
        )
        return None
    return theo / prev_close, m


def apply_forward_adjust(bars: list, actions: list[CorpAction]) -> list:
    """对 PriceBar 列表做前复权, 返回新列表(输入不变)。

    bars 须按日期升序。同一标的多次除权按时间倒序累乘。无 action 原样返回。
    要求 bar 有 date/open/high/low/close/volume 属性(PriceBar 同形即可)。
    """
    if not bars or not actions:
        return list(bars)
    acts = sorted(
        [a for a in actions if a and a.ex_date], key=lambda a: str(a.ex_date)
    )
    if not acts:
        return list(bars)
    dates = [str(getattr(b, "date", ""))[:10] for b in bars]
    closes = [getattr(b, "close", None) for b in bars]
    out = list(bars)
    # 倒序处理: 每次除权只影响其前面的 bar
    for a in reversed(acts):
        ex = str(a.ex_date)[:10]
        # 前收 = 除权日前最后一个 bar 的 close
        prev = None
        for d, c in zip(reversed(dates), reversed(closes)):
            if d < ex:
                prev = c
                break
        if prev is None:
            continue
        try:
            prev_f = float(prev)
        except Exception:
            continue
        fac = ex_factor(prev_f, a)
        if fac is None:
            continue
        f, m = fac
        adj = []
        for b, d in zip(out, dates):
            if d < ex:
                try:
                    from dataclasses import replace as _replace

                    adj.append(_replace(
                        b,
                        open=float(b.open) * f,
                        high=float(b.high) * f,
                        low=float(b.low) * f,
                        close=float(b.close) * f,
                        volume=float(b.volume or 0) / m,
                    ))
                except Exception:
                    adj.append(b)
            else:
                adj.append(b)
        out = adj
    return out


def sync_corporate_actions(symbols: list[str], market: str = "CN") -> dict:
    """从 MarketData.dividend 拉分红并 upsert 入库。返回 {added, skipped, symbols}。

    进度非"实施/完成"类的预案条目跳过(只要已实施除权)。网络异常整批返回 error。
    """
    from src.core.marketdata_client import get_market_data
    from src.web.database import SessionLocal
    from src.web.models import CorporateAction

    stats = {"added": 0, "skipped": 0, "symbols": list(symbols or []), "error": ""}
    if not symbols:
        return stats
    try:
        items = get_market_data().dividend(list(symbols), market=market)
    except Exception as e:  # noqa: BLE001
        stats["error"] = str(e)[:300]
        return stats
    db = SessionLocal()
    try:
        for it in items or []:
            ex = str(getattr(it, "ex_date", "") or "")[:10]
            prog = str(getattr(it, "progress", "") or "")
            # 只要已实施: 进度含实施/完成/除权, 或进度为空(东财历史数据常无进度)
            if prog and not any(k in prog for k in ("实施", "完成", "除权", "上市")):
                stats["skipped"] += 1
                continue
            if not ex or len(ex) < 10:
                stats["skipped"] += 1
                continue
            row = (
                db.query(CorporateAction)
                .filter(
                    CorporateAction.symbol == str(getattr(it, "symbol", "")),
                    CorporateAction.market == market,
                    CorporateAction.ex_date == ex,
                )
                .first()
            )
            if row:
                stats["skipped"] += 1
                continue
            db.add(CorporateAction(
                symbol=str(getattr(it, "symbol", "")),
                market=market,
                ex_date=ex,
                dividend_per_share=_num(getattr(it, "dividend_per_share", None)),
                bonus_ratio=_num(getattr(it, "bonus_ratio", None)),
                transfer_ratio=_num(getattr(it, "transfer_ratio", None)),
                source="eastmoney",
            ))
            stats["added"] += 1
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        stats["error"] = str(e)[:300]
    finally:
        db.close()
    return stats


def load_actions(symbol: str, market: str = "CN") -> list[CorpAction]:
    """读某标的全部除权事件(内存形态, 供回测/K线标记)。"""
    from src.web.database import SessionLocal
    from src.web.models import CorporateAction

    db = SessionLocal()
    try:
        rows = (
            db.query(CorporateAction)
            .filter(CorporateAction.symbol == symbol, CorporateAction.market == market)
            .order_by(CorporateAction.ex_date.asc())
            .all()
        )
        return [
            CorpAction(
                symbol=r.symbol, market=r.market, ex_date=str(r.ex_date)[:10],
                dividend_per_share=r.dividend_per_share,
                bonus_ratio=r.bonus_ratio, transfer_ratio=r.transfer_ratio,
                source=r.source or "eastmoney",
            )
            for r in rows
        ]
    finally:
        db.close()

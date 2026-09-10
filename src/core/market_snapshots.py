"""日级共享快照(P2): 涨停池/板块轮动/指数快照单写者定时刷, 全员读库。

写: sync_market_snapshots(db) — 定时任务/cron 调, 逐 kind 抓取 upsert,
    单 kind 失败记 error 保留旧行, 不抛。
读: read_snapshot(db, kind, date) — 前端/内部读, miss 返回 None(调用方
    用 P1 singleflight 回源, 读链路永不触发写)。
"""

from __future__ import annotations

import json
import logging
from datetime import date

logger = logging.getLogger(__name__)

KINDS = ("limit_up_pool", "sector_rotation", "index_snapshot")


def _collector():
    from src.collectors.market_sentiment_collector import MarketSentimentCollector

    return MarketSentimentCollector()


def _fetch_kind(kind: str, ymd: str):
    """单 kind 抓取。返回 (payload, source)。抛异常由 sync 按 kind 吞掉。"""
    col = _collector()
    if kind == "limit_up_pool":
        return col.get_limit_up_pool(ymd) or [], "wudao_eastmoney"
    if kind == "sector_rotation":
        return col.get_sector_rotation(top_n=20) or {}, "wudao_eastmoney"
    if kind == "index_snapshot":
        return col.get_index_snapshot() or [], "tencent"
    raise ValueError(f"未知快照 kind: {kind}")


def sync_market_snapshots(db, kinds=KINDS, snapshot_date: str | None = None) -> dict:
    """单写者入口: 抓取并 upsert 当日快照。幂等(唯一键 kind+date+market)。

    返回 {kind: {"rows": n} | {"error": msg}}。全部失败也不抛(保留旧快照)。
    """
    from src.web.models import MarketSnapshot

    day = snapshot_date or date.today().isoformat()
    ymd = day.replace("-", "")
    out: dict = {}
    for kind in kinds:
        try:
            payload, source = _fetch_kind(kind, ymd)
            row = (
                db.query(MarketSnapshot)
                .filter(
                    MarketSnapshot.kind == kind,
                    MarketSnapshot.snapshot_date == day,
                    MarketSnapshot.market == "CN",
                )
                .first()
            )
            blob = json.dumps(payload, ensure_ascii=False, default=str)
            if row is None:
                row = MarketSnapshot(kind=kind, snapshot_date=day, market="CN")
                db.add(row)
            row.payload = blob
            row.source = source
            db.commit()
            out[kind] = {"rows": len(payload) if isinstance(payload, list) else 1}
        except Exception as e:  # noqa: BLE001 — 单 kind 失败保留旧行
            db.rollback()
            logger.warning("market_snapshots: %s 同步失败(%s), 保留旧快照", kind, e)
            out[kind] = {"error": str(e)[:200]}
    return out


def read_snapshot(db, kind: str, snapshot_date: str | None = None) -> dict | None:
    """读快照: {asof, source, fetched_at, payload}。无行返回 None。"""
    from src.web.models import MarketSnapshot

    day = snapshot_date or date.today().isoformat()
    row = (
        db.query(MarketSnapshot)
        .filter(
            MarketSnapshot.kind == kind,
            MarketSnapshot.snapshot_date == day,
            MarketSnapshot.market == "CN",
        )
        .first()
    )
    if row is None:
        return None
    try:
        payload = json.loads(row.payload or "[]")
    except Exception:
        payload = []
    fetched = row.fetched_at
    return {
        "kind": kind,
        "asof": day,
        "source": row.source or "",
        "fetched_at": fetched.isoformat() if hasattr(fetched, "isoformat") else str(fetched or ""),
        "payload": payload,
    }

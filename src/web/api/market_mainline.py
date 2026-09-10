"""市场主线识别 API(v0.3.0, 2026-08-24)。

端点(挂在 /api/market 前缀下, 与现有 market.py 同前缀不冲突):
    GET /api/market/mainline         → Top20 主线排名 + 各主线成分股(含 rank_change)

设计:
  - 数据源: MarketSentimentCollector().get_limit_up_pool()(wudao 优先, 东财兜底)
  - 聚合: src.core.market_mainline.aggregate_mainline
  - 缓存: 模块内 60s 进程内 dict(per spec), 避免每 30s 前端轮询都翻涨停池
  - v0.4.7: ranked_groups 每项加 rank + rank_change(rank_change = 昨日rank - 今日rank,
    正数=上升, 首次无昨日快照时为 null)。当日快照 upsert 到 mainline_rank_daily。

与其他 market.* 接口解耦: 仅本路由聚合涨停池, 不动现有 indices/sparkline 缓存。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from src.core.market_mainline import aggregate_mainline

logger = logging.getLogger(__name__)

router = APIRouter()

# ──────────── Task 3: 主线日榜快照表(双方言兼容, v0.4.7) ────────────
# 主线榜每次计算成功后 upsert 当日 (date, name, rank, score),
# 跨日对比出 rank_change。PRIMARY KEY(date, name) 保证每日每线只留最新一条。
_rank_table_ready = False
_rank_table_lock = threading.Lock()


def _ensure_mainline_rank_table() -> None:
    """幂等建表 mainline_rank_daily, 模块加载跑一次。

    字段: date(DATE) / name(TEXT) / rank(int) / score(float8)
    主键: (date, name), 保证同日同线只有一条最新快照。
    """
    global _rank_table_ready
    if _rank_table_ready:
        return
    with _rank_table_lock:
        if _rank_table_ready:
            return
        try:
            from src.web.database import IS_PG, engine
            if IS_PG:
                ddl = (
                    """
                    CREATE TABLE IF NOT EXISTS mainline_rank_daily (
                        date DATE NOT NULL,
                        name TEXT NOT NULL,
                        rank INTEGER NOT NULL,
                        score DOUBLE PRECISION,
                        PRIMARY KEY (date, name)
                    )
                    """
                )
            else:
                ddl = (
                    """
                    CREATE TABLE IF NOT EXISTS mainline_rank_daily (
                        date TEXT NOT NULL,
                        name TEXT NOT NULL,
                        rank INTEGER NOT NULL,
                        score REAL,
                        PRIMARY KEY (date, name)
                    )
                    """
                )
            with engine.begin() as conn:
                conn.execute(text(ddl))
            _rank_table_ready = True
        except Exception as e:
            logger.debug(f"mainline_rank_daily 建表暂未就绪: {e}")


def _load_yesterday_ranks(today_iso: str) -> dict[str, int]:
    """读最近一日 < today 的快照, 返回 {name: rank}。

    没有历史快照时返回空 dict, 上层据此输出 rank_change=null。
    """
    try:
        from src.web.database import engine
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT MAX(date) AS latest_date
                    FROM mainline_rank_daily
                    WHERE date < :today
                    """
                ),
                {"today": today_iso},
            ).first()
            latest = row[0] if row else None
            if not latest:
                return {}
            rows = conn.execute(
                text(
                    """
                    SELECT name, rank
                    FROM mainline_rank_daily
                    WHERE date = :d
                    """
                ),
                {"d": str(latest)},
            ).fetchall()
            return {str(r[0]): int(r[1]) for r in rows if r[0] is not None}
    except Exception as e:
        logger.debug(f"mainline_rank_daily 读取昨日失败(静默): {e}")
        return {}


def _upsert_today_snapshot(today_iso: str, ranked_groups: list[dict]) -> None:
    """当日快照 upsert: PRIMARY KEY(date, name) 冲突时覆盖 rank + score。
    失败静默(主流程不应被落库异常阻断)。
    """
    if not ranked_groups:
        return
    try:
        from src.web.database import engine
        from src.web.database import IS_PG
        if IS_PG:
            stmt = (
                """
                INSERT INTO mainline_rank_daily (date, name, rank, score)
                VALUES (:date, :name, :rank, :score)
                ON CONFLICT (date, name) DO UPDATE
                SET rank = EXCLUDED.rank, score = EXCLUDED.score
                """
            )
        else:
            # SQLite UPSERT (3.24+)
            stmt = (
                """
                INSERT INTO mainline_rank_daily (date, name, rank, score)
                VALUES (:date, :name, :rank, :score)
                ON CONFLICT (date, name) DO UPDATE
                SET rank = excluded.rank, score = excluded.score
                """
            )
        with engine.begin() as conn:
            for g in ranked_groups:
                name = g.get("name")
                if not name:
                    continue
                conn.execute(
                    text(stmt),
                    {
                        "date": today_iso,
                        "name": str(name),
                        "rank": int(g.get("rank") or 0),
                        "score": g.get("score"),
                    },
                )
    except Exception as e:
        logger.debug(f"mainline_rank_daily 写入当日快照失败(静默): {e}")


def _enrich_with_rank_change(ranked_groups: list[dict]) -> list[dict]:
    """给 ranked_groups 每项加 rank(1-indexed)+ rank_change(昨日-今日, 正=上升)。

    首次无昨日快照 → rank_change=null(数据缺失诚实标注, 不编造)。
    """
    if not ranked_groups:
        return ranked_groups
    today_iso = date.today().isoformat()
    # 兜底: 首次写入时若建表未就绪, 再补一次
    if not _rank_table_ready:
        _ensure_mainline_rank_table()
    yesterday = _load_yesterday_ranks(today_iso)

    enriched: list[dict] = []
    for idx, g in enumerate(ranked_groups, start=1):
        item = dict(g)
        item["rank"] = idx
        name = g.get("name")
        if name in yesterday:
            # rank_change 正数=上升(昨日排名 - 今日排名)
            item["rank_change"] = int(yesterday[name]) - idx
        else:
            item["rank_change"] = None
        enriched.append(item)

    # 落当日快照(失败静默, 不影响响应)
    _upsert_today_snapshot(today_iso, enriched)
    return enriched


# 模块加载时尝试一次建表
_ensure_mainline_rank_table()

# ──────────── 60s 进程内缓存(per spec) ────────────
# key 固定为 "mainline:top20", 共享一份 Top20 排名(全市场视角)。
# 拉涨停池耗 5-15s, 60s 缓存压住前端 30s 轮询的并发翻页成本。
_CACHE_TTL_S = 60.0
_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}


def _fetch_mainline() -> dict:
    """拉涨停池 + 聚合。失败容错: 返回 aggregate_mainline([]) 空结构 + note。

    v0.4.7: 聚合后 enrich ranked_groups(加 rank + rank_change), 并 upsert 当日快照。
    enrichment 失败静默, 主流程返回结构不变(只是少 rank_change 字段)。
    """
    try:
        from src.collectors.market_sentiment_collector import MarketSentimentCollector

        collector = MarketSentimentCollector()
        pool = collector.get_limit_up_pool()
    except Exception as e:
        logger.warning("market_mainline: 涨停池拉取失败(%s), 返回空数据", e)
        pool = []

    result = aggregate_mainline(pool)
    # 增加一个 cache_ts(给前端读"最近一次拉取时间")
    result = dict(result)
    result["cache_ts"] = time.time()
    # P3: 计算口径日期(当日榜), 前端徽标显"截至 asof", 不再只显拉取时间
    result["asof"] = date.today().isoformat()
    # v0.4.7: 排名变动(对比昨日快照, 失败静默)
    try:
        ranked = result.get("ranked_groups") or []
        if ranked:
            result["ranked_groups"] = _enrich_with_rank_change(ranked)
    except Exception as e:
        logger.debug("market_mainline: rank_change 增强失败(静默): %s", e)
    return result


@router.get("/mainline")
def get_market_mainline() -> dict:
    """市场主线 Top20 排名 + 成分股列表。

    返回结构见 src.core.market_mainline.aggregate_mainline 文档:
      {
        "total_groups", "ranked_groups": [...], "unranked": [...],
        "filter_stats": {broad_filtered, below_min, ranked},
        "note", "cache_ts"
      }

    缓存: 进程内 60s TTL。clear_market_mainline_cache() 可强制刷新(运维用)。
    """
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get("mainline:top20")
        if hit is not None and now - hit[0] < _CACHE_TTL_S:
            return hit[1]

    payload = _fetch_mainline()
    with _cache_lock:
        _cache["mainline:top20"] = (now, payload)
    return payload


def clear_market_mainline_cache() -> None:
    """清空进程内 60s 缓存(运维/测试用)。

    不暴露 HTTP 路由(其他业务无强需求);测试可在 conftest autouse 里调。
    """
    with _cache_lock:
        _cache.clear()


@router.post("/snapshots/sync")
def sync_snapshots(date_str: str | None = None) -> dict:
    """单写者触发(P2): 定时任务/cron 调, 全 kind 抓取 upsert 当日快照。

    用户读链路不调这个(读走 GET /snapshots)。单 kind 失败保留旧行。
    """
    from src.core.market_snapshots import sync_market_snapshots
    from src.web.database import SessionLocal

    db = SessionLocal()
    try:
        day = date_str or date.today().isoformat()
        return {"asof": day, "result": sync_market_snapshots(db, snapshot_date=date_str)}
    finally:
        db.close()


@router.get("/snapshots")
def get_snapshot(kind: str, date_str: str | None = None) -> dict:
    """读快照(P2): 有行直返(含 asof/source/fetched_at)。

    miss 则 P1 单飞回源(120s 缓存, 不落库): 同一时刻多账号只打 1 次上游。
    kind: limit_up_pool/sector_rotation/index_snapshot。
    """
    from src.core.market_snapshots import KINDS, _fetch_kind, read_snapshot

    if kind not in KINDS:
        raise HTTPException(400, f"未知 kind: {kind}, 可选 {list(KINDS)}")
    from src.web.database import SessionLocal

    db = SessionLocal()
    try:
        row = read_snapshot(db, kind, date_str)
    finally:
        db.close()
    if row is not None:
        return row
    day = date_str or date.today().isoformat()
    from src.web.cache.biz_cache import biz_cache

    payload, source = biz_cache.get_or_fetch(
        f"snap:live:{kind}:{day}",
        ttl=120,
        fetch=lambda: _fetch_kind(kind, day.replace("-", "")),
    )
    return {
        "kind": kind, "asof": day, "source": source or "",
        "fetched_at": "", "payload": payload, "live": True,
    }

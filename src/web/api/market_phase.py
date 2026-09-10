"""市场情绪周期 6 阶段 API(2026-08-24)。

GET  /api/market/phase              → 当前阶段 + 近 30 天序列 + 分布统计(biz_cache 30s 复用)
POST /api/market/phase/sync         → 拉今日涨停池, 算指标落库, 重算阶段

口径:
- 每日梯队指标由 src/core/market_phase.compute_daily_metrics 派生, 昨日连板池
  通过 MarketSentimentCollector().get_limit_up_pool(yesterday) 获取
- 阶段标签由 classify_phase_series 重算(EMA + 2 日确认 + 弱档否决)
- 上证指数 pct 走 MarketSentimentCollector.get_index_snapshot()

前置模块: src/core/market_phase.py(纯函数), MarketSentimentCollector(涨停池)
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException
from sqlalchemy import desc

from src.collectors.market_sentiment_collector import MarketSentimentCollector
from src.core.market_phase import (
    PHASE_LABELS,
    PHASE_PRIORITY,
    classify_phase_series,
    compute_daily_metrics,
    ordered_distribution,
    phase_distribution,
)
from src.web.cache.biz_cache import biz_cache
from src.web.database import SessionLocal
from src.web.models import MarketPhaseDaily

router = APIRouter()
logger = logging.getLogger(__name__)

# biz_cache key 前缀(模块统一前缀 biz:, 在 cache 内置)
_PHASE_CACHE_KEY = "market:phase:current"
_PHASE_CACHE_TTL = 30  # 30s, 与前端轮询节奏一致

# 30 天序列上限
_RECENT_LIMIT = 120


# ─────────────────────────── 内部辅助 ───────────────────────────
def _date_to_ymd(d: date) -> str:
    """date → YYYYMMDD(给 collector 用)。"""
    return d.strftime("%Y%m%d")


def _sh_index_pct_today() -> float | None:
    """拉今日上证指数涨跌幅(%), 数据源不可用时 None。

    优先级: 名称含"上证/沪指"; 兜底取接口返回的第一个(腾讯接口固定 sh/sz/cyb 顺序)。

    P2: 先读当日 index_snapshot 行(定时单写者刷), miss 才 P1 单飞回源。
    """
    idx: list = []
    try:
        from src.web.database import SessionLocal
        from src.core.market_snapshots import read_snapshot

        db = SessionLocal()
        try:
            snap = read_snapshot(db, "index_snapshot")
        finally:
            db.close()
        idx = (snap or {}).get("payload") or []
    except Exception as e:  # noqa: BLE001
        logger.debug("market_phase: 快照读指数失败(回源): %s", e)
    if not idx:
        try:
            from src.web.cache.biz_cache import biz_cache
            from src.core.market_snapshots import _fetch_kind

            payload, _ = biz_cache.get_or_fetch(
                "snap:live:index_snapshot:today",
                ttl=120,
                fetch=lambda: _fetch_kind("index_snapshot", ""),
            )
            idx = payload or []
        except Exception as e:  # noqa: BLE001
            logger.debug("market_phase: 指数回源失败: %s", e)
            return None
    try:
        for x in idx:
            name = (x.get("name") or "").strip()
            if any(k in name for k in ("上证", "沪指", "上证综指")):
                pct = x.get("pct")
                if pct is not None:
                    return float(pct)
        if idx:
            pct = idx[0].get("pct")
            if pct is not None:
                return float(pct)
    except Exception as e:  # noqa: BLE001
        logger.warning("fetch sh index pct failed: %r", e)
    return None


def _row_to_dict(r: MarketPhaseDaily) -> dict:
    return {
        "date": r.date.isoformat() if hasattr(r.date, "isoformat") else str(r.date),
        "phase": r.phase or "",
        "label": PHASE_LABELS.get(r.phase or "", r.phase or ""),
        "first_board": r.first_board,
        "ge2_count": r.ge2_count,
        "ge3_count": r.ge3_count,
        "ge5_count": r.ge5_count,
        "max_height": r.max_height,
        "promo_rate": r.promo_rate,
        "seal_rate": r.seal_rate,
        "sh_index_pct": r.sh_index_pct,
    }


# ─────────────────────────── GET ───────────────────────────
@router.get("/phase")
def get_phase() -> dict:
    """当前阶段 + 近 30 天序列 + 分布统计。

    缓存: biz_cache(memory L1 + Redis L2), 30s TTL — 30s 内复用同一快照, 减轻 DB 压力。
    数据源: market_phase_daily 表(由 POST /sync 写入)。
    """
    cached = biz_cache.get_json(_PHASE_CACHE_KEY)
    if cached is not None:
        return cached

    db = SessionLocal()
    try:
        # 取最近 260 天(v0.4.6: 时间线从30天扩到120天, 留分布统计余量)
        rows = (
            db.query(MarketPhaseDaily)
            .order_by(desc(MarketPhaseDaily.date))
            .limit(260)
            .all()
        )
        rows = list(reversed(rows))  # 升序, 便于前端时间线展示

        if not rows:
            payload = {
                "available": False,
                "asof": None,
                "current": None,
                "recent_30d": [],
                "distribution": [],
                "total_days": 0,
                "note": "尚未同步阶段数据, 请调用 POST /api/market/phase/sync",
            }
            biz_cache.set_json(_PHASE_CACHE_KEY, payload, ttl=_PHASE_CACHE_TTL)
            return payload

        recent = rows[-_RECENT_LIMIT:]
        cur = recent[-1]
        phases_all = [r.phase for r in rows if r.phase]
        dist_raw = phase_distribution(phases_all)
        dist_ordered = ordered_distribution(dist_raw)

        payload = {
            "available": True,
            # P3: 数据口径日期(最后一条日记录), 前端徽标显"截至 asof"
            "asof": cur.date.isoformat() if hasattr(cur.date, "isoformat") else str(cur.date),
            "current": _row_to_dict(cur),
            "recent_30d": [_row_to_dict(r) for r in recent],
            "recent_days": [_row_to_dict(r) for r in recent],
            "distribution": [
                {"phase": k, "days": v, "label": lab}
                for k, v, lab in dist_ordered
            ],
            "total_days": len(rows),
            "note": (
                f"基于近 {len(rows)} 天历史, 当前阶段: "
                f"{PHASE_LABELS.get(cur.phase or '', cur.phase or '未知')} "
                f"(口径: EMA α=1/3 + 2 日确认 + 上证<-2% 弱档否决)"
            ),
        }
        biz_cache.set_json(_PHASE_CACHE_KEY, payload, ttl=_PHASE_CACHE_TTL)
        return payload
    except Exception as e:  # noqa: BLE001
        logger.exception("[market-phase] 查询失败")
        raise HTTPException(500, f"查询失败: {e!r}")
    finally:
        db.close()


# ─────────────────────────── POST /sync ───────────────────────────
@router.post("/phase/sync")
def sync_phase(date_str: str | None = None) -> dict:
    """拉今日(指定日)涨停池, 计算指标, 落库, 重算阶段。

    流程:
    1) MarketSentimentCollector().get_limit_up_pool(date) 取当日涨停池
    2) 同样方法取昨日池(用于 promo_rate; 周末/节假日池可能为空 → promo=None)
    3) compute_daily_metrics 算出梯队指标
    4) 上证指数 pct(用于弱档否决)
    5) upsert 到 market_phase_daily
    6) 全量重算 classify_phase_series, 更新每行 phase
    7) 清 biz_cache 让前端下次拉取拿到新数据

    内部用 + cron 用: 失败不抛给前端以外的链路(返回 502 + error 即可)。
    """
    target = (
        datetime.strptime(date_str, "%Y-%m-%d").date()
        if date_str
        else date.today()
    )
    ymd_today = _date_to_ymd(target)
    ymd_yesterday = _date_to_ymd(target - timedelta(days=1))

    col = MarketSentimentCollector()
    pool_today = col.get_limit_up_pool(ymd_today) or []
    pool_yesterday_raw = col.get_limit_up_pool(ymd_yesterday) or []
    pool_yesterday = pool_yesterday_raw if pool_yesterday_raw else None

    metrics = compute_daily_metrics(pool_today, pool_yesterday)
    metrics.sh_index_pct = _sh_index_pct_today()

    db = SessionLocal()
    try:
        existing = db.query(MarketPhaseDaily).filter(MarketPhaseDaily.date == target).first()
        if existing is None:
            existing = MarketPhaseDaily(date=target)
            db.add(existing)
        existing.first_board = metrics.first_board
        existing.ge2_count = metrics.ge2_count
        existing.ge3_count = metrics.ge3_count
        existing.ge5_count = metrics.ge5_count
        existing.max_height = metrics.max_height
        existing.promo_rate = metrics.promo_rate
        existing.seal_rate = metrics.seal_rate
        existing.sh_index_pct = metrics.sh_index_pct
        # phase 由下面重算统一更新
        db.commit()

        # 重算全量阶段(EMA 依赖完整序列, 不能只算今天)
        all_rows = (
            db.query(MarketPhaseDaily)
            .order_by(MarketPhaseDaily.date)
            .all()
        )
        history = [
            {
                "date": r.date,
                "first_board": r.first_board,
                "ge2_count": r.ge2_count,
                "ge3_count": r.ge3_count,
                "ge5_count": r.ge5_count,
                "max_height": r.max_height,
                "promo_rate": r.promo_rate,
                "seal_rate": r.seal_rate,
                "sh_index_pct": r.sh_index_pct,
            }
            for r in all_rows
        ]
        new_phases = classify_phase_series(history)
        for r, p in zip(all_rows, new_phases):
            r.phase = p
        db.commit()

        biz_cache.delete(_PHASE_CACHE_KEY)

        return {
            "synced": True,
            "date": target.isoformat(),
            "pool_size_today": len(pool_today),
            "pool_size_yesterday": len(pool_yesterday_raw),
            "metrics": {
                "first_board": metrics.first_board,
                "ge2_count": metrics.ge2_count,
                "ge3_count": metrics.ge3_count,
                "ge5_count": metrics.ge5_count,
                "max_height": metrics.max_height,
                "promo_rate": metrics.promo_rate,
                "seal_rate": metrics.seal_rate,
                "sh_index_pct": metrics.sh_index_pct,
            },
            "phase": new_phases[-1] if new_phases else None,
            "total_days_in_db": len(all_rows),
            "note": (
                f"已落 {target.isoformat()} 指标并重算 {len(all_rows)} 天阶段"
                + (f"; 当前阶段: {PHASE_LABELS.get(new_phases[-1], new_phases[-1])}" if new_phases else "")
            ),
        }
    except Exception as e:  # noqa: BLE001
        logger.exception("[market-phase-sync] 同步失败")
        db.rollback()
        raise HTTPException(502, f"阶段同步失败: {e!r}")
    finally:
        db.close()


# ─────────────────────────── register_cron (v0.4.6) ───────────────────────────
def register_cron(scheduler) -> bool:
    """把情绪周期每日同步 job 注册到传入的现有 APScheduler 实例。

    工作日 15:10 (收盘后) 自动 sync 当日涨停池指标 → market_phase_daily。
    模式对齐 src/core/auction_pool.register_cron: 复用现有 APScheduler,
    不新开 scheduler; 调度器不可用返回 False 不崩。
    """
    if scheduler is None or not hasattr(scheduler, "add_job"):
        return False

    def _phase_sync_once():
        try:
            result = sync_phase()
            logger.info("[market-phase] 每日阶段同步完成: %s", result.get("note", ""))
        except Exception as e:  # noqa: BLE001
            logger.error("[market-phase] 每日阶段同步异常: %r", e)

    try:
        scheduler.add_job(
            _phase_sync_once,
            "cron",
            day_of_week="mon-fri",
            hour=15,
            minute=10,
            id="market_phase_daily_sync",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        logger.info("[market-phase] 每日阶段同步 cron 已注册: 工作日 15:10")
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("[market-phase] cron 注册失败: %r", e)
        return False

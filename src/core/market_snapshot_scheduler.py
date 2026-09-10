"""日快照定时单写者(P2): 盘中每 30 分钟刷涨停池/板块轮动/指数快照入库。

照抄 KlineBackfillScheduler 形状(AsyncIOScheduler + 全局单例 + server
lifespan 启动)。失败只记日志保留旧快照; 周末跳过。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)

SNAPSHOT_INTERVAL_MINUTES = 30

_global_scheduler: "MarketSnapshotScheduler | None" = None


def _is_market_day() -> bool:
    return datetime.now().weekday() < 5  # 周一到周五, 节假日留 hook


def _sync_in_job() -> dict:
    from src.web.database import SessionLocal
    from src.core.market_snapshots import sync_market_snapshots

    db = SessionLocal()
    try:
        t0 = time.monotonic()
        out = sync_market_snapshots(db)
        logger.info("[market snapshots] 同步完成(%.1fs): %s", time.monotonic() - t0, out)
        return out
    finally:
        db.close()


class MarketSnapshotScheduler:
    """日快照调度器, 盘中 30 分钟一刷。"""

    def __init__(self, timezone: str = "Asia/Shanghai"):
        self.scheduler = AsyncIOScheduler(timezone=timezone)
        self._running = False

    async def _job(self):
        if self._running:
            logger.warning("[market snapshots] 上轮还在跑, 跳过本轮")
            return
        if not _is_market_day():
            return
        self._running = True
        try:
            import asyncio

            await asyncio.to_thread(_sync_in_job)
        except Exception as e:  # noqa: BLE001
            logger.error("[market snapshots] 定时同步失败: %s", e)
        finally:
            self._running = False

    def start(self):
        global _global_scheduler
        _global_scheduler = self
        self.scheduler.add_job(
            self._job,
            "interval",
            minutes=SNAPSHOT_INTERVAL_MINUTES,
            id="market_snapshots_interval",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.start()
        try:
            from src.core.scheduler_registry import register

            register("market_snapshots", self.scheduler)
        except Exception:  # noqa: BLE001 — 注册表缺失不阻断
            pass
        logger.info("[market snapshots] 调度器已启动: 每 %s 分钟(周一至五)", SNAPSHOT_INTERVAL_MINUTES)

    def shutdown(self):
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            pass

    def trigger_now(self) -> dict:
        """手动触发一次同步(管理界面或测试用)。"""
        return _sync_in_job()

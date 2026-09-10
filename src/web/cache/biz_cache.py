"""业务缓存层 (2026-08-22) — 把散落在各 API/collector 的内存 dict 缓存统一为
L1 内存 + L2 Redis 两级缓存。

设计目标:
- L1 进程内 dict(快, 现有行为) — 命中零网络开销
- L2 Redis(跨进程共享 + 重启不丢) — 多 worker / 多容器 / 重启后冷启动加速
- 优雅降级: Redis 不可达时退回纯 L1, 行为等价于现状(不抛错、不阻塞)

为什么同步接口:
- 业务缓存点(汇率/组合结果/发现页热点/K线等)大多在同步函数或 asyncio.to_thread
  里跑, 用 redis-py 同步客户端(线程安全连接池)最自然。
- 与 async 的 redis_client(限流/Stream 用) 是两套独立连接, 互不干扰。

用法:
    from src.web.cache.biz_cache import biz_cache
    v = biz_cache.get_json("fx:hkd")
    biz_cache.set_json("fx:hkd", {"rate": 0.92}, ttl=3600)
    data = biz_cache.get_or_fetch("discovery:stocks:CN:turnover:20", ttl=45, fetch=fetch_fn)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# 复用 redis_client 的配置源, 保证两套客户端连同一个 Redis
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0").strip()
REDIS_DISABLED = os.getenv("REDIS_DISABLED", "").strip().lower() in (
    "1", "true", "yes", "on",
)

# Redis 连接失败后的冷却时间(秒): 冷却窗口内不再尝试连接, 避免每次 miss 都撞超时
_REDIS_CONNECT_COOLDOWN = 30.0
# 单次 Redis 操作超时(秒)
_REDIS_OP_TIMEOUT = 1.5
# 业务缓存统一 key 前缀: 与限流(middleware 的 u:/i: key)和 stream(stream:*)
# 天然隔离, 也让 clear() 能安全地只清业务缓存而 flushdb 不误伤其他用途。
_KEY_PREFIX = "biz:"


# 单飞去重: 同 key 并发 miss 只放 1 个上游请求, waiter 最长等待(秒)
_SINGLEFLIGHT_WAIT_S = 30.0
# stale 快照上限(防无限膨胀, 超了淘汰最早写入的 key)
_STALE_MAX_KEYS = 2000


class BizCache:
    """L1 内存 + L2 Redis 两级缓存(同步接口)。"""

    _instance: Optional["BizCache"] = None

    def __init__(self) -> None:
        self._l1: dict[str, tuple[float, Any]] = {}  # key -> (expires_at, value)
        self._lock = threading.Lock()
        self._inflight: dict[str, threading.Event] = {}  # singleflight: key -> 完成事件
        # OT-Phase5 stale 回退: 每次写入同时留一份不过期快照, 上游挂时不断档
        self._stale: dict[str, Any] = {}
        self._redis: Any = None
        self._redis_attempted = False
        self._redis_next_attempt = 0.0
        self._enabled = not REDIS_DISABLED

    @classmethod
    def instance(cls) -> "BizCache":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ─── L2 Redis 连接(懒连接 + 冷却) ───
    def _ensure_redis(self) -> Any:
        """返回可用的同步 redis 客户端, 不可用返回 None(带冷却)。"""
        if not self._enabled:
            return None
        if self._redis is not None:
            return self._redis
        now = time.monotonic()
        if self._redis_attempted and now < self._redis_next_attempt:
            return None  # 冷却中, 不重试
        try:
            from redis import Redis
            self._redis = Redis.from_url(
                REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=1.5,
                socket_timeout=_REDIS_OP_TIMEOUT,
                health_check_interval=30,
            )
            self._redis.ping()
            self._redis_attempted = True
            logger.info(f"[biz-cache] Redis 连接成功: {REDIS_URL}")
            return self._redis
        except Exception as e:
            self._redis = None
            self._redis_attempted = True
            self._redis_next_attempt = now + _REDIS_CONNECT_COOLDOWN
            logger.warning(f"[biz-cache] Redis 不可用, 退回 L1 内存: {e}")
            return None

    def _redis_down(self) -> None:
        """Redis 操作失败时标记冷却, 避免每请求都撞超时。"""
        self._redis = None
        self._redis_attempted = True
        self._redis_next_attempt = time.monotonic() + _REDIS_CONNECT_COOLDOWN

    # ─── 基础读写 ───
    def get_json(self, key: str) -> Optional[Any]:
        """先 L1 后 L2; L2 命中回填 L1。返回 None 表示未命中。"""
        now = time.monotonic()
        with self._lock:
            entry = self._l1.get(key)
            if entry is not None:
                expires_at, value = entry
                if expires_at > now:
                    return value
                del self._l1[key]  # L1 过期, 删掉

        # L2
        r = self._ensure_redis()
        if r is None:
            return None
        try:
            raw = r.get(_KEY_PREFIX + key)
            if raw is None:
                return None
            value = json.loads(raw)
            with self._lock:
                # 回填 L1(继承剩余 TTL)
                ttl = r.ttl(_KEY_PREFIX + key)
                expires_at = now + (ttl if ttl and ttl > 0 else 30.0)
                self._l1[key] = (expires_at, value)
            return value
        except Exception as e:
            logger.debug(f"[biz-cache] get({key}) failed: {e}")
            self._redis_down()
            return None

    def set_json(self, key: str, value: Any, ttl: int | None = None) -> bool:
        """写 L1 + 尽力写 L2(失败不影响主流程)。同时留 stale 快照。"""
        now = time.monotonic()
        expires_at = now + (ttl if ttl and ttl > 0 else 60.0)
        with self._lock:
            self._l1[key] = (expires_at, value)
            self._stale[key] = value
            while len(self._stale) > _STALE_MAX_KEYS:
                self._stale.pop(next(iter(self._stale)))

        r = self._ensure_redis()
        if r is None:
            return False
        try:
            payload = json.dumps(value, default=str, ensure_ascii=False)
            if ttl and ttl > 0:
                r.set(_KEY_PREFIX + key, payload, ex=ttl)
            else:
                r.set(_KEY_PREFIX + key, payload)
            return True
        except Exception as e:
            logger.debug(f"[biz-cache] set({key}) failed: {e}")
            self._redis_down()
            return False

    def get_or_fetch(
        self,
        key: str,
        ttl: int,
        fetch: Callable[[], Any],
    ) -> Any:
        """缓存穿透封装: 命中直接返回; miss 调 fetch() 回填后返回。

        注意: fetch 必须是同步可调用(在 to_thread / 同步上下文跑)。

        单飞去重(P1): 同 key 并发 miss 只有 leader 调 fetch, waiter 等待
        事件后重读缓存; leader 超时未回填 waiter 自己抓一次(极端边沿,
        保证进度, 不死锁)。

        stale 回退(OT-Phase5): miss 且 fetch 抛异常时, 有 stale 快照就
        返回旧值(记 warning, 不断档); 无快照才向上抛。调用方如需区分
        新旧, 请用短 TTL + asof 字段, 不要依赖本函数辨新旧。
        """
        cached = self.get_json(key)
        if cached is not None:
            return cached
        with self._lock:
            ev = self._inflight.get(key)
            if ev is None:
                ev = threading.Event()
                self._inflight[key] = ev
                leader = True
            else:
                leader = False
        if not leader:
            ev.wait(timeout=_SINGLEFLIGHT_WAIT_S)
            cached = self.get_json(key)
            if cached is not None:
                return cached
            try:
                value = fetch()
            except Exception:
                stale = self._stale_get(key)
                if stale is not None:
                    logger.warning(f"[biz-cache] {key} 回源失败, 返回 stale 快照")
                    return stale
                raise
            if value is not None:
                self.set_json(key, value, ttl=ttl)
            return value
        try:
            value = fetch()
        except Exception:
            with self._lock:
                self._inflight.pop(key, None)
            ev.set()
            stale = self._stale_get(key)
            if stale is not None:
                logger.warning(f"[biz-cache] {key} 回源失败, 返回 stale 快照")
                return stale
            raise
        with self._lock:
            self._inflight.pop(key, None)
        if value is not None:
            self.set_json(key, value, ttl=ttl)
        ev.set()
        return value

    def _stale_get(self, key: str) -> Optional[Any]:
        """取 stale 快照(无过期概念, 没有返回 None)。"""
        with self._lock:
            return self._stale.get(key)

    def delete(self, *keys: str) -> int:
        """删 L1 + L2 + stale 快照。"""
        with self._lock:
            for k in keys:
                self._l1.pop(k, None)
                self._stale.pop(k, None)
        r = self._ensure_redis()
        if r is None or not keys:
            return 0
        try:
            return int(r.delete(*[_KEY_PREFIX + k for k in keys]))
        except Exception:
            self._redis_down()
            return 0

    def clear(self) -> None:
        """清空业务缓存: L1 直接清; L2 用 SCAN 只删 biz:* 前缀(不 flushdb 误伤限流/stream)。"""
        with self._lock:
            self._l1.clear()
            self._stale.clear()
        r = self._ensure_redis()
        if r is None:
            return
        try:
            cursor = 0
            while True:
                cursor, keys = r.scan(cursor, match=_KEY_PREFIX + "*", count=500)
                if keys:
                    r.delete(*keys)
                if cursor == 0:
                    break
        except Exception:
            self._redis_down()

    def stats(self) -> dict:
        """给 /health 端点: 显示 L1 条目数 + Redis 连通状态。"""
        with self._lock:
            l1_size = len(self._l1)
        r = self._ensure_redis()
        redis_ok = r is not None
        return {
            "l1_entries": l1_size,
            "redis": "ok" if redis_ok else ("disabled" if not self._enabled else "down"),
            "redis_url": REDIS_URL if redis_ok else None,
        }


# 全局单例
biz_cache = BizCache.instance()

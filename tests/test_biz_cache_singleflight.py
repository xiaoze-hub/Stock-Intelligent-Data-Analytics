"""缓存单飞去重(P1): 同 key 并发 miss 只放 1 个上游请求。

用独立 BizCache 实例(不用全局单例), 不污染业务缓存。
"""

import threading
import time

import pytest

import src.web.cache.biz_cache as bc
from src.web.cache.biz_cache import BizCache


@pytest.fixture()
def cache():
    c = BizCache()
    c._enabled = False  # 纯 L1: 排除环境 Redis 干扰, 单飞是进程级语义, 跨进程靠 L2 命中
    return c


def test_concurrent_miss_single_fetch(cache):
    """10 线程同 key 并发 → fetch 只调 1 次, 人人有值。"""
    calls = []
    barrier = threading.Barrier(10)

    def fetch():
        calls.append(1)
        time.sleep(0.3)
        return {"v": 1}

    results, errors = [], []

    def worker():
        try:
            barrier.wait(timeout=5)
            results.append(cache.get_or_fetch("sf:key1", ttl=60, fetch=fetch))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert not errors
    assert len(results) == 10 and all(r == {"v": 1} for r in results)
    assert len(calls) == 1, f"惊群未压住: fetch 被调 {len(calls)} 次"


def test_leader_exception_wakes_waiters_no_deadlock(cache):
    """leader 抛异常 → waiter 被唤醒后自己抓一次, 无死锁, 异常向上传。"""
    calls = []

    def boom_once():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("上游炸了")
        return {"v": 2}

    barrier = threading.Barrier(3)
    outcomes = []

    def worker():
        try:
            barrier.wait(timeout=5)
            outcomes.append(("ok", cache.get_or_fetch("sf:key2", ttl=60, fetch=boom_once)))
        except RuntimeError:
            outcomes.append(("err", None))

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert all(not t.is_alive() for t in threads), "死锁!"
    assert ("err", None) in outcomes  # leader 的异常向上传
    assert ("ok", {"v": 2}) in outcomes  # waiter 接替抓到值
    assert 2 <= len(calls) <= 3  # waiter 唤醒后重读竞态, 至多各补抓一次


def test_slow_leader_waiter_fallback_progress(cache, monkeypatch):
    """leader 卡住超等待 → waiter 自己抓, 不无限等(0.2s 等待内返回)。"""
    monkeypatch.setattr(bc, "_SINGLEFLIGHT_WAIT_S", 0.2)
    started = threading.Event()

    def slow():
        started.set()
        time.sleep(2.0)
        return {"v": "slow"}

    def fast():
        return {"v": "fast"}

    leader_out: list = []
    t_leader = threading.Thread(
        target=lambda: leader_out.append(cache.get_or_fetch("sf:key3", ttl=60, fetch=slow))
    )
    t_leader.start()
    assert started.wait(timeout=5)

    t0 = time.monotonic()
    got = cache.get_or_fetch("sf:key3", ttl=60, fetch=fast)
    dt = time.monotonic() - t0
    assert got == {"v": "fast"}
    assert dt < 1.5, f"waiter 被卡住: {dt:.2f}s"
    t_leader.join(timeout=10)
    assert leader_out == [{"v": "slow"}]

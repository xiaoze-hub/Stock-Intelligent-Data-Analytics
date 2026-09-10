from marketdata.cache import TTLCache
from marketdata.defaults import InMemoryMetricsSink
from marketdata.engine import Engine
from marketdata.ports import SourceConfig
from marketdata.types import Request


class FakeVendor:
    def __init__(self, name, behavior):
        self.name, self.behavior, self.supports_markets = name, behavior, set()

    def fetch(self, symbols, config):
        if self.behavior == "raise":
            raise RuntimeError("boom")
        if self.behavior == "empty":
            return []
        return [{"symbol": symbols[0].code, "v": self.name}]


class FakeConfig:
    def __init__(self, srcs):
        self._srcs = srcs

    def sources_for(self, datatype, market):
        return list(self._srcs)


def _engine(vendors, srcs, metrics=None):
    return Engine(
        datatype="quote", vendors=vendors, config=FakeConfig(srcs),
        metrics=metrics or InMemoryMetricsSink(), cache=TTLCache(5.0), default_ttl=5.0,
    )


def _req():
    return Request(symbols=("600519",), market="CN")


def test_first_success_wins():
    e = _engine({"a": FakeVendor("a", "ok"), "b": FakeVendor("b", "ok")},
                [SourceConfig(vendor="a", priority=1), SourceConfig(vendor="b", priority=2)])
    r = e.fetch(_req())
    assert r.ok and r.vendor == "a" and r.data[0]["v"] == "a"


def test_failover_empty_then_raise_then_ok():
    e = _engine({"a": FakeVendor("a", "empty"), "b": FakeVendor("b", "raise"), "c": FakeVendor("c", "ok")},
                [SourceConfig(vendor="a", priority=1), SourceConfig(vendor="b", priority=2), SourceConfig(vendor="c", priority=3)])
    r = e.fetch(_req())
    assert r.ok and r.vendor == "c"


def test_all_fail_returns_not_ok():
    e = _engine({"a": FakeVendor("a", "empty")}, [SourceConfig(vendor="a", priority=1)])
    assert e.fetch(_req()).ok is False


def test_market_filter_skips_unsupported():
    v = FakeVendor("a", "ok")
    v.supports_markets = {"US"}   # 不支持 CN
    assert _engine({"a": v}, [SourceConfig(vendor="a", priority=1)]).fetch(_req()).ok is False


def test_cache_hit_skips_second_call():
    v = FakeVendor("a", "ok")
    calls = {"n": 0}
    inner = v.fetch
    v.fetch = lambda s, c: (calls.__setitem__("n", calls["n"] + 1), inner(s, c))[1]
    e = _engine({"a": v}, [SourceConfig(vendor="a", priority=1)])
    e.fetch(_req()); e.fetch(_req())
    assert calls["n"] == 1


def test_metrics_recorded():
    m = InMemoryMetricsSink()
    _engine({"a": FakeVendor("a", "ok")}, [SourceConfig(vendor="a", priority=1)], metrics=m).fetch(_req())
    assert m.snapshot()["a"]["success_rate"] == 1.0


def test_priority_resort_when_config_unsorted():
    e = _engine({"a": FakeVendor("a", "ok"), "b": FakeVendor("b", "ok")},
                [SourceConfig(vendor="b", priority=2), SourceConfig(vendor="a", priority=1)])
    r = e.fetch(_req())
    assert r.ok and r.vendor == "a"


def test_min_count_prefers_first_sufficient():
    e = _engine({"a": FakeVendor("a", "ok"), "b": FakeVendor("b", "ok")},
                [SourceConfig(vendor="a", priority=1), SourceConfig(vendor="b", priority=2)])
    # FakeVendor "ok" 返回 1 条;min_count=2 → a 不足 → 试 b → b 也 1 条 → 都不足 → 取最长(并列取先到的 a)
    r = e.fetch(Request(symbols=("600519",), market="CN"), min_count=2)
    assert r.ok and len(r.data) == 1  # 返回了(最长的),不因不足而失败


def test_min_count_returns_first_meeting_threshold():
    class MultiVendor:
        def __init__(self, name, n): self.name=name; self.supports_markets=set(); self._n=n
        def fetch(self, symbols, config): return [{"i": i} for i in range(self._n)]
    e = _engine({"a": MultiVendor("a", 1), "b": MultiVendor("b", 5)},
                [SourceConfig(vendor="a", priority=1), SourceConfig(vendor="b", priority=2)])
    r = e.fetch(Request(symbols=("x",), market="CN"), min_count=3)
    assert r.ok and r.vendor == "b" and len(r.data) == 5  # a 不足(1<3)→ b 足(5≥3)


def test_min_count_default_one_unchanged():
    e = _engine({"a": FakeVendor("a", "ok")}, [SourceConfig(vendor="a", priority=1)])
    r = e.fetch(Request(symbols=("x",), market="CN"))  # 默认 min_count=1
    assert r.ok and r.vendor == "a"


def test_engine_passes_request_limit_as_days_to_vendor():
    seen = {}
    class DaysVendor:
        name = "d"
        supports_markets: set = set()
        def fetch(self, symbols, config):
            seen["days"] = config.get("days")
            return [{"x": 1}]
    e = _engine({"d": DaysVendor()}, [SourceConfig(vendor="d", priority=1)])
    e.fetch(Request(symbols=("x",), market="CN", limit=250))
    assert seen["days"] == 250


def test_engine_passes_request_extra_to_vendor_config():
    # 守护测试:events 等 vendor 需要 req.extra(如 since_days)透传进 call_config。
    seen = {}
    class ExtraVendor:
        name = "e"
        supports_markets: set = set()
        def fetch(self, symbols, config):
            seen["since_days"] = config.get("since_days")
            seen["days"] = config.get("days")
            return [{"x": 1}]
    e = _engine({"e": ExtraVendor()}, [SourceConfig(vendor="e", priority=1)])
    e.fetch(Request(symbols=("x",), market="CN", limit=99, extra=(("since_days", 30),)))
    assert seen["since_days"] == 30
    assert seen["days"] == 99  # extra 透传不应破坏原有 days 注入


def test_source_stamped_on_dict_items():
    e = _engine({"a": FakeVendor("a", "ok")}, [SourceConfig(vendor="a", priority=1)])
    r = e.fetch(_req())
    assert r.ok and r.data[0]["source"] == "a"


def test_source_stamped_on_dataclass_and_not_overwritten():
    from marketdata.types import Quote

    class QuoteVendor:
        name = "tencent"
        supports_markets: set = set()

        def fetch(self, symbols, config):
            return [Quote(symbol="600519", market="CN", current_price=10.0),
                    Quote(symbol="600519", market="CN", current_price=10.0, source="manual")]

    e = _engine({"tencent": QuoteVendor()}, [SourceConfig(vendor="tencent", priority=1)])
    r = e.fetch(_req())
    assert r.ok
    assert r.data[0].source == "tencent"  # 空源回填
    assert r.data[1].source == "manual"  # 显式源不覆盖


# ──────────── OT-Phase4: vendor 失败冷却 + 礼貌并发 ────────────
class CountingVendor:
    """计数 vendor，可按调用次数切换行为。"""

    def __init__(self, name, fails_first_n=0, delay=0.0):
        self.name = name
        self.supports_markets = set()
        self.calls = 0
        self.fails_first_n = fails_first_n
        self.delay = delay
        self.inflight = 0
        self.max_inflight = 0

    def fetch(self, symbols, config):
        import threading as _t
        import time as _time

        self.calls += 1
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            if self.delay:
                _time.sleep(self.delay)
            if self.calls <= self.fails_first_n:
                raise RuntimeError("boom")
            return [{"symbol": symbols[0].code, "v": self.name}]
        finally:
            self.inflight -= 1


def _req_sym(sym):
    from marketdata.types import Request

    return Request(symbols=(sym,), market="CN")


def test_vendor_cooldown_skips_failed():
    """vendor 抛异常后 60s 内不再被打扰(第二次 fetch 零新调用)。"""
    v = CountingVendor("a", fails_first_n=99)
    e = _engine({"a": v}, [SourceConfig(vendor="a", priority=1)])
    assert e.fetch(_req()).ok is False
    assert v.calls == 1
    assert "a" in e._vendor_cooldown_until
    assert e.fetch(_req()).ok is False
    assert v.calls == 1, "冷却期内又打了失败源"


def test_cooldown_expires_then_retries():
    """冷却过期后恢复尝试。"""
    import time as _time

    v = CountingVendor("a", fails_first_n=1)
    e = _engine({"a": v}, [SourceConfig(vendor="a", priority=1)])
    assert e.fetch(_req()).ok is False
    e._vendor_cooldown_until["a"] = _time.monotonic() - 1
    r = e.fetch(_req())
    assert v.calls == 2 and r.ok and r.vendor == "a"


def test_concurrency_limit_per_vendor():
    """4 线程并发只压同一 vendor，峰值在限额内且全部成功。"""
    import threading as _t

    v = CountingVendor("a", delay=0.3)
    e = _engine({"a": v}, [SourceConfig(vendor="a", priority=1)])
    outs, threads = [], []

    def worker(i):
        outs.append(e.fetch(_req_sym(f"6005{i:02d}")))

    for i in range(4):
        t = _t.Thread(target=worker, args=(i,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert all(r.ok for r in outs)
    assert v.max_inflight <= 2, f"并发超限: {v.max_inflight}"

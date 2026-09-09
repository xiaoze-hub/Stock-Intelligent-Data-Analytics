"""机会扫描 K 线摘要并行+按日缓存(M4, 2026-09-10)。"""

import src.core.entry_candidates as ec


def _mock_summaries(monkeypatch, calls, fail_on=(), delay=0.0):
    import time

    def _fake(self, symbol):
        calls.append(symbol)
        if delay:
            time.sleep(delay)
        if symbol in fail_on:
            raise RuntimeError("boom")
        return {"symbol": symbol, "trend": "多头排列"}

    monkeypatch.setattr(ec.KlineCollector, "get_kline_summary", _fake)
    ec._KLINE_SUMMARY_CACHE.clear()


def test_fetch_mapping_and_failure_soft(monkeypatch):
    """映射正确; 异常只回 {}, 不抛。"""
    calls: list = []
    _mock_summaries(monkeypatch, calls, fail_on=("000002",))

    out = ec.fetch_kline_summaries(
        ["CN:000001", "CN:000002", "BADKEY"], snapshot_date="2026-09-10"
    )
    assert out["CN:000001"] == {"symbol": "000001", "trend": "多头排列"}
    assert out["CN:000002"] == {}
    assert out["BADKEY"] == {}
    assert sorted(calls) == ["000001", "000002"]


def test_fetch_date_cache_skips_network(monkeypatch):
    """同日第二次零请求; 换日重新抓。"""
    calls: list = []
    _mock_summaries(monkeypatch, calls)

    ec.fetch_kline_summaries(["CN:000001"], snapshot_date="2026-09-10")
    assert calls == ["000001"]
    ec.fetch_kline_summaries(["CN:000001"], snapshot_date="2026-09-10")
    assert calls == ["000001"]
    ec.fetch_kline_summaries(["CN:000001"], snapshot_date="2026-09-11")
    assert calls == ["000001", "000001"]


def test_fetch_parallel_faster_than_serial(monkeypatch):
    """8 只 × 0.2s: 并行 < 1s(串行要 1.6s+)。"""
    import time

    calls: list = []
    _mock_summaries(monkeypatch, calls, delay=0.2)
    keys = [f"CN:00000{i}" for i in range(1, 9)]

    t0 = time.monotonic()
    out = ec.fetch_kline_summaries(keys, snapshot_date="2026-09-10", max_workers=8)
    dt = time.monotonic() - t0
    assert len(out) == 8 and all(v.get("trend") == "多头排列" for v in out.values())
    assert dt < 1.0, f"并行未生效: {dt:.2f}s"

"""策略评估 PIT(无未来函数)测试(M2, 2026-09-10)。

用冻结时钟 asof + 全 mock K线验证: outcome 只取决于 [snap, target] 窗口数据,
target 之后的数据无论怎么变都不影响已评估结果; 基准=次日可执行开盘。
跑完清理自建行, 不污染测试库。
"""

from datetime import date

import pytest

from src.collectors.kline_collector import KlineData
from src.web.database import SessionLocal
from src.web.models import StrategyOutcome, StrategySignalRun


def _bars():
    rows = [
        ("2026-01-02", 10.0, 10.0),
        ("2026-01-05", 10.0, 10.2),
        ("2026-01-06", 10.3, 10.5),
        ("2026-01-07", 10.6, 10.4),
        ("2026-01-08", 10.4, 10.9),
        ("2026-01-09", 999.0, 999.0),  # target 窗口之后的未来尖峰: 必须零影响
    ]
    return [
        KlineData(date=d, open=o, close=c, high=max(o, c), low=min(o, c), volume=1e6)
        for d, o, c in rows
    ]


@pytest.fixture()
def pit_signal(monkeypatch):
    from src.collectors import kline_collector as kc

    monkeypatch.setattr(
        kc.KlineCollector, "get_klines", lambda self, symbol, days=60: _bars()
    )
    db = SessionLocal()
    sig = StrategySignalRun(
        snapshot_date="2026-01-05",
        stock_symbol="PIT001",
        stock_market="CN",
        strategy_code="pit_test_strategy",
        status="active",
        action="buy",
        rank_score=80.0,
        entry_low=9.8,
        entry_high=10.2,
    )
    db.add(sig)
    db.commit()
    sig_id = sig.id
    try:
        yield sig_id
    finally:
        d2 = SessionLocal()
        try:
            d2.query(StrategyOutcome).filter(
                StrategyOutcome.signal_run_id == sig_id
            ).delete()
            d2.query(StrategySignalRun).filter(
                StrategySignalRun.id == sig_id
            ).delete()
            d2.commit()
        finally:
            d2.close()
        db.close()


def test_evaluate_asof_pit_no_future_leak(pit_signal):
    """asof=01-08,horizon=1: target=01-06, 基准=01-06开, 01-09的999零影响。"""
    from src.core.strategy_engine import evaluate_strategy_outcomes

    stats = evaluate_strategy_outcomes(horizons=(1,), snapshot_days=30, asof=date(2026, 1, 8))
    assert stats["evaluated"] >= 1
    db = SessionLocal()
    try:
        row = (
            db.query(StrategyOutcome)
            .filter(
                StrategyOutcome.signal_run_id == pit_signal,
                StrategyOutcome.horizon_days == 1,
            )
            .first()
        )
        assert row is not None
        assert row.target_date == "2026-01-06"
        assert abs(row.outcome_price - 10.5) < 1e-9  # 01-06收, 不是999
        assert abs(row.base_price - 10.3) < 1e-9  # 快照后首日开(可执行)
        assert abs(row.outcome_return_pct - (10.5 - 10.3) / 10.3 * 100) < 1e-6
        assert (row.meta or {}).get("base_kind") == "next_open"
    finally:
        db.close()


def test_evaluate_asof_not_due_skipped(pit_signal):
    """asof 早于 target: 记 skipped_not_due, 不写 outcome。"""
    from src.core.strategy_engine import evaluate_strategy_outcomes

    stats = evaluate_strategy_outcomes(horizons=(10,), snapshot_days=30, asof=date(2026, 1, 6))
    assert stats["skipped_not_due"] >= 1
    db = SessionLocal()
    try:
        n = (
            db.query(StrategyOutcome)
            .filter(
                StrategyOutcome.signal_run_id == pit_signal,
                StrategyOutcome.horizon_days == 10,
            )
            .count()
        )
        assert n == 0
    finally:
        db.close()

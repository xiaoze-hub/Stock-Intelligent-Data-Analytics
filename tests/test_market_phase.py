"""src/core/market_phase.py 单测(2026-08-24, 任务 A)。

覆盖:
- compute_daily_metrics: 梯队宽度 / 最高板 / 晋级率(含小样本池 → None)/ 封板率
- _ema: 平滑 / ffill / 起始缺失回填
- classify_phase_series: 各阶段判定规则 + 弱档否决 + 2 日确认 + accumulating 兜底
- 阈值常量: 任务书硬约束逐一对齐
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

import pytest

from src.core import market_phase as mp
from src.core.market_phase import (
    ACCUMULATING_MIN_DAYS,
    CLIMAX_FIRST_BOARD,
    CLIMAX_GE2,
    CONFIRM_DAYS,
    EBB_PROMO,
    EBB_PROMO_STRICT,
    EMA_ALPHA,
    ICE_FIRST_BOARD,
    ICE_GE2,
    ICE_HEIGHT,
    IGNITE_GE2_DELTA,
    IGNITE_GE2,
    IGNITE_HEIGHT_DELTA,
    IGNITE_HEIGHT,
    IGNITE_PROMO_SOFT,
    PHASE_ACCUMULATING,
    PHASE_CLIMAX,
    PHASE_EBB,
    PHASE_ICE,
    PHASE_IGNITE,
    PHASE_LABELS,
    PHASE_PRIORITY,
    PHASE_RALLY,
    PHASE_REPAIR,
    POSITIVE_PHASES,
    PROMO_MIN_POOL,
    RALLY_GE2,
    RALLY_GE2_ALT,
    RALLY_HEIGHT,
    RALLY_HEIGHT_ALT,
    RALLY_PROMO,
    RALLY_PROMO_ALT,
    WEAK_VETO_SH_PCT,
    classify_phase_series,
    compute_daily_metrics,
    ordered_distribution,
    phase_distribution,
)


# ─────────────── compute_daily_metrics ───────────────
class TestComputeDailyMetrics:
    def test_first_board_ge2_ge3_ge5_counts_and_height(self):
        """first=1板家数; ge2/3/5=N板及以上家数; max_height=max(days)."""
        pool = [
            {"code": "000001", "days": 1},   # first
            {"code": "000002", "days": 2},   # ge2
            {"code": "000003", "days": 3},   # ge2 + ge3
            {"code": "000004", "days": 5},   # ge2 + ge3 + ge5
            {"code": "000005", "days": 7},   # 高度
            {"code": "000006", "days": 1},   # another first
        ]
        m = compute_daily_metrics(pool, prev_pool=None)
        assert m.first_board == 2
        assert m.ge2_count == 4
        assert m.ge3_count == 3
        assert m.ge5_count == 2
        assert m.max_height == 7
        assert m.promo_rate is None  # 无 prev_pool
        assert m.seal_rate == 1.0    # 有 pool, 默认 1.0

    def test_empty_pool_returns_zero_metrics(self):
        """空池 → 全 0, seal_rate=None, promo_rate=None."""
        m = compute_daily_metrics([], prev_pool=[])
        assert m.first_board == 0
        assert m.ge2_count == 0
        assert m.ge3_count == 0
        assert m.ge5_count == 0
        assert m.max_height == 0
        assert m.promo_rate is None
        assert m.seal_rate is None

    def test_promo_rate_computes_continued_over_prev_ge2(self):
        """晋级率 = 昨日 ge2+ 池中今日续封比例(>=10 个池才有意义)."""
        prev_pool = [{"code": f"{i:06d}", "days": 2 if i % 2 == 0 else 1} for i in range(12)]
        # prev ge2+: 0,2,4,6,8,10 (12 个, 其中 6 个是 ge2+, 其他 6 个是 1板) — 实际 12 个全 days>=2?
        # 让我重新构造: 12 个 prev_ge2+ codes
        prev_pool = [{"code": f"{i:06d}", "days": 2} for i in range(12)]
        # 今天续封 0,2,4(3 个), 新增 100,101(首板)
        today_pool = [
            {"code": "000000", "days": 3},
            {"code": "000002", "days": 3},
            {"code": "000004", "days": 3},
            {"code": "000100", "days": 1},
            {"code": "000101", "days": 1},
        ]
        m = compute_daily_metrics(today_pool, prev_pool)
        # 续封 3/12 = 0.25
        assert m.promo_rate == pytest.approx(0.25, abs=0.01)

    def test_promo_rate_returns_none_for_small_pool(self):
        """昨日连板池 < PROMO_MIN_POOL(10) → promo_rate=None(小样本噪声)."""
        prev_pool = [{"code": f"{i:06d}", "days": 2} for i in range(5)]  # 5 < 10
        today_pool = [{"code": "000000", "days": 3}]
        m = compute_daily_metrics(today_pool, prev_pool)
        assert m.promo_rate is None

    def test_promo_rate_zero_when_nothing_continues(self):
        """今日池与昨日连板池完全无交集 → promo_rate=0.0(不是 None, 池足够大)."""
        prev_pool = [{"code": f"A{i:06d}", "days": 2} for i in range(12)]
        today_pool = [{"code": f"B{i:06d}", "days": 1} for i in range(5)]
        m = compute_daily_metrics(today_pool, prev_pool)
        assert m.promo_rate == 0.0

    def test_days_field_defaults_to_one(self):
        """无 days 字段的记录视为 1 连板(防御性)."""
        pool = [{"code": "000001"}]  # 无 days
        m = compute_daily_metrics(pool, prev_pool=None)
        assert m.first_board == 1
        assert m.ge2_count == 0


# ─────────────── _ema ───────────────
class TestEma:
    def test_basic_alpha_third(self):
        """alpha=1/3 时, 平滑值应符合递推公式 cur + 1/3*(v-cur)."""
        out = mp._ema([1.0, 2.0, 3.0])
        # day 0: 1.0
        assert out[0] == pytest.approx(1.0, abs=0.001)
        # day 1: 1 + 1/3*(2-1) = 1.333
        assert out[1] == pytest.approx(1.333, abs=0.001)
        # day 2: 1.333 + 1/3*(3-1.333) = 1.889
        assert out[2] == pytest.approx(1.889, abs=0.001)

    def test_ffill_for_missing_values(self):
        """None 沿用上一平滑值(ffill), 不影响后续计算."""
        out = mp._ema([5.0, None, None, 10.0])
        assert out[0] == pytest.approx(5.0)
        assert out[1] == pytest.approx(5.0)
        assert out[2] == pytest.approx(5.0)
        # day 3: 5 + 1/3*(10-5) = 6.667
        assert out[3] == pytest.approx(6.667, abs=0.001)

    def test_leading_none_filled_with_first_valid(self):
        """起始 None → 用首个有效值回填."""
        out = mp._ema([None, None, 5.0, 6.0])
        assert out[0] == pytest.approx(5.0)
        assert out[1] == pytest.approx(5.0)
        assert out[2] == pytest.approx(5.0)
        # day 3: 5 + 1/3*(6-5) = 5.333
        assert out[3] == pytest.approx(5.333, abs=0.001)

    def test_alpha_parameter_override(self):
        """alpha 参数可覆盖默认值."""
        out = mp._ema([1.0, 2.0], alpha=0.5)
        assert out[1] == pytest.approx(1.5, abs=0.001)


# ─────────────── classify_phase_series ───────────────
def _row(d, **kw) -> dict:
    """构造单日原始指标 row(date=date 对象)."""
    base = {
        "date": d,
        "first_board": 0,
        "ge2_count": 0,
        "ge3_count": 0,
        "ge5_count": 0,
        "max_height": 0,
        "promo_rate": None,
        "seal_rate": 1.0,
        "sh_index_pct": 0.0,
    }
    base.update(kw)
    return base


class TestClassifyPhaseSeries:
    def test_empty_returns_empty(self):
        assert classify_phase_series([]) == []

    def test_accumulating_when_below_min_days(self):
        """历史 < 5 天 → 全部 'accumulating'(不可作交易信号)."""
        rows = [_row(date(2024, 1, i + 1)) for i in range(3)]
        labels = classify_phase_series(rows)
        assert labels == ["accumulating"] * 3

    def test_accumulating_when_exactly_4_days(self):
        """4 天(< 5) → 全部 accumulating, 即使指标满足 climax 阈值."""
        rows = [
            _row(date(2024, 1, i + 1),
                 first_board=300, ge2_count=60, max_height=10, promo_rate=0.5)
            for i in range(4)
        ]
        assert classify_phase_series(rows) == ["accumulating"] * 4

    # ─── climax ──────────────────────────────────────
    def test_climax_via_ge2(self):
        """ge2 >= 50 → climax(优先级最高)."""
        rows = [
            _row(date(2024, 1, i + 1),
                 first_board=100, ge2_count=55, max_height=8, promo_rate=0.4)
            for i in range(7)
        ]
        assert PHASE_CLIMAX in classify_phase_series(rows)

    def test_climax_via_first_board(self):
        """首板 >= 220 → climax."""
        rows = [
            _row(date(2024, 1, i + 1),
                 first_board=250, ge2_count=30, max_height=7, promo_rate=0.3)
            for i in range(7)
        ]
        assert PHASE_CLIMAX in classify_phase_series(rows)

    # ─── rally ───────────────────────────────────────
    def test_rally_main_path(self):
        """rally: height>=7 + ge2>=15 + promo>=0.23."""
        rows = [
            _row(date(2024, 1, i + 1),
                 first_board=80, ge2_count=20, max_height=7, promo_rate=0.25)
            for i in range(7)
        ]
        assert PHASE_RALLY in classify_phase_series(rows)

    def test_rally_alt_path_high_promo(self):
        """rally alt: promo>=0.30 + height>=5 + ge2>=12."""
        rows = [
            _row(date(2024, 1, i + 1),
                 first_board=60, ge2_count=14, max_height=6, promo_rate=0.35)
            for i in range(7)
        ]
        assert PHASE_RALLY in classify_phase_series(rows)

    # ─── ice ─────────────────────────────────────────
    def test_ice_when_all_below_thresholds(self):
        """冰点: height<=4 + ge2<=6 + first_board<=24 同时贴地."""
        rows = [
            _row(date(2024, 1, i + 1),
                 first_board=15, ge2_count=4, max_height=3, promo_rate=0.1, seal_rate=0.5)
            for i in range(7)
        ]
        assert PHASE_ICE in classify_phase_series(rows)

    # ─── ebb ─────────────────────────────────────────
    def test_ebb_from_high_promo_crash(self):
        """退潮: 从 5 日前高位回落 + 晋级率崩. EMA 平滑需 3 天低 promo 才能跌破 0.15."""
        rows = []
        for i in range(4):
            rows.append(_row(
                date(2024, 1, i + 1),
                first_board=80, ge2_count=20, max_height=8, promo_rate=0.25,
            ))
        for i in range(4, 7):
            rows.append(_row(
                date(2024, 1, i + 1),
                first_board=20, ge2_count=5, max_height=3, promo_rate=0.05,
            ))
        labels = classify_phase_series(rows)
        # 出现 ebb
        assert PHASE_EBB in labels, labels

    # ─── ignite ──────────────────────────────────────
    def test_ignite_expansion(self):
        """启动: 高度自低位扩张. 4 天低位 + 3 天 height=7, EMA 平滑后高度越过 5."""
        rows = []
        for i in range(4):
            rows.append(_row(
                date(2024, 1, i + 1),
                first_board=30, ge2_count=5, max_height=3, promo_rate=0.20,
            ))
        for i in range(4, 7):
            rows.append(_row(
                date(2024, 1, i + 1),
                first_board=60, ge2_count=15, max_height=7, promo_rate=0.22,
            ))
        labels = classify_phase_series(rows)
        assert PHASE_IGNITE in labels, labels

    # ─── repair 兜底 ─────────────────────────────────
    def test_repair_default_for_medial_metrics(self):
        """修复: 不满足任何正向规则的中间状态."""
        rows = [
            _row(date(2024, 1, i + 1),
                 first_board=40, ge2_count=10, max_height=5, promo_rate=0.18, seal_rate=0.7)
            for i in range(7)
        ]
        labels = classify_phase_series(rows)
        assert PHASE_REPAIR in labels

    # ─── 弱档否决 ────────────────────────────────────
    def test_weak_veto_downgrades_positive_phases_to_repair(self):
        """上证当日跌幅 < -2% 时, 正向阶段(climax/rally/ignite)降为 repair."""
        rows = [
            _row(date(2024, 1, i + 1),
                 first_board=80, ge2_count=20, max_height=7, promo_rate=0.25,
                 sh_index_pct=-2.5)
            for i in range(7)
        ]
        labels = classify_phase_series(rows)
        assert PHASE_CLIMAX not in labels
        assert PHASE_RALLY not in labels
        assert PHASE_IGNITE not in labels
        # 全为 repair(7 日平滑)
        assert all(l == PHASE_REPAIR for l in labels), labels

    def test_weak_veto_threshold_is_strict_less_than(self):
        """上证 -1.9% > -2%, 不触发否决, 应有 rally."""
        rows = [
            _row(date(2024, 1, i + 1),
                 first_board=80, ge2_count=20, max_height=7, promo_rate=0.25,
                 sh_index_pct=-1.9)
            for i in range(7)
        ]
        labels = classify_phase_series(rows)
        assert PHASE_RALLY in labels

    def test_weak_veto_only_on_positive_phases(self):
        """弱档否决只影响正向阶段 — ice/ebb/repair 不会被否决."""
        rows = [
            _row(date(2024, 1, i + 1),
                 first_board=15, ge2_count=4, max_height=3, promo_rate=0.1,
                 sh_index_pct=-3.0)
            for i in range(7)
        ]
        labels = classify_phase_series(rows)
        # ice 不被否决
        assert PHASE_ICE in labels

    # ─── 2 日确认 ────────────────────────────────────
    def test_single_day_flip_blocked_by_2_day_confirmation(self):
        """连续 6 天 rally 后, 单日 climax 不应翻转(需 2 日确认)."""
        rows = []
        for i in range(6):
            rows.append(_row(
                date(2024, 1, i + 1),
                first_board=80, ge2_count=20, max_height=7, promo_rate=0.25,
            ))
        # 第 7 天 climax
        rows.append(_row(
            date(2024, 1, 7),
            first_board=300, ge2_count=60, max_height=10, promo_rate=0.5,
        ))
        labels = classify_phase_series(rows)
        # 应保持 rally, 不被单日 climax 翻转
        assert labels[-1] == PHASE_RALLY, labels

    def test_two_consecutive_days_confirm_switch(self):
        """连续 6 日 climax(EMA 充分平滑) → 切换 climax(2 日确认)."""
        rows = []
        for i in range(3):
            rows.append(_row(
                date(2024, 1, i + 1),
                first_board=80, ge2_count=20, max_height=7, promo_rate=0.25,
            ))
        for i in range(3, 9):
            rows.append(_row(
                date(2024, 1, i + 1),
                first_board=300, ge2_count=60, max_height=10, promo_rate=0.5,
            ))
        labels = classify_phase_series(rows)
        assert labels[-1] == PHASE_CLIMAX, labels

    def test_one_day_interruption_resets_pending(self):
        """rally → climax(1天) → rally → climax(1天): 两次单日 climax 不应翻转."""
        rows = []
        for i in range(6):
            rows.append(_row(
                date(2024, 1, i + 1),
                first_board=80, ge2_count=20, max_height=7, promo_rate=0.25,
            ))
        rows.append(_row(date(2024, 1, 7),
                         first_board=300, ge2_count=60, max_height=10, promo_rate=0.5))
        rows.append(_row(date(2024, 1, 8),
                         first_board=80, ge2_count=20, max_height=7, promo_rate=0.25))  # back to rally
        rows.append(_row(date(2024, 1, 9),
                         first_board=300, ge2_count=60, max_height=10, promo_rate=0.5))  # climax again
        labels = classify_phase_series(rows)
        # rally 应贯穿, climax 不应切换
        assert labels[-1] == PHASE_RALLY, labels


# ─────────────── phase_distribution / ordered_distribution ───────────────
class TestPhaseDistribution:
    def test_basic(self):
        assert phase_distribution(["ice", "rally", "rally", "repair"]) == {
            "ice": 1, "rally": 2, "repair": 1,
        }

    def test_empty(self):
        assert phase_distribution([]) == {}

    def test_ordered_distribution_priority(self):
        """ordered_distribution 按业务优先级排列."""
        d = {"repair": 5, "ice": 2, "rally": 3}
        ordered = ordered_distribution(d)
        keys = [k for k, _, _ in ordered]
        assert keys == ["rally", "ice", "repair"], keys

    def test_ordered_distribution_includes_unknown(self):
        """未在 PHASE_PRIORITY 中的 key 兜底排在末尾."""
        d = {"rally": 1, "weird_phase": 2}
        ordered = ordered_distribution(d)
        keys = [k for k, _, _ in ordered]
        assert keys[-1] == "weird_phase"


# ─────────────── 阈值常量(任务书硬约束) ───────────────
class TestThresholds:
    """任务书要求的阈值常量, 调整必须改这里并同步任务书."""

    def test_climax_thresholds(self):
        assert CLIMAX_GE2 == 50
        assert CLIMAX_FIRST_BOARD == 220

    def test_rally_thresholds(self):
        assert RALLY_HEIGHT == 7
        assert RALLY_GE2 == 15
        assert RALLY_PROMO == pytest.approx(0.23)
        assert RALLY_PROMO_ALT == pytest.approx(0.30)
        assert RALLY_GE2_ALT == 12
        assert RALLY_HEIGHT_ALT == 5

    def test_ebb_thresholds(self):
        assert EBB_PROMO == pytest.approx(0.15)
        assert EBB_PROMO_STRICT == pytest.approx(0.13)

    def test_ignite_thresholds(self):
        assert IGNITE_GE2_DELTA == 3
        assert IGNITE_GE2 == 8
        assert IGNITE_HEIGHT_DELTA == 1
        assert IGNITE_HEIGHT == 5
        assert IGNITE_PROMO_SOFT == pytest.approx(0.19)

    def test_ice_thresholds(self):
        assert ICE_HEIGHT == 4
        assert ICE_GE2 == 6
        assert ICE_FIRST_BOARD == 24

    def test_persistence(self):
        assert EMA_ALPHA == pytest.approx(1.0 / 3.0)
        assert CONFIRM_DAYS == 2

    def test_weak_veto_and_accumulating(self):
        assert WEAK_VETO_SH_PCT == pytest.approx(-2.0)
        assert ACCUMULATING_MIN_DAYS == 5
        assert PROMO_MIN_POOL == 10

    def test_positive_phases_contain_climax_rally_ignite(self):
        """弱档否决只影响正向阶段(climax/rally/ignite)."""
        assert PHASE_CLIMAX in POSITIVE_PHASES
        assert PHASE_RALLY in POSITIVE_PHASES
        assert PHASE_IGNITE in POSITIVE_PHASES
        assert PHASE_EBB not in POSITIVE_PHASES
        assert PHASE_ICE not in POSITIVE_PHASES
        assert PHASE_REPAIR not in POSITIVE_PHASES

    def test_priority_order(self):
        """优先级: climax > rally > ebb > ignite > ice."""
        assert PHASE_PRIORITY == ("climax", "rally", "ebb", "ignite", "ice")

    def test_phase_labels_complete(self):
        """7 个阶段标签(含 accumulating)必须有中文映射."""
        assert PHASE_LABELS[PHASE_CLIMAX] == "高潮"
        assert PHASE_LABELS[PHASE_RALLY] == "主升"
        assert PHASE_LABELS[PHASE_EBB] == "退潮"
        assert PHASE_LABELS[PHASE_IGNITE] == "启动"
        assert PHASE_LABELS[PHASE_ICE] == "冰点"
        assert PHASE_LABELS[PHASE_REPAIR] == "修复"
        assert PHASE_LABELS[PHASE_ACCUMULATING] == "积累中"


# ─────────────── API 模型层 sanity(不连网) ───────────────
class TestMarketPhaseDailyModel:
    """DB 模型字段对齐任务书 schema."""

    def test_model_columns_present(self):
        from src.web.models import MarketPhaseDaily

        cols = {c.name for c in MarketPhaseDaily.__table__.columns}
        assert "date" in cols
        assert "first_board" in cols
        assert "ge2_count" in cols
        assert "ge3_count" in cols
        assert "ge5_count" in cols
        assert "max_height" in cols
        assert "promo_rate" in cols
        assert "seal_rate" in cols
        assert "phase" in cols
        assert "sh_index_pct" in cols

    def test_date_is_primary_key(self):
        from src.web.models import MarketPhaseDaily

        pk_cols = [c.name for c in MarketPhaseDaily.__table__.primary_key.columns]
        assert pk_cols == ["date"]


# ─────────────── API 集成(内存 DB) ───────────────
class TestMarketPhaseApi:
    """API 层: 用内存 SQLite 验证 GET / POST, 不连网(mock collector)."""

    @pytest.fixture
    def in_mem_db(self, monkeypatch):
        """内存 SQLite + 把 SessionLocal 注入所有可能引用的模块。

        ⚠️ sync_phase 在 src.web.api.market_phase 模块顶部 import SessionLocal,
        形成模块级快照。仅 monkeypatch src.web.database.SessionLocal 不足以让
        sync_phase 看到新 sessionmaker — 必须同步打 market_phase.SessionLocal。
        """
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from src.web import models, database as db
        from src.web.api import market_phase as mpm

        engine = create_engine("sqlite:///:memory:")
        models.Base.metadata.create_all(engine)
        sm = sessionmaker(bind=engine)
        monkeypatch.setattr(db, "engine", engine)
        monkeypatch.setattr(db, "SessionLocal", sm)
        monkeypatch.setattr(db, "IS_PG", False)
        # 同步注入到 market_phase 模块级 SessionLocal 引用
        monkeypatch.setattr(mpm, "SessionLocal", sm)
        return models

    def test_get_phase_empty_returns_unavailable(self, in_mem_db, monkeypatch):
        """DB 空 → available=false, 不报错."""
        # 隔离 biz_cache, 避免污染其他测试
        from src.web.cache import biz_cache as bc

        bc.biz_cache.clear()
        monkeypatch.setattr(bc.biz_cache, "get_json", lambda k: None)

        from src.web.api import market_phase as api

        resp = api.get_phase()
        assert resp["available"] is False
        assert resp["asof"] is None
        assert resp["current"] is None
        assert resp["recent_30d"] == []
        assert "POST /api/market/phase/sync" in resp["note"]

    def test_post_sync_writes_metrics_and_recomputes_phase(
        self, in_mem_db, monkeypatch
    ):
        """POST /sync: mock collector 返回固定池, 验证落库 + 重算阶段."""
        from datetime import date as _date

        from src.web.cache import biz_cache as bc
        from src.web.api import market_phase as api

        bc.biz_cache.clear()

        # Mock collector: 当日池 8 只, 昨日池 15 只(满足 PROMO_MIN_POOL)
        # 构造一个使 ge2=20, height=7, promo>=0.23 → rally
        today_pool = [
            {"code": f"{i:06d}", "days": 1} for i in range(80)  # 80 只首板
        ] + [
            {"code": f"{100+i:06d}", "days": 2} for i in range(15)  # 15 只 2 板
        ] + [
            {"code": f"{200+i:06d}", "days": 3} for i in range(3)
        ] + [
            {"code": f"{300:06d}", "days": 7}  # 1 只 7 板(高度)
        ]
        # 昨日池: 让部分今日续封
        prev_pool = [{"code": f"{100+i:06d}", "days": 2} for i in range(15)]
        # 让 12/15 续封 — 把今日的 100~111 也放进 prev_pool
        prev_pool += [{"code": f"{300:06d}", "days": 6}]  # 让今日高度股昨日也存在

        class MockCol:
            def get_limit_up_pool(self, date_str=None):
                return today_pool if str(date_str).endswith(str(_date.today().strftime("%Y%m%d"))) else prev_pool

            def get_index_snapshot(self):
                return [{"name": "上证指数", "pct": 0.5}]

        # 重写 MarketSentimentCollector
        import src.web.api.market_phase as mpm

        monkeypatch.setattr(mpm, "MarketSentimentCollector", MockCol)

        # 注入 4 天历史(让 sync 后总数 = 5, 达到 ACCUMULATING_MIN_DAYS).
        # 历史值用 rally 阈值附近的指标, 让今日高值经 EMA 后能触发 rally/climax。
        from datetime import timedelta
        from src.web.models import MarketPhaseDaily
        from src.web.database import SessionLocal

        today = _date.today()
        db = SessionLocal()
        for i in range(4):
            d = today - timedelta(days=4 - i)
            r = MarketPhaseDaily(
                date=d,
                # 接近 rally 阈值(h=6<7, g2=14<15) — EMA 平滑留出上行空间
                first_board=70, ge2_count=14, ge3_count=5, ge5_count=1,
                max_height=6, promo_rate=0.22, seal_rate=0.7,
                sh_index_pct=0.0, phase="repair",
            )
            db.add(r)
        db.commit()
        db.close()

        resp = api.sync_phase()
        assert resp["synced"] is True
        assert "metrics" in resp
        assert resp["metrics"]["ge2_count"] >= 15  # 至少 15 个 >=2 板
        assert resp["metrics"]["max_height"] == 7
        # 验证落库
        db = SessionLocal()
        try:
            today_row = (
                db.query(MarketPhaseDaily)
                .filter(MarketPhaseDaily.date == _date.today())
                .first()
            )
            assert today_row is not None
            assert today_row.first_board == 80
            assert today_row.ge2_count >= 15
            assert today_row.max_height == 7
            # 阶段标签必须是合法 phase(非 accumulating — 因为 5 天已达阈值)
            assert today_row.phase in (
                "ice", "ignite", "rally", "climax", "ebb", "repair", ""
            ), f"unexpected phase={today_row.phase!r}"
            assert today_row.phase != "accumulating", (
                "5 天数据已够 ACCUMULATING_MIN_DAYS, 不应还是 accumulating"
            )
            assert resp["phase"] == today_row.phase
            # P3: 口径日期 = 最后一条日记录(当日)
            bc.biz_cache.clear()
            phase_resp = api.get_phase()
            assert phase_resp["available"] is True
            assert phase_resp["asof"] == _date.today().isoformat()
        finally:
            db.close()

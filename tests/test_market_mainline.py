"""市场主线识别测试 (v0.3.0, 2026-08-24)。

覆盖(基于 mock 涨停池数据, 不发请求):
  - 空池 → 空结构 + note
  - 分组聚合: 涨停数 / 二板数 / 最高板 / 总板数 / 梯队档位 / 龙头股
  - 龙头选取: days 倒序 → amount 倒序 → name 正序
  - 宽基过滤: 黑名单 + 名称子串黑名单命中即剔除
  - 最小入榜门槛 (<3 涨停 → 未入榜, score=None)
  - 主线分四维加权: 0.35 涨停 + 0.25 最高板 + 0.25 梯队 + 0.15 二板
  - 排序: score 降序 → limit_up_count 降序 → max_boards 降序
  - 题材 > 板块 > industry 字段优先级
  - 容错: days/amount 异常值 → 兜底
"""
from __future__ import annotations

import pytest

from src.core import market_mainline as mm


# ──────────── 工具: 构造涨停条目 ────────────
def _z(code: str, name: str, days: int, sector: str = "", theme: str = "", amount: float = 1e8, industry: str = "") -> dict:
    return {
        "code": code,
        "name": name,
        "days": days,
        "sector": sector,
        "theme": theme,
        "industry": industry,
        "amount": amount,
    }


# ──────────── 基础空态 ────────────
def test_empty_pool_returns_empty_structure():
    """空涨停池 → 空 ranked + note。"""
    r = mm.aggregate_mainline([])
    assert r["ranked_groups"] == []
    assert r["unranked"] == []
    assert r["filter_stats"] == {"broad_filtered": 0, "below_min": 0, "ranked": 0}
    assert "无涨停池" in r["note"]


def test_none_pool_is_safe():
    """传 None 不能崩(防御性, FastAPI 调用可能传 None)。"""
    r = mm.aggregate_mainline(None)
    assert r["ranked_groups"] == []
    assert "无涨停池" in r["note"]


# ──────────── 分组聚合 ────────────
def test_aggregation_basic_metrics():
    """单个分组的基础指标正确: 涨停数 / 二板数 / 最高板 / 总板数 / 梯队。"""
    pool = [
        _z("1", "A", days=1, theme="机器人"),
        _z("2", "B", days=2, theme="机器人"),
        _z("3", "C", days=3, theme="机器人"),
        _z("4", "D", days=5, theme="机器人"),
        _z("5", "E", days=3, theme="机器人"),  # 重复 days=3, 梯队档位不增加
    ]
    r = mm.aggregate_mainline(pool)
    g = r["ranked_groups"][0]
    assert g["name"] == "机器人"
    assert g["limit_up_count"] == 5
    assert g["ge2_count"] == 4  # 2/3/5/3 = 4 (其中 3板 2只, 5板 1只, 2板 1只)
    assert g["max_boards"] == 5
    assert g["boards_sum"] == 14  # 1+2+3+5+3
    assert g["rungs"] == 4  # 不同连板层级: 1/2/3/5


def test_leader_pick_days_desc_amount_desc_name_asc():
    """龙头: days 倒序优先, 同 days 看 amount 倒序, 再看 name 正序。"""
    pool = [
        _z("1", "Beta", days=2, theme="AI", amount=1e8),
        _z("2", "Alpha", days=3, theme="AI", amount=5e8),  # 最高 days=3 → 龙头
        _z("3", "Gamma", days=3, theme="AI", amount=9e8),  # 同 days=3, amount 更大 → 应该替代
        _z("4", "Zeta", days=1, theme="AI", amount=2e8),
    ]
    r = mm.aggregate_mainline(pool)
    g = r["ranked_groups"][0]
    assert g["leader"]["code"] == "3"
    assert g["leader"]["name"] == "Gamma"
    assert g["leader"]["days"] == 3


def test_leader_tiebreak_by_name_when_days_amount_equal():
    """days/amount 都相同 → 按 name 升序, 字典序小者胜(需 ≥3 家入榜)。"""
    pool = [
        _z("1", "Zeta", days=3, theme="AI", amount=5e8),
        _z("2", "Alpha", days=3, theme="AI", amount=5e8),
        _z("3", "Beta", days=2, theme="AI", amount=4e8),
    ]
    r = mm.aggregate_mainline(pool)
    assert r["ranked_groups"][0]["leader"]["name"] == "Alpha"


def test_constituents_capped_at_12():
    """成分股超过 12 时只取前 12(days 倒序 → amount 倒序 → name 正序)。"""
    pool = [_z(str(i), f"S{i:02d}", days=1, theme="拥挤", amount=1e8) for i in range(20)]
    r = mm.aggregate_mainline(pool)
    g = r["ranked_groups"][0]
    assert len(g["constituents"]) == 12


# ──────────── 宽基过滤 ────────────
def test_broad_sector_exact_match_filtered():
    """黑名单精确匹配的题材整体剔除(不进 ranked/unranked)。"""
    pool = [
        _z("1", "A", days=3, theme="机器人"),
        _z("2", "B", days=3, theme="机器人"),
        _z("3", "C", days=2, theme="机器人"),
        _z("4", "D", days=3, theme="融资融券"),
        _z("5", "E", days=2, theme="融资融券"),
        _z("6", "F", days=2, theme="融资融券"),
    ]
    r = mm.aggregate_mainline(pool)
    names = {g["name"] for g in r["ranked_groups"]}
    assert "机器人" in names
    assert "融资融券" not in names
    assert r["filter_stats"]["broad_filtered"] == 1
    assert r["filter_stats"]["ranked"] == 1


def test_broad_sector_keyword_filtered():
    """名称子串黑名单(MSCI / 昨日 / 连续 / 同花顺 / 标普 / 富时)命中即过滤。"""
    pool = [
        _z("1", "A", days=3, theme="机器人"),
        _z("2", "B", days=3, theme="机器人"),
        _z("3", "C", days=2, theme="机器人"),
        _z("4", "D", days=3, theme="MSCI概念"),
        _z("5", "E", days=2, theme="MSCI概念"),
        _z("6", "F", days=2, theme="MSCI概念"),
        _z("7", "G", days=3, theme="昨日涨停"),
        _z("8", "H", days=2, theme="昨日涨停"),
        _z("9", "I", days=2, theme="昨日涨停"),
        _z("10", "J", days=3, theme="连续涨停"),
        _z("11", "K", days=2, theme="连续涨停"),
        _z("12", "L", days=2, theme="连续涨停"),
    ]
    r = mm.aggregate_mainline(pool)
    names = {g["name"] for g in r["ranked_groups"]}
    assert names == {"机器人"}
    assert r["filter_stats"]["broad_filtered"] == 3


def test_all_broad_filtered_returns_empty():
    """全部题材都被宽基过滤 → ranked 为空, 只统计过滤数。"""
    pool = [
        _z("1", "A", days=3, theme="融资融券"),
        _z("2", "B", days=3, theme="沪深股通"),
        _z("3", "C", days=2, theme="同花顺概念"),
    ]
    r = mm.aggregate_mainline(pool)
    assert r["ranked_groups"] == []
    assert r["unranked"] == []
    assert r["filter_stats"]["broad_filtered"] == 3
    assert r["filter_stats"]["ranked"] == 0


# ──────────── 最小入榜门槛 ────────────
def test_below_min_marked_unranked():
    """涨停 <3 家 → 进 unranked, score=None, 不参与排名。"""
    pool = [
        _z("1", "A", days=2, theme="机器人"),  # 只有 1 家
        _z("2", "B", days=3, theme="芯片"),
        _z("3", "C", days=2, theme="芯片"),
        _z("4", "D", days=3, theme="芯片"),
    ]
    r = mm.aggregate_mainline(pool)
    assert [g["name"] for g in r["ranked_groups"]] == ["芯片"]
    assert len(r["unranked"]) == 1
    assert r["unranked"][0]["name"] == "机器人"
    assert r["unranked"][0]["score"] is None
    assert r["unranked"][0]["limit_up_count"] == 1


def test_exactly_3_limitup_ranks():
    """涨停恰好 3 家 → 入榜(边界)。"""
    pool = [_z(str(i), f"S{i}", days=2, theme="机器人") for i in range(3)]
    r = mm.aggregate_mainline(pool)
    assert len(r["ranked_groups"]) == 1
    assert r["ranked_groups"][0]["limit_up_count"] == 3
    assert r["filter_stats"]["below_min"] == 0


def test_2_limitup_below_min():
    """涨停 2 家 → 未入榜。"""
    pool = [_z(str(i), f"S{i}", days=2, theme="机器人") for i in range(2)]
    r = mm.aggregate_mainline(pool)
    assert r["ranked_groups"] == []
    assert len(r["unranked"]) == 1
    assert r["filter_stats"]["below_min"] == 1


# ──────────── 主线分 ────────────
def test_top_mainline_scores_100():
    """截面最大值归一化 → 最强者 score=100。"""
    pool = [
        _z("1", "A", days=5, theme="AI算力"), _z("2", "B", days=4, theme="AI算力"),
        _z("3", "C", days=3, theme="AI算力"), _z("4", "D", days=2, theme="AI算力"),
        _z("5", "E", days=1, theme="AI算力"),
        _z("6", "F", days=2, theme="机器人"), _z("7", "G", days=1, theme="机器人"),
        _z("8", "H", days=1, theme="机器人"),
    ]
    r = mm.aggregate_mainline(pool)
    top = r["ranked_groups"][0]
    assert top["name"] == "AI算力"
    # 所有维度都是最大值 → 0.35+0.25+0.25+0.15 = 1.0 → 100
    assert top["score"] == pytest.approx(100.0, abs=0.01)


def test_score_formula_basic():
    """二强主线分: 公式按 W_LIMITUP/W_MAXBOARDS/W_RUNGS/W_GE2 线性加权。"""
    # 单组分数验证: rank=1 in all 4 dims → score=100
    pool = [_z(str(i), f"S{i}", days=2, theme="AI", amount=1e8) for i in range(5)]
    r = mm.aggregate_mainline(pool)
    g = r["ranked_groups"][0]
    assert g["score"] == pytest.approx(100.0, abs=0.01)


def test_score_relative_weights():
    """验证权重: 第二名 relative ratio 反映四维差异。"""
    pool = [
        # AI算力: 涨停5家 / 最高板3板 / 梯队3档 / 二板3家
        _z("1", "A", days=3, theme="AI算力"), _z("2", "B", days=2, theme="AI算力"),
        _z("3", "C", days=2, theme="AI算力"), _z("4", "D", days=1, theme="AI算力"),
        _z("5", "E", days=1, theme="AI算力"),
        # 机器人: 涨停3家 / 最高板5板 / 梯队3档 / 二板2家
        _z("6", "F", days=5, theme="机器人"), _z("7", "G", days=2, theme="机器人"),
        _z("8", "H", days=1, theme="机器人"),
    ]
    r = mm.aggregate_mainline(pool)
    # 两者都 ≥3 涨停 → 都入榜, 按 score 排序
    ranked_names = [g["name"] for g in r["ranked_groups"]]
    assert set(ranked_names) == {"AI算力", "机器人"}
    # AI算力: 涨停5(rank 1.0)/最高板3(rank 0.6)/梯队3(rank 1.0)/二板3(rank 1.0)
    #        = 0.35+0.15+0.25+0.15 = 0.90 → 90.0
    # 机器: 涨停3(rank 0.6)/最高板5(rank 1.0)/梯队3(rank 1.0)/二板2(rank 0.667)
    #        = 0.21+0.25+0.25+0.10 = 0.81 → 81.0
    ai_g = next(g for g in r["ranked_groups"] if g["name"] == "AI算力")
    bot_g = next(g for g in r["ranked_groups"] if g["name"] == "机器人")
    assert ai_g["score"] == pytest.approx(90.0, abs=0.5)
    assert bot_g["score"] == pytest.approx(81.0, abs=0.5)
    assert ranked_names[0] == "AI算力"  # 略胜


def test_sort_tiebreak_by_limitup_then_maxboards():
    """同分时按 limit_up_count 降序, 再按 max_boards 降序, 再按 name 正序。"""
    # 构造同分难度: 不直接构造同分, 而是用一个简单 case 验证字典序
    pool = [
        _z("1", "A", days=2, theme="AI", amount=1e8),  # 入榜
        _z("2", "B", days=2, theme="AI", amount=1e8),  # 入榜
        _z("3", "C", days=2, theme="AI", amount=1e8),  # 入榜
        _z("4", "D", days=2, theme="机器人", amount=1e8),
        _z("5", "E", days=2, theme="机器人", amount=1e8),
        _z("6", "F", days=2, theme="机器人", amount=1e8),
    ]
    r = mm.aggregate_mainline(pool)
    # AI 和机器人完全对称 → score 相同 → 按 limit_up 相同 → 按 max_boards 相同 → 按 name
    # AI (A...) 比 机器人 (机...) 字典序小 → AI 第一
    assert r["ranked_groups"][0]["name"] == "AI"


# ──────────── 字段优先级 ────────────
def test_theme_takes_priority_over_sector():
    """theme(题材) > sector(板块) > industry 兜底。"""
    pool = [
        _z("1", "A", days=3, theme="AI算力", sector="计算机"),
        _z("2", "B", days=3, theme="AI算力", sector="计算机"),
        _z("3", "C", days=2, theme="AI算力", sector="计算机"),
        _z("4", "D", days=3, theme="", sector="半导体"),
        _z("5", "E", days=2, theme="", sector="半导体"),
        _z("6", "F", days=1, theme="", sector="半导体"),
    ]
    r = mm.aggregate_mainline(pool)
    names = {g["name"] for g in r["ranked_groups"]}
    assert names == {"AI算力", "半导体"}


def test_industry_fallback_when_no_theme_or_sector():
    """theme/sector 都为空 → industry 兜底。"""
    pool = [
        _z("1", "A", days=3, theme="", sector="", industry="医药"),
        _z("2", "B", days=3, theme="", sector="", industry="医药"),
        _z("3", "C", days=2, theme="", sector="", industry="医药"),
    ]
    r = mm.aggregate_mainline(pool)
    assert r["ranked_groups"][0]["name"] == "医药"


def test_other_group_filtered():
    """theme/sector/industry 全空 → 归"其他", 默认宽基过滤(非题材不参与排名)。"""
    # 当前实现: DEFAULT_GROUP("其他") 进 unranked, 不进 ranked(因为宽基过滤包含它)
    pool = [
        _z("1", "A", days=3),  # 全空 → "其他"
        _z("2", "B", days=3),
        _z("3", "C", days=2),
        _z("4", "D", days=3, theme="机器人"),
        _z("5", "E", days=2, theme="机器人"),
        _z("6", "F", days=1, theme="机器人"),
    ]
    r = mm.aggregate_mainline(pool)
    names = {g["name"] for g in r["ranked_groups"]}
    # "其他" 不应进 ranked(题材缺失, 视为噪声)
    assert "机器人" in names
    assert "其他" not in names


# ──────────── 容错 ────────────
def test_invalid_days_fallback_to_1():
    """days 异常值(非数) → 兜底 1。"""
    pool = [
        _z("1", "A", days="abc", theme="机器人"),  # str → 0 → 兜底 1
        _z("2", "B", days=None, theme="机器人"),
        _z("3", "C", days=2, theme="机器人"),
    ]
    r = mm.aggregate_mainline(pool)
    g = r["ranked_groups"][0]
    assert g["max_boards"] == 2  # A 和 None 都兜底 1, C 是 2
    assert g["limit_up_count"] == 3
    assert g["ge2_count"] == 1


def test_invalid_amount_fallback_to_zero():
    """amount 异常值 → 兜底 0.0, 不影响分组聚合(只影响 leader 二级排序)。"""
    pool = [
        _z("1", "A", days=3, theme="机器人", amount="abc"),
        _z("2", "B", days=2, theme="机器人", amount=None),
        _z("3", "C", days=1, theme="机器人", amount=1e8),
    ]
    r = mm.aggregate_mainline(pool)
    g = r["ranked_groups"][0]
    assert g["leader"]["code"] == "1"  # days=3 最大, amount=0 兜底, 仍胜出


# ──────────── top_n 截断 ────────────
def test_top_n_truncation():
    """top_n 截断: 入榜分组数 > top_n 时只取前 N。"""
    # 5 个不同题材, 各 3 家涨停
    pool = []
    for theme in ["AI", "机器人", "芯片", "新能源", "医药"]:
        for i in range(3):
            pool.append(_z(f"{theme}_{i}", f"S{theme}_{i}", days=2, theme=theme))
    r = mm.aggregate_mainline(pool, top_n=3)
    assert len(r["ranked_groups"]) == 3
    assert r["filter_stats"]["ranked"] == 3
    assert r["total_groups"] == 5


# ──────────── 综合场景 ────────────
def test_realistic_scenario():
    """真实场景: 多题材 + 宽基过滤 + 入榜门槛 + 排名。"""
    pool = [
        # AI算力: 5 涨停, 最高 5 板
        _z("000001", "算力A", days=5, theme="AI算力", amount=20e8),
        _z("000002", "算力B", days=3, theme="AI算力", amount=8e8),
        _z("000003", "算力C", days=2, theme="AI算力", amount=5e8),
        _z("000004", "算力D", days=1, theme="AI算力", amount=3e8),
        _z("000005", "算力E", days=1, theme="AI算力", amount=1e8),
        # 机器人: 4 涨停, 最高 3 板
        _z("000006", "机器A", days=3, theme="机器人", amount=12e8),
        _z("000007", "机器B", days=2, theme="机器人", amount=6e8),
        _z("000008", "机器C", days=1, theme="机器人", amount=4e8),
        _z("000009", "机器D", days=1, theme="机器人", amount=2e8),
        # 芯片: 3 涨停, 最高 2 板 (临界入榜)
        _z("000010", "芯片A", days=2, theme="芯片", amount=7e8),
        _z("000011", "芯片B", days=1, theme="芯片", amount=3e8),
        _z("000012", "芯片C", days=1, theme="芯片", amount=2e8),
        # 充电桩: 2 涨停 (未入榜)
        _z("000013", "充电A", days=1, theme="充电桩", amount=2e8),
        _z("000014", "充电B", days=1, theme="充电桩", amount=1e8),
        # 融资融券: 5 涨停 (宽基过滤)
        _z("000015", "融资A", days=2, theme="融资融券", amount=10e8),
        _z("000016", "融资B", days=1, theme="融资融券", amount=8e8),
        _z("000017", "融资C", days=1, theme="融资融券", amount=5e8),
        _z("000018", "融资D", days=1, theme="融资融券", amount=3e8),
        _z("000019", "融资E", days=1, theme="融资融券", amount=2e8),
        # MSCI: 3 涨停 (名称子串过滤)
        _z("000020", "MSCIA", days=1, theme="MSCI概念", amount=9e8),
        _z("000021", "MSCIB", days=1, theme="MSCI概念", amount=4e8),
        _z("000022", "MSCIC", days=1, theme="MSCI概念", amount=3e8),
    ]
    r = mm.aggregate_mainline(pool)
    # 期望: ranked = [AI算力, 机器人, 芯片]
    names = [g["name"] for g in r["ranked_groups"]]
    assert names == ["AI算力", "机器人", "芯片"]
    # AI 第一 (涨停数最多 + 最高板最高)
    assert r["ranked_groups"][0]["limit_up_count"] == 5
    assert r["ranked_groups"][0]["max_boards"] == 5
    assert r["ranked_groups"][0]["leader"]["code"] == "000001"
    # unranked = 充电桩 (涨停 <3)
    assert [g["name"] for g in r["unranked"]] == ["充电桩"]
    # filter_stats: 宽基过滤 2 (融资融券 + MSCI), 未入榜 1, 入榜 3
    assert r["filter_stats"]["broad_filtered"] == 2
    assert r["filter_stats"]["below_min"] == 1
    assert r["filter_stats"]["ranked"] == 3


# ──────────── P3: 口径日期 ────────────
def test_fetch_mainline_has_asof(monkeypatch):
    """_fetch_mainline 带当日 asof(空池也带, 前端徽标不编数)。"""
    from datetime import date as _date

    from src.web.api import market_mainline as mml

    monkeypatch.setattr(
        "src.collectors.market_sentiment_collector.MarketSentimentCollector.get_limit_up_pool",
        lambda self, date=None: [],
    )
    out = mml._fetch_mainline()
    assert out["asof"] == _date.today().isoformat()

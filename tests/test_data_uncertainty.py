"""数据不确定性因子(L2): 双源分歧度纯函数测试。单位: 元。"""

from src.core.data_uncertainty import compute_uncertainty


def test_missing_source_unknown():
    """任一缺失 → 未知, 不编数。"""
    assert compute_uncertainty(None, 1e6)["level"] == "未知"
    assert compute_uncertainty(1e6, None)["score"] is None
    assert compute_uncertainty("x", 1e6)["level"] == "未知"


def test_dust_both_low():
    """双尘埃(<50万) → 0.0 低。"""
    out = compute_uncertainty(10e4, -20e4)
    assert out == {"score": 0.0, "level": "低", "agree": True,
                   "detail": "双源皆无主力(尘埃), 一致"}


def test_same_direction_magnitude_ratio():
    """同向: 量级越接近分越低。"""
    assert compute_uncertainty(100e4, 100e4)["score"] == 0.0
    mid = compute_uncertainty(100e4, 50e4)
    assert mid["score"] == 0.5 and mid["level"] == "中" and mid["agree"] is True
    far = compute_uncertainty(100e4, 10e4)
    assert far["score"] == 0.9 and far["level"] == "高" and far["agree"] is False


def test_opposite_direction_max():
    """反向 → 1.0 高(含一端为零: 一源看到大单另一源看不到=最大分歧)。"""
    out = compute_uncertainty(500e4, -300e4)
    assert out["score"] == 1.0 and out["level"] == "高" and out["agree"] is False
    out0 = compute_uncertainty(0.0, 800e4)
    assert out0["score"] == 1.0 and out0["level"] == "高"


def test_detail_carries_both_sides_wan():
    """detail 带双边万元数, 可读可查。"""
    out = compute_uncertainty(200e4, -100e4)
    assert "万" in out["detail"] and "+200" in out["detail"]

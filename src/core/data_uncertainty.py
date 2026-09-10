"""数据不确定性因子(L2): 双源分歧度 → 0-1 分数。

把"暗盘方向误差"从 bug 变成 feature: TQ L2 明盘主力净流入(zjl_hb, 元)
vs 腾讯逐笔主力净额(main_net, 元) 都是元口径, 可比。分歧大 = 数据在
打架 = 降置信/降仓的依据。

本期只记录 + 展示, 不接交易阻断(阈值等分歧数据积累后再定, 走 S5a→S5b 路径)。
"""

from __future__ import annotations

# 双源都小于此值视为"无主力尘埃", 直接低不确定(元)
DUST_YUAN = 50e4


def _sign(v: float) -> int:
    return 1 if v > 0 else (-1 if v < 0 else 0)


def compute_uncertainty(l2_net, tick_net) -> dict:
    """双源分歧 → {score(0-1|None), level(低/中/高/未知), agree, detail}。

    - 任一缺失 → 未知(不编数)
    - 双尘埃 → 0.0 低
    - 同向 → 1 - min/max(量级越接近分越低)
    - 反向(含一端为零) → 1.0 高
    """
    if l2_net is None or tick_net is None:
        return {"score": None, "level": "未知", "agree": None,
                "detail": "双源缺一, 不确定性未知"}
    try:
        a, b = float(l2_net), float(tick_net)
    except (TypeError, ValueError):
        return {"score": None, "level": "未知", "agree": None,
                "detail": "输入非数值"}
    if abs(a) < DUST_YUAN and abs(b) < DUST_YUAN:
        return {"score": 0.0, "level": "低", "agree": True,
                "detail": "双源皆无主力(尘埃), 一致"}
    if _sign(a) == _sign(b) and _sign(a) != 0:
        ratio = min(abs(a), abs(b)) / max(abs(a), abs(b))
        score = round(1.0 - ratio, 3)
    else:
        score = 1.0
    level = "低" if score < 0.33 else ("中" if score < 0.66 else "高")
    return {
        "score": score,
        "level": level,
        "agree": score < 0.66,
        "detail": f"L2明盘{a / 1e4:+.0f}万 vs 逐笔{b / 1e4:+.0f}万",
    }

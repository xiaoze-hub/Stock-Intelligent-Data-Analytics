"""信号周滚动 HitRate 榜(L1): 读 strategy_outcomes, 按策略×持有期统计。

命中口径(文档即契约, 与榜单一起返回):
- hit = 到目标价(hit_target) 或 持有期正收益(evaluated 且 return>0)
- target_rate = 到目标价占比; stop_rate = 触止损占比; avg_ret = 平均收益%
- n < MIN_N 标"样本不足"; 近4周 hit_rate<45% 且 n>=20 标"待砍"
- 半衰期代理: 同策略 hit_rate 随 horizon(1/3/5/10d)衰减序列, 榜单按策略
  聚合展示 decay, 掉得快=半衰期短(舆情动量特征)
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

HORIZONS = (1, 3, 5, 10)
WINDOW_DAYS = 28
MIN_N = 10
CUT_HIT_RATE = 45.0
CUT_MIN_N = 20
BOARD_CACHE_TTL_S = 6 * 3600  # outcome 日级评估, 6h 缓存足够

_DONE = ("evaluated", "hit_target", "hit_stop")


def _is_hit(status: str, hit_target, ret) -> bool:
    if hit_target is True:
        return True
    return status == "evaluated" and ret is not None and ret > 0


def compute_hitrate_board(db, window_days: int = WINDOW_DAYS, today: str | None = None) -> dict:
    """算榜。返回 {asof, window_days, rows}。
    rows: [{strategy_code, horizon_days, n, hits, hit_rate, target_rate,
            stop_rate, avg_ret, status}] 按 hit_rate 倒序。
    """
    from src.web.models import StrategyOutcome

    asof = today or date.today().isoformat()
    cutoff = (date.fromisoformat(asof) - timedelta(days=window_days)).isoformat()
    q = (
        db.query(
            StrategyOutcome.strategy_code,
            StrategyOutcome.horizon_days,
            StrategyOutcome.outcome_status,
            StrategyOutcome.hit_target,
            StrategyOutcome.hit_stop,
            StrategyOutcome.outcome_return_pct,
        )
        .filter(
            StrategyOutcome.outcome_status.in_(_DONE),
            StrategyOutcome.target_date >= cutoff,
            StrategyOutcome.target_date <= asof,
        )
        .all()
    )
    groups: dict[tuple[str, int], dict] = {}
    for code, hz, status, ht, hs, ret in q:
        g = groups.setdefault((code or "", int(hz or 0)), {"n": 0, "hits": 0, "targets": 0, "stops": 0, "rets": []})
        g["n"] += 1
        if _is_hit(status, ht, ret):
            g["hits"] += 1
        if ht is True:
            g["targets"] += 1
        if hs is True:
            g["stops"] += 1
        if ret is not None:
            g["rets"].append(float(ret))
    rows = []
    for (code, hz), g in groups.items():
        n = g["n"]
        rets = g["rets"]
        hit_rate = round(g["hits"] / n * 100, 1)
        if n < MIN_N:
            status = "样本不足"
        elif hit_rate < CUT_HIT_RATE and n >= CUT_MIN_N:
            status = "待砍"
        else:
            status = "正常"
        rows.append({
            "strategy_code": code,
            "horizon_days": hz,
            "n": n,
            "hits": g["hits"],
            "hit_rate": hit_rate,
            "target_rate": round(g["targets"] / n * 100, 1),
            "stop_rate": round(g["stops"] / n * 100, 1),
            "avg_ret": round(sum(rets) / len(rets), 2) if rets else None,
            "status": status,
        })
    rows.sort(key=lambda r: (-r["hit_rate"], -r["n"]))
    return {
        "asof": asof,
        "window_days": window_days,
        "hit_def": "到目标价或持有期正收益",
        "rows": rows,
    }

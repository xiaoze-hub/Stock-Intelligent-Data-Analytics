"""市况×信号矩阵(L3): 哪类信号在什么市况下赚钱。

市况维度 = market_phase_daily.phase(情绪周期6阶段+accumulating, 日级真相,
POST /phase/sync 写入)；归因键 = outcome.snapshot_date(信号发出日市况)。
命中口径复用 L1(到目标价或持有期正收益=中)，两榜口径永远一致。

本期只展示矩阵 + 当前市况最佳策略；调权联动等矩阵养厚后再定。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from src.core.signal_hitrate import _DONE, _is_hit

logger = logging.getLogger(__name__)

WINDOW_DAYS = 90
MIN_N = 10
CUT_HIT_RATE = 45.0
CUT_MIN_N = 20
BOARD_CACHE_TTL_S = 6 * 3600


def compute_regime_matrix(db, window_days: int = WINDOW_DAYS, today: str | None = None) -> dict:
    """算矩阵。返回 {asof, window_days, current_phase, cells, best}。
    cells: [{phase, strategy_code, n, hits, hit_rate, avg_ret, status}]。
    best: {phase: {strategy_code, hit_rate, n}} 每市况最优(样本足者)。
    """
    from src.web.models import MarketPhaseDaily, StrategyOutcome

    asof = today or date.today().isoformat()
    cutoff = (date.fromisoformat(asof) - timedelta(days=window_days)).isoformat()
    phase_rows = (
        db.query(MarketPhaseDaily.date, MarketPhaseDaily.phase)
        .filter(MarketPhaseDaily.phase != "")
        .all()
    )
    phase_of = {}
    for d, p in phase_rows:
        try:
            phase_of[d.isoformat() if hasattr(d, "isoformat") else str(d)] = p
        except Exception:  # noqa: BLE001
            continue
    current_phase = phase_of.get(asof)
    if current_phase is None and phase_of:
        current_phase = phase_of.get(max(phase_of))

    q = (
        db.query(
            StrategyOutcome.strategy_code,
            StrategyOutcome.snapshot_date,
            StrategyOutcome.outcome_status,
            StrategyOutcome.hit_target,
            StrategyOutcome.outcome_return_pct,
        )
        .filter(
            StrategyOutcome.outcome_status.in_(_DONE),
            StrategyOutcome.target_date >= cutoff,
            StrategyOutcome.target_date <= asof,
        )
        .all()
    )
    groups: dict[tuple[str, str], dict] = {}
    for code, snap_day, status, ht, ret in q:
        phase = phase_of.get(snap_day or "")
        if not phase:
            continue  # 信号日无市况标签的不编归属, 直接丢(计数见 skipped)
        g = groups.setdefault((phase, code or ""), {"n": 0, "hits": 0, "rets": []})
        g["n"] += 1
        if _is_hit(status, ht, ret):
            g["hits"] += 1
        if ret is not None:
            g["rets"].append(float(ret))
    cells = []
    for (phase, code), g in groups.items():
        n = g["n"]
        rets = g["rets"]
        hit_rate = round(g["hits"] / n * 100, 1)
        if n < MIN_N:
            status = "样本不足"
        elif hit_rate < CUT_HIT_RATE and n >= CUT_MIN_N:
            status = "待砍"
        else:
            status = "正常"
        cells.append({
            "phase": phase,
            "strategy_code": code,
            "n": n,
            "hits": g["hits"],
            "hit_rate": hit_rate,
            "avg_ret": round(sum(rets) / len(rets), 2) if rets else None,
            "status": status,
        })
    cells.sort(key=lambda r: (r["phase"], -r["hit_rate"], -r["n"]))
    best: dict = {}
    for c in cells:
        ph = c["phase"]
        if c["status"] == "样本不足":
            continue
        if ph not in best or c["hit_rate"] > best[ph]["hit_rate"]:
            best[ph] = {"strategy_code": c["strategy_code"],
                        "hit_rate": c["hit_rate"], "n": c["n"]}
    return {
        "asof": asof,
        "window_days": window_days,
        "current_phase": current_phase,
        "hit_def": "到目标价或持有期正收益",
        "cells": cells,
        "best": best,
    }

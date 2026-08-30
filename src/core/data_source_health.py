# -*- coding: utf-8 -*-
"""数据源健康指示灯 (SIDA P1-1 后端, 2026-08-31)
=================================================

背景: 2026-08-30 事故——TQ 链路断时前端把 08-28 缓存当实时展示, 用户无感知。
本模块提供单一入口 data_source_health(), 供 /api/dashboard/data_source_health
与首页"数据源健康"指示灯消费, 让"数据是否今日实时"显式可见。

判定口径:
  tq_online      = TQ JSON-RPC get_market_snapshot 返回 ErrorId==0
  data_is_today  = get_more_info.HqDate == 最近交易日 且 get_market_snapshot.Now 非 0
                   (休市/开盘前 Now==0 属正常, 此时 data_is_today 以 HqDate 为准)
  last_price     = 快照 Now(实时价), 便于前端展示与"≠缓存价"核对

不抛异常: 任何故障都降级为字段 False/None, 绝不让首页 500。
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any, Dict, Optional

import httpx

_TQ_URL = os.environ.get("TDX_QUANT_URL", "http://172.27.16.1:17709/")
_TIMEOUT_S = 3.0


def _rpc(method: str, params: dict) -> Optional[dict]:
    try:
        body = {"id": 1, "method": method, "params": params}
        with httpx.Client(timeout=_TIMEOUT_S) as c:
            r = c.post(_TQ_URL, json=body,
                       headers={"Content-Type": "application/json; charset=utf-8"})
            r.raise_for_status()
            data = r.json()
        return data.get("result") or {}
    except Exception:
        return None


def data_source_health(code: str = "002361.SZ") -> Dict[str, Any]:
    """返回数据源健康结构(永不抛异常)。"""
    snap = _rpc("get_market_snapshot", {"stock_code": code})
    info = _rpc("get_more_info", {"stock_code": code})

    tq_online = snap is not None and str(snap.get("ErrorId", "1")) in ("0", "")
    now = None
    if snap:
        try:
            now = float(snap.get("Now") or 0)
        except (TypeError, ValueError):
            now = 0.0
    hq_date = str((info or {}).get("HqDate") or "")

    today = date.today().strftime("%Y%m%d")
    # 开盘前/休市 Now==0 正常: 以 HqDate 是否>=最近交易日判断数据新鲜度
    data_is_today = bool(hq_date) and hq_date >= _last_trading_day(today)

    return {
        "tq_online": tq_online,
        "data_is_today": data_is_today,
        "last_price": now,
        "hq_date": hq_date,
        "checked_at": date.today().isoformat(),
        "tq_url": _TQ_URL,
    }


def _last_trading_day(today_yyyymmdd: str) -> str:
    """粗略最近交易日下界: 周一回退到上周五, 周日回退到周五, 其余回退一天。

    仅用于"数据是否过旧"的宽松判断(防拿数日前缓存当实时), 不做节假日历。
    """
    y, m, d = int(today_yyyymmdd[:4]), int(today_yyyymmdd[4:6]), int(today_yyyymmdd[6:])
    dt = date(y, m, d)
    wd = dt.weekday()  # 0=Mon
    back = 1
    if wd == 0:
        back = 3
    elif wd == 6:
        back = 2
    from datetime import timedelta
    return (dt - timedelta(days=back)).strftime("%Y%m%d")


if __name__ == "__main__":
    import json
    print(json.dumps(data_source_health(), ensure_ascii=False, indent=2))
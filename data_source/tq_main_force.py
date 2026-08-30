# -*- coding: utf-8 -*-
"""TQ 主力意图资金 · 数据层
========================

封装本机通达信 TQ 网关(默认 http://127.0.0.1:17709/, JSON-RPC)的
主力意图资金所需字段,产出标准化 DataFrame。

字段口径(2026-08-29 实测, 详见 [任务TQ3][DONE] 回邮):
- BCancel/SCancel  买/卖撤单量, 单位【手】(推导性确认: 若按笔则超 L2OrderNum, 不可能)
- Inside/Outside   内/外盘, 单位【手】, 两者之和≈快照 Volume
- ZAF              当日涨跌幅, 单位【%】
- fLianB           量比(仓库版 tq.py 同样映射为 volume_ratio)
- Ltsz/Zsz         流通/总市值(亿); fHSL 换手率按流通股本
- Amount           成交额(万元); Volume 总量(手)
- 分档净额(超大/大/中/小单)依赖 formula_process_mul_zb L2AMO 公式,
  TQ3 阶段该接口参数未定(ErrorId 5), 此处预留接口并优雅降级为 None,
  待拿到可用调用样例后接入(见 fetch_l2amo 分档 TODO)。

用法:
    from tq_main_force import TQClient, build_main_force_dataframe
    client = TQClient()
    df = build_main_force_dataframe(client, ["002361.SZ", "603737.SH"])
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# 标准化输出列(任务规约要求 + 证据链需要的扩展列)
MAIN_FORCE_COLUMNS = [
    "代码", "时间",
    "超大单净额", "大单净额", "中单净额", "小单净额",
    "买撤量", "卖撤量",
    "内盘", "外盘",
    "涨幅", "量比",
    # 扩展列(证据链/调试用, 不在任务规约硬性清单内)
    "收盘", "昨收", "总手", "成交额万", "外盘占比",
    "主力净额", "60日分位", "换手率", "流通市值亿", "总市值亿",
    "情绪涨停家数", "情绪炸板家数",
]


class TQClient:
    """通达信 TQ JSON-RPC 客户端(极简, 仅标准库依赖)。"""

    def __init__(self, url: str = "http://127.0.0.1:17709/", timeout_s: float = 12.0):
        self.url = url
        self.timeout_s = timeout_s

    def rpc(self, method: str, params: Dict[str, Any], timeout_s: Optional[float] = None) -> Dict[str, Any]:
        """发 JSON-RPC, 返回完整 result dict; HTTP/业务错误抛异常。"""
        body = json.dumps(
            {"id": 1, "method": method, "params": params}, ensure_ascii=False
        ).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s or self.timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if "error" in data:
            raise RuntimeError(f"TQ rpc error: {data['error']}")
        return data.get("result") or {}

    # ---- 单接口封装 -------------------------------------------------

    def snapshot(self, code: str) -> Dict[str, Any]:
        """实时/收盘快照(平铺字段, ErrorId 共存在 result 里)。"""
        res = self.rpc("get_market_snapshot", {"stock_code": code})
        if str(res.get("ErrorId", "0")) != "0":
            raise RuntimeError(f"snapshot {code} ErrorId={res.get('ErrorId')}: {res.get('Error', '')}")
        return res

    def more_info(self, code: str) -> Dict[str, Any]:
        """104 字段扩展指标(字段平铺在 result.Value 下, 此处已剥壳)。"""
        res = self.rpc("get_more_info", {"stock_code": code})
        if str(res.get("ErrorId", "0")) != "0":
            raise RuntimeError(f"more_info {code} ErrorId={res.get('ErrorId')}")
        return res.get("Value") or res

    def kline_closes(self, code: str, days: int = 60) -> List[float]:
        """近 N 日收盘序列(前复权), 供 60 日分位计算。"""
        try:
            self.rpc("refresh_kline", {"stock_list": [code], "period": "1d"})
        except Exception:  # noqa: BLE001  刷新失败不阻塞, 直接取数
            pass
        res = self.rpc(
            "get_market_data",
            {
                "stock_list": [code],
                "period": "1d",
                "count": days,
                "dividend_type": "front",
            },
            timeout_s=max(12.0, 15.0),
        )
        rows = ((res or {}).get("Value") or {}).get(code) if isinstance(res, dict) else None
        if not rows:
            return []
        closes = rows.get("Close") or []
        out: List[float] = []
        for c in closes:
            try:
                out.append(float(c))
            except (TypeError, ValueError):
                continue
        return out

    def market_sentiment(self, start: str, end: str) -> Dict[str, Any]:
        """SC03 涨停/炸板家数序列(情绪周期温度计)。"""
        res = self.rpc(
            "get_scjy_value",
            {
                "stock_code": "999999.SH",
                "code": "999999.SH",
                "field_list": [],
                "table_list": ["SC03"],
                "start_time": start,
                "end_time": end,
            },
        )
        val = (res.get("Value") or {}).get("SC03") or []
        # Value 行: {"Date": "20260828", "Value": [涨停家数, 炸板家数]}
        out: Dict[str, Any] = {}
        for row in val:
            try:
                out[str(row["Date"])] = {"zt": float(row["Value"][0]), "zb": float(row["Value"][1])}
            except (KeyError, IndexError, TypeError, ValueError):
                continue
        return out

    def l2amo_bands(self, code: str, hq_date: str = "") -> Optional[Dict[str, float]]:
        """分档净额(超大/大/中/小单, 万元)。

        [2026-08-29 变更] formula 路线废弃(ErrorId 5, 见 TQ3 回邮)。
        改走 TQ4 自建分档: 读 C:\\TdxQ\\darkflow\\{code}_{date}.json
        (由 order_cluster.py 从超盘回放 .tck 逐笔还原产出, 口径=逐笔
        成交金额切档, 委托号显式关联研究中)。

        盘中/无落盘文件时返回 None(证据链如实标"盘后补全", 不编造)。
        """
        import os

        if not hq_date:
            return None
        path = os.path.join(r"C:\TdxQ\darkflow", f"{code}_{hq_date}.json")
        if not os.path.exists(path):
            logger.info("darkflow 分档缺失: %s (盘后补全), %s", path, code)
            return None
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            bands = payload["bands"]
            return {
                "超大单净额": bands["超大单"]["net_wan"],
                "大单净额": bands["大单"]["net_wan"],
                "中单净额": bands["中单"]["net_wan"],
                "小单净额": bands["小单"]["net_wan"],
                "_口径": payload.get("banding口径", ""),
            }
        except (KeyError, ValueError, OSError) as exc:
            logger.warning("darkflow %s 解析失败: %s", path, exc)
            return None


# ---- 组装 -----------------------------------------------------------


def _percentile_rank(value: float, series: List[float]) -> Optional[float]:
    """当前值在序列中的分位(0~1); 序列含当前日收盘。"""
    if not series:
        return None
    below = sum(1 for x in series if x <= value)
    return round(below / len(series), 4)


def build_main_force_row(
    client: TQClient,
    code: str,
    kline_days: int = 60,
    sentiment: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """拉单只股票并产出标准化行(列见 MAIN_FORCE_COLUMNS)。"""
    snap = client.snapshot(code)
    info = client.more_info(code)

    inside = float(snap.get("Inside", 0) or 0)
    outside = float(snap.get("Outside", 0) or 0)
    total = inside + outside
    now = float(snap.get("Now", 0) or 0)

    bands = client.l2amo_bands(code, str(info.get("HqDate", ""))) or {}

    # 60 日分位(近 60 日收盘序列含当日)
    closes = client.kline_closes(code, kline_days)
    pos = _percentile_rank(now, closes)

    latest_sent = None
    if sentiment:
        hq_date = str(info.get("HqDate", ""))
        latest_sent = sentiment.get(hq_date)

    row = {
        "代码": code,
        "时间": info.get("HqDate", ""),
        "超大单净额": bands.get("超大单净额"),
        "大单净额": bands.get("大单净额"),
        "中单净额": bands.get("中单净额"),
        "小单净额": bands.get("小单净额"),
        "买撤量": float(info.get("BCancel", 0) or 0),
        "卖撤量": float(info.get("SCancel", 0) or 0),
        "内盘": inside,
        "外盘": outside,
        "涨幅": float(info.get("ZAF", 0) or 0),
        "量比": float(info.get("fLianB", 0) or 0),
        "收盘": now,
        "昨收": float(snap.get("LastClose", 0) or 0),
        "总手": float(snap.get("Volume", 0) or 0),
        "成交额万": float(snap.get("Amount", 0) or 0),
        "外盘占比": round(outside / total, 4) if total > 0 else None,
        "主力净额": float(info.get("Zjl", 0) or 0),
        "60日分位": pos,
        "换手率": float(info.get("fHSL", 0) or 0),
        "流通市值亿": float(info.get("Ltsz", 0) or 0),
        "总市值亿": float(info.get("Zsz", 0) or 0),
        "情绪涨停家数": latest_sent["zt"] if latest_sent else None,
        "情绪炸板家数": latest_sent["zb"] if latest_sent else None,
    }
    return row


def build_main_force_dataframe(
    client: TQClient,
    codes: List[str],
    kline_days: int = 60,
    with_sentiment: bool = True,
) -> pd.DataFrame:
    """批量产出标准化 DataFrame(列序见 MAIN_FORCE_COLUMNS)。

    单只失败不阻塞整批(记 warning, 该行跳过)。
    """
    sentiment: Optional[Dict[str, Any]] = None
    if with_sentiment:
        try:
            sentiment = client.market_sentiment("20260801", "20261231")
        except Exception as exc:  # noqa: BLE001
            logger.warning("SC03 情绪序列获取失败, 情绪列置空: %s", exc)

    rows: List[Dict[str, Any]] = []
    for code in codes:
        try:
            rows.append(build_main_force_row(client, code, kline_days, sentiment))
        except Exception as exc:  # noqa: BLE001
            logger.warning("build row %s failed: %s", code, exc)
    df = pd.DataFrame(rows, columns=MAIN_FORCE_COLUMNS)
    return df

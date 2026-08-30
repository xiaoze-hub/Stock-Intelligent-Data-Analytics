# -*- coding: utf-8 -*-
"""通达信超盘回放 .tck 逐笔解析 + 委托/成交事件流(TQ4-a2/b)
==========================================================

文件格式(2026-08-29 实测 sz002361_20260827.tck):
- 容器同 .img: 24 字节头([8:12]=comp_size, [16:20]=raw_size), offset 24 起 zlib
- 解压后 6,742,368B = 187,288 × 36 字节定长记录:
    [0:2]   类型 u16: 0=成交(98,088) 1=委托(89,200)
    [2:6]   文件内缓变计数器(语义未定, 不用于业务)
    [6:14]  价格 float64(元); 撤单记录为 0.0
    [14:18] 量 u32, 单位【股】(实测全日 Σ=1.56 亿股 ↔ 成交额 16.27 亿元自洽)
    [18:22] 常量 2015(0x7df)
    [22:26] 全市场消息序号 u32(申报/撤单目标/成交共用同一空间, 上限当日 ~5090 万)
    [26:28] 成交方向 ASCII: "2B"=主买(50,071) "2S"=主卖(47,984); 委托记录: "00"=申报(65,626) "0C"=撤单(23,574)
    [28:36] 附加区(撤单: 被撤目标序号 u32 在 [28:32] 或 [32:36]; 语义研究进行中)

分档口径(当前版本, 诚实标注):
- 按逐笔成交金额切档(与 L2AMO 公式同口径): 超大单>=100万 / 大单20-100万 /
  中单5-20万 / 小单<5万, 方向取 "2B"/"2S"
- 「委托号聚簇还原」(按委托总金额切)的成交↔委托显式关联尚未打通:
  成交记录未携带委托号([22:26] 为消息序号, 全局唯一但非委托归组键);
  该关联为 TQ4 后续研究项, 打通前分档采用逐笔口径, 不冒充委托口径
- 25 条 price=0 记录(盘后/修正行)剔除, 计数如实保留在 anchors

用法:
    from order_cluster import parse_tck, band_summary
    recs = parse_tck("sz002361_20260827.tck")
    summary = band_summary(recs)      # 四档买卖净额(万元) + anchors
"""

from __future__ import annotations

import struct
import zlib
from typing import Any, Dict, List, Tuple

REC_SIZE = 36
HEADER_SIZE = 24

# 分档阈值(元, 按单笔/单委托成交金额)
BAND_XL = 1_000_000.0   # 超大单 >= 100 万
BAND_L = 200_000.0      # 大单   >= 20 万
BAND_M = 50_000.0       # 中单   >= 5 万


def parse_tck(path: str) -> Dict[str, Any]:
    """解包 .tck → {"trades": [...], "orders_new": [...], "orders_cancel": [...], "anchors": {...}}"""
    raw = open(path, "rb").read()
    comp = struct.unpack("<I", raw[8:12])[0]
    raw_size = struct.unpack("<I", raw[16:20])[0]
    dec = zlib.decompress(raw[HEADER_SIZE:HEADER_SIZE + comp])
    if len(dec) != raw_size:
        raise ValueError(f"{path}: decompressed {len(dec)} != header {raw_size}")
    n = len(dec) // REC_SIZE

    trades: List[Dict[str, Any]] = []
    orders_new: List[Dict[str, Any]] = []
    orders_cancel: List[Dict[str, Any]] = []
    seq_seen = 0

    for i in range(n):
        r = dec[i * REC_SIZE:(i + 1) * REC_SIZE]
        rtype = r[0]
        seq = struct.unpack("<I", r[22:26])[0]
        seq_seen = max(seq_seen, seq)
        price = struct.unpack("<d", r[6:14])[0]
        vol = struct.unpack("<I", r[14:18])[0]
        tag = r[26:28]
        if rtype == 0:
            d = tag.decode("ascii", errors="replace")  # "2B" / "2S"
            trades.append({"seq": seq, "price": price, "vol": vol,
                           "dir": "B" if d.endswith("B") else ("S" if d.endswith("S") else "?"),
                           "amount": price * vol})
        elif tag == b"00":
            orders_new.append({"seq": seq, "price": price, "vol": vol})
        elif tag == b"0C":
            tgt28 = struct.unpack("<I", r[28:32])[0]
            tgt32 = struct.unpack("<I", r[32:36])[0]
            orders_cancel.append({"seq": seq, "target": tgt28 or tgt32})

    anchors = {
        "trades": len(trades),
        "orders_new": len(orders_new),
        "orders_cancel": len(orders_cancel),
        "seq_max": seq_seen,
        "price_min": min((t["price"] for t in trades if t["price"] > 0), default=None),
        "price_max": max((t["price"] for t in trades), default=None),
        "vol_total_share": sum(t["vol"] for t in trades),
        "amount_total_yuan": sum(t["amount"] for t in trades),
        "dir_B_count": sum(1 for t in trades if t["dir"] == "B"),
        "dir_S_count": sum(1 for t in trades if t["dir"] == "S"),
    }
    return {"trades": trades, "orders_new": orders_new,
            "orders_cancel": orders_cancel, "anchors": anchors}


# 分档阈值(元, 按单笔/单委托成交金额) — 可被 mi1_config.json rules 段覆盖:
#   band_xl / band_l / band_m (调用方传入 cfg 时生效)
BAND_XL = 1_000_000.0   # 超大单 >= 100 万
BAND_L = 200_000.0      # 大单   >= 20 万
BAND_M = 50_000.0       # 中单   >= 5 万


def band_summary(parsed: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """逐笔成交金额切档 → 四档 买/卖/净额(万元)。

    :param cfg: mi1_config.json 的 rules 段; 可含 band_xl/band_l/band_m(元)。
    """
    cfg = cfg or {}
    band_xl = float(cfg.get("band_xl", BAND_XL))
    band_l = float(cfg.get("band_l", BAND_L))
    band_m = float(cfg.get("band_m", BAND_M))
    bands = {
        "超大单": {"ge": band_xl, "buy": 0.0, "sell": 0.0, "n": 0},
        "大单": {"ge": band_l, "buy": 0.0, "sell": 0.0, "n": 0},
        "中单": {"ge": band_m, "buy": 0.0, "sell": 0.0, "n": 0},
        "小单": {"ge": 0.0, "buy": 0.0, "sell": 0.0, "n": 0},
    }
    zero_price = 0
    for t in parsed["trades"]:
        if t["price"] <= 0:
            zero_price += 1
            continue
        amt = t["amount"]
        if amt >= band_xl:
            b = bands["超大单"]
        elif amt >= band_l:
            b = bands["大单"]
        elif amt >= band_m:
            b = bands["中单"]
        else:
            b = bands["小单"]
        b["n"] += 1
        if t["dir"] == "B":
            b["buy"] += amt
        elif t["dir"] == "S":
            b["sell"] += amt
    out: Dict[str, Any] = {}
    wan = 1e4
    for name, b in bands.items():
        buy_wan = round(b["buy"] / wan, 2)
        sell_wan = round(b["sell"] / wan, 2)
        out[name] = {"buy_wan": buy_wan, "sell_wan": sell_wan,
                     "net_wan": round(buy_wan - sell_wan, 2), "n": b["n"]}
    out["zero_price_skipped"] = zero_price
    return out


def dump_darkflow(code: str, date: str, path_out: str, parsed: Dict[str, Any]) -> Dict[str, Any]:
    """产出 darkflow JSON(结构化分档+锚点), 供 tq_main_force.l2amo_bands 消费。"""
    import json

    a = parsed["anchors"]
    payload = {
        "code": code,
        "date": date,
        "source": "tck_replay",
        "banding口径": "逐笔成交金额切档(委托号显式关联研究中, 见 TQ4 回邮)",
        "bands": band_summary(parsed),
        "anchors": {
            "trades": a["trades"],
            "orders_new": a["orders_new"],
            "orders_cancel": a["orders_cancel"],
            "seq_max": a["seq_max"],
            "price_min": a["price_min"],
            "price_max": a["price_max"],
            "vol_total_share": a["vol_total_share"],
            "amount_total_yuan": a["amount_total_yuan"],
            "dir_B_count": a["dir_B_count"],
            "dir_S_count": a["dir_S_count"],
        },
    }
    import os

    os.makedirs(os.path.dirname(path_out), exist_ok=True)
    with open(path_out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "samples/sz002361_20260827.tck"
    parsed = parse_tck(path)
    a = parsed["anchors"]
    print(f"trades={a['trades']} (B={a['dir_B_count']}/S={a['dir_S_count']}) "
          f"orders_new={a['orders_new']} cancels={a['orders_cancel']} seq_max={a['seq_max']}")
    print(f"price {a['price_min']}..{a['price_max']}  vol(股)={a['vol_total_share']:,}  "
          f"amount(元)={a['amount_total_yuan']:,.0f}")
    import json
    print(json.dumps(band_summary(parsed), ensure_ascii=False, indent=2))

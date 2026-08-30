# -*- coding: utf-8 -*-
"""通达信 .tck/.img 解析器 + 委托级方向还原 (SIDA P0-2, 按 Hermes 研究汇总 2026-08-30)
================================================================================

.tck 36 字节定长记录 (24 字节头 + zlib, 实测 sz002361_20260827):
  [0:2]   u16 类型      0=成交(98,088) 1=委托(89,200, 含撤单)
  [2:6]   u32 时间      (H)MMSSmmm (单调不减, 含集合竞价)
  [6:14]  f64 价格      元; 撤单记录为 0.0
  [14:18] u32 数量      股
  [18:22] u32 版本魔数  恒 2015(0x7DF)
  [22:26] u32 委托号 seq 全局唯一, 成交/申报/撤单三类互斥
  [26:28] tag          "2B"主买 / "2S"主卖 / "00"申报 / "0C"撤单
  [28:32] u32 a28 → 主动买成交 seq (100% 命中)
  [32:36] u32 a32 → 主动卖成交 seq (100% 命中)

方向判定铁律 (Hermes 口径, 2026-08-30 终版):
  主动买 = 委托量 == a28 指向成交的量;  主动卖 = 委托量 == a32 指向成交的量
  两者均满足 = 双向;  均不满足 = 被动(方向未定)

.img 24 字节头 + zlib → TLV 明文 (\\x03 记录壳, \\x02+2字节ID+值):
  01代码 0T时间 04开盘 1C成交额 1E/1F最高/最低
  20-29 买1-10价 30-39 买1-10量 40-49 卖1-10价 50-59 卖1-10量
  62/63 买卖委托笔数 64 委托队列(无委托号, 被动侧仅形态)

用法:
    from src.core.tdx_tick_parser import parse_tck, classify_orders, to_ticks, parse_img
    parsed = parse_tck("samples/sz002361_20260827.tck")
    counts = classify_orders(parsed)          # 主动买/主动卖/双向/被动 计数
    ticks  = to_ticks(parsed)                 # [{d, amt, vol, price, t}], 与 dark_flow 同构
"""

from __future__ import annotations

import struct
import zlib
from typing import Any, Dict, List, Optional

REC_SIZE = 36
HEADER_SIZE = 24
MAGIC = 2015  # [18:22] 版本魔数

# tag 常量
TAG_BUY = b"2B"
TAG_SELL = b"2S"
TAG_NEW = b"00"
TAG_CANCEL = b"0C"

# .img TLV
REC_START = 0x03
REC_END = 0x04
FIELD_SEP = 0x02

_DIR_TABS: Dict[str, str] = {
    "B": "主动买",
    "S": "主动卖",
    "D": "双向",
    "P": "被动",
}


def _open_and_decompress(path: str) -> bytes:
    raw = open(path, "rb").read()
    if len(raw) < HEADER_SIZE:
        raise ValueError(f"{path}: file too small ({len(raw)}B)")
    comp_size = struct.unpack("<I", raw[8:12])[0]
    raw_size = struct.unpack("<I", raw[16:20])[0]
    dec = zlib.decompress(raw[HEADER_SIZE:HEADER_SIZE + comp_size])
    if len(dec) != raw_size:
        raise ValueError(f"{path}: decompressed {len(dec)} != header raw_size {raw_size}")
    return dec


def parse_tck(path: str) -> Dict[str, Any]:
    """解包 .tck → {"trades": [...], "orders": [...], "cancels": [...], "anchors": {...}}

    trades/orders/cancels 元素字段:
      seq / time / price / vol (共4), 另:
      trades: dir ("B"/"S")
      orders: tag ("00"), a28, a32
      cancels: tag ("0C"), target (a28 或 a32)
    """
    dec = _open_and_decompress(path)
    if len(dec) % REC_SIZE:
        raise ValueError(f"{path}: decompressed {len(dec)}B not divisible by {REC_SIZE}")
    n = len(dec) // REC_SIZE

    trades: List[Dict[str, Any]] = []
    orders: List[Dict[str, Any]] = []
    cancels: List[Dict[str, Any]] = []

    for i in range(n):
        r = dec[i * REC_SIZE:(i + 1) * REC_SIZE]
        rtype = r[0]  # 0=成交 1=委托
        seq = struct.unpack("<I", r[22:26])[0]
        ttime = struct.unpack("<I", r[2:6])[0]
        price = struct.unpack("<d", r[6:14])[0]
        vol = struct.unpack("<I", r[14:18])[0]
        tag = r[26:28]
        a28 = struct.unpack("<I", r[28:32])[0]
        a32 = struct.unpack("<I", r[32:36])[0]
        if rtype == 0:
            trades.append({
                "seq": seq, "time": ttime, "price": price, "vol": vol,
                "dir": "B" if tag == TAG_BUY else ("S" if tag == TAG_SELL else "?"),
                "amt": round(price * vol, 4),
            })
        elif tag == TAG_NEW:
            orders.append({"seq": seq, "time": ttime, "price": price, "vol": vol,
                           "tag": "00", "a28": a28, "a32": a32})
        elif tag == TAG_CANCEL:
            cancels.append({"seq": seq, "time": ttime, "price": price, "vol": vol,
                            "tag": "0C", "target": a28 or a32})
        else:
            raise ValueError(f"{path}: unknown tag {tag!r} at record {i}")

    anchors = {
        "trades": len(trades),
        "orders": len(orders),
        "cancels": len(cancels),
        "total": n,
        "price_min": min((t["price"] for t in trades if t["price"] > 0), default=None),
        "price_max": max((t["price"] for t in trades), default=None),
        "vol_total_share": sum(t["vol"] for t in trades),
        "amount_total_yuan": sum(t["amt"] for t in trades),
        "dir_B_count": sum(1 for t in trades if t["dir"] == "B"),
        "dir_S_count": sum(1 for t in trades if t["dir"] == "S"),
        "seq_max": max([t["seq"] for t in trades] + [o["seq"] for o in orders] + [c["seq"] for c in cancels], default=0),
    }
    return {"trades": trades, "orders": orders, "cancels": cancels, "anchors": anchors}


# 连续竞价起点 (H)MMSSmmm: 9:30:00.000。集合竞价(9:15-9:30)为虚拟撮合,
# 无真实主动/被动方向, 会污染方向判定 —— Hermes 口径①(2026-08-31)剔除之。
CONTINUOUS_OPEN = 93_000_000


def classify_orders(parsed: Dict[str, Any], continuous_only: bool = True) -> Dict[str, int]:
    """委托方向分类 (Hermes 口径, 2026-08-31 定夺版, 严格+连续竞价)。

    对每条 "00" 申报:
      主动买 = a28 指向的成交存在 且 该成交 tag=="2B" 且 委托量 == 该成较量
      主动卖 = a32 指向的成交存在 且 该成交 tag=="2S" 且 委托量 == 该成较量
    continuous_only=True 时仅统计 time>=93000000 的连续竞价申报
    (剔除集合竞价 127 条, 对齐 Hermes 验收 65,499)。
    返回统计: {"主动买": n, "主动卖": n, "双向": n, "被动": n, "total": n}
    """
    trade_by_seq: Dict[int, Dict[str, Any]] = {t["seq"]: t for t in parsed["trades"]}
    counts = {"主动买": 0, "主动卖": 0, "双向": 0, "被动": 0, "total": 0}
    for o in parsed["orders"]:
        if continuous_only and o["time"] < CONTINUOUS_OPEN:
            continue
        t28 = trade_by_seq.get(o["a28"])
        t32 = trade_by_seq.get(o["a32"])
        buy_match = (t28 is not None and t28["dir"] == "B"
                     and o["vol"] == t28["vol"])
        sell_match = (t32 is not None and t32["dir"] == "S"
                      and o["vol"] == t32["vol"])
        counts["total"] += 1
        if buy_match and sell_match:
            counts["双向"] += 1
            o["dir"] = "D"
        elif buy_match:
            counts["主动买"] += 1
            o["dir"] = "B"
        elif sell_match:
            counts["主动卖"] += 1
            o["dir"] = "S"
        else:
            counts["被动"] += 1
            o["dir"] = "P"
    return counts


def to_ticks(parsed: Dict[str, Any]) -> List[Dict[str, Any]]:
    """成交记录 → [{d, amt, vol, price, t}], 与 dark_flow 逐笔列表同构 (P0-2 产出)。

    d = "B"/"S" (方向)   t = 时间 (H)MMSSmmm 原始 u32
    """
    return [{"d": t["dir"], "amt": t["amt"], "vol": t["vol"],
             "price": t["price"], "t": t["time"]} for t in parsed["trades"]]


def parse_img(path: str) -> List[Dict[str, Any]]:
    """解包 .img → 十档盘口快照列表 (含委托笔数 62/63 与委托队列 64, 如落盘有)。

    每元素: {"时间","代码","开盘","成交额","最高","最低", 买1-10价, 买1-10量, 卖1-10价, 卖1-10量,
             买笔数, 卖笔数, 队列} (字段缺失时为 None)
    """
    dec = _open_and_decompress(path)
    out: List[Dict[str, Any]] = []
    for record in dec.split(bytes([REC_START])):
        if not record:
            continue
        if record[-1:] == bytes([REC_END]):
            record = record[:-1]
        fields: Dict[str, Any] = {}
        for token in record.split(bytes([FIELD_SEP])):
            if len(token) < 2:
                continue
            key = token[:2].decode("ascii", errors="replace")
            val = token[2:].decode("ascii", errors="replace")
            try:
                fields[key] = float(val)
            except ValueError:
                fields[key] = val
        snap: Dict[str, Any] = {"时间": fields.get("0T"), "代码": fields.get("01"),
                                "开盘": fields.get("04"), "成交额": fields.get("1C"),
                                "最高": fields.get("1E"), "最低": fields.get("1F"),
                                "买笔数": fields.get("62"), "卖笔数": fields.get("63"),
                                "队列": fields.get("64")}
        for i in range(1, 11):
            snap[f"买{i}价"] = fields.get(f"{20 + i - 1:02X}")
            snap[f"买{i}量"] = fields.get(f"{30 + i - 1:02X}")
            snap[f"卖{i}价"] = fields.get(f"{40 + i - 1:02X}")
            snap[f"卖{i}量"] = fields.get(f"{50 + i - 1:02X}")
        out.append(snap)
    return out


# 分档阈值(元, 按单笔/单委托金额) — 与 order_cluster 同口径
BAND_XL = 1_000_000.0   # 超大单 >= 100 万
BAND_L = 200_000.0      # 大单   >= 20 万
BAND_M = 50_000.0       # 中单   >= 5 万


def reconstruct_order_bands(parsed: Dict[str, Any],
                            cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """P0-3 委托级还原分档 (Hermes 口径)。

    对每条已分类的 "00" 申报, 以 委托量*委托价 计金额, 按方向(B/S)切四档,
    输出每档 买/卖/净额(万元)。双向(D) 同时计入买与卖(净额自对冲)。
    验收(002361 2026-08-27): 超大+6427万/大+692万/中-2198万/小-3982万, 净+939万。
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
    classify_orders(parsed)  # 填充 o["dir"]
    for o in parsed["orders"]:
        if o["price"] <= 0:
            continue
        amt = o["vol"] * o["price"]
        if amt >= band_xl:
            b = bands["超大单"]
        elif amt >= band_l:
            b = bands["大单"]
        elif amt >= band_m:
            b = bands["中单"]
        else:
            b = bands["小单"]
        b["n"] += 1
        if o["dir"] in ("B", "D"):
            b["buy"] += amt
        if o["dir"] in ("S", "D"):
            b["sell"] += amt
    out: Dict[str, Any] = {}
    wan = 1e4
    net_total = 0.0
    for name, b in bands.items():
        buy_wan = round(b["buy"] / wan, 2)
        sell_wan = round(b["sell"] / wan, 2)
        net_wan = round(buy_wan - sell_wan, 2)
        net_total += net_wan
        out[name] = {"buy_wan": buy_wan, "sell_wan": sell_wan, "net_wan": net_wan, "n": b["n"]}
    out["net_total_wan"] = round(net_total, 2)
    return out


def reconstruct_trade_bands(parsed: Dict[str, Any],
                            cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """P0-3 正确口径(Hermes 2026-08-31 定夺): 逐笔成交分档(明盘)。

    数据源 = 成交记录(type=0); 按【单笔成交金额】切四档(100万/20万/5万);
    方向 = 成交 tag(2B买/2S卖); 净额 = 买额 - 卖额。
    验收(002361 2026-08-27): 超大+6427万/大+692万/中-2198万/小-3982万, 净+939万。
    (注: 非"委托分档"、非"拆单聚簇"——.tck 无母单↔子单聚簇键, 拆单留待 TQ8 遗留项。)
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
    for t in parsed["trades"]:
        if t["price"] <= 0:
            continue
        amt = t["amt"]
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
    net_total = 0.0
    for name, b in bands.items():
        buy_wan = round(b["buy"] / wan, 2)
        sell_wan = round(b["sell"] / wan, 2)
        net_wan = round(buy_wan - sell_wan, 2)
        net_total += net_wan
        out[name] = {"buy_wan": buy_wan, "sell_wan": sell_wan, "net_wan": net_wan, "n": b["n"]}
    out["net_total_wan"] = round(net_total, 2)
    return out


def acceptance_check(path: str) -> Dict[str, Any]:
    """P0-2 验收: 输出记录总数 + 方向分类 (对照 Hermes 实测:
    187,288 条 / 主动买 26,119 / 主动卖 23,870 / 双向 263 / 被动 15,247)"""
    parsed = parse_tck(path)
    counts = classify_orders(parsed, continuous_only=True)
    ticks = to_ticks(parsed)
    return {
        "anchors": parsed["anchors"],
        "order_direction": counts,
        "trade_bands": reconstruct_trade_bands(parsed),
        "ticks": len(ticks),
    }


if __name__ == "__main__":
    import json
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "samples/sz002361_20260827.tck"
    res = acceptance_check(path)
    a = res["anchors"]
    print(f"total={a['total']} trades={a['trades']} (B={a['dir_B_count']}/S={a['dir_S_count']}) "
          f"orders={a['orders']} cancels={a['cancels']}")
    print(f"price {a['price_min']}..{a['price_max']}  vol(股)={a['vol_total_share']:,}  "
          f"amount(元)={a['amount_total_yuan']:,.0f}")
    print("order_direction(连续竞价):", json.dumps(res["order_direction"], ensure_ascii=False))
    print("trade_bands(逐笔成交):", json.dumps(res["trade_bands"], ensure_ascii=False))
    print("ticks:", res["ticks"])
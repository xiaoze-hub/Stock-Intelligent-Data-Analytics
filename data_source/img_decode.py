# -*- coding: utf-8 -*-
"""通达信超盘回放 .img 十档盘口解析器(TQ4-a1)
==========================================

文件格式(2026-08-29 实测 sz002361_20260827.img):
- 24 字节头: [8:12]=comp_size(小端), [16:20]=raw_size(小端)
- offset 24 起 zlib 流(78 9c), 解压后=TLV 明文(3,257,631B → 5,066 条记录)
- 记录: \\x03 开头, \\x04 结尾; 字段 = \\x02 + 2字节ASCII字段ID + 值字符串
- 字段语义(002361 实测):
    01=代码  0T=时间(HHMMSS.mmm)  04=开盘  1C=成交额  1E/1F=最高/最低
    20-29=买1-10价  30-39=买1-10量  40-49=卖1-10价  50-59=卖1-10量
    (记录内字段顺序为 20,30,21,31,... 价/量交错)
- 注意: 集合竞价阶段(9:30 前)十档未成形、字段为 0

用法:
    from img_decode import decode_img
    df = decode_img("sz002361_20260827.img")          # 全部记录
    df = decode_img(path, after="093000")             # 仅连续竞价(九段后)
"""

from __future__ import annotations

import struct
import zlib
from typing import List, Optional

import pandas as pd

HEADER_SIZE = 24
REC_START = 0x03
REC_END = 0x04
FIELD_SEP = 0x02

# 字段 ID → 输出列名(买1-10 价/量, 卖1-10 价/量)
_BID_PRICE = [f"买{i}价" for i in range(1, 11)]
_BID_VOL = [f"买{i}量" for i in range(1, 11)]
_ASK_PRICE = [f"卖{i}价" for i in range(1, 11)]
_ASK_VOL = [f"卖{i}量" for i in range(1, 11)]

COLUMNS = (
    ["时间", "代码", "开盘", "成交额", "最高", "最低"]
    + _BID_PRICE + _BID_VOL + _ASK_PRICE + _ASK_VOL
)


def _parse_fields(record: bytes) -> dict:
    """单条 TLV 记录(已去 \\x03 壳) → {字段ID: 值字符串}。"""
    fields: dict = {}
    body = record
    if body[-1:] == bytes([REC_END]):
        body = body[:-1]
    for token in body.split(bytes([FIELD_SEP])):
        if len(token) < 2:
            continue
        fields[token[:2].decode("ascii", errors="replace")] = token[2:].decode(
            "ascii", errors="replace"
        )
    return fields


def _num(s: str) -> Optional[float]:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def decode_img(path: str, after: Optional[str] = None) -> pd.DataFrame:
    """解析 .img → 十档盘口序列 DataFrame。

    :param path: .img 文件路径
    :param after: "HHMMSS" 或 "HHMMSS.mmm", 只保留时间 >= 该值的记录
                  (连续竞价十档用 after="093000"; 缺省全量)
    """
    raw = open(path, "rb").read()
    if len(raw) < HEADER_SIZE:
        raise ValueError(f"{path}: file too small ({len(raw)}B)")
    comp_size = struct.unpack("<I", raw[8:12])[0]
    raw_size = struct.unpack("<I", raw[16:20])[0]
    dec = zlib.decompress(raw[HEADER_SIZE:HEADER_SIZE + comp_size])
    if len(dec) != raw_size:
        raise ValueError(f"{path}: decompressed {len(dec)}B != header raw_size {raw_size}")

    rows: List[dict] = []
    # 按记录分隔符 \x04 切, 每段应以 \x03 开头
    for chunk in dec.split(bytes([REC_END])):
        if not chunk or chunk[0] != REC_START:
            continue
        f = _parse_fields(chunk[1:])
        t = f.get("0T", "")
        # 归一化为 6 位 HHMMSS(上午时间缺前导零: "83627.000" → "083627")
        t6 = t.split(".")[0].zfill(6)
        if after and t6 < after.replace(".", "").zfill(6):
            continue
        row = {
            "时间": t6,
            "代码": f.get("01", ""),
            "开盘": _num(f.get("04", "")),
            "成交额": _num(f.get("1C", "")),
            "最高": _num(f.get("1E", "")),
            "最低": _num(f.get("1F", "")),
        }
        for i in range(10):
            row[_BID_PRICE[i]] = _num(f.get(f"{20 + i}", ""))
            row[_BID_VOL[i]] = _num(f.get(f"{30 + i}", ""))
            row[_ASK_PRICE[i]] = _num(f.get(f"{40 + i}", ""))
            row[_ASK_VOL[i]] = _num(f.get(f"{50 + i}", ""))
        rows.append(row)

    df = pd.DataFrame(rows, columns=COLUMNS)
    return df


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "samples/sz002361_20260827.img"
    df = decode_img(path)
    print(f"records={len(df)}  time {df['时间'].iloc[0]} .. {df['时间'].iloc[-1]}")
    live = decode_img(path, after="093000")
    print(f"continuous-auction rows (>=093000): {len(live)}")
    with pd.option_context("display.width", 200):
        print(live[["时间", "买1价", "买1量", "卖1价", "卖1量", "最高", "最低"]].head(5).to_string(index=False))
        print("...")
        print(live[["时间", "买1价", "买1量", "卖1价", "卖1量"]].tail(3).to_string(index=False))

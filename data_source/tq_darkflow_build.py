# -*- coding: utf-8 -*-
"""TQ4-c: 扫描 samples/ 下成对的 .tck/.img 落盘文件 → 产出 darkflow JSON
半自动流程: 超盘回放落盘(暂人工触发) → 把 {code}_{date}.tck/.img 放入本目录
→ 运行本脚本 → C:\TdxQ\darkflow\{code}_{date}.json 自动生成。
"""
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from order_cluster import dump_darkflow, parse_tck  # noqa: E402

SAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")
DARKFLOW = r"C:\TdxQ\darkflow"

for tck in glob.glob(os.path.join(SAMPLES, "*.tck")):
    base = os.path.basename(tck)
    m = re.match(r"([a-z]{2})(\d{6})_(\d{8})\.tck", base)
    if not m:
        print("skip (naming):", base)
        continue
    mkt, num, ymd = m.groups()
    code = f"{num}.{'SZ' if mkt == 'sz' else 'SH'}"
    parsed = parse_tck(tck)
    out = os.path.join(DARKFLOW, f"{code}_{ymd}.json")
    payload = dump_darkflow(code, ymd, out, parsed)
    print("darkflow:", out)
    print("  bands:", {k: v.get("net_wan") for k, v in payload["bands"].items() if k != "zero_price_skipped"})

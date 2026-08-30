# -*- coding: utf-8 -*-
"""TQ 主力意图资金 · CLI 运行器

用法(在本目录):
    python run_intent.py                          # 跑 mi1_config.json 的 watch_pool
    python run_intent.py --codes 002361.SZ,603737.SH
    python run_intent.py --output ./out           # 输出目录(默认 ./output)

输出:
    output/main_force_<日期>.json  评级+证据链(供 LLM/人消费)
    output/main_force_<日期>.csv   标准化 DataFrame
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tq_intent import evaluate_rules  # noqa: E402
from tq_main_force import TQClient, build_main_force_dataframe  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("mi1")


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="MI1 当日主力意图评级")
    ap.add_argument("--config", default=os.path.join(here, "mi1_config.json"))
    ap.add_argument("--codes", default="", help="逗号分隔, 缺省用配置 watch_pool")
    ap.add_argument("--output", default=os.path.join(here, "output"))
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    codes = [c.strip() for c in args.codes.split(",") if c.strip()] or list(cfg["watch_pool"])

    client = TQClient(url=cfg.get("tq_url", "http://127.0.0.1:17709/"),
                      timeout_s=float(cfg.get("timeout_s", 12)))
    df = build_main_force_dataframe(
        client, codes,
        kline_days=int(cfg.get("kline_days", 60)),
        with_sentiment=True,
    )
    if df.empty:
        logger.error("无可用数据(检查 TQ 网关/通达信是否在线)")
        sys.exit(2)

    os.makedirs(args.output, exist_ok=True)
    date_tag = datetime.now().strftime("%Y%m%d")
    csv_path = os.path.join(args.output, f"main_force_{date_tag}.csv")
    json_path = os.path.join(args.output, f"main_force_{date_tag}.json")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    results = []
    for _, row in df.iterrows():
        record = row.to_dict()
        verdict = evaluate_rules(record, cfg.get("rules", {}))
        record["评级"] = verdict["rating"]
        record["评级标签"] = verdict["label"]
        results.append({
            "代码": record["代码"],
            "时间": record["时间"],
            "评级": verdict["rating"],
            "评级标签": verdict["label"],
            "命中规则": verdict["matched"],
            "证据链": verdict["evidence"],
            "数据": record,
        })

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now().isoformat(),
                   "config": cfg.get("rules", {}),
                   "results": results}, f, ensure_ascii=False, indent=2, default=str)

    print(df[["代码", "时间", "涨幅", "量比", "买撤量", "卖撤量", "内盘", "外盘", "60日分位"]].to_string(index=False))
    for r in results:
        print(f"\n{r['代码']}  评级 {r['评级']}({r['评级标签']})  命中: {', '.join(r['命中规则']) or '无'}")
        for ev in r["证据链"]:
            print(f"  - [{ev['rule']}] {ev['口诀']}  <- {ev['判定逻辑']}")
    print(f"\n输出: {csv_path}\n输出: {json_path}")


if __name__ == "__main__":
    main()

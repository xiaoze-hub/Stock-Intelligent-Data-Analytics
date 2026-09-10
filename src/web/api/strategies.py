"""策略库 API: 列出/查看/应用借鉴 alphasift 的 YAML 策略到单只股票。

设计原则:
- 策略只用到可拿到的字段(实时或盘后)
- 单只股票评分(快速) + 全市场扫描(慢, 盘后)
- 字段缺失时显式标注, 不静默跳过
"""
import logging
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()
logger = logging.getLogger(__name__)

# 策略 YAML 路径(借鉴 alphasift 的格式, 翻译为 PanWatch 可用字段子集)
STRATEGIES_FILE = Path(__file__).parent.parent.parent.parent / "strategies" / "panwatch_strategies.yaml"


def _load_strategies() -> dict:
    if not STRATEGIES_FILE.exists():
        raise HTTPException(503, f"策略文件不存在: {STRATEGIES_FILE}")
    try:
        data = yaml.safe_load(STRATEGIES_FILE.read_text(encoding="utf-8"))
        return data
    except Exception as e:
        raise HTTPException(500, f"策略文件解析失败: {e}")


@router.get("/list")
async def list_strategies():
    """列出所有可用策略。"""
    data = _load_strategies()
    completeness = data.get("data_completeness", {})

    items = []
    for key, cfg in data.items():
        if key == "data_completeness" or not isinstance(cfg, dict):
            continue
        strategy_data_status = completeness.get("strategy_data_status", {}).get(key, {})
        items.append({
            "id": key,
            "display_name": cfg.get("display_name", key),
            "description": cfg.get("description", ""),
            "category": cfg.get("category", "other"),
            "tags": cfg.get("tags", []),
            "ui_badge": cfg.get("ui_badge", ""),
            "source": cfg.get("source", ""),
            "filter": cfg.get("filter", {}),
            "eod_fields": list(_eod_fields(cfg)),  # 需要的盘后字段
            "data_window": strategy_data_status.get("available_in", "realtime"),
            "available_now": strategy_data_status.get("available_in", "realtime") == "realtime",
        })
    return {"items": items, "total": len(items)}


@router.get("/hitrate-board")
def get_hitrate_board(window_days: int = 28) -> dict:
    """信号周滚动 HitRate 榜(L1): 按策略×持有期统计近 window_days 后验。

    口径见 src/core/signal_hitrate.py(到目标价或持有期正收益=中)。
    outcome 日级评估, 6h 缓存(P1 单飞, 多账号只算一次)。
    """
    from src.core.signal_hitrate import BOARD_CACHE_TTL_S, compute_hitrate_board
    from src.web.cache.biz_cache import biz_cache
    from src.web.database import SessionLocal

    window_days = max(7, min(int(window_days or 28), 90))

    def _build():
        db = SessionLocal()
        try:
            return compute_hitrate_board(db, window_days=window_days)
        finally:
            db.close()

    return biz_cache.get_or_fetch(
        f"hitrate:board:{window_days}", ttl=BOARD_CACHE_TTL_S, fetch=_build
    )


@router.get("/{strategy_id}")
async def get_strategy(strategy_id: str):
    """查看单个策略详情。"""
    data = _load_strategies()
    if strategy_id not in data:
        raise HTTPException(404, f"策略不存在: {strategy_id}")
    cfg = data[strategy_id]
    completeness = data.get("data_completeness", {}).get("strategy_data_status", {}).get(strategy_id, {})
    return {
        "id": strategy_id,
        "display_name": cfg.get("display_name", strategy_id),
        "description": cfg.get("description", ""),
        "category": cfg.get("category", "other"),
        "tags": cfg.get("tags", []),
        "filter": cfg.get("filter", {}),
        "ranking_factors": cfg.get("ranking_factors", {}),
        "eod_only_fields": list(_eod_fields(cfg)),
        "ui_badge": cfg.get("ui_badge", ""),
        "source": cfg.get("source", ""),
        "data_window": completeness.get("available_in", "realtime"),
    }


class ApplyRequest(BaseModel):
    strategy_id: str
    symbol: str
    market: str = "CN"


class ScanRequest(BaseModel):
    strategy_id: str
    market: str = "CN"
    limit: int = 50          # 返回 top N
    universe: str = "all"    # all=全市场, watchlist=自选+种子池
    min_score: float = 0.0   # 最低分过滤
    symbol_limit: int = 0    # 0=不限, 否则限制扫描股票数(调试用)
    # 自定义股票池(2026-08-22 共振查询): 传入则优先于 universe,
    # 只扫这几只(如 问小达+问财 合并后的候选), 上限 100 防滥用
    symbols: list[str] = Field(default_factory=list)


def _quote_to_dict(q) -> dict:
    """Quote 对象 → dict(与 apply 现有归一化输出同形)。"""
    if q is None:
        return {}
    if isinstance(q, dict):
        return q
    if hasattr(q, "__dict__"):
        return {k: v for k, v in vars(q).items() if not k.startswith("_")}
    return {"current_price": getattr(q, "current_price", None)}


def _evaluate_strategy(cfg: dict, q: dict, strategy_id: str, symbol: str, market: str) -> dict:
    """对单只股票的 dict 行情执行策略硬过滤 + 因子打分(apply/scan 共用)。

    纯函数, 不发起网络请求。字段缺失 → missing_fields 标注并跳过该过滤项。
    """
    filter_cfg = cfg.get("filter", {})
    ranking = cfg.get("ranking_factors", {})

    def getf(key, default=None):
        return q.get(key, default)

    # 字段名规范化: 腾讯行情 pe_ratio → pe_ttm 统一口径
    if q.get("pe_ttm") is None and q.get("pe_ratio") is not None:
        q = {**q, "pe_ttm": q["pe_ratio"]}

    current_price = getf("current_price")
    change_pct = getf("change_pct")
    volume_ratio = getf("volume_ratio")
    turnover_rate = getf("turnover_rate")
    # 2026-08-26 顺手修: 行情对象字段名是 turnover(成交额,元), 'amount' 恒为 None
    amount = getf("amount") or getf("turnover")  # 元
    open_p = getf("open")
    high = getf("high")
    low = getf("low")
    pe_ttm = getf("pe_ttm")
    if pe_ttm is None:
        pe_ttm = getf("pe_ratio")  # 腾讯行情字段名
    pb_ratio = getf("pb_ratio")
    market_cap = getf("total_market_value")  # 亿

    # 硬过滤 + 标注缺失项
    passed = True
    failed_filters = []
    missing_fields = []

    def check(name, actual, op, threshold):
        nonlocal passed
        if actual is None:
            # 2026-08-23 P3 修复: 字段缺失 = 无法验证 = 不通过(保守语义)。
            # 此前"跳过该条件"会让放量策略在无量能数据时裸筛全市场。
            missing_fields.append(name)
            passed = False
            failed_filters.append({"field": name, "actual": None, "required": op, "threshold": threshold})
            return
        ok = (
            (op == "min" and actual >= threshold)
            or (op == "max" and actual <= threshold)
        )
        if not ok:
            passed = False
            failed_filters.append({"field": name, "actual": actual, "required": op, "threshold": threshold})

    # 实时字段(2026-08-23 P3: 字段缺失时也进 check → missing 标注 + 不通过,
    # 此前的 "is not None" 守卫会让缺数据的票静默通过全部过滤)
    if "price_min" in filter_cfg:
        check("current_price", current_price, "min", filter_cfg["price_min"])
    if "price_max" in filter_cfg:
        check("current_price", current_price, "max", filter_cfg["price_max"])
    if "change_pct_min" in filter_cfg:
        check("change_pct", change_pct, "min", filter_cfg["change_pct_min"])
    if "change_pct_max" in filter_cfg:
        check("change_pct", change_pct, "max", filter_cfg["change_pct_max"])
    if "volume_ratio_min" in filter_cfg:
        check("volume_ratio", volume_ratio, "min", filter_cfg["volume_ratio_min"])
    if "volume_ratio_max" in filter_cfg:
        check("volume_ratio", volume_ratio, "max", filter_cfg["volume_ratio_max"])
    if "turnover_rate_min" in filter_cfg:
        check("turnover_rate", turnover_rate, "min", filter_cfg["turnover_rate_min"])
    if "turnover_rate_max" in filter_cfg:
        check("turnover_rate", turnover_rate, "max", filter_cfg["turnover_rate_max"])
    # 盘后字段(pe_ttm/pb/market_cap) — 既可能在 filter 里也可能在 cfg 顶层
    for prefix in ("pe_ttm", "pb", "market_cap"):
        for suffix in ("_min", "_max"):
            key = f"{prefix}{suffix}"
            threshold = filter_cfg.get(key)
            if threshold is None:
                threshold = cfg.get(key)  # 兜底从 cfg 顶层取(dual_low 写法)
            if threshold is None:
                continue
            actual_field = {"pe_ttm": "pe_ttm", "pb": "pb_ratio", "market_cap": "total_market_value"}[prefix]
            actual = getf(actual_field)
            if actual is None:
                missing_fields.append(actual_field)
                passed = False  # 2026-08-23 P3: 缺失 = 无法验证 = 不通过
                failed_filters.append({"field": actual_field, "actual": None, "required": suffix.lstrip("_"), "threshold": threshold})
            elif suffix == "_min" and actual < threshold:
                passed = False
                failed_filters.append({"field": actual_field, "actual": actual, "required": "min", "threshold": threshold})
            elif suffix == "_max" and actual > threshold:
                passed = False
                failed_filters.append({"field": actual_field, "actual": actual, "required": "max", "threshold": threshold})

    # 因子打分(简化版: 归一化到 0-100)
    score = 50.0
    score_breakdown = []
    if "low_pe" in ranking and pe_ttm is not None and pe_ttm > 0:
        # PE 越低分越高(PE=0 得 100, PE=30 得 0)
        s = max(0, min(100, 100 - pe_ttm * 3.3))
        # 2026-08-23 P3 修复: PE<3 多为一次性收益/ST 脱帽等异常, 封顶防价值陷阱排第一
        if pe_ttm < 3:
            s = min(s, 55.0)
        score += (s - 50) * ranking["low_pe"]
        score_breakdown.append({"factor": "low_pe", "raw": pe_ttm, "score": round(s, 1), "weight": ranking["low_pe"]})
    if "low_pb" in ranking and pb_ratio is not None and pb_ratio > 0:
        s = max(0, min(100, 100 - pb_ratio * 25))
        score += (s - 50) * ranking["low_pb"]
        score_breakdown.append({"factor": "low_pb", "raw": pb_ratio, "score": round(s, 1), "weight": ranking["low_pb"]})
    if "volume_ratio" in ranking and volume_ratio is not None:
        # 量比 1.0 = 50分, 2.0+ = 100, 0.5 = 0
        s = max(0, min(100, volume_ratio * 50))
        score += (s - 50) * ranking["volume_ratio"]
        score_breakdown.append({"factor": "volume_ratio", "raw": volume_ratio, "score": round(s, 1), "weight": rounding_safe(ranking["volume_ratio"])})
    if "change_pct" in ranking and change_pct is not None:
        # 涨跌幅 -5~+5% 映射到 0~100
        s = max(0, min(100, 50 + change_pct * 10))
        score += (s - 50) * ranking["change_pct"]
        score_breakdown.append({"factor": "change_pct", "raw": change_pct, "score": round(s, 1), "weight": rounding_safe(ranking["change_pct"])})
    if "turnover_rate" in ranking and turnover_rate is not None:
        # 换手率 0~8% 映射到 0~100
        s = max(0, min(100, turnover_rate * 12.5))
        score += (s - 50) * ranking["turnover_rate"]
        score_breakdown.append({"factor": "turnover_rate", "raw": turnover_rate, "score": round(s, 1), "weight": rounding_safe(ranking["turnover_rate"])})
    if "stable_amount" in ranking and amount is not None and amount > 0:
        # 成交额 1亿=50, 5亿+=100, 0.1亿=0
        s = max(0, min(100, 25 + 15 * (amount ** 0.3)))
        score += (s - 50) * ranking["stable_amount"]
        score_breakdown.append({"factor": "stable_amount", "raw": amount, "score": round(s, 1), "weight": rounding_safe(ranking["stable_amount"])})
    if "stable" in ranking and turnover_rate is not None and volume_ratio is not None:
        # 稳定 = 换手率中等 + 量比稳定(1附近)
        stability = 100 - abs(turnover_rate - 2.0) * 20 - abs(volume_ratio - 1.0) * 15
        s = max(0, min(100, stability))
        score += (s - 50) * ranking["stable"]
        score_breakdown.append({"factor": "stable", "raw": f"turnover={turnover_rate}, vol_ratio={volume_ratio}", "score": round(s, 1), "weight": rounding_safe(ranking["stable"])})
    if "oversold" in ranking and change_pct is not None:
        # 跌越多(超卖)分越高, change_pct=-5 → 100, +5 → 0
        s = max(0, min(100, 50 - change_pct * 10))
        score += (s - 50) * ranking["oversold"]
        score_breakdown.append({"factor": "oversold", "raw": change_pct, "score": round(s, 1), "weight": rounding_safe(ranking["oversold"])})
    if "reversal" in ranking and change_pct is not None:
        # 反转信号: 企稳度打分 — 涨/平(企稳)=满分, 继续跌按跌幅衰减(-5% → 50, -10% → 0)
        # 2026-08-23 P3 修复: 此前方向写反(涨 0 分跌加分, 与"企稳"语义相反)
        s = 100 if change_pct >= 0 else max(0, 100 + change_pct * 10)
        score += (s - 50) * ranking["reversal"]
        score_breakdown.append({"factor": "reversal", "raw": change_pct, "score": round(s, 1), "weight": rounding_safe(ranking["reversal"])})

    score = max(0, min(100, round(score, 1)))

    return {
        "strategy_id": strategy_id,
        "symbol": symbol,
        "market": market,
        "passed": passed,
        "score": score,
        "score_breakdown": score_breakdown,
        "failed_filters": failed_filters,
        "missing_fields": missing_fields,
        "current_data": {
            "current_price": current_price,
            "change_pct": change_pct,
            "volume_ratio": volume_ratio,
            "turnover_rate": turnover_rate,
            "amount": amount,
            "pe_ttm": pe_ttm,
            "pb_ratio": pb_ratio,
            "market_cap": market_cap,
        },
    }


@router.post("/scan")
async def scan_strategy(req: ScanRequest):
    """批量选股: 用策略硬过滤扫描全市场/候选池, 返回通过名单(按分数排序)。

    - universe=all: 全市场 A 股(优先缓存列表, 东财/akshare 兜底)
    - universe=watchlist: 自选 + 内置种子池(快, ~200 只)
    - symbols 非空: 只扫传入的自定义股票池(共振查询精筛用, ≤100 只)
    - 行情走腾讯批量接口(免费, 100 只/批, 盘中含 PE/PB/市值全字段)
    """
    from src.web.stock_list import get_stock_list
    from marketdata.vendors.tencent import TencentQuoteVendor
    from marketdata import Symbol

    data = _load_strategies()
    if req.strategy_id not in data:
        raise HTTPException(404, f"策略不存在: {req.strategy_id}")
    cfg = data[req.strategy_id]

    # 1. 确定扫描股票池
    mkt = (req.market or "CN").strip().upper()
    if req.symbols:
        # 自定义股票池(共振查询): 只保留 6 位数字码, 去重, 截断 100
        symbols = list(dict.fromkeys(s.strip() for s in req.symbols if str(s).strip().isdigit()))
        symbols = [s for s in symbols if s.startswith(("60", "00", "30", "68"))][:100]
    elif req.universe == "watchlist":
        from src.web.database import SessionLocal
        from src.web.models import Stock
        db = SessionLocal()
        try:
            rows = db.query(Stock).all()
        finally:
            db.close()
        symbols = [str(s.symbol).strip() for s in rows if str(s.market) == mkt]
        # 内置种子池补充
        from src.core.entry_candidates import MARKET_SCAN_SEED_SYMBOLS
        symbols += [s for s in MARKET_SCAN_SEED_SYMBOLS.get(mkt, []) if s not in symbols]
    else:
        all_stocks = get_stock_list()
        symbols = []
        for s in all_stocks:
            code = str(s.get("symbol") or s.get("code") or "").strip()
            market = str(s.get("market") or s.get("market_code") or "").strip().upper()
            if not code or not code.isdigit():
                continue
            if market != mkt:
                # 兼容缓存里 market 是中文或缺失: A股默认 CN
                if mkt == "CN" and market in ("", "A股", "CN", "SH", "SZ"):
                    pass
                else:
                    continue
            # 只要沪深主板+创业板 6位代码(排除北交所 4/8 开头)
            if mkt == "CN" and not code.startswith(("60", "00", "30", "68")):
                continue
            symbols.append(code)
        symbols = list(dict.fromkeys(symbols))

    if req.symbol_limit and req.symbol_limit > 0:
        symbols = symbols[: req.symbol_limit]
    if not symbols:
        return {"items": [], "total": 0, "scanned": 0, "message": "股票池为空"}

    # 2. 腾讯批量行情(100只/批)
    vendor = TencentQuoteVendor()
    quote_map: dict[str, dict] = {}
    batch_size = 100
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i : i + batch_size]
        try:
            syms = [Symbol.parse(c, mkt) for c in batch]
            quotes = vendor.fetch(syms, {})
            for q in quotes:
                d = _quote_to_dict(q)
                quote_map[str(q.symbol)] = d
        except Exception as e:
            logger.warning(f"[scan] 批量行情失败 {i}..{i+batch_size}: {e}")

    # 3. 逐只评估
    results = []
    for code in symbols:
        q = quote_map.get(code)
        if not q or not q.get("current_price"):
            continue
        r = _evaluate_strategy(cfg, q, req.strategy_id, code, mkt)
        if r["passed"] and r["score"] >= req.min_score:
            results.append(r)

    # 4. 按分数排序, 取 top N
    results.sort(key=lambda x: x["score"], reverse=True)
    top = results[: req.limit]
    return {
        "items": [
            {
                "symbol": r["symbol"],
                "name": quote_map.get(r["symbol"], {}).get("name", ""),
                "market": r["market"],
                "score": r["score"],
                "score_breakdown": r["score_breakdown"],
                "current_data": r["current_data"],
                "missing_fields": r["missing_fields"],
            }
            for r in top
        ],
        "total": len(results),
        "scanned": len(symbols),
        "quoted": len(quote_map),
    }


@router.post("/apply")
async def apply_strategy(req: ApplyRequest):
    """应用策略到单只股票: 硬过滤 + 因子打分。

    用现有 quotes 拿实时字段; 盘后字段如果有则用, 没有则跳过对应过滤项。
    """
    from src.core.marketdata_client import get_market_data

    data = _load_strategies()
    if req.strategy_id not in data:
        raise HTTPException(404, f"策略不存在: {req.strategy_id}")
    cfg = data[req.strategy_id]

    # 拉取股票行情(用 quotes, 通用接口)
    try:
        quotes = get_market_data().quotes([req.symbol], market=req.market)
    except Exception as e:
        logger.warning(f"拉取行情失败 {req.symbol}: {e}")
        quotes = []

    q = _quote_to_dict(quotes[0]) if quotes else None
    if not q or not q.get("current_price"):
        raise HTTPException(404, f"未找到行情: {req.symbol} ({req.market})")

    return _evaluate_strategy(cfg, q, req.strategy_id, req.symbol, req.market)


def _eod_fields(cfg: dict) -> set:
    """从 filter + 顶层 字段里识别哪些是盘后字段。"""
    eod_keys = {"pe_ttm_max", "pe_ttm_min", "pb_max", "pb_min", "market_cap_min", "market_cap_max"}
    f = cfg.get("filter", {})
    found = {k for k in f if k in eod_keys}
    # 顶层也可能有(dual_low 把 pe_ttm_max 放在 cfg 顶层)
    for k in cfg:
        if k in eod_keys:
            found.add(k)
    return found


def rounding_safe(v):
    if v is None:
        return 0
    try:
        return round(float(v), 2)
    except Exception:
        return 0
import asyncio
import json
import logging
import time as _time

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from src.web.database import get_db
from src.web.models import Stock
from pydantic import BaseModel, Field

from src.core.marketdata_client import md_quote_rows
from src.core.quote_period import classify_quote_period
from src.models.market import MarketCode

router = APIRouter()
logger = logging.getLogger(__name__)


class QuoteItem(BaseModel):
    symbol: str = Field(..., description="股票代码")
    market: str = Field(..., description="市场: CN/HK/US")


class QuoteBatchRequest(BaseModel):
    items: list[QuoteItem]


def _parse_market(market: str) -> MarketCode:
    # 兼容数据源返回的交易所代码(SH/SZ/BJ 均属 A股 CN 市场)
    if (market or "").upper() in ("SH", "SZ", "BJ"):
        market = "CN"
    try:
        return MarketCode(market)
    except ValueError:
        raise HTTPException(400, f"不支持的市场: {market}")


def _quote_to_response(symbol: str, market: MarketCode, quote: dict | None) -> dict:
    if not quote:
        return {
            "symbol": symbol,
            "market": market.value,
            "name": None,
            "current_price": None,
            "change_pct": None,
            "change_amount": None,
            "prev_close": None,
            "open_price": None,
            "high_price": None,
            "low_price": None,
            "volume": None,
            "turnover": None,
            "turnover_rate": None,
            "volume_ratio": None,
            "pe_ratio": None,
            "pb_ratio": None,
            "total_market_value": None,
            "circulating_market_value": None,
            "quote_time": None,
            "quote_date": None,
            "source": None,
            "daily_pnl_period": "unknown",
        }

    quote_date = quote.get("quote_date")
    return {
        "symbol": symbol,
        "market": market.value,
        "name": quote.get("name"),
        "current_price": quote.get("current_price"),
        "change_pct": quote.get("change_pct"),
        "change_amount": quote.get("change_amount"),
        "prev_close": quote.get("prev_close"),
        "open_price": quote.get("open_price"),
        "high_price": quote.get("high_price"),
        "low_price": quote.get("low_price"),
        "volume": quote.get("volume"),
        "turnover": quote.get("turnover"),
        "turnover_rate": quote.get("turnover_rate"),
        "volume_ratio": quote.get("volume_ratio"),
        "pe_ratio": quote.get("pe_ratio"),
        "pb_ratio": quote.get("pb_ratio"),
        "total_market_value": quote.get("total_market_value"),
        "circulating_market_value": quote.get("circulating_market_value"),
        "quote_time": quote.get("quote_time"),
        "quote_date": quote_date,
        "source": quote.get("source") or "",
        "daily_pnl_period": classify_quote_period(quote_date, market.value),
    }


@router.get("/{symbol}")
async def get_quote(symbol: str, market: str = "CN"):
    """获取单只股票实时行情"""
    market_code = _parse_market(market)
    rows = await asyncio.to_thread(md_quote_rows, [symbol], market_code.value)
    if not rows:
        raise HTTPException(404, "行情不存在")
    quote_map = {item.get("symbol"): item for item in rows}
    quote = quote_map.get(symbol)
    if not quote:
        raise HTTPException(404, "行情不存在")
    return _quote_to_response(symbol, market_code, quote)


@router.post("/batch")
async def get_quotes_batch(payload: QuoteBatchRequest):
    """批量获取股票实时行情"""
    if not payload.items:
        return []

    market_items: dict[MarketCode, list[str]] = {}
    for item in payload.items:
        market_code = _parse_market(item.market)
        market_items.setdefault(market_code, []).append(item.symbol)

    quotes_by_market: dict[MarketCode, dict[str, dict]] = {}
    for market_code, symbols in market_items.items():
        rows = await asyncio.to_thread(md_quote_rows, symbols, market_code.value)
        quotes_by_market[market_code] = {item.get("symbol"): item for item in rows}

    results = []
    for item in payload.items:
        market_code = _parse_market(item.market)
        quote = quotes_by_market.get(market_code, {}).get(item.symbol)
        results.append(_quote_to_response(item.symbol, market_code, quote))

    return results


@router.get("/{symbol}/more-info")
async def get_more_info(symbol: str, market: str = "CN"):
    """TQ 扩展指标(104字段, 含封单/竞价/连板/估值等)。仅 CN 且 TQ 在线时有数。"""
    from src.core.marketdata_client import md_more_info

    market_code = _parse_market(market)
    if market_code != MarketCode.CN:
        raise HTTPException(400, "more-info 仅支持 CN 市场(TQ 扩展指标)")
    rows = await asyncio.to_thread(md_more_info, [symbol], market_code.value)
    if not rows:
        raise HTTPException(404, "扩展指标不存在(TQ 未连接或该股无数据)")
    return rows[0]


@router.get("/{symbol}/dark-flow-tq")
async def get_dark_flow_tq(symbol: str, market: str = "CN"):
    """通达信 L2 暗盘资金(逐笔还原+十档盘口+自建分档, 盘后, ZCode TQ4 采集)。"""
    from src.core.marketdata_client import md_dark_flow_tq

    market_code = _parse_market(market)
    if market_code != MarketCode.CN:
        raise HTTPException(400, "dark-flow-tq 仅支持 CN 市场")
    data = await asyncio.to_thread(md_dark_flow_tq, symbol, market_code.value)
    if not data:
        raise HTTPException(404, "暗盘资金无数据(TQ4 盘后采集未覆盖该股或尚未采集)")
    return data


def _build_overlays(symbol: str, market: str, positions, trades, actions) -> dict:
    """纯函数(M5): 持仓/成交/除权行 → {cost_lines, trade_markers, ex_marks}。"""
    cost_lines: list = []
    trade_markers: list = []
    ex_marks: list = []

    def _day(v) -> str:
        try:
            if v is None:
                return ""
            return v.date().isoformat() if hasattr(v, "date") else str(v)[:10]
        except Exception:
            return ""

    for p in positions or []:
        entry = getattr(p, "entry_price", None)
        if entry:
            cost_lines.append({"price": float(entry), "label": f"持仓成本 {entry}", "kind": "cost"})
            sl = getattr(p, "stop_loss", None)
            if sl:
                cost_lines.append({"price": float(sl), "label": f"止损 {sl}", "kind": "stop"})
            tp = getattr(p, "target_price", None)
            if tp:
                cost_lines.append({"price": float(tp), "label": f"目标 {tp}", "kind": "target"})
            d = _day(getattr(p, "opened_at", None))
            if d:
                trade_markers.append({
                    "date": d, "side": "buy", "price": float(entry),
                    "label": f"买入 {getattr(p, 'quantity', '') or ''}股",
                })
    for t in trades or []:
        entry = getattr(t, "entry_price", None)
        d = _day(getattr(t, "opened_at", None))
        if d and entry:
            trade_markers.append({"date": d, "side": "buy", "price": float(entry), "label": "买入"})
        exitp = getattr(t, "exit_price", None)
        d2 = _day(getattr(t, "closed_at", None))
        if d2 and exitp:
            reason = getattr(t, "exit_reason", "") or ""
            trade_markers.append({
                "date": d2, "side": "sell", "price": float(exitp),
                "label": f"卖出({reason})" if reason else "卖出",
            })
    for a in actions or []:
        parts = []
        if getattr(a, "dividend_per_share", None):
            parts.append(f"派{getattr(a, 'dividend_per_share')}")
        if getattr(a, "bonus_ratio", None):
            parts.append(f"送{getattr(a, 'bonus_ratio')}")
        if getattr(a, "transfer_ratio", None):
            parts.append(f"转{getattr(a, 'transfer_ratio')}")
        exd = str(getattr(a, "ex_date", "") or "")[:10]
        if exd:
            ex_marks.append({"date": exd, "label": ("10" + "".join(parts)) if parts else "除权除息"})
    return {
        "symbol": symbol, "market": market,
        "cost_lines": cost_lines, "trade_markers": trade_markers, "ex_marks": ex_marks,
    }


@router.get("/{symbol}/overlays")
def get_kline_overlays(symbol: str, market: str = "CN", db: Session = Depends(get_db)):
    """K线叠加层(M5): 持仓成本/止损/目标线 + 买卖点 + 除权除息标记。单例影子账户, 无数据给空数组不抛。"""
    from src.web.models import CorporateAction, PaperTradingPosition, PaperTradingTrade

    market_code = _parse_market(market)
    mkt = market_code.value
    try:
        positions = (
            db.query(PaperTradingPosition)
            .filter(PaperTradingPosition.stock_symbol == symbol, PaperTradingPosition.stock_market == mkt)
            .all()
        )
    except Exception:
        positions = []
    try:
        trades = (
            db.query(PaperTradingTrade)
            .filter(PaperTradingTrade.stock_symbol == symbol, PaperTradingTrade.stock_market == mkt)
            .order_by(PaperTradingTrade.closed_at.asc())
            .all()
        )
    except Exception:
        trades = []
    try:
        actions = (
            db.query(CorporateAction)
            .filter(CorporateAction.symbol == symbol, CorporateAction.market == mkt)
            .order_by(CorporateAction.ex_date.asc())
            .all()
        )
    except Exception:  # 旧库未跑 _m127, 表不存在
        actions = []
    return _build_overlays(symbol, mkt, positions, trades, actions)


# 公司简介内存缓存(避免每次详情页都调 zhitu 耗时 1.8s)
_COMPANY_CACHE: dict = {}  # {(symbol, market): (ts, payload)}
_COMPANY_CACHE_TTL = 3600  # 1 小时


@router.get("/{symbol}/company")
async def get_company_info(symbol: str, market: str = "CN"):
    """获取公司基本信息(主营/简介/上市日期/行业等)。

    数据源: zhitu /gs/gsjj/{code}(公司简介,含 bscope主营/desc简介/ldate上市日期/idea概念)。
    缓存: 模块级内存缓存 1 小时,避免每次点详情页都调 zhitu(单次 1.8s)。
    注意: 必须定义在 /{symbol} 之后(FastAPI 按定义顺序匹配,先定义会被通配吞掉)。
    """
    market_code = _parse_market(market)
    if market_code != MarketCode.CN:
        return {"symbol": symbol, "market": market, "name": None, "industry": None,
                "area": None, "market_board": None, "list_status": None, "note": "仅A股支持公司简介"}

    # 命中缓存直接返回
    import time as _time
    cache_key = (symbol, market_code.value)
    cached = _COMPANY_CACHE.get(cache_key)
    if cached and (_time.time() - cached[0]) < _COMPANY_CACHE_TTL:
        return cached[1]

    try:
        import urllib.parse
        import urllib.request

        # token 优先级: 多 key 池化(pick_zhitu_token 轮换) > 单值 DB > env > 默认
        token = ""
        try:
            from marketdata.vendors.zhitu import pick_zhitu_token
            token = pick_zhitu_token()
        except Exception:
            pass
        if not token:
            try:
                from src.web.database import SessionLocal
                from src.web.models import AppSettings
                db = SessionLocal()
                row = db.query(AppSettings).filter(AppSettings.key == "zhitu_token").first()
                token = (row.value if row and row.value and row.value != "********" else "") or ""
                db.close()
            except Exception:
                pass
        if not token:
            # P0-2 (2026-08-23 审计): 删除硬编码 UUID fallback, 必须显式配置 ZHITU_TOKEN
            # (池化/DB 都没拿到时)。startup_check 启动期会告警;此处不再 fallback 任何值。
            import os
            token = os.environ.get("ZHITU_TOKEN", "")
            if not token:
                logger.warning(
                    "ZHITU_TOKEN 未配置, 公司简介接口返回空数据。"
                    "请设置环境变量 ZHITU_TOKEN 或在设置页/池化中配置 zhitu_token。"
                )
                return {
                    "symbol": symbol, "market": market, "name": None, "industry": None,
                    "area": None, "market_board": None, "list_status": None,
                    "note": "ZHITU_TOKEN 未配置, 无法获取公司信息",
                }

        url = f"https://api.zhituapi.com/hs/gs/gsjj/{symbol}?token={token}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        # 热修 2026-08-14: 同步 urlopen 包 to_thread, 防阻塞 asyncio 事件循环(登录超时根因)
        raw = json.loads(
            (await asyncio.to_thread(lambda: urllib.request.urlopen(req, timeout=15).read())).decode("utf-8", "ignore")
        )
        if not isinstance(raw, dict) or not raw.get("name"):
            payload = {"symbol": symbol, "market": market, "name": None, "note": "未查到公司信息"}
        else:
            payload = {
                "symbol": symbol,
                "market": market,
                "name": raw.get("name"),
                "ename": raw.get("ename"),
                "industry": raw.get("instype"),
                "area": raw.get("addr", "").split(" ")[0][:30] if raw.get("addr") else None,
                "market_board": raw.get("market"),
                "list_status": raw.get("organ"),
                "list_date": raw.get("ldate"),
                "reg_capital": raw.get("rprice"),
                "issuer": raw.get("principal"),
                "secretary": raw.get("secre"),
                "phone": raw.get("phone"),
                "website": raw.get("site"),
                "address": raw.get("addr"),
                "bscope": raw.get("bscope"),
                "desc": raw.get("desc"),
                "concepts": raw.get("idea"),
                "note": None,
            }
        # 写缓存
        _COMPANY_CACHE[cache_key] = (_time.time(), payload)
        return payload
    except Exception as e:
        logger.error(f"公司信息获取失败 {symbol}: {e}")


# ── 分时走势(腾讯实时, 盘中) ─────────────────────────────────────────────
_MINUTE_CACHE: dict = {}  # {symbol_market: (ts, points)}
# 2026-08-20 修复: 前端 30s 轮询 + 分钟接口冷启动 ~15s(swings 计算) → 必撞前端 20s 超时。
# 改为 60s TTL 让轮询始终命中缓存。盘中分钟变化微, 60s 仍实时够用。
_MINUTE_TTL = 60.0


def _tencent_minute(symbol: str, market: str) -> tuple[list[dict] | None, float | None]:
    """腾讯分时接口。返回 (points, prev_close) — prev_close 为昨收(±分界线用)。"""
    # 指数代码识别(2026-08-10 修复): 000001=上证指数(非平安银行!), 399001=深证成指
    # 指数约定: 沪指以 000 开头(sh), 深指以 399 开头(sz); 个股 000 开头是深市(sz)
    prefix = {"CN": "sh" if symbol.startswith(("6", "9")) else "sz",
              "HK": "hk", "US": ""}.get(market, "sh")
    if market == "CN":
        if symbol in ("000001", "000300", "000016", "000905", "000852"):
            prefix = "sh"   # 上证指数/沪深300/上证50/中证500/中证1000
        elif symbol.startswith("399"):
            prefix = "sz"   # 深证成指/创业板指(399 开头已是 sz, 保持)
    code = f"{prefix}{symbol}" if market != "US" else symbol
    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}"
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            import json as _json
            d = _json.load(resp)
        data = (d.get("data") or {}).get(code, {}).get("data", {}).get("data")
        if not data:
            return None, None
        # 昨收: qt list[4](指数与个股通用: [3]=现价 [4]=昨收 [5]=今开)
        prev_close = None
        try:
            qt = (d.get("data") or {}).get(code, {}).get("qt", {})
            qt_list = qt.get(code) if isinstance(qt, dict) else None
            if isinstance(qt_list, list) and len(qt_list) > 4 and qt_list[4]:
                prev_close = float(qt_list[4])
            elif isinstance(qt_list, dict):
                prev_close = float(qt_list.get("preclose") or qt_list.get("pre_close") or 0) or None
        except (TypeError, ValueError):
            pass
        # 字段: "0930 1308.66 173 22639818.00" = 时间 价格 累计量(手) 累计额(元)
        # ⚠️ 第3/4列本身已经是"当日累计"值, 不可再累加(旧版 cum_vol += vol 是 bug,
        #    会把累计量当增量二次累加, 导致均价分母膨胀、均价线整体失真)
        # 指数识别(avg 无意义: 腾讯对指数成交额字段语义不同, 不计算均价)
        is_index = market == "CN" and (
            symbol in ("000001", "000300", "000016", "000905", "000852")
            or symbol.startswith("399")
        )
        points = []
        prev_cum_vol = 0.0
        for row in data:
            parts = row.split()
            if len(parts) < 4:
                continue
            t, price = parts[0], float(parts[1])
            cum_vol, cum_amt = float(parts[2]), float(parts[3])  # 累计量(手) / 累计额(元)
            if is_index:
                # 指数: 均价=昨收(用昨收当基准线, 前端不再画均价线)
                avg = prev_close if prev_close else price
            else:
                # 均价 = 累计成交额 / 累计成交股数 (1手=100股)
                avg = (cum_amt / (cum_vol * 100.0)) if cum_vol > 0 else price
            bar_vol = max(cum_vol - prev_cum_vol, 0.0)  # 本分钟增量成交量(手)
            prev_cum_vol = cum_vol
            points.append({"t": t, "price": price, "avg": round(avg, 2), "volume": int(bar_vol)})
        return points, prev_close
    except Exception as e:
        logger.debug(f"腾讯分时失败 {symbol}: {e}")
        return None, None


@router.get("/minute/{symbol}")
async def get_minute(symbol: str, market: str = "CN"):
    """分时走势(盘中实时)。腾讯优先, 失败返回空。含昨收(±分界线)。

    2026-08-12: 附加 swings 字段(顺势拉升段/瞬时下探段标记, 逐单明细判别),
    供前端分时K线区间着色。仅 A 股计算(复用逐笔 30s 缓存, 开销 ~0.1s)。
    """
    is_index = market == "CN" and (
        symbol in ("000001", "000300", "000016", "000905", "000852")
        or symbol.startswith("399")
    )
    cache_key = f"{market}:{symbol}"
    cached = _MINUTE_CACHE.get(cache_key)
    if cached and (_time.time() - cached[0]) < _MINUTE_TTL:
        # 兼容旧3元组缓存(2026-08-12 加 swings 前): 缺第4元素则 swings=None
        swings_old = cached[3] if len(cached) > 3 else None
        return {"symbol": symbol, "market": market, "points": cached[1],
                "prev_close": cached[2], "is_index": is_index, "swings": swings_old or None}
    points, prev_close = _tencent_minute(symbol, market)
    if points is None:
        points = []
    # 拉升/下探段(仅A股个股, 逐单明细判别; 失败静默 None 不阻塞分时)
    swings = None
    if market == "CN" and not is_index:
        try:
            from src.core.rally_analysis import analyze_swings
            swings = analyze_swings(symbol)
        except Exception as e:
            logger.warning(f"minute swings 计算失败 {symbol}: {e}", exc_info=True)
            swings = None
    _MINUTE_CACHE[cache_key] = (_time.time(), points, prev_close, swings)
    return {"symbol": symbol, "market": market, "points": points,
            "prev_close": prev_close, "is_index": is_index, "swings": swings}


# 2026-08-18: 根路径 GET (前端默认请求, 返回自选股票列表)
@router.get("")
async def get_quotes_root(db: Session = Depends(get_db)):
    """首页 Dashboard 用的简化股票列表"""
    try:
        stocks = db.query(Stock).filter(Stock.is_watchlist == True).order_by(Stock.sort_order).limit(10).all()
        return {
            "code": 0,
            "success": True,
            "data": [
                {"symbol": s.symbol, "name": s.name, "market": s.market}
                for s in stocks
            ],
            "message": "",
        }
    except Exception as e:
        return {"code": 0, "success": True, "data": [], "message": str(e)[:100]}

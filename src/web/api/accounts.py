"""账户和持仓管理 API"""
import logging
import threading
import time
import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict

from datetime import datetime, timedelta, timezone

from src.web.database import get_db
from src.web.api.auth import get_current_user
from src.web.models import Account, PriceAlertRule, Position, Stock, User
from src.core.marketdata_client import md_quote_rows
from src.core.quote_period import classify_quote_period, summarize_daily_pnl_period
from src.models.market import MarketCode

logger = logging.getLogger(__name__)
router = APIRouter()

# 汇率缓存
_hkd_rate_cache: dict = {"rate": 0.92, "ts": 0}  # 港币默认汇率 0.92
_usd_rate_cache: dict = {"rate": 7.25, "ts": 0}  # 美元默认汇率 7.25
EXCHANGE_RATE_TTL = 3600  # 成功缓存 1 小时
# 2026-08-12: 海外节点新浪超时(每次冷启动白等 3s×2)。失败缓存短 TTL(5分钟)防反复撞墙;
# 成功后回到 1 小时长缓存。另用线程锁保证 HKD/USD 并发拉取不重复等待。
EXCHANGE_RATE_FAIL_TTL = 300
_fx_lock = threading.Lock()


def _fetch_fx_rate(
    url: str,
    cache: dict,
    name: str,
    redis_key: str,
    fail_ttl: int = EXCHANGE_RATE_FAIL_TTL,
) -> float:
    """拉取单个汇率(带失败短缓存 + Redis 跨进程缓存)。返回当前生效汇率。"""
    if time.time() - cache["ts"] < EXCHANGE_RATE_TTL:
        return cache["rate"]
    # 2026-08-22: 内存 miss 时查 Redis(跨进程/重启兜底), 命中则回填内存。
    from src.web.cache.biz_cache import biz_cache
    cached = biz_cache.get_json(redis_key)
    if cached is not None:
        try:
            cache["rate"], cache["ts"] = float(cached), time.time()
            return cache["rate"]
        except (TypeError, ValueError):
            pass
    try:
        # 2026-08-12: 数据源从新浪(hq.sinajs.cn)换成腾讯 qt.gtimg.cn——
        # 海外节点新浪超时(冷启动白等 3s×2), 腾讯 0.1s 秒回且支持外汇。
        # 腾讯格式: v_whUSDCNY="310~美元人民币~USDCNY~6.7461~0~时间~..."; GBK 编码
        resp = httpx.get(url, timeout=3, headers={
            "User-Agent": "Mozilla/5.0",
        })
        text = resp.content.decode("gbk", "replace")
        if "=" in text and "~" in text:
            val = text.split("=", 1)[1].strip().strip(";").strip('"')
            parts = val.split("~")
            if len(parts) > 3:
                rate = float(parts[3])  # 第4字段 = 现价
                cache["rate"], cache["ts"] = rate, time.time()  # 成功: 长 TTL
                biz_cache.set_json(redis_key, rate, ttl=EXCHANGE_RATE_TTL)
                logger.info(f"更新{name}汇率: {rate}")
                return rate
    except Exception as e:
        # 海外节点超时(2026-08-12): 失败也刷新 ts(短TTL), 防每次请求都撞超时
        logger.warning(f"获取{name}汇率失败，使用缓存: {e}")
    if time.time() - cache["ts"] >= fail_ttl:
        cache["ts"] = time.time()  # 失败: 短 TTL 后重试
    return cache["rate"]


def get_hkd_cny_rate() -> float:
    """获取港币兑人民币汇率(腾讯 qt.gtimg.cn, 海外可达)"""
    global _hkd_rate_cache
    with _fx_lock:
        return _fetch_fx_rate(
            "https://qt.gtimg.cn/q=whHKDCNY", _hkd_rate_cache, "港币", "fx:rate:hkd"
        )


def get_usd_cny_rate() -> float:
    """获取美元兑人民币汇率(腾讯 qt.gtimg.cn, 海外可达)"""
    global _usd_rate_cache
    with _fx_lock:
        return _fetch_fx_rate(
            "https://qt.gtimg.cn/q=whUSDCNY", _usd_rate_cache, "美元", "fx:rate:usd"
        )


# ========== Pydantic Models ==========

class AccountCreate(BaseModel):
    name: str
    available_funds: float = 0


class AccountUpdate(BaseModel):
    name: str | None = None
    available_funds: float | None = None
    enabled: bool | None = None


class AccountResponse(BaseModel):
    id: int
    name: str
    available_funds: float
    enabled: bool

    model_config = ConfigDict(from_attributes=True)


class PositionCreate(BaseModel):
    account_id: int
    stock_id: int
    cost_price: float
    quantity: int
    invested_amount: float | None = None
    trading_style: str | None = None  # short: 短线, swing: 波段, long: 长线


class PositionUpdate(BaseModel):
    cost_price: float | None = None
    quantity: int | None = None
    invested_amount: float | None = None
    trading_style: str | None = None


class PositionResponse(BaseModel):
    id: int
    account_id: int
    stock_id: int
    cost_price: float
    quantity: int
    invested_amount: float | None
    sort_order: int
    trading_style: str | None
    # 关联信息
    account_name: str | None = None
    stock_symbol: str | None = None
    stock_name: str | None = None

    model_config = ConfigDict(from_attributes=True)


class PositionReorderItem(BaseModel):
    id: int
    sort_order: int


class PositionReorderRequest(BaseModel):
    items: list[PositionReorderItem]


# ========== Account Endpoints ==========

@router.get("/accounts", response_model=list[AccountResponse])
def list_accounts(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """获取当前用户的账户(含全局共享)"""
    return db.query(Account).filter(
        or_(Account.user_id == user.id, Account.user_id.is_(None))
    ).order_by(Account.id).all()


@router.get("/accounts/{account_id}", response_model=AccountResponse)
def get_account(account_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """获取单个账户"""
    account = db.query(Account).filter(
        Account.id == account_id,
        or_(Account.user_id == user.id, Account.user_id.is_(None)),
    ).first()
    if not account:
        raise HTTPException(404, "账户不存在")
    return account


@router.post("/accounts", response_model=AccountResponse)
def create_account(data: AccountCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """创建账户"""
    account = Account(name=data.name, available_funds=data.available_funds, user_id=user.id)
    db.add(account)
    db.commit()
    db.refresh(account)
    logger.info(f"创建账户: {account.name}")
    return account


@router.put("/accounts/{account_id}", response_model=AccountResponse)
def update_account(account_id: int, data: AccountUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """更新账户"""
    account = db.query(Account).filter(
        Account.id == account_id,
        or_(Account.user_id == user.id, Account.user_id.is_(None)),
    ).first()
    if not account:
        raise HTTPException(404, "账户不存在")

    if data.name is not None:
        account.name = data.name
    if data.available_funds is not None:
        account.available_funds = data.available_funds
    if data.enabled is not None:
        account.enabled = data.enabled

    db.commit()
    db.refresh(account)
    logger.info(f"更新账户: {account.name}")
    return account


@router.delete("/accounts/{account_id}")
def delete_account(
    account_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """删除账户（会同时删除该账户的所有持仓）

    S5(2026-08-26): 归属校验 — 与 list/get/update 同款 or_ 过滤,
    只能删除自己的(或 NULL 全局)账户; 他人账户返回 404 防账号探测。
    """
    account = db.query(Account).filter(
        Account.id == account_id,
        or_(Account.user_id == user.id, Account.user_id.is_(None)),
    ).first()
    if not account:
        raise HTTPException(404, "账户不存在")

    db.delete(account)
    db.commit()
    logger.info(f"删除账户: {account.name}")
    return {"success": True}


# ========== Position Endpoints ==========

@router.get("/positions", response_model=list[PositionResponse])
def list_positions(
    account_id: int | None = None,
    stock_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """获取持仓列表，可按账户或股票筛选(仅当前用户的)"""
    query = db.query(Position).filter(
        or_(Position.user_id == user.id, Position.user_id.is_(None))
    )
    if account_id:
        query = query.filter(Position.account_id == account_id)
    if stock_id:
        query = query.filter(Position.stock_id == stock_id)

    positions = query.order_by(Position.account_id.asc(), Position.sort_order.asc(), Position.id.asc()).all()
    result = []
    for pos in positions:
        result.append({
            "id": pos.id,
            "account_id": pos.account_id,
            "stock_id": pos.stock_id,
            "cost_price": pos.cost_price,
            "quantity": pos.quantity,
            "invested_amount": pos.invested_amount,
            "sort_order": pos.sort_order or 0,
            "trading_style": pos.trading_style,
            "account_name": pos.account.name if pos.account else None,
            "stock_symbol": pos.stock.symbol if pos.stock else None,
            "stock_name": pos.stock.name if pos.stock else None,
        })
    return result


@router.post("/positions", response_model=PositionResponse)
def create_position(data: PositionCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """创建持仓"""
    # 检查账户和股票是否存在(仅自己的)
    account = db.query(Account).filter(
        Account.id == data.account_id,
        or_(Account.user_id == user.id, Account.user_id.is_(None)),
    ).first()
    if not account:
        raise HTTPException(400, "账户不存在")

    stock = db.query(Stock).filter(Stock.id == data.stock_id).first()
    if not stock:
        raise HTTPException(400, "股票不存在")

    # 检查是否已存在该账户的该股票持仓
    existing = db.query(Position).filter(
        Position.account_id == data.account_id,
        Position.stock_id == data.stock_id,
    ).first()
    if existing:
        raise HTTPException(400, f"账户 {account.name} 已有 {stock.name} 的持仓，请编辑现有持仓")

    max_order = db.query(func.max(Position.sort_order)).filter(
        Position.account_id == data.account_id
    ).scalar() or 0

    position = Position(
        account_id=data.account_id,
        stock_id=data.stock_id,
        cost_price=data.cost_price,
        quantity=data.quantity,
        invested_amount=data.invested_amount,
        sort_order=int(max_order) + 1,
        trading_style=data.trading_style,
        user_id=user.id,
    )
    db.add(position)
    db.commit()
    db.refresh(position)

    logger.info(f"创建持仓: {account.name} - {stock.name}")
    return {
        "id": position.id,
        "account_id": position.account_id,
        "stock_id": position.stock_id,
        "cost_price": position.cost_price,
        "quantity": position.quantity,
        "invested_amount": position.invested_amount,
        "sort_order": position.sort_order or 0,
        "trading_style": position.trading_style,
        "account_name": account.name,
        "stock_symbol": stock.symbol,
        "stock_name": stock.name,
    }


@router.put("/positions/{position_id}", response_model=PositionResponse)
def update_position(position_id: int, data: PositionUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """更新持仓"""
    position = db.query(Position).filter(
        Position.id == position_id,
        or_(Position.user_id == user.id, Position.user_id.is_(None)),
    ).first()
    if not position:
        raise HTTPException(404, "持仓不存在")

    if data.cost_price is not None:
        position.cost_price = data.cost_price
    if data.quantity is not None:
        position.quantity = data.quantity
    if data.invested_amount is not None:
        position.invested_amount = data.invested_amount
    if data.trading_style is not None:
        # 空字符串表示清空，设为 None
        position.trading_style = data.trading_style if data.trading_style else None

    db.commit()
    db.refresh(position)

    logger.info(f"更新持仓: {position.account.name} - {position.stock.name}")
    return {
        "id": position.id,
        "account_id": position.account_id,
        "stock_id": position.stock_id,
        "cost_price": position.cost_price,
        "quantity": position.quantity,
        "invested_amount": position.invested_amount,
        "sort_order": position.sort_order or 0,
        "trading_style": position.trading_style,
        "account_name": position.account.name,
        "stock_symbol": position.stock.symbol,
        "stock_name": position.stock.name,
    }


@router.delete("/positions/{position_id}")
def delete_position(position_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """删除持仓"""
    position = db.query(Position).filter(
        Position.id == position_id,
        or_(Position.user_id == user.id, Position.user_id.is_(None)),
    ).first()
    if not position:
        raise HTTPException(404, "持仓不存在")

    db.delete(position)
    db.commit()
    logger.info(f"删除持仓: {position.account.name} - {position.stock.name}")
    return {"success": True}


@router.put("/positions/reorder/batch")
def reorder_positions(data: PositionReorderRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """批量更新持仓排序"""
    if not data.items:
        return {"updated": 0}
    ids = [int(x.id) for x in data.items]
    rows = db.query(Position).filter(
        Position.id.in_(ids),
        or_(Position.user_id == user.id, Position.user_id.is_(None)),
    ).all()
    row_map = {r.id: r for r in rows}
    updated = 0
    for item in data.items:
        row = row_map.get(int(item.id))
        if not row:
            continue
        row.sort_order = int(item.sort_order)
        updated += 1
    db.commit()
    return {"updated": updated}


# ========== Portfolio Summary ==========

@router.get("/portfolio/summary")
def get_portfolio_summary(
    account_id: int | None = None,
    include_quotes: bool = True,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    获取持仓汇总信息

    Args:
        account_id: 可选，指定账户ID。不指定则汇总所有账户

    Returns:
        accounts: 账户列表及各账户持仓明细
        total: 所有账户汇总
    """
    # 获取账户(仅当前用户的)
    if account_id:
        accounts = db.query(Account).filter(
            Account.id == account_id, Account.enabled == True,
            or_(Account.user_id == user.id, Account.user_id.is_(None)),
        ).all()
    else:
        accounts = db.query(Account).filter(
            Account.enabled == True,
            or_(Account.user_id == user.id, Account.user_id.is_(None)),
        ).all()

    if not accounts:
        return {
            "accounts": [],
            "total": {
                "total_market_value": 0,
                "total_cost": 0,
                "total_pnl": 0,
                "total_pnl_pct": 0,
                "total_daily_pnl": 0,
                "daily_pnl_period": "unknown",
                "daily_pnl_label": "当日盈亏",
                "daily_pnl_date": None,
                "available_funds": 0,
                "total_assets": 0,
            }
        }

    # 获取所有相关股票
    all_stock_ids = set()
    for acc in accounts:
        for pos in acc.positions:
            all_stock_ids.add(pos.stock_id)

    stocks = db.query(Stock).filter(Stock.id.in_(all_stock_ids)).all() if all_stock_ids else []
    stock_map = {s.id: s for s in stocks}

    # 获取实时行情（可选）
    quotes = _fetch_quotes_for_stocks(stocks) if include_quotes else {}

    # 获取汇率
    hkd_rate = get_hkd_cny_rate()
    usd_rate = get_usd_cny_rate()

    # 计算各账户持仓
    account_summaries = []
    grand_total_market_value = 0
    grand_total_cost = 0
    grand_available_funds = 0
    grand_daily_pnl = 0
    grand_daily_pnl_periods: list[tuple[str, str | None]] = []

    for acc in accounts:
        positions_data = []
        acc_market_value = 0
        acc_cost = 0
        acc_daily_pnl = 0
        acc_daily_pnl_periods: list[tuple[str, str | None]] = []

        positions_sorted = sorted(
            list(acc.positions or []),
            key=lambda p: (int(getattr(p, "sort_order", 0) or 0), int(p.id)),
        )
        for pos in positions_sorted:
            stock = stock_map.get(pos.stock_id)
            if not stock:
                continue

            quote = quotes.get(stock.symbol)
            current_price = quote["current_price"] if quote else None
            change_pct = quote["change_pct"] if quote else None
            prev_close = quote.get("prev_close") if quote else None
            quote_time = quote.get("quote_time") if quote else None
            quote_date = quote.get("quote_date") if quote else None
            daily_pnl_period = classify_quote_period(quote_date, stock.market)

            # 根据市场确定汇率
            is_foreign = stock.market in ("HK", "US")
            if stock.market == "HK":
                rate = hkd_rate
            elif stock.market == "US":
                rate = usd_rate
            else:
                rate = 1.0

            market_value = None
            market_value_cny = None
            pnl = None
            pnl_pct = None
            daily_pnl = None
            daily_pnl_pct = None

            if current_price is not None and prev_close and prev_close > 0:
                daily_pnl = (current_price - prev_close) * pos.quantity * rate
                daily_pnl_pct = (current_price - prev_close) / prev_close * 100
                acc_daily_pnl += daily_pnl
                observation = (daily_pnl_period, quote_date)
                acc_daily_pnl_periods.append(observation)
                grand_daily_pnl_periods.append(observation)

            cost = pos.cost_price * pos.quantity
            cost_cny = cost * rate  # 假设成本价也是原币种
            acc_cost += cost_cny

            if current_price is not None:
                market_value = current_price * pos.quantity  # 原币种市值
                market_value_cny = market_value * rate  # 人民币市值
                pnl = market_value_cny - cost_cny
                pnl_pct = (pnl / cost_cny * 100) if cost_cny > 0 else 0

                acc_market_value += market_value_cny

            positions_data.append({
                "id": pos.id,
                "stock_id": pos.stock_id,
                "symbol": stock.symbol,
                "name": stock.name,
                "market": stock.market,
                "cost_price": pos.cost_price,
                "quantity": pos.quantity,
                "invested_amount": pos.invested_amount,
                "sort_order": pos.sort_order or 0,
                "trading_style": pos.trading_style,
                "current_price": current_price,
                "current_price_cny": round(current_price * rate, 2) if current_price else None,
                "change_pct": change_pct,
                "market_value": round(market_value, 2) if market_value else None,
                "market_value_cny": round(market_value_cny, 2) if market_value_cny else None,
                "pnl": round(pnl, 2) if pnl else None,
                "pnl_pct": round(pnl_pct, 2) if pnl_pct else None,
                "daily_pnl": round(daily_pnl, 2) if daily_pnl is not None else None,
                "daily_pnl_pct": round(daily_pnl_pct, 2) if daily_pnl_pct is not None else None,
                "daily_pnl_period": daily_pnl_period,
                "quote_time": quote_time,
                "quote_date": quote_date,
                "exchange_rate": rate if is_foreign else None,
            })

        if include_quotes:
            acc_pnl = acc_market_value - acc_cost
            acc_pnl_pct = (acc_pnl / acc_cost * 100) if acc_cost > 0 else 0
            acc_total_assets = acc_market_value + acc.available_funds
        else:
            acc_pnl = 0
            acc_pnl_pct = 0
            acc_total_assets = acc.available_funds

        account_summaries.append({
            "id": acc.id,
            "name": acc.name,
            "available_funds": acc.available_funds,
            "total_market_value": round(acc_market_value, 2),
            "total_cost": round(acc_cost, 2),
            "total_pnl": round(acc_pnl, 2),
            "total_pnl_pct": round(acc_pnl_pct, 2),
            "total_daily_pnl": round(acc_daily_pnl, 2),
            "total_assets": round(acc_total_assets, 2),
            "positions": positions_data,
            **summarize_daily_pnl_period(acc_daily_pnl_periods),
        })

        grand_total_market_value += acc_market_value
        grand_total_cost += acc_cost
        grand_available_funds += acc.available_funds
        grand_daily_pnl += acc_daily_pnl

    if include_quotes:
        grand_pnl = grand_total_market_value - grand_total_cost
        grand_pnl_pct = (grand_pnl / grand_total_cost * 100) if grand_total_cost > 0 else 0
        grand_total_assets = grand_total_market_value + grand_available_funds
    else:
        grand_pnl = 0
        grand_pnl_pct = 0
        grand_total_assets = grand_available_funds

    # 构建 quotes 字典（用于前端股票列表显示）
    quotes_dict = {}
    if include_quotes:
        for symbol, quote in quotes.items():
            quotes_dict[symbol] = {
                "current_price": quote.get("current_price"),
                "change_pct": quote.get("change_pct"),
                "quote_time": quote.get("quote_time"),
                "quote_date": quote.get("quote_date"),
            }

    total_daily_pnl_meta = summarize_daily_pnl_period(grand_daily_pnl_periods)
    return {
        "accounts": account_summaries,
        "total": {
            "total_market_value": round(grand_total_market_value, 2),
            "total_cost": round(grand_total_cost, 2),
            "total_pnl": round(grand_pnl, 2),
            "total_pnl_pct": round(grand_pnl_pct, 2),
            "total_daily_pnl": round(grand_daily_pnl, 2),
            "available_funds": round(grand_available_funds, 2),
            "total_assets": round(grand_total_assets, 2),
            **total_daily_pnl_meta,
        },
        "exchange_rates": {
            "HKD_CNY": hkd_rate,
            "USD_CNY": usd_rate,
        },
        "quotes": quotes_dict,  # 可选：返回行情数据
    }


def _fetch_quotes_for_stocks(stocks: list[Stock]) -> dict:
    """获取股票列表的实时行情"""
    if not stocks:
        return {}

    # 按市场分组
    market_stocks: dict[str, list[Stock]] = {}
    for s in stocks:
        market_stocks.setdefault(s.market, []).append(s)

    quotes = {}
    for market, stock_list in market_stocks.items():
        try:
            market_code = MarketCode(market)
        except ValueError:
            continue

        symbols = [s.symbol for s in stock_list]
        try:
            items = md_quote_rows(symbols, market_code.value)
            for item in items:
                quotes[item["symbol"]] = item
        except Exception as e:
            logger.error(f"获取 {market} 行情失败: {e}")

    return quotes


# 组合基准/归因结果缓存:重建全持仓 NAV 很贵(逐只拉 K 线),按持仓指纹缓存结果。
# 2026-08-22: 已迁到 biz_cache(L1 内存 + L2 Redis), 跨进程共享 + 重启不丢。
# 持仓变动即失效(指纹变);失败/空结果不缓存,避免把瞬时故障冻住 10 分钟。


def _holdings_signature(db: Session, user: User | None = None) -> str:
    """启用账户持仓的稳定指纹(stock_id + 合并后数量);仅查 DB,不拉行情/K 线。"""
    q = db.query(Position.stock_id, Position.quantity).join(
        Account, Account.id == Position.account_id
    ).filter(Account.enabled == True)  # noqa: E712
    if user is not None:
        q = q.filter(or_(Account.user_id == user.id, Account.user_id.is_(None)))
    rows = q.all()
    agg: dict[int, float] = {}
    for sid, qty in rows:
        agg[sid] = agg.get(sid, 0.0) + (qty or 0)
    return ";".join(f"{sid}:{agg[sid]:g}" for sid in sorted(agg))


def _gather_holdings(db: Session, user: User | None = None) -> list[dict]:
    """汇总所有启用账户的真实持仓为统一列表(CNY 市值/浮盈 + fx),多账户同股合并。"""
    q = db.query(Account).filter(Account.enabled == True)  # noqa: E712
    if user is not None:
        q = q.filter(or_(Account.user_id == user.id, Account.user_id.is_(None)))
    accounts = q.all()
    stock_ids = {p.stock_id for acc in accounts for p in acc.positions}
    stocks = db.query(Stock).filter(Stock.id.in_(stock_ids)).all() if stock_ids else []
    stock_map = {s.id: s for s in stocks}
    quotes = _fetch_quotes_for_stocks(stocks) if stocks else {}
    hkd, usd = get_hkd_cny_rate(), get_usd_cny_rate()

    out: list[dict] = []
    seen: dict[tuple[str, str], dict] = {}
    for acc in accounts:
        for pos in acc.positions:
            stock = stock_map.get(pos.stock_id)
            if not stock:
                continue
            rate = hkd if stock.market == "HK" else usd if stock.market == "US" else 1.0
            quote = quotes.get(stock.symbol)
            price = quote.get("current_price") if quote else None
            cost_cny = pos.cost_price * pos.quantity * rate
            mv_cny = (price * pos.quantity * rate) if price else cost_cny
            pnl_cny = (mv_cny - cost_cny) if price else 0.0
            key = (stock.market, stock.symbol)
            if key in seen:  # 多账户同一标的合并
                h = seen[key]
                h["quantity"] += pos.quantity
                h["market_value"] += mv_cny
                h["unrealized_pnl"] += pnl_cny
            else:
                h = {
                    "symbol": stock.symbol,
                    "market": stock.market,
                    "name": stock.name,
                    "quantity": pos.quantity,
                    "fx": rate,
                    "market_value": mv_cny,
                    "unrealized_pnl": pnl_cny,
                    "strategy_code": pos.trading_style or "",
                }
                seen[key] = h
                out.append(h)
    return out


@router.get("/portfolio/diagnostics")
def portfolio_diagnostics(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """真实持仓组合诊断:集中度(HHI)/最大单仓/市场分布/风险提示(只读)。"""
    from src.core.portfolio_diagnostics import diagnose_positions

    return diagnose_positions(_gather_holdings(db, user))


@router.get("/portfolio/benchmark")
def portfolio_benchmark(
    days: int = 60, benchmark: str = "000300", db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """真实持仓组合 vs 基准:超额收益/信息比率/相对回撤 + 归一化净值曲线。"""
    from src.core.portfolio_benchmark import (
        DEFAULT_BENCHMARK,
        build_portfolio_benchmark,
    )

    days = max(20, min(int(days), 250))
    bcode = benchmark or DEFAULT_BENCHMARK
    sig = _holdings_signature(db, user)
    if not sig:
        return {"empty": True, "reason": "no_holdings"}
    ckey = f"portfolio:bench:{days}:{bcode}:{sig}"
    from src.web.cache.biz_cache import biz_cache
    cached = biz_cache.get_json(ckey)
    if cached is not None:
        return cached

    holdings = _gather_holdings(db, user)
    if not holdings:
        return {"empty": True, "reason": "no_holdings"}
    res = build_portfolio_benchmark(holdings, days=days, benchmark_code=bcode)
    if not res:
        # 失败/数据不足不缓存,下轮可重试(由 K 线负缓存兜住打爆)
        return {"empty": True, "reason": "insufficient_data"}
    biz_cache.set_json(ckey, res, ttl=600)
    return res


@router.get("/portfolio/todos")
def portfolio_todos(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """首页空态待办:持仓但未设提醒 / 提醒即将到期(可行动,盘后也不空)。"""
    todos: list[dict] = []
    accounts = db.query(Account).filter(
        Account.enabled == True,  # noqa: E712
        or_(Account.user_id == user.id, Account.user_id.is_(None)),
    ).all()
    held_ids = {p.stock_id for acc in accounts for p in acc.positions}
    if held_ids:
        ruled = {
            r.stock_id
            for r in db.query(PriceAlertRule)
            .filter(PriceAlertRule.enabled == True, PriceAlertRule.stock_id.in_(held_ids))  # noqa: E712
            .all()
        }
        for sid in held_ids - ruled:
            stock = db.query(Stock).filter(Stock.id == sid).first()
            if stock:
                todos.append(
                    {
                        "type": "no_alert",
                        "symbol": stock.symbol,
                        "market": stock.market,
                        "message": f"{stock.name} 持仓中,未设价格提醒",
                    }
                )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    soon = now + timedelta(days=3)
    expiring = (
        db.query(PriceAlertRule)
        .filter(
            PriceAlertRule.enabled == True,  # noqa: E712
            PriceAlertRule.expire_at.isnot(None),
            PriceAlertRule.expire_at >= now,
            PriceAlertRule.expire_at <= soon,
        )
        .all()
    )
    for r in expiring:
        stock = db.query(Stock).filter(Stock.id == r.stock_id).first()
        todos.append(
            {
                "type": "alert_expiring",
                "symbol": stock.symbol if stock else "",
                "market": stock.market if stock else "CN",
                "message": f"{(r.name or '提醒')} 即将到期",
            }
        )

    return {"todos": todos[:10], "count": len(todos)}


@router.get("/portfolio/attribution")
def portfolio_attribution(days: int = 60, benchmark: str = "000300", db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """近 days 日各持仓对组合收益的贡献(谁拖累/贡献),降序。"""
    from src.core.portfolio_benchmark import DEFAULT_BENCHMARK, build_attribution

    days = max(20, min(int(days), 250))
    bcode = benchmark or DEFAULT_BENCHMARK
    sig = _holdings_signature(db, user)
    if not sig:
        return {"items": []}
    ckey = f"portfolio:attr:{days}:{bcode}:{sig}"
    from src.web.cache.biz_cache import biz_cache
    cached = biz_cache.get_json(ckey)
    if cached is not None:
        return cached

    holdings = _gather_holdings(db, user)
    if not holdings:
        return {"items": []}
    items = build_attribution(holdings, days=days, benchmark_code=bcode)
    result = {"items": items}
    if items:  # 空结果不缓存,下轮可重试
        biz_cache.set_json(ckey, result, ttl=600)
    return result


@router.post("/portfolio/ai-review")
async def portfolio_ai_review(model_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """组合 AI 体检:诊断+基准+归因 → 叙述结论 + 调仓建议(只读,不下单)。"""
    from src.core.portfolio_benchmark import build_attribution, build_portfolio_benchmark
    from src.core.portfolio_diagnostics import diagnose_positions
    from src.web.api.chat import _get_ai_client

    holdings = _gather_holdings(db, user)
    if not holdings:
        return {"empty": True, "reason": "no_holdings"}

    diag = diagnose_positions(holdings)
    bench = build_portfolio_benchmark(holdings, days=60) or {}
    attr = build_attribution(holdings, days=60)
    top = attr[:3]
    worst = list(reversed(attr[-3:])) if len(attr) > 3 else []

    lines = [
        f"持仓 {diag['position_count']} 只,总市值 {diag['total_market_value']:.0f},浮盈 {diag['total_unrealized_pnl']:.0f}",
        f"集中度 HHI {diag['hhi']},最大单仓 {diag['max_weight'] * 100:.0f}%",
    ]
    if bench.get("excess_return") is not None:
        lines.append(
            f"近60日 vs {bench.get('benchmark_label', '基准')}:超额 {bench['excess_return']}%"
            f"(组合 {bench.get('portfolio_return')}% / 基准 {bench.get('benchmark_return')}%),"
            f"相对回撤 {bench.get('relative_drawdown')}%"
        )
    if diag.get("by_market"):
        lines.append("市场分布:" + ", ".join(f"{k} {v:.0f}" for k, v in diag["by_market"].items()))
    if diag.get("alerts"):
        lines.append("风险提示:" + "; ".join(diag["alerts"]))
    if top:
        lines.append("贡献最大:" + ", ".join(f"{r['name']}({r['contribution_pct']:+.2f}%)" for r in top))
    if worst:
        lines.append("拖累最大:" + ", ".join(f"{r['name']}({r['contribution_pct']:+.2f}%)" for r in worst))

    system_prompt = (
        "你是稳健的组合顾问。基于给定的组合诊断/基准对比/个股归因,给一段简短体检 + 可执行调仓建议,"
        "只读分析、不下单、不承诺收益。严格格式:\n体检: 一句话总评\n建议:\n- (2~3 条具体可执行)\n风险: 一句话最大风险"
    )
    user_content = "组合概况:\n" + "\n".join(lines)
    try:
        content = await _get_ai_client(db, model_id).chat(system_prompt, user_content, temperature=0.3)
    except Exception as e:
        raise HTTPException(502, f"AI 体检失败: {e}")

    return {"content": content, "top": top, "worst": worst, "diagnostics": diag, "benchmark": bench}

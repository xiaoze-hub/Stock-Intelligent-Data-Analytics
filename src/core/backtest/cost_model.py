"""A 股交易成本模型 —— 回测(Phase 0)与模拟盘(Phase 1)共用。

成本口径(2023-08-28 印花税下调后):
- 印花税:**卖出单边** 0.05%(万 5)
- 佣金:双边,默认万 2.5,单笔最低 5 元
- 过户费:双边,成交额 0.001%(沪深统一,2022-04 起)
- 滑点:可配置基点(默认 5bps),买入价上滑 / 卖出价下滑,模拟冲击成本

滑点体现在实际成交价(fill_price),不重复计入显式规费;显式规费 = 佣金+印花税+过户费。
现金变动(cash_delta)= 买入为负、卖出为正,已扣全部成本与滑点,PnL 由买卖两腿 cash_delta 相加得出。

滑点分档(S3, 2026-09-10): 固定 5bps 对小票/高波动是系统性低估。
`slippage_bps_for(市值)` 按流通市值分档(大盘3/中盘5/小盘10/微盘15bps),
未知市值回退默认 5bps。`fill()`/`round_trip_pnl()` 接受 `slippage_bps` 覆盖,
不传保持旧行为(调用方零改动)。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostConfig:
    """成本参数(可配置;默认值贴近 A 股散户实际)。"""

    commission_rate: float = 0.00025   # 佣金费率(双边)万 2.5
    min_commission: float = 5.0        # 单笔最低佣金(元)
    stamp_duty_rate: float = 0.0005    # 印花税(仅卖出)万 5
    transfer_fee_rate: float = 0.00001  # 过户费(双边)十万分之 1
    slippage_bps: float = 5.0          # 滑点(基点,双边;5bps = 0.05%)


@dataclass(frozen=True)
class Fill:
    """一次成交的净结果(含成本拆解,便于展示与审计)。"""

    side: str            # "buy" | "sell"
    price: float         # 名义价(信号/行情价,未含滑点)
    fill_price: float    # 实际成交价(含滑点)
    quantity: int
    gross: float         # 实际成交额 = fill_price * quantity
    commission: float
    stamp_duty: float
    transfer_fee: float
    slippage_cost: float  # 滑点损耗 = |fill_price - price| * quantity(仅展示)
    explicit_fees: float  # 显式规费 = commission + stamp_duty + transfer_fee
    friction: float       # 总摩擦 = explicit_fees + slippage_cost(仅展示)
    cash_delta: float     # 现金变动:buy 为负,sell 为正(已扣显式规费;滑点含在 fill_price)


class CostModel:
    """A 股交易成本计算器。线程无关,可全局复用。"""

    # 流通市值分档阈值(元)与对应滑点(基点): 大/中/小/微
    SLIPPAGE_TIERS: tuple[tuple[float, float], ...] = (
        (500e8, 3.0),   # >=500亿: 大盘 3bps
        (100e8, 5.0),   # >=100亿: 中盘 5bps(默认档)
        (20e8, 10.0),   # >=20亿: 小盘 10bps
    )
    MICRO_SLIPPAGE_BPS = 15.0  # <20亿: 微盘 15bps

    def __init__(self, config: CostConfig | None = None) -> None:
        self.cfg = config or CostConfig()

    @classmethod
    def slippage_bps_for(cls, market_cap_yuan: float | None) -> float:
        """按流通市值取滑点档; 未知/非法回退默认档(5bps)。"""
        try:
            cap = float(market_cap_yuan) if market_cap_yuan is not None else float("nan")
        except Exception:
            return CostConfig().slippage_bps
        if cap != cap or cap <= 0:  # NaN/非正
            return CostConfig().slippage_bps
        for threshold, bps in cls.SLIPPAGE_TIERS:
            if cap >= threshold:
                return bps
        return cls.MICRO_SLIPPAGE_BPS

    def _apply_slippage(self, price: float, side: str, slippage_bps: float | None = None) -> float:
        bps = float(slippage_bps) if slippage_bps is not None else self.cfg.slippage_bps
        adj = price * bps / 10000.0
        return price + adj if side == "buy" else max(0.0, price - adj)

    def fill(self, side: str, price: float, quantity: int, slippage_bps: float | None = None) -> Fill:
        """计算一笔成交的成本与现金变动。

        Args:
            side: "buy" 或 "sell"
            price: 名义价(未含滑点)
            quantity: 股数(正整数)
        """
        side = (side or "").strip().lower()
        if side not in ("buy", "sell"):
            raise ValueError(f"side 必须是 buy/sell,得到 {side!r}")
        qty = int(quantity)
        if qty <= 0 or price <= 0:
            raise ValueError(f"price/quantity 必须为正,得到 price={price} qty={quantity}")

        fill_price = self._apply_slippage(price, side, slippage_bps)
        gross = fill_price * qty
        commission = max(gross * self.cfg.commission_rate, self.cfg.min_commission)
        stamp_duty = gross * self.cfg.stamp_duty_rate if side == "sell" else 0.0
        transfer_fee = gross * self.cfg.transfer_fee_rate
        slippage_cost = abs(fill_price - price) * qty
        explicit_fees = commission + stamp_duty + transfer_fee

        if side == "buy":
            cash_delta = -(gross + explicit_fees)
        else:
            cash_delta = gross - explicit_fees

        return Fill(
            side=side,
            price=float(price),
            fill_price=round(fill_price, 6),
            quantity=qty,
            gross=round(gross, 4),
            commission=round(commission, 4),
            stamp_duty=round(stamp_duty, 4),
            transfer_fee=round(transfer_fee, 4),
            slippage_cost=round(slippage_cost, 4),
            explicit_fees=round(explicit_fees, 4),
            friction=round(explicit_fees + slippage_cost, 4),
            cash_delta=round(cash_delta, 4),
        )

    def round_trip_pnl(
        self, entry_price: float, exit_price: float, quantity: int,
        slippage_bps: float | None = None,
    ) -> dict:
        """一买一卖的完整盈亏(扣全部成本)。便于单笔回测与对账。"""
        buy = self.fill("buy", entry_price, quantity, slippage_bps)
        sell = self.fill("sell", exit_price, quantity, slippage_bps)
        # 现金口径:买入流出 -cash_delta(正数),卖出流入 cash_delta
        invested = -buy.cash_delta
        proceeds = sell.cash_delta
        pnl = proceeds - invested
        pnl_pct = (pnl / invested * 100.0) if invested > 0 else 0.0
        total_cost = buy.friction + sell.friction
        return {
            "entry_price": float(entry_price),
            "exit_price": float(exit_price),
            "quantity": int(quantity),
            "invested": round(invested, 4),
            "proceeds": round(proceeds, 4),
            "pnl": round(pnl, 4),
            "pnl_pct": round(pnl_pct, 4),
            "total_cost": round(total_cost, 4),
            "buy": buy,
            "sell": sell,
        }


# 全局默认实例(可被覆盖配置)
DEFAULT_COST_MODEL = CostModel()

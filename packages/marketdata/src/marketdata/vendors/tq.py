"""通达信TQ行情 vendor(本机网关 http://127.0.0.1:5100, JSON-RPC)。

链路: PanWatch(容器, host网络可达宿主127.0.0.1:5100) → frps(云7100/5100)
      → 小主机frpc → 通达信客户端自带TQ HTTP服务(127.0.0.1:17709)。

实测延迟(上海生产机): 快照/扩展指标 ~27-30ms, K线(10只×250日) ~48ms,
并发10路单次中位67ms — 全部远优于腾讯/东财 HTTP 爬源。
仅 CN 市场可用; 客户端未开时接口连接失败 → Engine 自动降级下一源。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from marketdata.symbol import Market, Symbol
from marketdata.types import Bar, MoreInfo, Quote
from marketdata.vendors.base import KlineVendor, MoreInfoVendor, QuoteVendor

logger = logging.getLogger(__name__)

_TQ_URL = (os.environ.get("TDX_QUANT_URL") or "http://172.18.0.1:5100/").rstrip("/") + "/"  # 生产容器 env 注入直连地址; 兜底旧 frps 网关
_TIMEOUT_S = 4.0  # 正常 <100ms; 隧道断开时快速失败交给降级链


def _tq_enabled() -> bool:
    """通达信客户端接口总开关(默认关闭)。开启需 PANWATCH_ENABLE_TQ=1。

    默认关闭后 _rpc 快失败(不发网络请求), Engine 自动走下一源(腾讯/东财),
    more-info 端点回 404、前/后端按“无数据”显式标注。
    """
    return os.environ.get("PANWATCH_ENABLE_TQ", "0") == "1"


def _rpc(method: str, params: dict, timeout: float = _TIMEOUT_S):
    """发 JSON-RPC; 返回 result.Value 或抛异常(Engine 捕获后转下一源)。"""
    if not _tq_enabled():
        raise RuntimeError("TQ disabled by default (set PANWATCH_ENABLE_TQ=1 to enable)")
    body = json.dumps({"id": 1, "method": method, "params": params}, ensure_ascii=False).encode("utf-8")
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(_TQ_URL, content=body, headers={"Content-Type": "application/json; charset=utf-8"})
        resp.raise_for_status()
        data = json.loads(resp.content.decode("utf-8"))
    if "error" in data:
        raise RuntimeError(f"TQ rpc error: {data['error']}")
    result = data.get("result") or {}
    # 快照类直接平铺在 result 里(ErrorId 字段共存); 列表/K线在 result.Value
    err = str(result.get("ErrorId", "0"))
    if err not in ("0", "") and "Value" in result or (err not in ("0", "") and "Value" not in result):
        raise RuntimeError(f"TQ {method} ErrorId={err}: {result.get('Error', '')}")
    return result.get("Value", result)


def _to_float(v) -> float | None:
    try:
        f = float(str(v).strip())
        return f
    except Exception:  # noqa: BLE001
        return None


def _to_int(v) -> int | None:
    try:
        return int(float(str(v).strip()))
    except Exception:  # noqa: BLE001
        return None


def to_tq_code(sym: Symbol) -> str | None:
    """CN 代码 → TQ 格式(600519.SH / 000001.SZ / 430047.BJ); 非 CN 返回 None。"""
    code = sym.code.strip()
    if sym.market != Market.CN or len(code) != 6 or not code.isdigit():
        return None
    if code.startswith(("6", "9", "5")):
        return f"{code}.SH"
    if code.startswith(("4", "8", "92")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _parse_more_info(symbol: str, raw: dict) -> MoreInfo:
    """将 get_more_info 原始 dict 解析为 MoreInfo 强类型 + raw 透传。"""
    return MoreInfo(
        symbol=symbol,
        market="CN",
        turnover_rate=_to_float(raw.get("fHSL")),
        volume_ratio=_to_float(raw.get("fLianB")),
        commission_ratio=_to_float(raw.get("Wtb")),
        total_market_value=_to_float(raw.get("Zsz")),
        circulating_market_value=_to_float(raw.get("Ltsz")),
        change_pct=_to_float(raw.get("ZAF")),
        change_pct_5d=_to_float(raw.get("ZAFPre5")),
        change_pct_20d=_to_float(raw.get("ZAFPre20")),
        change_pct_ytd=_to_float(raw.get("ZAFYear")),
        limit_up_amount=_to_float(raw.get("FCAmo")),
        limit_up_ratio=_to_float(raw.get("FCb")),
        open_amount=_to_float(raw.get("OpenAmo")),
        open_limit_buy=_to_float(raw.get("OpenZTBuy")),
        consecutive_limit_days=_to_int(raw.get("EverZTCount")),
        consecutive_up_days=_to_int(raw.get("ConZAFDateNum")),
        pe_dynamic=_to_float(raw.get("DynaPE")),
        pe_ttm=_to_float(raw.get("StaticPE_TTM")),
        pb=_to_float(raw.get("PB_MRQ")),
        dividend_yield=_to_float(raw.get("DYRatio")),
        beta=_to_float(raw.get("BetaValue")),
        ma5_price=_to_float(raw.get("MA5Value")),
        high_52w=_to_float(raw.get("HisHigh")),
        low_52w=_to_float(raw.get("HisLow")),
        l2_tick_num=_to_int(raw.get("L2TicNum")),
        l2_order_num=_to_int(raw.get("L2OrderNum")),
        total_buy_vol=_to_float(raw.get("TotalBVol")),
        total_sell_vol=_to_float(raw.get("TotalSVol")),
        cancel_buy=_to_float(raw.get("BCancel")),
        cancel_sell=_to_float(raw.get("SCancel")),
        zjl=_to_float(raw.get("Zjl")),
        zjl_hb=_to_float(raw.get("Zjl_HB")),
        raw=dict(raw),
        quote_time=datetime.now(ZoneInfo("Asia/Shanghai")),
    )


class TqQuoteVendor(QuoteVendor):
    name = "tq"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Quote]:
        out: list[Quote] = []
        for sym in symbols:
            tqc = to_tq_code(sym)
            if not tqc:
                continue
            v = _rpc("get_market_snapshot", {"stock_code": tqc})
            if not isinstance(v, dict) or not v:
                continue
            now = _to_float(v.get("Now"))
            if now is None:
                continue
            last_close = _to_float(v.get("LastClose")) or 0.0
            change_amount = round(now - last_close, 4) if last_close else None
            change_pct = round((now - last_close) / last_close * 100, 4) if last_close else None
            inside = _to_float(v.get("Inside"))
            outside = _to_float(v.get("Outside"))
            # 尝试合并 more_info 丰富 quote（失败不阻塞，单 RPC 降级）
            turnover_rate = None
            volume_ratio = None
            pe_ratio = None
            pb_ratio = None
            circ_mv = None
            total_mv = None
            try:
                mi = _rpc("get_more_info", {"stock_code": tqc})
                if isinstance(mi, dict) and mi.get("ErrorId") in ("0", None, ""):
                    turnover_rate = _to_float(mi.get("fHSL"))
                    volume_ratio = _to_float(mi.get("fLianB"))
                    # 优先 DynaPE，否则 MorePE/StaticPE_TTM
                    pe_ratio = _to_float(mi.get("DynaPE")) or _to_float(mi.get("MorePE")) or _to_float(mi.get("StaticPE_TTM"))
                    pb_ratio = _to_float(mi.get("PB_MRQ"))
                    circ_mv = _to_float(mi.get("Ltsz"))
                    total_mv = _to_float(mi.get("Zsz"))
            except Exception:  # noqa: BLE001
                pass
            out.append(
                Quote(
                    symbol=sym.code,
                    market="CN",
                    name="",  # TQ快照不带名称, 上层已有名称映射; 不猜名
                    current_price=now,
                    prev_close=last_close or None,
                    open_price=_to_float(v.get("Open")),
                    high_price=_to_float(v.get("Max")),
                    low_price=_to_float(v.get("Min")),
                    change_amount=change_amount,
                    change_pct=change_pct,
                    volume=_to_float(v.get("Volume")),
                    turnover=_to_float(v.get("Amount")),
                    turnover_rate=turnover_rate,
                    volume_ratio=volume_ratio,
                    volume_inner=(inside if inside is not None else None),
                    volume_outer=(outside if outside is not None else None),
                    pe_ratio=pe_ratio,
                    pb_ratio=pb_ratio,
                    circulating_market_value=circ_mv,
                    total_market_value=total_mv,
                    quote_time=datetime.now(ZoneInfo("Asia/Shanghai")),
                )
            )
        return out


class TqMoreInfoVendor(MoreInfoVendor):
    """TQ 扩展指标 vendor - 直接透传 get_more_info 104字段。"""

    name = "tq"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[MoreInfo]:
        out: list[MoreInfo] = []
        for sym in symbols:
            tqc = to_tq_code(sym)
            if not tqc:
                continue
            try:
                raw = _rpc("get_more_info", {"stock_code": tqc})
            except Exception as e:  # noqa: BLE001
                logger.warning("TQ get_more_info %s failed: %s", tqc, e)
                continue
            if not isinstance(raw, dict) or raw.get("ErrorId") not in ("0", None, ""):
                # TQ 返回 ErrorId 非0 时 raw 可能含 Error 字段，直接跳过
                if isinstance(raw, dict) and raw.get("ErrorId") not in ("0", None, "", 0):
                    logger.warning("TQ get_more_info %s ErrorId=%s", tqc, raw.get("ErrorId"))
                    continue
            # 成功：raw 本身就是 104字段平铺 dict
            out.append(_parse_more_info(sym.code, raw))
        return out


class TqKlineVendor(KlineVendor):
    name = "tq"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Bar]:
        if not symbols:
            return []
        sym = symbols[0]
        tqc = to_tq_code(sym)
        if not tqc:
            return []
        try:
            days = int(config.get("days") or 120)
        except Exception:  # noqa: BLE001
            days = 120
        days = min(max(days, 1), 800)
        # TQ 冷缓存只返回最新1根 → 先刷新K线缓存(实测刷新后 count 生效)
        try:
            _rpc("refresh_kline", {"stock_list": [tqc], "period": "1d"}, timeout=_TIMEOUT_S)
        except Exception:  # noqa: BLE001  刷新失败不阻塞, 直接尝试取数
            pass
        v = _rpc(
            "get_market_data",
            {
                "stock_list": [tqc],
                "period": "1d",
                "count": days,
                "dividend_type": "front",
            },
            timeout=max(_TIMEOUT_S, 15.0),
        )
        rows = (v or {}).get(tqc) if isinstance(v, dict) else None
        if not rows:
            return []
        dates = rows.get("Date") or []
        opens = rows.get("Open") or []
        closes = rows.get("Close") or []
        highs = rows.get("High") or []
        lows = rows.get("Low") or []
        volumes = rows.get("Volume") or []
        out: list[Bar] = []
        for i, d in enumerate(dates):
            try:
                out.append(
                    Bar(
                        date=str(d),
                        open=float(opens[i]),
                        close=float(closes[i]),
                        high=float(highs[i]),
                        low=float(lows[i]),
                        volume=float(volumes[i]) if i < len(volumes) else 0.0,
                    )
                )
            except Exception:  # noqa: BLE001
                continue
        return out


def formula_mul(
    formula_name: str,
    stock_list: list[str],
    *,
    formula_arg: str = "",
    stock_period: str = "1d",
    count: int = -1,
    return_count: int = 1,
    dividend_type: int = 0,
    xsflag: int = -1,
) -> dict:
    """批量执行通达信指标公式(formula_process_mul_zb), 返回 {代码: {指标名: [值...]}}。

    周期参数 stock_period + periodstr 必须同传(TQ 17709 特有: 缺 periodstr 报
    "periodstr error"; 缺 stock_period 报 "formula data counts < 1")。
    常用内置公式: MACD / ZLJC(主力进出: JCL/JCM/JCS 三档净量)。
    L2_AMO 是公式函数(非独立公式), 需在客户端"公式管理器"自定义指标公式后按名调用。
    """
    params = {
        "formula_name": formula_name,
        "formula_arg": formula_arg,
        "stock_list": stock_list,
        "stock_period": stock_period,
        "periodstr": stock_period,
        "count": count,
        "return_count": return_count,
        "dividend_type": dividend_type,
        "xsflag": xsflag,
    }
    v = _rpc("formula_process_mul_zb", params, timeout=max(_TIMEOUT_S, 60.0))
    if not isinstance(v, dict):
        return {}
    v.pop("ErrorId", None)
    v.pop("Error", None)
    return v


def formula_zb_single(
    formula_name: str,
    stock_code: str,
    *,
    formula_arg: str = "",
    xsflag: int = -1,
) -> dict:
    """单只指标公式(依赖客户端当前打开的数据, 盘中实时单只场景)。"""
    v = _rpc("formula_zb", {
        "formula_name": formula_name,
        "formula_arg": formula_arg,
        "stock_code": stock_code,
        "xsflag": xsflag,
    }, timeout=max(_TIMEOUT_S, 60.0))
    if not isinstance(v, dict):
        return {}
    v.pop("ErrorId", None)
    v.pop("Error", None)
    return v

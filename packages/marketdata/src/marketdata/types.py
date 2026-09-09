"""请求 / 响应 / 行情数据类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Request:
    """一次数据请求。frozen=True 便于做缓存键。"""

    symbols: tuple[str, ...] = ()
    market: str = "CN"
    timeframe: str = "day"
    limit: int = 120
    since_hours: int = 12
    extra: tuple[tuple[str, Any], ...] = ()

    def cache_key(self, datatype: str) -> str:
        sym = ",".join(self.symbols)
        extra = ",".join(f"{k}={v}" for k, v in self.extra)
        return f"{datatype}|{self.market}|{self.timeframe}|{self.limit}|{self.since_hours}|{sym}|{extra}"


@dataclass
class Quote:
    """标准化实时报价。字段对齐 _parse_tencent_line 的产出。"""

    symbol: str
    market: str
    current_price: float
    name: str = ""
    prev_close: float | None = None
    open_price: float | None = None
    high_price: float | None = None
    low_price: float | None = None
    change_amount: float | None = None
    change_pct: float | None = None
    volume: float | None = None
    turnover: float | None = None
    turnover_rate: float | None = None
    volume_ratio: float | None = None
    volume_outer: float | None = None   # 外盘(主动买成交量, 手)
    volume_inner: float | None = None   # 内盘(主动卖成交量, 手)
    pe_ratio: float | None = None
    pb_ratio: float | None = None
    circulating_market_value: float | None = None
    total_market_value: float | None = None
    # 行情源提供的实际报价时间；无法确认时保持 None，不能用抓取时间冒充。
    quote_time: datetime | None = None
    timestamp: datetime = field(default_factory=datetime.now)
    # 实际命中的 vendor(引擎在 fetch 成功时回填, 空则填 vendor 名)。
    # 前端按此显式标注数据来源, 缺失时标“无数据”而非猜测。
    source: str = ""


@dataclass
class Bar:
    """标准化日K(对齐 PanWatch KlineData:date/open/close/high/low/volume)。"""

    date: str
    open: float
    close: float
    high: float
    low: float
    volume: float = 0.0


@dataclass
class CapitalFlow:
    """资金流向(对齐 PanWatch src/collectors/capital_flow_collector.CapitalFlow)。"""

    symbol: str
    name: str
    main_net_inflow: float | None = None      # 主力净流入
    main_net_inflow_pct: float | None = None   # 主力净流入占比
    super_net_inflow: float | None = None      # 超大单净流入
    big_net_inflow: float | None = None        # 大单净流入
    mid_net_inflow: float | None = None        # 中单净流入
    small_net_inflow: float | None = None      # 小单净流入
    main_net_5d: float | None = None           # 5日主力净流入
    date: str | None = None                    # 数据基准日(新浪/东财=T-1收盘; 盘中无当日实时)


@dataclass(frozen=True)
class BoardCapitalFlow:
    """板块资金流向(同花顺行业/概念资金,2026-08-09 加入)。"""

    board_name: str                              # 板块名称(行业/概念)
    board_type: str = "industry"                # industry 行业 / concept 概念
    index_value: float | None = None            # 板块指数
    change_pct: float | None = None             # 涨跌幅(%)
    inflow: float | None = None                 # 流入资金(亿)
    outflow: float | None = None                # 流出资金(亿)
    net_inflow: float | None = None             # 净额(亿)
    stock_count: int | None = None              # 公司家数
    leader_name: str = ""                       # 领涨股
    leader_change_pct: float | None = None      # 领涨股涨跌幅
    leader_price: float | None = None           # 领涨股当前价
    rank: int = 0                               # 排名
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class MarketCapitalFlow:
    """大盘资金流向汇总(全市场行业资金合计,2026-08-09 加入)。"""

    total_inflow: float | None = None           # 全市场总流入(亿)
    total_outflow: float | None = None          # 全市场总流出(亿)
    net_inflow: float | None = None             # 全市场净额(亿)
    board_count: int = 0                        # 参与汇总的板块数
    source: str = ""                            # 数据源
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class HotStock:
    """热门/异动股(对齐 PanWatch src/collectors/discovery_collector.HotStock)。"""

    symbol: str
    market: str
    name: str
    price: float | None
    change_pct: float | None
    turnover: float | None
    volume: float | None
    # --- 扩展字段(同花顺热点/热榜专用,2026-08-09 加入)---
    reason: str = ""                          # 题材归因/AI 分析
    rank: int = 0                             # 同花顺热榜序号
    heat: float | None = None                 # 人气值
    rank_chg: int = 0                         # 排名变化
    concepts: tuple[str, ...] = ()            # 概念标签
    change_amount: float | None = None        # 涨跌额
    source: str = ""                          # 数据源标识
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class HotBoard:
    """热门板块(对齐 PanWatch src/collectors/discovery_collector.HotBoard)。"""

    code: str
    name: str
    change_pct: float | None
    change_amount: float | None
    turnover: float | None


@dataclass
class EventItem:
    """结构化事件(对齐 PanWatch src/collectors/events_collector.EventItem)。"""

    source: str
    external_id: str
    event_type: str
    title: str
    publish_time: datetime
    symbols: list[str]
    importance: int
    url: str


@dataclass
class Fundamentals:
    """标准化基本面/财务数据(按 symbol)。估值类字段/财报类字段来源不同、可能分批到位,
    拿不到的字段一律 None,不伪造。"""

    symbol: str
    market: str
    name: str = ""
    # —— 估值类 ——
    pe_ttm: float | None = None                    # 市盈率(TTM)
    pe_static: float | None = None                  # 市盈率(静态)
    pb: float | None = None                         # 市净率
    ps_ttm: float | None = None                     # 市销率(TTM)
    total_market_value: float | None = None         # 总市值(亿)
    circulating_market_value: float | None = None   # 流通市值(亿)
    dividend_yield: float | None = None             # 股息率(%)
    total_shares: float | None = None               # 总股本(股)
    float_shares: float | None = None                # 流通股本(股)
    # —— 财报类 ——
    eps: float | None = None                        # 每股收益
    bps: float | None = None                        # 每股净资产
    roe: float | None = None                        # 净资产收益率(%)
    revenue: float | None = None                    # 营业收入
    net_profit: float | None = None                 # 归母净利润
    gross_margin: float | None = None               # 毛利率(%)
    net_margin: float | None = None                 # 净利率(%)
    revenue_yoy: float | None = None                # 营收同比增长(%)
    net_profit_yoy: float | None = None             # 净利润同比增长(%)
    report_date: str = ""                           # 报告期(原样字符串,不做日期解析)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class DragonTigerItem:
    """龙虎榜(东财每日龙虎榜明细,市场级,按 date 过滤)。字段待实抓校准。"""

    trade_date: str
    symbol: str
    name: str = ""
    reason: str | None = None          # 上榜原因
    close: float | None = None         # 收盘价
    change_pct: float | None = None    # 涨跌幅(%)
    net_buy: float | None = None       # 龙虎榜净买额(元)
    buy_amt: float | None = None       # 龙虎榜买入额(元)
    sell_amt: float | None = None      # 龙虎榜卖出额(元)
    turnover_pct: float | None = None  # 换手率(%)
    # 2026-08-20: 席位级明细(ftshare 独有,东财 datacenter 公开 API 无)。仅 ftshare 填。
    top_buyers: list | None = None     # [{name, buy, sell, net}, ...] 前5买方席位
    top_sellers: list | None = None    # [{name, buy, sell, net}, ...] 前5卖方席位


@dataclass
class MarginItem:
    """融资融券(东财 datacenter,按 symbol,取最新一条快照)。字段待实抓校准。"""

    date: str
    symbol: str
    rz_balance: float | None = None     # 融资余额(元)
    rz_buy: float | None = None         # 融资买入额(元)
    rz_repay: float | None = None       # 融资偿还额(元)
    rq_balance: float | None = None     # 融券余额(元)
    rq_sell_vol: float | None = None    # 融券卖出量(股)
    rq_repay_vol: float | None = None   # 融券偿还量(股)
    total_balance: float | None = None  # 两融余额(元)


@dataclass
class ShareholderItem:
    """股东户数(东财 datacenter,按 symbol,取最新一期)。字段待实抓校准。"""

    report_date: str
    symbol: str
    holder_num: int | None = None      # 股东户数
    change_num: int | None = None      # 户数变化(较上期)
    change_ratio: float | None = None  # 户数环比变化(%)
    avg_shares: float | None = None    # 户均持股(股)


@dataclass
class DividendItem:
    """分红(东财 datacenter,按 symbol,返回该只全部历史)。字段待实抓校准。"""

    ex_date: str
    symbol: str
    dividend_per_share: float | None = None  # 每股派息(税前,元)
    transfer_ratio: float | None = None      # 每10股转增(股)
    bonus_ratio: float | None = None         # 每10股送股(股)
    progress: str = ""                       # 方案进度


@dataclass
class NorthboundItem:
    """北向资金(同花顺 hexin 当日分钟累计净买入,市场级,取当日末值快照)。
    字段待实抓校准(沙箱代理拦截,无法验证真实响应结构)。"""

    date: str
    hgt_net: float | None = None   # 沪股通净买入(亿元)
    sgt_net: float | None = None   # 深股通净买入(亿元)⚠️ 近期不可靠(可能 NaN/量级异常),需容错
    total_net: float | None = None  # 北向合计=hgt_net+sgt_net;任一为 None 则 None(不臆造)
    time: str = ""                  # 末值对应的分钟时间点(可选)


@dataclass
class MoreInfo:
    """通达信 get_more_info 扩展指标(104字段, 2026-08-26 P1)。TQ独有, 盘中实时。
    核心字段强类型, 全量原始值保留在 raw 供前端透传。
    """

    symbol: str
    market: str = "CN"
    name: str = ""
    # —— 行情扩展 ——
    turnover_rate: float | None = None       # fHSL 换手率(%)
    volume_ratio: float | None = None        # fLianB 量比
    commission_ratio: float | None = None    # Wtb 委比(%)
    total_market_value: float | None = None  # Zsz 总市值(亿)
    circulating_market_value: float | None = None  # Ltsz 流通市值(亿)
    # —— 涨幅序列 ——
    change_pct: float | None = None          # ZAF 当日涨幅(%)
    change_pct_5d: float | None = None       # ZAFPre5 5日涨幅
    change_pct_20d: float | None = None      # ZAFPre20 20日涨幅
    change_pct_ytd: float | None = None      # ZAFYear 年初至今
    # —— 封单竞价 ——
    limit_up_amount: float | None = None     # FCAmo 封单额(万元)
    limit_up_ratio: float | None = None      # FCb 封成比
    open_amount: float | None = None         # OpenAmo 开盘金额(万元)
    open_limit_buy: float | None = None      # OpenZTBuy 竞价涨停买入金额(万元)
    # —— 连板 ——
    consecutive_limit_days: int | None = None  # EverZTCount 连板天
    consecutive_up_days: int | None = None   # ConZAFDateNum 连涨天数
    # —— 估值 ——
    pe_dynamic: float | None = None          # DynaPE 动态市盈率
    pe_ttm: float | None = None              # StaticPE_TTM TTM市盈率
    pb: float | None = None                  # PB_MRQ 市净率
    dividend_yield: float | None = None      # DYRatio 股息率(%)
    beta: float | None = None                # BetaValue
    # —— 其他 ——
    ma5_price: float | None = None           # MA5Value 5日均价
    high_52w: float | None = None            # HisHigh 52周最高
    low_52w: float | None = None             # HisLow 52周最低
    # —— L2 逐笔(需开通Level-2, 未开通时为 0, 盘中实时, 2026-08-26 增补) ——
    l2_tick_num: int | None = None           # L2TicNum L2逐笔数
    l2_order_num: int | None = None          # L2OrderNum L2委托数
    total_buy_vol: float | None = None       # TotalBVol 总买量(手)
    total_sell_vol: float | None = None      # TotalSVol 总卖量(手)
    cancel_buy: float | None = None          # BCancel 撤买量(手)
    cancel_sell: float | None = None         # SCancel 撤卖量(手)
    zjl: float | None = None                 # Zjl 主买净额(万元)
    zjl_hb: float | None = None              # Zjl_HB 主力净流入(万元, 同花顺口径'主力净额')
    listed_date: str = ""                    # J_start 上市日期(来自get_stock_info时填)
    # —— 原始透传 ——
    raw: dict = field(default_factory=dict)  # 完整104字段原始字符串值
    quote_time: datetime | None = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class DarkFlowTq:
    """通达信 L2 暗盘资金(逐笔还原 + 十档盘口, 盘后, ZCode TQ4 采集)。

    数据源: 超盘回放落盘 .tck(逐笔成交+委托, 带委托号) + .img(十档盘口),
    由小主机 ZCode 盘后采集解析, 产出 JSON 同步到 DATA_DIR/darkflow/。
    盘后数据(超盘回放=历史日), 盘中无 → 字段为 None 时前端显式标"盘后补全"。
    """

    symbol: str
    market: str = "CN"
    date: str = ""                          # 交易日期 YYYYMMDD
    # —— 自建分档(按委托还原总金额切, 万元; 替代 L2AMO formula) ——
    xl_net: float | None = None             # 超大单净额(委托还原≥100万)
    large_net: float | None = None          # 大单净额(20-100万)
    mid_net: float | None = None            # 中单净额(5-20万)
    small_net: float | None = None          # 小单净额(<5万)
    # —— 委托号聚簇还原(拆单识别) ——
    total_orders: int | None = None         # 逐笔委托总笔数
    reconstructed_orders: int | None = None # 聚簇还原后委托数
    split_order_count: int | None = None    # 拆单委托数(一笔委托拆多笔成交)
    avg_split_parts: float | None = None    # 平均拆单份数
    # —— 撤单动向 ——
    cancel_ratio: float | None = None       # 撤单比(撤单笔数/总笔数, 0~1)
    cancel_buy_vol: float | None = None     # 撤买量(手)
    cancel_sell_vol: float | None = None    # 撤卖量(手)
    # —— 十档盘口意图 ——
    tuopan: bool | None = None              # 托盘(某档委托量≥4倍均值且金额≥500万)
    yapan: bool | None = None               # 压盘
    suopan: bool | None = None              # 锁盘(买卖档委托量巨大且价差收窄)
    data_status: str = "insufficient"       # complete / insufficient
    raw: dict = field(default_factory=dict) # 原始 JSON 透传
    quote_time: datetime | None = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class FlashNews:
    """快讯(7×24,对齐 cls/sina/eastmoney 快讯流)。市场级,symbols 可空。"""

    source: str
    external_id: str
    title: str
    content: str
    publish_time: datetime
    symbols: list[str] = field(default_factory=list)
    importance: int = 0
    url: str = ""


@dataclass
class NewsArticle:
    """新闻资讯(个股新闻+公告,对齐 PanWatch src/collectors/news_collector.NewsItem)。
    来源可为 xueqiu(雪球个股新闻)/ eastmoney_news(东财个股新闻搜索)/ eastmoney(东财公告)。"""

    source: str
    external_id: str
    title: str
    content: str
    publish_time: datetime
    symbols: list[str] = field(default_factory=list)
    importance: int = 0
    url: str = ""


@dataclass
class Response:
    """Engine 返回:承载 payload + 命中的 vendor/延迟。"""

    ok: bool
    data: Any = None
    error: str = ""
    vendor: str = ""
    latency_ms: int = 0

    @property
    def is_empty(self) -> bool:
        if self.data is None:
            return True
        if isinstance(self.data, (list, tuple, dict, set)) and len(self.data) == 0:
            return True
        return False

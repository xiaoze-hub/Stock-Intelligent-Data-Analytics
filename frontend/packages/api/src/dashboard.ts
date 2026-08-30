import { fetchAPI } from './client'
import type { StrategySignalItem } from './recommendations'

type QueryValue = string | number | boolean | null | undefined

function withQuery(path: string, params: Record<string, QueryValue>): string {
  const q = new URLSearchParams()
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v === undefined || v === null) return
    const sv = String(v).trim()
    if (!sv) return
    q.set(k, sv)
  })
  const s = q.toString()
  return s ? `${path}?${s}` : path
}

export interface DashboardMarketIndex {
  symbol: string
  name: string
  market: string
  current_price: number | null
  change_pct: number | null
  change_amount: number | null
  prev_close: number | null
  /** 近20日收盘价,用于首页指数走势 sparkline;取数失败/无映射(如美股指数)则为空数组 */
  spark?: number[]
}

export interface DashboardMarketCapitalFlow {
  total_main_flow?: number          // 两市主力净流入(亿)
  sh_flow?: number
  sz_flow?: number
  cyb_flow?: number
  total_amount?: number             // 两市成交额(亿)
  up_count?: number
  down_count?: number
  flat_count?: number
  sh?: { point?: number; change_pct?: number } | null
  sz?: { point?: number; change_pct?: number } | null
  cyb?: { point?: number; change_pct?: number } | null
  source?: string
  timestamp?: string
  inflow_boards?: { name: string; net_inflow: number; change_pct?: number | null }[]
  outflow_boards?: { name: string; net_inflow: number; change_pct?: number | null }[]
}

/** 异动池条目(东财异动池, 首页区块)。字段尽量宽松, 兼容后端不同命名。 */
export interface MarketAnomalyItem {
  symbol?: string
  code?: string                    // 兼容: 部分数据源用 code
  name?: string
  market?: string                  // CN / HK / US
  change_pct?: number | null       // 当日涨跌幅(%)
  /** 累计偏离(%); 后端可能给 deviation / deviation_pct / bias */
  deviation?: number | null
  deviation_pct?: number | null
  /** 累计偏离持续天数(后端字段为 days; 兼容 deviation_days) */
  deviation_days?: number
  days?: number
  /** 触发规则文字(可能同时给 rule / reason) */
  rule?: string
  reason?: string
  /** 是否当日新异动 */
  is_today?: boolean
  /** 触发规则代码(东财 rule_code, 展示可不依赖) */
  rule_code?: number
  trade_date?: string
  source?: string
}

/** 异动池响应: {count, items} 或 {data:[...]} 或裸数组(组件侧归一化) */
export interface MarketAnomaliesResponse {
  count?: number
  items?: MarketAnomalyItem[]
  data?: MarketAnomalyItem[]
}

/** 热榜条目(同花顺热榜, 首页区块)。 */
export interface MarketHotStockItem {
  rank?: number
  symbol?: string
  code?: string
  name?: string
  market?: string
  change_pct?: number | null
  /** 热度(数字或带单位字符串, 原样展示) */
  heat?: number | string | null
  /** 概念标签 */
  concepts?: string[]
  tags?: string[]                  // 兼容字段名
  /** AI 归因摘要(展示时截断) */
  reason?: string
  summary?: string                 // 兼容字段名
}

/** 热榜响应: {period, count, items} 或 {data:[...]} 或裸数组(组件侧归一化) */
export interface MarketHotStocksResponse {
  period?: string
  count?: number
  items?: MarketHotStockItem[]
  data?: MarketHotStockItem[]
}

export interface DashboardMarketStatus {
  code: string
  name: string
  status: string
  status_text: string
  is_trading: boolean
  sessions: string[]
  local_time: string
}

export interface DashboardPosition {
  id: number
  stock_id: number
  symbol: string
  name: string
  market: string
  cost_price: number
  quantity: number
  invested_amount: number | null
  trading_style: string
  current_price: number | null
  change_pct: number | null
}

export interface DashboardAccountSummary {
  id: number
  name: string
  available_funds: number
  total_cost: number
  total_market_value: number
  total_pnl: number
  total_pnl_pct: number
  /** 行情源所属交易日的盈亏汇总(元) */
  total_daily_pnl: number
  daily_pnl_period: 'today' | 'previous_trading_day' | 'mixed' | 'unknown'
  daily_pnl_label: string
  daily_pnl_date: string | null
  total_assets: number
  positions: DashboardPosition[]
}

export interface DashboardPortfolioSummary {
  accounts: DashboardAccountSummary[]
  total: {
    total_market_value: number
    total_cost: number
    total_pnl: number
    total_pnl_pct: number
    /** 行情源所属交易日的全账户盈亏汇总(元) */
    total_daily_pnl: number
    daily_pnl_period: 'today' | 'previous_trading_day' | 'mixed' | 'unknown'
    daily_pnl_label: string
    daily_pnl_date: string | null
    available_funds: number
    total_assets: number
  }
  exchange_rates?: {
    HKD_CNY: number
    USD_CNY?: number
  }
}

export interface DashboardWatchStock {
  id: number
  symbol: string
  name: string
  market: string
}

export interface DashboardQuoteRequestItem {
  symbol: string
  market: string
}

export interface DashboardQuoteResponse {
  symbol: string
  market: string
  current_price: number | null
  change_pct: number | null
}

export interface DashboardHistoryItem {
  id: number
  agent_name: string
  stock_symbol: string
  analysis_date: string
  title: string
  content: string
  created_at: string
}

export interface DashboardSuggestion {
  action: string
  action_label: string
  signal?: string | null
  reason?: string | null
  should_alert?: boolean
  agent_label?: string | null
}

export interface DashboardMonitorStock {
  symbol: string
  name: string
  market: string
  current_price: number
  change_pct: number
  open_price: number | null
  high_price: number | null
  low_price: number | null
  volume: number | null
  turnover: number | null
  alert_type: string | null
  has_position: boolean
  cost_price: number | null
  pnl_pct: number | null
  trading_style: string | null
  suggestion: DashboardSuggestion | null
}

export interface DashboardIntradayScanResponse {
  stocks: DashboardMonitorStock[]
  available_funds: number
}

export interface DashboardInsightItem {
  id: number
  agent_name: string
  agent_label: string
  analysis_date: string
  title: string
  updated_at: string
}

export interface DashboardMarketStock {
  symbol: string
  market: string
  name: string
  score_seed: number
  change_pct?: number | null
  turnover?: number | null
  source: string
}

export interface DashboardTopicItem {
  name: string
  score: number
  sentiment: string
}

export interface DashboardRiskSignalItem extends StrategySignalItem {
  risk_flags?: string[]
}

/** 数据源健康(后端 src/core/data_source_health): 首页"主力动向"区块指示灯数据。
 *  后端异常时给兜底值, 字段全部可选、宽松。 */
export interface DataSourceHealth {
  /** TQ JSON-RPC 是否在线 */
  tq_online?: boolean
  /** 数据是否为最近交易日(开盘前/休市 Now==0 属正常, 以 HqDate 为准) */
  data_is_today?: boolean
  /** 快照实时价(可能为 0/None) */
  last_price?: number | null
  /** 行情日期(如 20260828) */
  hq_date?: string
  checked_at?: string
  tq_url?: string
}

/** 主力动向条目: 单只自选股的主力意图 + 暗盘资金(后端 compute_dark_flow 摘要)。 */
export interface DashboardMainForceItem {
  code?: string
  name?: string
  /** 主力意图文本(来自 compute_dark_flow.signal) */
  intent?: string
  /** 暗盘净额(元, 全量主动净额) */
  dark_net?: number | null
  /** 主力净额(元, ≥20万腾讯官方口径) */
  main_net?: number | null
  /** 数据状态: ok / insufficient(逐笔不足) / suspect(疑重复计数) */
  data_status?: string
}

export interface DashboardOverviewResponse {
  generated_at: string
  market: 'ALL' | 'CN' | 'HK' | 'US'
  snapshot_date: string
  data_freshness: {
    strategy_snapshot_date: string
    entry_snapshot_date: string
    market_scan_snapshot_date: string
    latest_history_updated_at: string
  }
  kpis: {
    watchlist_count: number
    positions_count: number
    available_funds: number
    invested_cost: number
    total_assets_estimate: number
    executable_opportunities: number
    risk_positions: number
    win_rate_3d?: number | null
    win_sample_3d: number
    errors_24h: number
  }
  portfolio: {
    positions_count: number
    watchlist_count: number
    available_funds: number
    invested_cost: number
    by_market: Array<{
      market: string
      positions: number
      invested_cost: number
    }>
  }
  action_center: {
    opportunities: StrategySignalItem[]
    risk_items: DashboardRiskSignalItem[]
  }
  market_pulse: {
    hot_stocks: DashboardMarketStock[]
    hot_topics: DashboardTopicItem[]
  }
  strategy: {
    coverage: Record<string, any>
    factor_stats: Record<string, any>
    by_market: Array<Record<string, any>>
    top_by_strategy: Array<Record<string, any>>
  }
  insights: DashboardInsightItem[]
  /** 数据源健康指示灯(可选: 老后端/降级时可能缺失) */
  data_source_health?: DataSourceHealth | null
  /** 自选股主力动向(暗盘资金, 最多 8 只; 降级时为空数组/缺失) */
  main_force?: DashboardMainForceItem[] | null
}

export const dashboardApi = {
  indices: () => fetchAPI<DashboardMarketIndex[]>('/market/indices'),

  marketCapitalFlow: () => fetchAPI<DashboardMarketCapitalFlow>('/market-data/market-capital-flow'),

  /** 异动池(东财):首页区块数据, 独立加载, 失败静默 */
  anomalies: (params?: { limit?: number }) =>
    fetchAPI<MarketAnomaliesResponse>(withQuery('/market-data/anomalies', { limit: params?.limit })),

  /** 热榜(同花顺):首页区块数据, 独立加载, 失败静默 */
  hotStocks: (params?: { period?: string; limit?: number }) =>
    fetchAPI<MarketHotStocksResponse>(withQuery('/market-data/hot-stocks', {
      period: params?.period,
      limit: params?.limit,
    })),

  marketStatus: () => fetchAPI<DashboardMarketStatus[]>('/stocks/markets/status'),

  portfolioSummary: (params?: { include_quotes?: boolean }) =>
    fetchAPI<DashboardPortfolioSummary>(
      withQuery('/portfolio/summary', {
        include_quotes: params?.include_quotes,
      })
    ),

  watchlist: () => fetchAPI<DashboardWatchStock[]>('/stocks'),

  batchQuotes: (items: DashboardQuoteRequestItem[]) =>
    fetchAPI<DashboardQuoteResponse[]>('/quotes/batch', {
      method: 'POST',
      body: JSON.stringify({ items }),
    }),

  history: (params: Record<string, QueryValue>) =>
    fetchAPI<DashboardHistoryItem[]>(withQuery('/history', params)),

  intradayScan: (params?: { analyze?: boolean }) =>
    fetchAPI<DashboardIntradayScanResponse>(
      withQuery('/agents/intraday/scan', {
        analyze: params?.analyze,
      }),
      {
        method: 'POST',
        timeoutMs: params?.analyze ? 90000 : 30000,
      }
    ),

  overview: (params?: {
    market?: 'ALL' | 'CN' | 'HK' | 'US'
    action_limit?: number
    risk_limit?: number
    days?: number
  }) => {
    const q = new URLSearchParams()
    if (params?.market) q.set('market', params.market)
    if (typeof params?.action_limit === 'number') q.set('action_limit', String(params.action_limit))
    if (typeof params?.risk_limit === 'number') q.set('risk_limit', String(params.risk_limit))
    if (typeof params?.days === 'number') q.set('days', String(params.days))
    const qs = q.toString()
    return fetchAPI<DashboardOverviewResponse>(`/dashboard/overview${qs ? `?${qs}` : ''}`, {
      timeoutMs: 45000,
    })
  },

  curate: (candidates: CurateCandidate[], model_id?: number) =>
    fetchAPI<{ items: CuratedItem[] }>('/dashboard/curate', {
      method: 'POST',
      body: JSON.stringify({ candidates, model_id }),
      timeoutMs: 40000,
    }),

  brief: (type: 'premarket' | 'eod') =>
    fetchAPI<DashboardBrief>(`/dashboard/brief?type=${type}`),
}

export interface DashboardBrief {
  empty?: boolean
  type: string
  agent_label?: string
  title?: string
  content?: string
  date?: string
  updated_at?: string
  stocks?: { name: string; symbol: string; market: string }[]
}

export interface CurateCandidate {
  type: string
  symbol?: string
  name?: string
  market?: string
  signal?: string
  change_pct?: number | null
}

export interface CuratedItem {
  index: number
  importance: number
  why: string
}

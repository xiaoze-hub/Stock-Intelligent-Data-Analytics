import { fetchAPI } from './client'

export interface StrategyItem {
  id: string
  display_name: string
  description: string
  category: string
  tags: string[]
  ui_badge: string
  source: string
  filter: Record<string, number | string>
  eod_fields: string[]
  data_window: 'realtime' | 'eod'
  available_now: boolean
}

export interface StrategyListResponse {
  items: StrategyItem[]
  total: number
}

export interface ApplyRequest {
  strategy_id: string
  symbol: string
  market?: string
}

export interface ScoreFactor {
  factor: string
  raw: number | string
  score: number
  weight: number
}

export interface ApplyResult {
  strategy_id: string
  symbol: string
  market: string
  passed: boolean
  score: number
  score_breakdown: ScoreFactor[]
  failed_filters: { field: string; actual: number; required: string; threshold: number }[]
  missing_fields: string[]
  current_data: Record<string, number | string | null>
  error?: string
}

export interface ScanRequest {
  strategy_id: string
  market?: string
  limit?: number
  universe?: 'all' | 'watchlist'
  min_score?: number
  symbol_limit?: number
  /** 自定义股票池(共振查询精筛): 传入则只扫这几只, 优先于 universe, ≤100 只 */
  symbols?: string[]
}

export interface ScanItem {
  symbol: string
  name: string
  market: string
  score: number
  score_breakdown: ScoreFactor[]
  current_data: Record<string, number | string | null>
  missing_fields: string[]
}

export interface ScanResult {
  items: ScanItem[]
  total: number
  scanned: number
  quoted: number
  message?: string
}

/** L1 周滚动 HitRate 榜(2026-09-10): GET /api/strategies/hitrate-board */
export interface HitrateRow {
  strategy_code: string
  horizon_days: number
  n: number
  hits: number
  hit_rate: number
  target_rate: number
  stop_rate: number
  avg_ret: number | null
  status: string
}

export interface HitrateBoard {
  asof: string
  window_days: number
  hit_def: string
  rows: HitrateRow[]
}

/** L3 市况×信号矩阵(2026-09-10): GET /api/strategies/regime-matrix */
export interface RegimeCell {
  phase: string
  strategy_code: string
  n: number
  hits: number
  hit_rate: number
  avg_ret: number | null
  status: string
}

export interface RegimeMatrix {
  asof: string
  window_days: number
  current_phase: string | null
  hit_def: string
  cells: RegimeCell[]
  best: Record<string, { strategy_code: string; hit_rate: number; n: number }>
}

export const strategiesApi = {
  list: () => fetchAPI<StrategyListResponse>(`/strategies/list`),

  hitrateBoard: (windowDays = 28) =>
    fetchAPI<HitrateBoard>(`/strategies/hitrate-board?window_days=${windowDays}`),

  regimeMatrix: (windowDays = 90) =>
    fetchAPI<RegimeMatrix>(`/strategies/regime-matrix?window_days=${windowDays}`),

  get: (id: string) => fetchAPI<StrategyItem>(`/strategies/${encodeURIComponent(id)}`),

  apply: (req: ApplyRequest) =>
    fetchAPI<ApplyResult>(`/strategies/apply`, {
      method: 'POST',
      body: JSON.stringify(req),
    }),

  scan: (req: ScanRequest) =>
    fetchAPI<ScanResult>(`/strategies/scan`, {
      method: 'POST',
      body: JSON.stringify(req),
    }),
}
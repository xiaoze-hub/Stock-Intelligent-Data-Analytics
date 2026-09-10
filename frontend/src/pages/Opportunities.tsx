import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, BookOpen, Filter, RefreshCw, Share2, Sparkles, ScanSearch, ThumbsDown, ThumbsUp, Download } from 'lucide-react'
import {
  getToken,
  fetchAPI,
  recommendationsApi,
  stocksApi,
  strategiesApi,
  tdxApi,
  wencaiApi,
  type EntryCandidateItem,
  type ScanItem,
  type StrategyCatalogItem,
  type StrategyItem,
  type StrategySignalItem,
  type StrategyStatsResponse,
  type TdxAskResponse,
  type WencaiRow,
} from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { Popover, PopoverContent, PopoverTrigger } from '@panwatch/base-ui/components/ui/popover'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@panwatch/base-ui/components/ui/tabs'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { useLocalStorage } from '@/lib/utils'
import StockInsightModal from '@panwatch/biz-ui/components/stock-insight-modal'
import FactorWeightsPanel from '@/components/FactorWeightsPanel'
import SignalScoreShareCard from '@/components/SignalScoreShareCard'
import WencaiPanel from '@panwatch/biz-ui/components/WencaiPanel'
import AuctionAnomalyTab from '@panwatch/biz-ui/components/AuctionAnomalyTab'
import StrategyHitrateBoard from '@panwatch/biz-ui/components/StrategyHitrateBoard'
import RegimeSignalMatrix from '@panwatch/biz-ui/components/RegimeSignalMatrix'
import AbnormalMovesCard from '@panwatch/biz-ui/components/AbnormalMovesCard'
import StrategyLibraryDialog from '@/components/StrategyLibraryDialog'

type SourceFilter = 'all' | 'market_scan' | 'watchlist' | 'mixed' | 'strategy' | 'auction' | 'tdx' | 'wencai'
type HoldingFilter = 'all' | 'held' | 'unheld'
type RiskFilter = 'all' | 'low' | 'medium' | 'high'
type ToolTab = 'resonance' | 'strategy' | 'tdx' | 'wencai' | 'stockpool'

type StockPoolRow = {
  symbol: string
  activity: number | null
  activity_level: string | null
  gs_state: string | null
  gs_signal: string | null
  l2_net: number | null
  resonance: string
  score: number
}

// ── 共振查询(2026-08-22): 一句输入 → 问小达+问财并发 → (可选)策略库精筛 → 共振排序 ──
type ResonanceRow = {
  symbol: string
  name: string
  fromTdx: boolean
  fromWencai: boolean
  tdxRank: number | null
  wencaiRank: number | null
  strategyPassed: boolean | null // null=未启用精筛
  strategyScore: number | null
}

const resonanceCountOf = (r: ResonanceRow): number =>
  [r.fromTdx, r.fromWencai, r.strategyPassed === true].filter(Boolean).length

/** 共振排序: 共振数 > 策略分 > 引擎内排名 */
const sortResRows = (rows: ResonanceRow[]): ResonanceRow[] =>
  [...rows].sort((a, b) => {
    const diff = resonanceCountOf(b) - resonanceCountOf(a)
    if (diff !== 0) return diff
    const sa = a.strategyScore ?? -1
    const sb = b.strategyScore ?? -1
    if (sa !== sb) return sb - sa
    const pa = a.tdxRank ?? a.wencaiRank ?? 999
    const pb = b.tdxRank ?? b.wencaiRank ?? 999
    return pa - pb
  })

/** tdx 行字段名动态(中文键兜底,与后端入池口径一致 tdx.py): 识别不了返回 '' */
const normSymbolTdx = (r: Record<string, unknown>): string => {
  const raw = r['代码'] ?? r['股票代码'] ?? r['sec_code'] ?? r['symbol'] ?? r['code']
  const s = String(raw ?? '')
    .replace(/^(USZA|USHA)/i, '')
    .replace(/\.(SH|SZ|BJ)$/i, '')
    .trim()
  return /^\d{6}$/.test(s) ? s : ''
}

/** 问财 symbol 是 thsdk 前缀码(USZA300033),剥前缀归一成 6 位 */
const normSymbolWencai = (r: { symbol?: string | number | null }): string => {
  const s = String(r.symbol ?? '')
    .replace(/^(USZA|USHA)/i, '')
    .replace(/\.(SH|SZ)$/i, '')
    .trim()
  return /^\d{6}$/.test(s) ? s : ''
}

// 统一筛选入口(2026-08-22): 7 项筛选条件的草稿形态(Popover 内编辑, 「应用」才落地)
type OpportunityFilters = {
  market: 'ALL' | 'CN' | 'HK' | 'US'
  source: SourceFilter
  holding: HoldingFilter
  strategy: string
  risk: RiskFilter
  minScore: string
  sector: string
}

// P1 多源整合(2026-08-21): 来源徽章中文映射
const CANDIDATE_SOURCE_CN: Record<string, string> = {
  watchlist: '自选建议',
  market_scan: '盘中扫描',
  mixed: '市场+自选',
  strategy: '策略信号',
  auction: '竞价异动',
  tdx: '问小达',
  wencai: '问财选股',
}
const sourceCn = (s?: string | null) => (s ? CANDIDATE_SOURCE_CN[s] || s : '')

const resonanceOf = (it: StrategySignalItem): number =>
  Number(it.meta?.resonance_count || 0)

const resonanceSourcesLabel = (it: StrategySignalItem): string => {
  const srcs = it.meta?.resonance_sources || []
  return srcs.map((s) => sourceCn(s)).join(' + ')
}

type GroupedSignal = {
  key: string
  primary: StrategySignalItem
  members: StrategySignalItem[]
  strategyNames: string[]
  sourceAgents: string[]
  hasMarketScan: boolean
  topScore: number
  resonanceCount: number
  allSources: string[]
}

const marketLabel = (m?: string) => {
  if (m === 'HK') return '港股'
  if (m === 'US') return '美股'
  return 'A股'
}

const sourceAgentLabelMap: Record<string, string> = {
  premarket_outlook: '盘前分析',
  intraday_monitor: '盘中监测',
  daily_report: '收盘复盘',
  news_digest: '新闻速递',
  market_scan: '市场扫描',
}

const sourceAgentLabel = (agent?: string) => {
  const key = (agent || '').trim()
  if (!key) return '--'
  return sourceAgentLabelMap[key] || key
}

const formatPlanPrice = (value: number | null | undefined) => {
  if (value == null || Number.isNaN(value)) return '--'
  const abs = Math.abs(value)
  const fixed = abs >= 100 ? 2 : abs >= 1 ? 3 : 4
  return Number(value).toFixed(fixed).replace(/\.?0+$/, '')
}

const toNumberOrNull = (value: unknown): number | null => {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const num = Number(value)
    if (Number.isFinite(num)) return num
  }
  return null
}

const sleep = (ms: number) => new Promise<void>((resolve) => {
  window.setTimeout(resolve, ms)
})

const formatMetric = (value: unknown, digits = 1) => {
  const n = toNumberOrNull(value)
  if (n == null) return '--'
  return n.toFixed(digits)
}

const DEFAULT_FILTERS = {
  market: 'ALL' as const,
  source: 'all' as const,
  holding: 'unheld' as const,
  strategy: 'all',
  risk: 'all' as const,
  minScore: '70',
}

const toneClass = (item: StrategySignalItem) => {
  const action = (item.action || '').toLowerCase()
  const score = Number(item.rank_score || item.score || 0)
  if (action === 'buy') {
    return 'border-l-2 border-l-rose-600/70'
  }
  if (action === 'add') {
    return 'border-l-2 border-l-emerald-600/70'
  }
  if (score >= 85) {
    return 'border-l-2 border-l-primary/70'
  }
  return ''
}

const actionBadgeClass = (action?: string) => {
  const key = (action || '').toLowerCase()
  if (key === 'buy') return 'bg-rose-500/15 text-rose-700 dark:text-rose-400 border border-rose-500/35'
  if (key === 'add') return 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-700 border border-emerald-500/35'
  if (key === 'hold') return 'bg-blue-500/15 text-blue-700 dark:text-blue-400 border border-blue-500/35'
  return 'bg-accent text-muted-foreground border border-border/50'
}

const displayActionLabel = (item: StrategySignalItem) => {
  const action = (item.action || '').toLowerCase()
  if (!item.is_holding_snapshot && action === 'hold') return '观望'
  if (!item.is_holding_snapshot && action === 'add') return '建仓'
  return item.action_label || item.action
}

const scoreOf = (item: StrategySignalItem) => Number(item.rank_score || item.score || 0)

const actionPriority = (item: StrategySignalItem) => {
  const key = (item.action || '').toLowerCase()
  if (key === 'buy') return 4
  if (key === 'add') return 3
  if (key === 'hold') return item.is_holding_snapshot ? 2 : 1
  return 0
}

const hasEntryPlan = (item: StrategySignalItem) => {
  const breakdown = item.score_breakdown || {}
  if (typeof breakdown.has_entry_plan === 'boolean') return breakdown.has_entry_plan
  return toNumberOrNull(item.entry_low) != null || toNumberOrNull(item.entry_high) != null
}

const itemTimestamp = (item: StrategySignalItem) => {
  const t = Date.parse(item.updated_at || item.created_at || '')
  return Number.isFinite(t) ? t : 0
}

const shouldReplacePrimary = (next: StrategySignalItem, current: StrategySignalItem) => {
  const activeDelta = Number((next.status || '').toLowerCase() === 'active') - Number((current.status || '').toLowerCase() === 'active')
  if (activeDelta !== 0) return activeDelta > 0
  const actionDelta = actionPriority(next) - actionPriority(current)
  if (actionDelta !== 0) return actionDelta > 0
  const entryDelta = Number(hasEntryPlan(next)) - Number(hasEntryPlan(current))
  if (entryDelta !== 0) return entryDelta > 0
  const scoreDelta = scoreOf(next) - scoreOf(current)
  if (Math.abs(scoreDelta) > 0.001) return scoreDelta > 0
  return itemTimestamp(next) > itemTimestamp(current)
}

const toSignalFromCandidate = (row: EntryCandidateItem): StrategySignalItem => {
  const source = row.candidate_source || 'watchlist'
  const sourceLabel = row.candidate_source_label || (source === 'market_scan' ? '市场池' : source === 'mixed' ? '市场+关注' : '关注池')
  const riskLevel: 'low' | 'medium' | 'high' = Number(row.score || 0) >= 85 ? 'high' : Number(row.score || 0) >= 70 ? 'medium' : 'low'
  const riskLabel = riskLevel === 'high' ? '高风险' : riskLevel === 'low' ? '低风险' : '中风险'
  return {
    id: Number(row.id || 0),
    snapshot_date: row.snapshot_date || '',
    stock_symbol: row.stock_symbol,
    stock_market: row.stock_market || 'CN',
    stock_name: row.stock_name || row.stock_symbol,
    strategy_code: (row.strategy_tags && row.strategy_tags[0]) || 'watchlist_agent',
    strategy_name: (row.strategy_labels && row.strategy_labels[0]) || '候选建议',
    strategy_version: 'v1',
    risk_level: riskLevel,
    risk_level_label: riskLabel,
    source_pool: source,
    source_pool_label: sourceLabel,
    score: Number(row.score || 0),
    rank_score: Number(row.score || 0),
    confidence: row.confidence ?? null,
    status: row.status || 'inactive',
    action: row.action || 'watch',
    action_label: row.action_label || '观望',
    signal: row.signal || '',
    reason: row.reason || '',
    evidence: row.evidence || [],
    holding_days: 3,
    entry_low: row.entry_low ?? null,
    entry_high: row.entry_high ?? null,
    stop_loss: row.stop_loss ?? null,
    target_price: row.target_price ?? null,
    invalidation: row.invalidation || '',
    plan_quality: row.plan_quality ?? 0,
    source_agent: row.source_agent || '',
    source_suggestion_id: row.source_suggestion_id ?? null,
    source_candidate_id: row.id ?? null,
    trace_id: '',
    is_holding_snapshot: !!row.is_holding_snapshot,
    context_quality_score: null,
    score_breakdown: {
      weighted_score: Number(row.score || 0),
      has_entry_plan: !!(row.entry_low != null || row.entry_high != null),
    },
    market_regime: {},
    cross_feature: {},
    news_metric: {},
    constrained: false,
    constraint_reasons: [],
    candidate_source: source,
    meta: (row.meta && typeof row.meta === 'object' ? row.meta : {}) as StrategySignalItem['meta'],
    payload: {
      source_meta: {
        plan: row.plan || {},
      },
    },
    created_at: row.created_at || '',
    updated_at: row.updated_at || row.created_at || '',
  }
}

const formatEntryDisplay = (action: string | undefined, entryLow: number | null, entryHigh: number | null) => {
  if (entryLow != null || entryHigh != null) {
    return `${formatPlanPrice(entryLow)} ~ ${formatPlanPrice(entryHigh)}`
  }
  const key = (action || '').toLowerCase()
  if (key === 'buy' || key === 'add') return '待补充入场位'
  return '当前不建议开仓'
}

const regimeToneClass = (regime?: string) => {
  if (regime === 'bullish') return 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-700 border border-emerald-500/30'
  if (regime === 'bearish') return 'bg-rose-500/15 text-rose-700 dark:text-rose-400 border border-rose-500/30'
  return 'bg-amber-500/12 text-amber-700 dark:text-amber-300 border border-amber-500/25'
}

export default function OpportunitiesPage() {
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  // 防重复提交锁:提交/轮询期间置 true,轮询结束或同步失败后复位
  const refreshingRef = useRef(false)
  const [error, setError] = useState('')
  const [items, setItems] = useState<StrategySignalItem[]>([])
  const [stats, setStats] = useState<StrategyStatsResponse | null>(null)
  const [strategyCatalog, setStrategyCatalog] = useState<StrategyCatalogItem[]>([])
  const [watchlist, setWatchlist] = useState<Set<string>>(new Set())

  const [market, setMarket] = useLocalStorage<'ALL' | 'CN' | 'HK' | 'US'>('panwatch_opportunities_market_v3', DEFAULT_FILTERS.market)
  const [source, setSource] = useLocalStorage<SourceFilter>('panwatch_opportunities_source_v3', DEFAULT_FILTERS.source)
  // P1 多源整合: 只看共振开关(默认关)
  const [resonanceOnly, setResonanceOnly] = useLocalStorage('panwatch_opportunities_resonance_v1', false)
  const [holding, setHolding] = useLocalStorage<HoldingFilter>('panwatch_opportunities_holding_v3', DEFAULT_FILTERS.holding)
  const [strategy, setStrategy] = useLocalStorage('panwatch_opportunities_strategy_v3', DEFAULT_FILTERS.strategy)
  const [risk, setRisk] = useLocalStorage<RiskFilter>('panwatch_opportunities_risk_v3', DEFAULT_FILTERS.risk)
  const [minScore, setMinScore] = useLocalStorage('panwatch_opportunities_min_score_v3', DEFAULT_FILTERS.minScore)
  const [sector, setSector] = useLocalStorage('panwatch_opportunities_sector_v3', '')
  const [sectorOpen, setSectorOpen] = useState(false)
  const [sectorQuery, setSectorQuery] = useState('')
  const [sectorResults, setSectorResults] = useState<{ code: string; name: string }[]>([])
  const [snapshotDate, setSnapshotDate] = useState('')

  // 机会页 Tab: 候选池 / 竞价异动(2026-08-20, v0.3.0)
  const [viewMode, setViewMode] = useLocalStorage<'candidates' | 'auction' | 'abnormal'>('panwatch_opportunities_viewmode_v2', 'candidates')

  // 统一筛选入口(2026-08-22): draft 模式 — Popover 内改草稿, 「应用」才写回持久化 state 并加载
  const [filterOpen, setFilterOpen] = useState(false)
  const [draft, setDraft] = useState<OpportunityFilters>({ market, source, holding, strategy, risk, minScore, sector })
  // 选股工具 tab: 共振查询(默认)/ 策略选股 / 问小达 / 问财
  const [toolTab, setToolTab] = useLocalStorage<ToolTab>('panwatch_opportunities_tool_tab_v1', 'resonance')

  const [insightOpen, setInsightOpen] = useState(false)
  const [insightSymbol, setInsightSymbol] = useState('')
  const [insightMarket, setInsightMarket] = useState('CN')
  const [insightName, setInsightName] = useState<string | undefined>(undefined)
  const [insightHasPosition, setInsightHasPosition] = useState(false)

  // 个股 AI 评分分享卡:当前分享的信号
  const [shareSignal, setShareSignal] = useState<StrategySignalItem | null>(null)

  // ── 候选反馈(有用/没用) ──
  // key: `${stock_market}:${stock_symbol}` → 最新一次反馈的 useful 值
  const { toast } = useToast()
  const [feedbackMap, setFeedbackMap] = useState<Record<string, boolean>>({})
  const [feedbackPending, setFeedbackPending] = useState<Set<string>>(new Set())

  // 选股池(决策先锋三指标共振扫描, 2026-08-30)
  const [poolSymbols, setPoolSymbols] = useState('002361, 600519, 300750, 002407')
  const [poolRows, setPoolRows] = useState<StockPoolRow[]>([])
  const [poolLoading, setPoolLoading] = useState(false)
  const [poolScanned, setPoolScanned] = useState(false)

  const runStockPool = useCallback(async () => {
    const symbols = poolSymbols
      .split(/[,，\s]+/)
      .map((s) => s.trim())
      .filter((s) => /^\d{6}$/.test(s))
    if (!symbols.length) {
      toast('请输入至少一个6位股票代码', 'error')
      return
    }
    setPoolLoading(true)
    try {
      const res = await fetchAPI<{ rows: StockPoolRow[]; truncated?: boolean }>('/stock-pool/screen', {
        method: 'POST',
        body: JSON.stringify({ symbols }),
      })
      setPoolRows(res.rows ?? [])
      setPoolScanned(true)
    } catch (e) {
      toast(`选股池扫描失败: ${e instanceof Error ? e.message : '未知错误'}`, 'error')
    } finally {
      setPoolLoading(false)
    }
  }, [poolSymbols, toast])

  const loadFeedback = useCallback(async (snapDate: string, rows: StrategySignalItem[]) => {
    if (!snapDate || rows.length === 0) return
    try {
      const res = await recommendationsApi.listEntryCandidateFeedback({ snapshot_date: snapDate, limit: 500 })
      const map: Record<string, boolean> = {}
      for (const fb of res.items || []) {
        const key = `${fb.stock_market || 'CN'}:${fb.stock_symbol}`
        map[key] = !!fb.useful
      }
      setFeedbackMap(map)
    } catch {
      // 反馈状态加载失败不阻塞页面
    }
  }, [])

  const handleCandidateFeedback = useCallback(async (item: StrategySignalItem, useful: boolean) => {
    const key = `${item.stock_market || 'CN'}:${item.stock_symbol}`
    if (feedbackMap[key] === useful) return // 已反馈相同值, 忽略重复提交
    if (feedbackPending.has(key)) return
    const nextPending = new Set(feedbackPending)
    nextPending.add(key)
    setFeedbackPending(nextPending)
    try {
      const res = await recommendationsApi.feedbackEntryCandidate({
        snapshot_date: item.snapshot_date || '',
        stock_symbol: item.stock_symbol,
        stock_market: item.stock_market || 'CN',
        useful,
        candidate_source: item.source_pool || 'watchlist',
        strategy_tags: item.strategy_code ? [item.strategy_code] : [],
      })
      if (res.ok) {
        setFeedbackMap((prev) => ({ ...prev, [key]: useful }))
        toast(useful ? '已标记为有用' : '已标记为没用', 'success')
      } else {
        toast('反馈提交失败，请稍后重试', 'error')
      }
    } catch (e) {
      toast(e instanceof Error ? e.message : '反馈提交失败，请稍后重试', 'error')
    } finally {
      setFeedbackPending((prev) => {
        const next = new Set(prev)
        next.delete(key)
        return next
      })
    }
  }, [feedbackMap, feedbackPending, toast])

  // ── 策略选股(策略库批量扫描) ──
  const [scanStrategies, setScanStrategies] = useState<StrategyItem[]>([])
  const [scanStrategyId, setScanStrategyId] = useState('')
  const [scanUniverse, setScanUniverse] = useState<'all' | 'watchlist'>('all')
  // 策略库弹窗(合并自原独立页面 /strategies)
  const [strategyLibOpen, setStrategyLibOpen] = useState(false)
  const [scanning, setScanning] = useState(false)
  const [scanResult, setScanResult] = useState<{ items: ScanItem[]; total: number; scanned: number; quoted: number } | null>(null)
  const [scanError, setScanError] = useState('')

  const loadScanStrategies = useCallback(async () => {
    try {
      const res = await strategiesApi.list()
      setScanStrategies(res.items || [])
    } catch {
      setScanStrategies([])
    }
  }, [])

  useEffect(() => { void loadScanStrategies() }, [loadScanStrategies])

  const doScan = useCallback(async () => {
    if (!scanStrategyId) return
    setScanning(true)
    setScanError('')
    setScanResult(null)
    try {
      const res = await strategiesApi.scan({
        strategy_id: scanStrategyId,
        market: market === 'ALL' ? 'CN' : market,
        limit: 50,
        universe: scanUniverse,
        min_score: 0,
      })
      setScanResult(res)
    } catch (e) {
      setScanError(e instanceof Error ? e.message : '策略扫描失败')
    } finally {
      setScanning(false)
    }
  }, [scanStrategyId, market, scanUniverse])

  const openInsight = useCallback((item: StrategySignalItem) => {
    setInsightSymbol(item.stock_symbol)
    setInsightMarket(item.stock_market || 'CN')
    setInsightName(item.stock_name)
    setInsightHasPosition(!!item.is_holding_snapshot)
    setInsightOpen(true)
  }, [])

  // 竞价异动 Tab 行点击 → 打开相同的个股洞察弹窗(2026-08-20)
  const openAuctionDetail = useCallback((symbol: string, market: string, name?: string) => {
    setInsightSymbol(symbol)
    setInsightMarket(market || 'CN')
    setInsightName(name)
    setInsightHasPosition(false)
    setInsightOpen(true)
  }, [])

  const loadWatchlist = useCallback(async () => {
    try {
      const rows = await stocksApi.list()
      const set = new Set<string>((rows || []).map((s) => `${s.market}:${s.symbol}`))
      setWatchlist(set)
    } catch {
      setWatchlist(new Set())
    }
  }, [])

  // 通达信问小达投研精选(用户主动按板块查询,避免每次进页面自动消耗 tdx ask 配额)
  const [tdxQuery, setTdxQuery] = useState('')
  const [tdxActiveQuery, setTdxActiveQuery] = useState<string | null>(null)
  const [tdxData, setTdxData] = useState<TdxAskResponse | null>(null)
  const [tdxLoading, setTdxLoading] = useState(false)

  const loadTdx = useCallback(async (query: string) => {
    const trimmed = query.trim()
    if (!trimmed) return
    setTdxLoading(true)
    setTdxActiveQuery(trimmed)
    try {
      const res = await tdxApi.ask(trimmed, 10)
      setTdxData(res)
    } catch {
      setTdxData(null)
    } finally {
      setTdxLoading(false)
    }
  }, [])

  const loadStats = useCallback(async () => {
    try {
      const s = await recommendationsApi.getStrategyStats(45)
      setStats(s)
    } catch {
      setStats(null)
    }
  }, [])

  const loadCatalog = useCallback(async () => {
    try {
      const res = await recommendationsApi.listStrategyCatalog(true)
      setStrategyCatalog(res.items || [])
    } catch {
      setStrategyCatalog([])
    }
  }, [])

  // 题材搜索(防抖 250ms)— 486 个概念板块,输入即搜
  const searchSector = useCallback(async (q: string) => {
    try {
      const res = await recommendationsApi.searchOpportunitySectors(q, 20)
      setSectorResults(res.items || [])
    } catch {
      setSectorResults([])
    }
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void searchSector(sectorQuery)
    }, 250)
    return () => window.clearTimeout(timer)
  }, [sectorQuery, searchSector])

  // 统一筛选入口(2026-08-22): override 供「应用筛选」用 — setState 是异步的, 同一拍内
  // 触发的 load() 若只读 state 会拿到旧值, 所以允许调用方直接传入新筛选值
  const load = useCallback(async (override?: Partial<OpportunityFilters>) => {
    const f: OpportunityFilters = { market, source, holding, strategy, risk, minScore, sector, ...override }
    setLoading(true)
    setError('')
    try {
      const req = {
        status: 'active' as const,
        source_pool: f.source,
        holding: f.holding,
        market: f.market === 'ALL' ? '' : f.market,
        strategy_code: f.strategy === 'all' ? '' : f.strategy,
        risk_level: f.risk,
        min_score: Number(f.minScore) || 0,
        sector: f.sector || '',
        limit: 120,
        include_payload: false,
      }
      let data: Awaited<ReturnType<typeof recommendationsApi.listStrategySignals>>
      try {
        data = await recommendationsApi.listStrategySignals({
          ...req,
          timeoutMs: 45000,
        })
      } catch (firstErr) {
        const msg = firstErr instanceof Error ? firstErr.message : ''
        if (!msg.includes('超时')) throw firstErr
        try {
          // Retry once for transient DB lock/contention.
          data = await recommendationsApi.listStrategySignals({
            ...req,
            timeoutMs: 90000,
          })
        } catch (secondErr) {
          const secondMsg = secondErr instanceof Error ? secondErr.message : ''
          if (!secondMsg.includes('超时')) throw secondErr
          const fallback = await recommendationsApi.listEntryCandidates({
            market: req.market,
            status: 'active',
            min_score: req.min_score,
            limit: req.limit,
            snapshot_date: '',
            source: f.source === 'all' ? 'all' : f.source,
            holding: req.holding,
            timeoutMs: 90000,
          })
          data = {
            snapshot_date: fallback.snapshot_date || '',
            count: fallback.count || 0,
            items: (fallback.items || []).map(toSignalFromCandidate),
          }
          setError('策略层请求超时，已降级展示候选快照')
        }
      }
      if ((!data.items || data.items.length === 0) && f.market !== 'ALL') {
        const fallback = await recommendationsApi.listStrategySignals({
          ...req,
          market: '',
          timeoutMs: 45000,
        })
        if (fallback.items && fallback.items.length > 0) {
          setError(`当前${marketLabel(f.market)}暂无满足条件机会，已展示全市场结果`)
          data = fallback
        }
      }
      setItems(data.items || [])
      setSnapshotDate(data.snapshot_date || '')
      void loadFeedback(data.snapshot_date || '', data.items || [])
      if (!data.snapshot_date) {
        setError('暂无机会快照，请点击“刷新”生成一次')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
      setItems([])
    } finally {
      setLoading(false)
    }
  }, [holding, loadFeedback, market, minScore, risk, sector, source, strategy])

  useEffect(() => {
    load()
    loadStats()
    loadCatalog()
    loadWatchlist()
  }, [load, loadCatalog, loadStats, loadWatchlist])

  const pollRefreshCompletion = useCallback(async () => {
    // 每 10s 轮询一次任务状态,最多 12 次(约 2 分钟)
    const maxPolls = 12
    try {
      for (let i = 0; i < maxPolls; i += 1) {
        try {
          const state = await recommendationsApi.getStrategyRefreshStatus()
          if (!state.running) {
            if (state.last_error) {
              setError(`后台刷新失败: ${state.last_error}`)
              toast(`后台刷新失败: ${state.last_error}`, 'error')
            } else {
              setError('')
              toast('刷新完成,机会列表已更新', 'success')
            }
            await Promise.all([load(), loadStats()])
            return
          }
        } catch {
          // 轮询瞬时错误忽略,继续下一轮
        }
        await sleep(10000)
      }
      // 2 分钟仍未完成:提示任务仍在后台,恢复按钮让用户稍后手动刷新
      await Promise.all([load(), loadStats()])
      setError((prev) => prev || '刷新任务仍在后台执行,全市场扫描约需 1-3 分钟,请稍后手动刷新查看')
      toast('刷新任务仍在后台执行,预计 1-3 分钟完成', 'info')
    } finally {
      // 轮询结束:无论成功/失败/超时都恢复按钮,允许再次提交
      refreshingRef.current = false
      setRefreshing(false)
    }
  }, [load, loadStats, toast])

  // ── 共振查询(2026-08-22) ──
  const [resQuery, setResQuery] = useState('')
  const [resStrategyId, setResStrategyId] = useState('') // '' = 不精筛
  const [resLoading, setResLoading] = useState(false)
  // 并发查询的原始合并结果(不含策略列): 精筛只对这份缓存跑 scan, 切换策略不重调 tdx/wencai
  const [resBaseRows, setResBaseRows] = useState<ResonanceRow[]>([])
  const [resRows, setResRows] = useState<ResonanceRow[]>([])
  const [resFiltering, setResFiltering] = useState(false)
  const [resFilterDown, setResFilterDown] = useState(false)
  const [resLastQuery, setResLastQuery] = useState('')
  const [resMeta, setResMeta] = useState<{ tdx: number; wencai: number; strategy: number; dropped: number; enginesDown: string } | null>(null)
  const [resError, setResError] = useState('')
  const [resOnly, setResOnly] = useLocalStorage('panwatch_opportunities_res_only_v1', false)

  // 策略精筛: 只对缓存的双引擎合并结果调 scan(symbols ≤100), 与并发查询解耦
  const applyResFilter = useCallback(async (strategyId: string, base: ResonanceRow[]) => {
    if (base.length === 0) return
    if (!strategyId) {
      // 不精筛: 纯本地清除策略列, 零 API 调用
      setResRows(sortResRows(base.map((r) => ({ ...r, strategyPassed: null, strategyScore: null }))))
      setResMeta((m) => (m ? { ...m, strategy: 0 } : m))
      setResFilterDown(false)
      return
    }
    setResFiltering(true)
    try {
      const symbols = base.map((r) => r.symbol).slice(0, 100)
      const scan = await strategiesApi.scan({ strategy_id: strategyId, market: 'CN', limit: 100, symbols, min_score: 0 })
      const passed = new Map(scan.items.map((it) => [it.symbol, it.score]))
      const rows = base.map((r) => (
        passed.has(r.symbol)
          ? { ...r, strategyPassed: true, strategyScore: passed.get(r.symbol) ?? null }
          : { ...r, strategyPassed: false, strategyScore: null }
      ))
      setResRows(sortResRows(rows))
      setResMeta((m) => (m ? { ...m, strategy: passed.size } : m))
      setResFilterDown(false)
    } catch {
      // 精筛失败: 退回未精筛结果并标注, 不影响已有数据
      setResRows(sortResRows(base))
      setResMeta((m) => (m ? { ...m, strategy: 0 } : m))
      setResFilterDown(true)
    } finally {
      setResFiltering(false)
    }
  }, [])

  const runResonance = useCallback(async () => {
    const q = resQuery.trim()
    if (!q) {
      setResError('请输入题材或选股条件')
      return
    }
    setResLoading(true)
    setResError('')
    setResRows([])
    setResBaseRows([])
    setResMeta(null)
    setResFilterDown(false)
    setResLastQuery(q)
    try {
      // ① 问小达 + 问财并发(各自失败降级, 不互相拖垮)
      const [tdxRes, wcRes] = await Promise.allSettled([tdxApi.ask(q, 10), wencaiApi.query(q)])
      const map = new Map<string, ResonanceRow>()
      const enginesDown: string[] = []
      let tdxCount = 0
      let wencaiCount = 0
      let dropped = 0

      if (tdxRes.status === 'fulfilled') {
        (tdxRes.value.rows || []).forEach((raw, i) => {
          const r = raw as Record<string, unknown>
          const sym = normSymbolTdx(r)
          if (!sym) {
            dropped += 1
            return
          }
          tdxCount += 1
          const name = String(r['名称'] ?? r['股票简称'] ?? r['sec_name'] ?? r['name'] ?? sym)
          const prev = map.get(sym)
          if (prev) {
            prev.fromTdx = true
            prev.tdxRank = i + 1
          } else {
            map.set(sym, { symbol: sym, name, fromTdx: true, fromWencai: false, tdxRank: i + 1, wencaiRank: null, strategyPassed: null, strategyScore: null })
          }
        })
      } else {
        enginesDown.push('问小达')
      }

      if (wcRes.status === 'fulfilled' && wcRes.value.available) {
        (wcRes.value.rows || []).forEach((r: WencaiRow, i: number) => {
          const sym = normSymbolWencai(r)
          if (!sym) {
            dropped += 1
            return
          }
          wencaiCount += 1
          const prev = map.get(sym)
          if (prev) {
            prev.fromWencai = true
            prev.wencaiRank = i + 1
          } else {
            map.set(sym, { symbol: sym, name: r.name || sym, fromTdx: false, fromWencai: true, tdxRank: null, wencaiRank: i + 1, strategyPassed: null, strategyScore: null })
          }
        })
      } else {
        enginesDown.push('问财')
      }

      if (map.size === 0) {
        setResMeta({ tdx: tdxCount, wencai: wencaiCount, strategy: 0, dropped, enginesDown: enginesDown.join('、') })
        setResError(enginesDown.length >= 2 ? '问小达与问财均不可用或无结果,无法共振' : '本次查询无有效结果')
        return
      }

      // ② 缓存合并结果; 若已选策略, 首次即精筛(只调 scan, 不重查引擎)
      const base = Array.from(map.values())
      setResBaseRows(base)
      setResMeta({ tdx: tdxCount, wencai: wencaiCount, strategy: 0, dropped, enginesDown: enginesDown.join('、') })
      if (resStrategyId) {
        await applyResFilter(resStrategyId, base)
      } else {
        setResRows(sortResRows(base))
      }

      // ③ 联动: tdx/wencai 查询已在后端入池, 触发轻量重算(跳过东财)让机会页 🔥 立即更新
      if (tdxCount + wencaiCount > 0 && !refreshingRef.current) {
        refreshingRef.current = true
        setRefreshing(true)
        try {
          await recommendationsApi.refreshStrategySignals({ rebuild_candidates: true, max_kline_symbols: 0, skip_market_scan: true, wait: false })
          toast('已并入候选池,机会页共振重算中…', 'success')
          void pollRefreshCompletion()
        } catch {
          refreshingRef.current = false
          setRefreshing(false)
        }
      }
    } catch (e) {
      setResError(e instanceof Error ? e.message : '共振查询失败')
    } finally {
      setResLoading(false)
    }
  }, [applyResFilter, pollRefreshCompletion, resQuery, resStrategyId, toast])

  const handleRefresh = async () => {
    if (refreshingRef.current) return
    refreshingRef.current = true
    setRefreshing(true)
    setError('')
    let backgroundQueued = false
    try {
      const resp = await recommendationsApi.refreshStrategySignals({
        rebuild_candidates: true,
        max_inputs: 500,
        market_scan_limit: 80,
        max_kline_symbols: 60,
        limit_candidates: 2000,
        wait: false,
      })
      if (resp.queued) {
        // 后台任务已接受:保持"刷新中"状态,轮询完成后自动重载列表
        backgroundQueued = true
        setError('')
        void pollRefreshCompletion()
        return
      }
      await Promise.all([load(), loadStats()])
    } catch (e) {
      const msg = e instanceof Error ? e.message : '刷新失败'
      if (msg.includes('超时')) {
        setError('刷新任务耗时较长,已在后台继续执行,请稍后再点刷新')
        toast('刷新任务已在后台继续执行', 'info')
        await load()
      } else {
        setError(msg)
        toast(`刷新失败: ${msg}`, 'error')
      }
    } finally {
      // 同步路径(未走后台轮询)才在此恢复按钮;后台轮询由 pollRefreshCompletion 统一恢复
      if (!backgroundQueued) {
        refreshingRef.current = false
        setRefreshing(false)
      }
    }
  }

  // 导出机会候选 CSV(/api/export/opportunities, 带 token 直接 fetch blob)
  const exportOpportunities = async () => {
    try {
      const token = getToken()
      const res = await fetch('/api/export/opportunities', {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `机会候选_${new Date().toISOString().slice(0, 10)}.csv`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
      toast('机会候选已导出', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '导出失败', 'error')
    }
  }

  // ── 统一筛选入口(2026-08-22) ──
  // 徽章数 = 已落地(持久化 state)的非默认筛选项数, 与列表当前展示一致
  const activeFilterCount = useMemo(() => {
    let n = 0
    if (market !== DEFAULT_FILTERS.market) n += 1
    if (source !== DEFAULT_FILTERS.source) n += 1
    if (holding !== DEFAULT_FILTERS.holding) n += 1
    if (strategy !== DEFAULT_FILTERS.strategy) n += 1
    if (risk !== DEFAULT_FILTERS.risk) n += 1
    if (minScore !== DEFAULT_FILTERS.minScore) n += 1
    if (sector) n += 1
    return n
  }, [holding, market, minScore, risk, sector, source, strategy])

  const applyDraft = () => {
    setMarket(draft.market)
    setSource(draft.source)
    setHolding(draft.holding)
    setStrategy(draft.strategy)
    setRisk(draft.risk)
    setMinScore(draft.minScore)
    setSector(draft.sector)
    setSectorQuery(draft.sector)
    setFilterOpen(false)
    setSectorOpen(false)
    void load(draft)
  }

  const strategyOptions = useMemo(() => {
    return strategyCatalog.map((row) => ({ value: row.code, label: row.name || row.code }))
  }, [strategyCatalog])

  const groupedItems = useMemo<GroupedSignal[]>(() => {
    const grouped = new Map<string, { primary: StrategySignalItem; members: StrategySignalItem[] }>()
    for (const row of items) {
      const key = `${row.stock_market || 'CN'}:${row.stock_symbol}`
      const prev = grouped.get(key)
      if (!prev) {
        grouped.set(key, { primary: row, members: [row] })
        continue
      }
      prev.members.push(row)
      if (shouldReplacePrimary(row, prev.primary)) {
        prev.primary = row
      }
    }

    const out: GroupedSignal[] = []
    for (const [key, val] of grouped.entries()) {
      const strategyNames = Array.from(new Set(val.members.map((x) => x.strategy_name || x.strategy_code).filter(Boolean)))
      const sourceAgents = Array.from(new Set(val.members.map((x) => sourceAgentLabel(x.source_agent)).filter((x) => x && x !== '--')))
      const hasMarketScan = val.members.some((x) => x.source_pool === 'market_scan' || x.source_pool === 'mixed')
      const topScore = Math.max(...val.members.map(scoreOf))
      // P1 多源整合: 聚合组内最大共振数 + 全部来源(candidate_source + meta.source_hits)
      const resonanceCount = Math.max(0, ...val.members.map(resonanceOf))
      const sourceSet = new Set<string>()
      for (const m of val.members) {
        if (m.candidate_source) sourceSet.add(m.candidate_source)
        for (const h of m.meta?.source_hits || []) sourceSet.add(h)
        const rs = m.meta?.resonance_sources || []
        for (const s of rs) if (s) sourceSet.add(s)
      }
      out.push({
        key,
        primary: val.primary,
        members: val.members,
        strategyNames,
        sourceAgents,
        hasMarketScan,
        topScore,
        resonanceCount,
        allSources: Array.from(sourceSet),
      })
    }
    out.sort((a, b) => {
      // P1: 共振票优先(多源命中=更高置信), 其次市场池, 再按分数
      const resDelta = Number(b.resonanceCount >= 2) - Number(a.resonanceCount >= 2)
      if (resDelta !== 0) return resDelta
      const sourceDelta = Number(b.hasMarketScan) - Number(a.hasMarketScan)
      if (sourceDelta !== 0) return sourceDelta
      const scoreDelta = b.topScore - a.topScore
      if (Math.abs(scoreDelta) > 0.001) return scoreDelta
      return actionPriority(b.primary) - actionPriority(a.primary)
    })
    return out
  }, [items])

  const filteredSummary = useMemo(() => {
    const total = groupedItems.length
    const unheld = groupedItems.filter((x) => !x.primary.is_holding_snapshot).length
    const marketPool = groupedItems.filter((x) => x.hasMarketScan).length
    return { total, unheld, marketPool }
  }, [groupedItems])

  // P1 多源整合: 只看共振过滤(resonance_count>=2)
  const visibleItems = useMemo(() => {
    if (!resonanceOnly) return groupedItems
    return groupedItems.filter((g) => g.resonanceCount >= 2)
  }, [groupedItems, resonanceOnly])

  const globalCoverage = stats?.coverage || null
  const factorStats = stats?.factor_stats || null
  const constraintStats = stats?.constraints || null

  const outcome3d = useMemo(() => {
    const rows = (stats?.by_strategy || []).filter((x) => Number(x.horizon_days) === 3)
    if (!rows.length) return null
    let sample = 0
    let wins = 0
    for (const r of rows) {
      sample += Number(r.sample_size || 0)
      wins += Number(r.wins || 0)
    }
    if (!sample) return null
    return {
      total: sample,
      win_rate: (wins / sample) * 100,
    }
  }, [stats])

  const regimeSummary = useMemo(() => {
    return (stats?.regimes || []).map((r) => ({
      market: r.market,
      label: r.regime_label || r.regime || '震荡',
      regime: r.regime || 'neutral',
      confidence: Number(r.confidence || 0),
      score: Number(r.regime_score || 0),
    }))
  }, [stats])

  const riskSummary = useMemo(() => {
    return (stats?.portfolio_risk || []).map((r) => ({
      market: r.market,
      riskLevel: r.risk_level || 'medium',
      concentration: Number(r.concentration_top5 || 0),
      highRiskRatio: Number(r.high_risk_ratio || 0),
    }))
  }, [stats])

  return (
    <div className="page-container pb-10">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between mb-4">
        <div>
          <h1 className="text-[20px] md:text-[22px] font-bold text-foreground tracking-tight flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-primary" />
            机会页
          </h1>
          <p className="text-[12px] text-muted-foreground mt-1">
            市场池优先，候选必须具备可执行入场计划
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-muted-foreground">{snapshotDate || '最新快照'}</span>
          <Button
            variant="secondary"
            size="sm"
            className="h-8 text-[12px]"
            onClick={() => setStrategyLibOpen(true)}
          >
            <BookOpen className="w-3.5 h-3.5 mr-1" />
            策略库
          </Button>
          <Button
            variant="secondary"
            size="sm"
            className="h-8 text-[12px]"
            onClick={exportOpportunities}
          >
            <Download className="w-3.5 h-3.5 mr-1" />
            导出
          </Button>
          <Button
            variant="secondary"
            size="sm"
            className="h-8 text-[12px]"
            onClick={handleRefresh}
            disabled={refreshing}
          >
            {refreshing ? (
              <>
                <span className="w-3.5 h-3.5 border-2 border-current/30 border-t-current rounded-full animate-spin mr-1" />
                刷新中…
              </>
            ) : (
              <>
                <RefreshCw className="w-3.5 h-3.5 mr-1" />
                刷新
              </>
            )}
          </Button>
        </div>
      </div>

      {/* 后台刷新任务进行中提示条:让用户知道任务在跑、预期多久 */}
      {refreshing && (
        <div className="card p-3 mb-4 flex items-center gap-2 text-[12px] text-primary">
          <span className="w-3.5 h-3.5 border-2 border-primary/30 border-t-primary rounded-full animate-spin shrink-0" />
          <div className="flex-1">
            <span className="font-medium">后台刷新中…</span>
            <span className="text-muted-foreground ml-1">全市场扫描约需 1-3 分钟,完成后将自动更新列表</span>
          </div>
          <span className="text-[11px] text-muted-foreground">已提交,请稍候</span>
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <div className="card relative overflow-hidden p-3 border-l-2 border-l-primary">
          <div className="text-[11px] font-semibold text-foreground/80">当前候选(全局)</div>
          <div className="text-[24px] font-bold mt-1 font-num tabular-nums">{globalCoverage?.total_signals ?? '--'}</div>
          <div className="text-[10px] text-muted-foreground mt-1">
            可执行: {globalCoverage?.active_signals ?? '--'}，观察: {(globalCoverage?.total_signals != null && globalCoverage?.active_signals != null) ? Math.max(0, globalCoverage.total_signals - globalCoverage.active_signals) : '--'}
          </div>
        </div>
        <div className="card p-3">
          <div className="text-[11px] text-muted-foreground">市场池占比</div>
          <div className="text-[18px] font-bold mt-1">{globalCoverage?.market_scan_share_pct != null ? `${globalCoverage.market_scan_share_pct.toFixed(1)}%` : '--'}</div>
          <div className="text-[10px] text-muted-foreground mt-1">
            市场池: {globalCoverage?.market_scan_signals ?? '--'}，关注池: {globalCoverage?.watchlist_signals ?? '--'}，融合: {globalCoverage?.mixed_signals ?? '--'}
          </div>
        </div>
        <div className="card p-3">
          <div className="text-[11px] text-muted-foreground">本次筛选结果</div>
          <div className="text-[18px] font-bold mt-1">{filteredSummary.total}</div>
          <div className="text-[10px] text-muted-foreground mt-1">
            未持仓: {filteredSummary.unheld}，市场池: {filteredSummary.marketPool}
          </div>
        </div>
        <div className="card p-3">
          <div className="text-[11px] text-muted-foreground">3日胜率(自动评估)</div>
          <div className="text-[18px] font-bold mt-1">{outcome3d ? `${outcome3d.win_rate.toFixed(1)}%` : '--'}</div>
          <div className="text-[10px] text-muted-foreground mt-1">
            自动样本: {outcome3d ? `${outcome3d.total}` : '--'}
          </div>
        </div>
      </div>

      {/* 机会页 Tab(2026-08-20): 候选池 / 竞价异动 */}
      <div className="flex items-center gap-1 mb-4">
        <button
          type="button"
          onClick={() => setViewMode('candidates')}
          className={`rounded-lg px-3 py-1.5 text-[12px] font-medium transition-colors ${
            viewMode === 'candidates' ? 'bg-primary text-primary-foreground' : 'bg-accent/50 text-muted-foreground hover:bg-accent'
          }`}
        >
          候选池
        </button>
        <button
          type="button"
          onClick={() => setViewMode('auction')}
          className={`rounded-lg px-3 py-1.5 text-[12px] font-medium transition-colors ${
            viewMode === 'auction' ? 'bg-primary text-primary-foreground' : 'bg-accent/50 text-muted-foreground hover:bg-accent'
          }`}
        >
          竞价异动
        </button>
        <button
          type="button"
          onClick={() => setViewMode('abnormal')}
          className={`rounded-lg px-3 py-1.5 text-[12px] font-medium transition-colors ${
            viewMode === 'abnormal' ? 'bg-primary text-primary-foreground' : 'bg-accent/50 text-muted-foreground hover:bg-accent'
          }`}
        >
          异动预警
        </button>
      </div>

      {viewMode === 'abnormal' ? (
        <AbnormalMovesCard />
      ) : viewMode === 'auction' ? (
        <AuctionAnomalyTab market="CN" onOpenDetail={openAuctionDetail} />
      ) : (
      <>
      {/* 统一筛选入口(2026-08-22): 7 项筛选收进 Popover 草稿; 只看共振保留外露快捷开关 */}
      <div className="flex items-center gap-2 mb-4">
        <Popover
          open={filterOpen}
          onOpenChange={(open) => {
            setFilterOpen(open)
            if (open) {
              setDraft({ market, source, holding, strategy, risk, minScore, sector })
              setSectorOpen(false)
            }
          }}
        >
          <PopoverTrigger asChild>
            <Button variant="outline" size="sm" className="h-8 text-[12px] gap-1.5">
              <Filter className="w-3.5 h-3.5" />
              筛选
              {activeFilterCount > 0 && (
                <span className="inline-flex items-center justify-center min-w-[16px] h-4 px-1 rounded-full bg-primary text-primary-foreground text-[10px] font-semibold leading-none">
                  {activeFilterCount}
                </span>
              )}
            </Button>
          </PopoverTrigger>
          <PopoverContent align="start" className="w-[340px] p-0">
            <div className="px-3 pt-3 pb-1 text-[12px] font-semibold text-foreground">筛选条件</div>
            <div className="px-3 pb-2 space-y-2">
              <div className="text-[10px] font-medium text-muted-foreground pt-1">基础</div>
              <div className="grid grid-cols-2 gap-2">
                <Select value={draft.market} onValueChange={(v) => setDraft((d) => ({ ...d, market: v as OpportunityFilters['market'] }))}>
                  <SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="ALL">全部市场</SelectItem>
                    <SelectItem value="CN">A股</SelectItem>
                    <SelectItem value="HK">港股</SelectItem>
                    <SelectItem value="US">美股</SelectItem>
                  </SelectContent>
                </Select>
                <Select value={draft.holding} onValueChange={(v) => setDraft((d) => ({ ...d, holding: v as HoldingFilter }))}>
                  <SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部持仓状态</SelectItem>
                    <SelectItem value="unheld">仅未持仓</SelectItem>
                    <SelectItem value="held">仅持仓中</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <Select value={draft.source} onValueChange={(v) => setDraft((d) => ({ ...d, source: v as SourceFilter }))}>
                <SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部来源</SelectItem>
                  <SelectItem value="market_scan">市场池</SelectItem>
                  <SelectItem value="mixed">融合池</SelectItem>
                  <SelectItem value="watchlist">关注池</SelectItem>
                  {/* P1 多源入池(2026-08-21) */}
                  <SelectItem value="strategy">策略信号</SelectItem>
                  <SelectItem value="auction">竞价异动</SelectItem>
                  <SelectItem value="tdx">问小达</SelectItem>
                  <SelectItem value="wencai">问财选股</SelectItem>
                </SelectContent>
              </Select>
              <div className="text-[10px] font-medium text-muted-foreground pt-1">信号质量</div>
              <div className="grid grid-cols-2 gap-2">
                <Select value={draft.minScore} onValueChange={(v) => setDraft((d) => ({ ...d, minScore: v }))}>
                  <SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="90">评分90+</SelectItem>
                    <SelectItem value="80">评分80+</SelectItem>
                    <SelectItem value="70">评分70+</SelectItem>
                    <SelectItem value="60">评分60+</SelectItem>
                    <SelectItem value="50">评分50+</SelectItem>
                    <SelectItem value="0">评分不过滤</SelectItem>
                  </SelectContent>
                </Select>
                <Select value={draft.risk} onValueChange={(v) => setDraft((d) => ({ ...d, risk: v as RiskFilter }))}>
                  <SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部风险等级</SelectItem>
                    <SelectItem value="low">低风险</SelectItem>
                    <SelectItem value="medium">中风险</SelectItem>
                    <SelectItem value="high">高风险</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {/* 信号策略 = 池内信号来源标签(策略目录), 与选股工具的"筛选策略"(策略库可执行规则)是两套口径 */}
              <div className="text-[10px] font-medium text-muted-foreground pt-1">信号策略</div>
              <Select value={draft.strategy} onValueChange={(v) => setDraft((d) => ({ ...d, strategy: v }))}>
                <SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部信号策略</SelectItem>
                  {strategyOptions.map((op) => (
                    <SelectItem key={op.value} value={op.value}>{op.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <div className="text-[10px] font-medium text-muted-foreground pt-1">题材</div>
              <div className="relative">
                <Input
                  value={draft.sector}
                  placeholder="题材:输入搜索(如 商业航天/低空经济)"
                  className="h-8 text-[12px]"
                  onFocus={() => { setSectorOpen(true); if (sectorResults.length === 0) void searchSector('') }}
                  onBlur={() => window.setTimeout(() => setSectorOpen(false), 200)}
                  onChange={(e) => { setDraft((d) => ({ ...d, sector: e.target.value })); setSectorQuery(e.target.value) }}
                  onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); setSectorOpen(false); applyDraft() } }}
                />
                {sectorOpen && (
                  <div className="absolute z-50 mt-1 w-full max-h-52 overflow-y-auto rounded-md border border-border/60 bg-popover p-1 shadow-lg">
                    {sectorResults.length === 0 && (
                      <div className="px-2 py-1.5 text-[11px] text-muted-foreground">无匹配题材</div>
                    )}
                    {sectorResults.map((b) => (
                      <button
                        key={b.code}
                        type="button"
                        className="w-full text-left px-2 py-1.5 rounded text-[12px] hover:bg-accent"
                        onMouseDown={(e) => { e.preventDefault(); setDraft((d) => ({ ...d, sector: b.name })); setSectorQuery(b.name); setSectorOpen(false) }}
                      >
                        {b.name}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
            <div className="flex items-center justify-between border-t border-border/60 px-3 py-2.5">
              <Button
                variant="ghost"
                size="sm"
                className="h-8 text-[12px] text-muted-foreground"
                onClick={() => { setDraft({ ...DEFAULT_FILTERS, sector: '' }); setSectorQuery('') }}
              >
                清空
              </Button>
              <Button size="sm" className="h-8 text-[12px]" onClick={applyDraft} disabled={loading}>
                {loading ? '加载中...' : '应用筛选'}
              </Button>
            </div>
          </PopoverContent>
        </Popover>
        {/* P1: 只看多源共振开关(高频快捷项, 保留外露) */}
        <button
          type="button"
          onClick={() => setResonanceOnly(!resonanceOnly)}
          className={`h-8 rounded-lg px-3 text-[12px] font-medium transition-colors border ${
            resonanceOnly
              ? 'bg-orange-500/15 text-orange-600 dark:text-orange-400 border-orange-500/40'
              : 'bg-accent/50 text-muted-foreground hover:bg-accent border-transparent'
          }`}
          title="只显示被 2 个及以上独立来源同时命中的候选(共振=更高置信)"
        >
          🔥 只看共振
        </button>
      </div>
      {/* ── 选股工具: 共振查询 / 策略选股 / 问小达 / 问财, 主动查询, 结果并入下方候选池 ── */}
      <div className="card p-4 mb-4">
        <Tabs value={toolTab} onValueChange={(v) => setToolTab(v as ToolTab)}>
          <TabsList>
            <TabsTrigger value="resonance">共振查询</TabsTrigger>
            <TabsTrigger value="strategy">策略选股</TabsTrigger>
            <TabsTrigger value="tdx">问小达</TabsTrigger>
            <TabsTrigger value="wencai">问财</TabsTrigger>
            <TabsTrigger value="stockpool">选股池</TabsTrigger>
          </TabsList>
          {/* 共振查询(2026-08-22): 一句输入 → 问小达+问财并发 → 策略库精筛 → 共振排序 */}
          <TabsContent value="resonance">
            <div className="flex items-center gap-2 flex-wrap">
              <Input
                value={resQuery}
                onChange={(e) => setResQuery(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') void runResonance() }}
                placeholder="输入题材或选股条件,如: 商业航天 / 均线多头排列,MACD金叉,非ST"
                className="flex-1 min-w-[260px]"
                disabled={resLoading}
              />
              <Select
                value={resStrategyId || 'none'}
                onValueChange={(v) => {
                  const id = v === 'none' ? '' : v
                  setResStrategyId(id)
                  // 切换策略只对已缓存的双引擎结果跑 scan, 不重复调用问小达/问财
                  if (resBaseRows.length > 0 && !resLoading) void applyResFilter(id, resBaseRows)
                }}
                disabled={resLoading}
              >
                <SelectTrigger className="h-8 text-[12px] w-[180px]">
                  <SelectValue placeholder="策略精筛(可选)" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">不精筛</SelectItem>
                  {scanStrategies.map((s) => (
                    <SelectItem key={s.id} value={s.id}>{s.display_name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button size="sm" onClick={() => void runResonance()} disabled={resLoading || !resQuery.trim()}>
                {resLoading
                  ? <span className="w-3.5 h-3.5 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                  : '▶'}
                {resLoading ? '三引擎查询中…' : '并发查询'}
              </Button>
            </div>
            <div className="mt-1.5 text-[11px] text-muted-foreground">
              问小达+问财同时查询合并去重;查完后切换策略即时对结果精筛(不重复调用引擎);每次查询消耗 1 次 tdx 配额
            </div>

            {resError && (
              <div className="mt-2 text-[12px] text-amber-700 dark:text-amber-500 flex items-center gap-1.5">
                <AlertTriangle className="w-3.5 h-3.5 shrink-0" /> {resError}
              </div>
            )}

            {resMeta && (
              <div className="mt-2 flex items-center gap-2 flex-wrap text-[11px] text-muted-foreground">
                <span>
                  「{resLastQuery}」合并 <span className="text-foreground font-medium">{resRows.length}</span> 只
                  (问小达 {resMeta.tdx} · 问财 {resMeta.wencai}
                  {resStrategyId ? ` · 策略通过 ${resMeta.strategy}` : ''})
                </span>
                {resMeta.dropped > 0 && <span>无法识别 {resMeta.dropped} 行已忽略</span>}
                {resFiltering && <span className="text-primary">策略精筛中…</span>}
                {resFilterDown && (
                  <span className="text-amber-600 dark:text-amber-500">⚠ 策略精筛不可用,已显示未精筛结果</span>
                )}
                {resMeta.enginesDown && (
                  <span className="text-amber-600 dark:text-amber-500">⚠ {resMeta.enginesDown} 不可用,基于剩余引擎共振</span>
                )}
                <button
                  type="button"
                  onClick={() => setResOnly(!resOnly)}
                  className={`rounded-lg px-2.5 py-1 text-[11px] font-medium transition-colors border ${
                    resOnly
                      ? 'bg-orange-500/15 text-orange-600 dark:text-orange-400 border-orange-500/40'
                      : 'bg-accent/50 text-muted-foreground hover:bg-accent border-transparent'
                  }`}
                  title="只显示被 2 个及以上引擎同时命中的候选"
                >
                  🔥 只看共振≥2
                </button>
              </div>
            )}

            {!resLoading && resRows.length > 0 && (
              <div className="mt-3 overflow-x-auto">
                <table className="w-full text-[12px]">
                  <thead>
                    <tr className="text-[11px] text-muted-foreground border-b border-border/50">
                      <th className="text-left py-1.5 pr-2">代码</th>
                      <th className="text-left py-1.5 pr-2">名称</th>
                      <th className="text-center py-1.5 pr-2">问小达</th>
                      <th className="text-center py-1.5 pr-2">问财</th>
                      <th className="text-center py-1.5 pr-2">策略</th>
                      <th className="text-center py-1.5" title="本次查询的引擎命中数(与机会页全池共振口径不同)">🔥 共振</th>
                    </tr>
                  </thead>
                  <tbody>
                    {resRows.filter((r) => !resOnly || resonanceCountOf(r) >= 2).map((r) => {
                      const cnt = resonanceCountOf(r)
                      return (
                        <tr
                          key={r.symbol}
                          className="border-b border-border/30 hover:bg-accent/40 cursor-pointer"
                          onClick={() => openInsight({
                            stock_symbol: r.symbol,
                            stock_market: 'CN',
                            stock_name: r.name,
                            action: 'watch',
                            action_label: '观望',
                            is_holding_snapshot: false,
                            rank_score: 0,
                            score: 0,
                            status: 'inactive',
                            source_pool: 'tdx',
                            source_pool_label: '共振查询',
                            risk_level: 'low',
                            risk_level_label: '低风险',
                            source_agent: 'market_scan',
                            strategy_code: 'resonance_query',
                            strategy_name: '共振查询',
                            strategy_version: 'v1',
                            confidence: null,
                            signal: '',
                            reason: `共振查询: ${resLastQuery}(命中 ${cnt} 个引擎)`,
                            evidence: [],
                            holding_days: 3,
                            entry_low: null,
                            entry_high: null,
                            stop_loss: null,
                            target_price: null,
                            invalidation: '',
                            plan_quality: 0,
                            source_suggestion_id: null,
                            source_candidate_id: null,
                            trace_id: '',
                            context_quality_score: null,
                            score_breakdown: { weighted_score: 0, has_entry_plan: false },
                            market_regime: {},
                            cross_feature: {},
                            news_metric: {},
                            constrained: false,
                            constraint_reasons: [],
                            payload: { source_meta: { plan: {} } },
                            created_at: '',
                            updated_at: '',
                          } as unknown as StrategySignalItem)}
                        >
                          <td className="py-1.5 pr-2 font-mono text-muted-foreground">{r.symbol}</td>
                          <td className="py-1.5 pr-2 font-medium text-foreground">{r.name}</td>
                          <td className="py-1.5 pr-2 text-center">
                            {r.fromTdx ? <span className="text-emerald-600 dark:text-emerald-400">✓{r.tdxRank ? `#${r.tdxRank}` : ''}</span> : <span className="text-muted-foreground/40">—</span>}
                          </td>
                          <td className="py-1.5 pr-2 text-center">
                            {r.fromWencai ? <span className="text-emerald-600 dark:text-emerald-400">✓{r.wencaiRank ? `#${r.wencaiRank}` : ''}</span> : <span className="text-muted-foreground/40">—</span>}
                          </td>
                          <td className="py-1.5 pr-2 text-center">
                            {r.strategyPassed == null
                              ? <span className="text-muted-foreground/40">—</span>
                              : r.strategyPassed
                                ? <span className="text-primary font-semibold">✓ {r.strategyScore?.toFixed(0)}</span>
                                : <span className="text-muted-foreground/50">✗</span>}
                          </td>
                          <td className="py-1.5 text-center">
                            {cnt >= 2
                              ? <span className="font-semibold text-orange-600 dark:text-orange-400">🔥×{cnt}</span>
                              : <span className="text-muted-foreground/50">×{cnt}</span>}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}

            {!resLoading && resMeta && resRows.length === 0 && !resError && (
              <div className="mt-3 text-[12px] text-muted-foreground">本次查询无有效结果</div>
            )}
            {!resLoading && resOnly && resRows.length > 0 && resRows.every((r) => resonanceCountOf(r) < 2) && (
              <div className="mt-2 text-[11px] text-muted-foreground">无 ≥2 引擎共振的标的,可关闭「只看共振」查看全部</div>
            )}
          </TabsContent>
          <TabsContent value="strategy">
            <StrategyHitrateBoard />
            <RegimeSignalMatrix />
            <div className="flex items-center gap-2 flex-wrap">
              <Select value={scanStrategyId} onValueChange={setScanStrategyId}>
                <SelectTrigger className="h-8 text-[12px] w-[220px]">
                  <SelectValue placeholder="选择策略" />
                </SelectTrigger>
                <SelectContent>
                  {scanStrategies.map((s) => (
                    <SelectItem key={s.id} value={s.id}>{s.display_name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={scanUniverse} onValueChange={(v) => setScanUniverse(v as 'all' | 'watchlist')}>
                <SelectTrigger className="h-8 text-[12px] w-[130px]"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全市场</SelectItem>
                  <SelectItem value="watchlist">自选+种子池</SelectItem>
                </SelectContent>
              </Select>
              <Button
                size="sm"
                className="h-8 text-[12px]"
                onClick={doScan}
                disabled={scanning || !scanStrategyId}
              >
                {scanning ? <span className="w-3.5 h-3.5 border-2 border-current/30 border-t-current rounded-full animate-spin" /> : <ScanSearch className="w-3.5 h-3.5 mr-1" />}
                {scanning ? '扫描中...' : '批量选股'}
              </Button>
              {scanResult && (
                <span className="text-[11px] text-muted-foreground">
                  扫描 {scanResult.scanned} 只 → 命中 {scanResult.total} 只
                </span>
              )}
            </div>
            {scanError && (
              <div className="mt-2 text-[12px] text-amber-700 dark:text-amber-500 flex items-center gap-1.5">
                <AlertTriangle className="w-3.5 h-3.5" /> {scanError}
              </div>
            )}
            {scanResult && scanResult.items.length > 0 && (
              <div className="mt-3 overflow-x-auto">
                <table className="w-full text-[12px]">
                  <thead>
                    <tr className="text-[11px] text-muted-foreground border-b border-border/50">
                      <th className="text-left py-1.5 pr-2">代码</th>
                      <th className="text-left py-1.5 pr-2">名称</th>
                      <th className="text-right py-1.5 pr-2">评分</th>
                      <th className="text-right py-1.5 pr-2">现价</th>
                      <th className="text-right py-1.5 pr-2">PE</th>
                      <th className="text-right py-1.5 pr-2">PB</th>
                      <th className="text-right py-1.5">市值(亿)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {scanResult.items.map((it) => {
                      const d = it.current_data || {}
                      const num = (v: unknown) => (v == null || Number.isNaN(Number(v)) ? '--' : Number(v).toFixed(2))
                      return (
                        <tr key={it.symbol} className="border-b border-border/30 hover:bg-accent/40 cursor-pointer" onClick={() => openInsight({
                          stock_symbol: it.symbol,
                          stock_market: (it.market || 'CN') as 'CN',
                          stock_name: it.name,
                          rank_score: it.score,
                          is_holding_snapshot: false,
                        } as unknown as StrategySignalItem)}>
                          <td className="py-1.5 pr-2 font-mono text-muted-foreground">{it.symbol}</td>
                          <td className="py-1.5 pr-2 font-medium text-foreground">{it.name}</td>
                          <td className="py-1.5 pr-2 text-right font-semibold text-primary">{it.score.toFixed(1)}</td>
                          <td className="py-1.5 pr-2 text-right font-mono">{num(d.current_price)}</td>
                          <td className="py-1.5 pr-2 text-right font-mono">{num(d.pe_ttm)}</td>
                          <td className="py-1.5 pr-2 text-right font-mono">{num(d.pb_ratio)}</td>
                          <td className="py-1.5 text-right font-mono text-muted-foreground">{num(d.market_cap)}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
            {scanResult && scanResult.items.length === 0 && (
              <div className="mt-3 text-[12px] text-muted-foreground">
                没有股票通过该策略的硬过滤条件
                <span className="block mt-1 text-[11px] text-muted-foreground/70">
                  {new Date().getHours() < 9 || new Date().getHours() >= 15
                    ? '💡 当前为非交易时段, 腾讯行情中涨跌幅/量比/换手为 0, 依赖量能条件的策略(资金热度/放量突破)会筛不出票。建议交易时段使用, 或改选估值类策略(双低/低波质量)。'
                    : '可尝试放宽条件或切换为「自选+种子池」范围'}
                </span>
              </div>
            )}
          </TabsContent>
          <TabsContent value="tdx">
            {/* 用户主动按板块查询,避免每次进页面自动消耗 tdx ask 配额 */}
            <form
              className="flex items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault()
                void loadTdx(tdxQuery)
              }}
            >
              <Input
                value={tdxQuery}
                onChange={(e) => setTdxQuery(e.target.value)}
                placeholder="输入板块/概念/选股条件,如:半导体、商业航天、今日涨幅前10的医药"
                className="flex-1"
                disabled={tdxLoading}
              />
              <Button
                type="submit"
                size="sm"
                disabled={tdxLoading || !tdxQuery.trim()}
              >
                {tdxLoading ? (
                  <>
                    <RefreshCw className="w-3 h-3 mr-1 animate-spin" />
                    查询中…
                  </>
                ) : (
                  '查询'
                )}
              </Button>
            </form>
            <div className="mt-3">
              {!tdxActiveQuery ? (
                <div className="text-[11px] text-muted-foreground py-6 text-center">
                  输入板块或选股条件,点击查询(每次查询消耗 1 次 tdx ask 配额)
                </div>
              ) : tdxData == null ? (
                <div className="text-[11px] text-red-600 py-3">查询失败,请稍后重试</div>
              ) : (
                <>
                  <div className="text-[11px] text-muted-foreground mb-2">
                    查询: <span className="text-foreground font-medium">{tdxActiveQuery}</span>
                    {' · '}
                    {tdxData.rows?.length || 0} 条结果
                  </div>
                  <div className="flex flex-col gap-1.5 max-h-[400px] overflow-y-auto pr-1">
                    {(tdxData.rows || []).slice(0, 10).map((r: Record<string, unknown>, i: number) => {
                      const code = String(r.sec_code ?? r.code ?? '')
                      const name = String(r.sec_name ?? r.name ?? '')
                      const chg = String(r.chg ?? r.change_pct ?? '')
                      const mainNet = Object.entries(r).find(([k]) => k.includes('主力净额') || k.includes('主力净'))?.[1]
                      const clickable = !!code
                      return (
                        <button
                          key={`${code}-${i}`}
                          type="button"
                          disabled={!clickable}
                          onClick={() => clickable && openInsight({
                            stock_symbol: code,
                            stock_market: 'CN',
                            stock_name: name,
                            action: 'watch',
                            action_label: '观望',
                            is_holding_snapshot: false,
                            rank_score: 0,
                            score: 0,
                            status: 'inactive',
                            source_pool: 'watchlist',
                            source_pool_label: '关注池',
                            risk_level: 'low',
                            risk_level_label: '低风险',
                            source_agent: 'market_scan',
                            strategy_code: 'tdx_wenda',
                            strategy_name: '通达信问小达',
                            strategy_version: 'v1',
                            confidence: null,
                            signal: '',
                            reason: `通达信问小达: ${tdxActiveQuery}`,
                            evidence: [],
                            holding_days: 3,
                            entry_low: null,
                            entry_high: null,
                            stop_loss: null,
                            target_price: null,
                            invalidation: '',
                            plan_quality: 0,
                            source_suggestion_id: null,
                            source_candidate_id: null,
                            trace_id: '',
                            context_quality_score: null,
                            score_breakdown: { weighted_score: 0, has_entry_plan: false },
                            market_regime: {},
                            cross_feature: {},
                            news_metric: {},
                            constrained: false,
                            constraint_reasons: [],
                            payload: { source_meta: { plan: {} } },
                            created_at: '',
                            updated_at: '',
                          } as unknown as StrategySignalItem)}
                          className={`text-left text-[11px] rounded px-2 py-1.5 flex items-center justify-between gap-2 ${
                            clickable ? 'hover:bg-accent cursor-pointer' : 'cursor-default'
                          }`}
                        >
                          <span className="truncate">
                            <span className="text-muted-foreground mr-1">{code}</span>
                            <span className="font-medium text-foreground">{name}</span>
                          </span>
                          <span className="flex items-center gap-1.5 shrink-0">
                            {chg && (
                              <span
                                className={
                                  String(chg).startsWith('-')
                                    ? 'text-emerald-700 dark:text-emerald-700'
                                    : 'text-rose-700 dark:text-rose-400'
                                }
                              >
                                {chg}%
                              </span>
                            )}
                            {mainNet != null && (
                              <span className="text-[10px] text-primary">主力{String(mainNet)}</span>
                            )}
                          </span>
                        </button>
                      )
                    })}
                    {(tdxData.rows || []).length === 0 && (
                      <div className="text-[11px] text-muted-foreground py-3 text-center">
                        暂无数据(试试简化查询词,如「半导体」)
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>
          </TabsContent>
          <TabsContent value="wencai">
            <WencaiPanel embedded />
          </TabsContent>
          <TabsContent value="stockpool">
            <div className="flex items-center gap-2 flex-wrap">
              <Input
                value={poolSymbols}
                onChange={(e) => setPoolSymbols(e.target.value)}
                placeholder="输入6位股票代码, 逗号分隔, 如 002361,600519"
                className="h-8 text-[12px] flex-1 min-w-[260px]"
              />
              <Button size="sm" onClick={() => void runStockPool()} disabled={poolLoading}>
                {poolLoading ? '扫描中...' : '扫描共振'}
              </Button>
            </div>
            <div className="mt-2 text-[11px] text-muted-foreground">
              决策先锋三指标共振：GS趋势 + AI机构活跃度 + L2主力净流入，三项全满足=强共振
            </div>
            {poolScanned ? (
              poolRows.length ? (
                <div className="mt-2 overflow-x-auto">
                  <table className="w-full text-[12px] border-collapse">
                    <thead>
                      <tr className="text-left text-muted-foreground border-b border-border/50">
                        <th className="py-1.5 pr-2 font-medium">代码</th>
                        <th className="py-1.5 pr-2 font-medium">活跃度</th>
                        <th className="py-1.5 pr-2 font-medium">GS状态</th>
                        <th className="py-1.5 pr-2 font-medium">L2净流入</th>
                        <th className="py-1.5 font-medium">共振</th>
                      </tr>
                    </thead>
                    <tbody>
                      {poolRows.map((r) => (
                        <tr key={r.symbol} className="border-b border-border/30">
                          <td className="py-1.5 pr-2 font-mono">{r.symbol}</td>
                          <td className="py-1.5 pr-2 font-mono">
                            {r.activity != null ? (
                              <span
                                className={
                                  r.activity_level === '大牛' || r.activity_level === '强势'
                                    ? 'text-rose-600'
                                    : ''
                                }
                              >
                                {r.activity.toFixed(2)}
                                {r.activity_level ? `(${r.activity_level})` : ''}
                              </span>
                            ) : (
                              '--'
                            )}
                          </td>
                          <td className="py-1.5 pr-2 font-mono">
                            {r.gs_state ?? '--'}
                            {r.gs_signal === 'G' ? ' G买' : r.gs_signal === 'S' ? ' S卖' : ''}
                          </td>
                          <td className="py-1.5 pr-2 font-mono">
                            {r.l2_net != null ? (
                              <span
                                className={
                                  r.l2_net > 0
                                    ? 'text-rose-600'
                                    : r.l2_net < 0
                                      ? 'text-emerald-600'
                                      : ''
                                }
                              >
                                {r.l2_net > 0 ? '+' : ''}
                                {r.l2_net.toFixed(0)}万
                              </span>
                            ) : (
                              '--'
                            )}
                          </td>
                          <td className="py-1.5">
                            <span
                              className={
                                r.resonance === '强'
                                  ? 'text-rose-600 font-semibold'
                                  : r.resonance === '弱'
                                    ? 'text-amber-600 font-medium'
                                    : 'text-muted-foreground'
                              }
                            >
                              {r.resonance === '强' ? '强共振' : r.resonance === '弱' ? '弱共振' : '无'}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="mt-2 text-[11px] text-muted-foreground">无结果</div>
              )
            ) : (
              <div className="mt-2 text-[11px] text-muted-foreground">输入代码后点「扫描共振」查看结果</div>
            )}
          </TabsContent>
        </Tabs>
      </div>

      {(factorStats || constraintStats) && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
          <div className="card p-3">
            <div className="text-[11px] text-muted-foreground">平均Alpha因子</div>
            <div className="text-[18px] font-bold mt-1">{factorStats ? factorStats.avg_alpha_score.toFixed(1) : '--'}</div>
            <div className="text-[10px] text-muted-foreground mt-1">样本 {factorStats?.sample_size ?? '--'}</div>
          </div>
          <div className="card p-3">
            <div className="text-[11px] text-muted-foreground">平均事件催化</div>
            <div className="text-[18px] font-bold mt-1">{factorStats ? factorStats.avg_catalyst_score.toFixed(1) : '--'}</div>
            <div className="text-[10px] text-muted-foreground mt-1">
              拥挤惩罚 {factorStats ? factorStats.avg_crowd_penalty.toFixed(1) : '--'}
            </div>
          </div>
          <div className="card p-3">
            <div className="text-[11px] text-muted-foreground">平均质量/风险</div>
            <div className="text-[18px] font-bold mt-1">
              {factorStats ? `${factorStats.avg_quality_score.toFixed(1)} / ${factorStats.avg_risk_penalty.toFixed(1)}` : '--'}
            </div>
            <div className="text-[10px] text-muted-foreground mt-1">质量分越高越好</div>
          </div>
          <div className="card p-3">
            <div className="text-[11px] text-muted-foreground">组合约束降级</div>
            <div className="text-[18px] font-bold mt-1">{constraintStats?.constrained_top20 ?? 0}</div>
            <div className="text-[10px] text-muted-foreground mt-1">Top20 被风控降级数量</div>
          </div>
        </div>
      )}

      {(regimeSummary.length > 0 || riskSummary.length > 0) && (
        <div className="card p-3 mb-4">
          <div className="text-[11px] text-muted-foreground mb-2">市场状态与组合风险</div>
          <div className="flex flex-wrap gap-2">
            {regimeSummary.map((r) => (
              <span key={`regime-${r.market}`} className={`text-[11px] px-2.5 py-1 rounded ${regimeToneClass(r.regime)}`}>
                {marketLabel(r.market)}: {r.label} · 置信 {Math.round(r.confidence * 100)}%
              </span>
            ))}
            {riskSummary.map((r) => (
              <span key={`risk-${r.market}`} className="text-[11px] px-2.5 py-1 rounded bg-accent/70 text-muted-foreground border border-border/60">
                {marketLabel(r.market)}风险: {r.riskLevel} · 集中度{(r.concentration * 100).toFixed(0)}% · 高风险占比{(r.highRiskRatio * 100).toFixed(0)}%
              </span>
            ))}
          </div>
        </div>
      )}


      {error && (
        <div className="card p-3 mb-4 text-[12px] text-amber-700 dark:text-amber-500 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4" />
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {visibleItems.map((group) => {
          const item = group.primary
          const payload = item.payload && typeof item.payload === 'object' ? item.payload as Record<string, unknown> : {}
          const sourceMeta = payload.source_meta && typeof payload.source_meta === 'object' ? payload.source_meta as Record<string, unknown> : {}
          const sourcePlan = sourceMeta.plan && typeof sourceMeta.plan === 'object' ? sourceMeta.plan as Record<string, unknown> : {}
          const entryLow = toNumberOrNull(item.entry_low) ?? toNumberOrNull(sourcePlan.entry_low)
          const entryHigh = toNumberOrNull(item.entry_high) ?? toNumberOrNull(sourcePlan.entry_high)
          const stopLoss = toNumberOrNull(item.stop_loss) ?? toNumberOrNull(sourcePlan.stop_loss)
          const targetPrice = toNumberOrNull(item.target_price) ?? toNumberOrNull(sourcePlan.target_price)
          const stateKey = `${item.snapshot_date}:${group.key}`
          const inWatchlist = watchlist.has(group.key)
          const breakdown = item.score_breakdown || {}
          const marketRegime = item.market_regime || {}
          const crossFeature = item.cross_feature || {}
          const newsMetric = item.news_metric || {}
          const strategyHead = group.strategyNames.slice(0, 2).join(' / ') || (item.strategy_name || item.strategy_code)
          const strategyTailCount = Math.max(0, group.strategyNames.length - 2)
          const sourceAgentHead = group.sourceAgents[0] || sourceAgentLabel(item.source_agent)
          const sourceAgentTailCount = Math.max(0, group.sourceAgents.length - 1)
          const eventScore = toNumberOrNull(newsMetric.event_score)
          const eventCount = Number(newsMetric.news_count || 0)
          // P1 多源整合: 来源徽章列表(candidate_source + hits + resonance_sources 去重)
          const badgeSources = group.allSources.length > 0
            ? group.allSources
            : [item.candidate_source || item.source_pool || 'watchlist']
          const resCount = group.resonanceCount
          const sourceFlags: string[] = []
          if (group.hasMarketScan) sourceFlags.push('市场候选')
          if (inWatchlist) sourceFlags.push('已关注标的')
          if (sourceFlags.length <= 0) sourceFlags.push('关注池')
          const sourcePoolLabel = group.hasMarketScan
            ? (group.members.some((x) => x.source_pool === 'mixed') ? '市场+关注' : '市场池')
            : (item.source_pool_label || '关注池')
          return (
            <div key={stateKey} className={`card p-3 sm:p-4 transition-colors ${toneClass(item)}`}>
              <button className="w-full text-left" onClick={() => openInsight(item)}>
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-[15px] font-semibold truncate flex items-center gap-1.5">
                      <span className="truncate">{item.stock_name || item.stock_symbol}</span>
                      {/* P1: 多源共振火焰 */}
                      {resCount >= 2 && (
                        <span
                          className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-orange-500/15 text-orange-600 dark:text-orange-400"
                          title={`多源共振×${resCount}: ${resonanceSourcesLabel(item) || badgeSources.map(sourceCn).join(' + ')}`}
                        >
                          🔥×{resCount}
                        </span>
                      )}
                    </div>
                    <div className="text-[11px] text-muted-foreground font-mono">{item.stock_market}:{item.stock_symbol}</div>
                  </div>
                  <div className="text-right">
                    <div className="text-[12px]">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] ${actionBadgeClass(item.action)}`}>
                        {displayActionLabel(item)}
                      </span>
                    </div>
                    <div className={`text-[13px] font-bold font-mono mt-1.5 ${Number(item.rank_score || item.score || 0) >= 80 ? 'text-primary' : 'text-muted-foreground'}`}>
                      评分 {Math.round(item.rank_score || item.score || 0)}
                    </div>
                    {item.ai_score != null && (
                      <div className="mt-1 flex items-center justify-end gap-1">
                        <span className="text-[10px] text-muted-foreground">AI</span>
                        <span className={`inline-flex items-center justify-center min-w-[18px] px-1.5 py-0.5 rounded text-[11px] font-semibold ${item.ai_score >= 8 ? 'bg-green-500/20 text-green-700 dark:text-green-400' : item.ai_score >= 6 ? 'bg-primary/20 text-primary' : item.ai_score >= 4 ? 'bg-amber-500/20 text-amber-700 dark:text-amber-600' : 'bg-red-500/20 text-red-700 dark:text-red-400'}`}>
                          {item.ai_score}
                        </span>
                      </div>
                    )}
                  </div>
                </div>
                <div className="mt-1.5 text-[12px] leading-5 text-foreground line-clamp-2">{item.signal || item.reason || '--'}</div>
                <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] leading-4 text-muted-foreground">
                  <div className="font-medium text-foreground/90">入场: {formatEntryDisplay(item.action, entryLow, entryHigh)}</div>
                  <div>止损: {formatPlanPrice(stopLoss)}</div>
                  <div>目标: {formatPlanPrice(targetPrice)}</div>
                  <div>失效: {item.invalidation || '--'}</div>
                  <div>
                    策略: {strategyHead}
                    {strategyTailCount > 0 ? ` +${strategyTailCount}` : ''}
                  </div>
                  {/* P1 多源整合: 来源徽章(命中来源全部展示) */}
                  <div className="flex items-center gap-1 flex-wrap col-span-2">
                    <span>来源:</span>
                    {badgeSources.map((s) => (
                      <span
                        key={s}
                        className="inline-flex items-center px-1.5 py-0 rounded text-[10px] rounded-full bg-primary/10 text-primary"
                      >
                        {sourceCn(s) || s}
                      </span>
                    ))}
                  </div>
                  <div>来源池: {sourcePoolLabel}</div>
                  <div>
                    来源Agent: {sourceAgentHead}
                    {sourceAgentTailCount > 0 ? ` +${sourceAgentTailCount}` : ''}
                  </div>
                  <div>风险: {item.risk_level_label || item.risk_level || '--'}</div>
                  <div>市场状态: {marketRegime.regime_label || marketRegime.regime || '--'}</div>
                  <div>持仓: {item.is_holding_snapshot ? '持仓中' : '未持仓'}</div>
                  <div>市场: {marketLabel(item.stock_market)}</div>
                </div>
                <div className="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-1 text-[10px] leading-4 text-muted-foreground">
                  <div>Alpha: {formatMetric(breakdown.alpha_score)}</div>
                  <div>催化: {formatMetric(breakdown.catalyst_score)}</div>
                  <div>质量: {formatMetric(breakdown.quality_score)}</div>
                  <div>风险惩罚: {formatMetric(breakdown.risk_penalty)}</div>
                  <div>相对强弱: {crossFeature.relative_strength_pct != null ? `${Number(crossFeature.relative_strength_pct).toFixed(0)}分位` : '--'}</div>
                  <div className="font-medium text-foreground/90">事件催化: {eventScore != null ? eventScore.toFixed(1) : '--'}{eventCount > 0 ? `（${eventCount}条）` : '（无命中）'}</div>
                </div>
                {item.factor_explain && (((item.factor_explain.positive?.length ?? 0) > 0) || ((item.factor_explain.negative?.length ?? 0) > 0)) && (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {(item.factor_explain.positive ?? []).map((f) => (
                      <span key={`p-${f.factor}`} className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] bg-green-500/15 text-green-700 dark:text-green-400">
                        {f.label} +{Math.abs(f.contribution).toFixed(1)}
                      </span>
                    ))}
                    {(item.factor_explain.negative ?? []).map((f) => (
                      <span key={`n-${f.factor}`} className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] bg-red-500/15 text-red-700 dark:text-red-400">
                        {f.label} {f.contribution.toFixed(1)}
                      </span>
                    ))}
                  </div>
                )}
                {item.constrained && (
                  <div className="mt-1.5 text-[10px] text-amber-700 dark:text-amber-600">
                    组合约束: {(item.constraint_reasons || []).join('；') || '已自动降级'}
                  </div>
                )}
              </button>

              <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2">
                <div className="text-[10px] text-muted-foreground">
                  来源: {sourceFlags.join(' + ')}
                </div>
                <div className="flex items-center gap-3">
                  <div className="flex items-center gap-0.5 text-[10px]">
                    <button
                      type="button"
                      disabled={feedbackPending.has(group.key)}
                      onClick={() => handleCandidateFeedback(item, true)}
                      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded transition-colors disabled:opacity-40 ${
                        feedbackMap[group.key] === true
                          ? 'bg-green-500/15 text-green-700 dark:text-green-400'
                          : 'text-muted-foreground hover:text-green-700 dark:hover:text-green-400'
                      }`}
                      title="这个候选建议有用"
                    >
                      <ThumbsUp className="h-3 w-3" />
                      有用
                    </button>
                    <button
                      type="button"
                      disabled={feedbackPending.has(group.key)}
                      onClick={() => handleCandidateFeedback(item, false)}
                      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded transition-colors disabled:opacity-40 ${
                        feedbackMap[group.key] === false
                          ? 'bg-red-500/15 text-red-700 dark:text-red-400'
                          : 'text-muted-foreground hover:text-red-700 dark:hover:text-red-400'
                      }`}
                      title="这个候选建议没用"
                    >
                      <ThumbsDown className="h-3 w-3" />
                      没用
                    </button>
                  </div>
                  <button
                    type="button"
                    onClick={() => setShareSignal(item)}
                    className="inline-flex items-center gap-1 text-[10px] text-muted-foreground transition-colors hover:text-primary"
                    title="生成 AI 评分分享图"
                  >
                    <Share2 className="h-3 w-3" />
                    分享图
                  </button>
                  <div className="text-[10px] text-muted-foreground">评估: 自动后验</div>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {!loading && groupedItems.length === 0 && (
        <div className="card p-8 text-center text-[12px] text-muted-foreground mt-4">暂无满足条件的机会</div>
      )}

      <details className="mt-6 group">
        <summary className="cursor-pointer list-none flex items-center gap-2 text-[12px] font-medium text-muted-foreground hover:text-foreground transition-colors">
          <span className="text-[11px] opacity-60 transition-transform group-open:rotate-90">▶</span>
          因子权重与战绩
        </summary>
        <div className="mt-3">
          <FactorWeightsPanel />
        </div>
      </details>
      </>
      )}

      <StockInsightModal
        open={insightOpen}
        onOpenChange={setInsightOpen}
        symbol={insightSymbol}
        market={insightMarket}
        stockName={insightName}
        hasPosition={insightHasPosition}
      />

      {shareSignal && (
        <SignalScoreShareCard
          open={!!shareSignal}
          onClose={() => setShareSignal(null)}
          item={shareSignal}
        />
      )}

      <StrategyLibraryDialog
        open={strategyLibOpen}
        onOpenChange={setStrategyLibOpen}
      />
    </div>
  )
}

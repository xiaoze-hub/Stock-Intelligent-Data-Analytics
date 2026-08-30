import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
// 反AI模板 P2:精简图标导入 — 段落头去"每节一图标"惯性, 只保留要紧事/体检两个扫描区的图标
import { RefreshCw, AlertTriangle, Activity, ShieldAlert, Share2, FileText, ChevronRight } from 'lucide-react'
import {
  dashboardApi,
  portfolioApi,
  recommendationsApi,
  homeApi,
  stocksApi,
  reportsApi,
  type DashboardMarketIndex,
  type DashboardMarketCapitalFlow,
  type DashboardMarketStatus,
  type DashboardMonitorStock,
  type DashboardOverviewResponse,
  type PortfolioDiagnostics,
  type PortfolioBenchmark,
  type StrategySignalItem,
  type AlertHitToday,
  type PortfolioTodo,
  type ReportItem,
  type CurateCandidate,
  type CuratedItem,
  type AttributionItem,
  type PortfolioAiReview,
  type MarketAnomalyItem,
  fetchAPI,
} from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Skeleton } from '@panwatch/base-ui/components/ui/skeleton'
import { Onboarding } from '@panwatch/biz-ui/components/onboarding'
import StockInsightModal from '@panwatch/biz-ui/components/stock-insight-modal'
import KpiBand, { usePhaseLabel, useMainlineTop1 } from '@panwatch/biz-ui/components/KpiBand'
import MarketPhaseCard from '@panwatch/biz-ui/components/MarketPhaseCard'
import MarketMainlineCard from '@panwatch/biz-ui/components/MarketMainlineCard'
import BreadthDistributionChart from '@panwatch/biz-ui/components/dashboard/BreadthDistributionChart'
import SentimentGauge from '@panwatch/biz-ui/components/dashboard/SentimentGauge'
import FlowHistoryChart from '@panwatch/biz-ui/components/dashboard/FlowHistoryChart'
import DiscoveryPanel from '@/components/DiscoveryPanel'
import SkeletonRows from '@/components/SkeletonRows'
import Sparkline from '@/components/Sparkline'
import ErrorBanner from '@/components/ErrorBanner'
import BenchChart from '@/components/BenchChart'
import BenchmarkShareCard from '@/components/BenchmarkShareCard'
import DiagnosticsShareCard from '@/components/DiagnosticsShareCard'
import DigestShareCard from '@/components/DigestShareCard'
import StockContextMenu, { type StockContextMenuState, type StockContextTarget } from '@/components/StockContextMenu'
import { parseServerTime } from '@/lib/utils'

/** 安全 toFixed: 处理 string / null / undefined / 非有限数, 一律返回 fallback。
 *  修复 2026-08-21: Dashboard 报 TypeError: c.price.toFixed is not a function
 *  真因 = 后端 PG 数值类型(DECIMAL) 经 psycopg2 → JSON 后变成字符串,
 *  前端直接 .toFixed() 报 TypeError, AppErrorBoundary 兜底 → "页面遇到了问题"。
 */
function safeNum(v: unknown): number | null {
  if (v == null) return null
  const n = typeof v === 'number' ? v : Number(v)
  return isFinite(n) ? n : null
}
function safeFixed(v: unknown, digits = 2, fallback = '--'): string {
  const n = safeNum(v)
  if (n == null) return fallback
  return n.toFixed(digits)
}
function safeFlow(v: unknown, digits = 1): string {
  const n = safeNum(v)
  if (n == null) return '--'
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}亿`
}
function pct(v?: number | null, digits = 2): string {
  if (v == null || !isFinite(v)) return '--'
  return `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`
}
function moveColor(v?: number | null): string {
  // 2026-08-23 U2: 收敛到 stock.up/down token(红涨绿跌, 暗色对比度统一控制)
  if (v == null) return 'text-muted-foreground'
  return v > 0 ? 'text-stock-up' : v < 0 ? 'text-stock-down' : 'text-muted-foreground'
}
/** 金额(元) → +1,234万 / -3.45亿 紧凑展示;null/非数 → '--'。供主力动向暗盘净额用。 */
function formatYuan(v: unknown): string {
  const n = safeNum(v)
  if (n == null) return '--'
  const sign = n >= 0 ? '+' : ''
  const abs = Math.abs(n)
  if (abs >= 1e8) return `${sign}${(n / 1e8).toFixed(2)}亿`
  if (abs >= 1e4) return `${sign}${(n / 1e4).toFixed(0)}万`
  return `${sign}${Math.round(n)}`
}
/** 涨跌着色 chip 的背景+文字类;null/平盘 → 灰底。红涨绿跌(A股口径)。 */
function pctChipCls(v?: number | null): string {
  if (v == null) return 'bg-accent text-muted-foreground'
  if (v > 0) return 'bg-stock-up/10 text-stock-up'
  if (v < 0) return 'bg-stock-down/10 text-stock-down'
  return 'bg-accent text-muted-foreground'
}
/** 归一化后端列表响应:可能直接是数组,也可能是 {items:[...]} / {data:[...]},取不到返回空数组。 */
function pickList<T>(v: unknown, key: string): T[] {
  if (Array.isArray(v)) return v as T[]
  if (v && typeof v === 'object') {
    const o = v as Record<string, unknown>
    if (Array.isArray(o[key])) return o[key] as T[]
    if (Array.isArray(o.data)) return o.data as T[]
  }
  return []
}
/** 去掉常见 markdown 标记,供简报摘要行取纯文本用。 */
const WEEKDAY_LABEL = ['日', '一', '二', '三', '四', '五', '六']
function formatHeaderTime(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${y}-${m}-${day} 周${WEEKDAY_LABEL[d.getDay()]} · ${hh}:${mm} 已刷新`
}
/** 报告相对时间:1分钟内→"刚刚";1小时内→"N分钟前";当天→"今天 HH:MM";昨天→"昨天 HH:MM";更早→"M月D日 HH:MM" */
function formatReportTime(iso: string): string {
  if (!iso) return ''
  const d = parseServerTime(iso)
  if (isNaN(d.getTime())) return iso.replace('T', ' ').slice(0, 16)
  const now = new Date()
  const diffMin = Math.floor((now.getTime() - d.getTime()) / 60000)
  if (diffMin < 1) return '刚刚'
  if (diffMin < 60) return `${diffMin}分钟前`
  const pad = (n: number) => String(n).padStart(2, '0')
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const startDay = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  if (startDay === startToday) return `今天 ${hm}`
  if (startDay === startToday - 86_400_000) return `昨天 ${hm}`
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hm}`
}
const ALERT_LABEL: Record<string, string> = {
  surge: '快速拉升',
  plunge: '快速跳水',
  high_volume: '放量异动',
  breakout: '突破',
  breakdown: '破位',
  limit_up: '涨停',
  limit_down: '跌停',
}

const FEED_BADGE: Record<string, { label: string; cls: string }> = {
  alert: { label: '提醒命中', cls: 'bg-rose-500/15 text-red-600' },
  holding: { label: '持仓', cls: 'bg-emerald-500/15 text-green-700' },
  watch: { label: '自选', cls: 'bg-accent text-muted-foreground' },
  risk: { label: '风险', cls: 'bg-amber-500/15 text-amber-600' },
  opportunity: { label: '机会', cls: 'bg-primary/10 text-primary' },
}

// 市场分布 stacked 条配色:CN 用品牌色,US/HK 用差异化色区分
const MARKET_BAR_CLS: Record<string, string> = {
  CN: 'bg-primary',
  US: 'bg-emerald-500',
  HK: 'bg-orange-500',
}

// 数据源健康指示灯(主力动向区块, P1-1): 绿=实时(在线+数据今日) / 黄=休市(在线但数据非今日) / 红=离线
const HEALTH_META: Record<'ok' | 'stale' | 'down', { dot: string; label: string }> = {
  ok: { dot: 'bg-green-500', label: '数据源: 实时' },
  stale: { dot: 'bg-amber-500', label: '数据源: 休市' },
  down: { dot: 'bg-red-500', label: '数据源: 离线' },
}

export default function DashboardPage() {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(true)
  const [indices, setIndices] = useState<DashboardMarketIndex[]>([])
  const [scan, setScan] = useState<DashboardMonitorStock[]>([])
  const [overview, setOverview] = useState<DashboardOverviewResponse | null>(null)
  const [diag, setDiag] = useState<PortfolioDiagnostics | null>(null)
  const [bench, setBench] = useState<PortfolioBenchmark | null>(null)
  const [benchState, setBenchState] = useState<'loading' | 'ready' | 'empty' | 'error'>('loading')
  const [oppFallback, setOppFallback] = useState<StrategySignalItem[]>([])
  const [alertHits, setAlertHits] = useState<AlertHitToday[]>([])
  const [todos, setTodos] = useState<PortfolioTodo[]>([])
  const [curated, setCurated] = useState<CuratedItem[]>([])
  const [attribution, setAttribution] = useState<AttributionItem[]>([])
  const [aiReview, setAiReview] = useState<PortfolioAiReview | null>(null)
  const [aiReviewLoading, setAiReviewLoading] = useState(false)
  // 自选股集合(用于判断盘前标的是否已在自选)
  const [watchSymbols, setWatchSymbols] = useState<Set<string>>(new Set())
  void watchSymbols // v0.4.7: 简报移除后暂无读值场景, setter 仍用于快捷加自选
  const [marketStatus, setMarketStatus] = useState<DashboardMarketStatus[]>([])
  // 最新报告(Hermes cron):首页速览取最近 4 条,30s 随首页自动刷新
  const [reports, setReports] = useState<ReportItem[]>([])
  const [reportsLoading, setReportsLoading] = useState(true)
  // 大盘资金流(同花顺源,东财 502 替代)
  const [marketFlow, setMarketFlow] = useState<DashboardMarketCapitalFlow | null>(null)
  // 异动池(东财) / 热榜(同花顺):独立加载,任一失败静默,不影响首页其他内容
  const [anomalies, setAnomalies] = useState<MarketAnomalyItem[]>([])
  const [anomaliesLoading, setAnomaliesLoading] = useState(true)
  const [refreshedAt, setRefreshedAt] = useState<Date | null>(null)
  // 2026-08-17: 数据源失败显式标识 — 收集 {source, message},横幅展示具体哪个源挂了
  const [sourceErrors, setSourceErrors] = useState<Array<{ id: number; source: string; message: string; retry?: () => void }>>([])
  // 2026-08-17: pushError 加 retry 参数(闭环修正 v0.2.60 回归 — ErrorBanner 重试按钮之前永不渲染)
  const pushError = (source: string, message: string, retry?: () => void) => {
    setSourceErrors(prev => {
      // 2026-08-17 v0.2.64 (B 报告 P1-5): 同一 source 已存在则合并更新 message + retry (避免横幅风暴)
      const existing = prev.find(p => p.source === source)
      if (existing) {
        return prev.map(p =>
          p.source === source
            ? { ...p, message: message.slice(0, 200), ...(retry ? { retry } : {}) }
            : p
        )
      }
      // 新 source — 加 id (B 报告 P1-6)
      return [...prev, { id: Date.now() + Math.random(), source, message: message.slice(0, 200), ...(retry ? { retry } : {}) }].slice(-8)
    })
  }
  // 分享卡开关:成绩单(基准)/ 组合体检 / 每日 digest
  const [shareBench, setShareBench] = useState(false)
  const [shareDiag, setShareDiag] = useState(false)
  const [shareDigest, setShareDigest] = useState(false)
  // 修复 2026-08-21: 默认关闭 Onboarding(原默认 true → 换浏览器/清缓存后用户被强制挡整页无法点击)
  // 改由头部"新手引导"按钮主动触发, localStorage 标志仍用于标记"已看过"以避免重复打扰
  const [showOnboarding, setShowOnboarding] = useState(false)
  const openOnboarding = useCallback(() => setShowOnboarding(true), [])
  const [modal, setModal] = useState<{ open: boolean; symbol: string; market: string; name: string; hasPosition: boolean }>({
    open: false,
    symbol: '',
    market: 'CN',
    name: '',
    hasPosition: false,
  })

  // 慢车道:基准/归因(拉全持仓 K 线,分钟级);独立可重试,失败/为空各有明确状态
  const loadBench = useCallback(() => {
    setBenchState('loading')
    Promise.allSettled([portfolioApi.benchmark({ days: 60 }), portfolioApi.attribution(60)]).then(([bn, at]) => {
      if (bn.status === 'fulfilled') {
        setBench(bn.value)
        setBenchState(!bn.value?.empty && (bn.value?.curve?.length ?? 0) >= 2 ? 'ready' : 'empty')
      } else {
        setBenchState('error')
      }
      if (at.status === 'fulfilled') setAttribution(at.value.items || [])
    })
  }, [])

  const load = useCallback(async (opts?: { skipBench?: boolean }) => {
    setLoading(true)
    setSourceErrors([])  // 清空上次错误
    // 指数 pills:独立加载不阻塞首屏(spark 冷启动可能 ~1s,数据到了自然浮现)
    dashboardApi.indices().then(setIndices).catch((err) => pushError('大盘指数', err?.message || '服务不可用', load))
    // 大盘资金流(同花顺源):独立加载,失败聚合到全局横幅
    dashboardApi.marketCapitalFlow().then(setMarketFlow).catch((err) => pushError('大盘资金流', err?.message || '服务不可用', load))
    // 最新报告(Hermes cron):独立加载,失败静默;cacheMode reload 保证 30s 轮询必拿新数据
    setReportsLoading(true)
    reportsApi
      .list({ limit: 8, cacheMode: 'reload' })
      .then((r) => setReports((r.items || []).slice(0, 4)))
      .catch((err) => {
        setReports([])
        pushError('Hermes 报告', err?.message || '服务不可用', load)
      })
      .finally(() => setReportsLoading(false))
    // 异动池(东财):独立加载,失败静默(端点未就绪时优雅降级为空态)
    setAnomaliesLoading(true)
    dashboardApi
      .anomalies({ limit: 10 })
      .then((r) => setAnomalies(pickList<MarketAnomalyItem>(r, 'items').slice(0, 10)))
      .catch((err) => {
        setAnomalies([])
        pushError('异动池 (东财)', err?.message || '服务不可用', load)
      })
      .finally(() => setAnomaliesLoading(false))
    // 快车道:DB/轻量查询,先让首屏(要紧事/体检分布)尽快出来
    const [sc, ov, dg, ht, td, ms] = await Promise.allSettled([
      dashboardApi.intradayScan(),
      dashboardApi.overview({ market: 'ALL', action_limit: 6, risk_limit: 6 }),
      portfolioApi.diagnostics(),
      homeApi.alertHitsToday(),
      homeApi.todos(),
      dashboardApi.marketStatus(),
    ])
    if (sc.status === 'fulfilled') setScan(sc.value.stocks || [])
    if (ov.status === 'fulfilled') setOverview(ov.value)
    if (dg.status === 'fulfilled') setDiag(dg.value)
    if (ht.status === 'fulfilled') setAlertHits(ht.value)
    if (td.status === 'fulfilled') setTodos(td.value.todos || [])
    if (ms.status === 'fulfilled') setMarketStatus(ms.value)
    if ([sc, ov, dg, ht, td, ms].some((r) => r.status === 'rejected')) {
      // 2026-08-17: 显示具体哪个接口失败(快车道 6 个接口任意失败)
      const failed = [sc, ov, dg, ht, td, ms]
        .map((r, i) => ({ r, name: ['盘中扫描', '首页概览', '组合体检', '今日告警', '待办', '市场状态'][i] }))
        .filter(x => x.r.status === 'rejected')
      failed.forEach(({ name, r }) => pushError(name, (r as PromiseRejectedResult).reason?.message || '服务不可用', load))
    }
    setLoading(false) // 首屏不再等基准/归因(要拉全持仓 K 线)
    setRefreshedAt(new Date())

    // 机会兜底:overview 无机会时再取(不挡首屏)
    if (ov.status !== 'fulfilled' || !ov.value.action_center?.opportunities?.length) {
      recommendationsApi
        .listStrategySignals({ status: 'active', limit: 5 })
        .then((r) => setOppFallback(r.items || []))
        .catch((err) => pushError('机会池兜底', err?.message || '服务不可用', load))
    }

    // 慢车道:基准/归因需拉全持仓 K 线(分钟级),独立加载,就绪后回填超额/归因。
    // 30s 自动刷新跳过(避免每分钟级重请求 + 图表反复"计算中"),仅首载/手动刷新触发
    if (!opts?.skipBench) loadBench()


    // 自选股列表(判断盘前标的是否已加自选)
    stocksApi.list().then((rows) => {
      setWatchSymbols(new Set((rows || []).map((s) => `${s.market}:${s.symbol}`)))
    }).catch((err) => pushError('自选股列表', err?.message || '服务不可用', load))
  }, [loadBench])

  // 盘前标的快捷加入自选
  const addToWatchlist = useCallback(async (symbol: string, name: string, market: string) => {
    try {
      const row = await stocksApi.create({ symbol, name, market })
      if (row?.id) {
        setWatchSymbols((prev) => new Set(prev).add(`${market}:${symbol}`))
      }
    } catch {
      // 已存在等错误静默
    }
  }, [])

  useEffect(() => {
    load()
    // 修复 2026-08-21: 不再自动弹 Onboarding(避免遮罩拦截点击)
    // 仅在用户主动点击"新手引导"按钮时打开
    // 历史兼容: 已看过的用户(localStorage 标志存在)依然不会被打扰
  }, [load])

  // 30s 自动刷新:页面可见才轮询(visibilitychange 隐藏时暂停,回到页面立即补一次);
  // busyRef 防请求叠加;慢车道(基准/归因)跳过,避免每分钟级重请求
  const autoRefreshBusy = useRef(false)
  useEffect(() => {
    const tick = () => {
      if (document.visibilityState !== 'visible' || autoRefreshBusy.current) return
      autoRefreshBusy.current = true
      load({ skipBench: true }).finally(() => {
        autoRefreshBusy.current = false
      })
    }
    const timer = setInterval(tick, 30_000)
    const onVisibility = () => {
      if (document.visibilityState === 'visible') tick()
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [load])

  const handleOnboardingComplete = () => {
    localStorage.setItem('panwatch_onboarding_completed', 'true')
    setShowOnboarding(false)
  }

  // 异动池/热榜等数据源返回 SH/SZ/BJ(交易所代码),行情接口统一按 A股 CN 处理
  const normalizeMarket = (m: string) => (['SH', 'SZ', 'BJ'].includes((m || '').toUpperCase()) ? 'CN' : m || 'CN')

  const openStock = (symbol: string, market: string, name = '', hasPosition = false) =>
    setModal({ open: true, symbol, market: normalizeMarket(market), name, hasPosition })

  // ========== PC 右键菜单 ==========
  const [stockCtxMenu, setStockCtxMenu] = useState<StockContextMenuState | null>(null)

  const openStockContextMenu = useCallback((e: React.MouseEvent, stock: StockContextTarget) => {
    e.preventDefault()
    e.stopPropagation()
    setStockCtxMenu({ x: e.clientX, y: e.clientY, stock: { ...stock, market: normalizeMarket(stock.market) } })
  }, [])

  const addToWatchlistFromMenu = useCallback((stock: StockContextTarget) => {
    void addToWatchlist(stock.symbol, stock.name || stock.symbol, stock.market || 'CN')
  }, [addToWatchlist])

  const viewDetailFromMenu = useCallback((stock: StockContextTarget) => {
    openStock(stock.symbol, stock.market || 'CN', stock.name || stock.symbol, stock.hasPosition)
  }, [openStock])

  const paperTradeFromMenu = useCallback(() => {
    navigate('/paper-trading')
  }, [navigate])

  const runAiReview = async () => {
    setAiReviewLoading(true)
    try {
      setAiReview(await portfolioApi.aiReview())
    } catch (e) {
      setAiReview({ content: e instanceof Error ? `AI 体检失败: ${e.message}` : 'AI 体检失败' })
    } finally {
      setAiReviewLoading(false)
    }
  }

  // 今日要紧事:持仓异动 + 触发的盯盘信号(有 AI 建议/告警优先)
  const urgent = useMemo(() => {
    const items = (scan || []).filter((s) => s.has_position || s.alert_type || s.suggestion?.should_alert)
    const weight = (s: DashboardMonitorStock) =>
      (s.suggestion?.should_alert ? 1000 : 0) + (s.has_position ? 500 : 0) + Math.abs(s.change_pct || 0)
    return items.sort((a, b) => weight(b) - weight(a)).slice(0, 8)
  }, [scan])

  const opportunities = useMemo(() => {
    const list = overview?.action_center?.opportunities?.length ? overview.action_center.opportunities : oppFallback
    return list.slice(0, 5)
  }, [overview, oppFallback])

  // 主力动向(P1-1): 自选股暗盘资金摘要; 后端降级/无自选股时为空数组 → 区块不渲染
  const mainForce = useMemo(() => overview?.main_force || [], [overview])
  const healthState: 'ok' | 'stale' | 'down' = !overview?.data_source_health?.tq_online
    ? 'down'
    : overview.data_source_health.data_is_today
      ? 'ok'
      : 'stale'

  // 今日必读候选(多源)→ 交 AI 策展(失败兜底原序)
  const candidates = useMemo<CurateCandidate[]>(() => {
    const out: CurateCandidate[] = []
    for (const h of alertHits) {
      out.push({ type: 'alert', symbol: h.symbol, name: h.name || h.symbol, market: h.market, signal: `触发提醒 ${h.rule_name}` })
    }
    for (const s of urgent) {
      out.push({
        type: s.has_position ? 'holding' : 'watch',
        symbol: s.symbol,
        name: s.name,
        market: s.market,
        change_pct: s.change_pct,
        signal: s.suggestion?.signal || (s.alert_type ? ALERT_LABEL[s.alert_type] || s.alert_type : ''),
      })
    }
    for (const a of diag?.alerts || []) out.push({ type: 'risk', name: '组合风险', market: '', signal: a })
    for (const o of opportunities.slice(0, 3)) {
      out.push({ type: 'opportunity', symbol: o.stock_symbol, name: o.stock_name || o.stock_symbol, market: o.stock_market, signal: o.signal || o.reason || o.action_label || '' })
    }
    return out
  }, [alertHits, urgent, diag, opportunities])

  const candKey = useMemo(
    () => candidates.map((c) => `${c.type}:${c.symbol}:${c.change_pct ?? ''}`).join('|'),
    [candidates],
  )

  useEffect(() => {
    if (candidates.length === 0) {
      setCurated([])
      return
    }
    let alive = true
    dashboardApi
      .curate(candidates)
      .then((r) => alive && setCurated(r.items || []))
      .catch(() => alive && setCurated([]))
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candKey])

  const feed = useMemo(() => {
    const rows = curated.length
      ? curated.map((ci) => (candidates[ci.index] ? { ...candidates[ci.index], why: ci.why } : null))
      : candidates.map((c) => ({ ...c, why: c.signal }))
    return rows.filter((x): x is CurateCandidate & { why: string } => !!x)
  }, [curated, candidates])

  const today = useMemo(() => {
    const d = new Date()
    const mm = String(d.getMonth() + 1).padStart(2, '0')
    const dd = String(d.getDate()).padStart(2, '0')
    return `${d.getFullYear()}-${mm}-${dd}`
  }, [])
  const hasHoldings = (diag?.position_count ?? 0) > 0
  const benchReady = bench && !bench.empty && bench.excess_return != null
  const hasWatchlist = (overview?.kpis?.watchlist_count ?? 0) > 0

  // 市场分布 stacked 条的分段(占比降序,过滤掉 0 占比)
  const marketSegs = useMemo(() => {
    if (!diag || diag.total_market_value <= 0) return []
    return Object.entries(diag.by_market)
      .map(([market, value]) => ({ market, pct: (value / diag.total_market_value) * 100 }))
      .filter((s) => s.pct > 0.05)
      .sort((a, b) => b.pct - a.pct)
  }, [diag])

  // 领涨/拖累双向条的归一基准(取全量 attribution 里最大贡献绝对值,双向对称)
  const attributionMaxAbs = useMemo(() => {
    if (attribution.length === 0) return 0
    return Math.max(...attribution.map((a) => Math.abs(a.contribution_pct)), 0.01)
  }, [attribution])

  // v0.4.6 KPI 带: 情绪阶段标签 + 主线 Top1(轻量拉取, 与下方完整卡错峰复用缓存)
  const phaseKpi = usePhaseLabel()
  const mainlineKpi = useMainlineTop1()


  return (
    <div className="page-container pb-10">
      {/* 顶部:标题 + 刷新 + 日期/市场状态 pills */}
      <div className="mb-3 flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <div className="flex items-center gap-2">
          <h1 className="text-[20px] font-bold tracking-tight text-foreground md:text-[22px]">今日该看什么</h1>
          <Button onClick={() => load()} disabled={loading} size="sm" variant="ghost" className="h-7 px-2">
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
          </Button>
          {/* 修复 2026-08-21: Onboarding 默认不弹, 加个低存在感入口让用户主动触发 */}
          <Button
            onClick={openOnboarding}
            size="sm"
            variant="ghost"
            className="h-7 px-2 text-[11px] text-muted-foreground"
            title="查看新手引导"
          >
            新手引导
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[11px]">
          {refreshedAt && <span className="text-muted-foreground">{formatHeaderTime(refreshedAt)}</span>}
          {marketStatus.map((m) => (
            <span key={m.code} className="inline-flex items-center gap-1.5 rounded-full bg-accent/40 px-2 py-0.5">
              <span className={`h-1.5 w-1.5 rounded-full ${m.is_trading ? 'bg-amber-500' : 'bg-muted-foreground/40'}`} />
              <span className="text-muted-foreground">{m.name}</span>
            </span>
          ))}
        </div>
      </div>

      {/* 核心接口失败横幅:失败≠空态,给出重试入口 */}
      {/* 2026-08-17: 数据源失败显式标识 — ErrorBanner 组件,展示具体哪个源挂了 */}
      <ErrorBanner errors={sourceErrors} onDismiss={(id) => setSourceErrors(prev => prev.filter(e => e.id !== id))} retryAll={load} />

            {/* 指数走势 pills */}
      <div className="mb-3 grid grid-cols-2 gap-2.5 md:grid-cols-3 lg:grid-cols-5">
        {loading && indices.length === 0
          ? Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="card p-2.5">
                <Skeleton className="h-2.5 w-16" />
                <Skeleton className="mt-1.5 h-4 w-14" />
                <Skeleton className="mt-2 h-6 w-full" />
              </div>
            ))
          : indices.slice(0, 5).map((ix) => (
          <button
            key={`${ix.market}:${ix.symbol}`}
            onClick={() => navigate(`/index/${ix.symbol}`)}
            className="card relative p-2.5 text-left hover:border-primary/40 transition-colors cursor-pointer"
          >
            <div className="flex items-start justify-between gap-1">
              <div className="min-w-0">
                <div className="truncate text-[11px] text-muted-foreground">{ix.name}</div>
                <div className="font-num text-[17px] font-semibold text-foreground tabular-nums">
                  {safeFixed(ix.current_price, 2)}
                </div>
              </div>
              <span className={`shrink-0 rounded px-1 py-0.5 font-num tabular-nums text-[10px] ${pctChipCls(ix.change_pct)}`}>
                {ix.change_pct != null ? pct(ix.change_pct) : '--'}
              </span>
            </div>
            {ix.spark && ix.spark.length >= 2 && (
              <div className="mt-1.5">
                <Sparkline data={ix.spark} height={26} className={moveColor(ix.change_pct)} />
              </div>
            )}
          </button>
        ))}
      </div>

      {/* v0.4.6 KPI 带(借鉴 TSP): 数字优先 6 格, 一眼看全市场状态 */}
      <KpiBand
        upCount={marketFlow?.up_count ?? null}
        downCount={marketFlow?.down_count ?? null}
        mainFlowYi={marketFlow?.total_main_flow ?? null}
        amountYi={marketFlow?.total_amount ?? null}
        phaseLabel={phaseKpi.label}
        phaseLoading={phaseKpi.loading}
        mainlineTop1={mainlineKpi.top}
        mainlineLoading={mainlineKpi.loading}
        limitUp={phaseKpi.limitUp}
        limitDown={phaseKpi.limitDown}
        sealRate={phaseKpi.sealRate}
      />

      {/* 主体:PC 工作台 3 列(要紧事3 | 体检6 | 机会3);次级 2 列(简报6 | 机会发现6)。1280px 以下回退 7/5-5/7 两行布局 */}
      {/* 反AI模板 P2:上方列表区已去卡片化, 工作台卡片区补 mt-3 维持呼吸感 */}
      <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-12">
        {/* 今日要紧事(左列,窄) */}
        <div className="card p-4 lg:col-span-7 xl:col-span-3">
          <div className="mb-2 flex items-center gap-2">
            <Activity className="h-4 w-4 text-primary" />
            <h2 className="text-sm font-semibold">今日要紧事</h2>
            <span className="text-[11px] text-muted-foreground">你的持仓/自选里今天该关注的</span>
            {feed.length > 0 && (
              <button
                type="button"
                onClick={() => setShareDigest(true)}
                className="ml-auto inline-flex items-center gap-1 text-[11px] text-muted-foreground transition-colors hover:text-primary"
                title="生成今日盯盘分享图"
              >
                <Share2 className="h-3.5 w-3.5" />
                分享图
              </button>
            )}
          </div>
          {loading && candidates.length === 0 ? (
            /* 首次加载骨架(扫描完成后替换为真实列表) */
            <SkeletonRows rows={6} />
          ) : candidates.length === 0 ? (
            todos.length > 0 ? (
              <div className="space-y-1.5 py-1">
                <div className="text-[11px] text-muted-foreground">今日暂无异动/触发 ✓ · 待办:</div>
                {todos.map((t, i) => (
                  <div
                    key={i}
                    className={`flex items-center gap-2 py-1 text-[12px] ${t.symbol ? 'cursor-pointer hover:bg-accent/30' : ''}`}
                    onClick={() => t.symbol && openStock(t.symbol, t.market || 'CN', '')}
                    onContextMenu={(e) => {
                      if (!t.symbol) return
                      openStockContextMenu(e, { symbol: t.symbol, name: t.symbol, market: t.market || 'CN', hasPosition: false })
                    }}
                  >
                    <span className="shrink-0 rounded bg-amber-500/15 px-1 text-[9px] text-amber-600">
                      {t.type === 'no_alert' ? '加提醒' : '将到期'}
                    </span>
                    <span className="truncate">{t.message}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="py-6 text-center text-[12px] text-muted-foreground">今日暂无明显异动或触发信号 ✓</div>
            )
          ) : (
            <div className="divide-y divide-border/40">
              {feed.map((it, i) => {
                const badge = FEED_BADGE[it.type] || { label: it.type, cls: 'bg-accent text-muted-foreground' }
                return (
                  <div
                    key={i}
                    className={`flex items-center gap-3 py-2 ${it.symbol ? 'cursor-pointer hover:bg-accent/30' : ''}`}
                    onClick={() => it.symbol && openStock(it.symbol, it.market || 'CN', it.name || '')}
                    onContextMenu={(e) => {
                      if (!it.symbol) return
                      openStockContextMenu(e, {
                        symbol: it.symbol,
                        name: it.name || it.symbol,
                        market: it.market || 'CN',
                        hasPosition: it.type === 'holding',
                      })
                    }}
                  >
                    <span className={`shrink-0 rounded px-1 text-[9px] ${badge.cls}`}>{badge.label}</span>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13px] font-medium">{it.name || it.symbol}</div>
                      {it.why && <div className="truncate text-[11px] text-muted-foreground">{it.why}</div>}
                    </div>
                    <span className={`shrink-0 rounded px-1.5 py-0.5 font-mono text-[11px] ${pctChipCls(it.change_pct)}`}>
                      {it.change_pct != null ? pct(it.change_pct) : '--'}
                    </span>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* 组合体检(中列,宽) */}
        <div className="card p-4 lg:col-span-5 xl:col-span-6">
          <div className="mb-2 flex items-center gap-2">
            <ShieldAlert className="h-4 w-4 text-primary" />
            <h2 className="text-sm font-semibold">组合体检</h2>
            {(benchReady || hasHoldings) && (
              /* v0.4.7: 两个分享入口合并为一个下拉, 减少头部按钮拥挤 */
              <details className="relative ml-auto">
                <summary className="inline-flex cursor-pointer list-none items-center gap-1 text-[11px] text-muted-foreground transition-colors hover:text-primary">
                  <Share2 className="h-3.5 w-3.5" />
                  分享 ▾
                </summary>
                <div className="absolute right-0 z-20 mt-1 w-28 rounded-lg border border-border/60 bg-card py-1 shadow-lg">
                  {benchReady && (
                    <button
                      type="button"
                      onClick={(e) => { setShareBench(true); (e.currentTarget.closest('details') as HTMLDetailsElement).open = false }}
                      className="block w-full px-3 py-1.5 text-left text-[12px] hover:bg-accent/30"
                    >
                      成绩单图
                    </button>
                  )}
                  {hasHoldings && (
                    <button
                      type="button"
                      onClick={(e) => { setShareDiag(true); (e.currentTarget.closest('details') as HTMLDetailsElement).open = false }}
                      className="block w-full px-3 py-1.5 text-left text-[12px] hover:bg-accent/30"
                    >
                      体检图
                    </button>
                  )}
                </div>
              </details>
            )}
          </div>
          {loading && !diag ? (
            /* 首次加载骨架 */
            <SkeletonRows rows={4} />
          ) : !hasHoldings ? (
            <div className="flex flex-col items-center gap-2 py-5 text-center">
              <div className="text-[12px] text-muted-foreground">{loading ? '加载中…' : '暂无持仓'}</div>
              {!loading && (
                <Button variant="outline" size="sm" className="h-7 text-[12px]" onClick={() => navigate('/portfolio')}>
                  去添加持仓
                </Button>
              )}
            </div>
          ) : (
            <div className="space-y-3 text-[12px]">
              {/* 图例行:色块 + 我的组合/基准收益 + 超额 chip */}
              <div className="flex flex-wrap items-center justify-between gap-2 text-[11px]">
                <div className="flex items-center gap-3">
                  <span className="flex items-center gap-1.5">
                    <span className="h-[3px] w-3.5 rounded-full bg-primary" />
                    <span className="text-muted-foreground">我的组合 {benchReady ? pct(bench!.portfolio_return) : ''}</span>
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span className="h-0 w-3.5 border-t-[1.5px] border-dashed border-muted-foreground/70" />
                    <span className="text-muted-foreground">
                      {bench?.benchmark_label || '沪深300'} {benchReady ? pct(bench!.benchmark_return) : ''}
                    </span>
                  </span>
                </div>
                {benchReady && (
                  <span className={`rounded px-1.5 py-0.5 font-mono ${pctChipCls(bench!.excess_return)}`}>
                    超额 {pct(bench!.excess_return)}
                  </span>
                )}
              </div>

              {/* 净值 vs 基准双线图:loading/ready/empty/error 四态,不再永远"计算中" */}
              {benchState === 'ready' && bench?.curve && bench.curve.length >= 2 ? (
                <BenchChart curve={bench.curve} />
              ) : (
                <div className="flex h-[150px] flex-col items-center justify-center gap-2 rounded-lg bg-accent/10 text-[11px] text-muted-foreground">
                  {benchState === 'loading' && <span>基准对比计算中…(需拉全部持仓 K 线,约 1 分钟)</span>}
                  {benchState === 'empty' && <span>{bench?.reason || '数据不足,暂无法计算基准对比'}</span>}
                  {benchState === 'error' && (
                    <>
                      <span>基准对比加载失败(超时或网络异常)</span>
                      <button
                        type="button"
                        onClick={loadBench}
                        className="rounded border border-border/60 px-2.5 py-1 text-[11px] text-primary hover:bg-accent/30"
                      >
                        重试
                      </button>
                    </>
                  )}
                </div>
              )}

              <div className="flex justify-between">
                <span className="text-muted-foreground">持仓 {diag!.position_count} 只 · 最大单仓</span>
                <span className={`font-mono ${diag!.max_weight >= 0.4 ? 'text-amber-600' : ''}`}>
                  {(diag!.max_weight * 100).toFixed(0)}%
                </span>
              </div>

              {/* 市场分布:stacked 单条 */}
              {marketSegs.length > 0 && (
                <div>
                  <div className="flex h-2 overflow-hidden rounded-full bg-accent/30">
                    {marketSegs.map((seg, i) => (
                      <div
                        key={seg.market}
                        className={`h-full ${MARKET_BAR_CLS[seg.market] || 'bg-muted-foreground/50'}`}
                        style={{ width: `${seg.pct}%`, marginRight: i < marketSegs.length - 1 ? 2 : 0 }}
                      />
                    ))}
                  </div>
                  <div className="mt-1 text-[10.5px] text-muted-foreground">
                    {marketSegs.map((seg) => `${seg.market} ${seg.pct.toFixed(0)}%`).join(' · ')}
                  </div>
                </div>
              )}

              {/* 领涨/拖累:双向条 */}
              {attribution.length > 1 &&
                [
                  { label: '领涨', item: attribution[0] },
                  { label: '拖累', item: attribution[attribution.length - 1] },
                ].map(({ label, item }) => {
                  const w = Math.min(50, (Math.abs(item.contribution_pct) / attributionMaxAbs) * 50)
                  const positive = item.contribution_pct >= 0
                  return (
                    <div key={label} className="flex items-center gap-2">
                      <span className="w-8 shrink-0 text-[10px] text-muted-foreground">{label}</span>
                      <div className="relative h-1.5 flex-1 rounded-full bg-accent/30">
                        <div className="absolute inset-y-0 left-1/2 w-px bg-border" />
                        <div
                          className={`absolute inset-y-0 rounded-full ${positive ? 'bg-stock-up' : 'bg-stock-down'}`}
                          style={
                            positive
                              ? { left: '50%', width: `${w}%` }
                              : { right: '50%', width: `${w}%` }
                          }
                        />
                      </div>
                      <span className="w-28 shrink-0 truncate text-right text-[11px]">
                        {item.name} <span className={`font-mono ${moveColor(item.contribution_pct)}`}>{pct(item.contribution_pct)}</span>
                      </span>
                    </div>
                  )
                })}

              {diag!.alerts.length > 0 ? (
                <div className="space-y-1 pt-1">
                  {diag!.alerts.map((a, i) => (
                    <div key={i} className="flex items-start gap-1 text-[11px] text-amber-600">
                      <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                      <span>{a}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="pt-1 text-[11px] text-green-700">✓ 集中度/分布未见明显风险</div>
              )}
              <button
                type="button"
                onClick={runAiReview}
                disabled={aiReviewLoading}
                className="mt-1 w-full rounded border border-border/60 py-1 text-[11px] text-primary hover:bg-accent/30 disabled:opacity-60"
              >
                {aiReviewLoading ? 'AI 体检中…' : 'AI 体检报告'}
              </button>
              {aiReview?.content && (
                <div className="prose prose-sm dark:prose-invert mt-1 max-w-none break-words text-[12px] [&_p]:my-1 [&_ul]:my-1">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{aiReview.content}</ReactMarkdown>
                </div>
              )}
            </div>
          )}
        </div>

        {/* 机会精选(右列,窄) */}
        <div className="card p-4 lg:col-span-5 xl:col-span-3">
          {/* 反AI模板 P2:机会非高频扫描区, 去掉图标 — 只给要紧事/体检保留扫描图标 */}
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold">机会精选</h2>
            <button
              type="button"
              className="text-[11px] text-muted-foreground hover:text-foreground"
              onClick={() => navigate('/opportunities')}
            >
              进入机会页
            </button>
          </div>
          {opportunities.length === 0 ? (
            <div className="py-6 text-center text-[12px] text-muted-foreground">{loading ? '加载中…' : '暂无活跃机会信号'}</div>
          ) : (
            <div className="divide-y divide-border/40">
              {opportunities.slice(0, 3).map((o) => {
                return (
                  <div
                    key={`${o.stock_market}:${o.stock_symbol}`}
                    className="flex cursor-pointer items-center gap-2 py-2 hover:bg-accent/30"
                    onClick={() => openStock(o.stock_symbol, o.stock_market, o.stock_name || o.stock_symbol)}
                    onContextMenu={(e) =>
                      openStockContextMenu(e, {
                        symbol: o.stock_symbol,
                        name: o.stock_name || o.stock_symbol,
                        market: o.stock_market || 'CN',
                        hasPosition: false,
                      })
                    }
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="truncate text-[13px] font-medium">{o.stock_name || o.stock_symbol}</span>
                        {o.action_label && <span className="rounded bg-primary/10 px-1 text-[9px] text-primary">{o.action_label}</span>}
                      </div>
                      {(o.signal || o.reason) && <div className="truncate text-[11px] text-muted-foreground">{o.signal || o.reason}</div>}
                    </div>
                    <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                  </div>
                )
              })}
            </div>
          )}
        </div>

{/* 最新报告(v0.4.6 降级): 右栏紧凑列表, 完整存档在报告页 */}
        <div className="card p-4 lg:col-span-5 xl:col-span-3">
          <div className="mb-1.5 flex items-baseline gap-2">
            <h2 className="text-[13px] font-semibold">最新报告</h2>
            <span className="text-[10px] text-muted-foreground">盘前/盘后</span>
            <button
              type="button"
              onClick={() => navigate('/reports')}
              className="ml-auto shrink-0 text-[11px] text-muted-foreground transition-colors hover:text-primary"
            >
              全部 →
            </button>
          </div>
          {reportsLoading && reports.length === 0 ? (
            <SkeletonRows rows={3} />
          ) : reports.length === 0 ? (
            <div className="py-4 text-center text-[12px] text-muted-foreground">
              报告生成后自动出现
            </div>
          ) : (
            <div className="divide-y divide-border/40">
              {reports.slice(0, 4).map((r) => (
                <button
                  key={`${r.job_id}:${r.file}`}
                  type="button"
                  onClick={() => navigate('/reports')}
                  className="flex w-full items-center gap-2 py-1.5 text-left transition-colors hover:bg-accent/30"
                >
                  <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[12px] font-medium">{r.title_preview || r.file}</div>
                    <div className="truncate text-[10px] text-muted-foreground">{formatReportTime(r.mtime_iso)}</div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

                {/* 简报已移除(v0.4.7): 与报告中心重复, 入口保留在最新报告卡 */}

{/* 机会发现(次级,右;1280px 以下整行) */}
        <div className="lg:col-span-12 xl:col-span-6">
          <DiscoveryPanel monitorStocks={scan} onOpenStock={openStock} />
        </div>

        {/* 主力动向(P1-1): 数据源健康指示灯 + 自选股暗盘资金卡片网格。
            首载骨架; 空数组/后端降级缺失 → 不渲染(不占位) */}
        {loading && !overview ? (
          <div className="card p-4 lg:col-span-12">
            <div className="mb-2 flex items-center gap-2">
              <h2 className="text-sm font-semibold">主力动向</h2>
              <span className="text-[11px] text-muted-foreground">暗盘资金 · 主力意图</span>
            </div>
            <SkeletonRows rows={2} />
          </div>
        ) : mainForce.length > 0 ? (
          <div className="card p-4 lg:col-span-12">
            <div className="mb-2 flex items-center gap-2">
              <h2 className="text-sm font-semibold">主力动向</h2>
              <span className="text-[11px] text-muted-foreground">暗盘资金 · 自选股前 8 只</span>
              <span
                className="ml-auto inline-flex items-center gap-1.5 text-[11px] text-muted-foreground"
                title={`TQ 在线: ${overview?.data_source_health?.tq_online ? '是' : '否'} · 行情数据日期: ${overview?.data_source_health?.hq_date || '--'}`}
              >
                <span className={`h-2 w-2 rounded-full ${HEALTH_META[healthState].dot}`} />
                {HEALTH_META[healthState].label}
              </span>
            </div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">
              {mainForce.map((m) => (
                <button
                  key={m.code || m.name}
                  type="button"
                  onClick={() => m.code && openStock(m.code, 'CN', m.name || '')}
                  className="rounded-lg border border-border/50 bg-accent/10 p-2.5 text-left transition-colors hover:border-primary/40"
                >
                  <div className="flex items-center justify-between gap-1">
                    <span className="truncate text-[13px] font-medium">{m.name || m.code || '--'}</span>
                    <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{m.code}</span>
                  </div>
                  <div className="mt-1 flex items-baseline justify-between gap-1">
                    <span className="text-[10px] text-muted-foreground">暗盘净额</span>
                    <span className={`font-num text-[14px] font-semibold tabular-nums ${moveColor(safeNum(m.dark_net))}`}>
                      {formatYuan(m.dark_net)}
                    </span>
                  </div>
                  <div className="mt-1 line-clamp-2 text-[11px] leading-4 text-muted-foreground">
                    {m.intent || (m.data_status === 'insufficient' ? '逐笔数据不足, 暂不判定意图' : '暂无意图信号')}
                  </div>
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </div>

      {/* 情绪周期6阶段 + 主线识别(TSP 口径): C 位主区 */}
      <div id="market-phase-anchor" className="mt-5 grid grid-cols-1 gap-x-4 gap-y-3 lg:grid-cols-3">
        <MarketPhaseCard />
        <MarketMainlineCard />
        {/* v0.4.7: 市场温度仪表盘(数据复用 phase 接口) */}
        <PhaseGaugeCard />
      </div>

      {/* 大盘资金流(东财两市主力净流入, 对齐同花顺APP) */}
      {/* 反AI模板 P2:次要列表区去卡片化 — 去掉盒子, 顶部 1px hairline + 留白, 标题/指标直排 */}
      {marketFlow && (
        <div className="mt-5 border-t border-border/60 pt-3">
          <div className="flex items-baseline gap-2">
            <span className="text-[13px] font-semibold">大盘资金流</span>
            <span className="text-[10px] text-muted-foreground">东财 · 两市主力</span>
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px]">
              <span className="text-muted-foreground">主力净流入
                <b className={`font-mono text-[15px] font-semibold ${(marketFlow.total_main_flow ?? 0) >= 0 ? 'text-stock-up' : 'text-stock-down'}`}>
                  <span className="text-muted-foreground">{safeFlow(marketFlow.total_main_flow)}</span>
                </b>
              </span>
              <span className="text-muted-foreground">成交额 <b className="font-mono">{safeFixed(marketFlow.total_amount, 0, '0')}亿</b></span>
              <span className="text-muted-foreground">涨 <b className="text-stock-up font-mono">{marketFlow.up_count ?? '--'}</b>
                <span className="mx-1">/</span>跌 <b className="text-stock-down font-mono">{marketFlow.down_count ?? '--'}</b></span>
              <span className="text-muted-foreground">沪 <b className="font-mono">{safeFixed(marketFlow.sh_flow, 1)}亿</b>
                <span className="mx-1">/</span>深 <b className="font-mono">{safeFixed(marketFlow.sz_flow, 1)}亿</b></span>
            </div>

          {/* v0.4.7: 日内主力净流入面积图(30s 快照序列) */}
          <FlowHistoryChart />

          {/* 板块资金明细: 流入榜 / 流出榜 */}
          {(marketFlow.inflow_boards?.length || marketFlow.outflow_boards?.length) ? (
            <div className="mt-2 grid grid-cols-1 md:grid-cols-2 gap-3">
              {marketFlow.inflow_boards?.length ? (
                <div>
                  <div className="mb-1 text-[11px] font-semibold text-stock-up">资金流入板块</div>
                  <div className="space-y-0.5">
                    {(() => {
                      const maxIn = Math.max(...(marketFlow.inflow_boards?.map(x => x.net_inflow) || [1]), 0.01)
                      return marketFlow.inflow_boards.map((b, bi) => (
                        <div key={b.name} className="relative flex justify-between overflow-hidden rounded text-[11px]">
                          <div className="absolute inset-y-0 left-0 bg-stock-up/10 transition-all duration-500" style={{ width: `${Math.min(100, (b.net_inflow / maxIn) * 100)}%`, transitionDelay: `${bi * 60}ms` }} />
                          <span className="relative z-10 truncate px-1 text-muted-foreground">{b.name}</span>
                          <span className="relative z-10 font-mono text-stock-up">+{safeFixed(b.net_inflow, 1)}亿</span>
                        </div>
                      ))
                    })()}
                  </div>
                </div>
              ) : null}
              {marketFlow.outflow_boards?.length ? (
                <div>
                  <div className="mb-1 text-[11px] font-semibold text-stock-down">资金流出板块</div>
                  <div className="space-y-0.5">
                    {(() => {
                      const maxOut = Math.max(...(marketFlow.outflow_boards?.map(x => Math.abs(x.net_inflow)) || [1]), 0.01)
                      return marketFlow.outflow_boards.map((b, bi) => (
                        <div key={b.name} className="relative flex justify-between overflow-hidden rounded text-[11px]">
                          <div className="absolute inset-y-0 left-0 bg-stock-down/10 transition-all duration-500" style={{ width: `${Math.min(100, (Math.abs(b.net_inflow) / maxOut) * 100)}%`, transitionDelay: `${bi * 60}ms` }} />
                          <span className="relative z-10 truncate px-1 text-muted-foreground">{b.name}</span>
                          <span className="relative z-10 font-mono text-stock-down">{safeFixed(b.net_inflow, 1)}亿</span>
                        </div>
                      ))
                    })()}
                  </div>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      )}

      {/* 异动池(东财) | 热榜(同花顺):并排双列,移动端堆叠;独立加载,任一失败静默不影响首页 */}
      {/* 反AI模板 P2:异动/热榜同为列表感区块, 去卡片化 — 与大盘资金流一致 hairline 分隔 */}
      <div className="mt-5 grid grid-cols-1 gap-x-6 md:grid-cols-2">
        {/* 异动池(东财) — v0.4.7 与涨跌分布并排 */}
        <div className="border-t border-border/60 pt-2.5">
          <div className="mb-2 flex items-baseline gap-2">
            <h2 className="text-[13px] font-semibold">异动池</h2>
            <span className="text-[10px] text-muted-foreground">东财异动</span>
            {anomaliesLoading && anomalies.length === 0 && (
              <RefreshCw className="ml-auto h-3 w-3 animate-spin self-center text-muted-foreground" />
            )}
          </div>
          {anomaliesLoading && anomalies.length === 0 ? (
            <div className="space-y-2 py-1">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : anomalies.length === 0 ? (
            <div className="py-6 text-center text-[12px] text-muted-foreground">暂无异动数据</div>
          ) : (
            <div className="divide-y divide-border/40">
              {anomalies.map((a, i) => {
                const sym = a.symbol || a.code || ''
                const dev = a.deviation ?? a.deviation_pct ?? null
                const days = a.deviation_days ?? a.days
                const rule = a.rule || a.reason || ''
                return (
                  <button
                    key={`${sym}-${i}`}
                    type="button"
                    onClick={() => sym && openStock(sym, a.market || 'CN', a.name || '')}
                    onContextMenu={(e) => {
                      if (!sym) return
                      openStockContextMenu(e, { symbol: sym, name: a.name || sym, market: a.market || 'CN', hasPosition: false })
                    }}
                    className="flex w-full items-center gap-2.5 py-1.5 text-left transition-colors hover:bg-accent/30"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="truncate text-[13px] font-medium">{a.name || sym || '--'}</span>
                        <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{sym}</span>
                        {a.is_today && (
                          <span className="shrink-0 rounded bg-amber-500/15 px-1 text-[9px] text-amber-600">当日</span>
                        )}
                      </div>
                      {rule && <div className="truncate text-[11px] text-muted-foreground">{rule}</div>}
                    </div>
                    {dev != null && (
                      <span className="shrink-0 text-[10px] text-muted-foreground">
                        偏离 {pct(dev)}
                        {days ? ` · ${days}天` : ''}
                      </span>
                    )}
                    <span className={`shrink-0 rounded px-1.5 py-0.5 font-mono text-[11px] ${pctChipCls(a.change_pct)}`}>
                      {a.change_pct != null ? pct(a.change_pct) : '--'}
                    </span>
                  </button>
                )
              })}
            </div>
          )}
        </div>

        {/* v0.4.7: 全市场涨跌分布(双向柱) */}
        <div className="border-t border-border/60 pt-2.5">
          <div className="mb-2 flex items-baseline gap-2">
            <h2 className="text-[13px] font-semibold">涨跌分布</h2>
            <span className="text-[10px] text-muted-foreground">全A · 9档</span>
          </div>
          <BreadthDistributionChart />
        </div>
      </div>
      {/* 热榜已移除(v0.4.7): 与发现页重复 */}

      <StockInsightModal
        open={modal.open}
        onOpenChange={(o) => setModal((m) => ({ ...m, open: o }))}
        symbol={modal.symbol}
        market={modal.market}
        stockName={modal.name}
        hasPosition={modal.hasPosition}
      />

      {/* 分享卡:模拟盘成绩单(vs 基准) */}
      {shareBench && bench && (
        <BenchmarkShareCard open={shareBench} onClose={() => setShareBench(false)} bench={bench} />
      )}

      {/* 分享卡:组合体检(脱敏,无金额) */}
      {shareDiag && diag && (
        <DiagnosticsShareCard
          open={shareDiag}
          onClose={() => setShareDiag(false)}
          diag={diag}
          excessReturn={benchReady ? bench!.excess_return : null}
          benchmarkLabel={bench?.benchmark_label}
        />
      )}

      {/* 分享卡:今日盯盘 digest */}
      <DigestShareCard
        open={shareDigest}
        onClose={() => setShareDigest(false)}
        date={today}
        items={feed.map((it) => ({
          type: it.type,
          name: it.name,
          symbol: it.symbol,
          why: it.why,
          change_pct: it.change_pct ?? null,
        }))}
      />

      {/* PC 右键菜单 */}
      <StockContextMenu
        menu={stockCtxMenu}
        onClose={() => setStockCtxMenu(null)}
        onAddWatchlist={addToWatchlistFromMenu}
        onViewDetail={viewDetailFromMenu}
        onPaperTrade={paperTradeFromMenu}
      />

      <Onboarding open={showOnboarding} onComplete={handleOnboardingComplete} hasStocks={hasWatchlist} />
    </div>
  )
}


/** v0.4.7: 市场温度卡 — 拉 /market/phase 喂 SentimentGauge(30s 轮询) */
function PhaseGaugeCard() {
  const [phaseData, setPhaseData] = useState<{ phase: string; label: string; max_height: number | null; promo_rate: number | null; seal_rate: number | null } | null>(null)
  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const res = await fetchAPI<{ available: boolean; current: { phase: string; label: string; max_height: number | null; promo_rate: number | null; seal_rate: number | null } | null }>('/market/phase')
        if (alive && res?.available && res.current) setPhaseData(res.current)
      } catch { /* 静默 */ }
    }
    void load()
    const t = window.setInterval(() => void load(), 30000)
    return () => { alive = false; window.clearInterval(t) }
  }, [])
  return (
    <div className="rounded-xl border border-border/50 bg-card p-3">
      <div className="mb-1 flex items-baseline gap-2">
        <span className="text-[13px] font-semibold">市场温度</span>
        <span className="text-[10px] text-muted-foreground">高度×15 + 晋级率×40 + 封板率×45</span>
      </div>
      {phaseData ? (
        <SentimentGauge
          phase={phaseData.phase}
          metrics={{ max_height: phaseData.max_height, promo_rate: phaseData.promo_rate, seal_rate: phaseData.seal_rate }}
        />
      ) : (
        <div className="flex h-[140px] items-center justify-center text-[11px] text-muted-foreground">阶段数据同步中…</div>
      )}
    </div>
  )
}

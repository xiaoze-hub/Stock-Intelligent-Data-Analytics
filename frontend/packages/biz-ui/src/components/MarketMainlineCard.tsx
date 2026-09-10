import { useCallback, useEffect, useRef, useState } from 'react'
import { RefreshCw, AlertTriangle, ChevronDown, ChevronRight, Layers, Crown } from 'lucide-react'
import { fetchAPI } from '@panwatch/api'
import AsOfBadge from './AsOfBadge'

/**
 * 市场主线识别卡片 (2026-08-24, v0.3.0)
 * 数据 GET /api/market/mainline (fetchAPI 自动补 /api 前缀)。
 *
 * 设计:
 *   - 顶部 Top10 主线横向列表(默认全收): 每行展示 主线名 / 主线分条形(横条宽=相对 100%)
 *     / 涨停家数 / 最高板 / 龙头股名。
 *   - 点击行展开该主线成分股(小标签流式排列; 标签上叠 days chip 表示连板数)。
 *   - 30s 轮询刷新(前端节奏, 后端自带 60s 进程内缓存)。
 *   - 数据风格对齐 DarkFlowCards / MainFlowCompareCard: rounded-xl + border-border/50 + 紧凑。
 *
 * 颜色:
 *   - 涨停数 ≥5 → 主线条染玫瑰色; ≥3 → 青色; <3 不入榜(走单独底部 chip)。
 *   - 龙头股用 Crown 图标 + 琥珀色突出。
 */

export interface MainlineConstituent {
  code: string
  name: string
  days: number
  amount: number
}

export interface MainlineGroup {
  name: string
  limit_up_count: number
  ge2_count: number
  max_boards: number
  boards_sum: number
  rungs: number
  score: number | null
  leader: {
    code: string
    name: string
    days: number
    amount: number
  }
  constituents: MainlineConstituent[]
}

export interface MainlineFilterStats {
  broad_filtered: number
  below_min: number
  ranked: number
}

export interface MainlineResp {
  total_groups: number
  ranked_groups: MainlineGroup[]
  unranked?: MainlineGroup[]
  filter_stats?: MainlineFilterStats
  note?: string
  cache_ts?: number
  /** P3: 计算口径日期(当日榜 YYYY-MM-DD) */
  asof?: string | null
}

/** 元 → 亿/万 紧凑显示(带符号), NaN → '--' */
function fmtYi(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '--'
  const abs = Math.abs(v)
  const sign = v > 0 ? '+' : v < 0 ? '-' : ''
  if (abs >= 1e8) return `${sign}${(abs / 1e8).toFixed(2)}亿`
  return `${sign}${(abs / 1e4).toFixed(0)}万`
}

/** 主线分档位颜色: ≥75 强 / ≥50 中 / 其余弱 */
function scoreTone(score: number | null | undefined): { bar: string; text: string; tag: string } {
  if (score == null || !Number.isFinite(score)) {
    return { bar: 'bg-white/15', text: 'text-muted-foreground', tag: '未入榜' }
  }
  if (score >= 75) return { bar: 'bg-rose-500/80', text: 'text-rose-500', tag: '强主线' }
  if (score >= 50) return { bar: 'bg-cyan-500/80', text: 'text-cyan-500', tag: '次主线' }
  return { bar: 'bg-slate-500/60', text: 'text-slate-400', tag: '弱主线' }
}

/** 单主线行 */
function MainlineRow({
  g,
  expanded,
  onToggle,
}: {
  g: MainlineGroup
  expanded: boolean
  onToggle: () => void
}) {
  const tone = scoreTone(g.score)
  const score = g.score ?? 0
  const leaderName = g.leader?.name || '--'
  const leaderDays = g.leader?.days ?? 0

  return (
    <div className="rounded-lg border border-border/40 bg-accent/10 overflow-hidden">
      {/* 主行: 可点击展开 */}
      <button
        type="button"
        onClick={onToggle}
        className="w-full px-2.5 py-2 flex items-center gap-2 hover:bg-accent/30 transition-colors text-left"
      >
        <span className="text-muted-foreground shrink-0">
          {expanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[12.5px] font-semibold text-foreground truncate">{g.name}</span>
            <span className={`text-[10px] tabular-nums font-mono ${tone.text}`}>
              {g.score != null ? g.score.toFixed(1) : '--'}
            </span>
          </div>
          {/* 主线分条形: width = score%, 底色 + 主色叠加 */}
          <div className="mt-1 h-1.5 rounded-full bg-white/10 overflow-hidden">
            <div
              className={`h-full ${tone.bar} transition-all duration-300`}
              style={{ width: `${Math.max(2, Math.min(100, score))}%` }}
            />
          </div>
        </div>
        {/* 关键指标: 涨停数 / 最高板 */}
        <div className="flex items-center gap-2.5 shrink-0 text-[10px] tabular-nums">
          <span className="text-rose-600 dark:text-rose-400 font-mono" title="涨停家数">
            {g.limit_up_count}家
          </span>
          <span className="text-amber-600 dark:text-amber-400 font-mono" title="最高板">
            {g.max_boards}板
          </span>
          <span className="text-muted-foreground font-mono" title="梯队档位">
            {g.rungs}档
          </span>
        </div>
        {/* 龙头 */}
        <div className="hidden md:flex items-center gap-1 shrink-0 max-w-[100px]">
          <Crown className="w-3 h-3 text-amber-500 shrink-0" />
          <span className="text-[10.5px] text-amber-600 dark:text-amber-400 truncate" title={`龙头 ${leaderName} ${leaderDays}板`}>
            {leaderName}
            {leaderDays > 1 ? <span className="ml-0.5 text-muted-foreground">{leaderDays}B</span> : null}
          </span>
        </div>
      </button>

      {/* 展开区: 成分股标签流 */}
      {expanded && g.constituents.length > 0 ? (
        <div className="px-2.5 pb-2 pt-1 border-t border-border/30">
          <div className="flex items-center gap-1.5 mb-1.5 text-[10px] text-muted-foreground">
            <Layers className="w-3 h-3" />
            <span>成分股 {g.constituents.length} 只</span>
            <span className="text-muted-foreground/60">·</span>
            <span>成交额 {fmtYi(g.constituents.reduce((s, c) => s + (c.amount || 0), 0))}</span>
          </div>
          <div className="flex flex-wrap gap-1">
            {g.constituents.map((c) => {
              const isLeader = c.code && g.leader?.code && c.code === g.leader.code
              return (
                <span
                  key={c.code || c.name}
                  className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10.5px] transition-colors ${
                    isLeader
                      ? 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400'
                      : 'border-border/50 bg-card text-foreground/90 hover:border-primary/40'
                  }`}
                  title={`${c.name}(${c.code}) ${c.days}板 成交${fmtYi(c.amount)}`}
                >
                  <span className="font-medium truncate max-w-[64px]">{c.name}</span>
                  {c.days > 1 ? (
                    <span className="text-[9px] font-mono text-rose-600 dark:text-rose-400 shrink-0">
                      {c.days}B
                    </span>
                  ) : null}
                </span>
              )
            })}
          </div>
        </div>
      ) : null}
    </div>
  )
}

export default function MarketMainlineCard() {
  const [data, setData] = useState<MainlineResp | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)
  const [expandedKey, setExpandedKey] = useState<string | null>(null)
  const mountedRef = useRef(true)

  const load = useCallback(async () => {
    setError('')
    try {
      const res = await fetchAPI<MainlineResp>('/market/mainline', {
        cacheMode: 'reload', // 实时, 跳过 GET 缓存
      })
      if (mountedRef.current) {
        setData(res)
        setUpdatedAt(new Date())
      }
    } catch (e) {
      if (mountedRef.current) setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      if (mountedRef.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    mountedRef.current = true
    setLoading(true)
    void load()
    const timer = window.setInterval(() => void load(), 30000) // 30s 刷新(后端 60s 缓存, 兜底节奏)
    return () => {
      mountedRef.current = false
      window.clearInterval(timer)
    }
  }, [load])

  if (loading && !data) {
    return (
      <div className="rounded-xl border border-border/50 bg-card p-3 animate-pulse">
        <div className="h-[20px] w-1/3 bg-accent/30 rounded mb-2" />
        <div className="space-y-1.5">
          {[0, 1, 2, 3, 4].map((i) => (
            <div key={i} className="h-[36px] bg-accent/20 rounded" />
          ))}
        </div>
      </div>
    )
  }

  const ranked = data?.ranked_groups ?? []
  const unranked = data?.unranked ?? []
  const stats = data?.filter_stats
  const note = data?.note
  const totalGroups = data?.total_groups ?? 0

  if (ranked.length === 0) {
    return (
      <div className="rounded-xl border border-border/50 bg-card p-3">
        <div className="flex items-center justify-between gap-2 mb-2">
          <div className="text-[13px] font-semibold text-foreground">🎯 市场主线</div>
          {updatedAt && (
            <span className="text-[10px] text-muted-foreground font-mono">
              {updatedAt.toLocaleTimeString('zh-CN', { hour12: false })}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 text-[12px] text-muted-foreground">
          <AlertTriangle className="w-3.5 h-3.5" />
          <span>{error || note || '暂无主线数据(非交易日/涨停池为空)'}</span>
        </div>
      </div>
    )
  }

  // 前 10 为主展示, 后续可滚动查看(任务规格 Top10)
  const top = ranked.slice(0, 10)
  const rest = ranked.slice(10)

  return (
    <div className="rounded-xl border border-border/50 bg-card p-3">
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="flex items-center gap-2">
          <div className="text-[13px] font-semibold text-foreground">🎯 市场主线</div>
          <span className="text-[10px] text-muted-foreground">
            Top {top.length}
            {rest.length > 0 ? ` / ${ranked.length}` : ''}
            {stats ? (
              <span className="ml-1.5 text-muted-foreground/70">
                (过滤 {stats.broad_filtered} 宽基 / 未入榜 {stats.below_min} / 共 {totalGroups} 题材)
              </span>
            ) : null}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <AsOfBadge asof={data?.asof} />
          {updatedAt && (
            <span className="text-[10px] text-muted-foreground font-mono">
              {updatedAt.toLocaleTimeString('zh-CN', { hour12: false })}
            </span>
          )}
          <button
            type="button"
            title="刷新"
            onClick={() => {
              setLoading(true)
              void load()
            }}
            className="text-muted-foreground hover:text-foreground transition-colors"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* 错误细条: 与 data 并存(上次成功数据不丢) */}
      {error ? (
        <div className="mb-2 flex items-center gap-1.5 text-[10px] text-amber-600 dark:text-amber-500">
          <AlertTriangle className="w-3 h-3" />
          <span>本次刷新失败: {error} — 仍展示上次成功数据</span>
        </div>
      ) : null}

      {/* 主线列表 */}
      <div className="space-y-1">
        {top.map((g) => {
          const key = `${g.name}`
          return (
            <MainlineRow
              key={key}
              g={g}
              expanded={expandedKey === key}
              onToggle={() => setExpandedKey(expandedKey === key ? null : key)}
            />
          )
        })}
      </div>

      {/* 11-20 名折叠展示: 默认收起, 留个角标 */}
      {rest.length > 0 ? (
        <details className="mt-2">
          <summary className="cursor-pointer text-[10px] text-muted-foreground hover:text-foreground select-none">
            展开 11-20 名 ({rest.length})
          </summary>
          <div className="space-y-1 mt-1">
            {rest.map((g) => {
              const key = `${g.name}`
              return (
                <MainlineRow
                  key={key}
                  g={g}
                  expanded={expandedKey === key}
                  onToggle={() => setExpandedKey(expandedKey === key ? null : key)}
                />
              )
            })}
          </div>
        </details>
      ) : null}

      {/* 未入榜 chip(涨停 <3 家) */}
      {unranked.length > 0 ? (
        <div className="mt-2 pt-2 border-t border-border/30">
          <div className="text-[10px] text-muted-foreground mb-1">
            未入榜(涨停 {"<"}3 家): {unranked.length} 个
          </div>
          <div className="flex flex-wrap gap-1">
            {unranked.map((g) => (
              <span
                key={g.name}
                className="inline-flex items-center gap-1 rounded-md border border-border/50 bg-card px-1.5 py-0.5 text-[10px] text-muted-foreground"
                title={`${g.name} ${g.limit_up_count}家 最高${g.max_boards}板`}
              >
                <span>{g.name}</span>
                <span className="text-rose-600 dark:text-rose-400 font-mono">{g.limit_up_count}家</span>
                <span className="text-muted-foreground/60">/</span>
                <span className="text-amber-600 dark:text-amber-400 font-mono">{g.max_boards}B</span>
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}

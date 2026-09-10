import { useCallback, useEffect, useRef, useState } from 'react'
import { Activity, RefreshCw, ShieldAlert, TrendingUp, TrendingDown, Minus } from 'lucide-react'
import { fetchAPI } from '@panwatch/api'
import AsOfBadge from './AsOfBadge'

/**
 * 市场情绪周期 6 阶段卡(2026-08-24, 任务 A)。
 *
 * 数据: GET /api/market/phase(fetchAPI 自动补 /api 前缀)。
 *   响应: {available, current, recent_30d, distribution, total_days, note}
 *
 * 节奏: 30s 轮询刷新(与暗盘/分时错峰; 后端 biz_cache 30s 复用)。
 *
 * 设计:
 *   - 顶部"当前阶段"大字(冰点蓝/启动青/主升红/高潮深红/退潮橙/修复灰/积累中灰)
 *   - 核心指标行: 高度/首板/≥2板/≥3板/≥5板/晋级率/封板率/上证
 *   - 30 天阶段色块时间线(每格一日, 颜色对应当日阶段)
 *   - 段长分布 chip(各阶段累计天数)
 *
 * 样式对齐 DarkFlowCards / MainFlowCompareCard / MarketMainlineCard:
 *   rounded-xl + border-border/50 + bg-card + text-[11px] 紧凑密度。
 */

export interface MarketPhaseDay {
  date: string
  phase: string
  label: string
  first_board: number | null
  ge2_count: number | null
  ge3_count: number | null
  ge5_count: number | null
  max_height: number | null
  promo_rate: number | null
  seal_rate: number | null
  sh_index_pct: number | null
}

export interface MarketPhaseDistributionItem {
  phase: string
  days: number
  label: string
}

export interface MarketPhaseResp {
  available: boolean
  /** P3: 数据口径日期(最后一条日记录 YYYY-MM-DD), 无数据时 null */
  asof?: string | null
  current: MarketPhaseDay | null
  recent_30d: MarketPhaseDay[]
  distribution: MarketPhaseDistributionItem[]
  total_days: number
  note: string
}

/** 阶段 → 配色 token。统一 bg/text/border 三件套, 暗色模式自适应。 */
const PHASE_STYLE: Record<
  string,
  { bg: string; text: string; border: string; dot: string; desc: string }
> = {
  ice: {
    bg: 'bg-blue-500/10',
    text: 'text-blue-700 dark:text-blue-400',
    border: 'border-blue-500/40',
    dot: 'bg-blue-500',
    desc: '高度/宽度/首板同时贴地, 情绪冰封',
  },
  ignite: {
    bg: 'bg-cyan-500/10',
    text: 'text-cyan-700 dark:text-cyan-400',
    border: 'border-cyan-500/40',
    dot: 'bg-cyan-500',
    desc: '宽度/高度自低位扩张, 晋级率回升',
  },
  rally: {
    bg: 'bg-red-500/10',
    text: 'text-red-700 dark:text-red-400',
    border: 'border-red-500/40',
    dot: 'bg-red-500',
    desc: '高度+宽度+晋级率同时高位, 主升浪',
  },
  climax: {
    bg: 'bg-red-900/15',
    text: 'text-red-900 dark:text-red-300',
    border: 'border-red-900/50',
    dot: 'bg-red-900',
    desc: '二板≥50 或首板≥220, 极端宣泄',
  },
  ebb: {
    bg: 'bg-orange-500/10',
    text: 'text-orange-700 dark:text-orange-400',
    border: 'border-orange-500/40',
    dot: 'bg-orange-500',
    desc: '晋级率崩, 自高位回落',
  },
  repair: {
    bg: 'bg-gray-500/10',
    text: 'text-gray-700 dark:text-gray-400',
    border: 'border-gray-500/40',
    dot: 'bg-gray-500',
    desc: '不满足任何正向规则, 修复兜底',
  },
  accumulating: {
    bg: 'bg-slate-500/10',
    text: 'text-slate-700 dark:text-slate-400',
    border: 'border-slate-500/40',
    dot: 'bg-slate-500',
    desc: '历史不足 5 天, 信号不可信',
  },
}

function phaseStyle(phase: string) {
  return PHASE_STYLE[phase] ?? PHASE_STYLE.accumulating
}

function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return '--'
  return `${v >= 0 ? '+' : ''}${v.toFixed(digits)}%`
}

function fmtSignedPct(v: number | null | undefined): string {
  return fmtPct(v, 2)
}

function fmtInt(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '--'
  return String(Math.round(v))
}

function pctColor(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return 'text-muted-foreground'
  if (v > 0) return 'text-rose-600 dark:text-rose-400'
  if (v < 0) return 'text-emerald-600 dark:text-emerald-400'
  return 'text-muted-foreground'
}

function pctIcon(v: number | null | undefined) {
  if (v == null || !Number.isFinite(v)) return <Minus className="w-3 h-3" />
  if (v > 0) return <TrendingUp className="w-3 h-3" />
  return <TrendingDown className="w-3 h-3" />
}

export default function MarketPhaseCard() {
  const [data, setData] = useState<MarketPhaseResp | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)
  const mountedRef = useRef(true)

  const load = useCallback(async () => {
    setError('')
    try {
      const res = await fetchAPI<MarketPhaseResp>('/market/phase', { cacheMode: 'reload' })
      if (mountedRef.current) setData(res)
      if (mountedRef.current) setUpdatedAt(new Date())
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
    const timer = window.setInterval(() => void load(), 30000) // 30s
    return () => {
      mountedRef.current = false
      window.clearInterval(timer)
    }
  }, [load])

  if (loading && !data) {
    return (
      <div className="rounded-xl border border-border/50 bg-card animate-pulse">
        <div className="h-[200px] bg-accent/20 rounded-xl" />
      </div>
    )
  }

  if (error && !data) {
    return (
      <div className="rounded-xl border border-border/50 bg-card p-3">
        <div className="flex items-center gap-2 text-[12px] text-muted-foreground">
          <ShieldAlert className="w-3.5 h-3.5" />
          <span>市场阶段数据暂不可用({error})</span>
        </div>
      </div>
    )
  }

  const cur = data?.current ?? null
  const recent = (data as MarketPhaseResp & { recent_days?: MarketPhaseDay[] })?.recent_days ?? data?.recent_30d ?? []
  const dist = data?.distribution ?? []
  const totalDays = data?.total_days ?? 0
  const note = data?.note ?? ''

  const phaseKey = cur?.phase || 'accumulating'
  const style = phaseStyle(phaseKey)

  return (
    <div className="rounded-xl border border-border/50 bg-card p-3">
      {/* 标题 + 时间戳 */}
      <div className="flex items-center justify-between gap-2 mb-3">
        <div className="flex items-center gap-2">
          <Activity className="w-3.5 h-3.5 text-muted-foreground" />
          <span className="text-[13px] font-semibold text-foreground">情绪周期阶段</span>
          <AsOfBadge asof={data?.asof} />
          {updatedAt && (
            <span className="text-[10px] text-muted-foreground font-mono">
              {updatedAt.toLocaleTimeString('zh-CN', { hour12: false })}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {data?.available ? (
            <span className="text-[10px] text-muted-foreground font-mono">近 {totalDays}d</span>
          ) : null}
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

      {/* 当前阶段大字 */}
      <div className={`rounded-lg border ${style.border} ${style.bg} px-3 py-3 mb-3`}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className={`text-[26px] md:text-[30px] font-bold ${style.text} leading-none tracking-tight truncate`}>
              {cur?.label || '积累中'}
            </div>
            {cur?.date ? (
              <div className="text-[10px] text-muted-foreground mt-1 font-mono">{cur.date}</div>
            ) : null}
            <div className="text-[10.5px] text-muted-foreground mt-1.5 leading-snug">{style.desc}</div>
          </div>
          {cur ? (
            <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[10px] shrink-0">
              <Mini label="高度" value={fmtInt(cur.max_height)} tone={style.text} />
              <Mini label="首板" value={fmtInt(cur.first_board)} tone={style.text} />
              <Mini label="≥2板" value={fmtInt(cur.ge2_count)} tone={style.text} />
              <Mini
                label="晋级"
                value={cur.promo_rate != null ? `${(cur.promo_rate * 100).toFixed(0)}%` : '--'}
                tone={style.text}
              />
            </div>
          ) : null}
        </div>
      </div>

      {/* 核心指标行 */}
      {cur ? (
        <div className="grid grid-cols-3 md:grid-cols-5 gap-1.5 text-[11px] mb-3">
          <Stat label="≥3板" value={fmtInt(cur.ge3_count)} />
          <Stat label="≥5板" value={fmtInt(cur.ge5_count)} />
          <Stat
            label="封板率"
            value={cur.seal_rate != null ? `${(cur.seal_rate * 100).toFixed(0)}%` : '--'}
          />
          <Stat
            label="上证"
            value={fmtSignedPct(cur.sh_index_pct)}
            valueClass={
              cur.sh_index_pct != null && cur.sh_index_pct < -2
                ? 'text-amber-600 dark:text-amber-400'
                : pctColor(cur.sh_index_pct)
            }
            icon={pctIcon(cur.sh_index_pct)}
          />
          <Stat label="段数" value={String(dist.length)} />
        </div>
      ) : null}

      {/* 30 天阶段色块时间线 */}
      <div className="mt-2">
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-[10px] text-muted-foreground">近 {recent.length} 个交易日阶段色带</span>
          {note ? (
            <span className="text-[10px] text-muted-foreground/70 truncate max-w-[60%]" title={note}>
              {note}
            </span>
          ) : null}
        </div>
        {recent.length === 0 ? (
          <div className="text-[11px] text-muted-foreground py-2">
            暂无历史数据(请先调用 POST /api/market/phase/sync)
          </div>
        ) : (
          <div className="flex gap-px h-7">
            {recent.map((r) => {
              const s = phaseStyle(r.phase)
              return (
                <div
                  key={r.date}
                  title={`${r.date}: ${r.label}\n高度 ${r.max_height ?? '--'} · 首板 ${r.first_board ?? '--'} · ≥2板 ${r.ge2_count ?? '--'}`}
                  className={`flex-1 ${s.bg} border-l first:border-l-0 ${s.border}`}
                  style={{ minWidth: 3 }}
                />
              )
            })}
          </div>
        )}

        {/* 段长分布 chip */}
        {dist.length > 0 ? (
          <div className="flex flex-wrap gap-1.5 mt-2">
            {dist.map((d) => {
              const s = phaseStyle(d.phase)
              return (
                <span
                  key={d.phase}
                  className={`inline-flex items-center gap-1 rounded-md border ${s.border} ${s.bg} px-1.5 py-0.5 text-[10.5px] font-mono`}
                >
                  <span className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
                  <span className={s.text}>{d.label}</span>
                  <span className="text-muted-foreground tabular-nums">{d.days}d</span>
                </span>
              )
            })}
          </div>
        ) : null}
      </div>
    </div>
  )
}

function Mini({ label, value, tone }: { label: string; value: string; tone: string }) {
  return (
    <div className="text-right">
      <div className="text-muted-foreground text-[9px] leading-none">{label}</div>
      <div className={`font-mono tabular-nums text-[12px] font-semibold leading-tight ${tone}`}>
        {value}
      </div>
    </div>
  )
}

function Stat({
  label,
  value,
  valueClass,
  icon,
}: {
  label: string
  value: string
  valueClass?: string
  icon?: React.ReactNode
}) {
  return (
    <div className="rounded-lg bg-accent/20 px-2.5 py-1.5">
      <div className="text-muted-foreground text-[9.5px] flex items-center gap-0.5">
        {icon}
        {label}
      </div>
      <div className={`font-mono tabular-nums text-[11.5px] ${valueClass ?? 'text-foreground'}`}>
        {value}
      </div>
    </div>
  )
}

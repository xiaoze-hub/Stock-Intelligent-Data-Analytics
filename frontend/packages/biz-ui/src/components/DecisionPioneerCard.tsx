import { useCallback, useEffect, useRef, useState } from 'react'
import { RefreshCw, ShieldAlert } from 'lucide-react'
import { fetchAPI } from '@panwatch/api'

/**
 * 决策先锋三指标卡片(2026-08-30)
 * 数据来自 GET /api/decision-pioneer/{symbol}?market=CN
 * (fetchAPI 自动补 /api 前缀)。盘中每 30 秒自动刷新, 与分时图节奏一致。
 * 三指标 = GS策略(趋势) × AI机构活跃度(强度) × L2主力净流入(资金, 明盘口径, 非暗盘)。
 */

export interface InstitutionActivity {
  activity: number | null
  level: '大牛' | '强势' | '生命' | '弱' | string
  life_line: number
  strong_line: number
  bull_line: number
  streak_days: number
  ma5: number | null
  is_yang?: boolean
}

export interface GsSignal {
  signal: 'G' | 'S' | null | string
  state: 'G区' | 'S区' | string
  bb0: number
  a0: number
}

export interface L2Flow {
  available: boolean
  zjl_hb: number | null
  direction: string | null
  l2_tick_num: number | null
  l2_order_num: number | null
}

export interface DecisionPioneerResp {
  symbol: string
  institution_activity: InstitutionActivity | null
  gs: GsSignal | null
  l2: L2Flow | null
  /** L2 双源分歧度(2026-09-10): 分歧大=降置信, 本期只展示不阻断 */
  uncertainty?: { score: number | null; level: string; agree: boolean | null; detail: string } | null
  data_time?: string
}

function fmtWan(v: number | null | undefined, digits = 0): string {
  if (v == null || !Number.isFinite(v)) return '--'
  const wan = v / 10000
  return `${wan > 0 ? '+' : ''}${wan.toFixed(digits)}万`
}

function upColor(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return 'text-muted-foreground'
  return v > 0 ? 'text-rose-700 dark:text-rose-400' : v < 0 ? 'text-emerald-700 dark:text-emerald-400' : 'text-muted-foreground'
}

function activityColor(level: string): string {
  if (level === '大牛') return 'text-fuchsia-700 dark:text-fuchsia-400'
  if (level === '强势') return 'text-rose-700 dark:text-rose-400'
  if (level === '生命') return 'text-orange-600 dark:text-orange-400'
  return 'text-muted-foreground'
}

export default function DecisionPioneerCard({ symbol, market }: { symbol: string; market: string }) {
  const [data, setData] = useState<DecisionPioneerResp | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string>('')
  const mountedRef = useRef(true)

  const load = useCallback(async () => {
    if (!symbol) return
    setError('')
    try {
      const res = await fetchAPI<DecisionPioneerResp>(
        `/decision-pioneer/${encodeURIComponent(symbol)}?market=${encodeURIComponent(market)}`,
        { cacheMode: 'reload' },
      )
      if (mountedRef.current) setData(res)
    } catch (e) {
      if (mountedRef.current) setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      if (mountedRef.current) setLoading(false)
    }
  }, [symbol, market])

  useEffect(() => {
    mountedRef.current = true
    setLoading(true)
    void load()
    const timer = window.setInterval(() => void load(), 30000)
    return () => {
      mountedRef.current = false
      window.clearInterval(timer)
    }
  }, [load])

  if (loading && !data) {
    return <div className="mt-3 h-[110px] rounded-xl border border-border/50 bg-card animate-pulse bg-accent/20" />
  }

  if (error && !data) {
    return (
      <div className="mt-3 rounded-xl border border-border/50 bg-card p-3">
        <div className="flex items-center gap-2 text-[12px] text-muted-foreground">
          <ShieldAlert className="w-3.5 h-3.5" />
          <span>决策先锋三指标暂不可用({error})</span>
        </div>
      </div>
    )
  }

  const act = data?.institution_activity ?? null
  const gs = data?.gs ?? null
  const l2 = data?.l2 ?? null
  const unc = data?.uncertainty ?? null
  const uncClass =
    !unc || unc.score == null
      ? 'text-muted-foreground'
      : unc.level === '高'
        ? 'text-rose-500 font-semibold'
        : unc.level === '中'
          ? 'text-amber-500'
          : 'text-muted-foreground'

  return (
    <div className="mt-3 rounded-xl border border-border/50 bg-card p-3">
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="flex items-center gap-2">
          <div className="text-[13px] font-semibold text-foreground">🧭 决策先锋三指标</div>
          <span className="text-[10px] text-muted-foreground">GS趋势 × 机构活跃度 × L2资金</span>
          {unc && (
            <span className={`text-[10px] font-mono ${uncClass}`} title={unc.detail || '双源分歧度'}>
              分歧{unc.score == null ? '未知' : unc.score.toFixed(2)}
            </span>
          )}
        </div>
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

      <div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-[11px]">
        {/* AI机构活跃度 */}
        <div className="rounded-lg bg-accent/20 px-2.5 py-1.5">
          <div className="text-muted-foreground mb-0.5">AI机构活跃度</div>
          {act ? (
            <>
              <div className={`font-mono text-[15px] font-semibold ${activityColor(act.level)}`}>
                {act.activity != null ? act.activity.toFixed(2) : '--'}
                <span className="text-[10px] ml-1">{act.level}</span>
              </div>
              <div className="text-muted-foreground mt-0.5">
                连强{act.streak_days}日{act.ma5 != null ? ` · 5日均${act.ma5.toFixed(2)}` : ''}
              </div>
              <div className="text-[9px] text-muted-foreground/70 mt-0.5">
                生命1.56/强势3/大牛6
              </div>
            </>
          ) : (
            <div className="text-muted-foreground">无数据</div>
          )}
        </div>

        {/* GS策略(趋势过滤) */}
        <div className="rounded-lg bg-accent/20 px-2.5 py-1.5">
          <div className="text-muted-foreground mb-0.5">GS策略(趋势过滤)</div>
          {gs ? (
            <>
              <div className="font-mono text-[15px] font-semibold text-foreground">{gs.state}</div>
              <div className="text-muted-foreground mt-0.5">
                快线{gs.a0?.toFixed(2)} / 慢线{gs.bb0?.toFixed(2)}
              </div>
              <div className="text-[9px] text-muted-foreground/70 mt-0.5">
                方向过滤 · 买卖点滞后仅参考
              </div>
            </>
          ) : (
            <div className="text-muted-foreground">无数据</div>
          )}
        </div>

        {/* L2 主力净流入 */}
        <div className="rounded-lg bg-accent/20 px-2.5 py-1.5">
          <div className="text-muted-foreground mb-0.5">主力净流入(L2·TQ)</div>
          {l2?.available && l2.zjl_hb != null ? (
            <>
              <div className={`font-mono text-[15px] font-semibold ${upColor(l2.zjl_hb)}`}>
                {fmtWan(l2.zjl_hb)}
              </div>
              <div className="text-muted-foreground mt-0.5">{l2.direction ?? '平衡'}</div>
              <div className="text-[9px] text-muted-foreground/70 mt-0.5">
                逐笔{l2.l2_tick_num ?? 0}笔 · 委托{l2.l2_order_num ?? 0}笔
              </div>
            </>
          ) : (
            <div className="text-muted-foreground">无数据(休市/TQ未连接)</div>
          )}
        </div>
      </div>

      {data?.data_time ? (
        <div className="text-[9px] text-muted-foreground/60 mt-2 text-right font-mono">
          更新于 {data.data_time}
        </div>
      ) : null}
    </div>
  )
}

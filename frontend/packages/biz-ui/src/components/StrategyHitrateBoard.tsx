import { useEffect, useState } from 'react'
import { strategiesApi, type HitrateBoard } from '@panwatch/api'
import AsOfBadge from './AsOfBadge'

/**
 * 信号周滚动 HitRate 榜(L1, 2026-09-10): 哪个策略真赚钱, 一眼见。
 * 口径: 到目标价或持有期正收益=中；样本<10标"样本不足"；近4周命中<45%
 * 且样本≥20标"待砍"(砍之前先看样本量, 别误杀)。
 */
function statusClass(s: string) {
  if (s === '待砍') return 'text-rose-500 font-semibold'
  if (s === '样本不足') return 'text-muted-foreground/70'
  return 'text-emerald-600 dark:text-emerald-500'
}

export default function StrategyHitrateBoard() {
  const [board, setBoard] = useState<HitrateBoard | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    strategiesApi
      .hitrateBoard()
      .then((r: HitrateBoard) => {
        if (!cancelled) setBoard(r)
      })
      .catch(() => {
        if (!cancelled) setError('加载失败')
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (error && !board) {
    return (
      <div className="rounded-xl border border-border/50 bg-card p-3 text-[12px] text-muted-foreground">
        命中榜暂不可用({error})
      </div>
    )
  }
  if (!board) {
    return <div className="rounded-xl border border-border/50 bg-card p-3 animate-pulse h-[120px]" />
  }
  const rows = board.rows ?? []
  return (
    <div className="rounded-xl border border-border/50 bg-card p-3 mb-3">
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="text-[13px] font-semibold text-foreground">🎯 信号命中榜</div>
        <AsOfBadge asof={board.asof} source={`近${board.window_days}天`} />
      </div>
      <div className="text-[11px] text-muted-foreground/70 mb-2">口径：{board.hit_def}</div>
      {rows.length === 0 ? (
        <div className="text-[12px] text-muted-foreground">窗口内无已评估信号(先跑评估再回来看)</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-[11px] text-muted-foreground border-b border-border/50">
                <th className="text-left py-1.5 pr-2">策略</th>
                <th className="text-right py-1.5 pr-2">持有期</th>
                <th className="text-right py-1.5 pr-2">样本</th>
                <th className="text-right py-1.5 pr-2">命中率</th>
                <th className="text-right py-1.5 pr-2">到目标</th>
                <th className="text-right py-1.5 pr-2">止损</th>
                <th className="text-right py-1.5 pr-2">平均收益</th>
                <th className="text-right py-1.5">状态</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 20).map((r) => (
                <tr key={`${r.strategy_code}:${r.horizon_days}`} className="border-b border-border/30">
                  <td className="py-1.5 pr-2 font-mono text-muted-foreground">{r.strategy_code}</td>
                  <td className="py-1.5 pr-2 text-right font-mono">{r.horizon_days}d</td>
                  <td className="py-1.5 pr-2 text-right font-mono">{r.n}</td>
                  <td className="py-1.5 pr-2 text-right font-semibold text-primary">{r.hit_rate}%</td>
                  <td className="py-1.5 pr-2 text-right font-mono">{r.target_rate}%</td>
                  <td className="py-1.5 pr-2 text-right font-mono">{r.stop_rate}%</td>
                  <td className="py-1.5 pr-2 text-right font-mono">
                    {r.avg_ret == null ? '--' : `${r.avg_ret > 0 ? '+' : ''}${r.avg_ret}%`}
                  </td>
                  <td className={`py-1.5 text-right text-[11px] ${statusClass(r.status)}`}>{r.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

import { useEffect, useState } from 'react'
import { strategiesApi, type RegimeMatrix } from '@panwatch/api'
import AsOfBadge from './AsOfBadge'

/**
 * 市况×信号矩阵(L3, 2026-09-10): 哪类信号在什么市况下赚钱。
 * 市况=情绪周期阶段(信号发出日)；口径与命中榜一致；本期只展示不调权。
 */
export default function RegimeSignalMatrix() {
  const [matrix, setMatrix] = useState<RegimeMatrix | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    strategiesApi
      .regimeMatrix()
      .then((r: RegimeMatrix) => {
        if (!cancelled) setMatrix(r)
      })
      .catch(() => {
        if (!cancelled) setError('加载失败')
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (error && !matrix) {
    return (
      <div className="rounded-xl border border-border/50 bg-card p-3 text-[12px] text-muted-foreground">
        市况矩阵暂不可用({error})
      </div>
    )
  }
  if (!matrix) {
    return <div className="rounded-xl border border-border/50 bg-card p-3 animate-pulse h-[120px]" />
  }
  const cells = matrix.cells ?? []
  const best = matrix.best ?? {}
  const bestList = Object.entries(best)
  return (
    <div className="rounded-xl border border-border/50 bg-card p-3 mb-3">
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="text-[13px] font-semibold text-foreground">🗺️ 市况×信号矩阵</div>
        <AsOfBadge asof={matrix.asof} source={`近${matrix.window_days}天`} />
      </div>
      {matrix.current_phase && best[matrix.current_phase] ? (
        <div className="text-[12px] mb-2">
          当前市况
          <span className="font-semibold text-primary mx-1">{matrix.current_phase}</span>
          最优
          <span className="font-mono mx-1">{best[matrix.current_phase].strategy_code}</span>
          <span className="font-mono text-muted-foreground">
            (命中{best[matrix.current_phase].hit_rate}%, n={best[matrix.current_phase].n})
          </span>
        </div>
      ) : (
        <div className="text-[12px] text-muted-foreground mb-2">当前市况暂无足样本最优策略</div>
      )}
      {cells.length === 0 ? (
        <div className="text-[12px] text-muted-foreground">窗口内无可归因信号(市况标签或后验缺失)</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-[11px] text-muted-foreground border-b border-border/50">
                <th className="text-left py-1.5 pr-2">市况</th>
                <th className="text-left py-1.5 pr-2">策略</th>
                <th className="text-right py-1.5 pr-2">样本</th>
                <th className="text-right py-1.5 pr-2">命中率</th>
                <th className="text-right py-1.5 pr-2">平均收益</th>
                <th className="text-right py-1.5">状态</th>
              </tr>
            </thead>
            <tbody>
              {cells.slice(0, 30).map((c) => (
                <tr key={`${c.phase}:${c.strategy_code}`} className="border-b border-border/30">
                  <td className="py-1.5 pr-2 font-medium text-foreground">{c.phase}</td>
                  <td className="py-1.5 pr-2 font-mono text-muted-foreground">{c.strategy_code}</td>
                  <td className="py-1.5 pr-2 text-right font-mono">{c.n}</td>
                  <td className="py-1.5 pr-2 text-right font-semibold text-primary">{c.hit_rate}%</td>
                  <td className="py-1.5 pr-2 text-right font-mono">
                    {c.avg_ret == null ? '--' : `${c.avg_ret > 0 ? '+' : ''}${c.avg_ret}%`}
                  </td>
                  <td
                    className={`py-1.5 text-right text-[11px] ${c.status === '待砍' ? 'text-rose-500 font-semibold' : 'text-muted-foreground'}`}
                  >
                    {c.status}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {bestList.length > 0 && (
        <div className="mt-2 text-[11px] text-muted-foreground">
          各市况最优：
          {bestList.map(([ph, b]) => (
            <span key={ph} className="mr-2">
              {ph}→<span className="font-mono">{b.strategy_code}</span>({b.hit_rate}%)
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

import { useEffect, useState } from 'react'

/**
 * 顶栏交易状态(借鉴 OpenTerminal TopBar)。
 * 北京时间时钟 + A股交易状态点：开盘中(红) / 集合竞价( amber ) / 已收盘(灰)。
 * 纯前端计算，无后端请求。节假日不识别——仅按周一到五+时段判定。
 */
function sessionState(now: Date): { label: string; open: boolean; bidding: boolean } {
  const bj = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Shanghai' }))
  const day = bj.getDay()
  const mins = bj.getHours() * 60 + bj.getMinutes()
  if (day === 0 || day === 6) return { label: '周末休市', open: false, bidding: false }
  if (mins >= 555 && mins < 560) return { label: '集合竞价', open: false, bidding: true }
  if ((mins >= 560 && mins < 690) || (mins >= 780 && mins < 900)) return { label: '开盘中', open: true, bidding: false }
  return { label: '已收盘', open: false, bidding: false }
}

export default function MarketStatusPill() {
  const [now, setNow] = useState<Date | null>(null)

  useEffect(() => {
    setNow(new Date())
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])

  if (!now) return null
  const st = sessionState(now)
  const dot = st.open ? 'bg-rose-500' : st.bidding ? 'bg-amber-500' : 'bg-muted-foreground/50'
  return (
    <span
      className="hidden sm:inline-flex items-center gap-1.5 text-[11px] text-muted-foreground font-mono"
      title="北京时间 · A股交易状态(不含节假日)"
    >
      <span className={`w-1.5 h-1.5 rounded-full ${dot}`} />
      {st.label}
      <span>{now.toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}</span>
    </span>
  )
}

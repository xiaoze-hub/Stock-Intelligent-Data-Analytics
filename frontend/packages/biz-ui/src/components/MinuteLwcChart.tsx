import { useEffect, useRef, useState } from 'react'
import type {
  DeepPartial,
  IChartApi,
  ISeriesApi,
  MouseEventParams,
  SeriesMarker,
  SeriesOptionsMap,
  Time,
  UTCTimestamp,
} from 'lightweight-charts'
import {
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
} from 'lightweight-charts'
import type { MinuteSwings } from './InteractiveKline'

export interface MinutePoint {
  t: string
  price: number
  avg: number
  volume: number
}

interface Props {
  points: MinutePoint[]
  prevClose: number | null
  isIndex: boolean
  swings?: MinuteSwings | null
}

/** 万格式化: 4693万 → "+4693万" / -1241万 → "-1241万" */
const fmtWan = (v: number) => `${v >= 0 ? '+' : ''}${(v / 1e4).toFixed(0)}万`

/** 主力/散户 段标记类型(2026-08-12 v3: 箭头+数字, 散户三角, 图例, tooltip) */
interface SwingMark {
  time: number
  kind: 'rally' | 'dip' | 'flat'
  main_net: number
  main_buy: number
  main_sell: number
  retail_net: number
  retail_buy: number
  retail_sell: number
  verdict: string
  score: number
  signals?: string[]
  post?: { main_net: number; price_change: number } | null
  start: string
  end: string
  amt: number
  price_up?: number
  price_down?: number
  buy_ratio?: number
  spread?: number
}

type LineOptions = DeepPartial<SeriesOptionsMap['Line']>
type HistOptions = DeepPartial<SeriesOptionsMap['Histogram']>

function addLineSeries(chart: IChartApi, options: LineOptions): ISeriesApi<'Line'> {
  return chart.addSeries(LineSeries, options)
}

function addHistogramSeries(chart: IChartApi, options: HistOptions, paneIndex?: number): ISeriesApi<'Histogram'> {
  return chart.addSeries(HistogramSeries, options, paneIndex)
}

/** "0930" -> UTC 当天 09:30 的时间戳(秒)。LWC 内部用 UTC, 必须统一避免 8h 时差。 */
function hhmmToTs(t: string): number {
  const h = parseInt(t.slice(0, 2), 10)
  const m = parseInt(t.slice(2, 4), 10)
  const now = new Date()
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), h, m, 0))
  return Math.floor(d.getTime() / 1000)
}

/**
 * Lightweight Charts 分时走势图(2026-08-12 替换 ECharts)。
 *
 * 标准金融分时样式: 价格线(红涨绿跌) + 均价线(黄, 个股) + 昨收基准虚线(±分界线)
 * + 下方成交量柱(红涨绿跌, pane 1)。与日K 同一库(Lightweight v5), 砍掉 ECharts 依赖。
 * 午休时段(11:30-13:00)无数据点, 时间戳连续 → 天然留空隙, 无需 whitespace。
 */
export default function MinuteLwcChart({ points, prevClose, isIndex, swings }: Props) {
  const ref = useRef<HTMLDivElement | null>(null)
  const [swingMarks, setSwingMarks] = useState<SwingMark[]>([])
  const [hoverMark, setHoverMark] = useState<{ x: number; y: number; mark: SwingMark } | null>(null)
  const [showLegend, setShowLegend] = useState(true)

  useEffect(() => {
    const el = ref.current
    if (!el || !points.length) return

    const rootStyle = getComputedStyle(document.documentElement)
    const bg = rootStyle.getPropertyValue('--card').trim()
    const fg = rootStyle.getPropertyValue('--foreground').trim()

    const colorUp = '#f43f5e'
    const colorDown = '#10b981'
    const prevC = prevClose ?? points[0]?.price ?? 0

    const chart = createChart(el, {
      width: el.clientWidth,
      height: 300,
      layout: {
        background: { color: `hsl(${bg})` },
        textColor: `hsl(${fg} / 0.85)`,
      },
      rightPriceScale: { borderVisible: false },
      timeScale: {
        borderVisible: false,
        fixRightEdge: true,
        rightOffset: 1,
        barSpacing: 6,
        minBarSpacing: 1,
        // 2026-08-12 用户反馈: 底部时间轴显示"12日"太粗 → 改成 HH:MM 分钟格式
        tickMarkFormatter: (time: Time) => {
          const d = new Date((typeof time === 'number' ? time : 0) * 1000)
          return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`
        },
        timeVisible: true,
        secondsVisible: false,
      },
      grid: {
        vertLines: { color: 'rgba(148, 163, 184, 0.08)' },
        horzLines: { color: 'rgba(148, 163, 184, 0.08)' },
      },
      crosshair: { mode: CrosshairMode.Magnet },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    })

    // 价格线(红涨绿跌按首尾方向) + 均价线
    const last = points[points.length - 1]?.price ?? 0
    const first = points[0]?.price ?? 0
    const priceColor = last >= first ? colorUp : colorDown

    const priceSeries = addLineSeries(chart, {
      color: priceColor,
      lineWidth: 2,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    })
    priceSeries.setData(points.map(p => ({ time: hhmmToTs(p.t) as UTCTimestamp, value: p.price })))

    // 2026-08-12 用户反馈: 分时波动幅度太小没视觉冲击 → Y轴紧凑化。
    // 券商风格: 以价格线自身为中心对称缩放(半幅=实际波动半幅 或 昨收0.8% 取大, 留15%余量),
    // 让涨跌占满图表高度。之前默认 autoscale 把昨收线/均价线也算进范围, 线被压扁。
    // 昨收虚线必须保留可见 → 下界向下延伸到昨收(若昨收在范围外)。
    if (!isIndex) {
      const priceValues = points.map(p => p.price)
      const hi = Math.max(...priceValues)
      const lo = Math.min(...priceValues)
      const center = (hi + lo) / 2
      const half = Math.max((hi - lo) / 2, prevC * 0.008)  // 最小半幅=昨收0.8%, 防波动太小看不出
      const pad = half * 0.15
      let minV = center - half - pad
      let maxV = center + half + pad
      // 昨收线可见性: 若昨收在下界下方, 下界延伸到昨收-小幅余量; 上界同理
      if (prevC < minV) minV = prevC - half * 0.1
      if (prevC > maxV) maxV = prevC + half * 0.1
      priceSeries.applyOptions({
        autoscaleInfoProvider: () => ({
          priceRange: { minValue: minV, maxValue: maxV },
        }),
      })
    }

    if (!isIndex) {
      const avgSeries = addLineSeries(chart, {
        color: 'rgba(245, 158, 11, 0.95)',
        lineWidth: 1,
        priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
      })
      avgSeries.setData(points.map(p => ({ time: hhmmToTs(p.t) as UTCTimestamp, value: p.avg })))
    }

    // 昨收虚线 = ±分界线(红线上方=涨, 下方=跌)
    priceSeries.createPriceLine?.({
      price: prevC,
      color: 'rgba(148, 163, 184, 0.8)',
      lineWidth: 1,
      lineStyle: 2,
      title: '',
    })

    // ═══ 拉升/下探段标记 v3(2026-08-12): 主力箭头+数字, 散户三角(红▲买/绿▼卖),
    // 图例说明 + crosshair 悬停完整分析。仅个股非指数。
    // 分时点 t 为 "0930" 格式, 段 start/end 为 "09:48" 格式 → 统一转 "HH:MM" 比较。
    const hmOf = (t: string) => (t.includes(':') ? t.slice(0, 5) : `${t.slice(0, 2)}:${t.slice(2, 4)}`)
    // 主力净额 < 100万 的段是噪音, 过滤不标(2026-08-12 用户反馈2)
    const MIN_MAIN_NET = 100e4
    const marks: SwingMark[] = []
    if (!isIndex && swings) {
      for (const r of swings.rallies || []) {
        if (Math.abs(r.main_net ?? 0) < MIN_MAIN_NET) continue
        const idx = points.findIndex(p => hmOf(p.t) === r.start)
        if (idx < 0) continue
        marks.push({
          time: hhmmToTs(points[idx].t), kind: 'rally',
          main_net: r.main_net, main_buy: r.main_buy ?? 0, main_sell: r.main_sell ?? 0,
          retail_net: r.retail_net, retail_buy: r.retail_buy ?? 0, retail_sell: r.retail_sell ?? 0,
          verdict: r.verdict, score: r.score, signals: r.signals, post: r.post,
          start: r.start, end: r.end, amt: r.amt, price_up: r.price_up,
        })
      }
      for (const d of swings.dips || []) {
        if (Math.abs(d.main_net ?? 0) < MIN_MAIN_NET) continue
        const idx = points.findIndex(p => hmOf(p.t) === d.start)
        if (idx < 0) continue
        marks.push({
          time: hhmmToTs(points[idx].t), kind: 'dip',
          main_net: d.main_net, main_buy: d.main_buy ?? 0, main_sell: d.main_sell ?? 0,
          retail_net: d.retail_net, retail_buy: d.retail_buy ?? 0, retail_sell: d.retail_sell ?? 0,
          verdict: d.verdict, score: d.score, signals: d.signals, post: d.post,
          start: d.start, end: d.end, amt: d.amt, price_down: d.price_down,
        })
      }
      // 放量横盘段(2026-08-12): 托盘出货/压盘吸筹/对倒换手
      for (const f of swings.flats || []) {
        if (Math.abs(f.main_net ?? 0) < 100e4) continue  // 横盘主力净额 <100万 太弱, 不标
        const idx = points.findIndex(p => hmOf(p.t) === f.start)
        if (idx < 0) continue
        marks.push({
          time: hhmmToTs(points[idx].t), kind: 'flat',
          main_net: f.main_net, main_buy: f.main_buy ?? 0, main_sell: f.main_sell ?? 0,
          retail_net: f.retail_net, retail_buy: f.retail_buy ?? 0, retail_sell: f.retail_sell ?? 0,
          verdict: f.verdict, score: f.score, signals: f.signals,
          start: f.start, end: f.end, amt: f.amt, buy_ratio: f.buy_ratio, spread: f.spread,
        })
      }
    }

    if (marks.length) {
      // 主力标记: 拉升红↑ / 下探绿↓ / 横盘灰紫■(2026-08-12)
      const mainMarkers: SeriesMarker<Time>[] = marks.map(m => {
        if (m.kind === 'flat') {
          // 横盘段: 方形标记, 托盘出货=紫 / 压盘吸筹=青 / 对倒=灰
          const flatColor = m.verdict.includes('出货') ? '#8b5cf6'
            : m.verdict.includes('吸筹') ? '#06b6d4'
            : '#94a3b8'
          return {
            time: m.time as UTCTimestamp,
            position: 'belowBar' as const,
            color: flatColor,
            shape: 'square' as const,
            text: fmtWan(m.main_net),
            size: 1,
          }
        }
        const isRally = m.kind === 'rally'
        const confirmed = isRally
          ? (m.verdict.includes('放量上涨') || m.verdict.includes('疑似真拉升'))
          : (m.verdict.includes('放量下杀') || m.verdict.includes('疑似出货'))
        const base = isRally ? '#f43f5e' : '#10b981'
        const position = (isRally ? 'belowBar' : 'aboveBar') as 'belowBar' | 'aboveBar'
        const shape = (isRally ? 'arrowUp' : 'arrowDown') as 'arrowUp' | 'arrowDown'
        return {
          time: m.time as UTCTimestamp,
          position,
          color: confirmed ? base : `${base}88`,
          shape,
          text: fmtWan(m.main_net),
          size: 1,
        }
      })
      // 散户圆点(橙●=散户净买追涨 / 蓝●=散户净卖抛压; 与主力标记相反侧)
      // 2026-08-12 用户确认: 散户单笔本就<20万, 不再过滤——每个段都显示散户净额,
      // 让"主力拉升+散户没追/散户在抛"的对比完整可见。
      const retailMarkers: SeriesMarker<Time>[] = marks.map(m => {
        const net = m.retail_net ?? 0
        const isBuy = net > 0
        const position = (m.kind === 'rally' ? 'aboveBar' : 'belowBar') as 'aboveBar' | 'belowBar'
        return {
          time: m.time as UTCTimestamp,
          position,  // 与主力标记相反侧
          color: isBuy ? '#f59e0b' : '#3b82f6',  // 黄=散户买(追) / 蓝=散户卖(抛)
          shape: 'circle' as const,
          text: fmtWan(net),
          size: 0,
        }
      })
      const allMarkers = [...mainMarkers, ...retailMarkers]
      if (allMarkers.length) {
        createSeriesMarkers(priceSeries, allMarkers)
      }
      setSwingMarks(marks)  // 供 tooltip/图例
    }

    // ═══ 悬停 tooltip(2026-08-12 v3): crosshair 移动时找最近的段标记, 显示完整分析
    if (marks.length) {
      chart.subscribeCrosshairMove((param: MouseEventParams) => {
        const point = param?.point
        if (!point || !param?.time) {
          setHoverMark(null)
          return
        }
        // 找时间最近的标记(±90秒)
        // ⚠️ 兼容: 分时数据用秒时间戳, LWC crosshair 的 time 可能是数字秒 或
        // 业务日对象({year,month,day})。数字直接用; 对象转当天秒戳比较。
        let t: number
        if (typeof param.time === 'number') {
          t = param.time
        } else if (param.time && typeof param.time === 'object') {
          const d0 = new Date(Date.UTC(param.time.year, (param.time.month ?? 1) - 1, param.time.day ?? 1))
          t = Math.floor(d0.getTime() / 1000)
        } else {
          setHoverMark(null)
          return
        }
        let best: SwingMark | null = null
        let bestDist = 90
        for (const m of marks) {
          const dist = Math.abs(m.time - t)
          if (dist <= bestDist) {
            bestDist = dist
            best = m
          }
        }
        if (!best) {
          setHoverMark(null)
          return
        }
        const tw = 240, th = 170
        let x = point.x + 12, y = point.y + 12
        if (x + tw > el.clientWidth - 6) x = point.x - tw - 12
        if (y + th > el.clientHeight - 6) y = point.y - th - 12
        x = Math.max(6, Math.min(x, Math.max(6, el.clientWidth - tw - 6)))
        y = Math.max(6, Math.min(y, Math.max(6, el.clientHeight - th - 6)))
        setHoverMark({ x, y, mark: best })
      })
    }

    // 量能柱(pane 1): v5 multi-pane, 必须指定 paneIndex=1,
    // 否则成交量柱画在 pane 0 盖住价格线(2026-08-12 用户反馈"像成交量一样的东西")
    const volSeries = addHistogramSeries(chart, {
      priceFormat: { type: 'volume' },
    }, 1)
    volSeries.setData(
      points.map(p => ({
        time: hhmmToTs(p.t) as UTCTimestamp,
        value: p.volume,
        color: p.price >= prevC ? 'rgba(239, 68, 68, 0.4)' : 'rgba(16, 185, 129, 0.4)',
      }))
    )
    // 2026-08-12 用户反馈: 分时默认显示全天完整K线(9:30-15:00 全在框内),
    // 不再裁剪到最近 2/3(之前 setVisibleLogicalRange 只显示部分)
    const total = points.length
    chart.timeScale().setVisibleLogicalRange({ from: 0, to: Math.max(total - 1, 10) + 4 })

    const ro = new ResizeObserver(() => {
      chart.applyOptions({ width: el.clientWidth })
    })
    ro.observe(el)

    return () => {
      ro.disconnect()
      try {
        chart.remove()
      } catch {
        /* ignore */
      }
    }
  }, [points, prevClose, isIndex, swings])

  return (
    <div className="relative">
      {/* ═══ 图例(2026-08-12 v3): 说明标记含义, 左上角悬浮 */}
      {!isIndex && swingMarks.length > 0 && showLegend && (
        <div className="absolute left-2 top-1 z-10 flex flex-col gap-0.5 rounded-md bg-background/85 backdrop-blur px-2 py-1.5 text-[10px] leading-tight shadow-sm border border-border/60">
          <div className="flex items-center gap-1.5">
            <span className="inline-block w-0 h-0 border-l-[5px] border-r-[5px] border-b-[8px] border-l-transparent border-r-transparent border-b-rose-500" />
            <span>主力净买(拉升)</span>
            <span className="text-muted-foreground">· 实色=确认</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="inline-block w-0 h-0 border-l-[5px] border-r-[5px] border-t-[8px] border-l-transparent border-r-transparent border-t-emerald-500" />
            <span>主力净卖(下探)</span>
            <span className="text-muted-foreground">· 半透明=存疑</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber-500" />
            <span>散户净买(追涨)</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="inline-block w-1.5 h-1.5 rounded-full bg-blue-500" />
            <span>散户净卖(抛压)</span>
          </div>
          <div className="flex items-center gap-1.5 mt-0.5 border-t border-border/40 pt-0.5">
            <span className="inline-block w-2 h-2 bg-violet-500" />
            <span>横盘托盘出货</span>
            <span className="inline-block w-2 h-2 bg-cyan-500 ml-1" />
            <span>横盘压盘吸筹</span>
          </div>
          <button
            className="mt-0.5 text-[9px] text-muted-foreground hover:text-foreground text-left"
            onClick={() => setShowLegend(false)}
          >
            收起图例 ✕
          </button>
        </div>
      )}
      {/* 图例收起后: 左上角小按钮重新展开(2026-08-12 用户反馈: 只有关没有开) */}
      {!isIndex && swingMarks.length > 0 && !showLegend && (
        <button
          className="absolute left-2 top-1 z-10 rounded-md bg-background/85 backdrop-blur px-1.5 py-0.5 text-[10px] text-muted-foreground hover:text-foreground border border-border/60 shadow-sm"
          onClick={() => setShowLegend(true)}
          title="显示图例"
        >
          📖 图例
        </button>
      )}

      <div ref={ref} className="w-full h-[300px]" />

      {/* ═══ 悬停分析 tooltip(2026-08-12 v3) */}
      {hoverMark && (
        <div
          className="absolute z-20 rounded-md border border-border bg-background/95 backdrop-blur p-2 text-[11px] shadow-lg pointer-events-none"
          style={{ left: hoverMark.x, top: hoverMark.y, width: 240 }}
        >
          <SwingTooltip mark={hoverMark.mark} />
        </div>
      )}
    </div>
  )
}

/** 段标记完整分析 tooltip 内容 */
function SwingTooltip({ mark }: { mark: SwingMark }) {
  const dir = mark.kind === 'rally' ? '拉升' : mark.kind === 'dip' ? '下探' : '横盘'
  const mainRatio = mark.main_buy + mark.main_sell > 0
    ? Math.round((mark.main_buy / (mark.main_buy + mark.main_sell)) * 100)
    : 0
  const retailDir = mark.retail_net > 0 ? '净买(追涨)' : mark.retail_net < 0 ? '净卖(抛压)' : '持平'
  const verdictCls = mark.verdict.includes('出货') || mark.verdict.includes('对倒')
    ? 'text-rose-500'
    : mark.verdict.includes('吸筹')
      ? 'text-cyan-500'
      : mark.verdict.includes('诱空') || mark.verdict.includes('下杀')
        ? 'text-emerald-500'
        : 'text-amber-500'
  return (
    <div className="space-y-0.5">
      <div className="font-medium text-[11px] mb-1">
        {mark.start}–{mark.end} {dir}段
        <span className={`ml-1.5 ${verdictCls}`}>{mark.verdict}</span>
        <span className="ml-1 text-muted-foreground">score {mark.score}</span>
      </div>
      {mark.spread !== undefined && (
        <div className="text-muted-foreground">
          波幅 {(mark.spread * 100).toFixed(2)}% · 成交{fmtWan(mark.amt)} · 买占{mark.buy_ratio ?? 0}%
        </div>
      )}
      <div>
        <span className="text-rose-500">主力</span>{' '}
        买{fmtWan(mark.main_buy)} / 卖{fmtWan(mark.main_sell)} → 净{fmtWan(mark.main_net)}
        <span className="text-muted-foreground"> (买占{mainRatio}%)</span>
      </div>
      <div>
        <span className="text-blue-500">散户</span>{' '}
        买{fmtWan(mark.retail_buy)} / 卖{fmtWan(mark.retail_sell)} → {retailDir} {fmtWan(mark.retail_net)}
      </div>
      {mark.post && (
        <div className="text-muted-foreground">
          段后5分钟: 主力{fmtWan(mark.post.main_net)} · 价{mark.post.price_change >= 0 ? '+' : ''}{mark.post.price_change.toFixed(2)}
        </div>
      )}
      {mark.signals && mark.signals.length > 0 && (
        <div className="text-muted-foreground border-t border-border/60 pt-0.5 mt-0.5">
          {mark.signals.join('; ')}
        </div>
      )}
    </div>
  )
}

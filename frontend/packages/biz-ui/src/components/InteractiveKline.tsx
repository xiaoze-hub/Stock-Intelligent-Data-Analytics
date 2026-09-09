import { useEffect, useMemo, useRef, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { fetchAPI } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import MinuteLwcChart from './MinuteLwcChart'
import DarkFlowCards from './DarkFlowCards'
import AuctionSnapshotCard from './AuctionSnapshotCard'

type KlineItem = {
  date: string
  open: number
  close: number
  high: number
  low: number
  volume: number
}

type KlinesResponse = {
  symbol: string
  market: string
  days: number
  interval?: string
  klines: KlineItem[]
}

type MinutePoint = {
  t: string
  price: number
  avg: number
  volume: number
}

/** 拉升/下探段(2026-08-12): 后端 minute 接口 swings 字段, 供分时K区间标记 */
export interface SwingSegment {
  start: string
  end: string
  price_up?: number
  price_down?: number
  amt: number
  main_net: number
  main_buy?: number
  main_sell?: number
  retail_net: number
  retail_buy?: number
  retail_sell?: number
  buy_ratio?: number
  sell_ratio?: number
  verdict: string
  score: number
  signals?: string[]
  post?: { main_net: number; price_change: number } | null
  spread?: number
}

export interface MinuteSwings {
  symbol: string
  current_price: number
  rallies: SwingSegment[]
  dips: SwingSegment[]
  flats?: SwingSegment[]
  summary?: { n_rallies?: number; n_dips?: number; true_rallies?: number; true_dips?: number; main_net_total?: number }
}

type MinuteResponse = {
  symbol: string
  market: string
  points: MinutePoint[]
  prev_close?: number | null
  is_index?: boolean
  swings?: MinuteSwings | null
}

/** 主力意图结构化数据(2026-08-12): 后端 klines summary API 返回, 供 K线 markers/筹码叠加 */
export interface MainIntentStructured {
  direction: 'buy' | 'sell' | 'neutral' | 'wash' | 'absorb'
  main_net: number
  big_net?: number
  mid_net?: number
  participation?: number | null
  buy_ratio?: number | null
  auction_amt?: number
  phase?: string | null
  signal?: string | null
  chip_peak?: number | null
  chip_band?: { low: number; high: number } | null
  profit_ratio?: number | null
  tail_net?: number
  /** 2026-08-12: 竞价/开盘初期数据不足标记 */
  data_status?: 'ok' | 'insufficient'
  tick_count?: number
}

type HoverTipRow = {
  date: string
  open: number
  high: number
  low: number
  close: number
  ma5: number | null
  ma10: number | null
  ma20: number | null
  macd: number | null
  signal: number | null
  rsi6: number | null
}

type HoverTip = {
  visible: boolean
  x: number
  y: number
  row: HoverTipRow | null
}

function parseBusinessDay(dateStr: string): BusinessDay | null {
  const s = String(dateStr || '').trim()
  // 兼容 '20260828'(8位无横杠, fallback联网源) 和 '2026-08-28'(带横杠, PG路径)
  const m = s.match(/^(\d{4})-?(\d{2})-?(\d{2})$/)
  if (!m) return null
  return { year: Number(m[1]), month: Number(m[2]), day: Number(m[3]) }
}

function parseCrosshairDateKey(time: any): string | null {
  if (!time || typeof time !== 'object') return null
  const year = Number(time.year)
  const month = Number(time.month)
  const day = Number(time.day)
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) return null
  return `${year.toString().padStart(4, '0')}-${month.toString().padStart(2, '0')}-${day.toString().padStart(2, '0')}`
}

function sma(values: number[], period: number): Array<number | null> {
  if (period <= 1) return values.map(v => v)
  const out: Array<number | null> = new Array(values.length).fill(null)
  let sum = 0
  for (let i = 0; i < values.length; i++) {
    sum += values[i]
    if (i >= period) sum -= values[i - period]
    if (i >= period - 1) out[i] = sum / period
  }
  return out
}

function ema(values: number[], period: number): Array<number | null> {
  const out: Array<number | null> = new Array(values.length).fill(null)
  if (values.length === 0) return out
  const k = 2 / (period + 1)
  let prev: number | null = null
  for (let i = 0; i < values.length; i++) {
    const v = values[i]
    if (prev == null) {
      prev = v
      out[i] = v
      continue
    }
    prev = v * k + prev * (1 - k)
    out[i] = prev
  }
  return out
}

function computeMacd(closes: number[]) {
  const e12 = ema(closes, 12)
  const e26 = ema(closes, 26)
  const macd: Array<number | null> = closes.map((_, i) => {
    const a = e12[i]
    const b = e26[i]
    if (a == null || b == null) return null
    return a - b
  })
  const macdVals = macd.map(v => (v == null ? 0 : v))
  const signal = ema(macdVals, 9)
  const hist: Array<number | null> = macd.map((v, i) => {
    if (v == null || signal[i] == null) return null
    return v - (signal[i] as number)
  })
  return { macd, signal, hist }
}

function computeRsi(closes: number[], period = 6): Array<number | null> {
  const out: Array<number | null> = new Array(closes.length).fill(null)
  if (closes.length <= period) return out
  let gain = 0
  let loss = 0
  for (let i = 1; i <= period; i++) {
    const diff = closes[i] - closes[i - 1]
    if (diff >= 0) gain += diff
    else loss += -diff
  }
  let avgGain = gain / period
  let avgLoss = loss / period
  out[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss)
  for (let i = period + 1; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1]
    const g = diff > 0 ? diff : 0
    const l = diff < 0 ? -diff : 0
    avgGain = (avgGain * (period - 1) + g) / period
    avgLoss = (avgLoss * (period - 1) + l) / period
    out[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss)
  }
  return out
}

import type {
  BusinessDay,
  DeepPartial,
  IChartApi,
  ISeriesApi,
  MouseEventParams,
  SeriesMarker,
  SeriesOptionsMap,
  Time,
} from 'lightweight-charts'
import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
} from 'lightweight-charts'

type CandleOptions = DeepPartial<SeriesOptionsMap['Candlestick']>
type LineOptions = DeepPartial<SeriesOptionsMap['Line']>
type HistOptions = DeepPartial<SeriesOptionsMap['Histogram']>

function addCandles(chart: IChartApi, options: CandleOptions): ISeriesApi<'Candlestick'> {
  return chart.addSeries(CandlestickSeries, options)
}

function addLine(chart: IChartApi, options: LineOptions, paneIndex?: number): ISeriesApi<'Line'> {
  return chart.addSeries(LineSeries, options, paneIndex)
}

function addHistogram(chart: IChartApi, options: HistOptions, paneIndex?: number): ISeriesApi<'Histogram'> {
  return chart.addSeries(HistogramSeries, options, paneIndex)
}

export default function InteractiveKline(props: {
  symbol: string
  market: string
  initialInterval?: '1d' | '1w' | '1m'
  initialDays?: '60' | '120' | '250'
  /** 主力意图结构化数据(可选): 传了才在K线上画 markers/筹码叠加 */
  mainIntent?: MainIntentStructured | null
}) {
  // lightweight-charts 为 npm 依赖(2026-09-09 从 CDN 改为打包引入), 库恒可用, 无需等待
  const [lwReady] = useState(true)
  // 主力意图结构化数据(2026-08-12): 组件自取 summary API, 用于K线 markers/筹码叠加
  const [mainIntentData, setMainIntentData] = useState<MainIntentStructured | null>(null)
  const [interval, setIntervalValue] = useState<'1d' | '1w' | '1m'>(props.initialInterval || '1d')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string>('')
  const [data, setData] = useState<KlineItem[]>([])
  const [showRsi, setShowRsi] = useState(true)
  const [hoverTip, setHoverTip] = useState<HoverTip>({ visible: false, x: 0, y: 0, row: null })
  // 分时模式(内嵌进K线图组件, 与日/周/月同一组切换器)
  const [mode, setMode] = useState<'minute' | 'kline'>('kline')
  const [minutePoints, setMinutePoints] = useState<MinutePoint[]>([])
  const [minutePrevClose, setMinutePrevClose] = useState<number | null>(null)
  const [minuteIsIndex, setMinuteIsIndex] = useState(false)
  const [minuteSwings, setMinuteSwings] = useState<MinuteSwings | null>(null)
  const [minuteLoading, setMinuteLoading] = useState(false)
  const [minuteError, setMinuteError] = useState<string>('')
  const minuteRef = useRef<{ pts: MinutePoint[]; prev: number | null }>({ pts: [], prev: null })
  minuteRef.current = { pts: minutePoints, prev: minutePrevClose }

  const loadMinute = async () => {
    if (!props.symbol) return
    setMinuteLoading(true)
    setMinuteError('')
    try {
      // 2026-08-20: 分钟接口冷启动 ~15s(swings 全量逐笔翻页), 前端默认 20s 超时必中招。
      // timeoutMs: 60000 给冷启动留足空间; 缓存命中后通常 0.01s, 不影响体验。
      const res = await fetchAPI<MinuteResponse>(
        `/quotes/minute/${encodeURIComponent(props.symbol)}?market=${encodeURIComponent(props.market)}`,
        { cacheMode: 'reload', timeoutMs: 60000 }  // 分时 30s 轮询需实时, 跳过前端缓存
      )
      setMinutePoints(res.points || [])
      setMinutePrevClose(res.prev_close ?? null)
      setMinuteIsIndex(!!res.is_index)
      setMinuteSwings(res.swings || null)
    } catch (e) {
      setMinuteError(e instanceof Error ? e.message : '加载分时失败')
      setMinutePoints([])
      setMinutePrevClose(null)
      setMinuteIsIndex(false)
      setMinuteSwings(null)
    } finally {
      setMinuteLoading(false)
    }
  }

  useEffect(() => {
    if (mode !== 'minute') return
    void loadMinute()
    const timer = window.setInterval(() => void loadMinute(), 30000) // 盘中30秒自动刷新
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, props.symbol, props.market])

  const fixedDays = useMemo(() => {
    const customDays = Number(props.initialDays)
    if (Number.isFinite(customDays) && customDays > 0) {
      return Math.floor(customDays)
    }
    if (interval === '1m') return 360
    if (interval === '1w') return 180
    return 120
  }, [props.initialDays, interval])

  const containerRef = useRef<HTMLDivElement | null>(null)

  // 拉取主力意图结构化数据(2026-08-12): 仅 A 股, 失败静默
  useEffect(() => {
    if (!props.symbol || props.market !== 'CN') return
    let cancelled = false
    fetchAPI<{ main_intent_structured?: MainIntentStructured | null }>(
      `/klines/${encodeURIComponent(props.symbol)}/summary?market=CN`
    )
      .then((res: { main_intent_structured?: MainIntentStructured | null }) => {
        if (!cancelled) setMainIntentData(res.main_intent_structured ?? null)
      })
      .catch(() => {
        if (!cancelled) setMainIntentData(null)
      })
    return () => {
      cancelled = true
    }
  }, [props.symbol, props.market])

  const load = async () => {
    if (!props.symbol) return
    setLoading(true)
    setError('')
    setHoverTip(prev => (prev.visible ? { visible: false, x: 0, y: 0, row: null } : prev))
    try {
      const query = (days: number) =>
        `/klines/${encodeURIComponent(props.symbol)}?market=${encodeURIComponent(props.market)}&days=${encodeURIComponent(String(days))}&interval=${encodeURIComponent(interval)}`
      const attempts = Array.from(new Set([fixedDays, Math.max(90, Math.floor(fixedDays * 0.75))]))
      let best: KlineItem[] = []
      let lastError: unknown = null
      for (const d of attempts) {
        try {
          const res = await fetchAPI<KlinesResponse>(query(d))
          const kl = res.klines || []
          if (kl.length > best.length) best = kl
          if (d === fixedDays && kl.length > 0) break
        } catch (e) {
          lastError = e
        }
      }
      if (!best.length && lastError) throw lastError
      setData(best)
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载K线失败')
      setData([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.symbol, props.market, interval, fixedDays])

  useEffect(() => {
    if (props.initialInterval) setIntervalValue(props.initialInterval)
  }, [props.initialInterval, props.symbol, props.market])

  const series = useMemo(() => {
    const klines = (data || []).slice().filter(k => !!parseBusinessDay(k.date))
    const candles = klines.map(k => ({
      time: parseBusinessDay(k.date) as BusinessDay,
      open: k.open,
      high: k.high,
      low: k.low,
      close: k.close,
    }))
    const volumes = klines.map(k => ({
      time: parseBusinessDay(k.date) as BusinessDay,
      value: k.volume,
      color: k.close >= k.open ? 'rgba(239, 68, 68, 0.35)' : 'rgba(16, 185, 129, 0.35)',
    }))
    const closes = klines.map(k => k.close)
    const ma5 = sma(closes, 5)
    const ma10 = sma(closes, 10)
    const ma20 = sma(closes, 20)
    const volRaw = klines.map(k => k.volume)
    const volMa5 = sma(volRaw, 5)
    const volMa10 = sma(volRaw, 10)
    const macd = computeMacd(closes)
    const rsi6 = computeRsi(closes, 6)
    return { klines, candles, volumes, ma5, ma10, ma20, volMa5, volMa10, macd, rsi6 }
  }, [data])

  const latestMetrics = useMemo(() => {
    if (!series.klines.length) return null
    const last = series.klines[series.klines.length - 1]
    const prev = series.klines.length > 1 ? series.klines[series.klines.length - 2] : null
    const maxHigh = Math.max(...series.klines.map(k => k.high))
    const minLow = Math.min(...series.klines.map(k => k.low))
    const avgVol = series.klines.reduce((acc, k) => acc + (k.volume || 0), 0) / series.klines.length
    const changePct = prev && prev.close ? ((last.close - prev.close) / prev.close) * 100 : 0
    const ampPct = last.close ? ((last.high - last.low) / last.close) * 100 : 0
    return { last, changePct, ampPct, maxHigh, minLow, avgVol }
  }, [series.klines])

  const indexByDate = useMemo(() => {
    const m = new Map<string, number>()
    for (let i = 0; i < series.klines.length; i++) {
      m.set(series.klines[i].date, i)
    }
    return m
  }, [series.klines])
  const showSkeleton = loading && !series.klines.length

  useEffect(() => {
    if (!containerRef.current) return
    if (!series.candles.length) return

    const container = containerRef.current

    container.innerHTML = ''

    const rootStyle = getComputedStyle(document.documentElement)
    const bg = rootStyle.getPropertyValue('--card').trim()
    const fg = rootStyle.getPropertyValue('--foreground').trim()

    const defaultBars = interval === '1d' ? 100 : interval === '1w' ? 78 : 72
    const defaultSpacing = interval === '1d' ? 8.5 : interval === '1w' ? 10 : 10
    const chart = createChart(container, {
      width: container.clientWidth,
      height: 380,
      layout: {
        background: { color: `hsl(${bg})` },
        textColor: `hsl(${fg} / 0.85)`,
      },
      rightPriceScale: { borderVisible: false },
      timeScale: {
        borderVisible: false,
        fixRightEdge: true,
        rightOffset: 1,
        barSpacing: defaultSpacing,
        minBarSpacing: 1,
        lockVisibleTimeRangeOnResize: true,
      },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      grid: {
        vertLines: { color: 'rgba(148, 163, 184, 0.08)' },
        horzLines: { color: 'rgba(148, 163, 184, 0.08)' },
      },
      crosshair: { mode: CrosshairMode.Magnet },
    })

    const candleSeries = addCandles(chart, {
      upColor: '#ef4444',
      downColor: '#10b981',
      borderUpColor: '#ef4444',
      borderDownColor: '#10b981',
      wickUpColor: '#ef4444',
      wickDownColor: '#10b981',
    })
    candleSeries.setData(series.candles)

    // 量能 pane 1 (v5 multi-pane, 2026-08-12 从 scaleMargins 挤压改为真 pane)
    const volSeries = addHistogram(chart, {
      priceFormat: { type: 'volume' },
    }, 1)
    volSeries.setData(series.volumes)
    const volMa5Series = addLine(chart, { color: 'rgba(245, 158, 11, 0.9)', lineWidth: 1 }, 1)
    const volMa10Series = addLine(chart, { color: 'rgba(14, 165, 233, 0.9)', lineWidth: 1 }, 1)

    const ma5Series = addLine(chart, { color: 'rgba(99, 102, 241, 0.85)', lineWidth: 2 })
    const ma10Series = addLine(chart, { color: 'rgba(245, 158, 11, 0.85)', lineWidth: 2 })
    const ma20Series = addLine(chart, { color: 'rgba(14, 165, 233, 0.85)', lineWidth: 2 })

    const mapLine = (arr: Array<number | null>) =>
      series.klines
        .map((k, i) => {
          const v = arr[i]
          return v == null ? null : { time: parseBusinessDay(k.date) as BusinessDay, value: v }
        })
        .filter(Boolean)

    ma5Series.setData(mapLine(series.ma5) as any)
    ma10Series.setData(mapLine(series.ma10) as any)
    ma20Series.setData(mapLine(series.ma20) as any)
    volMa5Series.setData(mapLine(series.volMa5) as any)
    volMa10Series.setData(mapLine(series.volMa10) as any)

    // MACD pane 2 + RSI pane 3 (v5 multi-pane, 2026-08-12 从独立 chart + 手动 sync 改为原生多面板)
    const macdLine = addLine(chart, { color: 'rgba(99, 102, 241, 0.85)', lineWidth: 2 }, 2)
    const sigLine = addLine(chart, { color: 'rgba(14, 165, 233, 0.85)', lineWidth: 2 }, 2)
    const hist = addHistogram(chart, {
      priceFormat: { type: 'price', precision: 3, minMove: 0.001 },
    }, 2)

    const macdLineData = series.klines
      .map((k, i) => {
        const v = series.macd.macd[i]
        return v == null ? null : { time: parseBusinessDay(k.date) as BusinessDay, value: v }
      })
      .filter(Boolean)
    const sigLineData = series.klines
      .map((k, i) => {
        const v = series.macd.signal[i]
        return v == null ? null : { time: parseBusinessDay(k.date) as BusinessDay, value: v }
      })
      .filter(Boolean)
    const histData = series.klines
      .map((k, i) => {
        const v = series.macd.hist[i]
        if (v == null) return null
        return {
          time: parseBusinessDay(k.date) as BusinessDay,
          value: v,
          color: v >= 0 ? 'rgba(239, 68, 68, 0.35)' : 'rgba(16, 185, 129, 0.35)',
        }
      })
      .filter(Boolean)

    macdLine.setData(macdLineData as any)
    sigLine.setData(sigLineData as any)
    hist.setData(histData as any)

    // RSI pane 3 (开关控制)
    if (showRsi) {
      const rsiLine = addLine(chart, { color: 'rgba(234, 88, 12, 0.9)', lineWidth: 2 }, 3)
      const rsiData = series.klines
        .map((k, i) => {
          const v = series.rsi6[i]
          return v == null ? null : { time: parseBusinessDay(k.date) as BusinessDay, value: v }
        })
        .filter(Boolean)
      rsiLine.setData(rsiData as any)
      rsiLine.createPriceLine?.({ price: 70, color: 'rgba(239,68,68,0.45)', lineWidth: 1, lineStyle: 2, title: '70' })
      rsiLine.createPriceLine?.({ price: 30, color: 'rgba(16,185,129,0.45)', lineWidth: 1, lineStyle: 2, title: '30' })
    }

    // 主力意图 markers + 筹码叠加 (2026-08-12)
    const mainIntent = props.mainIntent ?? mainIntentData
    if (mainIntent && mainIntent.data_status !== 'insufficient') {
      // ① 主力意图箭头: 标在最后一根K线上 (买↑红 / 派发↓绿 / 洗盘吸筹↑橙 / 平衡)
      if (series.klines.length) {
        const lastK = series.klines[series.klines.length - 1]
        const markers: SeriesMarker<Time>[] = []
        const d = mainIntent.direction
        if (d === 'buy') {
          markers.push({
            time: parseBusinessDay(lastK.date) as BusinessDay,
            position: 'belowBar',
            color: '#ef4444',
            shape: 'arrowUp',
            text: '主力吸筹',
            size: 1,
          })
        } else if (d === 'wash') {
          // 净流出但参与度高/买占高 = 洗盘吸筹(对倒换手), 意图仍是吸
          markers.push({
            time: parseBusinessDay(lastK.date) as BusinessDay,
            position: 'belowBar',
            color: '#f97316',
            shape: 'arrowUp',
            text: '洗盘吸筹',
            size: 1,
          })
        } else if (d === 'absorb') {
          markers.push({
            time: parseBusinessDay(lastK.date) as BusinessDay,
            position: 'belowBar',
            color: '#f59e0b',
            shape: 'arrowUp',
            text: '疑似吸筹',
            size: 1,
          })
        } else if (d === 'sell') {
          markers.push({
            time: parseBusinessDay(lastK.date) as BusinessDay,
            position: 'aboveBar',
            color: '#10b981',
            shape: 'arrowDown',
            text: '主力派发',
            size: 1,
          })
        }
        // ② 涨停/跌停点 (近 60 根): 涨幅≥9.8% 标红箭头, 跌幅≤-9.8% 标绿箭头
        const fromIdx = Math.max(0, series.klines.length - 60)
        for (let i = fromIdx; i < series.klines.length; i++) {
          const k = series.klines[i]
          const prev = i > 0 ? series.klines[i - 1] : null
          if (!prev || !prev.close) continue
          const chg = ((k.close - prev.close) / prev.close) * 100
          if (chg >= 9.8) {
            markers.push({
              time: parseBusinessDay(k.date) as BusinessDay,
              position: 'belowBar',
              color: 'rgba(239, 68, 68, 0.9)',
              shape: 'arrowUp',
              text: '涨停',
              size: 0,
            })
          } else if (chg <= -9.8) {
            markers.push({
              time: parseBusinessDay(k.date) as BusinessDay,
              position: 'aboveBar',
              color: 'rgba(16, 185, 129, 0.9)',
              shape: 'arrowDown',
              text: '跌停',
              size: 0,
            })
          }
        }
        if (markers.length) {
          // v5 唯一写法: 顶级 createSeriesMarkers(series, markers)
          createSeriesMarkers(candleSeries, markers)
        }
      }
      // ③ 筹码叠加: 筹码峰 + 成本带上/下沿画在价格 pane
      if (mainIntent.chip_peak != null) {
        candleSeries.createPriceLine?.({
          price: mainIntent.chip_peak,
          color: 'rgba(234, 179, 8, 0.6)',
          lineWidth: 1,
          lineStyle: 1,
          title: '筹码峰',
          axisLabelVisible: true,
        })
      }
      if (mainIntent.chip_band) {
        candleSeries.createPriceLine?.({
          price: mainIntent.chip_band.high,
          color: 'rgba(148, 163, 184, 0.45)',
          lineWidth: 1,
          lineStyle: 3,
          title: '成本上沿',
          axisLabelVisible: true,
        })
        candleSeries.createPriceLine?.({
          price: mainIntent.chip_band.low,
          color: 'rgba(148, 163, 184, 0.45)',
          lineWidth: 1,
          lineStyle: 3,
          title: '成本下沿',
          axisLabelVisible: true,
        })
      }
    }

    // v5 多面板共用 X 轴, 无需手动同步可见范围(2026-08-12 移除旧版独立 chart sync)
    chart.subscribeCrosshairMove((param: MouseEventParams) => {
      const point = param?.point
      const dateKey = parseCrosshairDateKey(param?.time)
      if (!point || !dateKey || !series.klines.length) {
        setHoverTip(prev => (prev.visible ? { visible: false, x: 0, y: 0, row: null } : prev))
        return
      }
      const inBounds =
        point.x >= 0 &&
        point.y >= 0 &&
        point.x <= container.clientWidth &&
        point.y <= container.clientHeight
      if (!inBounds) {
        setHoverTip(prev => (prev.visible ? { visible: false, x: 0, y: 0, row: null } : prev))
        return
      }
      const idx = indexByDate.get(dateKey)
      if (idx == null || idx < 0 || idx >= series.klines.length) {
        setHoverTip(prev => (prev.visible ? { visible: false, x: 0, y: 0, row: null } : prev))
        return
      }

      const k = series.klines[idx]
      const tooltipWidth = 280
      const tooltipHeight = 152
      let x = point.x + 12
      let y = point.y + 12
      if (x + tooltipWidth > container.clientWidth - 6) x = point.x - tooltipWidth - 12
      if (y + tooltipHeight > container.clientHeight - 6) y = point.y - tooltipHeight - 12
      x = Math.max(6, Math.min(x, Math.max(6, container.clientWidth - tooltipWidth - 6)))
      y = Math.max(6, Math.min(y, Math.max(6, container.clientHeight - tooltipHeight - 6)))

      setHoverTip({
        visible: true,
        x,
        y,
        row: {
          date: k.date,
          open: k.open,
          high: k.high,
          low: k.low,
          close: k.close,
          ma5: series.ma5[idx],
          ma10: series.ma10[idx],
          ma20: series.ma20[idx],
          macd: series.macd.macd[idx],
          signal: series.macd.signal[idx],
          rsi6: series.rsi6[idx],
        },
      })
    })

    const ro = new ResizeObserver(() => {
      chart.applyOptions({ width: container.clientWidth })
    })
    ro.observe(container)

    const total = series.candles.length
    const from = Math.max(0, total - defaultBars)
    const to = Math.max(total - 1, 0)
    chart.timeScale().setVisibleLogicalRange({ from, to })
    // 多面板高度分配 (v5): 价格 55% / 量能 15% / MACD 15% / RSI 15%
    try {
      const panes = chart.panes?.()
      if (Array.isArray(panes) && panes.length >= 2) {
        const totalH = 380 + (showRsi ? 240 : 160)
        panes[0]?.setHeight?.(Math.round(totalH * 0.55))
        panes[1]?.setHeight?.(Math.round(totalH * 0.15))
        panes[2]?.setHeight?.(Math.round(totalH * 0.15))
        if (panes[3]) panes[3]?.setHeight?.(Math.round(totalH * 0.15))
        chart.applyOptions({ height: totalH })
      }
    } catch {
      // v4 无 panes API, 忽略
    }
    return () => {
      ro.disconnect()
      try {
        chart.remove()
      } catch {
        // ignore
      }
    }
  }, [series, lwReady, showRsi, indexByDate, interval, mode, mainIntentData])

  // 主力意图图例(2026-08-12): 展示方向/筹码峰/成本带, 仅在传入结构化数据时显示
  const mi = props.mainIntent ?? mainIntentData
  // 2026-08-12: 竞价/开盘初期数据不足 → 显示占位提示而非误导性结论
  const intentLabel = mi
    ? mi.data_status === 'insufficient'
      ? { text: `数据不足(${mi.tick_count ?? 0}笔)`, cls: 'text-muted-foreground font-medium' }
      : mi.direction === 'buy'
        ? { text: '吸筹↑', cls: 'text-rose-500 font-medium' }
        : mi.direction === 'wash'
          ? { text: '洗盘吸筹↑', cls: 'text-orange-500 font-medium' }
          : mi.direction === 'absorb'
            ? { text: '疑似吸筹↑', cls: 'text-amber-500 font-medium' }
            : mi.direction === 'sell'
              ? { text: '派发↓', cls: 'text-emerald-500 font-medium' }
              : { text: '平衡→', cls: 'text-muted-foreground font-medium' }
    : null
  const mainIntentLegend = mi ? (
    <div className="mt-3 rounded-lg bg-rose-500/5 border border-rose-500/15 px-2.5 py-2 text-[11px] text-foreground/80">
      <span className="font-medium text-rose-400 mr-2">主力意图</span>
      {intentLabel && <span className={intentLabel.cls}>{intentLabel.text}</span>}
      {typeof mi.main_net === 'number' && (
        <span className="ml-2 font-mono">
          {(mi.main_net / 1e4).toFixed(0)}万
          {typeof mi.big_net === 'number' && ` (超大${(mi.big_net / 1e4).toFixed(0)}/大${(mi.mid_net ?? 0) / 1e4 >= 0 ? '+' : ''}${((mi.mid_net ?? 0) / 1e4).toFixed(0)})`}
        </span>
      )}
      {mi.chip_peak != null && <span className="ml-2">筹码峰 <span className="font-mono">{mi.chip_peak.toFixed(2)}</span></span>}
      {mi.chip_band && (
        <span className="ml-2">成本带 <span className="font-mono">{mi.chip_band.low.toFixed(2)}-{mi.chip_band.high.toFixed(2)}</span></span>
      )}
      {typeof mi.profit_ratio === 'number' && (
        <span className="ml-2">获利 <span className="font-mono">{(mi.profit_ratio * 100).toFixed(0)}%</span></span>
      )}
    </div>
  ) : null

  return (
    <div className="card p-4 md:p-5">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-2 mb-3">
        <div className="text-[13px] font-semibold text-foreground">{mode === 'minute' ? '分时走势' : 'K线图'}</div>
        <div className="flex items-center gap-2 flex-wrap">
          {mode === 'kline' ? (
            <Button variant={showRsi ? 'default' : 'secondary'} size="sm" className="h-8 px-2.5" onClick={() => setShowRsi(v => !v)}>
              强弱线
            </Button>
          ) : null}
          <div className="inline-flex rounded-lg border border-border/60 bg-accent/20 p-0.5">
            {([
              { value: 'minute', label: '分时' },
              { value: '1d', label: '日K' },
              { value: '1w', label: '周K' },
              { value: '1m', label: '月K' },
            ] as const).map(item => {
              const active = item.value === 'minute' ? mode === 'minute' : mode === 'kline' && interval === item.value
              return (
                <button
                  key={item.value}
                  type="button"
                  className={`h-7 min-w-[44px] rounded-md px-2.5 text-[12px] transition-colors ${
                    active
                      ? 'bg-primary text-primary-foreground'
                      : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
                  }`}
                  onClick={() => {
                    if (item.value === 'minute') {
                      setMode('minute')
                    } else {
                      setMode('kline')
                      setIntervalValue(item.value)
                    }
                  }}
                >
                  {item.label}
                </button>
              )
            })}
          </div>
          <Button
            variant="secondary"
            size="sm"
            className="h-8"
            onClick={() => (mode === 'minute' ? void loadMinute() : void load())}
            disabled={mode === 'minute' ? minuteLoading : loading}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${(mode === 'minute' ? minuteLoading : loading) ? 'animate-spin' : ''}`} />
            <span className="hidden sm:inline">刷新</span>
          </Button>
        </div>
      </div>

      {mode === 'minute' ? (
        <div>
          {minuteError ? (
            <div className="text-[12px] text-rose-600 bg-rose-500/10 border border-rose-500/20 rounded-lg px-3 py-2 mb-3">
              {minuteError}
            </div>
          ) : null}
          {minuteLoading && !minutePoints.length ? (
            <div className="w-full h-[380px] rounded-xl border border-border/50 animate-pulse bg-accent/20" />
          ) : !minutePoints.length ? (
            <div className="w-full h-[380px] rounded-xl border border-border/50 flex items-center justify-center text-[12px] text-muted-foreground">
              暂无分时数据（非交易日 / 停牌 / 数据源不可用）
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mb-3">
                <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]">
                  <span className="text-muted-foreground">现价</span>{' '}
                  <span className={`font-mono ml-1 ${minutePoints[minutePoints.length - 1]?.price >= (minutePrevClose ?? 0) ? 'text-rose-500' : 'text-emerald-500'}`}>
                    {minutePoints[minutePoints.length - 1]?.price.toFixed(2)}
                  </span>
                </div>
                <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]">
                  <span className="text-muted-foreground">较昨收</span>{' '}
                  <span className={`font-mono ml-1 ${minutePoints[minutePoints.length - 1]?.price >= (minutePrevClose ?? 0) ? 'text-rose-500' : 'text-emerald-500'}`}>
                    {minutePrevClose && minutePoints.length
                      ? `${((minutePoints[minutePoints.length - 1].price - minutePrevClose) / minutePrevClose * 100) >= 0 ? '+' : ''}${((minutePoints[minutePoints.length - 1].price - minutePrevClose) / minutePrevClose * 100).toFixed(2)}%`
                      : '--'}
                  </span>
                </div>
                {!minuteIsIndex && (
                  <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]">
                    <span className="text-muted-foreground">均价</span> <span className="font-mono ml-1">{minutePoints[minutePoints.length - 1]?.avg.toFixed(2)}</span>
                  </div>
                )}
                <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]">
                  <span className="text-muted-foreground">昨收</span> <span className="font-mono ml-1">{minutePrevClose?.toFixed(2) ?? '--'}</span>
                </div>
                <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]">
                  <span className="text-muted-foreground">成交量</span> <span className="font-mono ml-1">{(minutePoints.reduce((s, p) => s + (p.volume || 0), 0) / 10000).toFixed(1)}万手</span>
                </div>
              </div>
              <div className="w-full rounded-xl border border-border/50 bg-card">
                <MinuteLwcChart points={minutePoints} prevClose={minutePrevClose} isIndex={minuteIsIndex} swings={minuteSwings} />
              </div>
              <div className="mt-2 flex gap-4 text-[11px] text-muted-foreground">
                <span className="flex items-center gap-1"><span className="inline-block w-3 h-0.5 bg-rose-400" /> 价格</span>
                {!minuteIsIndex && (
                  <span className="flex items-center gap-1"><span className="inline-block w-3 h-0.5 bg-amber-400" /> 均价</span>
                )}
                <span className="flex items-center gap-1"><span className="inline-block w-3 h-0.5 border-t border-dashed border-slate-500" /> 昨收(±分界线)</span>
                <span>腾讯实时分时 · 每30秒自动刷新 · {minutePoints.length} 个点</span>
              </div>
              {/* 分时模式专属: 主力意图 + 内盘外盘 双卡片(2026-08-13, 仅 A 股) */}
              {/* 竞价快览单行卡(2026-08-19, 仅 A 股, 挂在主力意图卡上方) */}
              {props.market === 'CN' ? <AuctionSnapshotCard symbol={props.symbol} market={props.market} /> : null}
              {props.market === 'CN' ? <DarkFlowCards symbol={props.symbol} market={props.market} /> : null}
            </>
          )}
        </div>
      ) : (
      <>
      {error ? (
        <div className="text-[12px] text-rose-600 bg-rose-500/10 border border-rose-500/20 rounded-lg px-3 py-2 mb-3">
          {error}
        </div>
      ) : null}

      {showSkeleton ? (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mb-3 animate-pulse">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="rounded-lg bg-accent/20 px-2.5 py-2">
              <div className="h-3 w-14 bg-accent/60 rounded" />
              <div className="h-3 w-16 bg-accent/60 rounded mt-2" />
            </div>
          ))}
        </div>
      ) : latestMetrics ? (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mb-3">
          <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]"><span className="text-muted-foreground">最新价</span> <span className="font-mono ml-1">{latestMetrics.last.close.toFixed(2)}</span></div>
          <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]"><span className="text-muted-foreground">涨跌</span> <span className={`font-mono ml-1 ${latestMetrics.changePct >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>{latestMetrics.changePct >= 0 ? '+' : ''}{latestMetrics.changePct.toFixed(2)}%</span></div>
          <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]"><span className="text-muted-foreground">振幅</span> <span className="font-mono ml-1">{latestMetrics.ampPct.toFixed(2)}%</span></div>
          <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]"><span className="text-muted-foreground">区间高低</span> <span className="font-mono ml-1">{latestMetrics.maxHigh.toFixed(2)}/{latestMetrics.minLow.toFixed(2)}</span></div>
          <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]"><span className="text-muted-foreground">均量</span> <span className="font-mono ml-1">{(latestMetrics.avgVol / 10000).toFixed(1)}万</span></div>
        </div>
      ) : null}
      <div className="relative">
        {showSkeleton ? (
          <div className="w-full h-[380px] rounded-xl overflow-hidden border border-border/50 p-3 animate-pulse">
            <div className="h-full w-full rounded-lg bg-accent/20" />
          </div>
        ) : (
          <div ref={containerRef} className="w-full h-[380px] rounded-xl overflow-hidden border border-border/50" />
        )}
        {hoverTip.visible && hoverTip.row ? (
          <div
            className="pointer-events-none absolute z-10 w-[280px] rounded-lg border border-border/60 bg-card/95 px-3 py-2 shadow-lg backdrop-blur-[2px]"
            style={{ left: `${hoverTip.x}px`, top: `${hoverTip.y}px` }}
          >
            <div className="text-[11px] text-foreground font-medium mb-1.5">{hoverTip.row.date}</div>
            <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
              <span>开盘价 <span className="font-mono text-foreground">{hoverTip.row.open.toFixed(2)}</span></span>
              <span>收盘价 <span className="font-mono text-foreground">{hoverTip.row.close.toFixed(2)}</span></span>
              <span>最高价 <span className="font-mono text-foreground">{hoverTip.row.high.toFixed(2)}</span></span>
              <span>最低价 <span className="font-mono text-foreground">{hoverTip.row.low.toFixed(2)}</span></span>
              <span>5日均线 <span className="font-mono text-foreground">{hoverTip.row.ma5 != null ? hoverTip.row.ma5.toFixed(2) : '--'}</span></span>
              <span>10日均线 <span className="font-mono text-foreground">{hoverTip.row.ma10 != null ? hoverTip.row.ma10.toFixed(2) : '--'}</span></span>
              <span>20日均线 <span className="font-mono text-foreground">{hoverTip.row.ma20 != null ? hoverTip.row.ma20.toFixed(2) : '--'}</span></span>
              <span>MACD线 <span className="font-mono text-foreground">{hoverTip.row.macd != null ? hoverTip.row.macd.toFixed(3) : '--'}</span></span>
              <span>信号线 <span className="font-mono text-foreground">{hoverTip.row.signal != null ? hoverTip.row.signal.toFixed(3) : '--'}</span></span>
              <span>RSI强弱 <span className="font-mono text-foreground">{hoverTip.row.rsi6 != null ? hoverTip.row.rsi6.toFixed(1) : '--'}</span></span>
            </div>
          </div>
        ) : null}
      </div>
      {mainIntentLegend}
      </>
      )}
    </div>
  )
}

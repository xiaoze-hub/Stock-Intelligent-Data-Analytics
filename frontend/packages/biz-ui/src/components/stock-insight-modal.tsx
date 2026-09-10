import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Copy, Download, ExternalLink, RefreshCw, Share2, Sparkles } from 'lucide-react'
import {
  insightApi,
  stocksApi,
  tradingAgentsApi,
  fundamentalsApi,
  type FundamentalsDetail,
  type DeepAnalysisResult,
  type HistoryComparisonResponse,
} from '@panwatch/api'
import { getMarketBadge } from '@panwatch/biz-ui'
import { useLocalStorage, parseServerTime } from '@/lib/utils'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { Switch } from '@panwatch/base-ui/components/ui/switch'
import { SuggestionBadge, type KlineSummary, type SuggestionInfo } from '@panwatch/biz-ui/components/suggestion-badge'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import InteractiveKline, { type MainIntentStructured } from '@panwatch/biz-ui/components/InteractiveKline'
import { KlineIndicators } from '@panwatch/biz-ui/components/kline-indicators'
import { buildKlineSuggestion } from '@/lib/kline-scorer'
import StockPriceAlertPanel from '@panwatch/biz-ui/components/stock-price-alert-panel'
import { TechnicalBadge } from '@panwatch/biz-ui/components/technical-badge'
import AddPositionCalculator from '@panwatch/biz-ui/components/add-position-calculator'

interface QuoteResponse {
  symbol: string
  market: string
  name: string | null
  current_price: number | null
  change_pct: number | null
  change_amount: number | null
  prev_close: number | null
  open_price: number | null
  high_price: number | null
  low_price: number | null
  volume: number | null
  turnover: number | null
  turnover_rate?: number | null
  volume_ratio?: number | null
  pe_ratio?: number | null
  pb_ratio?: number | null
  total_market_value?: number | null
  circulating_market_value?: number | null
  /** 一眼表(借鉴 OpenTerminal Quote 面板): 行情来源露头 */
  source?: string | null
}

interface MoreInfoResponse {
  symbol: string
  market: string
  turnover_rate: number | null
  volume_ratio: number | null
  commission_ratio: number | null
  total_market_value: number | null
  circulating_market_value: number | null
  change_pct: number | null
  change_pct_5d: number | null
  change_pct_20d: number | null
  change_pct_ytd: number | null
  limit_up_amount: number | null
  limit_up_ratio: number | null
  open_amount: number | null
  open_limit_buy: number | null
  consecutive_limit_days: number | null
  consecutive_up_days: number | null
  pe_dynamic: number | null
  pe_ttm: number | null
  pb: number | null
  dividend_yield: number | null
  beta: number | null
  ma5_price: number | null
  high_52w: number | null
  low_52w: number | null
  l2_tick_num: number | null
  l2_order_num: number | null
  total_buy_vol: number | null
  total_sell_vol: number | null
  cancel_buy: number | null
  cancel_sell: number | null
  zjl: number | null
  zjl_hb: number | null
  raw: Record<string, string>
  quote_time: string | null
}

interface DarkFlowTqResponse {
  symbol?: string
  market?: string
  date?: string
  xl_net: number | null
  large_net: number | null
  mid_net: number | null
  small_net: number | null
  total_orders: number | null
  reconstructed_orders: number | null
  split_order_count: number | null
  avg_split_parts: number | null
  cancel_ratio: number | null
  cancel_buy_vol: number | null
  cancel_sell_vol: number | null
  tuopan: boolean | null
  yapan: boolean | null
  suopan: boolean | null
  data_status: string
}

interface CompanyInfo {
  symbol?: string
  name?: string | null
  ename?: string | null
  industry?: string | null
  area?: string | null
  market_board?: string | null
  list_status?: string | null
  list_date?: string | null
  reg_capital?: string | null
  issuer?: string | null
  secretary?: string | null
  phone?: string | null
  website?: string | null
  address?: string | null
  bscope?: string | null
  desc?: string | null
  concepts?: string | null
  note?: string | null
}

interface KlineSummaryResponse {
  symbol: string
  market: string
  summary: KlineSummary
  /** 2026-08-12 预热优化: 主力意图结构化(逐笔口径), 供 K线 tab 的 InteractiveKline 秒显图例卡 */
  main_intent_structured?: MainIntentStructured | null
}

interface MiniKlineResponse {
  symbol: string
  market: string
  klines: Array<{
    date: string
    open: number
    close: number
    high: number
    low: number
    volume: number
  }>
}

interface NewsItem {
  source: string
  source_label: string
  title: string
  content?: string
  publish_time: string
  url: string
  symbols?: string[]
}

interface HistoryRecord {
  id: number
  agent_name: string
  stock_symbol: string
  analysis_date: string
  title: string
  content: string
  suggestions?: Record<string, any> | null
  news?: Array<{
    source?: string
    title?: string
    publish_time?: string
    url?: string
  }> | null
  quality_overview?: Record<string, any> | null
  context_summary?: Record<string, any> | null
  context_payload?: Record<string, any> | null
  prompt_context?: string | null
  prompt_stats?: Record<string, any> | null
  news_debug?: Record<string, any> | null
  created_at: string
  updated_at?: string
}

interface PortfolioPosition {
  symbol: string
  market: string
  quantity: number
  cost_price: number
  market_value_cny: number | null
  pnl: number | null
}

interface PortfolioSummaryResponse {
  accounts: Array<{
    positions: PortfolioPosition[]
  }>
}

type InsightTab = 'overview' | 'kline' | 'suggestions' | 'news' | 'announcements' | 'reports' | 'deep' | 'company' | 'fundamentals'

interface StockAgentInfo {
  agent_name: string
  schedule?: string
  ai_model_id?: number | null
  notify_channel_ids?: number[]
}

interface StockItem {
  id: number
  symbol: string
  name: string
  market: string
  agents?: StockAgentInfo[]
}

const AGENT_LABELS: Record<string, string> = {
  daily_report: '盘后日报',
  premarket_outlook: '盘前分析',
  news_digest: '新闻速递',
}

function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value == null) return '--'
  return value.toFixed(digits)
}

function formatCompactNumber(value: number | null | undefined): string {
  if (value == null) return '--'
  const n = Number(value)
  if (!isFinite(n)) return '--'
  const abs = Math.abs(n)
  if (abs >= 1e8) return `${(n / 1e8).toFixed(2)}亿`
  if (abs >= 1e4) return `${(n / 1e4).toFixed(2)}万`
  return n.toFixed(0)
}

function formatMarketCap(value: number | null | undefined, market?: string): string {
  if (value == null) return '--'
  const n = Number(value)
  if (!isFinite(n)) return '--'
  const m = String(market || '').toUpperCase()
  const abs = Math.abs(n)

  // 腾讯 A 股字段常见为“亿元”口径（如 808 表示 808 亿元）
  if (m === 'CN' && abs > 0 && abs < 100000) {
    return `${n.toFixed(2)}亿元`
  }

  if (abs >= 1e8) return `${(n / 1e8).toFixed(2)}亿元`
  if (abs >= 1e4) return `${(n / 1e4).toFixed(2)}万元`
  return `${n.toFixed(0)}元`
}

const MORE_INFO_TIPS: Record<string, string> = {
  commission_ratio: "委比=(委买手数-委卖手数)/(委买+委卖)×100%。+100%全买盘，-100%全卖盘。+40%以上偏多，-40%以下偏空，但需结合价格位置看",
  limit_up_amount: "封单额=涨停价上的封单资金(元)。越大越强势，封单1亿以上为强封",
  limit_up_ratio: "封成比=封单额/流通市值。衡量封单强度，>5%为超强封板",
  open_amount: "竞价金额=09:15-09:25集合竞价成交额。放量高开易冲高回落",
  open_limit_buy: "竞价涨停买=竞价阶段涨停价的买单金额。预判开盘强度",
  consecutive_limit_days: "连板天数=连续涨停天数。≥3板为强势连板，注意炸板风险",
  consecutive_up_days: "连涨天数=连续上涨天数（含非涨停）。看趋势延续性",
  change_pct_5d: "5日涨幅=近5交易日累计涨跌。>15%短期过热，<-10%超跌",
  change_pct_20d: "20日涨幅=近月累计。看中期趋势",
  change_pct_ytd: "年初至今=今年以来累计。看年内主升/主跌",
  pe_dynamic: "动态PE=股价/预估每股收益。越低越便宜，<15低估，>40高估，结合行业对比",
  pb: "市净率=股价/每股净资产。<2破净附近，>5品牌溢价高",
  dividend_yield: "股息率=年分红/股价。>3%媲美理财，适合红利策略",
  beta: "Beta=相对大盘弹性。1跟大盘同步，>1.2更敏感，<0.8更稳健",
  ma5_price: "5日均价=近5日收盘均值。站上偏多，跌破偏空",
  high_52w_low_52w: "52周高/低=近一年最高/最低。接近新高压力大，接近新低有反弹可能",
  l2_tick_num: "L2逐笔数=Level2逐笔成交笔数（需L2权限）。数值大说明交投活跃",
  l2_order_num: "L2委托数=Level2委托队列笔数。看盘口深度",
  total_buy_vol: "总买量=全天委托买入总量（手）。与总卖量对比看多空力量",
  total_sell_vol: "总卖量=全天委托卖出总量。与总买量对比，卖>买为抛压重",
  cancel_buy: "撤买=撤销的买单数。撤单多为假单诱多，需警惕虚假买盘",
  cancel_sell: "撤卖=撤销的卖单数。撤卖多为假单诱空或洗盘",
  zjl: "主买净额=主动买入净额（万元，=主动买-主动卖）。正为多方占优，负为空方占优",
  zjl_hb: "主力净流入=主力净额（万元，同花顺口径）。衡量大单/主力资金整体方向，持续为正代表主力建仓",
}

function InfoTip({ k }: { k: string }) {
  const tip = MORE_INFO_TIPS[k]
  if (!tip) return null
  return (
    <span
      className="ml-1 inline-flex h-3 w-3 items-center justify-center rounded-full bg-muted-foreground/15 text-[8px] text-muted-foreground cursor-help"
      title={tip}
    >
      ?
    </span>
  )
}

function formatTime(isoTime?: string): string {
  if (!isoTime) return ''
  const d = parseServerTime(isoTime)
  if (isNaN(d.getTime())) return ''
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

function parseToMs(input?: string): number | null {
  if (!input) return null
  const d = parseServerTime(input)
  if (!isNaN(d.getTime())) return d.getTime()
  const m = input.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!m) return null
  const dt = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 0, 0, 0)
  return isNaN(dt.getTime()) ? null : dt.getTime()
}

function parseSuggestionJson(raw: unknown): Record<string, any> | null {
  if (typeof raw !== 'string') return null
  const s = raw.trim()
  if (!s) return null
  const candidates: string[] = [s]
  const fence = s.match(/```(?:json)?\s*([\s\S]*?)```/i)
  if (fence?.[1]) candidates.unshift(fence[1].trim())
  if (/^json\s*[\r\n]/i.test(s)) candidates.unshift(s.replace(/^json\s*[\r\n]/i, '').trim())
  for (const c of candidates) {
    if (!c) continue
    const direct = c
    const sliceStart = c.indexOf('{')
    const sliceEnd = c.lastIndexOf('}')
    const sliced = sliceStart >= 0 && sliceEnd > sliceStart ? c.slice(sliceStart, sliceEnd + 1) : ''
    for (const text of [direct, sliced]) {
      if (!text || !text.startsWith('{') || !text.endsWith('}')) continue
      try {
        const obj = JSON.parse(text)
        if (obj && typeof obj === 'object') return obj as Record<string, any>
      } catch {
        // try next candidate
      }
    }
  }
  return null
}

function normalizeSuggestionAction(action?: string, actionLabel?: string): string {
  const a = String(action || '').trim().toLowerCase()
  const l = String(actionLabel || '').trim()
  if (a === 'buy/add' || a === 'add/buy') return /加仓|增持|补仓/.test(l) ? 'add' : 'buy'
  if (a === 'sell/reduce' || a === 'reduce/sell') return /减仓|减持/.test(l) ? 'reduce' : 'sell'
  return a || 'watch'
}

function pickSuggestionText(raw: unknown, field: 'signal' | 'reason'): string {
  const plain = String(raw || '').trim()
  const obj = parseSuggestionJson(plain)
  if (obj) {
    const v = String(obj[field] || '').trim()
    if (v) return v
    if (field === 'reason') {
      const rv = String(obj['raw'] || '').trim()
      if (rv) return rv
    }
    return ''
  }
  return plain
}

function normalizeTextList(raw: unknown): string[] {
  if (Array.isArray(raw)) return raw.map(x => String(x || '').trim()).filter(Boolean)
  const s = String(raw || '').trim()
  if (!s) return []
  const bySep = s.split(/[；;、|]/).map(x => x.trim()).filter(Boolean)
  return bySep.length > 1 ? bySep : [s]
}

function markdownToPlainText(input?: string): string {
  const raw = String(input || '').trim()
  if (!raw) return ''
  return raw
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]+)]\([^)]*\)/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^\s*>\s?/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\d+\.\s+/gm, '')
    .replace(/\*\*|__|\*|_/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

function StockReportMarkdown({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        table: ({ children }) => (
          <div className="my-4 max-w-full overflow-x-auto rounded-lg border border-border/60">
            <table className="m-0 w-max min-w-full border-collapse text-[12px]">{children}</table>
          </div>
        ),
        th: ({ children }) => (
          <th className="whitespace-nowrap border-b border-r border-border/60 bg-accent/50 px-3 py-2 text-left font-semibold last:border-r-0">
            {children}
          </th>
        ),
        td: ({ children }) => (
          <td className="min-w-[96px] border-b border-r border-border/40 px-3 py-2 align-top last:border-r-0">
            {children}
          </td>
        ),
        a: ({ children, href }) => (
          <a href={href} target="_blank" rel="noopener noreferrer" className="break-all text-primary underline underline-offset-2">
            {children}
          </a>
        ),
      }}
    >
      {content}
    </ReactMarkdown>
  )
}

function firstNonEmptyText(...vals: unknown[]): string {
  for (const v of vals) {
    const s = String(v || '').trim()
    if (s) return s
  }
  return ''
}

function buildShareTechnicalRisks(kline: KlineSummary | null): string[] {
  if (!kline) return []
  const out: string[] = []
  const rsi = String(kline.rsi_status || '')
  const macd = `${kline.macd_cross || ''} ${kline.macd_status || ''}`
  const vol = String(kline.volume_trend || '')
  if (rsi.includes('超买')) out.push('短线过热回撤风险')
  if (rsi.includes('超卖')) out.push('弱势延续风险')
  if (macd.includes('死叉')) out.push('趋势转弱风险')
  if (macd.includes('顶背离')) out.push('动能背离风险')
  if (vol.includes('放量')) out.push('波动放大风险')
  return out.slice(0, 3)
}

function TechnicalIndicatorStrip(props: {
  klineSummary: KlineSummary | null
  technicalSuggestion: SuggestionInfo | null
  stockName: string
  stockSymbol: string
  market: string
  hasPosition: boolean
  score?: number
  evidence?: Array<{ text: string; delta: number }>
}) {
  const { klineSummary, technicalSuggestion, stockName, stockSymbol, market, hasPosition, score, evidence = [] } = props
  if (!klineSummary) {
    return <div className="text-[12px] text-muted-foreground py-3">暂无技术指标</div>
  }
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[12px] text-muted-foreground">技术指标建议</span>
        <SuggestionBadge
          suggestion={technicalSuggestion}
          stockName={stockName}
          stockSymbol={stockSymbol}
          market={market}
          kline={klineSummary}
          hasPosition={hasPosition}
        />
        <TechnicalBadge label={`评分 ${Number(score ?? 0).toFixed(1)}`} tone="neutral" size="xs" className="text-foreground" />
      </div>
      {evidence.length > 0 && (
        <div className="flex flex-wrap gap-1.5 text-[10px]">
          {evidence.slice(0, 6).map((item, idx) => (
            <TechnicalBadge
              key={`${item.text}-${idx}`}
              label={`${item.text} ${item.delta > 0 ? `+${item.delta}` : item.delta}`}
              tone={item.delta > 0 ? 'bullish' : item.delta < 0 ? 'bearish' : 'neutral'}
              size="xs"
            />
          ))}
        </div>
      )}
      <KlineIndicators summary={klineSummary as any} />
    </div>
  )
}

/**
 * 基本面明细面板(龙虎榜/融资融券/股东户数/分红/事件日历)。
 * 数据来自 GET /api/market-data/fundamentals-detail/{symbol}?market=CN。
 * 容错策略: 加载中→"加载中"; 失败/无数据→静默"暂无"(不 toast, 不阻断弹窗其他功能);
 * 各分区有数据才显示, 字段缺失显示 "--"。字段口径与后端 chat.py 渲染保持一致
 * (金额=元, 户数=户, change_ratio 为百分数值, transfer/bonus_ratio 为每10股股数)。
 */
function FundamentalsPanel(props: {
  data: FundamentalsDetail | null
  loading: boolean
  loaded: boolean
}) {
  const { data, loading, loaded } = props
  // 防御性取值: 后端字段可能缺省/为 null, 统一规整为数组
  const d = data || ({} as FundamentalsDetail)
  const dtList = Array.isArray(d.dragon_tiger) ? d.dragon_tiger : []
  const mgList = Array.isArray(d.margin) ? d.margin : []
  const scList = Array.isArray(d.shareholders) ? d.shareholders : []
  const divList = Array.isArray(d.dividend) ? d.dividend : []
  const evList = Array.isArray(d.events) ? d.events : []
  const hasAny = dtList.length > 0 || mgList.length > 0 || scList.length > 0 || divList.length > 0 || evList.length > 0

  if (loading) {
    return <div className="card p-6 text-[12px] text-muted-foreground text-center">加载中...</div>
  }
  // 后端端点未就绪(404/超时)或该股确无数据: 静默降级为"暂无"
  if (!loaded || !hasAny) {
    return <div className="card p-6 text-[12px] text-muted-foreground text-center">暂无基本面数据</div>
  }

  /** 金额(元)→万/亿 紧凑展示; 龙虎榜净买入/股东变动用红涨绿跌配色 */
  const money = (v: number | null | undefined) => (v == null ? '--' : formatCompactNumber(v))
  const signedMoney = (v: number | null | undefined) =>
    v == null ? '--' : `${v >= 0 ? '+' : ''}${formatCompactNumber(v)}`
  const signedPct = (v: number | null | undefined) =>
    v == null ? '--' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`

  return (
    <div className="space-y-3">
      {/* 龙虎榜 */}
      {dtList.length > 0 && (
        <div className="card p-4">
          <div className="text-[11px] text-muted-foreground mb-2">龙虎榜</div>
          <div className="overflow-x-auto -mx-1">
            <table className="w-full text-[11px]">
              <thead className="text-muted-foreground">
                <tr className="border-b border-border/40">
                  <th className="text-left px-1 py-1 font-normal whitespace-nowrap">日期</th>
                  <th className="text-right px-1 py-1 font-normal whitespace-nowrap">净买入</th>
                  <th className="text-left px-1 py-1 font-normal">上榜原因</th>
                </tr>
              </thead>
              <tbody>
                {dtList.map((item, i) => (
                  <tr key={`dt-${item.trade_date || i}-${i}`} className="border-b border-border/20 hover:bg-accent/10">
                    <td className="px-1 py-1 text-muted-foreground whitespace-nowrap">{item.trade_date || '--'}</td>
                    <td className={`px-1 py-1 text-right font-mono whitespace-nowrap ${(item.net_buy ?? 0) >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                      {signedMoney(item.net_buy)}
                    </td>
                    <td className="px-1 py-1 text-foreground/80">{item.reason || '--'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 融资融券 */}
      {mgList.length > 0 && (
        <div className="card p-4">
          <div className="text-[11px] text-muted-foreground mb-2">融资融券</div>
          <div className="overflow-x-auto -mx-1">
            <table className="w-full text-[11px]">
              <thead className="text-muted-foreground">
                <tr className="border-b border-border/40">
                  <th className="text-left px-1 py-1 font-normal whitespace-nowrap">日期</th>
                  <th className="text-right px-1 py-1 font-normal whitespace-nowrap">融资余额</th>
                  <th className="text-right px-1 py-1 font-normal whitespace-nowrap">融券余额</th>
                  <th className="text-right px-1 py-1 font-normal whitespace-nowrap">融资买入</th>
                  <th className="text-right px-1 py-1 font-normal whitespace-nowrap">两融合计</th>
                </tr>
              </thead>
              <tbody>
                {mgList.map((item, i) => (
                  <tr key={`mg-${item.date || i}-${i}`} className="border-b border-border/20 hover:bg-accent/10">
                    <td className="px-1 py-1 text-muted-foreground whitespace-nowrap">{item.date || '--'}</td>
                    <td className="px-1 py-1 text-right font-mono whitespace-nowrap">{money(item.rz_balance)}</td>
                    <td className="px-1 py-1 text-right font-mono whitespace-nowrap">{money(item.rq_balance)}</td>
                    <td className="px-1 py-1 text-right font-mono whitespace-nowrap">{money(item.rz_buy)}</td>
                    <td className="px-1 py-1 text-right font-mono whitespace-nowrap">{money(item.total_balance)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 股东户数 */}
      {scList.length > 0 && (
        <div className="card p-4">
          <div className="text-[11px] text-muted-foreground mb-2">股东户数</div>
          <div className="overflow-x-auto -mx-1">
            <table className="w-full text-[11px]">
              <thead className="text-muted-foreground">
                <tr className="border-b border-border/40">
                  <th className="text-left px-1 py-1 font-normal whitespace-nowrap">日期</th>
                  <th className="text-right px-1 py-1 font-normal whitespace-nowrap">股东户数</th>
                  <th className="text-right px-1 py-1 font-normal whitespace-nowrap">变动</th>
                  <th className="text-right px-1 py-1 font-normal whitespace-nowrap">环比</th>
                </tr>
              </thead>
              <tbody>
                {scList.map((item, i) => (
                  <tr key={`sc-${item.report_date || i}-${i}`} className="border-b border-border/20 hover:bg-accent/10">
                    <td className="px-1 py-1 text-muted-foreground whitespace-nowrap">{item.report_date || '--'}</td>
                    <td className="px-1 py-1 text-right font-mono whitespace-nowrap">{money(item.holder_num)}</td>
                    {/* 户数减少=筹码集中, 按 A 股习惯红涨绿跌配色 */}
                    <td className={`px-1 py-1 text-right font-mono whitespace-nowrap ${item.change_num == null ? 'text-foreground/80' : item.change_num < 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                      {signedMoney(item.change_num)}
                    </td>
                    <td className={`px-1 py-1 text-right font-mono whitespace-nowrap ${item.change_ratio == null ? 'text-foreground/80' : item.change_ratio < 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                      {signedPct(item.change_ratio)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 分红 */}
      {divList.length > 0 && (
        <div className="card p-4">
          <div className="text-[11px] text-muted-foreground mb-2">分红</div>
          <div className="overflow-x-auto -mx-1">
            <table className="w-full text-[11px]">
              <thead className="text-muted-foreground">
                <tr className="border-b border-border/40">
                  <th className="text-left px-1 py-1 font-normal">分红方案</th>
                  <th className="text-left px-1 py-1 font-normal whitespace-nowrap">除权除息日</th>
                  <th className="text-right px-1 py-1 font-normal whitespace-nowrap">每股派息</th>
                  <th className="text-left px-1 py-1 font-normal whitespace-nowrap">进度</th>
                </tr>
              </thead>
              <tbody>
                {divList.map((item, i) => {
                  // 方案拼装: 10派X元 + 10转X + 10送X(与后端 chat.py 口径一致)
                  const parts: string[] = []
                  if (item.dividend_per_share != null) parts.push(`10派${(item.dividend_per_share * 10).toFixed(2)}元`)
                  if (item.transfer_ratio != null) parts.push(`10转${item.transfer_ratio}`)
                  if (item.bonus_ratio != null) parts.push(`10送${item.bonus_ratio}`)
                  return (
                    <tr key={`div-${item.ex_date || i}-${i}`} className="border-b border-border/20 hover:bg-accent/10">
                      <td className="px-1 py-1 text-foreground/90 whitespace-nowrap">{parts.length > 0 ? parts.join(' ') : '--'}</td>
                      <td className="px-1 py-1 text-muted-foreground whitespace-nowrap">{item.ex_date || '--'}</td>
                      <td className="px-1 py-1 text-right font-mono whitespace-nowrap">{item.dividend_per_share != null ? `${item.dividend_per_share.toFixed(2)}元` : '--'}</td>
                      <td className="px-1 py-1 text-muted-foreground whitespace-nowrap">{item.progress || '--'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 事件日历 */}
      {evList.length > 0 && (
        <div className="card p-4">
          <div className="text-[11px] text-muted-foreground mb-2">事件日历(近7日)</div>
          <div className="space-y-1.5">
            {evList.map((item, i) => {
              const evDate = typeof item.publish_time === 'string' ? item.publish_time.slice(0, 10) : ''
              const title = item.title || '--'
              return (
                <div key={`ev-${item.external_id || item.publish_time || i}-${i}`} className="flex items-start gap-2 rounded bg-accent/15 px-2 py-1.5">
                  <span className="text-[11px] text-muted-foreground whitespace-nowrap mt-px">{evDate || '--'}</span>
                  {item.event_type && (
                    <span className="shrink-0 rounded-full bg-accent/60 px-1.5 py-0.5 text-[10px] text-foreground/80">{item.event_type}</span>
                  )}
                  {item.url ? (
                    <a href={item.url} target="_blank" rel="noreferrer" className="text-[12px] text-foreground/90 leading-snug hover:text-primary hover:underline line-clamp-2">
                      {title}
                    </a>
                  ) : (
                    <span className="text-[12px] text-foreground/90 leading-snug line-clamp-2">{title}</span>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

export default function StockInsightModal(props: {
  open: boolean
  onOpenChange: (open: boolean) => void
  symbol: string
  market: string
  stockName?: string
  hasPosition?: boolean
}) {
  const { toast } = useToast()
  const symbol = String(props.symbol || '').trim()
  const market = String(props.market || 'CN').trim().toUpperCase()
  const [loading, setLoading] = useState(false)
  const [tab, setTab] = useState<InsightTab>('overview')
  const [newsHours, setNewsHours] = useLocalStorage<string>('stock_insight_news_hours', '168')
  const [announcementHours, setAnnouncementHours] = useLocalStorage<string>('stock_insight_announcement_hours', '168')
  const [includeExpiredSuggestions, setIncludeExpiredSuggestions] = useLocalStorage<boolean>(
    'stock_insight_include_expired_suggestions',
    true
  )
  const [autoRefreshEnabled, setAutoRefreshEnabled] = useLocalStorage<boolean>(
    'stock_insight_auto_refresh_enabled',
    true
  )
  const [autoRefreshSec, setAutoRefreshSec] = useLocalStorage<number>(
    'stock_insight_auto_refresh_sec',
    20
  )
  const [quote, setQuote] = useState<QuoteResponse | null>(null)
  const [moreInfo, setMoreInfo] = useState<MoreInfoResponse | null>(null)
  const [moreInfoLoading, setMoreInfoLoading] = useState(false)
  const [darkFlowTq, setDarkFlowTq] = useState<DarkFlowTqResponse | null>(null)
  const [companyInfo, setCompanyInfo] = useState<CompanyInfo | null>(null)
  const [companyLoading, setCompanyLoading] = useState(false)
  // 基本面明细(龙虎榜/股东户数/分红/两融/事件日历): 懒加载 + 静默降级
  const [fundamentals, setFundamentals] = useState<FundamentalsDetail | null>(null)
  const [fundamentalsLoading, setFundamentalsLoading] = useState(false)
  const [fundamentalsLoaded, setFundamentalsLoaded] = useState(false)
  const [klineSummary, setKlineSummary] = useState<KlineSummary | null>(null)
  /** 2026-08-12 预热优化: 弹窗打开即拉主力意图, 切到 K线 tab 秒显图例卡 */
  const [mainIntent, setMainIntent] = useState<MainIntentStructured | null>(null)
  const [miniKlines, setMiniKlines] = useState<MiniKlineResponse['klines']>([])
  const [miniKlineLoading, setMiniKlineLoading] = useState(false)
  const [miniHoverIdx, setMiniHoverIdx] = useState<number | null>(null)
  const [suggestions, setSuggestions] = useState<SuggestionInfo[]>([])
  const [news, setNews] = useState<NewsItem[]>([])
  const [announcements, setAnnouncements] = useState<NewsItem[]>([])
  const [reports, setReports] = useState<HistoryRecord[]>([])
  const [reportTab, setReportTab] = useState<'premarket_outlook' | 'daily_report' | 'news_digest'>('premarket_outlook')
  const [deepResult, setDeepResult] = useState<DeepAnalysisResult | null>(null)
  const [deepLoading, setDeepLoading] = useState(false)
  const [deepLoaded, setDeepLoaded] = useState(false)
  const [deepShowAnalyst, setDeepShowAnalyst] = useState(false)
  const [deepShowDebate, setDeepShowDebate] = useState(false)
  const [deepHistory, setDeepHistory] = useState<HistoryComparisonResponse | null>(null)
  const [deepHistoryLoading, setDeepHistoryLoading] = useState(false)
  const [klineInterval] = useState<'1d' | '1w' | '1m'>('1d')
  const [alerting, setAlerting] = useState(false)
  const [watchingStock, setWatchingStock] = useState<StockItem | null>(null)
  const [watchToggleLoading, setWatchToggleLoading] = useState(false)
  const [autoSuggesting, setAutoSuggesting] = useState(false)
  const [imageExporting, setImageExporting] = useState(false)
  const [holdingAgg, setHoldingAgg] = useState<{
    quantity: number
    cost: number
    unitCost: number
    marketValue: number
    pnl: number
  } | null>(null)
  const [holdingLoaded, setHoldingLoaded] = useState(false)
  const [holdingLoadError, setHoldingLoadError] = useState(false)
  const autoTriggeredRef = useRef<Record<string, number>>({})
  const stockCacheRef = useRef<Record<string, StockItem>>({})
  const resolvedName = useMemo(() => props.stockName || quote?.name || symbol, [props.stockName, quote?.name, symbol])

  const loadQuote = useCallback(async () => {
    if (!symbol) return
    const data = await insightApi.quote<QuoteResponse>(symbol, market)
    setQuote(data || null)
  }, [symbol, market])

  const loadMoreInfo = useCallback(async () => {
    if (!symbol) return
    setMoreInfoLoading(true)
    try {
      const data = await insightApi.moreInfo<MoreInfoResponse>(symbol, market)
      setMoreInfo(data || null)
    } catch {
      setMoreInfo(null)
    } finally {
      setMoreInfoLoading(false)
    }
  }, [symbol, market])

  const loadDarkFlowTq = useCallback(async () => {
    if (!symbol) return
    try {
      const data = await insightApi.darkFlowTq<DarkFlowTqResponse>(symbol, market)
      setDarkFlowTq(data || null)
    } catch {
      setDarkFlowTq(null) // 404 = 盘后未采集, 静默降级
    }
  }, [symbol, market])

  const loadCompany = useCallback(async () => {
    if (!symbol || companyInfo) return
    setCompanyLoading(true)
    try {
      const data = await insightApi.company<CompanyInfo>(symbol, market)
      setCompanyInfo(data || null)
    } catch {
      setCompanyInfo(null)
    } finally {
      setCompanyLoading(false)
    }
  }, [symbol, market, companyInfo])

  /**
   * 拉取基本面明细(龙虎榜/股东户数/分红/融资融券/事件日历)。
   * 该端点后端可能尚未就绪(404/超时): 失败静默降级, 不 toast、不阻断弹窗其他功能。
   */
  const loadFundamentals = useCallback(async () => {
    if (!symbol) return
    setFundamentalsLoading(true)
    try {
      const data = await fundamentalsApi.detail(symbol, market)
      setFundamentals(data || null)
    } catch {
      setFundamentals(null)
    } finally {
      setFundamentalsLoaded(true)
      setFundamentalsLoading(false)
    }
  }, [symbol, market])

  const loadKline = useCallback(async () => {
    if (!symbol) return
    const data = await insightApi.klineSummary<KlineSummaryResponse>(symbol, market)
    setKlineSummary(data?.summary || null)
    // 2026-08-12 预热优化: 顺手存主力意图, 传给 K线 tab 秒显(免组件二次请求)
    if (data?.main_intent_structured) setMainIntent(data.main_intent_structured)
  }, [symbol, market])

  const loadMiniKline = useCallback(async (opts?: { silent?: boolean }) => {
    if (!symbol) return
    const silent = !!opts?.silent
    if (!silent) setMiniKlineLoading(true)
    try {
      const data = await insightApi.klines<MiniKlineResponse>(symbol, {
        market,
        days: 36,
        interval: '1d',
      })
      setMiniKlines((data?.klines || []).slice(-30))
    } catch {
      setMiniKlines([])
    } finally {
      if (!silent) setMiniKlineLoading(false)
    }
  }, [symbol, market])

  const loadSuggestions = useCallback(async () => {
    if (!symbol) return
    const data = await insightApi.suggestions<any[]>(symbol, {
      market,
      limit: 20,
      include_expired: includeExpiredSuggestions,
    })
    const list = (data || []).map(item => ({
      id: item.id,
      action: normalizeSuggestionAction(item.action, item.action_label),
      action_label: item.action_label || '',
      signal: pickSuggestionText(item.signal, 'signal'),
      reason: pickSuggestionText(item.reason, 'reason'),
      should_alert: !!item.should_alert,
      agent_name: item.agent_name,
      agent_label: item.agent_label,
      created_at: item.created_at,
      is_expired: item.is_expired,
      prompt_context: item.prompt_context,
      ai_response: item.ai_response,
      raw: item.raw || '',
      meta: item.meta,
    })) as SuggestionInfo[]
    setSuggestions(list)
  }, [symbol, market, includeExpiredSuggestions])

  const loadNews = useCallback(async () => {
    if (!symbol) return
    const runQuery = async (opts: { useName: boolean; filterRelated: boolean }) => {
      const params = new URLSearchParams()
      params.set('hours', newsHours)
      params.set('limit', '50')
      if (!opts.filterRelated) params.set('filter_related', 'false')
      if (opts.useName && resolvedName && resolvedName !== symbol) params.set('names', resolvedName)
      else params.set('symbols', symbol)
      return insightApi.news<NewsItem[]>(Object.fromEntries(params.entries()))
    }

    try {
      let data: NewsItem[] = await runQuery({ useName: true, filterRelated: true })
      if ((data || []).length === 0 && resolvedName && resolvedName !== symbol) {
        data = await runQuery({ useName: false, filterRelated: true })
      }
      if ((data || []).length === 0) {
        data = await runQuery({ useName: true, filterRelated: false })
      }
      if ((data || []).length === 0) {
        data = await runQuery({ useName: false, filterRelated: false })
      }
      if ((data || []).length === 0) {
        const global = await insightApi.news<NewsItem[]>({
          hours: newsHours,
          limit: 80,
        }).catch(() => [])
        const upperSymbol = symbol.toUpperCase()
        const name = (resolvedName || '').trim()
        data = (global || []).filter((n) => {
          const text = `${n.title || ''} ${n.content || ''}`.toUpperCase()
          if (upperSymbol && text.includes(upperSymbol)) return true
          if (name && `${n.title || ''} ${n.content || ''}`.includes(name)) return true
          return (n.symbols || []).map(x => String(x).toUpperCase()).includes(upperSymbol)
        })
      }
      // 兜底：实时新闻为空时，回退到 news_digest 历史快照中的新闻列表
      if ((data || []).length === 0) {
        const bySymbol = await insightApi.history<HistoryRecord[]>({
          agent_name: 'news_digest',
          stock_symbol: symbol,
          limit: 1,
        }).catch(() => [])
        let rec: HistoryRecord | null = (bySymbol || [])[0] || null
        if (!rec) {
          const globals = await insightApi.history<HistoryRecord[]>({
            agent_name: 'news_digest',
            stock_symbol: '*',
            limit: 20,
          }).catch(() => [])
          const upperSymbol = symbol.toUpperCase()
          const name = (resolvedName || '').trim()
          rec = (globals || []).find((r) => {
            const sug = r?.suggestions || {}
            const keys = Object.keys(sug || {})
            if (keys.includes(symbol) || keys.map(k => k.toUpperCase()).includes(upperSymbol)) return true
            const text = `${r?.title || ''}\n${r?.content || ''}`.toUpperCase()
            if (upperSymbol && text.includes(upperSymbol)) return true
            if (name && `${r?.title || ''}\n${r?.content || ''}`.includes(name)) return true
            return false
          }) || null
        }
        if (rec?.news && Array.isArray(rec.news)) {
          data = rec.news
            .map((n) => ({
              source: n.source || 'news_digest',
              source_label: n.source || 'news_digest',
              title: n.title || '',
              publish_time: n.publish_time || rec?.analysis_date || '',
              url: n.url || '',
            }))
            .filter((n) => !!n.title)
        }
      }
      setNews(data || [])
    } catch {
      setNews([])
    }
  }, [symbol, newsHours, resolvedName])

  const loadAnnouncements = useCallback(async () => {
    if (!symbol) return
    try {
      const runQuery = async (opts: { useName: boolean; filterRelated: boolean }) => {
        const params = new URLSearchParams()
        params.set('hours', announcementHours)
        params.set('limit', '50')
        if (!opts.filterRelated) params.set('filter_related', 'false')
        params.set('source', 'eastmoney')
        if (opts.useName && resolvedName && resolvedName !== symbol) params.set('names', resolvedName)
        else params.set('symbols', symbol)
        return insightApi.news<NewsItem[]>(Object.fromEntries(params.entries()))
      }
      let data: NewsItem[] = await runQuery({ useName: true, filterRelated: true })
      if ((data || []).length === 0 && resolvedName && resolvedName !== symbol) {
        data = await runQuery({ useName: false, filterRelated: true })
      }
      if ((data || []).length === 0) {
        data = await runQuery({ useName: true, filterRelated: false })
      }
      if ((data || []).length === 0) {
        data = await runQuery({ useName: false, filterRelated: false })
      }
      if ((data || []).length === 0) {
        const global = await insightApi.news<NewsItem[]>({
          hours: announcementHours,
          limit: 80,
          source: 'eastmoney',
        }).catch(() => [])
        const upperSymbol = symbol.toUpperCase()
        const name = (resolvedName || '').trim()
        data = (global || []).filter((n) => {
          const text = `${n.title || ''} ${n.content || ''}`.toUpperCase()
          if (upperSymbol && text.includes(upperSymbol)) return true
          if (name && `${n.title || ''} ${n.content || ''}`.includes(name)) return true
          return (n.symbols || []).map(x => String(x).toUpperCase()).includes(upperSymbol)
        })
      }
      setAnnouncements(data || [])
    } catch {
      setAnnouncements([])
    }
  }, [symbol, announcementHours, resolvedName])

  const loadHoldingAgg = useCallback(async () => {
    if (!symbol) return
    setHoldingLoaded(false)
    setHoldingLoadError(false)
    try {
      const data = await insightApi.portfolioSummary<PortfolioSummaryResponse>({ include_quotes: true })
      let quantity = 0
      let cost = 0
      let marketValue = 0
      let pnl = 0
      for (const acc of data?.accounts || []) {
        for (const p of acc.positions || []) {
          if (p.symbol !== symbol || p.market !== market) continue
          quantity += Number(p.quantity || 0)
          cost += Number(p.cost_price || 0) * Number(p.quantity || 0)
          marketValue += Number(p.market_value_cny || 0)
          pnl += Number(p.pnl || 0)
        }
      }
      if (quantity > 0) setHoldingAgg({ quantity, cost, unitCost: cost / quantity, marketValue, pnl })
      else setHoldingAgg(null)
    } catch {
      setHoldingAgg(null)
      setHoldingLoadError(true)
    } finally {
      setHoldingLoaded(true)
    }
  }, [symbol, market])

  const loadReports = useCallback(async () => {
    if (!symbol) return
    try {
      const agents = ['premarket_outlook', 'daily_report', 'news_digest']
      const bySymbolResults = await Promise.all(
        agents.map(agent =>
          insightApi.history<HistoryRecord[]>({
            agent_name: agent,
            stock_symbol: symbol,
            limit: 1,
          }).catch(() => [])
        )
      )
      let merged = bySymbolResults
        .flatMap(items => items || [])
        .filter(Boolean)
      // 兼容全局记录（stock_symbol="*"）场景：从最近全局记录中筛选与当前股票相关的报告。
      if (merged.length === 0) {
        const globalResults = await Promise.all(
          agents.map(agent =>
            insightApi.history<HistoryRecord[]>({
              agent_name: agent,
              stock_symbol: '*',
              limit: 20,
            }).catch(() => [])
          )
        )
        const upperSymbol = symbol.toUpperCase()
        const name = (resolvedName || '').trim()
        merged = globalResults
          .map(items => {
            const rows = (items || []).filter(Boolean)
            const hit = rows.find((r) => {
              const sug = r?.suggestions || {}
              const keys = Object.keys(sug || {})
              if (keys.includes(symbol) || keys.map(k => k.toUpperCase()).includes(upperSymbol)) return true
              const text = `${r?.title || ''}\n${r?.content || ''}`.toUpperCase()
              if (upperSymbol && text.includes(upperSymbol)) return true
              if (name && `${r?.title || ''}\n${r?.content || ''}`.includes(name)) return true
              return false
            })
            return hit || null
          })
          .filter(Boolean) as HistoryRecord[]
      }
      merged = merged.sort((a, b) => {
        const am = parseToMs(a.updated_at || a.created_at || a.analysis_date) || 0
        const bm = parseToMs(b.updated_at || b.created_at || b.analysis_date) || 0
        return bm - am
      })
      setReports(merged)
    } catch {
      setReports([])
    }
  }, [symbol, resolvedName])

  const loadCore = useCallback(async () => {
    if (!symbol) return
    setLoading(true)
    try {
      await Promise.allSettled([loadQuote(), loadMoreInfo(), loadDarkFlowTq(), loadKline(), loadMiniKline(), loadHoldingAgg()])
    } catch (e) {
      toast(e instanceof Error ? e.message : '加载失败', 'error')
    } finally {
      setLoading(false)
    }
  }, [symbol, loadQuote, loadMoreInfo, loadDarkFlowTq, loadKline, loadMiniKline, loadHoldingAgg, toast])

  const handleRefreshAll = useCallback(async () => {
    if (!symbol) return
    setLoading(true)
    try {
      await Promise.allSettled([loadQuote(), loadMoreInfo(), loadDarkFlowTq(), loadKline(), loadMiniKline(), loadSuggestions(), loadNews(), loadAnnouncements(), loadHoldingAgg(), loadReports(), loadFundamentals()])
    } catch (e) {
      toast(e instanceof Error ? e.message : '加载失败', 'error')
    } finally {
      setLoading(false)
    }
  }, [symbol, loadQuote, loadMoreInfo, loadDarkFlowTq, loadKline, loadMiniKline, loadSuggestions, loadNews, loadAnnouncements, loadHoldingAgg, loadReports, loadFundamentals, toast])

  const refreshForAuto = useCallback(async () => {
    if (!symbol) return
    const tasks: Promise<any>[] = [loadQuote(), loadMoreInfo(), loadHoldingAgg()]
    if (tab === 'overview' || tab === 'kline') {
      tasks.push(loadKline(), loadMiniKline({ silent: true }))
    }
    if (tab === 'overview' || tab === 'suggestions') {
      tasks.push(loadSuggestions())
    }
    if (tab === 'overview' || tab === 'news') {
      tasks.push(loadNews())
    }
    if (tab === 'overview' || tab === 'announcements') {
      tasks.push(loadAnnouncements())
    }
    if (tab === 'overview' || tab === 'reports') {
      tasks.push(loadReports())
    }
    if (tab === 'company') {
      tasks.push(loadCompany())
    }
    if (tab === 'fundamentals') {
      tasks.push(loadFundamentals())
    }
    await Promise.allSettled(tasks)
  }, [symbol, tab, loadQuote, loadMoreInfo, loadHoldingAgg, loadKline, loadMiniKline, loadSuggestions, loadNews, loadAnnouncements, loadReports, loadCompany, loadFundamentals])

  const loadDeepResult = useCallback(async () => {
    if (!symbol) return
    setDeepLoading(true)
    setDeepHistoryLoading(true)
    try {
      const [latest, history] = await Promise.allSettled([
        tradingAgentsApi.getLatestForStock(symbol),
        tradingAgentsApi.getHistoryComparison(symbol, market, 90),
      ])
      setDeepResult(latest.status === 'fulfilled' ? latest.value : null)
      setDeepHistory(history.status === 'fulfilled' ? history.value : null)
    } catch {
      setDeepResult(null)
      setDeepHistory(null)
    } finally {
      setDeepLoaded(true)
      setDeepLoading(false)
      setDeepHistoryLoading(false)
    }
  }, [symbol, market])

  useEffect(() => {
    if (!props.open || !symbol) return
    setTab('overview')
    setSuggestions([])
    setNews([])
    setAnnouncements([])
    setReports([])
    setMiniKlines([])
    setWatchingStock(null)
    setDeepResult(null)
    setDeepLoaded(false)
    setDeepHistory(null)
    setFundamentals(null)
    setFundamentalsLoaded(false)
    setMoreInfo(null)
    loadCore()
  }, [props.open, symbol, market, loadCore])

  // 切到「深度」tab 时按需拉取(仅首次)
  useEffect(() => {
    if (!props.open || !symbol) return
    if (tab === 'deep' && !deepLoaded && !deepLoading) {
      loadDeepResult()
    }
  }, [tab, props.open, symbol, deepLoaded, deepLoading, loadDeepResult])

  // 切到「基本面」tab 时按需拉取(仅首次; 失败也置 loaded, 避免反复请求 404)
  useEffect(() => {
    if (!props.open || !symbol) return
    if (tab === 'fundamentals' && !fundamentalsLoaded && !fundamentalsLoading) {
      loadFundamentals()
    }
  }, [tab, props.open, symbol, fundamentalsLoaded, fundamentalsLoading, loadFundamentals])

  useEffect(() => {
    if (!props.open || !symbol) return
    let cancelled = false
    ;(async () => {
      try {
        const key = `${market}:${symbol}`
        const stocks = await stocksApi.list()
        if (cancelled) return
        const found = (stocks || []).find(s => s.symbol === symbol && s.market === market) || null
        if (found) {
          stockCacheRef.current[key] = found
        } else {
          delete stockCacheRef.current[key]
        }
        setWatchingStock(found)
      } catch {
        if (!cancelled) setWatchingStock(null)
      }
    })()
    return () => { cancelled = true }
  }, [props.open, symbol, market])

  useEffect(() => {
    if (!props.open || !symbol) return
    loadNews().catch(() => setNews([]))
  }, [props.open, symbol, newsHours, loadNews])

  useEffect(() => {
    if (!props.open || !symbol) return
    loadAnnouncements().catch(() => setAnnouncements([]))
  }, [props.open, symbol, announcementHours, loadAnnouncements])

  useEffect(() => {
    if (!props.open || !symbol) return
    loadSuggestions().catch(() => setSuggestions([]))
  }, [props.open, symbol, includeExpiredSuggestions, loadSuggestions])

  useEffect(() => {
    if (!props.open || !symbol) return
    loadReports().catch(() => setReports([]))
  }, [props.open, symbol, loadReports])

  useEffect(() => {
    if (!props.open || !symbol || !autoRefreshEnabled) return
    const sec = Number(autoRefreshSec) > 0 ? Number(autoRefreshSec) : 20
    const ms = Math.max(10, sec) * 1000
    const timer = setInterval(() => {
      refreshForAuto().catch(() => undefined)
    }, ms)
    return () => clearInterval(timer)
  }, [props.open, symbol, autoRefreshEnabled, autoRefreshSec, refreshForAuto])

  const hasHolding = !!props.hasPosition || !!holdingAgg
  const technicalScored = useMemo(() => {
    if (!klineSummary) return null
    return buildKlineSuggestion(klineSummary as any, hasHolding)
  }, [klineSummary, hasHolding])
  const technicalFallbackSuggestion = useMemo<SuggestionInfo | null>(() => {
    if (!klineSummary || !technicalScored) return null
    const topEvidence = (technicalScored.evidence || []).filter(e => e.delta !== 0).slice(0, 3).map(e => e.text)
    return {
      action: technicalScored.action,
      action_label: technicalScored.action_label,
      signal: technicalScored.signal || '技术面中性',
      reason: topEvidence.length > 0 ? topEvidence.join('；') : '基于K线技术指标自动生成的基础建议',
      should_alert: technicalScored.action === 'buy' || technicalScored.action === 'add' || technicalScored.action === 'sell' || technicalScored.action === 'reduce',
      agent_name: 'technical_fallback',
      agent_label: '技术指标',
      created_at: new Date().toISOString(),
      is_expired: false,
      meta: {
        fallback: true,
        score: technicalScored.score,
        evidence_count: technicalScored.evidence?.length || 0,
      },
    }
  }, [klineSummary, technicalScored])
  const buildPageContext = useCallback(() => {
    const parts: string[] = []
    if (quote) {
      const items = [`价格${quote.current_price}`, `涨跌幅${quote.change_pct}%`]
      if (quote.volume != null) items.push(`成交量${quote.volume}`)
      if (quote.turnover_rate != null) items.push(`换手率${quote.turnover_rate}%`)
      if (quote.pe_ratio != null) items.push(`市盈率${quote.pe_ratio}`)
      if (quote.total_market_value != null) items.push(`总市值${quote.total_market_value}`)
      parts.push(`实时行情：${items.join('，')}`)
    }
    if (klineSummary) {
      const k = klineSummary as any
      const items = []
      if (k.trend) items.push(`趋势${k.trend}`)
      if (k.macd_status) items.push(`MACD${k.macd_status}`)
      if (k.rsi_status) items.push(`RSI${k.rsi_status}${k.rsi6 != null ? `(${k.rsi6})` : ''}`)
      if (k.kdj_status) items.push(`KDJ${k.kdj_status}`)
      if (k.boll_status) items.push(`布林${k.boll_status}`)
      if (k.volume_trend) items.push(`量能${k.volume_trend}${k.volume_ratio != null ? `(${k.volume_ratio}x)` : ''}`)
      if (k.support != null) items.push(`支撑${k.support}`)
      if (k.resistance != null) items.push(`压力${k.resistance}`)
      if (items.length) parts.push(`技术面：${items.join('，')}`)
    }
    if (technicalScored) {
      parts.push(`技术评分：${technicalScored.action_label}(score=${technicalScored.score})，信号：${technicalScored.signal || '中性'}`)
      const evidence = (technicalScored.evidence || []).filter((e: any) => e.delta !== 0)
      if (evidence.length) {
        parts.push(`评分依据：${evidence.map((e: any) => `${e.text}(${e.delta > 0 ? '+' : ''}${e.delta})`).join('；')}`)
      }
    }
    if (suggestions.length > 0) {
      const lines = suggestions.slice(0, 3).map(s => `- [${s.agent_label || s.agent_name}] ${s.action_label}: ${s.signal}`)
      parts.push(`最近AI建议：\n${lines.join('\n')}`)
    }
    if (holdingAgg) {
      parts.push(`持仓：${holdingAgg.quantity}股，成本${holdingAgg.unitCost}，市值${holdingAgg.marketValue}，盈亏${holdingAgg.pnl}`)
    }
    return parts.join('\n')
  }, [quote, klineSummary, technicalScored, suggestions, holdingAgg])

  const quoteUp = (quote?.change_pct || 0) > 0
  const quoteDown = (quote?.change_pct || 0) < 0
  const changeColor = quoteUp ? 'text-rose-500' : quoteDown ? 'text-emerald-500' : 'text-foreground'
  const priceColor = quoteUp ? 'text-rose-500' : quoteDown ? 'text-emerald-500' : 'text-foreground'
  const levelColor = (value: number | null | undefined) => {
    if (value == null || quote?.prev_close == null) return 'text-foreground'
    if (value > quote.prev_close) return 'text-rose-500'
    if (value < quote.prev_close) return 'text-emerald-500'
    return 'text-foreground'
  }
  const badge = getMarketBadge(market)
  const amplitudePct = useMemo(() => {
    const hi = quote?.high_price
    const lo = quote?.low_price
    const pre = quote?.prev_close
    if (hi == null || lo == null || pre == null || pre === 0) return null
    return ((hi - lo) / pre) * 100
  }, [quote?.high_price, quote?.low_price, quote?.prev_close])

  const reportMap = useMemo(() => {
    const out: Record<string, HistoryRecord | null> = {
      premarket_outlook: null,
      daily_report: null,
      news_digest: null,
    }
    for (const r of reports) {
      if (!out[r.agent_name]) out[r.agent_name] = r
    }
    return out
  }, [reports])
  const activeReport = reportMap[reportTab]
  const latestReport = reports[0] || null
  const latestShareSuggestion = suggestions[0] || technicalFallbackSuggestion
  const shareCardPayload = useMemo(() => {
    const jsonSources = [
      parseSuggestionJson((latestShareSuggestion as any)?.signal),
      parseSuggestionJson((latestShareSuggestion as any)?.reason),
      parseSuggestionJson((latestShareSuggestion as any)?.raw),
      parseSuggestionJson((latestShareSuggestion as any)?.ai_response),
      parseSuggestionJson((latestShareSuggestion as any)?.prompt_context),
      (latestShareSuggestion as any)?.meta && typeof (latestShareSuggestion as any).meta === 'object'
        ? ((latestShareSuggestion as any).meta as Record<string, any>)
        : null,
    ].filter(Boolean) as Array<Record<string, any>>
    const pickFromJson = (...keys: string[]) => {
      for (const obj of jsonSources) {
        for (const key of keys) {
          const s = String(obj?.[key] || '').trim()
          if (s) return s
        }
      }
      return ''
    }
    const pickListFromJson = (...keys: string[]) => {
      for (const obj of jsonSources) {
        for (const key of keys) {
          const list = normalizeTextList(obj?.[key])
          if (list.length > 0) return list
        }
      }
      return [] as string[]
    }
    const marketLabel = badge.label
    const price = quote?.current_price != null ? formatNumber(quote.current_price) : '--'
    const chg = quote?.change_pct != null ? `${quote.change_pct >= 0 ? '+' : ''}${quote.change_pct.toFixed(2)}%` : '--'
    const action = latestShareSuggestion?.action_label || latestShareSuggestion?.action || '暂无'
    const signal = firstNonEmptyText(
      latestShareSuggestion?.signal,
      pickFromJson('signal', 'summary', 'core_view'),
      technicalScored?.signal,
      '技术面中性'
    ) || '--'
    const reason = firstNonEmptyText(
      latestShareSuggestion?.reason,
      pickFromJson('reason', 'thesis', 'core_judgement', 'core_judgment', 'analysis'),
      technicalFallbackSuggestion?.reason,
      '暂无'
    ) || '--'
    const risksList = [
      ...normalizeTextList((latestShareSuggestion as any)?.meta?.risks),
      ...pickListFromJson('risks', 'risk', 'risk_points'),
      ...buildShareTechnicalRisks(klineSummary),
    ].filter(Boolean)
    const dedupRisks = Array.from(new Set(risksList))
    const risks = dedupRisks.length > 0 ? dedupRisks.slice(0, 2).join('；') : '市场波动风险'
    const triggerList = pickListFromJson('triggers', 'trigger', 'signals')
    const invalidList = pickListFromJson('invalidations', 'invalidation', 'stop_conditions')
    const trigger = triggerList.length > 0 ? triggerList.slice(0, 2).join('；') : '--'
    const invalidation = invalidList.length > 0 ? invalidList.slice(0, 2).join('；') : '--'
    const technicalBrief = firstNonEmptyText(
      [klineSummary?.trend, klineSummary?.macd_status, klineSummary?.rsi_status].filter(Boolean).join(' / '),
      technicalScored?.signal
    ) || '--'
    const levelsBrief = (klineSummary?.support != null && klineSummary?.resistance != null)
      ? `支撑 ${formatNumber(klineSummary.support)} / 压力 ${formatNumber(klineSummary.resistance)}`
      : '--'
    const source = latestShareSuggestion?.agent_label || latestShareSuggestion?.agent_name || '技术指标'
    const ts = new Date().toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
    return { marketLabel, price, chg, action, signal, reason, risks, trigger, invalidation, technicalBrief, levelsBrief, source, ts }
  }, [badge.label, klineSummary, latestShareSuggestion, quote?.change_pct, quote?.current_price, technicalFallbackSuggestion?.reason, technicalScored?.signal])

  const shareText = useMemo(() => {
    const { marketLabel, price, chg, action, signal, reason, risks, trigger, invalidation, technicalBrief, levelsBrief, source, ts } = shareCardPayload
    const lines = [
      `【PanWatch 洞察】${resolvedName}（${symbol} · ${marketLabel}）`,
      `时间：${ts}`,
      `现价：${price}（${chg}）`,
      `建议：${action}`,
      `信号：${signal}`,
      `理由：${reason}`,
      `风险：${risks}`,
      `技术：${technicalBrief}`,
      `关键位：${levelsBrief}`,
      `来源：${source}`,
    ]
    if (trigger !== '--') lines.splice(7, 0, `触发：${trigger}`)
    if (invalidation !== '--') lines.splice(8, 0, `失效：${invalidation}`)
    return lines.join('\n')
  }, [shareCardPayload, resolvedName, symbol])

  const handleExportShareImage = useCallback(async () => {
    const esc = (s: string) => String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&apos;')
    const trim = (s: string, n = 42) => {
      const x = String(s || '')
      return x.length > n ? `${x.slice(0, n - 1)}…` : x
    }

    setImageExporting(true)
    try {
      const { marketLabel, price, chg, action, signal, reason, risks, technicalBrief, levelsBrief, source, ts } = shareCardPayload
      const up = (quote?.change_pct || 0) >= 0
      const changeColor = up ? '#ef4444' : '#10b981'
      const svg = `
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0b1220"/>
      <stop offset="100%" stop-color="#111827"/>
    </linearGradient>
  </defs>
  <rect x="0" y="0" width="1200" height="630" fill="url(#bg)"/>
  <rect x="40" y="30" width="1120" height="570" rx="22" fill="#0f172a" stroke="#1f2937"/>
  <text x="76" y="104" fill="#93c5fd" font-size="26" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">PanWatch 洞察</text>
  <text x="76" y="150" fill="#f8fafc" font-size="42" font-weight="700" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(trim(`${resolvedName}（${symbol} · ${marketLabel}）`, 28))}</text>
  <text x="76" y="198" fill="#94a3b8" font-size="22" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(ts)}</text>

  <text x="76" y="284" fill="#94a3b8" font-size="24" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">现价</text>
  <text x="180" y="284" fill="#f8fafc" font-size="52" font-weight="700" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(price)}</text>
  <text x="380" y="284" fill="${changeColor}" font-size="36" font-weight="700" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(chg)}</text>

  <text x="76" y="352" fill="#94a3b8" font-size="24" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">建议</text>
  <text x="180" y="352" fill="#22d3ee" font-size="34" font-weight="700" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(trim(action, 20))}</text>

  <text x="76" y="412" fill="#94a3b8" font-size="24" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">信号</text>
  <text x="180" y="412" fill="#e2e8f0" font-size="26" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(trim(signal, 46))}</text>

  <text x="76" y="466" fill="#94a3b8" font-size="24" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">理由</text>
  <text x="180" y="466" fill="#cbd5e1" font-size="24" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(trim(reason, 52))}</text>

  <text x="76" y="520" fill="#94a3b8" font-size="24" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">风险</text>
  <text x="180" y="520" fill="#cbd5e1" font-size="24" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(trim(risks, 52))}</text>

  <text x="76" y="560" fill="#94a3b8" font-size="22" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">技术</text>
  <text x="180" y="560" fill="#cbd5e1" font-size="21" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(trim(technicalBrief, 58))}</text>
  <text x="76" y="590" fill="#94a3b8" font-size="22" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">关键位</text>
  <text x="180" y="590" fill="#cbd5e1" font-size="21" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(trim(levelsBrief, 58))}</text>
  <text x="76" y="618" fill="#64748b" font-size="18" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">来源：${esc(source)} · 仅供参考，不构成投资建议</text>
</svg>`

      const blob = new Blob([svg], { type: 'image/svg+xml;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const img = await new Promise<HTMLImageElement>((resolve, reject) => {
        const el = new Image()
        el.onload = () => resolve(el)
        el.onerror = reject
        el.src = url
      })
      const canvas = document.createElement('canvas')
      canvas.width = 1200
      canvas.height = 630
      const ctx = canvas.getContext('2d')
      if (!ctx) throw new Error('无法创建画布')
      ctx.drawImage(img, 0, 0)
      URL.revokeObjectURL(url)
      const png = canvas.toDataURL('image/png')
      const a = document.createElement('a')
      a.href = png
      a.download = `panwatch-${symbol}-${Date.now()}.png`
      a.click()
      toast('分享图片已生成并下载', 'success')
    } catch {
      toast('图片生成失败，请稍后重试', 'error')
    } finally {
      setImageExporting(false)
    }
  }, [quote?.change_pct, resolvedName, shareCardPayload, symbol, toast])

  const copyTextWithFallback = useCallback(async (text: string): Promise<boolean> => {
    if (!text) return false

    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text)
        return true
      } catch {
        // Fallback to legacy copy below.
      }
    }

    if (typeof document !== 'undefined') {
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.setAttribute('readonly', '')
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      textarea.style.pointerEvents = 'none'
      textarea.style.left = '-9999px'
      document.body.appendChild(textarea)
      try {
        textarea.focus()
        textarea.select()
        textarea.setSelectionRange(0, textarea.value.length)
        return !!document.execCommand?.('copy')
      } catch {
        return false
      } finally {
        document.body.removeChild(textarea)
      }
    }
    return false
  }, [])

  const handleCopyShareText = useCallback(async () => {
    try {
      const copied = await copyTextWithFallback(shareText)
      if (copied) {
        toast('洞察内容已复制', 'success')
      } else {
        toast('复制失败，请优先使用“图片”分享', 'error')
      }
    } catch {
      toast('复制失败，请优先使用“图片”分享', 'error')
    }
  }, [copyTextWithFallback, shareText, toast])

  const handleShareInsight = useCallback(async () => {
    try {
      if (typeof navigator !== 'undefined' && (navigator as any).share) {
        await (navigator as any).share({
          title: `${resolvedName} 洞察`,
          text: shareText,
        })
        return
      }
      const copied = await copyTextWithFallback(shareText)
      if (copied) {
        toast('当前环境不支持系统分享，已自动复制内容', 'success')
      } else {
        toast('当前环境不支持分享且复制失败，请使用“图片”分享', 'error')
      }
    } catch (e: any) {
      if (e?.name === 'AbortError') return
      const copied = await copyTextWithFallback(shareText)
      if (copied) {
        toast('分享失败，已自动复制内容', 'success')
      } else {
        toast('分享失败且复制失败，请使用“图片”分享', 'error')
      }
    }
  }, [copyTextWithFallback, resolvedName, shareText, toast])

  const handleSetAlert = async () => {
    if (!symbol) return
    setAlerting(true)
    try {
      const stocks = await stocksApi.list()
      let stock = (stocks || []).find(s => s.symbol === symbol && s.market === market) || null
      if (!stock) {
        stock = await stocksApi.create({ symbol, name: resolvedName || symbol, market })
      }

      const existingAgents = (stock.agents || []).map(a => ({
        agent_name: a.agent_name,
        schedule: a.schedule || '',
        ai_model_id: a.ai_model_id ?? null,
        notify_channel_ids: a.notify_channel_ids || [],
      }))
      const hasIntraday = existingAgents.some(a => a.agent_name === 'intraday_monitor')
      const nextAgents = hasIntraday
        ? existingAgents
        : [...existingAgents, { agent_name: 'intraday_monitor', schedule: '', ai_model_id: null, notify_channel_ids: [] }]

      await stocksApi.updateAgents(stock.id, { agents: nextAgents })
      await stocksApi.triggerAgent(stock.id, 'intraday_monitor', {
        bypass_throttle: true,
        bypass_market_hours: true,
      })
      toast('已设置提醒，AI 分析已提交', 'success')
      // 轮询等待建议生成（最多 2 分钟，每 5 秒一次）
      const before = Date.now()
      const poll = setInterval(async () => {
        if (Date.now() - before > 120_000) { clearInterval(poll); setAlerting(false); return }
        await loadSuggestions()
      }, 5_000)
      await loadSuggestions()
      // 延迟清理：2 分钟后 interval 自动停止
      setTimeout(() => clearInterval(poll), 125_000)
      return
    } catch (e) {
      toast(e instanceof Error ? e.message : '设置提醒失败', 'error')
    } finally {
      setAlerting(false)
    }
  }

  const toggleWatch = useCallback(async () => {
    if (!symbol) return
    if (watchingStock && hasHolding) {
      toast('该股票存在持仓，请先删除持仓后再取消关注', 'error')
      return
    }

    setWatchToggleLoading(true)
    try {
      if (watchingStock) {
        await stocksApi.remove(watchingStock.id)
        setWatchingStock(null)
        delete stockCacheRef.current[`${market}:${symbol}`]
        toast('已取消关注', 'success')
      } else {
        const created = await stocksApi.create({ symbol, name: resolvedName || symbol, market })
        setWatchingStock(created)
        stockCacheRef.current[`${market}:${symbol}`] = created
        toast('已添加关注', 'success')
      }
    } catch (e) {
      toast(e instanceof Error ? e.message : '操作失败', 'error')
    } finally {
      setWatchToggleLoading(false)
    }
  }, [hasHolding, market, resolvedName, symbol, toast, watchingStock])

  const triggerAutoAiSuggestion = useCallback(async () => {
    // 自动建议仅针对”确认未持仓”的股票，且不自动创建股票/绑定 Agent。
    if (!symbol || !market || !holdingLoaded || holdingLoadError || hasHolding || autoSuggesting) return
    const key = `${market}:${symbol}`
    const lastTs = autoTriggeredRef.current[key] || 0
    if (Date.now() - lastTs < 5 * 60 * 1000) return
    autoTriggeredRef.current[key] = Date.now()
    setAutoSuggesting(true)
    try {
      // intraday_monitor 较 chart_analyst 更轻量、稳定，不依赖截图链路
      await stocksApi.triggerAgent(0, 'intraday_monitor', {
        allow_unbound: true,
        symbol,
        market,
        name: resolvedName || symbol,
        bypass_throttle: true,
        bypass_market_hours: true,
      })
      // 异步模式：triggerAgent 立即返回，轮询等待建议生成
      const before = Date.now()
      const poll = setInterval(async () => {
        if (Date.now() - before > 120_000) { clearInterval(poll); setAutoSuggesting(false); return }
        await loadSuggestions()
      }, 5_000)
      await loadSuggestions()
      setTimeout(() => clearInterval(poll), 125_000)
      return
    } catch (e) {
      toast(
        e instanceof Error ? e.message : '自动 AI 建议触发失败，可点击「一键设提醒」重试',
        'error'
      )
      setAutoSuggesting(false)
    }
  }, [symbol, market, resolvedName, holdingLoaded, holdingLoadError, hasHolding, autoSuggesting, loadSuggestions, toast])

  useEffect(() => {
    if (!props.open || !symbol) return
    const timer = setTimeout(() => {
      triggerAutoAiSuggestion().catch(() => undefined)
    }, 700)
    return () => clearTimeout(timer)
  }, [props.open, symbol, market, triggerAutoAiSuggestion])

  const miniKlineExtrema = useMemo(() => {
    if (!miniKlines.length) return null
    let low = Number.POSITIVE_INFINITY
    let high = Number.NEGATIVE_INFINITY
    for (const k of miniKlines) {
      low = Math.min(low, Number(k.low))
      high = Math.max(high, Number(k.high))
    }
    if (!isFinite(low) || !isFinite(high) || high <= low) return null
    return { low, high }
  }, [miniKlines])

  return (
    <>
      <Dialog open={props.open} onOpenChange={props.onOpenChange}>
        <DialogContent className="w-[92vw] max-w-6xl p-5 md:p-6 overflow-x-hidden">
          <DialogHeader className="mb-3">
            <div className="flex items-start justify-between gap-3 pr-10 md:pr-8">
              <div className="shrink-0">
                <DialogTitle className="flex items-center gap-2 flex-wrap">
                  <span className={`text-[10px] px-2 py-0.5 rounded ${badge.style}`}>{badge.label}</span>
                  <span className="break-all">{resolvedName}</span>
                  <span className="font-mono text-[12px] text-muted-foreground">({symbol})</span>
                </DialogTitle>
                <DialogDescription className="hidden md:block">概览、K线、AI建议、新闻、历史分析都在同一弹窗查看</DialogDescription>
              </div>
              <div className="hidden md:flex items-center gap-2">
                <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={() => handleExportShareImage()} disabled={imageExporting}>
                  <Download className={`w-3.5 h-3.5 ${imageExporting ? 'animate-pulse' : ''}`} />
                  <span>{imageExporting ? '生成中' : '图片'}</span>
                </Button>
                <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={() => handleShareInsight()}>
                  <Share2 className="w-3.5 h-3.5" />
                  <span>分享</span>
                </Button>
                <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={() => handleCopyShareText()}>
                  <Copy className="w-3.5 h-3.5" />
                  <span>复制</span>
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  className="h-8 px-2.5"
                  onClick={toggleWatch}
                  disabled={watchToggleLoading || (hasHolding && !!watchingStock)}
                  title={hasHolding && watchingStock ? '持仓中的股票无法取消关注' : undefined}
                >
                  {watchToggleLoading ? '处理中...' : (watchingStock ? (hasHolding ? '持仓中' : '取消关注') : '快速关注')}
                </Button>
                <StockPriceAlertPanel mode="inline" symbol={symbol} market={market} stockName={resolvedName} />
                <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={handleSetAlert} disabled={alerting}>
                  {alerting ? '设置中...' : '一键设提醒'}
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  className="h-8 px-2.5"
                  onClick={() => {
                    window.dispatchEvent(new CustomEvent('panwatch-open-chat', {
                      detail: { symbol, market, stockName: resolvedName, pageContext: buildPageContext() }
                    }))
                    props.onOpenChange(false)
                  }}
                >
                  <Sparkles className="w-3.5 h-3.5 mr-1" /> 问 AI
                </Button>
                <Button variant="outline" size="sm" className="h-8 px-2.5" onClick={() => handleRefreshAll()} disabled={loading}>
                  <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
                </Button>
              </div>
            </div>
            <div className="flex md:hidden items-center gap-2 mt-2 overflow-x-auto scrollbar-none pb-1 -mb-1">
              <Button variant="secondary" size="sm" className="h-8 px-2.5 shrink-0" onClick={() => handleExportShareImage()} disabled={imageExporting}>
                <Download className={`w-3.5 h-3.5 ${imageExporting ? 'animate-pulse' : ''}`} />
              </Button>
              <Button variant="secondary" size="sm" className="h-8 px-2.5 shrink-0" onClick={() => handleShareInsight()}>
                <Share2 className="w-3.5 h-3.5" />
              </Button>
              <Button variant="secondary" size="sm" className="h-8 px-2.5 shrink-0" onClick={() => handleCopyShareText()}>
                <Copy className="w-3.5 h-3.5" />
              </Button>
              <Button
                variant="secondary"
                size="sm"
                className="h-8 px-2.5 shrink-0"
                onClick={toggleWatch}
                disabled={watchToggleLoading || (hasHolding && !!watchingStock)}
              >
                {watchToggleLoading ? '处理中...' : (watchingStock ? (hasHolding ? '持仓中' : '取消关注') : '快速关注')}
              </Button>
              <StockPriceAlertPanel mode="inline" symbol={symbol} market={market} stockName={resolvedName} />
              <Button variant="secondary" size="sm" className="h-8 px-2.5 shrink-0" onClick={handleSetAlert} disabled={alerting}>
                {alerting ? '设置中...' : '一键设提醒'}
              </Button>
              <Button
                variant="secondary"
                size="sm"
                className="h-8 px-2.5 shrink-0"
                onClick={() => {
                  window.dispatchEvent(new CustomEvent('panwatch-open-chat', {
                    detail: { symbol, market, stockName: resolvedName, pageContext: buildPageContext() }
                  }))
                  props.onOpenChange(false)
                }}
              >
                <Sparkles className="w-3.5 h-3.5 mr-1" /> 问 AI
              </Button>
              <Button variant="outline" size="sm" className="h-8 px-2.5 shrink-0" onClick={() => handleRefreshAll()} disabled={loading}>
                <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
              </Button>
            </div>
          </DialogHeader>

          <div className="flex items-center justify-between gap-2 flex-wrap mb-3">
            <div className="flex items-center gap-1 flex-wrap">
              {[
                { id: 'overview', label: '概览' },
                { id: 'suggestions', label: `建议 (${suggestions.length})` },
                { id: 'reports', label: `报告 (${reports.length})` },
                { id: 'deep', label: deepResult ? '深度 (1)' : '深度' },
                { id: 'kline', label: 'K线' },
                { id: 'fundamentals', label: '基本面' },
                { id: 'announcements', label: `公告 (${announcements.length})` },
                { id: 'news', label: `新闻 (${news.length})` },
                { id: 'company', label: '简介' },
              ].map(item => (
                <button
                  key={item.id}
                  onClick={() => setTab(item.id as InsightTab)}
                  className={`text-[11px] px-2.5 py-1 rounded transition-colors ${
                    tab === item.id ? 'bg-primary text-primary-foreground' : 'bg-accent/50 text-muted-foreground hover:bg-accent'
                  }`}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-muted-foreground">自动刷新</span>
              <Switch
                checked={autoRefreshEnabled}
                onCheckedChange={setAutoRefreshEnabled}
                aria-label="自动刷新"
              />
              <Select value={String(autoRefreshSec)} onValueChange={(v) => setAutoRefreshSec(Number(v))}>
                <SelectTrigger className="h-7 w-[84px] text-[11px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="10">10秒</SelectItem>
                  <SelectItem value="20">20秒</SelectItem>
                  <SelectItem value="30">30秒</SelectItem>
                  <SelectItem value="60">60秒</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="max-h-[68vh] overflow-y-auto overflow-x-hidden pr-1 scrollbar">
            {tab === 'overview' && (
              <div className="space-y-3">
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 items-stretch">
                  <div className="card p-4 h-full">
                    <div className="mt-1 flex items-end justify-between gap-3">
                      <div className={`text-[34px] leading-none font-bold font-mono ${priceColor}`}>
                        {quote?.current_price != null ? formatNumber(quote.current_price) : '--'}
                      </div>
                      <div className={`text-[16px] font-mono ${changeColor}`}>
                        {quote?.change_pct != null ? `${quote.change_pct >= 0 ? '+' : ''}${quote.change_pct.toFixed(2)}%` : '--'}
                      </div>
                      {quote?.source ? (
                        <div className="text-[10px] font-mono text-muted-foreground" title={`行情来源: ${quote.source}`}>
                          来源 {quote.source}
                        </div>
                      ) : null}
                    </div>
                    <div className="mt-3 grid grid-cols-3 gap-2 text-[12px]">
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">今开</div><div className={`font-mono ${levelColor(quote?.open_price)}`}>{formatNumber(quote?.open_price)}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">最高</div><div className={`font-mono ${levelColor(quote?.high_price)}`}>{formatNumber(quote?.high_price)}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">最低</div><div className={`font-mono ${levelColor(quote?.low_price)}`}>{formatNumber(quote?.low_price)}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">成交量</div><div className="font-mono">{formatCompactNumber(quote?.volume)}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">成交额</div><div className="font-mono">{formatCompactNumber(quote?.turnover)}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">振幅</div><div className="font-mono">{amplitudePct != null ? `${amplitudePct.toFixed(2)}%` : '--'}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">换手率</div><div className="font-mono">{quote?.turnover_rate != null ? `${Number(quote.turnover_rate).toFixed(2)}%` : '--'}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">量比</div><div className="font-mono">{quote?.volume_ratio != null ? Number(quote.volume_ratio).toFixed(2) : '--'}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">市盈率</div><div className="font-mono">{quote?.pe_ratio != null ? Number(quote.pe_ratio).toFixed(2) : '--'}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">总市值</div><div className="font-mono">{formatMarketCap(quote?.total_market_value, market)}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">流通市值</div><div className="font-mono">{formatMarketCap(quote?.circulating_market_value, market)}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">52周高<InfoTip k="high_52w_low_52w" /></div><div className="font-mono">{moreInfo?.high_52w != null ? formatNumber(moreInfo.high_52w) : '--'}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">52周低<InfoTip k="high_52w_low_52w" /></div><div className="font-mono">{moreInfo?.low_52w != null ? formatNumber(moreInfo.low_52w) : '--'}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">股息率<InfoTip k="dividend_yield" /></div><div className="font-mono">{moreInfo?.dividend_yield != null ? `${(Number(moreInfo.dividend_yield) * 100).toFixed(2)}%` : '--'}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">Beta<InfoTip k="beta" /></div><div className="font-mono">{moreInfo?.beta != null ? Number(moreInfo.beta).toFixed(2) : '--'}</div></div>
                    </div>
                    <div className="mt-3 border-t border-border/50 pt-3">
                      <div className="text-[11px] text-muted-foreground mb-2">持仓信息</div>
                      {holdingAgg ? (
                        <div className="grid grid-cols-2 gap-2 text-[12px]">
                          <div className="rounded bg-emerald-500/10 px-2 py-1.5">
                            <div className="text-[10px] text-muted-foreground">持仓数量</div>
                            <div className="font-mono">{holdingAgg.quantity}</div>
                          </div>
                          <div className="rounded bg-emerald-500/10 px-2 py-1.5">
                            <div className="text-[10px] text-muted-foreground">持仓成本(单价)</div>
                            <div
                              className={`font-mono ${
                                quote?.current_price != null
                                  ? quote.current_price > holdingAgg.unitCost
                                    ? 'text-rose-500'
                                    : quote.current_price < holdingAgg.unitCost
                                      ? 'text-emerald-500'
                                      : 'text-foreground'
                                  : 'text-foreground'
                              }`}
                            >
                              {formatNumber(holdingAgg.unitCost)}
                            </div>
                          </div>
                          <div className="rounded bg-emerald-500/10 px-2 py-1.5">
                            <div className="text-[10px] text-muted-foreground">持仓市值</div>
                            <div className="font-mono">{formatCompactNumber(holdingAgg.marketValue)}</div>
                          </div>
                          <div className="rounded bg-emerald-500/10 px-2 py-1.5">
                            <div className="text-[10px] text-muted-foreground">总盈亏</div>
                            <div className={`font-mono ${holdingAgg.pnl >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                              {holdingAgg.pnl >= 0 ? '+' : ''}{formatCompactNumber(holdingAgg.pnl)}
                            </div>
                          </div>
                        </div>
                      ) : (
                        <div className="text-[11px] text-muted-foreground">未在持仓中</div>
                      )}
                      <AddPositionCalculator
                        symbol={symbol}
                        market={market}
                        currentQuantity={holdingAgg?.quantity ?? 0}
                        currentCost={holdingAgg?.unitCost ?? 0}
                        currentPrice={quote?.current_price ?? null}
                      />
                    </div>
                  </div>

                  <div className="card p-4 h-full">
                    <div className="text-[12px] text-muted-foreground mb-2">迷你K线</div>
                    {!klineSummary ? (
                      <div className="text-[12px] text-muted-foreground py-8">暂无K线摘要</div>
                    ) : (
                      <>
                        {miniKlineLoading ? (
                          <div className="h-32 rounded bg-accent/30 animate-pulse" />
                        ) : miniKlines.length > 0 && miniKlineExtrema ? (
                          <svg
                            viewBox="0 0 320 120"
                            className="w-full h-32 cursor-pointer"
                            onClick={() => setTab('kline')}
                            onMouseLeave={() => setMiniHoverIdx(null)}
                            onMouseMove={(e) => {
                              const rect = e.currentTarget.getBoundingClientRect()
                              const x = e.clientX - rect.left
                              const ratio = rect.width > 0 ? x / rect.width : 0
                              const idx = Math.floor(ratio * miniKlines.length)
                              setMiniHoverIdx(Math.max(0, Math.min(miniKlines.length - 1, idx)))
                            }}
                          >
                            <title>点击进入交互式K线</title>
                            {miniKlines.map((k, idx) => {
                              const xStep = 320 / miniKlines.length
                              const x = xStep * idx + xStep / 2
                              const bodyW = Math.max(2, xStep * 0.5)
                              const toY = (v: number) => 114 - ((v - miniKlineExtrema.low) / (miniKlineExtrema.high - miniKlineExtrema.low)) * 100
                              const yOpen = toY(Number(k.open))
                              const yClose = toY(Number(k.close))
                              const yHigh = toY(Number(k.high))
                              const yLow = toY(Number(k.low))
                              const up = Number(k.close) >= Number(k.open)
                              const color = up ? '#ef4444' : '#10b981'
                              const bodyTop = Math.min(yOpen, yClose)
                              const bodyH = Math.max(1.4, Math.abs(yOpen - yClose))
                              const active = miniHoverIdx === idx
                              return (
                                <g key={`${k.date}-${idx}`}>
                                  {active && <rect x={x - xStep / 2} y={6} width={xStep} height={108} fill="rgba(59,130,246,0.10)" />}
                                  <line x1={x} y1={yHigh} x2={x} y2={yLow} stroke={color} strokeWidth="1" />
                                  <rect x={x - bodyW / 2} y={bodyTop} width={bodyW} height={bodyH} fill={color} rx="0.6" />
                                </g>
                              )
                            })}
                          </svg>
                        ) : (
                          <div className="h-32 text-[11px] text-muted-foreground flex items-center justify-center">暂无迷你K线</div>
                        )}
                        <div className="mt-2 rounded bg-accent/10 p-2.5">
                          <TechnicalIndicatorStrip
                            klineSummary={klineSummary}
                            technicalSuggestion={technicalFallbackSuggestion}
                            stockName={resolvedName}
                            stockSymbol={symbol}
                            market={market}
                            hasPosition={!!props.hasPosition}
                            score={Number(technicalScored?.score ?? 0)}
                            evidence={technicalScored?.evidence || []}
                          />
                        </div>
                      </>
                    )}
                  </div>
                </div>

                {/* TQ 扩展指标 · 104字段精简18项（委比/封单/竞价/连板/涨幅/估值） */}
                {moreInfo && (
                  <div className="card p-4">
                    <div className="flex items-center justify-between mb-2">
                      <div className="text-[11px] text-muted-foreground">扩展指标 · TQ实时</div>
                      <div className="text-[10px] text-muted-foreground">{moreInfoLoading ? '更新中…' : moreInfo.quote_time ? formatTime(moreInfo.quote_time) : ''}</div>
                    </div>
                    <div className="grid grid-cols-3 md:grid-cols-4 xl:grid-cols-6 gap-2 text-[11px]">
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">委比<InfoTip k="commission_ratio" /></div><div className="font-mono">{moreInfo.commission_ratio != null ? `${Number(moreInfo.commission_ratio).toFixed(2)}%` : '--'}</div></div>
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">封单额<InfoTip k="limit_up_amount" /></div><div className="font-mono">{moreInfo.limit_up_amount != null && moreInfo.limit_up_amount !== 0 ? `${(moreInfo.limit_up_amount/10000).toFixed(2)}亿` : '--'}</div></div>
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">封成比<InfoTip k="limit_up_ratio" /></div><div className="font-mono">{moreInfo.limit_up_ratio != null ? Number(moreInfo.limit_up_ratio).toFixed(2) : '--'}</div></div>
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">竞价金额<InfoTip k="open_amount" /></div><div className="font-mono">{moreInfo.open_amount != null && moreInfo.open_amount !== 0 ? `${(moreInfo.open_amount/10000).toFixed(2)}亿` : '--'}</div></div>
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">竞价涨停买<InfoTip k="open_limit_buy" /></div><div className="font-mono">{moreInfo.open_limit_buy != null && moreInfo.open_limit_buy !== 0 ? `${(moreInfo.open_limit_buy/10000).toFixed(2)}亿` : '--'}</div></div>
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">连板天<InfoTip k="consecutive_limit_days" /></div><div className="font-mono">{moreInfo.consecutive_limit_days ?? '--'}</div></div>
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">连涨天<InfoTip k="consecutive_up_days" /></div><div className="font-mono">{moreInfo.consecutive_up_days ?? '--'}</div></div>
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">5日涨幅<InfoTip k="change_pct_5d" /></div><div className={`font-mono ${moreInfo.change_pct_5d != null && moreInfo.change_pct_5d>0 ? 'text-rose-500' : moreInfo.change_pct_5d!=null&&moreInfo.change_pct_5d<0 ? 'text-emerald-500' : ''}`}>{moreInfo.change_pct_5d != null ? `${moreInfo.change_pct_5d>0?'+':''}${moreInfo.change_pct_5d.toFixed(2)}%` : '--'}</div></div>
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">20日涨幅<InfoTip k="change_pct_20d" /></div><div className={`font-mono ${moreInfo.change_pct_20d != null && moreInfo.change_pct_20d>0 ? 'text-rose-500' : moreInfo.change_pct_20d!=null&&moreInfo.change_pct_20d<0 ? 'text-emerald-500' : ''}`}>{moreInfo.change_pct_20d != null ? `${moreInfo.change_pct_20d>0?'+':''}${moreInfo.change_pct_20d.toFixed(2)}%` : '--'}</div></div>
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">年初至今<InfoTip k="change_pct_ytd" /></div><div className={`font-mono ${moreInfo.change_pct_ytd != null && moreInfo.change_pct_ytd>0 ? 'text-rose-500' : moreInfo.change_pct_ytd!=null&&moreInfo.change_pct_ytd<0 ? 'text-emerald-500' : ''}`}>{moreInfo.change_pct_ytd != null ? `${moreInfo.change_pct_ytd>0?'+':''}${moreInfo.change_pct_ytd.toFixed(2)}%` : '--'}</div></div>
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">动态PE<InfoTip k="pe_dynamic" /></div><div className="font-mono">{moreInfo.pe_dynamic != null ? Number(moreInfo.pe_dynamic).toFixed(2) : '--'}</div></div>
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">市净率<InfoTip k="pb" /></div><div className="font-mono">{moreInfo.pb != null ? Number(moreInfo.pb).toFixed(2) : '--'}</div></div>
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">股息率<InfoTip k="dividend_yield" /></div><div className="font-mono">{moreInfo.dividend_yield != null ? `${Number(moreInfo.dividend_yield).toFixed(2)}%` : '--'}</div></div>
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">Beta<InfoTip k="beta" /></div><div className="font-mono">{moreInfo.beta != null ? Number(moreInfo.beta).toFixed(2) : '--'}</div></div>
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">5日均价<InfoTip k="ma5_price" /></div><div className="font-mono">{moreInfo.ma5_price != null ? formatNumber(moreInfo.ma5_price) : '--'}</div></div>
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">52周高/低<InfoTip k="high_52w_low_52w" /></div><div className="font-mono text-[10px]">{moreInfo.high_52w != null ? Number(moreInfo.high_52w).toFixed(2) : '--'} / {moreInfo.low_52w != null ? Number(moreInfo.low_52w).toFixed(2) : '--'}</div></div>
                    </div>
                    {/* L2 逐笔（需Level2，开通后实时） */}
                    <div className="mt-3 pt-3 border-t border-border/40 grid grid-cols-3 md:grid-cols-6 gap-2 text-[11px]">
                      <div className="rounded bg-amber-500/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">L2逐笔数<InfoTip k="l2_tick_num" /></div><div className="font-mono">{moreInfo.l2_tick_num != null ? Number(moreInfo.l2_tick_num).toLocaleString() : '--'}</div></div>
                      <div className="rounded bg-amber-500/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">L2委托数<InfoTip k="l2_order_num" /></div><div className="font-mono">{moreInfo.l2_order_num != null ? Number(moreInfo.l2_order_num).toLocaleString() : '--'}</div></div>
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">总买量<InfoTip k="total_buy_vol" /></div><div className="font-mono">{moreInfo.total_buy_vol != null ? `${(moreInfo.total_buy_vol/100).toFixed(0)}手` : '--'}</div></div>
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">总卖量<InfoTip k="total_sell_vol" /></div><div className="font-mono">{moreInfo.total_sell_vol != null ? `${(moreInfo.total_sell_vol/100).toFixed(0)}手` : '--'}</div></div>
                      <div className="rounded bg-rose-500/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">撤买<InfoTip k="cancel_buy" /></div><div className="font-mono">{moreInfo.cancel_buy != null ? `${(moreInfo.cancel_buy).toFixed(0)}` : '--'}</div></div>
                      <div className="rounded bg-emerald-500/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">撤卖<InfoTip k="cancel_sell" /></div><div className="font-mono">{moreInfo.cancel_sell != null ? `${(moreInfo.cancel_sell).toFixed(0)}` : '--'}</div></div>
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">主力净流入<InfoTip k="zjl_hb" /></div><div className={`font-mono ${moreInfo.zjl_hb != null ? (moreInfo.zjl_hb >= 0 ? 'text-red-600' : 'text-green-700') : ''}`}>{moreInfo.zjl_hb != null ? `${(moreInfo.zjl_hb).toFixed(0)}万` : '--'}</div></div>
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground flex items-center">主买净额<InfoTip k="zjl" /></div><div className={`font-mono ${moreInfo.zjl != null ? (moreInfo.zjl >= 0 ? 'text-red-600' : 'text-green-700') : ''}`}>{moreInfo.zjl != null ? `${(moreInfo.zjl).toFixed(0)}万` : '--'}</div></div>
                    </div>
                    {moreInfo.total_buy_vol != null && moreInfo.total_sell_vol != null && (moreInfo.total_buy_vol + moreInfo.total_sell_vol) > 0 && (
                      <div className="mt-2 h-1.5 w-full flex rounded overflow-hidden bg-muted">
                        <div className="bg-rose-500" style={{width: `${(moreInfo.total_buy_vol / (moreInfo.total_buy_vol + moreInfo.total_sell_vol) * 100).toFixed(1)}%`}} />
                        <div className="bg-emerald-500 flex-1" />
                      </div>
                    )}
                  </div>
                )}

                {/* 暗盘资金 · TQ逐笔还原(盘后, ZCode TQ4 采集) */}
                {darkFlowTq && darkFlowTq.data_status === 'complete' && (
                  <div className="card p-4">
                    <div className="flex items-center justify-between mb-2">
                      <div className="text-[11px] text-muted-foreground">暗盘资金 · TQ逐笔还原</div>
                      <div className="text-[10px] text-muted-foreground">{darkFlowTq.date ? `盘后 ${darkFlowTq.date}` : '盘后'}</div>
                    </div>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px]">
                      <div className="rounded bg-violet-500/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">超大单净额</div><div className={`font-mono ${darkFlowTq.xl_net != null ? (darkFlowTq.xl_net >= 0 ? 'text-red-600' : 'text-green-700') : ''}`}>{darkFlowTq.xl_net != null ? `${darkFlowTq.xl_net.toFixed(0)}万` : '--'}</div></div>
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">大单净额</div><div className={`font-mono ${darkFlowTq.large_net != null ? (darkFlowTq.large_net >= 0 ? 'text-red-600' : 'text-green-700') : ''}`}>{darkFlowTq.large_net != null ? `${darkFlowTq.large_net.toFixed(0)}万` : '--'}</div></div>
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">中单净额</div><div className={`font-mono ${darkFlowTq.mid_net != null ? (darkFlowTq.mid_net >= 0 ? 'text-red-600' : 'text-green-700') : ''}`}>{darkFlowTq.mid_net != null ? `${darkFlowTq.mid_net.toFixed(0)}万` : '--'}</div></div>
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">小单净额</div><div className={`font-mono ${darkFlowTq.small_net != null ? (darkFlowTq.small_net >= 0 ? 'text-red-600' : 'text-green-700') : ''}`}>{darkFlowTq.small_net != null ? `${darkFlowTq.small_net.toFixed(0)}万` : '--'}</div></div>
                    </div>
                    <div className="mt-3 pt-3 border-t border-border/40 grid grid-cols-3 md:grid-cols-5 gap-2 text-[11px]">
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">拆单委托</div><div className="font-mono">{darkFlowTq.split_order_count != null ? darkFlowTq.split_order_count.toLocaleString() : '--'}</div></div>
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">平均拆单份数</div><div className="font-mono">{darkFlowTq.avg_split_parts != null ? darkFlowTq.avg_split_parts.toFixed(1) : '--'}</div></div>
                      <div className="rounded bg-accent/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">撤单比</div><div className="font-mono">{darkFlowTq.cancel_ratio != null ? `${(darkFlowTq.cancel_ratio * 100).toFixed(1)}%` : '--'}</div></div>
                      <div className="rounded bg-rose-500/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">撤买量</div><div className="font-mono">{darkFlowTq.cancel_buy_vol != null ? `${darkFlowTq.cancel_buy_vol.toFixed(0)}` : '--'}</div></div>
                      <div className="rounded bg-emerald-500/10 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">撤卖量</div><div className="font-mono">{darkFlowTq.cancel_sell_vol != null ? `${darkFlowTq.cancel_sell_vol.toFixed(0)}` : '--'}</div></div>
                    </div>
                    {(darkFlowTq.tuopan || darkFlowTq.yapan || darkFlowTq.suopan) && (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {darkFlowTq.tuopan && <span className="rounded bg-amber-500/15 px-2 py-0.5 text-[10px] text-amber-600">⚠️ 托盘</span>}
                        {darkFlowTq.yapan && <span className="rounded bg-sky-500/15 px-2 py-0.5 text-[10px] text-sky-600">压盘</span>}
                        {darkFlowTq.suopan && <span className="rounded bg-violet-500/15 px-2 py-0.5 text-[10px] text-violet-600">锁盘</span>}
                      </div>
                    )}
                  </div>
                )}

                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3 items-stretch">
                  <div className="card p-4 h-full flex flex-col">
                    <div className="flex items-center justify-between mb-2">
                      <div className="text-[12px] text-muted-foreground">AI建议</div>
                      <Button variant="ghost" size="sm" className="h-7 px-2 text-[11px] text-muted-foreground" onClick={() => setTab('suggestions')}>
                        更多
                      </Button>
                      {autoSuggesting && suggestions.length > 0 && (
                        <div className="text-[10px] text-primary">更新中...</div>
                      )}
                    </div>
                    {suggestions.length > 0 ? (
                      <div className="space-y-2">
                        <SuggestionBadge
                          suggestion={suggestions[0]}
                          stockName={resolvedName}
                          stockSymbol={symbol}
                          market={market}
                          hasPosition={!!props.hasPosition}
                          showTechnicalCompanion={false}
                        />
                        <div className="rounded bg-accent/10 p-2 text-[11px]">
                          <div className="text-muted-foreground">核心判断</div>
                          <div className="mt-1 text-foreground line-clamp-2">{suggestions[0].signal || suggestions[0].reason || '暂无说明'}</div>
                          <div className="mt-1 text-muted-foreground">动作: {suggestions[0].action_label || suggestions[0].action || '--'}</div>
                          <div className="mt-1 text-foreground line-clamp-2">依据: {suggestions[0].reason || '暂无补充依据'}</div>
                          <div className="mt-1 text-muted-foreground">
                            来源: {suggestions[0].agent_label || suggestions[0].agent_name || 'AI'}{suggestions[0].created_at ? ` · ${formatTime(suggestions[0].created_at)}` : ''}
                          </div>
                        </div>
                        {suggestions.length > 1 && (
                          <div className="rounded bg-accent/10 p-2 text-[11px]">
                            <div className="text-muted-foreground mb-1">近期补充建议</div>
                            {suggestions.slice(1, 3).map((item, idx) => (
                              <div key={`${item.created_at || 'extra'}-${idx}`} className="line-clamp-1 text-foreground">
                                {item.action_label || item.action} · {item.signal || item.reason || '--'}
                              </div>
                            ))}
                          </div>
                        )}
                        <div className="text-[10px] text-primary min-h-[14px]">{autoSuggesting && suggestions.length === 0 ? '正在自动生成 AI 建议...' : ''}</div>
                      </div>
                    ) : (
                      <div className="text-[12px] text-muted-foreground py-6">
                        {autoSuggesting ? '正在自动生成 AI 建议（通常 5-15 秒）...' : '暂无 AI 建议'}
                      </div>
                    )}
                  </div>

                  <div className="card p-4 h-full flex flex-col">
                    <div className="flex items-center justify-between mb-2">
                      <div className="text-[12px] text-muted-foreground">新闻</div>
                      <Button variant="ghost" size="sm" className="h-7 px-2 text-[11px] text-muted-foreground" onClick={() => setTab('news')}>
                        更多
                      </Button>
                    </div>
                    <div className="flex-1 space-y-2">
                      {news.length === 0 ? (
                        <div className="text-[12px] text-muted-foreground py-6">暂无相关新闻</div>
                      ) : (
                        news.slice(0, 3).map((item, idx) => (
                          <a
                            key={`${item.publish_time || 'n'}-${idx}`}
                            href={item.url}
                            target="_blank"
                            rel="noreferrer"
                            className="block rounded-lg border border-border/30 bg-accent/10 p-2.5 hover:bg-accent/20 transition-colors"
                          >
                            <div className="text-[12px] text-foreground line-clamp-2">{item.title}</div>
                            <div className="mt-1 text-[10px] text-muted-foreground">{item.source_label || item.source} · {formatTime(item.publish_time)}</div>
                          </a>
                        ))
                      )}
                    </div>
                  </div>
                  <div className="card p-4 h-full flex flex-col">
                    <div className="flex items-center justify-between gap-2 mb-2">
                      <div className="text-[12px] text-muted-foreground">AI报告</div>
                      <Button variant="ghost" size="sm" className="h-7 px-2 text-[11px] text-muted-foreground" onClick={() => setTab('reports')}>
                        更多
                      </Button>
                    </div>
                    {!latestReport ? (
                      <div className="text-[12px] text-muted-foreground py-3">暂无报告</div>
                    ) : (
                      <div className="rounded-lg border border-border/30 bg-accent/10 p-2.5">
                        <div className="text-[11px] text-muted-foreground">
                          {AGENT_LABELS[latestReport.agent_name] || latestReport.agent_name} · {latestReport.analysis_date}
                        </div>
                        <div className="mt-1 text-[13px] font-medium line-clamp-1">{latestReport.title || '报告摘要'}</div>
                        <div className="mt-1 text-[12px] text-foreground/90 line-clamp-3">
                          {markdownToPlainText(latestReport.content) || '暂无报告内容'}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}

            {tab === 'kline' && (
              <div className="card p-4">
                <InteractiveKline
                  symbol={symbol}
                  market={market}
                  initialInterval={klineInterval}
                  mainIntent={mainIntent}
                />
              </div>
            )}

            {tab === 'reports' && (
              <div className="space-y-3">
                <div className="card p-3">
                  <div className="flex items-center gap-1">
                    {([
                      { key: 'premarket_outlook', label: '盘前' },
                      { key: 'daily_report', label: '盘后' },
                      { key: 'news_digest', label: '新闻' },
                    ] as const).map(item => (
                      <button
                        key={item.key}
                        onClick={() => setReportTab(item.key)}
                        className={`text-[11px] px-2.5 py-1 rounded ${
                          reportTab === item.key ? 'bg-primary text-primary-foreground' : 'bg-accent/60 text-muted-foreground hover:bg-accent'
                        }`}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                </div>
                {!activeReport ? (
                  <div className="card p-6 text-[12px] text-muted-foreground text-center">暂无报告</div>
                ) : (
                  <div className="card p-4 space-y-3">
                    <div className="text-[11px] text-muted-foreground">
                      {AGENT_LABELS[activeReport.agent_name] || activeReport.agent_name} · {activeReport.analysis_date}
                    </div>
                    <div className="text-[15px] font-medium">{activeReport.title || '报告摘要'}</div>
                    {activeReport.suggestions && (activeReport.suggestions as any)?.[symbol]?.action_label && (
                      <div className="text-[11px] inline-flex px-2 py-0.5 rounded bg-primary/10 text-primary">
                        {(activeReport.suggestions as any)[symbol].action_label}
                      </div>
                    )}
                    <div className="rounded-lg bg-accent/10 p-3">
                      <div className="prose prose-sm dark:prose-invert max-w-none text-foreground/90 break-words">
                        <StockReportMarkdown content={activeReport.content || '暂无报告内容'} />
                      </div>
                    </div>
                    {(activeReport.prompt_context || activeReport.context_payload || activeReport.news_debug) && (
                      <details className="rounded-lg border border-border/40 bg-accent/10 p-3">
                        <summary className="cursor-pointer text-[12px] text-muted-foreground select-none">查看分析上下文</summary>
                        {activeReport.prompt_stats ? (
                          <div className="mt-2">
                            <div className="text-[11px] text-muted-foreground mb-1">Prompt统计</div>
                            <pre className="text-[11px] text-muted-foreground whitespace-pre-wrap break-words overflow-x-auto">{JSON.stringify(activeReport.prompt_stats, null, 2)}</pre>
                          </div>
                        ) : null}
                        {activeReport.news_debug ? (
                          <div className="mt-2">
                            <div className="text-[11px] text-muted-foreground mb-1">新闻注入明细</div>
                            <pre className="text-[11px] text-muted-foreground whitespace-pre-wrap break-words overflow-x-auto">{JSON.stringify(activeReport.news_debug, null, 2)}</pre>
                          </div>
                        ) : null}
                        {activeReport.context_payload ? (
                          <div className="mt-2">
                            <div className="text-[11px] text-muted-foreground mb-1">上下文快照</div>
                            <pre className="text-[11px] text-muted-foreground whitespace-pre-wrap break-words overflow-x-auto max-h-[220px] overflow-y-auto">{JSON.stringify(activeReport.context_payload, null, 2)}</pre>
                          </div>
                        ) : null}
                        {activeReport.prompt_context ? (
                          <div className="mt-2">
                            <div className="text-[11px] text-muted-foreground mb-1">Prompt原文</div>
                            <pre className="text-[11px] text-muted-foreground whitespace-pre-wrap break-words overflow-x-auto max-h-[220px] overflow-y-auto">{activeReport.prompt_context}</pre>
                          </div>
                        ) : null}
                      </details>
                    )}
                  </div>
                )}
              </div>
            )}

            {tab === 'deep' && (
              <div className="space-y-3">
                {deepResult && (
                  <div className="flex justify-end">
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-8 px-2.5"
                      onClick={() =>
                        window.open(
                          `/analysis/${symbol}/${deepResult.timestamp ? String(deepResult.timestamp).slice(0, 10) : new Date().toISOString().slice(0, 10)}`,
                          '_blank',
                        )
                      }
                    >
                      打开详情页 ↗
                    </Button>
                  </div>
                )}
                <DeepAnalysisSection
                  loading={deepLoading}
                  loaded={deepLoaded}
                  result={deepResult}
                  history={deepHistory}
                  historyLoading={deepHistoryLoading}
                  showAnalyst={deepShowAnalyst}
                  setShowAnalyst={setDeepShowAnalyst}
                  showDebate={deepShowDebate}
                  setShowDebate={setDeepShowDebate}
                  onRefresh={loadDeepResult}
                />
              </div>
            )}

            {tab === 'suggestions' && (
              <div className="space-y-3">
                <div className="card p-3 flex items-center justify-between gap-3">
                  <div className="text-[12px] text-muted-foreground">显示过期建议</div>
                  <div className="flex items-center gap-2">
                    <span className="text-[11px] text-muted-foreground">{includeExpiredSuggestions ? '包含过期' : '仅有效'}</span>
                    <Switch
                      checked={includeExpiredSuggestions}
                      onCheckedChange={setIncludeExpiredSuggestions}
                      aria-label="显示过期建议"
                    />
                  </div>
                </div>
                {suggestions.length === 0 ? (
                  technicalFallbackSuggestion ? (
                    <div className="card p-4">
                      <SuggestionBadge suggestion={technicalFallbackSuggestion} stockName={resolvedName} stockSymbol={symbol} kline={klineSummary} hasPosition={!!props.hasPosition} />
                      <div className="mt-2 text-[10px] text-muted-foreground">
                        {autoSuggesting ? '正在自动生成 AI 建议（通常 5-15 秒）...' : '当前显示技术指标基础建议'}
                      </div>
                    </div>
                  ) : (
                    <div className="card p-6 text-[12px] text-muted-foreground text-center">
                      {autoSuggesting ? '正在自动生成 AI 建议（通常 5-15 秒）...' : '暂无建议'}
                    </div>
                  )
                ) : (
                  <div className="max-h-[56vh] overflow-y-auto pr-1 scrollbar space-y-3">
                    {suggestions.map((item, idx) => (
                      <div key={`${item.created_at || 's'}-${idx}`} className="card p-4">
                        <SuggestionBadge suggestion={item} stockName={resolvedName} stockSymbol={symbol} kline={klineSummary} hasPosition={!!props.hasPosition} />
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {tab === 'news' && (
              <div className="space-y-3">
                <div className="flex items-center justify-end">
                  <Select value={newsHours} onValueChange={setNewsHours}>
                    <SelectTrigger className="h-8 w-[110px] text-[12px]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="6">近6小时</SelectItem>
                      <SelectItem value="12">近12小时</SelectItem>
                      <SelectItem value="24">近24小时</SelectItem>
                      <SelectItem value="48">近48小时</SelectItem>
                      <SelectItem value="168">近7天</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {news.length === 0 ? (
                  <div className="card p-6 text-[12px] text-muted-foreground text-center">暂无相关新闻</div>
                ) : (
                  news.map((item, idx) => (
                    <a
                      key={`${item.publish_time || 'n'}-${idx}`}
                      href={item.url}
                      target="_blank"
                      rel="noreferrer"
                      className="card block p-4 hover:bg-accent/20 transition-colors"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="text-[13px] font-medium text-foreground line-clamp-2">{item.title}</div>
                        <ExternalLink className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                      </div>
                      <div className="mt-2 text-[11px] text-muted-foreground">{item.source_label || item.source} · {formatTime(item.publish_time)}</div>
                    </a>
                  ))
                )}
              </div>
            )}

            {tab === 'announcements' && (
              <div className="space-y-3">
                <div className="flex items-center justify-end">
                  <Select value={announcementHours} onValueChange={setAnnouncementHours}>
                    <SelectTrigger className="h-8 w-[110px] text-[12px]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="168">近7天</SelectItem>
                      <SelectItem value="336">近14天</SelectItem>
                      <SelectItem value="720">近30天</SelectItem>
                      <SelectItem value="2160">近90天</SelectItem>
                      <SelectItem value="4320">近180天</SelectItem>
                      <SelectItem value="24">近24小时</SelectItem>
                      <SelectItem value="48">近48小时</SelectItem>
                      <SelectItem value="72">近72小时</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {announcements.length === 0 ? (
                  <div className="card p-6 text-[12px] text-muted-foreground text-center">暂无公告</div>
                ) : (
                  announcements.map((item, idx) => (
                    <a
                      key={`${item.publish_time || 'a'}-${idx}`}
                      href={item.url}
                      target="_blank"
                      rel="noreferrer"
                      className="card block p-4 hover:bg-accent/20 transition-colors"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="text-[13px] font-medium text-foreground line-clamp-2">{item.title}</div>
                        <ExternalLink className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                      </div>
                      <div className="mt-2 text-[11px] text-muted-foreground">{item.source_label || item.source} · {formatTime(item.publish_time)}</div>
                    </a>
                  ))
                )}
              </div>
            )}

            {tab === 'company' && (
              <div className="space-y-3">
                {companyLoading ? (
                  <div className="card p-6 text-[12px] text-muted-foreground text-center">加载公司信息...</div>
                ) : !companyInfo || (!companyInfo.name && companyInfo.note) ? (
                  <div className="card p-6 text-[12px] text-muted-foreground text-center">
                    {companyInfo?.note || '暂无公司信息'}
                  </div>
                ) : (
                  <div className="space-y-3">
                    {/* 主营 */}
                    {companyInfo.bscope && (
                      <div className="card p-4">
                        <div className="text-[11px] text-muted-foreground mb-1">主营业务</div>
                        <div className="text-[13px] text-foreground font-medium">{companyInfo.bscope}</div>
                      </div>
                    )}
                    {/* 公司简介 */}
                    {companyInfo.desc && (
                      <div className="card p-4">
                        <div className="text-[11px] text-muted-foreground mb-1">公司简介</div>
                        <div className="text-[12px] text-foreground/90 leading-relaxed">{companyInfo.desc}</div>
                      </div>
                    )}
                    {/* 基本信息网格 */}
                    <div className="card p-4">
                      <div className="text-[11px] text-muted-foreground mb-2">基本信息</div>
                      <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-[12px]">
                        {companyInfo.name && (
                          <>
                            <div className="text-muted-foreground">公司全称</div>
                            <div className="text-foreground text-right break-words">{companyInfo.name}</div>
                          </>
                        )}
                        {companyInfo.ename && (
                          <>
                            <div className="text-muted-foreground">英文名</div>
                            <div className="text-foreground text-right break-words">{companyInfo.ename}</div>
                          </>
                        )}
                        {companyInfo.market_board && (
                          <>
                            <div className="text-muted-foreground">交易所</div>
                            <div className="text-foreground text-right">{companyInfo.market_board}</div>
                          </>
                        )}
                        {companyInfo.list_date && (
                          <>
                            <div className="text-muted-foreground">上市日期</div>
                            <div className="text-foreground text-right">{companyInfo.list_date}</div>
                          </>
                        )}
                        {companyInfo.reg_capital && (
                          <>
                            <div className="text-muted-foreground">注册资本</div>
                            <div className="text-foreground text-right">{companyInfo.reg_capital}</div>
                          </>
                        )}
                        {companyInfo.list_status && (
                          <>
                            <div className="text-muted-foreground">企业性质</div>
                            <div className="text-foreground text-right">{companyInfo.list_status}</div>
                          </>
                        )}
                        {companyInfo.issuer && (
                          <>
                            <div className="text-muted-foreground">主承销商</div>
                            <div className="text-foreground text-right">{companyInfo.issuer}</div>
                          </>
                        )}
                        {companyInfo.secretary && (
                          <>
                            <div className="text-muted-foreground">董秘</div>
                            <div className="text-foreground text-right">{companyInfo.secretary}</div>
                          </>
                        )}
                        {companyInfo.area && (
                          <>
                            <div className="text-muted-foreground">注册地址</div>
                            <div className="text-foreground text-right break-words">{companyInfo.area}</div>
                          </>
                        )}
                        {companyInfo.website && (
                          <>
                            <div className="text-muted-foreground">官网</div>
                            <a href={companyInfo.website} target="_blank" rel="noreferrer" className="text-right text-primary hover:underline break-words">{companyInfo.website}</a>
                          </>
                        )}
                      </div>
                    </div>
                    {/* 概念板块 */}
                    {companyInfo.concepts && (
                      <div className="card p-4">
                        <div className="text-[11px] text-muted-foreground mb-2">概念板块</div>
                        <div className="flex flex-wrap gap-1.5">
                          {companyInfo.concepts.split(',').filter(Boolean).map((c) => (
                            <span key={c} className="rounded-full bg-accent/50 px-2 py-0.5 text-[11px] text-foreground/80">{c.trim()}</span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {tab === 'fundamentals' && (
              <FundamentalsPanel data={fundamentals} loading={fundamentalsLoading} loaded={fundamentalsLoaded} />
            )}

          </div>
        </DialogContent>
      </Dialog>

    </>
  )
}

const DEEP_DECISION_COLOR: Record<string, string> = {
  buy: 'text-emerald-600 dark:text-emerald-400',
  hold: 'text-amber-600 dark:text-amber-400',
  sell: 'text-rose-600 dark:text-rose-400',
}

const DEEP_STAGE_LABEL: Record<string, string> = {
  market: '技术分析师',
  social: '情绪分析师',
  news: '新闻分析师',
  fundamentals: '基本面分析师',
}

function DeepAnalysisSection({
  loading,
  loaded,
  result,
  history,
  historyLoading,
  showAnalyst,
  setShowAnalyst,
  showDebate,
  setShowDebate,
  onRefresh,
}: {
  loading: boolean
  loaded: boolean
  result: DeepAnalysisResult | null
  history: HistoryComparisonResponse | null
  historyLoading: boolean
  showAnalyst: boolean
  setShowAnalyst: (v: boolean) => void
  showDebate: boolean
  setShowDebate: (v: boolean) => void
  onRefresh: () => void
}) {
  if (loading && !loaded) {
    return (
      <div className="card p-6 text-center text-[12px] text-muted-foreground">
        <span className="inline-block w-4 h-4 border-2 border-primary/30 border-t-primary rounded-full animate-spin mr-2 align-middle" />
        正在加载深度分析报告...
      </div>
    )
  }
  if (!result && !history?.items?.length) {
    return (
      <div className="card p-6 text-center text-[12px] text-muted-foreground space-y-2">
        <div>暂无深度分析报告</div>
        <div className="text-[11px] text-muted-foreground/70">
          可在持仓 / 自选页点击 🧠 深度分析按钮触发
        </div>
      </div>
    )
  }

  const rawData = (result?.raw_data || {}) as Partial<DeepAnalysisResult['raw_data']>
  const sug = rawData.suggestion
  const reports = rawData.analyst_reports || { market: '', social: '', news: '', fundamentals: '' }
  const debate = rawData.debate_history
  const costUsd = rawData.cost_usd

  return (
    <div className="space-y-3 text-[13px]">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[11px] text-muted-foreground">
          TradingAgents 深度{result?.timestamp ? ` · ${result.timestamp.slice(0, 16).replace('T', ' ')}` : ''}
        </div>
        <Button variant="ghost" size="sm" className="h-7 px-2 text-[11px]" onClick={onRefresh} disabled={loading || historyLoading}>
          <RefreshCw className={`w-3.5 h-3.5 ${loading || historyLoading ? 'animate-spin' : ''}`} />
        </Button>
      </div>

      {sug && (
        <div className="rounded-lg bg-accent/30 p-4 space-y-2">
          <div className="flex items-center gap-3">
            <span className={`text-[20px] font-bold ${DEEP_DECISION_COLOR[sug.action] || ''}`}>
              {sug.action_label}
            </span>
            {typeof sug.confidence === 'number' && (
              <span className="text-[12px] text-muted-foreground">
                置信度 {sug.confidence.toFixed(1)} / 10
              </span>
            )}
          </div>
          {sug.reason && <div className="text-[12px] text-foreground/80">{sug.reason.slice(0, 240)}</div>}
          {typeof costUsd === 'number' && (
            <div className="text-[10px] text-muted-foreground mt-2">成本:${costUsd.toFixed(4)}</div>
          )}
        </div>
      )}

      <DeepHistoryComparison history={history} loading={historyLoading} />

      {result?.content && (
        <div className="rounded-lg border border-border/50 p-4">
          <div className="prose prose-sm dark:prose-invert max-w-none break-words">
            <StockReportMarkdown content={result.content} />
          </div>
        </div>
      )}

      {result && (
        <div>
          <button
            className="text-[12px] text-muted-foreground hover:text-foreground flex items-center gap-1"
            onClick={() => setShowAnalyst(!showAnalyst)}
          >
            {showAnalyst ? '▼' : '▶'} 4 位分析师报告
          </button>
          {showAnalyst && (
            <div className="space-y-3 mt-2 pl-3 border-l-2 border-border/40">
              {(['market', 'social', 'news', 'fundamentals'] as const).map((k) => {
                const text = (reports as unknown as Record<string, string>)[k] || ''
                if (!text) return null
                return (
                  <details key={k} open className="text-[12px]">
                    <summary className="font-medium cursor-pointer">{DEEP_STAGE_LABEL[k] || k}</summary>
                    <div className="mt-2 text-[11px] text-foreground/80 whitespace-pre-wrap">
                      {text.slice(0, 1500)}
                      {text.length > 1500 && '... (截断)'}
                    </div>
                  </details>
                )
              })}
            </div>
          )}
        </div>
      )}

      {debate && debate.history && (
        <div>
          <button
            className="text-[12px] text-muted-foreground hover:text-foreground flex items-center gap-1"
            onClick={() => setShowDebate(!showDebate)}
          >
            {showDebate ? '▼' : '▶'} 看多看空辩论
          </button>
          {showDebate && (
            <div className="mt-2 pl-3 border-l-2 border-border/40 text-[11px] text-foreground/80 whitespace-pre-wrap max-h-96 overflow-y-auto">
              {debate.history}
              {debate.judge_decision && (
                <>
                  <div className="font-medium mt-3 mb-1">研究主管裁决:</div>
                  <div>{debate.judge_decision}</div>
                </>
              )}
            </div>
          )}
        </div>
      )}

      <div className="text-[10px] text-muted-foreground/70 italic border-t border-border/30 pt-2">
        本分析由 AI 多 Agent 框架生成,仅供学习研究参考,不构成任何投资建议。
      </div>
    </div>
  )
}

function DeepHistoryComparison({
  history,
  loading,
}: {
  history: HistoryComparisonResponse | null
  loading: boolean
}) {
  if (loading && !history) {
    return (
      <div className="rounded-lg border border-border/40 p-3 text-[11px] text-muted-foreground text-center">
        历史对比加载中...
      </div>
    )
  }
  if (!history || history.items.length === 0) return null

  const stats = history.stats
  const fmtPct = (v: number | null): string => (v == null ? '-' : `${(v * 100).toFixed(0)}%`)
  const fmtRet = (v: number | null): string => (v == null ? '-' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`)
  const retCls = (v: number | null): string =>
    v == null ? 'text-muted-foreground' : v > 0 ? 'text-emerald-600 dark:text-emerald-400' : v < 0 ? 'text-rose-600 dark:text-rose-400' : 'text-muted-foreground'

  return (
    <div className="rounded-lg border border-border/50 p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[12px] font-medium">历史决策 vs 实际涨跌</div>
        <div className="text-[10px] text-muted-foreground">仅基于满 20 个交易日的决策统计</div>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px]">
        <div className="rounded bg-accent/30 px-2 py-1.5">
          <div className="text-muted-foreground">总命中率</div>
          <div className="font-semibold">{fmtPct(stats.overall_hit_rate)}</div>
        </div>
        <div className="rounded bg-accent/30 px-2 py-1.5">
          <div className="text-muted-foreground">买入 ({stats.buy_count})</div>
          <div className="font-semibold text-emerald-600 dark:text-emerald-400">{fmtPct(stats.buy_hit_rate)}</div>
        </div>
        <div className="rounded bg-accent/30 px-2 py-1.5">
          <div className="text-muted-foreground">卖出 ({stats.sell_count})</div>
          <div className="font-semibold text-rose-600 dark:text-rose-400">{fmtPct(stats.sell_hit_rate)}</div>
        </div>
        <div className="rounded bg-accent/30 px-2 py-1.5">
          <div className="text-muted-foreground">平均 20 日收益</div>
          <div className={`font-semibold ${retCls(stats.avg_return_20d_pct)}`}>{fmtRet(stats.avg_return_20d_pct)}</div>
        </div>
      </div>
      <div className="overflow-x-auto -mx-1 mt-2">
        <table className="w-full text-[11px]">
          <thead className="text-muted-foreground">
            <tr className="border-b border-border/40">
              <th className="text-left px-1 py-1 font-normal">日期</th>
              <th className="text-left px-1 py-1 font-normal">决策</th>
              <th className="text-right px-1 py-1 font-normal">分析价</th>
              <th className="text-right px-1 py-1 font-normal">1日</th>
              <th className="text-right px-1 py-1 font-normal">5日</th>
              <th className="text-right px-1 py-1 font-normal">20日</th>
              <th className="text-center px-1 py-1 font-normal">命中</th>
            </tr>
          </thead>
          <tbody>
            {history.items.map((item, i) => (
              <tr key={`${item.analysis_date}-${i}`} className="border-b border-border/20 hover:bg-accent/10">
                <td className="px-1 py-1 text-muted-foreground whitespace-nowrap">{item.analysis_date}</td>
                <td className="px-1 py-1">
                  <span className={DEEP_DECISION_COLOR[item.action] || ''}>{item.action_label}</span>
                  {typeof item.confidence === 'number' && (
                    <span className="text-muted-foreground text-[10px] ml-1">({item.confidence.toFixed(1)})</span>
                  )}
                </td>
                <td className="px-1 py-1 text-right text-foreground/80">{item.price_at_analysis ?? '-'}</td>
                <td className={`px-1 py-1 text-right ${retCls(item.return_1d_pct)}`}>{fmtRet(item.return_1d_pct)}</td>
                <td className={`px-1 py-1 text-right ${retCls(item.return_5d_pct)}`}>{fmtRet(item.return_5d_pct)}</td>
                <td className={`px-1 py-1 text-right ${retCls(item.return_20d_pct)}`}>{fmtRet(item.return_20d_pct)}</td>
                <td className="px-1 py-1 text-center">
                  {item.hit_20d == null ? <span className="text-muted-foreground">-</span> : item.hit_20d ? '✓' : '✗'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

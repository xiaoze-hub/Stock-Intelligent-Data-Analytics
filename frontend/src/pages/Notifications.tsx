import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  AlertCircle,
  AlertTriangle,
  BellRing,
  CheckCheck,
  CheckCircle2,
  ChevronRight,
  ExternalLink,
  Inbox,
  Info,
  RefreshCw,
  Send,
  Trash2,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { fetchAPI } from '@panwatch/api'
import { REFRESH_TIERS } from '@/lib/refresh-tiers'
import { Button } from '@panwatch/base-ui/components/ui/button'
import SkeletonRows from '@/components/SkeletonRows'
import { parseServerTime } from '@/lib/utils'
import ErrorBanner from '@/components/ErrorBanner'

interface NotificationItem {
  id: number
  category: string
  level: 'info' | 'success' | 'warning' | 'error'
  title: string
  body: string
  link: string
  source: string
  trace_id: string
  push_status: string
  push_error: string
  push_channels: Array<{ id: number; name: string; type: string; status: string; error?: string }>
  read: boolean
  created_at: string
}

interface AgentRunDetail {
  status: string
  result: string
  error: string
  duration_ms: number
  model_label: string
  trigger_source: string
  created_at: string
}

interface NotificationDetail extends NotificationItem {
  task: AgentRunDetail | null
}

interface ConfiguredChannel {
  id: number
  name: string
  type: string
}

type FilterKey = 'all' | 'unread' | 'failed'

const CATEGORY_LABELS: Record<string, string> = {
  agent_run: 'Agent 任务',
  report: '报告',
  strategy: '策略',
  price_alert: '价格提醒',
  system: '系统',
}

const LEVEL_META = {
  success: { label: '成功', icon: CheckCircle2, className: 'text-emerald-700 dark:text-emerald-700 bg-emerald-500/10' },
  error: { label: '失败', icon: AlertCircle, className: 'text-rose-600 dark:text-rose-600 bg-rose-500/10' },
  warning: { label: '警告', icon: AlertTriangle, className: 'text-amber-700 dark:text-amber-500 bg-amber-500/10' },
  info: { label: '信息', icon: Info, className: 'text-primary bg-primary/10' },
}

const PUSH_META: Record<string, { label: string; className: string }> = {
  sent: { label: '已外部推送', className: 'text-emerald-700 dark:text-emerald-700 bg-emerald-500/10' },
  failed: { label: '外部推送失败', className: 'text-rose-600 dark:text-rose-600 bg-rose-500/10' },
  skipped: { label: '仅站内通知', className: 'text-muted-foreground bg-accent/60' },
  pending: { label: '正在推送', className: 'text-amber-700 dark:text-amber-500 bg-amber-500/10' },
}

const CHANNEL_TYPE_LABELS: Record<string, string> = {
  pushplus: 'PushPlus',
  telegram: 'Telegram',
  bark: 'Bark',
  dingtalk: '钉钉',
  wecom: '企业微信',
  hermes: 'Hermes',
  wechat_ilink: '个人微信',
  lark: '飞书',
  serverchan: 'Server酱',
  discord: 'Discord',
  pushover: 'Pushover',
}

function channelName(channel: NotificationItem['push_channels'][number]): string {
  return channel.name || CHANNEL_TYPE_LABELS[channel.type] || channel.type || '未命名渠道'
}

function channelSummary(item: NotificationItem): string {
  const names = (item.push_channels || []).map(channelName)
  if (names.length > 0) return `${names.join('、')} · ${PUSH_META[item.push_status]?.label || item.push_status}`
  if (item.push_status === 'sent' || item.push_status === 'failed') return '历史记录未记录渠道'
  return PUSH_META[item.push_status]?.label || item.push_status
}

function formatDateTime(iso: string): string {
  if (!iso) return '时间未知'
  const value = parseServerTime(iso)
  if (Number.isNaN(value.getTime())) return iso
  return value.toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  })
}

function normalizeInternalLink(link: string): string {
  const value = String(link || '').trim()
  if (value === '/stocks' || value.startsWith('/stocks?')) {
    return `/portfolio${value.slice('/stocks'.length)}`
  }
  return value.startsWith('/') && !value.startsWith('//') ? value : ''
}

function MarkdownBlock({ content }: { content: string }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-border/40 bg-background/40 p-4 text-[13px] leading-6 text-foreground">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          table: ({ children }) => <table className="my-3 min-w-full border-collapse text-[12px]">{children}</table>,
          th: ({ children }) => <th className="border border-border/50 bg-accent/50 px-3 py-2 text-left font-medium">{children}</th>,
          td: ({ children }) => <td className="border border-border/50 px-3 py-2 align-top">{children}</td>,
          a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer" className="text-primary underline underline-offset-2">{children}</a>,
          code: ({ children }) => <code className="rounded bg-accent/70 px-1 py-0.5 text-[12px]">{children}</code>,
          ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5">{children}</ul>,
          ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5">{children}</ol>,
          p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}

function EmptyState({ filtered }: { filtered: boolean }) {
  return (
    <div className="flex min-h-[280px] flex-col items-center justify-center px-6 text-center">
      <span className="mb-3 inline-flex h-12 w-12 items-center justify-center rounded-full bg-accent/60 text-muted-foreground">
        <Inbox className="h-5 w-5" />
      </span>
      <div className="text-[14px] font-medium text-foreground">{filtered ? '没有符合条件的通知' : '暂无通知'}</div>
      <div className="mt-1 text-[12px] text-muted-foreground">后台任务、策略与提醒的结果会集中显示在这里。</div>
    </div>
  )
}

export default function NotificationsPage() {
  const nav = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [items, setItems] = useState<NotificationItem[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [filter, setFilter] = useState<FilterKey>('all')
  const [category, setCategory] = useState('')
  const [channelFilter, setChannelFilter] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [detail, setDetail] = useState<NotificationDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')
  const [configuredChannels, setConfiguredChannels] = useState<ConfiguredChannel[]>([])

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const result = await fetchAPI<{ items: NotificationItem[]; unread: number; configured_channels: ConfiguredChannel[] }>('/notifications?limit=200', { cacheMode: 'reload' })
      const next = result?.items || []
      setItems(next)
      setConfiguredChannels(result?.configured_channels || [])
      const requested = Number(searchParams.get('id'))
      setSelectedId(current => {
        if (Number.isFinite(requested) && next.some(item => item.id === requested)) return requested
        if (current && next.some(item => item.id === current)) return current
        return next[0]?.id ?? null
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : '通知加载失败')
    } finally {
      setLoading(false)
    }
  }, [searchParams])

  useEffect(() => { void load() }, [load])

  // 30s 自动刷新:页面可见才轮询(隐藏时暂停,回到页面立即补一次),增量拉新通知;
  // busyRef 防请求叠加;已读状态/选中项由本地逻辑保持,列表已有数据时不闪 spinner
  const autoRefreshBusy = useRef(false)
  useEffect(() => {
    const tick = () => {
      if (document.visibilityState !== 'visible' || autoRefreshBusy.current) return
      autoRefreshBusy.current = true
      load().finally(() => {
        autoRefreshBusy.current = false
      })
    }
    const timer = setInterval(tick, REFRESH_TIERS.BOARD_MS)
    const onVisibility = () => {
      if (document.visibilityState === 'visible') tick()
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [load])

  useEffect(() => {
    if (!selectedId) {
      setDetail(null)
      setDetailError('')
      return
    }
    let cancelled = false
    setDetailLoading(true)
    setDetailError('')
    fetchAPI<NotificationDetail>(`/notifications/${selectedId}`)
      .then(result => {
        if (!cancelled) setDetail(result)
      })
      .catch(e => {
        if (!cancelled) {
          setDetail(null)
          setDetailError(e instanceof Error ? e.message : '任务详情加载失败')
        }
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false)
      })
    return () => { cancelled = true }
  }, [selectedId])

  const unread = useMemo(() => items.filter(item => !item.read).length, [items])
  const failed = useMemo(() => items.filter(item => item.push_status === 'failed').length, [items])
  const categories = useMemo(() => Array.from(new Set(items.map(item => item.category).filter(Boolean))), [items])
  const channelOptions = useMemo(() => {
    const options = new Map<string, string>()
    for (const channel of configuredChannels) {
      if (channel.type) options.set(channel.type, channel.name || CHANNEL_TYPE_LABELS[channel.type] || channel.type)
    }
    for (const item of items) {
      for (const channel of item.push_channels || []) {
        if (channel.type) options.set(channel.type, channelName(channel))
      }
    }
    return Array.from(options.entries()).map(([value, label]) => ({ value, label }))
  }, [configuredChannels, items])
  const filtered = useMemo(() => items.filter(item => {
    if (filter === 'unread' && item.read) return false
    if (filter === 'failed' && item.push_status !== 'failed') return false
    if (category && item.category !== category) return false
    if (channelFilter === '__site_only__' && item.push_status !== 'skipped') return false
    if (channelFilter === '__unrecorded__' && !(
      (item.push_status === 'sent' || item.push_status === 'failed') && (item.push_channels || []).length === 0
    )) return false
    if (channelFilter && !channelFilter.startsWith('__') && !(item.push_channels || []).some(channel => channel.type === channelFilter)) return false
    return true
  }), [category, channelFilter, filter, items])
  const selected = items.find(item => item.id === selectedId) || null
  const selectedDetail = detail?.id === selectedId ? detail : null
  const task = selectedDetail?.task || null

  const selectItem = useCallback(async (item: NotificationItem) => {
    setSelectedId(item.id)
    setSearchParams({ id: String(item.id) }, { replace: true })
    if (item.read) return
    setItems(current => current.map(value => value.id === item.id ? { ...value, read: true } : value))
    try {
      await fetchAPI(`/notifications/${item.id}/read`, { method: 'POST' })
    } catch {
      setItems(current => current.map(value => value.id === item.id ? { ...value, read: false } : value))
    }
  }, [setSearchParams])

  const markAll = async () => {
    try {
      await fetchAPI('/notifications/read-all', { method: 'POST' })
      setItems(current => current.map(item => ({ ...item, read: true })))
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败')
    }
  }

  const clearRead = async () => {
    if (!window.confirm('确认清空所有已读通知？未读通知会保留。')) return
    try {
      await fetchAPI('/notifications/clear', { method: 'DELETE' })
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : '清空失败')
    }
  }

  const linkedPage = selected ? normalizeInternalLink(selected.link) : ''
  const selectedLevel = selected ? (LEVEL_META[selected.level] || LEVEL_META.info) : LEVEL_META.info
  const SelectedLevelIcon = selectedLevel.icon
  const selectedPush = selected ? (PUSH_META[selected.push_status] || null) : null

  return (
    <div className="mx-auto w-full max-w-[1480px] space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-[20px] font-bold tracking-tight text-foreground md:text-[22px]">
            <BellRing className="h-5 w-5 text-primary" />
            通知管理中心
          </h1>
          <p className="mt-1 text-[12px] text-muted-foreground">集中查看站内消息、外部推送结果与任务详细信息。</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" onClick={() => void load()} disabled={loading}>
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </Button>
          <Button variant="secondary" size="sm" onClick={() => void markAll()} disabled={unread === 0}>
            <CheckCheck className="h-3.5 w-3.5" />
            全部已读
          </Button>
          <Button variant="secondary" size="sm" onClick={() => void clearRead()} disabled={!items.some(item => item.read)}>
            <Trash2 className="h-3.5 w-3.5" />
            清空已读
          </Button>
        </div>
      </div>

      <ErrorBanner errors={error ? [{ source: '通知', message: error, retry: () => void load() }] : []} onDismiss={() => setError('')} />

      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border/50 bg-card/70 p-1.5">
        <div className="flex shrink-0 items-center gap-1 rounded-lg bg-background/45 p-1">
          {([
            ['all', '全部', items.length],
            ['unread', '未读', unread],
            ['failed', '推送失败', failed],
          ] as [FilterKey, string, number][]).map(([key, label, value]) => (
            <button
              key={key}
              type="button"
              title={key === 'all' ? '全部通知' : label}
              onClick={() => setFilter(key)}
              className={`inline-flex h-7 items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 text-[11px] font-medium transition-colors ${
                filter === key
                  ? 'bg-primary text-primary-foreground shadow-sm'
                  : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground'
              }`}
            >
              <span>{label}</span>
              <span className={`min-w-[18px] rounded-full px-1.5 py-0.5 text-center text-[9px] leading-3 ${filter === key ? 'bg-white/18 text-white' : 'bg-accent text-foreground'}`}>
                {value}
              </span>
            </button>
          ))}
        </div>

        <span className="hidden h-5 w-px bg-border/60 sm:block" />

        <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
          <button
            type="button"
            onClick={() => setCategory('')}
            className={`h-7 whitespace-nowrap rounded-md px-2.5 text-[11px] transition-colors ${!category ? 'bg-primary/12 font-medium text-primary' : 'text-muted-foreground hover:bg-accent/50 hover:text-foreground'}`}
          >
            全部类型
          </button>
          {categories.map(value => (
            <button
              key={value}
              type="button"
              onClick={() => setCategory(value)}
              className={`h-7 whitespace-nowrap rounded-md px-2.5 text-[11px] transition-colors ${category === value ? 'bg-primary/12 font-medium text-primary' : 'text-muted-foreground hover:bg-accent/50 hover:text-foreground'}`}
            >
              {CATEGORY_LABELS[value] || value}
            </button>
          ))}
        </div>

        <span className="hidden h-5 w-px bg-border/60 sm:block" />

        <label className="relative flex h-8 shrink-0 items-center rounded-lg border border-border/50 bg-background/45 pl-7 text-muted-foreground transition-colors focus-within:border-primary/40 focus-within:text-primary">
          <Send className="pointer-events-none absolute left-2.5 h-3 w-3" />
          <select
            aria-label="按推送渠道筛选"
            value={channelFilter}
            onChange={event => setChannelFilter(event.target.value)}
            className="h-full max-w-[150px] cursor-pointer bg-transparent pl-0 pr-2 text-[11px] text-foreground outline-none"
          >
            <option value="">全部渠道</option>
            {channelOptions.map(option => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
            <option value="__site_only__">仅站内通知</option>
            <option value="__unrecorded__">历史未记录渠道</option>
          </select>
        </label>
      </div>

      <div className="grid min-h-[560px] overflow-hidden rounded-2xl border border-border/50 bg-card lg:grid-cols-[minmax(320px,0.82fr)_minmax(0,1.45fr)]">
        <section className="border-b border-border/50 lg:border-b-0 lg:border-r">
          <div className="flex items-center justify-between border-b border-border/40 px-4 py-3">
            <span className="text-[12px] font-medium text-foreground">通知列表</span>
            <span className="text-[11px] text-muted-foreground">{filtered.length} 条</span>
          </div>
          <div className="max-h-[420px] overflow-y-auto lg:max-h-[620px]">
            {loading && items.length === 0 ? (
              /* 首次加载骨架(列表已有数据时静默刷新,不闪 spinner) */
              <SkeletonRows rows={7} />
            ) : filtered.length === 0 ? (
              <EmptyState filtered={items.length > 0} />
            ) : filtered.map(item => {
              const meta = LEVEL_META[item.level] || LEVEL_META.info
              const Icon = meta.icon
              const isSelected = selectedId === item.id
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => void selectItem(item)}
                  aria-pressed={isSelected}
                  className={`group relative flex w-full gap-3 border-b border-l-[3px] px-4 py-3.5 text-left transition-[border-color,background-color,box-shadow] last:border-b-0 ${
                    isSelected
                      ? 'border-b-primary/20 border-l-primary bg-primary/12 shadow-[inset_0_0_0_1px_hsl(var(--primary)/0.18)]'
                      : 'border-b-border/30 border-l-transparent hover:border-l-primary/35 hover:bg-accent/35'
                  }`}
                >
                  <span className={`mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-shadow ${meta.className} ${isSelected ? 'ring-2 ring-primary/30 ring-offset-2 ring-offset-card' : ''}`}>
                    <Icon className="h-4 w-4" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                      {!item.read && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-rose-500" />}
                      <span className={`truncate text-[12.5px] font-medium ${isSelected ? 'text-primary' : 'text-foreground'}`}>{item.title || '未命名通知'}</span>
                      {isSelected && (
                        <span className="shrink-0 rounded-full bg-primary px-1.5 py-0.5 text-[9px] font-medium text-primary-foreground">正在查看</span>
                      )}
                    </span>
                    <span className="mt-1 block line-clamp-2 text-[11px] leading-5 text-muted-foreground">{item.body || '无正文'}</span>
                    <span className="mt-1.5 flex min-w-0 items-center gap-2 text-[10px] text-muted-foreground/70">
                      <span className="shrink-0">{formatDateTime(item.created_at)}</span>
                      {item.push_status && <span className={`min-w-0 truncate ${item.push_status === 'failed' ? 'text-rose-600 dark:text-rose-600' : item.push_status === 'sent' ? 'text-emerald-700 dark:text-emerald-700' : ''}`}>{channelSummary(item)}</span>}
                    </span>
                  </span>
                  <span className={`mt-1.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full transition-colors ${isSelected ? 'bg-primary text-primary-foreground' : 'text-muted-foreground/50 group-hover:bg-accent group-hover:text-foreground'}`}>
                    <ChevronRight className="h-3.5 w-3.5" />
                  </span>
                </button>
              )
            })}
          </div>
        </section>

        <section className="min-w-0">
          {!selected ? (
            <EmptyState filtered={false} />
          ) : (
            <div className="p-5 md:p-6">
              <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border/40 pb-5">
                <div className="flex min-w-0 gap-3">
                  <span className={`inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full ${selectedLevel.className}`}>
                    <SelectedLevelIcon className="h-5 w-5" />
                  </span>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-[17px] font-semibold text-foreground">{selected.title || '未命名通知'}</h2>
                      <span className="rounded-full bg-accent/60 px-2 py-0.5 text-[10px] text-muted-foreground">{CATEGORY_LABELS[selected.category] || selected.category || '系统'}</span>
                      <span className={`rounded-full px-2 py-0.5 text-[10px] ${selected.read ? 'bg-accent/60 text-muted-foreground' : 'bg-rose-500/10 text-rose-600 dark:text-rose-600'}`}>{selected.read ? '已读' : '未读'}</span>
                    </div>
                    <div className="mt-1 text-[11px] text-muted-foreground">{formatDateTime(selected.created_at)}</div>
                  </div>
                </div>
                {linkedPage && (
                  <Button size="sm" onClick={() => nav(linkedPage)}>
                    <ExternalLink className="h-3.5 w-3.5" />
                    查看关联页面
                  </Button>
                )}
              </div>

              <div className="grid gap-3 border-b border-border/40 py-5 sm:grid-cols-2 xl:grid-cols-4">
                <div className="rounded-xl bg-accent/30 p-3">
                  <div className="text-[10px] text-muted-foreground">站内状态</div>
                  <div className="mt-1 flex items-center gap-1.5 text-[12px] font-medium text-foreground"><CheckCircle2 className="h-3.5 w-3.5 text-emerald-700 dark:text-emerald-700" />已送达消息中心</div>
                </div>
                <div className="rounded-xl bg-accent/30 p-3">
                  <div className="text-[10px] text-muted-foreground">推送渠道</div>
                  {(selected.push_channels || []).length > 0 ? (
                    <div className="mt-1.5 flex flex-wrap gap-1.5">
                      {selected.push_channels.map((channel, index) => (
                        <span
                          key={`${channel.id || channel.type}-${index}`}
                          title={channel.error || ''}
                          className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ${
                            channel.status === 'sent'
                              ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-700'
                              : channel.status === 'failed'
                                ? 'bg-rose-500/10 text-rose-600 dark:text-rose-600'
                                : 'bg-amber-500/10 text-amber-700 dark:text-amber-500'
                          }`}
                        >
                          <Send className="h-3 w-3" />
                          {channelName(channel)}
                          <span>· {channel.status === 'sent' ? '已发送' : channel.status === 'failed' ? '失败' : '发送中'}</span>
                        </span>
                      ))}
                    </div>
                  ) : (
                    <div className="mt-1.5 space-y-1.5">
                      <div className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[10px] font-medium ${selectedPush?.className || 'bg-accent text-muted-foreground'}`}>
                        <Send className="h-3 w-3" />
                        {selected.push_status === 'sent' || selected.push_status === 'failed' ? '历史记录未记录渠道' : selectedPush?.label || '未记录'}
                      </div>
                      {(selected.push_status === 'sent' || selected.push_status === 'failed') && configuredChannels.length > 0 && (
                        <div className="text-[9px] leading-4 text-muted-foreground" title="当前配置不代表该条历史通知当时实际使用的渠道">
                          当前启用：{configuredChannels.map(channel => channel.name || CHANNEL_TYPE_LABELS[channel.type] || channel.type).join('、')}（仅供参考）
                        </div>
                      )}
                    </div>
                  )}
                </div>
                <div className="rounded-xl bg-accent/30 p-3">
                  <div className="text-[10px] text-muted-foreground">来源</div>
                  <div className="mt-1 truncate text-[12px] font-medium text-foreground" title={selected.source}>{selected.source || '系统'}</div>
                </div>
                <div className="rounded-xl bg-accent/30 p-3">
                  <div className="text-[10px] text-muted-foreground">级别</div>
                  <div className="mt-1 text-[12px] font-medium text-foreground">{selectedLevel.label}</div>
                </div>
              </div>

              {selected.push_error && (
                <div className="mt-5 rounded-xl border border-rose-500/25 bg-rose-500/8 p-4">
                  <div className="flex items-center gap-2 text-[12px] font-medium text-rose-600 dark:text-rose-600"><AlertCircle className="h-4 w-4" />推送失败详情</div>
                  <div className="mt-2 whitespace-pre-wrap break-words font-mono text-[11px] leading-5 text-rose-700 dark:text-rose-400">{selected.push_error}</div>
                </div>
              )}

              <div className="mt-5">
                <div className="mb-2 flex items-center justify-between gap-3">
                  <span className="text-[11px] font-medium text-muted-foreground">任务执行详情</span>
                  {task && (
                    <div className="flex flex-wrap items-center justify-end gap-1.5 text-[10px]">
                      <span className={`rounded-full px-2 py-0.5 font-medium ${
                        task.status === 'success'
                          ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-700'
                          : task.status === 'skipped'
                            ? 'bg-amber-500/10 text-amber-700 dark:text-amber-500'
                            : 'bg-rose-500/10 text-rose-600 dark:text-rose-600'
                      }`}>
                        {task.status === 'success' ? '执行成功' : task.status === 'skipped' ? '已跳过' : '执行失败'}
                      </span>
                      {task.duration_ms > 0 && <span className="rounded-full bg-accent/60 px-2 py-0.5 text-muted-foreground">耗时 {(task.duration_ms / 1000).toFixed(1)}s</span>}
                      {task.model_label && <span className="max-w-[220px] truncate rounded-full bg-accent/60 px-2 py-0.5 text-muted-foreground" title={task.model_label}>{task.model_label}</span>}
                    </div>
                  )}
                </div>
                {detailLoading ? (
                  <div className="flex min-h-28 items-center justify-center rounded-xl border border-border/40 bg-background/30 text-[12px] text-muted-foreground">
                    <RefreshCw className="mr-2 h-4 w-4 animate-spin" />正在读取本次任务结果…
                  </div>
                ) : detailError ? (
                  <div className="rounded-xl border border-rose-500/25 bg-rose-500/8 p-4 text-[12px] text-rose-600 dark:text-rose-600">{detailError}</div>
                ) : task?.error ? (
                  <div className="rounded-xl border border-rose-500/25 bg-rose-500/8 p-4 font-mono text-[12px] leading-6 text-rose-700 dark:text-rose-400 whitespace-pre-wrap break-words">{task.error}</div>
                ) : task?.result ? (
                  <MarkdownBlock content={task.result} />
                ) : selected.trace_id ? (
                  <div className="rounded-xl border border-dashed border-border/60 px-4 py-6 text-center text-[12px] text-muted-foreground">已有任务追踪编号，但未找到对应的执行结果。</div>
                ) : (
                  <div className="rounded-xl border border-dashed border-border/60 px-4 py-6 text-center text-[12px] text-muted-foreground">该类通知没有关联 Agent 执行记录。</div>
                )}
              </div>

              <div className="mt-5">
                <div className="mb-2 text-[11px] font-medium text-muted-foreground">通知正文</div>
                {selected.body ? (
                  <MarkdownBlock content={selected.body} />
                ) : (
                  <div className="rounded-xl border border-dashed border-border/60 px-4 py-8 text-center text-[12px] text-muted-foreground">该通知没有正文。</div>
                )}
              </div>

              {(selected.trace_id || linkedPage) && (
                <details className="mt-5 rounded-xl border border-border/40 bg-accent/20 px-4 py-3">
                  <summary className="cursor-pointer text-[11px] font-medium text-muted-foreground">技术详情</summary>
                  <dl className="mt-3 grid gap-2 text-[11px] sm:grid-cols-[88px_1fr]">
                    {selected.trace_id && <><dt className="text-muted-foreground">Trace ID</dt><dd className="break-all font-mono text-foreground">{selected.trace_id}</dd></>}
                    {linkedPage && <><dt className="text-muted-foreground">关联地址</dt><dd className="break-all font-mono text-foreground">{linkedPage}</dd></>}
                  </dl>
                </details>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}

import { Fragment, useState, useEffect, useRef, lazy, Suspense } from 'react'
import { Routes, Route, NavLink, useLocation, useNavigate, Navigate } from 'react-router-dom'
import { TrendingUp, Bot, ScrollText, Settings, List, Database, Clock, LayoutDashboard, Github, BellRing, Sparkles, Activity, LineChart, FileText, Shield, HelpCircle, ShieldCheck } from 'lucide-react'
import { useTheme } from '@/hooks/use-theme'
import { useHotkeys } from '@/hooks/use-hotkeys'
import { appApi, fetchAPI, getMyPermissions, isAuthenticated } from '@panwatch/api'
// 2026-08-12 性能优化: 路由懒加载 — 17 个页面原本静态 import 打进单 bundle 1.2MB,
// 点任意路由都要下载/解析整个应用。改为 React.lazy 按需加载, 首屏只下载登录页+当前页。
const DashboardPage = lazy(() => import('@/pages/Dashboard'))
const OpportunitiesPage = lazy(() => import('@/pages/Opportunities'))
const StocksPage = lazy(() => import('@/pages/Stocks'))
const AgentsPage = lazy(() => import('@/pages/Agents'))
const SettingsPage = lazy(() => import('@/pages/Settings'))
const DataSourcesPage = lazy(() => import('@/pages/DataSources'))
const HistoryPage = lazy(() => import('@/pages/History'))
const ReportsPage = lazy(() => import('@/pages/Reports'))
const AnalysisDetailPage = lazy(() => import('@/pages/AnalysisDetail'))
const PriceAlertsPage = lazy(() => import('@/pages/PriceAlerts'))
const PaperTradingPage = lazy(() => import('@/pages/PaperTrading'))
const LoginPage = lazy(() => import('@/pages/Login'))
const ForecastPage = lazy(() => import('@/pages/Forecast'))
const IndexDetailPage = lazy(() => import('@/pages/IndexDetail'))
const BoardDetailPage = lazy(() => import('@/pages/BoardDetail'))
const ShadowAccountPage = lazy(() => import('@/pages/ShadowAccount'))
const NotificationsPage = lazy(() => import('@/pages/Notifications'))
const ProfilePage = lazy(() => import('@/pages/Profile'))
const HelpPage = lazy(() => import('@/pages/Help'))
const AuditPage = lazy(() => import('@/pages/Audit'))
import LogsModal from '@panwatch/biz-ui/components/logs-modal'
import AmbientBackground from '@panwatch/biz-ui/components/AmbientBackground'
import NotificationBell from '@panwatch/biz-ui/components/notification-bell'
import ChatWidget from '@/components/ChatWidget'
import BrowserNotificationBridge from '@/components/BrowserNotificationBridge'
import StockCommandPalette from '@/components/StockCommandPalette'
import AccountMenu from '@/components/AccountMenu'
import SelfCheckModal from '@/components/SelfCheckModal'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { Button } from '@panwatch/base-ui/components/ui/button'
import AppErrorBoundary from '@/components/ErrorBoundary'

const navItems = [
  { to: '/', icon: LayoutDashboard, label: '首页', perm: 'view_dashboard' },
  { to: '/portfolio', icon: List, label: '持仓', perm: 'edit_portfolio' },
  { to: '/opportunities', icon: Sparkles, label: '机会', perm: 'view_opportunities' },
  { to: '/forecast', icon: LineChart, label: '预测', perm: 'view_forecast' },
  { to: '/paper-trading', icon: Activity, label: '模拟盘', perm: 'manage_paper_trading' },
  { to: '/alerts', icon: BellRing, label: '提醒' },
  { to: '/agents', icon: Bot, label: 'Agent', perm: 'manage_agents' },
  { to: '/reports', icon: FileText, label: '报告', perm: 'view_reports' },
  { to: '/shadow', icon: Shield, label: '影子账户', perm: 'manage_shadow' },
  { to: '/history', icon: Clock, label: '历史' },
  { to: '/datasources', icon: Database, label: '数据源', perm: 'manage_datasources' },
  { to: '/settings', icon: Settings, label: '设置' },
  { to: '/help', icon: HelpCircle, label: '帮助' },
  { to: '/audit', icon: ShieldCheck, label: '审计', ownerOnly: true },
]
// 桌面端导航按业务分组(2026-08-12): 行情 / 交易 / 系统, 13 项全部平铺显示, 不再 slice 截断
const desktopNavGroups = [
  { key: 'market', items: navItems.filter(n => ['/', '/portfolio', '/opportunities', '/forecast'].includes(n.to)) },
  { key: 'trading', items: navItems.filter(n => ['/paper-trading', '/alerts', '/shadow'].includes(n.to)) },
  { key: 'system', items: navItems.filter(n => ['/agents', '/reports', '/history', '/datasources', '/settings', '/help', '/audit'].includes(n.to)) },
]
// 移动端底部 5 槽位按 to 路径挑选: 首页/持仓/机会/预测/提醒(2026-08-13, 模拟盘移入"更多"下拉,
// 不再用 slice(0,5) 依赖 navItems 顺序); navItems 数组顺序保持不动, 桌面端平铺分组完全不变
const MOBILE_PRIMARY_TO = ['/', '/portfolio', '/opportunities', '/forecast', '/alerts']

// ═══ demo 账号只读模式(2026-08-15): 从 JWT payload 解出 username/role, 按角色控制导航 ═══
// 修复(M-2, 2026-08-23): atob 解 JWT payload 必须容错:
// - token 不是合法 base64(空 token / 第三方篡改) → JSON.parse 抛 → catch 返回 null
// - token 仅有两段(去除签名/头为空) → split('.')[1] 拿到 undefined → atob 抛
// - payload 是数组/其它非对象类型(JSON.parse 合法但结构异常) → null 安全返回
// 仅用于 UI 展示用途的 claims 解析, 不参与授权判断(权限最终看后端).
function _safeJwtPayload(token: string): Record<string, unknown> | null {
  try {
    if (!token) return null
    const parts = token.split('.')
    if (parts.length < 2) return null
    // base64url → base64: '-' '_' 替换成 '+' '/', 补 '='
    let s = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    while (s.length % 4) s += '='
    const decoded = atob(s)
    const obj = JSON.parse(decoded)
    return obj && typeof obj === 'object' && !Array.isArray(obj) ? (obj as Record<string, unknown>) : null
  } catch {
    return null
  }
}
const getJwtUsername = (): string | null => {
  const t = localStorage.getItem('token') || ''
  const p = _safeJwtPayload(t)
  const u = p?.username
  return typeof u === 'string' ? u : null
}
// 2026-08-15: 从 JWT payload 取 role(owner|member|guest); demo 账号兼容按 username==demo 判定
const getJwtRole = (): string | null => {
  const t = localStorage.getItem('token') || ''
  const p = _safeJwtPayload(t)
  const r = p?.role
  return typeof r === 'string' ? r : null
}
const isDemoUser = (): boolean => getJwtUsername() === 'demo'
// 是否 guest 角色: role==guest 或 demo 账号(后端口径: username=="demo" || role=="guest")
const isGuestUser = (): boolean => getJwtRole() === 'guest' || isDemoUser()
// 角色化隐藏导航: owner/member 全部显示(现状不变); guest(demo) 隐藏管理/个人页面
// (数据源/AI配置/Agent/策略等核心内容; 设置/持仓/自选可浏览但只读)
const GUEST_HIDDEN_PATHS = ['/paper-trading', '/alerts', '/shadow', '/agents', '/datasources']
const isNavHiddenForGuest = (to: string): boolean =>
  GUEST_HIDDEN_PATHS.includes(to) || to.startsWith('/manage')
// owner 专属导航(审计页): 非 owner 一律隐藏(2026-08-15)
const isNavHiddenForRole = (n: { to: string; ownerOnly?: boolean }): boolean =>
  !!n.ownerOnly && getJwtRole() !== 'owner'
// 模块权限过滤(2026-08-16): 导航项带 perm 权限点 → 当前用户 effective 权限
// 不含该点则隐藏(未授权模块前端也看不到, 不再"能点进去但数据403")。
// 注意: 拉取失败(myPerms==null)或权限点未映射的项 → 不隐藏(回退角色判断, 不误伤)。
const isNavHiddenForPerm = (n: { perm?: string }, myPerms: Set<string> | null): boolean => {
  if (!n.perm) return false
  if (!myPerms) return false
  return !myPerms.has(n.perm)
}
const mobilePrimaryNavItems = navItems.filter(n => MOBILE_PRIMARY_TO.includes(n.to))
const mobileMoreNavItems = navItems.filter(n => !MOBILE_PRIMARY_TO.includes(n.to))

function LegacyStocksRedirect() {
  const location = useLocation()
  return <Navigate to={`/portfolio${location.search}`} replace />
}

// 认证守卫组件
function RequireAuth({ children }: { children: React.ReactNode }) {
  const [authState, setAuthState] = useState<'checking' | 'authenticated' | 'unauthenticated'>('checking')
  const location = useLocation()

  useEffect(() => {
    // 检查本地 token
    if (isAuthenticated()) {
      setAuthState('authenticated')
      return
    }

    // 没有 token，需要去登录页（设置密码或登录）
    setAuthState('unauthenticated')
  }, [])

  if (authState === 'checking') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <span className="w-6 h-6 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
      </div>
    )
  }

  if (authState === 'unauthenticated') {
    return <Navigate to="/login" state={{ from: location }} replace />
  }

  return <>{children}</>
}

// 模块权限路由守卫(2026-08-16): 未授权模块直接跳首页。
// 配合后端 403 + 导航过滤, 三层防"未授权模块可见"。
// myPerms 尚未加载(null)时不拦截(避免闪跳), 加载完成后才生效。
function PermGuard({ perm, myPerms, children }: { perm?: string; myPerms: Set<string> | null; children: React.ReactNode }) {
  if (!perm || !myPerms) return <>{children}</>
  if (!myPerms.has(perm)) return <Navigate to="/" replace />
  return <>{children}</>
}

/** 懒加载路由的轻量占位(2026-08-12): 纯静态骨架, 不依赖任何懒加载模块 */
function PageFallback() {
  return (
    <div className="w-full h-[60vh] flex items-center justify-center">
      <div className="flex flex-col items-center gap-3 text-muted-foreground">
        <div className="w-6 h-6 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
        <span className="text-[12px]">加载中…</span>
      </div>
    </div>
  )
}

function App() {
  const { mode, setMode } = useTheme()
  const location = useLocation()
  const [version, setVersion] = useState('')
  const [logsOpen, setLogsOpen] = useState(false)
  const [selfCheckOpen, setSelfCheckOpen] = useState(false)
  const [upgradeOpen, setUpgradeOpen] = useState(false)
  const [upgradeInfo, setUpgradeInfo] = useState<{ latest: string; url: string } | null>(null)
  const checkedUpdateRef = useRef(false)
  // 当前用户模块权限(导航过滤: 未授权模块隐藏入口; 拉取失败回退角色判断)
  const [myPerms, setMyPerms] = useState<Set<string> | null>(null)
  const repoUrl = 'https://github.com/xiaoze-hub/Stock-Intelligent-Data-Analytics'

  useEffect(() => {
    if (!isAuthenticated()) return
    getMyPermissions()
      .then(p => p && setMyPerms(new Set(p.effective)))
      .catch(() => {})
  }, [])

  useEffect(() => {
    appApi.version()
      .then(data => setVersion(data?.version || ''))
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (checkedUpdateRef.current) return
    if (!isAuthenticated()) return
    const current = String(version || '').trim()
    if (!current || current === 'dev') return
    checkedUpdateRef.current = true

    fetchAPI<any>('/settings/update-check')
      .then((res) => {
        const latest = String(res?.latest_version || '').trim()
        const shouldOpen = !!res?.update_available && !!latest
        if (!shouldOpen) return
        const dismissed = localStorage.getItem('panwatch_upgrade_dismissed_version') || ''
        if (dismissed === latest) return
        setUpgradeInfo({ latest, url: String(res?.release_url || 'https://github.com/xiaoze-hub/Stock-Intelligent-Data-Analytics/releases') })
        setUpgradeOpen(true)
      })
      .catch(() => {})
  }, [version])

  // ===== PC 快捷键(2026-08-12,增量功能,不影响现有交互) =====
  // 仅桌面端(>=768px)生效,移动端自动禁用;登录页不响应
  const navigate = useNavigate()
  const [hotkeysOpen, setHotkeysOpen] = useState(false)
  // 登录页守卫:登录态外不响应快捷键
  const runOnDesktop = (fn: () => void) => () => {
    if (location.pathname === '/login') return
    fn()
  }

  useHotkeys([
    {
      combo: 'mod+k',
      handler: runOnDesktop(() => {
        // 优先聚焦搜索框;当前无全局搜索框,先打开日志弹窗 LogsModal 作为占位,后续接搜索
        const searchInput = document.querySelector<HTMLInputElement>(
          'input[type="search"], input[data-search-input], input[placeholder*="搜索" i]',
        )
        if (searchInput) {
          searchInput.focus()
          searchInput.scrollIntoView({ block: 'center', behavior: 'smooth' })
          return
        }
        setLogsOpen(true)
      }),
    },
    { combo: 'mod+,', handler: runOnDesktop(() => navigate('/settings')) },
    { sequence: ['g', 'd'], sequenceTimeout: 1500, handler: runOnDesktop(() => navigate('/')) },
    { sequence: ['g', 'p'], sequenceTimeout: 1500, handler: runOnDesktop(() => navigate('/portfolio')) },
    { combo: '?', preventDefault: true, handler: runOnDesktop(() => setHotkeysOpen(true)) },
  ])

  // 登录页面不显示导航
  if (location.pathname === '/login') {
    return (
      <Suspense fallback={<PageFallback />}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
        </Routes>
      </Suspense>
    )
  }

  return (
    <RequireAuth>
    <div className="min-h-screen pb-16 md:pb-0 relative overflow-x-clip bg-background">
      <BrowserNotificationBridge />
      <AmbientBackground />
      {/* Desktop Floating Nav */}
      <div className="sticky top-0 z-50 px-4 md:px-6 pt-3 md:pt-4 pb-2 hidden md:block">
        <header className="card px-4 md:px-5">
          <div className="h-14 flex items-center justify-between">
            {/* Logo */}
            <NavLink to="/" className="flex items-center gap-2.5 group shrink-0">
              <div className="w-8 h-8 rounded-2xl bg-gradient-to-br from-primary to-primary/70 flex items-center justify-center shadow-sm">
                <TrendingUp className="w-4 h-4 text-white" />
              </div>
              <span className="text-[15px] font-bold text-foreground">数智分析</span>
              {version && <span className="text-[11px] text-muted-foreground/60 font-normal">v{version}</span>}
              {isDemoUser() && (
                <span className="ml-1 shrink-0 rounded-md bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-600" title="演示账号为只读浏览模式">
                  演示模式 · 只读
                </span>
              )}
            </NavLink>

            {/* Nav Links — 桌面端三组全平铺(行情/交易/系统), 组间 1px 分隔线, 不再 slice(0,5) */}
            <nav className="flex items-center gap-1 min-w-0 flex-1 justify-center overflow-x-auto">
              {desktopNavGroups.map((group, gi) => {
                // guest(demo): 隐藏管理/个人页面导航; 模块权限: 未授权模块隐藏入口
                const items = group.items.filter(n => (!isGuestUser() || !isNavHiddenForGuest(n.to)) && !isNavHiddenForRole(n) && !isNavHiddenForPerm(n, myPerms))
                if (items.length === 0) return null
                return (
                <Fragment key={group.key}>
                  {gi > 0 && <div className="w-px h-5 bg-border/50 mx-1 shrink-0" aria-hidden="true" />}
                  {items.map(({ to, icon: Icon, label }) => {
                    const isActive = to === '/' ? location.pathname === '/' : location.pathname.startsWith(to)
                    return (
                      <NavLink
                        key={to}
                        to={to}
                        className="relative shrink-0"
                      >
                        <span
                          className={`absolute inset-0 rounded-xl transition-shadow ${
                            isActive
                              ? 'bg-[linear-gradient(135deg,hsl(var(--primary)/0.14),hsl(var(--primary)/0.04),hsl(var(--success)/0.06))] ring-1 ring-primary/20 shadow-[0_8px_24px_-18px_hsl(var(--primary)/0.55)]'
                              : 'bg-transparent'
                          }`}
                        />
                        <span
                          className={`relative px-2.5 py-2 rounded-xl text-[13px] font-medium transition-colors flex items-center gap-1.5 ${
                            isActive
                              ? 'text-foreground'
                              : 'text-muted-foreground hover:text-foreground hover:bg-accent'
                          }`}
                        >
                          <Icon className={`w-4 h-4 ${isActive ? 'text-primary' : ''}`} />
                          {label}
                        </span>
                      </NavLink>
                    )
                  })}
                </Fragment>
                )
              })}
            </nav>

            {/* action wrapper:GitHub + 日志 + 头像(桌面端头像下拉仅含主题/自检/退出, 导航已平铺) */}
            <div className="flex items-center gap-1.5 px-1.5 py-1 rounded-2xl bg-accent/20 border border-border/40 shrink-0">
              <button
                onClick={() => window.open(repoUrl, '_blank', 'noopener,noreferrer')}
                className="w-9 h-9 rounded-xl flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-background/70 transition-colors"
                title="GitHub 项目"
              >
                <Github className="w-4 h-4" />
              </button>
              <button
                onClick={() => setLogsOpen(true)}
                className="w-9 h-9 rounded-xl flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-background/70 transition-colors"
                title="查看日志"
              >
                <ScrollText className="w-4 h-4" />
              </button>
              <NotificationBell />
              <AccountMenu
                navItems={[]}
                mode={mode}
                onSetMode={setMode}
                onOpenSelfCheck={() => setSelfCheckOpen(true)}
              />
            </div>
          </div>
        </header>
      </div>

      {/* Mobile Top Bar */}
      <div className="sticky top-0 z-50 px-4 pt-[max(0.75rem,env(safe-area-inset-top))] pb-2 md:hidden">
        <header className="card px-4">
          <div className="h-12 flex items-center justify-between">
            <NavLink to="/" className="flex items-center gap-2 group">
              <div className="w-7 h-7 rounded-xl bg-gradient-to-br from-primary to-primary/70 flex items-center justify-center shadow-sm">
                <TrendingUp className="w-3.5 h-3.5 text-white" />
              </div>
              <span className="text-[14px] font-bold text-foreground">数智分析</span>
              {version && <span className="text-[10px] text-muted-foreground/60 font-normal">v{version}</span>}
            </NavLink>
            <div className="flex items-center gap-1.5 px-1.5 py-1 rounded-2xl bg-accent/20 border border-border/40">
              <button
                onClick={() => window.open(repoUrl, '_blank', 'noopener,noreferrer')}
                className="w-8 h-8 rounded-xl flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-background/70 transition-colors"
                title="GitHub 项目"
              >
                <Github className="w-4 h-4" />
              </button>
              <button
                onClick={() => setLogsOpen(true)}
                className="w-8 h-8 rounded-xl flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-background/70 transition-colors"
                title="查看日志"
              >
                <ScrollText className="w-4 h-4" />
              </button>
              <NotificationBell size="sm" />
              <AccountMenu
                size="sm"
                navItems={isGuestUser() ? mobileMoreNavItems.filter(n => !isNavHiddenForGuest(n.to) && !isNavHiddenForRole(n) && !isNavHiddenForPerm(n, myPerms)) : mobileMoreNavItems.filter(n => !isNavHiddenForRole(n) && !isNavHiddenForPerm(n, myPerms))}
                mode={mode}
                onSetMode={setMode}
                onOpenSelfCheck={() => setSelfCheckOpen(true)}
              />
            </div>
          </div>
        </header>
      </div>

      {/* 全局搜股面板(OT-Phase2): Cmd/Ctrl+K 唤起 */}
      <StockCommandPalette />
      {/* Mobile Bottom Nav */}
      <nav className="fixed bottom-0 left-0 right-0 z-50 md:hidden bg-card border-t border-border px-2 pb-[env(safe-area-inset-bottom)]">
        <div className="flex items-center justify-around h-14">
          {mobilePrimaryNavItems.filter(n => (!isGuestUser() || !isNavHiddenForGuest(n.to)) && !isNavHiddenForRole(n) && !isNavHiddenForPerm(n, myPerms)).map(({ to, icon: Icon, label }) => {
            const isActive = to === '/' ? location.pathname === '/' : location.pathname.startsWith(to)
            return (
              <NavLink
                key={to}
                to={to}
                className={`flex flex-col items-center justify-center gap-0.5 px-2 py-1.5 rounded-xl transition-[background-color,color,box-shadow] min-w-[56px] ${
                  isActive
                    ? 'text-primary bg-primary/8 ring-1 ring-primary/15'
                    : 'text-muted-foreground hover:bg-accent/30'
                }`}
              >
                <Icon className="w-5 h-5" />
                <span className="text-[10px] font-medium">{label}</span>
              </NavLink>
            )
          })}
        </div>
      </nav>

      {/* Content */}
      <main className="px-4 md:px-6 py-4 md:py-6 w-full">
        <Suspense fallback={<PageFallback />}>
          <AppErrorBoundary>
            <Routes>

              <Route path="/" element={<DashboardPage />} />
              <Route path="/opportunities" element={<PermGuard perm="view_opportunities" myPerms={myPerms}><OpportunitiesPage /></PermGuard>} />
              <Route path="/forecast" element={<PermGuard perm="view_forecast" myPerms={myPerms}><ForecastPage /></PermGuard>} />
              <Route path="/index/:symbol" element={<IndexDetailPage />} />
              <Route path="/boards/:blockCode" element={<BoardDetailPage />} />
              <Route path="/portfolio" element={<PermGuard perm="edit_portfolio" myPerms={myPerms}><StocksPage /></PermGuard>} />
              <Route path="/stocks" element={<LegacyStocksRedirect />} />
              <Route path="/agents" element={<PermGuard perm="manage_agents" myPerms={myPerms}><AgentsPage /></PermGuard>} />
              <Route path="/history" element={<HistoryPage />} />
              <Route path="/reports" element={<PermGuard perm="view_reports" myPerms={myPerms}><ReportsPage /></PermGuard>} />
              <Route path="/shadow" element={<PermGuard perm="manage_shadow" myPerms={myPerms}><ShadowAccountPage /></PermGuard>} />
              <Route path="/paper-trading" element={<PermGuard perm="manage_paper_trading" myPerms={myPerms}><PaperTradingPage /></PermGuard>} />
              <Route path="/alerts" element={<PriceAlertsPage />} />
              <Route path="/notifications" element={<NotificationsPage />} />
              <Route path="/profile" element={<ProfilePage />} />
              <Route path="/help" element={<HelpPage />} />
              <Route path="/audit" element={getJwtRole() === 'owner' ? <AuditPage /> : <Navigate to="/" replace />} />
              <Route path="/datasources" element={<PermGuard perm="manage_datasources" myPerms={myPerms}><DataSourcesPage /></PermGuard>} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/analysis/:symbol/:date" element={<AnalysisDetailPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </AppErrorBoundary>

        </Suspense>
      </main>
      <ChatWidget />
      <LogsModal open={logsOpen} onOpenChange={setLogsOpen} />
      <SelfCheckModal open={selfCheckOpen} onClose={() => setSelfCheckOpen(false)} />
      <Dialog open={upgradeOpen} onOpenChange={setUpgradeOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>发现新版本</DialogTitle>
            <DialogDescription>
              当前版本 v{version}，可升级到 v{upgradeInfo?.latest}。
            </DialogDescription>
          </DialogHeader>
          <div className="text-[12px] text-muted-foreground">
            建议升级以获取最新功能和修复。
          </div>
          <div className="flex items-center justify-end gap-2">
            <Button
              variant="secondary"
              onClick={() => {
                if (upgradeInfo?.latest) localStorage.setItem('panwatch_upgrade_dismissed_version', upgradeInfo.latest)
                setUpgradeOpen(false)
              }}
            >
              稍后提醒
            </Button>
            <Button
              onClick={() => {
                const url = upgradeInfo?.url || 'https://github.com/xiaoze-hub/Stock-Intelligent-Data-Analytics/releases'
                window.open(url, '_blank', 'noopener,noreferrer')
              }}
            >
              去升级
            </Button>
          </div>
        </DialogContent>
      </Dialog>
      {/* 快捷键帮助(2026-08-12):按 ? 打开 */}
      <Dialog open={hotkeysOpen} onOpenChange={setHotkeysOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>键盘快捷键</DialogTitle>
            <DialogDescription>仅桌面端生效,移动端自动禁用。</DialogDescription>
          </DialogHeader>
          <div className="space-y-2.5 text-[13px]">
            {[
              { desc: '打开日志(搜索占位)', keys: ['⌘/Ctrl', 'K'] },
              { desc: '跳转设置', keys: ['⌘/Ctrl', ','] },
              { desc: '跳转首页', keys: ['G', 'D'] },
              { desc: '跳转持仓', keys: ['G', 'P'] },
              { desc: '显示本帮助', keys: ['?'] },
            ].map(row => (
              <div key={row.desc} className="flex items-center justify-between gap-4">
                <span className="text-muted-foreground">{row.desc}</span>
                <span className="flex items-center gap-1 shrink-0">
                  {row.keys.map(k => (
                    <kbd
                      key={k}
                      className="px-1.5 py-0.5 rounded-md bg-accent border border-border text-[11px] font-medium text-foreground"
                    >
                      {k}
                    </kbd>
                  ))}
                </span>
              </div>
            ))}
          </div>
        </DialogContent>
      </Dialog>
    </div>
    </RequireAuth>
  )
}

export default App

/**
 * 轮询档位表(借鉴 OpenTerminal 快慢分离)。
 * 所有页面轮询只许用这四档，禁止手写魔法数字：
 * - REALTIME(5s): 实时行情类(WS 为主，轮询只做兜底)
 * - BOARD(30s): 榜单/阶段/通知类
 * - SLOW(5min): 慢数据(引擎状态/日历/配置类)
 * - STALE_ONLY: 除权/研报/阶段历史等——只 stale 不轮询(由用户动作或定时任务刷新)
 */
export const REFRESH_TIERS = {
  REALTIME_MS: 5_000,
  BOARD_MS: 30_000,
  SLOW_MS: 5 * 60_000,
} as const

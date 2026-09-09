import type { DailyPnlMeta, DailyPnlPeriod, PortfolioSummary, QuoteSnapshot } from './types'

export const round2 = (value: number) => Math.round(value * 100) / 100

export const summarizeDailyPnlPeriod = (
  observations: Array<{ period: DailyPnlPeriod; date: string | null }>,
): DailyPnlMeta => {
  if (observations.length === 0) {
    return { daily_pnl_period: 'unknown', daily_pnl_label: '当日盈亏', daily_pnl_date: null }
  }
  const periods = new Set(observations.map(item => item.period))
  const dates = new Set(observations.map(item => item.date).filter((value): value is string => !!value))
  if (periods.size === 1 && periods.has('today')) {
    return {
      daily_pnl_period: 'today',
      daily_pnl_label: '今日盈亏',
      daily_pnl_date: dates.size === 1 ? [...dates][0] : null,
    }
  }
  if (periods.size === 1 && periods.has('previous_trading_day') && dates.size === 1) {
    return {
      daily_pnl_period: 'previous_trading_day',
      daily_pnl_label: '上一交易日盈亏',
      daily_pnl_date: [...dates][0],
    }
  }
  return {
    daily_pnl_period: periods.size > 1 || dates.size > 1 ? 'mixed' : 'unknown',
    daily_pnl_label: dates.size > 0 ? '最近交易日盈亏' : '当日盈亏',
    daily_pnl_date: null,
  }
}

export const dailyPnlDisplayLabel = (meta: DailyPnlMeta, compact = false): string => {
  const date = meta.daily_pnl_date ? meta.daily_pnl_date.slice(5) : ''
  if (compact) {
    if (meta.daily_pnl_period === 'today') return '今日'
    if (meta.daily_pnl_period === 'previous_trading_day') return date ? `上一交易日 ${date}` : '上一交易日'
    if (meta.daily_pnl_period === 'mixed') return '最近交易日'
    return '当日'
  }
  return date && meta.daily_pnl_period === 'previous_trading_day'
    ? `${meta.daily_pnl_label} (${date})`
    : meta.daily_pnl_label
}

export const mergePortfolioQuotes = (
  portfolio: PortfolioSummary | null,
  quotes: Record<string, QuoteSnapshot>
): PortfolioSummary | null => {
  if (!portfolio) return null

  const hkdRate = portfolio.exchange_rates?.HKD_CNY ?? 0.92
  const usdRate = portfolio.exchange_rates?.USD_CNY ?? 7.25

  let grandMarketValue = 0
  let grandCost = 0
  let grandAvailable = 0
  let grandDailyPnl = 0
  const grandDailyPnlObservations: Array<{ period: DailyPnlPeriod; date: string | null }> = []

  const accounts = portfolio.accounts.map(account => {
    let accMarketValue = 0
    let accCost = 0
    let accDailyPnl = 0
    const accDailyPnlObservations: Array<{ period: DailyPnlPeriod; date: string | null }> = []

    const positions = account.positions.map(pos => {
      const quote = quotes[`${pos.market}:${pos.symbol}`]
      const current_price = quote?.current_price ?? pos.current_price ?? null
      const change_pct = quote?.change_pct ?? pos.change_pct ?? null
      const quote_time = quote?.quote_time ?? pos.quote_time ?? null
      const quote_date = quote?.quote_date ?? pos.quote_date ?? null
      const daily_pnl_period = quote?.daily_pnl_period ?? pos.daily_pnl_period ?? 'unknown'
      const rate = pos.market === 'HK' ? hkdRate : pos.market === 'US' ? usdRate : 1

      const cost = pos.cost_price * pos.quantity * rate
      accCost += cost

      let market_value: number | null = null
      let market_value_cny: number | null = null
      let pnl: number | null = null
      let pnl_pct: number | null = null
      let daily_pnl: number | null = null
      let daily_pnl_pct: number | null = null

      if (current_price != null) {
        market_value = current_price * pos.quantity
        market_value_cny = market_value * rate
        accMarketValue += market_value_cny
        pnl = market_value_cny - cost
        pnl_pct = cost > 0 ? (pnl / cost * 100) : 0
      }

      if (current_price != null && change_pct != null && change_pct !== -100) {
        const prev = current_price / (1 + change_pct / 100)
        if (isFinite(prev) && prev > 0) {
          daily_pnl = round2((current_price - prev) * pos.quantity * rate)
          daily_pnl_pct = round2(change_pct)
          accDailyPnl += daily_pnl
          const observation = { period: daily_pnl_period, date: quote_date }
          accDailyPnlObservations.push(observation)
          grandDailyPnlObservations.push(observation)
        }
      }

      return {
        ...pos,
        current_price,
        current_price_cny: current_price != null ? current_price * rate : null,
        change_pct,
        market_value,
        market_value_cny,
        pnl,
        pnl_pct,
        daily_pnl,
        daily_pnl_pct,
        daily_pnl_period,
        quote_time,
        quote_date,
        exchange_rate: pos.market === 'HK' || pos.market === 'US' ? rate : null,
      }
    })

    const accPnl = accMarketValue - accCost
    const accPnlPct = accCost > 0 ? (accPnl / accCost * 100) : 0
    const accTotalAssets = accMarketValue + account.available_funds

    grandMarketValue += accMarketValue
    grandCost += accCost
    grandAvailable += account.available_funds
    grandDailyPnl += accDailyPnl

    return {
      ...account,
      total_market_value: round2(accMarketValue),
      total_cost: round2(accCost),
      total_pnl: round2(accPnl),
      total_pnl_pct: round2(accPnlPct),
      total_daily_pnl: round2(accDailyPnl),
      total_assets: round2(accTotalAssets),
      ...summarizeDailyPnlPeriod(accDailyPnlObservations),
      positions,
    }
  })

  const grandPnl = grandMarketValue - grandCost
  const grandPnlPct = grandCost > 0 ? (grandPnl / grandCost * 100) : 0
  const grandTotalAssets = grandMarketValue + grandAvailable

  return {
    ...portfolio,
    accounts,
    total: {
      total_market_value: round2(grandMarketValue),
      total_cost: round2(grandCost),
      total_pnl: round2(grandPnl),
      total_pnl_pct: round2(grandPnlPct),
      total_daily_pnl: round2(grandDailyPnl),
      available_funds: round2(grandAvailable),
      total_assets: round2(grandTotalAssets),
      ...summarizeDailyPnlPeriod(grandDailyPnlObservations),
    },
  }
}

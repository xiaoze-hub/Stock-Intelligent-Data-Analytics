/**
 * 数据口径徽标(P3, 2026-09-10): 显式标注数据截至日期 + 来源, 滞后可以、撒谎不行。
 * - live=true → "实时"(行情等秒级数据)
 * - asof 为空 → "无数据"(禁止编数, 由调用方保证不渲染假日期)
 * - 否则 → "截至 YYYY-MM-DD", hover 显示来源
 */
export default function AsOfBadge({
  asof,
  source,
  live,
}: {
  asof?: string | null
  source?: string | null
  live?: boolean
}) {
  if (live) {
    return (
      <span className="text-[10px] text-emerald-500 font-mono" title="实时数据">
        实时
      </span>
    )
  }
  if (!asof) {
    return (
      <span className="text-[10px] text-muted-foreground font-mono" title="暂无口径日期">
        无数据
      </span>
    )
  }
  return (
    <span
      className="text-[10px] text-muted-foreground font-mono"
      title={source ? `来源 ${source}` : `数据截至 ${asof}`}
    >
      截至 {asof}
      {source ? ` · ${source}` : ''}
    </span>
  )
}

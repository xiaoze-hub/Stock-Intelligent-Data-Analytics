import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchAPI, stocksApi } from '@panwatch/api'
import type { SearchResult } from '../pages/stocks/types'

/**
 * 全局搜股面板(OT-Phase2, 借鉴 OpenTerminal CommandPalette)。
 * Cmd/Ctrl+K 唤起；Enter 跳持仓页并打开洞察；Shift+Enter 顺手加自选。
 * 无结果显"无数据"，不编数。
 */
export default function StockCommandPalette() {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [selected, setSelected] = useState(0)
  const [notice, setNotice] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const timer = useRef<number | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen((v) => !v)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    if (open) {
      setQuery('')
      setResults([])
      setSelected(0)
      setNotice('')
      setTimeout(() => inputRef.current?.focus(), 30)
    }
  }, [open ])

  const doSearch = useCallback(async (q: string) => {
    if (!q.trim()) {
      setResults([])
      return
    }
    try {
      const res = await fetchAPI<SearchResult[]>(`/stocks/search?q=${encodeURIComponent(q.trim())}`)
      setResults(res || [])
      setSelected(0)
    } catch {
      setResults([])
    }
  }, [])

  const onChange = (v: string) => {
    setQuery(v)
    setNotice('')
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => void doSearch(v), 300)
  }

  const pick = async (r: SearchResult, addWatch: boolean) => {
    if (addWatch) {
      try {
        await stocksApi.create({ symbol: r.symbol, name: r.name || r.symbol, market: r.market || 'CN' })
        setNotice(`已加入自选 ${r.symbol}`)
      } catch (e) {
        setNotice(e instanceof Error ? e.message : '加入自选失败')
      }
    }
    setOpen(false)
    navigate(`/stocks?symbol=${encodeURIComponent(r.symbol)}&market=${encodeURIComponent(r.market || 'CN')}`)
  }

  useEffect(() => setSelected(0), [results.length])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 bg-black/60 z-[100] flex items-start justify-center pt-24"
      onClick={() => setOpen(false)}
    >
      <div
        className="w-[560px] max-w-[92vw] rounded-xl border border-border bg-card shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') setOpen(false)
            if (e.key === 'ArrowDown') setSelected((s) => Math.min(s + 1, results.length - 1))
            if (e.key === 'ArrowUp') setSelected((s) => Math.max(s - 1, 0))
            if (e.key === 'Enter' && results[selected]) void pick(results[selected], e.shiftKey)
          }}
          placeholder="搜代码/名称… (Enter 打开 · Shift+Enter 打开+加自选)"
          className="w-full bg-transparent border-0 border-b border-border px-3 py-2.5 text-[13px] outline-none"
        />
        <div className="max-h-80 overflow-auto">
          {results.map((r, i) => (
            <div
              key={`${r.market}:${r.symbol}`}
              onClick={() => void pick(r, false)}
              className={`px-3 py-1.5 flex gap-3 cursor-pointer text-[12px] ${
                i === selected ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/50'
              }`}
            >
              <span className="w-24 font-mono font-semibold">{r.symbol}</span>
              <span className="flex-1 truncate text-muted-foreground">{r.name}</span>
              <span className="text-muted-foreground/70">{r.market}</span>
            </div>
          ))}
          {query.trim() && results.length === 0 && (
            <div className="px-3 py-3 text-[12px] text-muted-foreground">无数据</div>
          )}
        </div>
        {notice && <div className="px-3 py-1.5 text-[11px] text-muted-foreground border-t border-border">{notice}</div>}
      </div>
    </div>
  )
}

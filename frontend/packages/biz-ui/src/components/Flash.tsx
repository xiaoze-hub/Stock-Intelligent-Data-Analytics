import { useEffect, useRef, useState } from 'react'

/**
 * 值变闪动(Phase 1, 借鉴 OpenTerminal Flash.tsx)。
 * 行情数字变化时闪 450ms，让人一眼看到"刚动的是谁"。
 * 样式: src/index.css 的 .sida-flash。
 */

/** 值相对上一次变化的瞬间为 true(450ms 后复位)。 */
export function useFlash(value: string | number | null | undefined): boolean {
  const prev = useRef(value)
  const [flashing, setFlashing] = useState(false)

  useEffect(() => {
    if (value !== undefined && value !== null && prev.current !== value && prev.current !== undefined && prev.current !== null) {
      setFlashing(true)
      const t = setTimeout(() => setFlashing(false), 450)
      prev.current = value
      return () => clearTimeout(t)
    }
    prev.current = value
  }, [value])

  return flashing
}

/** 包住行情数字，值变时闪一下。span 包裹，零布局位移。 */
export default function Flash({
  value,
  className,
  children,
}: {
  value: string | number | null | undefined
  className?: string
  children: React.ReactNode
}) {
  const flashing = useFlash(value)
  return <span className={`${className ?? ''} ${flashing ? 'sida-flash' : ''}`}>{children}</span>
}

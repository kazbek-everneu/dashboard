import { useRef, useCallback } from "react"
import { cn } from "@/lib/utils"
import { BrowserIcon } from "@/components/ui/BrowserIcon"

const BROWSERS = [
  { value: "chromium", label: "Chromium" },
  { value: "chrome",   label: "Chrome" },
  { value: "firefox",  label: "Firefox" },
  { value: "webkit",   label: "Safari" },
  { value: "msedge",   label: "Edge" },
] as const

type BrowserValue = (typeof BROWSERS)[number]["value"]

interface Props {
  value: string
  onChange: (value: BrowserValue) => void
}

export function BrowserRadioGroup({ value, onChange }: Props) {
  const refs = useRef<(HTMLDivElement | null)[]>([])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent, index: number) => {
      let next = index
      if (e.key === "ArrowRight" || e.key === "ArrowDown") {
        next = (index + 1) % BROWSERS.length
      } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
        next = (index - 1 + BROWSERS.length) % BROWSERS.length
      } else {
        return
      }
      e.preventDefault()
      onChange(BROWSERS[next].value)
      refs.current[next]?.focus()
    },
    [onChange],
  )

  return (
    <div role="radiogroup" aria-label="Default browser" className="flex flex-wrap gap-3">
      {BROWSERS.map((browser, i) => {
        const isSelected = value === browser.value
        return (
          <div
            key={browser.value}
            ref={(el) => { refs.current[i] = el }}
            role="radio"
            aria-checked={isSelected}
            tabIndex={isSelected ? 0 : -1}
            onClick={() => onChange(browser.value)}
            onKeyDown={(e) => handleKeyDown(e, i)}
            className={cn(
              "flex h-[88px] w-20 cursor-pointer select-none flex-col items-center justify-center gap-2 border-2 transition-colors duration-150",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
              isSelected
                ? "border-accent bg-muted text-foreground"
                : "border-border bg-card text-foreground hover:border-foreground/30",
            )}
          >
            <BrowserIcon browser={browser.value} size={36} />
            <span className="text-xs font-medium leading-none">{browser.label}</span>
          </div>
        )
      })}
    </div>
  )
}

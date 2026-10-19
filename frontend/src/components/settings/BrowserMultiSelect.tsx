import { cn } from "@/lib/utils"
import { BrowserIcon } from "@/components/ui/BrowserIcon"

export interface BrowserMultiSelectProps {
  /** null = only default browsers are active (no explicit override stored) */
  selected: string[] | null
  /** The project-level default browsers — used when selected is null */
  defaultBrowsers: string[]
  onChange: (browsers: string[] | null) => void
  className?: string
}

const BROWSERS = [
  { value: "chrome", label: "Chrome" },
  { value: "firefox", label: "Firefox" },
  { value: "webkit", label: "Safari" },
  { value: "msedge", label: "Edge" },
] as const

function arraysEqual(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false
  const sa = [...a].sort()
  const sb = [...b].sort()
  return sa.every((v, i) => v === sb[i])
}

export function BrowserMultiSelect({
  selected,
  defaultBrowsers,
  onChange,
  className,
}: BrowserMultiSelectProps) {
  // When null, treat the defaults as active
  const activeBrowsers = selected ?? [...defaultBrowsers]

  function toggle(browserValue: string) {
    let next: string[]
    if (activeBrowsers.includes(browserValue)) {
      next = activeBrowsers.filter((b) => b !== browserValue)
      if (next.length === 0) return // enforce min 1
    } else {
      next = [...activeBrowsers, browserValue]
    }
    // Collapse back to null when the result is identical to the defaults
    onChange(arraysEqual(next, defaultBrowsers) ? null : next)
  }

  return (
    <div className={cn("flex items-center gap-0.5", className)}>
      {BROWSERS.map(({ value, label }) => {
        const isActive = activeBrowsers.includes(value)
        const isDefault = defaultBrowsers.includes(value)

        return (
          <button
            key={value}
            type="button"
            onClick={() => toggle(value)}
            title={isDefault ? `${label} (default)` : label}
            className={cn(
              "border p-1 transition-opacity",
              isActive ? "opacity-100 bg-muted" : "opacity-30 hover:opacity-70",
            )}
          >
            <BrowserIcon browser={value} size={20} />
          </button>
        )
      })}
    </div>
  )
}

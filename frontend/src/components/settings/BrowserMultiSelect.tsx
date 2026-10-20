import { BrowserIcon } from "@/components/ui/BrowserIcon"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"

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

  function handleChange(next: string[]) {
    if (next.length === 0) return // enforce min 1
    // Collapse back to null when the result is identical to the defaults
    onChange(arraysEqual(next, defaultBrowsers) ? null : next)
  }

  return (
    <ToggleGroup
      type="multiple"
      variant="outline"
      size="sm"
      spacing={0}
      value={activeBrowsers}
      onValueChange={handleChange}
      aria-label="Browsers"
      className={className}
    >
      {BROWSERS.map(({ value, label }) => {
        const name = defaultBrowsers.includes(value) ? `${label} (default)` : label
        return (
          <ToggleGroupItem
            key={value}
            value={value}
            aria-label={name}
            title={name}
            className="data-[state=off]:opacity-40 data-[state=off]:hover:opacity-80"
          >
            <BrowserIcon browser={value} size={16} />
          </ToggleGroupItem>
        )
      })}
    </ToggleGroup>
  )
}

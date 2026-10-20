import type { CSSProperties, ReactNode } from "react"
import { cn } from "@/lib/utils"

export interface MetricItem {
  label: string
  value: ReactNode
  valueClassName?: string
}

export function TaskStatsBar({
  items,
  compact = false,
}: {
  items: MetricItem[]
  compact?: boolean
}) {
  return (
    <dl
      // One row on wide screens, a wrapped grid below that.
      style={{ "--metric-cols": items.length } as CSSProperties}
      className="grid w-full grid-cols-3 gap-y-3 rounded-xl border bg-card py-3 sm:grid-cols-5 xl:grid-cols-[repeat(var(--metric-cols),minmax(0,1fr))]"
    >
      {items.map((item, index) => (
        <div
          key={item.label}
          className={cn(
            "flex flex-col items-center justify-center gap-1 text-center",
            compact ? "px-2" : "px-3",
            index > 0 && "xl:border-l",
          )}
        >
          <dt className="text-xs text-muted-foreground">{item.label}</dt>
          <dd className={item.valueClassName ?? "text-sm font-semibold leading-none text-foreground"}>
            {item.value}
          </dd>
        </div>
      ))}
    </dl>
  )
}

import type { ReactNode } from "react"
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
    <div className="w-full">
      <dl
        style={{ gridTemplateColumns: `repeat(${items.length}, minmax(0, 1fr))` }}
        className="grid w-full items-stretch"
      >
        {items.map((item, index) => (
          <div
            key={item.label}
            className={cn(
              "flex flex-col items-center justify-center text-center",
              compact ? "min-h-12 px-3" : "min-h-14 px-4",
            )}
          >
            <div className={cn("flex w-full flex-col items-center justify-center gap-0.5", index > 0 && "border-l surface-divider")}>
              <dt className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                {item.label}
              </dt>
              <dd className={item.valueClassName ?? "text-sm font-semibold leading-none text-foreground"}>
                {item.value}
              </dd>
            </div>
          </div>
        ))}
      </dl>
    </div>
  )
}

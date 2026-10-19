import type { LucideIcon } from "lucide-react"
import { cn } from "@/lib/utils"

interface StatsCardProps {
  icon: LucideIcon
  value: number | string
  label: string
  trend?: string
  trendUp?: boolean
  pulse?: boolean
  className?: string
  meta?: string
}

export function StatsCard({
  icon: Icon,
  value,
  label,
  trend,
  trendUp,
  pulse,
  className,
  meta,
}: StatsCardProps) {
  return (
    <div className={cn("glass-card hover-lift p-6", className)}>
      <div className="mb-5 flex items-start justify-between gap-3">
        <div className="flex h-12 w-12 items-center justify-center border bg-background">
          <Icon className="h-5 w-5 text-foreground" />
        </div>
        {meta ? <span className="technical-caption">{meta}</span> : null}
      </div>
      <div className="flex items-end gap-2">
        <span className="text-3xl font-semibold tracking-tight text-foreground">{value}</span>
        {pulse && (
          <span className="mt-2 inline-block h-2 w-2 bg-accent animate-pulse" />
        )}
      </div>
      <p className="mt-1.5 text-sm font-medium text-[color:var(--text-secondary)]">{label}</p>
      {trend && (
        <p
          className="mt-3 text-xs font-medium"
          style={{ color: trendUp === false ? "var(--destructive)" : "var(--accent)" }}
        >
          {trendUp !== false ? "↑" : "↓"} {trend}
        </p>
      )}
    </div>
  )
}

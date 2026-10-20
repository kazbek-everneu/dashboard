import type { LucideIcon } from "lucide-react"
import { cn } from "@/lib/utils"
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"

interface StatsCardProps {
  icon: LucideIcon
  value: number | string
  label: string
  /** Pulsing dot next to the value, e.g. while tasks are running */
  pulse?: boolean
  className?: string
}

export function StatsCard({ icon: Icon, value, label, pulse, className }: StatsCardProps) {
  return (
    <Card className={cn("@container/card", className)}>
      <CardHeader>
        <CardDescription className="flex items-center gap-2">
          <Icon className="size-4" />
          {label}
        </CardDescription>
        <CardTitle className="flex items-center gap-2 text-2xl font-semibold tabular-nums @[200px]/card:text-3xl">
          {value}
          {pulse && <span className="size-2 animate-pulse rounded-full bg-status-running-dot" />}
        </CardTitle>
      </CardHeader>
    </Card>
  )
}

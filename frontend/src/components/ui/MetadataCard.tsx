import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

interface MetadataCardProps {
  label: string
  children: ReactNode
  className?: string
}

export function MetadataCard({ label, children, className }: MetadataCardProps) {
  return (
    <div className={cn("glass-card p-4", className)}>
      <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
        {label}
      </p>
      <div className="text-sm text-foreground">{children}</div>
    </div>
  )
}

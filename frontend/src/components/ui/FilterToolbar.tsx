import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

interface FilterToolbarProps {
  filters: ReactNode
  actions?: ReactNode
  className?: string
}

export function FilterToolbar({ filters, actions, className }: FilterToolbarProps) {
  return (
    <div
      className={cn(
        "glass-card flex flex-col gap-3 px-4 py-4 lg:flex-row lg:items-center lg:justify-between",
        className,
      )}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-2.5">{filters}</div>
      {actions ? <div className="flex flex-wrap items-center gap-2 lg:justify-end">{actions}</div> : null}
    </div>
  )
}

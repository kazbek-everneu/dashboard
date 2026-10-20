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
        "flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between",
        className,
      )}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-2">{filters}</div>
      {actions ? <div className="flex flex-wrap items-center gap-2 lg:justify-end">{actions}</div> : null}
    </div>
  )
}

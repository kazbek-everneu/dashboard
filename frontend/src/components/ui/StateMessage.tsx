import type { ReactNode } from "react"

import { cn } from "@/lib/utils"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"

interface StateMessageProps {
  title: string
  description?: string
  icon?: ReactNode
  action?: ReactNode
  tone?: "default" | "destructive"
  compact?: boolean
  className?: string
}

export function StateMessage({
  title,
  description,
  icon,
  action,
  tone = "default",
  compact = false,
  className,
}: StateMessageProps) {
  return (
    <Empty
      className={cn(
        "border",
        compact ? "min-h-[180px]" : "min-h-[260px]",
        tone === "destructive" && "border-destructive/30",
        className,
      )}
    >
      <EmptyHeader>
        {icon ? (
          <EmptyMedia
            variant="icon"
            className={cn(tone === "destructive" && "bg-destructive/10 text-destructive")}
          >
            {icon}
          </EmptyMedia>
        ) : null}
        <EmptyTitle className={cn(tone === "destructive" && "text-destructive")}>{title}</EmptyTitle>
        {description ? <EmptyDescription>{description}</EmptyDescription> : null}
      </EmptyHeader>
      {action ? <EmptyContent>{action}</EmptyContent> : null}
    </Empty>
  )
}

import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

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
    <div
      className={cn(
        "glass-card flex flex-col items-center justify-center px-6 text-center",
        compact ? "min-h-[180px] py-10" : "min-h-[260px] py-14",
        className,
      )}
    >
      {icon ? (
        <div
          className={cn(
            "mb-4 flex h-14 w-14 items-center justify-center border",
            tone === "destructive"
              ? "border-destructive/20 bg-destructive/10 text-destructive"
              : "bg-background text-foreground",
          )}
        >
          <div className="[&>*]:h-6 [&>*]:w-6">{icon}</div>
        </div>
      ) : null}
      <p className={cn("text-base font-semibold", tone === "destructive" ? "text-destructive" : "text-foreground")}>
        {title}
      </p>
      {description ? <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">{description}</p> : null}
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  )
}

import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

interface PageHeaderProps {
  title: ReactNode
  description?: ReactNode
  meta?: ReactNode
  actions?: ReactNode
  footer?: ReactNode
  footerClassName?: string
  className?: string
  contentClassName?: string
  descriptionClassName?: string
}

export default function PageHeader({
  title,
  description,
  meta,
  actions,
  footer,
  footerClassName,
  className,
  contentClassName,
  descriptionClassName,
}: PageHeaderProps) {
  return (
    <div className={cn("flex flex-col gap-4", className)}>
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div className={cn("min-w-0 space-y-1", contentClassName)}>
          {title}
          {description ? (
            <div className={cn("max-w-3xl text-sm text-muted-foreground", descriptionClassName)}>
              {description}
            </div>
          ) : null}
          {meta ? <div className="text-xs text-muted-foreground">{meta}</div> : null}
        </div>

        {actions ? (
          <div className="flex flex-wrap items-center gap-2 md:justify-end">{actions}</div>
        ) : null}
      </div>

      {footer ? <div className={footerClassName}>{footer}</div> : null}
    </div>
  )
}

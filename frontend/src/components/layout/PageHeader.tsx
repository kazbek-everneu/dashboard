import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

interface PageHeaderProps {
  title: ReactNode
  description?: ReactNode
  meta?: ReactNode
  actions?: ReactNode
  footer?: ReactNode
  footerClassName?: string
  bordered?: boolean
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
  bordered = true,
  className,
  contentClassName,
  descriptionClassName,
}: PageHeaderProps) {
  return (
    <div
      className={cn(
        "relative flex flex-col gap-5 glass-card px-6 py-6 sm:px-7 sm:py-7",
        bordered && "border",
        className,
      )}
    >
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className={cn("min-w-0 space-y-2", contentClassName)}>
          {title}
          {description ? (
            <div className={cn("max-w-3xl text-[15px] leading-6 text-[color:var(--text-secondary)]", descriptionClassName)}>
              {description}
            </div>
          ) : null}
          {meta ? <div className="technical-caption">{meta}</div> : null}
        </div>

        {actions ? <div className="flex flex-wrap gap-2 lg:justify-end">{actions}</div> : null}
      </div>

      {footer ? <div className={cn("surface-divider", footerClassName)}>{footer}</div> : null}
    </div>
  )
}

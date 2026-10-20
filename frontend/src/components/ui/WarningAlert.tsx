import type { ComponentProps, ReactNode } from "react"
import { AlertTriangle } from "lucide-react"

import { cn } from "@/lib/utils"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"

interface WarningAlertProps extends Omit<ComponentProps<typeof Alert>, "title" | "variant"> {
  title?: ReactNode
  /** Replaces the warning triangle. */
  icon?: ReactNode
}

/** Alert in the warning (amber) tone: something in a result may be off, but it
 *  is not an error. Children become the description. */
export function WarningAlert({ title, icon, className, children, ...props }: WarningAlertProps) {
  return (
    <Alert
      className={cn("border-warning/40 bg-status-warning-bg text-status-warning-fg", className)}
      {...props}
    >
      {icon ?? <AlertTriangle aria-hidden="true" />}
      {title ? <AlertTitle className="min-w-0">{title}</AlertTitle> : null}
      {children ? (
        <AlertDescription className="min-w-0 text-foreground/80">{children}</AlertDescription>
      ) : null}
    </Alert>
  )
}

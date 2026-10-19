import { useEffect, useRef, useState } from "react"
import type { ReactNode } from "react"
import { ChevronDown } from "lucide-react"

import { cn } from "@/lib/utils"

interface MenuSurfaceProps {
  trigger: ReactNode
  children: ReactNode
  className?: string
  contentClassName?: string
}

export function MenuSurface({
  trigger,
  children,
  className,
  contentClassName,
}: MenuSurfaceProps) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClick)
    return () => document.removeEventListener("mousedown", handleClick)
  }, [open])

  return (
    <div ref={ref} className={cn("relative", className)}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className={cn(
          "flex items-center gap-2 border bg-background px-3 py-2 text-sm text-[color:var(--text-secondary)] transition-colors hover:border-foreground/20 hover:bg-muted hover:text-foreground",
          open && "border-foreground/20 text-foreground shadow-sm",
        )}
      >
        {trigger}
        <ChevronDown
          size={14}
          className={cn("text-muted-foreground transition-transform duration-150", open && "rotate-180")}
        />
      </button>

      {open ? (
        <div className={cn("glass-elevated absolute left-0 top-full z-50 mt-2 min-w-[220px] py-1.5", contentClassName)}>
          {children}
        </div>
      ) : null}
    </div>
  )
}

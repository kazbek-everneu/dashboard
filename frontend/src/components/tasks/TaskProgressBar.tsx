import { AlertTriangle } from "lucide-react"
import { Progress as ProgressPrimitive } from "radix-ui"

import { cn } from "@/lib/utils"
import type { TaskStatus } from "@/types"

interface TaskProgressBarProps {
  status: TaskStatus | string
  done: number
  total: number
  failed?: number
  compact?: boolean
  className?: string
}

function getFillClassName(status: string, progress: number): string {
  if (status === "failed") return "task-progress-fill task-progress-fill-failed"
  if (status === "timed_out") return "task-progress-fill task-progress-fill-timed-out"
  if (status === "cancelled") return "task-progress-fill task-progress-fill-cancelled"
  if (progress >= 100 || status === "completed") {
    return "task-progress-fill task-progress-fill-complete"
  }
  return "task-progress-fill task-progress-fill-active"
}

export function TaskProgressBar({
  status,
  done,
  total,
  failed = 0,
  compact = false,
  className,
}: TaskProgressBarProps) {
  const rawProgress = total > 0 ? Math.round((done / total) * 100) : 0
  const progress = Math.min(100, Math.max(0, rawProgress))
  const hasMismatch = rawProgress > 100

  return (
    <div className={cn("space-y-2 min-w-[168px]", compact && "space-y-1 min-w-[148px]", className)}>
      <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5 min-w-0">
          <span className="truncate">
            {done}/{total}
          </span>
          {hasMismatch && (
            <span title="Task count mismatch — task may have been created with different settings">
              <AlertTriangle size={11} className="text-amber-500" />
            </span>
          )}
          {failed > 0 && (
            <span className="text-destructive whitespace-nowrap">({failed} failed)</span>
          )}
        </span>
        <span className="font-medium tabular-nums">{progress}%</span>
      </div>

      <ProgressPrimitive.Root
        value={progress}
        max={100}
        aria-label={`Task progress: ${done} of ${total} completed`}
        aria-valuetext={`${progress}% complete`}
        className={cn(
          "relative w-full overflow-hidden rounded-full bg-task-progress-track",
          compact ? "h-1.5" : "h-2",
        )}
      >
        <ProgressPrimitive.Indicator
          className={cn(
            "size-full rounded-full transition-transform duration-500",
            getFillClassName(status, progress),
          )}
          style={{ transform: `translateX(-${100 - progress}%)` }}
        />
      </ProgressPrimitive.Root>
    </div>
  )
}

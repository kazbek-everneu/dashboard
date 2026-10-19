import { Badge } from "@/components/ui/badge"

export function StatusBadge({ status }: { status: string }) {
  const label = status.replace(/_/g, " ")

  return (
    <Badge
      className="border-0 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.08em]"
      style={{
        background: `var(--status-${status === "ok" ? "completed" : status}-bg, var(--status-cancelled-bg))`,
        color: `var(--status-${status === "ok" ? "completed" : status}-fg, var(--status-cancelled-fg))`,
      }}
    >
      {status === "running" && (
        <span
          className="mr-1.5 inline-block h-1.5 w-1.5 animate-pulse shrink-0"
          style={{ background: "var(--status-running-dot)" }}
        />
      )}
      {label}
    </Badge>
  )
}

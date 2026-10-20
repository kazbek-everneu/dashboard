import { useMemo } from "react"
import { Download } from "lucide-react"
import { formatDate } from "@/lib/helpers"
import { StateMessage } from "@/components/ui/StateMessage"
import { StatusBadge } from "@/components/ui/StatusBadge"
import type { Screenshot } from "@/types"

const API_BASE = import.meta.env.VITE_API_URL ?? "/api"

function fileExtension(screenshot: Screenshot): string {
  return screenshot.file_format === "jpeg" ? "jpg" : screenshot.file_format
}

export function LogsTable({ screenshots }: { screenshots: Screenshot[] }) {
  const sorted = useMemo(
    () => [...screenshots].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()),
    [screenshots],
  )

  if (sorted.length === 0) {
    return (
      <StateMessage
        compact
        title="No log entries yet"
        description="They appear here as screenshots are taken."
      />
    )
  }

  return (
    <div className="glass-card overflow-hidden text-xs">
      <table className="w-full">
        <thead className="sticky-table-head border-b surface-divider">
          <tr>
            {["Time", "URL", "Viewport", "Browser", "Status", ""].map((h) => (
              <th key={h} className="px-4 py-3 text-left text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((s) => (
            <tr
              key={s.id}
              className={`border-b surface-divider last:border-0 ${s.status === "failed" ? "bg-destructive/5" : "hover:bg-muted/60"}`}
            >
              <td className="px-4 py-3 text-muted-foreground font-mono whitespace-nowrap">
                {formatDate(s.created_at)}
              </td>
              <td className="px-4 py-3 max-w-xs">
                <span className="truncate block" title={s.url}>{s.url}</span>
                {s.status === "failed" && s.error_message && (
                  <span className="text-destructive block mt-0.5">{s.error_message}</span>
                )}
                {s.status === "ok" && s.captured_with_unstable_dom && s.error_message && (
                  <span className="text-amber-600 dark:text-amber-400 block mt-0.5">
                    {s.error_message}
                  </span>
                )}
              </td>
              <td className="px-4 py-3 font-mono whitespace-nowrap">{s.width}x{s.height}</td>
              <td className="px-4 py-3">{s.browser}</td>
              <td className="px-4 py-3"><StatusBadge status={s.status} /></td>
              <td className="px-4 py-3">
                {s.status === "ok" && s.signed_url && (
                  <a
                    href={`${API_BASE}${s.signed_url}`}
                    download={[
                      s.url?.split("/").filter(Boolean).pop() || "screenshot",
                      `${s.width}x${s.height}`,
                      s.browser,
                    ].join("_") + `.${fileExtension(s)}`}
                    onClick={(e) => e.stopPropagation()}
                    className="p-1.5 text-muted-foreground hover:text-foreground transition-colors"
                    title="Download screenshot"
                  >
                    <Download size={14} />
                  </a>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

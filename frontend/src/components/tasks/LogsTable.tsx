import { useMemo } from "react"
import { Download } from "lucide-react"
import { cn } from "@/lib/utils"
import { formatDate } from "@/lib/helpers"
import { browserLabel } from "@/lib/task-formatters"
import { BrowserIcon } from "@/components/ui/BrowserIcon"
import { Button } from "@/components/ui/button"
import { StateMessage } from "@/components/ui/StateMessage"
import { StatusBadge } from "@/components/ui/StatusBadge"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import type { Screenshot } from "@/types"

const API_BASE = import.meta.env.VITE_API_URL ?? "/api"

function fileExtension(screenshot: Screenshot): string {
  return screenshot.file_format === "jpeg" ? "jpg" : screenshot.file_format
}

const HEADERS = ["Time", "URL", "Viewport", "Browser", "Status"]

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
    <div className="overflow-hidden rounded-xl border bg-card">
      <Table className="text-xs">
        <TableHeader className="bg-muted/50">
          <TableRow className="hover:bg-transparent">
            {HEADERS.map((h) => (
              <TableHead key={h} className="px-4 text-muted-foreground">
                {h}
              </TableHead>
            ))}
            <TableHead className="w-12 px-4">
              <span className="sr-only">Download</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((s) => (
            <TableRow
              key={s.id}
              className={cn(s.status === "failed" && "bg-destructive/5 hover:bg-destructive/10")}
            >
              <TableCell className="px-4 py-2.5 font-mono text-muted-foreground">
                {formatDate(s.created_at)}
              </TableCell>
              <TableCell className="max-w-xs px-4 py-2.5 whitespace-normal">
                <span className="block truncate" title={s.url}>
                  {s.url}
                </span>
                {s.status === "failed" && s.error_message && (
                  <span className="mt-0.5 block text-destructive">{s.error_message}</span>
                )}
                {s.status === "ok" && s.captured_with_unstable_dom && s.error_message && (
                  <span className="mt-0.5 block text-warning">{s.error_message}</span>
                )}
              </TableCell>
              <TableCell className="px-4 py-2.5 font-mono">
                {s.width}×{s.height}
              </TableCell>
              <TableCell className="px-4 py-2.5">
                <span className="inline-flex items-center gap-1.5">
                  <BrowserIcon browser={s.browser} size={14} />
                  {browserLabel(s.browser)}
                </span>
              </TableCell>
              <TableCell className="px-4 py-2.5">
                <StatusBadge status={s.status} />
              </TableCell>
              <TableCell className="px-4 py-2.5 text-right">
                {s.status === "ok" && s.signed_url && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button asChild variant="ghost" size="icon-xs" className="text-muted-foreground">
                        <a
                          href={`${API_BASE}${s.signed_url}`}
                          download={
                            [
                              s.url?.split("/").filter(Boolean).pop() || "screenshot",
                              `${s.width}x${s.height}`,
                              s.browser,
                            ].join("_") + `.${fileExtension(s)}`
                          }
                          aria-label="Download screenshot"
                        >
                          <Download />
                        </a>
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Download screenshot</TooltipContent>
                  </Tooltip>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
